"""The executor must operate without Playwright pages or locator-shaped fakes."""

import pytest

from interface_cu.contracts import (
    ActionFacts, Bindings, Click, Fill, InputRef, LiteralValue, Navigate,
    NavigationState, ResolvedTarget, RoleLocator, SurfaceError, TargetSpec,
)
from interface_cu.evidence import Evidence
from interface_cu.execution import GuardedExecutor
from interface_cu.handoff import SessionControl
from interface_cu.policy import Policy


TARGET = TargetSpec(candidates=[RoleLocator(role="link", name=LiteralValue(value="Find a member"))],
                    rationale="A surface-independent target")


class FakeSurface:
    """Only the public execution contract; no locator, evaluate-JS, or page API."""

    def __init__(self, facts=None, error=None, frame_url=None):
        self.handle = ResolvedTarget()
        self.facts = facts or ActionFacts(tag="a", href="http://127.0.0.1:5055/members")
        self.error, self.frame_url = error, frame_url
        self.calls = []

    async def resolve(self, target, bindings):
        self.calls.append("resolve")
        return self.handle

    async def inspect(self, resolved):
        assert resolved is self.handle
        self.calls.append("inspect")
        return self.facts

    async def perform(self, action, resolved=None, value=None, facts=None):
        if not isinstance(action, Navigate):
            assert resolved is self.handle
            assert facts is self.facts
        self.calls.append(("perform", value))
        if self.error:
            raise self.error

    async def navigation_state(self):
        self.calls.append("navigation_state")
        return NavigationState(session_id="session", page_id="page", url="http://127.0.0.1:5055/",
                               frame_urls=[self.frame_url] if self.frame_url else [])


def executor_for(surface, tmp_path):
    evidence = Evidence(tmp_path, "automated_test")
    control = SessionControl(evidence)
    return GuardedExecutor(surface, Policy(), control, evidence), control


async def test_executor_uses_only_public_adapter_contract(tmp_path):
    surface = FakeSurface()
    executor, _ = executor_for(surface, tmp_path)
    assert (await executor.execute(Click(target=TARGET))).status == "executed"
    assert surface.calls == ["resolve", "inspect", ("perform", None), "navigation_state"]


async def test_fill_binds_value_without_raw_locator(tmp_path):
    surface = FakeSurface(facts=ActionFacts(tag="input", type="text"))
    executor, _ = executor_for(surface, tmp_path)
    result = await executor.execute(Fill(target=TARGET, value=InputRef(name="member_id")),
                                    Bindings(inputs={"member_id": "06789"}))
    assert result.status == "executed"
    assert ("perform", "06789") in surface.calls


async def test_policy_checks_inspected_effect_before_primitive(tmp_path):
    surface = FakeSurface(facts=ActionFacts(tag="button", type="submit",
                                           form_action="http://127.0.0.1:5055/transfer", form_method="post"))
    executor, _ = executor_for(surface, tmp_path)
    assert (await executor.execute(Click(target=TARGET))).status == "blocked"
    assert surface.calls == ["resolve", "inspect"]


async def test_navigation_checks_child_frame_state(tmp_path):
    surface = FakeSurface(frame_url="https://example.com/")
    executor, _ = executor_for(surface, tmp_path)
    result = await executor.execute(Navigate(url="http://127.0.0.1:5055/"))
    assert result.code == "ORIGIN_DENIED"
    assert result.completion_uncertain


async def test_human_ownership_blocks_before_adapter_calls(tmp_path):
    surface = FakeSurface()
    executor, control = executor_for(surface, tmp_path)
    await control.give_to_human()
    assert (await executor.execute(Click(target=TARGET))).code == "AUTOMATION_DOES_NOT_OWN_SESSION"
    assert surface.calls == []


@pytest.mark.parametrize("uncertain", [False, True])
async def test_adapter_errors_preserve_completion_uncertainty(tmp_path, uncertain):
    surface = FakeSurface(error=SurfaceError("BROWSER_ERROR", completion_uncertain=uncertain))
    executor, _ = executor_for(surface, tmp_path)
    result = await executor.execute(Click(target=TARGET))
    assert result.status == "failed"
    assert result.completion_uncertain is uncertain
