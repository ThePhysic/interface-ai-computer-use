# Computer-use capability discovery and replay

A real vision-capable LLM discovers a savings-balance workflow in a fictional legacy
banking UI. The successful execution compiles into typed, versioned JSON. Replay
runs that capability with different member inputs, independent checkpoints, and
**no LLM**. Runtime failures, bounded recovery, and same-session human handoff are
implemented. This is one local engineering demonstration, not production banking software.

## Recommended demo (no API key)

After installation below, start the app in terminal A:

```powershell
.\.venv\Scripts\python.exe -m interface_cu.cli serve --port 5065 --scenario normal
```

In terminal B, from the project root:

```powershell
.\.venv\Scripts\python.exe -m interface_cu.cli replay --origin http://127.0.0.1:5065 --artifact evidence/phase2/savings.capability.json --member-id 06789 --evidence .runs/replay
.\.venv\Scripts\python.exe -m interface_cu.cli replay --origin http://127.0.0.1:5065 --artifact evidence/phase2/savings.capability.json --member-id 99999 --evidence .runs/not-found
```

The first returns `success` with member `06789`, `balance_minor_units: 987654`, and
`currency: USD`. The second returns `business_outcome` / `MEMBER_NOT_FOUND`.
Both exit 0. A structured Failure exits 1. Each invocation opens a fresh browser;
`--headed` makes it visible. Stop a fixture server with Ctrl+C when finished.

Inspect the [genuine discovery, saved artifact, replay, and human evidence](evidence/README.md)
without rerunning paid discovery. Continue with transient recovery and handoff below.
The complete [requirements audit](REQUIREMENTS.md) maps implementation, tests,
actual demonstrations, and limitations.

## Prerequisites and installation

Tested on **Windows with Python 3.13 and PowerShell**. Python 3.11+ is declared;
other Python versions and operating systems were not independently validated.
Chromium needs several hundred MB of disk and network access for its initial
installation. A graphical desktop is required for manual handoff. No real banking
account or login is used. Fictional records are `12345` and `06789`.

Run from the project root; activation and PowerShell execution-policy changes are
unnecessary:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
.\.venv\Scripts\python.exe -m pip check
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) '.venv\browsers'
.\.venv\Scripts\python.exe -m playwright install chromium
```

The browser adapter defaults to `<Python environment>/browsers`; the explicit
variable also directs the installer there. `requirements.lock.txt` pins the verified
Windows environment; `pyproject.toml` declares supported ranges. Package downloads
may use a normal pip cache; the isolated validation used a new venv and browser
installation, without system site packages. See [final validation](evidence/phase5/README.md).

On Linux/macOS, the equivalent interpreter is `.venv/bin/python`; Playwright also
requires platform libraries. Those platforms and their OS dependency installation
were not tested, so this submission makes no cross-platform reliability claim.

## Optional genuine discovery (paid API)

Keep terminal A's normal fixture running. Set `OPENAI_API_KEY` in the discovery
process through your secret manager or this interactive PowerShell input:

```powershell
$interfaceModelKey = Read-Host 'OpenAI API key' -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $interfaceModelKey).Password
Remove-Variable interfaceModelKey
```

Then discover and replay the resulting artifact:

```powershell
.\.venv\Scripts\python.exe -m interface_cu.cli discover --origin http://127.0.0.1:5065 --goal 'Look up member 12345 and read their current savings balance' --member-id 12345 --artifact .runs/discovered.capability.json --evidence .runs/discovery --max-steps 12 --timeout 180
.\.venv\Scripts\python.exe -m interface_cu.cli replay --origin http://127.0.0.1:5065 --artifact .runs/discovered.capability.json --member-id 06789 --evidence .runs/discovered-replay
```

An existing artifact is never overwritten; choose a new path for another discovery.
Missing credentials fail before any model request. Optional `INTERFACE_VISION_MODEL`
or `--model` selects a compatible vision/Responses structured-output model; the
recorded genuine run used `gpt-5-mini-2025-08-07`. No new paid calls were needed for
final validation. A successful discovery is evidence of feasibility, not model reliability.

The model receives a masked screenshot, bounded visible controls/readable cells,
parameter names, output definitions, and its executed history. It chooses each next
action and extraction target; no navigation sequence or acceptance locators are
provided. Code supplies typed values, parses extracted UI text, and independently
verifies member/account identity and success. Default bounds are 16 decisions and
180 seconds. Only finite positive deadlines and bounded integer step counts are accepted.

Replay imports no model provider, removes supported provider keys from its own
process, and denies non-loopback Python network connections. The browser has its
own origin/route/method policy. Installation and optional discovery need network;
replay and tests need only local processes after setup. `--inputs file.json` accepts
UTF-8 JSON, including a Windows UTF-8 BOM, instead of `--member-id`.

## Runtime outcomes and recovery

Start another fixture in terminal C:

```powershell
.\.venv\Scripts\python.exe -m interface_cu.cli serve --port 5066 --scenario transient_failure
```

Run its reviewed runtime profile:

```powershell
.\.venv\Scripts\python.exe -m interface_cu.cli replay --origin http://127.0.0.1:5066 --artifact evidence/phase2/savings.capability.json --member-id 06789 --runtime-profile evidence/phase3/savings.runtime.json --evidence .runs/recovery
```

This succeeds after one inspected GET-link retry and independent checkpoints.
Restart the server to reset a transient scenario; no fixture-state API is exposed.
`serve --help` lists all injections. Replace its scenario to exercise other states:

| Scenario | Deliberate response |
| --- | --- |
| `validation`, `member_not_found`, `account_not_found`, `permission_denied` | Known BusinessOutcome, not a crash. Malformed invocation parameters instead fail validation. |
| `slow_loading`, `loading_exhausted` | At most three waits of 0.25 seconds and reinspection; exhaustion stops. |
| `transient_failure`, `retry_exhausted` | At most two inspected read-link retries; exhaustion stops. |
| `known_interstitial` | One reviewed harmless continuation, followed by original checkpoints. |
| `unknown_interstitial`, `native_dialog` | Explicit failure. Unknown native dialogs are dismissed during automation and latched. |
| `session_expired`, `persistent_error` | Specific session/application Failure; no automatic login or repair. |
| `unsafe_redirect` | Policy stop before the forbidden destination is requested. All redirects are conservatively rejected. |

The runtime profile is an optional schema-1.0 sidecar tied to exact capability/app
versions. Its six-attempt shared budget and per-rule/run deadlines are bounded.
It contains no code or general branching language. Uncertain original actions are
never reissued: only a verified false-to-true postcondition can establish completion.
Unresolved uncertainty stops. Failure includes capability/version, step, code/category,
expected/observed state, uncertainty, recovery history, evidence, and escalation suitability.

## Real same-session handoff

Start another fixture:

```powershell
.\.venv\Scripts\python.exe -m interface_cu.cli serve --port 5067 --scenario operator_challenge
```

Then run:

```powershell
.\.venv\Scripts\python.exe -m interface_cu.cli replay --origin http://127.0.0.1:5067 --artifact evidence/phase2/savings.capability.json --runtime-profile evidence/phase3/savings.runtime.json --member-id 06789 --handoff --headed --operator-timeout 180 --evidence .runs/handoff
```

Automation pauses at **Operator acknowledgement required**. In that existing browser:
check **I acknowledge read-only access**, click **Acknowledge read-only access**, and
stop on **Member record**. Enter `resume` or press Enter in the CLI. For a negative
demo, run again and enter `cancel`; `unresolved`, EOF, and timeout also stop explicitly.
The operator wait keeps browser events live and pauses only the automation deadline.

Ownership follows AUTOMATION -> PAUSED -> HUMAN -> RESUMING -> AUTOMATION; verified
completion ends COMPLETED, cancellation/rejected resumption ends FAILED. The lock
and generation checks prevent guarded actions during human control. Browser/context/
page, step cursor, bindings, extracted outputs, and recovery history stay in memory.
Resume verifies session, origin, application, safety latches, member identity, blocker
resolution, and the relevant checkpoint. Two observations must agree. It does not
restart from step zero or trust the operator's completion signal as success.

Only this reviewed challenge is routed; policy denials cannot be overridden.
Manual click/change/submit tags and navigation are recorded without values or
keystrokes, restricted to the original page. A human-assisted discovery can finish
through the same coordinator, but cannot be compiled into unattended automation.
Existing real takeover/resumption and cancellation evidence is linked in the index;
discovery handoff tests use a scripted provider, clearly labelled as testing.

## Architecture and artifact

| Component | Responsibility |
| --- | --- |
| `discovery.py`, `vision.py` | Bounded screenshot-led model decisions on fresh observations. |
| `browser.py`, `contracts.py` | Surface seam, masked observations, exact observation-scoped IDs, frame/identity/uniqueness checks. |
| `execution.py`, `policy.py` | Shared guarded boundary for normal actions and recoveries; inspected effects determine policy. |
| `compiler.py`, `capability.py` | Verified trace to strict, reviewable JSON; rejects weak targets, concrete identifiers, and assisted traces. |
| `replay.py`, `runtime.py` | Model-free steps, checkpoint verification, typed outputs, classifications and bounded recovery. |
| `handoff.py`, `intervention.py`, `activity.js` | Ownership, contextual requests, operator input, capture and reconciliation. |
| `evidence.py`, `demo_app.py` | Sanitized evidence and a separate fictional server-rendered target. |

The app has a genuine iframe, table layout, an unlabelled field, and no test IDs.
Control IDs expire with their observation; they never enter the capability. Locator
candidates use semantic names, identifying rows, and limited caption-relative structure.
Coordinate fallback performs exact same-origin DOM hit-testing, with the same checks;
there is no nearest-neighbor guess or direct coordinate clicking.

The artifact declares schema/capability/app versions, typed inputs/outputs, ordered
stable step IDs, targets with robustness rationale, literal/input/extracted bindings,
pre/postconditions, deterministic parsers, success/business predicates, and discovery
provenance. Member IDs remain strings; USD balances become integer cents. There is
no executable code or raw model transcript. See [REPORT.md](REPORT.md) for trade-offs.

## Tests, evidence integrity, and publication

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -m 'not browser' -q
.\.venv\Scripts\python.exe scripts/verify_submission.py --write-manifest
```

Tests start separate local fixtures and need installed Chromium, not model keys.
Scripted providers and automated operator gestures are tests, not genuine live proof.
Final full runs passed 181 tests in the original workspace (577.87 seconds) and
the clean environment (582.45 seconds), with zero failures/errors/skips. All 171
Phase 4 cases remain, with 10 focused submission regressions added.
[Final validation](evidence/phase5/README.md) records actual results and reproduction.
The verifier checks artifact/provenance, screenshot hashes, evidence references,
publication inventory, and scoped secret/path patterns. It is not a general PII detector.

Historical execution logs/artifacts/screenshots remain unchanged. Old machine-specific
JUnit reports, verification summaries, review notes, and one absolute-path command
manifest remain local and are ignored. The public [evidence index](evidence/README.md)
links reviewed raw records, including unsuccessful attempts with explicit labels.
`.gitattributes` preserves historical evidence bytes across Git newline conversion.

`.gitignore` excludes private context/PDF, coaching notes, environments, browsers,
caches, unreviewed `.runs`, machine-specific review metadata, and ZIPs. Before staging,
run the verifier and inspect `git status --short --ignored` and `git diff --cached`.
Publication, repository visibility, and submission are manual; no script pushes or emails.

## Limits

- One synthetic application and savings workflow; no claim of broad cross-app reliability.
- DOM-backed targeting and a fixed 1100x800 viewport. Canvas-only, inaccessible frames,
  and desktop automation are not implemented. Unknown/ambiguous targets fail closed.
- Only reviewed read-only routes and USD formats. Financial mutation is hard-blocked.
- Human acknowledgement is supported; arbitrary manual repair, login, and durable
  process-restart continuation are not. Losing the process loses the continuation.
- A local ownership lock cannot freeze physical human input during reconciliation.
  Operators must stop interacting; detected state changes fail closed. This is not
  enterprise-grade operator isolation or hostile-page security.
- Sanitization is specific to fictional fixtures; screenshots mask fields, not every
  possible secret or PII location. DOM capture can lose events during navigation;
  complete forensic recording is not claimed. Never use real records or credentials.
