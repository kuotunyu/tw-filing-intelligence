# Zenodo Research Package Plan

`status: RELEASE CANDIDATE — v1.0.5 metadata 已凍結；Zenodo 尚未發布`

本 repository 適合發布為 **research software + evaluation evidence**，不適合描述為完整
benchmark dataset、production filing assistant，或從官方 source 到 model output 的
end-to-end reproducibility package。

## 建議收錄

1. release source archive 與 `uv.lock`，並註明 locked run code commit
   `595268f3a64ee9430efc397140c2f600c925436b`。
2. Frozen Protocol bundle：`docs/FEASIBILITY_PROTOCOL.md`、
   `results/feasibility/protocol_lock.json`、frozen manifests、gold 與 probes。
3. Locked evidence：`results/runs/F0`–`F7`、`results/runs/probes`、resource records 與
   `locked_run_started.json`。
4. Derived evidence：`summary.json`、`GO_NO_GO.json`、`results_verification.json`、
   `error_analysis.jsonl`、`analysis_audit.json`、final report 與 analysis supplement。
5. Offline verifiers、tests、CI definition、`README.md`、`CITATION.cff`、`LICENSE`、
   `CONTENT_LICENSE.md`、`NOTICE.md`。

Archive 應另產生 file inventory 與 SHA-256 checksum；不要把目前 working tree 或本機 cache
直接壓縮上傳。

## 必須排除

- MOPS／TWSE／issuer 的 third-party raw PDF、OpenAPI snapshot、XBRL 與 HTML body
- rendered pages、crops、captions、chunks、vectors、DuckDB、model weights
- `.env*`、key、credential、log、cache、`.venv`、`.worktrees`
- 私人 `interview.md`、Spec、clipboard、Codex／Claude 工作檔與任何絕對路徑
- 授權狀態不明、未列入 manifest 的外部文件

這些排除項不能為了提高「可重現」觀感而偷偷加入；raw source 缺失必須保留為明示限制。

## 授權矩陣

| 內容 | 目前狀態 | Zenodo 動作 |
|---|---|---|
| repository source code | MIT | 可依 `LICENSE` 發布 |
| 作者自有的 docs、gold metadata、run／analysis artifacts | CC BY 4.0 | 依 `CONTENT_LICENSE.md` 發布並保留 attribution |
| MOPS／TWSE／issuer source material | 不屬於本專案授權 | 不上傳；只留 URL、metadata、SHA-256 |
| model weights／第三方套件 | 各自授權 | 不重新散布；只列名稱、revision／digest 與環境 |

CC BY 4.0 只涵蓋 owner 有權授權的原創內容；內嵌的第三方 source excerpts、公開財報與
dataset 不因此被重新授權。`CITATION.cff` 的 MIT 欄位描述 software；mixed-license archive
必須在 Zenodo description 同時列出 MIT、CC BY 4.0 與 third-party exclusions。

## 發布前驗收條件

在下列項目完成前，Zenodo package **不可發布**：

1. review release diff，建立正式 release commit；後續 tag 必須精確指向該 clean commit。
2. Zenodo metadata 與 `CITATION.cff` 的 version、date、creator、URL、license 一致。
3. 產生最終 archive inventory／checksums，確認沒有上述排除項、secret、PII 或絕對路徑。
4. 在 archive 解壓後的 clean directory 執行下方 acceptance commands。

`CITATION.cff` 已標示 `1.0.5`、正式 release date 與預定 immutable release URL；只有在
本清單全部通過後，才可建立對應 tag／GitHub Release 與 Zenodo draft。

四筆 pending gold review 不是禁止發布負結果的必要條件，但 metadata 與摘要必須揭露
29/33 trustworthy、final audit 非 independent blind；若要宣稱完成獨立 audit，則必須由
另一位 reviewer 新增 post-hoc audit artifact，不能改 frozen gold。

## Acceptance commands

```bash
uv sync --extra dev --frozen
uv run python scripts/verify_results.py --dry-run
uv run python scripts/verify_evidence.py
uv run python scripts/verify_analysis_audit.py
uv run python scripts/check_leakage.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src scripts
```

`verify_manifests.py --require-all` 只有在使用者另外取得 manifest 所列的 bit-identical
third-party raw files 後才應通過；它不是 public archive 的 clean-clone acceptance command。

## Zenodo 最終 owner 確認

1. 確認 Zenodo creator 顯示名稱／ORCID（如有）。
2. 檢查 draft 預覽中的檔案、授權與 third-party exclusions。
3. 親自確認 Zenodo 條款並執行不可逆的 Publish。

GitHub PR 不等於 Zenodo 發布授權；可以先建立與上傳 draft，但在 owner 實際確認 deposit
預覽前，不 reserve DOI 或 publish，也不替 owner 接受 Zenodo 法律條款。
