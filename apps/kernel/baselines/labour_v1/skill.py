"""
---
id: labour.baseline.v1.shift_validator
kind: python
created_by: bootstrap-w2.7
capability_tags:
  - labour
  - shift
  - validator
  - baseline
retention:
  stateless: true
---
"""

import json

_WEEKLY_HOURS_CAP = 40


def _validate(case):
    weekly = case["weekly_hours_so_far"] + case["shift_hours"]
    if weekly > _WEEKLY_HOURS_CAP:
        return {"valid": False, "reason": "overtime_cap"}
    if case["required_skill"] not in case["worker_skills"]:
        return {"valid": False, "reason": "skill_mismatch"}
    return {"valid": True, "reason": "clean"}


# `input_data` is injected as a global by run_pipeline's prologue —
# every sandboxed skill receives it the same way. See
# ownevo_kernel/agent_tools/run_pipeline.py for the wire contract.
_results = [
    {"task_id": case["task_id"], "decision": _validate(case)}
    for case in input_data["cases"]  # noqa: F821 — injected by sandbox prologue
]
print(json.dumps({"results": _results}))
