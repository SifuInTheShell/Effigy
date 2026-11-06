"""Named param sets per step, what to suggest after an OOM, and what each GPU step needs of the GPU (M45); the
best preset a card fits (M46b)."""

import os

PRESETS = {
    "trellis2": {
        # Best first: the default is the first one this card fits (`best`; the owner, 2026-10-13: "quality over
        # speed should be the default, but the user may choose other"); UI menus list them in this order.
        # M46b: Faithful at 1536 (finer geometry and O-Voxel volume), 4K: 24 GB cards
        "faithful-hd": {
            "op": "generate",
            "weights": "faithful",
            "pipeline_type": "1536_cascade",
            "fov": 0.2,
            "steps": 16,
            "texture_size": 4096,
        },
        # M46b: 20 steps and 4K, the same peak as 12 steps and 2K on the laptop, +25 % time (docs/bench/m46.md)
        "faithful-hq": {
            "op": "generate",
            "weights": "faithful",
            "pipeline_type": "1024_cascade",
            "fov": 0.2,
            "steps": 20,
            "texture_size": 4096,
        },
        # Faithful over Fast: the owner, after the M11 A/B
        "faithful": {
            "op": "generate",
            "weights": "faithful",
            "pipeline_type": "1024_cascade",  # Pixal3D only has the cascades
            "fov": 0.2,
            "steps": 12,
            "texture_size": 2048,
        },
        "fast": {
            "op": "generate",
            "weights": "fast",
            "pipeline_type": "1024_cascade",
            "steps": 12,
            "texture_size": 2048,
        },
        # M12: run on a UV-mapped mesh version; image = a prep version (default: the one up the chain)
        "retexture": {"op": "texture", "resolution": 1024, "steps": 12, "texture_size": 2048},
    },
    # M43b texture detail: the defaults; not in CHAINS until the owner accepts it on the bench
    "refine": {"detail": {}},
}
GENERATE = [name for name, p in PRESETS["trellis2"].items() if p["op"] == "generate"]
# M46c: a download group only a bigger card can use, and the (step, params) whose need decides: Setup offers it, and
# "Everything" includes it, only where that fits (server/install)
GROUP_NEED = {"refine-full": ("refine", {"tile": "full"})}
# M46b: the card the app is built for (the RTX 5080 Laptop, 15.92 GB): a setting it cannot run is an extra for bigger
# cards, never a warning (server/machine.check)
REFERENCE_GB = 15.92

# job.error["suggest"] after an OOM (exit 3): the next-lower params for the same step
LOWER = {
    "trellis2": {
        "weights": "fast",
        "pipeline_type": "512",
        "resolution": 512,
        "texture_size": 1024,
    },
    "refine": {"texture_size": "keep", "tile": "lite"},
    "redraw": {"window": 768},  # M61: smaller windows for the same picture
    # M46a: a model kept on the GPU because the budget seemed to hold it: offload it instead
    "canon": {"offload": True},
    "motion": {"offload": True},
}

# M45: the VRAM (GB) a GPU step's own process peaks at on the reference laptop: torch's peak, or its reserved
# pool where a bench has it. A number, or (param, the step's default, {value: GB}) when a param picks the
# model. The light steps (server/jobs.LIGHT) need none; any other step not listed counts as needing the
# whole budget until it is measured.
VRAM_GB = {
    "textures": (
        "op",
        "upscale",
        {"upscale": 2.0, "generate": 8.5, "enhance": 12.3, "retexture": 9.0},
    ),
    # phase0.md: Fast's decode peak 8.1, 9.0 reserved under cudaMallocAsync; m2.md: Faithful 13.31
    "trellis2": ("weights", "fast", {"fast": 9.0, "faithful": 13.3}),
    # m43b.md: the lite Tile 12.2 reserved, the full one 15.57 (it restarted the laptop before the cap; M46c: its own
    # download group, offered where it fits)
    "refine": ("tile", "lite", {"lite": 12.3, "full": 15.6}),
    # M61: op redraw is refine's lite Tile at 1024 px windows (12.3, whatever the picture's size); op upscale one
    # Real-ESRGAN window (1536 x 1024 x2: torch peak 9.3 GB redraw, card 13.6 GB; upscale ~2 est.)
    "redraw": ("op", "redraw", {"redraw": 12.3, "upscale": 2.0}),
    "concepts": 8.5,  # m10.md: Z-Image fp8, resident 8.42; m50.md: op image 8.29 at 1920 x 1088, 8.44 with a style
    "canon": 9.2,  # m16.md: FLUX.2-klein 9.23, offloaded; whole on the GPU from a budget of 17 GB (M46a)
    "prep": 3.3,  # phase0.md: every step but trellis2 ≤ 3.3 whole-GPU
    "bake": 4.8,  # phase0.md 3.3 + M43a's AO rays, 1.5 (m43a.md)
    "project": 1.3,  # m23.md
    "parts": 1.8,  # m31.md / m32.md: GeoSAM2 segment 1.76
    "rig_skin": 3.4,  # m20.md: SkinTokens 3.43
    "mocap": 4.3,  # m34.md: GEM-X 4.31
    # m26.md: the Kimodo denoiser, its text encoder on the CPU. From a budget of 18 GB the encoder goes on the GPU too
    # (M46a, steps/motion.ENCODER_GPU_GB) and a resident worker holds ~17 GB until it is evicted or idles out
    "motion": 1.0,
    "rig_fit": 0,  # numpy and Blender, no torch
    "depth": 9.0,  # m55.md: MoGe-3 ViT-g 8.93
    "layers": 7.8,  # m55.md: Z-Image's inpaint 7.70 at 2048 x 768 (the VAE tiled)
}
# M46b: trellis2 at 1536 (`pipeline_type` 1536_cascade) by its weights. Fast: a lower bound, the whole card's peak
# on the laptop (torch 12.6 inside its cap, the card at 15.5 of 15.9 GB, the worker once ended without a word; m46.md).
# Faithful: est., Fast's plus what Faithful takes over Fast at 1024 (13.3 - 9.0), until a user's report measures it.
HD_GB = {"fast": 15.1, "faithful": 19.4}


# Measured on the reference laptop (2026-10-10): Windows + the desktop held 0.47 GB, a CUDA process's context +
# cuBLAS workspace 0.27 GB outside torch's cap; the full refine Tile reserved 15.57 of 15.92 GB, overflowed the
# card by ~0.4 GB and restarted Windows. So the cap is what is free now minus the context and a margin.
DESKTOP_GB, CONTEXT_GB, MARGIN_GB = 0.5, 0.3, 0.5


def budget_gb(total: float, free: float | None = None) -> float:
    """The most one step process may allocate (its torch cap, server/jobs) on a GPU with `total` GB, `free` of
    them free right now (None: assume a typical desktop). EFFIGY_VRAM_RESERVE_GB fixes the reserve instead."""
    if fixed := os.environ.get("EFFIGY_VRAM_RESERVE_GB"):
        cap = total - float(fixed)
        return cap if free is None else min(cap, free - CONTEXT_GB - MARGIN_GB)
    return (total - DESKTOP_GB if free is None else free) - CONTEXT_GB - MARGIN_GB


def reserve_gb(total: float) -> float:
    """What a typical machine keeps out of a step's reach: Windows and the display, the CUDA context, a margin."""
    return total - budget_gb(total)


# M45: the host RAM (GB, the process's peak) a step needs to load its models in a fresh process, measured on
# the 32 GB laptop (docs/bench/m45.md): trellis2 Faithful 19.3–21.7, Fast 18.4 (21.3 after an in-process swap,
# which jobs.py no longer does). The pre-flight refuses a step while less than this is free: Windows paged and
# the worker died or crawled at ~3 GB free. Not listed: not measured, no check.
RAM_GB = {"trellis2": 20}
# M46b: at 1536, est.: the worker's ~18 GB plus the 1536 mesh (23.2 M faces against 10.8 M; host RAM fell to ~2 GB
# free on the 32 GB laptop, m46.md)
RAM_HD_GB = 28


def ram_need(step: str, params: dict) -> float | None:
    """The host RAM (GB) `step` needs to load its models in a fresh process with `params`; None: not measured."""
    if step == "trellis2" and params.get("pipeline_type") == "1536_cascade":
        return RAM_HD_GB
    return RAM_GB.get(step)


def ram_gb() -> tuple[float, float]:
    """(total, free) GB of host RAM."""
    if os.name == "nt":
        import ctypes

        class Status(ctypes.Structure):  # MEMORYSTATUSEX
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (n, ctypes.c_ulonglong)
                for n in ("total", "free", "page", "page_free", "virt", "virt_free", "ext")
            ]

        s = Status()
        s.length = ctypes.sizeof(Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
        return s.total / 2**30, s.free / 2**30
    page = os.sysconf("SC_PAGE_SIZE")
    return page * os.sysconf("SC_PHYS_PAGES") / 2**30, page * os.sysconf("SC_AVPHYS_PAGES") / 2**30


def variant(step: str, params: dict):
    """The value of the param that picks `step`'s model (trellis2's `weights`), or None for a single model."""
    row = VRAM_GB.get(step)
    return params.get(row[0], row[1]) if isinstance(row, tuple) else None


def variants(step: str) -> tuple[str | None, dict] | None:
    """(the param that picks the variant, {value: GB}) of a measured step; (None, {None: GB}) for one variant."""
    row = VRAM_GB.get(step)
    if row is None:
        return None
    return (row[0], row[2]) if isinstance(row, tuple) else (None, {None: row})


def need_gb(step: str, params: dict) -> float | None:
    """The VRAM `step` needs with `params`; None: not measured. An unknown value counts as the largest."""
    row = VRAM_GB.get(step)
    if not isinstance(row, tuple):
        return row
    key, default, by = row
    value = (
        default if params.get(key) is None else params[key]
    )  # a null is the default, as the step reads it
    need = by.get(value, max(by.values()))
    if step == "trellis2" and params.get("pipeline_type") == "1536_cascade":  # M46b
        need = max(need, HD_GB.get(value, max(HD_GB.values())))
    return need


def lighter(step: str, gb: float, params: dict | None = None) -> dict | None:
    """What to change in `params` so that `step` needs at most `gb`: trellis2 at 1536 back to 1024 (M46b), then the
    largest variant that fits ({"weights": "fast"}); None when nothing fits."""
    params = params or {}
    hd = step == "trellis2" and params.get("pipeline_type") == "1536_cascade"
    down = {"pipeline_type": "1024_cascade"} if hd else {}
    if down and need_gb(step, params | down) <= gb:
        return down
    key, by = variants(step) or (None, {})
    fits = [(need, value) for value, need in by.items() if need <= gb]
    return down | {key: max(fits)[1]} if key and fits else None


def best(step: str, gb: float | None) -> str:
    """M46b: the first of `step`'s generate presets (best first) whose need fits a budget of `gb`; the lightest when
    none does, or with no GPU to ask (`gb` None: the job's own pre-flight then speaks)."""
    names = [n for n, p in PRESETS.get(step, {}).items() if p.get("op") == "generate"]
    if not names:
        raise ValueError(f"{step} has no generate presets to pick the best from")
    fits = [n for n in names if gb is not None and need_gb(step, PRESETS[step][n]) <= gb]
    return fits[0] if fits else names[-1]


# Whole-asset chains (PLAN §5 presets), run by tools/bench.py. "build" steps run one after another, each
# on the previous one's version; every "check" step runs on the last build version.
CHAINS = {
    "game-prop": {  # PLAN §5: tri 2–10k, 1–2K textures, no rig
        "build": [
            ("prep", {}),
            ("trellis2", PRESETS["trellis2"]["faithful"]),
            ("clean", {}),
            ("remesh", {"faces": 10_000}),
            ("uv", {}),
            ("bake", {"texture_size": 2048}),
        ],
        "check": [("validate", {"max_triangles": 10_000}), ("export", {"profile": "godot"})],
    },
    "game-character": {  # PLAN §5: A-pose first, 1024, 15k tris (quads stay opt-in), 2K, rig, clips
        "build": [
            ("prep", {}),
            ("canon", {}),
            ("prep", {}),  # the re-posed character cut out again
            ("trellis2", PRESETS["trellis2"]["faithful"]),
            ("clean", {}),
            ("remesh", {"faces": 15_000}),
            ("uv", {}),
            ("bake", {"texture_size": 2048}),
            ("project", {}),  # M23: the generated image onto the head
            ("rig_fit", {}),
            ("rig_skin", {}),
            ("anim", {}),
        ],
        "check": [("validate", {"max_triangles": 15_000}), ("export", {"profile": "godot"})],
    },
    "game-prop-2d": {  # M54: a prop for a 2D game: the 3D prop drawn from the side in whole pixels, pixel art
        "build": [
            ("prep", {}),
            ("trellis2", PRESETS["trellis2"]["faithful"]),
            ("clean", {}),
            ("remesh", {"faces": 10_000}),
            ("uv", {}),
            ("bake", {"texture_size": 2048}),
            ("sprites", {"yaws": [90], "height_px": 64}),
            ("pixelize", {"scale": 1, "colors": 15, "outline": "darkest"}),
        ],
        "check": [("validate", {}), ("export", {"profile": "godot-2d"})],
    },
    "hero-character": {  # PLAN §5 / M46b: the best Faithful this card runs, quads, 4K textures, rigged
        "build": [
            ("prep", {}),
            ("canon", {}),
            ("prep", {}),
            ("trellis2", {"preset": "best"}),
            ("clean", {}),
            ("remesh", {"faces": 40_000, "topology": "quad"}),
            ("uv", {}),
            ("bake", {"texture_size": 4096}),
            ("project", {}),
            ("rig_fit", {}),
            ("rig_skin", {}),
            ("anim", {}),
        ],
        "check": [("validate", {"max_triangles": 80_000}), ("export", {"profile": "godot"})],
    },
    "game-creature": {  # M36: a four-legged animal: no A-pose (a person's), the fox template and its clips
        "build": [
            ("prep", {}),
            ("trellis2", PRESETS["trellis2"]["faithful"]),
            ("clean", {}),
            ("remesh", {"faces": 15_000}),
            ("uv", {}),
            ("bake", {"texture_size": 2048}),
            ("rig_fit", {"template": "fox"}),
            ("rig_skin", {}),
            ("anim", {"clips": ["Idle", "Walk", "Run"]}),
        ],
        "check": [("validate", {"max_triangles": 15_000}), ("export", {"profile": "godot"})],
    },
}
