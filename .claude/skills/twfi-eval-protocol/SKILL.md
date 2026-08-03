---
name: twfi-eval-protocol
description: tw-filing-intelligence 的 frozen evaluation audit 規則：dev/locked 分離、Protocol 1.0.0 hash 驗證、F0–F7 committed records、G1–G10 與 NO_GO 重算。v1.x 已 Feature Freeze，只能驗證，不得再標註、調參或重跑模型。
---

# TWFI Evaluation Protocol

權威來源依序是 frozen `docs/FEASIBILITY_PROTOCOL.md`／protocol lock／committed raw records、
`docs/ERRATA.md` 與 `docs/FEASIBILITY_REPORT.md`。本 skill 只摘要最終可驗證狀態。

## 兩個集合，永不混用

| | DEV | LOCKED |
|---|---|---|
| 檔案 | `data/evaluation/dev/gold.jsonl` | `data/evaluation/locked/gold.jsonl` |
| 題數 | 15 | **33** |
| 公司 | 2412 中華電信、1301 台塑 | 2330 台積電、2317 鴻海、2882 國泰金控 |
| v1.x 可改 | ❌ Feature Freeze | ❌ freeze 後永不 |
| 歷史用途 | freeze 前調參／prompt／threshold | 唯一正式比較依據 |

**歷史上所有開發選擇只能看 DEV；v1.x 現在兩個集合都不可再用來調整。**
Locked 正式 run 已完成一次，不得為了改善結果重跑。

## Locked 題型分布（33 題，freeze 後不可改）

`narrative_fact` 6｜`table_cell` 5｜`numeric_calculation` 5 ⚡｜
`cross_period_comparison` 4 ⚡｜`chart_value_trend` 2 ⚡｜`cross_page` 4 ⚡｜
`cross_document` 3 ⚡｜`unanswerable` 4
（⚡ = hard category，G2 gate 用）

## Gold record 規則

- 必填欄位見 protocol §1.5（含 `bbox`、`structured_source_key`、`required_evidence`、
  `tolerance`、`annotator`）
- `annotator`／`question_author` 必須具名；模型協助與人工 audit 狀態必須揭露
- **gold answer 不得由 candidate 輸出產生**
- 數值題必須有 `structured_source_key` 或 `bbox`
- `unanswerable` 的 `answer` 固定 `null` ＋ `refusal_reason_class`
- 引文上限 40 字

## 模型（freeze 後不可換）

| 角色 | 模型 | backend |
|---|---|---|
| Embedding | `BAAI/bge-m3` fp16 | HF transformers |
| Reranker | `BAAI/bge-reranker-v2-m3` fp16 | HF transformers |
| Generation ＋ chart | `qwen3.6:27b` digest `a50eda8ed977` Q4_K_M | ollama 0.32.0 |

**文字與圖表共用同一個多模態模型**（已實測有 vision）。數值答案一律走 SQL，不由模型生成。
decoding 固定：`temperature=0.0, top_p=1.0, top_k=1, seed=20260731,
num_predict=512, num_ctx=8192, think=false`。`gpt-oss:20b` 不進 pipeline。

**Chart challenger 已在 freeze 前取消**：DEV 文件沒有可支撐 challenger 的 chart 題，
因此沒有用 test set 回頭選模型；locked run 只使用 frozen model pins。

## Factor ladder（factor-at-a-time）

`F0` baseline（PyMuPDF plain + 固定 chunk + BM25 + 固定 top-k）
→ `F1` structure-aware parsing → `F2` hybrid retrieval → `F3` cross-encoder rerank
→ `F4` numeric SQL route → `F5` chart caption index → `F6` crop evidence answering
→ `F7` typed bounded routing（= candidate，所有 gate 用 F7）

`Δ(Fk) = metric(Fk) − metric(Fk−1)` 才是增益歸因。
**不要只比 F0 vs F7。**

## 指標（deterministic 優先）

- Retrieval：Recall@5、MRR@10、complete evidence coverage、cross-page evidence coverage
- Answer：exact match、token F1、numeric accuracy（宣告 tolerance）、unit accuracy、
  period accuracy、refusal precision/recall、over-answer rate
- Citation：precision、recall、page correctness、bbox(IoU≥0.3)/row validity、
  citation validity
- Routing：route accuracy、confusion matrix（6 類）
- Systems：ingestion latency、retrieval p50/p95、generation p50/p95、VRAM peak、
  tokens、cost（全 local ⇒ `0.0` ＋ `cost_basis`，不捏造）、**cold/warm 分開**

**LLM-as-judge 只用於 `evidence_sufficiency`，且不參與任何 gate。**

## 小樣本誠實性（不可省略）

單一類別只有 3–6 題 ⇒ **1 題 = 17–33pp**。所以：
每個指標都要輸出 `n` ＋ 分子 ＋ 分母 ＋ **Wilson 95% CI**；只寫百分比視為 report
不完整（G9 會擋）。Report limitations 必須寫明本輪樣本量只能判斷
「可行／不可行」與增益方向，**不能**做精確效果量估計或宣稱統計顯著。
結果好壞都要寫。

## Normalization（scoring 前必套，freeze 後不可改）

全角→半角｜去千分位｜`億=1e8`/`萬=1e4`/`千元=1e3`｜括號負數 `(1,234)→-1234`｜
`12.3%` ≠ `0.123`（以 gold `unit` 為準）｜幣別正規化為 `TWD`｜
民國年↔西元年（`112年`↔`2023`）｜數值題用數值比較

## v1.x 只允許驗證，不允許重跑

```bash
uv run pytest tests/test_protocol_lock.py::test_real_protocol_lock_still_holds -q -p no:cacheprovider -o addopts=
uv run python scripts/verify_results.py --dry-run
uv run python scripts/check_leakage.py
uv run python scripts/verify_evidence.py
```

以上只讀 committed artifacts。不得執行 `freeze_protocol.py`、`run_eval.py` 或任何模型推論
來改寫 v1.x 結果；新的研究假設必須另開 Protocol 2.x。

## Gate 摘要（G1–G9 hard、G10 soft）

G1 資料可重現｜**G2 合併 hard set（18 題）改善 ≥15pp ＋ 至少一單類 ≥10pp（兩者都要）**｜
G3 overall 不退步 >5pp｜G4 citation validity ≥90%｜G5 numeric route ≥90%｜
G6 route accuracy ≥85%｜G7 over-answer ≤25% 且 refusal precision ≥80%｜
G8 no-evidence probe 5 題中 ≥4 拒答｜G9 結果可由 raw artifacts 重建｜
G10 retrieval p95 ≤3s、generation p95 ≤60s、VRAM ≤22GB

**GO** = G1–G9 全過 ＋ G10 過｜**CONDITIONAL_GO** = G1–G9 全過但 G10 不過｜
**NO_GO** = G1–G9 任一不過

唯一 locked run 的判定是 `NO_GO`，由 frozen gate 邏輯產生，**人工不得覆寫**。
不是 GO 就不得改題目、不得刪負面結果、不得建產品 UI，
必須在 report 寫出「最小的下一個研究問題」。只有明確 GO 才能進 ⑤B。
