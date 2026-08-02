"""Write docs/FEASIBILITY_REPORT.md from the frozen artifacts, or refuse and say why.

    uv run python scripts/make_report.py
    uv run python scripts/make_report.py --dry-run

Reads `results/feasibility/{summary.json,GO_NO_GO.json,protocol_lock.json}` plus the gold
composition, and hands them to `twfi.eval.report.build`, which raises rather than emit a
document missing a denominator, a required limitation, the lock hash, or a failed gate.

The limitations text lives here rather than in the module, because it is prose about this
study's particular constraints and it has to be reviewed by a person. What the module enforces
is that none of it is empty -- a report cannot become concise by dropping the awkward part.

The verdict is copied from GO_NO_GO.json and never recomputed here. Protocol 4 says nobody
overwrites the gate evaluator's verdict, and a report generator that could reach its own
conclusion would be the obvious place for that to happen.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Annotated, Any

import typer

from twfi.console import use_utf8_output
from twfi.eval.gold import composition, load_gold
from twfi.eval.report import MissingContent, build
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)

#: Prose about this study's constraints, each keyed to a section the module requires. Written
#: out rather than generated: these are claims about what the work does not support, and a
#: generated sentence would be a claim nobody checked.
LIMITATIONS: dict[str, str] = {
    "sample_size": (
        "locked set 為 33 題分到 8 個類型，單一類別只有 2–6 題。**一題等於 17–50 個百分點**，"
        "所以本輪樣本只能支持「可行／不可行」與「增益方向」的判斷，"
        "**不能**支持精確的效果量估計，也不能宣稱類別之間的差異具統計顯著性。"
        "每個比率都附 Wilson 95% 信賴區間；區間重疊即代表這份樣本無法分辨兩者。"
        "這一段不因結果好壞而改寫 —— 若 candidate 大幅勝出，同樣要寫「信賴區間很寬」。"
    ),
    "chart_route": (
        "`chart_value_trend` 只有 **2 題**，且**兩題都來自台積電的兩頁**（D-020）。"
        "全語料 503 個 figure candidate 中，逐一目視確認的真圖表只有 4 張，"
        "鴻海與國泰金 0 張，兩份財務報告書 0 張。"
        "因此這兩題能回答的是「能不能讀台積電那兩張資訊圖」，**不是「能不能讀圖表」**。"
        "更進一步（D-022 更正）：那兩頁的文字層完整，年份與圖例都抽得出來，"
        "所以**純文字系統靠座標鄰近也可能答對** —— 本語料唯一的真圖表也是文字可還原的。"
        "F5（caption）與 F6（crop VLM）的輸入幾乎全是有框表格，"
        "它們的增益**不得**被描述為 chart-reading 能力（D-021）。"
    ),
    "numeric_coverage": (
        "locked numeric route 使用 `numeric_broad.duckdb`：`load_all_rows.py` 逐頁走訪所有"
        "可用 filing，**不看 gold** 的答案、頁碼或 structured key，並載入抽取器找到的可分類"
        "科目。因此 F4 的可用數字不是由題目清單安排出來。這仍不是完整的財報資料庫：只有"
        "科目可分類、欄位能對應單一會計年度，且頁面有單位或可由前頁繼承時才會載入；受損"
        "文字層、碎裂表頭、合併年度、旋轉頁與列標籤脫節都會形成覆蓋缺口。DEV 上 broad store"
        "的 F4 為 11/15，只是凍結前的開發觀察，不能代替 locked 結果或一般化覆蓋率。"
    ),
    "structured_source": (
        "TWSE OpenAPI 只提供當期快照，與本研究的文件年度（FY2023／FY2024）交集為空，"
        "所以歷史結構化數值來自**本 repository 自己的表格抽取**（`source_kind=extracted_table`）。"
        "因此報告一律寫「**已驗證結構化資料**」而非「官方結構化資料」（R7）。"
        "若日後取得官方 XBRL，這項限制大幅緩解，但那需要重跑並重新標示來源。"
    ),
    "parser_generality": (
        "candidate parser 是**自建的 rule-based layout parser**（D-002），"
        "本輪**未驗證** learned layout model（如 docling、LayoutLM 類）。"
        "所以「結構化 parsing 帶來多少增益」的結論**不得**外推到學習式版面模型。"
        "表格抽取採 pdfplumber 兩種 strategy 的聯集（D-027），"
        "該選擇是在 dev 上量測後決定的，理由是沒有任一 strategy 支配另一個。"
    ),
    "text_layer": (
        "兩份 development filing 的原生文字層都受損：2412-FY2023-AR 有 17.9% 字元解碼為"
        "錯誤字集、48% 頁面受影響；1301-FY2023-AR 分別為 15.4% 與 43%。因此 DEV 上選出的"
        "閾值、chunking 與路由行為同時反映系統能力和受損文字層，不能直接推論到文字層完整的"
        "一般年報。locked 結果會誠實保留這個 domain shift，而不是把它解讀成純模型效果。"
    ),
    "dev_clustering": (
        "DEV 的 15 題只涵蓋 4 個不同的 (document, page-set) 證據目標，其中一個 chunk 承載"
        "8 題；DEV-0011 的註冊答案又不在文件中，所以 retrieval 指標存在已知上限。這些題目"
        "高度相關，DEV 差異只能用來選定方向與發現接線錯誤，不能當作獨立樣本的效果量或"
        "統計顯著性證據。"
    ),
    "numeric_ambiguity": (
        "全語料抽取顯示 account name 不是 filing 內的唯一鍵：locked 三家按正式 key 分組有"
        "46/115（40.0%）存在衝突值，國泰金控為 32/34（94.1%），因附註會為不同子公司重複"
        "相同科目。DEV 在加入"
        "consolidated/parent-only basis 後觀察到 0% 衝突，不能據此保證 locked。store 會保留"
        "每個 `source_ref`；同一 key 有多個候選時 numeric route 拒答，不讓最後讀到的頁面覆蓋"
        "先前來源。這是安全但會降低覆蓋率的失效模式，不是通用財報資料庫的正確性證明。"
    ),
    "approval_process": (
        "D-048（numeric store）、D-049（company scope）與 D-050（正式版號）的最終批准方式，"
        "是使用者在 development 結果已可見後委任實作者判斷，**不是**由未看過結果的獨立審查者"
        "盲評。採用 broad store 與全階一致 scope 的理由在數字反轉時仍成立，但這無法恢復"
        "『先決定再看數字』的獨立性；讀者應據此降低對 F4 與整體 confirmatory 解讀的信任。"
    ),
}


def _read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]] | None:
    if not path.is_file():
        return None
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        rows.append(payload)
    return rows


def _error_findings(rows: list[dict[str, Any]]) -> list[str]:
    """Turn the committed per-question error analysis into one counted finding."""
    counts: Counter[str] = Counter()
    for row in rows:
        buckets = row.get("buckets")
        if isinstance(buckets, list):
            counts.update(str(bucket) for bucket in buckets if str(bucket).strip())
    if not counts:
        return ["F7 error analysis：沒有 bucketed failure。"]
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    rendered = "、".join(f"{bucket}={count}" for bucket, count in ordered)
    return [f"F7 error analysis（同一題可屬多個 bucket）：{rendered}。"]


_NEXT_QUESTION_BY_GATE = {
    "G1": (
        "能否在不更換研究文件的前提下，於乾淨環境重建全部 required artifact 並通過 SHA-256 驗證？"
    ),
    "G2": (
        "僅將 rule-based layout parser 替換為 learned layout model，其餘設定固定，能否讓 "
        "pooled hard categories 相對 F0 達到註冊的 +15pp？"
    ),
    "G3": (
        "僅改善 evidence ranking、保持 parser 與 answer model 固定，能否讓 F7 overall "
        "accuracy 相對 F0 達到註冊增益？"
    ),
    "G4": "僅修正證據選擇與引用 grounding、不改答案內容，能否把 citation validity 提高到 90%？",
    "G5": (
        "為 numeric row key 加入子公司／報表實體識別、仍不讀 gold，能否把 numeric route "
        "accuracy 提高到 90%？"
    ),
    "G6": "保持各 route 不變，只替換 typed router，能否把 route accuracy 提高到 85%？",
    "G7": (
        "保持回答模型不變，只加入可校準的 abstention gate，能否同時滿足 over-answer 與 "
        "refusal precision 門檻？"
    ),
    "G8": (
        "保持問題與模型不變，只加入 evidence-presence gate，能否讓五個 no-evidence "
        "probes 全部拒答？"
    ),
    "G9": (
        "哪一個最小的 artifact/schema 修正能讓 summary 的每個數字在乾淨 clone 中由 raw "
        "records 重算？"
    ),
    "G10": (
        "保持模型與準確率設定不變，只調整 batching／量化，能否讓 latency 與 VRAM 同時進入 "
        "G10 預算？"
    ),
}


def _next_question(verdict: str, gates: list[dict[str, Any]]) -> str | None:
    """Pick one follow-up from the first failed registered gate, never from aesthetics."""
    if verdict == "GO":
        return None
    failed = [
        str(gate.get("gate", ""))
        for gate in gates
        if gate.get("passed") is False
        and (str(gate.get("kind", "hard")) == "hard" or verdict == "CONDITIONAL_GO")
    ]
    failed.sort(key=lambda name: int("".join(char for char in name if char.isdigit()) or 999))
    if not failed:
        return "哪一個單一、可獨立驗證的變更能解掉本次非 GO 判定的主要成因？"
    gate = failed[0]
    return _NEXT_QUESTION_BY_GATE.get(
        gate, f"哪一個單一、可獨立驗證的變更能使 {gate} 通過而不改動其他 gate？"
    )


def _report_lock_sha256(summary: dict[str, Any], lock: dict[str, Any]) -> str | None:
    """Use G9's digest of the lock file; support legacy self-identifying locks second."""
    value = summary.get("protocol_lock_sha256") or lock.get("protocol_sha256") or lock.get("sha256")
    return str(value).strip() or None


@app.command()
def main(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the report without writing it.")
    ] = False,
) -> None:
    """Build the report, refusing if any required content is absent."""
    paths = repo_paths()
    summary = _read(paths.feasibility / "summary.json")
    verdict_payload = _read(paths.feasibility / "GO_NO_GO.json")
    lock = _read(paths.feasibility / "protocol_lock.json")
    error_analysis = _read_jsonl(paths.error_analysis_jsonl)

    absent = [
        name
        for name, value in (
            ("summary.json", summary),
            ("GO_NO_GO.json", verdict_payload),
            ("protocol_lock.json", lock),
            ("error_analysis.jsonl", error_analysis),
        )
        if value is None
    ]
    if absent:
        typer.echo(f"cannot write the report: {absent} missing under results/feasibility/")
        typer.echo("Run the locked evaluation, then run_gate, then freeze_protocol first.")
        raise typer.Exit(code=2)
    assert (
        summary is not None
        and verdict_payload is not None
        and lock is not None
        and error_analysis is not None
    )

    records = load_gold(paths.locked_gold.read_text(encoding="utf-8").splitlines())

    try:
        verdict = str(verdict_payload.get("verdict", ""))
        gates = list(verdict_payload.get("gates", ()))
        text = build(
            verdict=verdict,
            gates=gates,
            summary=summary,
            composition=composition(records),
            limitations=LIMITATIONS,
            protocol_lock_sha256=_report_lock_sha256(summary, lock),
            findings=[*list(summary.get("findings", ())), *_error_findings(error_analysis)],
            next_question=_next_question(verdict, gates),
        )
    except MissingContent as exc:
        typer.echo("refusing to write an incomplete report:")
        typer.echo(f"  {exc}")
        raise typer.Exit(code=1) from exc

    if dry_run:
        typer.echo(text)
        return
    destination = paths.root / "docs" / "FEASIBILITY_REPORT.md"
    destination.write_text(text, encoding="utf-8")
    typer.echo(f"wrote: {destination.relative_to(paths.root)} ({len(text):,} chars)")


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
