"""Local demo, feasibility probes, discovery/compilation and model-free replay."""

import argparse
import asyncio
import json
import hashlib
import ipaddress
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .browser import open_browser
from .contracts import (
    Bindings, Click, Fill, InputRef, LiteralValue, Navigate, Predicate, RoleLocator,
    ResolutionError, StructuralLocator, SurfaceError, TargetSpec, ValueEquals, Visible,
)
from .demo_app import SCENARIOS, create_app
from .evidence import Evidence
from .execution import GuardedExecutor
from .handoff import SessionControl
from .policy import Policy


def vision_checkpoint(entry: str) -> tuple[str, list[Predicate]]:
    """Independent demo acceptance criteria, never part of the model's prompt."""
    if entry not in {"/", "/members"}:
        raise ValueError("UNSUPPORTED_PROBE_ENTRY")
    frames = ["workspace"] if entry == "/" else []
    heading = TargetSpec(frames=frames, candidates=[RoleLocator(
        role="heading", name=LiteralValue(value="Member search"))],
        rationale="Expected member-search page heading")
    field = TargetSpec(frames=frames, candidates=[StructuralLocator(
        tag="input", name="member_id", adjacent_label=LiteralValue(value="Member ID"))],
        expected_tag="input", rationale="Member ID field next to its visible caption")
    if entry == "/":
        return "member_search_opened", [Visible(target=heading), Visible(target=field)]
    return "member_id_entered", [Visible(target=heading), ValueEquals(
        target=field, value=InputRef(name="member_id"))]


async def vision_probe(args):
    # Importing the CLI for offline browser tests never constructs a model client.
    from .vision import OpenAIVision

    evidence = Evidence(args.evidence, "live_llm")
    try:
        provider = OpenAIVision(args.model)
        policy, control = Policy(origin=args.origin), SessionControl(evidence)
        bindings = Bindings(inputs={"member_id": "12345"})
        checkpoint_name, predicates = vision_checkpoint(args.entry)
        async with open_browser(policy, control, evidence, headed=args.headed) as surface:
            executor = GuardedExecutor(surface, policy, control, evidence)
            navigation = await executor.execute(Navigate(url=args.origin + args.entry))
            if navigation.status != "executed":
                raise ValueError("ENTRY_NAVIGATION_FAILED")
            satisfied_before = all([await surface.evaluate(p, bindings) for p in predicates])
            if satisfied_before:
                raise ValueError("CHECKPOINT_ALREADY_SATISFIED")
            evidence.emit("probe_checkpoint", name=checkpoint_name, satisfied_before=False,
                          predicates=[p.model_dump() for p in predicates])
            before = await surface.observe()
            before_file = evidence.screenshot(before.screenshot_png)
            evidence.emit("model_request", model=provider.model, observation_id=before.id,
                          screenshot_sha256=before.screenshot_sha256, screenshot=before_file,
                          image_width=before.width, image_height=before.height,
                          input_names=["member_id"], observation_mode="screenshot_with_visible_controls",
                          visible_controls=[control.model_dump() for control in before.controls])
            choice = await provider.choose(args.goal, before, ["member_id"])
            evidence.emit("model_response", response_id=provider.response_id, model=provider.returned_model,
                          usage=provider.usage, choice=choice.model_dump())
            if choice.observation_id != before.id:
                raise ResolutionError("STALE_OBSERVATION")
            if choice.selection == "control_id":
                target = await surface.ground_control(choice.observation_id, choice.control_id)
            else:
                target = await surface.ground_point(before, choice.x, choice.y)
            if choice.action == "fill":
                if choice.input_name != "member_id":
                    raise ValueError("UNKNOWN_MODEL_INPUT_REFERENCE")
                action = Fill(target=target, value=InputRef(name="member_id"))
            else:
                if choice.input_name is not None:
                    raise ValueError("CLICK_MUST_NOT_BIND_INPUT")
                action = Click(target=target)
            evidence.emit("grounded_target", observation_id=before.id, selection=choice.selection,
                          control_id=choice.control_id, target=target.model_dump())
            result = await executor.execute(action, bindings)
            after = await surface.observe()
            evidence.screenshot(after.screenshot_png)
            satisfied_after = all([await surface.evaluate(p, bindings) for p in predicates])
            worked = result.status == "executed" and not satisfied_before and satisfied_after
            evidence.emit("probe_finished", checkpoint_name=checkpoint_name,
                          checkpoint_satisfied_before=satisfied_before,
                          checkpoint_satisfied_after=satisfied_after,
                          live_model_action_verified=worked, action_result=result.model_dump(),
                          full_workflow_completed=False)
            print(json.dumps({"live_model_action_verified": worked, "evidence": str(evidence.directory), "action": choice.action}))
            return 0 if worked else 1
    except Exception as exc:
        # SDK errors can contain request bodies. Persist only a type, not repr/str.
        error = {"error_type": type(exc).__name__, "http_status": getattr(exc, "status_code", None),
                 "api_error_code": None if isinstance(exc, SurfaceError) else getattr(exc, "code", None),
                 "surface_error_code": exc.code if isinstance(exc, SurfaceError) else None}
        evidence.emit("probe_blocked", **error, live_model_action_verified=False)
        print(json.dumps({"live_model_action_verified": False, **error, "evidence": str(evidence.directory)}))
        return 1


async def human_probe(args):
    evidence = Evidence(args.evidence, "manual_probe")
    policy, control = Policy(origin=args.origin), SessionControl(evidence)
    async with open_browser(policy, control, evidence, headed=True) as surface:
        executor = GuardedExecutor(surface, policy, control, evidence)
        result = await executor.execute(Navigate(url=args.origin + "/"))
        if result.status != "executed":
            print("Demo application unavailable; start the serve command first.")
            return 1
        await control.give_to_human()
        evidence.emit("manual_probe_started", session_id=surface.session_id, page_id=surface.page_id)
        print(f"Manual control: use the existing browser to search for member 12345. Evidence: {evidence.directory}", flush=True)
        if args.duration:
            # Timer mode lets an IDE-launched process keep its browser event loop alive.
            await asyncio.sleep(args.duration)
        else:
            await asyncio.to_thread(input, "Return here and press Enter when finished (probe ends; no automatic resume): ")
        await surface.flush_activity()
        count = surface.capture.events
        await control.finish_probe()
        evidence.emit("manual_probe_finished", captured_events=count, automatic_resume_implemented=False)
        print(json.dumps({"captured_events": count, "evidence": str(evidence.directory)}))
        return 0 if count else 1


def _offline_process():
    """CLI-only defense: remove provider keys and prohibit Python network egress."""
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ.pop(name, None)

    def audit(event, args):
        if event == "socket.getaddrinfo":
            host = args[0]
        elif event == "socket.connect":
            address = args[1]
            if not isinstance(address, tuple):
                return
            host = address[0]
        else:
            return
        if host == "localhost":
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise RuntimeError("OFFLINE_NETWORK_DENIED")

    sys.addaudithook(audit)


async def workflow_command(args):
    from .replay import Replay, failure

    replaying = args.command == "replay"
    evidence = Evidence(args.evidence, "deterministic_replay" if replaying else "live_llm")
    try:
        inputs = json.loads(args.inputs.read_text(encoding="utf-8-sig")) if args.inputs else {"member_id":args.member_id}
        if not replaying and args.artifact.exists():
            raise FileExistsError("Artifact already exists")
        artifact_bytes = args.artifact.read_bytes() if replaying else None
        artifact = json.loads(artifact_bytes) if replaying else None
        policy, control = Policy(origin=args.origin), SessionControl(evidence)
        if replaying:
            evidence.emit("offline_replay", provider_environment_empty=not os.environ.get("OPENAI_API_KEY"),
                          artifact_file_sha256=hashlib.sha256(artifact_bytes).hexdigest())
        async with open_browser(policy, control, evidence, headed=args.headed) as surface:
            executor = GuardedExecutor(surface, policy, control, evidence)
            handoff = None
            if args.handoff:
                from .intervention import ConsoleOperator, HandoffCoordinator, banking_intervention_rule
                handoff = HandoffCoordinator(surface, executor, evidence, ConsoleOperator(), banking_intervention_rule(),
                                             operator_timeout=args.operator_timeout)
            if replaying:
                profile = json.loads(args.runtime_profile.read_text(encoding="utf-8-sig")) if args.runtime_profile else None
                result = await Replay(surface, executor, evidence, handoff=handoff).run(artifact, inputs, timeout_seconds=args.timeout, runtime_profile=profile)
            else:
                from .compiler import compile_capability
                from .discovery import Discovery
                from .task import savings_task
                from .vision import OpenAIVision

                discovered = await Discovery(surface, executor, OpenAIVision(args.model), evidence, handoff=handoff).run(
                    args.goal, args.origin + args.entry, savings_task(), inputs,
                    max_steps=args.max_steps, timeout_seconds=args.timeout)
                result = discovered.result
                from .contracts import Success
                if isinstance(result, Success) and discovered.trace.human_assisted:
                    evidence.emit("capability_compilation_skipped", reason="HUMAN_ASSISTED_TRACE", artifact_saved=False)
                elif isinstance(result, Success):
                    capability = compile_capability(discovered, origin=args.origin)
                    args.artifact.parent.mkdir(parents=True, exist_ok=True)
                    with args.artifact.open("x", encoding="utf-8") as handle:
                        handle.write(capability.model_dump_json(indent=2) + "\n")
                    evidence.emit("capability_compiled", artifact=str(args.artifact),
                                  capability_id=capability.capability_id, step_count=len(capability.steps))
            await control.finish_run(result.status in {"success", "business_outcome"})
    except Exception as exc:
        evidence.emit("command_exception", error_type=type(exc).__name__,
                      http_status=getattr(exc, "status_code", None), api_error_code=getattr(exc, "code", None))
        result = failure(evidence, "COMMAND_FAILED")
    evidence.emit("command_result", result=result.model_dump())
    (evidence.directory / "result.json").write_text(json.dumps(result.model_dump(), indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result":result.model_dump(), "evidence":str(evidence.directory)}))
    return 0 if result.status in {"success", "business_outcome"} else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=5055)
    serve.add_argument("--scenario", choices=SCENARIOS, default="normal")
    for name in ("vision-probe", "human-probe"):
        command = sub.add_parser(name)
        command.add_argument("--origin", default="http://127.0.0.1:5055")
        command.add_argument("--evidence", type=Path, default=Path(".runs"))
        if name == "vision-probe":
            command.add_argument("--goal", default="Look up member 12345 and read their current savings balance.")
            command.add_argument("--entry", choices=["/", "/members"], default="/")
            command.add_argument("--model")
            command.add_argument("--headed", action="store_true")
        else:
            command.add_argument("--duration", type=int, choices=range(30, 601))
    for name in ("discover", "replay"):
        command = sub.add_parser(name)
        command.add_argument("--origin", default="http://127.0.0.1:5055")
        command.add_argument("--evidence", type=Path, default=Path(".runs"))
        command.add_argument("--artifact", type=Path, required=True)
        command.add_argument("--headed", action="store_true")
        command.add_argument("--handoff", action="store_true", help="Enable same-session operator acknowledgement and checked resumption")
        command.add_argument("--operator-timeout", type=float, default=180, help="Seconds allowed for each operator response (maximum 600)")
        command.add_argument("--timeout", type=float, default=180 if name == "discover" else 90)
        values = command.add_mutually_exclusive_group(required=True)
        values.add_argument("--member-id")
        values.add_argument("--inputs", type=Path, help="JSON invocation values")
        if name == "discover":
            command.add_argument("--goal", required=True)
            command.add_argument("--entry", default="/")
            command.add_argument("--max-steps", type=int, default=16)
            command.add_argument("--model")
        else:
            command.add_argument("--runtime-profile", type=Path, help="Versioned declarative application exception/recovery rules")
    args = parser.parse_args()
    if args.command == "serve":
        # Headed Chromium can open an idle speculative connection before the
        # guarded route.fetch request. A single-threaded server can wait on that
        # connection and starve the actual GET. Keep one fixture process, allowing
        # Flask's ordinary request threads to serve the inspected read.
        create_app(args.scenario).run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False, threaded=True)
        return 0
    if args.command == "replay":
        _offline_process()
    if args.command in {"discover", "replay"} and args.handoff and not args.headed:
        parser.error("--handoff requires --headed so the operator can use the existing browser.")
    origin = urlsplit(args.origin)
    if origin.hostname not in {"127.0.0.1", "localhost"} or origin.scheme != "http" or origin.path or origin.query or origin.fragment or origin.username or origin.password:
        parser.error("Commands are restricted to a local synthetic target origin.")
    if args.command in {"discover", "replay"}:
        return asyncio.run(workflow_command(args))
    return asyncio.run(vision_probe(args) if args.command == "vision-probe" else human_probe(args))


if __name__ == "__main__":
    raise SystemExit(main())
