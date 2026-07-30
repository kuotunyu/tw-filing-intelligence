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

---

## 全部待確認事項已解決

2026-07-31 使用者拍板：D-002 自建 layout parser、D-003 `qwen3.6:27b` 文字＋圖表共用、
D-004 照原表。無未決問題。
