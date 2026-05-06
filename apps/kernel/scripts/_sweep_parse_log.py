"""Parse a nl_gen_smoketest JSONL log and emit one summary table row.

Called by run_ollama_sweep.sh and run_lmstudio_sweep.sh via:
    MODEL="..." RC="..." LOG="..." python3 scripts/_sweep_parse_log.py

Outputs a single Markdown table row to stdout.
"""

from __future__ import annotations

import json
import os
import pathlib

model = os.environ["MODEL"]
rc = os.environ["RC"]
log_path = os.environ["LOG"]

rows: dict[str, dict] = {}
for line in pathlib.Path(log_path).read_text().splitlines():
    if not line.startswith("{"):
        continue
    try:
        d = json.loads(line)
    except Exception:
        continue
    if "workflow_id" in d:
        rows[d["workflow_id"]] = d


def cell(wf: str) -> str:
    d = rows.get(wf)
    if not d:
        return "—"
    val = d.get("value")
    met = "✅" if d.get("meets_target") else "❌"
    return f"{val:.2f} {met}" if val is not None else f"? {met}"


wall = sum(d.get("wall_seconds", 0) for d in rows.values())
print(
    f"| {model} | "
    + cell("demand-prediction") + " | "
    + cell("credit-risk") + " | "
    + cell("contract-review") + " | "
    + f"{wall:.1f}s | {rc} |"
)
