# Frozen Protocol Errata

Protocol 1.0.0 已 freeze，不得改寫。以下只修正公開閱讀方式，不改 protocol lock、gold、
model pins、threshold、run records、metrics 或 `NO_GO`。

## 1. Declared documents: seven versus ten

`docs/FEASIBILITY_PROTOCOL.md` is frozen under Protocol 1.0.0 and must not be edited. Its §1.2 prose still says the study declared seven PDFs.

Before the protocol freeze, decision D-012 expanded the declared document set from seven annual reports to ten filings by adding three FY2024 financial reports. The frozen `data/manifests/documents.yaml`, `twfi.protocol.DECLARED_DOCUMENTS`, acquisition records, locked run and final report all use **10 declared documents, of which 8 are machine-usable**.

This is a frozen-prose inconsistency, not a post-result sample substitution. The protocol lock remains unchanged; this external erratum corrects the public reading without rewriting the frozen artifact.

Authoritative evidence:

- [`results/feasibility/protocol_lock.json`](../results/feasibility/protocol_lock.json)
- [`data/manifests/documents.yaml`](../data/manifests/documents.yaml)
- [`src/twfi/protocol.py`](../src/twfi/protocol.py)
- [`docs/DECISIONS.md` — D-012](DECISIONS.md#d-012-協議修訂新增-3-份-fy2024-財務報告書7--10-份文件)
- [`docs/FEASIBILITY_REPORT.md`](FEASIBILITY_REPORT.md)

## 2. Gold authorship: fully-human shorthand versus disclosed model assistance

Protocol §1.5 的最終 schema 明定 `annotator` 可以是 `human` 或具名的起草模型，candidate
在型別上不能產生 gold；模型協助的題目另以固定規則做人工抽樣或強制稽核，且 report 必須
列出 composition。D-019 在 freeze 前已接受這套規則，frozen gold 與最終報告也依此執行。

但 Protocol §5 execution order 的第 6 步仍殘留簡寫 `annotator=human`。它與同一 frozen
文件較具體的 §1.5、frozen gold metadata、D-019 與最終報告矛盾。正確公開閱讀是：

- gold answer 不得由被測 candidate 產生；
- authorship／model assistance 必須逐題具名；
- audit 狀態與 composition 必須公開；
- 本研究並非 fully-human gold，也不是完全獨立或 blind 的 final audit。

這是 frozen-prose inconsistency，不是事後替換 gold。author、audit、trustworthy 與 composition
欄位均已在唯一 locked run 前凍結，v1.0.2 沒有修改任何 gold record。

Authoritative evidence:

- [`docs/FEASIBILITY_PROTOCOL.md` §1.5](FEASIBILITY_PROTOCOL.md#15-gold-record-schema)
- [`data/evaluation/locked/gold.jsonl`](../data/evaluation/locked/gold.jsonl)
- [`docs/DECISIONS.md` — D-019](DECISIONS.md#d-019-gold-改為模型起草--人工抽樣稽核並把這件事寫在臉上)
- [`docs/FEASIBILITY_REPORT.md` — Gold set composition](FEASIBILITY_REPORT.md#gold-set-組成d-019-要求逐項印出)

## 3. Archived execution-instruction reference

Protocol 1.0.0 refers to `CLAUDE.md` rule 4 when discussing candidate-independent gold
construction. `CLAUDE.md` was an internal execution file, archived in immutable release v1.0.2
and removed from the v1.0.3 public tree. The operative rule is unchanged: candidate outputs must
not create or revise locked gold answers.

No protocol, gold, threshold, run record, metric, or verdict was modified by this
publication-only cleanup.
