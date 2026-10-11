"""Compile-path hardening for the Typst engine (FIX-4/5/6/7): wall-clock
timeout, fail-closed package rejection, per-render root scoping, and located
diagnostics. All exercise the real out-of-process compiler."""

import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.parse

import pytest

from app.services import pdf_render, typst_compiler

pytest.importorskip("typst")


def test_source_imports_package_is_comment_and_string_aware():
    assert typst_compiler.source_imports_package('#import "@preview/cetz:0.2.0"')
    assert typst_compiler.source_imports_package('import "@local/x:1.0.0": *')
    # only inside a comment -> not an import
    assert not typst_compiler.source_imports_package('// #import "@preview/cetz:0.2.0"\n= hi')
    assert not typst_compiler.source_imports_package('/* import "@preview/x:1.0" */\n= hi')
    # a plain file import is not a package import
    assert not typst_compiler.source_imports_package('#import "header.typ": *')


def test_typst_rejects_remote_package_import_fast(tmp_path):
    """FIX-5: a source importing a package fails closed (RuntimeError) BEFORE any
    compile/network fetch. The reject is local, so it returns near-instantly."""
    src = '#import "@preview/cetz:0.2.0"\n= hi\n'
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="package"):
        pdf_render.compile_typst_pdf(src, tmp_path, stem="pkg", sys_inputs={})
    assert time.monotonic() - t0 < 2.0  # no network round trip


def test_typst_package_mention_in_comment_still_compiles(tmp_path):
    """A @preview reference inside a comment must NOT trip the gate."""
    src = '// see #import "@preview/cetz:0.2.0" for ideas\n= Hello\n'
    pdf = pdf_render.compile_typst_pdf(src, tmp_path, stem="ok", sys_inputs={})
    assert pdf.exists()


def test_typst_compile_times_out(tmp_path, monkeypatch):
    """FIX-4: a pathological compile is bounded and killed, not left to hang."""
    monkeypatch.setattr(typst_compiler, "TYPST_COMPILE_TIMEOUT_S", 2.0)
    slow = "#for i in range(2000000) [x ]\n"  # >> any few-second bound
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="time limit|terminated"):
        pdf_render.compile_typst_pdf(slow, tmp_path, stem="slow", sys_inputs={})
    elapsed = time.monotonic() - t0
    assert elapsed < 20, f"did not terminate near the bound (took {elapsed:.1f}s)"


def test_typst_cannot_read_sibling_render_source(tmp_path):
    """FIX-6: the render root is scoped to a staging dir holding only this
    render's input, so a template cannot read a sibling file in the output dir
    (base resumes share one tex/ dir)."""
    (tmp_path / "secret.typ").write_text("SECRET DATA", encoding="utf-8")
    src = '#let s = read("secret.typ")\n#s\n'
    with pytest.raises(RuntimeError, match="typst failed"):
        pdf_render.compile_typst_pdf(src, tmp_path, stem="leak", sys_inputs={})


def test_typst_error_message_carries_location(tmp_path):
    """FIX-7: the RuntimeError surfaces the located diagnostic (file:line:col),
    not just the bare message."""
    src = "#let x = 1\n#bad_expr(\n"  # unclosed delimiter at 2:9
    with pytest.raises(RuntimeError) as ei:
        pdf_render.compile_typst_pdf(src, tmp_path, stem="syn", sys_inputs={})
    msg = str(ei.value)
    assert "typst failed" in msg
    assert re.search(r":\d+:\d+", msg), f"no file:line:col in message: {msg!r}"


# A computed import path walks past the regex gate: no string literal starts
# with "@". The network cut in the compile child is what stops the download.
_COMPUTED_PACKAGE_IMPORT = (
    '#let p = "@pre" + "view/fontawesome:0.6.0"\n'
    "#import p: fa-icon\n"
    "= hi\n"
)


def test_the_regex_gate_misses_a_computed_package_import():
    """Pins WHY the network cut exists: the regex is not the control."""
    assert not typst_compiler.source_imports_package(_COMPUTED_PACKAGE_IMPORT)


def test_a_computed_package_import_cannot_download(tmp_path):
    """The regex gate misses this source, so the compile reaches the package
    resolver; with the proxy variables pointed at a dead port the download
    fails and nothing lands in the package cache. Offline-deterministic: the
    request never leaves loopback, whatever the machine's network is doing."""
    src = tmp_path / "bad.typ"
    src.write_text(_COMPUTED_PACKAGE_IMPORT, encoding="utf-8")
    cache = tmp_path / "pkgcache"
    cache.mkdir()
    with pytest.raises(RuntimeError, match="typst failed") as ei:
        typst_compiler.run_typst_compile(
            input=str(src),
            output=str(tmp_path / "bad.pdf"),
            root=str(tmp_path),
            font_paths=[],
            sys_inputs={},
            package_dir=str(cache),
            timeout=30,
        )
    # "Connection Failed" proves the request went to the dead proxy and was
    # refused; an offline machine with no proxy would say "Dns Failed" instead.
    assert "failed to download package" in str(ei.value).lower()
    assert "connection failed" in str(ei.value).lower()
    assert list(cache.rglob("*")) == [], "a package was fetched into the cache"
    assert not (tmp_path / "bad.pdf").exists()


def test_a_computed_package_import_fails_through_compile_typst_pdf(tmp_path):
    with pytest.raises(RuntimeError, match="typst failed"):
        pdf_render.compile_typst_pdf(
            _COMPUTED_PACKAGE_IMPORT, tmp_path, stem="computed", sys_inputs={}
        )
    assert not (tmp_path / "computed.pdf").exists()


def test_cut_network_env_sets_every_proxy_variable_and_drops_no_proxy(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "packages.typst.org")
    monkeypatch.setenv("no_proxy", "packages.typst.org")
    for name in typst_compiler._PROXY_VARS:
        monkeypatch.setenv(name, "http://elsewhere.invalid:3128")

    typst_compiler.cut_network_env()

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                 "http_proxy", "https_proxy", "all_proxy"):
        assert os.environ[name] == typst_compiler._DEAD_PROXY
    assert "NO_PROXY" not in os.environ
    assert "no_proxy" not in os.environ


_CHILD_PROBE = """
import json, os, sys
from app.services import typst_compiler

class Conn:
    def send(self, msg): pass
    def close(self): pass

# A bad input makes typst.compile raise at once; the env cut has already run.
typst_compiler._worker(Conn(), {"input": "/nonexistent/x.typ", "output": "/dev/null"})
print(json.dumps({k: os.environ.get(k) for k in
    ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy",
     "all_proxy", "NO_PROXY", "no_proxy"]}))
"""


def test_worker_cuts_the_network_in_the_child_only(monkeypatch):
    """`_worker` itself applies the cut (a child sees dead proxies and no
    NO_PROXY, even if the parent had them), and the parent's environment is
    not touched by anything the child does."""
    monkeypatch.setenv("NO_PROXY", "packages.typst.org")
    monkeypatch.setenv("HTTPS_PROXY", "http://parent.invalid:3128")

    out = subprocess.run(
        [sys.executable, "-c", _CHILD_PROBE],
        capture_output=True, text=True, check=True, timeout=60,
        cwd=pathlib.Path(typst_compiler.__file__).parents[2],
    )
    seen = json.loads(out.stdout.strip().splitlines()[-1])

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                 "http_proxy", "https_proxy", "all_proxy"):
        assert seen[name] == typst_compiler._DEAD_PROXY, name
    assert seen["NO_PROXY"] is None and seen["no_proxy"] is None


def test_the_dead_proxy_is_a_valid_url_with_an_in_range_port():
    """An unparsable proxy value (``:99999``) is silently ignored by the
    client, which then goes direct: the cut would fail OPEN. ``.port`` raises
    ValueError for an out-of-range port."""
    parsed = urllib.parse.urlparse(typst_compiler._DEAD_PROXY)
    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port is not None and 0 <= parsed.port <= 65535


def test_a_real_compile_leaves_the_parents_proxy_environment_alone(tmp_path, monkeypatch):
    """The cut runs in the spawned child; run the real spawn path and check the
    app process's own environment afterwards."""
    monkeypatch.setenv("NO_PROXY", "packages.typst.org")
    monkeypatch.setenv("HTTPS_PROXY", "http://parent.invalid:3128")
    before = dict(os.environ)
    src = tmp_path / "ok.typ"
    src.write_text("= hi\n", encoding="utf-8")
    pkg = tmp_path / "pkgs"
    pkg.mkdir()

    typst_compiler.run_typst_compile(
        input=str(src), output=str(tmp_path / "ok.pdf"), root=str(tmp_path),
        font_paths=[], sys_inputs={}, package_dir=str(pkg), timeout=60,
    )

    assert (tmp_path / "ok.pdf").exists()
    assert dict(os.environ) == before
