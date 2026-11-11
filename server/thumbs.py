"""Thumbnails for the library (256², cached as `_thumb.png` in the version folder; versions don't change).

A version's thumbnail: its own preview (generate's Cycles render, a Blender pull's screenshot), else a
render of its GLB (textured if baked, grey if not: clean/remesh still show their shape), else its picture
(concepts: the 2×2 candidates; prep/upload: the image), else its parent's (validate; export always: its GLBs hold collision hulls and LODs, not a look).
An asset's: its newest version with a 3D model, else its newest version with any thumbnail.

A Blender render (a still, a turntable) never runs in a request: it goes to the library's render worker, the request
answers at once (a blank still, a 503 turntable) and a `thumb` event says when the picture is there.
"""

import json
import os
import queue
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from server import blender_install, jobs, library
from steps._mesh import textured

SIZE = 256
CACHE = "_thumb.png"
IMAGES = (".png", ".jpg", ".jpeg", ".webp")
PIXEL_STEPS = {
    "pixelize",
    "sprites",
}  # research 15: their pictures are pixel art, shown with whole pixels
PENDING = Path("<rendering>")  # a version_thumb answer: the render is queued, a `thumb` event follows

# ponytail: one worker, one Blender at a time (each is a CPU render beside a job's GPU work); more threads on
# _loop if a big library's first visit drags
_work: queue.SimpleQueue = queue.SimpleQueue()
_queued: set[tuple] = set()
_failed: dict[tuple, str] = {}  # renders that came to nothing (no Blender, a broken GLB), until a restart
_guard = threading.Lock()
_worker: threading.Thread | None = None


def background(key: tuple[str, str, str], work: Callable[[], None]) -> None:
    """Queue `work` (kind, asset id, version) on the render worker once; a failure is kept in `_failed`."""
    global _worker
    with _guard:
        if key in _queued:
            return
        _queued.add(key)
        if _worker is None:
            _worker = threading.Thread(target=_loop, name="library-renders", daemon=True)
            _worker.start()
    _work.put((key, work))


def _loop() -> None:
    while True:
        key, work = _work.get()
        try:
            work()
        except Exception as e:  # noqa: BLE001 - one bad render must not stop the worker
            print(f"library render {key} failed: {e}")
            _failed[key] = str(e)
        finally:
            with _guard:
                _queued.discard(key)
            jobs.publish("thumb", {"kind": key[0], "asset_id": key[1], "version": key[2]})


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


def _files(d: Path, outputs: dict) -> dict:
    return {k: v for k, v in outputs.items() if isinstance(v, str) and (d / v).is_file()}


def _own(files: dict) -> str | None:
    return next(
        (files[k] for k in ("preview", "thumb") if files.get(k, "").lower().endswith(IMAGES)), None
    )


def _mesh(d: Path, files: dict) -> str | None:
    return next((v for v in files.values() if v.lower().endswith(".glb") and _has_mesh(d / v)), None)


def _make(d: Path, outputs: dict, dst: Path, nearest: bool = False, render: bool = True) -> bool:
    files = _files(d, outputs)
    if own := _own(files):
        _fit([d / own], dst, nearest=nearest)
        return True
    glb = _mesh(d, files)
    if glb and render and _render(d / glb, dst):
        return True
    cands = [d / files[k] for k in sorted(files) if k.startswith("cand_")]
    picture = cands or (
        [d / files["image"]] if files.get("image", "").lower().endswith(IMAGES) else []
    )
    if picture:
        _fit(picture, dst, nearest=nearest)
    return bool(picture)


def version_thumb(asset_id: str, version: str) -> Path | None:
    """The cached still, else `PENDING` (its render queued), else its parent's, else None."""
    d = library.version_dir(asset_id, version)
    dst = d / CACHE
    if dst.is_file():
        return dst
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    key = ("still", asset_id, version)
    if meta.get("step") != "export":
        outputs, nearest = meta.get("outputs", {}), meta.get("step") in PIXEL_STEPS
        files = _files(d, outputs)
        if key not in _failed and not _own(files) and _mesh(d, files):

            def work() -> None:
                if not _make(d, outputs, dst, nearest):
                    raise RuntimeError("no render and no picture")

            background(key, work)
            return PENDING
        if _make(d, outputs, dst, nearest, render=False):  # pictures only: quick, here
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
