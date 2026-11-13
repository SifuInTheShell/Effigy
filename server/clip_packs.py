"""The owner's clearance for a clip pack (docs/clip_packs_spec.md, owner 2026-11-13), and what it changes in the open
library: the anim versions made with the pack, and the versions made from those.

The clearance is free text (the licence or permission the owner got), kept in `<pack>/clearance.json`: written here,
never by a pack's producer, so a producer's own `license` never lifts a restriction. Lifting is conservative: a
version is lifted only when its parent is clean and its own step could not have added a restriction of its own;
everything else keeps its flag and is reported. An empty clearance puts the restriction back on all of them.
"""

import json

from server import jobs, library, settings
from server.library import ApiError

# steps whose version may hold a restricted source besides its parent: they keep their flag when lifting
KEEP = {
    "blender_pull": "a Blender pull may hold objects from other versions",
    "textures": "a texture edit may hold another asset's material",
    "export": "its files were written restricted: export it again",
    **{s: "an experimental engine's output" for s in library.RESTRICTED_STEPS},
}


def set_clearance(pack: str, text: str) -> dict:
    """Save (or, empty, remove) the clearance and apply the pack's new state to the open library's versions:
    {"pack": the pack as /api/clip-packs lists it, "lifted"/"restricted": ["asset/version"], "kept": [{"version",
    "reason"}]}."""
    from steps.anim import BUILTIN, PACK_ID, pack_row, packs

    root = settings.models_root() / "clips"
    if pack == BUILTIN or not PACK_ID.fullmatch(pack) or not (root / pack / "pack.json").is_file():
        raise ApiError(404, "not_found", f"no clip pack {pack!r} with a pack.json")
    if any(j.status in ("queued", "running") for j in list(jobs.JOBS.values())):
        raise ApiError(409, "busy", "a job is running; set the clearance after it ends")
    text = text.strip()[:300]
    f = root / pack / "clearance.json"
    if text:
        f.write_text(
            json.dumps({"license": text, "set": library._now()}, indent=2), encoding="utf-8"
        )
    else:
        f.unlink(missing_ok=True)
    p = next(x for x in packs(root) if x["id"] == pack)
    report = _apply(pack_row(p), p["restricted"])
    return {"pack": {k: v for k, v in p.items() if k != "clips"}, **report}


def _apply(row: dict, restricted: bool) -> dict:
    lifted, put, kept = [], [], []
    with library._meta:
        for a in library.list_assets():
            metas = {m["version"]: m for m in library.versions(a["id"])}
            made = []  # the anim versions that used the pack: their row first, then the flags
            for v, m in metas.items():
                rows = m.get("metrics", {}).get("clip_packs", [])
                if m.get("step") == "anim" and any(r.get("name") == row["name"] for r in rows):
                    m["metrics"]["clip_packs"] = [
                        row if r.get("name") == row["name"] else r for r in rows
                    ]
                    _save(a["id"], m)
                    made.append(v)
            for v in made:
                if not restricted:
                    _lift(a["id"], v, metas, lifted, kept)
                    continue
                for name in library.subtree(a["id"], v):
                    if not metas[name].get("restricted"):
                        metas[name]["restricted"] = True
                        _save(a["id"], metas[name])
                        put.append(f"{a['id']}/{name}")
    return {"lifted": lifted, "restricted": put, "kept": sorted(kept, key=lambda k: k["version"])}


def _lift(asset_id: str, top: str, metas: dict, lifted: list, kept: list) -> None:
    """Lift `top` and the versions made from it, parents first, where nothing else restricts them."""
    for name in library.subtree(asset_id, top):
        m = metas[name]
        if not m.get("restricted") or any(k["version"] == f"{asset_id}/{name}" for k in kept):
            continue
        parent = metas.get(m.get("parent") or "")
        if parent and parent.get("restricted"):
            reason = f"its parent {parent['version']} is restricted"
        elif m.get("step") == "anim":
            others = [
                r["name"]
                for r in m.get("metrics", {}).get("clip_packs", [])
                if not r.get("commercial")
            ]
            reason = f"also uses restricted clip packs {others}" if others else ""
        else:
            reason = KEEP.get(m.get("step"), "")
        if reason:
            kept.append({"version": f"{asset_id}/{name}", "reason": reason})
            continue
        m["restricted"] = False
        _save(asset_id, m)
        lifted.append(f"{asset_id}/{name}")


def _save(asset_id: str, meta: dict) -> None:
    library._write(library.version_dir(asset_id, meta["version"]) / "meta.json", meta)
