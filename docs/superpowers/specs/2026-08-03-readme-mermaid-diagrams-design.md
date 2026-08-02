# README Mermaid 圖表設計

- date: `2026-08-03`
- status: `approved revision 2`
- scope: `README.md only`

## 目標

讓第一次接觸專案的讀者不必猜測箭頭、資料來源或術語，就能在一分鐘內回答四個問題：

1. MOPS PDF 如何變成可檢索、可引用的 evidence？
2. 本次 locked run 的歷史數值實際來自哪裡，哪些來源沒有使用？
3. 問題如何在 narrative、numeric、chart routes 間分流並產生可追溯答案？
4. Protocol 1.0.0 如何防止看到 locked 結果後再調整評估規則？

README 維持正體中文主體、technical terms 保留原文、不使用 emoji，並如實呈現 `NO_GO` 與資料限制。

## Review 發現

原圖將 MOPS PDF 與 TWSE OpenAPI / XBRL 放進同一個 provenance 區塊，兩條未標示的箭頭分別通往不同處理流程。讀者需要自行推測來源與去向，且圖面暗示 OpenAPI / XBRL 直接建立了 FY2023–FY2024 locked numeric store。

實際情況是：

- TWSE OpenAPI 只有 FY2026Q1 snapshot，與研究文件年度無交集。
- XBRL 本輪未取得。
- locked numeric route 使用 `numeric_broad.duckdb`，歷史 rows 由 filing line stream 重建，`source_kind=extracted_text_row`。

修正版必須直接寫出這三件事，不能把它們留給讀者從完整 report 推導。

## 方案比較

### 方案 A：只翻譯既有 labels

改動最少，但來源線仍交錯，也沒有修正 OpenAPI / XBRL 對 locked store 的錯誤暗示。不採用。

### 方案 B：保留單圖，改成兩條 swimlane

能區分 PDF 與 structured sources，但 GitHub README 仍會產生過寬或過高的大圖，縮放後文字偏小。不採用。

### 方案 C：拆成四張單一責任圖

將資料準備拆成「PDF 如何變成 evidence」與「本輪 numeric store 的實際來源」，再保留 query flow 與 evaluation flow。每張圖先以一句白話說明目的，節點只表達一個動作。

採用方案 C。

## 圖表設計

### 圖一：PDF 如何變成 evidence

位置：README「系統設計」開頭。

白話導讀：這張圖只回答「一份 PDF 進來後，哪些內容會進 index、DuckDB 或 VLM」。

流程：

1. MOPS PDF 先記錄官方 URL、檔案大小與 SHA-256。
2. 判斷文字層是否可解析。
3. 不可解析時保留失敗紀錄，但不作為 gold 題目的 evidence source。
4. 可解析時進行 layout、table、figure parsing。
5. 段落 chunks 進 BM25 + dense index。
6. 可分類的表格／文字 rows 驗證 period、unit、basis 與 source 後進 DuckDB。
7. Figure caption 只進 index；原始 crop pixels 才交給 VLM 讀值。

### 圖二：本輪 numeric store 的實際來源

位置：圖一之後。

白話導讀：這張圖明確回答「locked numeric route 查的數字究竟是哪裡來的」。

三條來源結果：

- MOPS filing line stream → row reconstruction → `numeric_broad.duckdb` → 本次 locked numeric route 使用。
- TWSE OpenAPI → FY2026Q1 snapshot → 與 FY2023–FY2024 無交集 → 未進 locked store。
- XBRL → 本輪未取得 → 未進 locked store。

圖後加一句限制：本輪應稱「已驗證結構化資料」，不能稱「官方結構化歷史資料」。

### 圖三：問題如何選 route 並產生答案

位置：圖二之後。

白話導讀：問題先限制公司、年度與文件，再依題型選路徑；所有路徑都必須通過 evidence 與 citation 驗證，否則拒答。

- Narrative：找相關文字 → rerank → LLM 僅依 evidence 回答。
- Numeric：查 DuckDB → 固定 SQL template → 程式計算 formula 與 operands。
- Chart / table：caption 只負責找頁 → 回到 original crop pixels → VLM 讀值。
- 最後統一驗證 evidence、citation 與來源衝突；不通過時 structured refusal。

### 圖四：事前註冊評估如何防止調答案

位置：README「事前註冊實驗」，F0–F7 table 之前。

白話導讀：只有 DEV 階段可以調整設定；freeze 後只能執行、重算與依既定 gates 判定。

流程：DEV 定案 → protocol freeze + seven hashes → 唯一 locked run → raw records 重算 → G1–G10 機械判定 → `GO / CONDITIONAL_GO / NO_GO`。本次實際路徑標示 `NO_GO`，連到 F0 17/33、F7 6/33、hard-category pooled gain -27.8pp。

## 文案與視覺規則

- 每張圖前必須有一句「這張圖在回答什麼」，不得只靠標題。
- 主標題、節點與箭頭以正體中文動作句為主；專有名詞保留原文並在同一節解釋用途。
- 一條箭頭只表示一種資料流；不同來源不可共用沒有標籤的入口。
- 不使用大型混合 subgraph；優先採簡短的 `flowchart TD` 或小型 `flowchart LR`。
- 每張圖節點不超過 15 個；node label 使用引號與 `<br/>`。
- 每個 `classDef` 明確設定高對比 `fill`、`stroke`、`color`。
- 不使用 emoji 或裝飾性 Unicode symbols。
- 只描述 repository 已實作或已量測的 current state，不畫 future-state architecture。

## 錯誤與限制呈現

- 「不可解析 PDF」是被量測並保留的資料品質結果，不畫成被刪除。
- OpenAPI 與 XBRL 沒有進 locked store 必須直接寫出原因。
- Query flow 明示證據不足、來源衝突或 citation invalid 都會拒答。
- Evaluation flow 明示 freeze 後不得修改 locked set、threshold 或 model。
- `NO_GO` 使用結果樣式而非錯誤樣式：它是有效研究結論，不是 pipeline failure。

## 驗證方式

1. 將每個 Mermaid block 抽成獨立 `.mmd` 暫存檔。
2. 使用 Mermaid CLI 實際 render，四張圖都必須 exit 0 且產生非空 SVG / PNG。
3. 實際檢視四張 render，確認文字尺寸、箭頭方向與 branch labels 清楚。
4. 檢查 README 不含 emoji，並保留「不是投資建議」與「不是 production 系統」。
5. 對照 report、protocol、`src/twfi/` 與 `scripts/run_eval.py`，確認 sources、routes、順序與數字一致。
6. 執行 pytest、Ruff、format check 與 strict mypy。

不提交 render 後的 `.mmd`、PNG 或 SVG；GitHub 直接渲染 README 內的 Mermaid。
