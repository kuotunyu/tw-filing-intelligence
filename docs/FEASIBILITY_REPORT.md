# 可行性報告（⑤A TW Filing Intelligence）

> **這不是投資建議，也不是 production 系統。**
> 本文件是一份事前註冊可行性驗證的結果報告，樣本量小，結論的適用範圍見「限制」一節。

`protocol_lock_sha256: 18da972fb7c5242114e82c339724f28eb3b68d67aeff4cf6f907adbebf23679d`

> **Post-hoc audit（v1.0.5）**：本報告保留原始 runtime 結果。後續發現文字題的
> runtime scorer 只採 exact match，未實作 Protocol 已註冊的
> `exact match OR token-F1 >= 0.8`。Protocol-literal 重算使 F0 由 17/33 變為 18/33，
> F7 仍為 6/33，`NO_GO` 不變。另需注意：G1 表示 run 當時的本機 raw files 通過 hash
> 驗證；third-party raw bytes 未散布，所以 clean clone 只能重算 committed evaluation，
> 不能完整重建 ingestion。完整證據與 metric 缺口見 [`ANALYSIS_AUDIT.md`](ANALYSIS_AUDIT.md)。

## 判定：NO_GO

至少一個 hard gate 未通過。依協議 4，**不得**為了通過而修改題目、答案、tolerance 或門檻，也不得刪除這個結果。下方「最小的下一個研究問題」是唯一允許的前進方式。

| gate | 判定 | 類型 | 說明 |
|---|---|---|---|
| G1 data reproducible | ✅ PASS | hard | verify_manifests reported every SHA-256 matching |
| G2 hard category gain | ❌ **FAIL** | hard | pooled gain -27.8pp is below 15.0pp |
| | | | ↳ pooled hard set: F0 33.3% (6/18, 95% CI 16.3%-56.3%) -> F7 5.6% (1/18, 95% CI 1.0%-25.8%) (-27.8pp) |
| | | | ↳ cross_document: F0 0.0% (0/3, 95% CI 0.0%-56.1%) -> F7 0.0% (0/3, 95% CI 0.0%-56.1%) (+0.0pp) |
| | | | ↳ cross_page: F0 0.0% (0/4, 95% CI 0.0%-49.0%) -> F7 25.0% (1/4, 95% CI 4.6%-69.9%) (+25.0pp) |
| | | | ↳ cross_period_comparison: F0 50.0% (2/4, 95% CI 15.0%-85.0%) -> F7 0.0% (0/4, 95% CI 0.0%-49.0%) (-50.0pp) |
| | | | ↳ numeric_calculation: F0 40.0% (2/5, 95% CI 11.8%-76.9%) -> F7 0.0% (0/5, 95% CI 0.0%-43.4%) (-40.0pp) |
| G3 no overall regression | ❌ **FAIL** | hard | exceeds the 5.0pp allowance (change -33.3pp) |
| | | | ↳ F0 51.5% (17/33, 95% CI 35.2%-67.5%) |
| | | | ↳ F7 18.2% (6/33, 95% CI 8.6%-34.4%) |
| G4 citation validity | ❌ **FAIL** | hard | below the 90% threshold |
| | | | ↳ 52.9% (9/17, 95% CI 31.0%-73.8%) |
| G5 numeric route accuracy | ❌ **FAIL** | hard | below the 90% threshold |
| | | | ↳ 0.0% (0/12, 95% CI 0.0%-24.2%) |
| G6 route accuracy | ❌ **FAIL** | hard | below the 85% threshold |
| | | | ↳ 33.3% (11/33, 95% CI 19.8%-50.4%) |
| G7 does not over-answer | ❌ **FAIL** | hard | over-answer rate 75% exceeds 25%; refusal precision 6% is below 80% |
| | | | ↳ over-answered 75.0% (3/4, 95% CI 30.1%-95.4%) |
| | | | ↳ refusal precision 6.2% (1/16, 95% CI 1.1%-28.3%) |
| G8 refuses without evidence | ✅ PASS | hard | 5 of 5 probes refused; meets the required 4 |
| | | | ↳ 100.0% (5/5, 95% CI 56.6%-100.0%) |
| G9 results reproducible | ✅ PASS | hard | verify_results recomputed every summary figure from raw artifacts |
| G10 resources feasible | ✅ PASS | soft | within every limit |
| | | | ↳ retrieval p95 0.149 (limit 3) |
| | | | ↳ generation p95 2.957 (limit 60) |
| | | | ↳ VRAM peak 20.09 (limit 22) |

## 主要比較

Registered baseline: `F0`；candidate: `F7`。

| factor | overall answer accuracy |
|---|---|
| F0 | 51.5% (17/33, 95% CI 35.2%-67.5%) |
| F1 | 45.5% (15/33, 95% CI 29.8%-62.0%) |
| F2 | 42.4% (14/33, 95% CI 27.2%-59.2%) |
| F3 | 57.6% (19/33, 95% CI 40.8%-72.8%) |
| F4 | 57.6% (19/33, 95% CI 40.8%-72.8%) |
| F5 | 60.6% (20/33, 95% CI 43.7%-75.3%) |
| F6 | 54.5% (18/33, 95% CI 38.0%-70.2%) |
| F7 | 18.2% (6/33, 95% CI 8.6%-34.4%) |

### F7 category accuracy

| category | accuracy |
|---|---|
| chart_value_trend | 0.0% (0/2, 95% CI 0.0%-65.8%) |
| cross_document | 0.0% (0/3, 95% CI 0.0%-56.1%) |
| cross_page | 25.0% (1/4, 95% CI 4.6%-69.9%) |
| cross_period_comparison | 0.0% (0/4, 95% CI 0.0%-49.0%) |
| narrative_fact | 66.7% (4/6, 95% CI 30.0%-90.3%) |
| numeric_calculation | 0.0% (0/5, 95% CI 0.0%-43.4%) |
| table_cell | 0.0% (0/5, 95% CI 0.0%-43.4%) |
| unanswerable | 25.0% (1/4, 95% CI 4.6%-69.9%) |

### Candidate gate proportions

| metric | observed |
|---|---|
| citation validity | 52.9% (9/17, 95% CI 31.0%-73.8%) |
| numeric route accuracy | 0.0% (0/12, 95% CI 0.0%-24.2%) |
| route accuracy | 33.3% (11/33, 95% CI 19.8%-50.4%) |
| over-answer rate | 75.0% (3/4, 95% CI 30.1%-95.4%) |
| refusal precision | 6.2% (1/16, 95% CI 1.1%-28.3%) |
| no-evidence probes refused | 100.0% (5/5, 95% CI 56.6%-100.0%) |

### Resource measurements

| metric | observed |
|---|---|
| retrieval_p95_s | 0.149 |
| generation_p95_s | 2.957 |
| vram_peak_gb | 20.09 |

每個比率都附 n、分子與 Wilson 95% 信賴區間。**區間重疊代表這份樣本無法分辨兩者** ——
這一句不因結果好壞而刪除。

## Gold set 組成（D-019 要求逐項印出）

| 項目 | 數量 |
|---|---|
| records | 33 |
| fully_human | 19 |
| answer_model_drafted | 7 |
| question_model_chosen | 9 |
| needs_audit | 14 |
| audited | 10 |
| trustworthy | 29 |
| audit rate | 10/14 (71%) |

部分 gold 由模型讀渲染頁面起草，並經固定種子的人工抽樣稽核。上表讓讀者自行折扣。

## 發現（含負面結果）

- F7 error analysis（同一題可屬多個 bucket）：route_error=22、incorrect_refusal=15、citation_invalid=8、retrieval_miss=6、answer_error=3、over_answer=3。

## 最小的下一個研究問題

僅將 rule-based layout parser 替換為 learned layout model，其餘設定固定，能否讓 pooled hard categories 相對 F0 達到註冊的 +15pp？

## 限制

### What the sample can and cannot support

locked set 為 33 題分到 8 個類型，單一類別只有 2–6 題。**一題等於 17–50 個百分點**，所以本輪樣本只能支持「可行／不可行」與「增益方向」的判斷，**不能**支持精確的效果量估計，也不能宣稱類別之間的差異具統計顯著性。每個比率都附 Wilson 95% 信賴區間；區間重疊即代表這份樣本無法分辨兩者。這一段不因結果好壞而改寫 —— 若 candidate 大幅勝出，同樣要寫「信賴區間很寬」。

### What the two chart questions actually measure

`chart_value_trend` 只有 **2 題**，且**兩題都來自台積電的兩頁**（D-020）。全語料 503 個 figure candidate 中，逐一目視確認的真圖表只有 4 張，鴻海與國泰金 0 張，兩份財務報告書 0 張。因此這兩題能回答的是「能不能讀台積電那兩張資訊圖」，**不是「能不能讀圖表」**。更進一步（D-022 更正）：那兩頁的文字層完整，年份與圖例都抽得出來，所以**純文字系統靠座標鄰近也可能答對** —— 本語料唯一的真圖表也是文字可還原的。F5（caption）與 F6（crop VLM）的輸入幾乎全是有框表格，它們的增益**不得**被描述為 chart-reading 能力（D-021）。原訂的 chart challenger因兩份 DEV filing 沒有真圖表、16 題無從建立而標記為 `cancelled`，`outcome=null`，因此**沒有比較結果**；依事前 fallback，所有 route 使用 `qwen3.6:27b`。

### Why the numeric store holds what it holds

locked numeric route 使用 `numeric_broad.duckdb`：`load_all_rows.py` 逐頁走訪所有可用 filing，**不看 gold** 的答案、頁碼或 structured key，並載入抽取器找到的可分類科目。因此 F4 的可用數字不是由題目清單安排出來。這仍不是完整的財報資料庫：只有科目可分類、欄位能對應單一會計年度，且頁面有單位或可由前頁繼承時才會載入；受損文字層、碎裂表頭、合併年度、旋轉頁與列標籤脫節都會形成覆蓋缺口。DEV 上 broad store的 F4 為 11/15，只是凍結前的開發觀察，不能代替 locked 結果或一般化覆蓋率。

### Why this says verified rather than official structured data

TWSE OpenAPI 只提供當期快照，與本研究的文件年度（FY2023／FY2024）交集為空，所以 locked store 的歷史結構化數值來自**本 repository 自己從 filing line stream重建的 row**（`source_kind=extracted_text_row`）。因此報告一律寫「**已驗證結構化資料**」而非「官方結構化資料」（R7）。若日後取得官方 XBRL，這項限制大幅緩解，但那需要重跑並重新標示來源。

### What was not tested: learned layout models

candidate parser 是**自建的 rule-based layout parser**（D-002），本輪**未驗證** learned layout model（如 docling、LayoutLM 類）。所以「結構化 parsing 帶來多少增益」的結論**不得**外推到學習式版面模型。表格抽取採 pdfplumber 兩種 strategy 的聯集（D-027），該選擇是在 dev 上量測後決定的，理由是沒有任一 strategy 支配另一個。

### That both development-set filings have damaged text layers

兩份 development filing 的原生文字層都受損：2412-FY2023-AR 的頁面可讀率為 95%，但 17.9% 字元解碼為錯誤字集、48% 頁面受影響；1301-FY2023-AR 分別為 96%、15.4%與 43%。『可讀』只表示頁面產出字元，**不代表字元正確**。此外，全語料有 150 頁含無法解讀的 private-use 字元；2882-FY2024-AR p26 的 125 個打勾全數遺失。gold 的 71 個引用頁面都不屬亂碼頁，標註者讀的是渲染頁，`verify_gold_answers.py` 會強制檢查。因此 DEV 上選出的閾值、chunking 與路由行為同時反映系統能力和受損文字層，不能直接推論到文字層完整的一般年報。locked 結果保留這個 domain shift，不把它解讀成純模型效果。

### That dev's 15 questions cover only four distinct evidence targets

DEV 的 15 題只涵蓋 4 個不同的 (document, page-set) 證據目標，其中一個 chunk 承載8 題；DEV-0011 的註冊答案又不在文件中，所以 retrieval 指標天花板是 14/15。這些題目高度相關，DEV 差異只能用來選定方向與發現接線錯誤，不能當作獨立樣本的效果量或統計顯著性證據（量測差異皆未達 5%，最小 p=0.219）。字元預算雖拉平，baseline 每chunk 平均跨 1.57 頁、candidate 1.25 頁，同預算的相異頁數仍差 1.3–1.7 倍；因此預算表差距不得直接宣稱為檢索能力差距。

### That an account name is not a unique key in a filing

全語料抽取顯示 account name 不是 filing 內的唯一鍵：locked 三家按正式 key 分組有46/115（40.0%）存在衝突值，國泰金控為 32/34（94.1%），因附註會為不同子公司重複相同科目。DEV 在加入consolidated/parent-only basis 後觀察到 0% 衝突，不能據此保證 locked。store 會保留每個 `source_ref`；同一 key 有多個候選時 numeric route 拒答，不讓最後讀到的頁面覆蓋先前來源。這是安全但會降低覆蓋率的失效模式，不是通用財報資料庫的正確性證明。

### That final pre-freeze approval was not independent or blind

D-048（numeric store）、D-049（company scope）與 D-050（正式版號）的最終批准方式，是使用者在 development 結果已可見後委任實作者判斷，**不是**由未看過結果的獨立審查者盲評。採用 broad store 與全階一致 scope 的理由在數字反轉時仍成立，但這無法恢復『先決定再看數字』的獨立性；讀者應據此降低對 F4 與整體 confirmatory 解讀的信任。
