# trace-format

Typed `AgentEvent` schema. The contract between any customer agent and the ownEvo improvement loop.

Same role as OTel for distributed tracing — standardize once, everything downstream (clustering, eval, gate, replay) works regardless of which framework the customer uses.

Target license: Apache 2 (maximum adoption — this is meant to become the de facto standard). Per `../../../ownevo_docs/ownEvo_MVP.md` § Trace Format.
