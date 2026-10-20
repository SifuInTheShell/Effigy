"""Install and repair a feature (IMPLEMENTATION.md M35b): its Python environment and its model files, one job.

An install sets up the environment the feature's step runs in when it is missing or out of date (`uv sync
--frozen`, what run.bat runs for the core ones), then fetches the model group (`tools.download_models` in
.venv-ml) and checks every file against tools/models_manifest.json. Repair also test-imports the environment
(reinstalling its packages if that fails) and deletes the model files whose size or SHA-256 is wrong, so the
download fetches them again. Installs run one at a time, in the order they were asked for.
"""

import hashlib
import os
import shutil
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path

from server import gpu_amd, jobs, models, settings
from server.library import ApiError

ROOT = Path(__file__).resolve().parents[1]
# the environment each model group's step runs in; every other group's runs in .venv-ml
GROUP_VENV = {
    "skin": ".venv-skin",
    "kimodo": ".venv-kimodo",
    "parts": ".venv-parts",
    "mocap": ".venv-gemx",
    "layers": ".venv-moge",  # M55: MoGe-3's depth step
}
# what shows an environment works: its step's own packages import (GeoSAM2 installs as `sam2`)
IMPORTS = {
    ".venv-ml": "torch, diffusers, transformers, onnxruntime",
    ".venv-skin": "torch, skintokens",
    ".venv-kimodo": "torch, kimodo",
    ".venv-parts": "torch, sam2, cv2",
    ".venv-gemx": "torch, soma, warp, onnxruntime, cv2, av",
    ".venv-moge": "torch, moge, flex_gemm.nn",
}
# what you want to make (the first run, M35c): the model groups each needs, after PLAN §5's chains
PRESETS = [
    (
        "props",
        "Props and objects",
        "Pictures from a description, and 3D models from them or from your own pictures.",
        ["concepts", "faithful"],
    ),
    (
        "characters",
        "Game characters",
        "Props, plus humanoid characters: put in a standing pose, rigged, with ready-made animations.",
        ["concepts", "faithful", "texture", "pose", "canon-flux", "skin", "clips"],
    ),
    (
        "motion",
        "Characters with motion",
        "Game characters, plus new motions from a sentence or from a video of a person.",
        [
            "concepts",
            "faithful",
            "texture",
            "pose",
            "canon-flux",
            "skin",
            "clips",
            "kimodo",
            "mocap",
        ],
    ),
    (
        "everything",
        "Everything",
        "All of it, plus the Fast 3D model (less memory), the four-view model and Parts.",
        sorted(models.GROUPS),
    ),
]
_turn = threading.Lock()  # one install at a time; the others wait, queued
_asking = threading.Lock()
_syncing: set[str] = set()  # environments an install is changing right now


class _Failed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _uv_env(venv: str) -> dict:
    env = {k: v for k, v in jobs._env().items() if k != "VIRTUAL_ENV"}  # the backend's own .venv
    # GEM-X's SOMA-X comes from git: its LFS assets are model files (download_models FILES), not packages
    return env | {"UV_PROJECT_ENVIRONMENT": venv, "GIT_LFS_SKIP_SMUDGE": "1"}


def _sync_cmd(venv: str, *extra: str) -> list[str]:
    return [jobs.uv(), "sync", "--frozen", "--group", jobs.VENV_GROUP[venv], *extra]


def _rocm(venv: str) -> bool:
    """AMD: run.bat installs .venv-ml from requirements-ml-rocm.txt, not from uv.lock; a sync here would put
    CUDA torch back."""
    return venv == ".venv-ml" and gpu_amd.rocm_fits()


def venv_state(venv: str) -> str:
    """ok, missing, outdated (packages differ from uv.lock), installing, or unknown (no uv to ask)."""
    if venv in _syncing:
        return "installing"
    if not jobs.venv_python(venv).is_file():
        return "missing"
    if _rocm(venv):
        return "ok"
    try:  # 80 ms: compares the installed packages with the lock, changes nothing
        r = subprocess.run(
            _sync_cmd(venv, "--check"),
            cwd=ROOT,
            env=_uv_env(venv),
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return {0: "ok", 1: "outdated"}.get(r.returncode, "unknown")  # 2: uv failed to check


def environments() -> list[dict]:
    """Each feature environment, its state and the model groups whose steps run in it."""
    out = []
    for venv in jobs.VENV_GROUP:
        if venv == ".venv-hunyuan" and not (ROOT / venv).is_dir():
            continue  # the experimental engine is set up by hand only (MX1)
        groups = [g for g in sorted(models.GROUPS) if GROUP_VENV.get(g, ".venv-ml") == venv]
        out.append({"name": venv, "state": venv_state(venv), "groups": groups})
    return out


def too_big(group: str) -> str | None:
    """M46c: why this card cannot use `group` (presets.GROUP_NEED), or None. No NVIDIA card known: None (the
    machine check says so on its own)."""
    from server import machine  # here: machine imports this module
    from server import presets as needs  # this module has a presets() of its own

    if group not in needs.GROUP_NEED or not (gpu := machine._gpu()):
        return None
    step, params = needs.GROUP_NEED[group]
    need, budget = needs.need_gb(step, params), needs.budget_gb(gpu[1])
    if need is None or need <= budget:
        return None
    return f"{group} needs a card with more video memory: about {need:.1f} GB, this one allows {budget:.1f} GB"


def presets() -> list[dict]:
    """Each preset with what it puts on disk (shared entries once) and what is still to download; Everything
    leaves out what this card cannot use (M46c)."""
    out = []
    for key, name, what, groups in PRESETS:
        groups = [g for g in groups if not too_big(g)]
        rows = [models.state(e) for e in models.entries() if set(e["groups"]) & set(groups)]
        want, have = sum(r["want"] for r in rows), sum(r["have"] for r in rows)
        out.append(
            {
                "id": key,
                "name": name,
                "what": what,
                "groups": groups,
                "bytes": want,
                "missing": want - have,
            }
        )
    return out


def install(group: str, repair: bool = False) -> jobs.Job:
    if group not in models.GROUPS:
        raise ApiError(
            404, "not_found", f"no model group {group!r} (one of {sorted(models.GROUPS)})"
        )
    if why := too_big(group):  # M46c: a 6.7 GB download this card could never run
        raise ApiError(409, "oom", why)
    from server.storage import _drive  # the drive or mount point, even before the folder exists

    with _asking:  # two requests for one group at once: one job
        if any(
            j.step == "move_models" and j.status in ("queued", "running")
            for j in list(jobs.JOBS.values())
        ):
            raise ApiError(409, "busy", "the models are being moved; install after that")
        active = [
            j
            for j in list(jobs.JOBS.values())
            if j.step == "install" and j.status in ("queued", "running")
        ]
        if any(j.params["group"] == group for j in active):
            raise ApiError(409, "busy", f"{group} is already being installed")
        # the rest of this group and of the queued ones, and a GB to spare. ponytail: a repair's files
        # of the right size but the wrong hash count as there; they are few
        need = sum(models.missing_bytes(g) for g in {group, *(j.params["group"] for j in active)})
        drive = _drive(models.root())
        free = shutil.disk_usage(drive).free
        if need and free < need + 2**30:
            raise ApiError(
                507,
                "no_space",
                f"{group} needs {need / 1e9:.1f} GB more, {free / 1e9:.1f} GB are free on {drive}: "
                "free some space (Setup → Space on disk) or choose another models folder",
            )
        job = jobs.Job("", "install", None, {"group": group, "repair": repair})
        jobs.JOBS[job.id] = job
    threading.Thread(target=_work, args=(job,), daemon=True).start()
    return job


def _work(job: jobs.Job) -> None:
    group, repair = job.params["group"], job.params["repair"]
    with _turn:
        with jobs._state:
            if job.status == "cancelled":
                return
            job.status = "running"
        jobs.publish("job", job.public())
        try:
            own = GROUP_VENV.get(group, ".venv-ml")
            for venv in dict.fromkeys([".venv-ml", own]):  # .venv-ml runs the download itself
                _environment(job, venv, deep=repair and venv == own)
            if repair:
                _drop_damaged(job, group)
            _download(job, group)
            bad = [f"{e['where']}/{r}" for e in _entries(group) for r in models.state(e)["bad"]]
            if bad:
                raise _Failed("failed", f"{len(bad)} files still missing or damaged, e.g. {bad[0]}")
            end = "done", None
        except _Failed as e:
            end = "failed", {"code": e.code, "message": str(e)}
        except Exception as e:  # noqa: BLE001 - a bug fails the job visibly, never the thread silently
            end = "failed", {"code": "failed", "message": f"{type(e).__name__}: {e}"}
        with jobs._state:  # a cancel that came in the last moment stays a cancel
            if job.status != "cancelled":
                job.status, job.error = end
        jobs.publish("job", job.public())


def _entries(group: str) -> list[dict]:
    return [e for e in models.entries() if group in e["groups"]]


def _say(job: jobs.Job, p: float | None, text: str) -> None:
    ev = {
        "job_id": job.id,
        "asset_id": "",
        "step": f"install {job.params['group']}" if "group" in job.params else "move models",
        "p": p,
        "text": text,
    }
    jobs.publish("progress", ev)


def _log(job: jobs.Job, line: str) -> None:
    jobs.publish("log", {"job_id": job.id, "line": line})


def _cmd(job: jobs.Job, cmd: list[str], env: dict | None = None) -> tuple[int, list[str]]:
    """Run one command, every output line to the job's log; (exit code, the lines)."""
    if job.status == "cancelled":
        raise _Failed("cancelled", "cancelled")
    try:
        proc = job.proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            env=env or jobs._env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as e:
        raise _Failed("failed", f"could not start {Path(cmd[0]).name}: {e}") from e
    lines = []
    for line in proc.stdout:  # text mode splits tqdm's \r updates into lines too
        if line := line.strip():
            lines.append(line)
            _log(job, line)
    rc = proc.wait()
    job.proc = None  # a cancel between commands must not kill a reused process id
    if job.status == "cancelled":
        raise _Failed("cancelled", "cancelled")
    return rc, lines


@contextmanager
def _gpu(job: jobs.Job):
    """No step while an environment or model files change: every step runs under the GPU lock; the
    resident worker holds both open. ponytail: blocks every step for the whole sync, also of other
    environments (minutes when torch is downloaded for the first time); per-environment locks if that hurts."""
    if not jobs.GPU.acquire(blocking=False):
        _say(job, None, "Waiting for the running step to finish")
        jobs.GPU.acquire()
    try:
        if job.status == "cancelled":
            raise _Failed("cancelled", "cancelled")
        jobs.evict()
        yield
    finally:
        jobs.GPU.release()


def _environment(job: jobs.Job, venv: str, deep: bool) -> None:
    state = venv_state(venv)
    # unknown: no uv to ask, the environment is there
    if state in ("ok", "unknown") and not deep or _rocm(venv):
        return
    _say(job, None, f"Setting up its Python environment ({venv})")
    with _gpu(job):
        _syncing.add(venv)
        try:
            if state in ("missing", "outdated"):
                rc, lines = _cmd(job, _sync_cmd(venv), _uv_env(venv))
                if rc:
                    raise _Failed("failed", f"{venv}: {lines[-1] if lines else f'uv exited {rc}'}")
            if deep and not _imports(job, venv):
                _say(job, None, f"Reinstalling the packages of {venv}")
                rc, lines = _cmd(job, _sync_cmd(venv, "--reinstall"), _uv_env(venv))
                if rc or not _imports(job, venv):
                    raise _Failed(
                        "failed",
                        f"{venv} does not work after reinstalling its packages: see the log",
                    )
        finally:
            _syncing.discard(venv)


def _imports(job: jobs.Job, venv: str) -> bool:
    _say(job, None, f"Checking {venv}")
    rc, _ = _cmd(job, [str(jobs.venv_python(venv)), "-c", f"import {IMPORTS[venv]}"])
    return rc == 0


def _sha256(job: jobs.Job, f: Path) -> str:
    h = hashlib.sha256()
    with open(f, "rb") as fh:
        while chunk := fh.read(8 << 20):
            if job.status == "cancelled":  # a 16 GB file takes a while
                raise _Failed("cancelled", "cancelled")
            h.update(chunk)
    return h.hexdigest()


def _inside(e: dict, f: Path) -> bool:
    """A file Repair may delete: in the models folder, not behind a link (a dev junction may point into
    another checkout; models.remove refuses those too)."""
    top = models.root().resolve()
    base = models.root() / e["where"]
    if any(os.path.isjunction(p) for p in (base, *base.parents) if models.root() in p.parents):
        return False
    return Path(os.path.realpath(f)).is_relative_to(top)


def _drop_damaged(job: jobs.Job, group: str) -> None:
    """Delete the group's files whose size or SHA-256 (where the manifest has one) is wrong; the download
    fetches them again. Reading them takes no lock (minutes for a big group); deleting does. An HF-cache
    link goes with its blob, or the download would only relink the damaged blob."""
    damaged = []
    for e in _entries(group):
        for rel, (size, sha) in models.MANIFEST_ROWS.get(e["key"], {}).items():
            f, name = models.target(e, rel), Path(e["where"], rel).as_posix()
            if not f.exists():
                continue
            if not _inside(e, f):
                _log(job, f"not checked, it lies behind a link: {name}")
                continue
            bad = f.stat().st_size != size
            if not bad and sha:
                _say(job, None, f"Checking {name}")
                bad = _sha256(job, f) != sha
            if bad:
                damaged.append((f, name))
    if not damaged:
        return
    with _gpu(job):
        for f, name in damaged:
            _log(job, f"damaged, fetching it again: {name}")
            if f.is_symlink():
                Path(os.path.realpath(f)).unlink(missing_ok=True)  # _inside: the blob is ours
            f.unlink(missing_ok=True)


def _on_disk(e: dict) -> int:
    """The bytes of an entry's download so far: its files present, and the parts being written."""
    have = sum(
        f.stat().st_size
        for rel in models.MANIFEST_ROWS.get(e["key"], {})
        if (f := models.target(e, rel)).is_file()
    )
    base = models.root() / e["where"]
    return have + (
        sum(f.stat().st_size for f in base.rglob("*.incomplete")) if base.is_dir() else 0
    )


def _progress(job: jobs.Job, group: str, stop: threading.Event) -> None:
    want = sum(models.state(e)["want"] for e in _entries(group)) or 1
    while not stop.wait(2):
        try:
            have = sum(_on_disk(e) for e in _entries(group))
        except OSError:  # a file renamed while counting: the next round counts it
            continue
        _say(job, min(have / want, 0.99), f"{have / 1e9:.1f} of {want / 1e9:.1f} GB")


def _download(job: jobs.Job, group: str) -> None:
    """tools.download_models in .venv-ml; a gated repo (it prints `gated: …`) fails with code `gated`."""
    stop = threading.Event()
    counter = threading.Thread(target=_progress, args=(job, group, stop), daemon=True)
    counter.start()
    try:
        rc, lines = _cmd(
            job, [jobs._python("download_models"), "-m", "tools.download_models", group]
        )
    finally:
        stop.set()
        counter.join(5)
    gated = [line.removeprefix("gated:").strip() for line in lines if line.startswith("gated:")]
    if gated:
        raise _Failed("gated", "; ".join(gated))
    if rc:
        raise _Failed("failed", lines[-1] if lines else f"download_models exited {rc}")


def move_models(old: Path, new: Path) -> jobs.Job:
    """Move the model files to `new` (empty or new) as a job; the setting follows once they are there."""
    with _asking:
        if models._downloading():
            raise ApiError(409, "busy", "an install is running; move the models after it ends")
        job = jobs.Job("", "move_models", None, {"from": str(old), "to": str(new)})
        jobs.JOBS[job.id] = job
    threading.Thread(target=_move, args=(job, old, new), daemon=True).start()
    return job


def _move(job: jobs.Job, old: Path, new: Path) -> None:
    """The same drive: one rename. Otherwise (another drive, or the rename refused): copy into a staging
    folder beside `new`, rename it into place once whole, switch the setting, then delete the old copy (what
    cannot be deleted is logged: the models already work from `new`). A failed or cancelled copy removes its
    staging folder and leaves everything where it was."""
    switched = False
    with _turn:  # no install meanwhile
        with jobs._state:
            if job.status == "cancelled":
                return
            job.status = "running"
        jobs.publish("job", job.public())
        try:
            with _gpu(job):  # no step reads the models meanwhile
                if new.exists():
                    new.rmdir()  # empty (choose_folder checked): a rename needs it gone
                try:
                    os.replace(old, new)
                    _log(job, f"renamed {old} to {new}")
                    renamed = True
                except OSError:  # another drive, or a file open: copy
                    _copy(job, old, new)
                    renamed = False
                try:
                    settings.write(models=str(new))
                except (
                    OSError
                ):  # the setting is the switch: without it the models go back (OPS-002)
                    if renamed:
                        os.replace(new, old)
                    else:
                        shutil.rmtree(new, ignore_errors=True)  # the old copy is still whole
                    raise
                switched = True
                storage_stale()
            if old.exists():
                _say(job, None, "Deleting the old copy")
                shutil.rmtree(
                    old, onexc=lambda fn, p, exc: _log(job, f"could not delete {p}: {exc}")
                )
            end = "done", None
        except _Failed as e:
            end = "failed", {"code": e.code, "message": str(e)}
        except Exception as e:  # noqa: BLE001 - a bug fails the job visibly, never the thread silently
            end = "failed", {"code": "failed", "message": f"{type(e).__name__}: {e}"}
        with jobs._state:
            if switched or job.status != "cancelled":  # once switched, the move happened: say so
                job.status, job.error = end
        jobs.publish("job", job.public())


def storage_stale() -> None:
    from server import storage  # the space view measures the new place

    storage._last = None


def _copy(job: jobs.Job, old: Path, new: Path) -> None:
    """Every file of `old` into `<new>.effigy-partial-<job id>`, checked by size, then renamed to `new`. A
    link (a junction) inside is not followed: it is logged and left behind. The job id in the name: a
    sibling called `<new>.effigy-partial` was taken for ours and deleted (audit 2026-10-10, OPS-001); a
    staging folder that died with the app now stays, named for its job, until the user removes it."""
    from server.storage import _drive

    staging = new.with_name(f"{new.name}.effigy-partial-{job.id}")
    files = []
    for d, dirs, names in os.walk(old):
        for x in [x for x in dirs if os.path.isjunction(os.path.join(d, x))]:
            _log(job, f"not copied, it is a link: {Path(d, x)}")
        files += [Path(d, n) for n in names]
        dirs[:] = [x for x in dirs if not os.path.isjunction(os.path.join(d, x))]
    total = sum(f.stat().st_size for f in files)
    free = shutil.disk_usage(_drive(new)).free
    if free < total + 2**30:
        raise _Failed(
            "no_space", f"the models need {total / 1e9:.1f} GB, {free / 1e9:.1f} GB are free"
        )
    done = 0
    try:
        for f in files:
            if job.status == "cancelled":
                raise _Failed("cancelled", "cancelled: the models stay where they were")
            dest = staging / f.relative_to(old)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            if dest.stat().st_size != f.stat().st_size:
                raise _Failed(
                    "failed", f"{dest} did not copy whole: the models stay where they were"
                )
            done += f.stat().st_size
            _say(job, done / (total or 1), f"{done / 1e9:.1f} of {total / 1e9:.1f} GB copied")
        os.replace(staging, new)
    except BaseException:
        shutil.rmtree(
            staging, ignore_errors=True
        )  # nothing half-copied stays to be mistaken for models
        raise
