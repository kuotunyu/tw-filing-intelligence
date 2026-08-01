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
| 題數 | 15（規範 12–18） | **33**（規範 ≥30） |
| 文件 | 2412 FY2023、1301 FY2023 | 2330 FY2023/FY2024、2317 FY2023/FY2024、2882 FY2024 |
| 可否修改 | 可以，隨時重跑 | freeze 後不可 |
| 用途 | 除錯、prompt 設計、threshold 探索 | 唯一的正式比較依據 |

DEV 原定另有一個輔助集合 `data/evaluation/dev/chart_challenger.jsonl`（16 題 chart crop
讀值），用於 §2.3 的 freeze 前模型決策。
**2026-08-01 修訂（D-021）：此集合取消，因為 DEV 兩份文件裡沒有任何圖表** ——
20 個幾何正例逐一目視，20 / 20 是表格（主要為斜線表頭）或印章。詳見 §2.3。

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
| `chart_value_trend` | 2 | ✅（僅計入 pooled） |
| `cross_page` | 4 | ✅ |
| `cross_document` | 3 | ✅ |
| `unanswerable` | 4 | |
| **合計** | **33** | |

**2026-07-31 修訂（D-020，freeze 前，依量測而非依結果）**：
`chart_value_trend` 由 5 題降為 **2 題**，並**自 G2 的 hard category 移除**。
理由是量測結果：8 份可用文件的 503 個 chart candidate 中，
**locked 只有 4 張確認的真圖表，全部來自台積電、全部在兩頁上**
（鴻海與國泰金 0 張；兩份財務報告書 0 張）。
5 題全從那兩頁出，就是對同兩張資訊圖讀 5 次 —— 高度相關到「一個能力決定整組」。
而 10 個百分點的門檻在 n=2 上無意義（一題就是 50 點）。

**chart 仍留在 pooled hard set**（它仍是 hard category，且 pool 需要題數），
但**不得用來滿足 G2 的第二個條件**（單一類別改善 ≥ 門檻）——
否則一次幸運的讀值就能單獨通關。
程式上以 `SINGLE_GATE_CATEGORIES` 表達（= `HARD_CATEGORIES` 減 chart）。

**連帶調整 G2 的 pooled 門檻：10pp → 15pp。**
pool 由 21 題縮為 **18 題**，在 10pp 下只需 1.8 題（即 2 個多對的答案）就能過關，
而那條 gate 原本要求「超過 2 個」。15pp × 18 = 2.7 → **需要 3 個**，
證據強度回到原水準。**門檻只能往「更難 GO」的方向動**，這一次正是如此。
（此修訂在跑任何 evaluation 之前完成 —— 沒有任何結果影響它。）

**兩題的實際內容（2026-08-01）**：
`LOCK-0032` = `2330-FY2023-AR` p7「產能計劃」的年成長率；
`LOCK-0033` = `2330-FY2024-AR` p6「晶圓銷售計劃」的 7 奈米及以下佔比。
刻意取不同文件的不同圖，把相關性壓到語料允許的最低。
兩題都排除圖上的區間值（15-16、60-70%、20-30%）：**±0.1pp 的容差無法評分一個區間**。

**必須寫進 report limitations 的一句話**：
> 這兩題來自同一家公司（台積電）的兩頁。它們能回答的是
> 「能不能讀台積電那兩張資訊圖」，不是「能不能讀圖表」。

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
  "answer_provenance": "human_read_pdf | official_structured",
  "annotated_at": "2026-..-.."
}
```

規則：

- **Gold answer 不得由任何 candidate system output 產生。**
  `annotator` 具名（`human` 或起草它的模型），**candidate 在型別上不可表示**。
  2026-07-31 修訂（D-019）：允許 `claude-opus-5` 讀**渲染頁面像素**起草，
  搭配**固定種子的人工抽樣稽核**；`audited` 逐筆記錄是否有人查過。
  模型起草的記錄**不得**聲稱 `human_read_pdf`。
  **報告必須印出 `composition()` 的組成數字**（human／model_drafted／audited 比率），
  讓讀者自行折扣。整組模型起草且零稽核會被 `set_problems` 直接判為問題。
- **`answer_provenance` 只允許兩個來源**（D-016，2026-07-31 增訂）：
  `human_read_pdf`（人讀原始 filing）或 `official_structured`（TWSE 官方結構化資料集）。
  **本 repository 自己的 table／figure 抽取器不是合法來源**，且在型別上不可表示。
  理由是循環性：抽取器正是 F1／F4 要測的東西。若 gold 表格值由抽取器產生，
  抽錯就會變成錯的 gold，而 candidate 用同一個抽取器會「答對」那個錯答案 ——
  量到的 F1／F4 增益會是「用 parser 評測自己」的假象，而非能力。
- 由此推論一個實務限制：`cross_period_comparison` 需要 FY2023 vs FY2024，
  但 TWSE OpenAPI 只有當期快照（§8 發現 1）→ 這些題的歷史值**只能是 `human_read_pdf`**。
- `answer_provenance = official_structured` 時**必須**填 `structured_source_key`，
  否則無法重跑查驗。
- **`page_numbers` 與 `bbox.page` 一律是 PDF 位置（1-based），不是印刷頁碼。**
  parser 產出的是 PDF 位置，G4 citation 閘門比對的也是它。若標註者填印刷頁碼，
  每一筆 citation 檢查都會失敗，而失敗會被誤讀成模型引用錯誤。
  實測三份文件的抽樣頁：`2330-FY2024-FS` p55／p56、`2882-FY2024-FS` p12 兩者一致；
  其餘抽樣頁前段沒有印刷頁碼標記（年報圖表頁常無）。
  讀頁時用 `scripts/render_pages.py` 或 PDF 檢視器的頁次，不要用頁面上印的數字。
- **推導出來的答案必須記錄運算元**（`derived_from`）。
  成長率、增減額不會印在頁面上，但它的兩個輸入會。填了 `derived_from` 表示
  「這個答案是算的」，且**至少要兩個運算元**，`scripts/verify_gold_answers.py`
  改為驗證運算元是否出現在引用頁面上，而不是驗答案本身。
  空的 `derived_from` 表示「這個答案是讀的」，答案本身必須出現在引用頁面。
  理由來自 PROBE-0004：驗算做了但沒回報，就無法被第三方確認是對著寫下去的數字做的。
  **算術必須可被重算，不能只被相信。**
- **答案必須讀自渲染的頁面像素或原始 PDF，不得讀自本 repo 的文字抽取結果。**
  抽取器是被測物；若它算錯一個數字，gold 就會錯，而 candidate 用同一個抽取器
  會「答對」那個錯答案。`scripts/render_pages.py` 存在就是為了提供一個合法的閱讀面 ——
  渲染出的像素與 PDF 檢視器顯示的相同，完全繞過文字層。
  這也是唯一能讀到 `2330-FY2024-FS` pp.7–15 的方式（那些頁沒有文字層，D-017）。
- 數值題必須有 `structured_source_key` 或 `bbox`（可取得時兩者都要）。
- `required_evidence` 定義「完整證據集」，用於 complete evidence coverage 指標。
  各題型至少需要一種對應證據：`chart_value_trend` 需 `chart_crop`、
  `table_cell` 需 `table_cell`、`numeric_calculation` 需 `sql_row` 或 `table_cell`。
  只給頁碼的圖表題無法用來評分 crop-level citation，而那正是 chart route 的量測目的。
- **`chart_value_trend` 全部強制稽核，且只能取得「部分佐證」**（D-022，2026-08-01）：
  1. 每一題都進稽核樣本（`twfi.eval.audit.ALWAYS_AUDITED`），**不抽樣**。
     強制題**排除在抽籤池之外**，所以新增強制類別不會重抽其他類別、
     不會作廢已完成的稽核。
  2. `verify_gold_answers.py` 對 chart 題做**列層級**佐證：答案的每個
     「民國NNN年 → 數值」配對，那個數值必須是**所引 bbox 內、該年份同一列**的標籤。
     bbox 指到同頁另一張圖、或年份與數值配錯，都會失敗。
  3. 這類記錄報為 `~ partial`，**永遠不是 ok**。可查的是數值與**年份**歸屬；
     **查不到的是系列歸屬** —— 產能計劃同一列同時有 9% 與 15-16，
     哪個是年成長率只在圖例顏色裡。強制稽核的主要理由仍是
     **模型同時選題又作答**（D-019 的循環風險）。
  4. ⚠️ 本條原先寫「年份與圖例是 text layer 丟掉的東西」，**那是錯的**：
     文字層完整，壞碼來自主控台的 cp950 編碼（D-022 更正、D-023）。
     連帶的實質後果：**這兩題不需要視覺也可能答對**（文字層帶座標，靠 y 鄰近就能配對），
     所以 report 不得宣稱它們證明了 chart-reading 能力 ——
     這反而是又一個對 RQ3 不利的發現。
- `unanswerable` 題 `answer` 固定為 `null`，並提供 `refusal_reason_class`
  （`absent_from_documents` / `outside_selected_scope` / `irreconcilable_conflict`，
  對應 §1.4 的 (a)(b)(c)）。

**標註分工**（D-016）：判斷類題型（`narrative_fact`、`chart_value_trend`、`cross_page`、
`cross_document`、`unanswerable`、`table_cell`、`cross_period_comparison`）的答案由人
對照原始 filing 產生；只有 `numeric_calculation` 可由官方結構化資料機械建置並自動重驗。
工具可以預填機械欄位（公司、期間、`doc_id`、頁碼、`bbox`、`unit`、`tolerance`、
`source_url`）並切出證據 crop，但**不得產生答案**。機器提議的題目槽位寫入
`data/evaluation/worklist/`，與 gold 檔完全分離。

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
- **F5／F6 量的不是讀圖表能力**（D-021，2026-08-01 修訂）。
  兩階的輸入是 figure candidate（8 份可用文件共 503 個）。逐一目視的結果是
  **確認的真圖表只有 4 張**（全部台積電，集中在 2 頁），其餘是有框表格。
  因此：
  > F5／F6 的增益是關於**視覺區塊（在本語料中絕大多數是有框表格）**的證據，
  > **不得**在 report 中描述為 chart-reading 能力。

  兩階**保留**（它們只做增益歸因、不參與 gate）：
  「替有框表格生 caption、用 VLM 讀表格 crop 有沒有幫助」對這份語料
  其實是更切題的問題。只是它必須用它真正的名字來報告。
- 每個 factor 相對前一階只改一件事（factor-at-a-time），因此
  `Δ(Fk) = metric(Fk) − metric(Fk−1)` 可歸因於該 factor。

### 2.1 Parser（最多兩個，不做 parser 排行榜）

- baseline parser：**PyMuPDF `get_text()`**（純文字、固定 chunk）
- candidate parser：**in-repo layout-aware parser**（PyMuPDF dict-mode blocks
  ＋字級／字重 heading 分群 ＋ reading order ＋ pdfplumber 表格 ＋ 圖形密度偵測 figure region）

不引入第三、第四種 parser，不加入多套 OCR，不呼叫雲端 parsing API。

### 2.2 模型（每個角色一個，revision 固定，不得因結果不佳臨時更換）

| 角色 | 模型 | backend | 精度 | 備註 |
|---|---|---|---|---|
| Embedding | `BAAI/bge-m3` | HF transformers | fp16 | dense only（sparse 不用，BM25 另外算） |
| Reranker | `BAAI/bge-reranker-v2-m3` | HF transformers | fp16 | cross-encoder |
| Generation ＋ VLM | `qwen3.6:27b` digest `a50eda8ed977` | ollama 0.32.0 | Q4_K_M | **文字與圖表共用同一個多模態模型**（實測 capabilities: completion / vision / tools / thinking；architecture `qwen35`；27.8B） |

- 數值答案**不由模型生成**，由 §2.4 的 deterministic SQL 產生（見 D-005）。
  模型只負責敘述性回答、chart crop 讀值、caption 與 routing。
- **F0…F7 全部使用同一個 generation backend 與同一組 decoding 參數。**
  不得在同一份比較中混用 ollama 與 HF transformers 作為同一角色的後端。
- decoding（固定）：`temperature=0.0`、`top_p=1.0`、`top_k=1`、`seed=20260731`、
  `num_predict=512`、`num_ctx=8192`、**`think=false`**。
  > `think=false` 的理由：thinking 會產生長度不可預測的推理段落，讓
  > generation p95 latency 與 token 計數不可比較；數值推理本來就走 SQL，
  > 不依賴 chain-of-thought。此設定在 freeze 前決定，locked run 後不得更改。
- 實際 digest／revision 與 ollama 版本由 `scripts/pin_models.py` 寫入
  `configs/models.lock.json`，並納入 protocol lock hash。
- **`gpt-oss:20b` 明確不進入正式 pipeline**（不做模型排行榜）。

### 2.3 Chart challenger（**已取消** — 素材不存在）

> **2026-08-01 修訂（D-021）：challenger 取消，chart route 使用 `qwen3.6:27b`。**
>
> 取消的原因不是結果，而是**素材不存在**：下方第 1 條要求 16 題 **DEV 文件**的
> chart crop 讀值題，而 DEV 兩份年報（2412 FY2023、1301 FY2023）的
> **20 個幾何正例逐一目視後，20 / 20 都不是圖表** ——
> 主要是「項目＼年度」的**斜線表頭**，其次是紅色印章的曲線。
> DEV 文件裡一張圖表都沒有，這 16 題無從出題。
>
> 為什麼這不是事後換模型：
> 1. **一次比較都沒跑過**，沒有任何分數被看到。
> 2. 結論與下方第 3 條**事前就寫死的 fallback 分支完全相同**
>    （「否則全部 route 用 `qwen3.6:27b`」），所以取消不可能對本研究有利。
> 3. 替代方案都更糟：
>    用 locked 的 4 張圖選模型 = **在 locked 上做模型選擇**（禁止）；
>    用 DEV 的表格跑「chart challenger」= 把讀表格的比較當成讀圖表的決策來報告。
>
> `qwen3-vl:8b` 不進入 pipeline，`configs/models.lock.json` 必須記錄
> challenger 為 `cancelled` 並附上原因，report 必須說明它為何沒有跑。
> **freeze 之後同樣不得比較模型。**

以下為原規則，保留以備查核（**不再執行**）。

`qwen3-vl:8b` digest `901cae732162`（8.8B、Q4_K_M、architecture `qwen3vl`）
只作為 chart route 的小型 challenger，用來確認「用 27B 通才同時處理文字與圖表」
不是一個明顯錯誤的選擇。

事前決策規則（**不得在看到結果後修改**）：

1. Challenger 只在 **DEV 文件**（2412 FY2023、1301 FY2023）上跑，使用
   `data/evaluation/dev/chart_challenger.jsonl`：**16 個** 人工標註的 chart crop
   讀值題（與 15 題 DEV set 分開，也與 locked set 完全無關）。
2. 兩個模型使用相同 crop、相同 prompt、相同 decoding 參數。
3. 判定：若 `qwen3-vl:8b` 的 crop 讀值正確率**高出 `qwen3.6:27b` ≥ 10 個百分點**
   （即 16 題中至少多對 2 題），則 locked run 的 **chart route** 改用
   `qwen3-vl:8b`，其餘 route 仍用 `qwen3.6:27b`；否則全部 route 用 `qwen3.6:27b`。
4. 結果（無論哪邊贏）寫入 `docs/DECISIONS.md` 與 `configs/models.lock.json`，
   並在 report 中報告 challenger 的實際數字。
5. **Locked run 只用一個 chart 模型。** challenger 本身不進入 locked evaluation，
   也不列入 F0…F7 ladder。

> 這條的存在意義：把「換模型」變成一個**事前有規則、只用 DEV 資料、
> 結果公開**的決策，而不是看到 locked 分數後的補救。

### 2.4 Candidate route 規格

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

**numeric store 的來源（2026-08-02 補入，freeze 前；⚠️ 待使用者批准）**

本節原本沒有寫明 numeric route 讀哪一個 store，而實作上有兩個，
且**選哪一個會改變 F4 的數字**。這個缺口必須在 freeze 前補上：
留著不寫，等於允許 locked run 之後挑一個對自己有利的 store，那會讓整份研究失效。

| store | 建立方式 | 覆蓋範圍 |
|---|---|---|
| `numeric.duckdb` | `load_historical.py`，只載入 gold `structured_source_key` 指名的格 | gold 指名者 |
| `numeric_broad.duckdb` | `load_all_rows.py`，全語料逐頁抽取，**不看 gold** | 每個可分類的 row |

**註冊的讀法：locked run 使用 `numeric_broad.duckdb`。**
理由**只有原則，沒有數字**：

1. gold-keyed store 的內容是 locked **答案卷**的函數 ——
   系統會剛好拿到它將被問到的那幾格。那不是覆蓋率，是安排，
   F4 的 locked 數字也就無法當成能力宣稱來讀。
2. `load_all_rows.py` 全程不看 gold，所以它的覆蓋率是**管線的性質**。
   RQ2 問的是「官方結構化數值走 deterministic SQL 是否勝過讓模型讀回數字」，
   只有後者能回答這個問題。
3. 這與 `CLAUDE.md` 規則 4（gold 不得由 candidate 產生）是同一條原則的另一面：
   **candidate 的輸入也不得由 gold 產生。**

> **誠實揭露**：寫下這段時 dev 上兩個 store 的數字都已經量過
> （gold-keyed 7/15、broad 11/15，見 D-044）。
> 依 §2.5 澄清段的同一個承諾，**已量到的數字不作為本段的理由** ——
> 上面三點若把數字反過來也完全成立（broad store 較差時，
> 它仍然是唯一能回答 RQ2 的那一個，而 gold-keyed 的高分仍然是安排出來的）。
> 但「先決定再看數字」這個條件在事實上已經破了，
> 所以本段**標為待使用者批准**，而不是由實作者逕行寫定。

### 2.5 固定超參數（在 DEV 上決定，locked run 前寫死）

`top_k_retrieve=20`、`top_k_rerank=5`、baseline `top_k=5`、
baseline chunk `size=800 chars / overlap=100`、RRF `k=60`、
crop `dpi=200`、crop 最長邊 resize 至 `1024 px`、每題最多 3 個 crop、
`num_ctx=8192`、`num_predict=512`。

**管線語意澄清（2026-08-01 補入，freeze 前，依原則而非依數字）**

`top_k_retrieve=20` 與 `top_k_rerank=5` 原本沒有寫明是管線的哪一段，
而在有 RRF 融合的情況下至少有兩種讀法。以下為**確定的讀法**：

1. **`top_k_retrieve=20` 是「交給 reranker 的候選數」** ——
   融合後的清單截到 20 筆再進 reranker。
2. **融合前每一側各自抓多少（`Retriever.fetch_depth`）不是事前註冊的常數**，
   它只需 ≥20 以填滿融合清單，屬 §1.3 允許在 DEV 上調整的參數。
3. **`top_k_rerank=5` 是 reranker 的輸出數**。
4. **§3.2 的 Recall@5 與 complete evidence coverage 判定的是「管線最終的 5 筆」**：
   F3 以上有 reranker，就是 rerank 後的 5 筆；F0–F2 沒有 reranker
   （reranking 本身是 F3 這一階），就是檢索的前 5 筆。

> 為什麼要在這裡寫死：這份文件被 freeze 之後，
> 「Recall@5 指的是哪個 5」就不能再解釋了，而它會直接決定 G3 的判定。
> 澄清的方向是**先定原則再看數字** —— 寫下這段時 DEV 上各種 depth 的數字已經量過，
> 若照數字挑讀法就是在事後選一個對自己有利的定義。已量到的數字一律不作為本段的理由。

> VRAM 預算（G10 用）：`qwen3.6:27b` Q4_K_M 權重約 17 GB ＋ `num_ctx=8192` 的 KV cache
> ＋ 常駐的 bge-m3／reranker 約 2.2 GB ≈ **20–21 GB**，加上桌面程式約 1.4 GB
> 仍在 24 GB 內，但餘裕不大。G10 的 22 GB 上限是依**硬體**（RTX 4090 24GB
> 扣除桌面佔用）設定的，不是依模型調出來的，因此不因換模型而放寬。

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
| G2 | Hard category 增益 | **兩個條件都要成立**：(a) F7 在**合併 hard set**（`numeric_calculation` 5 ＋ `cross_period_comparison` 4 ＋ `chart_value_trend` 2 ＋ `cross_page` 4 ＋ `cross_document` 3 = **18 題**）的 primary answer metric 相對 F0 改善 **≥ 15 個百分點**（≥ 多對 3 題）；(b) 至少一個**單一** hard category（`SINGLE_GATE_CATEGORIES`，**不含 chart**）改善 ≥ 10 個百分點 | **hard** |
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

### 小樣本誠實性（freeze 前寫入，非事後補充）

33 題分到 8 個類型，單一類別只有 2–6 題（chart 只有 2 題）。因此：

- 單一類別 1 題 = **17–33 個百分點**。任何「單一類別改善 ≥10pp」的說法，
  可能只代表**多對 1 題**，不足以支持「有差異化」的結論。
  這是 G2 加上「合併 18 題 hard set」條件的原因（D-020 後由 21 縮為 18，門檻同步由 10pp 提到 15pp）。
- `summary.json` 與 report **必須**對每個指標同時輸出
  `n`、分子、分母、以及 **Wilson score 95% 信賴區間**。
  只寫百分比不寫 n 視為 report 不完整（G9 會擋）。
- Report 的 limitations 段必須明確寫出：本輪樣本量只能支持
  「可行 / 不可行」與「增益方向」的判斷，**不能**支持精確的效果量估計，
  也不能宣稱類別間差異具統計顯著性。
- 這一節不因結果好壞調整。若 candidate 大幅勝出，同樣要寫「信賴區間很寬」。

### 文字層誠實性（freeze 前寫入，2026-08-01 依量測補入，非事後補充）

D-033 量測全 corpus 216 萬個非空白字元後確認：**development set 的兩份文件文字層都有損壞。**

| 文件 | 可讀%（有沒有字元） | 亂碼字元% | 亂碼頁% |
|---|---|---|---|
| 2412-FY2023-AR | 95% | **17.9%** | **48%** |
| 1301-FY2023-AR | 96% | **15.4%** | **43%** |

那兩份就是 dev 的全部。因此 report 的 limitations 段**必須**寫出：

- **本研究所有在 dev 上做的調參與門檻選擇，都建立在文字層有 43%／48% 頁面損壞的文件上。**
  這不是可修的實作缺陷，是「dev 上的數字能代表什麼」的界限。
- 「可讀%」量的是**頁面有沒有產出字元**，不是**字元對不對**。
  一頁可以 100% 可讀而整頁是亂碼。兩個指標都要印。
- **private-use area 的字元不受比率門檻管**：那個區段沒有指派任何字元，
  所以每一個出現都無法解讀。全 corpus 有 150 頁含 PUA 字元卻在門檻以下。
  已知具體代價：`2882-FY2024-AR` p26 的董事專業矩陣有 **125 個打勾**編碼成 U+F0FC，
  列標題與欄標題讀得完美，**每一個打勾都不見了** ——
  純文字管線讀不出「哪位董事具備哪一項專業」。
- **gold 的 71 個被引用頁面沒有一頁是亂碼**（dev／locked／probe 全部）。
  標註是看渲染頁做的。`verify_gold_answers.py` 現在會強制這件事，
  引用到亂碼頁會報 `unverifiable`。
- 這一節不因結果好壞調整。若 candidate 大幅勝出，同樣要寫「調參基礎的文字層是壞的」。

### DEV 集合的聚集性（freeze 前寫入，2026-08-01 依量測補入）

D-035 量出 dev 15 題的實際覆蓋：**只有 8 份文件中的 2 份、3 個頁面、
4 個不同的 (文件, 頁組) 目標**（p188×8、p191×2、p188+191×1、p137×4）。因此：

- **n=15 是「15 種問法對 4 個目標」，不是 15 次獨立試驗。**
  任何把 15 當獨立樣本算出的信賴區間（含本研究輸出的 Wilson 區間）都**偏窄**。
  report **必須**同時寫出題數與**相異目標數**。
- **candidate index 中覆蓋 1301 p188 的 chunk 只有一個**，而 15 題裡有 8 題只能靠它。
  所以 2–3 題的差異可能只是**一個 chunk 的排名移動**，不是檢索能力的差異。
- **`DEV-0011` 在任何設定下都取不到**（`unanswerable` 題，gold 頁是標註者選的範圍證據，
  文件裡確實沒有該數據）。**所以 dev 每個檢索比率的天花板是 14/15**，
  report 引用 dev recall 時必須寫出這個天花板，否則 14/15 會被誤讀為「還差一題」。
- **差異一律附精確配對 McNemar 的 p 值**（`twfi.eval.gates.mcnemar_exact`）。
  2026-08-01 的量測中**沒有任何一組差異達到 5% 顯著**（最強者 p=0.219）。
- **檢索指標的殘餘偏差必須一併印出**：字元預算拉平了「模型要讀多少字」，
  但沒有拉平「交出幾頁」（跨頁 chunk 兩頁都算，baseline 每 chunk 跨 1.57 頁、
  candidate 1.25 頁，同預算下相異頁數差 1.3–1.7 倍）。
  因此 report **不得**把預算表的差距直接說成檢索能力差距。
- 這一節不因結果好壞調整。

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
2. Gold set 標註：DEV 15 題、LOCKED **33** 題 ＋ 5 個 probe
   （原列的「DEV chart challenger 16 題」已取消，見 §2.3／D-021）
3. 在 **DEV** 上開發、調參、決定所有超參數與 prompt
4. ~~**Chart challenger（§2.3）**~~ **已取消**（D-021）：DEV 文件沒有圖表，
   16 題無從出題。chart route 依 §2.3 事前寫死的 fallback 使用 `qwen3.6:27b`，
   `configs/models.lock.json` 記錄 challenger 為 `cancelled` 及原因
5. `scripts/pin_models.py` → `configs/models.lock.json`（digest／revision／ollama 版本）
6. `scripts/check_leakage.py` 通過（DEV／LOCKED 公司與文件不重疊、`annotator=human`）
7. **`scripts/freeze_protocol.py`** → 產生 `results/feasibility/protocol_lock.json`
8. 跑 F0…F7 於 LOCKED（cold ＋ warm）
9. `scripts/verify_results.py` → `scripts/run_gate.py` → `GO_NO_GO.json`
10. 寫 `docs/FEASIBILITY_REPORT.md`（含失敗分析與負面結果）

> 第 7 步之後才允許碰 LOCKED。第 8 步開始，任何對 `src/` 的修改都必須重跑
> 全部 F0…F7 並在 report 記錄，不允許只重跑對自己有利的 config。
> Chart challenger 原本只能在第 4 步做一次；它已取消（D-021），
> 而**「freeze 之後不得比較模型」這條完全不變** ——
> challenger 沒跑不構成之後補跑的理由。

---

## 6. 已知威脅（詳見 `docs/THREAT_MODEL.md`）

- 年報內文可能包含 prompt-injection 樣式文字 → 所有文件內容一律視為 data。
- Dev→locked 洩漏（同公司、同頁、同數字）→ `check_leakage.py`。
- 題目偏易 → 強制題型分布 ＋ hard category 定義。
- 指標挑選偏誤 → primary metric 事前指定，不得事後改用對自己有利的指標。
- LLM-as-judge 被 gaming → judge 不參與 gate。
