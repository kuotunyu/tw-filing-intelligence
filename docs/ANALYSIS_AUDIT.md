# Locked Analysis Audit

> 本文件是 `v1.0.5` 的 post-hoc research supplement。它只使用 committed artifacts
> 做 deterministic recomputation；**不改寫** frozen Protocol、gold、threshold、run records、
> `summary.json` 或原始 `NO_GO`。

## 結論

原始 locked run 的 runtime scorer 與 Protocol 1.0.0 有一處不一致：文字題的 runtime
只接受 `exact_match`，但 Protocol §4 註冊的是
`exact_match OR token-F1 >= 0.8`。以 frozen gold 重新計算既有 prediction 後，F0 baseline
由 recorded 17/33 變為 protocol-literal 18/33；F7 candidate 維持 6/33。**NO_GO 不變，
而且 baseline 與 candidate 的差距更大。**

這是 analysis correction，不是重新執行實驗，也不是為了得到較好的結果調整 protocol。
機器可讀證據在 [`analysis_audit.json`](../results/feasibility/analysis_audit.json)，可用
`uv run python scripts/verify_analysis_audit.py` 離線驗證。

## 1. Scoring deviation

| factor | recorded correct | protocol-literal correct | 差異題目 |
|---|---:|---:|---|
| F0 | 17/33 | 18/33 | LOCK-0019 |
| F1 | 15/33 | 16/33 | LOCK-0019 |
| F2 | 14/33 | 16/33 | LOCK-0019、LOCK-0022 |
| F3 | 19/33 | 19/33 | — |
| F4 | 19/33 | 19/33 | — |
| F5 | 20/33 | 20/33 | — |
| F6 | 18/33 | 18/33 | — |
| F7 | 6/33 | 6/33 | — |

所有 264 筆 prediction 以目前 committed runtime scorer 對 frozen gold 重評後，都與原本
`score` object 一致；差異來自 runtime 的 primary `correct` rule 未實作已註冊的 F1
disjunction，而不是 prediction、gold 或 raw record 被更換。

原始與 protocol-literal 兩種讀法都保留：

| 比較 | baseline only | candidate only | exact McNemar p（two-sided） |
|---|---:|---:|---:|
| recorded overall（33 題） | 12 | 1 | 0.0034179688 |
| protocol-literal overall（33 題） | 13 | 1 | 0.0018310547 |
| pooled hard set（18 題；兩種讀法相同） | 6 | 1 | 0.125 |

McNemar 使用同一批題目的 paired outcomes；它沒有解決公司、文件與頁面群聚造成的相依性，
也不能把 33 題 purposive sample 外推為一般財報問答可靠度。

## 2. 可從 committed records 重算的 secondary metrics

以下只描述 F7 candidate：

| metric | recomputation |
|---|---:|
| Recall@5 | 25/33（75.8%） |
| MRR@10 | mean 0.4584（n=33） |
| complete evidence coverage@5 | 19/33（57.6%） |
| 多目標題完整覆蓋@5 | 1/8（12.5%） |
| exact match（answerable） | 4/29（13.8%） |
| token-F1（answerable） | mean 0.1972（n=29） |
| numeric_ok（runtime applicability） | 4/26（15.4%） |
| unit_ok（applicable records） | 2/16（12.5%） |
| period_ok（answerable） | 1/29（3.4%） |
| refusal precision / recall | 1/16（6.2%）／1/4（25.0%） |

`numeric_ok` 的 denominator 沿用 runtime scorer 對「答案是否含可解析數值」的判斷，
不應被誤讀為某個預先定義題型的獨立 benchmark。

### Citation metric 的證據界線

`citation validity = 9/17（52.9%）` 可以從 committed `cited_ok` 再聚合，但 supporting
passage text、逐 citation relevance label、candidate bbox／row verdict 沒有一併提交。
因此它只能標記為 **reaggregated runtime verdict**，不能描述成 clean-clone independent
regrading。下列 preregistered secondary metrics並未保存足夠資料，狀態是 `not_collected`：

- citation precision
- citation recall
- citation page correctness
- citation bbox / structured-row validity

不以 `0%` 代替 missing，也不從首頁隱藏。

## 3. Gold 與 selection risk

33 筆 gold 中，19 筆題目與答案都由人撰寫；14 筆含 model-chosen question 或
model-drafted answer，其中 10 筆有人工 audit。依 repository schema，29/33 筆為
`trustworthy`。下列四筆仍是 machine-chosen question 且 `audited=false`：

- LOCK-0025
- LOCK-0026
- LOCK-0030
- LOCK-0031

這不代表四筆答案已知錯誤，而是 final gold 並非完全 independent、blind audit。
Candidate `qwen3.6:27b` 沒有產生 gold；但研究由同一實作者設計、執行與分析，且 locked
文件內容對實作者可見，所以不能排除 question-selection bias 或未知的模型 pretraining
contamination。

若日後補 independent review，應新增獨立的 post-hoc audit artifact，不得回寫 frozen
`gold.jsonl` 的 `audited` 欄位。

## 4. Reproducibility tiers

| 層級 | 狀態 | clean clone 能做什麼 |
|---|---|---|
| Frozen protocol / gold / run records | Verified | 驗證 hash 與 264 筆 locked records |
| Summary / gates / NO_GO | Verified | 從 committed records 重算 headline metrics 與 gate |
| Post-hoc scoring audit | Verified | 重新 score prediction、比對 runtime、套用 protocol-literal rule |
| Source acquisition | Partial | 只有 manifest、URL、bytes 與 SHA-256；third-party raw bytes 未散布 |
| Parsing / index build | Partial | 需重新取得 bit-identical source；derived index 與 DuckDB 未提交 |
| Model rerun | Partial | model weights 未散布；HF embedding／reranker 只有 observed revision，declared revision 為 null |

因此正確表述是：**committed evaluation 與 analysis 可由 clean clone 離線重算；從官方
source 到 model prediction 的 end-to-end reproduction 尚未在 clean clone 證明。**
Locked run 使用 code commit `595268f3a64ee9430efc397140c2f600c925436b`；後續版本只補
publication、verification 與 presentation，不應假裝是原始 execution code。

## 5. 研究定位

- Receipt benchmark（如 SROIE）主要測收據 OCR／欄位擷取；本研究是臺灣公開財報、
  多文件／跨頁證據與 preregistered go/no-go，資料域與研究問題不同。
- Hybrid RAG 是本研究使用的 architecture family，不是本專案宣稱的新演算法；本專案
  沒有 Knowledge Graph，也不主張 architecture novelty。
- RAG attribution / citation benchmark 研究與本專案相鄰，但本研究的 citation validity
  是較窄的 local runtime metric，不能與完整 attribution evaluation 等同。

研究貢獻在於保存「增加系統複雜度仍大幅退步」的可審查 failure evidence、指出 routing、
numeric coverage 與 evidence completeness 的耦合風險，而不是提出更強的新 RAG。
