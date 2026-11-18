"""Windows integration checks for run.bat using an isolated copy and stub uv."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="run.bat behavior requires Windows cmd.exe"
)

ROOT = Path(__file__).resolve().parents[1]


def launcher_copy(tmp_path: Path, *, fail: str = "", gpu: str = "nvidia"):
    app = tmp_path / "Effigy 日本語 & (test)! with spaces"
    bin_dir = app / "test & tools"
    bin_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "run.bat", app / "run.bat")
    (app / "VERSION").write_text("v-test\n", encoding="ascii")
    trace = app / "uv-trace.txt"
    ready = app / "server-ready.txt"
    stop = app / "stop-server.txt"
    stub = bin_dir / "uv.cmd"
    stub.write_text(
        """@echo off
>>"%EFFIGY_UV_TRACE%" echo %*
if /i "%~4"=="server.gpu_amd" if /i "%EFFIGY_TEST_GPU%"=="amd" exit /b 0
if /i "%~4"=="server.gpu_amd" exit /b 1
echo launcher-test-stdout
1>&2 echo launcher-test-stderr
if /i "%EFFIGY_TEST_FAIL%"=="base" if /i "%~1"=="sync" exit /b 37
if /i "%EFFIGY_TEST_FAIL%"=="ml" if /i "%~1"=="sync" if /i "%UV_PROJECT_ENVIRONMENT%"==".venv-ml" exit /b 43
if /i "%EFFIGY_TEST_FAIL%"=="blender" if /i "%~4"=="server.blender_install" exit /b 41
if /i "%~4"=="server" goto server
exit /b 0
:server
>"%EFFIGY_SERVER_READY%" echo ready
:server_wait
if exist "%EFFIGY_SERVER_STOP%" exit /b 0
timeout /t 1 /nobreak >nul
goto server_wait
""",
        encoding="ascii",
    )
    env = os.environ.copy()
    system32 = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32")
    env.update(
        {
            "PATH": f"{bin_dir};{system32}",
            "EFFIGY_NO_PAUSE": "1",
            "EFFIGY_TEST_FAIL": fail,
            "EFFIGY_TEST_GPU": gpu,
            "EFFIGY_UV_TRACE": str(trace),
            "EFFIGY_SERVER_READY": str(ready),
            "EFFIGY_SERVER_STOP": str(stop),
        }
    )
    return app, env, trace, ready, stop


def start_batch(app: Path, env: dict[str, str]):
    command = f'cmd.exe /d /s /c ""{app / "run.bat"}""'
    return subprocess.Popen(
        command,
        cwd=Path.cwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=False,
    )


def run_batch(app: Path, env: dict[str, str], timeout: float = 15):
    process = start_batch(app, env)
    output, _ = process.communicate(timeout=timeout)
    return (
        process.returncode,
        output.decode(errors="replace"),
        (app / "run.log").read_text(encoding="utf-8", errors="replace"),
    )


def test_uv_discovery_failure_leaves_a_log_and_returns_nonzero(tmp_path):
    app, env, _, _, _ = launcher_copy(tmp_path)
    env["PATH"] = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32")

    code, output, log = run_batch(app, env)

    assert code != 0
    assert "Effigy could not finish starting" in output
    assert "Failed phase: uv discovery" in output
    assert "[phase] uv discovery" in log
    assert "[failure] Phase: uv discovery" in log
    assert "Version: v-test" in log


def test_base_sync_captures_output_phase_exit_code_and_hides_environment_secrets(tmp_path):
    app, env, _, _, _ = launcher_copy(tmp_path, fail="base")
    (app / ".git").mkdir()
    secret = "synthetic-launcher-secret-7f2c"
    env["HF_TOKEN"] = secret

    code, output, log = run_batch(app, env)

    assert code == 37
    assert "Failed phase: base Python environment" in output
    assert str(app / "run.log") in output
    assert "launcher-test-stdout" in log
    assert "launcher-test-stderr" in log
    assert "Startup mode: developer checkout" in log
    assert "[failure] Exit code: 37" in log
    assert secret not in output + log


def test_nested_nvidia_sync_failure_keeps_its_exit_code(tmp_path):
    app, env, _, _, _ = launcher_copy(tmp_path, fail="ml")

    code, output, log = run_batch(app, env)

    assert code == 43
    assert "Failed phase: NVIDIA ML environment" in output
    assert "[failure] Exit code: 43" in log
    assert "[phase] skinning environment" not in log


def test_later_blender_failure_names_phase_and_preserves_nvidia_branch(tmp_path):
    app, env, trace, _, _ = launcher_copy(tmp_path, fail="blender")

    code, output, log = run_batch(app, env)

    commands = trace.read_text(encoding="utf-8").splitlines()
    assert code == 41
    assert "Failed phase: Blender installation/check" in output
    assert "[phase] NVIDIA ML environment" in log
    assert "[phase] skinning environment" in log
    assert "[failure] Phase: Blender installation/check" in log
    assert any("--group ml" in command for command in commands)
    assert any("--group skin" in command for command in commands)
    assert "gltfpack installation/check" not in log


def test_amd_branch_keeps_rocm_sync_isolated(tmp_path):
    app, env, trace, _, _ = launcher_copy(tmp_path, fail="blender", gpu="amd")
    (app / "requirements-ml-rocm.txt").write_text("same\n", encoding="ascii")
    (app / ".venv-ml").mkdir()
    (app / ".venv-ml" / "rocm-installed.txt").write_text("same\n", encoding="ascii")

    code, output, log = run_batch(app, env)

    commands = trace.read_text(encoding="utf-8").splitlines()
    assert code == 41
    assert "Failed phase: Blender installation/check" in output
    assert "[phase] AMD ROCm environment" in log
    assert not any("--group ml" in command or "--group skin" in command for command in commands)


def test_successful_server_stays_foreground_without_pausing(tmp_path):
    app, env, _, ready, stop = launcher_copy(tmp_path)
    env.pop("EFFIGY_NO_PAUSE")
    process = start_batch(app, env)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready.exists() and process.poll() is None:
            time.sleep(0.05)
        assert ready.exists(), "launcher did not reach the server command"
        assert process.poll() is None, "launcher exited instead of keeping server foreground"
        log = (app / "run.log").read_text(encoding="utf-8", errors="replace")
        assert "[phase] Effigy server startup" in log
        stop.touch()
        process.communicate(timeout=8)
        assert process.returncode == 0
        assert "Effigy server process exited successfully" in (app / "run.log").read_text(
            encoding="utf-8", errors="replace"
        )
    finally:
        if process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def test_portable_mode_uses_bundled_uv(tmp_path):
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("no local uv.exe available to stand in for the bundled portable uv")
    app, env, _, _, _ = launcher_copy(tmp_path)
    shutil.copy2(uv, app / "uv.exe")
    env["PATH"] = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32")

    code, output, log = run_batch(app, env)

    assert code != 0
    assert "Startup mode: portable" in log
    assert "[phase] base Python environment" in log
    assert "Effigy could not finish starting" in output
