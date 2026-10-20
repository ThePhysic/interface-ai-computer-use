"""Async browser adapter. UI-only inspection; no application state or API access."""

import hashlib
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import ElementHandle, Error as BrowserError, Frame, Locator, async_playwright

from .contracts import (
    Action, ActionFacts, Bindings, Click, Extract, ExtractedRef, Fill, InputRef, LabelLocator, Navigate, NavigationState,
    LiteralValue, Observation, Press, ResolvedTarget, ResolutionError, RoleLocator, RowLocator,
    Ownership, StructuralLocator, SurfaceError, TargetSpec, TextEquals, TextLocator, ValueEquals,
    VisibleControl, identifier,
)
from .evidence import safe_url
from .handoff import ActivityCapture
from .policy import PolicyViolation


@dataclass(frozen=True)
class _BrowserTarget(ResolvedTarget):
    locator: Locator
    frame: Frame
    session_id: str


@dataclass(frozen=True)
class _ObservedControl:
    node: ElementHandle
    frame: Frame
    control: VisibleControl
    facts: ActionFacts


FACTS = """el => ({
  tag: el.tagName.toLowerCase(), type: el.type || '', disabled: !!el.disabled,
  href: el.tagName === 'A' ? el.href : null, target: el.target || '',
  form_action: el.form ? (el.hasAttribute('formaction') ? el.formAction : el.form.action) : null,
  form_method: el.form ? (el.hasAttribute('formmethod') ? el.formMethod : el.form.method) : null,
  operator_only: !!el.closest('[data-operator-only]')
})"""


class BrowserSurface:
    def __init__(self, page, policy, control, evidence):
        self._page, self.policy, self.control, self.evidence = page, policy, control, evidence
        self._original_page, self._original_context = page, page.context
        self.session_id, self.page_id = identifier(), identifier()
        self.capture = ActivityCapture(control, evidence, self.session_id, self.page_id)
        self.revision = 0
        self._latest_observation: Observation | None = None
        self._observed_controls: dict[str, _ObservedControl] = {}
        self._blocked_request_code: str | None = None
        self._native_dialog_type: str | None = None

    async def install(self):
        async def gate(route):
            denied_url = route.request.url
            try:
                self.policy.check_url(route.request.url, route.request.method, human=self.control.state == Ownership.HUMAN)
                # Playwright does not route every hop in a browser redirect
                # chain. Fetch exactly one allowed GET, then reject redirects
                # before fulfilling the response to the browser. No redirected
                # request can reach an unchecked destination, even on this origin.
                response = await route.fetch(max_redirects=0, timeout=2500)
                try:
                    if 300 <= response.status < 400:
                        location = response.headers.get("location")
                        if location:
                            denied_url = urljoin(route.request.url, location)
                            self.policy.check_url(denied_url)
                        raise PolicyViolation("UNEXPECTED_REDIRECT")
                    await route.fulfill(response=response)
                finally:
                    await response.dispose()
            except (PolicyViolation, ValueError) as exc:
                self._blocked_request_code = str(exc) if isinstance(exc, PolicyViolation) else "INVALID_NAVIGATION_URL"
                self.evidence.emit("network_blocked", url=denied_url, method=route.request.method, code=self._blocked_request_code)
                await route.abort("blockedbyclient")
            except BrowserError:
                self._blocked_request_code = "NETWORK_READ_FAILED"
                self.evidence.emit("network_read_failed", url=route.request.url)
                try:
                    await route.abort("failed")
                except BrowserError:
                    pass  # Closing a session can cancel an already pending read.

        await self._page.context.route("**/*", gate)
        await self.capture.install(self._page.context, self._page)

        def navigated(frame):
            self.revision += 1
            self.capture.navigation(frame)

        self._page.on("framenavigated", navigated)

        async def dialog(dialog):
            self._native_dialog_type = dialog.type
            self.evidence.emit("unexpected_native_dialog", dialog_type=dialog.type, owner=self.control.state.value)
            # Cancel unknown native confirmations, then stop automation. The
            # latch is deliberately not cleared by a successful subsequent read.
            if self.control.state == Ownership.AUTOMATION:
                await dialog.dismiss()

        self._page.on("dialog", dialog)

    async def _scope(self, names):
        frame = self._page.main_frame
        for name in names:
            # Resolve the actual document boundary, rather than relying on the
            # cached child-frame list after a top-level reload.
            elements = [node for node in await frame.locator("iframe").all()
                        if await node.get_attribute("name") == name]
            if len(elements) != 1:
                raise ResolutionError("FRAME_NOT_UNIQUE")
            element = elements[0]
            if not await element.is_visible():
                raise ResolutionError("FRAME_NOT_VISIBLE")
            handle = await element.element_handle()
            try:
                frame = await handle.content_frame() if handle else None
            finally:
                if handle:
                    await handle.dispose()
            if frame is None:
                raise ResolutionError("FRAME_NOT_UNIQUE")
            self.policy.check_url(frame.url)
        return frame

    def _locator(self, frame, spec, bindings):
        def text(binding):
            value = bindings.resolve(binding)
            if not isinstance(value, str):
                raise ResolutionError("LOCATOR_REQUIRES_STRING")
            return value

        if isinstance(spec, RoleLocator):
            return frame.get_by_role(spec.role, name=text(spec.name), exact=True)
        if isinstance(spec, LabelLocator):
            return frame.get_by_label(text(spec.label), exact=True)
        if isinstance(spec, TextLocator):
            return frame.get_by_text(text(spec.text), exact=True)
        if isinstance(spec, RowLocator):
            row = frame.get_by_role("row").filter(has=frame.get_by_role("cell", name=text(spec.row_text), exact=True))
            return row.get_by_role(spec.role, name=text(spec.name), exact=True)
        if isinstance(spec, StructuralLocator):
            selector = spec.tag + (f'[name="{spec.name}"]' if spec.name else "")
            if spec.adjacent_label:
                cell = frame.get_by_text(text(spec.adjacent_label), exact=True).locator("xpath=ancestor-or-self::td[1]/following-sibling::td[1]")
                return cell if spec.tag == "td" and not spec.name else cell.locator(selector)
            return frame.locator(selector)
        raise ResolutionError("UNSUPPORTED_LOCATOR")

    async def _resolve(self, target: TargetSpec, bindings: Bindings):
        self.policy.check_url(self._page.url)
        frame = await self._scope(target.frames)
        chosen = None
        for candidate in target.candidates:
            locator = self._locator(frame, candidate, bindings)
            if await locator.count() > 100:
                raise ResolutionError("TARGET_TOO_BROAD")
            matches = [node for node in await locator.all() if await node.is_visible()]
            if len(matches) > 1:
                raise ResolutionError("AMBIGUOUS_TARGET")
            if not matches:
                continue
            match = matches[0]
            if target.expected_tag and await match.evaluate("el => el.tagName.toLowerCase()") != target.expected_tag:
                raise ResolutionError("IDENTITY_MISMATCH")
            if target.expected_text and (await match.inner_text()).strip() != bindings.resolve(target.expected_text):
                raise ResolutionError("IDENTITY_MISMATCH")
            if chosen is not None:
                handle = await chosen.element_handle()
                try:
                    if not await match.evaluate("(el, other) => el === other", handle):
                        raise ResolutionError("CONFLICTING_TARGETS")
                finally:
                    await handle.dispose()
            chosen = match
        if chosen is None:
            raise ResolutionError("TARGET_NOT_FOUND")
        if not await chosen.is_enabled():
            raise ResolutionError("TARGET_NOT_ENABLED")
        return chosen

    async def evaluate(self, predicate, bindings: Bindings) -> bool:
        try:
            locator = await self._resolve(predicate.target, bindings)
            if isinstance(predicate, TextEquals):
                return (await locator.inner_text()).strip() == bindings.resolve(predicate.value)
            if isinstance(predicate, ValueEquals):
                return await locator.input_value() == bindings.resolve(predicate.value)
            return True
        except ResolutionError:
            return False
        except BrowserError:
            raise SurfaceError("BROWSER_ERROR") from None

    async def resolve(self, target: TargetSpec, bindings: Bindings) -> ResolvedTarget:
        try:
            return _BrowserTarget(await self._resolve(target, bindings),
                                  await self._scope(target.frames), self.session_id)
        except BrowserError:
            raise SurfaceError("BROWSER_ERROR") from None

    def _browser_target(self, resolved: ResolvedTarget | None) -> _BrowserTarget:
        if not isinstance(resolved, _BrowserTarget) or resolved.session_id != self.session_id:
            raise ResolutionError("INVALID_TARGET_HANDLE")
        return resolved

    async def inspect(self, resolved: ResolvedTarget) -> ActionFacts:
        try:
            return ActionFacts.model_validate(await self._browser_target(resolved).locator.evaluate(FACTS))
        except BrowserError:
            raise SurfaceError("BROWSER_ERROR") from None

    async def navigation_state(self) -> NavigationState:
        if self._page is not self._original_page or self._page.context is not self._original_context:
            raise SurfaceError("SESSION_IDENTITY_CHANGED")
        if self._page.is_closed():
            raise SurfaceError("SESSION_CLOSED")
        return NavigationState(session_id=self.session_id, page_id=self.page_id, url=self._page.url,
                               frame_urls=[frame.url for frame in self._page.frames if frame.parent_frame],
                               blocked_request_code=self._blocked_request_code,
                               native_dialog_type=self._native_dialog_type)

    async def perform(self, action: Action, resolved: ResolvedTarget | None = None,
                      value: str | None = None, facts: ActionFacts | None = None):
        """Execute the inspected action, translating browser failures at this boundary."""
        attempted = False
        try:
            if self._blocked_request_code:
                raise SurfaceError(self._blocked_request_code)
            if self._native_dialog_type:
                raise SurfaceError("UNEXPECTED_NATIVE_DIALOG")
            if isinstance(action, Navigate):
                attempted = True
                await self._page.goto(action.url, wait_until="load")
                return
            target = self._browser_target(resolved)
            locator = target.locator
            if isinstance(action, Fill) and not await locator.is_editable():
                raise ResolutionError("FIELD_NOT_EDITABLE")
            if isinstance(action, Click):
                await locator.click(trial=True)
            # Do not execute if the effect changed after policy inspected it.
            if facts is None or await self.inspect(resolved) != facts:
                raise ResolutionError("RESOLVED_ACTION_CHANGED")
            attempted = True
            return await self._perform(action, target, value, facts)
        except BrowserError:
            raise SurfaceError("BROWSER_ERROR", completion_uncertain=attempted) from None

    async def _perform(self, action, resolved, value, facts):
        locator = resolved.locator
        if isinstance(action, Extract):
            return (await locator.inner_text()).strip()

        async def act():
            if isinstance(action, Fill):
                await locator.fill(value)
            elif isinstance(action, Click):
                await locator.click()
            elif isinstance(action, Press):
                await locator.press(action.key)

        # Explicitly wait for known link/form navigations. A click returning does
        # not establish that an iframe's replacement document is ready.
        submits = facts.form_action and (
            isinstance(action, Press) and action.key == "Enter"
            or isinstance(action, Click) and facts.type == "submit"
        )
        if (isinstance(action, Click) and facts.href) or submits:
            async with resolved.frame.expect_navigation(wait_until="load", timeout=2500):
                await act()
        else:
            await act()

    @staticmethod
    def _frame_path(frame: Frame) -> list[str]:
        names = []
        while frame.parent_frame:
            names.insert(0, frame.name)
            frame = frame.parent_frame
        return names

    async def _describe_control(self, node, frame: Frame, control_id: str) -> VisibleControl | None:
        rect = await node.bounding_box()
        viewport = self._page.viewport_size
        if not rect or not await node.is_visible():
            return None
        if (rect["x"] + rect["width"] <= 0 or rect["y"] + rect["height"] <= 0
                or rect["x"] >= viewport["width"] or rect["y"] >= viewport["height"]):
            return None
        description = await node.evaluate("""el => {
            const tag = el.tagName.toLowerCase();
            const caption = tag === 'input' || tag === 'td'
                ? [...(el.labels || [])].map(x => x.innerText).join(' ')
                  || el.closest('td')?.previousElementSibling?.innerText || '' : '';
            return {tag, text: tag === 'input' ? '[input]' : el.innerText.trim().slice(0,100),
                role: el.getAttribute('role') || ({a:'link',button:'button',input:'textbox',td:'cell',h1:'heading',h2:'heading'}[tag] || ''),
                label: (el.getAttribute('aria-label') || caption).trim().slice(0,100),
                input_type: tag === 'input' ? el.type : ''};
        }""")
        return VisibleControl(control_id=control_id, frames=self._frame_path(frame), rect=rect, **description)

    async def observe(self) -> Observation:
        return await self._capture_observation(register=True)

    async def _capture_observation(self, *, register: bool) -> Observation:
        self.policy.check_url(self._page.url)
        if register:
            self._latest_observation = None
            for captured in self._observed_controls.values():
                await captured.node.dispose()
            self._observed_controls = {}
        controls, readables, headings, masks = [], [], [], []
        revision = self.revision
        for frame in self._page.frames:
            self.policy.check_url(frame.url)
            masks.append(frame.locator("input,textarea,select"))
            for node in await frame.locator("a,button,input").all():
                if len(controls) >= 40:
                    break
                control_id = f"c{len(controls) + 1}"
                description = await self._describe_control(node, frame, control_id)
                if description is None:
                    continue
                controls.append(description)
                if register:
                    handle = await node.element_handle()
                    if handle is None:
                        raise ResolutionError("OBSERVATION_CHANGED")
                    facts = ActionFacts.model_validate(await handle.evaluate(FACTS))
                    self._observed_controls[control_id] = _ObservedControl(handle, frame, description, facts)
            # Read only visible, non-interactive value cells adjacent to a caption.
            # This is bounded UI inspection, not application-state access.
            for node in await frame.locator("td").all():
                if len(readables) >= 20:
                    break
                if not await node.evaluate("el => el.previousElementSibling?.tagName === 'TD' && !el.querySelector('a,button,input,textarea,select,table')"):
                    continue
                control_id = f"r{len(readables) + 1}"
                description = await self._describe_control(node, frame, control_id)
                if description is None or not description.label:
                    continue
                readables.append(description)
                if register:
                    handle = await node.element_handle()
                    if handle is None:
                        raise ResolutionError("OBSERVATION_CHANGED")
                    facts = ActionFacts.model_validate(await handle.evaluate(FACTS))
                    self._observed_controls[control_id] = _ObservedControl(handle, frame, description, facts)
            for node in (await frame.locator("h1,h2").all())[:8]:
                description = await self._describe_control(node, frame, f"h{len(headings) + 1}")
                if description:
                    headings.append(description)
        png = await self._page.screenshot(scale="css", mask=masks, animations="disabled")
        if revision != self.revision:
            raise ResolutionError("OBSERVATION_CHANGED")
        viewport = self._page.viewport_size
        observation = Observation(session_id=self.session_id, page_id=self.page_id,
                           navigation_revision=revision, url=safe_url(self._page.url),
                           width=viewport["width"], height=viewport["height"], screenshot_png=png,
                           screenshot_sha256=hashlib.sha256(png).hexdigest(), controls=controls,
                           readables=readables, headings=headings)
        if register:
            self._latest_observation = observation.model_copy(deep=True)
        return observation

    async def _require_fresh_observation(self, observation_id: str) -> Observation:
        issued = self._latest_observation
        if issued is None or issued.id != observation_id:
            raise ResolutionError("STALE_OBSERVATION")
        if issued.navigation_revision != self.revision:
            raise ResolutionError("STALE_OBSERVATION")
        current = await self._capture_observation(register=False)
        if (current.screenshot_sha256 != issued.screenshot_sha256
                or current.controls != issued.controls
                or current.readables != issued.readables or current.headings != issued.headings
                or current.navigation_revision != issued.navigation_revision):
            raise ResolutionError("STALE_OBSERVATION")
        return issued

    async def ground_control(self, observation_id: str, control_id: str, bindings: Bindings | None = None) -> TargetSpec:
        """Resolve only an ID issued for this exact, still-current observation."""
        await self._require_fresh_observation(observation_id)
        captured = self._observed_controls.get(control_id)
        if captured is None:
            raise ResolutionError("UNKNOWN_CONTROL_ID")
        return await self._ground_captured_control(captured, bindings)

    async def ground_point(self, observation: Observation, x: int, y: int, bindings: Bindings | None = None) -> TargetSpec:
        """Exact live hit-testing fallback. No nearest-control or coordinate clicks."""
        if observation.session_id != self.session_id or observation.page_id != self.page_id:
            raise ResolutionError("WRONG_SESSION_OBSERVATION")
        await self._require_fresh_observation(observation.id)
        if not (0 <= x < observation.width and 0 <= y < observation.height):
            raise ResolutionError("POINT_OUTSIDE_VIEWPORT")
        handle = await self._page.evaluate_handle("""({x,y}) => {
            let doc=document, el=doc.elementFromPoint(x,y);
            while (el?.tagName === 'IFRAME') {
                const rect=el.getBoundingClientRect();
                x -= rect.left + el.clientLeft; y -= rect.top + el.clientTop;
                doc=el.contentDocument;
                if (!doc) return null;
                el=doc.elementFromPoint(x,y);
            }
            return el?.closest('a,button,input,td') || null;
        }""", {"x": x, "y": y})
        try:
            node = handle.as_element()
            if node is None:
                raise ResolutionError("POINT_NOT_UNIQUE_CONTROL")
            frame = await node.owner_frame()
            if frame is None:
                raise ResolutionError("FRAME_NOT_UNIQUE")
            description = await self._describe_control(node, frame, "coordinate")
            if description is None:
                raise ResolutionError("POINT_NOT_UNIQUE_CONTROL")
            facts = ActionFacts.model_validate(await node.evaluate(FACTS))
            return await self._ground_captured_control(_ObservedControl(node, frame, description, facts), bindings)
        finally:
            await handle.dispose()

    async def _ground_captured_control(self, captured: _ObservedControl, bindings: Bindings | None = None) -> TargetSpec:
        node, hit = captured.node, captured.control
        frame = await self._scope(hit.frames)
        if (frame != captured.frame or not await node.evaluate("el => el.isConnected")
                or await node.owner_frame() != frame):
            raise ResolutionError("CONTROL_IDENTITY_CHANGED")
        if await self._describe_control(node, frame, hit.control_id) != hit:
            raise ResolutionError("CONTROL_IDENTITY_CHANGED")
        if ActionFacts.model_validate(await node.evaluate(FACTS)) != captured.facts:
            raise ResolutionError("CONTROL_EFFECT_CHANGED")
        # Check occlusion in frame-local coordinates before deriving a stable locator.
        if not await node.evaluate("""el => {const r=el.getBoundingClientRect();
            const top=el.ownerDocument.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
            return top === el || el.contains(top);} """):
            raise ResolutionError("CONTROL_OCCLUDED")
        bindings = bindings or Bindings()

        def observed_binding(text):
            matches = [InputRef(name=k) for k, v in bindings.inputs.items() if isinstance(v, str) and v == text]
            matches += [ExtractedRef(name=k) for k, v in bindings.extracted.items() if isinstance(v, str) and v == text]
            if len(matches) > 1:
                raise ResolutionError("AMBIGUOUS_PARAMETER_PROVENANCE")
            return matches[0] if matches else LiteralValue(value=text)

        if hit.tag == "td":
            candidate = StructuralLocator(tag="td", adjacent_label=observed_binding(hit.label))
        elif hit.tag == "input":
            name = await node.get_attribute("name")
            candidate = StructuralLocator(tag="input", name=name) if name else StructuralLocator(tag="input")
        else:
            candidate = RoleLocator(role="link" if hit.tag == "a" else "button", name=observed_binding(hit.label or hit.text))
            row = await node.evaluate("el => [...(el.closest('tr')?.cells || [])].map(c => c.innerText.trim())")
            # Parameterize an actually observed identifying cell, not a substring
            # guessed from a URL or model transcript.
            for text in row:
                binding = observed_binding(text)
                if isinstance(binding, (InputRef, ExtractedRef)):
                    candidate = RowLocator(row_text=binding, role=candidate.role, name=candidate.name)
                    break
        target = TargetSpec(frames=hit.frames, candidates=[candidate], expected_tag=hit.tag,
                            rationale="Grounded from the observation to the same unique visible live control.")
        resolved = await self._resolve(target, bindings)
        if not await resolved.evaluate("(el, captured) => el === captured", node):
            raise ResolutionError("CONTROL_IDENTITY_CHANGED")
        return target

    async def flush_activity(self):
        try:
            for frame in self._page.frames:
                await frame.evaluate("async () => { await Promise.allSettled([...(window.__interfacePending || [])]); }")
        except BrowserError:
            raise SurfaceError("SESSION_CLOSED" if self._page.is_closed() else "BROWSER_ERROR") from None


@asynccontextmanager
async def open_browser(policy, control, evidence, *, headed=False):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(sys.prefix) / "browsers"))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not headed)
        try:
            context = await browser.new_context(viewport={"width": 1100, "height": 800}, device_scale_factor=1,
                                                service_workers="block", accept_downloads=False)
            page = await context.new_page()
            page.set_default_timeout(2500)
            surface = BrowserSurface(page, policy, control, evidence)
            await surface.install()
            evidence.emit("session_opened", session_id=surface.session_id, page_id=surface.page_id, headed=headed)
            yield surface
        finally:
            await browser.close()
