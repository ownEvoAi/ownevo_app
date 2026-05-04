"""sweep_probes — drive probe_tool_calling + probe_skill_quality across many models.

Run both probes against a list of model identifiers (Ollama and / or LMS),
one model at a time, capturing structured results to JSONL. The sweep
exists because the local-model-testing.md candidate lists (~33 untested
models) would take 5+ hours of full Phase 1 runs to triage manually;
probes do it in 2-4 hours.

Input list format (line-oriented; lines starting with `#` are comments):
    ollama qwen3:8b
    ollama llama3.1:8b
    lms qwen/qwen3-coder-30b
    lms google/gemma-4-26b-a4b

For each row the driver:
  1. Checks the model is loaded / available on the configured host
     (Ollama: /api/tags ; LMS: /api/v0/models). Skipped with a clear
     reason if missing — does NOT auto-pull / auto-download (multi-GB).
  2. Invokes probe_tool_calling.py as a subprocess; captures the JSON
     line from stdout + the exit code (0=pass, 1=fail-no-tool, 2=error).
  3. If tool-call probe passed, invokes probe_skill_quality.py and
     captures the same shape.
  4. Appends a JSONL row to sweep.jsonl in the run directory.

Resumable: pass --skip-completed and the driver reads existing
sweep.jsonl, skips any (backend, model) already recorded as a
non-error result. Re-runs error rows on resume so transient failures
self-heal.

Limitations (read these before drawing conclusions from output):
  * F4 from local-model-testing.md — passing both probes is necessary
    but not sufficient. 8B models pass simple probes but stall in the
    M5 multi-turn read-loop. The sweep gives probe-passers, not
    Phase-1-viable models.
  * LMS REST unload doesn't work (BL3 known gaps). During the LMS
    portion of the sweep VRAM may not strictly respect "one model
    loaded at a time"; LRU eviction is best-effort. Empirical signal:
    cache_read=0 + slow elapsed_s when LMS is thrashing.
  * Ollama keep_alive=0 is requested but the daemon-side default
    OLLAMA_KEEP_ALIVE may override per the operator's restart config.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ENV_LLM_HOST = "OWNEVO_LLM_HOST"
ENV_LLM_API_KEY = "OWNEVO_LLM_API_KEY"

_DEFAULT_LLM_HOST = "localhost"
_PROBE_DIR = Path(__file__).resolve().parent
_PROBE_TOOL_CALLING = _PROBE_DIR / "probe_tool_calling.py"
_PROBE_SKILL_QUALITY = _PROBE_DIR / "probe_skill_quality.py"
_AVAILABILITY_TIMEOUT_S = 5.0
_TOOL_CALLING_TIMEOUT_S = 120.0
"""Per-model wall budget for the tool-calling probe. The probe itself
caps at 60s; the extra 60s absorbs cold model load (which Ollama and
LMS both do lazily on first call)."""
_SKILL_QUALITY_TIMEOUT_S = 240.0
"""Per-model wall budget for the skill-quality probe. Generation can
run 30-90s on a slow CPU offload; cap at 4min so a single hung model
doesn't gate the whole sweep."""


@dataclass
class ModelEntry:
    backend: str  # "ollama" | "lms"
    model: str
    line_no: int  # for diagnostic messages

    @property
    def key(self) -> tuple[str, str]:
        return (self.backend, self.model)


@dataclass
class SweepRow:
    backend: str
    model: str
    timestamp: str
    available: bool
    skipped_reason: str | None = None
    tool_calling: dict[str, Any] | None = None
    skill_quality: dict[str, Any] | None = None
    overall: str = "skipped"
    """One of: 'pass' (both probes pass), 'tool-call-only' (tool-calling
    pass, skill-quality fail), 'fail' (tool-calling fails), 'error'
    (transport / probe crash), 'skipped' (model unavailable)."""
    elapsed_s: float = 0.0
    notes: list[str] = field(default_factory=list)


def parse_models_file(path: Path) -> list[ModelEntry]:
    out: list[ModelEntry] = []
    with path.open() as f:
        for i, raw in enumerate(f, start=1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) != 2 or parts[0] not in {"ollama", "lms"}:
                raise SystemExit(
                    f"{path}:{i}: expected '<backend> <model>' (backend in {{ollama, lms}})"
                )
            out.append(ModelEntry(backend=parts[0], model=parts[1], line_no=i))
    return out


async def _ollama_available(host: str, model: str) -> tuple[bool, str | None]:
    """Return (available, reason_if_not). Hits /api/tags and matches `name`."""
    url = f"http://{host}:11434/api/tags"
    async with httpx.AsyncClient(timeout=_AVAILABILITY_TIMEOUT_S) as client:
        try:
            r = await client.get(url)
        except httpx.HTTPError as e:
            return False, f"ollama-unreachable: {type(e).__name__}: {e}"
    if r.status_code != 200:
        return False, f"ollama HTTP {r.status_code}"
    names = {m.get("name") for m in r.json().get("models", [])}
    if model not in names:
        return False, f"not-pulled (run `ollama pull {model}` to add)"
    return True, None


async def _lms_available(host: str, model: str) -> tuple[bool, str | None]:
    """Return (available, reason_if_not). LMS exposes loaded + downloaded
    models at /api/v0/models with a `state` field — we accept any
    listed model, since LMS lazy-loads on first chat call."""
    url = f"http://{host}:1234/api/v0/models"
    async with httpx.AsyncClient(timeout=_AVAILABILITY_TIMEOUT_S) as client:
        try:
            r = await client.get(url)
        except httpx.HTTPError as e:
            return False, f"lms-unreachable: {type(e).__name__}: {e}"
    if r.status_code != 200:
        return False, f"lms HTTP {r.status_code}"
    ids = {m.get("id") for m in r.json().get("data", [])}
    if model not in ids:
        return False, "not in LMS catalog (download via LMS GUI on the host)"
    return True, None


async def _ollama_unload(host: str, model: str) -> None:
    """Best-effort: send a chat call with keep_alive=0 to evict.
    Returns silently on any error — the sweep continues regardless."""
    url = f"http://{host}:11434/api/chat"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "."}],
        "stream": False,
        "keep_alive": 0,
        "options": {"num_predict": 1},
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            await client.post(url, json=payload)
        except httpx.HTTPError:
            pass


def _run_probe(probe_path: Path, args: list[str], timeout_s: float) -> dict[str, Any]:
    """Run a probe subprocess; return the parsed JSON line + meta.

    Captures stdout (probes emit a single JSON line) and stderr (only
    used for the error path). Returns a dict with the probe's keys
    plus `_exit_code` + `_stderr_tail` so callers can classify pass /
    fail / error consistently."""
    import subprocess

    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(probe_path), *args],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {
            "result": "error",
            "error": f"sweep-driver timeout after {timeout_s:.0f}s",
            "_exit_code": -1,
            "_stderr_tail": "",
            "_wall_s": round(time.time() - started, 1),
        }
    out: dict[str, Any] = {}
    last_json_line = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            last_json_line = json.loads(line)
        except json.JSONDecodeError:
            continue
    if last_json_line is not None:
        out.update(last_json_line)
    if "result" not in out:
        out["result"] = "error"
        out["error"] = (
            f"probe emitted no JSON; exit={proc.returncode}; "
            f"stderr_tail={proc.stderr[-400:]!r}"
        )
    out["_exit_code"] = proc.returncode
    out["_stderr_tail"] = proc.stderr[-400:] if proc.returncode != 0 else ""
    out["_wall_s"] = round(time.time() - started, 1)
    return out


def _resolve_base_url(backend: str, host: str) -> str:
    if backend == "ollama":
        return f"http://{host}:11434/v1"
    return f"http://{host}:1234"


async def sweep_one(
    entry: ModelEntry,
    host: str,
    api_key: str,
    ollama_num_ctx: int,
) -> SweepRow:
    """Run both probes against one model, returning a SweepRow.

    Sequencing: availability → tool-calling → skill-quality →
    Ollama unload (best-effort, for memory hygiene). LMS unload is
    not attempted (REST endpoints don't free VRAM as of 2026-05-04).
    """
    started = time.time()
    iso_now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
    row = SweepRow(
        backend=entry.backend,
        model=entry.model,
        timestamp=iso_now,
        available=False,
    )

    # 1. Availability
    if entry.backend == "ollama":
        available, reason = await _ollama_available(host, entry.model)
    else:
        available, reason = await _lms_available(host, entry.model)
    if not available:
        row.skipped_reason = reason
        row.elapsed_s = round(time.time() - started, 1)
        return row
    row.available = True

    # 2. Tool-calling probe
    api_format = "openai" if entry.backend == "ollama" else "anthropic"
    base_url = _resolve_base_url(entry.backend, host)
    tc_args = [
        "--api-format", api_format,
        "--llm-model", entry.model,
        "--llm-base-url", base_url,
        "--llm-api-key", api_key,
    ]
    if entry.backend == "ollama":
        tc_args += ["--ollama-num-ctx", str(ollama_num_ctx)]
    tc = _run_probe(_PROBE_TOOL_CALLING, tc_args, _TOOL_CALLING_TIMEOUT_S)
    row.tool_calling = tc

    if tc.get("result") != "pass":
        # Skip skill-quality if the model can't even tool-call.
        if tc.get("result") == "error":
            row.overall = "error"
        else:
            row.overall = "fail"
        row.elapsed_s = round(time.time() - started, 1)
        if entry.backend == "ollama":
            await _ollama_unload(host, entry.model)
        return row

    # 3. Skill-quality probe
    sq_args = [
        "--api-format", api_format,
        "--llm-model", entry.model,
        "--llm-base-url", base_url,
        "--llm-api-key", api_key,
    ]
    if entry.backend == "ollama":
        sq_args += ["--ollama-num-ctx", str(ollama_num_ctx)]
    sq = _run_probe(_PROBE_SKILL_QUALITY, sq_args, _SKILL_QUALITY_TIMEOUT_S)
    row.skill_quality = sq

    if sq.get("result") == "pass":
        row.overall = "pass"
    elif sq.get("result") == "error":
        row.overall = "error"
    else:
        row.overall = "tool-call-only"

    row.elapsed_s = round(time.time() - started, 1)
    if entry.backend == "ollama":
        await _ollama_unload(host, entry.model)
    return row


def _load_completed(jsonl_path: Path) -> set[tuple[str, str]]:
    """Return the set of (backend, model) pairs already recorded with a
    non-error overall in the JSONL. Used by --skip-completed to avoid
    redoing successful runs after Ctrl+C."""
    if not jsonl_path.exists():
        return set()
    done: set[tuple[str, str]] = set()
    for line in jsonl_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("overall") == "error":
            continue  # let errors retry on resume
        done.add((row.get("backend"), row.get("model")))
    return done


def _summary_table(rows: list[SweepRow]) -> str:
    """Markdown table summarizing pass/fail per backend, ordered by
    (backend, overall, elapsed_s) for readability."""
    headers = [
        "Backend", "Model", "Overall",
        "TC result", "TC elapsed", "TC errors",
        "SQ result", "SQ elapsed", "SQ issues",
        "Total wall",
    ]
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    rows_sorted = sorted(rows, key=lambda r: (r.backend, r.overall, r.elapsed_s))
    for r in rows_sorted:
        tc = r.tool_calling or {}
        sq = r.skill_quality or {}
        tc_issues = ""  # tool_calling probe has no `issues` field
        sq_issues = ", ".join(sq.get("issues", [])[:2]) if sq else ""
        cells = [
            r.backend,
            f"`{r.model}`",
            r.overall + (f" ({r.skipped_reason})" if r.overall == "skipped" else ""),
            tc.get("result", ""),
            f"{tc.get('elapsed_s', '')}",
            tc.get("error", "") if tc.get("result") == "error" else "",
            sq.get("result", ""),
            f"{sq.get('elapsed_s', '')}",
            sq_issues,
            f"{r.elapsed_s}s",
        ]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sweep_probes", description=__doc__)
    p.add_argument(
        "--models",
        type=Path,
        required=True,
        help="Path to model list (one '<backend> <model>' per line).",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Directory to write sweep.jsonl + sweep.md to. Created if missing.",
    )
    p.add_argument(
        "--llm-host",
        default=os.environ.get(ENV_LLM_HOST, _DEFAULT_LLM_HOST),
        help=f"Host for both Ollama (:11434) and LMS (:1234). Default: ${ENV_LLM_HOST} or '{_DEFAULT_LLM_HOST}'.",
    )
    p.add_argument(
        "--llm-api-key",
        default=os.environ.get(ENV_LLM_API_KEY, "lm-studio"),
        help="API key forwarded to the probe subprocess. Default: 'lm-studio'.",
    )
    p.add_argument(
        "--ollama-num-ctx",
        type=int,
        default=65536,
        help="Forwarded to ollama via probes' --ollama-num-ctx. Default 65536 per F1.",
    )
    p.add_argument(
        "--skip-completed",
        action="store_true",
        help="Skip (backend, model) pairs already recorded with a non-error overall in <out-dir>/sweep.jsonl.",
    )
    return p.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = args.out_dir / "sweep.jsonl"
    md_path = args.out_dir / "sweep.md"

    entries = parse_models_file(args.models)
    completed: set[tuple[str, str]] = (
        _load_completed(jsonl_path) if args.skip_completed else set()
    )
    if completed:
        print(f"sweep: skipping {len(completed)} completed rows (--skip-completed).", file=sys.stderr)

    rows: list[SweepRow] = []
    # Re-load any prior rows so the final summary covers the full sweep,
    # not just this resume's increment.
    if jsonl_path.exists():
        for line in jsonl_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append(SweepRow(
                backend=d.get("backend", ""),
                model=d.get("model", ""),
                timestamp=d.get("timestamp", ""),
                available=d.get("available", False),
                skipped_reason=d.get("skipped_reason"),
                tool_calling=d.get("tool_calling"),
                skill_quality=d.get("skill_quality"),
                overall=d.get("overall", "skipped"),
                elapsed_s=d.get("elapsed_s", 0.0),
                notes=d.get("notes", []),
            ))

    new_rows: list[SweepRow] = []
    total = len(entries)
    for idx, entry in enumerate(entries, start=1):
        if entry.key in completed:
            print(f"[{idx}/{total}] {entry.backend} {entry.model} — already done, skipping.", file=sys.stderr)
            continue
        print(f"[{idx}/{total}] {entry.backend} {entry.model} — running probes...", file=sys.stderr)
        row = await sweep_one(
            entry,
            host=args.llm_host,
            api_key=args.llm_api_key,
            ollama_num_ctx=args.ollama_num_ctx,
        )
        rows.append(row)
        new_rows.append(row)
        # Append the new row to JSONL atomically (one row per line).
        with jsonl_path.open("a") as f:
            f.write(json.dumps(_row_to_dict(row)) + "\n")
        print(
            f"        → overall={row.overall} elapsed={row.elapsed_s}s",
            file=sys.stderr,
        )

    md_path.write_text(_summary_table(rows) + "\n", encoding="utf-8")
    print(f"\nsweep: wrote {len(new_rows)} new rows to {jsonl_path}", file=sys.stderr)
    print(f"sweep: full summary in {md_path}", file=sys.stderr)
    return 0


def _row_to_dict(row: SweepRow) -> dict[str, Any]:
    return {
        "backend": row.backend,
        "model": row.model,
        "timestamp": row.timestamp,
        "available": row.available,
        "skipped_reason": row.skipped_reason,
        "tool_calling": row.tool_calling,
        "skill_quality": row.skill_quality,
        "overall": row.overall,
        "elapsed_s": row.elapsed_s,
        "notes": row.notes,
    }


def main() -> int:
    return asyncio.run(main_async(parse_args(sys.argv[1:])))


if __name__ == "__main__":
    raise SystemExit(main())
