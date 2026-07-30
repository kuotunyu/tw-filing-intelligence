# PROGRESS LOG

> **回來接手先讀這一頁。** 下面的「目前狀態」與「下一步」永遠是最新的。
> 每個 session 結束前必須更新本檔並 commit。

---

## 目前狀態

- **Phase**：P0（Repo scaffold ＋ 規劃文件）— 🟢 **完成**
- **Protocol 狀態**：`1.0.0-draft`，**尚未 freeze**（可以修改）
- **Locked set 狀態**：尚未建立
- **Toolchain 狀態**：`uv sync --extra dev` OK（Python 3.13.13）、
  `ruff check` 乾淨、`ruff format --check` 乾淨、`mypy src` strict 乾淨、
  `pytest` **150 passed / 1 skipped**、coverage **99.61%**（gate 85%）
- **最後更新**：2026-07-31

## 下一步（照順序）

1. **P1**：`src/twfi/io/http.py`（host allowlist ＋ rate limit ＋ 下載上限 ＋
   SSRF 防護）＋ 對應離線測試（非 allowlist host／http／redirect 逃逸／私有 IP 皆被拒）。
2. `scripts/explore_sources.py` 抓 `openapi.twse.com.tw/v1/swagger.json`，
   輸出 `docs/reference/twse_openapi_endpoints.md`。
3. 實測 MOPS 年報／財報 PDF 的穩定公開路徑（R1 風險），結果寫入
   `docs/DATA_PROVENANCE.md`；若無穩定路徑就明確走人工放置 fallback。
4. `data/manifests/documents.yaml` ／ `structured.yaml` 填入 7 份 PDF ＋
   結構化資料的宣告（公司／年度／類型／split／source_page）。

---

## Phase 狀態表

| Phase | 名稱 | 狀態 | 完成日 | 備註 |
|---|---|---|---|---|
| P0 | Repo scaffold ＋ 規劃文件 | 🟢 完成 | 2026-07-31 | 文件 ＋ 骨架 ＋ toolchain 全綠 |
| P1 | 資料來源探勘 ＋ manifest schema | ⚪ 未開始 | — | 需連外 |
| P2 | 資料取得 ＋ provenance ＋ SHA-256 | ⚪ 未開始 | — | 需連外 |
| P3 | Parsing（baseline ＋ layout-aware） | ⚪ 未開始 | — | CPU |
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
| R1 | MOPS 年報 PDF URL 可能含流水號、非決定性 | P1 實測；manifest 記 `resolved_url`＋hash；備人工 fallback |
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

**沒做什麼**：任何連外請求、任何 GPU 任務、任何 evaluation、任何 gold 標註。

**下一步**：見上方「下一步」。
