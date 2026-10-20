# Final validation

All commands below were actually executed. No new paid model request was made.
The original genuine discovery and real cancellation remain in the
[evidence index](../README.md), unchanged. A further real human handoff succeeded
from the clean installation; automated operator gestures are not substituted for it.

## Regression and clean installation

| Check | Actual result | Machine-safe record |
| --- | --- | --- |
| Original workspace full suite | 181 passed in 577.87 seconds; zero failures/errors/skips | [individual cases](workspace-tests.json) |
| Clean project full suite | 181 passed in 582.45 seconds; zero failures/errors/skips | [individual cases](clean-tests.json) |
| Preserved regressions | All 171 Phase 4 test identities remain; 10 new cases | Both reports above |
| Installation | New Python 3.13.0 venv, locked packages, editable project install, pip check, fresh Chromium download | [setup](setup.json) |
| Environment isolation | No system/user site packages; package import resolves to clean source; separate new browser directory | [setup](setup.json) |
| Genuine evidence integrity | All 133 historical evidence files match the approved Phase 4 archive, including private ancillary metadata | [validation](validation.json) |

The clean copy was created under the ignored private working area from the reviewed
public inventory. It did not copy the original venv, browsers, caches, private PDF,
context, review archives or preparation notes. Normal pip download caches were
allowed. Dependency and browser installation required network; replay and tests
used local fixtures. Raw JUnit files remain private because they contain hostnames;
the public JSON reports preserve actual test names, counts and timings without those
identifiers. These are derived test summaries, not modified historical executions.

The documented setup steps were exercised with a fresh environment: `python -m venv`,
`pip install -r requirements.lock.txt`, `pip install -e '.[test]'`, `pip check`,
`PLAYWRIGHT_BROWSERS_PATH` directed to `.venv/browsers`, and `playwright install chromium`.
The venv was created from the parent directory at the equivalent clean-project path;
package installation then targeted that interpreter. The following full suites ran:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --junitxml=.private/phase5/workspace-tests.xml
.\.venv\Scripts\python.exe -m pytest -q --junitxml=.runs/phase5-clean-tests.xml
```

The first command ran in the original workspace; the second ran in the clean project.
The two output paths are private test metadata, not mandatory public runtime files.

## Exact README commands, independent offline executions

Fixture processes were started with:

```powershell
.\.venv\Scripts\python.exe -m interface_cu.cli serve --port 5065 --scenario normal
.\.venv\Scripts\python.exe -m interface_cu.cli serve --port 5066 --scenario transient_failure
.\.venv\Scripts\python.exe -m interface_cu.cli serve --port 5067 --scenario operator_challenge
```

These separate processes were stopped after the demonstrations. The original
capability and runtime profile were used without changes:

```powershell
.\.venv\Scripts\python.exe -m interface_cu.cli replay --origin http://127.0.0.1:5065 --artifact evidence/phase2/savings.capability.json --member-id 06789 --evidence .runs/replay
.\.venv\Scripts\python.exe -m interface_cu.cli replay --origin http://127.0.0.1:5065 --artifact evidence/phase2/savings.capability.json --member-id 99999 --evidence .runs/not-found
.\.venv\Scripts\python.exe -m interface_cu.cli replay --origin http://127.0.0.1:5066 --artifact evidence/phase2/savings.capability.json --member-id 06789 --runtime-profile evidence/phase3/savings.runtime.json --evidence .runs/recovery
.\.venv\Scripts\python.exe -m interface_cu.cli replay --origin http://127.0.0.1:5067 --artifact evidence/phase2/savings.capability.json --runtime-profile evidence/phase3/savings.runtime.json --member-id 06789 --handoff --headed --operator-timeout 180 --evidence .runs/handoff
```

| Actual execution | Result | Archived raw log |
| --- | --- | --- |
| Fresh replay, `fae5e757e9ba4034a6f31941cafed30f` | Success: 06789, 987654 cents, USD; exit 0 | [events](reproduction/runs/replay/fae5e757e9ba4034a6f31941cafed30f/events.jsonl) |
| Not found, `e1c885877da7498c86c3a352907daae3` | BusinessOutcome MEMBER_NOT_FOUND; exit 0 | [events](reproduction/runs/not-found/e1c885877da7498c86c3a352907daae3/events.jsonl) |
| Transient recovery, `8650a7a2a6ff44828701767407b11763` | One GET-link retry cleared the 503; independently verified Success; exit 0 | [events](reproduction/runs/recovery/8650a7a2a6ff44828701767407b11763/events.jsonl) |
| Real handoff, `d42acbd9ab904ef387549854870aa2c9` | Human acknowledged in the same browser, independently reconciled, resumed after s005, Success; exit 0 | [events](reproduction/runs/handoff/d42acbd9ab904ef387549854870aa2c9/events.jsonl) |

All successful workflows execute s001-s009 once, preserve member identity, and record
zero model calls. The real operator replied Done after checkbox/submit interaction;
only then was `resume` forwarded to the waiting CLI. The evidence has one original
session/page, manual click/change/submit/navigation, ownership transitions, verified
reconciliation and final typed result. [commands.json](commands.json) includes the
actual responses, exit codes, commands and confirmation. The preserved Phase 4
negative example demonstrates actual operator cancellation.

The raw new files were copied byte-for-byte from `.runs/` in the isolated project.
Their original relative references are unchanged. For archive validation only,
`.runs/` resolves to `evidence/phase5/reproduction/runs/` in the submission. The
verifier applies this explicit mapping; it does not rewrite records or targeting.

## Model boundary and targeted engineering fixes

The optional discovery command was tested with OPENAI_API_KEY explicitly removed.
It returned Failure COMMAND_FAILED with a ValueError preflight event and no model
request or new artifact: [record](reproduction/runs/discovery/92a7b024ef7e4cb5a8d8b200b705967e/events.jsonl).
Its `live_llm` source denotes CLI mode, not a successful API call. This is a
credential-preflight check, not another genuine discovery. Successful paid discovery
remains the original eight-response proof; the same SDK/controller integration also
passes mocked-provider tests in the fresh environment. Credentials were not persisted.

Targeted code fixes reject non-finite/bool deadlines and non-integer discovery step
budgets, accept Windows UTF-8 BOM input/profile JSON, and correct an outdated model
adapter docstring. Ten focused regressions cover those behaviors, including actual
CLI balance extraction from BOM-prefixed files. Core architecture and historical
capability/runtime contracts are preserved. No framework or runtime dependency was added.

## Requirements, publication and limits

The [coverage matrix](../../REQUIREMENTS.md) maps every mandatory Section 3/6 item.
[validation.json](validation.json) records report headings, historical integrity,
regression preservation, safety checks and remaining manual steps.
[public-manifest.json](public-manifest.json) lists reviewed file hashes. The final
`REPORT.md` contains **877 words** and retains all seven required headings.
The earlier Phase 5 validation record (`validation.json`) reports **1,045 words**
because it was generated before the final editorial revision. The updated
`public-manifest.json` reflects the final report. Historical validation records
have been preserved unchanged for traceability.

At the time of final validation, publication was still a manual gate: the validation
tooling did not commit, push, change repository visibility, or send email. Publication
was subsequently performed manually by the owner. Private preparation remains ignored
and absent from the public repository. Private preparation is ignored and absent
from the reviewed copy. Old review ZIPs contain caches and must not be distributed;
private context/PDF and machine-specific historical metadata remain local. Raw
execution evidence is preserved; selected public files pass scoped secret/path checks.
This does not claim exhaustive secret detection or generic image PII redaction.

Known limits remain one synthetic workflow, DOM-backed targeting and fixed viewport,
one reviewed acknowledgement, no process-restart continuation or secure physical-input
isolation, and limited cross-application reliability evidence. No live Linux/macOS or
other Python-version validation is claimed. The CLI's early preflight wrapper uses
generic COMMAND_FAILED while recording a sanitized exception type; detailed runtime
replay failures retain their specific contracts. There are no known failing tests or
missing implemented mandatory capability; GitHub publication is complete; submission
of the repository URL remains the final owner action.
