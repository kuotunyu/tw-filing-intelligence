# TW Filing Intelligence — Feasibility Study (⑤A)

**這是一份可行性驗證（feasibility study），不是產品，也不是 production 系統。**

本 repository 用來回答一個research question：

> 臺灣上市公司的**公開**資訊（MOPS 年報 / 財務報告 PDF、TWSE OpenAPI、XBRL）
> 是否足以支撐一個真正有差異化、且**可被驗證**的
> multimodal filing intelligence 系統？

驗證方式是一個**事前註冊（pre-registered）**的實驗：先把評分協議與 GO／NO-GO 門檻寫死並
hash 凍結，再跑 baseline 與 candidate，最後由程式依門檻自動產生 `GO / CONDITIONAL_GO / NO_GO`。

---

## ⚠️ 免責聲明

- **本專案不是投資建議工具。** 所有輸出僅為文件檢索與資訊擷取的技術驗證結果，
  不構成任何證券、金融商品之推薦、要約或投資建議。
- **本專案不是 production 系統。** 沒有 SLA、沒有認證授權、沒有多租戶隔離、
  沒有經過安全稽核，不應用於任何實際決策流程。
- 所有數字都可能錯誤。任何財務數字請以
  [公開資訊觀測站](https://mops.twse.com.tw/) 之原始文件為準。
- 本 repository **不重新散布**原始年報／財報 PDF。只提交 manifest、來源 URL、
  SHA-256 與重建腳本。

---

## 這個專案在驗證什麼

四類問題各自對應一條 route，四類都必須被量測：

| 類別 | 問題形態 | Route |
|---|---|---|
| Narrative | 公司策略、風險因素、營運變化、年報文字敘述 | narrative |
| Structured numeric | 營收／獲利／資產／負債、跨期變化、比率計算、單位／幣別／合併或個別 | numeric |
| Table / chart | 表格欄位、圖表數值、圖例、座標、趨勢；caption 是否幫助檢索 | chart |
| Cross-doc / unanswerable | 跨頁、跨年度、PDF↔結構化資料交叉驗證、文件中不存在的資訊、資料衝突與拒答 | cross_modal / unanswerable |

核心設計原則（也是本專案的差異化主張）：

1. **可靠的結構化數值不丟進 embedding 讓 LLM 猜** — 走 DuckDB + deterministic SQL，
   並輸出 formula 與 operands。
2. **chart caption 只用於 index／retrieval** — 最終數值答案必須回到
   原始 crop pixels 或可靠結構化資料。
3. **typed bounded router**，保留 reason 與 confidence，最多一次 bounded correction，
   **沒有無上限 agent loop**。
4. **沒有證據就拒答**，且拒答行為本身被量測（refusal precision / recall）。

---

## 現況

> **尚未 freeze，因此尚無任何結果。**
> 目前完成到 P4。protocol 還沒 hash 凍結，locked evaluation 還沒跑，
> `results/feasibility/` 是空的。**這個 repository 目前不宣稱任何可行性結論。**

| | Phase | 狀態 |
|---|---|---|
| 🟢 | P0 protocol／gate／toolchain | 完成 |
| 🟢 | P1 來源探勘 | 完成 |
| 🟢 | P2 資料取得 ＋ SHA-256 provenance | 完成（宣告 10 份，可用 8 份） |
| 🟢 | P3 Parsing（layout／table／figure） | 完成 |
| 🟢 | P4 數值層（DuckDB ＋ deterministic SQL） | 完成 |
| ⚪ | P5 Gold set 人工標註 | 未開始（關鍵路徑） |
| ⚪ | P6–P9 retrieval／chart／router／eval | 未開始 |
| ⚪ | P10 freeze → locked run → gate → report | 未開始 |

**「可用 8 份」不是失敗，是量測結果。** 兩份不可用的都是鴻海（2317）年報：
FY2023 只有 148/707 頁（21%）能抽出可讀文字，FY2024 是 **0%** —— 字型沒有
ToUnicode mapping，PDF 看得到字但抽不出字。

這兩份**留在宣告清單裡**而不是被悄悄換掉。「臺灣公開文件有多少比例真的機器可讀」
本身就是研究問題的一部分；出題時只從可用文件取材，但把不可用的刪掉會讓事前註冊
失去意義，也會把一個真實的負面發現粉飾掉。

進度、下一步、以及「隔一段時間回來怎麼接手」請看 [`docs/PROGRESS.md`](docs/PROGRESS.md)。

| 文件 | 內容 |
|---|---|
| [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) | 實作計畫、phase 切分、每個 phase 的完成條件 |
| [`docs/FEASIBILITY_PROTOCOL.md`](docs/FEASIBILITY_PROTOCOL.md) | **事前凍結**的評分協議與 GO／NO-GO gate |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | 固定下來的模型、parser、資料選擇與其 revision |
| [`docs/DATA_PROVENANCE.md`](docs/DATA_PROVENANCE.md) | 官方來源、取得方式、授權、什麼不進 git |
| [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) | prompt injection、SSRF、rate limit、leakage、secrets |
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | 進度日誌（每個 session 更新） |

結果產物（跑完才會有）：

```
results/feasibility/summary.json          # 所有 config × split 的指標
results/feasibility/error_analysis.jsonl  # 逐題 failure analysis
results/feasibility/GO_NO_GO.json         # 由程式依事前 gate 自動產生
```

---

## Quickstart

需求：Windows / Linux、**Python 3.13**、[uv](https://docs.astral.sh/uv/)。
（3.13 是硬需求，不是偏好——原因見 [`docs/DECISIONS.md`](docs/DECISIONS.md) D-001：
repo 路徑含非 ASCII 字元，Python ≤3.12 會以系統 locale 讀 `.pth` 而爆
`UnicodeDecodeError`。）
GPU 只有在 index build 與 generation 階段需要（RTX 4090 24GB 為目標環境）；
測試與資料驗證全部 CPU 且離線。

```bash
uv sync --extra dev
```

離線測試（不碰 MOPS／TWSE／模型／API、不需要 GPU、不讀 `.env`）：

```bash
uv run pytest
```

Lint / type check：

```bash
uv run ruff check . ; uv run ruff format --check . ; uv run mypy src
```

資料取得（會連外，有 rate limit；失敗時走人工放置 fallback）：

```bash
uv run python scripts/fetch_twse_openapi.py --manifest data/manifests/structured.yaml
uv run python scripts/fetch_documents.py --manifest data/manifests/documents.yaml
uv run python scripts/verify_manifests.py
```

---

## 專案獨立性

本 repository 完全獨立：不 import 其他本機專案、沒有 submodule、沒有 local path
dependency、沒有 symlink、不共用資料庫／cache／evaluation artifacts。
所有程式碼、schema、manifest、測試、evaluation 與文件都存在本 repository 內。

## License

程式碼採 [MIT](LICENSE)。
**License 不涵蓋**臺灣證券交易所／公開資訊觀測站之原始文件與資料；
那些內容依其原始授權條款，且本 repository 不重新散布。
