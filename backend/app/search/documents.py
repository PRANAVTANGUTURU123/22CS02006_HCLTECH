"""PDF text extraction and chunking (pypdf)."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    """Split text into overlapping word-based chunks of about chunk_size characters."""
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        if current and length + len(word) + 1 > chunk_size:
            chunks.append(" ".join(current))
            tail: list[str] = []
            tail_len = 0
            for w in reversed(current):
                if tail_len + len(w) + 1 > overlap:
                    break
                tail.insert(0, w)
                tail_len += len(w) + 1
            current, length = tail, tail_len
        current.append(word)
        length += len(word) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks


def load_pdf_chunks(path: str | Path, chunk_size: int = 800, overlap: int = 120) -> list[Chunk]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ImportError("pypdf is not installed: pip install -r requirements-search.txt") from exc

    path = Path(path)
    reader = PdfReader(str(path))
    chunks: list[Chunk] = []
    for page_no, page in enumerate(reader.pages, start=1):
        for i, piece in enumerate(chunk_text(page.extract_text() or "", chunk_size, overlap)):
            chunks.append(
                Chunk(
                    id=f"{path.stem}-p{page_no}-c{i}",
                    text=piece,
                    metadata={"source": path.name, "page": page_no},
                )
            )
    return chunks
