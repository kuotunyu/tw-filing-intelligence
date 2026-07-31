"""An index that cannot say where it came from cannot support a claim.

No GPU and no model here: everything except the encoder call itself is pure, and these tests
run in the default CPU-only environment. What they pin down is the refusal behaviour -- the
cases where a vector set must not load rather than load and quietly retrieve nonsense.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from twfi.index.embeddings import (
    DEFAULT_MODEL,
    EmbeddingConfig,
    EmbeddingManifest,
    batches,
    load_vectors,
    save_vectors,
    utc_now,
)


def manifest(rows: int = 4, dimension: int = 8, **overrides: object) -> EmbeddingManifest:
    base: dict[str, object] = {
        "parser": "pymupdf-plain",
        "rows": rows,
        "dimension": dimension,
        "config": EmbeddingConfig(dimension=dimension),
        "built_at": utc_now(),
        "documents": ("2412-FY2023-AR",),
    }
    base.update(overrides)
    return EmbeddingManifest(**base)  # type: ignore[arg-type]


def vectors(rows: int = 4, dimension: int = 8) -> np.ndarray:
    return np.ones((rows, dimension), dtype=np.float32)


# ------------------------------------------------------------------------ config


def test_the_default_model_is_the_declared_one() -> None:
    """Protocol 2.2 names bge-m3; a different default here would silently diverge from it."""
    assert DEFAULT_MODEL == "BAAI/bge-m3"


@pytest.mark.parametrize(
    ("field", "value"),
    [("dimension", 0), ("batch_size", 0), ("max_length", 0), ("dimension", -1)],
)
def test_a_nonsense_config_is_rejected(field: str, value: int) -> None:
    with pytest.raises(ValueError):
        EmbeddingConfig(**{field: value})  # type: ignore[arg-type]


def test_vectors_are_normalised_by_default() -> None:
    """Cosine similarity becomes a dot product, so the query path does no extra work."""
    assert EmbeddingConfig().normalise is True


# ----------------------------------------------------------------------- batching


def test_batching_covers_every_item_exactly_once() -> None:
    items = [str(n) for n in range(23)]
    seen = [item for batch in batches(items, 5) for item in batch]
    assert seen == items


def test_the_last_batch_may_be_short() -> None:
    assert [len(batch) for batch in batches(["a"] * 7, 3)] == [3, 3, 1]


def test_an_empty_sequence_yields_no_batches() -> None:
    assert list(batches([], 4)) == []


def test_a_zero_batch_size_raises_rather_than_looping_forever() -> None:
    with pytest.raises(ValueError, match="positive"):
        list(batches(["a"], 0))


# -------------------------------------------------------------- save and refuse


def test_saving_writes_both_files_together(tmp_path: Path) -> None:
    """A .npy without a manifest cannot be checked against anything."""
    vector_path, manifest_path = save_vectors(tmp_path / "idx", vectors(), manifest())
    assert vector_path.is_file()
    assert manifest_path.is_file()


def test_a_row_count_disagreement_is_refused_at_save(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="manifest says 4 rows"):
        save_vectors(tmp_path / "idx", vectors(rows=3), manifest(rows=4))


def test_a_dimension_disagreement_is_refused_at_save(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="dimension"):
        save_vectors(tmp_path / "idx", vectors(dimension=6), manifest(dimension=8))


def test_a_saved_index_round_trips(tmp_path: Path) -> None:
    original = np.random.default_rng(0).normal(size=(5, 8)).astype(np.float32)
    save_vectors(tmp_path / "idx", original, manifest(rows=5))
    loaded, payload = load_vectors(tmp_path / "idx")
    assert np.allclose(loaded, original)
    assert payload["parser"] == "pymupdf-plain"


def test_half_an_index_is_not_an_index(tmp_path: Path) -> None:
    directory = tmp_path / "idx"
    save_vectors(directory, vectors(), manifest())
    (directory / "manifest.json").unlink()
    with pytest.raises(FileNotFoundError, match="rebuild"):
        load_vectors(directory)


def test_a_vector_file_that_outgrew_its_manifest_is_refused(tmp_path: Path) -> None:
    """The failure this prevents looks like bad retrieval, which is what F2 measures."""
    directory = tmp_path / "idx"
    save_vectors(directory, vectors(rows=4), manifest(rows=4))
    np.save(directory / "vectors.npy", vectors(rows=9))
    with pytest.raises(ValueError, match="different chunking"):
        load_vectors(directory)


def test_a_stale_index_is_refused_against_the_current_corpus(tmp_path: Path) -> None:
    directory = tmp_path / "idx"
    save_vectors(directory, vectors(rows=4), manifest(rows=4))
    with pytest.raises(ValueError, match="rebuild rather than searching a stale index"):
        load_vectors(directory, expect_rows=10)


def test_matching_expect_rows_loads(tmp_path: Path) -> None:
    directory = tmp_path / "idx"
    save_vectors(directory, vectors(rows=4), manifest(rows=4))
    loaded, _ = load_vectors(directory, expect_rows=4)
    assert loaded.shape == (4, 8)


# ------------------------------------------------------------------- provenance


def test_the_manifest_records_what_produced_the_vectors() -> None:
    payload = manifest(model_revision="abc123").to_json()
    assert payload["config"]["model"] == DEFAULT_MODEL
    assert payload["config"]["dtype"] == "float16"
    assert payload["model_revision"] == "abc123"
    assert payload["parser"] == "pymupdf-plain"


def test_chunk_ids_are_summarised_not_dumped() -> None:
    """The full list is large and the row count is the real check; a head aids eyeballing."""
    payload = manifest(chunk_ids=tuple(f"c{n}" for n in range(50))).to_json()
    assert payload["chunk_ids_head"] == ["c0", "c1", "c2", "c3", "c4"]


def test_a_manifest_without_chunk_ids_omits_the_head() -> None:
    assert "chunk_ids_head" not in manifest().to_json()


def test_built_at_is_utc_to_the_second() -> None:
    stamp = utc_now()
    assert stamp.endswith("+00:00")
    assert "." not in stamp, "microseconds add noise without adding information"
