# PROGRESS LOG

> **回來接手先讀這一頁。** 下面的「目前狀態」與「下一步」永遠是最新的。
> 每個 session 結束前必須更新本檔並 commit。

---

## 目前狀態

- **Phase**：P0–P4 🟢 完成。**P5 🟡 進行中：36/72 已標註**（probe 5 ／ **locked 31/36** ／ dev 0 ／ challenger 0）。
  **標註方式已於 2026-07-31 修訂（D-019）**：模型起草 ＋ 固定種子人工抽樣稽核。
  目前 locked 組成：fully_human 19／question_model_chosen 7／answer_model_drafted 5／
  needs_audit 12／audited 0。**報告必須印出這組數字。**
  只剩 `chart_value_trend` 5 題 —— 建議人工，因為「LLM 讀圖表」去考「LLM 讀圖表」
  是這裡最接近循環的組合。
- **發布狀態**：已 push 到 `https://github.com/kuotunyu/tw-filing-intelligence`（public）。
  commit 作者一律 `kuotunyu <61350295+kuotunyu@users.noreply.github.com>`，
  **不得加 `Co-authored-by:` trailer**（見 `CLAUDE.md` 規則 9）。
- **資料現況**（`results/runs/document_quality.json`）：

  可讀率**已於 2026-07-31 重新量測**（D-017：舊分母是 `pages_with_text`，
  純圖片頁同時離開分子與分母，所以偵測不到）。能力欄來自
  `results/runs/question_sources.json`。

  | 文件 | 頁 | 可讀% | 報表狀態 | 可出題型 |
  |---|---|---|---|---|
  | 2412-FY2023-AR | 277 | 95% | readable | 7 |
  | 1301-FY2023-AR | 362 | 96% | readable | 7 |
  | 2330-FY2023-AR | 345 | 98% | readable | 7 |
  | 2330-FY2024-AR | 91 | 100% | absent_by_design | 7 |
  | **2330-FY2024-FS** | 124 | **91%** | **image_only pp.7–15** | 7 |
  | **2317-FY2023-AR** | 705 | **21%** | readable | **0** |
  | **2317-FY2024-AR** | 134 | **0%** | absent | **0** |
  | 2317-FY2024-FS | 202 | 95% | readable | 7 |
  | 2882-FY2024-AR | 248 | 100% | absent_by_design | 7 |
  | 2882-FY2024-FS | 401 | 99% | readable | 7 |

  **8/10 可用**，但可用性不是二元的：

  - **`2330-FY2024-FS` 的四大報表無文字層**（pp.7–15）。FY2024 營收讀得到，
    但在 **p55「附註二一 營業收入」**，旁邊就是 FY2023 比較數。
    只讀報表表格的系統在這份文件上找不到；讀附註的找得到 —— **這是可行性發現**。
    （原本量到「65 個表格只有 3 個宣告單位」，那是 D-018 的偵測缺陷，
    修正後為 64 個，此文件可出數值題。）
  - **鴻海只有 `2317-FY2024-FS` 能出題**。兩份年報歸零 →
    2317 的 narrative 題只能來自財報附註散文，不是年報敘述。
  - `absent_by_design` 不是缺陷：FY2024 股東會年報依規定不含報表（D-012），
    數字在配對的財務報告書裡。
- **P3 parser 現況**：2895 頁，F0 `0.003s/頁`、F1 `0.008s/頁`，
  candidate chunk **100%** 帶 section path（fixed window 0%）
- **Phase**：P3（parsing 層）— 🟢 **完成**
  - ✅ `types.py` 文件模型（BBox／Span／Line／Block／ParsedDocument／Chunk，
    全部 frozen，帶 page＋bbox 以承載 citation 契約）
  - ✅ `baseline.py`（F0：PyMuPDF 純文字 ＋ 固定 800/100 chunk）
  - ✅ `layout.py`（candidate：字級統計 heading／編號樣式／section tree／
    running footer 偵測／reading order），**結構判斷是純函式**，不需 PDF 即可測
  - ✅ `chunker.py`（不跨 section、不切表格、heading path 前綴、短塊回折）
  - ✅ 合成 PDF fixture（3 頁、三層標題、跨頁表格、括號負數、單位列、圖表區）
  - ✅ `tables.py`（pdfplumber `text` strategy ＋ `UnitSpec` ＋ 跨頁接續）
  - ✅ `figures.py`（向量繪圖密度分群 ＋ raster image ＋ caption ＋ crop 渲染）
  - ✅ `results/runs/parse_stats.json`、`results/runs/document_quality.json`
  - ✅ `document.py`（三個抽取器合併 ＋ 重疊解析 ＋ section 繼承 ＋ 統一 reading order）
  - ✅ chart 候選規則（D-014）：**1,744 → 503**（減 71%），
    caption 成本 ~145 分 → ~42 分，且不靠任意上限
  - **P3 完成**
- **Phase**：P4（數值層）— 🟢 **完成**
  - ✅ `numeric/amounts.py`（括號負數／全形／placeholder→None／`仟元`→`千元` 正規化，
    全程 `Decimal`；與未來的 grader 共用同一份解析，避免兩邊對 `(12,345)` 的理解不一致）
  - ✅ `numeric/schema.sql` ＋ `store.py`（DuckDB；PK 含 `source_kind`）
  - ✅ `numeric/calculator.py`（`difference` / `growth_rate` / `ratio`，輸出 formula ＋ operands）
  - ✅ `numeric/sql_tools.py`（5 個 template；template 外一律 `TemplateMissError`）
  - ✅ `numeric/loaders.py` ＋ `scripts/load_numeric.py`：**253 筆 figures** 載入，
    2882 正確標為 `financial_holding`（69 個 account）
  - ✅ 端對端實測：`營業毛利（毛損） ÷ 營業收入 × 100
    = 751,295,421 ÷ 1,134,103,440 × 100 = 66.25%`，兩個 operand 都附來源欄位
  - **三個被真實資料修正的設計**（詳見 `docs/DECISIONS.md` D-015）：
    1. **不猜單位**：`t187ap14_L` 的 `營業收入` 完全沒有單位標記。慣例是千元，
       但「慣例」是猜測 → 以 `unit=None` 載入且 `is_usable=False`。
       **載入但不可用**比假設誠實；假設會產出一個看起來精確、卻差一千倍的數字。
    2. **不挑來源**：`require()` 遇到同一 key 有多個候選就拒絕，並列出每個候選的
       `statement/unit=value` ＋ 消歧方式。真實觸發：2330 的 `營業收入`
       同時存在於損益表（千元）與營益分析（百萬元）。
    3. **`declares_industry`**：`t187ap14_L` 涵蓋所有上市公司（含金控），
       當成一般業載入會把 2882 重新標記、抹掉它之所以是 hard case 的差異。
       只有 per-industry 的 `_ci`／`_fh` 可以決定產業別，彙總 endpoint 不行。
  - ⬜ **未做**：把已驗證的表格數值以 `source_kind="extracted_table"` 載入
    （FY2023／FY2024 歷史數值）。刻意延後到 P5 確定需要哪些 account。
- **Phase**：P2（資料取得）— 🟢 **完成**（宣告 10 份、全部取得並 hash、8 份可用）
  - ✅ 9 個 OpenAPI dataset 已取得並記入 `acquisition.lock.yaml`
    （swagger 306KB／t187ap03_L 1092 列／t187ap05_L 1082 列／
    t187ap06_L_ci 1045 列／**t187ap06_L_fh 13 列**／t187ap07_L_ci 1045 列／
    t187ap07_L_fh 13 列／t187ap14_L 1081 列／t187ap17_L 1051 列，共 5.3MB）
  - ✅ 10 份 filing 全部由使用者依 `fetch_documents.py` 的指示自 MOPS 取得並 hash
    （`acquisition.lock.yaml` 共 19 筆 artifact）。`verify_manifests.py` integrity 全過。
  - ⚠️ **取得路徑上我錯了三次，每次都是使用者拿真實頁面糾正**（見下方 Session 日誌）：
    資料類型沒有「年報」選項（在股東會相關資料底下）／下拉只顯示中文名／
    F04 與 F18 優先序被我寫反。**下次改任何抓取指示前先看實際頁面，不要憑推測。**
- **Phase**：P1（資料來源探勘 ＋ manifest schema）— 🟢 **完成**
- **P1 的三個關鍵發現**（改變了 P2/P4 設計，詳見 `docs/DATA_PROVENANCE.md §8`）：
  1. **TWSE OpenAPI 是單期快照**（`t187ap06_L_ci` 1045 列全部 `年度=115 季別=1`）
     → 歷史數值不能靠 OpenAPI
  2. **一般業 vs 金控業是兩套 schema**（資產負債表 26 vs **60** 欄）
     → 2882 是真 hard case，numeric route 要處理 per-industry schema
  3. **新版 MOPS 是 JS SPA**（`/mops/web/*` 只回 65 bytes JS bootstrap）
     → 文件走**人工放置 ＋ SHA-256**，這符合 G1 而非 G1 的例外
- **P2 追加實證**：`t187ap06_L_fh` 只有 13 列（全國 13 家金控），
  `2882 國泰金` 25 欄且 **`'營業收入' in row` → False**。
  「查營收」對一般業理所當然，對金控直接查不到欄位 →
  numeric route 必須**報錯或拒答，不能拿別的欄位硬湊**。
- **Phase**：P0（Repo scaffold ＋ 規劃文件）— 🟢 **完成**
- **Protocol 狀態**：`1.0.0-draft`，**尚未 freeze**（可以修改）
- **Locked set 狀態**：標註中，**31/36**。只缺 chart_value_trend 5 題。
- **Toolchain 狀態**：`uv sync --extra dev` OK（Python 3.13.13）、
  `ruff check` 乾淨、`ruff format --check` 乾淨、`mypy` strict 乾淨（50 個 source file，含 `scripts/`）、
  `pytest` **960 passed / 1 skipped**、coverage **97.88%**（gate 85%）
- **最後更新**：2026-07-31

## 下一步（照順序）

1. **P5（關鍵路徑）**：gold 標註（locked 36 ＋ dev 15 ＋ probes 5 ＋ challenger 16）。
   **純 CPU，不需要 GPU。**
   - 出題來源以 `results/runs/question_sources.json` 為準，**不是** `USABLE_DOCUMENTS`
     ——後者是二元旗標，不知道 `2330-FY2024-FS` 不能出數值題（D-017）。
   - gold answer **不得由 candidate 產生**（型別強制，candidate 不可表示）。
     `annotator` 與 `question_author` 各自具名；模型起草需人工抽樣稽核（D-019）。
   - **72 題全部需要人讀 PDF。** OpenAPI 只有 FY2026Q1，與文件集（FY2023／FY2024）
     交集為空，所以沒有可機械建置的子集（D-016）。
   - `answer_provenance` 不得是本 repo 的抽取器 —— 那與 F1／F4 循環（D-016）。
   - **已有工具**：`make_worklist.py --for probe` 產出 25 個證據 slot；
     `validate_gold.py`、`check_leakage.py` 是 freeze 前的閘門。
   - ✅ **probe 5 題完成**；**locked 31/36 完成** —— table_cell 5／cross_period 4／
     unanswerable 4／numeric_calculation 5／narrative_fact 6／cross_page 4／
     cross_document 3。**只剩 `chart_value_trend` 5 題。**
     檔案：`data/evaluation/locked/probes.jsonl`、`gold.jsonl`。
     注意 probe **不是** unanswerable：G8 強制清空檢索，所以好的 probe 是
     **模型很可能記得答案**的題目。
   - **下一個要人做的事**：`audit_gold.py --set locked` 抽出的 8 題稽核
     （種子 20260731 決定，我無法挑）。目前抽到 LOCK-0020…0024、0027、0028 等。
   - **標註流程已定型**，剩下 41 題照這個走：
     `make_worklist.py` 定位 → `render_pages.py` 渲染成圖 →
     人**看圖**讀數字（不看抽取文字）→ `fill_gold.py --set <set>` 填表單（不碰 JSON）→
     `validate_gold.py` ＋ `check_leakage.py` ＋ `verify_gold_answers.py`。
   - **一批 4–5 題最順**：我一次給清單（哪張圖／哪一列／哪一欄），使用者一次讀完回報，
     我填表並跑三個檢查。逐題往返太慢。
   - **刻意重用頁面**：一張渲染圖出 2–3 題，切換成本大幅下降。
     locked batch 2 完全沒有開新圖。
   - 剩 **41 題**（locked 5 ＋ dev 15 ＋ challenger 16）。
   - **每一題的判斷都必須有一張圖可看。** unanswerable 題原本只給搜尋結果，
     使用者拒絕在看不到的東西上簽名 —— 那是對的，四題因此各配一張證據圖。
   - 使用者決定：**先把 locked 做完**，dev／challenger 之後再決定是否減量。
2. **P4 收尾**（P5 之後才做）：把 P5 確定需要的 account，以
   `source_kind="extracted_table"` 載入 FY2023／FY2024 歷史數值（OpenAPI 只有當期）。
   刻意排在 P5 後面 —— 先知道要哪些 account，才不用載入整份年報的所有表格。
3. **P6**：檢索（bge-m3 ＋ BM25 ＋ RRF ＋ reranker）—— **這裡開始需要 GPU**，
   跑之前先 `nvidia-smi` 確認 SafeSynth 沒在用。

**不阻塞但可選**：XBRL 7 份仍未提供（`optional`）。有的話歷史結構化數值
來源升級為官方 XBRL；沒有的話報告要把說法降級為「已驗證結構化數值」。

---

## Phase 狀態表

| Phase | 名稱 | 狀態 | 完成日 | 備註 |
|---|---|---|---|---|
| P0 | Repo scaffold ＋ 規劃文件 | 🟢 完成 | 2026-07-31 | 文件 ＋ 骨架 ＋ toolchain 全綠 |
| P1 | 資料來源探勘 ＋ manifest schema | 🟢 完成 | 2026-07-31 | 3 個關鍵發現，見上方 |
| P2 | 資料取得 ＋ provenance ＋ SHA-256 | 🟢 完成 | 2026-07-31 | 10 份宣告全部取得；8 份可用 |
| P3 | Parsing（baseline ＋ layout-aware） | 🟢 完成 | 2026-07-31 | 含 tables／figures／assembly |
| P4 | 數值層（DuckDB ＋ deterministic SQL） | 🟢 完成 | 2026-07-31 | 253 筆 figures 已載入 |
| P5 | Gold set 標註 | 🟡 進行中 | — | **36/72**（probe 5 ＋ locked 31）。關鍵路徑，CPU |
| P6 | Retrieval ＋ rerank | ⚪ 未開始 | — | GPU |
| P7 | Chart route | ⚪ 未開始 | — | GPU |
| P8 | Router ＋ answer/citation | ⚪ 未開始 | — | GPU |
| P9 | Eval harness ＋ metrics | ⚪ 未開始 | — | 部分 GPU |
| P10 | Freeze → locked run → gate → report | ⚪ 未開始 | — | GPU |

圖例：⚪ 未開始 / 🟡 進行中 / 🟢 完成 / 🔴 卡住

---

## 環境事實（2026-07-31 實測）

- 目標目錄原本**存在但完全空的**（無 `.git`、無檔案）→ 視為全新建立，未覆蓋任何內容。
- `git 2.41.0.windows.1`、系統 `Python 3.10.9`（無 CUDA torch）、`uv 0.11.18`
- ⚠️ **踩到的坑**：repo 路徑含非 ASCII（`CC_github部隊`）。Python ≤3.12 的 `site`
  模組以系統 locale（cp950）讀 editable install 的 `.pth`，直接
  `UnicodeDecodeError`，`uv run` 全掛。→ 專案固定 **Python 3.13**（3.13 起 `.pth`
  以 UTF-8 讀取）。詳見 `docs/DECISIONS.md` D-001。實測 3.13.13 正常。
- GPU：**RTX 4090 24564 MiB**，當下僅桌面程式佔用 ~1396 MiB，
  **沒有** SafeSynth 或其他訓練任務在跑 → GPU 可用
- C: 磁碟可用空間 ~150 GB
- HF cache 已有：`BAAI/bge-m3`、`BAAI/bge-reranker-v2-m3`、`Qwen/Qwen3-VL-8B-Instruct`、
  `Qwen/Qwen3-4B-Instruct-2507`、`Qwen/Qwen3-Embedding-0.6B`、PaddleOCR PP-OCRv6、
  `vidore/colqwen2-v1.0-hf`
- ollama 已有：`qwen3-vl:8b`(901cae732162)、`qwen3.6:27b`、`gemma3:12b`、
  `gpt-oss:20b`、`qwen3:4b` 等
- **結論**：四個角色的模型權重都已在本機，**零新下載**

---

## 決策待辦

**全部已拍板（2026-07-31）**，無阻塞項目。

| # | 問題 | 決定 |
|---|---|---|
| Q1 | Parser candidate | **自建 rule-based layout parser**（D-002）。不引入 docling；report 必須註明「本輪未驗證 learned layout model」 |
| Q2 | Generation ／ VLM | **`qwen3.6:27b` digest `a50eda8ed977`（ollama, Q4_K_M）文字與圖表共用同一模型**；數值走 SQL；`qwen3-vl:8b` 只做 freeze 前 chart challenger；`gpt-oss:20b` 不進 pipeline（D-003 / D-009） |
| Q3 | 公司／年度組合 | **照原表**（D-004）：DEV 2412+1301 FY2023；LOCKED 2330+2317 FY2023/FY2024 ＋ 2882 FY2024 |

實測確認（2026-07-31）：`ollama show qwen3.6:27b` →
`capabilities: completion / vision / tools / thinking`、`architecture qwen35`、
`27.8B`、`Q4_K_M`、`context length 262144`。
**確認具備 vision**，所以「文字＋圖表共用同一模型」在 chart route 成立。
ollama 版本 `0.32.0`。

---

## 已知風險 / 未解問題

| # | 風險 | 目前判斷 |
|---|---|---|
| ~~R1~~ | ~~MOPS 年報 PDF URL 非決定性~~ | **已解決**：實測後決定不自動化，走人工放置 ＋ SHA-256（D-010）。理由是規則而非能力 |
| R7 | 若使用者未提供 XBRL，歷史結構化數值來自我們自己的表格擷取 | RQ2 仍成立，但 report 必須把「官方結構化資料」降級為「已驗證結構化資料」（D-010） |
| R2 | 年報 300+ 頁，VLM caption 成本高 | figure crop 數量上限 ＋ embedding cache ＋ cold/warm 分開量 |
| R3 | `qwen3.6:27b` Q4_K_M 17GB ＋ KV cache ＋ 檢索模型 2.2GB ≈ 20–21GB，逼近 G10 的 22GB | `num_ctx=8192`、crop 最長邊 1024、每題 ≤3 crop；必要時檢索模型 offload CPU。**gate 不因模型放寬** |
| R6 | Q4_K_M 量化可能影響敘述題品質 | 數值題走 SQL 不受影響；敘述題影響會如實反映在指標，不換模型補救 |
| R4 | 我自己標註 gold 的偏誤 | 答案必須指回頁碼/bbox/row；標註前不看 pipeline 輸出 |
| R5 | 金控 2882 報表結構特殊，numeric route 可能失敗 | 視為 hard case 如實記錄，不換公司 |

---

## Session 日誌

### 2026-07-31 — Session 7（P4 commit ＋ 發布到 GitHub）

**做了什麼**

- **P4 commit**（`cc38147`）：數值層完成，ruff／mypy 全綠、804 passed、coverage 97.83%
- 修掉本檔三處自我矛盾：P3 同時標成「進行中」與「完成」、
  P4 在 phase 表出現兩次（一列完成一列未開始）、P2 還寫著「等使用者放 7 份 PDF」
- `README.md` 加「現況」段：**明講尚未 freeze、`results/feasibility/` 是空的、
  本 repository 目前不宣稱任何可行性結論**。並說明「8/10 可用」的真正原因。
- **`CLAUDE.md` 規則 9 改寫**：原本禁止建立 GitHub remote，與實際做法衝突。
  改為允許 push 到 `origin`、仍禁 tag／release／deploy，
  並新增常駐規則：**commit 不得帶 `Co-authored-by:` trailer**。
- **發布**：`kuotunyu/tw-filing-intelligence`（public），22 commits／335 objects／349 KB。
  push 前稽核：無 PDF／模型權重／DuckDB／`.env` 被追蹤；98 個檔案，最大 `uv.lock` 172 KB。

**Contributors 只留 kuotunyu 的做法**

`git filter-branch` 一次處理兩件事：`--msg-filter` 刪掉 20 個 `Co-authored-by:` trailer，
`--env-filter` 把作者與 committer 都設成 `61350295+kuotunyu@users.noreply.github.com`。
用 GitHub noreply 位址而非學校信箱，是為了保證歸屬正確、同時不把個人信箱
永久公開在 public repo 的 commit 紀錄裡。改寫在 push 前完成，所以不需要 force push。

**兩個教訓（都是我自己的錯，記下來免得重犯）**

1. **驗證用的 pattern 必須跟過濾用的 pattern 一致。**
   `sed` 用行首錨定 `/^[Cc]o-.../d`，但我的計數器用了未錨定的
   `grep -ci 'co-authored-by'`，於是把 commit 訊息裡一句「commits carry no
   Co-authored-by trailer」的**散文**也算進去，報出「remaining: 1（must be 0）」的假警報。
   使用者是在門檻顯示未通過的情況下 push 的。
2. **`git log --all` 會走訪 `refs/original/`。**
   `filter-branch` 把改寫前的 commit 留在那裡當備份，所以用 `--all` 掃描會看到
   20 個「還沒清掉」的 trailer —— 那是備份，不是 `main`。
   驗證改寫結果要用 `git log main`，並用 `git ls-remote origin` 確認 remote 只有一個 ref。
   確認無誤後才刪 `refs/original/` ＋ `reflog expire` ＋ `gc --prune=now`。

### 2026-07-31 — Session 1（P0）

**做了什麼**

- 確認目標目錄為空 → `git init -b main`（本機，無 remote）
- 環境探勘：git / python / uv / GPU / HF cache / ollama / 磁碟
- 寫入規劃文件：
  - `README.md`（含「不是投資建議、不是 production」聲明）
  - `LICENSE`（MIT，明確排除第三方資料）
  - `CLAUDE.md`（10 條不可違反規則 ＋ 常用指令）
  - `docs/FEASIBILITY_PROTOCOL.md`（**事前註冊協議**：資料切分、題型分布 36 題、
    gold schema、F0–F7 factor ladder、指標定義、normalization 規則、
    G1–G10 GO/NO-GO gate、執行順序）
  - `docs/IMPLEMENTATION_PLAN.md`（P0–P10 ＋ 每 phase DoD ＋ 檔案佈局 ＋ 風險表）
  - `docs/DECISIONS.md`（D-001…D-008 ＋ 三個待確認問題）
  - `docs/DATA_PROVENANCE.md`（來源、allowlist、取得規則、manifest schema、
    人工 fallback）
  - `docs/THREAT_MODEL.md`（T1–T11：prompt injection／leakage／SSRF／作弊防護等）
  - `.gitignore`（原始 PDF 與權重不進 git）
  - `.claude/skills/`（專案層級 skills）

- 建立可運作的最小程式骨架與 toolchain：
  - `pyproject.toml`（uv、ruff、mypy strict、pytest ＋ coverage gate 85%）
  - `src/twfi/{__init__,errors,paths}.py`、`src/twfi/io/hashing.py`、
    `src/twfi/eval/protocol_lock.py`（freeze／verify 機制，含 tamper 偵測）
  - `tests/conftest.py`：**autouse offline guard**（非 loopback socket 直接拋錯）
    ＋ 憑證環境變數清除 ＋ 強制 `HF_HUB_OFFLINE`／`TRANSFORMERS_OFFLINE`
  - `tests/test_repo_invariants.py`：把「不是投資建議／不是 production」、
    gate G1–G10、題型分布、F0–F7、DEV/LOCKED 公司互斥、`.gitignore` 排除 PDF、
    src 不讀 dotenv 等**寫成會失敗的測試**，避免文件漂移
- 結果：`ruff check` 乾淨、`ruff format --check` 乾淨、`mypy src` strict 乾淨、
  `pytest` **150 passed / 1 skipped**、coverage **99.61%**

**決策定案後的協議更新（同一 session）**

- 實測 `ollama show`：`qwen3.6:27b` **有 vision**（`qwen35`, 27.8B, Q4_K_M, ctx 262144）、
  `qwen3-vl:8b`（`qwen3vl`, 8.8B, Q4_K_M）、ollama `0.32.0`
- 依使用者決定改寫 protocol §2.2（模型表改為 27B 文字＋圖表共用、
  `think=false`、decoding 固定）、新增 §2.3 **Chart challenger 事前決策規則**、
  §2.4/§2.5 重新編號、§5 執行順序改為 10 步（challenger → pin → leakage → freeze）
- 新增 `configs/models.yaml`（模型宣告、challenger 規則、排除清單、VRAM 預算）
- `docs/DECISIONS.md`：D-002/D-003/D-004 標記使用者確認、D-003 全面改寫、
  新增 **D-009 Chart challenger**、移除待確認區塊
- 新增 8 個 invariant 測試把「模型宣告 ↔ protocol 一致」「challenger 規則 ≥10pp
  且 outcome 未被手改」「decoding 決定性」「VRAM 預算低於 gate」變成會失敗的測試
- 結果：`ruff` 乾淨、`pytest` **157 passed / 1 skipped**、coverage 99.61%

**協議自我複核發現的洞（freeze 前修正，方向只往嚴格）**

- **G2 原本沒有鑑別力**：原條件是「至少一個 hard category 相對 F0 改善 ≥10pp」，
  但單一類別只有 3–5 題 → `chart_value_trend`(5) 多對 1 題 = 20pp、
  `cross_document`(3) 多對 1 題 = 33pp ⇒ G2 幾乎必然通過，等於沒有門檻。
- 修正：G2 改為**兩個條件都要成立** —— (a) 合併 hard set（21 題）改善 ≥10pp
  （≥ 多對 3 題）＋ (b) 至少一個單類 ≥10pp。
- 新增 protocol「小樣本誠實性」節：每個指標必須輸出 `n` ＋ 分子 ＋ 分母 ＋
  **Wilson 95% CI**（只寫百分比視為 report 不完整，G9 會擋）；
  limitations 必須寫明本輪樣本量只能判斷可行性與增益方向，
  不能做精確效果量估計或宣稱統計顯著；**結果好壞都要寫**。
- 3 個新測試把上述固定住（合併 21 題、`Wilson`、小樣本節存在）。

**沒做什麼**：任何連外請求、任何 GPU 任務、任何 evaluation、任何 gold 標註。

---

### 2026-07-31 — Session 6（P3 收尾 ＋ P4 數值層）

**P3 收尾**

- `document.py`：三個抽取器合併、重疊解析、section 繼承、統一 reading order。
  抓到自己第一版的 bug：只走 layout 頁面會讓**只有框線表格的頁面**上的表格被無聲丟棄。
- **chart 候選規則（D-014）試了三次**。1,744 個圖表區、~145 分鐘 caption 成本。
  「有數字標籤」只減 22%；看分布才發現最高分的「圖表」是**有框線的表格**
  （253 標籤／529 路徑／31 萬面積）。正解不是再一個門檻，
  而是**用表格抽取器自己的輸出**排除。**1,744 → 503，成本 → ~42 分鐘**，不需任意上限。

**P4 數值層**

- `amounts.py`（`Decimal`，括號負數／全形／仟千同義／百萬元換算）、
  `schema.sql`、`store.py`、`calculator.py`、`sql_tools.py`、`loaders.py`
- **實跑載入 253 筆 figures**，2882 正確標記為 `financial_holding`（69 個 account）
- 實測 `營業毛利（毛損） ÷ 營業收入 × 100 = 66.25%`，formula 與 2 個 citation
  都指回實際來源欄位

**載入真實資料時抓到的三件事**

1. `t187ap14_L`（跨產業彙總）**會把 2882 重新標記為一般業** → 加
   `declares_industry`，只信 `_ci`／`_fh`。
2. 2330 的 `營業收入` 同時存在於損益表（千元）與營益分析（百萬元）→
   `require()` **拒絕自行選擇**，並列出每個候選與消歧方式。
3. **「金控沒有營業收入」這句話不夠精確**：
   `t187ap14_L` 對 2882 回報 `營業收入 = 72,538,053`（且**無單位標註**）。
   精確的說法是「損益表沒有這一行，彙總 endpoint 合成了一個，兩者不是同一個量」——
   而且跨公司比較這兩個數字**沒有任何單位檢查抓得到**。

**結果**：`ruff` 乾淨、`mypy --strict` 乾淨、`pytest` **804 passed / 1 skipped**、
coverage 97.83%

---

### 2026-07-31 — Session 5（P2 完成 ＋ 真實資料揭露的四個缺陷）

**使用者完成的事**：10 份文件全部人工下載放置（71.3 MB）。

**真實資料立刻揭露四個缺陷 —— 合成 fixture 全都抓不到**

1. **MOPS `資料年度` 是股東會年度**（= 財報年度 + 1）。若照查詢欄位命名，
   7 份全部會錯一年。→ 寫 `identify_documents.py` 從**文件自己**判定身分
   （MOPS 檔名 ＋ 封面交叉比對，衝突就報錯不擅自選邊）。
2. **FY2024 年報不再內含合併財報** —— 是另一份申報（財務報告書 / IFRSs合併財報）。
   MOPS 該年度只列一個檔案，所以不是分檔。→ 協議修訂 D-012，文件 7 → 10 份。
3. **heading 誤判 23,677 個**（707 頁）。第一個假設（編號樣式）**是錯的**，
   修完只降到 23,035。真因：該文件有**多個 body size**（10pt 209k 字元、
   12pt 106k 字元），單一 mode 讓 10 萬字正常本文全部變標題；
   level 還跑到 14。→ `body_font_sizes()` 取所有佔 ≥3% 字元的字級，
   以**最大者**為門檻；level 上限 6。降到 4,214（每頁 ~6，合理）。
4. **文字層部分壞掉**：2317-FY2023 的標題抽出來是 `Ψҗᇙ୍ܺ`，
   但文件層級 anchor 檢查說它 usable。→ 改成**逐頁**量測可讀比例。

**我自己的測量也錯過一次，而且值得記錄**

第一版 anchor 清單只有敘述詞彙（`公司`／`股東`／`董事`），
導致「純財報頁」被判為不可讀 —— 1301 因此顯示 53%，**差點被寫進協議當成缺陷文件**。
把財報／附註詞彙（`合併`／`資產`／`負債`／`附註`／`單位`）加進去後，1301 是 **96%**。
→ 教訓：**判定資料品質的工具本身也要被質疑**。

**協議修訂（DRAFT 未 freeze，合法）**

- **D-012**：新增 3 份 FY2024 財務報告書，7 → 10 份（仍在 5–10 內）
- **D-013**：`DECLARED_DOCUMENTS` 增加 `usable` 欄位。
  兩份鴻海年報標 `usable=False` 但**保留宣告** —— 刪掉紀錄等於刪掉發現，
  且 SHA-256 需要有地方掛。出題只用 `USABLE_DOCUMENTS`。

**結果**：`ruff` 乾淨、`mypy --strict` 乾淨、`pytest` **602 passed / 1 skipped**、
coverage 98.71%、0 個 PDF 進 git。

---

### 2026-07-31 — Session 4（P3 parsing 層，前半）

**做了什麼**

- `src/twfi/parsing/types.py`：全 pipeline 共用的文件模型。
  每個 Block／Chunk 都帶 `page` 與 `bbox`，因為 citation 契約要求
  「答案的證據必須能解析到真實的頁／表格／crop／SQL row」——
  一個無法表達「這來自哪裡」的模型會讓那個契約無法執行。
  `BBox.iou()` 就是 G4 的 `IoU ≥ 0.3` 判定基礎。
- `baseline.py`（F0）：刻意天真，但**仍然保留頁級歸屬** ——
  一個連頁碼都引不出來的 baseline 會因為與 parsing 品質無關的理由輸掉
  citation 指標，那會美化 candidate。
- `layout.py`（candidate）：字級加權統計抓 body size、
  編號樣式（`一、`→L2、`（一）`→L3、`1.2.3`→L3）、
  **重複性**而非位置判定 running header/footer、reading order 分帶排序。
  結構判斷（`classify_pages`）是**純函式**，所以邏輯直接被測，不必透過 PDF。
- `chunker.py`：三條規則各對應一個 fixed-chunk 的具體失敗
  （跨 section／切斷表格／失去 heading 脈絡），外加短塊回折。
- 合成 PDF fixture：3 頁、三層標題、跨頁表格、括號負數、單位列、向量圖表區。
  實測 PyMuPDF 內建 `china-t` 可正確寫入並抽回繁體中文。

**測試抓到的兩個真實問題（不是測試寫錯）**

1. **編號樣式繞過了句末標點檢查** → 「第二節內容，與第一節無關的敘述。」
   這種開頭帶編號的**段落**被誤判為標題，並且劫持了它之後所有內容的 section path。
   修法：把「長度上限 ＋ 不以句末標點結尾」抽成 `_could_be_heading()`，
   讓編號訊號也必須通過。已加 regression test。
2. **兩個 chunker 測試「通過但理由是錯的」** —— 1180+5 沒有超過 max_chars 1200，
   所以合併路徑從未執行（coverage 停在 85% 是證據）。
   改用 max_chars=100／99 字段落，並加一行斷言先確認「沒合併時真的是 2 塊」。

**刻意記錄的限制**

- 數字正規化會把「差一個數字」的行視為同一個 key（這才能認出 `- 1 -`…`- 9 -`），
  代價是**位於邊界區、且出現在多數頁面的編號標題**也會被當成 furniture。
  有一個測試專門把這個取捨寫下來，而不是讓它隱形。
- reading order 假設單欄；真正的雙欄版面會退化成左到右。

**結果**：`ruff` 乾淨、`mypy src` strict 乾淨、
`pytest` **474 passed / 1 skipped**、coverage **98.81%**

---

### 2026-07-31 — Session 3（P2 自動化半邊）

**做了什麼**

- **重構 manifest：宣告與紀錄分離**。原設計會讓 fetch 腳本把 provenance 寫回
  `documents.yaml`，**沖掉 P1 發現與人工下載指示的註解**。改成：
  `documents.yaml`／`structured.yaml` 手寫（沒有腳本會改它們）、
  `acquisition.lock.yaml` 由程式寫。
  副作用是設計變好：`AcquisitionRecord` 的驗證欄位全部必填，
  **「半記錄狀態」變成無法表達**，不需要 validator 去擋。
- `src/twfi/io/acquire.py`：兩種取得模式一種紀錄格式
  （`fetched` 走 PoliteClient／`manual` 走人工放置），
  HTTP client、時鐘、PDF 頁數計算全部可注入 → 離線可測
- 三個腳本：`fetch_twse_openapi.py`、`fetch_documents.py`（印精確人工指示）、
  `verify_manifests.py`（integrity ＋ coverage ＋ 產生 provenance 表）
- **實跑**：9 個 OpenAPI dataset 全部成功，5.3MB，9 次請求 0 retry
- `docs/reference/provenance_table.md`（產生檔）
- `acquisition.lock.yaml` 加入 protocol lock 的凍結清單 ——
  凍結每一個 SHA-256 才讓 G1「結果可從 raw artifacts 重建」變成可檢查的

**設計上刻意的取捨**

- 一個 endpoint 失敗**不會**丟掉其他已成功的紀錄（有測試）
- 檔案被換掉會被標為 `CHANGED` 而不是默默接受（有測試）
- 檔案沒變**不會**重新蓋時間戳（有測試）
- XBRL 標為 `required=False`：協議不依賴它，硬性阻擋整條 pipeline 是不誠實的

**結果**：`ruff` 乾淨、`mypy src` strict 乾淨、
`pytest` **333 passed / 3 skipped**、coverage **99.89%**

---

### 2026-07-31 — Session 2（P1）

**做了什麼**

- `src/twfi/io/http.py`：**全專案唯一的對外 HTTP 出口**
  - host allowlist 寫死（3 個 TWSE host）、https only、拒 IP literal／
    localhost／私有網段／`169.254.169.254`、拒 URL 內含帳密、拒非 443 port
  - **每個 redirect hop 重新驗證**（redirect 是可被攻擊者影響的輸入）
  - 每 host 最小間隔 1.5s、per-host 請求上限 40、單檔 80MB／單次 600MB
    （**串流時就中止**，且 `.partial` 一定刪除）、`Content-Length` 不符即失敗
  - retry 只對 5xx 與 transport error，指數退避 2/4/8s；**4xx 不重試**
  - `snapshot()` 產出可入 provenance 的網路行為紀錄
  - 61 個測試全走 `httpx.MockTransport`，**不開任何 socket**
- `src/twfi/protocol.py`：把事前註冊協議變成**程式常數**
  （公司／split、題型分布、pooled hard set、route 對應、F0–F7、G1–G10 門檻），
  ＋ `consistency_problems()` 可注入參數 → 檢查本身也被測試
- `src/twfi/io/manifest.py`：pydantic manifest
  - URL 必須通過**同一個** allowlist（manifest 無法夾帶外部 host）
  - `split` 必須等於協議指派的 split（分離無法從 manifest 漂移）
  - **拒絕半記錄狀態**（有 hash 沒 timestamp 之類）
  - `verify_local_documents()` 重算 SHA-256 ＋ 檔案大小
- `scripts/explore_sources.py` → `docs/reference/twse_openapi_endpoints.md`
  （**143 endpoint**，44 個入 shortlist）
- `scripts/sample_endpoint.py`、`scripts/probe_mops.py`（含 body 落地，避免重複請求）
- `data/manifests/documents.yaml`（7 份年報，全 `pending`，含人工下載指示）
  ＋ `structured.yaml`（8 個 OpenAPI dataset ＋ 7 個 XBRL 選配）
- 三個 md 文件依實測結果修訂：`DATA_PROVENANCE.md §8`（新增）、
  `DECISIONS.md` D-010／D-011、`twfi-data-access` skill 加上三個必知事實

**連外請求總計**：11 次（OpenAPI 2、mops 6 含 redirect、doc 1，另 sample 1），
全部經 rate limiter，遠低於 40/host 上限。

**結果**：`ruff` 乾淨、`mypy src` strict 乾淨、
`pytest` **308 passed / 1 skipped**、coverage **99.87%**

**沒做什麼**：任何 GPU 任務、任何 evaluation、任何 gold 標註、
任何 MOPS 表單模擬或逆向。

**下一步**：見上方「下一步」。
