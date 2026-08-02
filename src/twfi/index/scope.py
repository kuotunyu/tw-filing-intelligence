"""Resolve the companies explicitly named in a query to their usable filings."""

from __future__ import annotations

import re
from typing import Final

from twfi.protocol import COMPANIES, USABLE_DOCUMENTS

__all__ = ["company_document_scope"]

_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "1301": ("台塑", "台灣塑膠"),
    "2412": ("中華電信",),
    "2330": ("台積電", "台積公司", "台灣積體電路製造"),
    "2317": ("鴻海", "鴻海精密"),
    "2882": ("國泰金控", "國泰金"),
}


def company_document_scope(query: str) -> frozenset[str] | None:
    """Return usable document ids for companies named in ``query``.

    ``None`` deliberately differs from an empty set: it means that the query did not name a
    registered company, so callers retain their existing corpus-wide behaviour.  Multiple
    names form a union, which keeps future cross-company questions representable.
    """
    selected: set[str] = set()
    known_codes = {company.code for company in COMPANIES}
    for code, aliases in _ALIASES.items():
        code_named = re.search(rf"(?<!\d){re.escape(code)}(?!\d)", query) is not None
        if code in known_codes and (code_named or any(alias in query for alias in aliases)):
            selected.add(code)
    if not selected:
        return None
    return frozenset(
        document.doc_id for document in USABLE_DOCUMENTS if document.company_code in selected
    )
