import json

from interface_cu.evidence import Evidence, safe_url


def test_secret_and_input_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "canary-secret-content")
    evidence = Evidence(tmp_path, "automated_test")
    evidence.emit("canary", password="hidden", value="12345", detail="canary-secret-content name@example.com",
                  frame_url="http://user:pass@localhost:5055/members?member_id=secret#fragment")
    content = (evidence.directory / "events.jsonl").read_text()
    for secret in ("canary-secret-content", "name@example.com", "12345", "user:pass", "member_id=secret", "hidden"):
        assert secret not in content
    assert json.loads(content)["source"] == "automated_test"
    assert safe_url("http://localhost:5055/members?x=y#z") == "http://localhost:5055/members"
