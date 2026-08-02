# README Mermaid 圖表設計

- date: `2026-08-03`
- status: `approved`
- scope: `README.md only`

## 目標

以 GitHub 原生 Mermaid 取代 README 目前的 ASCII 架構框，讓第一次接觸專案的讀者能在一分鐘內回答三個問題：

1. 公開財報如何被整理成可查詢的 evidence？
2. 查詢如何在 narrative、numeric、chart routes 間分流並產生可追溯答案？
3. Protocol 1.0.0 如何防止看到 locked 結果後再調整評估規則？

圖表必須維持正體中文主體、保留原文 technical terms、不使用 emoji，並如實呈現 `NO_GO`，不得美化負面結果。

## 方案比較

### 方案 A：單張端到端大全景

將 ingestion、query-time routing 與 evaluation 全部放在一張圖。優點是只佔一個區塊；缺點是節點與跨線過多，手機與 GitHub 窄版難以閱讀，也混淆 runtime architecture 與 research methodology。

### 方案 B：系統架構與研究流程兩張圖

第一張合併 offline preparation 與 query-time routing，第二張呈現 protocol workflow。資訊量適中，但第一張仍需同時表達資料建置與三條 route，視覺層次容易擁擠。

### 方案 C：三張單一責任圖

拆成 offline data preparation、query-time answer flow、pre-registered evaluation。每張圖只回答一個問題，節點維持在約 10–15 個，最適合 README 快速閱讀。

採用方案 C。

## 圖表設計

### 圖一：Offline data preparation

位置：README「系統設計」開頭。

由左至右呈現兩條資料來源：

- MOPS PDF 經 SHA-256 provenance、layout / table / figure parsing，形成 chunks、table evidence 與 crop pixels。
- TWSE OpenAPI / XBRL 經 schema validation 與 row normalization，進入可驗證的 structured rows。

衍生儲存分成兩類：

- chunks 與 captions 建立 BM25、dense index；caption 只能用於 retrieval。
- validated rows 進入 DuckDB；保留 source、unit、basis 與 period。

此圖不畫 query 或模型回答，避免混淆 offline artifacts 與 runtime execution。

### 圖二：Query-time answer flow

位置：圖一之後，取代現有 ASCII 架構框並保留精簡文字說明。

查詢先經 company scope 與 typed bounded router，再分成：

- Narrative route：hybrid retrieval、cross-encoder reranking、grounded generation。
- Numeric route：DuckDB、templated SQL、deterministic calculation。
- Chart route：caption-assisted retrieval、original crop pixels、VLM reading。

三條 route 統一進入 answer / citation contract。證據充分時輸出 grounded answer、citation、formula / operands；證據不足、來源衝突或 citation 無法驗證時輸出 structured refusal。圖中必須明確顯示 caption 不可作為最終數值來源，numeric route 不允許 free-form SQL。

### 圖三：Pre-registered evaluation

位置：README「事前註冊實驗」段落，在 F0–F7 表格之前。

流程由 DEV-only decisions 開始，依序經過：

1. 固定 gold set、models、tolerance、F0–F7 與 G1–G10 gates。
2. `freeze_protocol.py` 寫入七個 artifact hashes。
3. 唯一一次 locked evaluation 執行 F0–F7。
4. `verify_results.py` 從 raw artifacts 重算。
5. `run_gate.py` 依 frozen gates 機械判定。
6. 產生 `GO_NO_GO.json` 與 feasibility report。

決策節點同時呈現理論上的 GO、CONDITIONAL_GO、NO_GO 三條輸出；本次實際路徑標示為 `NO_GO`，並連到 F7 6/33、F0 17/33、hard-category pooled gain -27.8pp。

## 視覺規則

- 使用 GitHub 支援度高的 `flowchart LR` / `flowchart TD`，不採用實驗性語法。
- 每張圖採一致的高對比 `classDef`，每個定義都明確設定 `color`。
- 正體中文作為說明語言；RAG、VLM、BM25、DuckDB、Protocol 等保留原文。
- 不使用 emoji 或裝飾性 Unicode symbols。
- node label 使用引號與 `<br/>`，避免括號、斜線或冒號造成 Mermaid parser ambiguity。
- 每張圖只描述 repository 已實作或已量測的行為，不加入 future-state architecture。

## 錯誤與限制呈現

- 資料品質問題以「provenance 驗證失敗／不可解析」路徑終止，不假裝所有 PDF 都可用。
- Query flow 將證據不足、來源衝突與 citation invalid 收斂至 refusal，不省略 failure path。
- Evaluation flow 明示 protocol freeze 之後不得修改 locked set、threshold 或 model。
- `NO_GO` 使用結果樣式而非錯誤樣式：它是有效研究結論，不是 pipeline failure。

## 驗證方式

1. 將每個 Mermaid code block 抽成獨立 `.mmd` 暫存檔。
2. 使用 Mermaid CLI 實際 render，三張圖都必須 exit 0 且產生非空輸出。
3. 檢查 README 不含 emoji，並保留「不是投資建議」與「不是 production 系統」。
4. 對照 `src/twfi/`、`scripts/`、Protocol 1.0.0 與 `results/feasibility/summary.json`，確認節點、順序與數字一致。
5. 執行 repository tests、Ruff、format check 與 strict mypy，避免 README invariant 或 CI 回歸。

不提交 render 後的 PNG / SVG；GitHub 直接渲染 README 內的 Mermaid，以免增加重複 artifact 與維護成本。
