"""τ³-bench failure analyzer (P1.5 / M7).

Inputs:
  - Path to a tau2 run's ``results.json`` (auto-saved by tau2.run_domain
    under ``DATA_DIR/simulations/<run>/results.json``). The runner
    surfaces this path on ``SandboxedTauBenchRunner.last_raw_run_dir``
    + ``BenchmarkResult.raw_run_dir`` in the M3 entrypoint payload.

Output:
  - Ranked list of ``Tau3FailureSnapshot`` records, worst-first, each
    carrying the conversation features the proposer / clusterer need:
      * `task_id`, `domain`, `split`, `reward`
      * `termination_reason` (user_stop / max_steps / infrastructure_error)
      * `n_messages`, `agent_cost`, `user_cost`, `duration_s`
      * `tool_calls_tail` — last 3 tool calls' names + truncated args
      * `last_user_request` and `last_assistant_text` — short snippets
        for context (≤200 chars each)
      * `failure_hints` — short tags ("user-gave-up", "max-steps",
        "infra-error", "wrong-write", "no-writes-attempted")
      * `text_signature` — one-line ≤200-char embedding input for
        clustering, mirroring the M5 analyzer's shape

Pure-stdlib (json + dataclasses + re). No tau2 import — kernel-side
stays free of tau2's heavy dep tree (litellm, openai>=2). Reads the
JSON tau2 wrote in the sandbox; treats it as untrusted/version-tolerant
data (defaults on missing fields, never raises on extra fields).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Reward threshold that defines "failure" for the purposes of this
# analyzer. tau-bench scores 0.0 or 1.0 per task (binary completion);
# a reward of None (infra error) is also a failure, but distinct in
# its hint shape.
FAILURE_REWARD_THRESHOLD = 0.5


@dataclass(frozen=True)
class Tau3FailureSnapshot:
    """One failed tau-bench simulation with structured context for clustering.

    ``text_signature`` is the single-line embedding input for the
    clustering pipeline (B3.2 reused for τ³). Format::

        "task=<id> dom=<domain> term=<reason> rew=<x.xx>
         msgs=<n> tools=<a,b,c> hints=[<tag>,<tag>]"
    """

    task_id: str
    domain: str
    split: str
    reward: float | None
    termination_reason: str
    n_messages: int
    agent_cost: float
    user_cost: float
    duration_s: float
    tool_calls_tail: tuple[dict[str, Any], ...]
    last_user_request: str
    last_assistant_text: str
    failure_hints: tuple[str, ...] = field(default_factory=tuple)
    text_signature: str = ""


class Tau3FailureAnalyzerError(ValueError):
    """The supplied ``results.json`` is missing required keys or shapes.

    The analyzer is forgiving on extras (tau2 may add fields across
    versions) but strict on the keys it actually consumes. Errors here
    indicate a bug or a tau2 schema break — they should never fire on
    a results.json the M3 runner produced.
    """


def analyze_tau3_failures(
    results_json_path: Path,
    *,
    top_k: int | None = None,
    domain_hint: str | None = None,
    split_hint: str | None = None,
    threshold: float = FAILURE_REWARD_THRESHOLD,
) -> list[Tau3FailureSnapshot]:
    """Return ranked failures from one tau2 run's results.json.

    Sort order: infra errors (reward is None) first, then ascending
    reward, then descending duration_s. Ties broken by task_id for
    determinism. ``top_k`` truncates the returned list (None = all
    failures returned).

    ``domain_hint`` / ``split_hint`` override the values pulled from
    the JSON ``info`` block, useful when a caller already knows what
    the runner used and wants to be defensive about tau2 omitting
    them. The runner's M3 entrypoint always runs against a single
    (domain, split) pair so a hint is unambiguous.
    """
    path = Path(results_json_path)
    if not path.is_file():
        raise Tau3FailureAnalyzerError(
            f"results.json not found at {path}",
        )

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise Tau3FailureAnalyzerError(
            f"could not parse {path}: {exc}",
        ) from exc

    sims = data.get("simulations")
    if not isinstance(sims, list):
        raise Tau3FailureAnalyzerError(
            "results.json: missing 'simulations' list",
        )

    info = data.get("info") or {}
    env_info = info.get("environment_info") or {}
    domain = (
        domain_hint
        or env_info.get("domain_name")
        or info.get("domain")
        or info.get("config", {}).get("domain")
        or "unknown"
    )
    # tau2's results.json doesn't carry the train/test split name in
    # `info` (verified on a real auto-harness run, 2026-05-08). Caller
    # is the canonical source — pass `split_hint=`. Fallback "unknown"
    # is non-blocking; the analyzer's other fields are still valid.
    split = (
        split_hint
        or info.get("task_split_name")
        or info.get("config", {}).get("task_split_name")
        or "unknown"
    )

    failures: list[Tau3FailureSnapshot] = []
    for sim in sims:
        if not isinstance(sim, dict):
            continue
        reward_info = sim.get("reward_info")
        if reward_info is None:
            reward: float | None = None
        else:
            try:
                reward = float(reward_info.get("reward", 0.0))
            except (TypeError, ValueError):
                reward = None

        if reward is not None and reward >= threshold:
            continue

        failures.append(_snapshot_from_sim(sim, domain, split, reward))

    failures.sort(
        key=lambda s: (
            0 if s.reward is None else 1,                # infra errors first
            s.reward if s.reward is not None else -1.0,  # then ascending reward
            -s.duration_s,                               # then longest duration
            s.task_id,                                   # then deterministic
        ),
    )

    if top_k is not None:
        failures = failures[:top_k]
    return failures


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_TAIL_TOOL_CALLS = 3
_MAX_LAST_TEXT = 200
_MAX_TEXT_SIGNATURE = 220


def _snapshot_from_sim(
    sim: dict[str, Any],
    domain: str,
    split: str,
    reward: float | None,
) -> Tau3FailureSnapshot:
    task_id = str(sim.get("task_id", "?"))
    termination = str(sim.get("termination_reason", "unknown"))
    messages = sim.get("messages") or []
    n_messages = len(messages) if isinstance(messages, list) else 0
    duration_s = float(sim.get("duration", 0.0) or 0.0)
    agent_cost = float(sim.get("agent_cost", 0.0) or 0.0)
    user_cost = float(sim.get("user_cost", 0.0) or 0.0)

    tool_calls_tail = _extract_tool_calls_tail(messages)
    last_user_request = _extract_first_user_request(messages)
    last_assistant_text = _extract_last_assistant_text(messages)

    hints = _derive_hints(
        reward=reward,
        termination=termination,
        n_messages=n_messages,
        tool_calls_tail=tool_calls_tail,
    )

    text_signature = _build_text_signature(
        task_id=task_id,
        domain=domain,
        split=split,
        reward=reward,
        termination=termination,
        n_messages=n_messages,
        tool_calls_tail=tool_calls_tail,
        hints=hints,
    )

    return Tau3FailureSnapshot(
        task_id=task_id,
        domain=domain,
        split=split,
        reward=reward,
        termination_reason=termination,
        n_messages=n_messages,
        agent_cost=agent_cost,
        user_cost=user_cost,
        duration_s=duration_s,
        tool_calls_tail=tool_calls_tail,
        last_user_request=last_user_request,
        last_assistant_text=last_assistant_text,
        failure_hints=hints,
        text_signature=text_signature,
    )


def _extract_tool_calls_tail(
    messages: list[Any],
) -> tuple[dict[str, Any], ...]:
    """Last N assistant tool calls. Each entry is {name, arguments}.

    Arguments serialized to JSON string and truncated to 120 chars so
    the embedding input stays bounded. Order preserves chronological
    sequence (oldest of the tail first)."""
    tail: list[dict[str, Any]] = []
    if not isinstance(messages, list):
        return ()
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        tcs = msg.get("tool_calls") or []
        if not isinstance(tcs, list):
            continue
        for tc in tcs:
            if not isinstance(tc, dict):
                continue
            name = str(tc.get("name") or
                       (tc.get("function") or {}).get("name") or "?")
            args = tc.get("arguments") or (tc.get("function") or {}).get("arguments")
            if isinstance(args, str):
                args_str = args
            else:
                try:
                    args_str = json.dumps(args, default=str)
                except (TypeError, ValueError):
                    args_str = str(args)
            tail.append({"name": name, "args": args_str[:120]})
    return tuple(tail[-_TAIL_TOOL_CALLS:])


def _extract_first_user_request(messages: list[Any]) -> str:
    """The first user message — usually the task spec the agent must satisfy.

    Fallback: empty string if no user message is present (rare; tau-bench
    user simulator opens every conversation)."""
    if not isinstance(messages, list):
        return ""
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content") or ""
        if isinstance(content, str):
            return _truncate(content, _MAX_LAST_TEXT)
        return _truncate(str(content), _MAX_LAST_TEXT)
    return ""


def _extract_last_assistant_text(messages: list[Any]) -> str:
    """The last assistant message's textual content (if any).

    Some assistant turns are tool calls only with content=None; this
    skips those and walks back to the most recent text turn."""
    if not isinstance(messages, list):
        return ""
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return _truncate(content, _MAX_LAST_TEXT)
    return ""


def _derive_hints(
    *,
    reward: float | None,
    termination: str,
    n_messages: int,
    tool_calls_tail: tuple[dict[str, Any], ...],
) -> tuple[str, ...]:
    """Short tags derived from the failure pattern.

    These are descriptive (not prescriptive). The clusterer uses them
    as low-cardinality slicing dimensions; the proposer reads them in
    the cluster summary to decide what kind of fix to try.
    """
    hints: list[str] = []

    if reward is None or termination == "infrastructure_error":
        hints.append("infra-error")
        # No further hints — the run never produced an evaluable trajectory.
        return tuple(hints)

    if termination == "max_steps":
        hints.append("max-steps")
    elif termination == "user_stop":
        hints.append("user-gave-up")
    elif termination == "agent_stop":
        hints.append("agent-stopped-early")
    else:
        hints.append(f"term:{termination}")

    if not tool_calls_tail:
        hints.append("no-tool-calls")
    else:
        # Heuristic: did the agent ever attempt a write action? tau-bench
        # write tools share the verbs cancel / modify / exchange / return
        # / refund / update / set / send / book / create / delete.
        write_verbs = (
            "cancel", "modify", "exchange", "return", "refund",
            "update", "set", "send", "book", "create", "delete",
            "submit", "deactivate", "reactivate",
        )
        names = [str(tc.get("name", "")).lower() for tc in tool_calls_tail]
        if any(any(v in n for v in write_verbs) for n in names):
            hints.append("write-attempted")
        else:
            hints.append("no-writes-attempted")

    if n_messages >= 30:
        hints.append("long-conversation")
    elif n_messages <= 5:
        hints.append("short-conversation")

    return tuple(hints)


def _build_text_signature(
    *,
    task_id: str,
    domain: str,
    split: str,
    reward: float | None,
    termination: str,
    n_messages: int,
    tool_calls_tail: tuple[dict[str, Any], ...],
    hints: tuple[str, ...],
) -> str:
    rew_str = "null" if reward is None else f"{reward:.2f}"
    tools_str = ",".join(str(tc.get("name", "?")) for tc in tool_calls_tail) or "-"
    hints_str = ",".join(hints) or "-"
    sig = (
        f"task={task_id} dom={domain} split={split} term={termination} "
        f"rew={rew_str} msgs={n_messages} tools={tools_str} hints=[{hints_str}]"
    )
    return _truncate(sig, _MAX_TEXT_SIGNATURE)


def _truncate(s: str, n: int) -> str:
    s = s.replace("\n", " ").replace("\r", " ").strip()
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"
