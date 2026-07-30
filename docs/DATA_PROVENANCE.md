# DATA PROVENANCE

`status: SKELETON — 表格待 P1/P2 填實`
`last_updated: 2026-07-31`

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

| doc_id | 公司 | 年度 | 類型 | split | SHA-256 | 取得日 | 方式 |
|---|---|---|---|---|---|---|---|
| _(待填)_ | | | | | | | |

---

## 6. 不進 git 的東西

`data/raw/`、`data/interim/`、`data/processed/`、`data/cache/`、`data/index/`、
`data/duckdb/`、所有 `*.pdf` / `*.xbrl` / `*.zip`、模型權重、log。

## 7. 進 git 的東西

`data/manifests/**`、`data/evaluation/**`（gold set 只含題目、答案、頁碼、bbox、
row key 與極短引文，不含長篇原文重製）、`results/feasibility/` 的四個檔案。

> Gold record 中的引文長度上限 **40 字**，僅用於標註可追溯性，
> 不構成原文重製。
