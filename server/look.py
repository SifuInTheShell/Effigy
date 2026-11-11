"""A version as the agent sees it (M40): a 2×2 sheet of the model (front, right, back, three-quarter), four
frames of one clip, or the version's pictures in a grid. Cached in the version folder, keyed by clip and size."""

import hashlib
import json
import os
import re
import shutil
import struct
import threading
from pathlib import Path

from server import blender_install, jobs, library, thumbs
from server.library import ApiError

IMAGES = thumbs.IMAGES
# one look render at a time; its own lock, so a preview never holds up the library's thumbnails
_LOCK = threading.Lock()


def clips(glb: Path) -> list[str]:
    """The animation names in a GLB's JSON chunk."""
    with open(glb, "rb") as f:
        f.seek(12)
        n, _ = struct.unpack("<II", f.read(8))
        return [a.get("name", "") for a in json.loads(f.read(n)).get("animations", [])]


def _sheet(cells: list[Path], dst: Path, size: int, nearest: bool = False) -> None:
    thumbs._fit(cells, dst, size, nearest)


def _render(glb: Path, dst: Path, size: int, clip: str) -> None:
    from steps._blender import BlenderError, run_blender

    os.environ.setdefault("EFFIGY_BLENDER", str(blender_install.blender_exe()))
    tmp = dst.parent / "_look_render"
    try:
        run_blender("render_look", [glb], tmp, {"size": size // 2, "clip": clip}, timeout=180)
        _sheet([tmp / f"look_{i}.png" for i in range(4)], dst, size)
    except (BlenderError, OSError) as e:
        raise ApiError(500, "render_failed", f"could not render {glb.name}: {e}") from e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def look(asset_id: str, version: str, clip: str = "", size: int = 768) -> Path:
    size = max(256, min(1024, size)) // 2 * 2
    d = library.version_dir(asset_id, version)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    outputs = meta.get("outputs", {})
    files = [d / v for v in outputs.values() if isinstance(v, str) and (d / v).is_file()]
    glb = next((f for f in files if f.suffix.lower() == ".glb"), None)
    pics = [f for f in files if f.suffix.lower() in IMAGES]
    # the candidates as a grid; else a version's own preview (a pixelize sheet) alone; else every picture
    pics = (
        [f for f in pics if f.name.startswith("cand_")]
        or [f for f in pics if f.stem == "preview"]
        or pics
    )
    nearest = meta.get("step") in thumbs.PIXEL_STEPS
    if not glb and not pics:
        raise ApiError(400, "nothing_to_show", f"{version} has no model or picture to look at")
    if clip:
        known = clips(glb) if glb else []
        if clip not in known:
            raise ApiError(400, "bad_clip", f"no clip {clip!r} in {version}: {known or 'none'}")
    # readable, plus a short hash: "Walk Cycle" and "Walk_Cycle" are two clips, two files
    key = re.sub(r"[^\w.-]", "_", clip) + "_" + hashlib.sha1(clip.encode()).hexdigest()[:6]
    dst = d / (f"_look_{key}_{size}.png" if clip else f"_look_{size}.png")
    src = [glb] if glb else pics
    with _LOCK:
        if not (dst.is_file() and all(dst.stat().st_mtime >= s.stat().st_mtime for s in src)):
            _render(glb, dst, size, clip) if glb else _sheet(pics, dst, size, nearest)
    return dst


def turntable(asset_id: str, version: str) -> Path:
    """The model turning once (M42): 24 Workbench frames of 256², an animated WebP cached as `_turn.webp`.
    A validate or export version shows the nearest ancestor that is neither (their GLBs are not a look)."""
    from PIL import Image

    meta = library._read(library.version_dir(asset_id, version) / "meta.json")
    while meta.get("step") in ("validate", "export") and meta.get("parent"):
        meta = library._read(library.version_dir(asset_id, meta["parent"]) / "meta.json")
    d = library.version_dir(asset_id, meta["version"])
    outs = [d / v for v in meta.get("outputs", {}).values() if isinstance(v, str)]
    glb = next((f for f in outs if f.suffix.lower() == ".glb" and f.is_file()), None)
    if not glb:
        raise ApiError(400, "nothing_to_show", f"{meta['version']} has no model to turn")
    dst, n = d / "_turn.webp", 24
    if dst.is_file() and dst.stat().st_mtime >= glb.stat().st_mtime:
        return dst
    key = ("turn", asset_id, meta["version"])
    if err := thumbs._failed.get(key):
        raise ApiError(500, "render_failed", f"could not render {glb.name}: {err}")
    # never in the request, never beside a job on the GPU: the card retries on the `thumb` event or the next hover
    if jobs.GPU.locked():
        raise ApiError(503, "busy", "busy, try again later")

    def work() -> None:
        from steps._blender import run_blender

        os.environ.setdefault("EFFIGY_BLENDER", str(blender_install.blender_exe()))
        tmp = d / "_turn_render"
        try:
            with _LOCK:  # not beside an agent's look render
                run_blender("render_look", [glb], tmp, {"size": 256, "turn": n}, timeout=180)
            frames = [Image.open(tmp / f"look_{i}.png").convert("RGB") for i in range(n)]
            part = dst.with_suffix(".tmp.webp")
            frames[0].save(
                part, save_all=True, append_images=frames[1:], duration=67, loop=0, quality=80
            )
            part.replace(dst)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    thumbs.background(key, work)
    raise ApiError(503, "rendering", "the turntable is being rendered: a `thumb` event follows")
