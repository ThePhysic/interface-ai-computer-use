import pytest
from pydantic import ValidationError

from interface_cu.capability import Capability, FieldDefinition, Provenance, Step, validate_inputs
from interface_cu.contracts import (
    Extract, ExtractedRef, Fill, InputRef, LiteralValue, Navigate, StructuralLocator, TargetSpec,
)
from interface_cu.parsing import parse_value
from interface_cu.task import savings_task


def example_capability():
    task = savings_task()
    cell = TargetSpec(frames=["workspace"], candidates=[StructuralLocator(tag="td", adjacent_label=LiteralValue(value="Member ID"))],
                      rationale="Caption-relative cell")
    return Capability(schema_version="1.0", capability_id=task.capability_id, capability_version="1.0.0", description=task.description,
        application=task.application, inputs=task.inputs, outputs={"member_id":task.outputs["member_id"]},
        steps=[Step(id="s001", action=Navigate(url="/")), Step(id="s002", action=Extract(target=cell, name="member_id", parser="text"))],
        success_conditions=task.success_conditions, provenance=Provenance(run_id="test", evidence_file="test.jsonl",
            source="automated_test", model="scripted", response_ids=["scripted-1"]))


def test_strict_schema_round_trip():
    artifact = example_capability()
    assert Capability.model_validate_json(artifact.model_dump_json()) == artifact


@pytest.mark.parametrize("mutation", ["version", "missing_version", "code", "unknown_input", "forward_extraction", "duplicate_step", "parser", "absolute_route", "ephemeral_id"])
def test_malformed_artifacts_rejected(mutation):
    data = example_capability().model_dump(mode="json")
    step = data["steps"][1]
    if mutation == "version": data["schema_version"] = "2.0"
    if mutation == "missing_version": del data["schema_version"]
    if mutation == "code": data["script"] = "print('unsafe')"
    if mutation == "unknown_input": data["outputs"]["member_id"]["equals"]["name"] = "unknown"
    if mutation == "forward_extraction": data["steps"][0]["preconditions"] = [{"kind":"text_equals", "target":step["action"]["target"], "value":{"kind":"extracted", "name":"member_id"}}]
    if mutation == "duplicate_step": step["id"] = "s001"
    if mutation == "parser": step["action"]["parser"] = "eval"
    if mutation == "absolute_route": data["steps"][0]["action"]["url"] = "https://example.com/"
    if mutation == "ephemeral_id": step["action"]["target"]["control_id"] = "c3"
    with pytest.raises(ValidationError): Capability.model_validate(data)


def test_extracted_binding_can_feed_later_action():
    data = example_capability().model_dump(mode="json")
    data["steps"].append({"id":"s003", "action":{"kind":"fill", "target":{"frames":["workspace"],
        "candidates":[{"kind":"structural","tag":"input","name":"member_id"}], "rationale":"Named field"},
        "value":{"kind":"extracted","name":"member_id"}}})
    artifact = Capability.model_validate(data)
    assert isinstance(artifact.steps[-1].action.value, ExtractedRef)


def test_input_types_are_strict_and_leading_zero_survives():
    definitions = savings_task().inputs
    assert validate_inputs(definitions, {"member_id":"06789"})["member_id"] == "06789"
    for values in ({"member_id":6789}, {"member_id":"6789"}, {"member_id":"１２３４５"}, {"member_id":"12345","extra":1}):
        with pytest.raises(ValueError): validate_inputs(definitions, values)
    with pytest.raises(ValueError): FieldDefinition(type="integer", description="Cents").validate_value(True)


@pytest.mark.parametrize("text,expected", [("USD 1,234.56",123456), ("USD 9876.54",987654), ("USD 0.01",1)])
def test_exact_money_parser(text, expected):
    assert parse_value("usd_minor_units", text) == expected
    assert parse_value("usd_currency", text) == "USD"
    assert parse_value("text", "06789") == "06789"


@pytest.mark.parametrize("text", ["USD 1,23.45", "USD 1.2", "$1.23", "EUR 1.23", "USD NaN", "USD -1.23"])
def test_money_parser_rejects_ambiguous_or_unsupported_formats(text):
    with pytest.raises(ValueError): parse_value("usd_minor_units", text)
