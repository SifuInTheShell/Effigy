"""Windows portable zip (IMPLEMENTATION.md M15, ARCHITECTURE §8): unzip, double-click run.bat.

    python tools/package.py      # → dist/Effigy-<git describe>-win64.zip (stdlib only)

The zip holds the tracked files the app runs (git ls-files, so untracked junk can't leak in), the built
UI (ui/dist: built here when it's missing or older than ui/src, tools/ui_stale.py, so users need no
Node) and the pinned uv.exe + its licence. Python, the venvs, Blender and the models are downloaded
by run.bat on first run, into the app folder.
"""

import hashlib
import io
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# top-level repo entries a user needs; the rest (tests, ui sources, CI) stays out
KEEP = {
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "run.bat",
    "requirements-ml-rocm.txt",  # run.bat on an AMD card
    ".mcp.json",
    "licenses.yaml",
    "LICENSE",
    "README.md",
    "SETUP.md",
    "MANUAL.md",
    "CHANGELOG.md",  # README links it
    "docs",  # README links docs/ too
    "wheels",
    "prompts",
    "tools",
    "server",
    "steps",
    "engine",
    "blender_ops",
    "mcp",
}
GOLDEN = "tests/fixtures/golden/"  # the bench's prompts (tools/bench.py --make-golden), run on the laptop
UV = "https://github.com/astral-sh/uv/releases/download/0.8.17/uv-x86_64-pc-windows-msvc.zip"
# = the release's uv-x86_64-pc-windows-msvc.zip.sha256
UV_SHA = "0d051779fbcb173b183efeae1c3e96148764fd82709bbbf0966df3efe48b67c5"
UV_LICENSE = "https://raw.githubusercontent.com/astral-sh/uv/0.8.17/LICENSE-MIT"
UV_LICENSE_SHA = "860e3d7a86b84e6a7012c7a635fc64df475cebc6cce34dfeb73a5982ec58176c"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def manifest() -> list[str]:
    """Repo-relative paths that go into the zip (besides uv.exe and its licence)."""
    files = [
        f
        for f in git("ls-files", "-z").split("\0")
        if f.split("/")[0] in KEEP or f.startswith(GOLDEN)
    ]
    dist = ROOT / "ui" / "dist"
    return sorted(files + [p.relative_to(ROOT).as_posix() for p in dist.rglob("*") if p.is_file()])


def fetch(url: str, sha: str) -> bytes:
    with urllib.request.urlopen(url) as r:
        data = r.read()
    if (got := hashlib.sha256(data).hexdigest()) != sha:
        raise SystemExit(f"{url}: SHA-256 {got} != pinned {sha}")
    return data


def main() -> Path:
    stale = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "ui_stale.py")], check=False
    ).returncode
    if stale:
        subprocess.run(
            ("npm ci && " if stale == 2 else "") + "npm run build",
            shell=True,
            cwd=ROOT / "ui",
            check=True,
        )
    uv = zipfile.ZipFile(io.BytesIO(fetch(UV, UV_SHA))).read("uv.exe")
    version = git("describe", "--tags", "--always", "--dirty").strip()
    out = ROOT / "dist" / f"Effigy-{version}-win64.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in manifest():
            z.write(ROOT / f, f"Effigy/{f}")
        z.writestr("Effigy/uv.exe", uv)
        z.writestr("Effigy/uv-LICENSE-MIT.txt", fetch(UV_LICENSE, UV_LICENSE_SHA))
        z.writestr("Effigy/VERSION", version + "\n")  # no .git in the zip: bench.py reads this
    return out


if __name__ == "__main__":
    print(main())
