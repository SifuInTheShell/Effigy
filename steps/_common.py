"""Step CLI protocol (docs/IMPLEMENTATION.md §3.1). Every step calls main(run).

    python -m steps.<name> [--in DIR] --out DIR [--params JSON] [--serve]

A handler is run(op, in_dir, out_dir, params) -> {"outputs", "metrics", "models"?, "licenses"?, "note"?}.
"""

import argparse
import faulthandler
import json
import os
import sys
from pathlib import Path

import numpy as np

# Protocol lines go to a private copy of fd 1; fd 1 itself (print, C extensions) goes to stderr.
# Only inside a step process (`python -m steps.<name>`): tests import step modules and keep their fds.
_main = getattr(sys.modules.get("__main__"), "__spec__", None)
if _main and _main.name.startswith("steps."):
    sys.stdout.flush()
    _out = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
else:
    _out = sys.stdout

_req_id = None  # set while a --serve request runs, prefixed to every protocol line

EXIT = {"bad_params": 2, "missing_input": 2, "oom": 3, "failed": 1}


class StepError(Exception):
    """Raise from a handler to fail with a specific code (see EXIT)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _emit(kind: str, payload: str) -> None:
    prefix = f"{kind} {_req_id} " if _req_id else f"{kind} "
    _out.write((prefix + payload).rstrip() + "\n")


def progress(p: float, text: str = "") -> None:
    _emit("PROGRESS", f"{max(0.0, min(1.0, p)):.3f} {text}".rstrip())


def _is_oom(e: BaseException) -> bool:
    # torch.cuda.OutOfMemoryError without importing torch here
    return type(e).__name__ == "OutOfMemoryError" or "out of memory" in str(e).lower()


def _call(run, op: str, in_dir, out_dir: str, params: dict) -> tuple[str, dict]:
    """Run one request; returns ("RESULT", payload) or ("ERROR", payload)."""
    if in_dir is not None and not os.path.isdir(in_dir):
        return "ERROR", {"code": "missing_input", "message": f"--in not found: {in_dir}"}
    os.makedirs(out_dir, exist_ok=True)
    # a request still running after 10 min prints every thread's stack to stderr (jobs.log), every
    # 10 min: a single-view Faithful run once spun > 25 min on one core and couldn't be reproduced
    faulthandler.dump_traceback_later(600, repeat=True)
    try:
        res = run(op, in_dir, out_dir, params)
    except StepError as e:
        return "ERROR", {"code": e.code, "message": str(e)}
    except Exception as e:  # noqa: BLE001 - the protocol must report every failure
        code = "oom" if _is_oom(e) else "failed"
        return "ERROR", {"code": code, "message": f"{type(e).__name__}: {e}"}
    finally:
        faulthandler.cancel_dump_traceback_later()
    res = res or {}
    extra = {k: res[k] for k in ("models", "licenses", "note") if k in res}
    return "RESULT", {"outputs": res.get("outputs", {}), "metrics": res.get("metrics", {})} | extra


def _release_cache() -> None:
    torch = sys.modules.get("torch")  # only if the step imported it
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()


def _serve(run) -> int:
    global _req_id
    _emit("READY", "")
    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        if req.get("op") == "quit":
            return 0
        _req_id = req["id"]
        _cap_vram(req.get("budget"))  # this run's cap, from the backend's pre-flight (BE-006)
        kind, payload = _call(run, req["op"], req.get("in"), req["out"], req.get("params") or {})
        _emit(kind, json.dumps(payload))
        _req_id = None
        _release_cache()  # the next steps run in other processes and need the VRAM
    return 0


_budget: float | None = (
    None  # this run's budget (GB): EFFIGY_VRAM_BUDGET_GB, or a --serve request's
)


def root_meta(version: Path) -> dict:
    """The meta of the first version of `version`'s chain (a brief, an upload); {} when the chain is broken."""
    while (version / "meta.json").is_file():
        meta = json.loads((version / "meta.json").read_text(encoding="utf-8"))
        if not meta.get("parent"):
            return meta
        version = version.parent / meta["parent"]
    return {}


def budget_gb() -> float | None:
    """M46a: what this run may allocate on the GPU (GB), None when the backend set no cap. A step keeps a model on
    the GPU instead of offloading it when its measured need fits."""
    return _budget


def offload(params: dict, gpu_gb: float) -> bool:
    """M46a: offload (move parts of the model to the CPU between uses)? params "offload" true or false decides (true is
    the retry after an out-of-memory error, server.presets.LOWER); else only when this run's budget is below the
    `gpu_gb` the whole model needs on the GPU. A wrong threshold costs one out-of-memory error, not a spill (the cap)."""
    if (forced := params.get("offload")) is not None:
        if not isinstance(forced, bool):
            raise StepError(
                "bad_params", "offload: true or false (or leave it out: the card's budget decides)"
            )
        return forced
    return (_budget or 0) < gpu_gb


def _cap_vram(gb: float | str | None = None) -> None:
    """M45: torch in this process allocates at most `gb` (a --serve request's budget) or EFFIGY_VRAM_BUDGET_GB
    (the backend sets it for GPU steps), so going over raises OutOfMemoryError (code oom) instead of Windows
    spilling into shared memory, which crawled and once restarted the laptop. Both of torch's allocators
    honour the fraction, and it can be set again per request (audit BE-006)."""
    global _budget
    gb = gb or os.environ.get("EFFIGY_VRAM_BUDGET_GB")
    _budget = float(gb) if gb else None
    if not gb:
        return
    import torch

    if torch.cuda.is_available():
        total = torch.cuda.get_device_properties(0).total_memory
        torch.cuda.set_per_process_memory_fraction(min(1.0, float(gb) * 2**30 / total))


def main(run) -> None:
    from steps import _rocm

    _rocm.install()  # AMD: large GEMMs on ROCm/Windows, a block of rows at a time (no-op on CUDA)
    _cap_vram()  # once per process: a --serve worker keeps it for every request
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_dir")
    ap.add_argument("--out")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--serve", action="store_true")
    a = ap.parse_args()
    if a.serve:
        sys.exit(_serve(run))
    try:
        params = json.loads(
            Path(a.params[1:]).read_text(encoding="utf-8") if a.params.startswith("@") else a.params
        )
        if not isinstance(params, dict) or not a.out:
            raise ValueError("--out and a JSON object --params are required")
    except (ValueError, OSError) as e:
        _emit("ERROR", json.dumps({"code": "bad_params", "message": str(e)}))
        sys.exit(EXIT["bad_params"])
    kind, payload = _call(run, params.pop("op", "run"), a.in_dir, a.out, params)
    _emit(kind, json.dumps(payload))
    sys.exit(0 if kind == "RESULT" else EXIT.get(payload["code"], 1))


# --- §3.3 ovoxel.npz: the volume TRELLIS decoded, kept so any later mesh can be re-baked ----------

OVOXEL_KEYS = ("base_color", "metallic", "roughness", "alpha")


def save_ovoxel(path, coords, attrs, layout: dict, resolution: int) -> None:
    """coords (N,3) voxel indices, attrs (N,C), layout {attribute: [start, end]} in the TRELLIS frame."""
    coords, attrs = np.asarray(coords, np.int32), np.asarray(attrs, np.float16)
    assert coords.ndim == 2 and coords.shape[1] == 3 and len(attrs) == len(coords), (
        coords.shape,
        attrs.shape,
    )
    assert (
        set(layout) == set(OVOXEL_KEYS) and max(e for _, e in layout.values()) <= attrs.shape[1]
    ), layout
    np.savez(
        path,
        coords=coords,
        attrs=attrs,
        layout=json.dumps(layout),
        resolution=np.int32(resolution),
        aabb=np.array([[-0.5] * 3, [0.5] * 3], np.float32),
        voxel_size=np.full(3, 1 / resolution, np.float32),
        frame="trellis",
    )


def load_ovoxel(path) -> dict:
    with np.load(path) as z:
        if str(z["frame"]) != "trellis":
            raise StepError("failed", f"{path}: frame {z['frame']!s}, expected trellis")
        out = {k: z[k] for k in ("coords", "attrs", "aabb", "voxel_size")}
        return out | {"layout": json.loads(str(z["layout"])), "resolution": int(z["resolution"])}


# --- §3.4 frames: TRELLIS (unit cube, Z up) ↔ library (glTF: Y up, metres, base centre at origin) ---

TRELLIS_TO_GLTF = np.array(
    [[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], float
)  # (x, z, -y): upstream to_glb's own swap
# Pixal3D's volume is glTF's frame already: Y up, facing +Z (the camera). The (-x, y, -z) read from its
# inference.py turned the first real output (2026-10-04) around to show its back.
PIXAL3D_TO_GLTF = np.eye(4)


def trellis_to_library(bbox_min, bbox_max, height_m: float = 1.0, to_gltf=TRELLIS_TO_GLTF):
    """4×4 for a mesh with this engine-frame bbox: axes → glTF (Y up), base centre → origin, height →
    metres. to_gltf: the engine's axis change (a signed permutation)."""
    r = to_gltf[:3, :3]
    a, b = r @ np.asarray(bbox_min, float), r @ np.asarray(bbox_max, float)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    move = np.eye(4)
    move[:3, 3] = -np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2])
    scale = np.diag([height_m / (hi[1] - lo[1])] * 3 + [1.0])
    return scale @ move @ to_gltf


def library_to_trellis(m: np.ndarray) -> np.ndarray:
    return np.linalg.inv(m)
