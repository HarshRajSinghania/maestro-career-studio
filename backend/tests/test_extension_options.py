"""Companion address settings and the store upload archive."""

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXTENSION = ROOT / "extension"
PACK_SCRIPT = ROOT / "scripts/pack_extension.py"


@pytest.fixture
def pack_tree(tmp_path):
    assert PACK_SCRIPT.is_file(), "extension pack script is missing"
    (tmp_path / "scripts").mkdir()
    shutil.copy2(PACK_SCRIPT, tmp_path / "scripts/pack_extension.py")
    shutil.copytree(EXTENSION, tmp_path / "extension")
    (tmp_path / "backend").mkdir()
    shutil.copy2(ROOT / "backend/pyproject.toml", tmp_path / "backend/pyproject.toml")
    return tmp_path


def pack(root, *args):
    return subprocess.run(
        [sys.executable, str(root / "scripts/pack_extension.py"), *args],
        cwd=root / "backend", capture_output=True, text=True, check=False,
    )


def test_pack_refuses_version_mismatch_before_writing_zip(pack_tree):
    path = pack_tree / "extension/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["version"] = "99.0.0"
    path.write_text(json.dumps(manifest))
    done = pack(pack_tree)
    assert done.returncode != 0
    assert "version mismatch" in done.stderr.lower()
    assert not list(pack_tree.glob("dist/*.zip"))


@pytest.mark.parametrize("first_upload", [False, True])
def test_pack_key_changes_only_in_first_upload_archive(pack_tree, first_upload):
    path = pack_tree / "extension/manifest.json"
    original = path.read_bytes()
    manifest = json.loads(original)
    done = pack(pack_tree, *(["--first-upload"] if first_upload else []))
    assert done.returncode == 0, done.stderr
    archive = pack_tree / f"dist/maestro-cs-companion-{manifest['version']}.zip"
    with zipfile.ZipFile(archive) as zipped:
        bundled = json.loads(zipped.read("manifest.json"))
    assert ("key" in bundled) is not first_upload
    if not first_upload:
        assert bundled["key"] == manifest["key"]
    assert path.read_bytes() == original
    assert manifest["key"]


def test_pack_excludes_development_files_but_keeps_runtime_assets(pack_tree):
    excluded = {
        "README.md", "INTERNALS.md", "STORE_LISTING.md", ".slopconfig.json",
        ".slop-baseline.json", ".hidden", ".cache/leak.js", "tests/options.test.js",
        "shared/tests/helper.js", "shared/.hidden", "test_options.py", "options.test.js",
    }
    for relative in excluded:
        path = pack_tree / "extension" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("development only")
    done = pack(pack_tree)
    assert done.returncode == 0, done.stderr
    with zipfile.ZipFile(next(pack_tree.glob("dist/*.zip"))) as zipped:
        names = set(zipped.namelist())
        assert names.isdisjoint(excluded)
        assert {"manifest.json", "sw.js", "options.html", "options.js", "icons/128.png"} <= names
        manifest = json.loads(zipped.read("manifest.json"))
        for entry in manifest["content_scripts"]:
            assert set(entry["js"]) <= names
        assert manifest["side_panel"]["default_path"] in names


def test_options_page_registration_and_safe_source():
    manifest = json.loads((EXTENSION / "manifest.json").read_text())
    assert manifest.get("options_ui") == {"page": "options.html", "open_in_tab": False}
    assert set(manifest["permissions"]) == {
        "storage", "tabs", "webNavigation", "scripting", "sidePanel",
    }
    html = (EXTENSION / "options.html").read_text()
    source = (EXTENSION / "options.js").read_text()
    assert "innerHTML" not in html + source
    assert html.isascii() and source.isascii(), "options use words, never emoji or text glyphs"
    assert '<script src="options.js"></script>' in html
    assert "aria-live=\"polite\"" in html
    assert "backendUrl" in source and "appUrl" in source
    assert "localhost" not in source, "defaults must come from read_settings"
    assert "sw.js" not in source


_OPTIONS_DRIVER = r"""
const fs = require("node:fs");
const vm = require("node:vm");
const spec = JSON.parse(fs.readFileSync(0, "utf8"));
const elements = {};
for (const id of ["settings", "backendUrl", "appUrl", "save", "test", "note"]) {
  elements[id] = {
    value: "", textContent: "", disabled: true, listeners: {}, focused: false,
    addEventListener(type, handler) { this.listeners[type] = handler; },
    focus() { this.focused = true; },
  };
}
global.document = { getElementById: (id) => elements[id] };
const messages = [], writes = [], requests = [];
global.chrome = {
  runtime: { sendMessage: async (message) => {
    messages.push(message);
    if (spec.readReject) throw new Error("private runtime error");
    return spec.readRefused ? { ok: false, error: "private runtime error" }
      : { ok: true, data: spec.settings };
  } },
  storage: { sync: { set: async (values) => {
    if (spec.saveReject) throw new Error("private storage error");
    writes.push(values);
  } } },
};
global.fetch = async (url) => {
  requests.push(url);
  if (spec.fetchReject) throw new Error("private network error");
  return { ok: spec.healthOk !== false };
};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
(async () => {
  await new Promise(setImmediate);
  const initial = { backendUrl: elements.backendUrl.value, appUrl: elements.appUrl.value };
  for (const [key, value] of Object.entries(spec.edit || {})) elements[key].value = value;
  if (spec.action) {
    const [id, type] = spec.action === "save" ? ["settings", "submit"] : ["test", "click"];
    await elements[id].listeners[type]({ preventDefault() {} });
    await new Promise(setImmediate);
  }
  process.stdout.write(JSON.stringify({
    initial, messages, writes, requests, note: elements.note.textContent,
    disabled: { save: elements.save.disabled, test: elements.test.disabled },
  }));
})().catch((err) => { console.error(err); process.exitCode = 1; });
"""


def run_options(**overrides):
    path = EXTENSION / "options.js"
    assert path.is_file(), "options page script is missing"
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    spec = {
        "settings": {"backendUrl": "http://localhost:9123", "appUrl": "https://localhost:3123",
                     "telemetryEnabled": False, "fillMode": "rules"},
        **overrides,
    }
    done = subprocess.run([node, "-e", _OPTIONS_DRIVER, str(path)], input=json.dumps(spec),
                          capture_output=True, text=True, check=False, timeout=10)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_options_load_through_settings_message_and_save_only_address_keys():
    out = run_options(action="save", edit={"backendUrl": " https://localhost:9443/ "})
    assert out["messages"] == [{"type": "read_settings"}]
    assert out["initial"] == {
        "backendUrl": "http://localhost:9123", "appUrl": "https://localhost:3123",
    }
    assert out["writes"] == [{"backendUrl": "https://localhost:9443", "appUrl": "https://localhost:3123"}]
    assert out["note"] == "Saved."
    assert out["disabled"] == {"save": False, "test": False}


@pytest.mark.parametrize("address", [
    "", "localhost:8001", "ftp://localhost", "javascript:alert(1)", "http://",
    "http://localhost/api", "http://localhost/../", "http://localhost?", "http://localhost#",
    "http://user:password@localhost", "http://@localhost", "http://localhost\\api", "http://local host",
])
@pytest.mark.parametrize("key", ["backendUrl", "appUrl"])
def test_options_refuse_invalid_or_non_host_addresses(address, key):
    out = run_options(action="save", edit={key: address})
    assert out["writes"] == []
    assert "http:// or https://" in out["note"]


@pytest.mark.parametrize("address", ["http://localhost:8001", "https://example.com", "http://[::1]:8001/"])
def test_options_accept_http_hosts_and_ports(address):
    out = run_options(action="save", edit={"backendUrl": address})
    assert out["writes"][0]["backendUrl"] == address.rstrip("/")


@pytest.mark.parametrize("health", [{}, {"healthOk": False}, {"fetchReject": True}])
def test_connection_checks_current_unsaved_backend_address(health):
    out = run_options(action="test", edit={"backendUrl": "http://127.0.0.1:9001/"}, **health)
    assert out["requests"] == ["http://127.0.0.1:9001/health"]
    assert out["writes"] == []
    expected = "Connected." if not health else "Can't reach Maestro at http://127.0.0.1:9001."
    assert out["note"] == expected
    assert out["disabled"] == {"save": False, "test": False}


def test_connection_never_fetches_invalid_backend_address():
    out = run_options(action="test", edit={"backendUrl": "http://localhost/api"})
    assert out["requests"] == []
    assert "http:// or https://" in out["note"]


@pytest.mark.parametrize("failure", [{"readReject": True}, {"readRefused": True}])
def test_settings_read_failure_keeps_save_disabled(failure):
    out = run_options(**failure)
    assert out["writes"] == []
    assert out["disabled"] == {"save": True, "test": True}
    assert out["note"] == "Couldn't load settings. Reopen this page to try again."


def test_settings_save_failure_is_visible():
    out = run_options(action="save", saveReject=True)
    assert out["writes"] == []
    assert out["note"] == "Couldn't save settings. Try again."
    assert out["disabled"] == {"save": False, "test": False}
