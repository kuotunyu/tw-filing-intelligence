# PROGRESS LOG

> **回來接手先讀這一頁。** 下面的「目前狀態」與「下一步」永遠是最新的。
> 每個 session 結束前必須更新本檔並 commit。

---

## 目前狀態

- **Phase**：P3（parsing 層）— 🟡 **進行中**
  - ✅ `types.py` 文件模型（BBox／Span／Line／Block／ParsedDocument／Chunk，
    全部 frozen，帶 page＋bbox 以承載 citation 契約）
  - ✅ `baseline.py`（F0：PyMuPDF 純文字 ＋ 固定 800/100 chunk）
  - ✅ `layout.py`（candidate：字級統計 heading／編號樣式／section tree／
    running footer 偵測／reading order），**結構判斷是純函式**，不需 PDF 即可測
  - ✅ `chunker.py`（不跨 section、不切表格、heading path 前綴、短塊回折）
  - ✅ 合成 PDF fixture（3 頁、三層標題、跨頁表格、括號負數、單位列、圖表區）
  - ⬜ `tables.py`（pdfplumber 表格 → typed Table ＋ 單位偵測 ＋ 跨頁接續）
  - ⬜ `figures.py`（figure/chart region 偵測 → crop bbox）
  - ⬜ `results/runs/parse_stats.json`（需要真實 PDF 才能產生）
- **Phase**：P2（資料取得）— 🟡 **自動化半邊完成，等使用者放 7 份 PDF**
  - ✅ 9 個 OpenAPI dataset 已取得並記入 `acquisition.lock.yaml`
    （swagger 306KB／t187ap03_L 1092 列／t187ap05_L 1082 列／
    t187ap06_L_ci 1045 列／**t187ap06_L_fh 13 列**／t187ap07_L_ci 1045 列／
    t187ap07_L_fh 13 列／t187ap14_L 1081 列／t187ap17_L 1051 列，共 5.3MB）
  - ✅ `verify_manifests.py`：integrity 全過，7 份文件如實標為「尚未取得」
  - ⬜ 7 份年報 PDF 需人工放置（`uv run python scripts/fetch_documents.py` 會印出精確指示）
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
- **Locked set 狀態**：尚未建立
- **Toolchain 狀態**：`uv sync --extra dev` OK（Python 3.13.13）、
  `ruff check` 乾淨、`ruff format --check` 乾淨、`mypy src` strict 乾淨、
  `pytest` **308 passed / 1 skipped**、coverage **99.87%**（gate 85%）
- **最後更新**：2026-07-31

## 下一步（照順序）

1. **需要使用者動手**：把 7 份年報 PDF 放到 `data/raw/manual/`
   （檔名與來源見 `data/manifests/documents.yaml` 每筆的 `notes`）。
   XBRL（7 份，選配但建議）同樣放這裡。
2. **P2**：`scripts/fetch_twse_openapi.py`（自動抓 8 個 OpenAPI dataset）
   ＋ `scripts/fetch_documents.py --manual-dir`（計算並寫入 SHA-256）
   ＋ `scripts/verify_manifests.py`。
3. **P3**：parsing 層（PyMuPDF baseline ＋ 自建 layout-aware parser）。
   合成 PDF fixture 可先寫，不必等真實 PDF。
4. **P4**：DuckDB schema ＋ deterministic SQL。
   注意 per-industry schema（`_ci` vs `_fh`）與單位（千元 vs 百萬元）。

---

## Phase 狀態表

| Phase | 名稱 | 狀態 | 完成日 | 備註 |
|---|---|---|---|---|
| P0 | Repo scaffold ＋ 規劃文件 | 🟢 完成 | 2026-07-31 | 文件 ＋ 骨架 ＋ toolchain 全綠 |
| P1 | 資料來源探勘 ＋ manifest schema | 🟢 完成 | 2026-07-31 | 3 個關鍵發現，見上方 |
| P2 | 資料取得 ＋ provenance ＋ SHA-256 | 🟡 等使用者放 PDF | — | OpenAPI 部分可先自動化 |
| P3 | Parsing（baseline ＋ layout-aware） | 🟡 進行中 | — | tables/figures 未完成 |
| P4 | 結構化數值層（DuckDB ＋ SQL） | ⚪ 未開始 | — | CPU |
| P5 | Gold set 標註 | ⚪ 未開始 | — | 需先有 P2/P3 |
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
