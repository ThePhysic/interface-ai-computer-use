"""Explicitly labelled JSONL evidence; never persist raw browser or SDK errors."""

import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .contracts import identifier, utc_now


def safe_url(value: str) -> str:
    parts = urlsplit(value)
    # Drop userinfo, query parameters and fragments, including manual form input.
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme, host + port, parts.path, "", ""))


class Evidence:
    def __init__(self, root: Path, source: str):
        if source not in {"live_llm", "deterministic_replay", "manual_probe", "automated_test"}:
            raise ValueError("Evidence requires explicit provenance")
        self.run_id = identifier()
        self.directory = root / self.run_id
        self.directory.mkdir(parents=True)
        self.source = source
        self.sequence = 0
        self.secrets = [v for k, v in os.environ.items() if any(s in k.upper() for s in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")) and len(v) >= 6]

    def sanitize(self, value, key=""):
        if any(s in key.lower() for s in ("password", "token", "secret", "credential", "api_key", "value")):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {k: self.sanitize(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self.sanitize(v) for v in value]
        if isinstance(value, str):
            if key.endswith("url"):
                value = safe_url(value)
            for secret in self.secrets:
                value = value.replace(secret, "[REDACTED]")
            value = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", value)
            value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", value)
            return value[:1500]
        return value

    def emit(self, event: str, **data) -> str:
        self.sequence += 1
        record = {"run_id": self.run_id, "sequence": self.sequence, "at": utc_now().isoformat(), "source": self.source, "event": event, "data": self.sanitize(data)}
        path = self.directory / "events.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return f"{path.as_posix()}#event-{self.sequence}"

    def screenshot(self, png: bytes) -> str:
        """Caller must supply the surface's masked, synthetic-demo-only screenshot."""
        digest = hashlib.sha256(png).hexdigest()
        path = self.directory / f"{digest}.png"
        path.write_bytes(png)
        self.emit("screenshot", sha256=digest, filename=path.name)
        return path.as_posix()
