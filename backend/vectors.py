import hashlib
import threading

from qdrant_client import QdrantClient, models

from backend.config import Settings
from backend.models import Chunk, Source


class VectorStore:
    def __init__(self, settings: Settings):
        self.client = (
            QdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key.get_secret_value() or None,
                timeout=30,
            )
            if settings.qdrant_url
            else QdrantClient(path=str(settings.data_dir / "qdrant"))
        )
        # Keep incompatible embedding spaces isolated even when dimensions match.
        identity = (
            settings.azure_openai_embedding_endpoint or settings.azure_openai_endpoint,
            settings.azure_openai_embedding_deployment,
        )
        suffix = hashlib.sha256(repr(identity).encode()).hexdigest()[:12]
        self.collection = f"{settings.qdrant_collection}_{suffix}"
        self.lock = threading.RLock()

    def health(self):
        with self.lock:
            self.client.get_collections()

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]):
        if len(chunks) != len(vectors) or not vectors or not vectors[0]:
            raise ValueError("Embedding count does not match document chunks.")
        size = len(vectors[0])
        if any(len(v) != size for v in vectors):
            raise ValueError("Embedding dimensions are inconsistent.")
        with self.lock:
            if not self.client.collection_exists(self.collection):
                self.client.create_collection(
                    self.collection,
                    vectors_config=models.VectorParams(size=size, distance=models.Distance.COSINE),
                )
            for start in range(0, len(chunks), 64):
                self.client.upsert(
                    self.collection,
                    wait=True,
                    points=[
                        models.PointStruct(id=c.chunk_id, vector=v, payload=c.model_dump())
                        for c, v in zip(chunks[start : start + 64], vectors[start : start + 64])
                    ],
                )

    def search(
        self,
        vector: list[float],
        document_ids: list[str],
        limit: int,
        score_threshold: float,
        user,
    ) -> list[Source]:
        if not document_ids:
            return []
        with self.lock:
            if not self.client.collection_exists(self.collection):
                return []
            result = self.client.query_points(
                self.collection,
                query=vector,
                limit=limit,
                with_payload=True,
                score_threshold=score_threshold,
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id", match=models.MatchAny(any=document_ids)
                        ),
                        models.FieldCondition(
                            key="tenant_id", match=models.MatchValue(value=user.tenant_id)
                        ),
                        models.Filter(
                            should=[
                                models.FieldCondition(
                                    key="owner_id", match=models.MatchValue(value=user.user_id)
                                ),
                                models.FieldCondition(
                                    key="allowed_users", match=models.MatchValue(value=user.user_id)
                                ),
                                models.FieldCondition(
                                    key="allowed_roles", match=models.MatchAny(any=user.roles)
                                ),
                            ]
                        ),
                    ],
                ),
            )
        return [
            Source(**point.payload, score=point.score, citation=i + 1)
            for i, point in enumerate(result.points)
        ]

    def set_acl(self, document_id: str, policy: dict):
        with self.lock:
            if self.client.collection_exists(self.collection):
                self.client.set_payload(
                    self.collection,
                    payload=policy,
                    wait=True,
                    points=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="document_id", match=models.MatchValue(value=document_id)
                            )
                        ]
                    ),
                )

    def remove(self, document_id: str):
        with self.lock:
            if self.client.collection_exists(self.collection):
                self.client.delete(
                    self.collection,
                    wait=True,
                    points_selector=models.FilterSelector(
                        filter=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="document_id", match=models.MatchValue(value=document_id)
                                )
                            ]
                        )
                    ),
                )

    def close(self):
        self.client.close()
