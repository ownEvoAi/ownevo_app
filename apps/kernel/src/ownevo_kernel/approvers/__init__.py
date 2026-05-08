"""Approver implementations (W5.2 onward).

Approvers consume a `(proposal, plain-language-explanation)` pair and
emit an admit / reject decision. They sit between the regression gate
(which checks that a proposal is *correct*) and the approval service
(which records the decision + advances the proposal state).

Today this package ships:

  * `llm_judge` — W5.2 LLM-as-judge stub. Used for unattended benchmark
    runs where a human reviewer isn't in the loop. Admits proposals
    whose explanation contains the three structural elements
    (cluster reference, change description, metric-direction claim);
    rejects everything else.

Future:

  * Severity-based auto-approve, time-delayed deploy, and other
    enterprise-polish approvers are out of scope for the MVP.
    See `docs/PLAN.md` § "Out of scope" → "Approval-process enterprise
    polish" for the full list.
"""
