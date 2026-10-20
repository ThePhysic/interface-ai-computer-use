"""Bounded model-directed discovery; acceptance checks remain deterministic."""

import asyncio
import math
from dataclasses import dataclass, field

from .capability import Step, validate_inputs
from .contracts import (
    Bindings, Click, Extract, Fill, InputRef, LiteralValue, Navigate, Ownership,
    RoleLocator, Scalar, Success, SurfaceError, TargetSpec, TerminalResult, ValueEquals, Visible,
)
from .handoff import OwnershipError
from .intervention import member_identity
from .policy import PolicyViolation
from .replay import business_outcome, conditions_hold, failure, output_values
from .task import DiscoveryTask


@dataclass
class DiscoveryTrace:
    task: DiscoveryTask
    invocation: dict[str, Scalar]
    run_id: str
    evidence_file: str
    source: str
    model: str = "unknown"
    response_ids: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    verified_success: bool = False
    human_assisted: bool = False


@dataclass
class DiscoveryResult:
    result: TerminalResult
    trace: DiscoveryTrace


class Discovery:
    def __init__(self, surface, executor, provider, evidence, *, handoff=None):
        self.surface, self.executor, self.provider, self.evidence = surface, executor, provider, evidence
        self.in_action = False
        self.handoff = handoff

    async def _intervene(self, task, bindings, trace, generation, deadline, goal, *, action_state):
        if self.handoff is None:
            return generation
        decision = await self.handoff.check(mode="discovery", capability_id=task.capability_id,
            capability_version=None, goal=goal, step_id=f"s{len(trace.steps) + 1:03}",
            application_checkpoint=task.application.checkpoint,
            identities=member_identity(task.success_conditions, task.inputs), bindings=bindings,
            generation=generation, checkpoint=self.handoff.rule.resume_checkpoint,
            action_state=action_state, deadline=deadline)
        if decision:
            trace.human_assisted = True
            return decision.ownership_generation
        return generation

    async def run(self, goal, entrypoint, task, inputs, *, max_steps=16, timeout_seconds=180):
        trace = DiscoveryTrace(task=task, invocation=inputs.copy() if isinstance(inputs, dict) else {}, run_id=self.evidence.run_id,
                               evidence_file=str(self.evidence.directory / "events.jsonl"), source=self.evidence.source)
        generation = self.executor.control.generation
        self.in_action = False
        try:
            validated = validate_inputs(task.inputs, inputs)
            if (type(max_steps) is not int or not 1 <= max_steps <= 39
                    or type(timeout_seconds) not in {int, float}
                    or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
                raise ValueError("INVALID_LIMITS")
        except (ValueError, TypeError):
            return DiscoveryResult(failure(self.evidence, "INVALID_INPUT"), trace)
        try:
            async with asyncio.timeout(timeout_seconds) as deadline:
                result = await self._run(goal, entrypoint, task, Bindings(inputs=validated), trace, max_steps, generation, deadline)
        except TimeoutError:
            result = failure(self.evidence, "DISCOVERY_TIMEOUT", uncertain=self.in_action)
        except (SurfaceError, PolicyViolation, OwnershipError) as exc:
            result = failure(self.evidence, exc.code if isinstance(exc, SurfaceError) else str(exc),
                             uncertain=getattr(exc, "completion_uncertain", False))
        except Exception as exc:
            # No raw SDK error body or model output in diagnostics.
            self.evidence.emit("discovery_exception", error_type=type(exc).__name__,
                               http_status=getattr(exc, "status_code", None), api_error_code=getattr(exc, "code", None))
            result = failure(self.evidence, "DISCOVERY_ERROR")
        trace.human_assisted = trace.human_assisted or self.executor.control.generation != generation or self.executor.control.state != Ownership.AUTOMATION
        return DiscoveryResult(result, trace)

    async def _run(self, goal, entrypoint, task, bindings, trace, max_steps, generation, deadline):
        self.evidence.emit("discovery_started", input_names=list(bindings.inputs), max_steps=max_steps)
        self.in_action = True
        initial = await self.executor.execute(Navigate(url=entrypoint), bindings, expected_generation=generation)
        self.in_action = False
        if initial.status != "executed":
            return failure(self.evidence, initial.code, uncertain=initial.completion_uncertain)
        if not await self.surface.evaluate(task.application.checkpoint, bindings):
            return failure(self.evidence, "APPLICATION_MISMATCH")
        trace.steps.append(Step(id="s001", action=Navigate(url=entrypoint), postconditions=[task.application.checkpoint]))
        history = []
        for _ in range(max_steps):
            self.executor.control.require_automation(generation)
            generation = await self._intervene(task, bindings, trace, generation, deadline, goal, action_state="pending")
            outcome = await business_outcome(self.surface, task.business_outcomes, bindings)
            if outcome:
                return outcome
            pending = {name: spec for name, spec in task.extractions.items() if name not in bindings.extracted}
            if not pending and await conditions_hold(self.surface, task.success_conditions, bindings):
                outputs = output_values(task.outputs, bindings)
                trace.verified_success = True
                self.evidence.emit("discovery_completed", outputs=outputs, verified_success=True,
                                   step_count=len(trace.steps), model_calls=len(trace.response_ids))
                return Success(outputs=outputs)
            observation = await self.surface.observe()
            self.evidence.screenshot(observation.screenshot_png)
            self.evidence.emit("discovery_model_request", observation_id=observation.id,
                               screenshot_sha256=observation.screenshot_sha256,
                               visible_controls=[c.model_dump() for c in observation.controls],
                               readable_cells=[c.model_dump() for c in observation.readables],
                               pending_extractions=list(pending))
            choice = await self.provider.decide(goal, observation, list(task.inputs),
                                               {k: v.model_dump() for k, v in pending.items()}, history[-20:])
            response_id = self.provider.response_id
            if not response_id:
                raise ValueError("MISSING_RESPONSE_PROVENANCE")
            trace.response_ids.append(response_id)
            trace.model = self.provider.returned_model
            self.evidence.emit("discovery_model_response", response_id=response_id, model=trace.model, choice=choice.model_dump())
            if choice.observation_id != observation.id:
                raise SurfaceError("STALE_OBSERVATION")
            if choice.action in {"done", "stop"}:
                return failure(self.evidence, "UNVERIFIED_COMPLETION" if choice.action == "done" else "MODEL_STOPPED")
            if choice.action == "navigate":
                action = Navigate(url=choice.url)
                preconditions = []
                label = "navigation"
            else:
                target = (await self.surface.ground_control(observation.id, choice.control_id, bindings)
                          if choice.selection == "control_id" else
                          await self.surface.ground_point(observation, choice.x, choice.y, bindings))
                preconditions = [Visible(target=target)]
                selected = next((c for c in [*observation.controls, *observation.readables] if c.control_id == choice.control_id), None)
                label = (selected.label or selected.text) if selected else "coordinate selection"
                if choice.action == "fill":
                    if choice.input_name not in task.inputs:
                        raise ValueError("UNKNOWN_INPUT_REFERENCE")
                    action = Fill(target=target, value=InputRef(name=choice.input_name))
                elif choice.action == "extract":
                    if choice.extraction_name not in pending:
                        raise ValueError("UNKNOWN_EXTRACTION")
                    action = Extract(target=target, name=choice.extraction_name,
                                     parser=pending[choice.extraction_name].parser)
                else:
                    action = Click(target=target)
            self.in_action = True
            result = await self.executor.execute(action, bindings, expected_generation=generation)
            self.in_action = False
            if result.status != "executed":
                return failure(self.evidence, result.code, uncertain=result.completion_uncertain)
            if isinstance(action, Extract):
                definition = task.outputs.get(action.name)
                if definition:
                    definition.validate_value(result.data)
                    if definition.equals is not None and result.data != bindings.resolve(definition.equals):
                        return failure(self.evidence, "OUTPUT_IDENTITY_MISMATCH")
                bindings = bindings.model_copy(update={"extracted": {**bindings.extracted, action.name: result.data}})
            generation = await self._intervene(task, bindings, trace, generation, deadline, goal, action_state="executed")
            after = await self.surface.observe()
            self.evidence.screenshot(after.screenshot_png)
            postconditions = [Visible(target=TargetSpec(frames=h.frames, candidates=[RoleLocator(
                role="heading", name=LiteralValue(value=h.text))], rationale="Observed page heading after action")) for h in after.headings]
            if isinstance(action, Fill):
                postconditions.append(ValueEquals(target=action.target, value=action.value))
            # Preserve independently observed record identity at intermediate views.
            for condition in task.success_conditions:
                if await self.surface.evaluate(condition, bindings) and condition not in postconditions:
                    postconditions.append(condition)
            if not await conditions_hold(self.surface, postconditions, bindings):
                return failure(self.evidence, "DISCOVERY_POSTCONDITION_FAILED")
            step = Step(id=f"s{len(trace.steps) + 1:03}", action=action,
                        preconditions=preconditions, postconditions=postconditions)
            trace.steps.append(step)
            self.evidence.emit("discovery_step", step_id=step.id, action=action.model_dump(),
                               result=result.model_dump(exclude={"data"}), postconditions_verified=True)
            history.append({"action":choice.action, "label":label, "input_name":choice.input_name,
                            "extraction_name":choice.extraction_name, "status":"executed"})
        # Completion at the exact action budget is still independently verified.
        if set(task.extractions) <= bindings.extracted.keys() and await conditions_hold(self.surface, task.success_conditions, bindings):
            outputs = output_values(task.outputs, bindings)
            trace.verified_success = True
            self.evidence.emit("discovery_completed", outputs=outputs, verified_success=True,
                               step_count=len(trace.steps), model_calls=len(trace.response_ids))
            return Success(outputs=outputs)
        return failure(self.evidence, "MAX_STEPS_REACHED")
