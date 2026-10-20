"""The shared, policy-checked execution boundary for discovery and replay."""

from .contracts import Action, Bindings, Extract, Fill, Navigate, StepResult, Surface, SurfaceError
from .handoff import OwnershipError
from .policy import PolicyViolation
from .parsing import parse_value


class GuardedExecutor:
    def __init__(self, surface: Surface, policy, control, evidence):
        self.surface, self.policy = surface, policy
        self.control, self.evidence = control, evidence

    async def execute(self, action: Action, bindings: Bindings | None = None, *, read_recovery: bool = False,
                      expected_generation: int | None = None) -> StepResult:
        bindings = bindings or Bindings()
        completed = False
        data = None
        # Capture before waiting: a queued action cannot acquire a new owner epoch.
        generation = self.control.generation if expected_generation is None else expected_generation
        async with self.control.lock:
            try:
                self.control.require_automation(generation)
                self.policy.check_action(action.kind)
                if isinstance(action, Navigate):
                    self.policy.check_url(action.url)
                    if read_recovery:
                        self.policy.check_read_recovery(action.kind)
                    self.control.require_automation(generation)
                    await self.surface.perform(action)
                else:
                    resolved = await self.surface.resolve(action.target, bindings)
                    facts = await self.surface.inspect(resolved)
                    self.policy.check_action(action.kind, facts)
                    if read_recovery:
                        self.policy.check_read_recovery(action.kind, facts)
                    value = None
                    if isinstance(action, Fill):
                        value = bindings.resolve(action.value)
                        if not isinstance(value, str):
                            raise ValueError("FILL_REQUIRES_STRING")
                    self.control.require_automation(generation)
                    raw = await self.surface.perform(action, resolved, value, facts)
                    if isinstance(action, Extract):
                        try:
                            data = parse_value(action.parser, raw)
                        except (ValueError, TypeError, AttributeError):
                            raise SurfaceError("OUTPUT_PARSE_ERROR") from None
                completed = True
                navigation = await self.surface.navigation_state()
                if navigation.blocked_request_code:
                    raise PolicyViolation(navigation.blocked_request_code)
                if navigation.native_dialog_type:
                    raise SurfaceError("UNEXPECTED_NATIVE_DIALOG", completion_uncertain=completed)
                for url in [navigation.url, *navigation.frame_urls]:
                    self.policy.check_url(url)
                ref = self.evidence.emit("action_executed", action_kind=action.kind, owner=self.control.state.value)
                return StepResult(status="executed", action_kind=action.kind, code="EXECUTED", evidence_ref=ref, data=data)
            except (PolicyViolation, OwnershipError, ValueError) as exc:
                # Only our fixed error codes are recorded, never exception payloads.
                code = str(exc) if isinstance(exc, (PolicyViolation, OwnershipError)) else "INVALID_BINDING"
                ref = self.evidence.emit("action_blocked", action_kind=action.kind, code=code)
                return StepResult(status="blocked", action_kind=action.kind, code=code, evidence_ref=ref, completion_uncertain=completed)
            except SurfaceError as exc:
                status = "failed" if exc.code in {"BROWSER_ERROR", "SESSION_CLOSED"} else "blocked"
                uncertain = completed or exc.completion_uncertain
                ref = self.evidence.emit("action_" + status, action_kind=action.kind, code=exc.code, completion_uncertain=uncertain)
                return StepResult(status=status, action_kind=action.kind, code=exc.code, evidence_ref=ref, completion_uncertain=uncertain)
