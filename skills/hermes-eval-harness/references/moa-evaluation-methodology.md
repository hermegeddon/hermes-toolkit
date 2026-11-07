# Mixture-of-Agents Evaluation Methodology

Use this workflow when auditing, benchmarking, or optimizing a Mixture-of-Agents (MoA) or comparable multi-model ensemble.

## 1. Separate the surfaces before evaluating

Do not assume similarly named MoA features share a runtime path. Identify and record each applicable surface independently:

- upstream/default configuration values;
- persistent named presets and legacy flattened compatibility fields;
- profile overlays and the effective `HERMES_HOME`;
- a persistent virtual provider such as `provider: moa`;
- one-shot commands such as `/moa`;
- callable model tools that perform multi-agent synthesis;
- outer WebUI, gateway, or orchestrator envelopes.

A smoke test for one surface is not evidence about another. Record the active profile, provider, preset, runtime version/commit, and effective topology in every evaluation receipt.

## 2. Start with a read-only static audit

Before generating paid provider traffic:

1. Resolve configuration precedence and identify the canonical editable representation.
2. Record advisor and aggregator provider/model slots.
3. Inspect sampling controls, advisor and aggregator token limits, fan-out cadence, caching, tracing, and failure behavior.
4. Determine whether the runtime waits for all advisors or supports quorum, deadlines, cancellation, or partial aggregation.
5. Verify whether advisors rerun after tool-state changes.
6. Inspect accounting coverage. Report missing prices as **unknown/unpriced**, never as zero.
7. Identify privacy implications: every provider may receive conversational context, and full traces may persist exact inputs and outputs.
8. Label topology-based recommendations as hypotheses. Diversity of model names is not proof of quality uplift or useful complementarity.

### Token-control pitfall

Inspect the runtime call path rather than inferring semantics from field names. Advisor caps, one-shot synthesis caps, and acting-model output limits may be distinct even when configuration contains a generic `max_tokens` field.

## 3. Benchmark design

Always include a strong direct single-model baseline. A useful staged matrix is:

- **A:** direct aggregator model alone;
- **B:** current MoA configuration;
- **C:** remove a potentially redundant advisor while retaining the aggregator;
- **C-cap variants:** test one or more advisor-output caps;
- **D:** a lean subset chosen after contribution analysis;
- **E:** change fan-out cadence only after topology and token-cap effects are understood.

Change one variable class at a time. Do not simultaneously alter advisor membership, temperatures, token caps, and cadence; otherwise causal attribution is lost.

Cover technical reasoning, diagnosis, planning, conflicting-evidence synthesis, deterministic/exact-output work, trivial prompts, adversarial confident errors, and tool-heavy multi-iteration tasks. Use repeated cases where model variance matters.

## 4. Rubric and gates

A reasonable starting rubric is:

| Dimension | Weight |
|---|---:|
| Quality and task success | 40% |
| Reliability and error rate | 15% |
| Latency | 12.5% |
| Cost | 12.5% |
| Advisor complementarity | 10% |
| Operability and observability | 10% |

Treat uplift over the direct-model baseline as a **promotion gate**, not as another weighted category that double-counts quality.

Require hard gates in addition to the weighted score:

- material quality uplift over the direct baseline;
- no increase in severe-error rate;
- no regression on trivial or deterministic tasks;
- acceptable p50/p95 latency and marginal spend;
- graceful behavior when an advisor fails;
- complete-enough token, pricing, and provenance receipts;
- intentional multi-provider disclosure and trace-retention posture.

Calibrate numeric thresholds from baseline variance rather than inventing universal cutoffs before the first run.

## 5. Complementarity and accounting

Model-count diversity is only a proxy. Measure:

- unique useful findings per advisor;
- disagreement rate;
- correlated errors;
- corrections adopted by the aggregator;
- marginal quality contribution when an advisor is removed;
- quality uplift per additional second and priced dollar.

Keep priced and unpriced usage separate. Never reprice all advisor tokens at the aggregator's rate, and never silently treat an unresolved route as free.

## 6. Harness-specific caution

A suite that merely asserts that a `mixture_of_agents` tool was or was not called validates routing and over-triggering only. It does not establish:

- persistent-provider correctness;
- quality uplift;
- advisor complementarity;
- total cost;
- failure or straggler behavior.

Calibrate tool names and trajectory extraction for the installed runtime, then build separate quality and reliability cases for each target surface.

## 7. Promotion discipline

Create named candidate presets or isolated temporary profiles; preserve the current preset as the reference. Promote only after the candidate passes the hard gates. Keep benchmark artifacts and optional full traces in an isolated directory with an explicit retention/deletion decision.
