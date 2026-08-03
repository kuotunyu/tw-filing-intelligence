# Frozen Protocol Erratum

`docs/FEASIBILITY_PROTOCOL.md` is frozen under Protocol 1.0.0 and must not be edited. Its §1.2 prose still says the study declared seven PDFs.

Before the protocol freeze, decision D-012 expanded the declared document set from seven annual reports to ten filings by adding three FY2024 financial reports. The frozen `data/manifests/documents.yaml`, `twfi.protocol.DECLARED_DOCUMENTS`, acquisition records, locked run and final report all use **10 declared documents, of which 8 are machine-usable**.

This is a frozen-prose inconsistency, not a post-result sample substitution. The protocol lock remains unchanged; this external erratum corrects the public reading without rewriting the frozen artifact.

Authoritative evidence:

- [`results/feasibility/protocol_lock.json`](../results/feasibility/protocol_lock.json)
- [`data/manifests/documents.yaml`](../data/manifests/documents.yaml)
- [`src/twfi/protocol.py`](../src/twfi/protocol.py)
- [`docs/DECISIONS.md` — D-012](DECISIONS.md#d-012-協議修訂新增-3-份-fy2024-財務報告書7--10-份文件)
- [`docs/FEASIBILITY_REPORT.md`](FEASIBILITY_REPORT.md)
