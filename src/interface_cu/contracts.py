"""Small declarative contracts shared by discovery, replay and guarded execution."""

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def identifier() -> str:
    return uuid4().hex


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


Scalar = str | int | bool | None


class LiteralValue(Contract):
    kind: Literal["literal"] = "literal"
    value: Scalar


class InputRef(Contract):
    kind: Literal["input"] = "input"
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


class ExtractedRef(Contract):
    kind: Literal["extracted"] = "extracted"
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


Binding = Annotated[LiteralValue | InputRef | ExtractedRef, Field(discriminator="kind")]


class Bindings(Contract):
    inputs: dict[str, Scalar] = Field(default_factory=dict)
    extracted: dict[str, Scalar] = Field(default_factory=dict)

    def resolve(self, binding: Binding) -> Scalar:
        if isinstance(binding, LiteralValue):
            return binding.value
        source = self.inputs if isinstance(binding, InputRef) else self.extracted
        if binding.name not in source:
            raise ValueError("Unbound reference")
        return source[binding.name]


class RoleLocator(Contract):
    kind: Literal["role"] = "role"
    role: Literal["button", "link", "textbox", "cell", "heading"]
    name: Binding


class LabelLocator(Contract):
    kind: Literal["label"] = "label"
    label: Binding


class TextLocator(Contract):
    kind: Literal["text"] = "text"
    text: Binding


class RowLocator(Contract):
    kind: Literal["row"] = "row"
    row_text: Binding
    role: Literal["link", "button", "cell"]
    name: Binding


class StructuralLocator(Contract):
    """A deliberately limited structural vocabulary, never arbitrary selectors."""

    kind: Literal["structural"] = "structural"
    tag: Literal["input", "button", "a", "td"]
    name: str | None = Field(default=None, pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
    adjacent_label: Binding | None = None


LocatorSpec = Annotated[
    RoleLocator | LabelLocator | TextLocator | RowLocator | StructuralLocator,
    Field(discriminator="kind"),
]


class TargetSpec(Contract):
    frames: list[str] = Field(default_factory=list, max_length=4)
    candidates: list[LocatorSpec] = Field(min_length=1, max_length=4)
    expected_tag: Literal["input", "button", "a", "td"] | None = None
    expected_text: Binding | None = None
    rationale: str = Field(min_length=1, max_length=500)


class Navigate(Contract):
    kind: Literal["navigate"] = "navigate"
    url: str


class Click(Contract):
    kind: Literal["click"] = "click"
    target: TargetSpec


class Fill(Contract):
    kind: Literal["fill"] = "fill"
    target: TargetSpec
    value: Binding


class Press(Contract):
    kind: Literal["press"] = "press"
    target: TargetSpec
    key: Literal["Enter", "Tab", "Escape"]


class Extract(Contract):
    kind: Literal["extract"] = "extract"
    target: TargetSpec
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    parser: Literal["text", "usd_minor_units", "usd_currency"]


Action = Annotated[Navigate | Click | Fill | Press | Extract, Field(discriminator="kind")]


class Visible(Contract):
    kind: Literal["visible"] = "visible"
    target: TargetSpec


class TextEquals(Contract):
    kind: Literal["text_equals"] = "text_equals"
    target: TargetSpec
    value: Binding


class ValueEquals(Contract):
    kind: Literal["value_equals"] = "value_equals"
    target: TargetSpec
    value: Binding


Predicate = Annotated[Visible | TextEquals | ValueEquals, Field(discriminator="kind")]


class Ownership(str, Enum):
    AUTOMATION = "automation"
    PAUSED = "paused"
    HUMAN = "human"
    RESUMING = "resuming"
    COMPLETED = "completed"
    FAILED = "failed"


class Rect(Contract):
    x: float
    y: float
    width: float
    height: float


class VisibleControl(Contract):
    control_id: str
    frames: list[str]
    tag: str
    text: str
    role: str
    label: str
    input_type: str
    rect: Rect


class Observation(Contract):
    id: str = Field(default_factory=identifier)
    session_id: str
    page_id: str
    navigation_revision: int = 0
    observed_at: datetime = Field(default_factory=utc_now)
    url: str
    width: int
    height: int
    screenshot_sha256: str
    screenshot_png: bytes = Field(exclude=True, repr=False)
    controls: list[VisibleControl]
    readables: list[VisibleControl] = Field(default_factory=list)
    headings: list[VisibleControl] = Field(default_factory=list)


class StepResult(Contract):
    status: Literal["executed", "blocked", "failed"]
    action_kind: str
    code: str
    evidence_ref: str | None = None
    # A timeout after a click does not prove the action had no effect.
    completion_uncertain: bool = False
    data: Scalar = Field(default=None, repr=False)


class Success(Contract):
    status: Literal["success"] = "success"
    outputs: dict[str, Scalar]


class BusinessOutcome(Contract):
    status: Literal["business_outcome"] = "business_outcome"
    code: str
    payload: dict[str, Scalar] = Field(default_factory=dict)


class RecoveryAttempt(Contract):
    condition_code: str
    strategy: Literal["wait", "dismiss", "retry_read", "checkpoint_reinspection"]
    attempt: int = Field(ge=1)
    result: Literal["cleared", "still_present", "blocked", "failed", "verified", "not_proven"]
    evidence_ref: str
    completion_uncertain: bool = False


class Failure(Contract):
    status: Literal["failure"] = "failure"
    code: str
    step_id: str | None = None
    expected: str
    observed: str
    evidence_ref: str | None = None
    completion_uncertain: bool = False
    capability_id: str | None = None
    capability_version: str | None = None
    category: Literal["validation", "targeting", "policy", "runtime", "application", "session", "ownership", "output", "internal"] = "runtime"
    recovery_attempts: list[RecoveryAttempt] = Field(default_factory=list)
    human_intervention_appropriate: bool = False


TerminalResult = Annotated[Success | BusinessOutcome | Failure, Field(discriminator="status")]


class Intervention(Contract):
    id: str = Field(default_factory=identifier)
    state: Literal["intervention_required"] = "intervention_required"
    session_id: str
    page_id: str
    reason: str
    ownership: Ownership


class ResumeConditions(Contract):
    application_checkpoint: Predicate
    member_identity: list[Predicate] = Field(min_length=1, max_length=4)
    workflow_checkpoint: list[Predicate] = Field(min_length=1, max_length=12)
    action_state: Literal["pending", "executed", "uncertain"]
    baseline_satisfied: bool | None = None
    same_session_required: Literal[True] = True
    policy_signals_must_be_clear: Literal[True] = True


class InterventionRequest(Contract):
    id: str = Field(default_factory=identifier)
    run_id: str
    mode: Literal["replay", "discovery"]
    capability_id: str
    capability_version: str | None = None
    goal: str | None = None
    step_id: str
    reason: str
    session_id: str
    page_id: str
    observed: str
    evidence_ref: str
    expected: str
    completion_uncertain: bool
    allowed_resume: ResumeConditions
    status: Literal["requested", "human", "resuming", "completed", "cancelled", "unresolved", "timed_out", "rejected", "failed"] = "requested"
    ownership_generation: int


class OperatorResponse(Contract):
    intervention_id: str
    ownership_generation: int
    action: Literal["resume", "cancel", "unresolved"]


class ResumeDecision(Contract):
    intervention_id: str
    ownership_generation: int
    disposition: Literal["pending", "completed"]


class ResolvedTarget:
    """Opaque, transient adapter-owned handle. Never serialize into an artifact."""


class ActionFacts(Contract):
    """Observed control effects for policy; never supplied by the model."""

    tag: str
    type: str = ""
    disabled: bool = False
    href: str | None = None
    target: str = ""
    form_action: str | None = None
    form_method: str | None = None
    operator_only: bool = False


class NavigationState(Contract):
    """In-memory state for policy. URLs are sanitized separately for evidence."""

    session_id: str
    page_id: str
    url: str
    frame_urls: list[str] = Field(default_factory=list)
    # Latched safety signals: a later successful DOM operation cannot erase them.
    blocked_request_code: str | None = None
    native_dialog_type: str | None = None


class SurfaceError(Exception):
    def __init__(self, code: str, *, completion_uncertain: bool = False):
        super().__init__(code)
        self.code = code
        self.completion_uncertain = completion_uncertain


class ResolutionError(SurfaceError):
    pass


class Surface(Protocol):
    """Policy inspects facts; only the adapter knows how a handle operates."""

    async def resolve(self, target: TargetSpec, bindings: Bindings) -> ResolvedTarget: ...

    async def inspect(self, resolved: ResolvedTarget) -> ActionFacts: ...

    async def perform(self, action: Action, resolved: ResolvedTarget | None = None,
                      value: str | None = None, facts: ActionFacts | None = None) -> Scalar: ...

    async def navigation_state(self) -> NavigationState: ...

    async def observe(self) -> Observation: ...

    async def evaluate(self, predicate: Predicate, bindings: Bindings) -> bool: ...

    async def flush_activity(self) -> None: ...
