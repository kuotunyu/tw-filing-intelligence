---
name: twfi-eval-protocol
description: tw-filing-intelligence 的評估紀律：dev/locked set 分離、protocol freeze 與 hash 驗證、F0–F7 factor ladder、retrieval/answer/citation/routing/systems 指標定義、normalization 規則、G1–G10 GO/NO-GO gate。當要標註 gold set、加指標、跑 evaluation、freeze protocol、判定 GO/NO-GO 或寫 feasibility report 時使用。
---

# TWFI Evaluation Protocol

權威來源是 `docs/FEASIBILITY_PROTOCOL.md`。本 skill 是操作摘要，衝突時以該文件為準。

## 兩個集合，永不混用

| | DEV | LOCKED |
|---|---|---|
| 檔案 | `data/evaluation/dev/gold.jsonl` | `data/evaluation/locked/gold.jsonl` |
| 題數 | 15 | **36** |
| 公司 | 2412 中華電信、1301 台塑 | 2330 台積電、2317 鴻海、2882 國泰金控 |
| 可改 | ✅ 隨時 | ❌ freeze 後永不 |
| 用途 | 調參／prompt／threshold | 唯一正式比較依據 |

**所有超參數、prompt、tolerance、chunk size、top-k 只能在 DEV 上調。**
Locked 只跑一次；重跑須記錄原因與次數。

## Locked 題型分布（36 題，freeze 後不可改）

`narrative_fact` 6｜`table_cell` 5｜`numeric_calculation` 5 ⚡｜
`cross_period_comparison` 4 ⚡｜`chart_value_trend` 5 ⚡｜`cross_page` 4 ⚡｜
`cross_document` 3 ⚡｜`unanswerable` 4
（⚡ = hard category，G2 gate 用）

## Gold record 規則

- 必填欄位見 protocol §1.5（含 `bbox`、`structured_source_key`、`required_evidence`、
  `tolerance`、`annotator`）
- **`annotator` 必須是 `human`；gold answer 不得由 candidate 輸出產生**
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

**Chart challenger（只有一次，只在 freeze 前，只用 DEV 資料）**：
`qwen3-vl:8b` digest `901cae732162` 對 `data/evaluation/dev/chart_challenger.jsonl`
（16 題）與 27B 比較。事前規則：**8b 高出 ≥10pp（≥多對 2 題）才改用它跑 chart route**，
否則全部 route 用 27B。結果無論輸贏都要寫進 report。freeze 後不得再比較模型。

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

## 執行順序（不可調換）

1. 資料 ＋ manifest hash 驗證
2. gold 標註（DEV 15 / LOCKED 36 / probes 5 / DEV chart challenger 16）
3. **只在 DEV 上**開發調參
4. Chart challenger（一次性，依事前規則決定 chart route 模型）
5. `scripts/pin_models.py` → `configs/models.lock.json`
6. `scripts/check_leakage.py`
7. `scripts/freeze_protocol.py` → `results/feasibility/protocol_lock.json`
8. 跑 F0…F7 於 LOCKED（cold ＋ warm）
9. `scripts/verify_results.py` → `scripts/run_gate.py` → `GO_NO_GO.json`
10. `docs/FEASIBILITY_REPORT.md`

第 8 步後改 `src/` ⇒ **必須重跑全部 F0…F7**，不可只重跑有利的 config。

## Gate 摘要（G1–G9 hard、G10 soft）

G1 資料可重現｜**G2 合併 hard set（21 題）改善 ≥10pp ＋ 至少一單類 ≥10pp（兩者都要）**｜
G3 overall 不退步 >5pp｜G4 citation validity ≥90%｜G5 numeric route ≥90%｜
G6 route accuracy ≥85%｜G7 over-answer ≤25% 且 refusal precision ≥80%｜
G8 no-evidence probe 5 題中 ≥4 拒答｜G9 結果可由 raw artifacts 重建｜
G10 retrieval p95 ≤3s、generation p95 ≤60s、VRAM ≤22GB

**GO** = G1–G9 全過 ＋ G10 過｜**CONDITIONAL_GO** = G1–G9 全過但 G10 不過｜
**NO_GO** = G1–G9 任一不過

判定由 `scripts/run_gate.py` 產生，**人工不得覆寫**。
不是 GO 就不得改題目、不得刪負面結果、不得建產品 UI，
必須在 report 寫出「最小的下一個研究問題」。只有明確 GO 才能進 ⑤B。
