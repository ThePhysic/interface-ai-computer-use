"""Small, opt-in runtime profile and bounded UI recovery; no model dependency."""

import asyncio
import json
from typing import Literal

from pydantic import Field, model_validator

from .capability import OutcomeRule, references
from .contracts import (
    Click, Contract, ExtractedRef, InputRef, LiteralValue, Predicate, RecoveryAttempt,
)


class StopRule(Contract):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    condition: Predicate
    category: Literal["application", "session", "runtime"]
    human_intervention_appropriate: bool = True


class RecoveryRule(Contract):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    condition: Predicate
    strategy: Literal["wait", "dismiss", "retry_read"]
    action: Click | None = None
    max_attempts: int = Field(default=2, ge=1, le=3)
    interval_seconds: float = Field(default=0.3, ge=0.05, le=1)
    timeout_seconds: float = Field(default=3.0, ge=0.1, le=5)

    @model_validator(mode="after")
    def supported_recovery(self):
        if (self.strategy == "wait") != (self.action is None):
            raise ValueError("Only wait recovery omits an action")
        if self.strategy == "wait" and self.interval_seconds >= self.timeout_seconds:
            raise ValueError("Wait interval must fit within its attempt timeout")
        return self


class RuntimeProfile(Contract):
    schema_version: Literal["1.0"]
    capability_id: str
    capability_version: str
    application_family: str
    application_version: str
    business_outcomes: list[OutcomeRule] = Field(default_factory=list, max_length=8)
    stop_rules: list[StopRule] = Field(default_factory=list, max_length=8)
    recovery_rules: list[RecoveryRule] = Field(default_factory=list, max_length=6)
    max_total_attempts: int = Field(default=6, ge=1, le=8)
    uncertain_checks: int = Field(default=2, ge=1, le=3)
    uncertain_interval_seconds: float = Field(default=0.15, ge=0.05, le=1)

    @model_validator(mode="after")
    def unique_codes(self):
        codes = [r.code for r in [*self.business_outcomes, *self.stop_rules, *self.recovery_rules]]
        if len(codes) != len(set(codes)):
            raise ValueError("Duplicate runtime condition code")
        return self

    def validate_for(self, capability):
        expected = (capability.capability_id, capability.capability_version,
                    capability.application.family, capability.application.version)
        actual = (self.capability_id, self.capability_version,
                  self.application_family, self.application_version)
        if actual != expected:
            raise ValueError("RUNTIME_PROFILE_MISMATCH")
        # Runtime conditions may be inspected before the first extraction.
        for ref in references(self):
            if isinstance(ref, ExtractedRef) or isinstance(ref, InputRef) and ref.name not in capability.inputs:
                raise ValueError("INVALID_RUNTIME_REFERENCE")


class RuntimeFault(Exception):
    def __init__(self, code, *, category="runtime", human=True, uncertain=False):
        self.code, self.category, self.human, self.uncertain = code, category, human, uncertain
        super().__init__(code)


def category_for(code):
    if code in {"ORIGIN_DENIED", "METHOD_OR_CREDENTIALS_DENIED", "PATH_DENIED", "ROUTE_DENIED",
                "ACTION_DENIED", "DISABLED_CONTROL", "NEW_WINDOW_DENIED", "FIELD_DENIED",
                "UNKNOWN_CONTROL", "UNKNOWN_FORM_METHOD", "UNKNOWN_EFFECT", "READ_TARGET_DENIED",
                "RECOVERY_REQUIRES_READ_LINK", "INVALID_NAVIGATION_URL", "UNEXPECTED_REDIRECT",
                "HUMAN_ONLY_CONTROL", "HUMAN_ACK_REQUIRES_OPERATOR"}:
        return "policy"
    if code in {"AUTOMATION_DOES_NOT_OWN_SESSION", "STALE_INTERVENTION", "INTERVENTION_ALREADY_ACTIVE"}:
        return "ownership"
    if code in {"SESSION_CLOSED", "SESSION_IDENTITY_CHANGED"}:
        return "session"
    if code.startswith("OUTPUT_"):
        return "output"
    if code == "UNBOUND_REFERENCE":
        return "validation"
    if code in {"INVALID_INPUT", "INVALID_BINDING", "INVALID_ARTIFACT", "INVALID_RUNTIME_PROFILE", "RUNTIME_PROFILE_MISMATCH"}:
        return "validation"
    if code in {"PRECONDITION_FAILED", "POSTCONDITION_FAILED", "SUCCESS_CONDITION_FAILED", "IDENTITY_MISMATCH",
                "AMBIGUOUS_TARGET", "CONFLICTING_TARGETS", "TARGET_NOT_FOUND", "TARGET_NOT_ENABLED",
                "FRAME_NOT_UNIQUE", "FRAME_NOT_VISIBLE", "APPLICATION_MISMATCH"}:
        return "targeting"
    return "runtime"


def describe_conditions(conditions):
    descriptions = []
    for predicate in conditions:
        candidate = predicate.target.candidates[0]
        label = getattr(candidate, "name", None) or getattr(candidate, "adjacent_label", None) or getattr(candidate, "text", None)
        label = label.value if isinstance(label, LiteralValue) else label if isinstance(label, str) else "declared target"
        value = getattr(predicate, "value", None)
        if value is not None:
            expected = value.value if isinstance(value, LiteralValue) else f"{value.kind}.{value.name}"
            descriptions.append(f"{predicate.kind}: {label} equals {expected}")
        else:
            descriptions.append(f"visible: {label}")
    return "; ".join(descriptions)[:1200] or "Verified capability execution"


async def diagnostic_state(surface, evidence, *, step_id, checkpoint, code, cause=None):
    """Bounded, masked UI diagnostics, never raw DOM, cookies or input values."""
    state = {"step_id": step_id, "checkpoint": checkpoint[:300], "condition_code": code}
    if cause:
        state["detail_code"] = cause
    try:
        nav = await asyncio.wait_for(surface.navigation_state(), 0.5)
        state.update(session_id=nav.session_id, page_id=nav.page_id, url=nav.url,
                     native_dialog_type=nav.native_dialog_type, blocked_request_code=nav.blocked_request_code)
    except Exception as exc:
        state["navigation_unavailable"] = type(exc).__name__
    try:
        observation = await asyncio.wait_for(surface.observe(), 1.5)
        state.update(headings=[h.text[:80] for h in observation.headings][:6],
                     readable_captions=[c.label[:60] for c in observation.readables][:6],
                     screenshot_ref=evidence.screenshot(observation.screenshot_png))
    except Exception as exc:
        state["screenshot_unavailable"] = type(exc).__name__
    sanitized = evidence.sanitize(state)
    evidence.emit("runtime_diagnostic", **sanitized)
    encoded = json.dumps(sanitized, ensure_ascii=False)
    if len(encoded) > 1400:
        sanitized.pop("readable_captions", None)
        sanitized["headings"] = sanitized.get("headings", [])[:3]
        encoded = json.dumps(sanitized, ensure_ascii=False)
    return encoded


async def safety_signals(surface):
    state = await surface.navigation_state()
    if state.blocked_request_code:
        category = category_for(state.blocked_request_code)
        raise RuntimeFault(state.blocked_request_code, category=category, human=category != "policy", uncertain=True)
    if state.native_dialog_type:
        raise RuntimeFault("UNEXPECTED_NATIVE_DIALOG", human=True, uncertain=True)


class RuntimeMonitor:
    def __init__(self, surface, executor, evidence, profile, generation):
        self.surface, self.executor, self.evidence = surface, executor, evidence
        self.profile, self.generation = profile, generation
        self.history: list[RecoveryAttempt] = []
        self.counts: dict[str, int] = {}
        self.in_action = False
        self.pending_attempt = None

    def record(self, code, strategy, attempt, result, *, uncertain=False):
        ref = self.evidence.emit("recovery_attempt", condition_code=code, strategy=strategy,
                                 attempt=attempt, result=result, completion_uncertain=uncertain)
        self.history.append(RecoveryAttempt(condition_code=code, strategy=strategy, attempt=attempt,
                                             result=result, evidence_ref=ref, completion_uncertain=uncertain))
        self.pending_attempt = None

    async def check(self, bindings, step_id):
        """Return a business outcome or clear every recognized recoverable state.

        No original workflow action is repeated here. Only an inspected allowed
        GET link declared by this exact application profile can execute recovery.
        """
        from .contracts import BusinessOutcome

        while True:
            self.executor.control.require_automation(self.generation)
            await safety_signals(self.surface)
            if self.profile is None:
                return None
            groups = [*self.profile.stop_rules, *self.profile.business_outcomes, *self.profile.recovery_rules]
            matches = [rule for rule in groups if await self.surface.evaluate(rule.condition, bindings)]
            if len(matches) > 1:
                raise RuntimeFault("RUNTIME_STATE_AMBIGUOUS")
            if not matches:
                return None
            rule = matches[0]
            if isinstance(rule, StopRule):
                raise RuntimeFault(rule.code, category=rule.category, human=rule.human_intervention_appropriate)
            if isinstance(rule, OutcomeRule):
                await diagnostic_state(self.surface, self.evidence, step_id=step_id,
                                       checkpoint=describe_conditions([rule.condition]), code=rule.code)
                self.evidence.emit("business_outcome", code=rule.code, step_id=step_id)
                return BusinessOutcome(code=rule.code)
            if self.counts.get(rule.code, 0) >= rule.max_attempts or len(self.history) >= self.profile.max_total_attempts:
                raise RuntimeFault("RECOVERY_EXHAUSTED")
            attempt = self.counts.get(rule.code, 0) + 1
            self.counts[rule.code] = attempt
            self.pending_attempt = (rule.code, rule.strategy, attempt)
            await diagnostic_state(self.surface, self.evidence, step_id=step_id,
                                   checkpoint=describe_conditions([rule.condition]), code=rule.code)
            self.evidence.emit("recovery_started", step_id=step_id, condition_code=rule.code,
                               strategy=rule.strategy, attempt=attempt, max_attempts=rule.max_attempts,
                               timeout_seconds=rule.timeout_seconds)
            try:
                if rule.strategy == "wait":
                    await asyncio.wait_for(asyncio.sleep(rule.interval_seconds), rule.timeout_seconds)
                    self.executor.control.require_automation(self.generation)
                else:
                    self.in_action = True
                    result = await asyncio.wait_for(self.executor.execute(rule.action, bindings, read_recovery=True,
                                                                         expected_generation=self.generation), rule.timeout_seconds)
                    self.in_action = False
                    if result.status != "executed":
                        self.record(rule.code, rule.strategy, attempt, result.status,
                                    uncertain=result.completion_uncertain)
                        await safety_signals(self.surface)
                        raise RuntimeFault(result.code, category=category_for(result.code),
                                           human=category_for(result.code) != "policy", uncertain=result.completion_uncertain)
            except TimeoutError:
                self.record(rule.code, rule.strategy, attempt, "failed", uncertain=self.in_action)
                raise RuntimeFault("RECOVERY_TIMEOUT", uncertain=self.in_action) from None
            remaining = await self.surface.evaluate(rule.condition, bindings)
            self.record(rule.code, rule.strategy, attempt, "still_present" if remaining else "cleared")
            await diagnostic_state(self.surface, self.evidence, step_id=step_id,
                                   checkpoint="Reinspect state after recovery", code=rule.code)

    async def reconcile(self, step, bindings, baseline, result):
        """Reinspect only. An already-true checkpoint cannot prove a new commit."""
        if self.profile is None:
            return False
        from .contracts import Extract
        for attempt in range(1, self.profile.uncertain_checks + 1):
            if len(self.history) >= self.profile.max_total_attempts:
                return False
            self.executor.control.require_automation(self.generation)
            await asyncio.sleep(self.profile.uncertain_interval_seconds)
            await safety_signals(self.surface)
            verified = (not isinstance(step.action, Extract) and bool(step.postconditions) and baseline is False
                        and all([await self.surface.evaluate(c, bindings) for c in step.postconditions]))
            self.record(result.code, "checkpoint_reinspection", attempt, "verified" if verified else "not_proven", uncertain=True)
            await diagnostic_state(self.surface, self.evidence, step_id=step.id,
                                   checkpoint=describe_conditions(step.postconditions), code=result.code)
            if verified:
                return True
        return False
