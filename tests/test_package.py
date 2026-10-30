"""tools/package.py file selection (M15): what a user needs goes into the zip, dev and runtime junk doesn't."""

from tools.package import manifest


def test_zip_manifest():
    files = manifest()
    for must in [
        "run.bat",
        "requirements-ml-rocm.txt",
        "pyproject.toml",
        "uv.lock",
        "licenses.yaml",
        "LICENSE",
        ".mcp.json",
        "SETUP.md",
        "MANUAL.md",
        "CHANGELOG.md",
    ]:
        assert must in files, must
    for folder in ["server", "steps", "engine", "blender_ops", "mcp", "prompts", "tools", "wheels"]:
        assert any(f.startswith(folder + "/") for f in files), folder
    assert any(f.endswith(".whl") for f in files)
    # .venv and __pycache__ exist on disk here (pytest runs in .venv and imports server/): untracked
    assert "tests/fixtures/golden/prompts.yaml" in files  # the bench runs from the zip
    files = [f for f in files if not f.startswith("tests/fixtures/golden/")]
    bad = ("tests/", ".github/", ".claude/", ".venv", "models/", "library/", "blender/")
    assert not [f for f in files if f.startswith(bad) or "__pycache__" in f or "node_modules" in f]
    assert not [f for f in files if f.startswith("ui/") and not f.startswith("ui/dist/")]


def test_ui_is_rebuilt_when_its_source_is_newer(tmp_path):
    """run.bat: 0 up to date, 1 rebuild (a pull changed ui/src), 2 npm ci first (no node_modules); the
    portable zip, the build without its source: up to date."""
    import os

    from tools.ui_stale import stale

    ui = tmp_path / "ui"
    for f, t in (
        ("src/main.tsx", 100),
        ("index.html", 100),
        ("package-lock.json", 100),
        ("node_modules/.package-lock.json", 100),
        ("dist/index.html", 200),
    ):
        (ui / f).parent.mkdir(parents=True, exist_ok=True)
        (ui / f).write_text("x")
        os.utime(ui / f, (t, t))
    assert stale(ui) == 0
    os.utime(ui / "src" / "main.tsx", (300, 300))
    assert stale(ui) == 1
    (ui / "node_modules" / ".package-lock.json").unlink()
    assert stale(ui) == 2
    for f in ("src/main.tsx", "index.html", "package-lock.json"):
        (ui / f).unlink()
    assert stale(ui) == 0
