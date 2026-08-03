# TW Filing Intelligence

[![CI](https://github.com/kuotunyu/tw-filing-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/kuotunyu/tw-filing-intelligence/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/kuotunyu/tw-filing-intelligence)](https://github.com/kuotunyu/tw-filing-intelligence/releases/latest)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB)](https://www.python.org/)

> **Software / repository release: 1.0.2. Frozen evaluation protocol: 1.0.0.**
> v1.0.2 只修正 release、citation、license 與歷史文件狀態；未重跑 locked evaluation，
> 也未改變 `NO_GO` 結論。

針對臺灣上市公司公開財報打造的 multimodal RAG / VLM 可行性研究。專案以事前註冊的 Protocol 1.0.0，驗證 MOPS PDF、TWSE OpenAPI 與 XBRL 能否支撐可追溯、可拒答、可重現的 filing intelligence 系統。

研究已完整結案，唯一一次 locked evaluation 由程式依預先凍結的 gate 判定為 [`NO_GO`](docs/FEASIBILITY_REPORT.md)。Protocol 1.0.0 的候選系統沒有通過；評估機制成功辨識了它。這個負結果不是未完成，也不能用事後挑選較高分的 ablation rung 取代。

本 repository **不是投資建議，也不是 production system**。它是一份可重算、可接受失敗的 research feasibility study。

## 研究規模

| 項目 | 規模 |
|---|---:|
| 公司與產業 | 5 家公司、4 個產業 |
| 文件範圍 | FY2023–FY2024，宣告 10 份、機器可用 8 份（見 [erratum](docs/ERRATA.md)） |
| Evaluation records | 53 筆：DEV 15、LOCKED 33、獨立 no-evidence probes 5 |
| Factor ladder | F0–F7，共 8 組受控實驗 |
| 品質驗證 | clean clone 1,647 passed／1 expected skip、coverage 94.11%、strict mypy、Ruff |

兩份不可用文件均為真實資料品質問題：PDF 缺少可用的 ToUnicode mapping。它們被保留在 manifest 與 provenance 中，沒有為了提高結果而替換樣本。

Clean clone 的唯一 skip 是原始 acquisition artifacts 未隨 repository 重新散布；在本機已取得這些 artifacts 的完整資料環境中，同一 suite 為 1,648 passed。兩種環境都不呼叫模型 API，也不需要 GPU。

正式 answer accuracy 的 denominator 是 **LOCKED 33**。5 筆 no-evidence probes 只評估 retrieval 被清空時是否拒答，不計入 33 題 accuracy。Gold annotation 不是全部 fully human：部分題目或答案曾由模型協助選擇／起草，實際 authorship、audit rate 與 `trustworthy` 數量完整列在[最終報告](docs/FEASIBILITY_REPORT.md#gold-set-%E7%B5%84%E6%88%90d-019-%E8%A6%81%E6%B1%82%E9%80%90%E9%A0%85%E5%8D%B0%E5%87%BA)。

## 系統設計

### PDF 如何變成可查詢證據

這張圖只回答一件事：一份 PDF 進入專案後，哪些內容會進 search index、DuckDB 或 VLM。

```mermaid
flowchart TD
    PDF["MOPS PDF"] --> Record["記錄來源與檔案指紋<br/>官方 URL / file size / SHA-256"]
    Record --> Readable{"PDF 文字層可解析？"}
    Readable -->|"否"| Keep["保留失敗紀錄與 SHA-256"]
    Keep --> Exclude["不作為評估題目的<br/>答案證據來源"]
    Readable -->|"是"| Parse["解析版面、表格與圖像位置"]
    Parse --> Chunks["段落與章節 chunks"]
    Chunks --> Index[("BM25 + dense index")]
    Parse --> Rows["可分類的表格／文字 rows"]
    Rows --> Validate["驗證年度、單位、合併或個別、來源"]
    Validate --> DB[("DuckDB")]
    Parse --> Figures["Figure crops"]
    Figures --> Caption["模型產生的圖片描述<br/>caption 只協助找頁"]
    Caption --> Index
    Figures --> Pixels["Original crop pixels<br/>交給 VLM 讀值"]

    classDef source fill:#DDEBFF,stroke:#245A9A,stroke-width:2px,color:#102A43
    classDef process fill:#E3F9E5,stroke:#2F855A,stroke-width:2px,color:#173F2A
    classDef decision fill:#FFF3BF,stroke:#B7791F,stroke-width:2px,color:#4A2C0A
    classDef store fill:#E9D8FD,stroke:#6B46C1,stroke-width:2px,color:#2D1B4E
    classDef excluded fill:#FDE2E2,stroke:#C53030,stroke-width:2px,color:#4A1717

    class PDF source
    class Record,Parse,Chunks,Rows,Validate,Figures,Caption,Pixels process
    class Readable decision
    class Index,DB store
    class Keep,Exclude excluded
```

### 本次實驗的歷史數值來自哪裡

本次 locked run 沒有用 OpenAPI 或 XBRL 補齊 FY2023–FY2024 歷史數值；numeric route 查的是專案從 filing line stream 重建的 rows。

```mermaid
flowchart TD
    Question["本次 locked numeric route<br/>實際查哪一份歷史資料？"]

    Question -->|"實際使用"| Filing["MOPS filing line stream"]
    Filing --> Rebuild["重建可分類的 FY2023–FY2024 rows"]
    Rebuild --> Broad[("numeric_broad.duckdb")]
    Broad --> Used["本次 locked numeric route 使用"]

    Question -->|"未使用"| OpenAPI["TWSE OpenAPI"]
    OpenAPI --> Snapshot["只有 FY2026Q1 snapshot"]
    Snapshot --> NoOverlap["與 FY2023–FY2024 無交集<br/>未進 locked store"]

    Question -->|"未取得"| XBRL["XBRL"]
    XBRL --> Missing["本輪未取得<br/>未進 locked store"]

    classDef question fill:#FFF3BF,stroke:#B7791F,stroke-width:2px,color:#4A2C0A
    classDef used fill:#E3F9E5,stroke:#2F855A,stroke-width:2px,color:#173F2A
    classDef store fill:#E9D8FD,stroke:#6B46C1,stroke-width:2px,color:#2D1B4E
    classDef unused fill:#FDE2E2,stroke:#C53030,stroke-width:2px,color:#4A1717

    class Question question
    class Filing,Rebuild,Used used
    class Broad store
    class OpenAPI,Snapshot,NoOverlap,XBRL,Missing unused
```

因此本輪只能稱為「已驗證結構化資料」，不能稱為「官方結構化歷史資料」。每筆 row 仍保留 `source_kind` 與 `source_ref`，可追回原始文件位置。

### F7 如何選路徑並產生答案

F7 先限制到指定公司與文件，再由 deterministic rules 做一次 single-pass typed dispatch。這不是 Agent workflow，也沒有循環規劃。Numeric 與 visual route 在缺少可用資料時可以局部拒答；narrative route 也允許模型依 prompt 拒答，但本次 runtime **沒有**在輸出後強制執行 citation-validity gate。

```mermaid
flowchart TD
    Query(["問題"]) --> Scope["限定公司與可用文件"]
    Scope --> Router["single-pass rule router<br/>輸出 route / reason / confidence"]

    Router -->|"narrative / cross-modal"| Narrative["找出相關文字"]
    Narrative --> Rerank["依相關性重新排序"]
    Rerank --> Generate["共用 answer prompt"]

    Router -->|"numeric"| Numeric["查 DuckDB"]
    Numeric --> SQL["使用固定 SQL template<br/>不讓 LLM 自由寫 SQL"]
    SQL --> NumericResult{"row / template 可用？"}
    NumericResult -->|"是"| Calc["程式計算<br/>formula + operands"]
    NumericResult -->|"否"| RouteRefusal["route-level refusal"]

    Router -->|"chart / table"| Visual["caption 協助找到 visual region"]
    Visual --> Crop{"original crop 可讀？"}
    Crop -->|"是"| VLM["VLM 從 crop pixels 讀值"]
    Crop -->|"否"| RouteRefusal

    Generate --> Output["保存 answer / cited indices / telemetry"]
    Calc --> Output
    VLM --> Output
    RouteRefusal --> Output

    classDef input fill:#DDEBFF,stroke:#245A9A,stroke-width:2px,color:#102A43
    classDef control fill:#FFF3BF,stroke:#B7791F,stroke-width:2px,color:#4A2C0A
    classDef route fill:#E3F9E5,stroke:#2F855A,stroke-width:2px,color:#173F2A
    classDef output fill:#D6E4FF,stroke:#364FC7,stroke-width:2px,color:#172B4D
    classDef refusal fill:#FDE2E2,stroke:#C53030,stroke-width:2px,color:#4A1717

    class Query input
    class Scope,Router,NumericResult,Crop control
    class Narrative,Rerank,Generate,Numeric,SQL,Calc,Visual,VLM route
    class Output output
    class RouteRefusal refusal
```

### Offline evaluation 如何判定結果

Citation、answer、route、refusal 與 GO / NO-GO 都在 locked output 產生後離線評分，不是 runtime 保證。

```mermaid
flowchart TD
    Records[("F0–F7 raw records")] --> AnswerGrade["answer grader"]
    Gold[("frozen gold / probes")] --> AnswerGrade
    Records --> CitationGrade["citation grader"]
    Evidence[("acquired evidence metadata")] --> CitationGrade
    Records --> RouteGrade["route + refusal metrics"]
    Gold --> RouteGrade

    AnswerGrade --> Summary["summary.json"]
    CitationGrade --> Summary
    RouteGrade --> Summary
    Summary --> Verify["verify_results.py<br/>從 records 重算"]
    Verify --> Gates["G1–G10<br/>frozen thresholds"]
    Gates --> Verdict["NO_GO"]

    classDef artifact fill:#E9D8FD,stroke:#6B46C1,stroke-width:2px,color:#2D1B4E
    classDef process fill:#E3F9E5,stroke:#2F855A,stroke-width:2px,color:#173F2A
    classDef result fill:#D6E4FF,stroke:#364FC7,stroke-width:3px,color:#172B4D

    class Records,Gold,Evidence,Summary artifact
    class AnswerGrade,CitationGrade,RouteGrade,Verify,Gates process
    class Verdict result
```

關鍵邊界：

- Numeric route 不允許 LLM 自由生成 SQL。
- Caption 只協助 retrieval；visual-region 數值來源是 original crop pixels。
- F7 實作是 bounded single-pass dispatch，沒有 Agent loop。
- Citation invalid 會讓 offline citation gate 失敗，但不保證 runtime 自動把答案改成拒答。

## 事前註冊實驗

評分規則、tolerance、GO / NO-GO gates 與七個關鍵 artifact hash 在 locked run 前完成凍結。執行後不改題目、不調門檻、不挑結果。

### 事前註冊如何防止看到結果後再調整

只有 DEV 階段可以調整設定；protocol freeze 之後，只能執行 locked evaluation、從 raw records 重算，並依事先固定的 gates 判定。

```mermaid
flowchart TD
    Dev["只在 DEV 階段調整<br/>題目、models、tolerance"] --> Register["先固定 F0–F7<br/>與 G1–G10 gates"]
    Register --> Freeze["freeze_protocol.py<br/>寫入 7 個 artifact hashes"]
    Freeze --> Rule["從此不得修改<br/>locked set / thresholds / models"]
    Rule --> Eval["唯一一次 LOCKED evaluation<br/>執行 F0–F7"]
    Eval --> Recompute["verify_results.py<br/>從 raw records 重算結果"]
    Recompute --> Verified{"重算結果一致？"}
    Verified -->|"否"| Stop["停止發布<br/>結果不可採信"]
    Verified -->|"是"| Gate["run_gate.py<br/>依凍結的 G1–G10 判定"]
    Gate --> Decision{"依規則自動判定"}
    Decision -->|"全部 hard gates 通過"| Go["GO"]
    Decision -->|"只有 soft gate 未通過"| Conditional["CONDITIONAL_GO"]
    Decision -->|"任一 hard gate 未通過"| NoGo["NO_GO<br/>本次結果"]
    NoGo --> Result["F0 17/33<br/>F7 6/33<br/>hard gain -27.8pp"]

    classDef dev fill:#DDEBFF,stroke:#245A9A,stroke-width:2px,color:#102A43
    classDef frozen fill:#FFF3BF,stroke:#B7791F,stroke-width:2px,color:#4A2C0A
    classDef process fill:#E3F9E5,stroke:#2F855A,stroke-width:2px,color:#173F2A
    classDef decision fill:#E9D8FD,stroke:#6B46C1,stroke-width:2px,color:#2D1B4E
    classDef invalid fill:#FDE2E2,stroke:#C53030,stroke-width:2px,color:#4A1717
    classDef outcome fill:#C6F6D5,stroke:#2F855A,stroke-width:2px,color:#173F2A
    classDef actual fill:#D6E4FF,stroke:#364FC7,stroke-width:3px,color:#172B4D

    class Dev,Register dev
    class Freeze,Rule frozen
    class Eval,Recompute,Gate process
    class Verified,Decision decision
    class Stop invalid
    class Go,Conditional outcome
    class NoGo,Result actual
```

| Factor | Locked accuracy | 主要變因 |
|---|---:|---|
| F0 | 51.5%（17/33） | baseline |
| F1 | 45.5%（15/33） | layout-aware parsing |
| F2 | 42.4%（14/33） | hybrid retrieval |
| F3 | 57.6%（19/33） | cross-encoder reranking |
| F4 | 57.6%（19/33） | structured numeric route |
| F5 | 60.6%（20/33） | visual-region caption indexing |
| F6 | 54.5%（18/33） | original crop evidence / crop VLM |
| F7 | 18.2%（6/33） | **事前指定的 full candidate：typed dispatch** |

最終 candidate 相對 baseline 的 hard-category pooled gain 為 **-27.8pp**。G1、G8、G9、G10 通過；G2–G7 未通過，因此結論為 `NO_GO`。

F5／F6 在這份語料中處理的 503 個 visual-region candidates 絕大多數是有框表格；真正的 chart questions 只有兩題，且都來自台積電。因此不得把這兩階描述為一般化 chart-reading 能力。F5 的 20/33 是 locked ablation observation，不是正式 candidate，也不能在看到結果後改稱勝出的系統。

關鍵失敗訊號：

- citation validity：52.9%（9/17）
- numeric route accuracy：0%（0/12）
- route accuracy：33.3%（11/33）
- retrieval p95：0.149 秒
- generation p95：2.957 秒
- peak VRAM：20.09 GB

負面結果不是未完成。這份研究完成了 protocol freeze、資料 provenance、locked evaluation、raw-artifact 重算與機械式 gate decision，並留下下一輪 Protocol 2.x 可直接驗證的 failure decomposition。

## 證據導航

| 公開 claim | Raw／frozen evidence | 離線重算 | Human-readable report |
|---|---|---|---|
| Protocol 已凍結 | [`protocol_lock.json`](results/feasibility/protocol_lock.json) | `uv run pytest tests/test_protocol_lock.py::test_real_protocol_lock_still_holds -q -p no:cacheprovider -o addopts=` | [Protocol 1.0.0](docs/FEASIBILITY_PROTOCOL.md)、[erratum](docs/ERRATA.md) |
| F0 17/33 | [`F0/records.jsonl`](results/runs/F0/records.jsonl) | `uv run python scripts/verify_results.py --dry-run` | [Final report](docs/FEASIBILITY_REPORT.md#主要比較) |
| F7 6/33 | [`F7/records.jsonl`](results/runs/F7/records.jsonl) | `uv run python scripts/verify_results.py --dry-run` | [Final report](docs/FEASIBILITY_REPORT.md#主要比較) |
| Citation 9/17 | [`F7/records.jsonl`](results/runs/F7/records.jsonl) | `uv run python scripts/verify_results.py --dry-run` | [Candidate gate proportions](docs/FEASIBILITY_REPORT.md#candidate-gate-proportions) |
| Numeric 0/12 | [`F7/records.jsonl`](results/runs/F7/records.jsonl) | `uv run python scripts/verify_results.py --dry-run` | [Candidate gate proportions](docs/FEASIBILITY_REPORT.md#candidate-gate-proportions) |
| Route 11/33 | [`F7/records.jsonl`](results/runs/F7/records.jsonl) | `uv run python scripts/verify_results.py --dry-run` | [`error_analysis.jsonl`](results/feasibility/error_analysis.jsonl) |
| `NO_GO` | [`GO_NO_GO.json`](results/feasibility/GO_NO_GO.json) | `uv run python scripts/verify_evidence.py` | [Final report](docs/FEASIBILITY_REPORT.md#判定no_go) |
| No leakage detected | [`dev`](data/evaluation/dev/gold.jsonl)、[`locked`](data/evaluation/locked/gold.jsonl)、[`probes`](data/evaluation/locked/probes.jsonl) | `uv run python scripts/check_leakage.py` | [Protocol split](docs/FEASIBILITY_PROTOCOL.md#13-兩個評估集) |

## 重現方式

需求：Python 3.13、[uv](https://docs.astral.sh/uv/)。離線測試與資料驗證只使用 CPU；index build 與 generation 才需要 GPU，目標環境為 RTX 4090 24GB。

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src scripts
uv run python scripts/verify_results.py --dry-run
uv run python scripts/verify_evidence.py
uv run python scripts/check_leakage.py
```

重新取得公開資料：

```bash
uv run python scripts/fetch_twse_openapi.py --manifest data/manifests/structured.yaml
uv run python scripts/fetch_documents.py --manifest data/manifests/documents.yaml
uv run python scripts/verify_manifests.py
```

Repository 不重新散布原始年報或財報 PDF，只提交 manifest、官方來源 URL、SHA-256 與重建腳本。

## 研究文件

| 文件 | 內容 |
|---|---|
| [最終報告](docs/FEASIBILITY_REPORT.md) | 唯一 locked run、完整指標、限制與 `NO_GO` 判定 |
| [評估協議](docs/FEASIBILITY_PROTOCOL.md) | 事前凍結的 Protocol 1.0.0 與 gates |
| [Frozen protocol errata](docs/ERRATA.md) | 不修改 lock，說明 7 → 10 份文件與 gold authorship shorthand 的 frozen prose inconsistencies |
| [Changelog](CHANGELOG.md) | Software 1.0.1 的 presentation / evidence-verification closeout 範圍 |
| [資料 provenance](docs/DATA_PROVENANCE.md) | 官方來源、取得方式、授權與 SHA-256 |
| [威脅模型](docs/THREAT_MODEL.md) | prompt injection、SSRF、rate limit、leakage、secrets |
| [Citation metadata](CITATION.cff) | v1.0.2 的 machine-readable software citation |
| [Third-party notice](NOTICE.md) | 程式碼授權與 TWSE／MOPS／發行人資料權利的邊界 |

主要結果位於 `results/feasibility/`，包含 protocol lock、F0–F7 summary、逐題 error analysis、重算驗證與最終 gate decision。

## 使用範圍

本專案**不是投資建議**，所有輸出僅為文件檢索與資訊擷取的技術驗證，不構成證券或金融商品的推薦、要約或決策依據。任何財務數字均應回到[公開資訊觀測站](https://mops.twse.com.tw/)原始文件核對。

本專案**不是 production 系統**，不提供 SLA、認證授權、多租戶隔離或安全稽核，不應直接用於實際決策流程。

程式碼採 [MIT License](LICENSE)。授權不涵蓋 TWSE / MOPS 原始文件與資料；完整邊界見 [Third-Party Data and Use Notice](NOTICE.md)。
