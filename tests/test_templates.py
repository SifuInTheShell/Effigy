"""M36 creature skeletons: the templates (Mesh2Motion's fox, horse, bird), the fit onto an animal, the skin and
the template's own clips. The Blender parts skip without a Blender; the clips without the clip pack."""

import json
import shutil

import numpy as np
import pytest
import trimesh

from server.blender_install import blender_exe
from steps import _templates, anim, rig_fit, rig_skin
from steps._print import _faces

BLENDER = blender_exe()
CLIPS = anim.CLIPS / anim.BUILTIN / "creatures" / "fox-animations.glb"


def animal(turn: float = 0, tail_up: bool = False, ears: float | None = None) -> trimesh.Trimesh:
    """A four-legged animal of boxes, 1 m high, facing +Z (head raised at the front, a tail at the back, or
    held up higher than the head as the cat does; with `ears`, two ear posts on a head turned that many
    degrees), turned `turn` degrees about Y: one closed surface (the voxels' outer faces)."""
    pitch = 0.02
    tail = (
        (-0.03, 0.03, 0.7, 1.15, -0.62, -0.56) if tail_up else (-0.04, 0.04, 0.6, 0.68, -1.0, -0.55)
    )
    boxes = [  # (x0, x1, y0, y1, z0, z1)
        (-0.15, 0.15, 0.45, 0.75, -0.6, 0.6),  # body
        (-0.12, 0.12, 0.6, 1.0, 0.5, 0.85),  # neck and head
        tail,
        *(
            (x - 0.05, x + 0.05, 0.0, 0.5, z - 0.05, z + 0.05)
            for x in (-0.1, 0.1)
            for z in (-0.45, 0.45)
        ),
        *(
            (x - 0.02, x + 0.02, 1.0, 1.15, z - 0.02, z + 0.02)
            for x, z in ([] if ears is None else _ear_posts(ears))
        ),
    ]
    lo = np.array([-0.3, 0.0, -1.1])
    grid = np.zeros(np.ceil((np.array([0.3, 1.2, 1.0]) - lo) / pitch).astype(int), bool)
    for x0, x1, y0, y1, z0, z1 in boxes:
        a = ((np.array([x0, y0, z0]) - lo) / pitch).round().astype(int)
        b = ((np.array([x1, y1, z1]) - lo) / pitch).round().astype(int)
        grid[a[0] : b[0], a[1] : b[1], a[2] : b[2]] = True
    m = _faces(grid, lo, pitch)
    m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(turn), [0, 1, 0]))
    return m


def mouse() -> trimesh.Trimesh:
    """A mouse of boxes, 0.5 m long, facing +Z: a low body on short legs, big ears, a long thin tail lying on
    the floor behind it."""
    pitch, lo = 0.008, np.array([-0.1, 0.0, -0.35])
    boxes = [  # (x0, x1, y0, y1, z0, z1)
        (-0.05, 0.05, 0.03, 0.11, -0.12, 0.08),  # body
        (-0.04, 0.04, 0.05, 0.12, 0.08, 0.17),  # head
        (-0.06, -0.025, 0.11, 0.16, 0.11, 0.125),  # ears
        (0.025, 0.06, 0.11, 0.16, 0.11, 0.125),
        (-0.008, 0.008, 0.0, 0.016, -0.32, -0.11),  # tail
        *(
            (x - 0.012, x + 0.012, 0.0, 0.05, z - 0.012, z + 0.012)
            for x in (-0.03, 0.03)
            for z in (-0.08, 0.05)
        ),
    ]
    grid = np.zeros(np.ceil((np.array([0.1, 0.2, 0.2]) - lo) / pitch).astype(int), bool)
    for x0, x1, y0, y1, z0, z1 in boxes:
        a = ((np.array([x0, y0, z0]) - lo) / pitch).round().astype(int)
        b = ((np.array([x1, y1, z1]) - lo) / pitch).round().astype(int)
        grid[a[0] : b[0], a[1] : b[1], a[2] : b[2]] = True
    return _faces(grid, lo, pitch)


def _ear_posts(turn: float) -> list:
    """(x, z) of two ear posts 16 cm apart on the head's top, their line turned `turn` degrees about Y."""
    t = np.radians(turn)
    return [(k * 0.08 * np.cos(t), 0.675 - k * 0.08 * np.sin(t)) for k in (1, -1)]


@pytest.mark.parametrize("name", list(_templates.TEMPLATES))
def test_a_template_is_one_skeleton_with_paired_sides(name):
    bones = _templates.bones(name)
    names = [b["name"] for b in bones]
    assert len(names) == len(set(names)) and sum(b["parent"] is None for b in bones) == 1
    assert all(b["parent"] in names for b in bones if b["parent"])
    sided = [n for n in names if _templates.twin(name, n)]
    assert all(_templates.twin(name, n) in names for n in sided)  # every side has its other side
    assert (sided and _templates.limbs(name)) or name == "snake"  # the snake: no sides, no limbs
    assert set(_templates.torso(name)) <= set(names)
    assert not set(_templates.torso(name)) & set(_templates.limbs(name))


@pytest.mark.parametrize("turn", [0, 90, 180, 270])
def test_the_fit_finds_where_the_animal_faces(turn):
    m = animal(turn)
    assert _templates.facing("fox", m.vertices) == turn


@pytest.mark.parametrize("turn", [0, 90])
def test_a_tail_held_up_higher_than_the_head_is_not_taken_for_the_head(turn):
    """The cat holds its tail up above its ears: the head is the end with more of the body above the back."""
    assert _templates.facing("fox", animal(turn, tail_up=True).vertices) == turn


@pytest.mark.parametrize("turn", [30, 135, 323.5])
def test_the_fit_turns_with_an_animal_standing_at_an_angle(turn):
    """A three-quarter picture makes an animal that stands at an angle (the cat: 323.5Ã‚Â°): the fit's yaw is that
    turn, the head in front along it, the spine in the body."""
    m = animal(turn)
    bones, yaw = _templates.fit("fox", m.vertices, f=m.faces)
    assert abs((yaw - turn + 180) % 360 - 180) < 3, yaw
    at = {n: np.asarray(b["position"]) for n, b in bones.items()}
    ahead = _templates._yaw(turn) @ [0, 0, 1]
    assert (at["Head"] - at["Hips"]) @ ahead > 0.6
    for n in ("Hips", "Spine_1", "Spine_2", "Spine_3"):
        assert 0.45 < at[n][1] < 0.75, (n, at[n])
    assert (
        _templates.fit("fox", m.vertices, yaw + 90, m.faces)[1] == round(yaw + 90, 1) % 360
    )  # Turn a quarter


def test_the_limb_tracer_climbs_a_tail_held_up():
    """The cat's tail stands up out of its rump, where the template's lies back: the tracer's points climb it
    from the chain's root to its tip; a tail as thick as the box animal's lying one is left to the template."""
    traced = {}
    for up in (True, False):
        m = animal(0, tail_up=up)
        surf = _templates.surface(m.vertices, m.faces)
        vox = _templates._voxels(surf)
        lying = np.array(
            [[0, 0.6, -0.55 - 0.1 * i] for i in range(5)]
        )  # the template's tail, lying back
        traced[up] = _templates._limb(
            vox, _templates._thin(vox), lying, lying[0], np.linspace(0, 1, 5), 2.0, 0.1, 0.1, set()
        )
    along = traced[True]
    assert traced[False] is None and len(along) == 5
    assert np.all(np.diff(along[:, 1]) > 0) and along[0, 1] < 0.85 and along[-1, 1] > 1.05
    assert (
        np.abs(along[:, 0]).max() < 0.03 and np.abs(along[:, 2] + 0.59).max() < 0.05
    )  # on the post


def test_the_face_turns_with_the_ears():
    """A head turned 30Ã‚Â° off the body (the cat looks at the camera): the face turns with the ear posts' line,
    the ear chains end on the post tops; a head without ears stays straight."""
    heading = lambda d: np.degrees(np.arctan2(d[0], d[2]))
    face = {}
    for ears in (None, 30):
        m = animal(ears=ears)
        bones, _ = _templates.fit("fox", m.vertices, f=m.faces)
        at = {n: np.asarray(b["position"]) for n, b in bones.items()}
        face[ears] = heading(at["Nose"] - at["Head"])
    assert abs(face[30] - face[None] - 30) < 8, face
    tops = [np.array([x, 1.15, z]) for x, z in _ear_posts(30)]
    near = [
        [np.linalg.norm(np.asarray(bones[n]["tail"]) - t) for t in tops]
        for n in ("Ear_Tip_L", "Ear_Tip_R")
    ]
    assert max(min(d) for d in near) < 0.05 and np.argmin(near[0]) != np.argmin(near[1]), near


def test_a_tail_lying_on_the_floor_is_neither_feet_nor_lost():
    """A mouse's tail lies on the floor: the feet stay its paws (its hips went behind its rump when the tail
    counted as back feet) and the tail chain lies along the tail."""
    m = mouse()
    bones, yaw = _templates.fit("fox", m.vertices, f=m.faces)
    at = {n: np.asarray(b["position"]) for n, b in bones.items()}
    assert (
        yaw == 0
        and abs(at["Back_Leg_Tip_L"][2] + 0.08) < 0.02
        and abs(at["Front_Leg_Tip_L"][2] - 0.05) < 0.02
    )
    assert -0.12 < at["Hips"][2] < -0.06 and all(
        at[n][1] < 0.02 for n in ("Tail_Mid", "Tail_End", "Tail_Tip")
    )


def test_the_fit_puts_the_spine_in_the_body_and_the_tail_in_the_tail():
    m = animal(90)
    bones, yaw = _templates.fit("fox", m.vertices, f=m.faces)
    assert yaw == 90
    at = {n: np.asarray(b["position"]) for n, b in bones.items()}
    for n in ("Hips", "Spine_1", "Spine_2", "Spine_3"):  # the body: y 0.45Ã¢â‚¬â€œ0.75
        assert 0.45 < at[n][1] < 0.75, (n, at[n])
    assert (
        0.55 < at["Tail_Mid"][1] < 0.72 and at["Tail_Tip"][0] < -0.5
    )  # in the tail, behind (the turn: -x)
    assert at["Head"][0] > 0.4 and at["Head"][1] > at["Hips"][1]  # in front, higher
    assert bones["Hips"]["parent"] == "root" and "tail" in bones["Hips"]


def version(tmp_path, mesh: trimesh.Trimesh):
    src = tmp_path / "010_upload"
    src.mkdir()
    mesh.export(src / "mesh.glb")
    meta = {"version": "010_upload", "asset_id": "dog", "outputs": {"mesh": "mesh.glb"}}
    (src / "meta.json").write_text(json.dumps(meta))
    return src


def test_rig_fit_writes_a_creature_skeleton_and_keeps_it_through_edits(tmp_path):
    src = version(tmp_path, animal(90))
    fit = tmp_path / "020_rig_fit"
    fit.mkdir()
    res = rig_fit.run("run", str(src), str(fit), {"template": "fox"})
    skel = json.loads((fit / "skeleton.json").read_text())
    assert skel["template"] == "fox" and skel["yaw"] == 90 and skel["sides"] == ["_L", "_R"]
    assert "markers" not in res["outputs"] and res["metrics"]["template"] == "fox"
    (fit / "meta.json").write_text(
        json.dumps({"version": "020_rig_fit", "outputs": res["outputs"]})
    )
    edit = tmp_path / "030_rig_fit"
    edit.mkdir()
    ear = next(j for j in skel["joints"] if j["name"] == "Ear_Tip_L")
    moved = (np.asarray(ear["position"]) + [0, 0.05, 0]).tolist()
    rig_fit.run("run", str(fit), str(edit), {"joints": {"Ear_Tip_L": moved}})
    after = {j["name"]: j for j in json.loads((edit / "skeleton.json").read_text())["joints"]}
    assert json.loads((edit / "skeleton.json").read_text())["template"] == "fox"
    assert np.allclose(after["Ear_Tip_L"]["position"], moved)
    assert np.allclose(
        np.asarray(after["Ear_Tip_L"]["tail"]) - ear["tail"], [0, 0.05, 0], atol=1e-5
    )
    with pytest.raises(Exception, match="template"):
        rig_fit.run("run", str(src), str(tmp_path / "x"), {"template": "unicorn"})
    with pytest.raises(Exception, match="yaw"):
        rig_fit.run("run", str(src), str(tmp_path / "x"), {"template": "fox", "yaw": "left"})


def test_creatures_get_their_own_clips():
    if not CLIPS.is_file():
        pytest.skip("no creature clips (python -m tools.download_models clips)")
    names = [c["name"] for c in anim.catalogue("fox")]
    assert {"Idle", "Walk", "Run", "Sit"} <= set(names) and not any(
        "rest" in n.lower() for n in names
    )
    assert not set(names) & {c["name"] for c in anim.catalogue()} - {
        "Idle",
        "Walk",
        "Run",
        "Death",
        "Jump",
    }


@pytest.mark.skipif(not BLENDER.is_file(), reason="no Blender (EFFIGY_BLENDER / blender_install)")
def test_a_creature_skins_and_walks(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFIGY_BLENDER", str(BLENDER))
    src = version(tmp_path, animal(90))
    fit, skin = tmp_path / "020_rig_fit", tmp_path / "030_rig_skin"
    fit.mkdir()
    skin.mkdir()
    res = rig_fit.run("run", str(src), str(fit), {"template": "fox"})
    (fit / "meta.json").write_text(
        json.dumps({"version": "020_rig_fit", "outputs": res["outputs"]})
    )
    with pytest.raises(Exception, match="SkinTokens"):
        rig_skin.run("run", str(fit), str(tmp_path / "x"), {"engine": "skintokens"})
    res = rig_skin.run("run", str(fit), str(skin), {})
    qa = json.loads((skin / "qa.json").read_text())
    assert qa["picked"] == "blender" and res["metrics"]["template"] == "fox"
    assert all(it["status"] != "fail" for it in qa["items"].values()), qa["items"]
    doc = trimesh.load(skin / "rigged.glb", force="scene")
    assert doc.geometry  # skinned and loadable
    if not CLIPS.is_file():
        pytest.skip("no creature clips")
    out = tmp_path / "040_anim"
    out.mkdir()
    res = anim.run("run", str(skin), str(out), {"clips": ["Walk", "Sit"]})
    assert res["metrics"]["clips"] == ["Walk", "Sit"] and res["licenses"] == [
        "Mesh2Motion/mesh2motion-app"
    ]
    with pytest.raises(Exception, match="people"):
        anim.run("run", str(skin), str(tmp_path / "y"), {"clips": ["310_motion"]})
    shutil.rmtree(out)


def kaiju(turn: float = 0) -> trimesh.Trimesh:
    """A kaiju of boxes, 2 m high, facing +Z: upright on two legs, a heavy chest and head, a long tail that
    slopes down to the floor behind (one group of feet: both under the hips)."""
    pitch, lo = 0.02, np.array([-0.4, 0.0, -1.5])
    boxes = [  # (x0, x1, y0, y1, z0, z1)
        (-0.3, 0.3, 0.7, 1.4, -0.3, 0.5),  # body
        (-0.2, 0.2, 1.2, 2.0, 0.3, 0.7),  # chest, neck and head
        (-0.1, 0.1, 0.5, 0.9, -1.5, -0.3),  # tail
        *((x - 0.1, x + 0.1, 0.0, 0.7, -0.2, 0.2) for x in (-0.2, 0.2)),  # legs
    ]
    grid = np.zeros(np.ceil((np.array([0.4, 2.1, 0.8]) - lo) / pitch).astype(int), bool)
    for x0, x1, y0, y1, z0, z1 in boxes:
        a = ((np.array([x0, y0, z0]) - lo) / pitch).round().astype(int)
        b = ((np.array([x1, y1, z1]) - lo) / pitch).round().astype(int)
        grid[a[0] : b[0], a[1] : b[1], a[2] : b[2]] = True
    m = _faces(grid, lo, pitch)
    m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(turn), [0, 1, 0]))
    return m


def shark(turn: float = 0) -> trimesh.Trimesh:
    """A shark of boxes, 2.6 m long, facing +Z, its belly 0.3 m off the floor of its box: a body, a dorsal fin,
    a tail fin standing up at the back, two pectoral fins out sideways and down, two small pelvic fins."""
    pitch, lo = 0.02, np.array([-1.0, 0.0, -1.7])
    boxes = [  # (x0, x1, y0, y1, z0, z1)
        (-0.25, 0.25, 0.3, 0.8, -1.2, 1.0),  # body
        (-0.03, 0.03, 0.8, 1.2, 0.1, 0.5),  # dorsal fin
        (-0.03, 0.03, 0.15, 1.0, -1.6, -1.2),  # tail fin
        *(
            (x0, x1, 0.27, 0.31, 0.2, 0.45) for x0, x1 in ((0.25, 0.85), (-0.85, -0.25))
        ),  # pectoral fins
        *(
            (x0, x1, 0.3, 0.34, -0.7, -0.55) for x0, x1 in ((0.25, 0.5), (-0.5, -0.25))
        ),  # pelvic fins
    ]
    grid = np.zeros(np.ceil((np.array([1.0, 1.3, 1.1]) - lo) / pitch).astype(int), bool)
    for x0, x1, y0, y1, z0, z1 in boxes:
        a = ((np.array([x0, y0, z0]) - lo) / pitch).round().astype(int)
        b = ((np.array([x1, y1, z1]) - lo) / pitch).round().astype(int)
        grid[a[0] : b[0], a[1] : b[1], a[2] : b[2]] = True
    m = _faces(grid, lo, pitch)
    m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(turn), [0, 1, 0]))
    return m


def dragon(turn: float = 0, wings: bool = True) -> trimesh.Trimesh:
    """A dragon of boxes, 3.2 m from wing tip to wing tip, facing +Z: a body on four legs, a raised neck and head,
    a tail, two thin wings spread sideways at the shoulders (without `wings`: folded away, a long body)."""
    pitch, lo = 0.02, np.array([-1.7, 0.0, -1.7])
    boxes = [  # (x0, x1, y0, y1, z0, z1)
        (-0.25, 0.25, 0.4, 0.8, -0.6, 0.6),  # body
        (-0.12, 0.12, 0.6, 1.1, 0.5, 1.0),  # neck and head
        (-0.06, 0.06, 0.55, 0.67, -1.6, -0.6),  # tail
        *(
            (x - 0.07, x + 0.07, 0.0, 0.45, z - 0.07, z + 0.07)
            for x in (-0.15, 0.15)
            for z in (-0.45, 0.45)
        ),
        *(
            ((0.25, 1.6, 0.74, 0.78, -0.2, 0.5), (-1.6, -0.25, 0.74, 0.78, -0.2, 0.5))
            if wings
            else ()
        ),
    ]
    grid = np.zeros(np.ceil((np.array([1.7, 1.2, 1.1]) - lo) / pitch).astype(int), bool)
    for x0, x1, y0, y1, z0, z1 in boxes:
        a = ((np.array([x0, y0, z0]) - lo) / pitch).round().astype(int)
        b = ((np.array([x1, y1, z1]) - lo) / pitch).round().astype(int)
        grid[a[0] : b[0], a[1] : b[1], a[2] : b[2]] = True
    m = _faces(grid, lo, pitch)
    m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(turn), [0, 1, 0]))
    return m


@pytest.mark.parametrize("turn", [0, 90, 180, 270])
def test_a_dragon_faces_right_with_its_wings_spread_or_folded(turn):
    """The dragon's template is wider than long: the axis comes from the body's thick core, not the box (folded
    wings make the box long, the template's is wide)."""
    for wings in (True, False):
        assert _templates.facing("dragon", dragon(turn, wings).vertices) == turn, wings


def test_a_dragons_wing_runs_out_along_its_wing_and_the_membrane_fingers_follow():
    """The wing chain runs on through the wing's bones (the membrane fingers hang off them) and out along the
    mesh's own wing; the fingers move with their wing bone, on the wing's side."""
    m = dragon(0)
    bones, yaw = _templates.fit("dragon", m.vertices, f=m.faces)
    at = {n: np.asarray(b["position"]) for n, b in bones.items()}
    tip = {s: np.asarray(bones[f"wing_tip_{s}"]["tail"]) for s in "lr"}
    assert yaw == 0 and tip["l"][0] > 1.2 and tip["r"][0] < -1.2, tip
    for s, sign in (("l", 1), ("r", -1)):
        for n in ("wing_3", "wing_5", "wing_feather_3_1", "wing_feather_5_1"):
            assert sign * at[f"{n}_{s}"][0] > 0.4 and 0.6 < at[f"{n}_{s}"][1] < 0.9, (
                n,
                s,
                at[f"{n}_{s}"],
            )


@pytest.mark.parametrize("turn", [0, 90])
def test_a_shark_floats_and_its_fins_follow_the_meshs_own(turn):
    """The shark's rig hangs below its root (no feet to map); each pectoral fin chain runs out along its own
    side's fin, the spine stays in the body."""
    m = shark(turn)
    bones, yaw = _templates.fit("shark", m.vertices, f=m.faces)
    assert yaw == turn
    back = _templates._yaw(-turn)  # the shark facing +Z again
    at = {n: np.asarray(b["position"]) @ back.T for n, b in bones.items()}
    tip = {s: np.asarray(bones[f"front_fin_tip_{s}"]["tail"]) @ back.T for s in "lr"}
    assert tip["l"][0] > 0.6 and tip["r"][0] < -0.6, tip  # out to each fin's end, its own side
    assert all(0.15 < tip[s][2] < 0.5 and tip[s][1] < 0.45 for s in "lr"), tip
    for n in ("pelvis", "tail_1", "tail_3", "neck", "head"):
        assert abs(at[n][0]) < 0.25 and 0.3 < at[n][1] < 0.8, (n, at[n])


@pytest.mark.parametrize("turn", [0, 90, 180, 270])
def test_a_kaiju_faces_right_and_its_one_group_of_feet_is_one_point(turn):
    m = kaiju(turn)
    assert _templates.facing("kaiju", m.vertices) == turn
    axis = 2 if turn % 180 == 0 else 0
    assert len(_templates._feet(_templates.surface(m.vertices, m.faces), axis)) == 1
    bones, yaw = _templates.fit("kaiju", m.vertices, f=m.faces)
    assert yaw == turn and set(bones) == {b["name"] for b in _templates.bones("kaiju")}


def test_two_groups_of_feet_stay_two_and_the_map_needs_the_same_count():
    m = animal(0)
    assert len(_templates._feet(_templates.surface(m.vertices, m.faces), 2)) == 2


def spider(turn: float = 0, tilt: float = 0) -> trimesh.Trimesh:
    """A spider, 1.4 m from foot to foot, facing +Z: a low head-and-chest at the front and a big abdomen standing
    higher behind it (round: a box's edges are thin parts of their own), four legs of boxes a side out from the
    chest and down to the floor, the front pair reaching forward, the back pair back. `tilt`: degrees about X, as
    a spider generated from a picture taken from above lies."""
    pitch, lo = 0.02, np.array([-0.8, 0.0, -0.9])
    legs = [  # one side's, x > 0: a leg's bars at knee height (x0, x1, z0, z1), its foot's post (x, z)
        ([(0.1, 0.4, 0.27, 0.33), (0.34, 0.4, 0.27, 0.66)], (0.37, 0.63)),
        ([(0.1, 0.7, 0.12, 0.18)], (0.67, 0.15)),
        ([(0.1, 0.7, -0.03, 0.03)], (0.67, 0.0)),
        ([(0.1, 0.55, -0.18, -0.12), (0.49, 0.55, -0.6, -0.12)], (0.52, -0.57)),
    ]
    boxes = []  # (x0, x1, y0, y1, z0, z1)
    for s in (1, -1):
        for bars, (x, z) in legs:
            boxes += [(*sorted((s * x0, s * x1)), 0.2, 0.26, z0, z1) for x0, x1, z0, z1 in bars]
            boxes.append((*sorted((s * (x - 0.03), s * (x + 0.03))), 0.0, 0.26, z - 0.03, z + 0.03))
    grid = np.zeros(np.ceil((np.array([0.8, 0.6, 0.8]) - lo) / pitch).astype(int), bool)
    at = np.stack(np.meshgrid(*(np.arange(n) for n in grid.shape), indexing="ij"), -1) * pitch + lo
    for centre, half in (
        ((0, 0.19, 0.07), (0.15, 0.09, 0.28)),
        ((0, 0.31, -0.55), (0.18, 0.19, 0.35)),
    ):
        grid |= (((at - centre) / half) ** 2).sum(-1) <= 1  # head and chest; abdomen
    for x0, x1, y0, y1, z0, z1 in boxes:
        a = ((np.array([x0, y0, z0]) - lo) / pitch).round().astype(int)
        b = ((np.array([x1, y1, z1]) - lo) / pitch).round().astype(int)
        grid[a[0] : b[0], a[1] : b[1], a[2] : b[2]] = True
    m = _faces(grid, lo, pitch)
    m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(tilt), [1, 0, 0]))
    m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(turn), [0, 1, 0]))
    return m


@pytest.mark.parametrize("turn", [0, 90, 180, 270])
def test_a_spider_faces_its_low_end(turn):
    """HEAD_LOW: the spider's abdomen stands higher than its head; its head is the other end."""
    m = spider(turn)
    assert _templates.facing("spider", _templates.surface(m.vertices, m.faces)) == turn


@pytest.mark.parametrize("tilt", [0, 30])
def test_a_spiders_eight_legs_go_into_its_eight_legs(tilt):
    """The leg anchor is a hub: a chain per leg; the legs on a side, one thin part meeting at the chest, split by
    their feet; each leg runs out along its own, the front pair `leg_a`, its tip on the floor. A spider lying
    tilted is stood up first."""
    m = spider(0, tilt)
    bones, yaw = _templates.fit("spider", m.vertices, f=m.faces)
    up = np.linalg.inv(trimesh.transformations.rotation_matrix(np.radians(tilt), [1, 0, 0])[:3, :3])
    tip = {n: np.asarray(b["tail"]) @ up.T for n, b in bones.items() if "_tip_" in n}
    assert yaw == 0
    for s, sign in (("l", 1), ("r", -1)):
        for leg, (x, z) in zip("abcd", ((0.37, 0.63), (0.67, 0.15), (0.67, 0.0), (0.52, -0.57))):
            t = tip[f"leg_{leg}_tip_{s}"]
            assert np.hypot(sign * t[0] - x, t[2] - z) < 0.08 and t[1] < 0.1, (leg, s, t)


def test_rig_fit_stands_a_tilted_spider_up(tmp_path):
    """The mesh is written level (not copied), the skeleton in that frame, the turn recorded; a level one: identity."""
    heights = {}
    for tilt in (30, 0):
        (tmp_path / str(tilt)).mkdir()
        fit = tmp_path / str(tilt) / "020_rig_fit"
        fit.mkdir()
        res = rig_fit.run(
            "run",
            str(version(tmp_path / str(tilt), spider(0, tilt))),
            str(fit),
            {"template": "spider"},
        )
        skel = json.loads((fit / "skeleton.json").read_text())
        heights[tilt] = np.ptp(trimesh.load(fit / "mesh.glb", force="mesh").vertices[:, 1])
        if tilt:
            assert abs(res["metrics"]["leveled_deg"] - tilt) < 3
            assert not np.allclose(skel["level"], np.eye(3))
            assert abs(min(j["position"][1] for j in skel["joints"])) < 0.1  # the feet on the floor
            leveled = trimesh.load(fit / "mesh.glb", force="mesh").vertices
            assert abs(leveled[:, 1].min()) < 1e-4  # the stood-up mesh back on the floor
        else:
            assert np.allclose(skel["level"], np.eye(3)) and res["metrics"]["leveled_deg"] == 0
    assert abs(heights[30] - heights[0]) < 0.03


def test_glb_rotate_turns_the_vertices_and_nothing_else(tmp_path):
    from steps import _glb

    animal(0).export(tmp_path / "a.glb")
    R = trimesh.transformations.rotation_matrix(0.5, [1, 0, 1])[:3, :3]
    assert _glb.rotate(tmp_path / "a.glb", tmp_path / "b.glb", R) is None
    (d0, b0), (d1, b1) = _glb.read(tmp_path / "a.glb"), _glb.read(tmp_path / "b.glb")
    assert len(b0) == len(b1)
    p = d1["meshes"][0]["primitives"][0]
    pos = _glb.accessor(d1, b1, p["attributes"]["POSITION"])
    assert np.allclose(pos, _glb.accessor(d0, b0, p["attributes"]["POSITION"]) @ R, atol=1e-5)
    acc = d1["accessors"][p["attributes"]["POSITION"]]
    assert np.allclose(acc["min"], pos.min(0)) and np.allclose(acc["max"], pos.max(0))
    assert np.array_equal(_glb.accessor(d0, b0, p["indices"]), _glb.accessor(d1, b1, p["indices"]))
    assert _glb.rotate(tmp_path / "b.glb", tmp_path / "c.glb", R.T, shift=(0, 2, 0)) is None
    d2, b2 = _glb.read(tmp_path / "c.glb")
    assert np.allclose(
        _glb.accessor(d2, b2, p["attributes"]["POSITION"]),
        _glb.accessor(d0, b0, p["attributes"]["POSITION"]) + [0, 2, 0],
        atol=1e-5,
    )
    assert _glb.rotate(tmp_path / "c.glb", tmp_path / "c.glb", np.eye(3), shift=(0, -2, 0)) is None
    d2, b2 = _glb.read(tmp_path / "c.glb")
    assert np.allclose(
        _glb.accessor(d2, b2, p["attributes"]["POSITION"]),
        _glb.accessor(d0, b0, p["attributes"]["POSITION"]),
        atol=1e-5,
    )


def _edited_glb(path, edit, two=False):
    from steps import _glb

    (trimesh.Scene([animal(0), animal(0)]) if two else animal(0)).export(path)
    doc, blob = _glb.read(path)
    edit(doc)
    _glb.write(path, doc, blob)


def _parent(d):
    d["nodes"].append({"children": [0], "translation": [1, 0, 0]})
    d["scenes"][0]["nodes"] = [len(d["nodes"]) - 1]


def _pos(d):
    return d["accessors"][d["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]


@pytest.mark.parametrize(
    ("edit", "two"),
    [
        (lambda d: d.update(skins=[{"joints": [0]}]), False),
        (lambda d: d["nodes"][0].update(translation=[1, 0, 0]), False),
        (lambda d: d["meshes"][0]["primitives"][0].update(targets=[{"POSITION": 0}]), False),
        (lambda d: None, True),
        (lambda d: _pos(d).update(componentType=5123), False),
        (_parent, False),
    ],
    ids=["skin", "node transform", "morph targets", "several meshes", "non-float", "parent"],
)
def test_glb_rotate_refuses_what_is_not_ours_to_turn(tmp_path, edit, two):
    from steps import _glb

    _edited_glb(tmp_path / "a.glb", edit, two)
    assert _glb.rotate(tmp_path / "a.glb", tmp_path / "b.glb", np.eye(3))
    assert not (tmp_path / "b.glb").exists()


def test_rig_fit_says_why_it_did_not_stand_a_spider_up(tmp_path):
    v = version(tmp_path, spider(0, 30))
    from steps import _glb

    doc, blob = _glb.read(v / "mesh.glb")
    _parent(doc)
    _glb.write(v / "mesh.glb", doc, blob)
    fit = tmp_path / "020_rig_fit"
    fit.mkdir()
    res = rig_fit.run("run", str(v), str(fit), {"template": "spider"})
    assert "another node" in res["metrics"]["level_skipped"]
    assert "transform" not in res["metrics"]


def test_a_leveled_spider_records_its_transform_and_keeps_its_level_through_edits_and_turns(
    tmp_path,
):
    v = version(tmp_path, spider(0, 30))
    fit = tmp_path / "020_rig_fit"
    fit.mkdir()
    res = rig_fit.run("run", str(v), str(fit), {"template": "spider"})
    skel = json.loads((fit / "skeleton.json").read_text())
    before = trimesh.load(v / "mesh.glb", force="mesh").vertices
    after = trimesh.load(fit / "mesh.glb", force="mesh").vertices
    t = np.asarray(res["metrics"]["transform"])
    assert np.allclose(t[:3, :3], np.asarray(skel["level"]).T, atol=1e-5)
    assert np.allclose(before @ t[:3, :3].T + t[:3, 3], after, atol=1e-4)
    (fit / "meta.json").write_text(
        json.dumps({"version": "020_rig_fit", "asset_id": "dog", "outputs": {"mesh": "mesh.glb"}})
    )
    moved = {"joints": {skel["joints"][1]["name"]: [0.0, 0.2, 0.0]}}
    for n, params in (("030", moved), ("040", {"yaw": 90})):
        d = tmp_path / f"{n}_rig_fit"
        d.mkdir()
        res2 = rig_fit.run("run", str(fit), str(d), {"template": "spider"} | params)
        assert np.allclose(json.loads((d / "skeleton.json").read_text())["level"], skel["level"])
        assert "transform" not in res2["metrics"]
        assert abs(trimesh.load(d / "mesh.glb", force="mesh").vertices[:, 1].min()) < 1e-4


def test_the_bench_measures_a_fit():
    from tools.creature_bench import measure

    m = animal(90)
    yaw, _, inside, joints, score, right = measure("fox", m.vertices, m.faces, 90)
    assert right and yaw == 90 and 0 < inside <= joints and score > 0


@pytest.mark.parametrize(
    "name, clips", [("kaiju", 9), ("shark", 6), ("dragon", 4), ("spider", 9), ("snake", 7)]
)
def test_the_new_creatures_have_their_clips(name, clips):
    if not (anim.CLIPS / anim.BUILTIN / "creatures" / f"{name}-animations.glb").is_file():
        pytest.skip("no creature clips (python -m tools.download_models clips)")
    names = [c["name"] for c in anim.catalogue(name)]
    assert len(names) == clips and not any("rest" in n.lower() for n in names)


@pytest.mark.skipif(not BLENDER.is_file(), reason="no Blender (EFFIGY_BLENDER / blender_install)")
def test_a_kaiju_bakes_with_the_travel_scaled_from_its_lowest_joint(tmp_path, monkeypatch):
    if not (anim.CLIPS / anim.BUILTIN / "creatures" / "kaiju-animations.glb").is_file():
        pytest.skip("no creature clips")
    monkeypatch.setenv("EFFIGY_BLENDER", str(BLENDER))
    src = version(tmp_path, kaiju(0))
    fit, skin, out = (tmp_path / n for n in ("020_rig_fit", "030_rig_skin", "040_anim"))
    for d in (fit, skin, out):
        d.mkdir()
    res = rig_fit.run("run", str(src), str(fit), {"template": "kaiju"})
    (fit / "meta.json").write_text(json.dumps({"version": "x", "outputs": res["outputs"]}))
    rig_skin.run("run", str(fit), str(skin), {})
    clips = [c["name"] for c in anim.catalogue("kaiju")]
    res = anim.run("run", str(skin), str(out), {"clips": clips})
    assert res["metrics"]["clips"] == clips
    joints = {
        j["name"]: j["position"][1]
        for j in json.loads((fit / "skeleton.json").read_text())["joints"]
    }
    tmpl = {b["name"]: b["head"][1] for b in _templates.bones("kaiju")}
    want = (joints["Hips"] - min(joints.values())) / (tmpl["Hips"] - min(tmpl.values()))
    assert abs(json.loads((out / "clips.json").read_text())["height_scale"] - want) < 0.02


def snake(turn: float = 0) -> trimesh.Trimesh:
    """A tube snake 2 m long lying on the floor in an S along +Z (x = 0.15 cos 2Ï€z, straight at both ends), 10 cm
    thick, its head (the last 15 cm, 16 cm thick) at +Z, turned `turn` degrees about Y."""
    pitch, lo = 0.01, np.array([-0.3, 0.0, -1.1])
    grid = np.zeros(np.ceil((np.array([0.3, 0.2, 1.1]) - lo) / pitch).astype(int), bool)
    at = np.stack(np.meshgrid(*(np.arange(n) for n in grid.shape), indexing="ij"), -1) * pitch + lo
    for z in np.arange(-1, 1, 0.005):
        r = 0.08 if z > 0.85 else 0.05
        grid |= ((at - [0.15 * np.cos(2 * np.pi * z), r, z]) ** 2).sum(-1) <= r**2
    m = _faces(grid, lo, pitch)
    m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(turn), [0, 1, 0]))
    return m


@pytest.mark.parametrize("turn", [0, 90, 180, 270])
def test_a_snake_lies_along_its_own_body_head_at_the_thick_end(turn):
    """No limbs: the chain along the tube's S, every joint of it inside, the head at the thick end, `yaw` its heading."""
    from steps import _rig

    m = snake(turn)
    bones, yaw = _templates.fit("snake", m.vertices, f=m.faces)
    assert abs((yaw - turn + 180) % 360 - 180) < 10
    chain = ["head", "neck", *(f"tail{i:02d}" for i in range(1, 21)), "tail_tip"]
    pos = np.array([bones[n]["position"] for n in chain])
    assert _rig.inside(m.vertices, m.faces, pos).all()
    back = pos @ trimesh.transformations.rotation_matrix(np.radians(turn), [0, 1, 0])[:3, :3]
    assert back[0, 2] > 0.8 and back[-1, 2] < -0.8  # head at +Z, the tail's end at -Z
    turned = _templates.fit("snake", m.vertices, (yaw + 180) % 360, m.faces)[
        0
    ]  # the editor's Turn around
    assert np.allclose(turned["head"]["position"], bones["tail_tip"]["tail"], atol=0.25)
