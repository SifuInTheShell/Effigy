"""UV-unwrap (IMPLEMENTATION.md M4, M70) → mesh.glb with UVs.

unwrapper "cumesh" (default, GPU): CuMesh charts (cone angle) + xatlas packing;
  params {cone_deg: 90, refine: 0, global_iterations: 1, smooth: 1}.
unwrapper "blender" (CPU, our Blender): normal-cone charts flattened with SLIM, fewer and bigger islands with
  shorter seams on organic meshes, slower (blender_ops/unwrap.py); params {cone_deg: 60, min_faces: 30, margin: 0.002}.
"""

import json
import shutil
from pathlib import Path

import numpy as np

from steps._blender import BlenderError, run_blender
from steps._common import StepError, main, progress
from steps._mesh import read_glb, uv_report, weld, write_glb


def _cumesh_unwrap(v, f, p: dict):
    import cumesh
    import torch

    m = cumesh.CuMesh()
    m.init(torch.tensor(v, dtype=torch.float32).cuda(), torch.tensor(f, dtype=torch.int32).cuda())
    v2, f2, uv, _ = m.uv_unwrap(
        compute_charts_kwargs={
            "threshold_cone_half_angle_rad": float(np.radians(p.get("cone_deg", 90))),
            "refine_iterations": int(p.get("refine", 0)),
            "global_iterations": int(p.get("global_iterations", 1)),
            "smooth_strength": float(p.get("smooth", 1)),
        },
        return_vmaps=True,
    )
    return v2.cpu().numpy(), f2.cpu().numpy(), uv.cpu().numpy()


def _blender_unwrap(v, f, p: dict, out_dir):
    # inside the version: run_blender writes its job, log and result there
    tmp = Path(out_dir) / "_unwrap"
    tmp.mkdir()
    write_glb(tmp / "in.glb", v, f)
    keys = {k: p[k] for k in ("cone_deg", "min_faces", "margin") if k in p}
    try:
        res = run_blender("unwrap", [tmp / "in.glb"], tmp, keys, timeout=1800)
    except BlenderError as e:
        raise StepError("failed", str(e)) from e
    v, f, uv = read_glb(tmp / "mesh.glb")
    if uv is None:
        raise StepError("failed", "the Blender unwrap wrote no UVs (see _unwrap/unwrap.log)")
    shutil.rmtree(tmp)
    return v, f, uv, {"charts": res["charts"], "folded_faces": res["folded"]}


def run(op, in_dir, out_dir, params):
    unwrapper = params.get("unwrapper", "cumesh")
    if unwrapper not in ("cumesh", "blender"):
        raise StepError("bad_params", 'unwrapper must be "cumesh" or "blender"')
    if not in_dir:
        raise StepError("missing_input", "uv needs a parent version with a mesh")
    meta = json.loads((Path(in_dir) / "meta.json").read_text(encoding="utf-8"))
    glb = next((f for f in meta["outputs"].values() if f.endswith(".glb")), None)
    if not glb:
        raise StepError("missing_input", f"{meta['version']} has no .glb")
    v, f = weld(*read_glb(Path(in_dir) / glb)[:2])
    progress(0.2, f"unwrapping {len(f)} faces ({unwrapper})")
    extra = {}
    if unwrapper == "blender":
        v, f, uv, extra = _blender_unwrap(v, f, params, out_dir)
    else:
        v, f, uv = _cumesh_unwrap(v, f, params)
    write_glb(Path(out_dir) / "mesh.glb", v, f, uv)
    rep = uv_report(uv, f, v)
    (Path(out_dir) / "uv_report.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    if not rep["in_0_1"]:
        raise StepError("failed", "unwrap produced UVs outside [0, 1]")
    return {
        "outputs": {"mesh": "mesh.glb", "uv_report": "uv_report.json"},
        "metrics": {"islands": rep["islands"], "unwrapper": unwrapper, **extra},
    }


if __name__ == "__main__":
    main(run)
