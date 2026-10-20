"""Observation IDs are local handles, never guesses or reusable artifact locators."""

import pytest

from interface_cu.contracts import Bindings, Click, ResolutionError
from test_browser import open_member

pytestmark = pytest.mark.browser


async def test_valid_control_id_executes_in_original_frame(browser_env):
    surface, executor, _, _ = browser_env
    observation = await surface.observe()
    selected = next(c for c in observation.controls if c.text == "Find a member")
    target = await surface.ground_control(observation.id, selected.control_id)
    assert target.frames == ["workspace"]
    assert (await executor.execute(Click(target=target))).status == "executed"
    assert await surface._page.frame(name="workspace").get_by_role("heading", name="Member search").is_visible()


async def test_unknown_id_never_falls_back_to_a_control(browser_env):
    surface, _, _, _ = browser_env
    observation = await surface.observe()
    with pytest.raises(ResolutionError, match="UNKNOWN_CONTROL_ID"):
        await surface.ground_control(observation.id, "invented-id")


async def test_new_observation_invalidates_prior_id_scope(browser_env):
    surface, _, _, _ = browser_env
    original = await surface.observe()
    replacement = await surface.observe()
    assert original.id != replacement.id
    with pytest.raises(ResolutionError, match="STALE_OBSERVATION"):
        await surface.ground_control(original.id, replacement.controls[0].control_id)


async def test_changed_screenshot_invalidates_selection(browser_env):
    surface, _, _, _ = browser_env
    observation = await surface.observe()
    await surface._page.frame(name="workspace").evaluate("document.body.style.background = 'yellow'")
    with pytest.raises(ResolutionError, match="STALE_OBSERVATION"):
        await surface.ground_control(observation.id, observation.controls[0].control_id)


async def test_same_pixels_after_navigation_are_still_stale(browser_env):
    surface, _, _, _ = browser_env
    observation = await surface.observe()
    await surface._page.frame(name="workspace").goto(surface.policy.origin + "/home")
    with pytest.raises(ResolutionError, match="STALE_OBSERVATION"):
        await surface.ground_control(observation.id, observation.controls[0].control_id)


async def test_identical_node_replacement_is_not_the_observed_control(browser_env):
    surface, _, _, _ = browser_env
    observation = await surface.observe()
    selected = next(c for c in observation.controls if c.text == "Find a member")
    await surface._page.frame(name="workspace").get_by_role("link", name="Find a member").evaluate(
        "el => el.replaceWith(el.cloneNode(true))")
    with pytest.raises(ResolutionError, match="CONTROL_IDENTITY_CHANGED"):
        await surface.ground_control(observation.id, selected.control_id)


async def test_hidden_link_effect_change_is_rejected(browser_env):
    surface, _, _, _ = browser_env
    observation = await surface.observe()
    selected = next(c for c in observation.controls if c.text == "Find a member")
    await surface._page.frame(name="workspace").get_by_role("link", name="Find a member").evaluate(
        "el => el.href = '/transfer'")
    with pytest.raises(ResolutionError, match="CONTROL_EFFECT_CHANGED"):
        await surface.ground_control(observation.id, selected.control_id)


async def test_control_id_does_not_bypass_ambiguous_locator(browser_env):
    surface, _, _, _ = browser_env
    await surface._page.frame(name="workspace").evaluate(
        "document.body.insertAdjacentHTML('beforeend', '<a href=\"/members\">Find a member</a>')")
    observation = await surface.observe()
    selected = next(c for c in observation.controls if c.text == "Find a member")
    with pytest.raises(ResolutionError, match="AMBIGUOUS_TARGET"):
        await surface.ground_control(observation.id, selected.control_id)


async def test_control_id_cannot_bypass_financial_policy(browser_env):
    surface, executor, _, _ = browser_env
    await open_member(executor)
    observation = await surface.observe()
    selected = next(c for c in observation.controls if c.text == "Initiate transfer")
    target = await surface.ground_control(observation.id, selected.control_id)
    result = await executor.execute(Click(target=target))
    assert result.status == "blocked"
    assert surface._page.frame(name="workspace").url.endswith("/members/06789")


async def test_coordinate_fallback_hits_an_unlisted_control_exactly(browser_env):
    surface, executor, _, _ = browser_env
    frame = surface._page.frame(name="workspace")
    await frame.evaluate("""() => {
        const container=document.createElement('div');
        container.style='display:flex;flex-wrap:wrap;width:900px';
        for(let i=0;i<45;i++) {
            const a=document.createElement('a');a.href='/members';
            a.textContent='Unlisted '+i;a.style='width:100px;height:22px;font-size:12px';
            container.append(a);
        }
        document.body.append(container);
    }""")
    observation = await surface.observe()
    assert len(observation.controls) == 40
    assert not any(c.text == "Unlisted 44" for c in observation.controls)
    box = await frame.get_by_role("link", name="Unlisted 44", exact=True).bounding_box()
    target = await surface.ground_point(observation, int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2))
    assert (await executor.execute(Click(target=target))).status == "executed"


async def test_coordinate_whitespace_does_not_guess_nearest_control(browser_env):
    surface, _, _, _ = browser_env
    observation = await surface.observe()
    with pytest.raises(ResolutionError, match="POINT_NOT_UNIQUE_CONTROL"):
        await surface.ground_point(observation, 300, 220)
