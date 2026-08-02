# README Mermaid 圖表 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 README 加入三張經實際 render 驗證的 Mermaid 圖，清楚說明 offline data preparation、query-time routes 與 pre-registered evaluation。

**Architecture:** 每張圖只處理一個視角，使用 GitHub 原生 `flowchart` 與一致的高對比樣式。README 直接保存 Mermaid source，不提交衍生 PNG / SVG；系統行為與數字只取自現有 code、Protocol 1.0.0 與 frozen results。

**Tech Stack:** GitHub Flavored Markdown、Mermaid flowchart、Mermaid CLI、PowerShell、pytest、Ruff、mypy

## Global Constraints

- README 以正體中文為主，technical terms 保留原文。
- README 與 Mermaid labels 不使用 emoji 或裝飾性 Unicode symbols。
- 必須保留「不是投資建議」與「不是 production 系統」。
- 不修改 `docs/FEASIBILITY_PROTOCOL.md`、locked data 或 `results/feasibility/`。
- `NO_GO` 是有效研究結論，不得畫成 pipeline error。
- Commit 作者只能是 `kuotunyu <61350295+kuotunyu@users.noreply.github.com>`，不得加入 `Co-authored-by:`。
- 不提交 `.mmd`、PNG 或 SVG；Mermaid 驗證產物只放暫存目錄並在驗證後移除。

---

### Task 1: 產生並驗證三張 Mermaid 圖

**Files:**
- Create temporarily: `.tmp-mermaid/offline-preparation.mmd`
- Create temporarily: `.tmp-mermaid/query-flow.mmd`
- Create temporarily: `.tmp-mermaid/evaluation-flow.mmd`
- Do not commit: `.tmp-mermaid/`

**Interfaces:**
- Consumes: `docs/superpowers/specs/2026-08-03-readme-mermaid-diagrams-design.md`、`src/twfi/`、`docs/FEASIBILITY_PROTOCOL.md`、`results/feasibility/summary.json`
- Produces: 三段通過 Mermaid CLI render 的 source code，供 Task 2 原樣嵌入 README

- [ ] **Step 1: 建立 offline data preparation 圖**

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

- [ ] **Step 2: 建立 query-time answer flow 圖**

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

- [ ] **Step 3: 建立 pre-registered evaluation 圖**

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

- [ ] **Step 4: 用 Mermaid CLI render 三張圖**

Run:

```powershell
mmdc -i .tmp-mermaid/offline-preparation.mmd -o $env:TEMP/twfi-offline.svg -b transparent
mmdc -i .tmp-mermaid/query-flow.mmd -o $env:TEMP/twfi-query.svg -b transparent
mmdc -i .tmp-mermaid/evaluation-flow.mmd -o $env:TEMP/twfi-evaluation.svg -b transparent
Get-Item $env:TEMP/twfi-offline.svg, $env:TEMP/twfi-query.svg, $env:TEMP/twfi-evaluation.svg | Select-Object Name,Length
```

Expected: 三個 `mmdc` command 均 exit 0，三個 SVG 的 `Length` 均大於 0。

---

### Task 2: 將已驗證圖表整合進 README

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1 三段已通過 render 的 Mermaid source
- Produces: GitHub 可直接渲染、正體中文主體且無 emoji 的 README

- [ ] **Step 1: 取代現有 ASCII 系統框架**

在 `## 系統設計` 下依序加入小標題 `### Offline data preparation` 與 `### Query-time answer flow`，嵌入 Task 1 的前兩段 Mermaid source。保留五條 route 說明，但刪除原本的 `text` code block。

- [ ] **Step 2: 加入研究流程圖**

在 `## 事前註冊實驗` 的說明文字後、F0–F7 table 前加入 `### Pre-registered evaluation`，嵌入 Task 1 的第三段 Mermaid source。

- [ ] **Step 3: 檢查 README invariants**

Run:

```powershell
rg -n "不是投資建議|不是 production 系統|NO_GO|F0 17/33|F7 6/33|-27.8pp" README.md
rg -n -P "[\x{1F300}-\x{1FAFF}\x{2600}-\x{27BF}⑤]" README.md
```

Expected: 第一個 command 找到所有必要文字；第二個 command exit 1 且沒有輸出，代表無 emoji。

- [ ] **Step 4: 從完成版 README 再次驗證 Mermaid**

Run:

```powershell
python C:/Users/3Hml/.agents/skills/design-doc-mermaid/scripts/extract_mermaid.py README.md --validate
```

Expected: 找到三張 Mermaid 圖且三張 validation 全部通過。

---

### Task 3: Repository 品質驗證、提交與推送

**Files:**
- Modify: `README.md`
- Preserve untracked/ignored: `interview.md`

**Interfaces:**
- Consumes: 完成版 README
- Produces: 通過 local checks 與 GitHub Actions 的 `main`

- [ ] **Step 1: 執行完整本機驗證**

Run:

```powershell
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy src
git diff --check
git check-ignore -v interview.md
```

Expected: pytest 0 failures、Ruff / format / mypy exit 0、`git diff --check` 無輸出、`interview.md` 由 `.git/info/exclude` 排除。

- [ ] **Step 2: 確認提交範圍與作者**

Run:

```powershell
git status --short
git diff --stat
git config user.name
git config user.email
```

Expected: 只包含 plan 與 README 文件變更；作者為 `kuotunyu` 與 GitHub noreply email，沒有 PDF、`.env`、rendered images 或 `interview.md`。

- [ ] **Step 3: 提交 README 實作**

```powershell
git add -- README.md
git commit -m "加入 Mermaid 架構與研究流程圖"
```

- [ ] **Step 4: 推送 main 並等待 CI**

```powershell
git push origin main
$runId = gh run list --workflow ci.yml --limit 1 --json databaseId --jq '.[0].databaseId'
gh run watch $runId --exit-status --interval 5
```

Expected: push 成功；最新 CI conclusion 為 `success`。

- [ ] **Step 5: GitHub 端最終稽核**

Run:

```powershell
git ls-remote --heads origin
gh api repos/kuotunyu/tw-filing-intelligence/contributors --paginate --jq '.[].login'
gh api repos/kuotunyu/tw-filing-intelligence/contents/README.md --jq '.sha'
git status --short --branch
```

Expected: 遠端只有 `main`；Contributors 只有 `kuotunyu`；README SHA 對應目前 `main`；工作區乾淨並追蹤 `origin/main`。
