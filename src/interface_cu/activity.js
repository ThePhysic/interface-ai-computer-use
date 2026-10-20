(() => {
  if (window.__interfaceCaptureInstalled) return;
  window.__interfaceCaptureInstalled = true;
  window.__interfacePending = new Set();
  for (const eventName of ['click', 'change', 'submit']) {
    document.addEventListener(eventName, event => {
      const target = event.target.closest('input,button,a,form,select,textarea');
      if (!target) return;
      // Never read value, key, textContent, clipboard, or form contents.
      const pending = window.__interfaceActivity({event: eventName, tag: target.tagName});
      window.__interfacePending.add(pending);
      pending.finally(() => window.__interfacePending.delete(pending));
    }, true);
  }
})();
