"""Build the KB vector index: kb/**/*.md -> kb/index/{chunks.json, vectors.npy}.

Usage: env -u PYTHONPATH .venv/bin/python scripts/ingest.py
Re-run whenever a KB Markdown file changes, then commit kb/index/.
"""

import asyncio
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.clients.gemini import GeminiClient  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.rag.store import VectorIndex, load_kb_dir  # noqa: E402

KB_DIR = ROOT / "kb"
INDEX_DIR = KB_DIR / "index"
BATCH = 16


async def main() -> int:
    key = get_settings().gemini_api_key
    if not key:
        print("GEMINI_API_KEY missing")
        return 1
    chunks = load_kb_dir(KB_DIR)
    if not chunks:
        print("no KB chunks found")
        return 1
    g = GeminiClient(key.get_secret_value())
    vectors: list[list[float]] = []
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i + BATCH]
        vectors.extend(await g.embed([c.embed_input() for c in batch]))
        print(f"embedded {min(i + BATCH, len(chunks))}/{len(chunks)}")
    VectorIndex(chunks, np.array(vectors)).save(INDEX_DIR)
    by_module: dict[str, int] = {}
    for c in chunks:
        by_module[c.meta.get("module", "?")] = by_module.get(c.meta.get("module", "?"), 0) + 1
    print(f"saved {len(chunks)} chunks to {INDEX_DIR.relative_to(ROOT)} {by_module}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
