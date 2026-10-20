# Evidence index

Start with the first four rows. These are genuine model/UI and human/browser
executions, preserved byte-for-byte. Model decision records include actual API
response IDs and fresh observations; model thought transcripts are not stored.
The complete [capability](phase2/savings.capability.json) and
[reviewed runtime profile](phase3/savings.runtime.json) are declarative JSON.

| Example | Why it matters | Records |
| --- | --- | --- |
| Genuine complete discovery, member 12345 | Eight real model decisions, nine compiled steps, independent success: 123456 cents/USD | [log](phase2/discovery/58835bd89e854f5a825075b8e58b89ae/events.jsonl), [result](phase2/discovery/58835bd89e854f5a825075b8e58b89ae/result.json), [artifact](phase2/savings.capability.json) |
| Fresh parameterized replay, member 06789 | Same artifact, separate browser, leading zero preserved, 987654 cents/USD, no model | [log](phase2/replay/2294bfa9500f4457872166ed75ad439c/events.jsonl), [result](phase2/replay/2294bfa9500f4457872166ed75ad439c/result.json) |
| Real same-session human resolution | Human checkbox/submit, original session/page, verified reconciliation, continuation after s005, success | [log](phase4/manual-success/3edd60f0fa29496687b2a713d4e5f8f2/events.jsonl), [result](phase4/manual-success/3edd60f0fa29496687b2a713d4e5f8f2/result.json), [confirmations](phase4/operator-confirmations.json) |
| Real operator cancellation | Operator requested cancel; no step after s005 and structured Failure | [log](phase4/manual-cancel/7a6b3c13366a41c08d7af39414a3eb8c/events.jsonl), [result](phase4/manual-cancel/7a6b3c13366a41c08d7af39414a3eb8c/result.json) |
| Known not-found outcome | A legitimate caller result rather than a crash | [log](phase2/not-found/b80488b73a2744a28d9e84587b6becc9/events.jsonl), [result](phase2/not-found/b80488b73a2744a28d9e84587b6becc9/result.json) |
| Business validation rejection | Explicit business classification | [log](phase3/validation/df2da83535f24f99ab2be7cc24b1cd2b/events.jsonl), [result](phase3/validation/df2da83535f24f99ab2be7cc24b1cd2b/result.json) |
| Transient failure recovered | One inspected GET retry, then independently verified success | [log](phase3/transient_failure/4f7222459b1040c4be54df4bec420428/events.jsonl), [result](phase3/transient_failure/4f7222459b1040c4be54df4bec420428/result.json) |
| Session expiry | Session-category failure with step/checkpoint, screenshot and escalation classification; not a login/resume claim | [log](phase3/session_expired/9fa16af257c6483899c9e0de6916289a/events.jsonl), [result](phase3/session_expired/9fa16af257c6483899c9e0de6916289a/result.json) |
| Recovery exhausted | Two unsuccessful read retries stop with recorded history | [log](phase3/retry_exhausted/da13f52ee3794e53ab916c0bd1b7916a/events.jsonl), [result](phase3/retry_exhausted/da13f52ee3794e53ab916c0bd1b7916a/result.json) |
| Harmless interstitial | One reviewed continuation before success | [log](phase3/known_interstitial/ed35d9ea322a4ad6a6be1c85762bce09/events.jsonl), [result](phase3/known_interstitial/ed35d9ea322a4ad6a6be1c85762bce09/result.json) |
| Normal/slow CLI runs | Runtime baseline; the slow example completed before executor waiting was needed | [normal](phase3/normal/3bea3967390b44deb45df509fa7b6884/events.jsonl), [slow](phase3/slow_loading/40b00b5628f94d1a89500262b52cbef4/events.jsonl) |
| Final validation and fresh setup | Two passing 181-case suites, clean install, new offline runs, another real human handoff, safety inventory | [validation](phase5/README.md), [public file hashes](phase5/public-manifest.json) |

The successful manual run's session is `cbabf8c6ed404505aa127adb2499b207`, page
`952f5923866b4103a8d3c63281d60b2f`. Events 18-24 pause/request/transfer ownership;
25-30 record actual human click/change/submit/navigation; 31-38 reconcile and
return automation ownership; 40-54 continue and verify success. The operator
answered Done in the IDE; only then was `resume` forwarded to the CLI. The
negative example similarly records the operator's cancellation choice. See the
[blocked screenshot](phase4/manual-success/3edd60f0fa29496687b2a713d4e5f8f2/4a777d85238ab6ddb9ad640a8ae5879adc6b406c88084abfa9120419f1f43482.png)
and [reconciled member record](phase4/manual-success/3edd60f0fa29496687b2a713d4e5f8f2/c54d7b4dfc7ea0fd0add636fd331123560dc96533af41cc4730b16a6a12aff35.png).

## Provenance and retained unsuccessful attempts

`live_llm` labels actual model calls, including unsuccessful attempts;
`deterministic_replay` labels CLI execution, with HUMAN ownership/manual event
types identifying the operator portion. `manual_probe` labels the earlier capture
probe. Scripted model providers and automated operator gestures run only in tests
with `automated_test`; the final test report is not counted as real discovery.

Earlier small examples are retained to make failures and progress inspectable:

- [One successful screenshot/control-ID selection](phase1/visual-targeting/live/c3d1ef02cfa8459e90a9972e7527ea4a/events.jsonl): real model chose c3 in the iframe; an independent checkpoint transitioned false to true. This is one action, not a complete workflow.
- [Original manual capture](phase1/manual/132cd8aa025d4fc1a11e561cd1730523/events.jsonl): real same-session activity; no resumed automation was implemented at that point.
- [First exhausted-credit attempt](phase1/live/6c1b6d9b73a74b29b095a5ad0877a848/events.jsonl) and [diagnostic attempt](phase1/live/6f2d6c5054a34740be70cb8d33ca5750/events.jsonl): actual API errors, no verified action.
- [Rejected real coordinate selection](phase1/refinement/live/719fb6bf441b4689bffcc12ce61badb6/events.jsonl): model chose whitespace; grounding rejected it. The old `api_error_code` field here contains the local resolution code, not an API failure.
- Four [preliminary headed entry failures](phase4/runs.json): `3e008b49b2aa4799aa76a43c5596df24`, `d312427906fb40258a38d5fd4726baf2`, `2c515fb7c5b94f6db28d28f84135fefd`, and `bd3f594db92b444fb4acf0ada5ff60f0`. They never reached a handoff. Flask request threading fixed idle-connection starvation before the recorded successful/manual-cancel runs.

The slow-load waiting strategy is exercised with controlled browser time in tests;
the naturally completing CLI slow run is not relabelled as evidence of recovery.
Screenshot filenames are SHA-256 hashes; duplicate images in different run
directories preserve original evidence references. They are not duplicate claims
of independent model discoveries.

## Integrity, privacy, and curation

Run `.\.venv\Scripts\python.exe scripts/verify_submission.py --write-manifest`.
It checks screenshot hashes, event sequencing/references, strict schemas, genuine
discovery response provenance, scoped secret/path patterns, and Git/public inventory.
The original capability file hash is
`e965f3f08a4ec5c139c507446752c213f29886e4504f90f8b1eb7dadab331cb3`.

Historical raw execution logs, results, artifacts and screenshots are unchanged.
Old phase-specific review READMEs, verification summaries and JUnit reports, plus
one absolute-path command manifest, remain in the original local workspace but
are excluded from publication. They contain machine metadata or private review
references and are unnecessary to assess actual runs. This new index replaces
them for reviewers; nothing is relabelled to appear successful. The Phase 4
relative-command manifest and operator confirmations are safe to retain.

Only fictional records are used. Fields are masked in screenshots; literal binding
values in logs may be redacted, while the separately validated artifact retains
reusable UI labels. Activity contains tags, not raw values or keystrokes. These are
demo-specific safeguards, not a general detector of secrets anywhere in an image.
