import pytest

from interface_cu.contracts import ActionFacts
from interface_cu.policy import Policy, PolicyViolation


@pytest.mark.parametrize("path", ["/", "/home", "/members?member_id=06789", "/members/06789/savings"])
def test_read_routes_allowed(path):
    Policy().check_url("http://127.0.0.1:5055" + path)


@pytest.mark.parametrize("url,method", [
    ("http://127.0.0.1:5055/transfer", "GET"),
    ("http://127.0.0.1:5055/members", "POST"),
    ("http://127.0.0.1:5056/members", "GET"),
    ("https://example.com/", "GET"),
    ("http://user:password@127.0.0.1:5055/members", "GET"),
    ("http://127.0.0.1:5055/members/%2e%2e/home", "GET"),
    ("javascript:alert(1)", "GET"),
])
def test_default_denial(url, method):
    with pytest.raises(PolicyViolation):
        Policy().check_url(url, method)


@pytest.mark.parametrize("kind", ["click", "press", "fill"])
def test_resolved_financial_form_denied_for_every_interaction(kind):
    with pytest.raises(PolicyViolation):
        Policy().check_action(kind, ActionFacts(tag="input", type="text", form_action="http://127.0.0.1:5055/transfer", form_method="post"))


def test_configurable_action_allowlist():
    with pytest.raises(PolicyViolation):
        Policy(allowed_actions=["navigate"]).check_action("click")
