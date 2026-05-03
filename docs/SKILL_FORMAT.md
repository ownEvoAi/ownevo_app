# Skill File Format

Locked 2026-05-03 by eng review (D3). Every skill — Python pipeline skill (M5
forecasting), instruction skill (NL-gen-emitted prompts), τ³ multi-turn agent
skill — uses this format.

## Why

The skill registry needs a uniform way to know:

1. **What the skill is** — kind (Python code vs instructions), capability tags.
2. **What state the skill keeps across turns** — the **retention contract**, so we can generate retention-violation tests as a regression class.
3. **Who wrote it** — agent / human / NL-gen.

YAML frontmatter is the simplest declarative answer that works for every kind of
skill (Python files use `# ---` block comments; markdown skills use the standard
`---` fence).

## Layout

A skill file starts with a YAML frontmatter block, followed by the body (Python code or markdown instructions).

### Python skill (e.g., M5 LightGBM components)

```python
"""
---
id: m5-feature-engineer
kind: python
created_by: agent:claude-sonnet-4-6
capability_tags: [forecasting, feature-engineering]

retention:
  remembers:
    - field: feature_pipeline_version
      reason: stable identifier across the run
  refetches:
    - source: m5_calendar_features
      stale_after: 24h
      reason: holiday/event flags update daily
    - source: m5_price_history
      stale_after: 1h
      reason: price changes mid-day during promotions
---
"""

import lightgbm as lgb
import pandas as pd

def engineer_features(df: pd.DataFrame, calendar_df: pd.DataFrame, price_df: pd.DataFrame) -> pd.DataFrame:
    # ... implementation
    ...
```

### Instruction skill (e.g., NL-gen-emitted, τ³ multi-turn)

```markdown
---
id: supplier-negotiation
kind: instruction
created_by: nl-gen
capability_tags: [supply-chain, negotiation]

retention:
  remembers:
    - field: supplier_id
      reason: identifies the negotiation thread
    - field: established_relationship_terms
      reason: agreed-upon framing carries through the conversation
  refetches:
    - source: supplier_doc:lead_time_days
      stale_after: 24h
      reason: lead time changes daily based on capacity
    - source: supplier_doc:current_inventory
      stale_after: 1h
      reason: inventory updates with production runs
---

# Supplier Negotiation Skill

When the user asks about a supplier, follow these steps:

1. Look up the supplier's current lead-time (re-fetch — never cache).
2. Reference established relationship terms (carry forward in the session).
3. ...
```

## Field reference

### `id` (required, string)
Skill identifier. Stable across versions. Matches `skills.id` in the database.

### `kind` (required, enum)
`python | instruction | composite`

- `python` — the body is executable Python.
- `instruction` — the body is markdown/prompt content for an LLM to read.
- `composite` — the body declares sub-skills (Phase 2; not used in MVP).

### `created_by` (required, string)
`agent:<model_id>` | `human:<id>` | `nl-gen`

### `capability_tags` (optional, list of strings)
For the `list_skills(capability=...)` query (Phase 2 lazy registry). MVP doesn't query by tag, but tags are recorded so Phase 2 doesn't need to backfill.

### `retention` (required for non-trivial skills, object)

The retention contract.

```yaml
retention:
  remembers:
    - field: <name>           # named field the skill keeps across turns
      reason: <why>
  refetches:
    - source: <source_id>     # identifier matching a tool / data source
      stale_after: <duration> # 1h / 24h / 7d / never
      reason: <why>
```

If a skill has no state retention requirements, declare it explicitly:

```yaml
retention:
  remembers: []
  refetches: []
  stateless: true
```

### Stale duration format

ISO-8601-ish: `1h`, `24h`, `7d`, `30d`, `never`. Parser at `apps/kernel/src/ownevo_kernel/skills/retention.py`.

## How retention contracts produce tests

The eval-case generator walks every skill's `retention.refetches` list. For each `(source, stale_after)` pair, it generates an eval case of provenance `retention-violation`:

```
input:
  source <source_id> updated at T
  T + stale_after / 2  → skill should still trust cache (negative case)
  T + stale_after + 1m → skill MUST re-fetch (positive case)

expected_behavior:
  negative case: trace contains NO re-fetch tool call
  positive case: trace contains a re-fetch tool call before answering
```

These eval cases land in `eval_cases` with `provenance = 'retention-violation'`.
The regression gate enforces them like any other eval case: a skill change that
silently stops re-fetching gets blocked.

## Validation

`apps/kernel/src/ownevo_kernel/skills/format.py` provides:

- `parse_skill(content: str) -> SkillRecord` — extracts frontmatter + body
- `validate_skill(record: SkillRecord) -> list[ValidationError]` — schema-validates frontmatter

Skill-registry write fails with `SkillFormatError` if validation fails. Stays
out of the registry; agent gets the error in `tool_call_result`.

## Skill-detail UI rendering (W7.1.10 / W7.1.11)

The workspace UI shows:

- `id`, `kind`, `created_by`, `capability_tags` — metadata strip.
- `retention.remembers` / `retention.refetches` — bulleted list with reason tooltips.
- The body — syntax-highlighted code (Python) or rendered markdown (instruction).
- Linked retention-violation eval cases — "this skill is tested against the following retention rules."
- Version history — diffs against parent_version_id.
