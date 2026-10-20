"""Verify reviewed evidence and publication inventory; never stage, push, or call a model."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from interface_cu.capability import Capability
from interface_cu.runtime import RuntimeProfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "evidence/phase5/public-manifest.json"
ROOT_FILES = {".gitignore", ".gitattributes", "README.md", "REPORT.md", "REQUIREMENTS.md",
              "pyproject.toml", "requirements.lock.txt"}
ARTIFACT = "evidence/phase2/savings.capability.json"
ARTIFACT_SHA = "e965f3f08a4ec5c139c507446752c213f29886e4504f90f8b1eb7dadab331cb3"


def require(condition, code):
    if not condition:
        raise ValueError(code)


def historical_metadata(path):
    parts = path.parts
    return (len(parts) > 2 and parts[0] == "evidence" and parts[1] in {"phase1", "phase2", "phase3", "phase4"}
            and (path.name in {"README.md", "verification.json", "tests.xml"}
                 or path.as_posix() == "evidence/phase3/runs.json"))


def public_files():
    files = [ROOT / name for name in ROOT_FILES]
    require(all(p.is_file() for p in files), "MISSING_ROOT_DELIVERABLE")
    for directory in ("src/interface_cu", "tests", "scripts", "evidence"):
        for path in (ROOT / directory).rglob("*"):
            relative = path.relative_to(ROOT)
            if not path.is_file() or "__pycache__" in relative.parts or historical_metadata(relative):
                continue
            allowed = {".py", ".js", ".html"} if directory != "evidence" else {".md", ".json", ".jsonl", ".png"}
            require(path.suffix in allowed, "UNREVIEWED_FILE_TYPE:" + relative.as_posix())
            files.append(path)
    for path in files:
        require(not path.is_symlink() and path.resolve().is_relative_to(ROOT), "OUTSIDE_PUBLIC_FILE")
    return sorted(set(files))


def read_events(path):
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    require([r["sequence"] for r in records] == list(range(1, len(records) + 1)), "INVALID_EVENT_SEQUENCE")
    require(all(r["run_id"] == path.parent.name for r in records), "EVENT_RUN_MISMATCH")
    require(len({r["source"] for r in records}) == 1, "MIXED_RUN_PROVENANCE")
    return records


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def evidence_checks(files):
    eligible = {p.relative_to(ROOT).as_posix() for p in files}
    logs = {p.relative_to(ROOT).as_posix(): read_events(p) for p in files if p.suffix == ".jsonl"}
    references = 0
    screenshots = 0
    for path in files:
        if path.suffix == ".png":
            require(hashlib.sha256(path.read_bytes()).hexdigest() == path.stem, "SCREENSHOT_HASH_MISMATCH")
            screenshots += 1
        if path.suffix not in {".json", ".jsonl"}:
            continue
        objects = logs[path.relative_to(ROOT).as_posix()] if path.suffix == ".jsonl" else [json.loads(path.read_text(encoding="utf-8"))]
        for value in strings(objects):
            normalized = value.replace("\\", "/")
            # The exact README commands write to .runs in the isolated project.
            # Their raw files are archived unchanged; only the verifier's root
            # mapping changes, never the recorded execution or its references.
            if normalized.startswith(".runs/"):
                normalized = "evidence/phase5/reproduction/runs/" + normalized[len(".runs/"):]
            if not normalized.startswith("evidence/") or "\n" in normalized:
                continue
            target, separator, event = normalized.partition("#event-")
            # References in machine-readable manifests include documentation and
            # run directories as well as individual evidence files.
            target_path = ROOT / target
            require(target_path.resolve().is_relative_to(ROOT), "OUTSIDE_EVIDENCE_REFERENCE")
            require(target_path.exists(), "MISSING_EVIDENCE_REFERENCE:" + target)
            if target_path.is_file():
                require(target in eligible, "PRIVATE_EVIDENCE_REFERENCE:" + target)
            if separator:
                require(target in logs and event.isdigit() and 1 <= int(event) <= len(logs[target]), "INVALID_EVENT_REFERENCE")
            references += 1
    artifact = Capability.model_validate_json((ROOT / ARTIFACT).read_bytes())
    require(hashlib.sha256((ROOT / ARTIFACT).read_bytes()).hexdigest() == ARTIFACT_SHA, "ORIGINAL_ARTIFACT_CHANGED")
    profile = RuntimeProfile.model_validate_json((ROOT / "evidence/phase3/savings.runtime.json").read_bytes())
    profile.validate_for(artifact)
    discovery = logs[artifact.provenance.evidence_file.replace("\\", "/")]
    requests = [r["data"] for r in discovery if r["event"] == "discovery_model_request"]
    responses = [r["data"] for r in discovery if r["event"] == "discovery_model_response"]
    require(artifact.provenance.source == "live_llm" and all(r["source"] == "live_llm" for r in discovery), "DISCOVERY_PROVENANCE_MISMATCH")
    require(len(requests) == len(responses) == 8, "DISCOVERY_RESPONSE_COUNT_MISMATCH")
    require([r["response_id"] for r in responses] == artifact.provenance.response_ids, "ARTIFACT_RESPONSE_PROVENANCE_MISMATCH")
    require(all(a["observation_id"] == b["choice"]["observation_id"] for a, b in zip(requests, responses)), "DISCOVERY_OBSERVATION_MISMATCH")
    require(any(r["event"] == "discovery_completed" and r["data"]["verified_success"] for r in discovery), "UNVERIFIED_DISCOVERY")
    require(any(r["event"] == "capability_compiled" for r in discovery), "MISSING_COMPILATION_EVIDENCE")
    return {"event_logs": len(logs), "screenshots": screenshots, "references_checked": references,
            "genuine_discovery_response_ids": len(responses), "artifact_sha256": ARTIFACT_SHA,
            "capability_schema": artifact.schema_version, "runtime_profile_schema": profile.schema_version}


def privacy_checks(files):
    secrets = [v for k, v in os.environ.items() if len(v) >= 6 and any(s in k.upper() for s in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))]
    local_identifiers = [v for k in ("USERNAME", "COMPUTERNAME") if len(v := os.environ.get(k, "")) >= 4]
    patterns = [re.compile(r"[A-Za-z]:[\\/]+Users[\\/]", re.I), re.compile(r"/(?:Users|home)/[^/\s]+/"),
                re.compile(r"sk-[A-Za-z0-9_-]{20,}")]
    for path in files:
        if path.suffix == ".png":
            continue  # Images are synthetic and separately reviewed; no OCR claim.
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT).as_posix()
        require(not any(s in text for s in secrets), "ENVIRONMENT_SECRET_MATCH:" + relative)
        require(not any(p.search(text) for p in patterns), "PRIVATE_PATH_OR_KEY_PATTERN:" + relative)
        require(not any(re.search(r"\b" + re.escape(v) + r"\b", text, re.I) for v in local_identifiers), "LOCAL_ACCOUNT_METADATA:" + relative)
    return {"text_files_checked": sum(p.suffix != ".png" for p in files), "matches": 0,
            "scope": "Exact environment secrets, key patterns, local account/machine names and absolute user paths; fictional images reviewed separately."}


def git_checks(files):
    if not (ROOT / ".git").exists():
        return {"repository_initialized_here": False, "publication": "manual"}
    allowed = {p.relative_to(ROOT).as_posix() for p in files} | {MANIFEST}
    def names(*args):
        result = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", *args], check=True, capture_output=True)
        return {n for n in result.stdout.decode("utf-8").split("\0") if n}
    tracked = names("--cached")
    untracked = names("--others", "--exclude-standard")
    require(not (tracked | untracked) - allowed, "UNREVIEWED_GIT_CONTENT")
    ignored = names("--others", "--ignored", "--exclude-standard", "--directory")
    return {"repository_initialized_here": True, "tracked_or_staged_files": len(tracked),
            "reviewed_untracked_files": len(untracked), "ignored_entries": len(ignored), "publication": "manual"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--copy-to", type=Path, help="Copy only reviewed files into a directory under .private; no deletion")
    args = parser.parse_args()
    files = public_files()
    result = {"evidence": evidence_checks(files), "privacy": privacy_checks(files), "git": git_checks(files)}
    report = (ROOT / "REPORT.md").read_text(encoding="utf-8")
    headings = [line[2:] for line in report.splitlines() if line.startswith("# ")]
    require(headings == ["Architecture", "Artifact schema", "Determinism & error handling",
                         "Heterogeneity & multi-tenant", "Escalation & handoff", "Safety", "Cuts"],
            "REPORT_HEADINGS_CHANGED")
    result["report"] = {"exact_required_headings": True, "word_count": len(report.split()),
                        "page_count": "approximately 1-3 pages; rendering dependent"}
    if args.write_manifest:
        manifest = {"kind": "reviewed_public_inventory", "self_excluded_from_hashes": MANIFEST,
                    "files": [{"path": p.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                              for p in files if p.relative_to(ROOT).as_posix() != MANIFEST], "checks": result}
        output = ROOT / MANIFEST
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        files = public_files()
    if args.copy_to:
        destination = args.copy_to.resolve()
        require(destination.is_relative_to(ROOT / ".private"), "COPY_MUST_STAY_IN_PRIVATE_WORKSPACE")
        for source in files:
            target = destination / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        result["copied_reviewed_files"] = len(files)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
