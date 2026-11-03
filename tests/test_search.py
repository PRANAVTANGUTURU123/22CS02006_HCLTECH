"""Optional search module. Skipped automatically if requirements-search.txt isn't installed."""

import hashlib
import math

import pytest

from backend.app.search.documents import Chunk, chunk_text


def test_chunk_text_respects_size_and_overlap():
    text = " ".join(f"word{i}" for i in range(500))
    chunks = chunk_text(text, chunk_size=200, overlap=40)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
    assert chunks[0].split()[-1] in chunks[1]


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic bag-of-words hash embedding, so tests don't download a model."""
    out = []
    for t in texts:
        v = [0.0] * 64
        for tok in t.lower().split():
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 64] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def test_hybrid_search_ranks_relevant_chunk_first():
    pytest.importorskip("chromadb")
    pytest.importorskip("rank_bm25")
    from backend.app.search.hybrid import HybridIndex

    index = HybridIndex(embed=_fake_embed, collection="test")
    index.add(
        [
            Chunk("a", "invoices are paid within thirty days", {"page": 1}),
            Chunk("b", "the cafeteria opens at nine", {"page": 2}),
            Chunk("c", "refund policy for damaged goods", {"page": 3}),
        ]
    )
    hits = index.search("when are invoices paid", k=2)
    assert hits[0].chunk.id == "a"
    assert hits[0].bm25_rank == 0


def test_load_pdf_chunks(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    from backend.app.search.documents import load_pdf_chunks

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    path = tmp_path / "blank.pdf"
    with path.open("wb") as f:
        writer.write(f)
    assert load_pdf_chunks(path) == []
