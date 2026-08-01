"""Measure the resource budget G10 is judged against, instead of estimating it.

    uv run python scripts/measure_resources.py --gpu
    uv run python scripts/measure_resources.py --gpu --generate 5 --embed

Three numbers in this study were assumptions, and one of them could invalidate the design:

* **VRAM with the generation model resident.** Risk R3 estimated 20-21 GB against G10's hard
  22 GB limit, by adding up model sizes on paper. If that is wrong the architecture has to
  change, and it is much cheaper to learn now than after the protocol is frozen.
* **Generation latency at the frozen decoding parameters.** G10 requires 60 s or less.
  Nothing had measured it.
* **Embedding throughput** over the 8,859 chunks both parsers produce. Needs ``--embed``,
  because torch lives in the optional ``models`` extra that is deliberately not installed --
  the default environment stays CPU-only and offline so the tests cannot drift onto a GPU.

Generation goes through the local ollama HTTP API rather than the CLI, because the frozen
parameters (``temperature=0``, ``num_ctx=8192``, ``num_predict=512``, ``think=false``) have to
be set exactly; latency measured under different settings is not the latency G10 judges.
``httpx`` is already a declared dependency, so this costs no new install.

**Dev filings and synthetic prompts only.** Timing the locked questions would mean running
the locked set before the freeze, which protocol 1.3 forbids. Latency does not depend on
which company the text is about, so there is no reason to spend locked data on it.

Cold and warm are reported separately: the first call includes loading 17 GB of weights, and
protocol 5 step 8 measures the ladder both ways.

Writes ``results/runs/resource_budget.json``. Never touches ``results/feasibility/``.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import time
from typing import Annotated, Any

import httpx
import typer

from twfi.console import use_utf8_output
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)

OLLAMA_URL = "http://127.0.0.1:11434"
GENERATION_MODEL = "qwen3.6:27b"
EMBEDDING_MODEL = "BAAI/bge-m3"
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

#: Chunks both parsers produce over the eight **usable** filings, measured by actually
#: building the index: 4,063 baseline + 4,796 candidate. parse_stats.json reports 18,931,
#: but that sums all ten declared documents including the two unusable 2317 annual reports,
#: which are never indexed. Using the larger figure overstated the projected build time by
#: more than double.
#:
#: Was 13_953, from a candidate index of 9,890 chunks. The heading-detection fix (D-031)
#: halved the candidate chunking to 4,796, so any projection made against the old figure
#: overstated the build by 58%. Re-measure rather than scale: `results/runs/resource_budget.json`
#: still carries the old projection until `--embed` is run again.
CORPUS_CHUNKS = 8_859

#: Another process holding more than this much is someone else's training run. Protocol
#: rule 8: yield rather than compete.
FOREIGN_VRAM_LIMIT_MIB = 2_500

#: Protocol 2.2, frozen before the locked run.
DECODING: dict[str, Any] = {
    "temperature": 0.0,
    "top_p": 1.0,
    "top_k": 1,
    "seed": 20260731,
    "num_predict": 512,
    "num_ctx": 8192,
}

#: Short prompts in the register the study uses, long enough to exercise the context but
#: deliberately not gold questions: this measures the clock, not the answers.
PROMPTS: tuple[str, ...] = (
    (
        "以下是年報片段。請用一句話說明它在講什麼。\n\n"
        "本公司民國112年度營業收入為 223,199,260 仟元，較前一年度成長。"
    ),
    (
        "根據下列片段回答：資產總計是多少？只回答數字。\n\n"
        "資產總計 530,738,356 負債總計 183,378,211 權益總計 347,360,145"
    ),
    (
        "請判斷下列問題能否由片段回答，若不能請說明原因。\n\n"
        "問題：民國113年度的營業收入是多少？\n片段：民國112年度營業收入 223,199,260 仟元。"
    ),
    (
        "請將下列三個比率照原樣列出。\n\n"
        "現金流量比率 5.97% 現金流量允當比率 82.82% 現金再投資比率 -3.35%"
    ),
    (
        "以下片段是否提到碳排放強度？只回答有或沒有。\n\n"
        "本公司持續推動節能減碳，並揭露範疇一及範疇二排放量。"
    ),
)


def _vram_mib() -> tuple[int, int]:
    """``(used, free)`` for the whole card, from nvidia-smi."""
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.free", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=True,
    )
    used, free = (int(part.strip()) for part in result.stdout.strip().splitlines()[0].split(","))
    return used, free


def _foreign_holder() -> str | None:
    """The name of a process using the card that is not ollama, if there is one.

    The first version of this guard compared total VRAM against a threshold, which is the
    wrong test: it refused to run because *this script's own previous run* had left
    qwen3.6:27b resident in ollama. A warm cache of the study's declared model is not
    competition for the card, and a guard that cannot tell it from someone else's training
    job will either block legitimate work or wave through the thing it exists to prevent.

    Compute processes, therefore, not a memory total. Ollama is ours; anything else is not.
    """
    result = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=process_name,used_memory", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.strip().splitlines():
        name, _, memory = line.partition(",")
        name = name.strip()
        if not name or "Insufficient Permissions" in name:
            continue
        if "ollama" in name.lower():
            continue
        try:
            held = int(memory.strip().split()[0])
        except (ValueError, IndexError):
            continue
        if held > FOREIGN_VRAM_LIMIT_MIB:
            return f"{name} holding {held} MiB"
    return None


def _ollama_resident() -> str | None:
    """What ollama currently holds, so a warm start can be reported as one."""
    result = subprocess.run(["ollama", "ps"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    lines = [line for line in result.stdout.strip().splitlines()[1:] if line.strip()]
    return lines[0].split()[0] if lines else None


@app.command()
def main(
    gpu: Annotated[
        bool, typer.Option("--gpu", help="Required. This command uses the GPU.")
    ] = False,
    generate: Annotated[int, typer.Option(help="Prompts to time.")] = 5,
    embed: Annotated[
        bool, typer.Option("--embed", help="Also measure bge-m3 (needs the models extra).")
    ] = False,
    embed_sample: Annotated[int, typer.Option(help="Chunks to embed if --embed.")] = 500,
) -> None:
    """Measure VRAM, generation latency, and optionally embedding throughput."""
    paths = repo_paths()
    if not gpu:
        typer.echo("this command needs the GPU; pass --gpu to say so deliberately")
        raise typer.Exit(code=2)

    at_rest, free = _vram_mib()
    typer.echo(f"VRAM at rest: {at_rest} MiB used, {free} MiB free")

    foreign = _foreign_holder()
    if foreign is not None:
        typer.echo(
            f"another process is using the card: {foreign}. Protocol rule 8 says yield rather "
            "than compete. Re-run when it has finished."
        )
        raise typer.Exit(code=3)

    warm = _ollama_resident()
    if warm:
        typer.echo(f"ollama already holds {warm}; this will be a warm start, not a cold one")

    report: dict[str, Any] = {
        "vram_at_rest_mib": at_rest,
        "vram_total_mib": at_rest + free,
        "decoding": dict(DECODING),
        "scope": "dev filings and synthetic prompts only; locked is not read before the freeze",
    }

    # Two measurements, because the first one alone is misleading. Short prompts returning
    # a dozen tokens finish in under a second, and reporting that as "generation latency"
    # would make G10 look settled by a workload the study never runs.
    report["generation_short"] = _measure_generation(PROMPTS[:generate], label="short prompts")
    report["generation"] = _measure_generation(
        _realistic_prompts(paths, count=3), label="retrieved context, 512-token answer"
    )
    if embed:
        report["embedding"] = _measure_embedding(paths, embed_sample)
    else:
        report["embedding"] = {
            "skipped": "torch is in the optional models extra and is not installed; "
            "pass --embed after `uv sync --extra models` (a ~2-3 GB download)"
        }

    peak = max(
        int(report["vram_at_rest_mib"]),
        int(report["generation"].get("vram_resident_mib", 0)),
        int(report.get("embedding", {}).get("vram_resident_mib", 0) or 0),
    )
    report["vram_peak_observed_mib"] = peak
    report["vram_peak_observed_gb"] = round(peak / 1024, 2)
    report["g10_vram_limit_gb"] = 22.0
    report["vram_headroom_gb"] = round(22.0 - peak / 1024, 2)

    destination = paths.runs / "resource_budget.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    typer.echo("")
    typer.echo(f"highest card usage observed: {peak} MiB ({peak / 1024:.2f} GiB)")
    typer.echo(f"G10 limit 22 GB -> headroom {report['vram_headroom_gb']:+.2f} GB")
    if not embed:
        typer.echo("")
        typer.echo("Note: this is the generation model alone. The retrieval models add to it,")
        typer.echo("so this figure is a floor for the simultaneous peak, not the peak itself.")
    typer.echo("")
    typer.echo(f"wrote: {destination.relative_to(paths.root)}")


def _realistic_prompts(paths: Any, *, count: int) -> tuple[str, ...]:
    """Prompts shaped like the real workload: retrieved context in, a long answer out.

    The locked run puts several retrieved chunks in front of a question and allows 512
    output tokens. A one-line prompt returning ten tokens measures the floor of the model's
    latency, not the study's, and G10 is judged on the study's.

    Built from a dev filing. Latency does not depend on which company the text is about.
    """
    from twfi.io.manifest import load_acquisition_lock
    from twfi.parsing.baseline import chunk_fixed, parse_baseline

    lock = load_acquisition_lock(paths.acquisition_lock)
    acquired = lock.get("1301-FY2023-AR")
    if acquired is None or not acquired.local_path(paths.root).is_file():
        return PROMPTS[:count]
    parsed = parse_baseline(acquired.local_path(paths.root), "1301-FY2023-AR")
    # Nine 800-character chunks is about 3,600 CJK tokens, the order of magnitude a
    # top-k retrieval hands to the generator.
    context = "\n\n---\n\n".join(chunk.text for chunk in chunk_fixed(parsed)[:9])
    question = (
        "以下是年報片段。請根據片段詳細說明台塑民國112年度的財務狀況變化，"
        "逐項列出你能找到的數字並註明來源片段。\n\n"
    )
    return tuple([question + context] * count)


def _measure_generation(prompts: tuple[str, ...], *, label: str) -> dict[str, Any]:
    """Time the generation model, separating the cold call from the warm ones."""
    typer.echo(f"timing {GENERATION_MODEL} over {len(prompts)} prompts ({label}) …")
    latencies: list[float] = []
    prompt_tokens: list[int] = []
    output_tokens: list[int] = []
    resident = 0
    with httpx.Client(base_url=OLLAMA_URL, timeout=600.0) as client:
        for index, prompt in enumerate(prompts, start=1):
            started = time.perf_counter()
            try:
                response = client.post(
                    "/api/generate",
                    json={
                        "model": GENERATION_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        "think": False,
                        "options": DECODING,
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                typer.echo(f"  prompt {index}: FAILED ({exc})")
                continue
            elapsed = time.perf_counter() - started
            latencies.append(elapsed)
            used, _free = _vram_mib()
            resident = max(resident, used)
            payload = response.json()
            # Both counts are recorded: 12 s for 512 output tokens and 12 s for 20 mean
            # different things, and a latency without them cannot be compared to anything.
            prompt_tokens.append(int(payload.get("prompt_eval_count") or 0))
            output_tokens.append(int(payload.get("eval_count") or 0))
            # `phase`, not `label` -- the parameter names the whole measurement and was
            # being shadowed here.
            phase = "cold" if index == 1 else "warm"
            typer.echo(
                f"  prompt {index} ({phase}): {elapsed:.1f}s, in {prompt_tokens[-1]} tok, "
                f"out {output_tokens[-1]} tok, card {used} MiB"
            )

    if not latencies:
        return {"model": GENERATION_MODEL, "n": 0, "label": label, "error": "every request failed"}

    cold = latencies[0]
    warm = latencies[1:]
    return {
        "model": GENERATION_MODEL,
        "label": label,
        "n": len(latencies),
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "cold_seconds": round(cold, 2),
        "warm_seconds": [round(value, 2) for value in warm],
        "warm_median_seconds": round(statistics.median(warm), 2) if warm else None,
        "warm_max_seconds": round(max(warm), 2) if warm else None,
        "vram_resident_mib": resident,
        # With a handful of samples a percentile is just an order statistic wearing a
        # percentile's name. The max is reported instead, and G10's p95 must come from the
        # locked run's own latencies.
        "note": "n too small for a p95; max reported. G10's p95 comes from the locked run.",
    }


def _measure_embedding(paths: Any, sample: int) -> dict[str, Any]:
    """Time bge-m3 over real dev chunks. Only called with --embed."""
    try:
        import torch
        from transformers import (
            AutoModel,
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )
    except ImportError as exc:
        return {"skipped": f"models extra not installed: {exc}"}

    from twfi.io.manifest import load_acquisition_lock
    from twfi.parsing.baseline import chunk_fixed, parse_baseline

    lock = load_acquisition_lock(paths.acquisition_lock)
    texts: list[str] = []
    for doc_id in ("2412-FY2023-AR", "1301-FY2023-AR"):
        acquired = lock.get(doc_id)
        if acquired is None or not acquired.local_path(paths.root).is_file():
            continue
        for chunk in chunk_fixed(parse_baseline(acquired.local_path(paths.root), doc_id)):
            texts.append(chunk.text)
            if len(texts) >= sample:
                break
        if len(texts) >= sample:
            break

    typer.echo(f"loading {EMBEDDING_MODEL} and embedding {len(texts)} dev chunks …")
    tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL)
    model = AutoModel.from_pretrained(EMBEDDING_MODEL, torch_dtype=torch.float16).to("cuda").eval()

    batch = 16
    started = time.perf_counter()
    with torch.inference_mode():
        for index in range(0, len(texts), batch):
            encoded = tokenizer(
                texts[index : index + batch],
                padding=True,
                truncation=True,
                max_length=1024,
                return_tensors="pt",
            ).to("cuda")
            model(**encoded)
    elapsed = time.perf_counter() - started
    embedding_only = _vram_mib()[0]

    # The reranker is loaded *without* releasing the embedder, and while ollama still holds
    # the generation model. That simultaneous figure is what G10's 22 GB applies to, and it
    # is the number R3 could only estimate. Loading them one at a time and taking the max
    # would have reported a peak the pipeline never actually reaches -- flattering and wrong.
    typer.echo(f"loading {RERANKER_MODEL} alongside it …")
    reranker = (
        AutoModelForSequenceClassification.from_pretrained(
            RERANKER_MODEL, torch_dtype=torch.float16
        )
        .to("cuda")
        .eval()
    )
    reranker_tokenizer = AutoTokenizer.from_pretrained(RERANKER_MODEL)
    pairs = [(texts[0][:400], text[:400]) for text in texts[:16]]
    rerank_started = time.perf_counter()
    with torch.inference_mode():
        encoded = reranker_tokenizer(
            [left for left, _ in pairs],
            [right for _, right in pairs],
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        ).to("cuda")
        reranker(**encoded)
    rerank_seconds = time.perf_counter() - rerank_started
    all_resident = _vram_mib()[0]

    del model, reranker
    torch.cuda.empty_cache()

    per_second = len(texts) / elapsed if elapsed else 0.0
    typer.echo(f"  {len(texts)} chunks in {elapsed:.1f}s = {per_second:.0f}/s")
    typer.echo(f"  embedder resident: {embedding_only} MiB")
    typer.echo(f"  all three resident: {all_resident} MiB ({all_resident / 1024:.2f} GiB)")
    return {
        "model": EMBEDDING_MODEL,
        "reranker": RERANKER_MODEL,
        "chunks": len(texts),
        "seconds": round(elapsed, 2),
        "chunks_per_second": round(per_second, 1),
        "rerank_seconds_16_pairs": round(rerank_seconds, 3),
        "batch_size": batch,
        "vram_with_embedder_mib": embedding_only,
        "vram_resident_mib": all_resident,
        "vram_all_three_gb": round(all_resident / 1024, 2),
        "corpus_chunks_total": CORPUS_CHUNKS,
        "projected_full_index_minutes": (
            round(CORPUS_CHUNKS / per_second / 60, 1) if per_second else None
        ),
    }


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
