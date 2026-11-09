"""Fit the 52-joint Mixamo skeleton to a character mesh (IMPLEMENTATION.md M19), or a creature template
(M36: steps/_templates.py, Mesh2Motion's fox, horse, bird, dragon, shark, kaiju, spider, snake: turned to the mesh and stretched onto it).

Input: a version with a mesh, standing in the library frame (Y up, facing +Z), ideally A-posed (M16).
params: {"markers": {name: [x, y, z]}?, "size": 1024, "template": "mixamo52" | a creature (fox, horse, bird, dragon, shark, kaiju, spider, snake),
"yaw": degrees (a creature's turn about the vertical, else guessed: its lean plus a quarter turn)}
Without markers: an orthographic front render (blender_ops/render_ortho) → DWPose keypoints → the 13
markers (steps/_rig.MARKERS) at the middle of the body along the view ray. With markers (the UI after the
user moved some): those, with the parent's hand keypoints carried along with each wrist.
Outputs: skeleton.json (joints: name, parent, position, library frame), markers.json (markers, per-marker
confidence, hand keypoints), mesh.glb (the input's, so skin/animate find mesh and skeleton together),
report.json (joints-inside-mesh check: steps/_rig.inside; a joint outside is first snapped into the
nearest solid span within 5 % of the height).
"""

import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from steps import _glb, _hands, _pose, _rig, _templates
from steps._blender import BlenderError, run_blender
from steps._common import StepError, main, progress
from steps._mesh import read_glb, textured, weld

MIN_HAND = 0.4  # mean DWPose score of a hand's 21 keypoints to trust them over proportions


def _render(glb: Path, out: Path, size: int, view: str):
    """(RGBA image, camera) of an orthographic render from the front or the back."""
    try:
        cam = run_blender(
            "render_ortho", [glb], out / "_ortho", {"size": size, "view": view}, timeout=300
        )["camera"]
        return np.asarray(Image.open(out / "_ortho" / "front.png").convert("RGBA")), cam
    except BlenderError as e:
        raise StepError("failed", str(e)) from e
    finally:
        shutil.rmtree(out / "_ortho", ignore_errors=True)


def _textured_twin(src: Path) -> Path | None:
    """The nearest version up the chain (this one first) whose GLB has textures, if no step in between
    moved the frame (a clean that rescaled or recentred, a Blender or Claude edit): its render is what
    the pose model can read, and its keypoints land on this mesh (remesh and uv keep the frame)."""
    d = src
    while d and (d / "meta.json").is_file():
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        for g in [g for g in meta["outputs"].values() if str(g).endswith(".glb")]:
            if textured(d / g):
                return d / g
        t = meta.get("metrics", {}).get("transform")
        if meta.get("step") in ("blender", "claude") or (
            t is not None and not np.allclose(t, np.eye(4), atol=1e-3)
        ):
            return None
        d = d.parent / meta["parent"] if meta.get("parent") else None
    return None


def _detect(glb: Path, out: Path, v, f, size: int):
    """Markers from the front render, or the back one when the character faces -Z (the face decides)."""
    progress(0.1, "front render")
    look = _textured_twin(glb.parent) or glb
    session = _pose.load()
    views = {}
    for view in (
        "front",
        "back",
    ):  # the side the face is on is the front (TRELLIS keeps no convention)
        img, cam = _render(look, out, size, view)
        views[view] = (img, cam, *_pose.keypoints_rgba(session, img))
    face = {k: float(x[3][:5].mean()) for k, x in views.items()}  # nose, eyes, ears
    body = {k: float(x[3][5:17].mean()) for k, x in views.items()}  # shoulders to ankles
    # which way the body faces: the face when it can be read (≥ 0.5: 0.60–0.92 on readable faces), else
    # the feet (toes reach ahead of the shins), else the library's +Z convention (M22: the helmeted dwarf
    # and the flat cartoon runner read 0.3–0.5 from both sides)
    seen = max(face, key=face.get)
    if face[seen] >= 0.5:
        facing, by = (1 if seen == "front" else -1), "face"
    else:
        cue = _rig.feet_cue(v)
        facing, by = ((1 if cue >= 0 else -1), "feet") if abs(cue) >= 0.01 else (1, "default")
    # which render to read the joints from: the face's when it was read, else the one the pose model sees a
    # body in (the dwarf's helmet reads as the back of a head, 0.38, its back as a body, 0.69); the render's
    # own side maps its pixels, the facing above places toes and fingers and settles the side names
    view = seen if by == "face" else max(body, key=body.get)
    img, cam, pts, scores = views[view]
    Image.fromarray(img).save(out / "front.png")
    side = cam["facing"]  # the viewer's side of this render
    zc = float((v[:, 2].min() + v[:, 2].max()) / 2)

    def to3d(u, w, near=None):
        x = cam["cx"] + side * (u / cam["size"] - 0.5) * cam["scale"]
        y = cam["cy"] - (w / cam["size"] - 0.5) * cam["scale"]
        z = _rig.mid_depth(v, f, x, y, near, side)
        return np.array([x, y, (zc if near is None else near) if z is None else z])

    pts = _rig.refine_shoulders(pts, img[..., 3].astype(np.float32) / 255)
    markers, conf = _rig.auto_markers(pts, scores, to3d, float(v[:, 1].min()), float(v[:, 1].max()))
    hands = {}
    for s in _rig.SIDES:
        k0 = _rig.HAND0[s]
        if scores[k0 : k0 + 21].mean() >= MIN_HAND:
            near = markers[f"{s}Wrist"][2]
            hands[s] = np.array([to3d(*pts[k0 + i], near) for i in range(21)])
    detected = {
        "facing": facing,
        "facing_by": by,
        "face_scores": {k: round(s, 2) for k, s in face.items()},
        "body_scores": {k: round(s, 2) for k, s in body.items()},
        "detected_view": view,
        "detected_on": str(look.relative_to(glb.parent.parent)).replace("\\", "/"),
    }
    if not _rig.sides_agree(
        markers, facing
    ):  # the pose model's left is on the right: swap the names
        markers, conf, hands = _rig.swap_sides(markers, conf, hands)
        detected["sides_swapped"] = True
    # M70: each hand rendered on its own and read by MediaPipe, ahead of DWPose's points from the whole body
    try:
        detect = _hands.finder()
    except Exception as e:  # noqa: BLE001 - mediapipe missing or the model unreadable: DWPose's points stand
        detect, detected["hand_finder"] = None, f"{type(e).__name__}: {e}"
    progress(0.15, "the hands")
    try:
        hands, detected["hands_by"], seen = _hands.found(v, f, markers, hands, detect)
    finally:
        if detect is not None:
            detect.close()
    if seen:
        detected["hand_views"] = seen
    return markers, conf, hands, detected


def _creature(
    src: Path, out: Path, glb: str, v, f, meta: dict, template: str, params: dict, edited: bool
):
    """M36: a creature template turned to the mesh and stretched onto it; or the editor's moves on top of
    the previous creature skeleton (a moved joint's own tail moves with it)."""
    if template not in _templates.TEMPLATES:
        raise StepError(
            "bad_params",
            f"template {template!r}: one of {[_templates.HUMAN, *_templates.TEMPLATES]}",
        )
    if edited:
        prev = json.loads((src / "skeleton.json").read_text(encoding="utf-8"))
        yaw = prev.get("yaw", 0)
        bones = {x["name"]: dict(x) for x in prev["joints"]}
        for name, p in (params.get("joints") or {}).items():
            if name in bones:
                shift = np.asarray(p, float) - np.asarray(bones[name]["position"])
                bones[name]["position"] = np.round(p, 5).tolist()
                if "tail" in bones[name]:
                    bones[name]["tail"] = (
                        (np.asarray(bones[name]["tail"]) + shift).round(5).tolist()
                    )
    else:
        if params.get("yaw") is not None and not (
            isinstance(params["yaw"], int | float)
            and not isinstance(params["yaw"], bool)
            and np.isfinite(params["yaw"])
        ):
            raise StepError(
                "bad_params", "yaw: the degrees the animal is turned about the vertical"
            )
        progress(0.5, f"the {template} skeleton onto the mesh")
        fitted, yaw = _templates.fit(template, v, params.get("yaw"), f)
        bones = {n: {"name": n} | b for n, b in fitted.items()}
    lev, skipped, rise, transform = (
        np.eye(3),
        None,
        float(np.ptp(v[:, 1])),
        None,
    )  # rise: the snap's reach, of the mesh as it came
    carried = (
        None  # a source already stood up (a Turn re-run on a rig_fit version): its turn is kept
    )
    if not edited and (src / "skeleton.json").is_file():
        carried = json.loads((src / "skeleton.json").read_text(encoding="utf-8")).get("level")
    if carried is not None:
        lev = np.asarray(carried)
    elif not edited:  # a flat creature lying tilted (a spider from a picture taken from above) is stood up, mesh and joints
        lev = _templates.level(template, _templates.surface(v, f))
        if not np.allclose(lev, np.eye(3)):
            turned = (
                v @ lev
            )  # back on the floor, its box centre where it was: the turn is about the origin
            shift = (v.min(0) + v.max(0) - turned.min(0) - turned.max(0)) / 2
            shift[1] = -turned[:, 1].min()
            skipped = _glb.rotate(src / glb, out / "mesh.glb", lev, shift)
            if skipped is None:
                v = turned + shift
                for b in bones.values():
                    for k in ("position", "tail"):
                        if k in b:
                            b[k] = (np.asarray(b[k]) @ lev + shift).round(5).tolist()
                transform = np.eye(
                    4
                )  # p' = lev.T @ p + shift, the column-vector form clean.py records
                transform[:3, :3], transform[:3, 3] = lev.T, shift
            else:
                lev = np.eye(3)
    names = list(bones)
    pos = np.array([bones[n]["position"] for n in names])
    ok = _rig.inside(v, f, pos)
    if not edited:  # stretched from a box: a joint a little outside goes to the nearest solid span
        reach = 0.05 * rise
        for i in np.flatnonzero(~ok):
            if bones[names[i]]["parent"] is None:  # the root stays on the ground, under the body
                continue
            pos[i] = _rig.snap_inside(v, f, pos[i], reach)
            bones[names[i]]["position"] = pos[i].round(5).tolist()
        ok = _rig.inside(v, f, pos)
    outside = [
        n for n, x in zip(names, ok) if not x and bones[n]["parent"]
    ]  # the root stands on the floor
    sides = list(_templates.TEMPLATES[template][1])  # the editor mirrors Ear_L onto Ear_R by these
    if edited:
        lev = np.asarray(prev.get("level", lev))
    skel = {"template": template, "frame": "gltf_yup_m", "yaw": yaw, "sides": sides}
    skel["level"] = lev.round(6).tolist()  # the turn the mesh was stood up by (identity: none)
    skel["joints"] = [bones[n] for n in names]
    (out / "skeleton.json").write_text(json.dumps(skel, indent=1), encoding="utf-8")
    if edited or not (out / "mesh.glb").is_file():  # else already written, stood up
        shutil.copy(src / glb, out / "mesh.glb")
    report = {
        "joints": len(names),
        "inside": len(names) - len(outside),
        "outside": outside,
        "low_confidence": [],
    }
    (out / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    outputs = {"mesh": "mesh.glb", "skeleton": "skeleton.json", "report": "report.json"}
    if (src / "parts.json").is_file():
        shutil.copy(src / "parts.json", out / "parts.json")
        outputs["parts"] = "parts.json"
    metrics = {"template": template, "yaw": yaw, "inside": report["inside"], "joints": len(names)}
    metrics |= {"outside": outside[:8], "source": "edited" if edited else "template"}
    if "parts" in outputs:
        metrics["parts"] = meta.get("metrics", {}).get("parts", {})
    if not edited:
        metrics["leveled_deg"] = round(
            float(np.degrees(np.arccos(np.clip((np.trace(lev) - 1) / 2, -1, 1)))), 1
        )
        if skipped:
            metrics["level_skipped"] = f"not stood up: {skipped}"
        if transform is not None:
            metrics["transform"] = transform.round(9).tolist()
    if edited:
        metrics["edited_joints"] = sorted(params.get("joints") or {})
    return {"outputs": outputs, "metrics": metrics}


def run(op, in_dir, out_dir, params):
    if not in_dir:
        raise StepError("missing_input", "rig_fit needs a version with a character mesh")
    src, out = Path(in_dir), Path(out_dir)
    meta = json.loads((src / "meta.json").read_text(encoding="utf-8"))
    glb = next((g for g in meta["outputs"].values() if str(g).endswith(".glb")), None)
    if not glb:
        raise StepError("missing_input", f"{meta['version']} has no .glb")
    v, f = weld(*read_glb(src / glb)[:2])
    twists: dict = {}
    edited = "joints" in params or "twists" in params  # also when empty: a copy, no re-detection
    template = params.get("template", _templates.HUMAN)
    if edited and (src / "skeleton.json").is_file():
        template = json.loads((src / "skeleton.json").read_text(encoding="utf-8")).get(
            "template", template
        )
    if template != _templates.HUMAN:
        return _creature(src, out, glb, v, f, meta, template, params, edited)
    if (
        edited
    ):  # M22b: the user moved joints or turned twists in the editor, on top of this skeleton
        if not (src / "skeleton.json").is_file():
            raise StepError("missing_input", "joint edits need a skeleton version as their input")
        prev = json.loads((src / "skeleton.json").read_text(encoding="utf-8"))
        joints = {x["name"]: np.asarray(x["position"], float) for x in prev["joints"]}
        twists = {
            x["name"]: np.asarray(x["twist"], float) for x in prev["joints"] if x.get("twist")
        }
        joints |= {k: np.asarray(p, float) for k, p in (params.get("joints") or {}).items()}
        twists |= {k: np.asarray(t, float) for k, t in (params.get("twists") or {}).items()}
        pm = src / "markers.json"
        marks = json.loads(pm.read_text(encoding="utf-8")) if pm.is_file() else {}
        markers = {k: np.asarray(p, float) for k, p in marks.get("markers", {}).items()}
        conf = marks.get("confidence", {})
        hands = {s: np.asarray(k, float) for s, k in marks.get("hands", {}).items()}
        detected = {"facing": marks.get("facing", 1)}
    elif params.get("markers"):
        markers = {k: np.asarray(params["markers"][k], float) for k in _rig.MARKERS}
        prev = (
            json.loads((src / "markers.json").read_text())
            if (src / "markers.json").is_file()
            else {}
        )
        conf = {k: 1.0 for k in _rig.MARKERS}  # the user placed or kept them
        detected = {"facing": prev.get("facing", 1)}
        hands = {}
        for s, kps in prev.get("hands", {}).items():  # follow the wrist the user moved
            shift = markers[f"{s}Wrist"] - np.asarray(prev["markers"][f"{s}Wrist"])
            hands[s] = np.asarray(kps) + shift
    else:
        markers, conf, hands, detected = _detect(
            src / glb, out, v, f, int(params.get("size", 1024))
        )
    if not edited:
        progress(0.7, "skeleton from the markers")
        joints = _rig.skeleton(markers, v, f, hands, detected["facing"])
    progress(0.85, "joints inside the mesh")
    names = list(joints)
    pos = np.array([joints[n] for n in names])
    ok = _rig.inside(v, f, pos)
    if not edited:  # a joint the user placed stays where the user put it
        reach = 0.05 * float(np.ptp(v[:, 1]))
        for i in np.flatnonzero(~ok):
            pos[i] = _rig.snap_inside(v, f, pos[i], reach)
        ok = _rig.inside(v, f, pos)
        palms = _rig.palm_normals(dict(zip(names, pos)), v)  # the hands' twist, for the retarget
        twists |= {f"{s}Hand": n for s, n in palms.items() if n is not None}
    joints = {n: pos[i].round(5).tolist() for i, n in enumerate(names)}
    twists = {k: np.round(t, 5).tolist() for k, t in twists.items() if k in joints}
    outside = [n for n, x in zip(names, ok) if not x]
    parents = dict(_rig.template())
    skel = {
        "template": _templates.HUMAN,
        "frame": "gltf_yup_m",
        "joints": [
            {"name": n, "parent": parents[n], "position": joints[n]}
            | ({"twist": twists[n]} if n in twists else {})
            for n in names
        ],
    }
    (out / "skeleton.json").write_text(json.dumps(skel, indent=1), encoding="utf-8")
    marks = {
        "markers": {k: np.round(p, 5).tolist() for k, p in markers.items()},
        "confidence": {k: round(c, 3) for k, c in conf.items()},
        "hands": {s: np.round(k, 5).tolist() for s, k in hands.items()},
        "source": "edited" if (params.get("markers") or edited) else "dwpose",
    } | detected
    (out / "markers.json").write_text(json.dumps(marks, indent=1), encoding="utf-8")
    shutil.copy(src / glb, out / "mesh.glb")
    report = {"joints": len(names), "inside": len(names) - len(outside), "outside": outside}
    report["low_confidence"] = [k for k, c in conf.items() if c < _pose.MIN_SCORE]
    (out / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    outputs = {
        "mesh": "mesh.glb",
        "skeleton": "skeleton.json",
        "markers": "markers.json",
        "report": "report.json",
    }
    if (out / "front.png").is_file():
        outputs["front"] = "front.png"
    if (src / "parts.json").is_file():  # M32: the parts ride along to the skinning and the export
        shutil.copy(src / "parts.json", out / "parts.json")
        outputs["parts"] = "parts.json"
    metrics = {
        "inside": report["inside"],
        "joints": len(names),
        "hands_detected": sorted(hands),
    } | detected
    metrics |= {"outside": outside[:8], "low_confidence": report["low_confidence"]}
    metrics |= {"source": marks["source"], "twists": sorted(twists)}
    if "parts" in outputs:  # the names the Skin it choices list (a parts or rig version's metrics)
        metrics["parts"] = meta.get("metrics", {}).get("parts", {})
    if edited:  # what the editor changed, for the panel
        metrics |= {
            "edited_joints": sorted(params.get("joints") or {}),
            "edited_twists": sorted(params.get("twists") or {}),
        }
    result = {"outputs": outputs, "metrics": metrics}
    if not (params.get("markers") or edited):
        ids = [_pose.MODEL] + (
            [_hands.CREDIT] if "mediapipe" in detected.get("hands_by", {}).values() else []
        )
        result |= {"models": [{"id": i} for i in ids], "licenses": ids}
    return result


if __name__ == "__main__":
    main(run)
