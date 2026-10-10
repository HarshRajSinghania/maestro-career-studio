/* Address defaults are read from the service worker's settings message. */
(() => {
  "use strict";

  const form = document.getElementById("settings");
  const backend = document.getElementById("backendUrl");
  const app = document.getElementById("appUrl");
  const save = document.getElementById("save");
  const test = document.getElementById("test");
  const note = document.getElementById("note");
  let ready = false;
  let busy = false;

  function address(input, label) {
    const value = input.value.trim();
    try {
      // Check the spelling before URL normalizes paths such as /../ away.
      if (!/^https?:\/\/[^/?#@\\\s]+\/?$/i.test(value)) throw new Error();
      const url = new URL(value);
      if (!url.hostname || url.username || url.password) throw new Error();
      return url.origin;
    } catch (_) {
      note.textContent = `${label}: use http:// or https:// and a host, with an optional port.`;
      input.focus();
      return null;
    }
  }

  function setBusy(value) {
    busy = value;
    save.disabled = !ready || busy;
    test.disabled = !ready || busy;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!ready || busy) return;
    const backendUrl = address(backend, "Backend address");
    if (!backendUrl) return;
    const appUrl = address(app, "App address");
    if (!appUrl) return;
    setBusy(true);
    try {
      await chrome.storage.sync.set({ backendUrl, appUrl });
      backend.value = backendUrl;
      app.value = appUrl;
      note.textContent = "Saved.";
    } catch (_) {
      note.textContent = "Couldn't save settings. Try again.";
    } finally {
      setBusy(false);
    }
  });

  test.addEventListener("click", async () => {
    if (!ready || busy) return;
    const backendUrl = address(backend, "Backend address");
    if (!backendUrl) return;
    setBusy(true);
    note.textContent = "Connecting...";
    try {
      const response = await fetch(`${backendUrl}/health`, { signal: AbortSignal.timeout(5000) });
      if (!response.ok) throw new Error();
      note.textContent = "Connected.";
    } catch (_) {
      note.textContent = `Can't reach Maestro at ${backendUrl}.`;
    } finally {
      setBusy(false);
    }
  });

  (async () => {
    try {
      const reply = await chrome.runtime.sendMessage({ type: "read_settings" });
      if (!reply?.ok) throw new Error();
      backend.value = reply.data.backendUrl;
      app.value = reply.data.appUrl;
      ready = true;
      setBusy(false);
    } catch (_) {
      note.textContent = "Couldn't load settings. Reopen this page to try again.";
    }
  })();
})();
