"""Deterministic execution. This module has no model/provider dependency."""

import asyncio
import hashlib
import json
import math
from urllib.parse import urljoin

from pydantic import ValidationError

from .capability import Capability, validate_inputs
from .contracts import Bindings, BusinessOutcome, Extract, Failure, Navigate, Success, SurfaceError
from .handoff import OwnershipError
from .intervention import member_identity
from .policy import PolicyViolation
from .runtime import (
    RuntimeFault, RuntimeMonitor, RuntimeProfile, category_for, describe_conditions,
    diagnostic_state, safety_signals,
)


async def conditions_hold(surface, conditions, bindings):
    return all([await surface.evaluate(condition, bindings) for condition in conditions])


async def business_outcome(surface, rules, bindings):
    for rule in rules:
        if await surface.evaluate(rule.condition, bindings):
            return BusinessOutcome(code=rule.code)
    return None


def output_values(definitions, bindings):
    outputs = {}
    for name, definition in definitions.items():
        value = definition.validate_value(bindings.resolve(definition.binding))
        if definition.equals is not None and value != bindings.resolve(definition.equals):
            raise ValueError("OUTPUT_IDENTITY_MISMATCH")
        outputs[name] = value
    return outputs


def failure(evidence, code, step_id=None, *, uncertain=False, expected="Verified capability execution"):
    ref = evidence.emit("run_failed", code=code, step_id=step_id, expected=expected,
                        completion_uncertain=uncertain)
    return Failure(code=code, step_id=step_id, expected=expected, observed=code,
                   evidence_ref=ref, completion_uncertain=uncertain)


class Replay:
    def __init__(self, surface, executor, evidence, *, handoff=None):
        self.surface, self.executor, self.evidence = surface, executor, evidence
        self.handoff = handoff

    async def _intervene(self, step, bindings, generation, deadline, *, action_state, baseline=None):
        if self.handoff is None or isinstance(step.action, Extract) and action_state != "pending":
            return None
        return await self.handoff.check(mode="replay", capability_id=self.capability.capability_id,
            capability_version=self.capability.capability_version, goal=None, step_id=step.id,
            application_checkpoint=self.capability.application.checkpoint,
            identities=member_identity(self.capability.success_conditions, self.capability.inputs),
            bindings=bindings, generation=generation,
            checkpoint=step.preconditions if action_state == "pending" else step.postconditions,
            action_state=action_state, baseline=baseline, deadline=deadline)

    async def _failure(self, code, step_id=None, *, uncertain=False, category=None, human=None, cause=None):
        category = category or category_for(code)
        if self.monitor:
            uncertain = uncertain or self.monitor.in_action
            if self.monitor.pending_attempt:
                condition, strategy, attempt = self.monitor.pending_attempt
                self.monitor.record(condition, strategy, attempt,
                                    "blocked" if category in {"policy", "ownership"} else "failed", uncertain=uncertain)
        observed = await diagnostic_state(self.surface, self.evidence, step_id=step_id,
                                          checkpoint=self.expected, code=code, cause=cause)
        result = Failure(code=code, step_id=step_id, expected=self.evidence.sanitize(self.expected), observed=observed,
                         completion_uncertain=uncertain, capability_id=self.capability.capability_id if self.capability else None,
                         capability_version=self.capability.capability_version if self.capability else None,
                         category=category, recovery_attempts=self.monitor.history if self.monitor else [],
                         human_intervention_appropriate=human if human is not None else category not in {"validation", "policy", "internal"}
                         and code not in {"INTERVENTION_CANCELLED", "INTERVENTION_TIMEOUT", "INTERVENTION_UNRESOLVED"})
        ref = self.evidence.emit("run_failed", **result.model_dump(exclude={"evidence_ref"}))
        return result.model_copy(update={"evidence_ref":ref})

    async def _checkpoint_failure(self, code, step, conditions, bindings):
        cause = "CHECKPOINT_VALUE_MISMATCH"
        for condition in conditions:
            if not await self.surface.evaluate(condition, bindings):
                try:
                    await self.surface.resolve(condition.target, bindings)
                except SurfaceError as exc:
                    cause = exc.code
                break
        return await self._failure(code, step, cause=cause)

    async def run(self, artifact, inputs, *, timeout_seconds=90, runtime_profile=None):
        self.capability, self.monitor = None, None
        self.expected = "Valid declarative capability and invocation"
        try:
            # Revalidate even an existing model instance: model_copy/construct may
            # bypass Pydantic validation in calling code.
            raw = artifact.model_dump_json() if isinstance(artifact, Capability) else json.dumps(artifact)
            capability = Capability.model_validate_json(raw)
        except (ValidationError, ValueError, TypeError):
            return await self._failure("INVALID_ARTIFACT")
        self.capability = capability
        try:
            bindings = Bindings(inputs=validate_inputs(capability.inputs, inputs))
            if type(timeout_seconds) not in {int, float} or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
                raise ValueError("INVALID_TIMEOUT")
        except (ValueError, TypeError, ValidationError):
            return await self._failure("INVALID_INPUT")
        profile = None
        if runtime_profile is not None:
            try:
                encoded = runtime_profile.model_dump_json() if isinstance(runtime_profile, RuntimeProfile) else json.dumps(runtime_profile)
                profile = RuntimeProfile.model_validate_json(encoded)
                profile.validate_for(capability)
            except (ValidationError, ValueError, TypeError) as exc:
                code = "RUNTIME_PROFILE_MISMATCH" if str(exc) == "RUNTIME_PROFILE_MISMATCH" else "INVALID_RUNTIME_PROFILE"
                return await self._failure(code)
        generation = self.executor.control.generation
        self.monitor = RuntimeMonitor(self.surface, self.executor, self.evidence, profile, generation)
        digest = hashlib.sha256(capability.model_dump_json().encode()).hexdigest()
        self.evidence.emit("replay_started", capability_id=capability.capability_id,
                           capability_version=capability.capability_version, artifact_sha256=digest,
                           input_names=list(inputs), model_enabled=False,
                           runtime_profile_enabled=profile is not None,
                           runtime_profile_sha256=hashlib.sha256(profile.model_dump_json().encode()).hexdigest() if profile else None)
        active_step = None
        in_action = False
        unresolved_commit = False
        try:
            async with asyncio.timeout(timeout_seconds) as deadline:
                for index, step in enumerate(capability.steps):
                    active_step = step.id
                    self.executor.control.require_automation(generation)
                    if index:
                        self.expected = describe_conditions(step.preconditions)
                        outcome = await self.monitor.check(bindings, step.id)
                        if outcome:
                            return outcome
                        decision = await self._intervene(step, bindings, generation, deadline, action_state="pending")
                        if decision:
                            generation = self.monitor.generation = decision.ownership_generation
                        outcome = await business_outcome(self.surface, capability.business_outcomes, bindings)
                        if outcome:
                            self.evidence.emit("business_outcome", code=outcome.code, step_id=step.id)
                            return outcome
                    self.expected = describe_conditions(step.preconditions)
                    if not await conditions_hold(self.surface, step.preconditions, bindings):
                        return await self._checkpoint_failure("PRECONDITION_FAILED", step.id, step.preconditions, bindings)
                    action = step.action
                    if isinstance(action, Navigate):
                        action = Navigate(url=urljoin(self.executor.policy.origin + "/", action.url))
                    # Only a false -> true checkpoint transition can establish
                    # commit after a primitive reports uncertain completion.
                    # A fresh surface may still be about:blank, outside the app
                    # policy. No pre-entry observation can establish a baseline.
                    baseline = None
                    if index > 0:
                        baseline = await conditions_hold(self.surface, step.postconditions, bindings) if step.postconditions else True
                    self.expected = describe_conditions(step.postconditions)
                    in_action = True
                    result = await asyncio.wait_for(self.executor.execute(action, bindings, expected_generation=generation), step.timeout_seconds)
                    in_action = False
                    self.evidence.emit("replay_step", step_id=step.id, result=result.model_dump(exclude={"data"}))
                    if result.status != "executed":
                        unresolved_commit = result.completion_uncertain
                        await safety_signals(self.surface)
                        if category_for(result.code) in {"policy", "ownership"}:
                            return await self._failure(result.code, step.id, uncertain=result.completion_uncertain)
                        if not result.completion_uncertain:
                            return await self._failure(result.code, step.id)
                        decision = await self._intervene(step, bindings, generation, deadline, action_state="uncertain", baseline=baseline)
                        if decision:
                            generation = self.monitor.generation = decision.ownership_generation
                        elif not await self.monitor.reconcile(step, bindings, baseline, result):
                            return await self._failure("ACTION_COMPLETION_UNCERTAIN", step.id, uncertain=True, cause=result.code)
                        unresolved_commit = False
                    if isinstance(action, Extract):
                        bindings = bindings.model_copy(update={"extracted": {**bindings.extracted, action.name: result.data}})
                    if index == 0 and not await self.surface.evaluate(capability.application.checkpoint, bindings):
                        return await self._failure("APPLICATION_MISMATCH", step.id)
                    outcome = await self.monitor.check(bindings, step.id)
                    if outcome:
                        return outcome
                    decision = await self._intervene(step, bindings, generation, deadline, action_state="executed", baseline=baseline)
                    if decision:
                        generation = self.monitor.generation = decision.ownership_generation
                    snapshot = await self.surface.observe()
                    self.evidence.screenshot(snapshot.screenshot_png)
                    outcome = await business_outcome(self.surface, capability.business_outcomes, bindings)
                    if outcome:
                        self.evidence.emit("business_outcome", code=outcome.code, step_id=step.id)
                        return outcome
                    if not await conditions_hold(self.surface, step.postconditions, bindings):
                        return await self._checkpoint_failure("POSTCONDITION_FAILED", step.id, step.postconditions, bindings)
                self.expected = describe_conditions(capability.success_conditions)
                if not await conditions_hold(self.surface, capability.success_conditions, bindings):
                    return await self._checkpoint_failure("SUCCESS_CONDITION_FAILED", active_step, capability.success_conditions, bindings)
                outputs = output_values(capability.outputs, bindings)
                self.evidence.emit("replay_completed", outputs=outputs, verified_success=True, model_calls=0)
                return Success(outputs=outputs)
        except TimeoutError:
            if self.monitor.pending_attempt:
                code, strategy, attempt = self.monitor.pending_attempt
                self.monitor.record(code, strategy, attempt, "failed", uncertain=self.monitor.in_action)
            return await self._failure("REPLAY_TIMEOUT", active_step, uncertain=in_action or self.monitor.in_action or unresolved_commit)
        except RuntimeFault as exc:
            return await self._failure(exc.code, active_step, category=exc.category, human=exc.human, uncertain=exc.uncertain or unresolved_commit)
        except (SurfaceError, PolicyViolation, OwnershipError) as exc:
            return await self._failure(exc.code if isinstance(exc, SurfaceError) else str(exc), active_step,
                                       uncertain=getattr(exc, "completion_uncertain", False) or unresolved_commit)
        except ValueError as exc:
            codes = {"VALUE_TYPE_MISMATCH":"OUTPUT_TYPE_MISMATCH", "INVALID_MEMBER_ID":"OUTPUT_FORMAT_MISMATCH",
                     "INVALID_CURRENCY":"OUTPUT_FORMAT_MISMATCH", "VALUE_NOT_IN_ENUM":"OUTPUT_ENUM_MISMATCH",
                     "OUTPUT_IDENTITY_MISMATCH":"OUTPUT_IDENTITY_MISMATCH", "Unbound reference":"UNBOUND_REFERENCE"}
            return await self._failure(codes.get(str(exc), "INVALID_BINDING"), active_step)
        except Exception as exc:
            self.evidence.emit("replay_exception", error_type=type(exc).__name__)
            return await self._failure("REPLAY_INTERNAL_ERROR", active_step, category="internal", human=False)
