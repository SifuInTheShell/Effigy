"""M45 GPU safeguards on the CPU: the VRAM table, the budget, the pre-flight, the OOM message, the cap."""

import sys
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from server import jobs, machine, presets
from server.app import app
from steps import _common

LAPTOP = 16303 / 1024  # what nvidia-smi reports for the 16 GB RTX 5080 Laptop


def test_the_table_and_the_budget():
    assert presets.need_gb("trellis2", {}) == 9.0  # the step's default: fast
    assert presets.need_gb("trellis2", {"weights": "faithful"}) == 13.3
    assert presets.need_gb("trellis2", {"weights": "new"}) == 13.3  # unknown: the largest
    assert presets.need_gb("canon", {}) == 9.2 and presets.need_gb("rig_fit", {}) == 0
    # the Tile by its file (M46c): the lite one fits the laptop, the full one is for bigger cards
    assert (
        presets.need_gb("refine", {})
        == presets.need_gb("refine", {"tile": None})
        == 12.3
        < presets.budget_gb(LAPTOP)
    )
    assert presets.need_gb("refine", {"tile": "full"}) == 15.6 > presets.budget_gb(LAPTOP)
    assert presets.need_gb("brand_new_step", {}) is None
    assert presets.lighter("trellis2", 10.5) == {"weights": "fast"}
    assert presets.lighter("trellis2", 20) == {"weights": "faithful"}
    assert presets.lighter("trellis2", 5) is None and presets.lighter("canon", 20) is None

    # Windows + the desktop (0.5), the CUDA context (0.3), a margin (0.5); with a live reading, what is free now
    assert presets.reserve_gb(8) == pytest.approx(1.3) == presets.reserve_gb(24)
    assert presets.budget_gb(LAPTOP) == pytest.approx(LAPTOP - 1.3)  # 14.6: Faithful's 13.3 fits
    # the laptop idle, as measured: 0.47 GB used by Windows and the desktop
    assert presets.budget_gb(LAPTOP, free=15.46) == pytest.approx(14.66)
    assert presets.budget_gb(LAPTOP, free=10.0) == pytest.approx(9.2)  # a game holds 5.9 GB


def test_the_reserve_can_be_set(monkeypatch):
    monkeypatch.setenv("EFFIGY_VRAM_RESERVE_GB", "3")
    assert presets.budget_gb(LAPTOP) == pytest.approx(LAPTOP - 3)


def test_the_preflight_decisions():
    faithful = {"weights": "faithful"}
    assert jobs._refusal("trellis2", faithful, 13.3, LAPTOP, 15.5) is None  # the laptop, idle

    never = jobs._refusal("trellis2", faithful, 13.3, 11.99, 11.5)  # a 12 GB card
    assert never["code"] == "oom" and never["suggest"] == {"weights": "fast"}
    assert "needs about 13.3 GB" in never["message"] and "use weights fast" in never["message"]

    busy = jobs._refusal("trellis2", faithful, 13.3, LAPTOP, 10.0)  # a game holds 5.9 GB
    assert busy["message"].startswith("the GPU is busy: other programs use 5.9 of its 15.9 GB")
    assert busy["suggest"] == {"weights": "fast"}  # 9.0 fits in what is free
    assert "suggest" not in jobs._refusal("canon", {}, 9.2, LAPTOP, 4.0)  # no lighter canon

    assert jobs._refusal("brand_new_step", {}, None, LAPTOP, 15.5) is None  # needs the budget: free
    unmeasured = jobs._refusal("brand_new_step", {}, None, LAPTOP, 10.0)
    assert "busy" in unmeasured["message"] and "not measured yet" in unmeasured["message"]


def test_a_steps_own_oom_says_what_it_may_use():
    msg = jobs._oom_message("refine", 15.6, 14.33, "OutOfMemoryError: would exceed allowed memory")
    assert msg == (
        "refine ran out of video memory: it may use 14.3 GB of this GPU, it needed about 15.6 GB on "
        "the 16 GB reference laptop (OutOfMemoryError: would exceed allowed memory)"
    )
    assert jobs._oom_message("_echo", None, None, "x") == "_echo ran out of video memory (x)"


def test_the_machine_check_marks_what_never_fits(monkeypatch):
    monkeypatch.setattr(machine, "_ram_gb", lambda: 31.7)
    monkeypatch.setattr(machine, "_long_paths", lambda: None)
    monkeypatch.setattr(machine, "_gpu", lambda: ("RTX 4070", 11.99, "617.42"))
    monkeypatch.setattr(machine, "sm", lambda: 8.9)
    gpu = machine.check()[0]
    assert gpu["level"] == "warn" and gpu["budget_gb"] == 10.7
    assert gpu["too_big"] == [  # the 3D model by its presets (M46b), the rest by their variants
        {"step": "trellis2", "param": "preset", "value": "faithful-hd", "need_gb": 19.4},
        {"step": "trellis2", "param": "preset", "value": "faithful-hq", "need_gb": 13.3},
        {"step": "trellis2", "param": "preset", "value": "faithful", "need_gb": 13.3},
        {"step": "refine", "param": "tile", "value": "lite", "need_gb": 12.3},
        {
            "step": "refine",
            "param": "tile",
            "value": "full",
            "need_gb": 15.6,
            "group": "refine-full",
        },
        {"step": "redraw", "param": "op", "value": "redraw", "need_gb": 12.3},  # M61
    ]
    assert "trellis2 faithful (13.3 GB)" in gpu["detail"] and "use Fast" in gpu["detail"]
    assert "faithful-hd" not in gpu["detail"]  # for bigger cards: listed for the menu, no warning
    monkeypatch.setattr(machine, "_gpu", lambda: ("RTX 5080 Laptop GPU", LAPTOP, "617.42"))
    laptop = machine.check()[0]
    assert laptop["level"] == "ok" and [b["value"] for b in laptop["too_big"]] == [
        "faithful-hd",
        "full",
    ]


def fake_smi(total_mib, free_mib):
    def smi(*fields):
        v = {"index": 0, "memory.total": total_mib, "memory.free": free_mib, "utilization.gpu": 0}
        return [v.get(f, total_mib - free_mib) for f in fields]

    return smi


def test_a_refused_job_never_starts(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    monkeypatch.setattr(jobs, "_smi", fake_smi(12282, 11800))  # a 12 GB card

    def no_spawn(*a, **k):
        raise AssertionError("a refused job spawned its step")

    monkeypatch.setattr(jobs, "_spawn", no_spawn)
    with TestClient(app) as c:
        asset = c.post("/api/assets", json={"name": "Crate"}).json()["id"]
        body = {"params": {"op": "generate", "weights": "faithful"}}
        job_id = c.post(f"/api/assets/{asset}/steps/trellis2", json=body).json()["job_id"]
        end = time.time() + 10
        while (job := c.get(f"/api/jobs/{job_id}").json())["status"] in ("queued", "running"):
            assert time.time() < end, job
            time.sleep(0.05)
        assert job["status"] == "failed" and job["error"]["code"] == "oom", job
        assert job["error"]["suggest"] == {"weights": "fast"}
        assert c.get(f"/api/assets/{asset}").json()["versions"] == []


def test_the_cap_is_set_only_when_asked(monkeypatch):
    calls = []
    cuda = SimpleNamespace(
        is_available=lambda: True,
        get_device_properties=lambda i: SimpleNamespace(total_memory=16 * 2**30),
        set_per_process_memory_fraction=lambda f: calls.append(f),
    )
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=cuda))
    monkeypatch.delenv("EFFIGY_VRAM_BUDGET_GB", raising=False)
    _common._cap_vram()
    assert calls == []  # a light step: torch not touched
    monkeypatch.setenv("EFFIGY_VRAM_BUDGET_GB", "14.40")
    _common._cap_vram()
    assert calls == [pytest.approx(0.9)]
    assert _common.budget_gb() == 14.4  # M46a: what the step reads to keep a model on the GPU
    _common._cap_vram(22.7)  # a --serve request's own
    assert _common.budget_gb() == 22.7


def test_a_step_offloads_by_its_param_then_by_the_budget(monkeypatch):
    """M46a: "offload" true or false decides (true: LOWER's retry after an OOM); else a model is kept whole on the
    GPU only on a budget that holds it, and a run without a cap (a light step, a test) offloads."""
    for budget, params, off in [
        (22.7, {}, False),
        (14.65, {}, True),
        (None, {}, True),
        (22.7, {"offload": True}, True),
        (14.65, {"offload": False}, False),
    ]:
        monkeypatch.setattr(_common, "_budget", budget)
        assert _common.offload(params, 17.0) is off, (budget, params)
    with pytest.raises(_common.StepError):
        _common.offload({"offload": "yes"}, 17.0)
    assert presets.LOWER["canon"] == presets.LOWER["motion"] == {"offload": True}


def test_the_budget_reaches_the_step_process(monkeypatch):
    assert jobs._env(14.333)["EFFIGY_VRAM_BUDGET_GB"] == "14.33"
    monkeypatch.setenv("EFFIGY_VRAM_BUDGET_GB", "99")  # never inherited by a light step
    assert jobs._env(None)["EFFIGY_VRAM_BUDGET_GB"] == ""


# Card layouts the app meets (nvidia-smi's MiB; what Windows and the display hold): what Setup says, what is
# refused, and what a job may use when it starts. Needs from presets.VRAM_GB: trellis2 fast 9.0 / faithful 13.3,
# concepts 8.5, canon 9.2; the 3D model by its presets (M46b: faithful-hd 19.4 is for bigger cards, not a warning).
HQ_HD = {"faithful-hq", "faithful-hd"}
LAYOUTS = [
    # name, total MiB, used by others MiB, Setup level, refused variants, live budget GB
    (
        "RTX 4060 Laptop 8 GB",
        8188,
        450,
        "bad",
        {
            "fast",
            "faithful",
            "concepts",
            "canon",
            "lite",  # refine's Tiles
            "full",
            "redraw",
            "depth",
            "layers",
        }  # M55: MoGe-3 9.0, its inpaint 7.8
        | HQ_HD,
        6.76,
    ),
    (
        "RTX 3060 12 GB desktop",
        12288,
        800,
        "warn",
        {"faithful", "lite", "full", "redraw"} | HQ_HD,
        10.42,
    ),
    # M46b/c: the HD preset and the full Tile are for bigger cards: listed, no warning
    ("RTX 5080 Laptop 16 GB (reference)", 16303, 470, "ok", {"faithful-hd", "full"}, 14.66),
    (
        "RTX 4080 16 GB, two 4K monitors + a browser",
        16376,
        2600,
        "ok",
        {"faithful-hd", "full"},
        12.65,
    ),
    ("RTX 4090 24 GB", 24564, 600, "ok", set(), 22.60),
    ("RTX 5090 32 GB", 32607, 500, "ok", set(), 30.55),
    ("RTX 6000 Ada 48 GB, no display", 49140, 0, "ok", set(), 47.19),
]


@pytest.mark.parametrize(
    "name,total_mib,used_mib,level,refused,live", LAYOUTS, ids=[x[0] for x in LAYOUTS]
)
def test_card_layouts(monkeypatch, name, total_mib, used_mib, level, refused, live):
    total, free = total_mib / 1024, (total_mib - used_mib) / 1024
    monkeypatch.setattr(machine, "_ram_gb", lambda: 31.7)
    monkeypatch.setattr(machine, "_long_paths", lambda: None)
    monkeypatch.setattr(machine, "_gpu", lambda: (name, total, "617.42"))
    monkeypatch.setattr(machine, "sm", lambda: 8.9)
    gpu = machine.check()[0]
    assert gpu["level"] == level
    too_big = {b["value"] or b["step"] for b in gpu["too_big"]}
    assert too_big == refused
    assert presets.budget_gb(total, free) == pytest.approx(live, abs=0.01)
    # what Setup marks is what the pre-flight refuses on an idle card; everything else starts
    for step, params, key in (
        ("trellis2", {"weights": "fast"}, "fast"),
        ("trellis2", {"weights": "faithful"}, "faithful"),
        ("concepts", {}, "concepts"),
        ("canon", {}, "canon"),
        ("trellis2", presets.PRESETS["trellis2"]["faithful-hd"], "faithful-hd"),
    ):
        need = presets.need_gb(step, params)
        idle = jobs._refusal(step, params, need, total, total - presets.DESKTOP_GB)
        assert (idle is not None) == (key in refused), (step, params)
    # the busy desktop: Faithful waits for the monitors' memory, Fast still runs
    if name.startswith("RTX 4080"):
        busy = jobs._refusal("trellis2", {"weights": "faithful"}, 13.3, total, free)
        assert "the GPU is busy" in busy["message"] and busy["suggest"] == {"weights": "fast"}
        assert jobs._refusal("trellis2", {"weights": "fast"}, 9.0, total, free) is None


def test_the_best_fit_is_the_default_and_a_1536_job_steps_down(monkeypatch, tmp_path):
    """M46b: the best preset a card fits (quality first on every card, the owner 2026-10-13); 1536 needs its own entry
    (Fast's measured lower bound, Faithful's est.) and RAM, and a refused 1536 job is told to go back to 1024 first."""
    hd, hq = presets.PRESETS["trellis2"]["faithful-hd"], presets.PRESETS["trellis2"]["faithful-hq"]
    assert presets.GENERATE[:4] == ["faithful-hd", "faithful-hq", "faithful", "fast"]
    for budget, best in [
        (30.5, "faithful-hd"),
        (22.6, "faithful-hd"),
        (14.66, "faithful-hq"),
        (10.42, "fast"),
    ]:
        assert presets.best("trellis2", budget) == best, budget
    assert (
        presets.best("trellis2", 6.76) == presets.best("trellis2", None) == "fast"
    )  # the lightest: the job says why
    assert presets.need_gb("trellis2", hd) == 19.4 and presets.need_gb("trellis2", hq) == 13.3
    assert presets.need_gb("trellis2", {"weights": "fast", "pipeline_type": "1536_cascade"}) == 15.1
    assert presets.ram_need("trellis2", hd) == 28 and presets.ram_need("trellis2", hq) == 20
    assert presets.lighter("trellis2", 14.66, hd) == {"pipeline_type": "1024_cascade"}
    assert presets.lighter("trellis2", 10.42, hd) == {
        "pipeline_type": "1024_cascade",
        "weights": "fast",
    }
    assert presets.lighter("trellis2", 14.66, hq) == {"weights": "faithful"}
    # {"preset": "best"} resolves against this card when the job is submitted
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    monkeypatch.setattr(jobs, "_smi", fake_smi(24564, 24000))
    monkeypatch.setattr(
        jobs, "_spawn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("not here"))
    )
    with TestClient(app) as c:
        asset = c.post("/api/assets", json={"name": "Hero"}).json()["id"]
        for total, params, want in [
            (24564, {"preset": "best", "seed": 3}, hd | {"seed": 3}),
            (16303, {"preset": "best"}, hq),
        ]:
            monkeypatch.setattr(jobs, "_smi", fake_smi(total, total - 500))
            job, _ = jobs.submit(asset, "trellis2", None, params)
            assert job.params == want
            end = time.time() + 10
            while job.status in (
                "queued",
                "running",
            ):  # it ends (no step here) before the library goes away
                assert time.time() < end
                time.sleep(0.05)


def test_an_rtx_30_card_is_told_the_3d_steps_are_not_supported(monkeypatch, tmp_path):
    """M46b: our CUDA wheels are built for 8.9 and 12.0 only: an RTX 3090 (8.6) is no 'ok' card for the 3D model;
    Setup says so, the generator menu greys every preset out with the reason, the 3D steps are refused, the rest runs."""
    monkeypatch.setattr(machine, "_ram_gb", lambda: 63.7)
    monkeypatch.setattr(machine, "_long_paths", lambda: None)
    monkeypatch.setattr(machine, "_gpu", lambda: ("NVIDIA GeForce RTX 3090", 24.0, "617.42"))
    monkeypatch.setattr(machine, "sm", lambda: 8.6)
    gpu = machine.check()[0]
    assert (
        gpu["level"] == "warn"
        and "No 3D model can be made" in gpu["detail"]
        and "8.6" in gpu["detail"]
    )
    marked = {b["value"]: b.get("reason") for b in gpu["too_big"] if b["step"] == "trellis2"}
    assert set(marked) == set(presets.GENERATE) and set(marked.values()) == {machine.UNSUPPORTED}
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    monkeypatch.setattr(jobs, "_smi", fake_smi(24576, 24000))

    def no_spawn(*a, **k):
        raise AssertionError("an unsupported step spawned")

    monkeypatch.setattr(jobs, "_spawn", no_spawn)
    with TestClient(app) as c:
        asset = c.post("/api/assets", json={"name": "Crate"}).json()["id"]
        job_id = c.post(f"/api/assets/{asset}/steps/clean", json={"params": {}}).json()["job_id"]
        end = time.time() + 10
        while (job := c.get(f"/api/jobs/{job_id}").json())["status"] in ("queued", "running"):
            assert time.time() < end, job
            time.sleep(0.05)
        assert (
            job["error"]["code"] == "unsupported" and "RTX 40 or 50" in job["error"]["message"]
        ), job


def test_two_gpus_the_steps_run_on_the_bigger_one(monkeypatch):
    # a GTX 1060 6 GB drives the display (PCI bus first), an RTX 3090 24 GB computes
    gpus = [
        {"index": 0, "memory.total": 6144, "memory.free": 5200, "memory.used": 512},
        {"index": 1, "memory.total": 24576, "memory.free": 24000, "memory.used": 0},
    ]

    def smi(cmd, **k):  # nvidia-smi answers the fields it is asked for, one line per GPU
        fields = cmd[1].removeprefix("--query-gpu=").split(",")
        return "".join(", ".join(str(g[f]) for f in fields) + "\n" for g in gpus)

    monkeypatch.setattr(jobs.subprocess, "check_output", smi)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    assert jobs._smi("memory.free", "memory.used") == [24000, 0]
    assert jobs._pin_gpu() == {"CUDA_DEVICE_ORDER": "PCI_BUS_ID", "CUDA_VISIBLE_DEVICES": "1"}
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")  # the user's own choice wins
    assert jobs._pin_gpu() == {}

    smi = "NVIDIA GeForce GTX 1060, 6144, 617.42\nNVIDIA GeForce RTX 3090, 24576, 617.42\n"
    monkeypatch.setattr(
        machine.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=smi, returncode=0)
    )
    assert machine._gpu() == ("NVIDIA GeForce RTX 3090", 24.0, "617.42")


def test_no_nvidia_gpu(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(jobs.subprocess, "check_output", missing)
    monkeypatch.setattr(machine.subprocess, "run", missing)
    monkeypatch.setattr(jobs.gpu_amd, "adapter", lambda: None)  # no AMD card either
    assert jobs._smi("memory.free") is None and jobs._pin_gpu() == {}
    assert machine._gpu() is None
    job = SimpleNamespace(step="trellis2", params={"weights": "faithful"}, budget=None)
    assert jobs._preflight(job) is None  # nothing to ask: the step reports its own error


def test_amd_gpu_without_nvidia_smi(monkeypatch):
    """AMD: an AMD card answers what nvidia-smi would (server/gpu_amd.py); no pinning, no driver gate."""

    def missing(*a, **k):
        raise FileNotFoundError("nvidia-smi")

    card = {
        "name": "AMD Radeon RX 9070 XT",
        "total_mib": 16304.0,
        "driver": "32.0.22042.14002",
        "luid": "x",
    }
    monkeypatch.setattr(jobs.subprocess, "check_output", missing)
    monkeypatch.setattr(machine.subprocess, "run", missing)
    monkeypatch.setattr(jobs.gpu_amd, "adapter", lambda: card)
    monkeypatch.setattr(jobs.gpu_amd, "used_mib", lambda: 3600.0)
    monkeypatch.setattr(jobs.gpu_amd.shutil, "which", lambda exe: None)
    assert jobs._smi("memory.total", "memory.free") == [16304.0, 16304.0 - 3600.0]
    assert jobs._smi("utilization.gpu", "memory.used") == [None, 3600.0]
    assert jobs._pin_gpu() == {}  # HIP numbers the devices itself
    assert machine._gpu() == ("AMD Radeon RX 9070 XT", 16304.0 / 1024, "32.0.22042.14002")
    assert jobs.gpu_amd.active()
    assert "backend:native" in jobs._env()["PYTORCH_CUDA_ALLOC_CONF"]
    driver = next(i for i in machine.check() if i["id"] == "driver")
    assert driver["level"] == "ok"


def test_ram_is_checked_like_vram(monkeypatch):
    total, free = presets.ram_gb()
    assert total >= free > 0
    monkeypatch.setattr(jobs, "_smi", fake_smi(16303, 15500))
    monkeypatch.setattr(jobs, "_worker", None)
    fast = SimpleNamespace(step="trellis2", params={"weights": "fast"}, budget=None)
    monkeypatch.setattr(presets, "ram_gb", lambda: (31.7, 12.0))  # a game and a browser hold 20 GB
    err = jobs._preflight(fast)
    assert err["code"] == "oom" and "free memory (RAM)" in err["message"]
    assert "12.0 of 32 GB free" in err["message"] and "suggest" not in err  # Fast needs it too
    monkeypatch.setattr(presets, "ram_gb", lambda: (31.7, 24.0))
    assert jobs._preflight(fast) is None
    # the resident worker already holds Fast: nothing to load, no RAM needed; Faithful would be a fresh
    # process (the worker is evicted first), so its RAM is checked again
    worker = SimpleNamespace(
        step="trellis2", key=("trellis2", "fast"), proc=SimpleNamespace(poll=lambda: None)
    )
    monkeypatch.setattr(jobs, "_worker", worker)
    monkeypatch.setattr(presets, "ram_gb", lambda: (31.7, 5.0))
    faithful = SimpleNamespace(step="trellis2", params={"weights": "faithful"}, budget=None)
    assert jobs._resident(fast) and not jobs._resident(faithful)
    assert jobs._preflight(fast) is None and jobs._preflight(faithful)["code"] == "oom"
    assert jobs._key(SimpleNamespace(step="trellis2", params={})) == (
        "trellis2",
        "fast",
    )  # the default
    assert jobs._key(SimpleNamespace(step="concepts", params={})) == ("concepts", None)


def test_evict_waits_for_the_dead_workers_ram(monkeypatch):
    # free RAM as Windows hands the 18 GB back: 6 → 15 → 24 → 24.1 (settled: a rise under 0.2 GB)
    readings = [(31.7, 6.0), (31.7, 15.0), (31.7, 24.0), (31.7, 24.1), (31.7, 24.1)]
    monkeypatch.setattr(presets, "ram_gb", lambda: readings.pop(0))
    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)
    monkeypatch.setattr(jobs, "_kill_tree", lambda p: None)
    monkeypatch.setattr(jobs, "publish", lambda *a: None)
    monkeypatch.setattr(jobs, "_free_gb", lambda: 15.0)
    proc = SimpleNamespace(poll=lambda: 0, wait=lambda: 0)
    monkeypatch.setattr(
        jobs, "_worker", SimpleNamespace(step="trellis2", key=("trellis2", "fast"), proc=proc)
    )
    jobs.evict()
    assert jobs._worker is None and len(readings) == 1  # four readings taken, the last one unused


def test_a_warm_run_reads_what_other_programs_took_since_the_worker_went_idle(monkeypatch):
    """BE-006: a warm resident job's free VRAM is the cold start's, less what left the GPU since the worker
    went idle; a game that came meanwhile makes it busy; a worker without a baseline is taken to hold all."""
    from types import SimpleNamespace

    job = jobs.Job("a", "trellis2", None, {"weights": "fast"})  # needs 9.0 GB
    worker = SimpleNamespace(
        key=jobs._key(job), proc=SimpleNamespace(poll=lambda: None), free_cold=15.4, idle_free=10.0
    )
    monkeypatch.setattr(jobs, "_worker", worker)
    monkeypatch.setattr(jobs, "_smi", lambda *f: [LAPTOP * 1024, 10.0 * 1024])  # nothing changed
    assert jobs._preflight(job) is None and job.budget == pytest.approx(
        presets.budget_gb(LAPTOP, 15.4)
    )
    monkeypatch.setattr(jobs, "_smi", lambda *f: [LAPTOP * 1024, 3.0 * 1024])  # a game took 7 GB
    refused = jobs._preflight(job)
    assert (
        refused
        and "busy" in refused["message"]
        and job.budget == pytest.approx(presets.budget_gb(LAPTOP, 8.4))
    )
    worker.idle_free = None  # no baseline yet: the old assumption, everything used is the worker's
    assert jobs._preflight(job) is None
    monkeypatch.setattr(jobs, "_worker", None)  # a cold start keeps its free reading for the worker
    monkeypatch.setattr(jobs, "_smi", lambda *f: [LAPTOP * 1024, 15.4 * 1024])
    monkeypatch.setattr(
        presets, "ram_gb", lambda: (31.0, 31.0)
    )  # the cold path reads RAM: not this machine's
    assert jobs._preflight(job) is None and job.free_gb == pytest.approx(15.4)


def test_a_resident_request_carries_the_budget_and_the_worker_caps_itself(monkeypatch, tmp_path):
    """BE-006: the request line holds the job's budget; a --serve worker sets torch's cap from it."""
    import io
    import json
    import sys
    from types import SimpleNamespace

    from steps import _common

    job = jobs.Job("a", "trellis2", None, {"weights": "fast"})
    job.budget = 9.5
    rid = job.id
    stdin, stdout = (
        io.StringIO(),
        iter([f"RESULT {rid} " + json.dumps({"outputs": {}, "metrics": {}}) + "\n"]),
    )
    worker = SimpleNamespace(
        key=jobs._key(job),
        proc=SimpleNamespace(poll=lambda: None, stdin=stdin, stdout=stdout),
        job=None,
    )
    monkeypatch.setattr(jobs, "_worker", worker)
    monkeypatch.setattr(jobs, "_free_gb", lambda: 12.0)
    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)
    assert jobs._run_resident(job, None, tmp_path)[0] == "RESULT"
    assert json.loads(stdin.getvalue())["budget"] == 9.5 and worker.idle_free == 12.0

    fractions = []
    fake = SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: True,
            get_device_properties=lambda i: SimpleNamespace(total_memory=16 * 2**30),
            set_per_process_memory_fraction=fractions.append,
            empty_cache=lambda: None,
        )
    )
    monkeypatch.setitem(sys.modules, "torch", fake)
    monkeypatch.delenv("EFFIGY_VRAM_BUDGET_GB", raising=False)
    out = tmp_path / "o"
    reqs = [
        {"id": "1", "op": "run", "out": str(out), "budget": 8.0},
        {"id": "2", "op": "run", "out": str(out)},
        {"op": "quit"},
    ]
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(json.dumps(r) + "\n" for r in reqs)))
    monkeypatch.setattr(_common, "_out", io.StringIO())
    assert _common._serve(lambda op, i, o, p: {"outputs": {}}) == 0
    assert fractions == [0.5]  # the second request, without a budget, keeps the cap
