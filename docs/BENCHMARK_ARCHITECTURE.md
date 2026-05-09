# Multi-Benchmark Substrate — Architecture

**Status:** design draft (2026-05-08)
**Scope:** sandboxed, multi-benchmark agent evaluation as durable ownEvo IP. Replaces the
auto-harness fork's role with native ownevo_kernel components. Currently informs τ³-bench
implementation; same shape applies to terminal-bench, BIRD-Interact, SWE-bench, claw-eval,
and post-MVP custom benchmarks (OpsAgent-Bench).

Companion docs:
- [`TAU3_LOCAL_TESTPLAN.md`](TAU3_LOCAL_TESTPLAN.md) — first benchmark using this substrate
- [`SCHEMA.md`](SCHEMA.md) — DB tables (iterations, failure_clusters, eval_cases, audit_entries, skills)
- [`SKILL_FORMAT.md`](SKILL_FORMAT.md) — skill bundle frontmatter contract
- [`STATE_MACHINES.md`](STATE_MACHINES.md) — proposal/iteration/workflow state transitions
- `apps/kernel/src/ownevo_kernel/sandbox/__init__.py` — `SandboxRuntime` Protocol

---

## Why this architecture

Three forces driving the shape:

1. **Multiple benchmarks, one substrate.** τ³-bench is first; terminal-bench / BIRD-Interact
   / SWE-bench Verified / claw-eval are queued. NeoSigma's auto-harness already supports 3
   (tau / terminal / bird) by wrapping each in a runner. We need the same extensibility,
   without the auto-harness's "edit a single file in a workspace" model — ownEvo's skill
   registry + DB-backed iterations are the right substrate.

2. **Sandboxed by default.** Two threat surfaces:
   - **Agent-written code** (the proposed new skill version) gets executed by the gate.
     Treat as untrusted — same threat model as M5's LightGBM code.
   - **Adversarial inputs** (user simulator messages, dataset content) reach the agent.
     A prompt-injected user message could try to escape via tool calls; tools that touch
     the host filesystem or arbitrary network endpoints are dangerous.

   M5 chose `--network=none` + read-only rootfs + cap-drop=ALL. tau³ + most LLM-driven
   benchmarks need network egress (cloud API), so we generalize the sandbox profile.

3. **Reuse existing ownEvo substrate.** The `iterations` / `failure_clusters` / `eval_cases`
   / `audit_entries` / `skills` tables, the `run_gate` / `persist_gate_run` flow, the W7
   web UI, and the regression-gate semantics are benchmark-agnostic by design. Adding a
   benchmark = adding a runner + a sandbox profile + a skill seed. Everything else reuses.

---

## Layered design

```
┌────────────────────────────────────────────────────────────────────────────┐
│  apps/web/  — workspace UI                                                  │
│  Workflow detail (tau3-retail-v1, terminal-bench-v1, m5-demand-prediction)  │
│  Lift chart + Failures + Audit + Skills + Traces tabs (already shipped W7)  │
└────────────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │  REST + SSE (already shipped W7)
                                  │
┌────────────────────────────────────────────────────────────────────────────┐
│  ownevo_kernel.api  — FastAPI                                               │
│   /api/workflows/{id}/iterations, /failure_clusters, /skills, /traces        │
└────────────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │
┌────────────────────────────────────────────────────────────────────────────┐
│  ownevo_kernel.gate  — run_gate (3-step: regression / improvement / error)  │
│  ownevo_kernel.audit  — append-only audit chain                              │
│  ownevo_kernel.evolution  — proposer / curator / reflector                   │
└────────────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │  uses the BenchmarkRunner Protocol
                                  │
┌────────────────────────────────────────────────────────────────────────────┐
│  ownevo_kernel.benchmarks/  — one package per benchmark                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐ ┌─────────────┐ ┌────────────┐ │
│  │ m5/      │ │ tau3/    │ │ terminal/    │ │ bird/       │ │ claw/      │ │
│  │ (exists) │ │ (P1.5)   │ │ (Phase 2)    │ │ (Phase 2)   │ │ (Phase 3)  │ │
│  └──────────┘ └──────────┘ └──────────────┘ └─────────────┘ └────────────┘ │
│                                                                             │
│  Each implements: BenchmarkRunner Protocol + a SandboxProfile              │
└────────────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │  uses the SandboxRuntime Protocol
                                  │
┌────────────────────────────────────────────────────────────────────────────┐
│  ownevo_kernel.sandbox  — SandboxRuntime + profiles                          │
│  LocalDockerSandbox (default)                                                │
│   ↳ M5 profile           — --network=none, m5 image                         │
│   ↳ Tau3 profile         — egress allowlist, tau3 image                     │
│   ↳ TerminalBench profile — e2b adapter / shell-tool image (Phase 2)        │
│  E2BSandbox (Phase 2 — TODO-2 retrofit)                                      │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## The two protocols

### `BenchmarkRunner` (already exists for M5; generalize)

```python
class BenchmarkRunner(Protocol):
    workflow_id: str

    def run(
        self,
        skill_override_dir: Path | None = None,
        task_ids: list[str] | None = None,
        split: Literal["train", "test", "all"] = "test",
    ) -> BenchmarkResult: ...

@dataclass(frozen=True)
class BenchmarkResult:
    val_score: float                       # mean reward (higher is better)
    per_task: dict[str, float | None]      # task_id -> reward
    error_class: ErrorClass | None         # OOM | Timeout | Crash | None
    raw_trace_dir: Path | None             # location of full traces (Meta-Harness ablation requires full)
    duration_s: float
    cost_usd: float                        # cumulative LLM cost across all calls
    metadata: dict[str, Any]               # benchmark-specific (n_tasks, model versions, etc.)
```

**Key contracts:**
- `skill_override_dir` accepts the agent-proposed skill bundle. If `None`, use the
  registered baseline. **Same shape as M5's existing path.**
- Returns `BenchmarkResult` — flat enough for `persist_gate_run` to consume directly.
- `raw_trace_dir` preserves **full message/tool-call history** (Meta-Harness ablation:
  scores-only 34.6% → +summaries 34.9% → full traces 50.0% in their loop's diagnostic step).

### `SandboxProfile` (new — extends `SandboxRuntime`)

```python
@dataclass(frozen=True)
class SandboxProfile:
    image: str                             # docker image tag
    network: NetworkPolicy                 # none | bridge | egress-allowlist
    mem_mb: int
    cpu_quota: float
    pids_limit: int
    timeout_s: int
    extra_env: dict[str, str] = field(default_factory=dict)
    extra_volumes: list[VolumeMount] = field(default_factory=list)

class NetworkPolicy(Enum):
    NONE = "none"                          # M5: no network at all
    BRIDGE = "bridge"                      # default Docker bridge (egress unrestricted)
    EGRESS_ALLOWLIST = "egress-allowlist"  # bridge + iptables-allow only specific hosts
```

**Per-benchmark profiles** (concrete settings):

| Profile | Image | Network | Mem | Timeout | Why |
|---|---|---|---|---|---|
| `m5` | `ownevo-sandbox-m5:0.1.0` | none | 1024 MB | 600s | Offline LightGBM training; no API |
| `tau3` | `ownevo-sandbox-tau3:0.1.0` | egress-allowlist (api.anthropic.com, api.openai.com, 192.168.1.50:11434) | 512 MB | 1800s | Cloud LLM API access; longer timeout for multi-turn conversations |
| `terminal_bench` | (delegate to e2b/Daytona per benchmark spec) | provider-default | per-task | per-task | benchmark already sandboxed by upstream |
| `bird_interact` | `ownevo-sandbox-bird:0.1.0` | bridge + Postgres on 6100/6101/6102 | 2048 MB | 1800s | needs PG client + ADK services |

**Defense-in-depth that stays constant across profiles:**
- Read-only rootfs + tmpfs `/tmp`
- `--cap-drop=ALL` (no Linux capabilities)
- No host volume mounts other than explicit `extra_volumes` (each must be `:ro` unless
  documented otherwise)
- Hard timeout (no graceful kill)
- `pids` limit to prevent fork bombs

---

## Skill bundles per benchmark

Skills live in the registry (`skills` table), versioned. Each benchmark's skill is a
file or directory tree with SKILL_FORMAT frontmatter (id, kind, schema_version,
retention contract).

| Benchmark | Skill shape | Loop edits |
|---|---|---|
| **M5** | 6-file Python package (data_loader.py, feature_engineer.py, model_trainer.py, predictor.py, ensemble.py, outlier_handler.py) | One file at a time per iteration |
| **τ³** | 1 Python file: `agent.py` containing `HarnessAgent` class + `AGENT_INSTRUCTION` system prompt | Whole file each iteration (small enough — ~50-300 lines) |
| **terminal-bench** | 1 Python file: similar `HarnessAgent` shape | Whole file each iteration |
| **BIRD-Interact** | Multi-file (HarnessAgent + helpers/ subdir) | Whole bundle each iteration |
| **SWE-bench** | TBD (likely 1 file: agent system prompt + tool dispatch) | Whole file |

The skill registry already supports multi-file via the bundle pattern; M5 uses it. No
schema changes needed.

---

## Failure analyzer per benchmark

Each benchmark produces sub-0.5-reward runs whose trace patterns get clustered. The
analyzer is benchmark-specific (different traces have different shapes), but the output
is uniform: `failure_clusters` rows with `text_signature` + `severity` + `dominant_hint`.

| Benchmark | Failure analyzer signature extraction |
|---|---|
| **M5** | top-k worst-predicted M5 series with peak-error day offset + signed value + hierarchy hint (existing `m5_failure_analyzer.py`) |
| **τ³** | per-failed-sim: termination reason + last 3 tool calls + reward gap; cluster by tool-call pattern |
| **terminal-bench** | per-failed-task: shell exit code + last command + error tail; cluster by error class |
| **BIRD-Interact** | per-failed-task: SQL execution error + intent class; cluster by error type |
| **claw-eval** | three-channel (trace + service log + env snapshot) per task; cluster by safety vs robustness vs completion failure |

All share: full message/event preservation in DB (Meta-Harness ablation), embedding via
sentence-transformers, UMAP + HDBSCAN clustering, LLM-labeled cluster names (existing
clustering pipeline at `clustering/`).

---

## Adding a new benchmark — 7-step recipe

This is the critical contract: **new benchmark = small, predictable amount of work.**

| Step | What | Effort | Reuses |
|---|---|---|---|
| **1** | Add benchmark dependency to `pyproject.toml` `[project.optional-dependencies]` | XS | uv |
| **2** | Build sandbox image (`apps/kernel/sandbox/Dockerfile.<benchmark>`) | S — half day | `Dockerfile.m5` template |
| **3** | Define `SandboxProfile` constants in `benchmarks/<name>/profile.py` | XS — 30 min | `LocalDockerSandbox` |
| **4** | Implement `<Name>BenchmarkRunner(BenchmarkRunner)` wrapping the upstream library's run entry point | S-M — half to one day | `BenchmarkRunner` Protocol, `persist_gate_run` |
| **5** | Author baseline skill bundle in `apps/kernel/baselines/<name>_v1/` | XS-S — 30 min to half day | SKILL_FORMAT, skill registry |
| **6** | Implement `<name>_failure_analyzer.py` for cluster signature extraction | M — half day | clustering pipeline, embedding |
| **7** | Register workflow + seed eval cases via `scripts/<name>_register.py` | S — 2-3 hr | workflow + eval_cases tables |

After step 7: existing W7 web UI surfaces lift chart, failure cards, audit chain, skill
diff for the new benchmark. **No UI work per benchmark.** Existing run_improvement_loop
gains a `--workflow <name>-v1` branch (one routing case).

---

## Sequencing — don't generalize prematurely

**Now (P1.5 in the τ³ test plan):** build τ³ following the recipe. **Don't** pre-build
abstractions for terminal-bench / claw-eval / SWE-bench. Hard-code `tau3` paths where
the future generalization point isn't obvious.

**After τ³ ships and one improvement-loop iteration succeeds:** the patterns for the
recipe steps will be obvious. **Then** extract `SandboxProfile`, `BenchmarkRunner`
generalizations from concrete code. This is the second-implementation rule — the second
benchmark is what teaches you what to abstract.

**When terminal-bench is added (Phase 2 / TODO-13 / TODO-14):**
- Steps 1-7 above each take what they took for τ³.
- Sandbox profile reuses `EGRESS_ALLOWLIST` from τ³ (same shape, different image + hosts).
- Failure analyzer pattern reuses τ³'s "trace → text_signature" structure.
- Web UI is zero work.

**Estimated effort to add second benchmark (terminal-bench):** ~2-3 days CC. Compare to
τ³'s ~3-5 days — second is faster because the substrate is built.

---

## What stays in scope vs out of scope

**In scope for the substrate:**
- Multi-benchmark Protocol design
- Sandbox profile abstraction
- Skill registry / failure clustering / regression gate / audit chain reuse
- DB schema is benchmark-agnostic (already)
- Web UI is benchmark-agnostic (already, post W7)

**Out of scope (per-benchmark, not substrate):**
- Benchmark-specific dataset acquisition / licensing
- Benchmark-specific failure-pattern heuristics
- Benchmark-specific user-simulator quality
- Cloud API rate limit handling (LiteLLM's job, not ours)

**Out of scope (deferred to TODOs):**
- Multi-tenant isolation (D4 / TODO-1)
- E2B / Modal sandbox swap (D3 / TODO-2)
- Crypto-grade audit chain (D2 / TODO-3)

---

## Migration path from auto-harness

The auto-harness is already running τ³ on this branch. Migration is incremental:

| Auto-harness pieces | Replaced by | When |
|---|---|---|
| `benchmark.py:TauBenchRunner` | `ownevo_kernel.benchmarks.tau3.TauBenchRunner` | M3 |
| `gating.py:run_gate` | `ownevo_kernel.gate.run_gate` (already exists) | M6 |
| `record.py:results.tsv` | `iterations` table | M6 |
| `workspace/suite.json` | `eval_cases` table | M5 |
| `agent/agent.py` editable file | Skill registry entry | M4 |
| `workspace/learnings.md` | `failure_clusters` + `analyze_failures` tool | M7 |
| `workspace/traces/` | DB-backed traces | M7 |
| `prepare.py` workspace init | `scripts/tau3_register.py` | M5 |
| Docker image (auto-harness's `python:3.12-slim` + tau2 git install) | `ownevo-sandbox-tau3:0.1.0` (ownEvo-built, hardened) | M2 |
| `experiment_config.yaml` | Workflow row + skill metadata | M5 |
| `program_templates/tau_bench.md` | Loop kickoff prompt under `apps/kernel/scripts/m5_agent_prompt.md` style | M9 |

**What we keep:** the `notes_jit.txt` insights (NeoSigma's 14 accepted commits, the
pattern they discovered) as a reference document. Their open-source contribution stays
visible in `ownevo_docs/competitors/neosigma.md`.

**What we don't:** the auto-harness's "edit one file" workflow model. ownEvo's skill
registry with versioning + audit chain is strictly better for the enterprise story —
auditable, revertable, customer-portable.

---

## Open questions

| # | Question | Decision needed by |
|---|---|---|
| Q1 | Does egress-allowlist via Docker iptables work cross-platform (macOS Docker Desktop vs Linux daemon)? Alternative: explicit HTTP proxy. | Before M2 |
| Q2 | Should the τ³ skill bundle include the tau2_patches.py monkey-patches (NL_ASSERTIONS + ENV_INTERFACE), or apply them globally at sandbox boot? | Before M3 |
| Q3 | For benchmarks that need GPU (potential future addition), how does the sandbox profile handle `--gpus`? | Phase 2 (after τ³) |
| Q4 | Should we standardize on tau2's results.json format for `raw_trace_dir`, or convert to `AgentEvent` schema? | Before M7 (failure analyzer) |
| Q5 | When does the `BenchmarkRunner` Protocol get formally extracted from M5's existing implementation? Now (pre-τ³) or after τ³ proves the second pattern? | Before M3 |
