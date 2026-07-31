# DECISIONS — 固定下來的技術選擇

格式：ADR-lite。每條含 決策 / 理由 / 替代方案 / 影響 / 狀態。
**Freeze 之後不得因為結果不好而更改**（見 `FEASIBILITY_PROTOCOL.md`）。

---

## D-001 Python 3.13 + uv（**不是** 3.11，原因是非 ASCII 路徑）

- **決策**：Python 3.13（`>=3.13,<3.14`），uv 管理環境與 lockfile。
- **理由**：
  1. 本機系統 Python 是 3.10.9 且無 CUDA torch → 用 uv 建獨立環境，不污染系統。
  2. **關鍵限制**：本 repo 位於 `...\CC_github部隊\...`，路徑含非 ASCII 字元。
     editable install 產生的 `_editable_impl_tw_filing_intelligence.pth` 內含
     UTF-8 編碼的絕對路徑，而 **Python ≤ 3.12 的 `site` 模組用系統 locale
     （本機為 cp950）讀取 `.pth`** → venv 一啟動就
     `UnicodeDecodeError: 'cp950' codec can't decode byte 0xe9`，
     `uv run` 完全不能用。Python 3.13 起 `.pth` 以 UTF-8 讀取，問題消失。
     實測：3.11 → 壞；3.13.13 → 正常（`uv run python -c "import twfi"` 通過）。
  3. 3.13 對 PyMuPDF / pdfplumber / duckdb / torch / transformers 都已有輪子。
- **替代**：
  - 3.11 ＋ 全域 `PYTHONUTF8=1`（脆弱，依賴使用者環境變數）
  - 3.11 ＋ 非 editable 安裝（改一行程式就要重裝）
  - 把 repo 搬到純 ASCII 路徑（不在本任務授權範圍，且會動到使用者的目錄結構）
- **影響**：README 的 Quickstart 標示 Python 3.13。若日後把 repo 移到 ASCII 路徑，
  可下修版本需求，但沒有必要。
- **狀態**：ACCEPTED (2026-07-31)

## D-002 Parser：baseline = PyMuPDF plain，candidate = in-repo layout-aware

- **決策**：只比較兩個 parser。baseline 為 PyMuPDF `get_text()` ＋ 固定 chunk；
  candidate 為**本 repo 內自建**的 layout-aware parser
  （PyMuPDF dict-mode blocks ＋ 字級/字重 heading 分群 ＋ reading order ＋
  pdfplumber 表格 ＋ 向量繪圖密度 figure 偵測）。
- **理由**：
  1. Protocol 要求最多兩個 parser、不做 parser 排行榜。
  2. 台灣年報是 digital-born PDF，native text layer 完整，瓶頸在**結構**
     （heading 階層、跨頁表、單位列、圖表區）而不是 OCR。
  3. 自建 parser 讓「所有程式碼存在本 repository」成立，且**完全 deterministic**、
     可用合成 PDF fixture 離線測試、不需要下載額外 layout 模型。
- **替代**：`docling`（需下載 layout/TableFormer 權重、Windows 依賴較重、
  非 deterministic）、PaddleOCR PP-Structure（Windows 安裝痛點）。
  兩者列為 **out of scope**，並在 report 中說明「本輪未驗證 learned layout model」
  這個限制。
- **影響**：結論不能延伸為「learned layout parser 沒用」，只能說
  「rule-based structure-aware parsing 已足以產生 X 的增益」。
  這個限制必須寫進 `docs/FEASIBILITY_REPORT.md`。
- **狀態**：ACCEPTED (2026-07-31，使用者確認)

## D-003 模型：`qwen3.6:27b` 同時擔任 generation 與 VLM

| 角色 | 模型 | backend | 精度 |
|---|---|---|---|
| Embedding | `BAAI/bge-m3` | HF transformers（cache 已存在） | fp16 |
| Reranker | `BAAI/bge-reranker-v2-m3` | HF transformers（cache 已存在） | fp16 |
| Generation ＋ VLM | `qwen3.6:27b` digest `a50eda8ed977` | ollama 0.32.0（已 pull） | Q4_K_M |

- **決策**（使用者 2026-07-31 拍板）：⑤A 的正式主候選是 `qwen3.6:27b`，
  **文字與圖表共用同一個模型**；數值答案由 SQL 完成；
  `qwen3-vl:8b` 只在 freeze 前做小型 chart challenger（見 D-009）；
  `gpt-oss:20b` 不進正式 pipeline。
- **可行性已實測**：`ollama show qwen3.6:27b` 回報
  `capabilities: completion / vision / tools / thinking`、`architecture qwen35`、
  `27.8B`、`Q4_K_M`、`context length 262144`。
  **確認具備 vision**，因此「同一模型處理文字與 chart crop」成立；
  若它是純文字模型，chart route（必須從 original crop pixels 讀值）就不可能實作。
- **理由**：
  1. 全部**零新下載**（HF cache ＋ ollama 皆已有）。
  2. 27B 通才對繁體中文長篇年報敘述的理解優於 8B，而 chart crop 讀值由同一模型
     承擔可避免第二套權重佔 VRAM。
  3. 數值不靠模型（D-005），所以 Q4 量化對「數值正確率」這個主指標的風險有限。
- **替代**：`Qwen/Qwen3-VL-8B-Instruct` bf16（HF，品質未必更好且需兩套權重）、
  `Qwen3-4B-Instruct-2507`（太小，會拖低 candidate 使 gate 判斷失真）。
- **固定設定**：`temperature=0.0`、`top_p=1.0`、`top_k=1`、`seed=20260731`、
  `num_predict=512`、`num_ctx=8192`、**`think=false`**。
  關閉 thinking 的理由：長度不可預測的推理段落會讓 generation p95 latency 與
  token 計數不可比較，而數值推理本來就走 SQL。
- **已知風險**：VRAM 約 20–21GB（17GB 權重 ＋ KV cache ＋ 2.2GB 檢索模型），
  G10 的 22GB 上限餘裕不大。上限是依硬體設定，**不因換模型放寬**。
- **狀態**：ACCEPTED (2026-07-31，使用者確認)

## D-004 資料選擇：5 家公司 / 4 產業 / 2 年度 / 7 份 PDF

| 公司 | 代號 | 產業 | 年度 | split |
|---|---|---|---|---|
| 中華電信 | 2412 | 電信 | FY2023 | DEV |
| 台塑 | 1301 | 塑膠／石化 | FY2023 | DEV |
| 台積電 | 2330 | 半導體 | FY2023, FY2024 | LOCKED |
| 鴻海 | 2317 | 電子製造服務 | FY2023, FY2024 | LOCKED |
| 國泰金控 | 2882 | 金融保險 | FY2024 | LOCKED |

- **理由**：DEV/LOCKED **公司層級完全分離**（最嚴格的分離方式，避免同公司同段落洩漏）；
  4 個產業 ≥ 2；含金控（報表結構與一般業完全不同、版面最難）避免「只選簡單版面」；
  2 個年度支援 cross-period 題型。
- **替代（已評估後不採用）**：移除 2882 金控可降低 numeric route 失敗風險，
  但會失去結構最不同的產業，使結論說服力下降；加入 2454 聯發科可擴大覆蓋，
  但 F0–F7 × 36 題的 GPU 時間會明顯增加。
- **狀態**：ACCEPTED (2026-07-31，使用者確認)

## D-005 Numeric route 不允許 LLM 自由生成 SQL

- **決策**：只用 templated、參數化 SQL（lookup / delta / ratio / growth），
  由 router 決定 template ＋ 參數。
- **理由**：protocol 要求 deterministic；自由生成 SQL 會引入不可重現的失敗模式，
  也讓「數值正確率」不可歸因。
- **影響**：無法回答 template 未覆蓋的數值問題 → 這類題目走拒答，並在
  error analysis 標為 `template_miss`（誠實記錄能力邊界）。
- **狀態**：ACCEPTED (2026-07-31)

## D-006 Chart caption 只進 index

- **決策**：VLM caption 寫入檢索索引；**最終數值答案必須**來自 original crop pixels
  或結構化資料。程式層面以 answer provenance 檢查強制（有測試）。
- **理由**：caption 是有損摘要，直接當數值來源會產生無法追溯的幻覺。
- **狀態**：ACCEPTED (2026-07-31)

## D-007 Router 不做 agent loop

- **決策**：single-pass typed classification ＋ 最多一次 bounded correction。
- **理由**：protocol 明確禁止無上限 loop；latency 與成本可預測；失敗可歸因。
- **狀態**：ACCEPTED (2026-07-31)

## D-008 LLM-as-judge 不參與 gate

- **決策**：judge 只用於 `evidence_sufficiency` 的輔助觀察，不進入任何 GO／NO-GO 判定。
- **理由**：避免用同一家族模型自我評分造成的樂觀偏誤。
- **狀態**：ACCEPTED (2026-07-31)

## D-009 Chart challenger：freeze 前一次性模型決策，規則事前寫死

- **決策**：`qwen3-vl:8b` digest `901cae732162` 只作為 chart route 的 challenger，
  在 **DEV 文件上**跑一次 16 題 chart crop 讀值比較
  （`data/evaluation/dev/chart_challenger.jsonl`），依**事前規則**決定 locked run 的
  chart route 用哪個模型：
  > 若 `qwen3-vl:8b` 正確率高出 `qwen3.6:27b` **≥ 10 個百分點**（16 題中至少多對 2 題），
  > chart route 改用 `qwen3-vl:8b`，其餘 route 仍用 `qwen3.6:27b`；否則全部用 27B。
- **理由**：使用者要求保留一個小型 chart challenger。但「跑完再看要用哪個」在
  方法論上等於事後換模型，除非**規則、資料、時點都事先固定**。因此：
  規則寫死在 protocol §2.3、只用 DEV 資料、只在 freeze 前執行一次、
  結果無論輸贏都要公開在 report。
- **限制**：challenger 不進入 locked evaluation、不列入 F0…F7 ladder、
  locked run 只用一個 chart 模型。freeze 之後**不得再比較模型**。
- **狀態**：ACCEPTED (2026-07-31)

## D-010 資料取得策略（P1 實測後修訂）

- **決策**：
  | 資料 | 取得方式 | 自動化？ |
  |---|---|---|
  | TWSE OpenAPI（公司基本資料、**當期**財報、營益分析、EPS、月營收） | `scripts/fetch_twse_openapi.py` | ✅ 全自動 |
  | 年報 PDF（7 份） | **人工放置** `data/raw/manual/` ＋ SHA-256 驗證 | ❌ 刻意不自動化 |
  | MOPS XBRL（7 份，FY2023／FY2024） | **人工放置**（建議但非必要） | ❌ |
  | 歷史結構化數值 | XBRL 優先；未提供時退回**已驗證的表格擷取值** | — |

- **理由**（完整證據見 `docs/DATA_PROVENANCE.md §8`）：
  1. **OpenAPI 是單期快照**。實測 `t187ap06_L_ci` 回 1045 列全部 `年度=115 季別=1`。
     原本 P4「用 OpenAPI 當歷史數值來源」的假設是錯的，必須修正。
  2. **新版 MOPS 是 JS SPA**。`/mops/web/*` 只回 65 bytes 的 JS bootstrap；
     要取資料就得呼叫未公開 XHR API → 協議禁止。
  3. **`doc.twse.com.tw/server-java/t57sb01` 沒有 CAPTCHA 但是 POST 表單**，
     `step` 語意未公開 → 驅動它屬於表單模擬／逆向，且只為 7 份文件不值得。
- **對 G1 的影響**：無。人工放置 ＋ `source_page` ＋ SHA-256 完全可重現，
  且不依賴破解或私人 endpoint —— 這是**符合** G1 的取得方式。
- **對研究結論的限制**（必須寫進 report）：
  - FY2023／FY2024 的結構化數值若未提供 XBRL，來源是**我們自己的表格擷取**
    而非官方 XBRL。RQ2（deterministic SQL vs LLM 猜）仍然成立，
    但「官方結構化資料」這個更強的說法要降級為「已驗證的結構化資料」。
  - 「臺灣公開資訊在文件層級沒有穩定官方批量下載介面」本身是 feasibility 發現。
- **狀態**：ACCEPTED (2026-07-31，P1 實測後)

## D-011 OpenAPI 當期資料改當作**獨立交叉來源**

- **決策**：既然 OpenAPI 只有當期，就不當歷史來源，而是當
  **獨立於 PDF 的第二個來源**，用於 cross_document 題與資料衝突偵測。
- **理由**：把限制轉成研究材料。「PDF 的 FY2024 數字」vs「OpenAPI 的當期數字」
  是真實世界的跨來源比對場景，正好對應 RQ4 的
  「PDF 與結構化資料交叉驗證」與「資料衝突與拒答」。
- **注意**：`t187ap17_L` 的 `營業收入(百萬元)` 單位是**百萬元**，
  而 `t187ap06_L_ci` 的 `營業收入` 是**千元** → 這是天然的 unit trap，
  正好用來測 unit accuracy。
- **狀態**：ACCEPTED (2026-07-31)

## D-012 協議修訂：新增 3 份 FY2024 財務報告書（7 → 10 份文件）

- **背景（實測發現，非事前預期）**：
  1. **從 FY2024 起，股東會年報不再內含合併財報。** MOPS 對該年度只列**一個**檔案，
     所以不是分檔上傳，是財報變成**另一份申報**（資料類型「財務報告書」→
     細節「IFRSs合併財報」→ 季別「第四季」）。結果頁甚至直接提供
     「查詢財務報告書」按鈕。
     - `2330-FY2024-AR`（91 頁）：有 `公司治理`／`風險事項`，**0 個財報標記**
     - `2882-FY2024-AR`（248 頁）：同樣 **0 個財報標記**
  2. **`2317-FY2024-AR` 文字層不可用。** 抽出 118,681 字元，但
     `公司`／`年度`／`財務`／`營業`／`股東`／`董事` **一個都沒出現** ——
     嵌入字型缺 ToUnicode 對照表，抽出的是 glyph code。
- **決策**：
  - **新增** `2330-FY2024-FS`、`2317-FY2024-FS`、`2882-FY2024-FS`
    （`doc_type: financial_report`）→ 宣告文件 **7 → 10 份**，仍在協議的 5–10 份內。
    實測三份皆 **usable，3/3 財報標記齊全**（124／202／402 頁）。
  - **保留** `2317-FY2024-AR` 在宣告清單中，標記 `usable=False` 並記錄原因，
    但**不從它出題**。
- **為什麼保留不可用的文件**：「7 份公開年報中有 1 份文字層不可用」
  本身就是 feasibility 發現。刪掉紀錄等於刪掉發現。
  `twfi.protocol.DECLARED_DOCUMENTS` 用 `usable` 欄位區分，
  `USABLE_DOCUMENTS` 才是出題來源。
- **為什麼這反而是更好的設計**：真實分析師本來就得同時讀年報（敘述）與財報（數字）。
  這個修訂讓 pipeline 更貼近真實工作流，並天然產生 `cross_document` 題型的場景。
- **合法性**：協議此時仍為 `1.0.0-draft`，**未 freeze**，因此修訂合法。
  Freeze 之後同樣的修訂就必須改 `protocol_version` 並重跑全部 locked evaluation。
- **每家 locked 公司仍有兩個年度的可用證據**：
  FY2023 完整年報 ＋ FY2024 財報（2330／2317／2882）。
- **狀態**：ACCEPTED (2026-07-31，資料取得後)

## D-014 Chart route 的圖表候選規則（取代任意上限）

- **問題**：向量分群在 8 份可用文件上找到 **1,744 個圖表區**。
  若全部送 VLM 生 caption，以每張 ~5 秒估算是 **~145 分鐘** GPU 時間，
  而且大部分是不該進 chart route 的東西。
- **先量測再決定**（不是先設上限）：
  - 只用「附近有數字標籤」過濾 → 1,744 → 1,359（**僅減 22%**）。
    原因：財報頁面到處都是數字，這個特徵分不開。
  - 檢查分布後發現真相：得分最高的「圖表」是 **253 個數字標籤、529 條路徑、
    31 萬面積** —— 那是**有框線的表格**。向量分群找到的大多是表格格線，
    而表格在每個密度指標上都**贏過**真圖表。
- **決策**：`chart_candidates()` 依序套用兩個排除：
  1. **與已偵測表格重疊 ≥50% 的區域是表格**，不是圖表。
     判準來自 pdfplumber 自己的輸出，不是另一組門檻。
  2. **附近沒有數字標籤的是裝飾**（logo、封面美術、分隔線）。
  順序很重要：只用 (2) 會留下表格而丟掉真圖表。
- **量測結果**：**1,744 → 503**（減 71%），caption 成本 ~145 分 → **~42 分**。
  因此**不需要任意上限**。
- **不得無聲截斷**：`AssembledDocument.discarded_figures` 記錄被排除的數量，
  並進入 parse stats。brief 要求「若 pipeline 限制了覆蓋範圍，必須 log 被丟掉的東西」。
- **限制**：這個規則會漏掉「沒有數字標籤的圖表」（純示意圖、流程圖）。
  那類圖本來就答不出數值題，但若 locked set 有 chart_value_trend 題目落在
  這種圖上，會被記為 retrieval 失敗而非資料限制 —— 標註時必須避開。
- **狀態**：ACCEPTED (2026-07-31，量測後)

## D-015 數值層：不猜單位，不挑來源，不寫自由 SQL

- **不猜單位**：`t187ap14_L`（各產業EPS統計）的 `營業收入` 欄位**沒有任何單位標註**。
  TWSE 財務數據慣例是千元，但「慣例」是猜測。該欄位以 `unit=None` 載入 →
  `LineItem.is_usable` 為 False → 任何計算都會被 `UnitMismatchError` 擋下。
  **載入但不可用**比「假設千元」誠實：後者會產生一個看起來正確、實際錯 1000 倍的數字。
- **不挑來源**：同一個 `(公司, 期間, account)` 可能有多筆。
  `store.require()` **拒絕自行選擇**，並在錯誤訊息中列出每個候選的
  `statement/unit=value (source)` 與消歧方式。實測觸發：2330 的 `營業收入`
  同時存在於損益表（千元）與營益分析（百萬元）。
- **不寫自由 SQL**：只有 5 個 template（`lookup`／`difference`／`growth_rate`／
  `ratio`／`cross_source_check`）。router 只能選 template 並填參數。
  template 未覆蓋的問題是 `TemplateMissError` —— 一個**可計數的能力邊界**，
  而不是一個無法重現的錯誤答案。
- **金控的發現要更精確**：不是「金控沒有營業收入」，而是
  **「它的損益表沒有這一行，但一個彙總 endpoint 合成了一個，兩者不是同一個量」**。
  實測：`t187ap14_L` 對 2882 回報 `營業收入 = 72,538,053`（無單位），
  而 `t187ap06_L_fh` 完全沒有這個欄位。
  跨公司比較這兩個數字會錯，而且**沒有任何單位檢查抓得到**——
  這是為什麼 account 詞彙表按 statement 分開。
- **產業分類只信該信的來源**：`_ci`／`_fh` 是 TWSE 按產業分開發布的，有權威性；
  `t187ap17_L`／`t187ap14_L` 是跨產業彙總。若讓後者決定產業別，
  2882 會被重新標記為一般業，抹掉整個 hard case。
  `DatasetSpec.declares_industry` 記錄這個區別。
- **狀態**：ACCEPTED (2026-07-31，載入真實資料後)

---

## D-016 Gold 標註：作者只能是人，來源不能是被測的抽取器

- **問題**：協議要求 `annotator = "human"`，但實作是由 LLM（Claude）協助進行的。
  若由 LLM 產生答案再蓋上 `human`，那是**偽造整個研究賴以成立的欄位** ——
  比任何程式 bug 嚴重，因為它讓事前註冊變成裝飾，而報告會包含一句假話。

- **更尖銳的問題（實作時才發現）**：協議原本只禁「gold 不得由 candidate system 產生」，
  但真正的陷阱在別處。**若 `table_cell` 的 gold 值來自本 repo 自己的表格抽取器，
  它與正在被評測的 F1／F4 因子是循環的**：抽取器抽錯 → gold 錯 → candidate 用同一個
  抽取器 → 它會「答對」一個錯答案。量到的增益是「用 parser 評測自己」的假象。
  這個循環**沒有任何單元測試抓得到**，因為兩邊都「一致」。

- **決定**：
  1. `GoldRecord.annotator` 的型別是 `Literal["human"]`。沒有第二個可填值，
     所以沒有任何程式路徑（包括之後趕時間寫的）能產生機器署名的 gold record。
  2. 新增 `answer_provenance`，只允許 `human_read_pdf` 與 `official_structured`。
     **本 repo 的抽取器在型別上不可表示** —— 不是「不建議」，是寫不出來。
  3. 草稿走另一條型別 `DraftItem`，**根本沒有 `answer` 欄位**。
     它是「指向證據的指標」，不是「少填一欄的 gold record」，不能靠加 key 升級。
     草稿檔在 `data/evaluation/worklist/`，與 gold 檔分離。
  4. `parse_record` 對 `annotator != "human"` 直接 raise，並在錯誤訊息裡說明
     草稿該放哪裡 —— 讓下一個人不必重新推導這個結論。

- **分工**：判斷類題型（narrative／chart／cross_page／cross_document／unanswerable／
  table_cell／cross_period_comparison）由人對照原始 filing 產生答案。
  只有 `numeric_calculation` 可由官方 OpenAPI 機械建置並自動重驗 ——
  該答案的真值任何人都能重跑查驗，人工轉錄只會增加錯誤而非增加準確度。
  工具負責預填機械欄位與切出證據 crop，**不得產生答案**。

- **推論出的限制**：`cross_period_comparison` 需要 FY2023 vs FY2024，
  但 OpenAPI 只有當期快照（D-011／§8 發現 1）→ 這 4 題的歷史值只能是 `human_read_pdf`。

- **狀態**：ACCEPTED (2026-07-31，使用者拍板「按題型分工」)

---

## D-017 可讀性的分母錯了，而題型能力必須逐份文件量測

- **怎麼發現的**：不是靠 review，是靠一個「不該是零」的結果。標註工具在
  `2330-FY2023-AR` 找到 5 個證據 slot，在 `2330-FY2024-FS` 找到 **0 個** ——
  而那份文件整本就是報表。追下去才發現文件本身的問題。

- **儀器的錯**：`quality.py` 的 `readable_ratio` 分母是 `pages_with_text`。
  完全沒有文字的頁面**同時離開分子與分母**，所以**任何數量的純圖片頁都無法拉低分數**。
  這個儀器**結構上偵測不到純圖片頁**。它把 `2330-FY2024-FS` 判為 **100% 可讀**。
  更糟的是，`tests/test_quality.py` 有一個測試叫
  `test_blank_pages_do_not_count_against_readability`，**把這個 bug 寫成了預期行為**。

- **文件的事實**：`2330-FY2024-FS` 有 11 頁無文字層，最長連續段 **pp.7–15（9 頁）**。
  以財報排版位置，那是**四大報表** —— 夾在會計師查核報告與附註（p18「編製基礎」）之間。
  修正後：**91% 可讀**，判定 `statements_not_machine_readable`。

- **這是可行性發現，不是麻煩**：只讀報表表格的系統在這份文件上找不到 FY2024 營收；
  讀附註的系統找得到 —— 它在 **p55「附註二一 營業收入」**，旁邊就是 FY2023 比較數。

- **修法**（兩個比率各司其職，因為它們測的是不同的失效）：
  - `readable_ratio` 分母改為**全部頁數** —— 沉默必須算進去。
  - `legible_text_ratio` 保留舊分母，回答較窄但仍有用的問題：**有文字的頁面裡有幾成看得懂**。
    亂碼會動這個數字，缺文字層不會。
  - 新增 `image_only_runs` 與 `statements_not_machine_readable` 判定。
    **長度比數量重要**：散落的空白頁是封面與隔頁，連續一打是抽不到的結構區塊。

- **題型能力逐份量測**（`scripts/check_question_sources.py` ＋ `eval/sources.py`）：
  單一 `usable` 旗標太粗，無法拿來出題。我的第一版推導有三個錯，都是量測後才發現：
  1. **忽略文件層級判定** → `2317-FY2024-AR`（**0 頁可讀**）拿到 2 個題型，
     因為抽取器在壞掉的文字層上仍「找到」66 個表格與 47 個圖表。
     **那些是亂碼格線與無標籤線稿，是 artefact 不是證據。**
     現在判定是閘門，在讀任何計數之前就先擋。
  2. **把「報表不存在」與「報表不可讀」混為一談** → `2330-FY2024-AR` 被標成文字層有問題，
     但它是 FY2024 股東會年報，**依設計就不含報表**（D-012）。
     改為三態 `readable / image_only / absent_by_design`。
  3. **單位門檻沿用表格門檻** → `2330-FY2024-FS` 以 3 個帶單位表格通過數值題，太薄。
     實測十份文件：帶單位表格 0–104，表格總數 55–513 —— **多數抽出的「表格」是排版而非報表**。
     單位門檻獨立為 5。

- **對 P5 的實際約束**（`results/runs/question_sources.json`）：
  - **鴻海只有 `2317-FY2024-FS` 能出題**，兩份年報歸零 →
    2317 的 narrative 題只能來自財報附註散文，不是年報敘述。
  - **`2330-FY2024-FS` 不能出數值題** → 2330 的數值題須來自 `2330-FY2023-AR`（30 個帶單位表格）。
  - `unanswerable` **不是文件屬性**，任何文件的能力清單裡都不會有它。
    而且能力最差的文件是最糟的取材處 —— 拒答會因為錯誤的理由而正確。

- **狀態**：ACCEPTED (2026-07-31，protocol 尚未 freeze，依量測修正)

---

## 全部待確認事項已解決

2026-07-31 使用者拍板：D-002 自建 layout parser、D-003 `qwen3.6:27b` 文字＋圖表共用、
D-004 照原表。無未決問題。
