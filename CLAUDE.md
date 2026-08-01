# CLAUDE.md — TW Filing Intelligence (⑤A Feasibility Study)

## 這是什麼

一份**事前註冊的可行性驗證**，不是產品。研究問題、資料、指標與 GO／NO-GO 門檻
都寫在 `docs/`，先凍結才允許跑 locked evaluation。

## 每次進來先做的三件事

1. 讀 `docs/PROGRESS.md` → 現在在哪個 phase、下一步是什麼、有什麼已知風險。
2. 讀 `docs/IMPLEMENTATION_PLAN.md` → 該 phase 的完成條件。
3. `git log --oneline -10` + `git status` → 確認工作區狀態。

## 不可違反的規則（violation 就是任務失敗）

1. **專案獨立**：不 import／複製其他本機 repository，不 submodule，不 local path
   dependency，不 symlink，不共用 DB／cache／evaluation artifacts。
2. **Locked set 神聖**：`docs/FEASIBILITY_PROTOCOL.md` 與
   `data/evaluation/locked/` 一旦 freeze（`results/feasibility/protocol_lock.json`
   有 hash），**不得**因為結果不好而修改題目、答案、tolerance、threshold 或模型。
3. **負面結果保留**：NO_GO / CONDITIONAL_GO 一律寫進報告，不刪不美化。
4. **Gold answer 不得由 candidate 產生**。人工標註，來源必須指回原始文件。
5. **測試離線**：`pytest` 不得連 MOPS／TWSE／HF／ollama，不讀 `.env`，不需要 GPU，
   不寫入 `results/feasibility/`。
6. **不繞過網站限制**：不解 CAPTCHA、不高頻爬 MOPS、不用逆向出的私人 endpoint。
   下載失敗就走人工放置 fallback。
7. **大檔不進 git**：PDF、模型權重、index、cache、DuckDB 檔都不 commit。
8. **GPU 禮讓**：跑 GPU 前先 `nvidia-smi`；若別的專案（如 SafeSynth）正在用，
   先做 CPU／資料／測試工作。
9. **Commit 署名只有 kuotunyu**：作者一律
   `kuotunyu <61350295+kuotunyu@users.noreply.github.com>`，
   **不得加 `Co-authored-by:` trailer**（那會讓別人出現在 GitHub Contributors）。
   可 push 到 `origin`（`kuotunyu/tw-filing-intelligence`，public）；
   **不 tag、不發 release、不 deploy**。push 前先跑 `git status` 確認沒有 PDF／
   大檔／`.env` 被夾帶進去。
10. **README／任何 UI 文案**都必須寫明「不是投資建議、不是 production 系統」。

## 常用指令

```bash
uv sync --extra dev
uv run pytest                       # 離線測試, coverage gate 85%
uv run ruff check . ; uv run mypy src
uv run python scripts/verify_manifests.py     # SHA-256 / provenance
uv run python scripts/check_leakage.py        # dev vs locked 洩漏檢查
uv run python scripts/freeze_protocol.py --dry-run   # 看會凍結什麼，不寫檔
uv run python scripts/freeze_protocol.py      # 凍結 protocol + locked set（不可逆，需確認旗標）
uv run python scripts/verify_results.py       # summary 與 raw artifacts 一致性
```

索引（兩個半邊要一起重建，順序固定）：

```bash
uv run python scripts/build_index.py --device cpu   # 向量 + chunks.jsonl（cuda 需先看 nvidia-smi）
uv run python scripts/build_bm25.py                 # BM25，必須在 build_index 之後
uv run python scripts/eval_retrieval.py --set dev   # recall（dev 是唯一可據以調整的集合）
```

## 目錄語意

- `src/twfi/` — 全部程式碼（`io / parsing / index / numeric / chart / router / answer / eval / telemetry`）
- `data/manifests/` — 資料來源宣告（URL、SHA-256、provenance）✅ commit
- `data/evaluation/dev/` — development set（可改）✅ commit
- `data/evaluation/locked/` — locked feasibility set（freeze 後不可改）✅ commit
- `data/raw/` — 下載的原始 PDF ❌ 不 commit
- `results/feasibility/` — summary / error_analysis / GO_NO_GO ✅ commit
- `configs/` — baseline、candidate 與 factor ladder 設定
