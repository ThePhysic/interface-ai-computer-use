"""UI integration with explicitly scripted model doubles, never live proof."""

import asyncio
import json
import subprocess
import sys

import pytest

from interface_cu.capability import Capability
from interface_cu.compiler import compile_capability
from interface_cu.contracts import Extract, Failure, Navigate, Success
from interface_cu.discovery import Discovery
from interface_cu.evidence import Evidence
from interface_cu.execution import GuardedExecutor
from interface_cu.handoff import SessionControl
from interface_cu.policy import Policy
from interface_cu.browser import open_browser
from interface_cu.replay import Replay
from interface_cu.task import savings_task
from interface_cu.vision import DiscoveryDecision


class ScriptedProvider:
    """Test-only workflow logic; production discovery has no such sequence."""

    returned_model = "scripted-test-provider"
    response_id = None

    def __init__(self):
        self.calls = 0

    async def decide(self, goal, observation, input_names, extractions, history):
        self.calls += 1
        self.response_id = f"scripted-response-{self.calls}"
        controls = {control.text:control for control in observation.controls}
        fields = {control.label:control for control in observation.readables}
        action, input_name, extraction_name = "click", None, None
        if any(heading.text == "Savings account" for heading in observation.headings):
            extraction_name = next(iter(extractions))
            selected = fields["Member ID" if extraction_name == "member_id" else "Current balance"]
            action = "extract"
        elif "View savings" in controls:
            selected = controls["View savings"]
        elif "Open member" in controls:
            selected = controls["Open member"]
        elif "Search" in controls:
            if not any(item["action"] == "fill" for item in history):
                selected, action, input_name = controls["[input]"], "fill", "member_id"
            else:
                selected = controls["Search"]
        else:
            selected = controls["Find a member"]
        return DiscoveryDecision(observation_id=observation.id, action=action, selection="control_id",
                                 control_id=selected.control_id, x=None, y=None,
                                 input_name=input_name, extraction_name=extraction_name, url=None)


@pytest.fixture
async def compiled(browser_env):
    surface, executor, _, evidence = browser_env
    provider = ScriptedProvider()
    discovery = await Discovery(surface, executor, provider, evidence).run(
        "Look up the supplied member and read their current savings balance", surface.policy.origin + "/",
        savings_task(), {"member_id":"12345"}, max_steps=8)
    assert isinstance(discovery.result, Success), discovery.result
    assert discovery.result.outputs == {"member_id":"12345", "balance_minor_units":123456, "currency":"USD"}
    assert provider.calls == 8
    artifact = compile_capability(discovery, origin=surface.policy.origin)
    return artifact, discovery


@pytest.mark.browser
async def test_revisited_entrypoint_preserves_observation_scope(browser_env):
    from interface_cu.contracts import Bindings
    surface, executor, _, _ = browser_env
    assert (await executor.execute(Navigate(url=surface.policy.origin + "/"))).status == "executed"
    task = savings_task()
    bindings = Bindings(inputs={"member_id":"12345"})
    assert await surface.evaluate(task.application.checkpoint, bindings)
    observation = await surface.observe()
    selected = next(c for c in observation.controls if c.text == "Find a member")
    target = await surface.ground_control(observation.id, selected.control_id, bindings)
    assert target.frames == ["workspace"]


@pytest.mark.browser
async def test_fresh_parameterized_replay_without_provider(compiled, origin, tmp_path, monkeypatch):
    artifact, discovered = compiled
    encoded = artifact.model_dump_json()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("Replay must never construct or call a model")
    monkeypatch.setattr("interface_cu.vision.OpenAIVision", forbidden)
    monkeypatch.setattr("interface_cu.vision.AsyncOpenAI", forbidden)
    evidence = Evidence(tmp_path / "replay", "automated_test")
    policy, control = Policy(origin=origin), SessionControl(evidence)
    async with open_browser(policy, control, evidence) as surface:
        executor = GuardedExecutor(surface, policy, control, evidence)
        result = await Replay(surface, executor, evidence).run(artifact, {"member_id":"06789"})
    assert isinstance(result, Success), result
    assert result.outputs == {"member_id":"06789", "balance_minor_units":987654, "currency":"USD"}
    assert artifact.model_dump_json() == encoded
    assert discovered.trace.source == "automated_test"
    assert artifact.provenance.source == "automated_test"
    from interface_cu.contracts import InputRef, RowLocator
    assert any(isinstance(candidate, RowLocator) and isinstance(candidate.row_text, InputRef)
               for step in artifact.steps if hasattr(step.action, "target") for candidate in step.action.target.candidates)


@pytest.mark.browser
async def test_cli_replay_is_offline_in_fresh_process(compiled, origin, tmp_path):
    artifact, _ = compiled
    path = tmp_path / "capability.json"
    path.write_text(artifact.model_dump_json(), encoding="utf-8")
    command = [sys.executable, "-m", "interface_cu.cli", "replay", "--origin", origin,
               "--artifact", str(path), "--member-id", "06789", "--evidence", str(tmp_path / "offline")]
    process = await asyncio.create_subprocess_exec(*command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(process.communicate(), 45)
    assert process.returncode == 0, (stdout, stderr)
    result = json.loads(stdout)["result"]
    assert result["outputs"] == {"member_id":"06789", "balance_minor_units":987654, "currency":"USD"}
    events = next((tmp_path / "offline").glob("*/events.jsonl")).read_text()
    assert '"provider_environment_empty": true' in events
    assert '"source": "deterministic_replay"' in events


@pytest.mark.browser
async def test_known_not_found_is_business_outcome(compiled, browser_env):
    artifact, _ = compiled
    surface, executor, _, evidence = browser_env
    result = await Replay(surface, executor, evidence).run(artifact, {"member_id":"99999"})
    assert result.status == "business_outcome"
    assert result.code == "MEMBER_NOT_FOUND"


@pytest.mark.browser
async def test_wrong_record_fails_identity_checkpoint(compiled, browser_env, monkeypatch):
    artifact, _ = compiled
    surface, executor, _, evidence = browser_env
    original = surface.perform
    async def substitute(action, *args, **kwargs):
        result = await original(action, *args, **kwargs)
        frame = surface._page.frame(name="workspace")
        if frame.url.endswith("/members/06789"):
            await frame.get_by_role("cell", name="06789", exact=True).evaluate("el => el.textContent='12345'")
        return result
    monkeypatch.setattr(surface, "perform", substitute)
    result = await Replay(surface, executor, evidence).run(artifact, {"member_id":"06789"})
    assert result.status == "failure"
    assert result.code == "POSTCONDITION_FAILED"


@pytest.mark.browser
async def test_replay_bad_inputs_and_artifacts_fail_before_navigation(compiled, browser_env, monkeypatch):
    artifact, _ = compiled
    surface, executor, _, evidence = browser_env
    async def forbidden(*args, **kwargs):
        raise AssertionError("Invalid input must not touch the browser")
    monkeypatch.setattr(surface, "perform", forbidden)
    assert (await Replay(surface, executor, evidence).run(artifact, {"member_id":6789})).code == "INVALID_INPUT"
    invalid = artifact.model_dump(mode="json")
    invalid["schema_version"] = "99"
    assert (await Replay(surface, executor, evidence).run(invalid, {"member_id":"06789"})).code == "INVALID_ARTIFACT"


@pytest.mark.browser
async def test_model_done_cannot_establish_success(browser_env):
    surface, executor, _, evidence = browser_env
    class Premature:
        response_id = "scripted-premature"
        returned_model = "scripted-test-provider"
        async def decide(self, goal, observation, *args):
            return DiscoveryDecision(observation_id=observation.id, action="done", selection="none",
                                     control_id=None, x=None, y=None, input_name=None, extraction_name=None, url=None)
    discovered = await Discovery(surface, executor, Premature(), evidence).run(
        "Read savings", surface.policy.origin + "/", savings_task(), {"member_id":"12345"})
    assert discovered.result.code == "UNVERIFIED_COMPLETION"
    with pytest.raises(ValueError, match="UNVERIFIED_DISCOVERY"):
        compile_capability(discovered, origin=surface.policy.origin)


@pytest.mark.browser
async def test_discovery_step_limit_and_timeout(browser_env):
    surface, executor, _, evidence = browser_env
    limited = await Discovery(surface, executor, ScriptedProvider(), evidence).run(
        "Read savings", surface.policy.origin + "/", savings_task(), {"member_id":"12345"}, max_steps=1)
    assert limited.result.code == "MAX_STEPS_REACHED"
    class Slow(ScriptedProvider):
        async def decide(self, *args):
            await asyncio.sleep(10)
    timed = await Discovery(surface, executor, Slow(), evidence).run(
        "Read savings", surface.policy.origin + "/", savings_task(), {"member_id":"12345"}, timeout_seconds=0.8)
    assert timed.result.code == "DISCOVERY_TIMEOUT"
    assert not timed.trace.verified_success


@pytest.mark.browser
async def test_human_assisted_trace_not_compiled(compiled):
    _, discovered = compiled
    discovered.trace.human_assisted = True
    with pytest.raises(ValueError, match="HUMAN_ASSISTED_TRACE"):
        compile_capability(discovered, origin="http://127.0.0.1:5055")


@pytest.mark.browser
async def test_compiler_rejects_literal_identifiers_and_weak_targets(compiled):
    from interface_cu.capability import Step
    from interface_cu.contracts import Click, LiteralValue, RoleLocator, StructuralLocator, TargetSpec
    _, discovered = compiled
    original = discovered.trace.steps.copy()
    for target in (
        TargetSpec(candidates=[RoleLocator(role="link",name=LiteralValue(value="Account 12345"))], rationale="Concrete account"),
        TargetSpec(candidates=[StructuralLocator(tag="input")], rationale="Weak unscoped field"),
    ):
        discovered.trace.steps = [*original, Step(id="s010",action=Click(target=target))]
        with pytest.raises(ValueError, match="CONCRETE_IDENTIFIER_IN_ARTIFACT|WEAK_STRUCTURAL_TARGET"):
            compile_capability(discovered, origin=discovered.trace.steps[0].action.url.rstrip("/"))
    discovered.trace.steps = original


@pytest.mark.browser
async def test_ambiguous_replay_target_fails(compiled, browser_env, monkeypatch):
    artifact, _ = compiled
    surface, executor, _, evidence = browser_env
    original = surface.perform
    async def duplicate(action, *args, **kwargs):
        result = await original(action, *args, **kwargs)
        if isinstance(action, Navigate):
            await surface._page.frame(name="workspace").evaluate(
                "document.body.insertAdjacentHTML('beforeend', '<a href=\"/members\">Find a member</a>')")
        return result
    monkeypatch.setattr(surface, "perform", duplicate)
    result = await Replay(surface, executor, evidence).run(artifact, {"member_id":"06789"})
    assert result.status == "failure"
    assert result.code == "PRECONDITION_FAILED"


@pytest.mark.browser
async def test_timeout_preserves_uncertain_commit_without_retry(compiled, browser_env, monkeypatch):
    artifact, _ = compiled
    surface, executor, _, evidence = browser_env
    calls = 0
    async def slow_action(*args, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(2)
    monkeypatch.setattr(executor, "execute", slow_action)
    result = await Replay(surface, executor, evidence).run(artifact, {"member_id":"06789"}, timeout_seconds=0.1)
    assert result.code == "REPLAY_TIMEOUT"
    assert result.completion_uncertain is True
    assert calls == 1
