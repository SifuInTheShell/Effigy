"""Animation edit (IMPLEMENTATION.md M37, §3.2 `anim_edit`): a correction layer baked into an anim version's clips.

Input: an `anim` or `anim_edit` version. params: {"clips": {clip: layer}} (the layer is documented in §3.2).
The layer applies to the base: the nearest `anim` (an `anim_edit` names it in its edit.json), so edits never
compound. steps/_anim.py samples the base's clips, evaluates the layer and writes the new dense animation
accessors into a copy of rigged_anim.glb; no Blender. The other clips and the rest of the file are untouched.
"""

import copy
import json
import math
import re
import shutil
from pathlib import Path

import numpy as np

from steps import _anim
from steps._common import StepError, main, progress

NAME_BAD = re.compile(r"[\x00-\x1f\x7f/\\:|,\[\];]")


def _bad(clip, msg):
    raise StepError("bad_params", f"clip {clip!r}: {msg}")


def _num(clip, what, v, lo=-1e4, hi=1e4):
    if (
        isinstance(v, bool)
        or not isinstance(v, (int, float))
        or not math.isfinite(v)
        or not lo <= v <= hi
    ):
        _bad(clip, f"{what} must be a finite number in [{lo}, {hi}], got {v!r}")
    return float(v)


def _vec(clip, what, v, n):
    if not isinstance(v, list) or len(v) != n:
        _bad(clip, f"{what} must be a list of {n} numbers")
    return [_num(clip, what, x) for x in v]


def validate(clip: str, layer, base: dict) -> dict:
    """The layer checked against the sampled base clip; returns it cleaned (quaternions unit, w >= 0)."""
    if not isinstance(layer, dict):
        _bad(clip, "the layer must be an object")
    if unknown := set(layer) - {
        "keys",
        "offsets",
        "trim",
        "speed",
        "loop",
        "ground",
        "in_place",
        "smooth",
        "feet",
        "ik",
        "from",
        "mirror",
        "filter",
    }:
        _bad(clip, f"unknown layer fields {sorted(unknown)}")
    F = base["frames"]
    bones = {b["name"]: b for b in base["bones"]}
    out = {}
    if "from" in layer:
        out["from"] = layer["from"]

    def per_bone(field, need, fix):
        res = {}
        if not isinstance(layer.get(field) or {}, dict):
            _bad(clip, f"{field} must be an object of bones")
        for bone, v in (layer.get(field) or {}).items():
            if bone not in bones:
                _bad(clip, f"{field}: no bone {bone!r}")
            if need not in bones[bone]:
                _bad(clip, f"{field}: bone {bone!r} has no animated {need}")
            res[bone] = fix(bone, v)
        return res

    def keys(bone, v, kind):  # kind: "q" (rotation keys) or "v" (translation offsets)
        if not isinstance(v, list) or not all(isinstance(k, dict) for k in v):
            _bad(clip, f"{bone!r}: a list of keys")
        if any(
            not float(k.get("f", 0.5)).is_integer()
            for k in v
            if isinstance(k.get("f"), (int, float))
        ):
            _bad(clip, f"{bone!r}: a key's frame is a whole number")
        res = [
            {
                "f": _num(clip, "f", k.get("f"), 0, F - 1),
                "falloff": _num(clip, "falloff", k.get("falloff", 0), 0, F),
                kind: _unit_q(clip, k.get(kind)) if kind == "q" else _vec(clip, "v", k.get("v"), 3),
            }
            for k in v
        ]
        if len({k["f"] for k in res}) != len(res):
            _bad(clip, f"two keys of {bone!r} on one frame")
        return res

    out["keys"] = per_bone("keys", _anim.ROT, lambda b, v: keys(b, v, "q"))
    out["offsets"] = per_bone("offsets", _anim.POS, lambda b, v: keys(b, v, "v"))
    out["smooth"] = per_bone("smooth", _anim.ROT, lambda b, v: _num(clip, "smooth sigma", v, 0, 10))
    G = F
    if (t := layer.get("trim")) is not None:
        if (
            not isinstance(t, list)
            or len(t) != 2
            or not all(isinstance(x, int) for x in t)
            or not 0 <= t[0] < t[1] <= F - 1
        ):
            _bad(clip, f"trim must be [in, out] integers with 0 <= in < out <= {F - 1}")
        out["trim"], G = t, t[1] - t[0] + 1
    out["feet"] = _feet(clip, layer.get("feet", []), bones, F)
    out["ik"] = _ik(clip, layer.get("ik", []), bones, F)
    out["speed"] = _num(clip, "speed", layer.get("speed", 1), 0.1, 10)
    if math.floor((G - 1) / out["speed"] + 0.5) + 1 < 2:
        _bad(clip, "trim and speed leave a single frame")
    n = layer.get("loop", 0)
    if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n < G:
        _bad(clip, f"loop must be an integer in [0, {G - 1}] (frames)")
    out["loop"] = n
    out["ground"] = _num(clip, "ground", layer.get("ground", 0), -100, 100)
    out["in_place"] = bool(layer.get("in_place", False))
    if "mirror" in layer:  # M69
        if not isinstance(layer["mirror"], bool):
            _bad(clip, "mirror is true or false")
        out["mirror"] = layer["mirror"]
    if "filter" in layer:
        if layer["filter"] not in ("gaussian", "butterworth"):
            _bad(clip, "filter is gaussian or butterworth")
        out["filter"] = layer["filter"]
    return out


def _feet(clip, v, bones, F):
    if not isinstance(v, list) or len(v) > 64:
        _bad(clip, "feet must be a list of at most 64 locks")
    res = []
    for lk in v:
        if not isinstance(lk, dict) or not {"bone", "from", "to", "blend"} <= set(lk) <= {
            "bone",
            "from",
            "to",
            "blend",
            "reach",
        }:
            _bad(clip, "a foot lock is {bone, from, to, blend, reach?}")
        n = lk["bone"]
        for _ in range(3):  # the bone, its parent, its grandparent
            if not isinstance(n, str) or n not in bones or _anim.ROT not in bones[n]:
                _bad(clip, f"feet: {lk['bone']!r}, its parent and grandparent must rotate")
            n = bones[n]["parent"]
        w = {}
        for k, hi in (("from", F - 1), ("to", F - 1), ("blend", F)):
            w[k] = _num(clip, f"feet {k}", lk[k], 0, hi)
            if not w[k].is_integer():
                _bad(clip, f"feet {k} is a whole number")
            w[k] = int(w[k])
        if w["from"] > w["to"]:
            _bad(clip, "feet: from must not exceed to")
        if "reach" in lk:  # M69: a share of the leg's length
            w["reach"] = _num(clip, "feet reach", lk["reach"], 0.5, 1)
        res.append({"bone": lk["bone"], **w})
    return res


def _ik(clip, v, bones, F):
    """M63 IK controls: {bone, chain, space?, keys: [{f, p, swivel?, w?, falloff?}]}, cleaned with the defaults."""
    if not isinstance(v, list) or len(v) > 64:
        _bad(clip, "ik must be a list of at most 64 controls")
    res = []
    for c in v:
        if not isinstance(c, dict) or not {"bone", "chain", "keys"} <= set(c) <= {
            "bone",
            "chain",
            "space",
            "keys",
        }:
            _bad(clip, "an IK control is {bone, chain, space?, keys}")
        n = c["chain"]
        if isinstance(n, bool) or not isinstance(n, int) or not 2 <= n <= 8:
            _bad(clip, "ik chain must be an integer in [2, 8]")
        b = c["bone"]
        for _ in range(n + 1):  # the bone and the chain above it
            if not isinstance(b, str) or b not in bones or _anim.ROT not in bones[b]:
                _bad(clip, f"ik: {c['bone']!r} and the {n} bones above it must rotate")
            b = bones[b]["parent"]
        if "space" in c and (not isinstance(c["space"], str) or c["space"] not in bones):
            _bad(clip, f"ik space: no bone {c['space']!r}")
        ks = c["keys"]
        if not isinstance(ks, list) or not ks:
            _bad(clip, "ik keys: a list of at least one key")
        out = []
        for k in ks:
            if not isinstance(k, dict) or not {"f", "p"} <= set(k) <= {
                "f",
                "p",
                "swivel",
                "w",
                "falloff",
            }:
                _bad(clip, "an IK key is {f, p, swivel?, w?, falloff?}")
            f = _num(clip, "ik f", k["f"], 0, F - 1)
            if not f.is_integer():
                _bad(clip, "ik f is a whole number")
            out.append(
                {
                    "f": int(f),
                    "p": _vec(clip, "ik p", k["p"], 3),
                    "swivel": _num(clip, "ik swivel", k.get("swivel", 0), -180, 180),
                    "w": _num(clip, "ik w", k.get("w", 1), 0, 1),
                    "falloff": _num(clip, "ik falloff", k.get("falloff", 0), 0, F),
                }
            )
        if len({k["f"] for k in out}) != len(out):
            _bad(clip, f"two IK keys of {c['bone']!r} on one frame")
        if any(r["bone"] == c["bone"] for r in res):
            _bad(clip, f"two IK controls on {c['bone']!r}")
        res.append(
            {
                "bone": c["bone"],
                "chain": n,
                **({"space": c["space"]} if "space" in c else {}),
                "keys": out,
            }
        )
    return res


def _unit_q(clip, q):
    q = np.array(_vec(clip, "q", q, 4))
    if (n := np.linalg.norm(q)) < 1e-6:
        _bad(clip, "a key's quaternion has zero length")
    q /= n * (1 if q[3] >= 0 else -1)  # unit, w >= 0
    return q.tolist()


def _base_of(src: Path) -> str:
    meta = (
        json.loads((src / "meta.json").read_text(encoding="utf-8"))
        if (src / "meta.json").is_file()
        else {}
    )
    if meta.get("step") == "anim" or (
        meta.get("step") == "import_model" and (src / "rigged_anim.glb").is_file()
    ):
        return src.name
    if meta.get("step") == "anim_edit" and (src / "edit.json").is_file():
        return json.loads((src / "edit.json").read_text(encoding="utf-8"))["base"]
    raise StepError(
        "missing_input",
        "anim_edit needs an animated version (anim, anim_edit, or an imported animation)",
    )


def run(op, in_dir, out_dir, params):
    src, out = Path(in_dir or "."), Path(out_dir)
    base_name = _base_of(src)
    base_dir = src.parent / base_name
    glb = base_dir / "rigged_anim.glb"
    if not glb.is_file():
        raise StepError("missing_input", f"{base_name}: no rigged_anim.glb")
    layers = params.get("clips")
    if not isinstance(layers, dict) or not layers:
        raise StepError("bad_params", "clips: an object {clip name: layer}")
    clips_json = json.loads((base_dir / "clips.json").read_text(encoding="utf-8"))
    fps = clips_json.get("fps", 24)
    doc, blob = _anim.read_glb(glb)
    progress(0.1, f"evaluating {len(layers)} clip(s)")
    have = {a["name"] for a in doc.get("animations", []) if a.get("name") is not None}
    copies = {n: l for n, l in layers.items() if n not in have}
    taken = {h.lower() for h in have}
    for n in copies:  # M44 name rule, the same as Editor.tsx `nameOk` (IMPLEMENTATION §3.2)
        if n != n.strip(" ") or not 1 <= len(n) <= 64 or NAME_BAD.search(n) or n.lower() in taken:
            _bad(
                n,
                "a copy's name is 1-64 characters, no space at either end, none of / \\ : | , [ ] ; "
                "or control characters, and differs from every other clip ignoring case",
            )
        taken.add(n.lower())
    if len(copies) > 64:
        raise StepError("bad_params", "at most 64 copies")
    edits, made = {}, {}
    for name, layer in layers.items():
        frm = name
        if name in copies:  # M44: a copy of a base clip, written as a new animation
            frm = layer.get("from") if isinstance(layer, dict) else None
            if not isinstance(frm, str) or frm not in have:
                _bad(name, f"no clip {name!r}, and from {frm!r} is not a base clip")
            made[name] = frm
        elif isinstance(layer, dict) and "from" in layer:
            _bad(name, "from is for a new clip, this name is a base clip")
        base = _anim.sample_clip(doc, blob, frm, fps)
        layers[name] = clean = validate(name, layer, base)
        edits[name] = _anim.evaluate(base, clean)
    for name, frm in made.items():
        a = copy.deepcopy(doc["animations"][_anim.clip_index(doc, frm)])
        doc["animations"].append({**a, "name": name})
    progress(0.7, "writing the animation")
    (out / "rigged_anim.glb").write_bytes(_anim.write_glb(doc, blob, edits, fps))
    for name, frm in made.items():
        row = next((c for c in clips_json["clips"] if c["name"] == frm), None)
        if row is None:
            raise StepError("failed", f"clips.json has no entry for {frm!r}")
        clips_json["clips"].append({**row, "name": name})
    for c in clips_json["clips"]:
        if c["name"] in edits:
            c["frames"] = edits[c["name"]]["frames"] - 1
            c["seconds"] = round(c["frames"] / fps, 3)
    (out / "clips.json").write_text(json.dumps(clips_json, indent=2), encoding="utf-8")
    (out / "edit.json").write_text(
        json.dumps({"base": base_name, "fps": fps, "clips": layers}), encoding="utf-8"
    )
    outputs = {"rigged_anim": "rigged_anim.glb", "clips": "clips.json", "edit": "edit.json"}
    for key, f in (("skeleton", "skeleton.json"), ("parts", "parts.json"), ("qa", "qa.json")):
        if (src / f).is_file():
            shutil.copy(src / f, out / f)
            if key != "qa":
                outputs[key] = f
    return {
        "outputs": outputs,
        "metrics": {"clips": list(edits), "base": base_name},
        "licenses": [],
    }


if __name__ == "__main__":
    main(run)
