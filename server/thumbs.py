"""Thumbnails for the library (256², cached as `_thumb.png` in the version folder; versions don't change).

A version's thumbnail: its own preview (generate's Cycles render, a Blender pull's screenshot), else a
render of its GLB (textured if baked, grey if not: clean/remesh still show their shape), else its picture
(concepts: the 2×2 candidates; prep/upload: the image), else its parent's (validate; export always: its GLBs hold collision hulls and LODs, not a look).
An asset's: its newest version with a 3D model, else its newest version with any thumbnail.
"""

import json
import os
import shutil
import threading
from pathlib import Path

from PIL import Image

from server import blender_install, library
from steps._mesh import textured

SIZE = 256
CACHE = "_thumb.png"
IMAGES = (".png", ".jpg", ".jpeg", ".webp")
PIXEL_STEPS = {
    "pixelize",
    "sprites",
}  # research 15: their pictures are pixel art, shown with whole pixels
# ponytail: one render at a time (each is a Blender on the CPU); a queue if it drags
_LOCK = threading.Lock()


def _fit(paths: list[Path], dst: Path, size: int = SIZE, nearest: bool = False) -> None:
    """One picture fitted into the square, or up to 4 in a 2×2 grid, on the viewer's dark grey; `nearest`:
    whole pixels (a zoom by a whole number up, a nearest shrink), never blurred."""
    canvas = Image.new("RGB", (size, size), (40, 40, 44))
    cell = size if len(paths) == 1 else size // 2
    for i, p in enumerate(paths[:4]):
        im = Image.open(p).convert("RGBA")
        if nearest and (k := cell // max(im.size)) > 1:
            im = im.resize((im.width * k, im.height * k), Image.NEAREST)
        im.thumbnail((cell, cell), Image.NEAREST if nearest else Image.LANCZOS)
        x, y = (i % 2) * cell + (cell - im.width) // 2, (i // 2) * cell + (cell - im.height) // 2
        canvas.paste(im, (x, y), im)
    tmp = dst.with_suffix(".tmp.png")
    canvas.save(tmp)
    tmp.replace(dst)


def _render(glb: Path, dst: Path) -> bool:
    from steps._blender import BlenderError, run_blender

    os.environ.setdefault("EFFIGY_BLENDER", str(blender_install.blender_exe()))
    tmp = dst.parent / "_thumb_render"
    try:
        run_blender("render_thumb", [glb], tmp, {"size": SIZE, "samples": 8}, timeout=180)
        (tmp / "thumb.png").replace(dst)
        return True
    except (BlenderError, OSError) as e:  # no Blender, a broken GLB: fall back to a picture
        print(f"thumbnail render failed for {glb}: {e}")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _has_mesh(path: Path) -> bool:
    """Motion GLBs contain bones/animation only. Inspect JSON without loading texture buffers."""
    try:
        with path.open("rb") as source:
            header = source.read(20)
            if header[:4] != b"glTF" or header[16:20] != b"JSON":
                return False
            doc = json.loads(source.read(int.from_bytes(header[12:16], "little")))
        meshes = doc.get("meshes", [])
        return any(
            "mesh" in node
            and 0 <= node["mesh"] < len(meshes)
            and any(
                "POSITION" in p.get("attributes", {})
                for p in meshes[node["mesh"]].get("primitives", [])
            )
            for node in doc.get("nodes", [])
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _make(d: Path, outputs: dict, dst: Path, nearest: bool = False) -> bool:
    files = {k: v for k, v in outputs.items() if isinstance(v, str) and (d / v).is_file()}
    own = next(
        (files[k] for k in ("preview", "thumb") if files.get(k, "").lower().endswith(IMAGES)), None
    )
    if own:
        _fit([d / own], dst, nearest=nearest)
        return True
    glb = next((v for v in files.values() if v.lower().endswith(".glb") and _has_mesh(d / v)), None)
    if glb and _render(d / glb, dst):
        return True
    cands = [d / files[k] for k in sorted(files) if k.startswith("cand_")]
    picture = cands or (
        [d / files["image"]] if files.get("image", "").lower().endswith(IMAGES) else []
    )
    if picture:
        _fit(picture, dst, nearest=nearest)
    return bool(picture)


def version_thumb(asset_id: str, version: str) -> Path | None:
    d = library.version_dir(asset_id, version)
    dst = d / CACHE
    if dst.is_file():
        return dst
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    with _LOCK:
        made = dst.is_file() or (
            meta.get("step") != "export"
            and _make(d, meta.get("outputs", {}), dst, meta.get("step") in PIXEL_STEPS)
        )
    if made:
        return dst
    return version_thumb(asset_id, meta["parent"]) if meta.get("parent") else None


def _glbs(asset_id: str, meta: dict) -> list[Path]:
    d = library.root() / asset_id / meta["version"]
    return [
        d / v
        for v in meta.get("outputs", {}).values()
        if str(v).lower().endswith(".glb") and _has_mesh(d / v)
    ]


def asset_version(asset_id: str) -> str | None:
    """The version an asset's card shows: the textured model first (the version worth picking up again),
    then any model, then the newest with any thumbnail."""
    newest = library.versions(asset_id)[::-1]
    kind = json.loads((library.asset_dir(asset_id) / "asset.json").read_text(encoding="utf-8")).get(
        "kind"
    )
    models = [
        m for m in newest if kind != "texture" and m.get("step") != "export" and _glbs(asset_id, m)
    ]
    looks = [m for m in models if any(textured(g) for g in _glbs(asset_id, m))]
    seen = set()
    for m in looks + models + newest:
        if m["version"] in seen:
            continue
        seen.add(m["version"])
        if version_thumb(asset_id, m["version"]):
            return m["version"]
    return None


def asset_thumb(asset_id: str) -> Path | None:
    return version_thumb(asset_id, v) if (v := asset_version(asset_id)) else None
