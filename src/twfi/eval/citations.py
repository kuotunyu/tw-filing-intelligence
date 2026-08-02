"""Grade prediction citations against the evidence they claim to use.

Protocol 3.4 makes citation validity a hard gate.  The runner therefore grades a citation
while it still has the retrieved passages, structured-route provenance, and chart crop in
hand.  The later result verifier deliberately trusts only this boolean; it recomputes the
aggregate but does not create a second, subtly different citation grader.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from twfi.chart.crop_answer import ChartAnswer
from twfi.eval.gold import GoldRecord
from twfi.index.retrieve import Hit
from twfi.numeric.route import NumericAnswer
from twfi.parsing.normalise import normalise

__all__ = ["CitationVerdict", "CitationGrader", "bbox_iou"]

_WHITESPACE = re.compile(r"\s+")
_SEPARATORS = re.compile(r"[,\s]")
_FIGURE = re.compile(r"-?\d[\d,]*(?:\.\d+)?%?")
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaffA-Za-z]{2,}")
_SOURCE = re.compile(r"\[(?P<kind>[a-z_]+):(?P<ref>[^\]]+)\]")
_ROW = re.compile(r"^(?P<doc>[A-Za-z0-9-]+)\|p(?P<page>\d+)\|(?P<row>[^|]+)\|(?P<column>.+)$")
_CONNECTIVES = frozenset(
    {"分別", "是多少", "以及", "單位", "單位為", "金額", "增加了多少", "無法回答"}
)
_UNIT_ALIAS = str.maketrans({"仟": "千", "臺": "台"})


@dataclass(frozen=True, slots=True)
class CitationVerdict:
    """One per-answer verdict; ``None`` means a refusal has no citation denominator."""

    valid: bool | None
    detail: str
    kind: str


def bbox_iou(
    left: tuple[float, float, float, float], right: tuple[float, float, float, float]
) -> float:
    """Intersection over union for two PDF-space boxes."""
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


class CitationGrader:
    """Resolve citations against the acquired filing paths, caching page reads."""

    def __init__(self, documents: Mapping[str, Path]) -> None:
        self._documents = dict(documents)
        self._page_sizes: dict[tuple[str, int], tuple[float, float]] = {}
        self._page_words: dict[
            tuple[str, int], tuple[tuple[float, float, float, float, str], ...]
        ] = {}

    def grade(
        self,
        *,
        record: GoldRecord,
        predicted: str,
        cited: Sequence[int],
        passages: Sequence[Hit],
        refused: bool,
        numeric: NumericAnswer | None = None,
        chart: ChartAnswer | None = None,
    ) -> CitationVerdict:
        """Grade the route that actually supplied the answer."""
        if refused:
            return CitationVerdict(None, "refusal: citation not applicable", "refusal")
        if numeric is not None and numeric.ok:
            return self._grade_numeric(record, predicted, numeric)
        if chart is not None and chart.ok:
            return self._grade_chart(record, predicted, chart)
        return self._grade_passages(predicted, cited, passages)

    def _grade_passages(
        self, predicted: str, cited: Sequence[int], passages: Sequence[Hit]
    ) -> CitationVerdict:
        if not cited:
            return CitationVerdict(False, "answered with no citation", "page")
        invalid = sorted({index for index in cited if not 1 <= index <= len(passages)})
        if invalid:
            return CitationVerdict(
                False,
                f"citation index out of range: {invalid}; only {len(passages)} passage(s)",
                "page",
            )
        selected = [passages[index - 1] for index in cited]
        for hit in selected:
            if not hit.doc_id or not hit.pages:
                return CitationVerdict(False, "citation has no document/page", "page")
            for page in hit.pages:
                if self._page_size(hit.doc_id, page) is None:
                    return CitationVerdict(
                        False, f"citation does not resolve: {hit.doc_id} page {page}", "page"
                    )
        evidence = "\n".join(hit.text for hit in selected)
        if not _supports(predicted, evidence):
            return CitationVerdict(False, "cited passage(s) do not contain the answer span", "page")
        return CitationVerdict(True, f"{len(selected)} page citation(s) resolve", "page")

    def _grade_numeric(
        self, record: GoldRecord, predicted: str, numeric: NumericAnswer
    ) -> CitationVerdict:
        if not numeric.source_refs:
            return CitationVerdict(
                False, "numeric answer has no structured row citation", "sql_row"
            )
        parsed: list[tuple[str, str, re.Match[str] | None]] = []
        for citation in numeric.source_refs:
            match = _SOURCE.search(citation)
            if match is None:
                return CitationVerdict(
                    False, f"unparseable structured citation: {citation}", "sql_row"
                )
            ref = match.group("ref")
            row = _ROW.match(ref)
            if (
                row is not None
                and self._page_size(row.group("doc"), int(row.group("page"))) is None
            ):
                return CitationVerdict(False, f"structured row does not resolve: {ref}", "sql_row")
            parsed.append((match.group("kind"), ref, row))

        expected = record.structured_source_key
        if expected is not None and not self._structured_rows_match(
            record, expected.row_key, parsed
        ):
            return CitationVerdict(
                False,
                f"structured row citation does not match registered row {expected.row_key}",
                "sql_row",
            )
        if record.derived_from:
            joined = " ".join(numeric.operands)
            missing = [value for value in record.derived_from if not _supports(value, joined)]
            if missing:
                return CitationVerdict(False, f"citation operands missing {missing}", "sql_row")
        elif not _supports(predicted, numeric.as_text()):
            return CitationVerdict(False, "structured value does not support the answer", "sql_row")
        return CitationVerdict(True, f"{len(parsed)} structured row citation(s) resolve", "sql_row")

    def _structured_rows_match(
        self,
        record: GoldRecord,
        expected: str,
        parsed: Sequence[tuple[str, str, re.Match[str] | None]],
    ) -> bool:
        if any(ref == expected for _kind, ref, _row in parsed):
            return True
        expected_row = _ROW.match(expected)
        rows = [row for _kind, _ref, row in parsed if row is not None]
        if expected_row is None or not rows:
            return False
        if any(
            row.group("doc") != expected_row.group("doc")
            or row.group("page") != expected_row.group("page")
            for row in rows
        ):
            return False
        if record.derived_from:
            return len(rows) >= len(record.derived_from)
        return all(
            row.group("row") == expected_row.group("row")
            and (
                row.group("column") in expected_row.group("column")
                or expected_row.group("column") in row.group("column")
            )
            for row in rows
        )

    def _grade_chart(
        self, record: GoldRecord, predicted: str, chart: ChartAnswer
    ) -> CitationVerdict:
        if chart.provenance != "crop_pixels":
            return CitationVerdict(False, "chart value did not come from crop pixels", "chart_crop")
        size = self._page_size(chart.doc_id, chart.crop_page)
        if size is None:
            return CitationVerdict(
                False,
                f"chart citation does not resolve: {chart.doc_id} page {chart.crop_page}",
                "chart_crop",
            )
        x0, y0, x1, y1 = chart.bbox
        if x1 <= x0 or y1 <= y0 or x0 < 0 or y0 < 0 or x1 > size[0] or y1 > size[1]:
            return CitationVerdict(False, "chart bbox lies outside the cited page", "chart_crop")
        same_page = [box for box in record.bbox if box.page == chart.crop_page]
        if same_page and max(bbox_iou(chart.bbox, box.bbox) for box in same_page) < 0.3:
            return CitationVerdict(
                False, "chart bbox IoU with every gold bbox is below 0.3", "chart_crop"
            )
        words = self._words_in(chart.doc_id, chart.crop_page, chart.bbox)
        visible_evidence = " ".join((*words, chart.value))
        if not chart.value.strip():
            return CitationVerdict(False, "chart crop produced no structured value", "chart_crop")
        if not _supports(predicted, visible_evidence):
            return CitationVerdict(
                False, "chart crop does not contain the answer labels", "chart_crop"
            )
        return CitationVerdict(
            True, "chart page, bbox and visible answer labels resolve", "chart_crop"
        )

    def _page_size(self, doc_id: str, page: int) -> tuple[float, float] | None:
        key = (doc_id, page)
        if key in self._page_sizes:
            return self._page_sizes[key]
        path = self._documents.get(doc_id)
        if path is None or not path.is_file() or page < 1:
            return None
        try:
            with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
                if page > document.page_count:
                    return None
                rect = document.load_page(page - 1).rect
                size = (float(rect.width), float(rect.height))
        except (OSError, RuntimeError, ValueError):
            return None
        self._page_sizes[key] = size
        return size

    def _words_in(
        self, doc_id: str, page: int, bbox: tuple[float, float, float, float]
    ) -> tuple[str, ...]:
        key = (doc_id, page)
        cached = self._page_words.get(key)
        if cached is None:
            path = self._documents.get(doc_id)
            if path is None:
                return ()
            try:
                with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
                    raw: Sequence[Sequence[Any]] = document.load_page(page - 1).get_text("words")
                    cached = tuple(
                        (float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4])) for w in raw
                    )
            except (OSError, RuntimeError, ValueError, IndexError):
                return ()
            self._page_words[key] = cached
        return tuple(word[4] for word in cached if _inside(word[:4], bbox))


def _inside(
    word: tuple[float, float, float, float], box: tuple[float, float, float, float]
) -> bool:
    tolerance = 0.5
    return (
        word[0] >= box[0] - tolerance
        and word[1] >= box[1] - tolerance
        and word[2] <= box[2] + tolerance
        and word[3] <= box[3] + tolerance
    )


def _flat(text: str) -> str:
    return _WHITESPACE.sub("", normalise(text)).translate(_UNIT_ALIAS)


def _supports(claim: str, evidence: str) -> bool:
    wanted = _flat(claim)
    seen = _flat(evidence)
    if not wanted:
        return False
    if wanted in seen or _SEPARATORS.sub("", wanted) in _SEPARATORS.sub("", seen):
        return True
    figures = [value for value in _FIGURE.findall(wanted) if len(value) >= 2]
    terms = [term for term in _CJK.findall(wanted) if term not in _CONNECTIVES]
    atoms = [*figures, *terms]
    return bool(atoms) and all(
        atom in seen or _SEPARATORS.sub("", atom) in _SEPARATORS.sub("", seen) for atom in atoms
    )
