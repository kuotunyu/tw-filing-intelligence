# README Mermaid 清楚度修正 Implementation Plan

`status: executed and archived — 2026-08-03`

> 四張 Mermaid 已完成、render／目視檢查通過，並隨 v1.0.1 closeout 進入 `main`。
> 本檔保留實作證據，不再是待執行工作。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將容易混淆的 Mermaid 資料準備圖重構成四張不需猜測來源、箭頭或術語的圖，並修正 locked numeric store 的實際資料來源。

**Architecture:** PDF evidence preparation 與 numeric source reality 分開；query flow 與 evaluation flow 改成白話中文動作句。每張圖前先說明它回答的問題，Mermaid source 直接放在 README，不提交衍生圖檔。

**Tech Stack:** GitHub Flavored Markdown、Mermaid 11 flowchart、Mermaid CLI、PowerShell、pytest、Ruff、mypy

## Global Constraints

- README 以正體中文為主，technical terms 保留原文但同段解釋用途。
- README 與 Mermaid labels 不使用 emoji 或裝飾性 Unicode symbols。
- 必須保留「不是投資建議」與「不是 production 系統」。
- 不修改 frozen protocol、locked data 或 `results/feasibility/`。
- 不得暗示 OpenAPI / XBRL 提供本次 FY2023–FY2024 locked numeric store。
- `NO_GO` 是有效研究結論，不畫成 pipeline error。
- Commit 作者只能是 `kuotunyu <61350295+kuotunyu@users.noreply.github.com>`，不得加入 `Co-authored-by:`。
- 不提交暫存 `.mmd`、PNG 或 SVG。

---

### Task 1: 建立並驗證四張修正版 Mermaid 圖

**Files:**
- Create temporarily: `.tmp-mermaid/pdf-evidence.mmd`
- Create temporarily: `.tmp-mermaid/numeric-sources.mmd`
- Create temporarily: `.tmp-mermaid/query-flow.mmd`
- Create temporarily: `.tmp-mermaid/evaluation-flow.mmd`
- Do not commit: `.tmp-mermaid/`

**Interfaces:**
- Consumes: `docs/FEASIBILITY_REPORT.md:126`、`docs/FEASIBILITY_REPORT.md:130`、`scripts/run_eval.py`、`src/twfi/`
- Produces: 四段通過 Mermaid CLI render 且經視覺檢查的 source code

- [x] **Step 1: 建立 PDF evidence preparation 圖**

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

- [x] **Step 2: 建立 locked numeric source reality 圖**

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

- [x] **Step 3: 建立白話 query flow 圖**

```mermaid
flowchart TD
    Query(["使用者問題"]) --> Scope["步驟 1：限定公司、年度與文件"]
    Scope --> Router["步驟 2：判斷題型並選回答路徑<br/>router 最多修正一次"]

    Router -->|"敘述／跨頁"| Narrative["找出相關文字"]
    Narrative --> Rerank["依相關性重新排序"]
    Rerank --> Generate["LLM 只根據 evidence 回答"]

    Router -->|"數值／跨期"| Numeric["查 DuckDB"]
    Numeric --> SQL["使用固定 SQL template<br/>不讓 LLM 自由寫 SQL"]
    SQL --> Calc["由程式計算<br/>formula + operands"]

    Router -->|"圖表／表格"| Chart["用 caption 找到相關頁"]
    Chart --> Crop["回到 original crop pixels"]
    Crop --> VLM["VLM 從原圖讀值"]

    Generate --> Check["步驟 3：驗證 evidence、citation 與來源衝突"]
    Calc --> Check
    VLM --> Check
    Check --> Valid{"證據足夠且引用可驗證？"}
    Valid -->|"是"| Answer["輸出答案、引用<br/>以及必要的計算過程"]
    Valid -->|"否"| Refusal["拒答並說明缺少什麼證據"]

    classDef input fill:#DDEBFF,stroke:#245A9A,stroke-width:2px,color:#102A43
    classDef control fill:#FFF3BF,stroke:#B7791F,stroke-width:2px,color:#4A2C0A
    classDef route fill:#E3F9E5,stroke:#2F855A,stroke-width:2px,color:#173F2A
    classDef success fill:#C6F6D5,stroke:#2F855A,stroke-width:2px,color:#173F2A
    classDef refusal fill:#FDE2E2,stroke:#C53030,stroke-width:2px,color:#4A1717

    class Query input
    class Scope,Router,Check,Valid control
    class Narrative,Rerank,Generate,Numeric,SQL,Calc,Chart,Crop,VLM route
    class Answer success
    class Refusal refusal
```

- [x] **Step 4: 建立白話 evaluation flow 圖**

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

- [x] **Step 5: 用 Mermaid CLI render 並目視檢查四張圖**

Run:

```powershell
npx --yes @mermaid-js/mermaid-cli -i .tmp-mermaid/pdf-evidence.mmd -o "$env:TEMP/twfi-pdf-evidence.png" -b white -w 1800
npx --yes @mermaid-js/mermaid-cli -i .tmp-mermaid/numeric-sources.mmd -o "$env:TEMP/twfi-numeric-sources.png" -b white -w 1800
npx --yes @mermaid-js/mermaid-cli -i .tmp-mermaid/query-flow.mmd -o "$env:TEMP/twfi-query-flow.png" -b white -w 1800
npx --yes @mermaid-js/mermaid-cli -i .tmp-mermaid/evaluation-flow.mmd -o "$env:TEMP/twfi-evaluation-flow.png" -b white -w 1800
Get-Item "$env:TEMP/twfi-pdf-evidence.png","$env:TEMP/twfi-numeric-sources.png","$env:TEMP/twfi-query-flow.png","$env:TEMP/twfi-evaluation-flow.png" | Select-Object Name,Length
```

Expected: 四個 render command 都 exit 0；四個 PNG 非空；目視確認來源與去向不需跨線推測。

---

### Task 2: 將四張圖與白話導讀整合進 README

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1 四段已驗證 Mermaid source
- Produces: 正體中文主體、無 emoji、資料來源準確的 GitHub README

- [x] **Step 1: 重寫系統設計的資料準備段落**

將 `### Offline data preparation` 改為 `### PDF 如何變成可查詢證據`，在圖前加入：

```markdown
這張圖只回答一件事：一份 PDF 進入專案後，哪些內容會進 search index、DuckDB 或 VLM。
```

以 Task 1 Step 1 的圖取代原圖。

- [x] **Step 2: 加入 numeric source reality**

新增 `### 本次實驗的歷史數值來自哪裡`，在圖前加入：

```markdown
本次 locked run 沒有用 OpenAPI 或 XBRL 補齊 FY2023–FY2024 歷史數值；numeric route 查的是專案從 filing line stream 重建的 rows。
```

圖後加入：

```markdown
因此本輪只能稱為「已驗證結構化資料」，不能稱為「官方結構化歷史資料」。每筆 row 仍保留 `source_kind` 與 `source_ref`，可追回原始文件位置。
```

- [x] **Step 3: 重寫 query flow 與導讀**

將標題改為 `### 問題如何選路徑並產生答案`，圖前加入：

```markdown
問題先限制到指定公司、年度與文件，再依題型選回答路徑；不論走哪條路，最後都必須通過證據與引用驗證，否則拒答。
```

以 Task 1 Step 3 的圖取代現圖。

- [x] **Step 4: 重寫 evaluation flow 與導讀**

將標題改為 `### 事前註冊如何防止看到結果後再調整`，圖前加入：

```markdown
只有 DEV 階段可以調整設定；protocol freeze 之後，只能執行 locked evaluation、從 raw records 重算，並依事先固定的 gates 判定。
```

以 Task 1 Step 4 的圖取代現圖。

- [x] **Step 5: 檢查讀者不需猜測的文案 invariants**

Run:

```powershell
rg -n "這張圖只回答一件事|沒有用 OpenAPI 或 XBRL|只有 FY2026Q1|XBRL.*未取得|已驗證結構化資料|問題先限制到指定公司|只有 DEV 階段可以調整" README.md
rg -n "Machine-usable|Document preparation|Company scope|Mechanical decision|Offline data preparation|Query-time answer flow|Pre-registered evaluation" README.md
```

Expected: 第一個 command 找到所有明確說明；第二個 command exit 1 且沒有輸出。

---

### Task 3: 完整驗證、提交與推送

**Files:**
- Modify: `README.md`
- Preserve ignored: `interview.md`

**Interfaces:**
- Consumes: 完成版 README
- Produces: 通過本機 checks 與 GitHub Actions 的 `main`

- [x] **Step 1: 從完成版 README 抽出四張 Mermaid 並重新 render**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe <DESIGN_DOC_MERMAID_SKILL_ROOT>/scripts/extract_mermaid.py README.md --output-dir .tmp-mermaid/extracted --prefix readme
$outputs = @()
Get-ChildItem .tmp-mermaid/extracted -Filter *.mmd | Sort-Object Name | ForEach-Object {
    $output = Join-Path $env:TEMP ("twfi-" + $_.BaseName + ".svg")
    npx --yes @mermaid-js/mermaid-cli -i $_.FullName -o $output -b transparent
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $outputs += Get-Item $output
}
$outputs | Select-Object Name,Length
if ($outputs.Count -ne 4 -or ($outputs | Where-Object Length -le 0)) { exit 1 }
```

Expected: extractor 找到四張圖；四張皆 exit 0 且輸出非空。

- [x] **Step 2: 執行 README 與 repository checks**

Run:

```powershell
rg -n "不是投資建議|不是 production 系統|NO_GO|F0 17/33|F7 6/33|-27.8pp" README.md
rg -n -P "[\x{1F300}-\x{1FAFF}\x{2600}-\x{27BF}⑤]" README.md
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy src
git diff --check
git check-ignore -v interview.md
```

Expected: README 必要文字存在、emoji scan exit 1、pytest 0 failures、Ruff / format / mypy exit 0、`git diff --check` 無輸出、`interview.md` 仍被排除。

- [x] **Step 3: 提交並推送**

```powershell
git add -- README.md docs/superpowers/plans/2026-08-03-readme-mermaid-diagrams.md
git commit -m "釐清 Mermaid 架構圖"
git push origin main
```

- [x] **Step 4: 等待 CI 並稽核 GitHub**

Run:

```powershell
$runId = gh run list --workflow ci.yml --limit 1 --json databaseId --jq '.[0].databaseId'
gh run watch $runId --exit-status --interval 5
git ls-remote --heads origin
gh api repos/kuotunyu/tw-filing-intelligence/contributors --paginate --jq '.[].login'
git status --short --branch
```

Expected: CI `success`、遠端只有 `main`、Contributors 只有 `kuotunyu`、工作區乾淨。
