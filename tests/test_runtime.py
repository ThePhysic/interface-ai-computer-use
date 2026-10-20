"""Deterministic server failure injection; no live model requests in this file."""

import asyncio
import hashlib
import json
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from pydantic import ValidationError

from interface_cu.banking_runtime import savings_runtime_profile, visible
from interface_cu.browser import open_browser
from interface_cu.capability import Capability
from interface_cu.contracts import (
    Click, Extract, LiteralValue, Navigate, Press, RoleLocator, SurfaceError, TargetSpec,
)
from interface_cu.demo_app import create_app
from interface_cu.evidence import Evidence
from interface_cu.execution import GuardedExecutor
from interface_cu.handoff import SessionControl
from interface_cu.policy import Policy
from interface_cu.replay import Replay
from interface_cu.runtime import RecoveryRule, RuntimeProfile


ARTIFACT_PATH = Path(__file__).resolve().parents[1] / "evidence/phase2/savings.capability.json"


@pytest.fixture
def artifact():
    # The actual proven artifact, not a freshly generated test-only equivalent.
    return Capability.model_validate_json(ARTIFACT_PATH.read_bytes())


@contextmanager
def scenario_server(scenario, request_log=None):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    log = request_log.open("wb") if request_log else None
    process = subprocess.Popen([sys.executable, "-m", "interface_cu.cli", "serve", "--port", str(port), "--scenario", scenario],
                               stdout=subprocess.DEVNULL, stderr=log or subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                with urlopen(origin, timeout=0.2) as response:
                    assert response.status == 200
                break
            except (URLError, TimeoutError):
                if process.poll() is not None:
                    raise AssertionError("Scenario server exited")
                time.sleep(0.05)
        else:
            raise AssertionError("Scenario server not ready")
        yield origin
    finally:
        process.terminate()
        process.wait(timeout=5)
        if log:
            log.close()


def events(evidence):
    return [json.loads(line) for line in (evidence.directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("mutation", ["version", "callback", "attempts", "total", "interval", "timeout", "action_shape", "duplicate_code", "extracted_reference", "input_reference"])
def test_runtime_profile_rejects_unsupported_or_unbounded_rules(artifact, mutation):
    data = savings_runtime_profile().model_dump(mode="json")
    if mutation == "version": data["schema_version"] = "2.0"
    if mutation == "callback": data["recovery_rules"][0]["callback"] = "eval"
    if mutation == "attempts": data["recovery_rules"][0]["max_attempts"] = 99
    if mutation == "total": data["max_total_attempts"] = 999
    if mutation == "interval": data["recovery_rules"][0]["interval_seconds"] = 99.0
    if mutation == "timeout": data["recovery_rules"][0]["timeout_seconds"] = 99.0
    if mutation == "action_shape": data["recovery_rules"][1]["action"] = None
    if mutation == "duplicate_code": data["stop_rules"][0]["code"] = data["stop_rules"][1]["code"]
    if mutation in {"extracted_reference", "input_reference"}:
        data["stop_rules"][0]["condition"]["target"]["candidates"][0]["name"] = {
            "kind":"extracted" if mutation == "extracted_reference" else "input", "name":"unavailable"}
    with pytest.raises((ValueError, ValidationError)):
        RuntimeProfile.model_validate(data).validate_for(artifact)


def test_profile_round_trip_and_original_capability_integrity(artifact):
    profile = savings_runtime_profile()
    assert RuntimeProfile.model_validate_json(profile.model_dump_json()) == profile
    profile.validate_for(artifact)
    assert hashlib.sha256(ARTIFACT_PATH.read_bytes()).hexdigest() == "e965f3f08a4ec5c139c507446752c213f29886e4504f90f8b1eb7dadab331cb3"


@pytest.mark.parametrize("scenario,path,text,status", [
    ("validation", "/members?member_id=06789", "Member lookup rejected", 200),
    ("member_not_found", "/members?member_id=06789", "No such member", 200),
    ("account_not_found", "/members/06789/savings", "Savings account not found", 404),
    ("permission_denied", "/members/06789", "Access denied", 403),
    ("session_expired", "/members/06789", "Session expired", 401),
    ("persistent_error", "/members/06789/savings", "Application error", 500),
])
def test_server_scenarios_are_visible_application_responses(scenario, path, text, status):
    response = create_app(scenario).test_client().get(path)
    assert response.status_code == status
    assert text.encode() in response.data


@pytest.mark.browser
@pytest.mark.parametrize("scenario,status,code,attempts", [
    ("validation", "business_outcome", "VALIDATION_REJECTED", 0),
    ("member_not_found", "business_outcome", "MEMBER_NOT_FOUND", 0),
    ("account_not_found", "business_outcome", "ACCOUNT_NOT_FOUND", 0),
    ("permission_denied", "business_outcome", "PERMISSION_DENIED", 0),
    ("known_interstitial", "success", None, 1),
    ("slow_loading", "success", None, None),
    ("transient_failure", "success", None, 1),
    ("loading_exhausted", "failure", "RECOVERY_EXHAUSTED", 3),
    ("retry_exhausted", "failure", "RECOVERY_EXHAUSTED", 2),
    ("persistent_error", "failure", "APPLICATION_ERROR_PERSISTENT", 0),
    ("session_expired", "failure", "SESSION_EXPIRED", 0),
    ("unknown_interstitial", "failure", "UNEXPECTED_INTERSTITIAL", 0),
    ("native_dialog", "failure", "UNEXPECTED_NATIVE_DIALOG", 0),
])
async def test_complete_runtime_matrix(artifact, tmp_path, monkeypatch, scenario, status, code, attempts):
    # Fail immediately if any model constructor is reached.
    def forbidden(*args, **kwargs):
        raise AssertionError("No model in runtime replay")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("interface_cu.vision.OpenAIVision", forbidden)
    monkeypatch.setattr("interface_cu.vision.AsyncOpenAI", forbidden)
    with scenario_server(scenario) as origin:
        evidence = Evidence(tmp_path, "automated_test")
        policy, control = Policy(origin=origin), SessionControl(evidence)
        async with open_browser(policy, control, evidence) as surface:
            executor = GuardedExecutor(surface, policy, control, evidence)
            if scenario == "slow_loading":
                # Pause only browser time; real asyncio recovery budgets keep
                # running. Release the app's real reload timer during the first
                # bounded wait, so this test cannot pass by missing a short spinner.
                await surface._page.clock.install(time="2026-01-01T00:00:00Z")
                await surface._page.clock.pause_at("2026-01-01T00:00:01Z")
                real_sleep = asyncio.sleep
                async def advance_browser_time(delay, *args, **kwargs):
                    if delay == 0.25:
                        frame = surface._page.frame(name="workspace")
                        async with frame.expect_navigation(wait_until="load"):
                            await surface._page.clock.run_for(500)
                    else:
                        await real_sleep(delay, *args, **kwargs)
                monkeypatch.setattr("interface_cu.runtime.asyncio.sleep", advance_browser_time)
            result = await Replay(surface, executor, evidence).run(artifact, {"member_id":"06789"}, runtime_profile=savings_runtime_profile())
            assert result.status == status, result
            if status == "success":
                assert result.outputs == {"member_id":"06789", "balance_minor_units":987654, "currency":"USD"}
            else:
                assert result.code == code, result
            history = [e for e in events(evidence) if e["event"] == "recovery_attempt"]
            if attempts is not None:
                assert len(history) == attempts
            else:
                assert 1 <= len(history) <= 3
            if status == "failure":
                assert result.capability_id == artifact.capability_id
                assert result.capability_version == artifact.capability_version
                assert result.step_id in {s.id for s in artifact.steps}
                assert result.expected and result.observed and result.evidence_ref
                assert result.human_intervention_appropriate
                observed = json.loads(result.observed)
                assert Path(observed["screenshot_ref"]).exists()
                assert len(result.recovery_attempts) == len(history)
                assert result.category == ("session" if scenario == "session_expired" else "application" if scenario == "persistent_error" else "runtime")
            if scenario == "native_dialog":
                assert result.completion_uncertain
                assert "Confirmation cancelled" in result.observed
                assert (await executor.execute(Navigate(url=origin + "/"))).code == "UNEXPECTED_NATIVE_DIALOG"
            if history:
                assert any(e["event"] == "runtime_diagnostic" for e in events(evidence))
            assert all(e["source"] == "automated_test" for e in events(evidence))
            assert not any(e["event"] == "discovery_model_request" for e in events(evidence))


@pytest.mark.browser
@pytest.mark.parametrize("committed,already_true", [(True, False), (False, False), (True, True)])
async def test_uncertain_completion_requires_new_checkpoint_and_never_retries(browser_env, artifact, monkeypatch, committed, already_true):
    surface, executor, _, evidence = browser_env
    original = surface.perform
    action_name = "Search" if already_true else "View savings"
    calls = 0
    async def uncertain(action, *args, **kwargs):
        nonlocal calls
        candidate = action.target.candidates[0] if isinstance(action, Click) else None
        if isinstance(candidate, RoleLocator) and candidate.name.value == action_name:
            calls += 1
            if committed:
                await original(action, *args, **kwargs)
            raise SurfaceError("BROWSER_ERROR", completion_uncertain=True)
        return await original(action, *args, **kwargs)
    monkeypatch.setattr(surface, "perform", uncertain)
    result = await Replay(surface, executor, evidence).run(artifact, {"member_id":"06789"}, runtime_profile=savings_runtime_profile())
    assert calls == 1
    if committed and not already_true:
        assert result.status == "success", result
        assert any(e["event"] == "recovery_attempt" and e["data"]["result"] == "verified" for e in events(evidence))
    else:
        assert result.code == "ACTION_COMPLETION_UNCERTAIN"
        assert result.completion_uncertain
        assert len(result.recovery_attempts) == 2
        assert all(a.strategy == "checkpoint_reinspection" and a.result == "not_proven" for a in result.recovery_attempts)


@pytest.mark.browser
async def test_policy_blocks_unexpected_redirect_without_recovery(artifact, tmp_path):
    request_log = tmp_path / "server.log"
    with scenario_server("unsafe_redirect", request_log) as origin:
        evidence = Evidence(tmp_path, "automated_test")
        policy, control = Policy(origin=origin), SessionControl(evidence)
        async with open_browser(policy, control, evidence) as surface:
            result = await Replay(surface, GuardedExecutor(surface, policy, control, evidence), evidence).run(
                artifact, {"member_id":"06789"}, runtime_profile=savings_runtime_profile())
            assert result.code == "ROUTE_DENIED", result
            assert result.category == "policy" and result.completion_uncertain
            assert not result.recovery_attempts
            assert not result.human_intervention_appropriate
            assert any(e["event"] == "network_blocked" for e in events(evidence))
            assert (await surface.navigation_state()).blocked_request_code == "ROUTE_DENIED"
            served = request_log.read_text(encoding="utf-8", errors="replace")
            assert "GET /members/06789/savings " in served
            assert "GET /transfer " not in served


@pytest.mark.browser
async def test_recovery_cannot_resubmit_get_form(browser_env, artifact):
    surface, executor, _, evidence = browser_env
    action = Click(target=TargetSpec(frames=["workspace"], candidates=[RoleLocator(role="button",name=LiteralValue(value="Search"))], rationale="A GET form is not a read retry link"))
    profile = savings_runtime_profile().model_copy(update={"recovery_rules":[RecoveryRule(
        code="BAD_RECOVERY", condition=visible("Member search"), strategy="retry_read", action=action)]})
    result = await Replay(surface, executor, evidence).run(artifact, {"member_id":"06789"}, runtime_profile=profile)
    assert result.code == "RECOVERY_REQUIRES_READ_LINK"
    assert result.category == "policy"
    assert result.recovery_attempts[0].result == "blocked"


@pytest.mark.browser
@pytest.mark.parametrize("path", ["direct", "button", "enter", "form_submit"])
async def test_policy_alternative_mutation_paths(browser_env, path):
    surface, executor, control, evidence = browser_env
    assert (await executor.execute(Navigate(url=surface.policy.origin + "/members/06789"))).status == "executed"
    frame = surface._page.main_frame
    target = TargetSpec(candidates=[RoleLocator(role="button", name=LiteralValue(value="Initiate transfer"))], rationale="Consequential submission")
    if path == "form_submit":
        await control.give_to_human()
        await frame.evaluate("document.querySelector('form').submit()")
        for _ in range(50):
            if any(e["event"] == "network_blocked" for e in events(evidence)):
                break
            await asyncio.sleep(0.02)
        assert any(e["event"] == "network_blocked" for e in events(evidence))
    else:
        action = Navigate(url=surface.policy.origin + "/transfer") if path == "direct" else Click(target=target) if path == "button" else Press(target=target,key="Enter")
        result = await executor.execute(action)
        assert result.status == "blocked"
        assert result.code in {"METHOD_OR_CREDENTIALS_DENIED", "ROUTE_DENIED"}


@pytest.mark.browser
async def test_profile_mismatch_and_version_fail_before_actions(browser_env, artifact, monkeypatch):
    surface, executor, _, evidence = browser_env
    async def forbidden(*args, **kwargs):
        raise AssertionError("Invalid profile cannot act")
    monkeypatch.setattr(surface, "perform", forbidden)
    profile = savings_runtime_profile().model_dump(mode="json")
    profile["capability_version"] = "9.0.0"
    assert (await Replay(surface, executor, evidence).run(artifact, {"member_id":"06789"}, runtime_profile=profile)).code == "RUNTIME_PROFILE_MISMATCH"
    profile["schema_version"] = "99"
    assert (await Replay(surface, executor, evidence).run(artifact, {"member_id":"06789"}, runtime_profile=profile)).code == "INVALID_RUNTIME_PROFILE"


@pytest.mark.browser
@pytest.mark.parametrize("corruption,expected", [("parse","OUTPUT_PARSE_ERROR"),("identity","OUTPUT_IDENTITY_MISMATCH"),("type","OUTPUT_TYPE_MISMATCH")])
async def test_output_errors_have_specific_codes(browser_env, artifact, monkeypatch, corruption, expected):
    surface, executor, _, evidence = browser_env
    original = executor.execute
    async def corrupt(action, *args, **kwargs):
        if isinstance(action, Extract) and action.name == "balance_minor_units" and corruption == "parse":
            await surface._page.frame(name="workspace").get_by_role("cell", name="USD 9,876.54", exact=True).evaluate("el => el.textContent='USD invalid'")
        result = await original(action, *args, **kwargs)
        if isinstance(action, Extract) and result.status == "executed":
            if corruption == "identity" and action.name == "member_id": return result.model_copy(update={"data":"12345"})
            if corruption == "type" and action.name == "balance_minor_units": return result.model_copy(update={"data":True})
        return result
    monkeypatch.setattr(executor, "execute", corrupt)
    result = await Replay(surface, executor, evidence).run(artifact, {"member_id":"06789"}, runtime_profile=savings_runtime_profile())
    assert result.code == expected, result
    assert result.category == "output"
    assert result.capability_id and result.step_id and result.evidence_ref


@pytest.mark.browser
async def test_failure_diagnostics_mask_inputs_and_explain_ambiguity(browser_env, artifact, monkeypatch):
    surface, executor, _, evidence = browser_env
    monkeypatch.setenv("CANARY_API_KEY", "not-a-real-secret-canary")
    evidence.secrets.append("not-a-real-secret-canary")
    original = surface.perform
    async def duplicate(action, *args, **kwargs):
        result = await original(action, *args, **kwargs)
        if isinstance(action, Navigate):
            await surface._page.frame(name="workspace").evaluate("""document.body.insertAdjacentHTML('beforeend',
                '<a href="/members">Find a member</a><input value="not-a-real-secret-canary">')""")
        return result
    monkeypatch.setattr(surface, "perform", duplicate)
    result = await Replay(surface, executor, evidence).run(artifact, {"member_id":"06789"}, runtime_profile=savings_runtime_profile())
    assert result.code == "PRECONDITION_FAILED"
    assert result.category == "targeting"
    assert "AMBIGUOUS_TARGET" in result.observed
    assert "Find a member" in result.expected
    assert "not-a-real-secret-canary" not in result.model_dump_json()
    assert "not-a-real-secret-canary" not in (evidence.directory / "events.jsonl").read_text()
    assert "screenshot_ref" in result.observed


@pytest.mark.browser
async def test_global_recovery_budget_is_independent_of_rule_budget(artifact, tmp_path):
    with scenario_server("retry_exhausted") as origin:
        evidence = Evidence(tmp_path, "automated_test")
        policy, control = Policy(origin=origin), SessionControl(evidence)
        async with open_browser(policy, control, evidence) as surface:
            profile = savings_runtime_profile().model_copy(update={"max_total_attempts":1})
            result = await Replay(surface, GuardedExecutor(surface,policy,control,evidence), evidence).run(
                artifact, {"member_id":"06789"}, runtime_profile=profile)
            assert result.code == "RECOVERY_EXHAUSTED"
            assert len(result.recovery_attempts) == 1
            assert result.recovery_attempts[0].result == "still_present"


@pytest.mark.browser
async def test_ownership_transfer_during_wait_stops_recovery(artifact, tmp_path, monkeypatch):
    with scenario_server("loading_exhausted") as origin:
        evidence = Evidence(tmp_path, "automated_test")
        policy, control = Policy(origin=origin), SessionControl(evidence)
        async with open_browser(policy, control, evidence) as surface:
            executor = GuardedExecutor(surface,policy,control,evidence)
            sleep = asyncio.sleep
            async def transfer_on_wait(delay, *args, **kwargs):
                if delay == 0.25:
                    await control.give_to_human()
                else:
                    await sleep(delay, *args, **kwargs)
            monkeypatch.setattr("interface_cu.runtime.asyncio.sleep", transfer_on_wait)
            result = await Replay(surface,executor,evidence).run(artifact,{"member_id":"06789"},runtime_profile=savings_runtime_profile())
            assert result.code == "AUTOMATION_DOES_NOT_OWN_SESSION"
            assert result.category == "ownership"
            assert len(result.recovery_attempts) == 1
            assert result.recovery_attempts[0].result == "blocked"
            assert (await executor.execute(Navigate(url=origin + "/"))).code == "AUTOMATION_DOES_NOT_OWN_SESSION"


@pytest.mark.browser
async def test_timeout_during_recovery_keeps_attempt_and_commit_uncertainty(browser_env, artifact, monkeypatch):
    surface, executor, _, evidence = browser_env
    target = TargetSpec(frames=["workspace"],candidates=[RoleLocator(role="link",name=LiteralValue(value="Find a member"))],rationale="Test-only read recovery")
    profile = savings_runtime_profile().model_copy(update={"recovery_rules":[RecoveryRule(
        code="INJECTED_READ_FAILURE",condition=visible("Staff home"),strategy="retry_read",action=Click(target=target))]})
    original = executor.execute
    async def timeout(action, *args, **kwargs):
        if kwargs.get("read_recovery"):
            await asyncio.wait_for(asyncio.sleep(10),0.02)
        return await original(action,*args,**kwargs)
    monkeypatch.setattr(executor,"execute",timeout)
    result = await Replay(surface,executor,evidence).run(artifact,{"member_id":"06789"},runtime_profile=profile)
    assert result.code == "RECOVERY_TIMEOUT" and result.completion_uncertain
    assert len(result.recovery_attempts) == 1
    assert result.recovery_attempts[0].result == "failed"
    assert result.recovery_attempts[0].completion_uncertain


@pytest.mark.browser
async def test_timeout_during_uncertain_reinspection_preserves_uncertainty(browser_env, artifact, monkeypatch):
    surface, executor, _, evidence = browser_env
    original = surface.perform
    async def uncertain(action,*args,**kwargs):
        if isinstance(action,Click):
            raise SurfaceError("BROWSER_ERROR",completion_uncertain=True)
        return await original(action,*args,**kwargs)
    async def timeout(*args,**kwargs):
        raise TimeoutError
    monkeypatch.setattr(surface,"perform",uncertain)
    monkeypatch.setattr("interface_cu.runtime.RuntimeMonitor.reconcile",timeout)
    result = await Replay(surface,executor,evidence).run(artifact,{"member_id":"06789"},runtime_profile=savings_runtime_profile())
    assert result.code == "REPLAY_TIMEOUT"
    assert result.completion_uncertain
