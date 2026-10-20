"""Submission boundary regressions; no model requests or paid services."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from interface_cu.capability import Capability
from interface_cu.discovery import Discovery
from interface_cu.evidence import Evidence
from interface_cu.replay import Replay
from interface_cu.task import savings_task


ARTIFACT = Path(__file__).resolve().parents[1] / "evidence/phase2/savings.capability.json"


@pytest.mark.parametrize("timeout", [float("inf"), float("nan"), float("-inf"), True, 0])
async def test_nonfinite_or_invalid_deadline_cannot_reach_surface(tmp_path, timeout):
    # These sentinels have no surface/provider methods: any attempted use fails.
    evidence = Evidence(tmp_path, "automated_test")
    task = savings_task()
    executor = SimpleNamespace(control=SimpleNamespace(generation=0))
    discovered = await Discovery(object(), executor, object(), evidence).run(
        "Read savings", "http://127.0.0.1:5055/", task, {"member_id":"06789"}, timeout_seconds=timeout)
    assert discovered.result.code == "INVALID_INPUT"
    capability = Capability.model_validate_json(ARTIFACT.read_bytes())
    result = await Replay(object(), object(), evidence).run(capability, {"member_id":"06789"}, timeout_seconds=timeout)
    assert result.code == "INVALID_INPUT"


@pytest.mark.parametrize("steps", [True, 1.5, 0, 40])
async def test_discovery_step_budget_is_a_bounded_integer(tmp_path, steps):
    executor = SimpleNamespace(control=SimpleNamespace(generation=0))
    result = await Discovery(object(), executor, object(), Evidence(tmp_path,"automated_test")).run(
        "Read savings", "http://127.0.0.1:5055/", savings_task(), {"member_id":"06789"}, max_steps=steps)
    assert result.result.code == "INVALID_INPUT"


@pytest.mark.browser
async def test_cli_reads_windows_utf8_bom_inputs_and_profile(origin, tmp_path):
    import asyncio
    import subprocess
    import sys
    inputs, profile = tmp_path / "inputs.json", tmp_path / "runtime.json"
    inputs.write_text(json.dumps({"member_id":"06789"}), encoding="utf-8-sig")
    saved_profile = ARTIFACT.parents[1] / "phase3/savings.runtime.json"
    profile.write_text(saved_profile.read_text(encoding="utf-8"), encoding="utf-8-sig")
    command = [sys.executable,"-m","interface_cu.cli","replay","--origin",origin,
               "--artifact",str(ARTIFACT),"--inputs",str(inputs),"--runtime-profile",str(profile),
               "--evidence",str(tmp_path / "replay")]
    process = await asyncio.create_subprocess_exec(*command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    stdout,stderr = await asyncio.wait_for(process.communicate(),45)
    assert process.returncode == 0, (stdout,stderr)
    assert json.loads(stdout)["result"]["outputs"] == {"member_id":"06789","balance_minor_units":987654,"currency":"USD"}
