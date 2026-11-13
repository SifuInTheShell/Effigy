"""Animation clips on a skinned character (IMPLEMENTATION.md M21): rigged.glb + a clip pack → rigged_anim.glb.

Input: a rig_skin version. params: {"clips": ["Idle_A", "Walk", "Jog"], "clearance": true}: names from the clip packs
(`catalogue()`, GET /api/clips). A clip pack is a folder <models>/clips/<pack>/ of GLBs (each with animations on one
skeleton the retarget reads: UE5, SOMA or Mixamo bone names). `mesh2motion` is built in (CC0,
`tools/download_models.py clips`, the Brawler / Sword fighter / Basic move sets); any other needs a pack.json
{"schema": "effigy-clip-pack/1", "title", "license" (SPDX id or "own"; empty = unknown), "restricted"?, "credit",
"move_sets": [{"id", "title", "clips"}]} (GET /api/clip-packs). A pack without a clean licence makes the animated
version restricted, so export refuses it; its credit reaches CREDITS.txt through `metrics.clip_packs`. Clip names are
unique across packs (a later pack's duplicate is skipped): prefix them ("PatrolCop · Walk"). Hip travel is read from
the Hips' posed position, so a root bone above the Hips that carries the travel works as well.
blender_ops/retarget does the work; the output GLB carries one animation per clip, the mesh and the
skeleton of the input, so the viewer plays it and export carries the clips into the engine files.
A creature (M36: a skeleton from steps/_templates.py) takes its template's own clips
(clips/mesh2motion/creatures/<template>-animations.glb) through blender_ops/creature_clips; the human-only
motions (Kimodo, a video) are refused.
M67: on a person, the arms opened where a clip would swing the forearms or hands through the torso
(`steps/_clear.py`; param `clearance`, default true; metrics `arms_opened`).
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
# one folder per pack; the server passes its own (the models folder chosen in Setup)
CLIPS = MODELS / "clips"
BUILTIN = "mesh2motion"  # the bundled pack: no pack.json, CC0, its creatures/ subfolder, the move sets below
SCHEMA = "effigy-clip-pack/1"
PACK_ID = re.compile(r"[a-z0-9_-]+")
# server/batch.CLEAN (steps don't import the server; a test keeps them equal)
CLEAN = {"CC0-1.0", "MIT", "Apache-2.0", "own"}
BUILTIN_SETS = [
    {
        "id": "brawler",
        "title": "Brawler (Double Dragon)",
        "clips": ["Fighting Idle", "Walk", "Jog", "Fighting Left Jab", "Fighting Right Jab", "Punch_Cross",
                  "Melee_Hook", "Kick_Breach", "Jump_Start", "Jump_air", "Jump_Land", "Hit_Head", "Hit_Chest",
                  "Hit_Knockback", "LayToIdle", "Death_A", "Defend", "PickUp_Table", "Throw Object", "Victory"],
    },
    {
        "id": "sword",
        "title": "Sword fighter (Golden Axe)",
        "clips": ["Idle_Sword", "Walk", "Jog", "Sword_Regular_A", "Sword_Regular_B", "Sword_Regular_C",
                  "Sword_Regular_Combo", "Sword_Attack", "Sword_Attack_Air_Vertical", "Sword_Block", "Jump_Start",
                  "Jump_air", "Jump_Land", "Hit_Chest", "Hit_Knockback", "LayToIdle", "Death_B",
                  "Spell_Simple_Shoot", "Victory"],
    },
    {"id": "basic", "title": "Basic (idle, walk, jog)", "clips": ["Idle_A", "Walk", "Jog"]},
]  # fmt: skip
DEFAULT = ["Idle_A", "Walk", "Jog"]
MOTION = r"\d{3}_(motion|mocap|import_motion)"  # the versions anim takes as clips
STILL_DEG = 1.0  # a clip whose every bone turns less than this over the whole clip never moves


def clip_names(glb: Path) -> list[str]:
    """The animation names in a GLB, from its JSON chunk, no Blender needed."""
    with open(glb, "rb") as f:
        n = int.from_bytes(f.read(20)[12:16], "little")
        doc = json.loads(f.read(n))
    return [a.get("name", f"clip_{i}") for i, a in enumerate(doc.get("animations", []))]


def _manifest(d: Path) -> tuple[dict, list[str]]:
    """A pack folder's pack.json read and checked: (its fields, problems); a problem leaves the folder out."""
    if not PACK_ID.fullmatch(d.name):
        return {}, [f"folder name {d.name!r}: use a-z, 0-9, _ and -"]
    try:
        m = json.loads((d / "pack.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, ["no pack.json"]
    except (OSError, ValueError) as e:
        return {}, [f"pack.json unreadable: {e}"]
    sets = m.get("move_sets", []) if isinstance(m, dict) else None
    if not isinstance(m, dict) or m.get("schema") != SCHEMA:
        return {}, [f'pack.json: "schema" must be "{SCHEMA}"']
    if not all(isinstance(m.get(k, ""), str) for k in ("title", "license", "credit")):
        return {}, ["pack.json: title, license and credit are text"]
    ok = isinstance(sets, list) and all(
        isinstance(s, dict) and isinstance(s.get("id"), str) and isinstance(s.get("clips"), list)
        for s in sets
    )
    if not ok:
        return {}, ['pack.json: "move_sets" is a list of {"id", "title", "clips": [names]}']
    lic = m.get("license", "")
    # clean by a clean licence or by the owner's clearance (clearance.json, written by the app, never by a producer);
    # "restricted" in pack.json can add a restriction, never lift one
    cleared = clearance(d)
    return {
        "title": m.get("title") or d.name,
        "license": cleared or lic,
        "restricted": not cleared and (lic not in CLEAN or bool(m.get("restricted"))),
        "credit": m.get("credit", ""),
        "cleared": cleared,
        "move_sets": sets,
    }, []


def clearance(d: Path) -> str:
    """The licence the owner says they got for this pack (free text, `clearance.json`), or ""."""
    try:
        return str(
            json.loads((d / "clearance.json").read_text(encoding="utf-8")).get("license", "")
        ).strip()
    except (OSError, ValueError, AttributeError):
        return ""


def packs(root: Path | None = None) -> list[dict]:
    """Every human clip pack under `root`: mesh2motion first, then the other folders by name. Each is {"id", "title",
    "license", "restricted", "credit", "move_sets", "clips": [{"name", "file", "pack"}], "problems"}; a folder
    without a valid pack.json is listed with its problem and no clips. A clip name another pack had first is skipped,
    a move set keeps only its own pack's clips; both are reported in `problems`."""
    out, seen, root = [], set(), root or CLIPS
    dirs = sorted(
        (d for d in root.glob("*") if d.is_dir() and d.name != BUILTIN), key=lambda d: d.name
    )
    for d in [root / BUILTIN, *dirs]:
        if d.name == BUILTIN:
            fields, problems = (
                {
                    "title": "Mesh2Motion",
                    "license": "CC0-1.0",
                    "restricted": False,
                    "credit": "Animations from Mesh2Motion (mesh2motion.org), CC0",
                    "move_sets": BUILTIN_SETS,
                },
                [],
            )
        else:
            fields, problems = _manifest(d)
        p = {
            "id": d.name,
            "title": d.name,
            "license": "",
            "restricted": True,
            "credit": "",
            "cleared": "",
            "move_sets": [],
        }
        p |= fields | {"clips": [], "problems": problems}
        if not problems:
            for glb in sorted(d.glob("*.glb")):
                for n in clip_names(glb):
                    if n in seen:
                        problems.append(
                            f"{n!r} in {glb.name}: another pack has that clip name; skipped"
                        )
                        continue
                    seen.add(n)
                    p["clips"].append({"name": n, "file": f"{d.name}/{glb.name}", "pack": d.name})
            have, sets = {c["name"] for c in p["clips"]}, []
            for s in p["move_sets"]:
                if gone := [n for n in s["clips"] if n not in have]:
                    problems.append(f"move set {s['id']!r}: not in this pack: {gone}")
                clips = [n for n in s["clips"] if n in have]
                sets.append({"id": s["id"], "title": s.get("title") or s["id"], "clips": clips})
            p["move_sets"] = sets
        out.append(p)
    return out


def catalogue(template: str = _templates.HUMAN, root: Path | None = None) -> list[dict]:
    """Every clip for a skeleton: {"name", "file", "pack"}, `file` relative to `root` (every pack's, or a creature
    template's own clips, its rest pose left out)."""
    root = root or CLIPS
    if template != _templates.HUMAN:
        glb = root / BUILTIN / "creatures" / f"{template}-animations.glb"
        names = clip_names(glb) if glb.is_file() else []
        rel = f"{BUILTIN}/creatures/{glb.name}"
        return [{"name": n, "file": rel, "pack": BUILTIN} for n in names if "rest" not in n.lower()]
    return [c for p in packs(root) for c in p["clips"]]


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
            [src / "rigged.glb", CLIPS / known[clips[0]]],
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
        return _finish(
            src, out, res, [], packs=[{"id": BUILTIN, "restricted": False}], clearance=False
        )
    every = packs()
    known = [c for p in every for c in p["clips"]]
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
    used = {c["pack"] for c in known if c["name"] in pack}
    used = [p for p in every if p["id"] in used]
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
                *(CLIPS / f for f in sorted(files)),
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
    return _finish(src, out, res, motions, packs=used, clearance=clearance)


def pack_row(p: dict) -> dict:
    """A user pack's licence row as export reads it (licenses.yaml's fields), from packs()."""
    return {
        "name": f"clip-pack/{p['id']}",
        "kind": "data",
        "license": p["license"] or "unknown",
        "commercial": not p["restricted"],
        "attribution": p["credit"],
        "flag": f"clip pack {p['title']!r}"
        + (f", cleared by the owner: {p['cleared']}" if p.get("cleared") else " (its pack.json)"),
    }


def _credits(used: list[dict]) -> dict:
    """The packs' share of the RESULT: Mesh2Motion by its licenses.yaml row; another pack by the licence row its
    pack.json makes, kept in `metrics.clip_packs` (export reads it there: a user pack has no licenses.yaml row and
    may be gone by then); `restricted` when any of them is."""
    names = [
        "Mesh2Motion/mesh2motion-app" if p["id"] == BUILTIN else f"clip-pack/{p['id']}"
        for p in used
    ]
    rows = [pack_row(p) for p in used if p["id"] != BUILTIN]
    return {
        "models": [{"id": n} for n in names],
        "licenses": names,
        "clip_packs": rows,
        "restricted": any(p["restricted"] for p in used),
    }


def _finish(
    src: Path, out: Path, res: dict, motions: list[str], packs: list[dict], clearance: bool
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
    own = _credits(packs)
    if own["clip_packs"]:
        metrics["clip_packs"] = own["clip_packs"]
    licenses = own["licenses"]
    for c in motions:
        meta = src.parent / c / "meta.json"
        if meta.is_file():
            licenses += json.loads(meta.read_text(encoding="utf-8")).get("licenses", [])
    return {
        "outputs": outputs,
        "metrics": metrics,
        "models": own["models"],
        "licenses": sorted(set(licenses)),
        **({"restricted": True} if own["restricted"] else {}),
    }


if __name__ == "__main__":
    main(run)
