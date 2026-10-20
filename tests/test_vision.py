from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from interface_cu.contracts import Observation, Rect, VisibleControl
from interface_cu.vision import OpenAIVision, VisionChoice


async def test_provider_receives_screenshot_and_no_hidden_workflow(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-credential")
    parsed = VisionChoice(observation_id="test-observation", action="click", selection="control_id",
                          control_id="c1", x=None, y=None, input_name=None)
    parse = AsyncMock(return_value=SimpleNamespace(output_parsed=parsed, id="simulated-response",
                                                  model="simulated-model", usage=None))
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            self.responses = SimpleNamespace(parse=parse)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr("interface_cu.vision.AsyncOpenAI", FakeClient)
    observation = Observation(id="test-observation", session_id="session", page_id="page", url="http://127.0.0.1:5055/",
                              width=1100, height=800, screenshot_sha256="test-hash", screenshot_png=b"test-image",
                              controls=[VisibleControl(control_id="c1", frames=["workspace"], tag="a",
                                  text="Observed link", role="link", label="", input_type="",
                                  rect=Rect(x=100, y=200, width=80, height=25))])
    provider = OpenAIVision()
    assert await provider.choose("Find the member", observation, ["member_id"]) == parsed
    request = parse.call_args.kwargs
    assert request["store"] is False
    assert request["text_format"] is VisionChoice
    assert request["input"][0]["content"][1]["type"] == "input_image"
    import json
    prompt = json.loads(request["input"][0]["content"][0]["text"])
    assert prompt["visible_controls"] == [control.model_dump() for control in observation.controls]
    assert set(prompt) == {"goal", "observation_id", "image_width", "image_height", "input_names", "visible_controls"}
    assert "test-key-not-a-credential" not in str(request)


def test_missing_key_fails_without_network(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY_MISSING"):
        OpenAIVision()


@pytest.mark.parametrize("selection,control_id,x,y", [
    ("control_id", None, None, None),
    ("control_id", "c1", 20, 30),
    ("coordinates", "c1", 20, 30),
    ("coordinates", None, None, 30),
])
def test_invalid_selection_shapes_rejected(selection, control_id, x, y):
    with pytest.raises(ValidationError):
        VisionChoice(observation_id="test", action="click", selection=selection,
                     control_id=control_id, x=x, y=y, input_name=None)


async def test_discovery_prompt_contains_observations_and_executed_history_only(monkeypatch):
    import json
    from unittest.mock import MagicMock
    from interface_cu.vision import DiscoveryDecision
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-credential")
    decision = DiscoveryDecision(observation_id="test-observation", action="done", selection="none",
                                 control_id=None, x=None, y=None, input_name=None, extraction_name=None, url=None)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock()
    client.responses.parse = AsyncMock(return_value=SimpleNamespace(output_parsed=decision, id="scripted",
                                                                   model="scripted", usage=None))
    monkeypatch.setattr("interface_cu.vision.AsyncOpenAI", lambda **kwargs:client)
    observation = Observation(id="test-observation", session_id="session", page_id="page", url="http://127.0.0.1:5055/",
                              width=1100,height=800,screenshot_sha256="hash",screenshot_png=b"image",controls=[])
    history = [{"action":"fill", "input_name":"member_id", "status":"executed"}]
    extractions = {"balance_minor_units":{"parser":"usd_minor_units","description":"Requested balance"}}
    assert await OpenAIVision().decide("Read requested balance", observation, ["member_id"], extractions, history) == decision
    request = client.responses.parse.call_args.kwargs
    assert request["text_format"] is DiscoveryDecision
    prompt = json.loads(request["input"][0]["content"][0]["text"])
    assert prompt["executed_history"] == history
    assert prompt["remaining_extractions"] == extractions
    assert set(prompt) == {"goal","observation_id","image_width","image_height","current_url","input_names",
                           "visible_controls","readable_cells","remaining_extractions","executed_history"}
