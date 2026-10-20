"""Compile a verified execution; never infer parameterization from a transcript."""

import re
from urllib.parse import urlsplit

from .capability import Capability, Provenance, Step
from .contracts import Contract, LiteralValue, Navigate, Success, StructuralLocator, TargetSpec


def walk(value):
    yield value
    if isinstance(value, Contract):
        for name in type(value).model_fields:
            yield from walk(getattr(value, name))
    elif isinstance(value, dict):
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def compile_capability(discovery, *, origin: str) -> Capability:
    trace = discovery.trace
    if not isinstance(discovery.result, Success) or not trace.verified_success:
        raise ValueError("UNVERIFIED_DISCOVERY")
    if trace.human_assisted:
        raise ValueError("HUMAN_ASSISTED_TRACE")
    task = trace.task
    steps = []
    expected_origin = urlsplit(origin)
    for recorded in trace.steps:
        action = recorded.action
        if isinstance(action, Navigate):
            parts = urlsplit(action.url)
            if (parts.scheme, parts.netloc) != (expected_origin.scheme, expected_origin.netloc) or parts.query or parts.fragment:
                raise ValueError("UNREUSABLE_NAVIGATION")
            action = Navigate(url=parts.path or "/")
        steps.append(Step.model_validate({**recorded.model_dump(), "action":action.model_dump()}))
    # Reject leaks; do not repair them with string substitution. Bindings were
    # assigned at fill/grounding time from observed provenance.
    for value in walk(steps):
        if isinstance(value, TargetSpec):
            if any(not name for name in value.frames):
                raise ValueError("UNSTABLE_FRAME")
            for candidate in value.candidates:
                if isinstance(candidate, StructuralLocator) and not candidate.name and not candidate.adjacent_label:
                    raise ValueError("WEAK_STRUCTURAL_TARGET")
        text = value.value if isinstance(value, LiteralValue) and isinstance(value.value, str) else value.url if isinstance(value, Navigate) else None
        if text is not None:
            if re.search(r"[0-9]{3,}", text):
                raise ValueError("CONCRETE_IDENTIFIER_IN_ARTIFACT")
            if any(isinstance(v,str) and v and v in text for v in trace.invocation.values()):
                raise ValueError("DISCOVERY_INPUT_IN_LITERAL")
    capability = Capability(
        schema_version="1.0", capability_id=task.capability_id, capability_version="1.0.0", description=task.description,
        application=task.application, inputs=task.inputs, outputs=task.outputs, steps=steps,
        success_conditions=task.success_conditions, business_outcomes=task.business_outcomes,
        provenance=Provenance(run_id=trace.run_id, evidence_file=trace.evidence_file, source=trace.source,
                              model=trace.model, response_ids=trace.response_ids, human_assisted=False),
    )
    return Capability.model_validate_json(capability.model_dump_json())
