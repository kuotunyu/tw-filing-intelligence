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

## Normalization（scoring 前必套，freeze 後不可改）

全角→半角｜去千分位｜`億=1e8`/`萬=1e4`/`千元=1e3`｜括號負數 `(1,234)→-1234`｜
`12.3%` ≠ `0.123`（以 gold `unit` 為準）｜幣別正規化為 `TWD`｜
民國年↔西元年（`112年`↔`2023`）｜數值題用數值比較

## 執行順序（不可調換）

1. 資料 ＋ manifest hash 驗證
2. gold 標註（DEV 15 / LOCKED 36 / probes 5）
3. **只在 DEV 上**開發調參
4. `scripts/check_leakage.py`
5. `scripts/freeze_protocol.py` → `results/feasibility/protocol_lock.json`
6. 跑 F0…F7 於 LOCKED（cold ＋ warm）
7. `scripts/verify_results.py` → `scripts/run_gate.py` → `GO_NO_GO.json`
8. `docs/FEASIBILITY_REPORT.md`

第 6 步後改 `src/` ⇒ **必須重跑全部 F0…F7**，不可只重跑有利的 config。

## Gate 摘要（G1–G9 hard、G10 soft）

G1 資料可重現｜G2 至少一個 hard category 相對 F0 改善 ≥10pp｜
G3 overall 不退步 >5pp｜G4 citation validity ≥90%｜G5 numeric route ≥90%｜
G6 route accuracy ≥85%｜G7 over-answer ≤25% 且 refusal precision ≥80%｜
G8 no-evidence probe 5 題中 ≥4 拒答｜G9 結果可由 raw artifacts 重建｜
G10 retrieval p95 ≤3s、generation p95 ≤60s、VRAM ≤22GB

**GO** = G1–G9 全過 ＋ G10 過｜**CONDITIONAL_GO** = G1–G9 全過但 G10 不過｜
**NO_GO** = G1–G9 任一不過

判定由 `scripts/run_gate.py` 產生，**人工不得覆寫**。
不是 GO 就不得改題目、不得刪負面結果、不得建產品 UI，
必須在 report 寫出「最小的下一個研究問題」。只有明確 GO 才能進 ⑤B。
