"""Acceptance/output contract for the demo, not a banking action sequence."""

from typing import Literal

from pydantic import Field

from .capability import Application, FieldDefinition, OutcomeRule, OutputDefinition
from .contracts import (
    Contract, ExtractedRef, InputRef, LiteralValue, Predicate, RoleLocator,
    StructuralLocator, TargetSpec, TextEquals, TextLocator, Visible,
)


class ExtractionDefinition(Contract):
    parser: Literal["text", "usd_minor_units", "usd_currency"]
    description: str


class DiscoveryTask(Contract):
    capability_id: str
    description: str
    application: Application
    inputs: dict[str, FieldDefinition]
    outputs: dict[str, OutputDefinition]
    extractions: dict[str, ExtractionDefinition]
    success_conditions: list[Predicate]
    business_outcomes: list[OutcomeRule] = Field(default_factory=list)


def value_cell(caption: str) -> TargetSpec:
    return TargetSpec(frames=["workspace"], candidates=[StructuralLocator(
        tag="td", adjacent_label=LiteralValue(value=caption))], expected_tag="td",
        rationale="Value cell adjacent to its visible caption")


def savings_task() -> DiscoveryTask:
    return DiscoveryTask(
        capability_id="banking.savings_balance", description="Read a fictional member's current savings balance.",
        application=Application(family="fictional-credit-union", version="1.0", checkpoint=Visible(
            target=TargetSpec(candidates=[TextLocator(text=LiteralValue(value="Fictional Credit Union — Staff Console v1.0"))],
                              rationale="Visible vendor/application version header"))),
        inputs={"member_id": FieldDefinition(type="string", format="member_id", description="Five-digit fictional member ID")},
        outputs={
            "member_id": OutputDefinition(type="string", format="member_id", description="Member ID read from the active record",
                binding=ExtractedRef(name="member_id"), equals=InputRef(name="member_id")),
            "balance_minor_units": OutputDefinition(type="integer", description="Savings balance in USD cents",
                binding=ExtractedRef(name="balance_minor_units")),
            "currency": OutputDefinition(type="string", format="currency", enum=["USD"], description="Currency read from the balance",
                binding=ExtractedRef(name="currency")),
        },
        extractions={
            "member_id": ExtractionDefinition(parser="text", description="Read the member ID displayed in the active record"),
            "balance_minor_units": ExtractionDefinition(parser="usd_minor_units", description="Read the current savings balance; code parses USD into cents"),
            "currency": ExtractionDefinition(parser="usd_currency", description="Read the currency from the displayed savings balance"),
        },
        success_conditions=[
            Visible(target=TargetSpec(frames=["workspace"], candidates=[RoleLocator(role="heading", name=LiteralValue(value="Savings account"))], rationale="Savings view reached")),
            TextEquals(target=value_cell("Member ID"), value=InputRef(name="member_id")),
            TextEquals(target=value_cell("Account type"), value=LiteralValue(value="Savings")),
        ],
        business_outcomes=[OutcomeRule(code="MEMBER_NOT_FOUND", condition=Visible(target=TargetSpec(
            frames=["workspace"], candidates=[TextLocator(text=LiteralValue(value="No such member."))], rationale="Known no-record message")))],
    )
