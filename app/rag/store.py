"""Tiny RAG layer: Markdown chunker + in-memory cosine-similarity index.

KB files are Markdown with a simple `key: value` front-matter block. Each `## ` section
becomes one chunk; front-matter keys become chunk metadata used for filtering.
The index lives in kb/index/chunks.json + vectors.npy (committed, so the server never re-embeds).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def query_text(q: str) -> str:
    """gemini-embedding-2 task prefix for a question-answering query."""
    return f"task: question answering | query: {q}"


def document_text(title: str, text: str) -> str:
    """gemini-embedding-2 document format for asymmetric retrieval."""
    return f"title: {title or 'none'} | text: {text}"


@dataclass
class Chunk:
    id: str
    title: str  # "<doc title> — <section heading>"
    text: str
    meta: dict = field(default_factory=dict)

    def embed_input(self) -> str:
        return document_text(self.title, self.text)


def parse_front_matter(raw: str) -> tuple[dict, str]:
    m = FRONT_MATTER.match(raw)
    if not m:
        return {}, raw
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            v = v.strip()
            # comma-separated lists for tag fields, e.g. "stage: sowing, vegetative"
            meta[k.strip()] = [s.strip() for s in v.split(",")] if k.strip() in LIST_KEYS else v
    return meta, raw[m.end():]


LIST_KEYS = {"stage", "topic", "crops"}


def chunk_markdown(raw: str, source: str = "") -> list[Chunk]:
    """Split a KB file at `## ` headings. Section-level front matter can be given as an
    HTML comment right under the heading, e.g. `<!-- stage: flowering; topic: irrigation -->`."""
    meta, body = parse_front_matter(raw)
    doc_id = meta.get("id") or Path(source).stem
    doc_title = meta.get("title") or doc_id
    # Keep the H1 intro paragraph as its own "overview" chunk
    parts = re.split(r"^## +", body, flags=re.M)
    chunks: list[Chunk] = []
    intro = re.sub(r"^# .*\n", "", parts[0]).strip()
    if intro:
        chunks.append(Chunk(f"{doc_id}#overview", f"{doc_title} — overview", intro, dict(meta, section="overview")))
    for i, part in enumerate(parts[1:], start=1):
        heading, _, text = part.partition("\n")
        heading = heading.strip()
        text = text.strip()
        sec_meta = dict(meta, section=heading)
        m = re.match(r"<!--(.*?)-->\s*", text, re.S)
        if m:
            for pair in m.group(1).split(";"):
                if ":" in pair:
                    k, v = pair.split(":", 1)
                    k, v = k.strip(), v.strip()
                    sec_meta[k] = [s.strip() for s in v.split(",")] if k in LIST_KEYS else v
            text = text[m.end():].strip()
        if not text:
            continue
        chunks.append(Chunk(f"{doc_id}#{i}", f"{doc_title} — {heading}", text, sec_meta))
    return chunks


def load_kb_dir(root: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(root.rglob("*.md")):
        chunks.extend(chunk_markdown(path.read_text(encoding="utf-8"), str(path)))
    return chunks


def _matches(meta: dict, filters: dict) -> bool:
    """A filter value matches if equal, or contained in a list-valued meta field.
    Chunks without the key (e.g. general crop info with no stage) are kept."""
    for k, want in filters.items():
        if want is None:
            continue
        have = meta.get(k)
        if have is None:
            continue
        if isinstance(have, list):
            if want not in have and "all" not in have:
                return False
        elif have != want:
            return False
    return True


def _required_ok(have, want) -> bool:
    """Exact match, except a chunk tagged 'all' (e.g. the crop calendar) satisfies any crop."""
    return have == want or have == "all"


@dataclass
class Hit:
    chunk: Chunk
    score: float


class VectorIndex:
    def __init__(self, chunks: list[Chunk], vectors: np.ndarray):
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors length mismatch")
        self.chunks = chunks
        v = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(v, axis=1, keepdims=True)
        self.vectors = v / np.where(norms == 0, 1, norms)

    def search(self, qvec, k: int = 4, filters: dict | None = None, require: dict | None = None) -> list[Hit]:
        """`filters`: soft (chunks missing the key pass). `require`: exact key must match."""
        q = np.asarray(qvec, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1)
        scores = self.vectors @ q
        order = np.argsort(-scores)
        hits = []
        for i in order:
            c = self.chunks[i]
            if require and any(not _required_ok(c.meta.get(key), val) for key, val in require.items()):
                continue
            if filters and not _matches(c.meta, filters):
                continue
            hits.append(Hit(c, float(scores[i])))
            if len(hits) >= k:
                break
        return hits

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "chunks.json").write_text(
            json.dumps([asdict(c) for c in self.chunks], ensure_ascii=False, indent=1), encoding="utf-8"
        )
        np.save(folder / "vectors.npy", self.vectors)

    @classmethod
    def load(cls, folder: Path) -> "VectorIndex":
        chunks = [Chunk(**c) for c in json.loads((folder / "chunks.json").read_text(encoding="utf-8"))]
        return cls(chunks, np.load(folder / "vectors.npy"))


def format_context(hits: list[Hit]) -> str:
    return "\n\n".join(f"[{i}] {h.chunk.title}\n{h.chunk.text}" for i, h in enumerate(hits, 1))
