# PROGRESS LOG

> **回來接手先讀這一頁。** 下面的「目前狀態」與「下一步」永遠是最新的。
> 每個 session 結束前必須更新本檔並 commit。

---

## 目前狀態

- 🟡 **重建 index —— 不需要 GPU，正在 CPU 上跑（D-034）。**
  D-031 修掉了一個 heading 偵測的 bug，**chunk 邊界因此改變**，
  所以舊的 `vectors.npy` 對應舊切塊。原本這條被標成 🔴 GPU 阻塞，**那是錯的**：
  bge-m3 在 24 執行緒 CPU 上每 chunk 601–825 ms，整個 corpus 約 2.6 小時。
  ```bash
  uv run python scripts/build_index.py --device cpu    # 重建向量與 chunks.jsonl
  uv run python scripts/build_bm25.py                  # 再重建 BM25（新腳本）
  uv run python scripts/eval_retrieval.py --set dev    # 然後重量
  ```
  **D-029／D-030 的所有 recall 數字都對應舊切塊，重建後必須重量。**
  ⚠️ 原本這裡寫「`load_vectors(expect_rows=)` 會擋下不一致，所以不會靜靜地用錯 index」，
  **那是假的** —— `eval_retrieval.py` 當時直接 `np.load` 繞過了那道保險。已修（D-034）。

- **Phase**：P0–P4 🟢 完成。**P5 🟢 完成：53/53 已標註、兩個集合的抽樣稽核都通過**
  （probe 5 ／ **locked 33/33 ✅ 稽核完成** ／ **dev 15/15 ✅ 8/8 抽樣稽核通過** ／
  ~~challenger~~ **已取消**）。
  分母由 72 降為 53：locked 36→33（D-020）、challenger 16→0（D-021）。
  **標註方式已於 2026-07-31 修訂（D-019）**：模型起草 ＋ 固定種子人工抽樣稽核。
  目前 locked 組成：fully_human 19／question_model_chosen 9／answer_model_drafted 7／
  needs_audit 14／**audited 10（稽核率 71%）**／trustworthy 29。
  **報告必須印出這組數字。**
  稽核結果：種子抽出的 8 題（2026-07-31）＋ 強制的 chart 2 題（2026-08-01）
  **全部通過**，使用者逐一對照渲染頁／裁切圖確認。
  未通過的處理原則是**整類重做**，不是只改那一題。
  抽籤池刻意**排除強制題**，所以新增 chart 類別**沒有**重抽其他類別，
  原本那 8 題依然是那 8 題。
  剩下 4 題 needs_audit 不在樣本內 —— 這是抽樣設計（8 抽自 12），不是待辦。
- **發布狀態**：已 push 到 `https://github.com/kuotunyu/tw-filing-intelligence`（public）。
  commit 作者一律 `kuotunyu <61350295+kuotunyu@users.noreply.github.com>`，
  **不得加 `Co-authored-by:` trailer**（見 `CLAUDE.md` 規則 9）。
- **資料現況**（`results/runs/document_quality.json`）：

  可讀率**已於 2026-07-31 重新量測**（D-017：舊分母是 `pages_with_text`，
  純圖片頁同時離開分子與分母，所以偵測不到）。能力欄來自
  `results/runs/question_sources.json`。

  ⚠️ **「可讀%」量的是「頁面有沒有產出字元」，不是「字元對不對」（D-033）。**
  一頁可以 100% 可讀而整頁是亂碼。`garbled%` 那一欄是第二個問題的答案，
  兩欄都要看 —— 而且**被抓出來的兩份正好是 dev 的兩份**。

  | 文件 | 頁 | 可讀% | **亂碼字元%** | **亂碼頁%** | 報表狀態 | 可出題型 |
  |---|---|---|---|---|---|---|
  | **2412-FY2023-AR** | 277 | 95% | **17.9%** | **48%** | readable | 7 |
  | **1301-FY2023-AR** | 362 | 96% | **15.4%** | **43%** | readable | 7 |
  | 2330-FY2023-AR | 345 | 98% | 0.1% | 0% | readable | 7 |
  | 2330-FY2024-AR | 91 | 100% | 0.1% | 0% | absent_by_design | 7 |
  | **2330-FY2024-FS** | 124 | **91%** | 0.0% | 0% | **image_only pp.7–15** | 7 |
  | **2317-FY2023-AR** | 705 | **21%** | **55.6%** | **73%** | readable | **0** |
  | **2317-FY2024-AR** | 134 | **0%** | **75.4%** | **100%** | absent | **0** |
  | 2317-FY2024-FS | 202 | 95% | 0.0% | 0% | readable | 7 |
  | 2882-FY2024-AR | 248 | 100% | 0.3% | 0.4% | absent_by_design | 7 |
  | 2882-FY2024-FS | 401 | 99% | 0.0% | 0% | readable | 7 |

  **8/10 可用**，但可用性不是二元的：

  - **`2330-FY2024-FS` 的四大報表無文字層**（pp.7–15）。FY2024 營收讀得到，
    但在 **p55「附註二一 營業收入」**，旁邊就是 FY2023 比較數。
    只讀報表表格的系統在這份文件上找不到；讀附註的找得到 —— **這是可行性發現**。
    （原本量到「65 個表格只有 3 個宣告單位」，那是 D-018 的偵測缺陷，
    修正後為 64 個，此文件可出數值題。）
  - **鴻海只有 `2317-FY2024-FS` 能出題**。兩份年報歸零 →
    2317 的 narrative 題只能來自財報附註散文，不是年報敘述。
    （兩份年報的 `亂碼字元%` 55.6% 與 75.4% 是**獨立指標的一致確認**：
    `readable%` 與 `garbled%` 用完全不同的方式量，兩者對這兩份的判斷相同。）
  - ⚠️ **dev 的兩份文件都有 43–48% 的頁面是亂碼（D-033）。**
    本研究**所有調參決策都建立在這兩份文件上**。這是「dev 上的數字能代表什麼」的事實，
    不是可修的缺陷，必須寫進 report 的限制。
    好消息是：dev／locked／probe 共引用 71 頁，**亂碼頁 0 頁** ——
    標註是看渲染圖做的，而現在 `verify_gold_answers.py` 會強制這件事。
  - `absent_by_design` 不是缺陷：FY2024 股東會年報依規定不含報表（D-012），
    數字在配對的財務報告書裡。
- **P4 數值層現況**（`results/runs/historical_load_{dev,locked}.json`）：
  gold 指名的 20 個 `row_key` 裡，**9 個是單格目標**（其餘 11 個是比率／年增率／
  兩年並列／差異％，依設計由 calculator 從期間格算出，不查欄）。
  **單格目標載入 8/9，零與 gold 不符**（dev 4/4；locked held-out 4/5）。
  其中 6 個走 D-032 的文字流路線 —— 格線重建在財報附註版型上失敗，文字流不會。
  唯一未取到的是 `2317-FY2024-FS` p14（代碼欄損益表），**刻意不修**，見下方待辦 9。
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

0. ✅ **dev 稽核完成（2026-08-01）**：種子抽出的 DEV-0001／0002／0004／0005／0008／
   0010／0012／0013 八題，使用者逐一對照渲染頁後回覆「全對」，已全部 accepted。
   **dev 8/15 audited（53%）**。dev 是全部模型起草的（不像 locked 有 19 題人工），
   所以這次稽核比 locked 更重要。數字我另用資產負債表恆等式交叉驗證過（四組全平衡），
   但題目選得對不對只有人能判 —— 那部分已經有人判了。

0b. ✅ **chart 兩題稽核完成（2026-08-01）**：LOCK-0032／LOCK-0033 兩題使用者對照
   裁切圖確認無誤。**locked 稽核率 71%。**
   為什麼這兩題非人看不可：**數值可以自動查**（`verify_gold_answers.py` 會確認它們
   落在所引 bbox 內，指錯圖就會失敗），但**「哪個數字屬於哪一年、哪個系列」查不到** ——
   承載那件事的是座標軸標籤與圖例顏色，正是 text layer 丟掉的東西（D-022）。

   ⚠️ **`--accept` 要一個 id 一個 flag**：
   `--accept LOCK-0032 --accept LOCK-0033`。
   原本 docstring 寫成 `--accept LOCK-0021 LOCK-0024`，typer 會報
   `unexpected extra argument` —— 使用者照著跑就失敗。已修正，
   並讓 script 直接把待稽核的 id 印成可貼的完整指令。

1. **P5（關鍵路徑）**：gold 標註（locked **33 ✅** ＋ dev 15 ＋ probes 5 ✅
   ＋ ~~challenger 16~~ **取消**）。**純 CPU，不需要 GPU。**
   - 出題來源以 `results/runs/question_sources.json` 為準，**不是** `USABLE_DOCUMENTS`
     ——後者是二元旗標，不知道 `2330-FY2024-FS` 不能出數值題（D-017）。
   - gold answer **不得由 candidate 產生**（型別強制，candidate 不可表示）。
     `annotator` 與 `question_author` 各自具名；模型起草需人工抽樣稽核（D-019）。
   - **53 題全部需要人讀 PDF（或人稽核模型讀的）。** OpenAPI 只有 FY2026Q1，
     與文件集（FY2023／FY2024）交集為空，所以沒有可機械建置的子集（D-016）。
   - `answer_provenance` 不得是本 repo 的抽取器 —— 那與 F1／F4 循環（D-016）。
   - **已有工具**：`make_worklist.py --for probe` 產出 25 個證據 slot；
     `validate_gold.py`、`check_leakage.py` 是 freeze 前的閘門。
   - ✅ **probe 5 題完成**；✅ **locked 33/33 完成** —— narrative_fact 6／table_cell 5／
     numeric_calculation 5／cross_period 4／cross_page 4／cross_document 3／
     unanswerable 4／**chart_value_trend 2**。
     檔案：`data/evaluation/locked/probes.jsonl`、`gold.jsonl`。
     注意 probe **不是** unanswerable：G8 強制清空檢索，所以好的 probe 是
     **模型很可能記得答案**的題目。
   - **標註流程已定型**，剩下 15 題（dev）照這個走：
     `make_worklist.py` 定位 → `render_pages.py` 渲染成圖 →
     人**看圖**讀數字（不看抽取文字）→ `fill_gold.py --set <set>` 填表單（不碰 JSON）→
     `validate_gold.py` ＋ `check_leakage.py` ＋ `verify_gold_answers.py`。
   - **一批 4–5 題最順**：我一次給清單（哪張圖／哪一列／哪一欄），使用者一次讀完回報，
     我填表並跑三個檢查。逐題往返太慢。
   - **刻意重用頁面**：一張渲染圖出 2–3 題，切換成本大幅下降。
     locked batch 2 完全沒有開新圖。
   - 剩 **15 題**（dev）＋ 2 題待稽核。challenger 16 題已取消（D-021）。
   - **每一題的判斷都必須有一張圖可看。** unanswerable 題原本只給搜尋結果，
     使用者拒絕在看不到的東西上簽名 —— 那是對的，四題因此各配一張證據圖。
   - 使用者決定：**先把 locked 做完**（已完成），dev 之後再決定是否減量。
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
| P4 | 數值層（DuckDB ＋ deterministic SQL） | 🟡 有實質限制 | 2026-08-01 | OpenAPI 253 筆已載入；歷史值 loader 已寫，但 **20 個 gold 指名 cell 只載入 2 個**（D-028，財報頁表格結構壞掉） |
| P5 | Gold set 標註 | 🟢 完成 | 2026-08-01 | **53/53**：locked 33（稽核 71%）＋ dev 15（稽核 53%）＋ probe 5 |
| P6 | Retrieval ＋ rerank | 🟡 進行中 | — | **端到端可跑（全 CPU）**：dense 4,063＋9,890 向量、BM25 兩個索引、RRF 融合。首次 recall 量測見 D-029。**rerank 尚未接** |
| P7 | Chart route | ⚪ 未開始 | — | GPU |
| P8 | Router ＋ answer/citation | ⚪ 未開始 | — | GPU |
| P9 | Eval harness ＋ metrics | 🟡 部分 | — | `run_gate`／`verify_results`／`make_report` 已寫並測試；**`run_eval` 未寫** |
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
| Q2 | Generation ／ VLM | **`qwen3.6:27b` digest `a50eda8ed977`（ollama, Q4_K_M）文字與圖表共用同一模型**；數值走 SQL；~~`qwen3-vl:8b` 只做 freeze 前 chart challenger~~ → **challenger 已取消（D-021）：DEV 文件沒有圖表，16 題無從出題；依事前寫死的 fallback 全部 route 用 27B**；`gpt-oss:20b` 不進 pipeline（D-003 / D-009 / D-021） |
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
| **R8** | **F5／F6 的輸入幾乎全是有框表格，不是圖表** | 逐一目視：503 個 candidate 中確認的真圖表只有 **4 張**（全台積電、2 頁）；DEV 20 個幾何正例 **0 張**。兩階**保留**（只做增益歸因、不參與 gate），但 report **不得**把它們的增益說成 chart-reading 能力（D-021） |
| **R9** | **`chart_value_trend` 只有 2 題且同一家公司** | 這是語料事實不是取樣偷懶。G2 已把 chart 移出單一類別 gate、pooled 門檻提到 15pp。report limitations 必須寫：這兩題答的是「能不能讀台積電那兩張圖」（D-020） |
| ~~R3~~ | ~~VRAM ≈ 20–21GB，逼近 G10 的 22GB~~ | **已解決，方法與原本設想相反（D-026）**。實測：三個模型同時放 GPU = **21.91 GiB**（餘裕 90 MB，不可行）；把 reranker 移到 CPU = **top-10 要 5.85 s**（超過 3 s 上限兩倍，原本的備案是死的）。**正解是 embedder 移到 CPU、reranker 留在 GPU** → **21.07 GiB（top-20），餘裕 +0.93 GB**，query embed 55 ms、rerank 61 ms。理由：embedder 查詢時只跑一次（CPU 55 ms）卻佔 2 GB；reranker 要對 top-k 個 pair 各跑一次 cross-encoder，CPU 上慢 96 倍 |
| **R11** | **top-k 是被 VRAM 綁住的，不是被檢索品質決定的** | top-20 → 21.07 GiB（+0.93 GB）／top-30 → 21.31（+0.69）／top-50 → 21.70（+0.30）。report **不得**把 top-k 呈現為自由選擇的超參數（D-026） |
| **R12** | **線上服務配置在這張卡上過不了 22 GB** | 批次評估可把檢索階段與生成階段分開跑、各自獨佔顯卡；真的線上服務兩者必須同時駐留。**本研究能證明的是前者**，report 必須這樣寫（D-026） |
| ~~R10~~ | ~~generation p95 可能超過 G10 的 60 秒~~ | **已解除**：實測真實負載（3,557 token 輸入 ＋ 512 token 輸出）**12.3–13.3 秒**，對 60 秒有 4.5 倍餘裕。短 prompt 的 0.7–2.4 秒是**下限不是工作負載**，兩者都記進 `resource_budget.json` 以免被誤引 |
| R6 | Q4_K_M 量化可能影響敘述題品質 | 數值題走 SQL 不受影響；敘述題影響會如實反映在指標，不換模型補救 |
| R4 | 我自己標註 gold 的偏誤 | 答案必須指回頁碼/bbox/row；標註前不看 pipeline 輸出 |
| R5 | 金控 2882 報表結構特殊，numeric route 可能失敗 | 視為 hard case 如實記錄，不換公司 |

---

## Session 日誌

### 2026-08-01 — Session 11（表格抽取器的失效、P6 檢索層、撞上 token 上限）

**最重要的發現：表格抽取器在 dev 文件上，15 個已知數字一個都取不出來。**

- 起因：要寫 P4 loader，先看抽取器在 `1301-FY2023-AR` p188（我親手讀過的 13×5 比較表）
  上抽到什麼 → **0 個表格**。
- 根因：那頁的表格用 **101 個 `rect` 畫，只有 1 條 `line`**，
  而 `TableConfig` 兩軸都用 `text` strategy，**它完全不看 rect**。
- **原本「用 text 不用 lines」的決定是在兩份 locked 文件上量的**，
  對 locked 成立、對 dev 完全不成立。**一個在單一 split 上量出的設定，被當成整個語料的性質。**

**我在這條上犯的兩個錯，都記在 D-027**：

1. 第一次量「哪個 strategy 偵測到更多表格」（text 298 / lines 175）——
   **那是在數過濾器有沒有通過，不是格子對不對**，與 D-020 同一個代理指標錯誤。
2. 改用「gold 已知數字能否被抽回」是對的標準，但第一次把 **locked ＋ dev 混在一起**（48 個 figure）。
   **拿 locked 答案決定抽取器設定 = 在 locked 上調參**（協議 §1.3 禁止）。
   發現後把 `compare_table_strategies.py --set` 預設改成 dev，並把這件事寫進 docstring。

**修正並已換預設（`strategy="union"`，跑兩種取聯集）**，依據建立在 dev，理由**與 split 無關**：
沒有任一 strategy 支配另一個，所以「兩個都跑」不需要知道哪個 split 偏好哪一種；
只用 `lines` 就是對 dev 過度擬合（held-out locked：text 27/48 vs lines 10/48 正好證實）。

| 文件 | 表格數 | chart 誤判候選 | 
|---|---|---|
| 1301-FY2023-AR | 180 → 223（+24%） | 104 → 67 |
| 2412-FY2023-AR | 195 → 253（+30%） | 111 → 64 |
| 2330-FY2023-AR | 193 → 260（+35%） | 33 → **7** |
| 2330-FY2024-AR | 55 → 90（+64%） | 40 → **9** |

**關鍵檢查通過**：D-020 確認的 4 張真圖表（2330-FY2023-AR p7、2330-FY2024-AR p6）
在聯集下仍是候選 → LOCK-0032／0033 沒有失去來源。
端到端：舊預設 `text` 取回 **0/9**，新預設 `union` 取回 **5/9**。

**P6 檢索層（BM25 ＋ dense ＋ RRF）＋ G9 的 results verifier 已落地**

用 workflow 開 3 個 agent 平行寫（互不相干的新檔案），我負責整合。
**但 workflow 的第二階段（三個對抗式審查 agent）全部被 session token 上限殺掉。**

於是**我自己做了那個審查** —— 不是逐行讀 1,500 行，而是探測這個 repo 真正踩過的失效模式：

- BM25：`U+F98E U+FA01` 與 `年度` tokenise 結果相同（D-024 的活案例）；
  `530,738,356` 保持完整；同分照 doc index 排序、兩次 build 分數一致
- dense：0 列矩陣／維度不合／`top_k<=0`／全零 query／矩陣含 NaN／1-D 矩陣**全部拒絕**並指出原因
- fusion：`rankings=[]` 拒絕，但 `[[],[]]` 回 `[]`（前者是接線錯誤、後者是結果）
- results（G9）：**summary 沒有 raw artifacts → 16 個問題**（刪掉 artifacts 不能讓 G9 通過）；
  虛報 2/2 而實際 1/2 → 抓到並指出兩個數字；空 summary ＋ 空 artifacts → 8 個問題，不是「一切正常」

**我原以為的兩個問題查下去都不成立**（`read_record` 接受矛盾記錄，但 `verify` 在聚合層抓到；
看似重複的報告其實是同一筆記錄的兩個不同問題）。

**已 commit 並 push**：`420e8d1` `16c0c52` `e189e19` `c9b1ed4` `968385b`。
1230 passed／1 skipped、coverage 96.61%、ruff ＋ mypy 全綠（66 檔）。

**撞上 token 上限之後仍完成的（我自己做，沒有再開 agent）**：

- ✅ **自審那 1,500 行 agent 程式碼**（原本要三個 agent 做的）——
  用探測失效模式的方式，不是逐行讀。三個模組在我測的每個性質上都站得住；
  我原以為的兩個問題查下去都不成立。
- ✅ **P4 loader**（`src/twfi/numeric/historical.py` ＋ `scripts/load_historical.py`）——
  **而且 loader 自己抓到自己的 bug**：存貨被載成 `88`（附註編號），
  因為原本「找不到欄位就取該列第一個數字」。改成**找不到欄位就拒絕**。
  → 引出 **D-028**：locked 財報頁的表格結構壞到讓這條路幾乎不通（20 個目標載入 2 個）。
- ✅ **`make_report.py`** ＋ `src/twfi/eval/report.py` ——
  核心是**拒絕產出缺少必要段落的報告**（沒有分母的比率、五個必要限制段、lock hash、失敗的 gate）。
  讀實際產出時抓到 gate 排序是字典序（G1, G10, G2）而非數字序。
- ✅ **`pin_models.py`** ＋ `configs/models.lock.json` ——
  核心是**宣告 digest 與實際不符就失敗，不改寫 lock**（故意弄壞驗證過）。
- ✅ **P6 檢索端到端**（`src/twfi/index/retrieve.py`）——
  **全部 CPU**（語料向量預先算好，查詢 embedding 在 CPU 55 ms）。
  → 引出 **D-029**：第一次真實 recall 量測。
- ✅ **`src/twfi/io/jsonl.py`** —— 修一個真 bug：
  `json.dumps(ensure_ascii=False)` 不轉義 U+2028／U+2029／U+0085，
  而 `str.splitlines()` 會在那裡斷行 → 寫出去的 JSONL 讀不回來。修在寫入端。

**檢索量測與三次自我更正（D-029、D-030）**

- **可重跑的 recall 量測**（`scripts/eval_retrieval.py` → `results/runs/retrieval_dev.json`），
  **全部 CPU**。自帶單調性檢查：同一候選池下 recall 不可能隨 k 下降，違反就 exit 1。
- **hybrid 一開始輸給 lexical**，原因是我把融合前的取回深度綁在 `top_k` 上，
  融合沒有可救回的東西。改成絕對 `fetch_depth=100` 後
  **baseline hybrid 13/15＠10、14/15＠20，並在 k=5 就贏過 lexical＠10**
  —— 那代表塞進生成的 chunk 更少而證據更好。
- **depth 不是用 argmax 選的**：n=15、8 個 cell，取最大值就是過度擬合。
  只主張「depth 要明顯大於 top_k」，100 落在那個區間裡。

**⚠️ 我在這一輪犯了三次同一形狀的量測錯誤，全部已更正，接手時請看更正後版本：**

| 我先說 | 真相 | 錯在哪 |
|---|---|---|
| candidate 少 23% 文字 | — | baseline 的 `chunks.jsonl` 含 800/100 重疊，重複計算 |
| candidate 少 14% 文字 | — | 空白與換行 |
| — | **0%，兩者內容 100% 相同** | 去空白後差 297 字元 / 2.16M |

**教訓：比較兩個排版方式不同的東西，然後把差異當成內容差異。**
去空白 ＋ 去重疊之後再比，是唯一正確的做法。
（好消息：layout parser 一個字都沒丟 → **F1 的增益純粹來自結構**，比我原本以為的乾淨。）

**D-030 最後仍然成立的兩項**：
1. **`recall@k` 不能跨 parser 比較** —— chunk 中位數 800 vs 99 字元，指標偏好大 chunk。
   公平版本要對齊**去空白且不含重疊的字元預算**，未實作。
2. ~~candidate 有 29% 的 chunk 不到 50 字元~~ → **已解決，降到 2–9%**（見下方 D-031）。

**D-031：一個 regex 讓 F1 的核心主張變成空的（本輪最重要的修正）**

追「29% 的 chunk 不到 50 字元」往上查，最後看到那些「頂層章節」的名字：

```
1301-FY2023-AR: 1,083 個頂層 section，叫 '0.00' '0.0005%' '0.0036,204,112'
2330-FY2023-AR:   627 個，叫 '0.00' '0.00%' '0.001' '0.00106%'
```

`_SINGLE_NUMBER = r"^(\d{1,2})[.、]"` 匹配 `0.00` 的 `0.` →
**每一份財報裡的每一個小數都變成一個頂層章節。**
既有的 `looks_tabular` 抓不到，因為 `0.00` 只有一個數字而 heading 允許含一個。

**F1 說「chunk 帶 section path 讓檢索與 citation 有上下文」——
當那個 path 是 `0.0036,204,112` 時，那個主張是空的。**

**還有第二個 heading bug**：`1.` 被判成 **level 1**，而慣例是
**壹／貳(1) → 一／二(2) → （一）(3) → 1./2.(4)** —— 表裡 `(1)` 已經是 4。
level 1 會**重設 section stack**，所以每一個編號清單都開了一個新的根。
**六個測試（含我幾分鐘前才寫的兩個）編碼了這個 bug**；我在改測試前先量效果確認哪邊才對。

兩個修正合起來的效果：

| 文件 | 頂層 section | <50 字 chunk | chunk 中位數 |
|---|---|---|---|
| 1301 | 1,083 → 518 → **22** | 32% → 13% → **2%** | 94 → 165 → **258** |
| 2330 | 627 → 180 → **56** | 26% → 9% → **9%** | 175 → 356 → **404** |
| 2412 | → 424 → **18** | 20% → 9% → **3%** | 163 → 234 → **322** |
| 2882 | → **10** | → **2%** | → 282 |

**2882 剛好 10 個頂層章節 —— 那正是年報實際的壹～拾。**
chunk 深度分布也變成合理的金字塔。
→ **D-030 的「29% 小碎片」問題實質解決（降到 2–9%）。**

順帶發現：**1301 的文字層也有壞碼**（含 `\x10` `\x16` 控制字元），不只 2412 ——
D-024 的範圍比原本記的大。

**仍未完成**：
1. 🔴 **重建 index（需要 GPU）** —— 見本頁最上方。
2. ~~**切開被合併成一張的附註**~~ → ✅ **已解決（D-032）**，而且不需要專門的切分啟發式：
   改用**文字流**讀頁（`twfi/numeric/rows.py`），「新的期間表頭開啟一張表」這條規則
   自然就把 p41 併成一張的三個附註切成三張。單格數值目標從 **2/9 到 8/9**，零不符。
3. ~~**旋轉頁處理**（2412 p137）~~ → ✅ **診斷本身是錯的（D-032 更正二）**：
   p137 的文字流讀得完全正常，取不到數字的原因是代碼欄與％欄讓格線重建失敗，
   不是旋轉。文字流路線已取到該頁兩個目標。
4. **字元預算對齊的 recall** —— 取代不可比的 parser 間 `recall@k`。
5. ~~2330 的頂層 section 仍偏多~~ → **已查明，不是 bug**：台積電用十進位編號（`5`／`5.1`），裸的 `5` 靠字體偵測所以沒有編號 level，其子項因此各自成根。不修（見 D-031 末段）。
6. **candidate hybrid 為何在 k=5/10/20 恆為 8/15** —— 已排除融合 bug，原因未明（且要重量）。
7. ~~**可信的壞字偵測器**~~ → ✅ **完成（D-033）**：白名單從全 corpus 的字元普查寫出來，
   不是猜的。乾淨／損壞相差五十倍，門檻 5% 落在量出來的空隙裡。
   抓出的兩份**正是 dev 的兩份**，而 `readable%` 給它們 95%／96%。
8. `run_eval` —— 需要生成（GPU）。
9. **`2317-FY2024-FS` p14 的代碼欄損益表版型**（D-032 剩下的那 1 個未取到目標）。
   **刻意留著**：那一頁只在 locked，為它調整抽取器就是在 locked 上調參。
   要修得先在 dev 上找到同型頁面，否則 freeze 後寫成已知限制。

---

### 2026-08-01 — Session 11（改讀文字流：單格數值目標 2/9 → 8/9）

使用者出門 10 小時，指定「盡量做完、需要我操作的等我回來、這段時間不要用 GPU」。
**GPU 全程未動。**

從 D-028 的待辦第 1 項（切開被合併的附註）開始，查證 `2330-FY2024-FS` p41 的完整結構後
發現要做的事更根本：**PyMuPDF 是一格一行輸出**，標籤與數值不在同一列，
而且**合計列往往沒有標籤** —— 格線重建救不了「哪幾行屬於同一個項目」。

新增 `twfi/numeric/rows.py`（用人讀的方式讀，三條規則）＋ `find_in_text` 備援。
**dev 4/4、locked（held-out，只跑一次）4/5，零 `disagrees`。** 詳見 D-032。

同一段時間另外完成（詳見 D-033／D-034）：

- **壞字偵測器第二版**（`twfi/parsing/garbled.py`）：白名單從 216 萬字元的普查寫出來。
  **抓出的兩份文件正好是 dev 的兩份**，而 `readable%` 給它們 95%／96%。
- **兩個索引半邊共用 `manifest.json`，BM25 覆蓋掉了 embedding 的 provenance。**
  更嚴重的是 `eval_retrieval.py` 直接 `np.load` 繞過 `load_vectors` ——
  我先前寫的「保險會擋下過期 index」在唯一量 recall 的那條路上是假的。
- **BM25 根本沒有建置腳本**（沒有任何程式呼叫 `save_index`），已補 `build_bm25.py`。
- **重建 index 不需要 GPU**：CPU 約 2.6 小時，這條被標成 🔴 GPU 阻塞的項目從來不是。
- **補上 `freeze_protocol.py`**（文件早就寫著這支指令，但它不存在）。
  dry-run 顯示**所有 freeze 前置條件現在都通過**。freeze 本身是使用者的決定。

這一節必須記三件事：

1. **我差點在 locked 上調參。** 設計初期的探測案例 2330／2317／2882 全是 locked，
   而我要的停止判準是「gold 對不對」—— 那就是用 locked 答案挑抽取器。
   發現後把迭代判準搬到 dev，locked 只跑一次當 held-out 且**跑完沒再改任何一行**。
   規則本身來自版面結構（任何財報都看得到），那部分保留。
2. **兩個會靜默載入錯誤數字的 bug。** 沒有千分位的整數（代碼欄 `1100`、％欄 `6`）
   被當成列標籤，讓某一列裝進**前一個科目**的去年數字；
   以及用「第一個包含關係」比對列標籤，會讓 `流動資產總計` 冒充 `資產總計`，
   也讓單字表頭片段 `資` 搶先匹配並讓解析整個放棄。兩者都有回歸測試。
3. **分母錯了會同時低估設計、也高估失敗。** D-028 反覆說的「20 個目標載入 2 個」，
   那 20 個裡有 11 個是衍生量或跨欄，依設計就該拒絕。真正的分母是 9。

---

### 2026-08-01 — Session 10（夜間：一個更正、一個真 bug、dev 15 題、gate 判定器）

使用者睡前指定「你能做的事盡量都做」。GPU 全程未動（FormosaNLU 在跑 robustness sweep，
`nvidia-smi` 顯示 11.4 GB 被佔用，依規則 8 讓行）。以下全是 CPU／離線工作。

**1. 更正我自己前一個 commit 裡的錯誤說法**

我寫「圖表頁的年份與圖例在 text layer 裡是壞碼」——**錯的**。
那些頁的文字層完整（`民國111年`、`年成長率`、`≤ 7奈米`、標題全都抽得出來）。
我看到的壞碼是**我自己主控台的 cp950 編碼**，也就是同一個 commit 剛修掉的那個 bug ——
**它在弄壞輸出之前先弄壞了我的診斷**。

- **代價**：我把一個錯誤的資料認知寫成「不需要查」的理由，放進檢查程式。
- **修正後反而變強**：既然年份可查，chart 檢查從「值的 multiset」升級為
  **「民國NNN年 → 值」的列層級配對**。舊版會接受「民國111年 6%；民國112年 9%」
  （值都在圖裡、只是配錯年），新版報 `2 of 3 pair(s) wrong`。
- **對 RQ3 不利的一面也要寫**：文字層帶座標，所以純文字系統靠 y 鄰近就能配對 ——
  **這兩題可能不需要視覺就答得出來**。本語料唯一的真圖表也是文字可還原的。
- 這是同一個模式第三次（D-017 量「有沒有文字」不是「對不對」；D-020 量「減少多少」
  不是「留下的對不對」；這次量了我的終端機不是資料）。規則寫進 D-022：
  **看到疑似資料損壞，先排除自己的輸出管線。**

**2. 找到一個真 bug：抽取文字沒有做 Unicode 正規化（D-024）**

檢查 dev 文件品質時發現**兩份都壞，壞法不同**：

| 文件 | 現象 | 結果 |
|---|---|---|
| 1301 | 年 存成 U+F98E、度 存成 U+FA01（CJK 相容表意文字），畫面正確但 codepoint 不等 | `「年度」`在 **91 頁**上完全不存在 → **NFC 全部救回（0 → 91）** |
| 2412 | 64,238 字散布在 Cyrillic／Armenian／Syriac／Ethiopic／Tibetan | **救不回**；只有 66/276 頁文字層乾淨（**76% 壞**） |

- 修在**抽取邊界**（`src/twfi/parsing/normalise.py`），套 `baseline`／`layout`／`tables`
  三處＋`verify_gold_answers` 比對兩側。**baseline 與 candidate 都套** ——
  只修 candidate 那側，它就會變成 candidate 的增益。
- **用 NFC 不是 NFKC**：我第一版寫 NFKC，三個 layout end-to-end 測試立刻擋下 ——
  NFKC 會把 `：`→`:`、`（`→`(`，而本 repo 好幾處**字面比對中文標點**
  （`tables.py` 是 `[^，。、）)]`）。**那不是 fixture 過時，是打擊面太大。**
  NFC 救回的頁數相同（91），多的廣度沒買到東西。
- **退掉一個我寫壞的偵測器**：範圍黑名單版本兩個方向都錯（漏了 U+2C00–2E7F、
  而且把 1301 評為 0% 損壞）。使用者睡覺無法仲裁時端出我知道是錯的指標，
  就是 D-017／D-020 再犯。**可信版本列為待辦。**

**3. dev 15 題完成（38 → 53 之中的最後一批標註）**

- 組成是**語料逼出來的，不是選的**：沒有 `chart_value_trend`（dev 沒圖表，D-021）、
  沒有 `cross_document`（dev 每家只有一份文件）。
  → table_cell 5／cross_period 3／numeric_calculation 3／unanswerable 2／
  cross_page 1／narrative_fact 1。
- **11 題來自 1301、只有 4 題來自 2412**，因為 2412 只有 66 頁文字層可用；
  它的「不存在」題也搬到 1301 —— **「搜尋不到」只有在文字讀得到時才算證據**。
- 每個數字都用**資產負債表恆等式**交叉驗證（負債＋權益＝資產，兩家兩年四組全平衡），
  那是四次獨立確認，不是同一眼看四次。
- 刻意放進兩個**對照設計**：
  - DEV-0005／0006（負債佔比 34.55% ＋ 權益佔比 65.45% = 100.00%）
    兩題一起能驗出「分母取錯」，單題驗不出。
  - DEV-0014：2412 兩年只差 **0.14%**，落在 0.5% 相對容差內 ——
    **答錯年度也會被判對**。這是「單一容差套用到所有數值題」的實測缺陷，
    report 要寫出來而不是讓它默默過去。
- **又抓到一個檢查器 bug**：`verify_gold_answers` 用**題型**決定走整串比對還是 atom 比對，
  而 locked 的 cross_period 答案剛好都是單一數字，所以從沒被觸發。
  dev 的是複合答案 → 三個正確答案被判「文件裡不存在」。
  現在改成**依答案形狀**分流（單一數字走整串以保留 elsewhere 判定，其餘走 atom）。

**4. 寫完 `run_gate.py` ＋ `src/twfi/eval/gates.py`（P9／P10 的前置）**

把 GO／CONDITIONAL_GO／NO_GO 變成**純函式**，37 個測試。三個關鍵性質：

- **缺資料一律判 fail**。刪掉一個指標不能成為通往 GO 的最便宜路徑
  （參數化測試逐一刪掉每個 key 驗證這件事）。
- **拒收沒有分母的比率**。`{"rate": 0.95}` 與缺 key 同等對待 ——
  協議本來就說「只寫百分比不寫 n 視為不完整」，這裡把它變成強制。
- **Wilson 95% 信賴區間**，不用常態近似（在 n=4 會給出 [0,1] 之外的界、
  在 0% 與 100% 會收縮成零寬度，正好是本研究最可能遇到的情況）。
  實測輸出：F0 36.4% (12/33, CI 22.2–53.4%) vs F7 63.6% (CI 46.6–77.8%) ——
  **區間重疊**，這正是協議要的誠實。
- 順帶自查：先前 smoke test 印出 `exit=0`，那是 `tail` 的 exit code 不是 python 的；
  直接量測確認 NO_GO 真的回傳 1。**同一個 pipe 遮蔽 exit code 的老問題，這次先確認才下結論。**

**沒做的事（明確說明）**

- **P4 收尾沒開工**。它需要一個「按 account 從抽取表格定位並載入」的 loader；
  半完成、可能靜靜裝著錯數字的 numeric store，比不做更糟。
  設計已想清楚：宣告式 manifest（doc/page/row/column → account/period/unit）
  ＋ 載入後**與 gold 既有數值對撞**（gold 來自像素、store 來自抽取器，兩者不一致就是有一邊錯）。
- **`build_index` / `run_eval` 沒寫**：它們的介面依賴 P6 檢索層，現在寫等於猜。
- **`pin_models` 沒跑**：只讀 metadata 不會佔 GPU，但 FormosaNLU 在跑，留給使用者決定。
- **`freeze_protocol` 絕對沒跑**：單向門，必須在 dev 調參完＋`pin_models` 之後、且使用者在場。

**待使用者一句話同意的一件事**：`set_problems` 的「全部模型起草且零稽核」規則
只在傳入 `type_counts` 時才觸發，而 `validate_gold` 只對 locked 傳。
所以 dev 現在 0% 稽核**不會**被擋。這是實作的偶然而非決策
（`type_counts` 管的是題數完整性，不是稽核），修法是加一個獨立的 `require_audit` 參數。
它讓 gate **更嚴**，方向是安全的，但會改變 `--require-complete` 的行為。

**現況**：locked 33/33 稽核完成、dev 15/15 已起草（8 題待稽核）、probe 5、challenger 取消。
1025 passed／1 skipped、coverage 97.32%、ruff ＋ mypy 全綠。

---

### 2026-08-01 — Session 9（locked 33/33 完成 ＋ D-020 的連帶影響全部解掉）

**做了什麼**

- **`chart_value_trend` 2 題出完 → locked 33/33 ✅**
  - `LOCK-0032` = `2330-FY2023-AR` p7「產能計劃」年成長率 9%／6%／6%
  - `LOCK-0033` = `2330-FY2024-AR` p6「晶圓銷售計劃」7奈米及以下 58%／69%
  - 刻意取不同文件的不同圖；兩題都排除區間值（±0.1pp 容差評不了區間）
- **量到一件影響題目說法的事**：這兩頁的**數字標籤存在於 text layer**
  （`15-16`、`47%`、`60-70%` 都找得到），但**年份與圖例是壞碼 CJK**。
  所以純文字路徑拿到一堆沒有歸屬的數字 → 這兩題測的是**歸屬**，不是 OCR。
  題目說明照這樣寫，不誇稱。
- **D-021：把 D-020 留下的三個連帶影響量完並解掉**
  - 逐一目視 **DEV 全部 20 個幾何正例 → 20/20 都不是圖表**
  - **我在 D-020 寫錯的一句話**：「表格永遠不需要斜線」。
    臺灣年報「項目＼年度」的**斜線表頭是標準版型**，是 DEV 最大誤判來源；
    其次是紅色印章的貝茲曲線。判別子 precision 是 **4/45 ≈ 9%**，不是「找到圖表」。
  - **chart challenger 取消**：16 題要求 DEV 文件的 chart crop，而 DEV 沒有圖表。
    這不是事後換模型（一次比較都沒跑），結論等同事前寫死的 fallback（全部用 27B）。
    寫進 `protocol.py`（`CHALLENGER_STATUS`）、`models.yaml`（`status: cancelled`，
    **`outcome` 保持 null**）、`validate_gold.py`（印「cancelled」不是「not annotated yet」）。
    加兩個測試：cancelled 不得寫進 `outcome`；challenger 檔案不得存在。
  - **F5／F6 改名它宣稱的東西**：增益是關於「視覺區塊（本語料中絕大多數是有框表格）」，
    **不得**說成 chart-reading 能力。兩階保留（只做增益歸因）。
- **D-022：chart 題全部強制稽核 ＋ 只能「部分佐證」**
  - `verify_gold_answers.py` 新增 crop 層級佐證：答案數值必須是**所引 bbox 內**的標籤
    （multiset）。負向對照實測：bbox 換成同頁另一張圖 → `3 of 3 not labelled`；
    換成空白 → `no text labels at all`。**判別力是真的。**
  - 這類記錄報 `~ partial`，**永遠不是 ok** —— 歸屬只有人看圖能確認。
  - `audit_sample` 從 script 搬到 `src/twfi/eval/audit.py` 並補測試：
    它是 protocol（研究對自己 gold 的主張），不該留在沒測試的 script 裡。
    強制題**排除在抽籤池外** → 新增類別**不重抽**其他類別，已完成的 8 題不作廢。
- **D-023：cp950 讓列印 gold 的 script 中途死掉**
  - `audit_gold.py --render` 列到 LOCK-0033 的 `≤` 就 `UnicodeEncodeError` 崩在半路 ——
    **比輸出很醜嚴重**，因為半份清單看起來像一份較短的樣本。
  - 新增 `src/twfi/console.py::use_utf8_output()`，7 支會列印文件／gold 的 script
    在 `_entrypoint()` 自己呼叫（**不做 import side effect**）。
    順帶：主控台的中文從此正常顯示。
  - LOCK-0033 題目改用「7奈米及以下」，圖例原樣記在 notes。

- **chart 兩題稽核完成**，locked 稽核率 **71%**（10/14）、trustworthy 29/33。
- **順手抓到的 CLI bug**：`--accept` 是 typer 的 repeatable option，
  但我自己的 docstring 寫成 `--accept LOCK-0021 LOCK-0024` ——
  使用者照著跑得到 `unexpected extra argument`。
  **usage 例子錯了就等於工具壞了**，因為那是動手前唯一會讀的東西。
  已修正，並讓 script 直接把待稽核 id 印成完整可貼指令、附上圖片目錄與
  「有 bbox 的記錄要看 `__crop<n>.png`」。

**現況**：locked **33/33 ✅ 稽核完成**、probe 5、dev 0/15、challenger 取消。
980 passed／1 skipped、coverage 97.93%、ruff ＋ mypy 全綠。

**下一步**：dev 15 題，或 P4 收尾（載入 FY2023／FY2024 歷史數值）。

---

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
