"""Hand-bootstrapped baselines used as Day-1 seeds for the agent loop.

Each subpackage here is a skill set the agent is expected to iterate on
once the Claude Agent SDK middleware adapter lands. Until then, the
baselines exist as registered v1 skills so the loop has a non-empty
starting point.

These modules are intentionally NOT inside `src/ownevo_kernel/`:

  * The kernel stays pandas-free (per CLAUDE.md). Baselines are free to
    pull pandas/lightgbm/sklearn at the file level.
  * The 400-LOC anti-pattern lint applies only to kernel source.
  * Agent-iterated skills don't belong on the kernel import path.
"""
