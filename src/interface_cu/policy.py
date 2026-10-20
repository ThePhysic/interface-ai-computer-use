"""Default-deny policy for the small synthetic target, independent of model labels."""

from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import Field

from .contracts import ActionFacts, Contract


class PolicyViolation(Exception):
    pass


class Policy(Contract):
    origin: str = "http://127.0.0.1:5055"
    allowed_paths: list[str] = Field(default_factory=lambda: [r"/", r"/home", r"/members", r"/members/[0-9]{5}", r"/members/[0-9]{5}/savings", r"/runtime.js"])
    allowed_actions: list[str] = Field(default_factory=lambda: ["navigate", "click", "fill", "press", "extract"])

    def check_url(self, url: str, method: str = "GET", *, human: bool = False) -> None:
        import re

        parsed = urlsplit(url)
        expected = urlsplit(self.origin)
        if (parsed.scheme, parsed.hostname, parsed.port) != (expected.scheme, expected.hostname, expected.port):
            raise PolicyViolation("ORIGIN_DENIED")
        if parsed.username or parsed.password or method.upper() != "GET":
            raise PolicyViolation("METHOD_OR_CREDENTIALS_DENIED")
        path = unquote(parsed.path)
        if any(part in {".", ".."} for part in path.split("/")) or "\\" in path:
            raise PolicyViolation("PATH_DENIED")
        if not any(re.fullmatch(pattern, path) for pattern in self.allowed_paths):
            raise PolicyViolation("ROUTE_DENIED")
        query = parse_qs(parsed.query, keep_blank_values=True)
        if "operator_ack" in query:
            if not human or query != {"operator_ack":["confirmed"]} or not re.fullmatch(r"/members/[0-9]{5}", path):
                raise PolicyViolation("HUMAN_ACK_REQUIRES_OPERATOR")

    def check_action(self, kind: str, facts: ActionFacts | None = None) -> None:
        if kind not in self.allowed_actions:
            raise PolicyViolation("ACTION_DENIED")
        if not facts:
            return
        if facts.disabled:
            raise PolicyViolation("DISABLED_CONTROL")
        if facts.operator_only:
            raise PolicyViolation("HUMAN_ONLY_CONTROL")
        tag = facts.tag
        if kind == "extract" and tag != "td":
            raise PolicyViolation("READ_TARGET_DENIED")
        if facts.target not in {"", "_self"}:
            raise PolicyViolation("NEW_WINDOW_DENIED")
        if kind == "fill" and (tag != "input" or facts.type not in {"text", "search", "number"}):
            raise PolicyViolation("FIELD_DENIED")
        if kind in {"click", "press"} and tag not in {"a", "button", "input"}:
            raise PolicyViolation("UNKNOWN_CONTROL")
        if facts.href:
            self.check_url(facts.href)
        if facts.form_action:
            if not facts.form_method:
                raise PolicyViolation("UNKNOWN_FORM_METHOD")
            self.check_url(facts.form_action, facts.form_method)
        if kind in {"click", "press"} and tag in {"button", "input"} and not facts.form_action and facts.type not in {"text", "search", "number"}:
            raise PolicyViolation("UNKNOWN_EFFECT")

    def check_read_recovery(self, kind: str, facts: ActionFacts | None = None) -> None:
        """Recovery never trusts an artifact/model claim that an action is safe."""
        if kind == "navigate":
            return  # The normal URL/GET policy still applies.
        if kind != "click" or facts is None or facts.tag != "a" or not facts.href or facts.form_action:
            raise PolicyViolation("RECOVERY_REQUIRES_READ_LINK")
        self.check_url(facts.href, "GET")
