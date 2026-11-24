"""Measure how fast the chosen Ollama model reads and writes, and estimate time per paper.

One call to `/api/generate` with a built-in prompt of about 3,000 tokens: Ollama reports
how long it spent reading the prompt and writing the answer (in nanoseconds), which gives
tokens per second for each. Nothing is downloaded and the model is unloaded afterwards.
"""

import json
import sqlite3
import statistics
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from pydantic import BaseModel

from research_pipeline.llm import LlmError
from research_pipeline.ollama_fit import _read_cache, _write_cache

BENCH_NAME = "bench.json"
BENCH_DEADLINE = 300.0
BENCH_PROMPT_TOKENS = 3000
BENCH_OUTPUT_TOKENS = 200
PAPER_OUTPUT_TOKENS = 2500
DEFAULT_PAPER_TOKENS = 22000
MIN_TEXTS = 5

_PARAGRAPH = (
    "Large language models read text as tokens, small pieces of words. A nightly pipeline "
    "collects new papers, ranks them against a reader's interests and writes a short "
    "summary and critique of each one. The speed of that last step depends on how fast the "
    "computer can read the paper and how fast it can write the answer. "
)


class Bench(BaseModel):
    model: str
    num_ctx: int
    read_tps: float
    write_tps: float
    seconds_per_paper: float
    paper_tokens: int
    measured_at: datetime


def bench_prompt(nonce: str) -> str:
    """About BENCH_PROMPT_TOKENS tokens of text. The nonce comes first so Ollama's prefix
    cache cannot reuse the reading work of an earlier run (that would measure nothing)."""
    reps = BENCH_PROMPT_TOKENS * 4 // len(_PARAGRAPH) + 1
    return f"{nonce}\n\n" + _PARAGRAPH * reps + "\nSummarise the text above in detail."


def estimate_seconds(read_tps: float, write_tps: float, paper_tokens: int) -> float:
    """Read the paper once (the critique reuses the cached paper), then write the answers."""
    return paper_tokens / read_tps + PAPER_OUTPUT_TOKENS / write_tps


def run_bench(url: str, model: str, num_ctx: int, paper_tokens: int,
              post=httpx.post, now=datetime.now) -> Bench:
    try:
        resp = post(
            f"{url}/api/generate",
            json={"model": model, "prompt": bench_prompt(uuid.uuid4().hex), "stream": False,
                  "keep_alive": 0,
                  "options": {"num_ctx": num_ctx, "num_predict": BENCH_OUTPUT_TOKENS,
                              "temperature": 0}},
            timeout=BENCH_DEADLINE,
        )
    except httpx.ConnectError as e:
        raise LlmError(f"cannot reach Ollama at {url}: start it with `ollama serve` or set the "
                       "address with `rpipe model set-url`") from e
    except httpx.HTTPError as e:
        raise LlmError(f"the benchmark did not finish within {BENCH_DEADLINE:g} seconds or failed: "
                       f"{e or type(e).__name__}") from e
    if resp.status_code >= 400:
        raise LlmError(f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
        vals = [data[k] for k in ("prompt_eval_count", "prompt_eval_duration",
                                  "eval_count", "eval_duration")]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in vals):
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise LlmError("Ollama did not report timing (prompt_eval/eval counts and durations), "
                       "so speed cannot be measured; update Ollama and try again.") from e
    read_n, read_ns, write_n, write_ns = vals
    read_tps = read_n / (read_ns / 1e9)
    write_tps = write_n / (write_ns / 1e9)
    return Bench(model=model, num_ctx=num_ctx, read_tps=read_tps, write_tps=write_tps,
                 seconds_per_paper=estimate_seconds(read_tps, write_tps, paper_tokens),
                 paper_tokens=paper_tokens, measured_at=now())


def median_paper_tokens(conn: sqlite3.Connection) -> int:
    """Median size (characters / 4) of the stored full texts; a default if fewer than 5."""
    sizes = []
    for row in conn.execute("SELECT sections FROM fulltexts"):
        try:
            sizes.append(sum(len(s["text"]) for s in json.loads(row["sections"])) // 4)
        except (ValueError, KeyError, TypeError):
            continue
    if len(sizes) < MIN_TEXTS:
        return DEFAULT_PAPER_TOKENS
    return int(statistics.median(sizes))


def load_benches(vault: Path) -> dict[str, Bench]:
    out: dict[str, Bench] = {}
    for model, d in _read_cache(vault / "cache" / BENCH_NAME).items():
        try:
            out[model] = Bench(**d)
        except (ValueError, TypeError):
            continue
    return out


def save_bench(vault: Path, bench: Bench) -> None:
    data = {m: b.model_dump(mode="json") for m, b in load_benches(vault).items()}
    data[bench.model] = bench.model_dump(mode="json")
    _write_cache(vault / "cache" / BENCH_NAME, data)
