"""Automated operator doubles on real fixture UI; never real manual proof."""

import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from interface_cu.banking_runtime import savings_runtime_profile
from interface_cu.browser import open_browser
from interface_cu.capability import Capability, Step
from interface_cu.compiler import compile_capability
from interface_cu.contracts import (
    ActionFacts, Click, Extract, ExtractedRef, InputRef, InterventionRequest, LiteralValue,
    Navigate, OperatorResponse, Ownership, RoleLocator, RowLocator, SurfaceError, TargetSpec, TextLocator,
)
from interface_cu.discovery import Discovery
from interface_cu.evidence import Evidence
from interface_cu.execution import GuardedExecutor
from interface_cu.handoff import ActivityCapture, OwnershipError, SessionControl
from interface_cu.intervention import ConsoleOperator, HandoffCoordinator, banking_intervention_rule, operator_budget
from interface_cu.policy import Policy, PolicyViolation
from interface_cu.replay import Replay
from interface_cu.task import savings_task

from test_execution import FakeSurface, executor_for
from test_runtime import ARTIFACT_PATH, scenario_server
from test_workflow import ScriptedProvider


def events(evidence):
    return [json.loads(line) for line in (evidence.directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def capability():
    return Capability.model_validate_json(ARTIFACT_PATH.read_bytes())


async def acknowledge(surface):
    """Test-only simulation of the real operator's checkbox + submit gestures."""
    frame = surface._page.frame(name="workspace")
    await frame.get_by_role("checkbox", name="I acknowledge read-only access").check()
    async with frame.expect_navigation(wait_until="load"):
        await frame.get_by_role("button", name="Acknowledge read-only access").click()
    await frame.wait_for_function("location.search === ''")


class AutomatedOperator:
    def __init__(self, surface, executor, behavior="resolve"):
        self.surface, self.executor, self.behavior = surface, executor, behavior
        self.seen = []

    async def respond(self, request):
        assert self.executor.control.state == Ownership.HUMAN
        self.seen.append(request)
        assert (await self.executor.execute(Navigate(url=self.surface.policy.origin + "/"))).code == "AUTOMATION_DOES_NOT_OWN_SESSION"
        action = "resume"
        generation = request.ownership_generation
        if self.behavior in {"resolve", "stale", "wrong_view"}:
            await acknowledge(self.surface)
            if self.behavior == "stale": generation -= 1
            if self.behavior == "wrong_view":
                frame = self.surface._page.frame(name="workspace")
                async with frame.expect_navigation(wait_until="load"):
                    await frame.get_by_role("link", name="View savings").click()
        elif self.behavior == "wrong_member":
            await self.surface._page.frame(name="workspace").goto(self.surface.policy.origin + "/members/12345")
            await acknowledge(self.surface)
        elif self.behavior in {"cancel", "unresolved"}:
            action = self.behavior
        elif self.behavior == "timeout":
            await asyncio.Event().wait()
        elif self.behavior == "closed":
            await self.surface._page.close()
        elif self.behavior == "replaced":
            self.surface._page = await self.surface._page.context.new_page()
        elif self.behavior == "policy":
            try:
                await self.surface._page.frame(name="workspace").goto(self.surface.policy.origin + "/transfer")
            except Exception:
                pass  # The network guard must reject this operator bypass.
        elif self.behavior != "still_blocked":
            raise AssertionError("Unsupported test operator")
        return OperatorResponse(intervention_id=request.id, ownership_generation=generation, action=action)


async def test_transition_generation_and_queued_action_cannot_adopt_new_owner(tmp_path):
    surface = FakeSurface()
    executor, control = executor_for(surface, tmp_path)
    paused = await control.pause("request-1", control.generation)
    human = await control.transfer_to_human("request-1", paused)
    with pytest.raises(OwnershipError, match="STALE_INTERVENTION"):
        await control.begin_resume("request-1", human - 1)
    resuming = await control.begin_resume("request-1", human)
    async with control.lock:
        queued = asyncio.create_task(executor.execute(Navigate(url=executor.policy.origin + "/")))
        await asyncio.sleep(0)
        control.approve_resume("request-1", resuming)
    assert (await queued).code == "AUTOMATION_DOES_NOT_OWN_SESSION"
    assert surface.calls == []
    await control.finish_run(True)
    assert control.state == Ownership.COMPLETED


async def test_operator_wait_pauses_automation_budget_and_keeps_event_loop_live():
    processed = asyncio.Event()
    async def event_processing():
        await asyncio.sleep(0.01)
        processed.set()
    async with asyncio.timeout(0.05) as deadline:
        async with operator_budget(deadline):
            task = asyncio.create_task(event_processing())
            await asyncio.sleep(0.08)
            assert processed.is_set()
            await task
        assert not deadline.expired()
        assert deadline.when() > asyncio.get_running_loop().time()


async def test_pause_waits_for_inflight_guarded_action_to_settle(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    class SettlingSurface(FakeSurface):
        async def perform(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return await super().perform(*args, **kwargs)
    executor, control = executor_for(SettlingSurface(), tmp_path)
    action = asyncio.create_task(executor.execute(Navigate(url=executor.policy.origin + "/")))
    await entered.wait()
    pause = asyncio.create_task(control.pause("inflight", control.generation))
    await asyncio.sleep(0)
    assert not pause.done() and control.state == Ownership.AUTOMATION
    release.set()
    assert (await action).status == "executed"
    paused = await pause
    await control.transfer_to_human("inflight", paused)
    assert control.state == Ownership.HUMAN


async def test_capture_rejects_activity_from_another_page(tmp_path):
    evidence = Evidence(tmp_path, "automated_test")
    control = SessionControl(evidence)
    await control.give_to_human()
    capture = ActivityCapture(control, evidence, "session", "page")
    original, other = object(), object()
    class Context:
        async def expose_binding(self, *args): pass
        async def add_init_script(self, **kwargs): pass
    await capture.install(Context(), original)
    frame = SimpleNamespace(url="http://127.0.0.1:5055/members/06789", page=other)
    capture.receive({"page":other,"frame":frame}, {"event":"click","tag":"BUTTON"})
    capture.navigation(frame)
    assert capture.events == 0
    assert not any(r["event"].startswith("manual_") for r in events(evidence))
    frame.page = original
    capture.receive({"page":original,"frame":frame}, {"event":"change","tag":"INPUT","value":"never persist"})
    assert capture.events == 1
    assert "never persist" not in (evidence.directory / "events.jsonl").read_text()


@pytest.mark.parametrize("method,path", [("GET","/members/06789?operator_ack=confirmed"),("POST","/members/06789?operator_ack=confirmed"),("GET","/transfer?operator_ack=confirmed")])
def test_operator_ack_cannot_bypass_automation_or_financial_policy(method, path):
    policy = Policy()
    with pytest.raises(PolicyViolation): policy.check_url(policy.origin + path, method)
    if method == "GET" and path.startswith("/members/"):
        policy.check_url(policy.origin + path, method, human=True)
    else:
        with pytest.raises(PolicyViolation): policy.check_url(policy.origin + path, method, human=True)
    with pytest.raises(PolicyViolation, match="HUMAN_ONLY_CONTROL"):
        policy.check_action("click", ActionFacts(tag="button", type="submit", operator_only=True))


@pytest.mark.browser
async def test_same_session_replay_resumes_correct_step_with_capture(capability, tmp_path):
    with scenario_server("operator_challenge") as origin:
        evidence = Evidence(tmp_path, "automated_test")
        policy, control = Policy(origin=origin), SessionControl(evidence)
        async with open_browser(policy, control, evidence) as surface:
            executor = GuardedExecutor(surface, policy, control, evidence)
            original = (surface._page, surface._page.context, surface.session_id, surface.page_id)
            operator = AutomatedOperator(surface, executor)
            coordinator = HandoffCoordinator(surface, executor, evidence, operator, banking_intervention_rule())
            result = await Replay(surface, executor, evidence, handoff=coordinator).run(capability, {"member_id":"06789"})
            assert result.status == "success", result
            assert result.outputs == {"member_id":"06789","balance_minor_units":987654,"currency":"USD"}
            assert original == (surface._page, surface._page.context, surface.session_id, surface.page_id)
            assert coordinator.requests[0].status == "completed"
            assert operator.seen[0].step_id == "s005"
            assert operator.seen[0].allowed_resume.action_state == "executed"
            assert InterventionRequest.model_validate_json(operator.seen[0].model_dump_json()) == operator.seen[0]
            records = events(evidence)
            assert [r["data"]["step_id"] for r in records if r["event"] == "replay_step"] == [s.id for s in capability.steps]
            await control.finish_run(True)
            records = events(evidence)
            assert [r["data"]["state"] for r in records if r["event"] == "ownership"] == ["paused","human","resuming","automation","completed"]
            activity = [r for r in records if r["event"] == "manual_activity"]
            assert {r["data"]["activity"] for r in activity} >= {"click","change","submit"}
            assert all(r["data"]["session_id"] == original[2] and r["data"]["page_id"] == original[3] for r in activity)
            assert all(r["data"]["input_redacted"] and "value" not in r["data"] for r in activity)
            assert any(r["event"] == "manual_navigation" for r in records)
            assert all(r["source"] == "automated_test" for r in records)


@pytest.mark.browser
@pytest.mark.parametrize("behavior,code,status", [
    ("cancel","INTERVENTION_CANCELLED","cancelled"),
    ("unresolved","INTERVENTION_UNRESOLVED","unresolved"),
    ("still_blocked","RESUME_BLOCKER_UNRESOLVED","rejected"),
    ("wrong_member","RESUME_IDENTITY_MISMATCH","rejected"),
    ("wrong_view","RESUME_CHECKPOINT_FAILED","rejected"),
    ("stale","STALE_INTERVENTION","rejected"),
    ("timeout","INTERVENTION_TIMEOUT","timed_out"),
    ("closed","SESSION_CLOSED","failed"),
    ("replaced","SESSION_IDENTITY_CHANGED","failed"),
    ("policy","ROUTE_DENIED","failed"),
])
async def test_rejected_interventions_fail_safely(capability, tmp_path, behavior, code, status):
    with scenario_server("operator_challenge") as origin:
        evidence = Evidence(tmp_path, "automated_test")
        policy, control = Policy(origin=origin), SessionControl(evidence)
        async with open_browser(policy, control, evidence) as surface:
            executor = GuardedExecutor(surface, policy, control, evidence)
            operator = AutomatedOperator(surface, executor, behavior)
            coordinator = HandoffCoordinator(surface,executor,evidence,operator,banking_intervention_rule(),
                                             operator_timeout=0.05 if behavior=="timeout" else 30)
            result = await Replay(surface,executor,evidence,handoff=coordinator).run(capability,{"member_id":"06789"})
            assert result.status == "failure" and result.code == code, result
            assert coordinator.requests[0].status == status
            assert control.state == Ownership.FAILED
            assert not any(r["event"] == "resume_verified" for r in events(evidence))
            assert [r["data"]["step_id"] for r in events(evidence) if r["event"] == "replay_step"] == ["s001","s002","s003","s004","s005"]
            if behavior == "policy":
                assert result.category == "policy" and not result.human_intervention_appropriate
                assert (await surface.navigation_state()).blocked_request_code == "ROUTE_DENIED"


@pytest.mark.browser
@pytest.mark.parametrize("known_baseline", [True, False])
async def test_uncertain_action_is_not_reexecuted_after_human_resolution(capability, tmp_path, monkeypatch, known_baseline):
    if not known_baseline:
        data = capability.model_dump(mode="json")
        data["steps"][4]["postconditions"] = []
        capability = Capability.model_validate(data)
    with scenario_server("operator_challenge") as origin:
        evidence = Evidence(tmp_path,"automated_test")
        policy,control = Policy(origin=origin),SessionControl(evidence)
        async with open_browser(policy,control,evidence) as surface:
            executor=GuardedExecutor(surface,policy,control,evidence)
            operator=AutomatedOperator(surface,executor)
            coordinator=HandoffCoordinator(surface,executor,evidence,operator,banking_intervention_rule())
            original=surface.perform
            calls=0
            async def uncertain(action,*args,**kwargs):
                nonlocal calls
                result=await original(action,*args,**kwargs)
                if isinstance(action,Click) and isinstance(action.target.candidates[0],RowLocator):
                    calls+=1
                    raise SurfaceError("BROWSER_ERROR",completion_uncertain=True)
                return result
            monkeypatch.setattr(surface,"perform",uncertain)
            result=await Replay(surface,executor,evidence,handoff=coordinator).run(capability,{"member_id":"06789"})
            assert calls==1
            assert operator.seen[0].completion_uncertain
            if known_baseline:
                assert result.status=="success",result
                assert operator.seen[0].allowed_resume.baseline_satisfied is False
                assert any(r["event"]=="resume_verified" and r["data"]["action_state"]=="uncertain" for r in events(evidence))
            else:
                assert result.status=="failure" and result.code=="RESUME_COMPLETION_UNCERTAIN",result
                assert result.completion_uncertain
                assert not any(r["event"]=="resume_verified" for r in events(evidence))


@pytest.mark.browser
async def test_partial_extractions_and_recovery_history_survive_handoff(capability,tmp_path):
    # Add a test-only early extraction; leave the proven artifact file unchanged.
    target=TargetSpec(frames=["workspace"],candidates=[TextLocator(text=InputRef(name="member_id"))],
                     expected_tag="td",rationale="Exact visible search result identity, excluding input values")
    early=Step(id="s010",action=Extract(target=target,name="early_member_id",parser="text"))
    data=capability.model_dump(mode="json")
    data["steps"].insert(4,early.model_dump(mode="json"))
    data["outputs"]["member_id"]["binding"]={"kind":"extracted","name":"early_member_id"}
    varied=Capability.model_validate(data)
    with scenario_server("operator_challenge_after_retry") as origin:
        evidence=Evidence(tmp_path,"automated_test")
        policy,control=Policy(origin=origin),SessionControl(evidence)
        async with open_browser(policy,control,evidence) as surface:
            executor=GuardedExecutor(surface,policy,control,evidence)
            coordinator=HandoffCoordinator(surface,executor,evidence,AutomatedOperator(surface,executor),banking_intervention_rule())
            replay=Replay(surface,executor,evidence,handoff=coordinator)
            result=await replay.run(varied,{"member_id":"06789"},runtime_profile=savings_runtime_profile())
            assert result.status=="success" and result.outputs["member_id"]=="06789",result
            assert len(replay.monitor.history)==1
            assert replay.monitor.counts["TRANSIENT_APPLICATION_ERROR"]==1
            assert len(coordinator.requests)==1


@pytest.mark.browser
async def test_discovery_handoff_is_assisted_and_cannot_compile(tmp_path):
    with scenario_server("operator_challenge") as origin:
        evidence=Evidence(tmp_path,"automated_test")
        policy,control=Policy(origin=origin),SessionControl(evidence)
        async with open_browser(policy,control,evidence) as surface:
            executor=GuardedExecutor(surface,policy,control,evidence)
            coordinator=HandoffCoordinator(surface,executor,evidence,AutomatedOperator(surface,executor),banking_intervention_rule())
            provider=ScriptedProvider()
            result=await Discovery(surface,executor,provider,evidence,handoff=coordinator).run(
                "Read the supplied member's savings balance",origin+"/",savings_task(),{"member_id":"06789"},max_steps=8)
            assert result.result.status=="success",result.result
            assert result.trace.human_assisted
            assert provider.calls==8
            assert len(coordinator.requests)==1
            with pytest.raises(ValueError,match="HUMAN_ASSISTED_TRACE"):
                compile_capability(result,origin=origin)
            assert not any(r["event"]=="capability_compiled" for r in events(evidence))


async def test_console_eof_is_unresolved_and_does_not_hang(monkeypatch):
    monkeypatch.setattr("sys.stdin",io.StringIO(""))
    task=savings_task()
    from interface_cu.contracts import ResumeConditions
    request=InterventionRequest(run_id="test",mode="replay",capability_id=task.capability_id,
        step_id="s005",reason="OPERATOR_CHALLENGE",session_id="session",page_id="page",observed="test",
        evidence_ref="test",expected="Member record",completion_uncertain=False,ownership_generation=2,
        allowed_resume=ResumeConditions(application_checkpoint=task.application.checkpoint,
            member_identity=[task.success_conditions[1]],workflow_checkpoint=[task.success_conditions[1]],action_state="executed"))
    response=await asyncio.wait_for(ConsoleOperator().respond(request),1)
    assert response.action=="unresolved"
    invalid=request.model_dump(mode="json")
    invalid["allowed_resume"]["member_identity"]=[]
    with pytest.raises(ValidationError):InterventionRequest.model_validate(invalid)
