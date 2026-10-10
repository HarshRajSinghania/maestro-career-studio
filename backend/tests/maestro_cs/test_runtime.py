import sys
from pathlib import Path

from maestro_cs import runtime as R


def test_node_path_honours_override(tmp_path):
    node = tmp_path / "node"
    assert R.node_path(environ={"MAESTRO_NODE": str(node)}) == node


def test_node_path_default_is_inside_the_wheel_package():
    posix = R.node_path("linux", {})
    assert posix.name == "node" and posix.parent.name == "bin"
    assert "nodejs_wheel" in posix.parts
    win = R.node_path("win32", {})
    assert win.name == "node.exe" and "nodejs_wheel" in win.parts


def test_the_default_node_exists_in_this_environment():
    # nodejs-wheel-binaries is a core dependency of the distribution.
    if sys.platform == "win32":
        return
    assert R.node_path().is_file()


def test_web_dir_override_and_default(tmp_path):
    assert R.web_dir({"MAESTRO_WEB_DIR": str(tmp_path)}) == tmp_path
    default = R.web_dir({})
    assert default.name == "web" and default.parent.name == "maestro_cs"


def test_web_dir_ok_needs_server_js(tmp_path):
    env = {"MAESTRO_WEB_DIR": str(tmp_path)}
    assert not R.web_dir_ok(env)
    (tmp_path / "server.js").write_text("//", encoding="utf-8")
    assert R.web_dir_ok(env)


def test_demo_resume_falls_back_to_the_checkout():
    path = R.demo_resume()
    assert path is not None and path.name == "example.json" and path.is_file()


def test_shim_path_prefers_a_launcher_inside_this_venv(tmp_path):
    inside = tmp_path / "venv" / "bin" / "maestro"
    inside.parent.mkdir(parents=True)
    inside.write_text("", encoding="utf-8")
    got = R.shim_path("linux", which=lambda _n: str(inside), prefix=str(tmp_path / "venv"))
    assert got == inside and got.is_absolute()


def test_shim_path_ignores_a_launcher_from_another_environment(tmp_path):
    other = tmp_path / "other" / "bin" / "maestro"
    got = R.shim_path("linux", which=lambda _n: str(other), prefix=str(tmp_path / "venv"))
    assert got == tmp_path / "venv" / "bin" / "maestro"
    got = R.shim_path("linux", which=lambda _n: None, prefix=str(tmp_path / "venv"))
    assert got == tmp_path / "venv" / "bin" / "maestro"


def test_shim_path_windows_layout(tmp_path):
    got = R.shim_path("win32", which=lambda _n: None, prefix=str(tmp_path / "venv"))
    assert got == tmp_path / "venv" / "Scripts" / "maestro.exe"


def test_venv_python(tmp_path):
    assert R.venv_python() == Path(sys.executable)
    exe = "C:/v/Scripts/python.exe"
    assert R.venv_python("win32", windowless=True, executable=exe) == Path("C:/v/Scripts/pythonw.exe")
    assert R.venv_python("win32", executable=exe) == Path(exe)
    assert R.venv_python("linux", windowless=True, executable="/v/bin/python") == Path("/v/bin/python")
