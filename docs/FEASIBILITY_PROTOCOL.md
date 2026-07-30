# FEASIBILITY PROTOCOL (pre-registered)

`protocol_version: 1.0.0-draft`
`status: DRAFT — 尚未 freeze`
`authored: 2026-07-31`

> **這份文件是事前註冊協議。**
> 一旦執行 `scripts/freeze_protocol.py`，本文件的 SHA-256、locked gold set 的
> SHA-256、以及 document/structured manifest 的 SHA-256 會被寫入
> `results/feasibility/protocol_lock.json`。
>
> Freeze 之後，下列項目**一律不得修改**：研究問題、資料切分、題目、gold answer、
> acceptable variants、tolerance、normalization 規則、指標定義、GO／NO-GO 門檻、
> 模型與 revision、factor ladder 定義。
>
> `tests/test_protocol_lock.py` 會在每次 `pytest` 時驗證 hash；hash 不符即測試失敗。
> 若真的必須修改，只能**新開 protocol_version 2.x 並重跑全部 locked evaluation**，
> 且舊版結果必須完整保留在 report 中（不得刪除負面結果）。

---

## 0. 研究問題（primary）

> 臺灣上市公司公開資訊，是否足以支撐一個有差異化且可驗證的
> multimodal filing intelligence 系統？

拆成四個可量測的 sub-question：

- **RQ1 (narrative)** — 年報敘述性內容（策略、風險因素、營運變化）能否被穩定檢索並
  以可驗證 citation 回答？
- **RQ2 (structured numeric)** — 官方 XBRL／OpenAPI 的結構化數值走 deterministic SQL，
  是否顯著優於「把數字丟進 embedding 讓 LLM 猜」？
- **RQ3 (table / chart)** — chart caption 是否改善 retrieval？最終數值答案回到
  original crop pixels 是否比純文字管線正確？
- **RQ4 (cross-document / unanswerable)** — 跨頁、跨年度、PDF↔結構化交叉驗證是否可行？
  沒有證據時系統能否拒答，而不是硬答？

**這一輪不驗證的事情**（明確 out of scope，避免結論被過度延伸）：多租戶、即時更新、
全市場覆蓋、上櫃／興櫃、非中文文件、production latency SLA、UI。

---

## 1. 資料

### 1.1 官方來源（只用這三個）

| # | 來源 | 用途 |
|---|---|---|
| S1 | 公開資訊觀測站 MOPS `https://mops.twse.com.tw/` | 年報、財務報告 PDF、XBRL |
| S2 | MOPS XBRL 單一公司／整批下載 | 結構化財務數值 |
| S3 | TWSE OpenAPI `https://openapi.twse.com.tw/` (`/v1/swagger.json`) | 結構化財務／基本資料 |

取得原則：只用正式 OpenAPI、公開下載頁與穩定直接文件連結；不解 CAPTCHA；
不模擬大量互動查詢；不用未公開／逆向 endpoint；遵守 rate limit / timeout / retry /
user-agent；不對 MOPS 高頻爬取；下載失敗提供人工放置 fallback。
細節見 `docs/DATA_PROVENANCE.md`。

### 1.2 文件選擇

5 家上市公司，4 個產業，2 個會計年度（FY2023 / FY2024），共 7 份 PDF
＋對應之結構化資料。刻意包含版面困難的文件（金控年報、跨頁大表）。

| 公司 | 代號 | 產業 | 年度 | 用途 |
|---|---|---|---|---|
| 中華電信 | 2412 | 電信 | FY2023 | **DEV** |
| 台塑 | 1301 | 塑膠／石化 | FY2023 | **DEV** |
| 台積電 | 2330 | 半導體 | FY2023, FY2024 | **LOCKED** |
| 鴻海 | 2317 | 電子製造服務 | FY2023, FY2024 | **LOCKED** |
| 國泰金控 | 2882 | 金融保險（表格結構最難） | FY2024 | **LOCKED** |

> 產業數 4 ≥ 2 ✅｜PDF 數 7（5–10 區間內）✅｜年度數 2 ≥ 2 ✅
> 非「只選版面最簡單」✅（2882 金控年報、2317 跨頁合併報表）
> 非「只選單一公司」✅

**文件層級分離**：DEV 與 LOCKED 使用**完全不同的公司**。
Dev set 的兩家公司（2412、1301）不出現在 locked set 的任何題目中，反之亦然。
`scripts/check_leakage.py` 會強制驗證這件事。

### 1.3 兩個評估集

| | DEV | LOCKED |
|---|---|---|
| 檔案 | `data/evaluation/dev/gold.jsonl` | `data/evaluation/locked/gold.jsonl` |
| 題數 | 15（規範 12–18） | **36**（規範 ≥30） |
| 文件 | 2412 FY2023、1301 FY2023 | 2330 FY2023/FY2024、2317 FY2023/FY2024、2882 FY2024 |
| 可否修改 | 可以，隨時重跑 | freeze 後不可 |
| 用途 | 除錯、prompt 設計、threshold 探索 | 唯一的正式比較依據 |

**所有 threshold、prompt、tolerance、chunk size、top-k 只允許在 DEV 上調整。**
Locked set 只跑一次正式 run（重跑只允許在「程式 crash 或環境錯誤」且無人看過分數的情況下，
並須在 report 記錄重跑原因與次數）。

### 1.4 Locked set 題型分布（freeze 前定義，freeze 後不可改）

| question_type | 題數 | hard? |
|---|---|---|
| `narrative_fact` | 6 | |
| `table_cell` | 5 | |
| `numeric_calculation` | 5 | ✅ |
| `cross_period_comparison` | 4 | ✅ |
| `chart_value_trend` | 5 | ✅ |
| `cross_page` | 4 | ✅ |
| `cross_document` | 3 | ✅ |
| `unanswerable` | 4 | |
| **合計** | **36** | |

`unanswerable` 4 題必須涵蓋三種成因，且至少各一題：
(a) 文件中確實不存在該資訊；(b) 資訊存在但超出所選文件範圍（例如未選之年度）；
(c) 兩個來源數值衝突且無法在文件內裁決。

### 1.5 Gold record schema

每題（`data/evaluation/*/gold.jsonl`，一行一 JSON object）必須包含：

```json
{
  "question_id": "LOCK-0001",
  "question_type": "numeric_calculation",
  "question": "...",
  "answer": "...",
  "acceptable_variants": ["...", "..."],
  "unit": "千元 | 元 | % | 倍 | null",
  "currency": "TWD | USD | null",
  "period": "FY2024 | FY2024Q4 | FY2023-FY2024",
  "company": {"name": "台積電", "code": "2330"},
  "statement_basis": "consolidated | parent_only | null",
  "source_document": ["doc_id"],
  "source_url": ["https://..."],
  "page_numbers": [123, 124],
  "bbox": [{"page": 123, "bbox": [x0, y0, x1, y1]}],
  "structured_source_key": {"table": "fin_line_item", "row_key": "..."},
  "required_evidence": [{"kind": "page|table_cell|chart_crop|sql_row", "ref": "..."}],
  "answerable": true,
  "tolerance": {"type": "relative", "value": 0.005},
  "annotation_notes": "...",
  "annotator": "human",
  "annotated_at": "2026-..-.."
}
```

規則：

- **Gold answer 不得由任何 candidate system output 自動產生**（`annotator` 必須是 `human`；
  `scripts/check_leakage.py` 驗證此欄位）。
- 數值題必須有 `structured_source_key` 或 `bbox`（可取得時兩者都要）。
- `required_evidence` 定義「完整證據集」，用於 complete evidence coverage 指標。
- `unanswerable` 題 `answer` 固定為 `null`，並提供 `refusal_reason_class`。

---

## 2. 系統設定（factor-at-a-time ladder）

Baseline 與所有 candidate factor **共用同一份 answer contract 與 citation contract**，
共用同一個 generation model 與 decoding 參數，確保比較公平。

| id | 設定 | parsing | retrieval | rerank | numeric SQL | chart caption | crop VLM | router |
|---|---|---|---|---|---|---|---|---|
| **F0** | baseline | PyMuPDF plain text + 固定 chunk | BM25 | ✗ | ✗ | ✗ | ✗ | ✗ (固定 top-k) |
| F1 | +structure-aware parsing/chunking | layout-aware | BM25 | ✗ | ✗ | ✗ | ✗ | ✗ |
| F2 | +hybrid retrieval | layout-aware | BM25＋dense (RRF) | ✗ | ✗ | ✗ | ✗ | ✗ |
| F3 | +cross-encoder rerank | layout-aware | hybrid | ✅ | ✗ | ✗ | ✗ | ✗ |
| F4 | +structured numeric route | layout-aware | hybrid | ✅ | ✅ | ✗ | ✗ | ✗ |
| F5 | +chart caption indexing | layout-aware | hybrid | ✅ | ✅ | ✅ | ✗ | ✗ |
| F6 | +original crop evidence | layout-aware | hybrid | ✅ | ✅ | ✅ | ✅ | ✗ |
| **F7** | candidate (full) | layout-aware | hybrid | ✅ | ✅ | ✅ | ✅ | ✅ typed bounded |

- 「Candidate」在所有 gate 判斷中一律指 **F7**。
- F1…F6 只用於**增益歸因**（哪個 factor 帶來多少改善），不參與 GO／NO-GO 門檻。
- 每個 factor 相對前一階只改一件事（factor-at-a-time），因此
  `Δ(Fk) = metric(Fk) − metric(Fk−1)` 可歸因於該 factor。

### 2.1 Parser（最多兩個，不做 parser 排行榜）

- baseline parser：**PyMuPDF `get_text()`**（純文字、固定 chunk）
- candidate parser：**in-repo layout-aware parser**（PyMuPDF dict-mode blocks
  ＋字級／字重 heading 分群 ＋ reading order ＋ pdfplumber 表格 ＋ 圖形密度偵測 figure region）

不引入第三、第四種 parser，不加入多套 OCR，不呼叫雲端 parsing API。

### 2.2 模型（各一個，revision 固定，不得因結果不佳臨時更換）

| 角色 | 模型 | 精度 | 備註 |
|---|---|---|---|
| Embedding | `BAAI/bge-m3` | fp16 | dense only（sparse 不用，BM25 另外算） |
| Reranker | `BAAI/bge-reranker-v2-m3` | fp16 | cross-encoder |
| Local VLM | `Qwen/Qwen3-VL-8B-Instruct` | bf16 | chart caption ＋ crop answering |
| Generation | `Qwen/Qwen3-VL-8B-Instruct`（text-only path） | bf16 | 與 VLM 同權重，避免第二套 8B 佔 VRAM |

- 實際使用的 commit revision 由 `scripts/pin_models.py` 寫入
  `configs/models.lock.json`，並納入 protocol lock hash。
- decoding：`temperature=0.0`、`top_p=1.0`、`seed=20260731`、`max_new_tokens=512`。
- 低 VRAM fallback（僅在 4090 不可用時，且必須在 report 註明）：
  ollama `qwen3-vl:8b` digest `901cae732162`。**不得**在同一份比較中混用兩種後端。

### 2.3 Candidate route 規格

- **narrative route**：保留 heading / section / page / bbox；hybrid retrieval；rerank；
  page-level citation。
- **numeric route**：官方 OpenAPI／XBRL／明確表格數值載入 DuckDB；deterministic
  templated SQL（**不允許 LLM 自由生成 SQL**）；保存 company / period / statement /
  account / unit / currency / source_url；計算題必須輸出 formula 與 operands。
- **chart route**：caption 只進 index；最終數值必須來自 original crop pixels 或
  可靠結構化資料；保存 crop page / bbox / caption model / source document。
- **typed router**：輸出 `narrative | numeric | chart | cross_modal | metadata |
  unanswerable`，並保留 `reason` 與 `confidence`。
  **最多一次 bounded correction**；無上限 agent loop 禁止。

### 2.4 固定超參數（在 DEV 上決定，locked run 前寫死）

`top_k_retrieve=20`、`top_k_rerank=5`、baseline `top_k=5`、
baseline chunk `size=800 chars / overlap=100`、RRF `k=60`、
crop `dpi=200`、每題最多 3 個 crop。

---

## 3. 指標定義

所有能 deterministic 計算的指標**必須** deterministic。
LLM-as-judge 只允許用於 `evidence_sufficiency` 一項，且必須同時報告
deterministic 指標；judge 分數不參與任何 GO／NO-GO gate。

### 3.1 Normalization（answer scoring 前一律套用，freeze 後不可改）

1. 全角→半角、去除空白與千分位逗號。
2. 中文數字單位展開：`億 = 1e8`、`萬 = 1e4`、`千元 = 1e3`（保留原單位欄位另判）。
3. 括號負數 `(1,234)` → `-1234`。
4. 百分比：`12.3%` 與 `0.123` 不視為等價；以 gold `unit` 為準。
5. 貨幣符號與「新台幣／NT$／TWD」正規化為 `TWD`。
6. 民國年↔西元年互轉（`112年` ↔ `2023`）。
7. 數值題比較用數值比較，不用字串比較。

### 3.2 Retrieval

- **Recall@5** — top-5 內是否命中 `required_evidence` 之任一項。
- **MRR@10**。
- **complete evidence coverage** — top-5 是否覆蓋 `required_evidence` **全部**項目。
- **cross-page evidence coverage** — 僅計 `required_evidence` 橫跨 ≥2 頁的題目，
  是否全部頁面都被檢索到。

### 3.3 Answer

- **exact match**（normalized）
- **token F1**（normalized，中文以 character-level bigram 計）
- **numeric accuracy**：以宣告 tolerance 判定。預設 `relative 0.5%`；
  比率／百分比預設 `absolute 0.1 percentage point`；題目 `tolerance` 欄位優先。
- **unit accuracy**、**period accuracy**（分開量測；答對數字但單位或期間錯 = 該項不通過）
- **refusal precision / recall**：以 `answerable=false` 為 positive class。
  - refusal recall = 正確拒答的 unanswerable 題 / 全部 unanswerable 題
  - refusal precision = 正確拒答 / 全部拒答
  - **over-answer rate** = 在 unanswerable 題上給出具體數值或事實斷言的比例

### 3.4 Citation

- **citation precision** — 引用的證據中，屬於 `required_evidence` 或確實支持答案的比例
- **citation recall** — `required_evidence` 被引用到的比例
- **page correctness** — 引用頁碼正確率
- **bbox / structured-row validity** — bbox 落在正確頁面且與 gold bbox `IoU ≥ 0.3`；
  SQL 來源 row key 與 gold `structured_source_key` 相符
- **citation validity**（gate 用）＝ 引用可解析、指向存在的頁／表／row、
  且該證據確實包含答案 span 或 operands 的比例

### 3.5 Routing

- **route accuracy**（對 6 類）、**route confusion matrix**
- gold route 由 `question_type` 映射：
  `narrative_fact→narrative`、`table_cell→chart`(表格走 chart/table route)、
  `numeric_calculation→numeric`、`cross_period_comparison→numeric`、
  `chart_value_trend→chart`、`cross_page→narrative`、`cross_document→cross_modal`、
  `unanswerable→unanswerable`。
  > 註：`metadata` 類別在 locked set 無題目，僅作為 router 輸出空間存在；
  > 若 router 輸出 `metadata` 一律計為錯誤。

### 3.6 Systems

ingestion latency（每文件、每頁）、retrieval p50/p95、generation p50/p95、
GPU VRAM peak（`torch.cuda.max_memory_allocated` ＋ `nvidia-smi` 取樣）、
prompt/completion tokens、API cost（全 local ⇒ `0`，且以 `"cost_usd": 0.0,
"cost_basis": "all-local-inference"` 記錄，不捏造貨幣成本）、
**cache cold / warm 分開報告**（cold = 清空 index/embedding cache 後首跑）。

---

## 4. GO / NO-GO GATES（事前凍結，看到結果後不得修改）

Gate 由 `scripts/run_gate.py` 讀取 `results/feasibility/summary.json` 自動判定，
輸出 `results/feasibility/GO_NO_GO.json`。人工不得覆寫判定。

| # | Gate | 判定條件 | 類型 |
|---|---|---|---|
| G1 | 資料可重現 | 所有文件與結構化資料可由 manifest ＋ 腳本重建，SHA-256 全部相符；無 CAPTCHA 破解、無私人 endpoint | **hard** |
| G2 | Hard category 增益 | F7 在 `numeric_calculation / cross_period_comparison / chart_value_trend / cross_page / cross_document` 之中，**至少一個**類別的 primary answer metric 相對 F0 改善 **≥ 10 個百分點** | **hard** |
| G3 | 無整體退步 | F7 overall answer accuracy 不得低於 F0 超過 **5 個百分點** | **hard** |
| G4 | Citation validity | F7 citation validity **≥ 90%** | **hard** |
| G5 | Numeric route 正確率 | F7 在可回答的 `numeric_calculation`＋`cross_period_comparison`＋`table_cell` 且經 numeric route 處理者，正確率 **≥ 90%** | **hard** |
| G6 | Route accuracy | F7 route accuracy **≥ 85%** | **hard** |
| G7 | 不大量強答 | unanswerable 題 **over-answer rate ≤ 25%**（即 refusal recall ≥ 75%），且 refusal precision **≥ 80%** | **hard** |
| G8 | 無證據能拒答 | 人工建構的 5 個 no-evidence probe（`data/evaluation/locked/probes.jsonl`，檢索結果被強制清空）中，≥ 4 個拒答 | **hard** |
| G9 | 結果可重建 | `scripts/verify_results.py` 通過：summary.json 每個數字都能由 `results/runs/**` raw artifacts 重算，且 protocol lock hash 相符 | **hard** |
| G10 | 資源可行 | retrieval p95 ≤ 3s、generation p95 ≤ 60s、VRAM peak ≤ 22 GB（RTX 4090 24GB） | **soft** |

`overall answer accuracy` 定義：answerable 題以 `numeric accuracy`（數值題）或
`exact match ∨ token-F1 ≥ 0.8`（文字題）判對；unanswerable 題以「正確拒答」判對；
所有題目等權平均。

### 決策規則（程式化）

- **GO** — G1–G9 全部通過，且 G10 通過。
- **CONDITIONAL_GO** — G1–G9 全部通過，但 G10 未通過（僅資源問題）。
- **NO_GO** — G1–G9 任一未通過。

### 若不是 GO

- **不得**為了進入下一階段修改題目、答案、tolerance 或 threshold。
- **不得**刪除或隱藏負面結果。
- **不得**建立正式產品 UI。
- 保留完整 feasibility report。
- 必須在 `docs/FEASIBILITY_REPORT.md` 明確寫出「**最小的下一個研究問題**」
  ——即單一、可獨立驗證、能解掉當前主要失敗成因的問題。
- 只有明確 **GO** 才允許進行 ⑤B。

---

## 5. 執行順序（不可調換）

1. 資料取得 ＋ manifest SHA-256 驗證（G1 的證據）
2. Gold set 人工標註：DEV 15 題、LOCKED 36 題 ＋ 5 個 probe
3. 在 **DEV** 上開發、調參、決定所有超參數與 prompt
4. `scripts/check_leakage.py` 通過（DEV／LOCKED 公司與文件不重疊、`annotator=human`）
5. **`scripts/freeze_protocol.py`** → 產生 `results/feasibility/protocol_lock.json`
6. 跑 F0…F7 於 LOCKED（cold ＋ warm）
7. `scripts/verify_results.py` → `scripts/run_gate.py` → `GO_NO_GO.json`
8. 寫 `docs/FEASIBILITY_REPORT.md`（含失敗分析與負面結果）

> 第 5 步之後才允許碰 LOCKED。第 6 步開始，任何對 `src/` 的修改都必須重跑
> 全部 F0…F7 並在 report 記錄，不允許只重跑對自己有利的 config。

---

## 6. 已知威脅（詳見 `docs/THREAT_MODEL.md`）

- 年報內文可能包含 prompt-injection 樣式文字 → 所有文件內容一律視為 data。
- Dev→locked 洩漏（同公司、同頁、同數字）→ `check_leakage.py`。
- 題目偏易 → 強制題型分布 ＋ hard category 定義。
- 指標挑選偏誤 → primary metric 事前指定，不得事後改用對自己有利的指標。
- LLM-as-judge 被 gaming → judge 不參與 gate。
