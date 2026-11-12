"""Check cited money/percentage literals. This does not establish semantic entailment."""

import re
import unicodedata
from decimal import Decimal

from backend.models import Source

NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
MONEY = re.compile(
    r"(?<!\w)([-−]?)\s*([$€£₹])\s*(" + NUMBER
    + r")\s*(billion|million|thousand|bn|[kmb])?(?!\w)", re.I
)
PERCENT = re.compile(r"(?<![\w.])(" + NUMBER + r")\s*(?:%|percent\b)", re.I)
CLAIM = re.compile(r"(.*?)(\[\d+\](?:\s*\[\d+\])*)", re.S)
SCALES = {"": 1, "k": 1000, "thousand": 1000, "m": 1000000, "million": 1000000,
          "b": 1000000000, "bn": 1000000000, "billion": 1000000000}


def financial_values(text: str) -> set[tuple[str, Decimal]]:
    text = unicodedata.normalize("NFKC", text).casefold()
    values = set()
    for match in MONEY.finditer(text):
        sign, symbol, number, scale = match.groups()
        value = Decimal(number.replace(",", "")) * SCALES[scale or ""]
        values.add((symbol, -value if sign in {"-", "−"} else value))
    for match in PERCENT.finditer(text):
        values.add(("%", Decimal(match[1].replace(",", ""))))
    return values


def check_financial_evidence(answer: str, sources: list[Source]) -> dict:
    """Bind each literal-bearing span to its trailing citation group.

    Existence of a value in a cited passage does not prove its entity, period,
    interpretation, or other prose. Uncited financial literals fail closed.
    """
    available = {source.citation: financial_values(source.text) for source in sources}
    checked = unsupported = values_checked = 0
    end = 0
    for match in CLAIM.finditer(answer):
        end = match.end()
        values = financial_values(match[1])
        if not values:
            continue
        checked += 1
        values_checked += len(values)
        if checked > 100:
            unsupported += 1
            break
        citations = set(map(int, re.findall(r"\[(\d+)\]", match[2])))
        evidence = set().union(*(available.get(citation, set()) for citation in citations))
        if not values.issubset(evidence):
            unsupported += 1
    if financial_values(answer[end:]):
        checked += 1
        unsupported += 1
    return {
        "scope": "cited_money_and_percent_literals",
        "status": "failed" if unsupported else "passed" if checked else "not_checked",
        "checked_spans": checked, "checked_values": values_checked,
        "unsupported_spans": unsupported,
    }
