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
- **狀態**：ACCEPTED (2026-07-31) — 需使用者確認

## D-003 模型：各一個，全部本機已有權重

| 角色 | 模型 | 來源 | 精度 |
|---|---|---|---|
| Embedding | `BAAI/bge-m3` | HF cache（已存在） | fp16 |
| Reranker | `BAAI/bge-reranker-v2-m3` | HF cache（已存在） | fp16 |
| Local VLM | `Qwen/Qwen3-VL-8B-Instruct` | HF cache（已存在） | bf16 |
| Generation | `Qwen/Qwen3-VL-8B-Instruct`（text-only path） | 同上 | bf16 |

- **理由**：
  1. 四個角色**零新下載**，取得成本與可重現性最好。
  2. 三個模型都對繁體中文有良好支援（bge-m3 多語、Qwen3-VL 中文與圖表理解強）。
  3. Generation 與 VLM 共用同一組權重 → 只需一組 8B 常駐，VRAM 從
     ~17GB(VLM)+~15GB(另一個 8B 文字模型) 降到 ~17GB，在 24GB 卡上可行。
     Protocol 說「最多」一個 VLM＋一個 generation model，共用不違規。
- **替代**：ollama `qwen3-vl:8b`（Q4_K_M，6.1GB）作為低 VRAM fallback；
  `qwen3.6:27b` 品質可能更好但 17GB 與檢索模型衝突且量化程度不同，不採用。
- **限制**：decoding 固定 `temperature=0.0, top_p=1.0, seed=20260731`。
  revision 由 `scripts/pin_models.py` 寫入 `configs/models.lock.json` 並納入 lock hash。
- **狀態**：ACCEPTED (2026-07-31) — 需使用者確認

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
- **狀態**：ACCEPTED (2026-07-31) — 需使用者確認

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

---

## 待確認（需使用者拍板，預設先照上面走）

- **Q1** D-002 parser 選擇：自建 rule-based layout parser（預設）vs 引入 `docling`。
- **Q2** D-003 generation model 與 VLM 共用 Qwen3-VL-8B（預設）vs 另用
  `qwen3.6:27b` / `gpt-oss:20b` 走 ollama。
- **Q3** D-004 公司／年度組合是否照上表（預設）。
