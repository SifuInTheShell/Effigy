"""Animation clips on a skinned character (IMPLEMENTATION.md M21): rigged.glb + a clip pack → rigged_anim.glb.

Input: a rig_skin version. params: {"clips": ["Idle_A", "Walk", "Jog"], "clearance": true}: names from the bundled pack
(`catalogue()`, GET /api/clips; the Mesh2Motion human pack, CC0, `tools/download_models.py clips`).
blender_ops/retarget does the work; the output GLB carries one animation per clip, the mesh and the
skeleton of the input, so the viewer plays it and export carries the clips into the engine files.
A creature (M36: a skeleton from steps/_templates.py) takes its template's own clips
(clips/mesh2motion/creatures/<template>-animations.glb) through blender_ops/creature_clips; the human-only
motions (Kimodo, a video) are refused.
M67: on a person, the arms opened where a clip would swing the forearms or hands through the torso
(`steps/_clear.py`; param `clearance`, default true; metrics `arms_opened`).
ponytail: one pack; a second one (KayKit, 100STYLE: manual downloads behind Cloudflare / 4.7 GB) adds a
`pack` param and its preset name when it's there.
"""

import json
import os
import re
import shutil
import sys
import traceback
from pathlib import Path

from steps import _anim, _clear, _templates
from steps._blender import BlenderError, run_blender
from steps._common import StepError, main, progress

MODELS = Path(os.environ.get("EFFIGY_MODELS", Path(__file__).resolve().parents[1] / "models"))
PACK = MODELS / "clips" / "mesh2motion"
DEFAULT = ["Idle_A", "Walk", "Jog"]
MOTION = r"\d{3}_(motion|mocap|import_motion)"  # the versions anim takes as clips
STILL_DEG = 1.0  # a clip whose every bone turns less than this over the whole clip never moves


def clip_names(glb: Path) -> list[str]:
    """The animation names in a GLB, from its JSON chunk, no Blender needed."""
    with open(glb, "rb") as f:
        n = int.from_bytes(f.read(20)[12:16], "little")
        doc = json.loads(f.read(n))
    return [a.get("name", f"clip_{i}") for i, a in enumerate(doc.get("animations", []))]


def catalogue(template: str = _templates.HUMAN) -> list[dict]:
    """Every clip for a skeleton: {"name", "file"} (the human pack, or a creature template's own clips,
    its rest pose left out)."""
    if template != _templates.HUMAN:
        glb = PACK / "creatures" / f"{template}-animations.glb"
        names = clip_names(glb) if glb.is_file() else []
        return [
            {"name": n, "file": f"creatures/{glb.name}"} for n in names if "rest" not in n.lower()
        ]
    return [
        {"name": n, "file": glb.name} for glb in sorted(PACK.glob("*.glb")) for n in clip_names(glb)
    ]


def _creature(src: Path, out: Path, template: str, clips: list[str], yaw: float) -> dict:
    """M36: a creature's clips from its template, bone for bone (blender_ops/creature_clips)."""
    if any(re.fullmatch(MOTION, c) for c in clips):
        raise StepError(
            "bad_params",
            "motions from text, a video or a file are for people; choose this animal's clips",
        )
    known = {c["name"]: c["file"] for c in catalogue(template)}
    if not known:
        raise StepError("missing_input", "no creature clips: python -m tools.download_models clips")
    if unknown := [c for c in clips if c not in known]:
        raise StepError("bad_params", f"unknown clips {unknown} for a {template}; see /api/clips")
    progress(0.1, f"{len(clips)} clips onto the {template} skeleton")
    try:
        return run_blender(
            "creature_clips",
            [src / "rigged.glb", PACK / known[clips[0]]],
            out / "_anim",
            # a snake lying curved (no limbs): each bone turned by its own heading (M41e)
            {"clips": clips, "yaw": yaw, "headings": not _templates.limbs(template)},
            timeout=1800,
        )
    except BlenderError as e:
        raise StepError("failed", str(e)) from e


def run(op, in_dir, out_dir, params):
    src, out = Path(in_dir or ""), Path(out_dir)
    if not in_dir or not (src / "rigged.glb").is_file():
        raise StepError("missing_input", "anim needs a skinned version (Rig panel: Skin it)")
    skel = (
        json.loads((src / "skeleton.json").read_text(encoding="utf-8"))
        if (src / "skeleton.json").is_file()
        else {}
    )
    template = skel.get("template", _templates.HUMAN)
    clips = list(params.get("clips", DEFAULT if template == _templates.HUMAN else ["Idle", "Walk"]))
    if not clips or not all(isinstance(c, str) and c for c in clips):
        raise StepError("bad_params", "clips: a list of clip names")
    if not isinstance(clearance := params.get("clearance", True), bool):
        raise StepError("bad_params", "clearance: true or false")
    if template != _templates.HUMAN:
        # a creature's clips are made for its own skeleton: no clearance
        res = _creature(src, out, template, clips, skel.get("yaw", 0))
        return _finish(src, out, res, [], pack=True, clearance=False)
    known = catalogue()
    # a motion version of this asset (M26, "310_motion"; M34 from a video, "320_mocap"; an animation file,
    # "330_import_motion") is a clip too: its clip.glb carries one animation, on a skeleton the retarget tells apart
    motions = [c for c in clips if re.fullmatch(MOTION, c)]
    for c in motions:
        if not (src.parent / c / "clip.glb").is_file():
            raise StepError("bad_params", f"{c}: no such motion version in this asset")
    pack = [c for c in clips if c not in motions]
    if pack and not known:
        raise StepError("missing_input", "no clip pack: python -m tools.download_models clips")
    files = {c["file"] for c in known if c["name"] in pack}
    unknown = [c for c in pack if c not in {k["name"] for k in known}]
    if unknown:
        raise StepError("bad_params", f"unknown clips {unknown}; see /api/clips")
    # a motion version's animation carries its own name (from the prompt, unique in the asset: motion.py),
    # which is what the viewer's menu and the exports show; the version name only picks it
    names = {c: (clip_names(src.parent / c / "clip.glb") or [c])[0] for c in motions}
    clips = [names.get(c, c) for c in clips]
    progress(0.1, f"retargeting {len(clips)} clips onto the skeleton")
    try:
        res = run_blender(
            "retarget",
            [
                src / "rigged.glb",
                *(PACK / f for f in sorted(files)),
                *(src.parent / c / "clip.glb" for c in motions),
            ],
            out / "_anim",
            {
                "clips": clips,
                "skeleton": str((src / "skeleton.json").resolve()),
            },  # its twists (M22b)
            timeout=1800,
        )
    except BlenderError as e:
        raise StepError("failed", str(e)) from e
    return _finish(src, out, res, motions, pack=bool(pack), clearance=clearance)


def _finish(
    src: Path, out: Path, res: dict, motions: list[str], pack: bool, clearance: bool
) -> dict:
    shutil.move(out / "_anim" / "rigged_anim.glb", out / "rigged_anim.glb")
    shutil.rmtree(out / "_anim", ignore_errors=True)
    rep = res["report"]
    # a clip that plays and never moves: its bone names were not mapped (the Mr. Mak review's silent-clip gate);
    # read before the clearance, which may turn a still clip's arms
    try:
        turned = _anim.turns(out / "rigged_anim.glb")
    except (
        StepError
    ):  # an accessor this reader does not take (compressed, sparse): the gate is advice, skip it
        turned = None
    if turned is not None and (
        still := [c["name"] for c in rep["clips"] if turned.get(c["name"], 0.0) < STILL_DEG]
    ):
        rep["still"] = still
    opened, skipped = {}, None
    if clearance:  # M67: the arms out of the torso
        progress(0.9, "keeping the arms out of the body")
        try:
            doc, blob = _anim.read_glb(out / "rigged_anim.glb")
            new, opened = _clear.clear(doc, blob, rep.get("fps", 24), skip=rep.get("still", ()))
        # never lose the retarget to this pass: the clips stay as retargeted, the reason on the version's page
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            new, skipped = None, str(e) if isinstance(e, StepError) else f"{type(e).__name__}: {e}"
        if new:
            (out / "rigged_anim.glb").write_bytes(new)
    for f in (
        "skeleton.json",
        "qa.json",
        "parts.json",
    ):  # parts.json: the export keeps the parts (M32)
        if (src / f).is_file():
            shutil.copy(src / f, out / f)
    (out / "clips.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    outputs = {"rigged_anim": "rigged_anim.glb", "clips": "clips.json"}
    if (out / "skeleton.json").is_file():
        outputs["skeleton"] = "skeleton.json"
    if (out / "parts.json").is_file():
        outputs["parts"] = "parts.json"
    metrics = {
        "clips": [c["name"] for c in rep["clips"]],
        **({"still": rep["still"]} if rep.get("still") else {}),
        "seconds_total": round(sum(c["seconds"] for c in rep["clips"]), 2),
        **({"arms_opened": opened} if opened else {}),
        **({"arms_clearance_skipped": skipped} if skipped else {}),
    }
    # a motion's own components (Kimodo and Llama 3, GEM-X and DINOv3) reach the exports' credits through the
    # animated version: the motion version is an input here, not an ancestor (M34; M26 lost them)
    licenses = ["Mesh2Motion/mesh2motion-app"] if pack else []
    for c in motions:
        meta = src.parent / c / "meta.json"
        if meta.is_file():
            licenses += json.loads(meta.read_text(encoding="utf-8")).get("licenses", [])
    return {
        "outputs": outputs,
        "metrics": metrics,
        "models": [{"id": "Mesh2Motion/mesh2motion-app"}] if pack else [],
        "licenses": sorted(set(licenses)),
    }


if __name__ == "__main__":
    main(run)
