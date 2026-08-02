-- Schema for the deterministic numeric route.
--
-- Every figure carries the four things that make it comparable and the three that
-- make it citable. Without unit and currency a number is not an answer; without
-- source_kind, source_url and source_ref an answer is not evidence.
--
-- source_kind is a first-class column rather than a loader detail so that "how much
-- do we trust this figure" is queryable. The study needs it: FY2024 statements come
-- from a 財務報告書, the current period comes from the TWSE OpenAPI, and anything
-- else was read out of a table by our own parser (DECISIONS D-010).

CREATE TABLE IF NOT EXISTS company (
    code             TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    -- 'general' or 'financial_holding'. A financial holding company files a different
    -- statement structure entirely -- 2882 has no 營業收入 line at all -- so the
    -- account vocabulary is per-schema and lookups must not cross the boundary.
    industry_schema  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fin_line_item (
    company_code     TEXT NOT NULL,
    period           TEXT NOT NULL,      -- FY2024, FY2024Q4
    statement        TEXT NOT NULL,      -- income | balance | ratio | monthly_revenue
    basis            TEXT NOT NULL,      -- consolidated | parent_only
    industry_schema  TEXT NOT NULL,
    account          TEXT NOT NULL,      -- verbatim, e.g. 營業收入 or 利息淨收益
    value            DECIMAL(38, 6),
    unit             TEXT,               -- 千元 | 百萬元 | 元 | % | 倍
    currency         TEXT,               -- TWD | USD | NULL when unstated
    -- False when the source stated an exception to its own unit, e.g.
    -- 「單位：新台幣仟元，惟每股盈餘為元」. The calculator refuses to combine a
    -- non-uniform figure rather than applying the headline scale to it.
    unit_is_uniform  BOOLEAN NOT NULL DEFAULT TRUE,
    unit_note        TEXT,
    source_kind      TEXT NOT NULL,      -- xbrl | openapi_current | extracted_table
                                         --   | extracted_text_row  (line stream, not grid)
    source_url       TEXT,
    source_ref       TEXT NOT NULL,      -- dataset id, or p102:r3:c1 for a table cell
    -- source_ref is part of identity: two pages in one filing may print the same account
    -- under different subsidiaries. Dropping it would make INSERT OR REPLACE silently keep
    -- the last page before the reader has a chance to reject the ambiguity.
    PRIMARY KEY (company_code, period, statement, basis, account, source_kind, source_ref)
);

CREATE INDEX IF NOT EXISTS fin_line_item_lookup
    ON fin_line_item (company_code, account, period);

-- What was loaded, so a figure can be traced back to the file it came from.
CREATE TABLE IF NOT EXISTS numeric_source (
    source_kind      TEXT NOT NULL,
    source_id        TEXT NOT NULL,      -- dataset_id or doc_id
    source_url       TEXT,
    sha256           TEXT,
    loaded_at        TEXT NOT NULL,
    rows_loaded      INTEGER NOT NULL,
    PRIMARY KEY (source_kind, source_id)
);
