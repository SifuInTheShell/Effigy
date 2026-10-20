"""The first run's machine check and the models folder (IMPLEMENTATION.md M35c).

check(): what this computer has against what the app needs (PLAN §1: every default preset on a 16 GB card,
Faithful ~24 GB of system RAM), each with what to do about it. folder(): where the model files go, with the
drives that could hold them; choose_folder() switches to another one, or moves the files there (a job,
server/install.py).
"""

import functools
import os
import socket
import subprocess
from pathlib import Path

from server import gpu_amd, install, library, models, presets, settings, storage
from server.library import ApiError

MIN_DRIVER = 575  # SETUP.md step 1: CUDA 12.8 (torch cu128) on Blackwell
RAM_GOOD, RAM_LOW = 30, 15  # GB: Faithful needs ~24 GB of system RAM (docs/bench/phase0.md)
LONG_PATHS = (
    'New-ItemProperty -Path "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem" '
    "-Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force"
)


# M46b: our CUDA wheels (cumesh, flex_gemm, o_voxel; .github/workflows/wheels.yml) are built for 8.9 and 12.0 with no
# PTX, so an RTX 30 card (8.6: the 3090, A5000, A6000) cannot run the steps that load them. The owner, 2026-10-13: no
# 8.6 build; say so instead of failing in the first generation.
MIN_SM = 8.9
# not `depth`: its FlexGEMM is the pure-Triton build in .venv-moge, which runs on 8.6
SM_STEPS = ("trellis2", "clean", "remesh", "uv", "bake")
UNSUPPORTED = "not supported: the 3D models need an RTX 40 or 50 series card"


@functools.cache  # ponytail: read once per app run; a card swapped in needs a restart of the app
def sm() -> float | None:
    """The compute capability (8.9 an RTX 40, 12.0 an RTX 50) of the GPU the steps run on, None if unknown (an old
    driver without the field: no check)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,compute_cap", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout
        rows = [[float(x) for x in line.split(",")] for line in out.splitlines() if line.strip()]
        return max(rows)[1]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        # no compute capability on AMD, so no gate (the HIP wheels are built for gfx1201 only)
        return None


def _gpu() -> tuple[str, float, str] | None:
    """(name, VRAM in GB, driver version) of the NVIDIA GPU with the most memory, the one the steps run on
    (server/jobs._pin_gpu), or None (none, or no driver)."""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout
        rows = [[x.strip() for x in line.split(",")] for line in out.splitlines() if line.strip()]
        name, mib, driver = max(rows, key=lambda r: float(r[1]))
        return name, float(mib) / 1024, driver
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        if g := gpu_amd.adapter():  # AMD: no nvidia-smi, an AMD card
            return g["name"], g["total_mib"] / 1024, g["driver"]
        return None


def _ram_gb() -> float:
    return presets.ram_gb()[0]


def _long_paths() -> bool | None:
    """Windows' LongPathsEnabled (PyTorch's folders are deep); None elsewhere."""
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as k:
            return winreg.QueryValueEx(k, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


def check() -> list[dict]:
    """[{id, label, level: ok | warn | bad, detail}]: bad = the app cannot run; warn = it runs, with less."""
    out = []

    def item(key, level, label, detail=""):
        out.append({"id": key, "level": level, "label": label, "detail": detail})

    gpu = _gpu()
    if not gpu:
        item(
            "gpu",
            "bad",
            "No NVIDIA graphics card found",
            "The app needs an NVIDIA RTX card with 16 GB of video memory and its driver (nvidia.com/drivers).",
        )
    else:
        name, vram, driver = gpu
        label, budget = f"{name}, {vram:.0f} GB of video memory", presets.budget_gb(vram)
        # M45: what this card can never run, the jobs refuse it; the 3D model by its presets (M46b)
        big = [
            {"step": "trellis2", "param": "preset", "value": n, "need_gb": gb}
            for n in presets.GENERATE
            if (gb := presets.need_gb("trellis2", presets.PRESETS["trellis2"][n])) > budget
        ] + [
            {"step": step, "param": key, "value": value, "need_gb": gb}
            for step in presets.VRAM_GB
            if step != "trellis2"
            for key, by in [presets.variants(step)]
            for value, gb in by.items()
            if gb > budget
        ]
        for b in big:  # M46c: the download group that variant needs, for Setup's card
            for group, (step, params) in presets.GROUP_NEED.items():
                if b["step"] == step and b["param"] and params.get(b["param"]) == b["value"]:
                    b["group"] = group
        # what the reference card runs and this one cannot: a warning; more than that is for bigger cards only
        core = [b for b in big if b["need_gb"] <= presets.budget_gb(presets.REFERENCE_GB)]
        old = (cap := sm()) is not None and cap < MIN_SM
        allows = (
            f"This card allows {budget:.1f} GB per step "
            f"({presets.reserve_gb(vram):.1f} GB stay for Windows and the display)"
        )
        if old:  # M46b: the 3D models cannot run at all; the rest can
            item(
                "gpu",
                "warn",
                label,
                f"No 3D model can be made on this card: the 3D model steps are {UNSUPPORTED} (compute "
                f"capability 8.9 or newer; this card has {cap:g}). Pictures, pixel art, motion and the rest run.",
            )
            big = [b for b in big if b["step"] != "trellis2"] + [
                {
                    "step": "trellis2",
                    "param": "preset",
                    "value": n,
                    "need_gb": None,
                    "reason": UNSUPPORTED,
                }
                for n in presets.GENERATE
            ]
        elif not core:
            item("gpu", "ok", label)
        elif presets.lighter("trellis2", budget) is None:
            least = min(presets.variants("trellis2")[1].values())
            item("gpu", "bad", label, f"The 3D models need about {least:.1f} GB. {allows}.")
        else:
            names = ", ".join(
                f"{b['step']}{' ' + b['value'] if b['value'] else ''} ({b['need_gb']:.1f} GB)"
                for b in core
            )
            fast = (
                " For the 3D model use Fast." if any(b["step"] == "trellis2" for b in core) else ""
            )
            item(
                "gpu",
                "warn",
                label,
                f"{allows}. Too big for it, so the app refuses: {names}.{fast}",
            )
        out[-1] |= {"budget_gb": round(budget, 1), "too_big": big}
        try:  # AMD: MIN_DRIVER is NVIDIA's numbering; any AMD driver HIP runs on is fine
            new = gpu_amd.active() or int(driver.split(".")[0]) >= MIN_DRIVER
        except ValueError:
            new = False
        if new:
            item("driver", "ok", f"Driver {driver}")
        else:
            item(
                "driver",
                "bad",
                f"Driver {driver}",
                f"Version {MIN_DRIVER} or newer is needed: NVIDIA App → Drivers, or nvidia.com/drivers.",
            )
    ram = _ram_gb()
    if ram >= RAM_GOOD:
        item("ram", "ok", f"{ram:.0f} GB of memory")
    else:
        item(
            "ram",
            "warn" if ram >= RAM_LOW else "bad",
            f"{ram:.0f} GB of memory",
            "Faithful needs about 24 GB: use the 3D model Fast, close other programs while generating.",
        )
    if (lp := _long_paths()) is not None:
        if lp:
            item("long_paths", "ok", "Long file paths allowed")
        else:
            item(
                "long_paths",
                "warn",
                "Long file paths are not allowed",
                f"Some downloads may fail. In PowerShell as administrator, once: {LONG_PATHS}",
            )
    return out


def folder() -> dict:
    """The models folder, its drive's space, and the drives that could hold the models instead."""
    here = models.root()
    choices = {}
    for p in [here, *_suggestions()]:
        d = storage._drive(p)
        try:
            u = storage.shutil.disk_usage(d)
        except OSError:
            continue
        key = os.path.normcase(os.path.abspath(p))
        if key not in choices and (
            p == here or u.total >= 64 * 10**9
        ):  # not a stick or a card reader
            choices[key] = {
                "path": str(Path(os.path.abspath(p))),
                "drive": d,
                "free": u.free,
                "total": u.total,
            }
    have = sum(e["bytes"] for e in models.listing())
    return {
        "path": str(Path(os.path.abspath(here))),
        "source": settings.models_source(),
        "bytes": have,
        "choices": list(choices.values()),
    }


def _suggestions() -> list[Path]:
    if os.name == "nt":
        app = storage._drive(storage.ROOT)
        return [
            storage.ROOT / "models" if d == app else Path(d) / "Effigy-models"
            for d in os.listdrives()
        ]
    return [storage.ROOT / "models", Path.home() / "Effigy-models"]


def choose_folder(path: str) -> dict:
    """Switch the models folder to `path`, or move the model files there (a job) when the current folder has
    some and `path` has none. `path` must be empty, new, or a models folder already (another install's)."""
    if settings.models_source() == "env":
        raise ApiError(409, "env", "EFFIGY_MODELS sets the models folder: change it there")
    new = Path(path.strip())
    if not path.strip() or not new.is_absolute():
        raise ApiError(400, "bad_params", "choose a full path, e.g. E:\\Effigy-models")
    new = Path(os.path.abspath(new))
    if new == Path(new.anchor):
        raise ApiError(400, "bad_params", f"choose a folder on {new}, e.g. {new}Effigy-models")
    old = Path(os.path.abspath(models.root()))
    if os.path.normcase(new) == os.path.normcase(old):
        return {"path": str(new), "job_id": None}
    lib = Path(os.path.abspath(library.root()))
    for a, b in ((new, lib), (lib, new), (new, old), (old, new)):
        if a.is_relative_to(b):
            raise ApiError(400, "bad_params", f"{new} overlaps {b}: choose a folder of its own")
    if models._downloading():
        raise ApiError(409, "busy", "an install is running; change the folder after it ends")
    theirs = _has_models(new)
    if new.exists() and any(new.iterdir()) and not theirs:
        raise ApiError(409, "not_empty", f"{new} has other files: choose an empty or a new folder")
    try:
        new.mkdir(parents=True, exist_ok=True)
        probe = new / ".effigy-write-test"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        raise ApiError(400, "not_writable", f"cannot write to {new}: {e}") from e
    if theirs or not _has_models(old):  # nothing to carry over: just use it
        settings.write(models=str(new))
        storage._last = None  # the space view measures the new place
        left = (
            str(old) if theirs and _has_models(old) else None
        )  # another install's folder: ours stays
        return {"path": str(new), "job_id": None, "left": left}
    if storage._drive(new) != storage._drive(old):  # a copy: it must fit whole before it starts
        need = sum(  # links not followed, as the copy does not
            f.stat().st_size
            for d, _, names in storage._walk(old)
            for f in (Path(d, n) for n in names)
        )
        free = storage.shutil.disk_usage(storage._drive(new)).free
        if free < need + 2**30:
            raise ApiError(
                507,
                "no_space",
                f"the models need {need / 1e9:.1f} GB, {free / 1e9:.1f} GB are free on {storage._drive(new)}",
            )
    return {"path": str(new), "job_id": install.move_models(old, new).id}


def first_run() -> bool:
    """Nothing installed, and the user has neither started an install from the guided setup nor skipped it:
    the app opens the guided setup by itself."""
    if settings.read().get("welcomed"):
        return False
    return not any(r["installed"] or r["partial"] for r in models.listing())


def _has_models(p: Path) -> bool:
    """Any file of a pinned entry there (a models folder, ours or another install's)."""
    return p.is_dir() and any((p / e["where"]).exists() for e in models.entries())


def port_free(port: int) -> bool:
    """Nobody answers on 127.0.0.1:`port` and this process can bind it (a listener on 0.0.0.0 may still let
    the bind through on Windows, hence the connect first)."""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
        return False
    except OSError:
        pass
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def port_info(running: int) -> dict:
    """The port this app runs on and the one it starts on next (EFFIGY_PORT, else Setup's choice, else 8000)."""
    return {
        "running": running,
        "next": settings.port(),
        "source": settings.port_source(),
        "default": settings.PORT,
    }


def choose_port(port: int, running: int) -> dict:
    """The port the app starts on next, saved; one another program holds is refused (`running` is this app's)."""
    if settings.port_source() == "env":
        raise ApiError(409, "env", "EFFIGY_PORT sets the port: change it there")
    if not 1024 <= port <= 65535:
        raise ApiError(400, "bad_params", "a port from 1024 to 65535")
    if port != running and not port_free(port):
        raise ApiError(409, "in_use", f"another program uses port {port}: choose another")
    settings.write(port=port)
    return port_info(running)
