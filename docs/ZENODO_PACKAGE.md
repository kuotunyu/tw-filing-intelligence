# Zenodo Research Package v1.0.5

`status: PUBLISHED — 2026-08-10`

- Version DOI：[`10.5281/zenodo.21874274`](https://doi.org/10.5281/zenodo.21874274)
- All-versions DOI：[`10.5281/zenodo.21874273`](https://doi.org/10.5281/zenodo.21874273)
- GitHub Release：[`v1.0.5`](https://github.com/kuotunyu/tw-filing-intelligence/releases/tag/v1.0.5)
- Release source commit：`e37739f3b76bac0301b7ed72c7e8cc4fe3f77fa2`

本 repository 適合發布為 **research software + evaluation evidence**，不適合描述為完整
benchmark dataset、production filing assistant，或從官方 source 到 model output 的
end-to-end reproducibility package。

## 已發布檔案

| 檔案 | 大小 | SHA-256 |
|---|---:|---|
| `tw-filing-intelligence-v1.0.5.zip` | 817,775 bytes | `bba7655082a49680d8bc6b33484aa783036b57c7a0166824a97f41f731de8317` |
| `tw-filing-intelligence-v1.0.5_FILE_INVENTORY.tsv` | 21,597 bytes | `1c83f15a7a76a49b0a0c5321a642cecfac702d50253a093ca4c5ffd7ac97e6bc` |
| `tw-filing-intelligence-v1.0.5_SHA256SUMS.txt` | 101 bytes | `594f077749725f99de959eb4be0cfc7f744d960cd93115df5e12f8ee2460447d` |

### Archive 內容

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

## 發布驗收紀錄

依凍結規則，在下列驗收全部通過前，Zenodo package **不可發布**；本次 v1.0.5 已逐項完成。

發布前已完成下列驗收：

1. review release diff，`v1.0.5` annotated tag 精確指向 release source commit。
2. Zenodo metadata 與 `CITATION.cff` 的 version、date、creator、URL、license 一致。
3. 最終 archive、inventory 與 checksums 均已產生；未包含上述排除項、secret、PII 或絕對路徑。
4. archive 解壓後的 clean directory 已通過下方 acceptance commands。

Clean archive 驗證結果為 **1,658 passed、1 skipped、coverage 93.69%**；ruff、format、mypy
與四項 offline evidence verifier 皆通過。GitHub Release 已設為 immutable。

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

## Zenodo owner 確認（已完成）

1. Zenodo creator 僅列 `kuotunyu`；未虛構或補填 ORCID。
2. Draft 預覽中的三個檔案、MIT、CC BY 4.0 與 third-party exclusions 已核對。
3. Owner 於 2026-08-10 確認不可逆限制後，明確授權執行 Publish。

公開紀錄已以匿名 HTTP 請求驗證可存取，version DOI 與 all-versions DOI 均已註冊。
