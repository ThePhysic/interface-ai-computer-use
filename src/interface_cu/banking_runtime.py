"""Reviewed UI-state rules for the fictional app; not a workflow or model prompt."""

from .capability import OutcomeRule
from .contracts import Click, LiteralValue, RoleLocator, TargetSpec, TextLocator, Visible
from .runtime import RecoveryRule, RuntimeProfile, StopRule


def visible(text, *, heading=True):
    candidate = RoleLocator(role="heading", name=LiteralValue(value=text)) if heading else TextLocator(text=LiteralValue(value=text))
    return Visible(target=TargetSpec(frames=["workspace"], candidates=[candidate], rationale="Reviewed visible runtime message"))


def link(text):
    return Click(target=TargetSpec(frames=["workspace"], candidates=[RoleLocator(role="link", name=LiteralValue(value=text))],
                                  expected_tag="a", expected_text=LiteralValue(value=text), rationale="Known harmless read-only recovery link"))


def savings_runtime_profile():
    return RuntimeProfile(schema_version="1.0", capability_id="banking.savings_balance", capability_version="1.0.0",
        application_family="fictional-credit-union", application_version="1.0",
        business_outcomes=[
            OutcomeRule(code="VALIDATION_REJECTED", condition=visible("Member lookup rejected by validation rules.", heading=False)),
            OutcomeRule(code="VALIDATION_REJECTED_FORMAT", condition=visible("Enter a five-digit fictional member ID.", heading=False)),
            OutcomeRule(code="MEMBER_NOT_FOUND", condition=visible("Member not found")),
            OutcomeRule(code="ACCOUNT_NOT_FOUND", condition=visible("Savings account not found")),
            OutcomeRule(code="PERMISSION_DENIED", condition=visible("Access denied for member records")),
        ],
        stop_rules=[
            StopRule(code="SESSION_EXPIRED", condition=visible("Session expired"), category="session"),
            StopRule(code="UNEXPECTED_INTERSTITIAL", condition=visible("Confirm operation"), category="runtime"),
            StopRule(code="APPLICATION_ERROR_PERSISTENT", condition=visible("Application error"), category="application"),
        ],
        recovery_rules=[
            RecoveryRule(code="SLOW_LOADING", condition=visible("Loading savings"), strategy="wait", max_attempts=3, interval_seconds=0.25),
            RecoveryRule(code="HARMLESS_INTERSTITIAL", condition=visible("Read-only notice"), strategy="dismiss", action=link("Continue to savings"), max_attempts=1),
            RecoveryRule(code="TRANSIENT_APPLICATION_ERROR", condition=visible("Temporary service failure"), strategy="retry_read", action=link("Retry read"), max_attempts=2),
        ])
