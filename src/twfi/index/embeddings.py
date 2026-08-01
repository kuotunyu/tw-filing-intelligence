"""Dense vectors for the retrieval route, and the provenance that makes them re-derivable.

Two things this module refuses to do, both for the same reason -- an index whose origin is
unknown cannot support a claim:

* **Vectors are never written without a manifest.** Model, revision, dtype, device, parser,
  chunk count and dimension go in the same directory as the array. A `.npy` on its own
  cannot be checked against anything, and the study's whole argument is that its numbers
  are re-derivable.
* **A vector set that does not match the chunks is not loadable.** :func:`load_vectors`
  compares the row count and dimension against the manifest and raises rather than handing
  back an array that silently belongs to a previous chunking. That failure would surface as
  bad retrieval, which is exactly what F2 is trying to measure.

Both parsers get their own index. F0's fixed windows and F1-F7's layout-aware chunks are
different corpora -- 4,063 chunks against 9,890 over the eight usable filings -- and
pooling them would let the baseline retrieve from the candidate's chunking.

The torch import is deliberately inside :func:`embed_texts`. Everything else here is pure
and is tested without a GPU, offline, in the default CPU-only environment; only the call
that actually runs a model needs the optional ``models`` extra.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

__all__ = [
    "DEFAULT_MODEL",
    "VECTOR_MANIFEST",
    "EmbeddingConfig",
    "EmbeddingManifest",
    "batches",
    "chunk_text_digest",
    "embed_texts",
    "load_vectors",
    "save_vectors",
    "utc_now",
]

#: Protocol 2.2. Dense only -- bge-m3's sparse and colbert heads are not used, because BM25
#: is computed separately and mixing the two would confound F2's lexical/dense comparison.
DEFAULT_MODEL = "BAAI/bge-m3"


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    """How a vector set was produced. Every field lands in the manifest."""

    model: str = DEFAULT_MODEL
    #: 1024 for bge-m3. Recorded rather than assumed, so a wrong model is caught on load.
    dimension: int = 1024
    batch_size: int = 16
    #: bge-m3 accepts 8192, but filings chunk to ~800 characters and padding to the model
    #: maximum would spend most of the compute on padding.
    max_length: int = 1024
    dtype: Literal["float16", "float32"] = "float16"
    device: str = "cuda"
    #: Cosine similarity on normalised vectors is a dot product, which is what the search
    #: does. Normalising once at build time keeps it out of the query path.
    normalise: bool = True

    def __post_init__(self) -> None:
        if self.dimension <= 0:
            raise ValueError("dimension must be positive")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if self.max_length <= 0:
            raise ValueError("max_length must be positive")


@dataclass(frozen=True, slots=True)
class EmbeddingManifest:
    """What a vector file is, in enough detail to rebuild or reject it."""

    parser: str
    rows: int
    dimension: int
    config: EmbeddingConfig
    built_at: str
    documents: tuple[str, ...] = ()
    #: Resolved revision of the model weights, when the loader can report one.
    model_revision: str | None = None
    notes: str = ""
    chunk_ids: tuple[str, ...] = field(default_factory=tuple)
    #: SHA-256 of the exact chunk *text* these vectors were computed from. See
    #: :func:`chunk_text_digest` for why a row count and a few ids are not enough.
    chunk_text_sha256: str | None = None

    def to_json(self) -> dict[str, Any]:
        payload = {
            "parser": self.parser,
            "rows": self.rows,
            "dimension": self.dimension,
            "config": asdict(self.config),
            "built_at": self.built_at,
            "documents": list(self.documents),
            "model_revision": self.model_revision,
            "chunk_text_sha256": self.chunk_text_sha256,
            "notes": self.notes,
        }
        # Chunk ids are the expensive part of this file and only the first few are needed to
        # tell two builds apart at a glance; the row count is the actual check.
        if self.chunk_ids:
            payload["chunk_ids_head"] = list(self.chunk_ids[:5])
        return payload


def chunk_text_digest(texts: Sequence[str]) -> str:
    """SHA-256 over the chunk texts an index was built from, in order.

    Both index halves record this, and it exists because the cheaper checks do not catch the
    failure that actually happened. A chunker fix can change chunk *text* while leaving the chunk
    *count* and every chunk *id* untouched -- exactly what correcting the merged-chunk heading
    line did, rewriting 1,515 of 4,796 candidate chunks -- and a stale index then passes a row
    count, passes a chunk-id comparison, and returns confident numbers computed from text that no
    longer exists. Only the content answers that.

    Ordered rather than a set: the vectors are addressed by row position, so two chunk sets with
    the same texts in a different order are different indexes.
    """
    digest = hashlib.sha256()
    for text in texts:
        # Length-prefixed so that concatenation cannot be ambiguous: ["ab", "c"] and ["a", "bc"]
        # must not hash alike.
        encoded = text.encode("utf-8")
        digest.update(str(len(encoded)).encode("ascii"))
        digest.update(b"\0")
        digest.update(encoded)
    return digest.hexdigest()


def batches(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    """Split into fixed-size batches. Separate from the model call so it is testable.

    Raises:
        ValueError: If ``size`` is not positive -- a zero would loop forever.
    """
    if size <= 0:
        raise ValueError("batch size must be positive")
    for start in range(0, len(items), size):
        yield items[start : start + size]


def embed_texts(
    texts: Sequence[str],
    config: EmbeddingConfig | None = None,
    *,
    progress: Any = None,
) -> tuple[np.ndarray, str | None]:
    """Encode texts with the configured model. Returns ``(vectors, model revision)``.

    Uses the CLS embedding, which is what bge-m3's dense head is trained to produce; mean
    pooling over tokens is a different representation and would not match the model's own
    retrieval behaviour.

    Raises:
        RuntimeError: If the optional ``models`` extra is not installed.
    """
    settings = config or EmbeddingConfig()
    try:
        import torch
        from transformers import AutoModel, AutoTokenizer
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise RuntimeError(
            "embedding needs the optional models extra: uv sync --extra models"
        ) from exc

    tokenizer = AutoTokenizer.from_pretrained(settings.model)
    torch_dtype = torch.float16 if settings.dtype == "float16" else torch.float32
    model = AutoModel.from_pretrained(settings.model, dtype=torch_dtype)
    model = model.to(settings.device).eval()
    revision = getattr(getattr(model, "config", None), "_commit_hash", None)

    out: list[np.ndarray] = []
    done = 0
    with torch.inference_mode():
        for batch in batches(texts, settings.batch_size):
            encoded = tokenizer(
                list(batch),
                padding=True,
                truncation=True,
                max_length=settings.max_length,
                return_tensors="pt",
            ).to(settings.device)
            hidden = model(**encoded).last_hidden_state[:, 0]
            if settings.normalise:
                hidden = torch.nn.functional.normalize(hidden, p=2, dim=1)
            out.append(hidden.to(torch.float32).cpu().numpy())
            done += len(batch)
            if progress is not None:
                progress(done, len(texts))

    del model
    if settings.device.startswith("cuda"):
        import torch as _torch

        _torch.cuda.empty_cache()
    return np.vstack(out) if out else np.empty((0, settings.dimension), dtype=np.float32), revision


#: The dense half's manifest. Named apart from the lexical half's on purpose: both used to write
#: `manifest.json` into the same directory, so building BM25 after the vectors overwrote the
#: embedding manifest -- and with it the model revision, the dimension and the chunk ids that let
#: a stale index be rejected. What survived recorded `k1` and `b`, which describe the other index.
VECTOR_MANIFEST = "vectors.manifest.json"


def save_vectors(
    directory: Path,
    vectors: np.ndarray,
    manifest: EmbeddingManifest,
) -> tuple[Path, Path]:
    """Write ``vectors.npy`` and its manifest together, never one without the other."""
    if vectors.shape[0] != manifest.rows:
        raise ValueError(f"manifest says {manifest.rows} rows, array has {vectors.shape[0]}")
    if vectors.ndim != 2 or vectors.shape[1] != manifest.dimension:
        raise ValueError(f"manifest says dimension {manifest.dimension}, array is {vectors.shape}")
    directory.mkdir(parents=True, exist_ok=True)
    vector_path = directory / "vectors.npy"
    manifest_path = directory / VECTOR_MANIFEST
    np.save(vector_path, vectors)
    manifest_path.write_text(
        json.dumps(manifest.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return vector_path, manifest_path


def load_vectors(directory: Path, *, expect_rows: int | None = None) -> tuple[np.ndarray, Any]:
    """Load a vector set, refusing one that does not match its own manifest.

    Raises:
        FileNotFoundError: If either file is absent -- half an index is not an index.
        ValueError: If the array disagrees with the manifest, or with ``expect_rows``.
    """
    vector_path = directory / "vectors.npy"
    manifest_path = directory / VECTOR_MANIFEST
    for path in (vector_path, manifest_path):
        if not path.is_file():
            if path == manifest_path and (directory / "manifest.json").is_file():
                # An index from before the two halves stopped sharing a filename. Its embedding
                # manifest was overwritten by the BM25 build, so what it can prove about itself
                # is gone: rebuild rather than trust a vector set with no provenance.
                raise FileNotFoundError(
                    f"{path} is missing and {directory / 'manifest.json'} exists: this index "
                    "predates the split manifests and its embedding provenance was overwritten "
                    "by the lexical build; rebuild it"
                )
            raise FileNotFoundError(f"{path} is missing; rebuild the index")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    vectors = np.load(vector_path)
    if vectors.shape[0] != manifest.get("rows"):
        raise ValueError(
            f"{directory} holds {vectors.shape[0]} vectors but its manifest says "
            f"{manifest.get('rows')}; this index belongs to a different chunking"
        )
    if vectors.shape[1] != manifest.get("dimension"):
        raise ValueError(
            f"{directory} holds dimension {vectors.shape[1]}, manifest says "
            f"{manifest.get('dimension')}"
        )
    if expect_rows is not None and vectors.shape[0] != expect_rows:
        raise ValueError(
            f"{directory} holds {vectors.shape[0]} vectors but the corpus now has "
            f"{expect_rows} chunks; rebuild rather than searching a stale index"
        )
    return vectors, manifest


def utc_now() -> str:
    """Timestamp for a manifest, in UTC and to the second."""
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
