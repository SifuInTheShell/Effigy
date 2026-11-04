"""M7/M13 Blender ops against a real Blender (skipped without one: set EFFIGY_BLENDER or run blender_install)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from server.blender_install import blender_exe
from server.gltfpack_install import gltfpack_exe
from steps import export
from steps._blender import run_blender

FIXTURE = Path(__file__).parent / "fixtures" / "two_tris.glb"
ROOT_MODELS = Path(os.environ.get("EFFIGY_MODELS", Path(__file__).resolve().parents[1] / "models"))
BLENDER = blender_exe()
GLTFPACK = gltfpack_exe()
pytestmark = pytest.mark.skipif(
    not BLENDER.is_file(), reason="no Blender (EFFIGY_BLENDER / blender_install)"
)


@pytest.fixture(autouse=True)
def blender_env(monkeypatch):
    monkeypatch.setenv("EFFIGY_BLENDER", str(BLENDER))


@pytest.fixture(scope="module")
def sphere(tmp_path_factory):
    path = tmp_path_factory.mktemp("sphere") / "sphere.glb"
    code = (
        "import bpy; bpy.ops.wm.read_factory_settings(use_empty=True); "
        "bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16); "
        f"bpy.ops.export_scene.gltf(filepath={str(path)!r}, export_format='GLB')"
    )
    subprocess.run(
        [BLENDER, "-b", "--factory-startup", "--python-expr", code], capture_output=True, check=True
    )
    return path


def test_unity_fbx_round_trip(tmp_path):
    files = run_blender(
        "export", [FIXTURE], tmp_path, {"name": "quad", "formats": export.BLENDER["unity"]}
    )
    assert files == {"files": ["quad.fbx"]}
    rep = run_blender("validate_mesh", [tmp_path / "quad.fbx"], tmp_path, {})["report"]
    assert rep["vertices"] == 4 and rep["triangles"] == 2


def test_quadriflow_unwrap_thumb(sphere, tmp_path):
    q = run_blender("quadriflow", [sphere], tmp_path / "q", {"target_faces": 400}, timeout=300)[
        "report"
    ]
    assert (
        q["faces"] > 0
        and q["quads"] == q["faces"]
        and q["non_manifold_edges"] == 0 == q["boundary_edges"]
    )
    u = run_blender("unwrap", [tmp_path / "q" / "mesh.glb"], tmp_path / "u", {})["report"]
    assert u["uv_layers"] == 1 and u["uv_in_unit_square"]
    run_blender("render_thumb", [sphere], tmp_path / "t", {"size": 64, "samples": 2})
    assert (tmp_path / "t" / "thumb.png").read_bytes()[:4] == b"\x89PNG"


def test_relative_out_dir(sphere, tmp_path, monkeypatch):
    r"""The library root is relative by default; Blender once wrote to C:\library\… on Windows."""
    monkeypatch.chdir(tmp_path)
    run_blender("render_thumb", [sphere], Path("lib") / "t", {"size": 32, "samples": 1})
    assert (tmp_path / "lib" / "t" / "thumb.png").is_file()


@pytest.mark.skipif(not GLTFPACK.is_file(), reason="no gltfpack (server.gltfpack_install)")
def test_engine_profiles(sphere, tmp_path, monkeypatch):
    """M13: LOD group + UCX_ hull in the Unreal FBX, -convcolonly hull in the Godot GLB, web GLB + USDZ."""
    monkeypatch.setenv("EFFIGY_GLTFPACK", str(GLTFPACK))
    parent = tmp_path / "020_uv"
    parent.mkdir()
    shutil.copy(sphere, parent / "mesh.glb")
    meta = {
        "asset_id": "crate",
        "version": "020_uv",
        "outputs": {"mesh": "mesh.glb"},
        "licenses": [],
    }
    (parent / "meta.json").write_text(json.dumps(meta))
    out = {
        p: export.run("run", str(parent), str(tmp_path / p), {"profile": p}) for p in export.BLENDER
    }

    fbx = (tmp_path / "unreal" / "unreal" / "crate.fbx").read_bytes()
    names = [b"LodGroup", *(f"crate_LOD{i}".encode() for i in range(4)), b"UCX_crate_00"]
    assert all(n in fbx for n in names)
    glb = (tmp_path / "godot" / "godot" / "crate.glb").read_bytes()
    nodes = json.loads(glb[20 : 20 + int.from_bytes(glb[12:16], "little")])["nodes"]
    assert sorted(n["name"] for n in nodes) == ["crate", "crate-convcolonly"]
    assert set(out["web"]["outputs"]) == {"crate.glb", "crate.usdz", "CREDITS.txt", "LICENSES.json"}


@pytest.mark.skipif(not GLTFPACK.is_file(), reason="no gltfpack (server.gltfpack_install)")
def test_parts_export_one_object_per_part(tmp_path, monkeypatch):
    """M31b: a parts version through Blender: an LOD group and a UCX_ hull per part in the Unreal FBX,
    the parts and their -convcolonly hulls as nodes in the Godot GLB."""
    from test_export_profiles import parts_version

    monkeypatch.setenv("EFFIGY_GLTFPACK", str(GLTFPACK))
    parent = parts_version(tmp_path)
    for p in ("unreal", "godot"):
        export.run("run", str(parent), str(tmp_path / p), {"profile": p})
    fbx = (tmp_path / "unreal" / "unreal" / "crate.fbx").read_bytes()
    for part in ("Iron Helmet", "Body"):
        names = [f"{part}_LOD{i}" for i in range(4)] + [f"UCX_{part}_00"]
        assert all(n.encode() in fbx for n in names), part
    assert fbx.count(b"LodGroup") >= 2
    glb = (tmp_path / "godot" / "godot" / "crate.glb").read_bytes()
    nodes = json.loads(glb[20 : 20 + int.from_bytes(glb[12:16], "little")])["nodes"]
    assert sorted(n["name"] for n in nodes) == [
        "Body",
        "Body-convcolonly",
        "Iron Helmet",
        "Iron Helmet-convcolonly",
    ]


def test_fbx_export_keeps_a_texture_per_image(tmp_path):
    """PIPE-002: two materials with their own packed base colour (a body and a hat) give two PNGs next to the
    FBX, each with its own pixels; the same role twice was one file before, the last image's."""
    import numpy as np
    import trimesh
    from PIL import Image

    from steps._blender import run_blender

    scene = trimesh.Scene()
    # the Belt's picture is the Body's byte for byte (a bake's ORM is embedded twice): one file for both
    for name, rgb, dx in (
        ("Body", (255, 0, 0), 0),
        ("Hat", (0, 255, 0), 2),
        ("Belt", (255, 0, 0), 4),
    ):
        m = trimesh.creation.box().apply_translation([dx, 0, 0])
        mat = trimesh.visual.material.PBRMaterial(
            name=name, baseColorTexture=Image.new("RGB", (4, 4), rgb)
        )
        m.visual = trimesh.visual.TextureVisuals(
            uv=np.full((len(m.vertices), 2), 0.5), material=mat
        )
        scene.add_geometry(m, node_name=name, geom_name=name)
    src = tmp_path / "two.glb"
    scene.export(src)
    out = tmp_path / "out"
    res = run_blender(
        "export",
        [src],
        out,
        {"name": "character", "formats": [{"kind": "fbx", "preset": "unreal"}]},
    )
    pngs = sorted(f for f in res["files"] if f.endswith(".png"))
    assert pngs == ["character_basecolor.png", "character_basecolor_2.png"]
    colours = {
        tuple(
            np.asarray(Image.open(out / f).convert("RGB"))
            .reshape(-1, 3)
            .mean(0)
            .round()
            .astype(int)
        )
        for f in pngs
    }
    assert colours == {(255, 0, 0), (0, 255, 0)}
    assert (out / "character.fbx").stat().st_size > 0


def test_live_link_snippets_pull_only_the_last_opened_version(sphere, tmp_path):
    """OPEN v010, OPEN v020, add an object to v020's collection, PULL: the GLB holds v020's objects only
    (both versions open once merged into one pulled mesh)."""
    import trimesh

    from server import blender_client as bc

    def snippet(code, **args):
        return "".join(f"{k} = {json.dumps(v)}\n" for k, v in args.items()) + code

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    a = "crate"
    script = "\n".join(
        [
            "import bpy\nbpy.ops.wm.read_factory_settings(use_empty=True)",
            snippet(
                bc.OPEN,
                collection=f"EFFIGY_{a}_v010",
                path=str(FIXTURE),
                asset_id=a,
                version="010_x",
            ),
            snippet(
                bc.OPEN,
                collection=f"EFFIGY_{a}_v020",
                path=str(sphere),
                asset_id=a,
                version="020_y",
            ),
            (
                "bpy.ops.mesh.primitive_cube_add(size=0.1)\no = bpy.context.object\n"
                "for c in o.users_collection: c.objects.unlink(o)\n"
                f"bpy.data.collections['EFFIGY_{a}_v020'].objects.link(o)"
            ),
            snippet(bc.ASSET_OBJECTS + bc.PULL, asset_id=a, inbox=str(inbox)),
        ]
    )
    (tmp_path / "s.py").write_text(script, encoding="utf-8")
    subprocess.run(
        [
            BLENDER,
            "-b",
            "--factory-startup",
            "--python-exit-code",
            "1",
            "--python",
            tmp_path / "s.py",
        ],
        capture_output=True,
        check=True,
    )
    pulled = trimesh.load(inbox / "edited.glb")
    assert len(pulled.geometry) == 2  # the sphere (v020) + the new cube, not the v010 triangles
    assert (inbox / "scene.blend").is_file()


def test_open_again_after_its_objects_were_removed(sphere, tmp_path):
    """An undo after Open removed the objects but left the empty collection: the next Open re-imports
    (it used to skip because the collection existed: "opened", nothing in Blender)."""
    from server import blender_client as bc

    def open_(v):
        args = {
            "collection": "EFFIGY_crate_v010",
            "path": str(sphere),
            "asset_id": "crate",
            "version": v,
        }
        return "".join(f"{k} = {json.dumps(x)}\n" for k, x in args.items()) + bc.OPEN

    out = tmp_path / "objects.json"
    script = "\n".join(
        [
            "import bpy, json\nbpy.ops.wm.read_factory_settings(use_empty=True)",
            open_("010_x"),
            "for o in list(bpy.data.collections['EFFIGY_crate_v010'].objects): bpy.data.objects.remove(o)",
            open_("010_x"),
            f"json.dump([o.name for o in bpy.data.collections['EFFIGY_crate_v010'].all_objects], open({str(out)!r}, 'w'))",
        ]
    )
    (tmp_path / "s.py").write_text(script, encoding="utf-8")
    subprocess.run(
        [
            BLENDER,
            "-b",
            "--factory-startup",
            "--python-exit-code",
            "1",
            "--python",
            tmp_path / "s.py",
        ],
        capture_output=True,
        check=True,
    )
    assert len(json.loads(out.read_text())) == 1


def test_skin_proxy_weights_a_block_figure(tmp_path):
    """M20: bone heat on the voxel proxy, moved onto the mesh, judged by rig_qa: every vertex weighted, ≤ 4 bones each."""
    import numpy as np
    import trimesh

    from steps.rig_skin import checklist
    from tests.test_rig import block_figure

    parts, joints = block_figure()
    v = np.concatenate([p[0] for p in parts])
    f = np.concatenate([p[1] + sum(len(q[0]) for q in parts[:i]) for i, p in enumerate(parts)])
    skel = {"joints": joints}
    trimesh.Trimesh(v, f).subdivide().subdivide().export(tmp_path / "mesh.glb")
    (tmp_path / "skeleton.json").write_text(json.dumps(skel))
    run_blender(
        "skin_proxy",
        [tmp_path / "mesh.glb", tmp_path / "skeleton.json"],
        tmp_path / "out",
        {},
        timeout=600,
    )
    rep = run_blender(
        "rig_qa", [tmp_path / "out" / "rigged.glb"], tmp_path / "qa", {}, timeout=600
    )["report"]
    assert (
        rep["unweighted_vertices"] == 0
        and rep["weight_sum_off"] == 0
        and rep["max_influences"] <= 4
    )
    assert (tmp_path / "out" / "rigged.glb").is_file()
    items = checklist(rep)
    assert items["unweighted"]["status"] == "pass" and items["influences"]["status"] == "pass"


def test_skin_proxy_keeps_the_parts_and_a_rigid_part_follows_one_bone(tmp_path):
    """M32: a parts version (the block figure's head as "Helmet", the rest "Body") comes out of bone
    heat as two skinned objects named like the parts; the helmet made rigid has every vertex on Head
    alone; rig_qa judges both parts together."""
    import numpy as np
    import trimesh

    from steps import _glb, rig_skin
    from tests.test_rig import block_figure

    parts, joints = block_figure()
    src = tmp_path / "200_rig_fit"
    src.mkdir()
    scene = trimesh.Scene()
    rest = [p for i, p in enumerate(parts) if i != 1]
    body = trimesh.util.concatenate([trimesh.Trimesh(*p) for p in rest])
    for name, m in (("Helmet", trimesh.Trimesh(*parts[1])), ("Body", body)):
        scene.add_geometry(m.subdivide().subdivide(), geom_name=name, node_name=name)
    scene.export(src / "mesh.glb")
    (src / "skeleton.json").write_text(json.dumps({"joints": joints}))
    (src / "parts.json").write_text(
        json.dumps(
            {"views": "x", "labels": [], "parts": [{"id": 1, "name": "Helmet", "faces": 192}]}
        )
    )
    # without parts.json the two objects stay joined, as any model of several pieces did before M32
    run_blender("skin_proxy", [src / "mesh.glb", src / "skeleton.json"], tmp_path / "one", {})
    doc, _ = _glb.read(tmp_path / "one" / "rigged.glb")
    assert len([n for n in doc["nodes"] if "mesh" in n and "skin" in n]) == 1
    run_blender(
        "skin_proxy",
        [src / "mesh.glb", src / "skeleton.json"],
        tmp_path / "out",
        {"parts": True},
        timeout=600,
    )
    glb = tmp_path / "out" / "rigged.glb"
    doc, _ = _glb.read(glb)
    skinned = {n["name"] for n in doc["nodes"] if "mesh" in n and "skin" in n}
    assert skinned == {"Helmet", "Body"}
    assert {m["name"] for m in doc["meshes"]} == {"Helmet", "Body"}  # no ".001" left over

    bones = rig_skin.rigid_bones(src, {"Helmet": ""})
    assert bones == {"Helmet": "Head"}
    rig_skin.make_rigid(glb, bones)
    doc, blob = _glb.read(glb)
    helmet = next(n for n in doc["nodes"] if n.get("name") == "Helmet" and "mesh" in n)
    head = next(
        k
        for k, i in enumerate(doc["skins"][helmet["skin"]]["joints"])
        if doc["nodes"][i]["name"] == "Head"
    )
    for prim in doc["meshes"][helmet["mesh"]]["primitives"]:
        jv = _glb.accessor(doc, blob, prim["attributes"]["JOINTS_0"])
        wv = _glb.accessor(doc, blob, prim["attributes"]["WEIGHTS_0"]).astype(float)
        assert (jv[:, 0] == head).all() and np.allclose(wv / wv[:, :1], [1, 0, 0, 0])
    rep = run_blender("rig_qa", [glb], tmp_path / "qa", {}, timeout=600)["report"]
    n_verts = sum(len(scene.geometry[g].vertices) for g in scene.geometry)
    assert rep["unweighted_vertices"] == 0 and rep["weight_sum_off"] == 0
    assert rep["vertices"] >= n_verts  # both parts (glTF splits vertices at seams, never merges)


@pytest.mark.skipif(
    not (ROOT_MODELS / "clips" / "mesh2motion" / "human-base-animations.glb").is_file(),
    reason="no clip pack (python -m tools.download_models clips)",
)
def _walk_on_block_figure(tmp_path) -> tuple[Path, Path, dict]:
    """The block figure skinned (skin/rigged.glb, skeleton.json) and the pack's Walk retargeted onto it
    (anim/rigged_anim.glb); the retarget's report."""
    import json as _json

    import numpy as np
    import trimesh

    from steps import _rig
    from tests.test_rig import _box

    parts = [
        _box((0.4, 0.6, 0.25), (0, 1.2, 0)),
        _box((0.22, 0.25, 0.22), (0, 1.62, 0)),
        _box((0.6, 0.1, 0.1), (0.5, 1.4, 0)),
        _box((0.6, 0.1, 0.1), (-0.5, 1.4, 0)),
        _box((0.14, 0.9, 0.14), (0.1, 0.45, 0)),
        _box((0.14, 0.9, 0.14), (-0.1, 0.45, 0)),
    ]
    v = np.concatenate([p[0] for p in parts])
    f = np.concatenate([p[1] + sum(len(q[0]) for q in parts[:i]) for i, p in enumerate(parts)])
    m = {"Chin": [0, 1.52, 0], "Groin": [0, 0.88, 0]}
    for s, x in (("Left", 1), ("Right", -1)):
        m |= {
            f"{s}Shoulder": [x * 0.22, 1.4, 0],
            f"{s}Elbow": [x * 0.5, 1.4, 0],
            f"{s}Wrist": [x * 0.75, 1.4, 0],
        }
        m |= {f"{s}Knee": [x * 0.1, 0.48, 0], f"{s}Ankle": [x * 0.1, 0.08, 0]}
    j = _rig.skeleton(m, v, f)
    parent = dict(_rig.template())
    trimesh.Trimesh(v, f).subdivide().export(tmp_path / "mesh.glb")
    (tmp_path / "skeleton.json").write_text(
        _json.dumps(
            {
                "joints": [
                    {"name": n, "parent": parent[n], "position": p}
                    | ({"twist": [0, -1, 0]} if n == "LeftHand" else {})  # M22b: a palm facing down
                    for n, p in j.items()
                ]
            }
        )
    )
    run_blender(
        "skin_proxy",
        [tmp_path / "mesh.glb", tmp_path / "skeleton.json"],
        tmp_path / "skin",
        {},
        timeout=600,
    )
    pack = ROOT_MODELS / "clips" / "mesh2motion" / "human-base-animations.glb"
    rep = run_blender(
        "retarget",
        [tmp_path / "skin" / "rigged.glb", pack],
        tmp_path / "anim",
        {"clips": ["Walk"], "skeleton": str(tmp_path / "skeleton.json")},
        timeout=900,
    )["report"]
    return tmp_path / "skin", tmp_path / "skeleton.json", rep


@pytest.mark.skipif(
    not (ROOT_MODELS / "clips" / "mesh2motion").is_dir(),
    reason="no clip pack (download_models clips)",
)
def test_retarget_a_clip_onto_the_block_figure(tmp_path):
    """M21: the Retarget extension binds our Mixamo-named rig to the pack's UE5-named one; Blender bakes
    the clip; the GLB carries one animation on every bone."""
    import json as _json
    import struct

    _, _, rep = _walk_on_block_figure(tmp_path)
    assert [c["name"] for c in rep["clips"]] == ["Walk"] and rep["clips"][0]["seconds"] > 1
    b = (tmp_path / "anim" / "rigged_anim.glb").read_bytes()
    doc = _json.loads(b[20 : 20 + struct.unpack("<I", b[12:16])[0]])
    assert [a["name"] for a in doc["animations"]] == ["Walk"]
    animated = {c["target"]["node"] for c in doc["animations"][0]["channels"]}
    assert len(animated) == 52 and len(doc["skins"][0]["joints"]) == 52


def test_parts_views_and_split_keep_the_face_order(tmp_path):
    """M31: GeoSAM2 labels the faces of views/mesh.glb as trimesh reads them and parts_split cuts them in
    Blender's import order; a model of two materials (two primitives) under a rotated, scaled parent
    node must come out normalised and cut along the labels."""
    import numpy as np
    import trimesh

    tilt = trimesh.transformations.rotation_matrix(0.7, [1, 1, 0]) @ np.diag([3.0, 3.0, 3.0, 1.0])
    tilt[:3, 3] = [5, -2, 1]
    scene = trimesh.Scene()
    scene.graph.update(frame_from=scene.graph.base_frame, frame_to="parent", matrix=tilt)
    for name, x, rgba in (("a", 2.0, [255, 0, 0, 255]), ("b", -2.0, [0, 0, 255, 255])):
        box = trimesh.creation.box(extents=[1, 1, 1])
        box.apply_translation([x, 0, 0])
        mat = trimesh.visual.material.PBRMaterial(name=name, baseColorFactor=rgba)
        box.visual = trimesh.visual.TextureVisuals(material=mat)
        scene.add_geometry(box, geom_name=name, node_name=name, parent_node_name="parent")
    src = tmp_path / "two.glb"
    scene.export(src)

    res = run_blender("parts_views", [src], tmp_path / "views", {}, timeout=300)
    assert (
        res["faces"] == 24 and res["radius"] <= 50 / 36 * 3**0.5 + 1e-3
    )  # the unit box's diagonal
    meta = json.loads((tmp_path / "views" / "meta.json").read_text())
    assert len(meta["transforms"]) == 12 and abs(meta["scaling_factor"] - res["scale"]) < 1e-4
    world_a = (tilt @ [2.0, 0, 0, 1])[:3]  # box a's centre in the file's frame
    world_b = (tilt @ [-2.0, 0, 0, 1])[:3]

    def near_a(centres):
        return np.linalg.norm(centres - world_a, axis=1) < np.linalg.norm(centres - world_b, axis=1)

    views_mesh = trimesh.load(tmp_path / "views" / "mesh.glb", force="mesh")  # as GeoSAM2 loads it
    labels = near_a(views_mesh.triangles_center).astype(int)
    assert labels.sum() == 12
    pj = {"labels": labels.tolist(), "parts": [{"id": 1, "name": "Box A", "faces": 12}]}
    (tmp_path / "parts.json").write_text(json.dumps(pj))
    split = run_blender(
        "parts_split",
        [tmp_path / "views" / "mesh.glb", tmp_path / "parts.json"],
        tmp_path / "s",
        {},
    )
    assert split["parts"] == {"Box A": 12, "Body": 12}
    out = trimesh.load(tmp_path / "s" / "mesh.glb")
    for node in out.graph.nodes_geometry:
        matrix, geom = out.graph[node]
        centres = trimesh.transform_points(out.geometry[geom].triangles_center, matrix)
        assert near_a(centres).all() == (node == "Box A") and near_a(centres).any() == (
            node == "Box A"
        )


def test_pull_and_close_snippets_pick_the_open_version(tmp_path):
    """Two versions of one asset open in a real Blender: PULL without a version takes the one opened last and
    lists both; with `version` it takes that one; CLOSE removes one collection and forgets it as the open one."""
    from server import blender_client

    script = tmp_path / "scene.py"
    script.write_text(
        "import bpy, json\n"
        "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
        "for n, v in ((1, '001_clean'), (3, '003_blender')):\n"
        "    coll = bpy.data.collections.new(f'EFFIGY_crate_v{n:03d}')\n"
        "    bpy.context.scene.collection.children.link(coll)\n"
        "    bpy.ops.mesh.primitive_cube_add()\n"
        "    o = bpy.context.object\n"
        "    o['effigy_asset_id'], o['effigy_version'] = 'crate', v\n"
        "    for c in o.users_collection: c.objects.unlink(o)\n"
        "    coll.objects.link(o)\n"
        "bpy.context.scene['effigy_open_crate'] = 'EFFIGY_crate_v003'\n"
        "out = {}\n"
        "def run(code, **args):\n"
        "    g = dict(args)\n"
        "    exec(code, g)\n"
        "    return g['__result__']\n"
        f"ASSET = {blender_client.ASSET_OBJECTS!r}\n"
        f"PULL = {blender_client.PULL!r}\n"
        f"CLOSE = {blender_client.CLOSE!r}\n"
        f"inbox = {str(tmp_path)!r}\n"
        "out['last'] = run(ASSET + PULL, asset_id='crate', inbox=inbox, version=None)\n"
        "out['named'] = run(ASSET + PULL, asset_id='crate', inbox=inbox, version='001_clean')\n"
        "out['closed'] = run(CLOSE, asset_id='crate', version='003_blender')\n"
        "out['after'] = run(ASSET + PULL, asset_id='crate', inbox=inbox, version=None)\n"
        "out['open_key'] = bpy.context.scene.get('effigy_open_crate')\n"
        "print('RESULT' + json.dumps(out))\n",
        encoding="utf-8",
    )
    done = subprocess.run(
        [BLENDER, "-b", "--factory-startup", "--python", str(script)],
        capture_output=True,
        text=True,
        check=True,
    )
    line = next(l for l in done.stdout.splitlines() if l.startswith("RESULT"))
    out = json.loads(line[6:])
    assert out["last"] == {
        "versions": ["003_blender"],
        "open": "EFFIGY_crate_v003",
        "open_all": ["EFFIGY_crate_v001", "EFFIGY_crate_v003"],
    }
    assert out["named"]["versions"] == ["001_clean"] and out["named"]["open"] == "EFFIGY_crate_v001"
    assert out["closed"] == ["EFFIGY_crate_v003"] and out["open_key"] is None
    assert out["after"] == {
        "versions": ["001_clean"],
        "open": None,
        "open_all": ["EFFIGY_crate_v001"],
    }


def _gltf_doc(path: Path) -> dict:
    b = path.read_bytes()
    return json.loads(b[20 : 20 + int.from_bytes(b[12:16], "little")])


@pytest.mark.skipif(
    not (ROOT_MODELS / "clips" / "mesh2motion").is_dir(),
    reason="no clip pack (download_models clips)",
)
def test_an_animation_file_comes_in_and_goes_back_onto_the_rig(tmp_path, monkeypatch):
    """Import an animation (owner 2026-10-12): the block figure's Walk (our Mixamo names, the mesh along) and the
    same as an FBX with Mixamo's "mixamorig:" prefix come in as motions (skeleton mixamo, one animation, no mesh,
    the Walk's length, the GLB's named after its take, Walk, made unique against the pack's own; the FBX's after the file, its
    take's name being generic); the FBX's goes back
    onto the rig through anim and moves it as the original Walk did, hands
    and feet within 1 cm (an FBX round trip may start the take a frame later: the best of three alignments)."""
    from steps import anim, import_motion

    monkeypatch.setenv("EFFIGY_BLENDER", str(BLENDER))
    skin, skeleton, _ = _walk_on_block_figure(tmp_path)
    walk = tmp_path / "anim" / "rigged_anim.glb"
    asset = tmp_path / "asset"
    rig = asset / "020_rig_skin"
    rig.mkdir(parents=True)
    shutil.copy(skin / "rigged.glb", rig / "rigged.glb")
    shutil.copy(skeleton, rig / "skeleton.json")
    # Mixamo's own export: prefixed bones, one take; renaming a bone renames the action's channels with it
    fbx = tmp_path / "walk_mixamo.fbx"
    code = (
        "import bpy\n"
        "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
        f"bpy.ops.import_scene.gltf(filepath={str(walk)!r})\n"
        "arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')\n"
        "ad = arm.animation_data\n"
        "for t in list(ad.nla_tracks): ad.nla_tracks.remove(t)\n"
        "act = bpy.data.actions[0]\n"
        "ad.action = act\n"
        "ad.action_slot = act.slots[0]\n"
        "for b in arm.data.bones: b.name = 'mixamorig:' + b.name\n"
        # the take's own frames, as an exporter writes them (the scene's default 1-250 padded it to 250)
        "bpy.context.scene.frame_start, bpy.context.scene.frame_end = (int(f) for f in act.frame_range)\n"
        f"bpy.ops.export_scene.fbx(filepath={str(fbx)!r}, bake_anim=True, bake_anim_use_all_actions=False,"
        " bake_anim_use_nla_strips=False, add_leaf_bones=False)\n"
    )
    subprocess.run(
        [BLENDER, "-b", "--factory-startup", "--python-expr", code], capture_output=True, check=True
    )
    assert fbx.is_file()

    def upload(n: str, path: Path, name: str) -> Path:
        up = asset / f"{n}_upload"
        (up / "source").mkdir(parents=True)
        shutil.copy(path, up / "source" / name)
        meta = {
            "version": up.name,
            "outputs": {"model": f"source/{name}"},
            "params": {"name": name},
        }
        (up / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return up

    for n, path, name, clip in (
        ("030", walk, "walk.glb", "Walk 040"),
        ("050", fbx, "Walk Mixamo.fbx", "Walk Mixamo"),
    ):
        out = asset / f"{int(n) + 10:03d}_import_motion"
        out.mkdir()
        res = import_motion.run(
            "run", str(upload(n, path, name)), str(out), {"for": "020_rig_skin"}
        )
        m = res["metrics"]
        assert m["skeleton"] == "mixamo" and m["name"] == clip and abs(m["seconds"] - 1.67) < 0.1, m
        doc = _gltf_doc(out / "clip.glb")
        assert [a["name"] for a in doc["animations"]] == [clip] and not doc.get("meshes"), name

    rep = anim.run("run", str(rig), str(asset / "070_anim"), {"clips": ["060_import_motion"]})
    assert rep["metrics"]["clips"] == ["Walk Mixamo"]
    # the same motion as the original: hands and feet in world space, a few frames apart
    check = tmp_path / "compare.py"
    check.write_text(
        "import bpy, json, sys\n"
        "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
        "arms = []\n"
        "for path in sys.argv[sys.argv.index('--') + 1:]:\n"
        "    before = set(bpy.data.objects)\n"
        "    bpy.ops.import_scene.gltf(filepath=path)\n"
        "    arm = next(o for o in bpy.data.objects if o not in before and o.type == 'ARMATURE')\n"
        "    ad = arm.animation_data\n"
        "    act = ad.nla_tracks[0].strips[0].action\n"
        "    for t in list(ad.nla_tracks): ad.nla_tracks.remove(t)\n"
        "    ad.action = act\n"
        "    ad.action_slot = act.slots[0]\n"
        "    arms.append(arm)\n"
        "def at(arm, f, b):\n"
        "    bpy.context.scene.frame_set(f)\n"
        "    return (arm.matrix_world @ arm.pose.bones[b].head).copy()\n"
        "best = min(\n"
        "    max((at(arms[0], f, b) - at(arms[1], f + k, b)).length\n"
        "        for f in (2, 8, 15, 22, 30) for b in ('LeftHand', 'RightHand', 'LeftFoot', 'RightFoot'))\n"
        "    for k in (-1, 0, 1))\n"
        "print('WORST', best)\n",
        encoding="utf-8",
    )
    done = subprocess.run(
        [
            BLENDER,
            "-b",
            "--factory-startup",
            "--python",
            str(check),
            "--",
            str(walk),
            str(asset / "070_anim" / "rigged_anim.glb"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    worst = float(
        next(line for line in done.stdout.splitlines() if line.startswith("WORST")).split()[1]
    )
    assert worst < 0.01, f"hands and feet {worst:.3f} m off the original Walk"


def test_quadriflow_exports_smooth_normals(sphere, tmp_path):
    """The quad remesh's GLB carries smooth normals (not one per quad: flat shading)."""
    import numpy as np

    from steps import _anim

    run_blender("quadriflow", [sphere], tmp_path / "q", {"target_faces": 400}, timeout=300)
    doc, blob = _anim.read_glb(tmp_path / "q" / "mesh.glb")
    prim = doc["meshes"][0]["primitives"][0]
    acc = doc["accessors"][prim["attributes"]["NORMAL"]]
    view = doc["bufferViews"][acc["bufferView"]]
    n = np.frombuffer(
        blob, "<f4", acc["count"] * 3, view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    )
    pos = doc["accessors"][prim["attributes"]["POSITION"]]
    assert (
        acc["count"] == pos["count"]
        and len(np.unique(n.reshape(-1, 3).round(4), axis=0)) > 0.5 * acc["count"]
    )
