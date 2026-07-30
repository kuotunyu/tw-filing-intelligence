# IMPLEMENTATION PLAN — ⑤A TW Filing Intelligence Feasibility

`authored: 2026-07-31`　`plan_version: 1.0`

實作原則：**先寫 md，再照 md 實作**。每個 phase 有明確 deliverable 與
「完成條件（Definition of Done）」。任何偏離計畫的決定必須寫回
`docs/DECISIONS.md`，任何進度變化必須寫回 `docs/PROGRESS.md`。

---

## 0. 全局約束（每個 phase 都適用）

- 專案完全獨立：不 import／複製其他本機 repository，不 submodule／local path
  dependency／symlink，不共用 DB／cache／artifacts。
- 測試預設離線、CPU、不讀 `.env`、不動 `results/feasibility/`、coverage ≥ 85%。
- GPU 前先 `nvidia-smi`；有別的專案在用就先做 CPU 工作。
- 不 push、不 tag、不 deploy、不建 GitHub remote。
- 大檔（PDF／權重／index／cache／DuckDB）不進 git。

---

## Phase 表

| Phase | 名稱 | 需要 GPU | 需要連外 | 狀態 |
|---|---|---|---|---|
| P0 | Repo scaffold ＋ 規劃文件 ＋ 專案 skills | ✗ | ✗ | 進行中 |
| P1 | 資料來源探勘與 manifest 定義 | ✗ | ✅ | 未開始 |
| P2 | 資料取得 ＋ provenance ＋ SHA-256 驗證 | ✗ | ✅ | 未開始 |
| P3 | Parsing 層（baseline ＋ layout-aware） | ✗ | ✗ | 未開始 |
| P4 | 結構化數值層（DuckDB ＋ deterministic SQL） | ✗ | ✗ | 未開始 |
| P5 | Gold set 標註（DEV 15 ＋ LOCKED 36 ＋ 5 probes） | ✗ | ✗ | 未開始 |
| P6 | Retrieval ＋ rerank ＋ index artifacts | ✅ | ✗ | 未開始 |
| P7 | Chart route（caption index ＋ crop answering） | ✅ | ✗ | 未開始 |
| P8 | Router ＋ answer/citation contract | ✅ | ✗ | 未開始 |
| P9 | Eval harness ＋ metrics ＋ factor ladder | 部分 | ✗ | 未開始 |
| P10 | Freeze → locked run → gate → report | ✅ | ✗ | 未開始 |

---

## P0 — Repo scaffold ＋ 規劃文件

**Deliverable**

- `git init`（本機、`main`）、`.gitignore`、`LICENSE`(MIT)、`README.md`、`CLAUDE.md`
- `pyproject.toml`（uv-managed，Python 3.11）、`uv.lock`
- 目錄骨架：`src/twfi/{io,parsing,index,numeric,chart,router,answer,eval,telemetry}`、
  `tests/`、`scripts/`、`configs/`、`data/{manifests,evaluation}`、
  `results/feasibility/`、`docs/`
- 文件：`FEASIBILITY_PROTOCOL.md`(draft)、`IMPLEMENTATION_PLAN.md`、`DECISIONS.md`、
  `DATA_PROVENANCE.md`(骨架)、`THREAT_MODEL.md`、`PROGRESS.md`
- 專案層級 skills：`.claude/skills/`
- Tooling：ruff、mypy、pytest ＋ coverage gate 85%

**DoD**：`uv sync --extra dev` 成功；`uv run pytest` 綠燈；`ruff` / `mypy` 乾淨；
一個清楚的 commit。

---

## P1 — 資料來源探勘與 manifest 定義

**Deliverable**

- `src/twfi/io/http.py` — 單一出口的 HTTP client：allowlist host（`mops.twse.com.tw`、
  `doc.twse.com.tw`、`openapi.twse.com.tw`）、timeout、指數退避 retry、
  `>= 1.5s` 間隔的 rate limiter、明確 user-agent、最大下載大小上限（防無限下載）。
- `scripts/explore_sources.py` — 抓 `openapi.twse.com.tw/v1/swagger.json`，
  列出可用 endpoint 與欄位，落地成 `docs/reference/twse_openapi_endpoints.md`。
- 確認 MOPS 文件下載的**穩定公開路徑**（年報／財報 PDF、XBRL），寫進
  `docs/DATA_PROVENANCE.md`。若無穩定路徑 → 明確記錄，改走人工放置 fallback。
- `data/manifests/documents.yaml`、`data/manifests/structured.yaml` schema 定案
  ＋ `src/twfi/io/manifest.py` 的 pydantic model。

**DoD**：manifest schema 有 pydantic 驗證與離線測試；
allowlist 之外的 host 會被 client 拒絕（有測試）；探勘結果寫入 docs。

**風險**：MOPS 年報 PDF 的 URL 可能非決定性（含流水號）。
→ 對策：manifest 以 `(company, year, doc_type)` 為 key，`resolved_url` 與 `sha256`
在首次取得後寫入並鎖定；重建時直接用 `resolved_url`，失敗則走人工 fallback。

---

## P2 — 資料取得 ＋ provenance ＋ SHA-256

**Deliverable**

- `scripts/fetch_twse_openapi.py` — 拉結構化資料落地 `data/raw/structured/`，
  記錄 `retrieved_at`、HTTP status、SHA-256。
- `scripts/fetch_documents.py` — 取 PDF／XBRL；支援
  `--manual-dir data/raw/manual/`（人工放置 fallback，仍需通過 SHA-256 比對）。
- `scripts/verify_manifests.py` — 驗證每筆資料的 SHA-256、來源頁、公司、年度、
  文件類型、取得日期齊備。
- `docs/DATA_PROVENANCE.md` 填實。

**DoD**：7 份 PDF ＋ 結構化資料齊備且 hash 驗證通過；
`data/raw/` 未被 commit；provenance 完整；重跑腳本得到相同 hash。

---

## P3 — Parsing 層

**Deliverable**

- `src/twfi/parsing/baseline.py` — PyMuPDF `get_text()` ＋ 固定 chunk（800/100）。
- `src/twfi/parsing/layout.py` — dict-mode blocks → span 字級/字重統計 → heading 分群
  → section tree → reading order；輸出帶 `page`、`bbox`、`section_path` 的 block。
- `src/twfi/parsing/tables.py` — pdfplumber 表格 → typed `Table`（含 row/col header、
  單位標註偵測「單位：新台幣千元」、跨頁表接續）。
- `src/twfi/parsing/figures.py` — figure/chart region 偵測（image block ＋ 向量繪圖密度）
  → crop bbox（不在此階段呼叫 VLM）。
- `src/twfi/parsing/chunker.py` — structure-aware chunk（不跨 section、表格不切開、
  保留 heading 前綴）。

**DoD**：以測試時用 PyMuPDF 生成的合成 PDF fixture 覆蓋（heading 階層、跨頁表、
圖表區、括號負數、單位列）；兩個 parser 都能對真實 7 份 PDF 跑完並輸出統計
（頁數、block 數、表格數、figure 數）到 `results/runs/parse_stats.json`。

---

## P4 — 結構化數值層

**Deliverable**

- `src/twfi/numeric/schema.sql` — `company`、`document`、`fin_statement`、
  `fin_line_item(company, period, statement, basis, account, value, unit, currency,
  source_url, source_ref)`、`monthly_revenue`。
- `src/twfi/numeric/duckdb_store.py` — 從 OpenAPI／XBRL／已驗證表格載入；
  每一列都必須有 `source_url` 與 `source_ref`。
- `src/twfi/numeric/sql_tools.py` — **templated、參數化**的查詢集合
  （lookup / cross-period delta / ratio / growth）。**不允許 LLM 自由生成 SQL。**
- `src/twfi/numeric/calculator.py` — 回傳 `{value, unit, formula, operands[]}`，
  每個 operand 帶 source。

**DoD**：離線 fixture DuckDB 測試；單位／幣別／basis(合併 vs 個別) 不一致時
明確報錯而非默默計算；計算題輸出可讀 formula。

---

## P5 — Gold set 標註

**Deliverable**

- `data/evaluation/dev/gold.jsonl`（15 題）
- `data/evaluation/locked/gold.jsonl`（36 題，分布依 protocol §1.4）
- `data/evaluation/locked/probes.jsonl`（5 個 no-evidence probe）
- `src/twfi/eval/gold_schema.py` ＋ `scripts/validate_gold.py`
- `scripts/check_leakage.py` — DEV/LOCKED 公司不重疊、文件不重疊、
  `annotator == "human"`、無重複題目、題型分布符合 protocol。

**DoD**：schema 驗證通過；leakage check 通過；每題都能人工追回原始頁碼／bbox／row key。

> 標註流程：由我（agent）**閱讀原始 PDF 與結構化資料**後產生候選題目與答案，
> 但每一題的答案來源都必須指回具體頁碼／bbox／SQL row，且
> **不得**用 candidate pipeline 的輸出當答案。標註記錄寫在 `annotation_notes`。

---

## P6 — Retrieval ＋ rerank

**Deliverable**

- `src/twfi/index/bm25.py`（中文分詞：character bigram ＋ 數字/英文 token）
- `src/twfi/index/dense.py`（`BAAI/bge-m3`，batch、fp16、cache）
- `src/twfi/index/hybrid.py`（RRF, k=60）
- `src/twfi/index/rerank.py`（`BAAI/bge-reranker-v2-m3`）
- `src/twfi/index/store.py`（on-disk index artifacts ＋ 版本 hash）

**DoD**：離線測試以假 embedding backend（monkeypatch）覆蓋；
index build 有 cold/warm 計時；`Recall@5` 在 DEV 上有合理數字。

---

## P7 — Chart route

**Deliverable**

- `src/twfi/chart/caption.py` — VLM 對 figure crop 產生 caption（**只進 index**）
- `src/twfi/chart/crop_answer.py` — 最終答案由 original crop pixels 產生，
  輸出 `{value, unit, crop_page, bbox, caption_model, source_document}`
- crop 產物存 `data/cache/crops/`（不 commit）

**DoD**：測試以 fake VLM backend 覆蓋；contract 保證「caption 不得作為最終數值來源」
（有測試檢查 answer provenance 不是 caption）。

---

## P8 — Router ＋ answer/citation contract

**Deliverable**

- `src/twfi/router/classify.py` — 輸出 `{route, reason, confidence}`，6 類
- `src/twfi/router/policy.py` — 最多一次 bounded correction，硬上限
- `src/twfi/answer/generate.py` — baseline 與 candidate 共用同一 answer contract
- `src/twfi/answer/citation.py` — citation 物件（page / table_cell / chart_crop /
  sql_row）＋ 可驗證性檢查
- `src/twfi/answer/refusal.py` — 無足夠證據 → 結構化拒答

**DoD**：router 有 reason/confidence；correction 次數上限有測試；
citation 無法解析時視為 invalid（有測試）。

---

## P9 — Eval harness

**Deliverable**

- `src/twfi/eval/normalize.py`（protocol §3.1 全部規則）
- `src/twfi/eval/metrics_{retrieval,answer,citation,routing}.py`
- `src/twfi/telemetry/{timing,vram,tokens,cost}.py`
- `src/twfi/eval/runner.py` — 跑 `config × split`，raw predictions 落
  `results/runs/<run_id>/`
- `src/twfi/eval/report.py` — 產 `summary.json`、`error_analysis.jsonl`
- `scripts/verify_results.py` — summary 可由 raw artifacts 重算
- `configs/F0.yaml … F7.yaml`

**DoD**：normalize 有完整 table-driven 測試（民國年、億/萬、括號負數、百分比、幣別）；
在 DEV 上跑完 F0 與 F7；`verify_results.py` 通過。

---

## P10 — Freeze → locked run → gate → report

**順序不可調換**（見 protocol §5）

1. `scripts/pin_models.py` → `configs/models.lock.json`
2. `scripts/check_leakage.py` 通過
3. `scripts/freeze_protocol.py` → `results/feasibility/protocol_lock.json`
   （protocol / gold / manifest / models.lock 的 SHA-256）
4. `nvidia-smi` 確認 GPU 空閒 → 跑 F0…F7 於 LOCKED，cold ＋ warm
5. `scripts/verify_results.py`
6. `scripts/run_gate.py` → `results/feasibility/GO_NO_GO.json`
7. `docs/FEASIBILITY_REPORT.md`

**DoD**：`GO_NO_GO.json` 由程式產生；負面結果保留；報告含 failure analysis 與
「最小的下一個研究問題」；`git status` 乾淨。

---

## 檔案佈局（目標）

```
src/twfi/
  config.py types.py errors.py
  io/        http.py manifest.py fetch_twse.py fetch_mops.py hashing.py
  parsing/   baseline.py layout.py tables.py figures.py chunker.py
  index/     bm25.py dense.py hybrid.py rerank.py store.py
  numeric/   schema.sql duckdb_store.py sql_tools.py calculator.py
  chart/     caption.py crop_answer.py
  router/    classify.py policy.py
  answer/    generate.py citation.py refusal.py contracts.py
  eval/      gold_schema.py normalize.py metrics_*.py runner.py report.py gate.py
  telemetry/ timing.py vram.py tokens.py cost.py
scripts/     explore_sources.py fetch_twse_openapi.py fetch_documents.py
             verify_manifests.py validate_gold.py check_leakage.py pin_models.py
             build_index.py load_numeric.py run_eval.py freeze_protocol.py
             verify_results.py run_gate.py make_report.py
configs/     models.yaml models.lock.json F0.yaml … F7.yaml
data/manifests/ documents.yaml structured.yaml
data/evaluation/ dev/gold.jsonl locked/gold.jsonl locked/probes.jsonl
results/feasibility/ summary.json error_analysis.jsonl GO_NO_GO.json protocol_lock.json
tests/       單元測試 ＋ fixtures（合成 PDF、fake model backends、fixture DuckDB）
```

---

## 已識別風險與對策

| 風險 | 影響 | 對策 |
|---|---|---|
| MOPS PDF URL 非決定性／被限流 | P2 卡住 | manifest 記 `resolved_url`＋hash；人工放置 fallback；不高頻爬 |
| 年報 300+ 頁 → ingestion 與 VLM 成本高 | P6/P7 慢 | figure crop 有數量上限；embedding cache；cold/warm 分開量 |
| Qwen3-VL-8B bf16 ＋ 檢索模型同時佔 VRAM | OOM | 明確 model lifecycle（用完 unload）；檢索模型可 offload CPU；VRAM gate 22GB |
| 金控（2882）報表結構特殊 | numeric route 失敗 | 視為 hard case，如實記錄；不因此換公司 |
| 我自己標註 gold 可能帶偏誤 | 結論失真 | 答案必須指回頁碼/bbox/row；不得用 pipeline 輸出；標註前不看模型答案 |
| 想在看到 locked 結果後調整 | 協議失效 | protocol lock hash ＋ pytest 驗證 |
