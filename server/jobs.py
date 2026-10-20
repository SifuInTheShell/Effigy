"""Jobs: run steps (§3.1) under one GPU lock, keep one heavy worker resident, publish SSE events (§3.5)."""

import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from server import blender_install, gltfpack_install, gpu_amd, library, presets, settings
from server.library import ApiError
from server.presets import LOWER

ROOT = Path(__file__).resolve().parents[1]
STEP = re.compile(r"^_?[a-z][a-z0-9_]*$")
SERVE = {"trellis2", "concepts", "motion"}  # families that run resident (`--serve`); one at a time
# ponytail: anything not listed here evicts the resident worker first; it may use presets.VRAM_GB if it over-evicts
LIGHT = {
    "_echo",
    "brief",
    "clean",
    "remesh",
    "uv",
    "validate",
    "export",
    "anim",
    "anim_edit",
    "import_model",
    "import_motion",
    "pixelize",  # M51: numpy and Pillow
    "sprites",  # M52: Blender on the CPU (Workbench), no model
    "parts:rename",  # a step, or step:op
    "transform",  # numpy on the CPU: a still model turned, scaled and placed
}  # rig_skin: SkinTokens on the GPU

GPU = threading.Lock()  # ponytail: one global lock, it's one laptop GPU
_state = threading.Lock()  # queued/running -> cancelled vs. queued -> running
JOBS: dict[str, "Job"] = {}
_subs: list[queue.SimpleQueue] = []
_worker: "Worker | None" = None
# A resident worker holds its models in RAM (TRELLIS ~18.6 GB, Pixal3D ~24 GB on a 32 GB laptop): stop it
# after this long without a job. ponytail: one timer, reset by every job; per-family limits if needed.
IDLE_S = float(os.environ.get("EFFIGY_WORKER_IDLE_S", "300"))
_idle: threading.Timer | None = None


@dataclass
class Job:
    asset_id: str
    step: str
    parent: str | None
    params: dict
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "queued"  # queued | running | done | failed | cancelled
    version: str | None = None
    error: dict | None = None
    proc: subprocess.Popen | None = None
    stop: Callable[[], None] | None = None  # how to cancel a running proc (default: terminate)
    budget: float | None = None  # GB its step process may allocate on the GPU (M45 pre-flight)
    free_gb: float | None = None  # free VRAM (GB) the pre-flight saw before a cold start (BE-006)

    progress: float | None = None  # the step's last PROGRESS line (the MCP bridge polls it)
    text: str = ""

    def public(self) -> dict:
        out = {"job_id": self.id, "asset_id": self.asset_id, "status": self.status}
        out |= {"progress": self.progress, "text": self.text} if self.progress is not None else {}
        return out | {k: v for k in ("version", "error") if (v := getattr(self, k)) is not None}


# --- events -----------------------------------------------------------------------------------


def subscribe() -> queue.SimpleQueue:
    q = queue.SimpleQueue()
    _subs.append(q)
    return q


def unsubscribe(q: queue.SimpleQueue) -> None:
    _subs.remove(q)


def publish(event: str, data: dict) -> None:
    for q in _subs.copy():  # SSE handlers (un)subscribe from other threads
        q.put((event, data))


def _log(job: Job, line: str) -> None:
    line = line.rstrip()
    with open(library.root() / job.asset_id / "jobs.log", "a", encoding="utf-8") as f:
        f.write(f"{job.id} {job.step} {line}\n")
    publish("log", {"job_id": job.id, "line": line})


def _pump(job_of, stream) -> None:
    for line in stream:
        if job := job_of():
            _log(job, line)


# --- step processes ---------------------------------------------------------------------------


# the environment each step runs in (the rest: .venv-ml), and the uv dependency group that makes each one
STEP_VENV = {
    "rig_skin": ".venv-skin",
    "hunyuan": ".venv-hunyuan",
    "motion": ".venv-kimodo",
    "parts": ".venv-parts",
    "mocap": ".venv-gemx",
    "depth": ".venv-moge",
}
VENV_GROUP = {
    ".venv-ml": "ml",
    ".venv-skin": "skin",
    ".venv-kimodo": "kimodo",
    ".venv-parts": "parts",
    ".venv-gemx": "gemx",
    ".venv-hunyuan": "hunyuan",
    ".venv-moge": "moge",
}


def uv() -> str:
    """uv on PATH, else the portable zip's own (run.bat puts it on PATH too)."""
    return shutil.which("uv") or str(ROOT / ("uv.exe" if os.name == "nt" else "uv"))


def venv_python(venv: str) -> Path:
    return ROOT / venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _python(step: str) -> str:
    if step.startswith("_"):  # test steps run in the backend's own venv
        return sys.executable
    venv = STEP_VENV.get(step, ".venv-ml")
    if not (exe := venv_python(venv)).is_file():
        raise RuntimeError(
            f"{venv} missing: install its feature in Setup (or UV_PROJECT_ENVIRONMENT={venv} uv sync "
            f"--frozen --group {VENV_GROUP[venv]})"
        )
    return str(exe)


def _env(budget: float | None = None) -> dict:
    models = str(settings.models_root())  # EFFIGY_MODELS, else the folder the user chose (M35c)
    return (
        os.environ
        | {
            # M45: steps/_common caps torch at this many GB, so going over is an OOM instead of a spill
            "EFFIGY_VRAM_BUDGET_GB": f"{budget:.2f}" if budget else "",
            "EFFIGY_LIBRARY": str(library.root().resolve()),
            "EFFIGY_MODELS": models,
            # the cache only: HF_HOME would also move the token `hf auth login` saved
            "HF_HUB_CACHE": str(Path(models) / "hub"),
            # we read children's pipes as UTF-8; Windows Python would otherwise write them in cp1252
            "PYTHONIOENCODING": "utf-8",
            # CUDA's stream-ordered allocator, set before the child imports torch (it picks the backend at
            # import). torch's caching allocator fragmented in the sparse decoder: the bench crate reserved
            # 23.7 GB for an 8.1 GB peak and Windows spilled to shared memory (23 s, whole GPU 15.5/16 GB);
            # cudaMallocAsync: 9.0 GB reserved, 2 s, 10.3 GB. expandable_segments isn't supported on Windows.
            # AMD: HIP on Windows hangs in its first allocation with cudaMallocAsync, and has no
            # expandable_segments: the native allocator, kept from splitting big blocks (the mesh decode
            # OOMed at 10.9 GB with 3.3 GB of it reserved but free)
            "PYTORCH_CUDA_ALLOC_CONF": os.environ.get(
                "PYTORCH_CUDA_ALLOC_CONF",
                "backend:native,max_split_size_mb:256,garbage_collection_threshold:0.6"
                if gpu_amd.active()
                else "backend:cudaMallocAsync",
            ),
            "EFFIGY_BLENDER": str(blender_install.blender_exe()),
            "EFFIGY_GLTFPACK": str(gltfpack_install.gltfpack_exe()),
        }
        | _pin_gpu()
    )


def _pin_gpu() -> dict:
    """M45: with several GPUs the steps run on the one with the most memory, the one the budget is for
    (CUDA's own order is "fastest first", nvidia-smi's the PCI bus). A CUDA_VISIBLE_DEVICES set by the user wins.
    ponytail: the budget is then still read from the biggest GPU."""
    if "CUDA_VISIBLE_DEVICES" in os.environ or not (i := _smi("index")):
        return {}
    return {"CUDA_DEVICE_ORDER": "PCI_BUS_ID", "CUDA_VISIBLE_DEVICES": str(int(i[0]))}


def _spawn(step: str, *args: str, stdin=None, budget: float | None = None) -> subprocess.Popen:
    cmd = [_python(step), "-m", f"steps.{step}", *args]
    return subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=_env(budget),
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=True,  # POSIX: its own process group, so a cancel takes its children too
    )


def _rc(rc: int | None) -> str:
    """An exit code as Windows names a crash: 0xc0000005 (access violation), 0xc0000409 (fail fast)."""
    return f"{rc & 0xFFFFFFFF:#x}" if rc and not -256 < rc < 256 else str(rc)


def _read(job: Job, stdout, rid: str | None = None) -> tuple[str, dict] | None:
    """Relay PROGRESS lines; return the first RESULT/ERROR, or None if stdout closed first."""
    for line in stdout:
        kind, _, rest = line.rstrip("\r\n").partition(" ")
        if rid is not None:
            got, _, rest = rest.partition(" ")
            if got != rid:
                continue
        if kind == "PROGRESS":
            p, _, text = rest.partition(" ")
            ev = {"job_id": job.id, "asset_id": job.asset_id, "step": job.step, "p": float(p)}
            job.progress, job.text = ev["p"], text
            publish("progress", ev | {"text": text})
        elif kind in ("RESULT", "ERROR"):
            return kind, json.loads(rest)
    return None


def _run_oneshot(job: Job, in_dir: Path | None, out: Path) -> tuple[str, dict] | None:
    params, file = json.dumps(job.params), out / "_params.json"
    if len(params) > 8000:  # Windows' command line stops at ~32k: long params go in a file (§3.1)
        out.mkdir(parents=True, exist_ok=True)
        file.write_text(params, encoding="utf-8")
        params = f"@{file}"
    try:
        args = ["--out", str(out), "--params", params]
        proc = job.proc = _spawn(
            job.step, *args, *(["--in", str(in_dir)] if in_dir else []), budget=job.budget
        )
        if job.status == "cancelled":  # cancel() ran before there was a process to stop
            _kill_tree(proc)
        threading.Thread(target=_pump, args=(lambda: job, proc.stderr), daemon=True).start()
        reply = _read(job, proc.stdout)
        rc = proc.wait()
        if rc and (
            reply is None or reply[0] == "RESULT"
        ):  # a RESULT then a crash is a crash (BE-005)
            code = {2: "bad_params", 3: "oom"}.get(rc, "failed")
            how = "after its RESULT" if reply else "without ERROR"
            return "ERROR", {"code": code, "message": f"steps.{job.step} exited {_rc(rc)} {how}"}
        return reply
    finally:
        file.unlink(missing_ok=True)


class Worker:
    """A `--serve` process for one heavy family; blocks until READY."""

    def __init__(self, job: Job):
        self.step, self.key, self.job = job.step, _key(job), job
        # free VRAM (GB) before this worker existed and when it last went idle: a warm pre-flight reads what
        # other programs took since (nvidia-smi can't tell processes apart on Windows; audit BE-006)
        self.free_cold, self.idle_free = job.free_gb, None
        self.proc = job.proc = _spawn(job.step, "--serve", stdin=subprocess.PIPE, budget=job.budget)
        threading.Thread(
            target=_pump, args=(lambda: self.job, self.proc.stderr), daemon=True
        ).start()
        if self.proc.stdout.readline().strip() != "READY":
            self.proc.kill()
            raise RuntimeError(f"steps.{self.step} --serve exited before READY")


def _smi(*fields: str) -> list[float] | None:
    """`fields` of the GPU the steps run on: the one with the most memory (M45; `_env` pins the steps to it),
    or None without an NVIDIA GPU or driver."""
    try:
        cmd = ["nvidia-smi", f"--query-gpu=index,memory.total,{','.join(fields)}"]
        out = subprocess.check_output([*cmd, "--format=csv,noheader,nounits"], text=True)
        rows = [[float(v) for v in line.split(",")] for line in out.splitlines() if line.strip()]
        return max(rows, key=lambda r: r[1])[2:]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        # AMD: no nvidia-smi; None for "index" (no pinning) or no AMD card
        return gpu_amd.smi(*fields)


def _free_gb() -> float | None:
    return round(v[0] / 1024, 1) if (v := _smi("memory.free")) else None


def _watch(job: Job) -> None:
    """`gpu_load` every 2 s while `job` holds the GPU, then once idle (the header shows it)."""
    while True:
        busy = job.status == "running"
        v = _smi("utilization.gpu", "memory.used", "memory.total") or [None] * 3
        gb = [round(x / 1024, 1) if x is not None else None for x in v[1:]]
        publish(
            "gpu_load",
            {"step": job.step if busy else None, "util": v[0], "used_gb": gb[0], "total_gb": gb[1]},
        )
        if not busy:
            return
        time.sleep(2)


def _key(job: Job) -> tuple:
    """What a resident worker holds: its step and the model its params pick (trellis2's `weights`). M45: a
    switch of model happens in a fresh process: swapping in place peaked at 21 GB of host RAM (the freed
    model's heap stays with the process) and left the 32 GB laptop paging."""
    return job.step, presets.variant(job.step, job.params)


def _resident(job: Job) -> bool:
    """The live resident worker holds `job`'s step and model."""
    return bool(_worker) and _worker.key == _key(job) and _worker.proc.poll() is None


def evict() -> None:
    """Stop the resident worker (VRAM is freed when the process exits)."""
    global _worker
    if _worker:
        w, _worker = _worker, None
        _kill_tree(w.proc)  # and its Blender child, if a preview is rendering
        w.proc.wait()
        _ram_settled()
        publish("gpu", {"resident": None, "free_gb": _free_gb()})


def _ram_settled(seconds: float = 10) -> None:
    """Wait until the host RAM a dead worker held has come back: Windows returns its ~18 GB over a second or
    two after the exit, and the pre-flight read 8 GB free right after an eviction (M45, docs/bench/m45.md)."""
    last, end = -1.0, time.time() + seconds
    while time.time() < end:
        free = presets.ram_gb()[1]
        if free - last < 0.2:
            return
        last = free
        time.sleep(0.5)


def _run_resident(job: Job, in_dir: Path | None, out: Path) -> tuple[str, dict] | None:
    global _worker
    if _worker and not _resident(job):
        evict()
    fresh = not _worker
    if fresh:
        _worker = Worker(job)
        publish("gpu", {"resident": job.step, "free_gb": _free_gb()})
    w, params = _worker, dict(job.params)
    w.job, job.proc = job, w.proc
    if job.status == "cancelled":  # cancel() ran before there was a process to stop (audit BE-007)
        if fresh:
            evict()  # a worker nobody asked for would hold its models until the idle timer
        return None
    # the job's budget rides along: the worker caps itself per request (its startup cap is stale once
    # other programs come or go; audit BE-006)
    req = {"id": job.id, "op": params.pop("op", "run"), "out": str(out), "params": params}
    req |= {"in": str(in_dir) if in_dir else None, "budget": job.budget}
    w.proc.stdin.write(json.dumps(req) + "\n")
    w.proc.stdin.flush()
    reply = _read(job, w.proc.stdout, rid=job.id)
    if reply is None:  # the worker died (cancel or crash); the exit code may be all it left (M46)
        evict()
        how = f"exited {_rc(w.proc.returncode)} without ERROR"
        return "ERROR", {"code": "failed", "message": f"the {job.step} worker {how}"}
    time.sleep(0.5)  # the worker empties its cache right after the RESULT
    w.idle_free = _free_gb()
    return reply


def shutdown() -> None:
    """App exit: no step process may outlive the backend (it would hold VRAM)."""
    for job in JOBS.values():
        if job.status == "running" and job.proc:
            _kill_tree(job.proc)
    evict()


# --- jobs -------------------------------------------------------------------------------------


_submitting = (
    threading.Lock()
)  # a dedupe scan and its insert as one: two identical requests start one job


def submit(
    asset_id: str, step: str, parent: str | None, params: dict, dedupe: bool = False
) -> tuple[Job, bool]:
    """(the job, whether it is new). `dedupe`: the queued or running job with the same asset, step, parent
    and params, if there is one."""
    if not STEP.match(step) or not (ROOT / "steps" / f"{step}.py").is_file():
        raise ApiError(404, "not_found", f"no step {step!r}")
    library.asset_dir(asset_id)
    if parent:
        library.version_dir(asset_id, parent)
    if (
        name := params.get("preset")
    ) is not None:  # a preset's name stands for its params (agent 2026-10-11)
        if name == "best" and step in presets.PRESETS:  # M46b: the best this card fits
            total = v[0] / 1024 if (v := _smi("memory.total")) else None
            name = presets.best(step, presets.budget_gb(total) if total else None)
        if name not in presets.PRESETS.get(step, {}):
            raise ApiError(
                400,
                "bad_params",
                f"{step} has no preset {name!r}: {sorted(presets.PRESETS.get(step, {}))}",
            )
        params = presets.PRESETS[step][name] | {k: v for k, v in params.items() if k != "preset"}
    with _submitting:
        if dedupe:
            for j in list(JOBS.values()):
                same = (j.asset_id, j.step, j.parent, j.params) == (asset_id, step, parent, params)
                if same and j.status in ("queued", "running"):
                    return j, False
        job = Job(asset_id, step, parent, params)
        JOBS[job.id] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job, True


def get(job_id: str) -> Job:
    if job_id not in JOBS:
        raise ApiError(404, "not_found", f"no job {job_id!r}")
    return JOBS[job_id]


def cancel(job_id: str) -> None:
    job = get(job_id)
    with _state:
        prev = job.status
        if prev not in ("queued", "running"):
            return
        job.status = "cancelled"
    if prev == "queued":
        publish("job", job.public())
    elif job.proc:
        (job.stop or (lambda: _kill_tree(job.proc)))()  # §3.1: the runner reports it


def _kill_tree(proc: subprocess.Popen) -> None:
    """The step and its children: a Blender op launched by the step outlived TerminateProcess on the
    step alone and kept writing after the cancel, with the GPU lock already released (code review
    2026-10-05). Windows: taskkill's tree kill; POSIX: the step's own process group (`_spawn`)."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass  # gone between the status check and here


def _check_outputs(out: Path, payload: dict) -> str | None:
    rels = payload.get("outputs", {}).values()
    if bad := [r for r in rels if not (out / r).resolve().is_relative_to(out.resolve())]:
        return f"outputs outside --out: {bad}"
    # a file, or a folder of them (the 2D track's frames/, research 15): an empty folder counts as missing
    if missing := [
        r
        for r in rels
        if not ((out / r).is_file() or ((out / r).is_dir() and any((out / r).iterdir())))
    ]:
        return f"missing outputs: {missing}"
    return None


def _evict_if_idle() -> None:
    if GPU.acquire(blocking=False):  # a job holding the lock will reset the timer itself
        try:
            evict()
        finally:
            GPU.release()


def _rearm_idle() -> None:
    global _idle
    if _idle:
        _idle.cancel()
    if _worker:
        _idle = threading.Timer(IDLE_S, _evict_if_idle)
        _idle.daemon = True
        _idle.start()


def _run(job: Job) -> None:
    try:
        _run_locked(job)
    finally:
        _rearm_idle()


def _light(job: Job) -> bool:
    """A light step or step:op: runs beside the resident worker and needs no VRAM of its own."""
    return job.step in LIGHT or f"{job.step}:{job.params.get('op')}" in LIGHT


def _keeps_worker(job: Job) -> bool:
    """A resident family or a light step runs beside the resident worker."""
    return job.step in SERVE or _light(job)


# --- M45 GPU safeguards: refuse what cannot fit, cap what runs --------------------------------


def _refusal(step: str, params: dict, need: float | None, total: float, free: float) -> dict | None:
    """The error a GPU job ends with before it starts, or None. `need` (GB, None = not measured: the whole
    budget) never fits a `total` GB GPU once Windows and the display have their reserve, or does not fit the
    `free` GB other programs leave now. Both say which lighter variant fits, if one does."""
    budget, now = presets.budget_gb(total), presets.budget_gb(total, free)
    what = (
        f"about {need:.1f} GB" if need is not None else f"up to {budget:.1f} GB (not measured yet)"
    )
    # an unmeasured step counts as the whole budget; it is only "busy" when others hold 1 GB over a typical desktop
    need, busy_at = (budget, budget - 1.0) if need is None else (need, need)
    if need > budget:
        why = (
            f"{step} needs {what} of video memory; this GPU has {total:.1f} GB and keeps "
            f"{presets.reserve_gb(total):.1f} GB for Windows and the display"
        )
        alt = presets.lighter(step, budget, params)
    elif busy_at > now:
        why = (
            f"the GPU is busy: other programs use {total - free:.1f} of its {total:.1f} GB and {step} "
            f"needs {what}. Close them (a game, a video, another AI app) and run it again"
        )
        alt = presets.lighter(step, now, params)
    else:
        return None
    use = f", or use {', '.join(f'{k} {v}' for k, v in alt.items())}" if alt else ""
    return {"code": "oom", "message": why + use} | ({"suggest": alt} if alt else {})


def _preflight(job: Job) -> dict | None:
    """Before a GPU job starts (after the eviction): set its budget, or refuse it (`_refusal`)."""
    from server import machine  # here: machine imports install, which imports this module

    if job.step in machine.SM_STEPS and (sm := machine.sm()) is not None and sm < machine.MIN_SM:
        return {
            "code": "unsupported",
            "message": f"{job.step}: {machine.UNSUPPORTED} (this card: {sm:g})",
        }
    gpu = not (job.step.startswith("_") or _light(job))  # test steps run in the backend's venv
    need = presets.need_gb(job.step, job.params) if gpu else 0
    if need == 0 or not (v := _smi("memory.total", "memory.free")):
        return None  # nothing on the GPU, or no NVIDIA GPU to ask
    total, free = v[0] / 1024, v[1] / 1024
    if _resident(job):
        # nvidia-smi can't tell processes apart on Windows: the GPU shows the worker's own models plus other
        # programs'. Against the worker's idle baseline, the change since is other programs' (BE-006); a
        # worker without a baseline yet is taken to hold everything
        cold, idle = getattr(_worker, "free_cold", None), getattr(_worker, "idle_free", None)
        if cold is not None and idle is not None:
            free = min(cold + (free - idle), total - presets.DESKTOP_GB)
        else:
            free = total - presets.DESKTOP_GB
    else:
        job.free_gb = free  # a cold start's baseline, kept by the worker it spawns
    job.budget = presets.budget_gb(total, free)  # this run's cap: what other programs leave now
    if refused := _refusal(job.step, job.params, need, total, free):
        return refused
    if not _resident(job) and (
        ram := presets.ram_need(job.step, job.params)
    ):  # a fresh process loads the models
        total_ram, free_ram = presets.ram_gb()
        if free_ram < ram:
            return {
                "code": "oom",
                "message": (
                    f"{job.step} needs about {ram:.0f} GB of free memory (RAM) to load its models and this "
                    f"computer has {free_ram:.1f} of {total_ram:.0f} GB free: close other programs (a "
                    "browser, a game, another AI app) and run it again"
                ),
            }
    return None


def _oom_message(step: str, need: float | None, budget: float | None, message: str) -> str:
    """A step's own OOM, with what it may use here and what it measured on the reference laptop."""
    here = f": it may use {budget:.1f} GB of this GPU" if budget else ""
    there = f", it needed about {need:.1f} GB on the 16 GB reference laptop" if need else ""
    return f"{step} ran out of video memory{here}{there} ({message})"


def _run_locked(job: Job) -> None:
    with GPU:
        with _state:
            if job.status == "cancelled":
                return
            job.status = "running"
        publish("job", job.public())
        threading.Thread(target=_watch, args=(job,), daemon=True).start()
        out = None
        try:
            _log(job, f"start parent={job.parent} params={json.dumps(job.params)}")
            if not (_light(job) or _resident(job)):
                evict()  # before the pre-flight reads the free VRAM and RAM
            if refused := _preflight(job):
                reply = "ERROR", refused
            else:
                out = library.new_version(job.asset_id, job.step)
                in_dir = library.version_dir(job.asset_id, job.parent) if job.parent else None
                run = _run_resident if job.step in SERVE else _run_oneshot
                reply = run(job, in_dir, out) or (
                    "ERROR",
                    {"code": "failed", "message": "no RESULT"},
                )
                if reply[0] == "ERROR" and reply[1].get("code") == "oom":  # the step's own OOM
                    need = presets.need_gb(job.step, job.params)
                    msg = _oom_message(job.step, need, job.budget, reply[1].get("message", ""))
                    # the next-lower params, unless this run had them already (an offloaded run's OOM)
                    down = LOWER.get(job.step, {})
                    tried = all(job.params.get(k) == v for k, v in down.items())
                    lower = {"suggest": down} if down and not tried else {}
                    reply = "ERROR", lower | reply[1] | {"message": msg}
        except Exception as e:  # noqa: BLE001 - a job must always end in a status
            reply = "ERROR", {"code": "failed", "message": f"{type(e).__name__}: {e}"}
        kind, payload = reply
        with _state:
            # finishing is part of the job: a failure here ends in "failed", not in "running" for ever
            # (code review 2026-10-05); a cancelled job writes nothing (a brief's meta updates asset.json)
            try:
                if kind == "RESULT" and (msg := _check_outputs(out, payload)):
                    kind, payload = "ERROR", {"code": "failed", "message": msg}
                if kind == "RESULT" and job.status != "cancelled":
                    meta = library.write_meta(out, job.parent, job.params, payload)
            except Exception as e:  # noqa: BLE001 - a job must always end in a status
                kind, payload = (
                    "ERROR",
                    {"code": "failed", "message": f"finishing: {type(e).__name__}: {e}"},
                )
            if kind == "RESULT" and job.status != "cancelled":
                job.version, job.status = out.name, "done"
                publish("version_created", {"asset_id": job.asset_id, "meta": meta})
            else:
                if out:
                    shutil.rmtree(out, ignore_errors=True)  # nothing half-written stays
                if job.status != "cancelled":
                    job.error, job.status = payload, "failed"
        _log(job, f"end {job.status}")
        publish("job", job.public())
