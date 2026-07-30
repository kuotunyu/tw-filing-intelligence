# DATA PROVENANCE

`status: P1 完成（來源已實測）／P2 取得表格待填`
`last_updated: 2026-07-31`

> **先讀 §8。** P1 的實測結果改變了資料取得策略：
> TWSE OpenAPI 是單期快照、新版 MOPS 是 JS SPA，
> 因此**文件走人工放置 fallback**，結構化當期資料走 OpenAPI 自動化。

本 repository **不重新散布**任何原始年報／財報 PDF 或 XBRL 檔案。
只提交：manifest、來源 URL、SHA-256、來源頁面、公司、年度、文件類型、
取得日期，以及能重建這些檔案的腳本。

---

## 1. 官方來源

| id | 名稱 | 進入點 | 用途 | 授權／使用條款 |
|---|---|---|---|---|
| S1 | 公開資訊觀測站 MOPS | https://mops.twse.com.tw/ | 年報、財務報告書 PDF | 依 MOPS 網站使用條款；不重新散布 |
| S2 | MOPS XBRL（單一公司／整批下載） | https://mops.twse.com.tw/ | 結構化財務數值 | 同上 |
| S3 | 臺灣證券交易所 OpenAPI | https://openapi.twse.com.tw/ ＋ https://openapi.twse.com.tw/v1/swagger.json | 結構化財務／基本資料 | TWSE OpenAPI 公開服務條款 |

**只允許連線的 host（allowlist，寫死在 `src/twfi/io/http.py`）**

```
mops.twse.com.tw
doc.twse.com.tw
openapi.twse.com.tw
```

任何其他 host 的請求會被 client 直接拒絕（有離線測試驗證）。
不接受由文件內容、模型輸出或使用者輸入動態決定的任意 URL（見 `THREAT_MODEL.md` 的 SSRF 段）。

---

## 2. 取得規則（硬性）

1. **不解 CAPTCHA。** 遇到需要驗證碼的路徑一律放棄，改走人工放置 fallback。
2. **不模擬大量互動式查詢。** 不驅動 MOPS 的表單／查詢介面做批量抓取。
3. **不使用未公開或逆向出的私人 endpoint。**
4. **Rate limit**：同一 host 最小請求間隔 `1.5s`，並行度 `1`。
5. **Timeout**：connect `10s` / read `60s`。
6. **Retry**：最多 3 次，指數退避（2s → 4s → 8s），只對 5xx 與連線錯誤重試；
   4xx 不重試。
7. **User-Agent**：`tw-filing-intelligence/0.1 (feasibility study; contact via repo)`。
8. **下載大小上限**：單檔 `80 MB`，單次執行總量 `600 MB`（防無限下載）。
9. **不對 MOPS 高頻爬取**：一次執行的 MOPS 請求數上限 `40`。
10. 下載失敗 → 走 §4 人工放置 fallback，**不繞過網站限制**。

---

## 3. Manifest 格式

### `data/manifests/documents.yaml`

```yaml
version: 1
documents:
  - doc_id: 2330-FY2024-AR
    company: {name: 台積電, code: "2330"}
    fiscal_year: 2024
    doc_type: annual_report        # annual_report | financial_report | xbrl
    split: locked                  # dev | locked
    source_page: "https://mops.twse.com.tw/..."   # 人可點的來源頁
    resolved_url: null             # 首次取得後寫入並鎖定
    sha256: null                   # 首次取得後寫入並鎖定
    bytes: null
    pages: null
    retrieved_at: null             # ISO-8601
    http_status: null
    acquisition: pending           # pending | fetched | manual
    notes: ""
```

### `data/manifests/structured.yaml`

```yaml
version: 1
datasets:
  - dataset_id: twse-openapi-t187ap06-L-ci
    source: S3
    endpoint: "https://openapi.twse.com.tw/v1/opendata/..."
    description: ""
    split: both
    sha256: null
    rows: null
    retrieved_at: null
    http_status: null
    acquisition: pending
```

**不變量**（由 `scripts/verify_manifests.py` 驗證）：

- 每筆 `acquisition != pending` 的紀錄都必須有 `sha256`、`retrieved_at`、
  `source_page` 或 `endpoint`。
- 本機檔案的實際 SHA-256 必須等於 manifest 中的值。
- `split` 必須與 `FEASIBILITY_PROTOCOL.md §1.2` 的公司分配一致。
- 沒有任何 `.pdf` / `.xbrl` / `.zip` 被 git 追蹤。

---

## 4. 人工放置 fallback

當自動下載失敗（限流、路徑變更、需要驗證碼等）：

1. 使用者自行從 `source_page` 以瀏覽器下載檔案。
2. 放到 `data/raw/manual/<doc_id>.pdf`。
3. 執行：

```bash
uv run python scripts/fetch_documents.py --manual-dir data/raw/manual --record-hash
```

4. 腳本計算 SHA-256 寫入 manifest，`acquisition: manual`，並要求填 `source_page`。
5. 之後任何重建都以此 hash 驗證檔案一致性。

> 這個 fallback 是為了**尊重網站限制**，不是繞過它。

---

## 5. 取得紀錄（P2 填實）

**宣告與紀錄分離**（避免腳本覆寫掉人寫的理由）：

| 檔案 | 誰寫 | 內容 |
|---|---|---|
| `data/manifests/documents.yaml` | 人 | 宣告：用哪些文件、人怎麼取得（含註解說明理由） |
| `data/manifests/structured.yaml` | 人 | 宣告：用哪些結構化 dataset |
| `data/manifests/acquisition.lock.yaml` | **程式** | 紀錄：實際取得了什麼（SHA-256／bytes／時間／頁數／rows） |

人類可讀的對照表由 `scripts/verify_manifests.py` 產生於
[`docs/reference/provenance_table.md`](reference/provenance_table.md)。

`AcquisitionRecord` 的 schema 讓「半記錄狀態」**無法被表達** ——
驗證所需的欄位全部是必填，所以一筆無法驗證的紀錄根本寫不出來。

---

## 6. 不進 git 的東西

`data/raw/`、`data/interim/`、`data/processed/`、`data/cache/`、`data/index/`、
`data/duckdb/`、所有 `*.pdf` / `*.xbrl` / `*.zip`、模型權重、log。

## 7. 進 git 的東西

`data/manifests/**`、`data/evaluation/**`（gold set 只含題目、答案、頁碼、bbox、
row key 與極短引文，不含長篇原文重製）、`results/feasibility/` 的四個檔案。

> Gold record 中的引文長度上限 **40 字**，僅用於標註可追溯性，
> 不構成原文重製。

---

## 8. P1 實測結果（2026-07-31）

三個發現改變了資料取得策略。原始證據：
`results/runs/mops_probe.json`、`docs/reference/twse_openapi_endpoints.md`。
重現方式：`scripts/explore_sources.py`、`scripts/sample_endpoint.py`、`scripts/probe_mops.py`。

### 8.1 TWSE OpenAPI 可用，但**只有單一期間**

- `openapi.twse.com.tw/v1/swagger.json`：**143 個 endpoint**，
  sha256 `2c2cecccb7a220ac9e263228a7659aa49b1ada5aea397650e601ad3dfcc48043`，306,043 bytes。
- `scripts/sample_endpoint.py /opendata/t187ap06_L_ci` → **1045 列，全部 `年度=115 季別=1`**。
  `年度`／`季別` 欄位存在，但只有**一個值**。
- **結論**：`/opendata/` 的財報 endpoint 是**當期快照**，
  **無法**提供 FY2023／FY2024。

影響：
- numeric route 的歷史數值**不能**靠 OpenAPI（原本 P4 的假設錯誤）。
- OpenAPI 改用於 (a) 公司基本資料（`t187ap03_L`）、
  (b) **當期**數值 —— 這反而是 cross_document 題與衝突偵測的好材料：
  「PDF 的 FY2024 數字」對「OpenAPI 的當期數字」是兩個獨立來源。

### 8.2 一般業與金控業是**兩套不同 schema**

| endpoint | 內容 | 欄位數 |
|---|---|---|
| `t187ap07_L_ci` | 上市公司資產負債表（一般業） | 26 |
| `t187ap07_L_fh` | 上市公司資產負債表（金控業） | **60** |
| `t187ap06_L_ci` | 綜合損益表（一般業） | 33 |
| `t187ap06_L_fh` | 綜合損益表（金控業） | 25（欄位名稱完全不同） |

金控用 `利息淨收益`、`保險負債準備淨變動`、`呆帳費用、承諾及保證責任準備提存`，
**沒有 `營業收入` 這一行**。這證實了 D-004 保留 2882 的理由：
numeric route 必須處理 per-industry schema，這是真實難點而不是人為刁難。

**P2 實測驗證**（`data/raw/structured/twse-openapi-t187ap06_L_fh.json`）：
`t187ap06_L_fh` 只有 **13 列**（全國 13 家金控：2880–2892、5880），
`2882 國泰金` 有 **25 個欄位**，`'營業收入' in row` → **False**。
所以「查營收」這個對一般業理所當然的操作，對金控會直接查不到欄位。
numeric route 必須在這種情況**報錯或拒答，而不是拿別的欄位硬湊**。

### 8.3 文件取得：**人工放置**，理由是規則而非能力

| 探測目標 | 結果 |
|---|---|
| `mops.twse.com.tw/mops/web/index` | 200，**65 bytes**，內容為 `<script> location.href = location.origin + "/mops"; </script>` |
| `mops.twse.com.tw/mops/web/t57sb01_q1` | 同上（同一個 JS bootstrap） |
| `doc.twse.com.tw/server-java/t57sb01` | 200，11,285 bytes，server-rendered `<form name='fm' action='/server-java/t57sb01' method='post'>`，欄位 `co_id`／`id`／`key`／`step`，標題「電子資料查詢作業」，**未偵測到 CAPTCHA** |

判斷：

1. 新版 MOPS 是 **single-page app**，沒有 server-rendered 內容。
   要取得資料就得呼叫它**未公開的 XHR API** → 違反「不使用未公開或逆向出的私人 endpoint」。
2. `doc.twse.com.tw/server-java/t57sb01` 是公開頁、**沒有驗證碼**，
   但它是 **POST 表單**，`step` 參數語意未公開 → 驅動它屬於**表單模擬／逆向**，
   而 brief 要求「優先使用正式 OpenAPI、公開下載頁及穩定直接文件」。
3. 本研究只需要 **7 份文件**。為 7 份文件去逆向表單，
   風險與規則成本都不划算。

**因此：文件（年報 PDF、XBRL）一律走 §4 的人工放置 fallback。**

#### 8.3.1 表單實際結構（從 `results/runs/mops_probe_bodies/doc-file-search.html` 讀出）

`doc.twse.com.tw/server-java/t57sb01` 的欄位：
`co_id`（公司代號）、`year`（資料年度，**民國**，maxlength=3）、
`seamon`（季別／月份）、`mtype`（資料類型）、`dtype`（資料細節說明）、
`step`（由 JavaScript 設定，語意未公開）。

`mtype` 的選項只有 7 類：
`A 財務報告書`、`B 公開說明書`、`C 財務預測書`、`D 基金財務報告書`、
`E 基金公開說明書`、**`F 股東會相關資料`**、`J 年度自結財務資訊`。

> ⚠️ **`mtype` 裡沒有「年報」這個選項。** 年報位於
> `F 股東會相關資料` → `dtype`：
> `F18 股東會年報(適用永續揭露準則)`、
> `F04 股東會年報(尚未適用永續揭露準則)`、
> `F11 股東會年報(股東會後修訂本)`。
> 先前文件中「資料類型『年報』」的寫法是錯的，已依實際頁面更正。

其他實務要點：
- `seamon` 只在 `mtype` 為 `A`／`D`（季別）或 `B`／`E`（月份）時才有選項；
  `F 股東會相關資料` **不需要**季別。
- 頁面提供 `DYNADOC WDL Viewer` 下載連結 → 舊年度檔案可能是 `.WDL` 而非 PDF。
  FY2023／FY2024 應為 PDF，若遇非 PDF 需在 manifest `notes` 記錄。
- **索引年度與封面年度可能不同**（年報於次年股東會提出）。
  以**封面**「民國112年度年報」為準命名檔案，不以 `year` 欄位為準。

這**不影響 G1**。G1 要求「資料取得流程可重現，沒有依賴破解或不穩定私人 endpoint」：
人工放置 ＋ `source_page` ＋ SHA-256 釘住，任何人都能重新下載並驗證位元相同，
而且完全不觸碰破解或私人 endpoint。這是**符合 G1 的取得方式**，
不是 G1 的例外。

> 這是一個**負面結果**，而且是有價值的負面結果：
> 「臺灣公開資訊在文件層級沒有穩定的官方批量下載介面」
> 本身就是 feasibility 問題的一部分，必須寫進 `docs/FEASIBILITY_REPORT.md`。
