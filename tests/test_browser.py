import asyncio
import json

import pytest

from interface_cu.contracts import (
    Bindings, Click, Fill, InputRef, LiteralValue, Navigate, Press, RoleLocator,
    ResolutionError, RowLocator, StructuralLocator, TargetSpec, TextEquals, TextLocator,
)

from conftest import wait_until

pytestmark = pytest.mark.browser


def role(name, role="link", frames=None):
    return TargetSpec(frames=["workspace"] if frames is None else frames,
                      candidates=[RoleLocator(role=role, name=LiteralValue(value=name))], rationale="Exact visible control name")


def member_field():
    return TargetSpec(frames=["workspace"], candidates=[StructuralLocator(tag="input", adjacent_label=LiteralValue(value="Member ID"))],
                      expected_tag="input", rationale="Legacy field in the cell following its visible caption")


async def go_to_search(executor):
    assert (await executor.execute(Click(target=role("Find a member")))).status == "executed"


async def open_member(executor, member="06789"):
    await go_to_search(executor)
    assert (await executor.execute(Fill(target=member_field(), value=InputRef(name="member_id")), Bindings(inputs={"member_id": member}))).status == "executed"
    assert (await executor.execute(Click(target=role("Search", "button")))).status == "executed"
    target = TargetSpec(frames=["workspace"], candidates=[RowLocator(row_text=InputRef(name="member_id"), role="link", name=LiteralValue(value="Open member"))], rationale="Exact member ID cell scopes the row")
    assert (await executor.execute(Click(target=target), Bindings(inputs={"member_id": member}))).status == "executed"


async def test_real_iframe_flow_and_typed_checkpoint(browser_env):
    surface, executor, _, _ = browser_env
    await open_member(executor)
    assert (await executor.execute(Click(target=role("View savings")))).status == "executed"
    balance = TargetSpec(frames=["workspace"], candidates=[StructuralLocator(tag="td", adjacent_label=LiteralValue(value="Current balance"))], rationale="Value cell following balance caption")
    assert await surface.evaluate(TextEquals(target=balance, value=LiteralValue(value="USD 9,876.54")), Bindings())
    observation = await surface.observe()
    assert observation.screenshot_png.startswith(b"\x89PNG")
    assert observation.width == 1100


async def test_screenshot_point_grounded_without_coordinate_click(browser_env):
    surface, executor, _, _ = browser_env
    before = await surface.observe()
    control = next(c for c in before.controls if c.text == "Find a member")
    target = await surface.ground_point(before, int(control.rect.x + control.rect.width / 2), int(control.rect.y + control.rect.height / 2))
    assert target.frames == ["workspace"]
    assert (await executor.execute(Click(target=target))).status == "executed"
    assert await surface._page.frame(name="workspace").get_by_role("heading", name="Member search").is_visible()
    with pytest.raises(ResolutionError, match="STALE_OBSERVATION"):
        await surface.ground_point(before, 1, 1)


async def test_masked_inputs_do_not_enter_observation(browser_env):
    surface, executor, _, _ = browser_env
    await go_to_search(executor)
    assert (await executor.execute(Fill(target=member_field(), value=LiteralValue(value="baseline")))).status == "executed"
    before = await surface.observe()
    assert (await executor.execute(Fill(target=member_field(), value=LiteralValue(value="canary-private")))).status == "executed"
    after = await surface.observe()
    assert before.screenshot_sha256 == after.screenshot_sha256
    assert "canary-private" not in after.model_dump_json()
    assert "screenshot_png" not in after.model_dump()


async def test_ambiguous_primary_does_not_fall_back(browser_env):
    surface, executor, _, _ = browser_env
    frame = surface._page.frame(name="workspace")
    await frame.evaluate("document.body.insertAdjacentHTML('beforeend', '<a href=\"/members\">Find a member</a>')")
    ambiguous = TargetSpec(frames=["workspace"], candidates=[RoleLocator(role="link", name=LiteralValue(value="Find a member")),
                                                          RoleLocator(role="link", name=LiteralValue(value="Member services"))], rationale="Ambiguity must stop")
    result = await executor.execute(Click(target=ambiguous))
    assert result.code == "AMBIGUOUS_TARGET"
    assert frame.url.endswith("/home")


async def test_fallback_only_when_absent_and_identity_checked(browser_env):
    surface, executor, _, _ = browser_env
    target = TargetSpec(frames=["workspace"], candidates=[TextLocator(text=LiteralValue(value="Missing name")),
                                                        RoleLocator(role="link", name=LiteralValue(value="Find a member"))],
                        expected_tag="button", rationale="Mismatching identity")
    result = await executor.execute(Click(target=target))
    assert result.code == "IDENTITY_MISMATCH"
    target = target.model_copy(update={"expected_tag": "a"})
    assert (await executor.execute(Click(target=target))).status == "executed"


@pytest.mark.parametrize("kind", ["click", "press", "navigate"])
async def test_financial_mutation_blocked(browser_env, kind):
    surface, executor, _, evidence = browser_env
    await open_member(executor)
    target = role("Initiate transfer", "button")
    action = Click(target=target) if kind == "click" else Press(target=target, key="Enter") if kind == "press" else Navigate(url=surface.policy.origin + "/transfer")
    result = await executor.execute(action)
    assert result.status == "blocked"
    assert surface._page.frame(name="workspace").url.endswith("/members/06789")
    assert "action_blocked" in (evidence.directory / "events.jsonl").read_text()


async def test_network_guard_blocks_manual_form_submission(browser_env):
    surface, executor, control, evidence = browser_env
    await open_member(executor)
    await control.give_to_human()
    frame = surface._page.frame(name="workspace")
    # Automated simulation of a manual bypass, explicitly labelled as a test.
    await frame.evaluate("document.querySelector('form').submit()")
    await wait_until(lambda: "network_blocked" in (evidence.directory / "events.jsonl").read_text())


async def test_manual_activity_same_session_redacted_and_automation_stopped(browser_env):
    surface, executor, control, evidence = browser_env
    await go_to_search(executor)
    identities = surface.session_id, surface.page_id, surface._page
    await control.give_to_human()
    assert (await executor.execute(Click(target=role("Search", "button")))).code == "AUTOMATION_DOES_NOT_OWN_SESSION"
    frame = surface._page.frame(name="workspace")
    # This tests capture plumbing, not proof of an actual human operator.
    await frame.locator("input").fill("12345")
    async with frame.expect_navigation(wait_until="load"):
        await frame.get_by_role("button", name="Search").click()
    await surface.flush_activity()
    await wait_until(lambda: surface.capture.events >= 1)
    frame = surface._page.frame(name="workspace")
    await frame.locator("input").fill("private-canary")
    await frame.locator("input").dispatch_event("change")
    await surface.flush_activity()
    assert identities == (surface.session_id, surface.page_id, surface._page)
    content = (evidence.directory / "events.jsonl").read_text()
    assert "private-canary" not in content
    records = [json.loads(line) for line in content.splitlines()]
    assert any(r["event"] == "manual_navigation" for r in records)
    assert all(r["source"] == "automated_test" for r in records)
    await control.finish_probe()


async def test_ownership_transfer_waits_for_execution_boundary(browser_env):
    _, _, control, _ = browser_env
    async with control.lock:
        transfer = asyncio.create_task(control.give_to_human())
        await asyncio.sleep(0)
        assert not transfer.done()
    await transfer


async def test_conflicting_candidates_fail(browser_env):
    _, executor, _, _ = browser_env
    target = TargetSpec(frames=["workspace"], candidates=[RoleLocator(role="link", name=LiteralValue(value="Find a member")),
                                                        RoleLocator(role="link", name=LiteralValue(value="Member services"))], rationale="Distinct controls must not be conflated")
    assert (await executor.execute(Click(target=target))).code == "CONFLICTING_TARGETS"


async def test_adapter_rejects_effect_changed_after_policy_inspection(browser_env):
    surface, _, _, _ = browser_env
    action = Click(target=role("Find a member"))
    resolved = await surface.resolve(action.target, Bindings())
    facts = await surface.inspect(resolved)
    surface.policy.check_action(action.kind, facts)
    await surface._page.frame(name="workspace").get_by_role("link", name="Find a member").evaluate(
        "el => el.href = '/transfer'")
    with pytest.raises(ResolutionError, match="RESOLVED_ACTION_CHANGED"):
        await surface.perform(action, resolved, facts=facts)
    assert surface._page.frame(name="workspace").url.endswith("/home")
