"""Where does the chunk holding the answer actually rank?

    uv run python scripts/diagnose_ranking.py --set dev
    uv run python scripts/diagnose_ranking.py --set dev --same-company-only
    uv run python scripts/diagnose_ranking.py --set dev --parser baseline --depth 200

**A diagnostic, never a gate.** Protocol 3.2 registers Recall@5, MRR@10 and the two coverage
metrics; this is none of them and must not be reported as one. It exists to answer a question
those four cannot: when the pipeline fails, *how far off* was it?

Recall@5 is page-level, and D-041 records what that hides -- a page splits into several chunks and
the one carrying the figure need not be the one retrieved. `run_eval.py` added
``answer_in_prompt`` for that, but a boolean only says the answer was absent, not whether it was
absent by two places or by four thousand. Those call for opposite work: a near miss is a ranking
problem, and a chunk that never surfaces at all may not be indexed in a findable form.

The check is deliberately crude -- does the longest figure in the gold answer appear verbatim in
the chunk's text. It over-counts: a chunk can contain 530,738,356 without being the evidence for
it. That bias is the safe direction here, because the finding this produces is that the answer
chunk ranks *badly*, and a measure that over-counts matches can only make that look better than
it is.

Questions whose answer is computed rather than printed are reported separately. 65.45 is
100 - 34.55 and appears nowhere in the corpus; counting it as a retrieval failure would blame the
retriever for a number no page contains.

``--same-company-only`` re-scores the *same* run with other issuers' hits dropped. It changes no
registered behaviour and is not an alternative configuration -- it exists because the top hit for
「台塑民國112年度的資產總計是多少？」 is a 台積公司 balance sheet, and the question is how much
of the miss is that. Every filing declares its company and every question names one; nothing in
the protocol 2.4 pipeline uses either.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Annotated, Any

import typer

from twfi.console import use_utf8_output
from twfi.eval.gold import load_gold
from twfi.index.embeddings import load_vectors
from twfi.index.lexical import load_index
from twfi.index.retrieve import Retriever
from twfi.io.jsonl import read_lines
from twfi.paths import repo_paths
from twfi.protocol import USABLE_DOCUMENTS

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

app = typer.Typer(add_completion=False, help=__doc__)

#: Long enough not to be a year, a note reference, or a column count. The decimal tail is not
#: optional decoration: without it 「95.40%；82.82%」 matched nothing, fell through to the prose
#: branch, and DEV-0004 was reported as computed-never-printed when 95.40 is on the page.
_FIGURE = re.compile(r"\d[\d,]*\.\d+|\d[\d,]{4,}")


def answer_key(answer: str) -> str:
    """The longest figure in a gold answer, or its opening characters for a prose answer."""
    figures = _FIGURE.findall(answer)
    if figures:
        return str(max(figures, key=len))
    return answer[:8]


def _embedder() -> Callable[[str], Any]:
    """bge-m3 on CPU. The card is for generation and reranking (CLAUDE.md rule 8)."""
    import torch
    from transformers import AutoModel, AutoTokenizer

    torch.set_num_threads(6)
    tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3")
    model = AutoModel.from_pretrained("BAAI/bge-m3", dtype=torch.float32).eval()

    def embed(query: str) -> Any:
        with torch.inference_mode():
            batch = tokenizer([query], padding=True, truncation=True, return_tensors="pt")
            return model(**batch).last_hidden_state[:, 0][0].numpy()

    return embed


@app.command()
def main(
    gold_set: Annotated[str, typer.Option("--set", help="dev only until the freeze.")] = "dev",
    parser: Annotated[str, typer.Option(help="baseline or candidate index.")] = "candidate",
    depth: Annotated[int, typer.Option(help="How far down to look before giving up.")] = 100,
    same_company: Annotated[
        bool,
        typer.Option(
            "--same-company-only",
            help="Score ranks after dropping hits from other companies. Measures how much of "
            "the failure is cross-company noise; changes no registered behaviour.",
        ),
    ] = False,
) -> None:
    """Report the rank of the answer-bearing chunk for every answerable question."""
    paths = repo_paths()
    if gold_set != "dev":
        typer.echo("refusing: only dev may be inspected before the freeze (protocol 1.3).")
        raise typer.Exit(code=2)

    records = list(load_gold(paths.dev_gold.read_text(encoding="utf-8").splitlines()))
    directory = paths.root / "data" / "index" / parser
    chunks = read_lines(directory / "chunks.jsonl")
    bm25, _ = load_index(directory, expect_documents=len(chunks))
    vectors, _ = load_vectors(directory, expect_rows=len(chunks))
    retriever = Retriever(
        chunks=chunks,
        bm25=bm25,
        vectors=vectors,
        embed_query=_embedder(),
        fetch_depth=max(depth, 100),
    )

    typer.echo(f"{parser} index: {len(chunks):,} chunks; looking {depth} deep")
    typer.echo("")
    typer.echo(f"{'question':<11}{'type':<24}{'answer key':<16}{'rank':>8}")

    ranked: list[int] = []
    unprinted: list[str] = []
    missing: list[str] = []
    for record in records:
        if not record.answerable or record.answer is None:
            continue
        key = answer_key(str(record.answer))
        company_of = record.company.code
        if not any(key in chunk["text"] for chunk in chunks):
            unprinted.append(record.question_id)
            typer.echo(f"{record.question_id:<11}{record.question_type:<24}{key:<16}{'—':>8}")
            continue
        hits = retriever.search(record.question, top_k=depth)
        if same_company:
            # The question names one company and every document declares one. Nothing in the
            # registered pipeline uses that, so 「台塑的資產總計」 competes against every other
            # issuer's 資產總計. Scoring the same run with the other companies removed says how
            # much of the miss is that, without changing what the pipeline does.
            wanted = {doc.doc_id for doc in USABLE_DOCUMENTS if doc.company_code == company_of}
            hits = [hit for hit in hits if hit.doc_id in wanted]
        places = [index + 1 for index, hit in enumerate(hits) if key in hit.text]
        if places:
            ranked.append(places[0])
            typer.echo(f"{record.question_id:<11}{record.question_type:<24}{key:<16}{places[0]:>8}")
        else:
            missing.append(record.question_id)
            typer.echo(
                f"{record.question_id:<11}{record.question_type:<24}{key:<16}{f'>{depth}':>8}"
            )

    typer.echo("")
    within = sum(1 for place in ranked if place <= 5)
    typer.echo(f"answer chunk in the top 5   : {within}/{len(ranked) + len(missing)}")
    typer.echo(f"answer chunk found at all   : {len(ranked)}/{len(ranked) + len(missing)}")
    if ranked:
        typer.echo(f"ranks                       : {sorted(ranked)}")
    if missing:
        typer.echo(f"not in the top {depth}          : {', '.join(missing)}")
    if unprinted:
        typer.echo(
            f"computed, never printed     : {', '.join(unprinted)} "
            "(absent from the corpus by construction, not a retrieval failure)"
        )
    typer.echo("")
    typer.echo(
        "This is a diagnostic, not a protocol 3.2 metric. It does not enter any gate and "
        "must not be reported as recall."
    )


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
