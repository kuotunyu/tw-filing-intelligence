"""Structure-aware chunking: the F1 counterpart to fixed-window chunking.

Three rules, each aimed at a specific failure the fixed chunker exhibits on
filings:

1. **Never cross a section boundary.** A chunk containing the tail of the risk
   factors and the head of the dividend policy retrieves for both and answers
   neither.
2. **Never split a table or a figure caption.** Half a table is worse than no
   table, because the surviving numbers still look authoritative.
3. **Carry the heading path in the text.** Retrieval sees "本公司主要銷售地區"
   without knowing it sits under 營運概況 → 市場分析; the reader of the answer
   needs that context too, and so does the citation.

Every chunk keeps per-page bounding boxes, so a chunk that legitimately spans two
pages cites both -- which is what the cross-page evidence metric measures.
"""

from __future__ import annotations

from dataclasses import dataclass

from twfi.parsing.types import BBox, Block, Chunk, PageRef, ParsedDocument

__all__ = ["StructureChunkConfig", "chunk_structure_aware"]

#: Blocks that must never be merged with neighbours or split apart.
_ATOMIC_KINDS = frozenset({"table", "figure", "caption"})


@dataclass(frozen=True, slots=True)
class StructureChunkConfig:
    """Chunking limits. Tunable on the development split only (protocol 1.3)."""

    max_chars: int = 1200
    min_chars: int = 200
    include_heading_prefix: bool = True
    heading_separator: str = " > "
    #: Whether an undersized chunk may fold into a *sibling* section, not only into its own.
    #:
    #: Measured on the corpus: 2,833 of 2,839 chunks under 50 characters were blocked from
    #: merging by one condition -- the preceding chunk's section path differed. That is
    #: structural, not incidental: :func:`chunk_structure_aware` flushes on every section
    #: change, so consecutive output chunks almost never share a section, and the reflow could
    #: only ever reach chunks that a length limit had split. It fired 79 to 293 times per
    #: filing while leaving 29% of chunks under 50 characters.
    #:
    #: Siblings rather than "any neighbour", because rule 1 -- do not merge across sections --
    #: exists to stop unrelated content being welded together, and 「柒、一」 with 「柒、二」 is not
    #: that. 「柒」 with 「捌」 would be. A merged chunk's section path becomes the shared parent,
    #: so its provenance says what it actually spans.
    merge_siblings: bool = True

    def __post_init__(self) -> None:
        if self.max_chars <= 0:
            raise ValueError("max_chars must be positive")
        if not 0 <= self.min_chars <= self.max_chars:
            raise ValueError("min_chars must be in [0, max_chars]")


def _refs_for(blocks: list[Block]) -> tuple[PageRef, ...]:
    """One reference per page, covering every block this chunk used on that page."""
    per_page: dict[int, BBox] = {}
    for block in blocks:
        existing = per_page.get(block.page)
        per_page[block.page] = block.bbox if existing is None else existing.union(block.bbox)
    return tuple(PageRef(page=page, bbox=bbox) for page, bbox in sorted(per_page.items()))


def _build(
    document: ParsedDocument,
    blocks: list[Block],
    index: int,
    config: StructureChunkConfig,
) -> Chunk:
    section_path = blocks[0].section_path
    body = "\n".join(block.text for block in blocks).strip()
    if config.include_heading_prefix and section_path:
        prefix = config.heading_separator.join(section_path)
        body = f"{prefix}\n{body}"
    return Chunk(
        chunk_id=f"{document.doc_id}:struct:{index:05d}",
        doc_id=document.doc_id,
        text=body,
        refs=_refs_for(blocks),
        section_path=section_path,
        kinds=tuple(dict.fromkeys(block.kind for block in blocks)),
        parser=document.parser,
    )


def chunk_structure_aware(
    document: ParsedDocument, config: StructureChunkConfig | None = None
) -> list[Chunk]:
    """Chunk along the document's own structure rather than at fixed offsets.

    Headings are attached to the content that follows them instead of becoming
    chunks of their own: a bare heading retrieves well and answers nothing.
    """
    config = config or StructureChunkConfig()

    chunks: list[Chunk] = []
    current: list[Block] = []
    current_section: tuple[str, ...] | None = None
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if current:
            chunks.append(_build(document, current, len(chunks), config))
        current = []
        current_chars = 0

    for block in document.content_blocks():
        if block.kind == "heading":
            # A heading starts a new chunk; its own text arrives via section_path.
            flush()
            current_section = block.section_path
            continue

        if block.section_path != current_section:
            flush()
            current_section = block.section_path

        if block.kind in _ATOMIC_KINDS:
            flush()
            current = [block]
            current_chars = len(block.text)
            flush()
            continue

        if current_chars and current_chars + len(block.text) > config.max_chars:
            flush()

        current.append(block)
        current_chars += len(block.text)

    flush()

    return _merge_undersized(document, chunks, config)


def _merge_undersized(
    document: ParsedDocument, chunks: list[Chunk], config: StructureChunkConfig
) -> list[Chunk]:
    """Fold a too-small chunk into its neighbour when the two sections may be joined.

    Short chunks are a real problem for lexical retrieval -- a two-sentence chunk has too
    little signal to rank -- and this corpus is full of them: 29% of candidate chunks were
    under 50 characters. The first version only merged chunks with an *identical* section
    path, which sounded like rule 1 and was not: `chunk_structure_aware` flushes on every
    section change, so consecutive chunks almost never share a path, and 2,833 of those 2,839
    tiny chunks were blocked by exactly that condition. The reflow could only ever reach
    chunks a length limit had split.

    So the condition is now "may these sections be joined" rather than "are they the same",
    and :func:`_mergeable_sections` decides it. Atomic blocks are still never touched.
    """
    if config.min_chars == 0:
        return chunks

    merged: list[Chunk] = []
    for chunk in chunks:
        atomic = bool(set(chunk.kinds) & _ATOMIC_KINDS)
        if (
            merged
            and not atomic
            and chunk.char_count < config.min_chars
            and not set(merged[-1].kinds) & _ATOMIC_KINDS
            and _mergeable_sections(merged[-1].section_path, chunk.section_path, config) is not None
        ):
            previous = merged.pop()
            # The merged chunk's section path is what the two have in common, so a chunk
            # spanning 「柒、一」 and 「柒、二」 reports 「柒」 rather than claiming to be
            # one of them.
            shared = _mergeable_sections(previous.section_path, chunk.section_path, config)
            assert shared is not None
            body = chunk.text
            if config.include_heading_prefix and chunk.section_path:
                prefix = config.heading_separator.join(chunk.section_path) + "\n"
                body = body.removeprefix(prefix)
            merged.append(
                Chunk(
                    chunk_id=previous.chunk_id,
                    doc_id=previous.doc_id,
                    text=f"{previous.text}\n{body}".strip(),
                    refs=_dedupe_refs(previous.refs + chunk.refs),
                    section_path=shared,
                    kinds=tuple(dict.fromkeys(previous.kinds + chunk.kinds)),
                    parser=previous.parser,
                )
            )
            continue
        merged.append(chunk)

    return [
        Chunk(
            chunk_id=f"{document.doc_id}:struct:{index:05d}",
            doc_id=chunk.doc_id,
            text=chunk.text,
            refs=chunk.refs,
            section_path=chunk.section_path,
            kinds=chunk.kinds,
            parser=chunk.parser,
        )
        for index, chunk in enumerate(merged)
    ]


def _mergeable_sections(
    left: tuple[str, ...], right: tuple[str, ...], config: StructureChunkConfig
) -> tuple[str, ...] | None:
    """The section path a merged chunk should carry, or ``None`` if they must not merge.

    Identical paths merge and keep the path. With ``merge_siblings``, two paths sharing a
    parent merge and take the parent -- so the result never claims to be a section it only
    partly covers. Anything else refuses: 「柒」 and 「捌」 are different subjects and welding them
    together is what rule 1 exists to prevent.

    A pair of empty paths counts as identical rather than as siblings, so content outside any
    heading still consolidates.
    """
    if left == right:
        return left
    if not config.merge_siblings:
        return None
    # Siblings: same parent, both actually inside a section. Two top-level sections have the
    # empty tuple as their parent, and merging those would be merging 「柒」 into 「捌」.
    if len(left) == len(right) >= 2 and left[:-1] == right[:-1]:
        return left[:-1]
    return None


def _dedupe_refs(refs: tuple[PageRef, ...]) -> tuple[PageRef, ...]:
    per_page: dict[int, BBox] = {}
    for ref in refs:
        existing = per_page.get(ref.page)
        per_page[ref.page] = ref.bbox if existing is None else existing.union(ref.bbox)
    return tuple(PageRef(page=page, bbox=bbox) for page, bbox in sorted(per_page.items()))
