"""Export a version for an engine (IMPLEMENTATION.md M7, M13): files + CREDITS.txt + LICENSES.json.

params: {"profile": unity|unreal|godot|web|print|obj|godot-2d|unreal-2d, "lods": true, "collision": true,
         "allow_restricted": false}

A 2D version (a sprite sheet: pixelize, sprites; research 15 §5.8, M53) takes the 2D profiles:
- godot-2d: a seamless tile (M54): <name>.png + <name>.tres (a TileSet holding it) + README.txt; else
  sheet.png + sheet.json (Aseprite), <name>.tres (a SpriteFrames, one animation per tag, the sheet's
  durations), <name>.tscn (an AnimatedSprite2D standing on its origin, Nearest filtering), README.txt (the
  pixel-perfect project settings).
- unreal-2d: <name>.png + <name>.paper2dsprites (Paper2D's sprite sheet: one sprite per frame named
  <animation>_<NN>, the pivot at the feet), README.txt; a tile: <name>.png + README.txt (its Tile Set is made in
  the editor).

Engines pick LODs and collision up by name (import checklist: docs/bench/m13.md):
- unity, unreal: one FBX with an LOD group `<name>` of `<name>_LOD0..3` (≈ 100/50/25/10 % triangles).
  unreal also gets the convex hull as `UCX_<name>_00`. Unity has no collision naming: add a
  Mesh Collider (Convex) and Unity hulls the mesh itself.
- godot: GLB + the hull as node `<name>-convcolonly` (a StaticBody3D on import); Godot makes LODs itself.
- a rigged model (a skeleton.json beside its mesh) ships no hull unless `collision: true` is sent: engines
  give characters their own physics (a capsule, Unreal's physics asset), and Unreal imports the hull of a
  skeletal mesh as a separate static mesh.
- web: meshopt GLB with WebP textures ≤ 1024 px (gltfpack) + USDZ (Blender) for iOS Quick Look.
- print: STL + 3MF in millimetres, Z up (slicers). obj: OBJ + MTL + base colour texture, Y up, metres.

A parts version (M31, a parts.json beside the mesh) keeps one object per part, named like the part: each
part gets its own LODs (Helmet_LOD0..3 in an LOD group Helmet) and its own hull (UCX_Helmet_00,
Helmet-convcolonly). In print (M33) the parts become solids that fit together (steps/_print.py, then
blender_ops/print_parts.py): one STL per part (<name>_Helmet.stl), all of them in <name>.stl, and a 3MF
with one named object per part; params "print_detail": voxels across the model's height (300).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import trimesh
import yaml
from PIL import Image

from steps import _engine2d
from steps._blender import run_blender
from steps._common import StepError, main, progress
from steps._print import split_solid

ROOT = Path(__file__).resolve().parents[1]
BLENDER = {  # blender_ops/export.py formats
    "unity": [{"kind": "fbx", "preset": "unity"}],
    "unreal": [{"kind": "fbx", "preset": "unreal"}],
    "godot": [{"kind": "glb"}],
    "web": [{"kind": "usdz"}],
}
PROFILES_2D = ["godot-2d", "unreal-2d"]
PROFILES = [*BLENDER, "print", "obj", *PROFILES_2D, "textures"]
LODS = {"unity", "unreal"}
COLLISION = {
    "unreal": "UCX_{}_00",
    "godot": "{}-convcolonly",
}  # {} = the mesh asset name, not _LOD0
LOD_RATIOS = (0.5, 0.25, 0.1)
WEB = ("-cc", "-tw", "-tl", "1024")  # meshopt, WebP, textures capped at 1024 px
# library frame (Y up, metres) → slicer frame (Z up, millimetres): (x, y, z) → 1000·(x, -z, y)
PRINT = np.array([[1e3, 0, 0, 0], [0, 0, -1e3, 0], [0, 1e3, 0, 0], [0, 0, 0, 1]])


def credits(names: list[str], rows: list[dict]) -> tuple[str, list[dict]]:
    """CREDITS.txt text and LICENSES.json rows for the components that touched the version chain."""
    by_name = {r["name"]: r for r in rows}
    if missing := sorted(set(names) - by_name.keys()):
        raise StepError("failed", f"components without a licenses.yaml row: {missing}")
    used = [by_name[n] for n in sorted(set(names))]
    lines = [r["attribution"] for r in used if r.get("attribution")]
    return "".join(f"{line}\n" for line in lines), used


def pack_rows(version: Path) -> list[dict]:
    """The licence rows of the clip packs this version's chain used: an anim keeps them in `metrics.clip_packs`
    (a user's pack has no licenses.yaml row, its pack.json is its record, and the pack may be gone by now)."""
    rows, d = [], version
    while (d / "meta.json").is_file():
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        rows += meta.get("metrics", {}).get("clip_packs", [])
        if not meta.get("parent"):
            break
        d = d.parent / meta["parent"]
    return rows


def gltfpack(src: Path, dst: Path, *args: str) -> Path:
    exe = os.environ.get("EFFIGY_GLTFPACK") or shutil.which("gltfpack")
    if not exe or not Path(exe).is_file():
        raise StepError(
            "failed", "gltfpack not found: run `uv run python -m server.gltfpack_install`"
        )
    r = subprocess.run(
        [exe, "-i", str(src), "-o", str(dst), *args],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if r.returncode or not dst.is_file():
        raise StepError("failed", f"gltfpack {' '.join(args)}: {r.stderr.strip()[-500:]}")
    return dst


def make_lods(src: Path, tmp: Path, *keep: str) -> list[Path]:
    """LOD1..3 by gltfpack's attribute-aware simplifier: UV/normal seams hold, so LODs stay textured.
    -noq: plain floats, no extensions, for Blender; -se 0.05: allow 5 % deviation to reach the ratio;
    keep "-kn" for a parts version (else gltfpack merges the parts into one unnamed mesh)."""
    return [
        gltfpack(src, tmp / f"lod{i}.glb", "-noq", "-si", str(r), "-se", "0.05", *keep)
        for i, r in enumerate(LOD_RATIOS, 1)
    ]


def part_meshes(src: Path) -> dict[str, trimesh.Trimesh]:
    """A parts version's meshes by node name (the part names), in the scene frame."""
    s = trimesh.load(src, force="scene")
    return {
        n: s.geometry[g].copy().apply_transform(t)
        for n in s.graph.nodes_geometry
        for t, g in [s.graph[n]]
    }


def hull(m: trimesh.Trimesh) -> "trimesh.Trimesh | None":
    """The convex hull, or None for a flat mesh (sign, plane): it hulls to zero volume, which engines
    reject, so it ships without one. ponytail: exact hull of every vertex; cap its vertex count if an
    engine refuses it"""
    try:
        h = m.convex_hull
    except RuntimeError:  # qhull gives up on degenerate input
        return None
    return None if h.volume < 1e-6 * m.scale**3 else h


def blender_profile(
    src: Path, out: Path, name: str, profile: str, params: dict, parts: bool
) -> list[str]:
    keep = ("-kn",) if parts else ()  # gltfpack: keep the named part nodes
    base = "{}" if parts else name  # "{}": blender_ops/export keeps the parts, each named by it
    inputs, objects = [src], [base]
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        if profile in LODS and params.get("lods", True):
            progress(0.2, "LODs")
            inputs += make_lods(src, Path(tmp), *keep)
            objects = [f"{base}_LOD{i}" for i in range(len(inputs))]
        skel = src.parent / "skeleton.json"
        # a character gets the engine's own physics (a capsule, Unreal's physics asset): no hull unless asked,
        # as the export panel defaults; Unreal 5.8 imports it beside the skeletal mesh (engine check 2026-10-11)
        if profile in COLLISION and params.get("collision", not skel.is_file()):
            meshes = part_meshes(src) if parts else {name: trimesh.load(src, force="mesh")}
            for part, m in meshes.items():
                if (h := hull(m)) is None:
                    print(f"no collision mesh for {part}: it has no volume to hull")
                    continue
                inputs.append(Path(tmp, f"hull{len(inputs)}.glb"))
                h.export(inputs[-1])
                objects.append(COLLISION[profile].format(part))  # UE 5.8: UCX_<asset>_NN
        progress(0.4, f"blender export {profile}")
        job = {"name": name, "objects": objects, "formats": BLENDER[profile]}
        if skel.is_file():  # M36: a creature keeps its bone names in the FBX
            job["humanoid"] = (
                json.loads(skel.read_text(encoding="utf-8")).get("template", "mixamo52")
                == "mixamo52"
            )
        files = run_blender("export", inputs, out, job)["files"]
    (out / "export_job.json").unlink(missing_ok=True)  # ship only the files; export.log stays
    if profile == "web":
        files.append(gltfpack(src, out / f"{name}.glb", *WEB, *keep).name)
    return files


def print_parts(src: Path, out: Path, name: str, detail: int) -> str:
    """M33: the parts as solids that fit together: one STL each, all in one STL, a 3MF of named objects.
    The parts travel through Blender as part0, part1... (it shortens names over 63 bytes); a part that comes
    back missing fails the export. Returns a note for the version ("" when all went well)."""
    if not 100 <= detail <= 600:
        raise StepError("bad_params", f"print_detail {detail}: 100 to 600 voxels over the height")
    meshes = part_meshes(src)
    names = list(meshes)
    parts = {f"part{i}": m for i, m in enumerate(meshes.values()) if m.area > 0}
    height = np.ptp(np.concatenate([m.vertices for m in parts.values()])[:, 1]) if parts else 0
    if not height:
        raise StepError("failed", "the parts have no height to print")
    pitch = height / detail
    progress(0.2, "the parts as solids")
    voxels, hollow = split_solid(parts, pitch)
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        scene = trimesh.Scene()
        for n, m in voxels.items():
            scene.add_geometry(m, geom_name=n, node_name=n)
        scene.export(Path(tmp, "voxels.glb"))
        progress(0.6, "rounding off the voxel steps")
        run_blender("print_parts", [Path(tmp, "voxels.glb")], Path(tmp), {"voxel": pitch})
        solids = {}
        for n, m in part_meshes(Path(tmp, "solid.glb")).items():
            m = trimesh.Trimesh(m.vertices, m.faces)
            m.merge_vertices()  # glTF splits them at hard normals: one closed surface again
            solids[names[int(n.removeprefix("part"))]] = m.apply_transform(PRINT)
    if lost := [n for n in names if n not in solids]:
        raise StepError(
            "failed", f"the print split lost {lost}: too small at print_detail {detail}?"
        )
    files = set()
    for n, m in solids.items():
        stem = f"{name}_{re.sub(r'[^A-Za-z0-9-]+', '_', n).strip('_') or 'part'}"
        while stem in files:  # two names that read alike once made file-safe
            stem += "_"
        files.add(stem)
        m.export(out / f"{stem}.stl")
    trimesh.util.concatenate(list(solids.values())).export(out / f"{name}.stl")
    write_3mf(out / f"{name}.3mf", [(n, m.vertices, m.faces) for n, m in solids.items()])
    if hollow:
        note = "a hole in the model too wide to close: the parts are hollow shells, check them in the slicer"
        print(note)
        return note
    return ""


def trimesh_profile(
    src: Path, out: Path, name: str, profile: str, parts: bool, params: dict
) -> tuple[list[str], str]:
    note = ""
    if profile == "print" and parts:
        note = print_parts(src, out, name, int(params.get("print_detail", 300)))
    elif profile == "obj":  # a scene: one object per part of a parts version
        s = trimesh.load(src, force="scene")
        for g in s.geometry.values():
            mat = getattr(g.visual, "material", None)
            if getattr(mat, "baseColorFactor", 0) is None:
                mat.baseColorFactor = [
                    255,
                    255,
                    255,
                    255,
                ]  # glTF's default; else trimesh writes Kd 0.4
        s.export(out / f"{name}.obj", mtl_name=f"{name}.mtl")
    else:
        m = trimesh.load(src, force="mesh")
        solid = trimesh.Trimesh(m.vertices, m.faces)  # geometry only, welded at UV seams
        solid.apply_transform(PRINT)
        solid.export(out / f"{name}.stl")
        write_3mf(out / f"{name}.3mf", [(name, solid.vertices, solid.faces)])
    return sorted(f.name for f in out.iterdir()), note


def write_3mf(path: Path, objects: list[tuple[str, np.ndarray, np.ndarray]]) -> None:
    """Minimal 3MF core package (named mesh objects, millimetres; a slicer lists each one): stdlib instead
    of trimesh's exporter, which needs lxml (its wheels bundle LGPL libiconv) for these three small XML
    files."""
    res, build = [], []
    for i, (name, vertices, faces) in enumerate(objects, 1):
        v = "".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in vertices)
        t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in faces)
        res.append(
            f'<object id="{i}" name="{escape(name, {chr(34): "&quot;"})}" type="model"><mesh>'
            f"<vertices>{v}</vertices><triangles>{t}</triangles></mesh></object>"
        )
        build.append(f'<item objectId="{i}"/>')
    xml = '<?xml version="1.0" encoding="UTF-8"?>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            f'{xml}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            f'{xml}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rel0" Target="/3D/3dmodel.model" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>',
        )
        z.writestr(
            "3D/3dmodel.model",
            f'{xml}<model unit="millimeter" xml:lang="en-US" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
            f"<resources>{''.join(res)}</resources><build>{''.join(build)}</build></model>",
        )


def sheet_profile(src: Path, outs: dict, out: Path, name: str, profile: str) -> list[str]:
    """A sprite sheet for a 2D engine (M53): the files of `godot-2d` or `unreal-2d`. A seamless tile (M54): the
    tile, and for Godot a TileSet holding it."""
    if "layers" in outs:  # M55: parallax layers, far to near
        layers = json.loads((src / outs["layers"]).read_text(encoding="utf-8"))
        files = []
        for d in layers:
            Image.open(src / d["file"]).save(out / d["file"])
            files.append(d["file"])
        w, h = Image.open(src / layers[0]["file"]).size
        speeds = ", ".join(f"{d['scroll_scale']:g}" for d in layers)
        if profile == "godot-2d":
            (out / "parallax.tscn").write_text(
                _engine2d.godot_parallax(name, layers), encoding="utf-8"
            )
            files.append("parallax.tscn")
            text = _engine2d.GODOT_LAYERS_README.format(
                name=name, speeds=speeds, w=w, h=h, fit=f"{360 / h:.3g}"
            )
        else:
            text = _engine2d.UNREAL_LAYERS_README.format(
                name=name, speeds=speeds, files=", ".join(files)
            )
        (out / "README.txt").write_text(text, encoding="utf-8")
        return [*files, "README.txt"]
    if "tile" in outs:
        tile = Image.open(src / outs["tile"]).convert("RGBA")
        tile.save(out / f"{name}.png")
        files, w, h = [f"{name}.png"], *tile.size
        if profile == "godot-2d":
            tileset = _engine2d.godot_tileset(f"{name}.png", w, h)
            (out / f"{name}.tres").write_text(tileset, encoding="utf-8")
            files.append(f"{name}.tres")
        if (
            profile == "godot-2d"
        ):  # pixel art (a pixelized tile has its sheet): the pixel-perfect settings too
            template = _engine2d.GODOT_TILE_README + ("sheet" in outs) * _engine2d.GODOT_PIXEL_TILE
        else:
            template = _engine2d.UNREAL_TILE_README
        (out / "README.txt").write_text(template.format(name=name, w=w, h=h), encoding="utf-8")
        return [*files, "README.txt"]
    sheet = Image.open(src / outs["sheet"]).convert("RGBA")
    data = json.loads((src / outs["data"]).read_text(encoding="utf-8"))
    if profile == "godot-2d":
        data["meta"] = data["meta"] | {"image": "sheet.png"}
        sheet.save(out / "sheet.png")
        (out / "sheet.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
        (out / f"{name}.tres").write_text(
            _engine2d.godot_sprite_frames(data, "sheet.png"), encoding="utf-8"
        )
        scene = _engine2d.godot_scene(name, f"{name}.tres", data, _engine2d.feet_row(sheet, data))
        (out / f"{name}.tscn").write_text(scene, encoding="utf-8")
        readme = _engine2d.readme(_engine2d.GODOT_README, name, data)
        files = ["sheet.png", "sheet.json", f"{name}.tres", f"{name}.tscn"]
    else:
        sheet.save(out / f"{name}.png")
        (out / f"{name}.paper2dsprites").write_text(
            _engine2d.paper2d(data, f"{name}.png"), encoding="utf-8"
        )
        readme = _engine2d.readme(_engine2d.UNREAL_README, name, data)
        files = [f"{name}.png", f"{name}.paper2dsprites"]
    (out / "README.txt").write_text(readme, encoding="utf-8")
    return [*files, "README.txt"]


def run(op, in_dir, out_dir, params):
    profile = params.get("profile", "unity")
    if profile not in PROFILES:
        raise StepError("bad_params", f"profile must be one of {PROFILES}")
    if not in_dir:
        raise StepError("missing_input", "export needs a parent version")
    meta = json.loads((Path(in_dir) / "meta.json").read_text(encoding="utf-8"))
    if meta.get("restricted") and not params.get("allow_restricted"):
        raise StepError(
            "restricted",
            "restricted version (an experimental engine, a third-party import or a restricted clip pack);"
            " see IMPLEMENTATION MX1",
        )
    outs = meta["outputs"]
    note = ""
    if profile == "textures":
        from steps import _textures as tx

        maps, manifest = tx.load_set(Path(in_dir), int(params.get("material", 0)))
        out = Path(out_dir) / profile
        files = list(dict.fromkeys(tx.write_set(out, maps, manifest).values()))
    elif profile in PROFILES_2D:  # a sprite sheet (M53)
        if not ("sheet" in outs and "data" in outs or "tile" in outs or "layers" in outs):
            raise StepError(
                "missing_input",
                f"{meta['version']}: a 2D export needs a sprite sheet (pixel art or sprites), a tile or layers",
            )
        progress(0.1, f"export {profile}")
        name, out, note = meta["asset_id"], Path(out_dir) / profile, ""
        out.mkdir(parents=True, exist_ok=True)
        files = sheet_profile(Path(in_dir), outs, out, name, profile)
    else:
        mesh = next((f for f in outs.values() if f.endswith(".glb")), None)
        if not mesh:
            raise StepError("missing_input", f"{meta['version']} has no .glb output")
        progress(0.1, f"export {profile}")
        src, name, out = Path(in_dir) / mesh, meta["asset_id"], Path(out_dir) / profile
        out.mkdir(parents=True, exist_ok=True)
        parts, note = (Path(in_dir) / "parts.json").is_file(), ""
        if profile in BLENDER:
            files = blender_profile(src, out, name, profile, params, parts)
        else:
            files, note = trimesh_profile(src, out, name, profile, parts, params)

    rows = yaml.safe_load((ROOT / "licenses.yaml").read_text(encoding="utf-8"))
    text, used = credits(meta["licenses"], rows + pack_rows(Path(in_dir)))
    if meta.get("restricted"):
        text += (
            "RESTRICTED: made with an experimental engine, third-party material or a clip pack whose licence"
            " limits use (see LICENSES.json).\n"
        )
    (out / "CREDITS.txt").write_text(text, encoding="utf-8")
    licenses = {"restricted": bool(meta.get("restricted")), "components": used}
    (out / "LICENSES.json").write_text(json.dumps(licenses, indent=2), encoding="utf-8")
    files = [*files, "CREDITS.txt", "LICENSES.json"]
    if profile == "textures":
        import zipfile

        with zipfile.ZipFile(out / "textures.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
            for filename in files:
                bundle.write(out / filename, filename)
        files.append("textures.zip")
    result = {"outputs": {f: f"{profile}/{f}" for f in files}, "metrics": {"profile": profile}}
    return result | ({"note": note} if note else {})


if __name__ == "__main__":
    main(run)
