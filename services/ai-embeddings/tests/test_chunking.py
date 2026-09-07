"""Semantic chunker: split mechanics with a stubbed embedder."""

from unittest.mock import patch

import pytest

from core import chunking


def test_percentile_linear_interpolation():
    assert chunking._percentile([1.0], 95) == 1.0
    assert chunking._percentile([0.0, 1.0], 50) == 0.5
    assert chunking._percentile([0.0, 1.0, 2.0, 3.0], 95) == pytest.approx(2.85)


async def test_single_sentence_is_one_chunk():
    assert await chunking.semantic_chunks("Just one sentence") == ["Just one sentence"]
    assert await chunking.semantic_chunks("   ") == []


async def test_breaks_at_distance_spike():
    # 4 sentences -> 4 windows; vectors chosen so the 2->3 distance is the spike
    vectors = [[1.0, 0.0], [1.0, 0.1], [0.0, 1.0], [0.0, 1.0]]

    async def fake_embed(texts):
        assert len(texts) == 4
        return vectors

    with patch.object(chunking, "embed_texts", fake_embed):
        chunks = await chunking.semantic_chunks("One a. Two b. Three c. Four d.")
    assert chunks == ["One a. Two b.", "Three c. Four d."]
