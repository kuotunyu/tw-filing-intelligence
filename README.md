# TW Filing Intelligence

[![CI](https://github.com/kuotunyu/tw-filing-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/kuotunyu/tw-filing-intelligence/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/kuotunyu/tw-filing-intelligence)](https://github.com/kuotunyu/tw-filing-intelligence/releases/tag/v1.0.0)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB)](https://www.python.org/)

針對臺灣上市公司公開財報打造的 multimodal RAG / VLM 可行性研究。專案以事前註冊的 Protocol 1.0.0，驗證 MOPS PDF、TWSE OpenAPI 與 XBRL 能否支撐可追溯、可拒答、可重現的 filing intelligence 系統。

研究已完整結案，唯一一次 locked evaluation 由程式依預先凍結的 gate 判定為 [`NO_GO`](docs/FEASIBILITY_REPORT.md)。候選系統沒有通過；評估機制成功辨識了它。

## 研究規模

| 項目 | 規模 |
|---|---:|
| 公司與產業 | 5 家公司、4 個產業 |
| 文件範圍 | FY2023–FY2024，宣告 10 份、機器可用 8 份 |
| Gold set | 53 筆：DEV 15、LOCKED 33、no-evidence probes 5 |
| Factor ladder | F0–F7，共 8 組受控實驗 |
| 品質驗證 | 1,632 tests、94.27% coverage、strict mypy、Ruff |

兩份不可用文件均為真實資料品質問題：PDF 缺少可用的 ToUnicode mapping。它們被保留在 manifest 與 provenance 中，沒有為了提高結果而替換樣本。

## 系統設計

### Offline data preparation

```mermaid
flowchart TD
    subgraph Sources["公開資料來源"]
        direction LR
        PDF["MOPS filings<br/>PDF"]
        Structured["TWSE OpenAPI / XBRL"]
    end

    subgraph Provenance["取得與 provenance"]
        direction LR
        Manifest["Manifest<br/>official URL + metadata"]
        Hash["SHA-256 verification"]
        Usable{"Machine-usable?"}
        Recorded["保留 provenance<br/>不進 evaluation corpus"]
        Schema["Schema validation<br/>row normalization"]
    end

    subgraph Preparation["Document preparation"]
        direction LR
        Layout["Layout-aware parsing"]
        Chunks["Section-aware chunks"]
        Tables["Table extraction"]
        Figures["Figure detection"]
    end

    subgraph Evidence["Evidence stores"]
        direction LR
        Search[("BM25 + dense index<br/>caption 僅供 retrieval")]
        DB[("DuckDB<br/>validated structured rows")]
        Crops[("Original crop pixels")]
    end

    PDF --> Manifest --> Hash --> Usable
    Usable -->|"否"| Recorded
    Usable -->|"是"| Layout
    Layout --> Chunks --> Search
    Layout --> Tables -->|"validated rows"| DB
    Layout --> Figures
    Figures -->|"caption"| Search
    Figures --> Crops
    Structured --> Schema --> DB

    classDef source fill:#DDEBFF,stroke:#245A9A,stroke-width:2px,color:#102A43
    classDef process fill:#E3F9E5,stroke:#2F855A,stroke-width:2px,color:#173F2A
    classDef decision fill:#FFF3BF,stroke:#B7791F,stroke-width:2px,color:#4A2C0A
    classDef store fill:#E9D8FD,stroke:#6B46C1,stroke-width:2px,color:#2D1B4E
    classDef excluded fill:#FDE2E2,stroke:#C53030,stroke-width:2px,color:#4A1717

    class PDF,Structured source
    class Manifest,Hash,Schema,Layout,Chunks,Tables,Figures process
    class Usable decision
    class Search,DB,Crops store
    class Recorded excluded
```

### Query-time answer flow

```mermaid
flowchart TD
    Query(["使用者問題"]) --> Scope["Company scope<br/>document scope"]
    Scope --> Router["Typed bounded router<br/>最多一次 correction"]

    subgraph Routes["Evidence routes"]
        direction LR
        Narrative["Narrative route"] --> Hybrid["BM25 + dense retrieval"] --> Rerank["Cross-encoder reranking"] --> Generate["Grounded generation"]
        Numeric["Numeric route"] --> DuckDB[("DuckDB")] --> SQL["Templated SQL<br/>禁止 free-form SQL"] --> Calc["Deterministic calculation<br/>formula + operands"]
        Chart["Chart route"] --> Caption["Caption-assisted retrieval<br/>caption 不可作為答案"] --> Crop["Original crop pixels"] --> VLM["VLM reading"]
    end

    Router -->|"narrative / cross-modal"| Narrative
    Router -->|"numeric"| Numeric
    Router -->|"chart / table"| Chart

    Generate --> Contract["Answer + citation contract"]
    Calc --> Contract
    VLM --> Contract
    Contract --> Valid{"Evidence 與 citation<br/>可驗證且無來源衝突?"}
    Valid -->|"是"| Answer["Grounded answer<br/>citation / formula / operands"]
    Valid -->|"否"| Refusal["Structured refusal"]

    classDef input fill:#DDEBFF,stroke:#245A9A,stroke-width:2px,color:#102A43
    classDef control fill:#FFF3BF,stroke:#B7791F,stroke-width:2px,color:#4A2C0A
    classDef route fill:#E3F9E5,stroke:#2F855A,stroke-width:2px,color:#173F2A
    classDef store fill:#E9D8FD,stroke:#6B46C1,stroke-width:2px,color:#2D1B4E
    classDef success fill:#C6F6D5,stroke:#2F855A,stroke-width:2px,color:#173F2A
    classDef refusal fill:#FDE2E2,stroke:#C53030,stroke-width:2px,color:#4A1717

    class Query input
    class Scope,Router,Contract,Valid control
    class Narrative,Hybrid,Rerank,Generate,Numeric,SQL,Calc,Chart,Caption,Crop,VLM route
    class DuckDB store
    class Answer success
    class Refusal refusal
```

- Narrative route：hybrid retrieval、cross-encoder reranking、可定位 citation。
- Numeric route：可靠數值不交給 embedding 猜測；使用 DuckDB、deterministic SQL，並保留 formula 與 operands。
- Chart route：caption 只參與 indexing / retrieval；答案必須回到 crop pixels 或可靠結構化資料。
- Router：typed、bounded，最多一次 correction，沒有無上限 agent loop。
- Refusal：證據不足時拒答，並量測 refusal precision / recall。

## 事前註冊實驗

評分規則、tolerance、GO / NO-GO gates 與七個關鍵 artifact hash 在 locked run 前完成凍結。執行後不改題目、不調門檻、不挑結果。

### Pre-registered evaluation

```mermaid
flowchart TD
    Dev["DEV-only decisions<br/>gold / models / tolerance"] --> Register["固定 F0-F7 與 G1-G10 gates"]
    Register --> Freeze["freeze_protocol.py"]
    Freeze --> Lock["Protocol 1.0.0 lock<br/>7 artifact hashes"]
    Lock -.-> Rule["凍結後不得修改<br/>locked set / thresholds / models"]
    Lock --> Eval["唯一一次 locked evaluation<br/>執行 F0-F7"]
    Eval --> Recompute["verify_results.py<br/>由 raw artifacts 重算"]
    Recompute --> Verified{"重算一致?"}
    Verified -->|"否"| Stop["停止發布<br/>結果不可採信"]
    Verified -->|"是"| Gate["run_gate.py<br/>依 frozen gates 判定"]
    Gate --> Decision{"Mechanical decision"}
    Decision -->|"通過全部 hard gates"| Go["GO"]
    Decision -->|"僅 soft gate 未通過"| Conditional["CONDITIONAL_GO"]
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
    class Freeze,Lock,Rule frozen
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
| F4 | 57.6%（19/33） | page-neighbor expansion |
| F5 | 60.6%（20/33） | table / chart evidence |
| F6 | 54.5%（18/33） | typed dispatch |
| F7 | 18.2%（6/33） | full candidate |

最終 candidate 相對 baseline 的 hard-category pooled gain 為 **-27.8pp**。G1、G8、G9、G10 通過；G2–G7 未通過，因此結論為 `NO_GO`。

關鍵失敗訊號：

- citation validity：52.9%（9/17）
- numeric route accuracy：0%（0/12）
- route accuracy：33.3%（11/33）
- retrieval p95：0.149 秒
- generation p95：2.957 秒
- peak VRAM：20.09 GB

負面結果不是未完成。這份研究完成了 protocol freeze、資料 provenance、locked evaluation、raw-artifact 重算與機械式 gate decision，並留下下一輪 Protocol 2.x 可直接驗證的 failure decomposition。

## 重現方式

需求：Python 3.13、[uv](https://docs.astral.sh/uv/)。離線測試與資料驗證只使用 CPU；index build 與 generation 才需要 GPU，目標環境為 RTX 4090 24GB。

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
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
| [實作計畫](docs/IMPLEMENTATION_PLAN.md) | phase、交付項目與完成條件 |
| [資料 provenance](docs/DATA_PROVENANCE.md) | 官方來源、取得方式、授權與 SHA-256 |
| [決策紀錄](docs/DECISIONS.md) | 模型、parser、資料與實驗設計取捨 |
| [威脅模型](docs/THREAT_MODEL.md) | prompt injection、SSRF、rate limit、leakage、secrets |

主要結果位於 `results/feasibility/`，包含 protocol lock、F0–F7 summary、逐題 error analysis、重算驗證與最終 gate decision。

## 使用範圍

本專案**不是投資建議**，所有輸出僅為文件檢索與資訊擷取的技術驗證，不構成證券或金融商品的推薦、要約或決策依據。任何財務數字均應回到[公開資訊觀測站](https://mops.twse.com.tw/)原始文件核對。

本專案**不是 production 系統**，不提供 SLA、認證授權、多租戶隔離或安全稽核，不應直接用於實際決策流程。

程式碼採 [MIT License](LICENSE)。授權不涵蓋 TWSE / MOPS 原始文件與資料；相關內容仍依原始來源條款使用。
