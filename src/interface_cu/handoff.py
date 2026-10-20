"""Serialized session ownership and sanitized same-browser activity capture."""

import asyncio
from importlib.resources import files

from .contracts import Ownership
from .evidence import Evidence


class OwnershipError(Exception):
    pass


class SessionControl:
    def __init__(self, evidence: Evidence):
        self.state = Ownership.AUTOMATION
        self.generation = 0
        self.lock = asyncio.Lock()
        self.evidence = evidence
        self.active_intervention_id: str | None = None

    def _transition(self, state):
        self.state = state
        self.generation += 1
        self.evidence.emit("ownership", state=state.value, generation=self.generation,
                           intervention_id=self.active_intervention_id)

    def require_automation(self, generation: int | None = None):
        if self.state != Ownership.AUTOMATION or (generation is not None and generation != self.generation):
            raise OwnershipError("AUTOMATION_DOES_NOT_OWN_SESSION")

    async def give_to_human(self):
        async with self.lock:
            self.require_automation()
            self._transition(Ownership.PAUSED)
            self._transition(Ownership.HUMAN)

    async def pause(self, intervention_id, expected_generation):
        async with self.lock:
            self.require_automation(expected_generation)
            if self.active_intervention_id is not None:
                raise OwnershipError("INTERVENTION_ALREADY_ACTIVE")
            self.active_intervention_id = intervention_id
            self._transition(Ownership.PAUSED)
            return self.generation

    def require_intervention(self, intervention_id, generation, state):
        if self.active_intervention_id != intervention_id or self.generation != generation or self.state != state:
            raise OwnershipError("STALE_INTERVENTION")

    async def transfer_to_human(self, intervention_id, generation):
        async with self.lock:
            self.require_intervention(intervention_id, generation, Ownership.PAUSED)
            self._transition(Ownership.HUMAN)
            return self.generation

    async def begin_resume(self, intervention_id, generation):
        async with self.lock:
            self.require_intervention(intervention_id, generation, Ownership.HUMAN)
            self._transition(Ownership.RESUMING)
            return self.generation

    def approve_resume(self, intervention_id, generation):
        """Coordinator holds lock across reconciliation and this final CAS."""
        if not self.lock.locked():
            raise OwnershipError("RESUME_REQUIRES_LOCK")
        self.require_intervention(intervention_id, generation, Ownership.RESUMING)
        self._transition(Ownership.AUTOMATION)
        self.active_intervention_id = None
        return self.generation

    async def reject_intervention(self, intervention_id):
        async with self.lock:
            if self.active_intervention_id == intervention_id:
                self._transition(Ownership.FAILED)
                self.active_intervention_id = None

    async def finish_run(self, successful):
        async with self.lock:
            if self.state not in {Ownership.COMPLETED, Ownership.FAILED}:
                self._transition(Ownership.COMPLETED if successful else Ownership.FAILED)

    async def finish_probe(self):
        async with self.lock:
            if self.state != Ownership.HUMAN:
                raise OwnershipError("HUMAN_DOES_NOT_OWN_SESSION")
            self._transition(Ownership.COMPLETED)


class ActivityCapture:
    def __init__(self, control: SessionControl, evidence: Evidence, session_id: str, page_id: str):
        self.control, self.evidence = control, evidence
        self.session_id, self.page_id = session_id, page_id
        self.events = 0
        self.page = None

    async def install(self, context, page):
        self.page = page
        await context.expose_binding("__interfaceActivity", self.receive)
        await context.add_init_script(script=files("interface_cu").joinpath("activity.js").read_text(encoding="utf-8"))

    def receive(self, source, payload):
        if (self.control.state != Ownership.HUMAN or not isinstance(payload, dict)
                or self.page is None or source.get("page") is not self.page):
            return
        if payload.get("event") not in {"click", "change", "submit"}:
            return
        # Only the fixed metadata vocabulary crosses the persistence boundary.
        tag = payload.get("tag")
        if tag not in {"INPUT", "BUTTON", "A", "FORM", "SELECT", "TEXTAREA"}:
            return
        self.events += 1
        self.evidence.emit("manual_activity", session_id=self.session_id, page_id=self.page_id,
                           frame_url=source["frame"].url, activity=payload["event"], tag=tag,
                           input_redacted=True, owner=self.control.state.value,
                           intervention_id=self.control.active_intervention_id, generation=self.control.generation)

    def navigation(self, frame):
        if self.control.state == Ownership.HUMAN and frame.page is self.page:
            self.evidence.emit("manual_navigation", session_id=self.session_id, page_id=self.page_id,
                               frame_url=frame.url, owner=self.control.state.value,
                               intervention_id=self.control.active_intervention_id, generation=self.control.generation)
