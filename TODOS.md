# TODOs

Deferred work captured by the 2026-05-03 CEO review and eng review. Each item has
context sufficient that someone picking it up in 3 months understands the
motivation, the current state, and where to start.

Format follows the standard:
- **What:** one-line description
- **Why:** the concrete problem it solves
- **Pros / Cons:** trade
- **Context:** background
- **Effort:** S / M / L / XL (human team) → human-team estimate; with CC + gstack, compress 5-10x.
- **Priority:** P1 / P2 / P3
- **Depends on:** prerequisites

---

## Phase-2 retrofit checklist (load-bearing)

### TODO-1: Multi-tenant retrofit (D4)

- **What:** Add `workspace_id` to every domain table; enable RLS; wrap every kernel session with `SET LOCAL app.workspace_id`.
- **Why:** D4 ratified single-tenant for MVP. Customer #2 onboarding needs workspace isolation.
- **Pros:** Bounded migration (we designed for it); unblocks every multi-tenant customer.
- **Cons:** ~1-2 weeks of focused work; touches every read query in the kernel and every API endpoint.
- **Context:** [`docs/SCHEMA.md`](docs/SCHEMA.md) § "Phase-2 retrofit (D4)" has the migration sketch. The schema was deliberately designed retrofit-friendly — no composite PKs that would need widening.
- **Effort:** L (human ~1-2 weeks / CC ~2-3 days).
- **Priority:** P1 — blocks customer #2.
- **Depends on:** customer #1 successfully shipped + design-partner-onboarding doc validated.

### TODO-2: Sandbox provider migration (D3)

- **What:** Add `E2BSandbox` (or `ModalSandbox`) implementation behind the `SandboxRuntime` interface; switch by env var.
- **Why:** D3 chose local Docker for MVP. Local Docker hits resource ceiling around 4-6 concurrent customers OR first managed customer.
- **Pros:** No code change beyond the new impl + a config flag; existing `LocalDockerSandbox` stays as the dev/CI runner.
- **Cons:** ~3-5 days of integration + retesting hardening guarantees in the new env.
- **Context:** `apps/kernel/src/ownevo_kernel/sandbox/` has the interface. Drop a sibling file. The hardening checklist (network=none, mem/cpu/pids limits, structured stderr) maps to e2b's `Sandbox` config and Modal's `Image` config.
- **Effort:** M (human ~3-5 days / CC ~half day).
- **Priority:** P2 — triggers when local Docker can't keep up.
- **Depends on:** observability showing sandbox throughput is the bottleneck.

### TODO-3: Audit chain crypto upgrade (D2)

- **What:** Add canonical-JSON content hash + parent hash to `audit_entries`; build a chain rotation procedure that migrations use; optionally Merkle tree + signed root + transparency log.
- **Why:** D2 reframed sovereignty pitch from "tamper-evident" to "append-only." A regulated-industry buyer (healthcare, finance, defense) will ask for crypto-grade.
- **Pros:** Closes the "is this just append-only or is it tamper-evident?" objection. Schema is ready (just add `prev_hash`, `entry_hash` columns).
- **Cons:** ~1-2 weeks if Merkle tree + transparency log; ~3-5 days if just hash chain.
- **Context:** [`docs/SCHEMA.md`](docs/SCHEMA.md) § `audit_entries`. Spec the migration as "stop the log, snapshot the canonical-JSON of every existing entry, compute hashes, restart with hashes from the snapshot."
- **Effort:** M-L (human ~1-2 weeks / CC ~1-2 days).
- **Priority:** P2 — triggers when first regulated buyer evaluates.
- **Depends on:** specific regulatory requirement (EU AI Act, HIPAA, SOC 2 Type II audit, etc.).

### TODO-4: `AgentEvent` schema license / public-release / naming

- **What:** Lock the license (Apache 2 working assumption per MVP doc § Open-Core Line, but no formal commitment in code yet), publication path (npm + PyPI? separate repo? quiet drop or public announcement?), and package naming (currently internal-only Python `ownevo_format`, no npm/PyPI name reserved).
- **Status (2026-05-03):** Spec written at [`packages/trace-format/SPEC.md`](packages/trace-format/SPEC.md). Pydantic + Zod implementations land in W1 against the spec, internal-use-only within `ownevo_app/`. The W3 schema-freeze deliverable bumps the spec to 1.0 internally. None of these decisions block W1 implementation.
- **Why deferred:** The "decide before W3" framing was unnecessarily aggressive. The spec exists; the team can build against it. License + publication + naming are externally-facing concerns that don't gate internal implementation. Premature commitment carries small but real costs (e.g., picking an npm scope before there's a customer to install it).
- **Trigger conditions to revisit:**
  - A customer asks "what license is this under?"
  - A second team or repo needs to depend on this package
  - An OTel Gen AI working group asks to align (or vice versa)
  - Strategic decision to publish (post-MVP, with first design partners)
- **Sub-decisions when triggered:**
  - License: Apache 2 (MVP doc default), or stay proprietary, or BSL/AGPL middle-ground (note: AGPL doesn't apply to schemas; BSL hurts standardization)
  - Where it lives: stay in monorepo, or extract to `ownEvoAi/agent-event-spec` standalone repo
  - Publication: quiet drop to npm + PyPI, or coordinated announcement (YC demo, OSS post, design-partner-only)
  - Scope: just the JSON Schema + Pydantic + Zod, or also reference middleware (Claude Agent SDK adapter)
  - Naming: npm scope (`@ownevoai/...`?), PyPI name (`ownevo-agent-event`?)
  - OTel Gen AI alignment: design-with-awareness (current state, no cross-walk doc) vs formal cross-walk doc as a Phase-2 task
- **Effort to lock when triggered:** ~30-45 min if Apache-2-in-monorepo (current default); 1-2 days if extract + publication pipeline; weeks if retrofitting to Apache 2 after public release as proprietary.
- **Priority:** **P1 — strategic, not blocking.** No longer P0 since the spec is written and W1 is unblocked.
- **Depends on:** founder/board discussion of OSS strategy, OR first external trigger above.

---

## Substrate quality (build-now items, captured here for tracking)

These were added to the W1-W3 plan by the 2026-05-03 eng review. Listed here as
backup tracking in case PLAN.md edits drift.

### TODO-5: Cluster-label LLM eval (W3, Track B) — ✅ DONE 2026-05-06

- **What:** Hand-label 20 M5 clusters with ground-truth names. Add nightly judge-vs-human eval at `apps/kernel/src/ownevo_kernel/clustering/label_eval/`. Target agreement ≥0.7.
- **Status (2026-05-06):** Shipped. 20 hand-authored fixtures at `apps/kernel/src/ownevo_kernel/clustering/label_eval/fixtures.py` (`LABELED_CLUSTER_CASES`) spanning the failure-mode taxonomy. Sonnet 4.6 judge (D4: different from haiku 4.5 labeler). The ≥0.7 gate runs on demand via `make cluster-label-eval LABEL_EVAL_ARGS='--require-agreement 0.7 ...'` — not in GitHub Actions, per project policy that CI doesn't consume API keys. Cost ~$1.20/run on default models. 64 new tests; kernel suite 1009 passing. Module landed at `clustering/label_eval/` (adjacent to the thing being evaluated, mirroring A4.6's `nl_gen/meta_eval/` pattern) rather than the original `eval_runner/cluster_label_eval/` path the row called out — that path predated the meta_eval pattern.
- **Why:** D4 (eng-review) — the demo storyboard at 0:25-0:38 shows a cluster card with an LLM-generated label. Hallucinated labels are a credibility hit; no eval = no detection.
- **Effort:** S (human ~1 day / CC ~2 hours, as predicted).
- **Priority:** P1 — surfaces in YC demo.
- **Depends on:** clustering pipeline operational (W3 Track B).
- **Follow-up: run the live gate locally before each W3-impacting release** — `make cluster-label-eval LABEL_EVAL_ARGS='--require-agreement 0.7 --concurrency 4 --max-retries-per-call 1 --pretty --include-records'` and record the agreement number + per-hint slice in the release notes. **2026-05-07 (pre-v0.4.0):** agreement 0.85 (17/20), judge `claude-opus-4-7` vs labeler `claude-sonnet-4-6`, 33.9s wall. Per-hint: under-forecast 5/6, over-forecast 5/5, flat-prediction 4/5, zero-inflated 2/3, high-variance 1/1.
- **Manual test-plan items deferred from PR #49 (B3.1+B3.2+B3.3) — still outstanding, run when blockers clear:**
  - `OWNEVO_DATABASE_URL=... uv run pytest` clean on a fresh DB
  - `make m5-cluster-failures CLUSTER_ARGS='--no-db --top-k 30 --pretty'` — stub-stages smoke on the in-process M5 baseline
  - (when M5 dataset available) `make m5-cluster-failures` with `OWNEVO_DATABASE_URL` — cluster rows + eval cases land in DB
  - (when network available) `make m5-cluster-failures CLUSTER_ARGS='--real'` — sentence-transformers + UMAP + HDBSCAN + Anthropic end-to-end

### TODO-6: LLM-judge stub eval expansion (W5) — ✅ DONE 2026-05-07

- **What:** Expand W5.2's "5 hand-crafted proposals" to ~30 hand-labeled (proposal, explanation) pairs with structural-element ground truth. Run nightly.
- **Status (2026-05-07):** Shipped in W5.2 (PR #57). 30 hand-authored `LabeledApprovalCase` fixtures across 4 buckets: 10 `structural` (admit), 8 `vague-but-positive` (reject), 6 `structural-but-wrong-direction` (reject), 6 `hand-wavy` (reject). `make llm-judge-approver-eval` with `--require-agreement 0.85` gate (on demand only — project policy; ~$0.40/run on opus 4.7 + 30-case set). 68 new tests.
- **Why:** Eng review surfaced that 5 cases is a smoke test, not an eval. The stub admits proposals during M5 unattended replay; a false-positive admit drifts the lift chart in the wrong direction.
- **Effort:** S (human ~1 day / CC ~2 hours, as predicted).
- **Priority:** P1 — required for unattended M5 replay.
- **Depends on:** LLM-judge stub operational (W5.2).

### TODO-7: Reproducibility CI cache strategy (W3) — partial (b only)

- **What:** Document and implement: (a) cached LLM responses replayed from a fixture file, (b) pre-built sandbox Docker image cached, (c) M5 data pre-loaded into a Postgres volume, (d) cached LightGBM training artifacts keyed by skill-version-hash.
- **Why:** Replaying 30 days in <30 min requires all four cache layers. Without them, CI hits live APIs and misses the budget by 10x.
- **Status (2026-05-03):** **Layer (b) shipped in PR #11d** — `.github/workflows/m5-replay-nightly.yml` builds `ownevo-sandbox-m5:0.1.0` via Buildx with `cache-from / cache-to: type=gha,scope=m5-sandbox`; cache hit skips apt + pip layers. (a) (c) (d) deferred — (a) wires when the agent loop hits LLMs (W4), (c) wires when real M5 data lands on disk, (d) is premature (synthetic-fixture LightGBM trains in seconds, the cache cost would exceed the savings).
- **Effort:** M (human ~2-3 days / CC ~half day).
- **Priority:** P1 — blocks reproducibility CI being green at full 30-day replay scale (current scope: synthetic fixture only).
- **Depends on:** M5 pipeline operational (W2).

### TODO-8: Parallel τ³/M5 conditions strategy (W6/W8) — ✅ DONE 2026-05-08 (PR #62)

- **What:** Run the 4 M5 conditions (frozen / static-LLM / loop-autonomous / loop-gated) in parallel on separate Docker compose stacks (each with its own Postgres + sandbox); merge results in `iterations` table at the end. Same pattern for τ³ A/B/C.
- **Why:** Sequential 30-day replay = ~150 hours wall time. 4-way parallel ≈ 37 hours. Without parallel strategy, W6 budget is too tight.
- **Effort:** M (human ~2-3 days / CC ~half day).
- **Priority:** P1 — required for W6/W8 timelines.
- **Depends on:** M5 pipeline + reproducibility rig operational (W4).
- **Status (2026-05-08):** Shipped in PR #62. Topology revised from 4 Docker Compose stacks to one Postgres / four `workflow_id`s — schema is already keyed by `workflow_id`, merge is a single `UNION ALL`, sandbox isolation stays per-iteration via Docker. New `replay/thirty_day.py` + `scripts/m5_replay_30day.py` + `make m5-replay-30day` drive condition A (frozen) / C (loop autonomous) / D (loop + LLM-judge) via `asyncio.gather`. `run_improvement_loop.py` gained `--approver {none|autonomous|llm-judge}`. Condition B (static frontier LLM) deferred as no-op slot — not load-bearing for the YC demo. 94 new unit tests; kernel suite 1323 passing. Live-system smoke covered C on granite-4.1-8b LMS and D on Sonnet 4.6 cloud. **Combined with the 2026-05-08 W5.2 / BL.3 local validations (TODO-19 closure + W5.2 local 0.9667 ≥ 0.85), conditions C and D both have free local-model paths now.**

### TODO-17: Sandbox classifier hardening — runner exit-code spoof — ✅ DONE 2026-05-03

- **What:** A hostile (or buggy) agent inside `LocalDockerSandbox` can call `os._exit(0)` directly, bypassing the runner's `try/except` around `runpy.run_path`. The classifier sees `exit_code == 0` and returns `status="ok"` — or `os._exit(100)` to spoof the "logical error the agent owns" path with `error_class=None`. Per `apps/kernel/src/ownevo_kernel/types.py:SandboxErrorClass`, the gate runner advances `best_ever_score` only when `error_class is None`, so this is part of the trust boundary.
- **Status (2026-05-03):** Approach 1 shipped. Runner now runs user code as a subprocess (`subprocess.run([sys.executable, '/sandbox/user_code.py'])`); the runner's own exit code is derived from the child's returncode via a fixed policy (0 → 0; 1 → 100; 100 → 102=Crash; negative → 128+|N|; else passthrough). Closes the `os._exit(100)` spoof and the same-process attack surface. The `os._exit(0)` case remains observably indistinguishable from clean exit at the process boundary; defense-in-depth lives at the metric layer (`run_pipeline`'s JSON-output requirement). Pinned by 3 new tests in `apps/kernel/tests/test_sandbox.py`. Documented limit captured in the runner script's policy comment.
- **Effort:** S (CC ~30 min, as predicted).
- **Priority:** P1 — fix before W4 unattended M5 replay. Not blocking W2/W3.
- **Depends on:** none. Self-contained sandbox change.

### TODO-18: Pagination + payload caps for unbounded list/detail endpoints

- **What:** Add `limit: int | None` (or keyset cursor) parameters to:
  - `export_audit_log` (audit/writer.py) and `list_eval_cases` (eval_cases/registry.py) — original W2 scope.
  - W7 list endpoints: `GET /api/workflows`, `/api/workflows/{id}/iterations`, `/api/workflows/{id}/failure_clusters`, `/api/workflows/{id}/traces`, `/api/workflows/{id}/skills`. None currently cap result sets.
  - W7 detail endpoint `GET /api/traces/{id}`: cap the inline JSONB events array (e.g. first 1000 events with `truncated: true`); current shape returns the full unbounded events stream.
- **Why:** All endpoints do unbounded `SELECT *` / full JSONB returns with no LIMIT. `audit_entries` is WORM (can't be trimmed), `traces.events` is a JSONB array per trace with no upper bound, and the per-trace events lateral (`jsonb_array_elements`) in `list_workflow_traces` is the highest OOM risk. For MVP internal-only usage this is fine. For production with a real customer, a single export call against a multi-month log or a long-running trace with monitor-signal storm becomes an OOM/latency bomb.
- **Pros / Cons:** Simple to add; keyset pagination (`since_seq`) is already half-built for `export_audit_log`. The gate runner needs the full eval-case list per run — callers that need the full set should pass `limit=None` explicitly so the pattern is auditable. Trace events truncation needs UI affordance (`truncated` banner + "load full trace" link).
- **Context:** `apps/kernel/src/ownevo_kernel/audit/writer.py:export_audit_log`, `apps/kernel/src/ownevo_kernel/eval_cases/registry.py:list_eval_cases`, and `apps/kernel/src/ownevo_kernel/api/routes/{workflows,traces,skills}.py`. Flagged in w2-foundations review (2026-05-03) by performance + security specialists; W7 endpoints flagged in w7-track1-rest review (2026-05-08) by adversarial + security + api-contract.
- **Effort:** M (human ~1 day / CC ~30 min once trace truncation UI is decided).
- **Priority:** P2 — triggers before customer #1 has meaningful log/trace volume.
- **Depends on:** none. Self-contained API change.

### TODO-9: Anti-pattern lint enforcement

- **What:** Custom check (Python ~5 lines, run in CI): any file in `apps/kernel/src/ownevo_kernel/` over 400 lines fails lint.
- **Why:** MVP doc § Anti-Patterns lists "don't put the whole harness in one file." Enforce as code, not as comment.
- **Effort:** XS (CC ~15 min).
- **Priority:** P3.
- **Depends on:** none.

### TODO-19: Local-model sweep — finish Phase 3 lift on real M5

- **What:** Drive the BL.3+ improvement loop to a measured `val_score < 0.330988` (Phase-2 real-M5 baseline) on at least one local-model + backend combination. Ship the iteration row, agent diff, and lift number as the headline "loop produces real lift on real data" claim.
- **Why:** Validates the full substrate (sandbox + skill registry + agent loop + gate) with a non-Anthropic-API LLM. Cost-control during W2-W4 substrate quality, single-vendor risk reduction, and a credibility signal independent of frontier-API capability.
- **Methodology:** four-tier funnel — Phase 0 probes (`probe_tool_calling`, `probe_skill_quality`) → Phase 1 synthetic-fixture full-loop scan → Phase 2 real-M5 baseline (DONE) → Phase 3 full loop on real M5. See `docs/PLAN.md` §"Pre-W3 (cont.) — Local-model sweep methodology" + `docs/local-model-testing.md` (canonical methodology + findings).
- **Status (2026-05-04):**
  - ✅ Phase 0 probes shipped (PR #29).
  - ✅ Phase 1 ran across 14 candidates; F5 conclusion: qwen3-coder-30b on LMS Anthropic streaming is the only end-to-end driver. ~37 candidates still untested by probes.
  - ✅ Phase 2 baseline locked: `val_score = 0.330988`.
  - ⚠️ Phase 3 v1-v3 burned iteration budget on `SkillFormatError` variants → fixed in PR #26-#28 (parser leniency) + PR #30 (structured-tool refactor — agent never serializes YAML).
  - ⚠️ Phase 3 v5: `write_skill` succeeded on structured surface (`version_seq=2` registered, no SkillFormatError). LMS server-side rejected a later tool call (`anthropic.APIStatusError: Failed to generate a valid tool call`) before the gate could run. Mid-debug — likely a JSON-Schema strictness mismatch in the new `write_skill` schema (esp. nested `retention` object).
- **Next moves:** (a) inspect content_delta tail to identify which tool call LMS rejected, (b) try a different backend (direct Anthropic Claude) as a sanity check, (c) once one backend reaches gate, probe-sweep the remaining ~37 candidates for redundancy.
- **Effort:** S-M (CC ~half day to debug crash + run; ~1 hour for the probe-sweep follow-up).
- **Priority:** P1 — directly feeds B4.2 ("First lift on M5") and B4.4 (Day-7 milestone review).
- **Depends on:** none — substrate is in place.
- **Status update (2026-05-04):** Phase 3 closed on Sonnet 4.6 via Anthropic cloud — v10 produced `val_score=0.395143` (+19% over baseline 0.331), v12 demonstrated the gate-blocked regression at 0.385126. B4.2 + B4.3 both achieved on real M5 for ~$0.78. Stage B 7-iter replay (also 2026-05-04) confirmed the gate held `best_ever=0.3958` through 6 consecutive non-pass iterations ($1.84 cost). **Stage C 7-iter replay with F9 fix produced first compound lift: iter 0 0.3859 → iter 2 0.3988, gate held best_ever across 5 non-pass iters, $1.86 cost.** Local-model lift on real M5 is NOT yet achieved — TODO-20/21/23 cover the gaps.
- **Status update (2026-05-07):** Probe-sweep residue (23 candidates) closed as superseded by the A4.4 broader sweep (PR #52 — 19 local models pass 3/3 across LMS + Ollama). The probe-sweep was looking for BL.3-loop drivers via two single-turn probes; PR #52 delivered a wider list via the actual A4.4 forced-tool-use gate, which is a stronger signal. Headline goal of TODO-19 (a measured local-model lift on real M5) remains open and is now gated on (a) cross-iter failure memory empirically helping qwen3-coder route around F6 — exercise pending the BL.3 OpenAI-loop `/no_think` fix landing — or (b) a non-frontier model with stronger codegen than devstral on M5. Devstral-small-2:latest formally dropped as a candidate (TODO-21 closed; CLAUDE.md no longer recommends it).
- **Status update (2026-05-08):** ✅ **CLOSED.** First local-model lift on real M5 achieved. qwen3-coder:30b on Ollama OpenAI + the PR #61 `/no_think` patch + PR #40 cross-iter failure memory lifted val_score 0.330346 → **0.379663 (+14.9%)** on Stage D iter 4. Agent diff: "Added is_weekend boolean feature." Free, ~12 min wall, fresh DB `ownevo_phase3_realm5_v22_qwen_memretest`. Memory hypothesis confirmed: 14 prior attempts on this model deterministically hit F6 `_long_frame`; with prior failures in context the agent proposed an entirely different feature class. B4.2 + B4.3 both reproduced on a free local model. Sweep-residue work formally not needed.

### TODO-20: F6 mitigation effectiveness retest on qwen3-coder-30b

- **What:** Re-run the Phase 3 loop (real M5, LMS Anthropic backend) on `qwen3-coder-30b` with the F6-mitigation prompt warning that shipped in PR #33. Measure whether the 13-attempt-100%-deterministic `_long_frame` length-mismatch bug is reduced or eliminated. Target: at least one gate-pass or a clean sandbox-error that's NOT the F6 pattern.
- **Why:** The F6 mitigation in PR #33 is a hypothesis: "warning the agent about long-format reshape NaN handling will stop it from indexing 1-D `dow` as 2-D." Without retest, the prompt change is untested. PR #33's tests cover the prompt-caching path, not F6 mitigation effectiveness.
- **Pros / Cons:** Cheap (local model, free, ~5 min wall). If it works, qwen3-coder-30b becomes a viable local-model end-to-end driver for Phase 3, restoring the cost-control + single-vendor-risk story. If it doesn't work, we know the F6 bug is not promptable away on this model and we move to a different local model (TODO-21).
- **Context:** Background in `apps/kernel/docs/local-model-testing.md` § F6. Prompt change in `apps/kernel/scripts/m5_agent_prompt.md` (PR #33 diff). Last 3 attempts (v7/v8/v11) all hit the same bug — pre-mitigation.
- **Status update (2026-05-04, retest after PR #35 merged):** ❌ F6 prompt warning did NOT prevent the bug. Retest on `qwen3-coder-30b` (LMS Anthropic, fresh DB `ownevo_phase3_realm5_v20_f6retest`) hit the same `_long_frame: ValueError: All arrays must be of the same length` at iter 0. **14 attempts now, 100% deterministic on this model.** Mitigation route (a) is exhausted. Path forward: (b) cross-iteration failure memory (TODO-22 option b), or (c) try a different local model entirely (TODO-23 below). Closing TODO-20 as "tested, did not fix."
- **Effort:** XS (CC ~30 min — single run + post-mortem). DONE.
- **Priority:** ~~P2~~ → closed.
- **Depends on:** none. Self-contained.

### TODO-21: Devstral OOM headroom — bump sandbox memory or constrain agent prompt

- **What:** Resolve v13b's `error_class=OOM` outcome: either (a) bump the M5 sandbox `mem_mb` from 512 → 1024 MB (or higher) and retest devstral-small-2 on real M5, or (b) add a "memory-conscious code" instruction to `m5_agent_prompt.md` (avoid duplicating series matrices, prefer in-place ops), or (c) accept that 30,490-series long-format DataFrames just need >512 MB.
- **Why:** v13b is the strongest local-model signal we have on real M5 (devstral wrote runnable code, did NOT trigger F6's `_long_frame` bug, but the resulting pipeline OOM'd). Without resolving the OOM, we can't measure devstral's val_score and can't confirm it as a local-model end-to-end driver.
- **Pros / Cons:** (a) is one CLI flag change in the runner + a re-run (~5 min) but increases sandbox blast radius; (b) is a prompt change that may or may not work on devstral's coding style; (c) closes the avenue. (a) preferred — 512 MB is a defensible-but-tight default; 1 GB is still bounded. Update `docs/local-model-testing.md` with the new finding regardless.
- **Context:** v13b runlog at `.temp/runlogs/20260504-140903-phase3-v13b-devstral-retry/loop.log`. 15 iterations, 14 tool calls, 4 tool errors. Final iteration hit `M5SandboxError: Sandboxed M5 pipeline did not return ok: status=error, error_class=OOM, error='Sandbox memory limit exceeded (OOM-killed)'`. Sandbox config at `apps/kernel/src/ownevo_kernel/sandbox/local_docker.py`.
- **Status update (2026-05-04, post-PR-#35):** PR #35 merged. `--sandbox-mem-mb` flag now on main. Two retests:
  - First retest (DB `_v21_devstral_1gb`): exit=`sandbox-error` with `'dict' object has no attribute 'train'` (agent returned dict not FeatureMatrix from `engineer()`). OOM ✅ cleared.
  - Second retest (DB `_v21_devstral_1gb_v2`, with F9-mitigation prompt): exit=6 ("agent did not register any skill change"). 13 iter / 12 tool calls / **9 tool errors**. OOM ✅ cleared again, but devstral writes runnable-looking code that fails `run_pipeline` validation each time and never produces a clean candidate to commit.
  - **TODO-21's primary ask (clear OOM) is DONE; devstral codegen quality is the bottleneck, not memory. Closing TODO-21.** Devstral on real M5 is not viable as a local-model lift driver.
- **Effort:** XS (CC ~30 min for option (a); ~1 h for option (b)).
- **Priority:** P2 — same reasoning as TODO-20: strengthens local-model story; not on YC critical path.
- **Depends on:** none. Self-contained.

### TODO-22: F9 mitigation — M5 date format in prompt + cross-iteration failure memory

- **What:** Fix the repeated `pd.Timestamp("d_1858")` sandbox crash that blocked iters 2–6 of Stage B. Two mitigations, one cheap and one correct:
  - **(a) Prompt fix (immediate):** Add to `apps/kernel/scripts/m5_agent_prompt.md` a note that `fold.validation` / `fold.test` are lists of M5 day-ID strings like `"d_1858"`, NOT calendar dates. To derive month: `_M5_ORIGIN + pd.Timedelta(days=int(d[2:]) - 1)` where `_M5_ORIGIN = pd.Timestamp("2011-01-29")`.
  - **(b) Cross-iteration failure memory (proper):** Populate `failure_clusters` from `sandbox-error` iterations with `error_class=None` so `analyze_failures` returns the F9 pattern in subsequent agent turns. Each new agent would read the cluster and avoid the same approach.
- **Why:** Stage B showed 5/7 iterations hitting the same bug independently. The gate held `best_ever=0.3958` throughout, but the loop made no forward progress. Without mitigation, any future multi-iteration run against a best_ever-constrained DB will cycle on the same error.
- **Pros / Cons:** (a) is 30 min and unblocks the lift curve immediately. (b) is the architecturally correct answer but requires wiring `analyze_failures` to read live cluster data + failure-cluster creation from sandbox runs (currently clusters are created from eval runs, not sandbox crashes). Do (a) now, track (b) as a separate item.
- **Context:** Stage B runlog `.temp/runlogs/20260504-143146-stageb-sonnet-7iter/`. Full analysis in `docs/local-model-testing.md` § F9. DB: `ownevo_phase3_realm5_stageb_v1`.
- **Status update (2026-05-04, post-PR-#35):** ✅ Option (a) prompt fix MERGED (PR #35) and EMPIRICALLY VALIDATED. Stage C's iter 0 successfully integrated the `month` feature using day-ID arithmetic (no `DateParseError`). First compound lift on real M5 followed: iter 0 0.3859 → iter 2 0.3988 (gate-passed twice). **Option (a) closed.** Option (b) cross-iteration failure memory remains open — Stage C still showed iter 4 + iter 6 hitting OOM patterns and iter 5 hitting a near-baseline regression, all with the same lack of memory of prior failures. Captured separately under TODO-23 below since this is a P1 substrate gap, not a workaround.
- **Effort:** ~~XS for (a)~~ DONE; M for (b) (CC ~half day).
- **Priority:** ~~P1 — blocks Stage B from producing a lift curve beyond iter 0. Prompt fix is the unblock; failure-memory is P2.~~ → (a) closed; (b) graduates to TODO-23.
- **Depends on:** ~~none for (a)~~ DONE.

### TODO-23: Cross-iteration failure memory (graduated from TODO-22 (b))

- **What:** Populate `failure_clusters` from `sandbox-error` iterations with `error_class=None` so `analyze_failures` returns prior failure patterns to subsequent agent turns. Currently `analyze_failures` returns workflow-level *eval-task* clusters; we need it to surface recent sandbox-crash signatures from `iterations.state='sandbox-error'`.
- **Why:** Stage C showed the gate working (5 correct rejections, 0 false promotions) but no cross-iteration learning. Each new agent invocation reads the latest skill, can't see *why* prior diffs were rejected. With 7 iterations against the same DB, we still got only 2 gate-passes — half the iterations were rediscovering rejected ideas. With memory, the agent could try genuinely new directions and the lift curve would be steeper.
- **Pros / Cons:** Architectural fix that touches `analyze_failures` + a new pattern-extraction routine. ~half-day. Returns dividends on every multi-iter run going forward (Stage D, customer agents, OpsAgent-Bench).
- **Context:** TODO-22 description. Stage C runlog `.temp/runlogs/20260504-151449-stagec-sonnet-7iter/`. Sandbox-error rationale strings already contain the failure signature (e.g., the OOM trace, the F9 DateParseError, the `dict has no attribute` AttributeError) — just need to surface them to the agent.
- **Effort:** M (CC ~half day).
- **Priority:** P1 — pattern is now the binding constraint on Stage D and beyond.
- **Depends on:** none. Self-contained. Touches `apps/kernel/src/ownevo_kernel/observability/learnings.py` and the `analyze_failures` tool definition.
- **Status update (2026-05-04, shipped in PR #40):** ✅ CLOSED. Implemented as **B+A** on `feat/cross-iter-failure-memory`:
  - **B (driver-side prompt injection):** new `observability/past_attempts.py` (`fetch_past_attempts` / `format_past_attempts` / `render_past_attempts_block`); `run_improvement_loop.py` queries the most recent finalized iterations on the workflow and prepends a compact "Past attempts" block to the agent kickoff. Memory is in-context, not tool-gated.
  - **A (`analyze_failures` extension):** `FailureSnapshot` gains `iteration_state` / `sandbox_error_class` / `eval_rationale`. SQL LEFT JOINs `iterations` + `proposals`; sandbox-error iterations sort to top regardless of tool-error count. Tool description and dispatcher updated to surface and explain the new ranking.
  - Tests: new `test_observability_past_attempts.py` (8 tests) + `test_analyze_failures_surfaces_sandbox_error_metadata`. Full kernel suite 436/436 green.
  - **Post-review fixes (2026-05-05):** LATERAL join replaces bare LEFT JOIN on `proposals` (no UNIQUE constraint on `iteration_id` — plain join would duplicate rows if an iteration ever gains a second proposal); `analyze_failures` sort-after-break bug fixed (early break prevented sandbox-error traces from reaching the sort when k+ newer non-sandbox traces were present — the exact scenario the feature was built for); `_truncate` extended to strip `\r`/`\r\n`; `render_past_attempts_block` call wrapped in exception guard so a DB hiccup degrades gracefully rather than crashing the loop.
  - **Empirical validation pending:** Stage D run on real M5 to confirm the lift curve is steeper with memory in-context than Stage C's 2/7 gate-passes.

### TODO-28: W6 row 6.1 — dogfood / dry-run NL-gen demo loop end-to-end

- **What:** Exercise `apps/kernel/scripts/nl_gen_demo_loop.py` (PR #64) end-to-end against the live `/workflows/preview` UI with a real reviewer flow ("type description → sim+evals+metric → loop runs → lift visible"). Confirm the **<5-minute total wall-time budget** holds for an external reviewer (PLAN.md row 6.1 validation gate). Captures any latency, prompt-clarity, or UI-glue bugs before W8 video record.
- **Why:** Row 6.1 demo loop shipped on `feat/w6-nl-gen-loop` (PR #64) with unit tests, but the validation gate is "external reviewer can sit through the live demo without intervention; lift chart visibly moves." That requires a human-in-the-loop dry-run, not a pytest pass. Without it, we discover demo-budget overruns during the YC video shoot in W8 — too late.
- **Pros / Cons:** ~30-60 min if everything works; longer if the loop blows the budget and needs prompt or pacing fixes. Cost: one Anthropic-API end-to-end run (~$0.30 on Sonnet 4.6 per 6.1's design). Output: a recorded run log + wall-time number + a list of any UX gaps that need patching before W8.
- **Context:** PR #64 / branch `feat/w6-nl-gen-loop`. Demo loop at `apps/kernel/scripts/nl_gen_demo_loop.py` + `apps/kernel/src/ownevo_kernel/nl_gen/loop.py` + `instruction_proposer.py`. Storyboard at `docs/W6_DEMO_STORYBOARD.md`. UI surface lives at `apps/web/app/workflows/preview/`.
- **Effort:** XS-S (CC ~30-60 min; depends on whether the budget holds first try).
- **Priority:** P2 — required before W8.1.1 video record but not blocking W7 work.
- **Depends on:** PR #64 merged.

### TODO-29: W6 row 6.3 — execute 30-day M5 replay + verify success thresholds

- **What:** Run `make m5-replay-30day` (TODO-8 / PR #62 infra — conditions A/C/D in parallel via `asyncio.gather`, optionally B) on real M5 and verify the four W6 success thresholds: ≥+25% RMSE lift Day-1→Day-30 in condition D, ≥50 eval cases generated, ≥15 approved revisions, ≥5 gate-blocked regressions. If any threshold misses, document why + decide between extending Phase 2 or accepting the lower number.
- **Why:** PLAN.md row 6.3 is the **Phase-2 validation gate** before W7 starts officially. The infrastructure is shipped (PR #62) and conditions C+D both have a free local-model path (TODO-19 closed). The only thing missing is actually executing the run on real M5 and recording the result. Without it, the W8 hero chart in `m5-results-2026-Q3.md` has no data behind it.
- **Pros / Cons:** Multi-hour wall-time job (PR #62 estimated ~37 hours for the 4-way parallel 30-day replay; condition D alone with `--approver llm-judge` will be the slowest). Cost: condition D on Sonnet 4.6 ~$5-15 across 30 iterations × 4 conditions; condition C on qwen3-coder:30b is free. Best run as an overnight job with a structured checklist.
- **Context:** Infra at `apps/kernel/src/ownevo_kernel/replay/thirty_day.py` + `apps/kernel/scripts/m5_replay_30day.py` + `make m5-replay-30day`. Each condition writes to its own `workflow_id`; merge is a single `UNION ALL` over `iterations`. PLAN.md § 6.3 has the threshold list. `benchmarks/m5-code-gen-loop.md` has the full Success Criteria spec.
- **Effort:** M (CC ~30 min to kick off + monitor; ~30+ hours wall-time; ~1 hour to write up the result).
- **Priority:** P1 — closes Phase 2 validation gate; feeds W8.1.2 `m5-results-2026-Q3.md`.
- **Depends on:** PR #64 merged (for full W6 surface area).

---

## Feature roadmap (Phase 2 / post-MVP)

### TODO-10: Existing-trace OTel-ingest path (Entry Point B)

- **What:** Build the OTel collector intake adapter that maps customer's existing OTel spans → ownEvo's typed `AgentEvent` schema. Add workflow-inference step that reconstructs a workflow spec from observed traces.
- **Why:** Per MVP doc, "Customer already has OTel traces" entry point is mocked at `24-existing-connect.html`/`25-existing-inferred.html`. Real for customers who refuse to adopt yet-another-SDK.
- **Effort:** L (human ~2-3 weeks / CC ~3-5 days).
- **Priority:** P2 — first Phase-2 priority post-MVP per MVP doc § Phase 2.
- **Depends on:** MVP shipped + customer pull justifies intake-adapter work.

### TODO-11: Knowledge ingestion pipeline

- **What:** Slack/email/docs/runbooks → eval cases AND skill-shaped prompts (rules + rationale + negative examples).
- **Why:** Cold-start mechanism for non-greenfield customers. On Q3 2026 roadmap per MVP doc.
- **Effort:** L (human ~3-4 weeks / CC ~5-7 days).
- **Priority:** P2 — driven by enterprise design-partner pull.
- **Depends on:** ≥3 design partners onboarded.

### TODO-12: Lazy capability-queryable skill registry

- **What:** `list_skills(capability="forecasting")` query path with skill embeddings.
- **Why:** MVP-scale (~3-6 hand-picked workflows + M5 + τ³ templates) doesn't need this. Add when registry exceeds ~30 skills.
- **Effort:** S (human ~3-5 days / CC ~half day).
- **Priority:** P3 — driven by skill count.
- **Depends on:** skill registry has ≥30 distinct skills.

### TODO-13: Wave-2 framework integrations

- **What:** Mastra, LangGraph, OpenAI Agents SDK, raw Anthropic SDK middleware adapters.
- **Why:** Per MVP doc § Integration Targets Roadmap. Wave 1 (Claude Agent SDK) is enough for the demo + first design partners.
- **Effort:** M (~200-400 LOC per adapter; ~1 week per once schema is locked).
- **Priority:** P2 — driven by design-partner stack composition.
- **Depends on:** Wave 1 adapter shipped + first design partner asks for one of these.

### TODO-14: SWE-Bench Verified

- **What:** Generalization signal benchmark. Reuses M5 + τ³ infrastructure.
- **Why:** Per MVP doc § Phase 2. Lowest-priority benchmark — only run if there's bandwidth post-Demo-Day.
- **Effort:** S (human ~1 week / CC ~1-2 days once M5+τ³ infra is operational).
- **Priority:** P3.
- **Depends on:** MVP shipped.

### TODO-15: OpsAgent-Bench (custom benchmark we publish)

- **What:** Post-Series-A custom benchmark. Requires real customer relationships + design-partner consent + ground-truth annotation labour.
- **Why:** Per MVP doc § Phase 2. Framing-ownership moat.
- **Effort:** XL (~4-6 weeks once design partners are onboarded).
- **Priority:** P3 — post-Series-A material.
- **Depends on:** ≥3-5 design partners onboarded.

### TODO-24: A4.4 deterministic decoding (`temperature=0`)

- **What:** Pass `temperature=0` (and `top_p=1.0` / `top_k=1` where backend honors them) on every agent-solver call in `apps/kernel/src/ownevo_kernel/eval_runner/agent_solver.py:predict_one`. Currently the call relies on backend defaults (Anthropic ~1.0, Ollama ~0.8, LMS instance config), which produces ~0.08–0.20 score variance per workflow across runs of the same model + prompt + ctx (F14j evidence: granite-4.1-8b laptop LMS run-1 vs run-2 swung credit 0.33 → 0.25, demand 0.60 → 0.80 between back-to-back runs).
- **Why:** Stochastic sampling muddies every A/B comparison we run — same model + same backend on different days can land different sides of a 0.40/0.50/0.75 threshold. Deterministic decoding makes the F14a-j tables reproducible (publishable claim) and lets us cleanly separate "did this prompt patch help?" from "was that just a lucky sample?". F14j credit-risk gap analysis (Apple Metal vs CUDA, ~0.17 systematic) is only believable because we ran multiple times on each host; with `temperature=0` it'd be a single-run claim.
- **Pros / Cons:** Tiny patch (~5 LOC, one OpenAI extra arg + one Anthropic call arg). Cost: deterministic decoding on classification gates is fine (no diversity needed), but if a future task needs sampling variance we'll need to make it conditional. Trade-off accepted given today's gate is forced-tool-use single-turn.
- **Context:** F14j writeup in `docs/local-model-testing.md`. CSV column `no_think` already added; could add a `temperature` column too on next sweep refresh.
- **Effort:** XS (CC ~15 min including a 2-host re-run of granite-4.1-8b to confirm variance collapsed).
- **Priority:** P2 — unblocks publishable F14 numbers; not on YC critical path.
- **Depends on:** none. Self-contained `agent_solver.py` change.

### TODO-25: Ollama agent path → `/api/chat` for proper `/no_think` support

- **What:** When `--openai-base-url` resolves to an Ollama daemon (`/v1` suffix at `:11434`), route agent calls through Ollama's native `/api/chat` endpoint instead of the OpenAI-compat `/v1/chat/completions`. Pass `think: false` in the request body for qwen3-family models. Map back to the same `predict_label` tool-use parsing.
- **Why:** F14h-hang + F14i found that Ollama's OpenAI-compat layer **silently strips** the `think` parameter from the request body, so `/no_think` only works on builds whose Modelfile TEMPLATE contains the `IsThinkSet` parser (desktop's `qwen3:14b` does, laptop's same-tag build doesn't). Going via `/api/chat` with `think: false` always works regardless of Modelfile template — verified by direct curl in F14h-hang ("OK" returned in 700 ms vs OpenAI-compat path emitting empty content + reasoning trace). Today's `agent_solver._maybe_no_think_suffix` injects the directive in user prompt as a fallback that only works on some builds; the proper fix is the API switch.
- **Pros / Cons:** ~50 LOC for the new branch in `predict_one` (host detection + native API client + tool-call extraction from `/api/chat` response shape). Unlocks reliable laptop Ollama testing of qwen3.5/3.6 family (today they hang regardless of /no_think directive). Cons: another code path to maintain; LiteLLM already does this translation for free if we route through it instead.
- **Context:** F14h-hang root cause analysis in `docs/local-model-testing.md`. Ollama issue #14502 + Crush #2457 + Qwen3 docs.
- **Effort:** S (CC ~half day including laptop re-test of qwen3.5:4b/9b/latest, qwen3:8b/14b).
- **Priority:** P3 — laptop tier didn't ship a qwen3-family 3/3 anyway and isn't blocking. Worth doing before the next major sweep refresh.
- **Depends on:** none.
- **Status update (2026-05-07):** Prompt-layer mirror of the agent_solver `_maybe_no_think_suffix` helper landed in `middleware/claude_sdk/runner.py:run_agent_turn_openai` so the BL.3 multi-turn loop now appends `/no_think` to the system prompt for any qwen3-family model. Surfaced because qwen3-coder:30b on Ollama OpenAI emitted 49 text tokens / 0 tool calls on the 2026-05-07 BL.3 retest — agent_solver's helper covered only the A4.4 single-turn gate; the loop runner was missing it. The transport-layer switch (this TODO's actual headline) remains open: qwen3.5/3.6 lineage still hangs on laptop Ollama via `/v1` regardless of the directive, and `/api/chat` with `think:false` is the only reliable suppression there. Prompt-layer fix is a strict subset of the proper transport fix.

### TODO-26: `--ollama-num-ctx` flag plumbed through `nl_gen_smoketest` to OpenAI-compat call

- **What:** Add a `--ollama-num-ctx` CLI flag to `apps/kernel/scripts/nl_gen_smoketest.py` and pass it through `agent_solver.predict_one` to the OpenAI client as `extra_body={"options": {"num_ctx": N}}`. Defaults to None (don't pass — preserves current behavior).
- **Why:** Ollama's default `num_ctx` per model is determined by the daemon at load time. Laptop Ollama defaults to 8192 for some models even though they support 65536+; the smoketest's prompt (workflow + tools + trajectory) is ~5-7K tokens, so 8K leaves ~1-3K for tool call + thinking trace = guaranteed truncation. Today we work around this with a curl preload to `/api/generate` with explicit `num_ctx`, but that's a pattern, not a feature. Documented in F14j and in F1 (the original Ollama context-truncation finding). LiteLLM proxy config already passes `num_ctx: 65536` for routed paths — direct OpenAI-compat paths don't.
- **Pros / Cons:** Clean plumbing fix (~10 LOC). Removes the preload-curl workaround for laptop runs. Doesn't help with Ollama's silent stripping of `think` — that's TODO-25.
- **Context:** F1 + F14j. `temp/run_laptop_4b_ctx32k.sh` is the current preload-hack pattern that this would replace.
- **Effort:** XS (CC ~15 min).
- **Priority:** P2 — would have saved hours this session.
- **Depends on:** none.

### TODO-27: Cloud NL-gen — sim_plan AST safety failures + `nemotron-3-super` workflow_spec validation

- **What:** Two cloud-NL-gen probe failures from F14g/F14j-adjacent work that warrant a follow-up retry once additional prompt mitigations land:
  - **`qwen3-coder:480b-cloud`** passes workflow_spec ✅ + sim_plan ✅ (both schema-aware patches in commit 594bbb4 helped) but its `init_state_code` violates `_ast_safety_check` rule #7 ("NO imports inside the function bodies") by emitting `from datetime import timedelta` inside the function body. Per-stage prompt rule already exists; cloud model didn't comply. Fix candidates: (a) move `from datetime import timedelta` to the `imports: list` field automatically in the renderer when detected (mechanical fix-up); (b) strengthen prompt rule #7 with an explicit example showing the violation pattern; (c) accept that 480B coder model writes Python the way it knows how and isn't a NL-gen pick.
  - **`nemotron-3-super:cloud`** still failed workflow_spec validation even after the rules-9-10-11 patch (3 errors in run 1 → 1 error in run 2 = partial improvement, but didn't reach sim_plan). Different failure mode each time; likely needs schema-error feedback loop OR few-shot example.
- **Why:** Cloud free-tier NL-gen is the cheapest "is there a non-Anthropic NL-gen driver" probe. Two real candidates landed close to passing — worth iterating once.
- **Effort:** S (CC ~half day to land prompt strengthening + fix-up renderer + re-run).
- **Priority:** P3 — Opus 4.7 is the validated NL-gen driver; cloud alternatives are nice-to-have unless cost or vendor risk forces the issue.
- **Depends on:** Ollama Cloud free-tier subscription stays current, OR willingness to subscribe to Pro tier (~$20/mo) to test `deepseek-v4-pro`, `glm-5`, etc.

### TODO-16: Multi-agent topology graph view

- **What:** n8n / Google Opal style visualization for multi-agent workflows.
- **Why:** MVP workflows are single-agent loops; the Workflow Agent-anatomy pane (W7.1.12) is enough for single-agent inspection.
- **Effort:** M (human ~1-2 weeks / CC ~2-3 days).
- **Priority:** P3 — wait for multi-agent workflows to actually exist.
- **Depends on:** customer pull for multi-agent topologies.
