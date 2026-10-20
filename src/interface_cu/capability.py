"""The deliberately small, versioned, non-executable capability contract."""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .contracts import (
    Action, Binding, Contract, Extract, ExtractedRef, InputRef, Navigate, Predicate, Scalar,
)
from .parsing import PARSER_TYPES


class FieldDefinition(Contract):
    type: Literal["string", "integer", "boolean"]
    format: Literal["plain", "member_id", "currency"] = "plain"
    description: str = Field(min_length=1, max_length=400)
    enum: list[Scalar] = Field(default_factory=list)

    def validate_value(self, value: Scalar) -> Scalar:
        expected = {"string": str, "integer": int, "boolean": bool}[self.type]
        if type(value) is not expected:
            raise ValueError("VALUE_TYPE_MISMATCH")
        if self.format == "member_id" and (not isinstance(value, str) or not re.fullmatch(r"[0-9]{5}", value)):
            raise ValueError("INVALID_MEMBER_ID")
        if self.format == "currency" and (not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value)):
            raise ValueError("INVALID_CURRENCY")
        if self.enum and value not in self.enum:
            raise ValueError("VALUE_NOT_IN_ENUM")
        return value

    @model_validator(mode="after")
    def valid_definition(self):
        if self.format != "plain" and self.type != "string":
            raise ValueError("String format requires string type")
        for item in self.enum:
            self.validate_value(item)
        return self


class OutputDefinition(FieldDefinition):
    binding: Binding
    equals: Binding | None = None


class Application(Contract):
    family: str = Field(min_length=1)
    version: str = Field(min_length=1)
    checkpoint: Predicate


class OutcomeRule(Contract):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    condition: Predicate


class Step(Contract):
    id: str = Field(pattern=r"^s[0-9]{3}$")
    action: Action
    preconditions: list[Predicate] = Field(default_factory=list, max_length=12)
    postconditions: list[Predicate] = Field(default_factory=list, max_length=12)
    timeout_seconds: float = Field(default=5.0, gt=0, le=30)


class Provenance(Contract):
    run_id: str
    evidence_file: str
    source: Literal["live_llm", "automated_test"]
    model: str
    response_ids: list[str] = Field(min_length=1)
    human_assisted: Literal[False] = False


def references(value):
    """Walk typed bindings only, never interpret strings as expressions."""
    if isinstance(value, (InputRef, ExtractedRef)):
        yield value
    elif isinstance(value, Contract):
        for name in type(value).model_fields:
            yield from references(getattr(value, name))
    elif isinstance(value, dict):
        for item in value.values():
            yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def validate_inputs(definitions: dict[str, FieldDefinition], values: dict[str, Scalar]) -> dict[str, Scalar]:
    if not isinstance(values, dict) or values.keys() != definitions.keys():
        raise ValueError("INPUT_NAMES_MISMATCH")
    return {name: definition.validate_value(values[name]) for name, definition in definitions.items()}


class Capability(Contract):
    schema_version: Literal["1.0"]
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    capability_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    description: str = Field(min_length=1, max_length=600)
    application: Application
    inputs: dict[str, FieldDefinition]
    outputs: dict[str, OutputDefinition]
    steps: list[Step] = Field(min_length=1, max_length=40)
    success_conditions: list[Predicate] = Field(min_length=1, max_length=12)
    business_outcomes: list[OutcomeRule] = Field(default_factory=list, max_length=8)
    provenance: Provenance

    @model_validator(mode="after")
    def validate_graph(self):
        if not self.outputs:
            raise ValueError("Outputs are required")
        if not isinstance(self.steps[0].action, Navigate):
            raise ValueError("First step must establish the entrypoint")
        for name in [*self.inputs, *self.outputs]:
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                raise ValueError("Invalid field name")
        available: dict[str, str] = {}
        ids = set()

        def check(value):
            for reference in references(value):
                if isinstance(reference, InputRef) and reference.name not in self.inputs:
                    raise ValueError("Unknown input reference")
                if isinstance(reference, ExtractedRef) and reference.name not in available:
                    raise ValueError("Unknown or forward extracted reference")

        check(self.application)
        # Global outcome checks can run before any extraction.
        check(self.business_outcomes)
        for step in self.steps:
            if step.id in ids:
                raise ValueError("Duplicate step ID")
            ids.add(step.id)
            check(step.preconditions)
            check(step.action)
            if isinstance(step.action, Navigate):
                parts = urlsplit(step.action.url)
                if not step.action.url.startswith("/") or step.action.url.startswith("//") or parts.scheme or parts.netloc or parts.query or parts.fragment:
                    raise ValueError("Artifact navigation must be an instance-relative path")
            if isinstance(step.action, Extract):
                if step.action.name in available:
                    raise ValueError("Duplicate extraction")
                available[step.action.name] = PARSER_TYPES[step.action.parser]
            check(step.postconditions)
        check(self.success_conditions)
        check(self.outputs)
        for output in self.outputs.values():
            binding = output.binding
            if isinstance(binding, ExtractedRef) and available[binding.name] != output.type:
                raise ValueError("Extraction/output type mismatch")
        return self
