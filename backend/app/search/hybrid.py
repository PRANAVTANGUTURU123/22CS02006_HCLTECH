"""Hybrid retrieval: BM25 (keywords) + Chroma (vectors), merged with reciprocal rank fusion."""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from backend.app.search.documents import Chunk

EmbedFn = Callable[[list[str]], list[list[float]]]

_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass(frozen=True)
class SearchHit:
    chunk: Chunk
    score: float
    bm25_rank: int | None
    vector_rank: int | None


def _default_embedder() -> EmbedFn:
    """Chroma's built-in ONNX MiniLM model. It downloads about 80 MB on first use."""
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    fn = DefaultEmbeddingFunction()
    return lambda texts: [list(map(float, v)) for v in fn(texts)]


class HybridIndex:
    def __init__(
        self,
        embed: EmbedFn | None = None,
        persist_dir: str | None = None,
        collection: str = "documents",
    ):
        try:
            import chromadb
            from rank_bm25 import BM25Okapi  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "Search extras not installed: pip install -r requirements-search.txt"
            ) from exc

        self._embed = embed or _default_embedder()
        client = (
            chromadb.PersistentClient(path=persist_dir)
            if persist_dir
            else chromadb.EphemeralClient()
        )
        self._collection = client.get_or_create_collection(
            name=collection, embedding_function=None, metadata={"hnsw:space": "cosine"}
        )
        self._chunks: list[Chunk] = []
        self._bm25 = None

    def add(self, chunks: Sequence[Chunk]) -> None:
        if not chunks:
            return
        from rank_bm25 import BM25Okapi

        texts = [c.text for c in chunks]
        self._collection.upsert(
            ids=[c.id for c in chunks],
            documents=texts,
            embeddings=self._embed(texts),
            metadatas=[c.metadata or {"_": ""} for c in chunks],
        )
        known = {c.id for c in chunks}
        self._chunks = [c for c in self._chunks if c.id not in known] + list(chunks)
        self._bm25 = BM25Okapi([tokenize(c.text) for c in self._chunks])

    def search(self, query: str, k: int = 5, rrf_k: int = 60) -> list[SearchHit]:
        if not self._chunks or self._bm25 is None:
            return []
        pool = min(len(self._chunks), max(k * 4, 10))

        bm25_scores = self._bm25.get_scores(tokenize(query))
        bm25_order = sorted(range(len(self._chunks)), key=lambda i: bm25_scores[i], reverse=True)
        bm25_ranks = {
            self._chunks[i].id: r for r, i in enumerate(bm25_order[:pool]) if bm25_scores[i] > 0
        }

        result = self._collection.query(query_embeddings=self._embed([query]), n_results=pool)
        vector_ranks = {cid: r for r, cid in enumerate(result["ids"][0])}

        by_id = {c.id: c for c in self._chunks}
        fused: list[SearchHit] = []
        for cid in set(bm25_ranks) | set(vector_ranks):
            if cid not in by_id:
                continue
            score = sum(
                1.0 / (rrf_k + r + 1)
                for r in (bm25_ranks.get(cid), vector_ranks.get(cid))
                if r is not None
            )
            fused.append(SearchHit(by_id[cid], score, bm25_ranks.get(cid), vector_ranks.get(cid)))
        fused.sort(key=lambda h: h.score, reverse=True)
        return fused[:k]
