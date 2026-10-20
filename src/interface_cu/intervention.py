"""Shared, bounded same-session intervention. No model or workflow execution here."""

import asyncio
import sys
import threading
from contextlib import asynccontextmanager
from typing import Protocol

from .contracts import (
    Contract, InputRef, InterventionRequest, OperatorResponse, Ownership, Predicate,
    ResumeConditions, ResumeDecision, SurfaceError, TextEquals, identifier,
)
from .handoff import OwnershipError
from .policy import PolicyViolation
from .runtime import RuntimeFault, describe_conditions, diagnostic_state, safety_signals


class InterventionRule(Contract):
    code: str
    blocker: Predicate
    resume_checkpoint: list[Predicate]


class Operator(Protocol):
    async def respond(self, request: InterventionRequest) -> OperatorResponse: ...


class ConsoleOperator:
    async def respond(self, request):
        print(f"\nIntervention {request.id}: {request.reason} at {request.step_id}.\n"
              f"Use the existing browser. Required state: {request.expected}\n"
              "Resolve the acknowledgement for the displayed member, then stop on Member record.\n"
              "Enter resume (or press Enter), cancel, or unresolved:", file=sys.stderr, flush=True)
        for _ in range(3):
            # A daemon reader avoids asyncio.to_thread's shutdown join hanging
            # forever after abandonment. Browser processing stays on the event loop.
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            def read_line():
                try:
                    line = sys.stdin.readline()
                except (OSError, ValueError):
                    line = ""
                def deliver():
                    if not future.done():
                        future.set_result(line)
                try:
                    loop.call_soon_threadsafe(deliver)
                except RuntimeError:
                    pass  # A timed-out command may already have closed its loop.
            threading.Thread(target=read_line, daemon=True).start()
            line = await future
            if line == "":
                action = "unresolved"  # EOF is abandonment, not confirmation.
            else:
                action = line.strip().lower() or "resume"
            if action in {"resume", "cancel", "unresolved"}:
                return OperatorResponse(intervention_id=request.id, ownership_generation=request.ownership_generation, action=action)
            print("Use resume, cancel, or unresolved.", file=sys.stderr, flush=True)
        return OperatorResponse(intervention_id=request.id, ownership_generation=request.ownership_generation, action="unresolved")


@asynccontextmanager
async def operator_budget(deadline):
    """Pause only the automation deadline; operator waiting has its own bound."""
    remaining = None
    if deadline is not None and deadline.when() is not None:
        remaining = deadline.when() - asyncio.get_running_loop().time()
        if remaining <= 0 or deadline.expired():
            raise TimeoutError
        deadline.reschedule(None)
    try:
        yield
    finally:
        if remaining is not None:
            deadline.reschedule(asyncio.get_running_loop().time() + remaining)


def member_identity(conditions, inputs):
    """Reuse declared identity bindings, never guess an ID from a URL or text."""
    return [condition for condition in conditions if isinstance(condition, TextEquals)
            and isinstance(condition.value, InputRef) and condition.value.name in inputs
            and inputs[condition.value.name].format == "member_id"]


class HandoffCoordinator:
    def __init__(self, surface, executor, evidence, operator: Operator, rule: InterventionRule,
                 *, operator_timeout=180.0, max_interventions=2):
        if not 0 < operator_timeout <= 600 or not 1 <= max_interventions <= 3:
            raise ValueError("INVALID_INTERVENTION_LIMITS")
        self.surface, self.executor, self.evidence = surface, executor, evidence
        self.operator, self.rule = operator, rule
        self.operator_timeout, self.max_interventions = operator_timeout, max_interventions
        self.requests: list[InterventionRequest] = []

    async def _safe_navigation(self, original=None):
        nav = await self.surface.navigation_state()
        if original and (nav.session_id, nav.page_id) != (original.session_id, original.page_id):
            raise SurfaceError("SESSION_IDENTITY_CHANGED")
        await safety_signals(self.surface)  # Never clears policy/native latches.
        for url in [nav.url, *nav.frame_urls]:
            self.executor.policy.check_url(url)
        return nav

    async def _all(self, conditions, bindings):
        return bool(conditions) and all([await self.surface.evaluate(c, bindings) for c in conditions])

    def _status(self, request, status, generation=None):
        request = request.model_copy(update={"status":status, "ownership_generation":generation if generation is not None else request.ownership_generation})
        self.requests[-1] = request
        self.evidence.emit("intervention_status", intervention_id=request.id, status=status,
                           ownership_generation=request.ownership_generation)
        return request

    async def check(self, *, mode, capability_id, capability_version, goal, step_id,
                    application_checkpoint, identities, bindings, generation, checkpoint,
                    action_state, baseline=None, deadline=None):
        """Suspend only a recognized operator-resolvable blocker, at a settled boundary.

        The caller retains its stack/cursor, bindings, outputs and recovery state.
        Policy failures, arbitrary app errors and invalid artifacts are not routed.
        """
        if not await self.surface.evaluate(self.rule.blocker, bindings):
            return None
        if len(self.requests) >= self.max_interventions:
            raise SurfaceError("INTERVENTION_LIMIT_REACHED")
        original = await self._safe_navigation()
        if not await self._all([application_checkpoint], bindings) or not await self._all(identities, bindings):
            raise SurfaceError("INTERVENTION_CONTEXT_MISMATCH")
        control = self.executor.control
        request_id = identifier()
        paused = await control.pause(request_id, generation)
        request = None
        status = "failed"
        try:
            await self._safe_navigation(original)
            if not await self.surface.evaluate(self.rule.blocker, bindings):
                raise SurfaceError("INTERVENTION_STATE_CHANGED")
            conditions = list(checkpoint) or list(self.rule.resume_checkpoint)
            expected = describe_conditions([*self.rule.resume_checkpoint, *identities, *conditions])
            observed = await diagnostic_state(self.surface, self.evidence, step_id=step_id,
                                              checkpoint=expected, code=self.rule.code)
            ref = self.evidence.emit("intervention_context", step_id=step_id, observed=observed)
            request = InterventionRequest(id=request_id, run_id=self.evidence.run_id, mode=mode,
                capability_id=capability_id, capability_version=capability_version, goal=goal,
                step_id=step_id, reason=self.rule.code, session_id=original.session_id, page_id=original.page_id,
                observed=observed, evidence_ref=ref, expected=expected,
                completion_uncertain=action_state == "uncertain", ownership_generation=paused,
                allowed_resume=ResumeConditions(application_checkpoint=application_checkpoint, member_identity=identities,
                    workflow_checkpoint=conditions, action_state=action_state, baseline_satisfied=baseline))
            self.requests.append(request)
            self.evidence.emit("intervention_requested", request=request.model_dump(mode="json"))
            human_generation = await control.transfer_to_human(request_id, paused)
            request = self._status(request, "human", human_generation)
            try:
                async with operator_budget(deadline):
                    response = await asyncio.wait_for(self.operator.respond(request), self.operator_timeout)
            except TimeoutError:
                status = "timed_out"
                raise SurfaceError("INTERVENTION_TIMEOUT") from None
            except asyncio.CancelledError:
                status = "cancelled"
                raise SurfaceError("INTERVENTION_CANCELLED") from None
            response = OperatorResponse.model_validate(response.model_dump())
            if response.intervention_id != request_id or response.ownership_generation != human_generation:
                status = "rejected"
                raise SurfaceError("STALE_INTERVENTION")
            self.evidence.emit("operator_response", intervention_id=request_id, action=response.action,
                               ownership_generation=response.ownership_generation)
            if response.action != "resume":
                status = "cancelled" if response.action == "cancel" else "unresolved"
                raise SurfaceError("INTERVENTION_CANCELLED" if response.action == "cancel" else "INTERVENTION_UNRESOLVED")
            await self._safe_navigation(original)
            # Drain metadata while HUMAN is still the authoritative owner.
            await asyncio.wait_for(self.surface.flush_activity(), 2)
            resuming = await control.begin_resume(request_id, human_generation)
            request = self._status(request, "resuming", resuming)
            status = "rejected"
            async with control.lock:
                control.require_intervention(request_id, resuming, Ownership.RESUMING)
                before_nav = await self._safe_navigation(original)
                before = await self.surface.observe()
                if not await self._all([application_checkpoint], bindings):
                    raise SurfaceError("RESUME_APPLICATION_MISMATCH")
                if not await self._all(identities, bindings):
                    raise SurfaceError("RESUME_IDENTITY_MISMATCH")
                if await self.surface.evaluate(self.rule.blocker, bindings):
                    raise SurfaceError("RESUME_BLOCKER_UNRESOLVED")
                if not await self._all(self.rule.resume_checkpoint, bindings) or not await self._all(conditions, bindings):
                    raise SurfaceError("RESUME_CHECKPOINT_FAILED")
                if action_state == "uncertain" and baseline is not False:
                    raise SurfaceError("RESUME_COMPLETION_UNCERTAIN", completion_uncertain=True)
                after = await self.surface.observe()
                after_nav = await self._safe_navigation(original)
                if (before_nav != after_nav or before.navigation_revision != after.navigation_revision
                        or before.screenshot_sha256 != after.screenshot_sha256
                        or before.controls != after.controls or before.readables != after.readables):
                    raise SurfaceError("RESUME_STATE_CHANGED")
                self.evidence.screenshot(after.screenshot_png)
                self.evidence.emit("resume_verified", intervention_id=request_id, step_id=step_id,
                    session_id=after_nav.session_id, page_id=after_nav.page_id,
                    same_session=True, application_verified=True, member_identity_verified=True,
                    checkpoint_verified=True, policy_signals_clear=True, action_state=action_state)
                resumed_generation = control.approve_resume(request_id, resuming)
            disposition = "pending" if action_state == "pending" else "completed"
            request = self._status(request, "completed", resumed_generation)
            self.evidence.emit("intervention_completed", intervention_id=request_id, step_id=step_id,
                               disposition=disposition, ownership_generation=resumed_generation)
            return ResumeDecision(intervention_id=request_id, ownership_generation=resumed_generation, disposition=disposition)
        except (SurfaceError, PolicyViolation, OwnershipError, RuntimeFault):
            if request:
                self._status(request, status)
            await control.reject_intervention(request_id)
            raise
        except asyncio.CancelledError:
            if request:
                self._status(request, "cancelled")
            await asyncio.shield(control.reject_intervention(request_id))
            raise SurfaceError("INTERVENTION_CANCELLED") from None
        except Exception as exc:
            if request:
                self._status(request, "failed")
            self.evidence.emit("intervention_error", error_type=type(exc).__name__)
            await control.reject_intervention(request_id)
            raise SurfaceError("INTERVENTION_ERROR") from None


def banking_intervention_rule():
    from .banking_runtime import visible
    return InterventionRule(code="OPERATOR_CHALLENGE", blocker=visible("Operator acknowledgement required"),
                            resume_checkpoint=[visible("Member record")])
