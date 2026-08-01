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
        "numeric store 只涵蓋 **gold 有問到的 account**，因為載入 2,895 頁的所有表格超出本輪範圍。"
        "「numeric route 拿得到它需要的數值」**不是覆蓋率的發現，是安排的結果**。"
        "而且實測（D-028）：20 個 gold 指名的 cell 只成功載入 2 個 —— "
        "locked 的財務報告書頁面上，表格結構壞到讓這條路幾乎不通"
        "（附註被合併成一張表、列標籤與數值脫節、表頭碎裂且合併年度、旋轉頁）。"
        "抽取器產出什麼就載什麼；**與 gold 相符從不作為載入條件**，"
        "否則 F4 會因為建構方式而正確。"
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
}


def _read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


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

    absent = [
        name
        for name, value in (
            ("summary.json", summary),
            ("GO_NO_GO.json", verdict_payload),
            ("protocol_lock.json", lock),
        )
        if value is None
    ]
    if absent:
        typer.echo(f"cannot write the report: {absent} missing under results/feasibility/")
        typer.echo("Run the locked evaluation, then run_gate, then freeze_protocol first.")
        raise typer.Exit(code=2)
    assert summary is not None and verdict_payload is not None and lock is not None

    records = load_gold(paths.locked_gold.read_text(encoding="utf-8").splitlines())

    try:
        text = build(
            verdict=str(verdict_payload.get("verdict", "")),
            gates=list(verdict_payload.get("gates", ())),
            summary=summary,
            composition=composition(records),
            limitations=LIMITATIONS,
            protocol_lock_sha256=str(
                lock.get("protocol_sha256") or lock.get("sha256") or ""
            ).strip()
            or None,
            findings=list(summary.get("findings", ())),
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
