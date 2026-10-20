# Final requirements coverage

This is a paraphrased audit of every mandatory item in assignment Sections 3 and
6. The supplied assignment remains private. **Verified** means implementation
and observed evidence support the claim; **design only** means the assignment
requires a credible extension design, not a built feature. **Manual gate** means
publication must still be performed by the owner. No optional stretch feature is
required or claimed. The only outstanding delivery action is public publication
and submission after approval; the implemented mandatory vertical slice is present.

Evidence keys below link to unchanged actual records, not test doubles:

- **D**: [genuine eight-decision discovery](evidence/phase2/discovery/58835bd89e854f5a825075b8e58b89ae/events.jsonl), [result](evidence/phase2/discovery/58835bd89e854f5a825075b8e58b89ae/result.json).
- **A**: [saved capability](evidence/phase2/savings.capability.json), [runtime profile](evidence/phase3/savings.runtime.json).
- **R**: [fresh model-free replay for 06789](evidence/phase2/replay/2294bfa9500f4457872166ed75ad439c/events.jsonl).
- **B**: [known not-found outcome](evidence/phase2/not-found/b80488b73a2744a28d9e84587b6becc9/events.jsonl).
- **T**: [actual transient recovery](evidence/phase3/transient_failure/4f7222459b1040c4be54df4bec420428/events.jsonl), [exhaustion](evidence/phase3/retry_exhausted/da13f52ee3794e53ab916c0bd1b7916a/events.jsonl).
- **F**: [session-expiry failure and diagnosis](evidence/phase3/session_expired/9fa16af257c6483899c9e0de6916289a/events.jsonl).
- **H**: [real human takeover/resumption](evidence/phase4/manual-success/3edd60f0fa29496687b2a713d4e5f8f2/events.jsonl), [operator confirmation](evidence/phase4/operator-confirmations.json).
- **C**: [real operator cancellation](evidence/phase4/manual-cancel/7a6b3c13366a41c08d7af39414a3eb8c/events.jsonl).
- **V**: [final validation and new offline command evidence](evidence/phase5/README.md).

Automated tests use scripted providers or operator gestures and are not evidence
of a real model or human. Their filenames below refer to the [tests](tests/) tree.

| Mandatory requirement | Implementation | Applicable automated tests | Actual demonstration | Status and limits |
| --- | --- | --- | --- | --- |
| 3.1 Accept natural-language goal and target/entrypoint | `cli.py`, `discovery.py` | `test_workflow.py`, `test_vision.py` | D, README discovery command | Verified; one declared savings task/output contract, not arbitrary task synthesis. |
| 3.1 Live LLM observe/decide/act until goal or stopping condition | `vision.py`, `discovery.py` | `test_discovery_step_limit_and_timeout`, `test_model_done_cannot_establish_success`, `test_submission.py` | D: eight actual API responses and verified completion | Verified; no new paid calls for final validation, one genuine complete discovery. |
| 3.1 Real click/type/navigation/read, with credible legacy targeting | `browser.py`, `execution.py`, fixture templates | `test_browser.py`, `test_visual_selection.py` | D, R: genuine iframe, search, detail, savings and extraction | Verified; screenshot-led but DOM-backed, fixed viewport, no canvas/desktop implementation. |
| 3.2 Emit a typed, serializable capability after successful discovery | `capability.py`, `compiler.py`, `cli.py` | `test_strict_schema_round_trip`, compiler tests in `test_workflow.py` | D compilation event; A with genuine response provenance | Verified; failed or assisted traces do not compile. |
| 3.2 Ordered steps/actions | `Step`, `Capability` | `test_malformed_artifacts_rejected` | A: nine stable step IDs | Verified; small linear flow, no general branching language. |
| 3.2 Identify targets and explain robustness | `TargetSpec`, locator variants, `browser.py`, `compiler.py` | ambiguity/conflicting/identity/effect/fallback tests in `test_browser.py`, `test_visual_selection.py` | A frame/row/caption/role targets and rationale; D grounding | Verified; absent candidates may fall back, ambiguity never does. Arbitrary CSS/XPath is not accepted. |
| 3.2 Typed invocation inputs | `FieldDefinition`, `Bindings`, `validate_inputs` | `test_input_types_are_strict_and_leading_zero_survives`, `test_bindings_keep_provenance_and_leading_zero` | A, R: 06789 remains a string | Verified; member ID format is five ASCII digits. |
| 3.2 Typed outputs/extraction shape | `OutputDefinition`, `Extract`, `parsing.py` | money-parser and output-error tests | D/R typed identity, integer cents and USD | Verified; exact supported USD format, no locale guessing. |
| 3.2 Checkpoint/success condition | predicates, discovery/replay independent verification | premature-completion, wrong-record, probe-checkpoint tests | D/R independently verified final state; H resume verification | Verified; a model declaration or changed screenshot cannot establish success. |
| 3.2 Versioned and reviewable contract | strict schema 1.0, semantic capability version, application metadata | malformed/version/reference validation tests | A; V schema/hash audit | Verified; unsupported versions/operations fail, no executable expressions or raw transcripts. |
| 3.3 Saved artifact plus parameters, without model decisions | `replay.py`, CLI offline boundary | `test_fresh_parameterized_replay_without_provider`, `test_cli_replay_is_offline_in_fresh_process` | R, V with no model keys/calls | Verified; throwing model constructors in tests. |
| 3.3 Stable targeting, checkpoints, outputs returned to caller | shared surface/executor, output validation | iframe, ambiguity, identity and parser tests | R, V | Verified; execution is deterministic for supported application state, not a claim that external state never varies. |
| 3.3 Detect validation, missing records, permission denials | `banking_runtime.py`, runtime/business predicates | `test_complete_runtime_matrix` | B, actual validation run in evidence index | Verified; BusinessOutcome instead of crash; malformed invocation is validation Failure. |
| 3.3 Detect unexpected dialogs and expiry | runtime stop rules; native-dialog latch | runtime matrix native/unknown/expiry cases | F; native/unknown cases are browser tests | Verified; no automatic acceptance/login; only the reviewed acknowledgement supports resumption. |
| 3.3 Detect and handle slow/failed loads | runtime wait/read-retry rules | runtime matrix, budget and timeout tests | T; slow-loading CLI record is explicitly not claimed as an executed wait | Verified; controlled browser time proves bounded waiting in tests. |
| 3.3 Separate business outcomes, recoverable conditions and hard failures | `TerminalResult`, `RuntimeMonitor` | terminal contract and complete matrix tests | B, T, F | Verified; recovery is nonterminal and bounded. |
| 3.3 Structured success/business/failure with debugging context | result contracts, `diagnostic_state`, replay failure handling | diagnostics, output-code, uncertainty and deadline tests | R/B/F/T/C | Verified; failure includes step, expected/observed state, evidence, history and uncertainty. |
| 3.4 Explicit configurable origin/route/action allowlist | `Policy`, inspected facts, browser network gate | `test_policy.py`, alternative mutation and redirect tests | policy events in D/R/H; direct/bypass paths tested | Verified; CLI fixture origins are local-only. Policy configuration is a Python contract, not a separate configuration service. |
| 3.4 Conservative treatment of risky/irreversible actions | GET/read-route allowlist, form/effect inspection, disabled fixture transfer | direct/click/Enter/form/recovery/redirect policy tests | H acknowledgement changes no financial data | Verified; financial mutation remains blocked even during HUMAN ownership. |
| 3.4 Do not persist secrets or raw regulated data | `evidence.py`, field masking, restricted activity capture | redaction canaries, masked observation/diagnostic tests, capture-scope tests | sanitized D/R/H; V inventory/secret scan | Verified for fictional fixtures only; no general PII classifier or OCR assurance. |
| 3.5 Structured actions and reasons | evidence events, locator rationale, checkpoint/rule descriptions | `test_evidence.py`, probe/runtime/workflow event assertions | D actions/targets/checkpoints, T recovery reasons, H request/context | Verified; structured decisions and rule reasons, not private model chain-of-thought. |
| 3.5 Rich failure diagnostic signal | masked screenshot or explicit unavailable signal | failure diagnostics and closed-session tests | F and exhaustion images/logs | Verified; no raw DOM, cookies or storage dump. |
| 3.6 Detect/route blocked state with capability/goal, step, state and reason | `InterventionRequest`, shared coordinator | positive/rejected discovery/replay handoff tests | H request, challenge screenshot and reason | Verified; only one reviewed human-resolvable blocker is routed; arbitrary failures/policy denials stay blocked. |
| 3.6 Human operates the same live session and hands back | `SessionControl`, headed context/page retention, console operator | same-session identity, settling, stale-generation and closed/replaced tests | H, with one session/page and actual manual gestures | Verified; local console, no remote co-browsing platform. |
| 3.6 Preserve context/evidence and capture human activity | retained replay/discovery coroutine, activity capture | partial extractions/recovery history, capture redaction/scope tests | H click/change/submit/navigation, continued s006-s009; C | Verified; events during destroyed-document navigation can be lost; no complete forensic claim. |
| 3.6 Explicit pause/ownership/resume model | lock, ownership generation, reconciliation predicates | ownership exclusion, wrong-member/view/unresolved/stale/policy/timeout/uncertainty tests | H ownership transitions and verified resumption; C cancellation | Verified; physical input cannot be isolated by the lock; no process-restart continuation. |
| 3.7 Surface abstraction for legacy web/desktop | public Surface contract, opaque targets, inspected facts | public-adapter test; iframe/limited structural tests | legacy fixture in D/R; REPORT design | Verified web seam; desktop target variants/native executor remain design only. |
| 3.7 Vendor/tenant reuse, safe specialization and drift handling | app family/version, relative entry routes, checkpoint identity; REPORT extension design | profile mismatch, target ambiguity, identity/checkpoint tests | same unchanged artifact at different local origins; A/R/V | Design only for real tenants: immutable base plus reviewed validated overrides, per-instance origins/contexts, version/digest/checkpoint validation. No automatic re-recording or tenant infrastructure. |
| 6.1 Source in a public Git repository | source/tests/manifests and publication verifier | Git/public inventory check in V | Local reviewed tree; no remote publication performed | **Manual gate:** owner must approve, commit and push publicly. Current repository has no commits/staged contents. |
| 6.1 Root README setup/run, keys/config and offline execution | [README.md](README.md) | fresh install, browser install, tests, CLI/API-boundary checks | V; preserved paid D | Verified Windows/Python 3.13; other OS/Python versions unverified. |
| 6.1 Exact discover-then-replay demo path | README optional discovery plus saved-artifact offline path | controller/CLI integration tests | D/A/R; V fresh offline replay | Verified; final validation does not spend credits to repeat D. |
| 6.2 Root REPORT, approximately 1-3 pages, exactly seven headings | [REPORT.md](REPORT.md) | heading/word-count validation in V | Final design document | Verified by exact heading audit and approximate word count; physical page count depends on rendering. |
| 6.2 Architecture and Artifact schema sections | REPORT first two sections | schema/compiler/shared executor tests | A/D/R | Verified decisions and trade-offs, not a development diary. |
| 6.2 Determinism & error handling section | REPORT third section | complete runtime matrix, uncertainty/identity tests | B/T/F | Verified taxonomy, bounded recovery, conservative drift handling. |
| 6.2 Heterogeneity & multi-tenant section | REPORT fourth section | seam/profile compatibility tests | legacy D/R; conceptual extension discussion | Design-only desktop/tenant reuse explicitly separated from implemented behavior. |
| 6.2 Escalation & handoff section | REPORT fifth section | handoff tests | H/C | Verified real mechanism and enforcement limits. |
| 6.2 Safety section | REPORT sixth section | policy/redaction/network tests | reviewed fictional evidence | Verified scoped protection; no enterprise isolation/general PII claim. |
| 6.2 Cuts section, including what comes next | REPORT final section | documentation audit | Final design document | Verified deliberate exclusions and next priorities; no stretch implementation. |
| 6.3 `/evidence/` artifact plus discovery/replay logs | [evidence index](evidence/README.md), A/D/R | schema/provenance/reference/hash verifier | Genuine D, fresh R | Verified; historical raw records remain unchanged; machine-specific ancillary review metadata is excluded from publication. |
| 6.3 Exceptional-state example (requested ideally) | fixture scenarios/runtime profile | runtime matrix | B/T/F/C plus V | Demonstrated beyond the minimum happy path. Optional screen recording not supplied. |

All genuine model and human evidence is identified above. Browser tests simulate
UI actions and operator gestures under `automated_test`; they do not increase the
count of genuine discovery or manual demonstrations. Read [the evidence index](evidence/README.md)
for unsuccessful attempts and [final validation](evidence/phase5/README.md) for
remaining risks, exact commands, and the publication gate.
