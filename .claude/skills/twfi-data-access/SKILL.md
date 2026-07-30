---
name: twfi-data-access
description: 從公開資訊觀測站 MOPS、TWSE OpenAPI、XBRL 取得臺灣上市公司公開文件與結構化財務資料的規則與流程。當要下載年報／財報 PDF、抓 openapi.twse.com.tw、更新 data/manifests、計算 SHA-256、處理下載失敗、或處理 provenance 時使用。
---

# TWFI Data Access

## 先知道這三個 P1 實測事實（否則會走錯路）

1. **TWSE OpenAPI 是單期快照。** `/opendata/t187ap06_L_ci` 回 1045 列
   但全部 `年度=115 季別=1`。**拿不到 FY2023／FY2024。**
   → 歷史數值走 XBRL 或已驗證表格擷取；OpenAPI 用於公司基本資料 ＋
   **當期**數值（當作獨立交叉來源，見 D-011）。
   驗證指令：`uv run python scripts/sample_endpoint.py /opendata/t187ap06_L_ci`
2. **新版 MOPS 是 JS SPA。** `mops.twse.com.tw/mops/web/*` 只回 65 bytes 的
   `location.href = origin + "/mops"`。要抓它就得用未公開 XHR API → **禁止**。
3. **文件一律人工放置。** `doc.twse.com.tw/server-java/t57sb01` 沒有 CAPTCHA，
   但是 POST 表單且 `step` 語意未公開 → 驅動它是表單模擬／逆向。
   只有 7 份文件，不值得。人工放置 ＋ SHA-256 **符合 G1**，不是 G1 的例外。

**不要**因為「自動化比較厲害」而去逆向 MOPS。這會直接違反協議。

## 一般業 vs 金控業是兩套 schema

| endpoint | 欄位數 |
|---|---|
| `t187ap07_L_ci` 資產負債表（一般業） | 26 |
| `t187ap07_L_fh` 資產負債表（金控業） | **60** |

金控沒有 `營業收入` 這一行，用 `利息淨收益`／`保險負債準備淨變動`。
2882 必須走 `_fh`。numeric route 要處理 per-industry schema。

單位陷阱：`t187ap17_L` 的 `營業收入(百萬元)` 是**百萬元**，
`t187ap06_L_ci` 的 `營業收入` 是**千元**。

## 只有三個來源，只有三個 host

| id | 來源 | host |
|---|---|---|
| S1 | 公開資訊觀測站 MOPS（年報／財報 PDF） | `mops.twse.com.tw`, `doc.twse.com.tw` |
| S2 | MOPS XBRL（單一公司／整批下載） | `mops.twse.com.tw` |
| S3 | TWSE OpenAPI（`/v1/swagger.json`） | `openapi.twse.com.tw` |

**allowlist 寫死在 `src/twfi/io/http.py`。** 其他 host 一律拒絕。
`https` only；拒絕 redirect 逃逸、IP literal、`localhost`、私有網段、`169.254.169.254`。

> **請求目標永遠只能來自 manifest。**
> 文件內容、模型輸出、使用者自由輸入都不得成為 fetch 目標（SSRF 防護）。

## 硬性禮節

- 同 host 最小間隔 **1.5s**，並行度 **1**
- timeout：connect 10s / read 60s
- retry：最多 3 次指數退避（2/4/8s），只對 5xx 與連線錯誤；**4xx 不重試**
- User-Agent：`tw-filing-intelligence/0.1 (feasibility study; contact via repo)`
- 單檔 ≤ **80MB**；單次執行總量 ≤ **600MB**；MOPS 請求數 ≤ **40**
- **不解 CAPTCHA、不模擬大量互動查詢、不用未公開/逆向 endpoint**

## 標準流程

```bash
uv run python scripts/explore_sources.py                      # 重建 endpoint 參考文件
uv run python scripts/sample_endpoint.py /opendata/t187ap06_L_ci   # 檢查某 endpoint 實際內容
uv run python scripts/probe_mops.py                           # 重驗 MOPS 是否仍為 SPA
uv run python scripts/fetch_twse_openapi.py                   # 自動抓結構化當期資料
uv run python scripts/fetch_documents.py                      # 記錄人工放置檔案的 SHA-256
uv run python scripts/verify_manifests.py                     # integrity + coverage
uv run python scripts/verify_manifests.py --require-all       # G1 證據（缺檔即失敗）
```

## 年報在表單裡的位置（最容易走錯的一步）

`doc.twse.com.tw/server-java/t57sb01`：

1. `資料類型` **沒有「年報」選項**，年報在 **「股東會相關資料」** 底下。
2. `資料細節說明` 選 **「股東會年報(尚未適用永續揭露準則)」**（F04）。
   **不要**選「(適用永續揭露準則)」（F18）—— 實測 `2412` + 年度 `112` + F18
   會回「查無所需資料」。永續揭露準則 2026 年起才分階段適用，
   所以 FY2023／FY2024 一律歸在「尚未適用」。
3. 下拉選單**只顯示中文名稱，不顯示 F 代碼**；兩個下拉的第一個選項是空白，
   看起來像壞掉但其實正常，要點開才有內容。
4. `資料年度` 是**民國**年；股東會相關資料**不需要**季別。
5. **以封面年度命名檔案**（「民國112年度年報」→ `FY2023`），不以索引年度為準。

不要選：`股東會年報(僅永續專章)`、`特別股股東會年報`、`英文版-股東會年報`。

## 下載失敗 → 人工放置 fallback（不是繞過限制）

1. 使用者從 manifest 的 `source_page` 用瀏覽器自行下載
2. 放到 `data/raw/manual/<doc_id>.pdf`
3. `uv run python scripts/fetch_documents.py --manual-dir data/raw/manual --record-hash`
4. 腳本寫入 SHA-256，`acquisition: manual`，要求填 `source_page`

## Manifest 不變量

- `acquisition != pending` 的紀錄必須有 `sha256` + `retrieved_at` + 來源
- 本機檔案實際 SHA-256 必須等於 manifest 值
- `split` 必須符合 `FEASIBILITY_PROTOCOL.md §1.2`（DEV: 2412/1301；LOCKED: 2330/2317/2882）
- **沒有** `*.pdf` / `*.xbrl` / `*.zip` 被 git 追蹤

## 絕不 commit 的東西

原始 PDF／XBRL／zip、模型權重、index、cache、DuckDB 檔、log。
只 commit manifest（URL＋SHA-256＋來源頁＋公司＋年度＋類型＋取得日）與重建腳本。
Gold record 中的原文引文上限 **40 字**。
