import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from backend import parsing
from backend.config import Settings
from backend.main import create_app
from backend.models import Block
from tests.auth_helpers import sign_in
from tests.fakes import FakeAzure
from tests.test_pipeline import upload_ready


def make_pdf(path, *, columns=False, grid=False, image=False):
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    canvas = Canvas(str(path))
    canvas.setFont("Helvetica-Bold", 20)
    canvas.drawString(60, 760, "Quarterly Revenue")
    canvas.setFont("Helvetica", 11)
    for row in range(12):
        canvas.drawString(60, 700 - row * 18, "APAC revenue grew through enterprise contracts.")
        if columns:
            canvas.drawString(360, 700 - row * 18, "EMEA revenue was stable.")
    if grid:
        canvas.rect(50, 470, 480, 250)
        canvas.line(300, 470, 300, 720)
    if image:
        canvas.drawImage(ImageReader(Image.new("RGB", (100, 100), "black")),
                         60, 150, width=150, height=150)
    canvas.save()


def test_simple_pdf_fast_path_keeps_text_heading_and_page(tmp_path):
    path = tmp_path / "text.pdf"
    make_pdf(path)
    details = {}
    with patch.object(parsing, "converter", side_effect=AssertionError("Heavy parser called")):
        blocks, _ = parsing.parse_document(path, details=details)
    assert details["parser"] == "pdf-native"
    assert details["ocr_enabled"] is False
    assert len([b for b in blocks if "APAC revenue" in b.text]) >= 1
    assert all(b.page == 1 and b.section == "Quarterly Revenue" for b in blocks)


def test_sparse_native_pages_do_not_require_ocr(tmp_path):
    path = tmp_path / "sparse.pdf"
    canvas = Canvas(str(path))
    canvas.drawString(60, 700, "APAC revenue was $82M.")
    canvas.showPage()
    canvas.drawString(60, 700, "Operating expenses were $171M.")
    canvas.save()
    details = {}
    with patch.object(parsing, "converter", side_effect=AssertionError("Heavy parser called")):
        blocks, _ = parsing.parse_document(path, details=details)
    assert details["ocr_enabled"] is False
    assert [b.page for b in blocks] == [1, 2]
    assert "82M" in blocks[0].text and "171M" in blocks[1].text


def test_complex_pdf_routes_to_docling_with_selective_ocr(tmp_path):
    from backend.pdf_native import inspect_pdf

    for name, options, ocr in [
        ("columns", {"columns": True}, False),
        ("table", {"grid": True}, False),
        ("image", {"image": True}, True),
    ]:
        path = tmp_path / (name + ".pdf")
        make_pdf(path, **options)
        result = inspect_pdf(path)
        assert not result.fast_eligible
        assert result.needs_ocr is ocr


def test_chunking_does_not_retokenize_every_word(monkeypatch):
    calls = 0
    original = parsing.tokens

    def measured(text):
        nonlocal calls
        calls += 1
        return original(text)

    monkeypatch.setattr(parsing, "tokens", measured)
    text = "Knowledge café 東京 " * 2000
    chunks = parsing.chunk_document([Block(text=text)], "doc", "unicode.txt")
    assert calls < len(chunks) * 5 + 10
    assert " ".join(c.text.strip() for c in chunks).split() == text.split()


def test_duplicate_upload_reuses_job_and_records_timings(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path), FakeAzure())) as client:
        sign_in(client)
        first, detail = upload_ready(client)
        second = client.post(
            "/documents", files={"file": ("report.md", b"# Revenue\n\nAPAC revenue was $82M.")}
        )
        assert second.json()["document_id"] == first
        assert second.json()["reused"] is True
        assert len(client.get("/documents").json()) == 1
        timings = detail["document"]["timings_ms"]
        assert set(timings) >= {"queued", "parsing", "chunking", "embedding", "indexing"}
        assert timings["total"] == pytest.approx(
            sum(v for k, v in timings.items() if k != "total"), abs=0.05
        )
        assert detail["document"]["completed_at"]
        assert detail["document"]["processing"]["parser"] == "native-text"
        assert len(list((tmp_path / "uploads").iterdir())) == 1


def test_parsing_next_upload_is_not_blocked_by_embedding(tmp_path):
    started = threading.Event()
    release = threading.Event()

    class SlowAzure(FakeAzure):
        def embed(self, texts):
            started.set()
            assert release.wait(5), "test embedding gate timed out"
            return super().embed(texts)

    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path), SlowAzure())) as client:
        sign_in(client)
        try:
            first = client.post("/documents", files={"file": ("a.txt", b"APAC revenue")})
            assert first.status_code == 202
            assert started.wait(2)
            second = client.post("/documents", files={"file": ("b.txt", b"Different revenue")})
            doc_id = second.json()["document_id"]
            for _ in range(100):
                doc = client.get(f"/documents/{doc_id}").json()["document"]
                if doc["status"] == "waiting_embedding":
                    break
                time.sleep(0.01)
            assert doc["status"] == "waiting_embedding"
            assert doc["chunk_count"] > 0
        finally:
            release.set()
    # Lifespan shutdown must drain both stages before closing provider/storage.
    assert all(d.status == "ready" for d in client.app.state.catalog.list())


def test_concurrent_admission_is_bounded_and_active_duplicates_reuse(tmp_path):
    release = threading.Event()

    class GatedAzure(FakeAzure):
        def embed(self, texts):
            assert release.wait(15)
            return super().embed(texts)

    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path), GatedAzure())) as c:
        sign_in(c)
        try:
            def send(number):
                return c.post("/documents", files={"file": ("a.txt", f"Unique {number}".encode())})

            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(send, range(6)))
            assert sorted(r.status_code for r in results) == [202] * 5 + [429]
            accepted = next(i for i, r in enumerate(results) if r.status_code == 202)
            duplicate = send(accepted)
            assert duplicate.status_code == 202
            assert duplicate.json()["reused"] is True
            assert duplicate.json()["document_id"] == results[accepted].json()["document_id"]
            assert len(c.get("/documents").json()) == 5
            assert len(list((tmp_path / "uploads").iterdir())) == 5
        finally:
            release.set()


def test_failed_upload_can_retry_and_failure_has_stage_timings(tmp_path):
    provider = FakeAzure()
    provider.fail_embeddings = True
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path), provider)) as c:
        sign_in(c)
        first, detail = upload_ready(c)
        failed = detail["document"]
        assert failed["status"] == "failed"
        assert failed["completed_at"]
        assert failed["timings_ms"]["embedding"] >= 0
        assert not c.post("/retrieve", json={"question": "revenue"}).json()
        provider.fail_embeddings = False
        second, detail = upload_ready(c)
        assert second != first
        assert detail["document"]["status"] == "ready"


def test_reuse_survives_restart_but_respects_embedding_space_and_format(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    with TestClient(create_app(settings, FakeAzure())) as c:
        sign_in(c)
        first, _ = upload_ready(c)
    with TestClient(create_app(settings, FakeAzure())) as c:
        sign_in(c)
        second, _ = upload_ready(c)
        assert first == second
        different_format, _ = upload_ready(c, "report.txt")
        assert different_format != first
    settings.qdrant_collection = "new_embedding_space"
    with TestClient(create_app(settings, FakeAzure())) as c:
        sign_in(c)
        third, _ = upload_ready(c)
        assert third != first


def test_pdf_mode_and_parser_details_reach_upload_pipeline(tmp_path, monkeypatch):
    from backend import ingestion

    modes = []

    def parser(path, *, details, pdf_mode):
        modes.append(pdf_mode)
        details.update(parser="docling", ocr_enabled=False)
        return [Block(text="APAC revenue", page=1)], []

    monkeypatch.setattr(ingestion, "parse_document", parser)
    settings = Settings(_env_file=None, data_dir=tmp_path, pdf_parsing_mode="docling")
    with TestClient(create_app(settings, FakeAzure())) as c:
        sign_in(c)
        _, detail = upload_ready(c)
        assert modes == ["docling"]
        assert detail["document"]["processing"]["parser"] == "docling"


@pytest.mark.parametrize("kind", ["scan", "mixed", "rotated", "outlined", "form"])
def test_uncertain_pdf_never_uses_native_fast_path(tmp_path, kind):
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    from backend.pdf_native import inspect_pdf

    path = tmp_path / f"{kind}.pdf"
    canvas = Canvas(str(path))
    if kind == "mixed":
        canvas.drawString(60, 700, "Native text on the first page. " * 5)
        canvas.showPage()
    if kind in {"scan", "mixed"}:
        canvas.drawImage(ImageReader(Image.new("RGB", (100, 100), "black")), 60, 150)
    elif kind == "rotated":
        canvas.rotate(15)
        canvas.drawString(60, 400, "Rotated text must use layout reconstruction. " * 4)
    elif kind == "outlined":
        canvas.drawString(60, 700, "Readable page before outlined text. " * 5)
        canvas.showPage()
        canvas.rect(60, 150, 300, 300)
    elif kind == "form":
        canvas.beginForm("nested")
        canvas.drawString(60, 700, "Nested transformed text needs layout reconstruction. " * 3)
        canvas.endForm()
        canvas.doForm("nested")
    canvas.save()
    profile = inspect_pdf(path)
    assert not profile.fast_eligible
    if kind in {"scan", "mixed", "outlined"}:
        assert profile.needs_ocr
