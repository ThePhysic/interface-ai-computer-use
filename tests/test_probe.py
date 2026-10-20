"""Scripted model substitutes test probe checks; none are live discovery evidence."""

import json
from types import SimpleNamespace

import pytest

from interface_cu.cli import vision_probe
from interface_cu.evidence import Evidence
from interface_cu.vision import VisionChoice


@pytest.mark.browser
@pytest.mark.parametrize("selection", ["control_id", "coordinates"])
@pytest.mark.parametrize("entry,control_text,action,expected", [
    ("/", "Find a member", "click", True),
    ("/", "Staff home", "click", False),
    ("/members", "[input]", "fill", True),
    ("/members", "Search", "click", False),
])
async def test_probe_requires_explicit_checkpoint(origin, tmp_path, monkeypatch,
                                                   entry, control_text, action, expected, selection):
    class ScriptedProvider:
        model = "scripted-test-double"
        returned_model = "scripted-test-double"
        response_id = "not-a-live-response"
        usage = None

        def __init__(self, model=None):
            pass

        async def choose(self, goal, observation, input_names):
            control = next(c for c in observation.controls if c.text == control_text)
            return VisionChoice(observation_id=observation.id, action=action,
                                selection=selection,
                                control_id=control.control_id if selection == "control_id" else None,
                                x=int(control.rect.x + control.rect.width / 2) if selection == "coordinates" else None,
                                y=int(control.rect.y + control.rect.height / 2) if selection == "coordinates" else None,
                                input_name="member_id" if action == "fill" else None)

    monkeypatch.setattr("interface_cu.vision.OpenAIVision", ScriptedProvider)
    # A simulated provider must never produce evidence labelled as a live model run.
    monkeypatch.setattr("interface_cu.cli.Evidence", lambda root, source: Evidence(root, "automated_test"))
    args = SimpleNamespace(origin=origin, entry=entry, model=None, headed=False,
                           goal="Find fictional member 12345", evidence=tmp_path)
    assert await vision_probe(args) == (0 if expected else 1)
    events_file = next(tmp_path.glob("*/events.jsonl"))
    events = [json.loads(line) for line in events_file.read_text().splitlines()]
    assert all(event["source"] == "automated_test" for event in events)
    finished = events[-1]
    assert finished["event"] == "probe_finished"
    assert finished["data"]["live_model_action_verified"] is expected
    assert finished["data"]["checkpoint_satisfied_before"] is False
    assert finished["data"]["checkpoint_satisfied_after"] is expected
    if entry == "/members" and action == "click":
        # Empty Search shows a validation error: visible change without progress.
        screenshots = [event["data"]["sha256"] for event in events if event["event"] == "screenshot"]
        assert len(set(screenshots)) == 2
