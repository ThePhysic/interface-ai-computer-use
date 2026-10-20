import pytest
from pydantic import TypeAdapter, ValidationError

from interface_cu.contracts import (
    Action, Bindings, BusinessOutcome, ExtractedRef, Failure, Fill, InputRef,
    LiteralValue, Ownership, RoleLocator, Success, TargetSpec, TerminalResult,
)
from interface_cu.handoff import OwnershipError, SessionControl
from interface_cu.evidence import Evidence


def test_bindings_keep_provenance_and_leading_zero():
    bindings = Bindings(inputs={"member_id": "06789"}, extracted={"account_id": "A2"})
    assert bindings.resolve(InputRef(name="member_id")) == "06789"
    assert bindings.resolve(ExtractedRef(name="account_id")) == "A2"
    assert bindings.resolve(LiteralValue(value="Savings")) == "Savings"
    with pytest.raises(ValueError, match="Unbound"):
        bindings.resolve(ExtractedRef(name="member_id"))


def test_action_round_trip_and_reject_code():
    target = TargetSpec(candidates=[RoleLocator(role="textbox", name=LiteralValue(value="Member ID"))], rationale="Visible label")
    action = Fill(target=target, value=InputRef(name="member_id"))
    assert TypeAdapter(Action).validate_json(action.model_dump_json()) == action
    for payload in ({"kind": "evaluate", "code": "danger()"}, {**action.model_dump(), "script": "danger()"}):
        with pytest.raises(ValidationError):
            TypeAdapter(Action).validate_python(payload)


def test_three_terminal_results_exclude_pause():
    adapter = TypeAdapter(TerminalResult)
    for value in (Success(outputs={"balance_minor_units": 123456}), BusinessOutcome(code="MEMBER_NOT_FOUND"),
                  Failure(code="TARGET_MISSING", expected="Unique member", observed="No control")):
        assert adapter.validate_json(value.model_dump_json()) == value
    with pytest.raises(ValidationError):
        adapter.validate_python({"status": "intervention_required"})


async def test_ownership_generation_and_no_unvalidated_resume(tmp_path):
    control = SessionControl(Evidence(tmp_path, "automated_test"))
    generation = control.generation
    await control.give_to_human()
    assert control.state == Ownership.HUMAN
    with pytest.raises(OwnershipError, match="AUTOMATION_DOES_NOT_OWN_SESSION"):
        control.require_automation(generation)
    await control.finish_probe()
    assert control.state == Ownership.COMPLETED
