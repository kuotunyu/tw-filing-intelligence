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
| P0 | Repo scaffold ＋ 規劃文件 ＋ 專案 skills | ✗ | ✗ | 🟢 完成 |
| P1 | 資料來源探勘與 manifest 定義 | ✗ | ✅ | 🟢 完成 |
| P2 | 資料取得 ＋ provenance ＋ SHA-256 驗證 | ✗ | 部分 | 🟢 完成（16 宣告／19 取得，全數 hash 相符） |
| P3 | Parsing 層（baseline ＋ layout-aware） | ✗ | ✗ | 🟢 完成 |
| P4 | 結構化數值層（DuckDB ＋ deterministic SQL） | ✗ | ✗ | 🟢 完成（D-032／D-042／D-044） |
| P5 | Gold set 標註（DEV 15 ＋ LOCKED 33 ＋ 5 probes） | ✗ | ✗ | 🟢 完成 53/53，抽樣稽核通過 |
| P6 | Retrieval ＋ rerank ＋ index artifacts | ✅ | ✗ | 🟢 完成（全程 CPU；D-034／D-036） |
| P7 | Chart route（caption index ＋ crop answering） | ✅ | ✗ | 🟢 完成（`twfi/chart/`，18 測試） |
| P8 | Router ＋ answer/citation contract | ✅ | ✗ | 🟢 完成（`twfi/router/`；D-043） |
| P9 | Eval harness ＋ metrics ＋ factor ladder | 部分 | ✗ | 🟢 完成（F0–F7 皆可跑） |
| P10 | Freeze → locked run → gate → report | ✅ | ✗ | 🔴 **唯一未完成**，見下方 |

> ⚠️ **2026-08-02 更正**：這張表在 P3–P10 一路標著「未開始」，
> 但 P3–P9 早已完成 —— 表沒有跟著更新。`CLAUDE.md` 要求每次進來先讀這張表，
> 所以它等於在對每一個接手的 session 說謊。已依 `docs/PROGRESS.md` 的實況重寫。
> **這張表與 PROGRESS 不一致時，以 PROGRESS 為準，並回頭修這裡。**

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

### P1 實際結果（2026-07-31，🟢 完成）

三個發現，完整證據在 `docs/DATA_PROVENANCE.md §8`：

1. **OpenAPI 是單期快照** — `t187ap06_L_ci` 回 1045 列，全部 `年度=115 季別=1`。
   → **P4 原本的假設錯誤**，已修正（見下方 P4）。
2. **一般業 vs 金控業兩套 schema** — 資產負債表 26 欄 vs **60** 欄，
   金控沒有 `營業收入` 這一行。2882 是真 hard case。
3. **新版 MOPS 是 JS SPA**，`doc.twse.com.tw/server-java/t57sb01` 是無 CAPTCHA
   的 POST 表單但 `step` 語意未公開 → **文件走人工放置**（D-010），
   這符合 G1 而不是 G1 的例外。

原本的 R1 風險（PDF URL 非決定性）因此**不再適用**：不自動化就沒有這個問題。

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

> **P1 之後修訂**：OpenAPI 只有當期，所以歷史數值來源改為
> XBRL（人工放置，優先）或**已驗證的表格擷取值**（fallback），
> OpenAPI 當期資料改當**獨立交叉來源**（D-010／D-011）。
> schema 必須容納 per-industry 差異（`_ci` 26 欄 vs `_fh` 60 欄）
> 與單位差異（千元 vs 百萬元）。

- `src/twfi/numeric/schema.sql` — `company`、`document`、`fin_statement`、
  `fin_line_item(company, period, statement, basis, industry_schema, account,
  value, unit, currency, source_kind, source_url, source_ref)`、`monthly_revenue`。
  `source_kind ∈ {xbrl, openapi_current, extracted_table}` —— 讓「這個數字多可信」
  可被查詢，而不是隱藏在載入邏輯裡。
- `src/twfi/numeric/duckdb_store.py` — 從 OpenAPI／XBRL／已驗證表格載入；
  每一列都必須有 `source_kind`、`source_url` 與 `source_ref`。
  一般業與金控業的 account 對應表分開維護，缺對應時**報錯**而非猜測。
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
- `data/evaluation/dev/chart_challenger.jsonl`（16 題 chart crop 讀值，
  只用於 protocol §2.3 的 freeze 前模型決策）
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

- `src/twfi/models/ollama_client.py` — 對 `qwen3.6:27b` 的最小 client
  （固定 decoding、`think=false`、bounded `num_ctx`、image 傳遞、
  逾時與重試、token/latency telemetry）。只連 `127.0.0.1:11434`。
- `src/twfi/chart/caption.py` — 對 figure crop 產生 caption（**只進 index**）
- `src/twfi/chart/crop_answer.py` — 最終答案由 original crop pixels 產生，
  輸出 `{value, unit, crop_page, bbox, model, source_document}`
- ~~`scripts/run_chart_challenger.py`~~ — **已取消，不會寫（D-021）**：DEV 的兩份文件
  沒有圖表，16 題無從出題，所以 protocol §2.3 的一次性比較沒有資料可比。
  chart route 依 §2.3 事前寫死的 fallback 使用 `qwen3.6:27b`，
  `configs/models.lock.json` 記錄 challenger 為 `cancelled` 及原因，`outcome` 留 `null`。
  **freeze 之後不得補跑** —— 「freeze 後不比較模型」這條不因 challenger 沒跑而放寬。
- crop 產物存 `data/cache/crops/`（不 commit）

**DoD**：測試以 fake ollama backend 覆蓋（離線）；contract 保證
「caption 不得作為最終數值來源」（有測試檢查 answer provenance 不是 caption）；
challenger 的判定規則以測試固定（≥10pp 才切換），不可由結果反推。

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

1. `scripts/run_chart_challenger.py`（DEV only，protocol §2.3 的一次性模型決策）
2. `scripts/pin_models.py` → `configs/models.lock.json`（含 challenger 結果）
3. `scripts/check_leakage.py` 通過
4. `scripts/freeze_protocol.py` → `results/feasibility/protocol_lock.json`
   （protocol / gold / probes / manifest / models.lock 的 SHA-256）
5. `nvidia-smi` 確認 GPU 空閒 → 跑 F0…F7 於 LOCKED，cold ＋ warm
6. `scripts/verify_results.py`
7. `scripts/run_gate.py` → `results/feasibility/GO_NO_GO.json`
8. `docs/FEASIBILITY_REPORT.md`

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
| `qwen3.6:27b` Q4_K_M（17GB）＋ KV cache ＋ 檢索模型 2.2GB ≈ 20–21GB | 接近 22GB gate，可能 OOM | `num_ctx=8192`、crop 最長邊 1024、每題 ≤3 crop；必要時檢索模型 offload CPU；gate 不放寬 |
| 金控（2882）報表結構特殊 | numeric route 失敗 | 視為 hard case，如實記錄；不因此換公司 |
| 我自己標註 gold 可能帶偏誤 | 結論失真 | 答案必須指回頁碼/bbox/row；不得用 pipeline 輸出；標註前不看模型答案 |
| 想在看到 locked 結果後調整 | 協議失效 | protocol lock hash ＋ pytest 驗證 |
