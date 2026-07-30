# FEASIBILITY REPORT

`status: NOT YET RUN`
`last_updated: 2026-07-31`

> 這份報告在 **P10** 產生。現在**沒有任何評估結果**，因此本檔案不含任何數字。
> 刻意不放佔位數字，避免被誤讀為已有結論。

## 為什麼現在是空的

依 `docs/FEASIBILITY_PROTOCOL.md §5` 的執行順序，報告是最後一步：

1. 資料取得 ＋ manifest SHA-256 驗證
2. Gold set 標註（DEV 15 / LOCKED 36 / 5 probes）
3. 只在 DEV 上開發與調參
4. `scripts/check_leakage.py`
5. `scripts/freeze_protocol.py`（凍結協議）
6. 跑 F0…F7 於 LOCKED（cold ＋ warm）
7. `scripts/verify_results.py` → `scripts/run_gate.py`
8. **本報告**

目前進度見 `docs/PROGRESS.md`。

## 產生後必須包含的內容（結構已定，不得省略對自己不利的段落）

- 資料來源與 provenance（含 SHA-256 與取得日期）
- 文件數與題目數（依題型分布）
- Baseline (F0) 設定與結果
- Candidate (F7) 設定與結果
- **Factor-at-a-time 增益歸因**：Δ(F1)…Δ(F7)，說明增益來自哪個 factor
- 各類指標：retrieval / answer / citation / routing / systems
- latency（p50/p95，cold vs warm）／VRAM peak／tokens／cost（全 local ⇒ 0.0）
- **Failure analysis**：逐類失敗成因，含 `template_miss`、citation invalid、
  route 混淆、拒答失敗等
- **負面結果**（不得刪除、不得美化）
- `GO / CONDITIONAL_GO / NO_GO` 決策與每個 gate 的判斷理由
- 若非 GO：**最小的下一個研究問題**
- 本輪明確未驗證的限制（例如：未驗證 learned layout model、未涵蓋上櫃／興櫃、
  未涵蓋非中文文件）

## 免責

本報告不是投資建議。本專案不是 production 系統。
所有財務數字以公開資訊觀測站原始文件為準。
