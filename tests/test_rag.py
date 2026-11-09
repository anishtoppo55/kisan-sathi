import numpy as np

from app.rag.store import Chunk, VectorIndex, chunk_markdown, format_context, load_kb_dir, query_text

DOC = """---
id: demo
module: crop
crop: chana
title: Chana guide
---

# Chana
Intro text about chana.

## Irrigation
<!-- stage: vegetative, flowering; topic: irrigation -->
- One light irrigation at branching.

## Empty

## Harvest
<!-- stage: maturity -->
Harvest when pods dry.
"""


def test_chunker_keeps_metadata_and_sections():
    chunks = chunk_markdown(DOC, "kb/crops/demo.md")
    assert [c.id for c in chunks] == ["demo#overview", "demo#1", "demo#3"]
    irr = chunks[1]
    assert irr.title == "Chana guide — Irrigation"
    assert irr.meta["module"] == "crop" and irr.meta["crop"] == "chana"
    assert irr.meta["stage"] == ["vegetative", "flowering"] and irr.meta["topic"] == ["irrigation"]
    assert "<!--" not in irr.text and irr.text.startswith("- One light")
    assert "stage" not in chunks[0].meta  # overview has no stage


def test_query_and_document_prefixes():
    assert query_text("q") == "task: question answering | query: q"
    assert Chunk("i", "T", "x").embed_input() == "title: T | text: x"


def _index():
    chunks = [
        Chunk("a", "A", "a", {"module": "yojana"}),
        Chunk("b", "B", "b", {"module": "crop", "crop": "chana", "stage": ["flowering"]}),
        Chunk("c", "C", "c", {"module": "crop", "crop": "chana", "stage": ["sowing"]}),
        Chunk("d", "D", "d", {"module": "crop", "crop": "chana"}),  # no stage: general info
        Chunk("e", "E", "e", {"module": "crop", "crop": "wheat", "stage": ["all"]}),
    ]
    vecs = np.array([[1, 0, 0], [0.9, 0.1, 0], [0.8, 0.2, 0], [0.5, 0.5, 0], [0.95, 0.05, 0]], dtype=float)
    return VectorIndex(chunks, vecs)


def test_search_ranks_by_cosine():
    hits = _index().search([1, 0, 0], k=3)
    assert [h.chunk.id for h in hits] == ["a", "e", "b"]
    assert abs(hits[0].score - 1.0) < 1e-6


def test_search_require_and_soft_filters():
    idx = _index()
    hits = idx.search([1, 0, 0], k=5, require={"module": "crop", "crop": "chana"}, filters={"stage": "flowering"})
    assert [h.chunk.id for h in hits] == ["b", "d"]  # sowing chunk dropped, stage-less kept
    hits = idx.search([1, 0, 0], k=5, require={"module": "crop"}, filters={"stage": "maturity"})
    assert "e" in [h.chunk.id for h in hits]  # "all" matches any stage


def test_save_load_roundtrip(tmp_path):
    idx = _index()
    idx.save(tmp_path)
    again = VectorIndex.load(tmp_path)
    assert [c.id for c in again.chunks] == [c.id for c in idx.chunks]
    assert again.chunks[1].meta["stage"] == ["flowering"]
    assert np.allclose(again.vectors, idx.vectors)


def test_format_context_numbers_chunks():
    hits = _index().search([1, 0, 0], k=2)
    ctx = format_context(hits)
    assert ctx.startswith("[1] A\na") and "[2] E" in ctx


def test_real_kb_parses():
    from pathlib import Path
    chunks = load_kb_dir(Path(__file__).resolve().parents[1] / "kb")
    mods = {c.meta.get("module") for c in chunks}
    assert mods == {"yojana", "crop"}
    assert {c.meta.get("crop") for c in chunks if c.meta.get("module") == "crop"} == {"paddy", "chana", "wheat", "teora", "maize", "all"}
    cal = [c for c in chunks if c.meta.get("crop") == "all"]
    assert len(cal) >= 7 and any("November" in c.title for c in cal)


def test_calendar_chunk_matches_any_required_crop():
    chunks = [Chunk("cal", "Calendar — November", "x", {"module": "crop", "crop": "all"}),
              Chunk("w", "Wheat", "x", {"module": "crop", "crop": "wheat"}),
              Chunk("p", "Paddy", "x", {"module": "crop", "crop": "paddy"})]
    idx = VectorIndex(chunks, np.array([[1, 0], [0.9, 0.1], [0.8, 0.2]], dtype=float))
    assert [h.chunk.id for h in idx.search([1, 0], k=3, require={"module": "crop", "crop": "wheat"})] == ["cal", "w"]
    assert [h.chunk.id for h in idx.search([1, 0], k=3, require={"module": "crop"})] == ["cal", "w", "p"]
