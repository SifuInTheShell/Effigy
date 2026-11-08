"""M4 / §3.9: mesh and UV checks on fixtures, and clean/remesh/uv/validate with CuMesh faked (CPU)."""

import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest
import trimesh

from steps import clean, remesh, uv, validate
from steps._common import StepError
from steps._mesh import mesh_report, read_glb, uv_report, write_glb

FIXTURE = Path(__file__).parent / "fixtures" / "two_tris.glb"


def cube(offset=(0, 0, 0)):
    m = trimesh.creation.box(extents=[1, 1, 1])
    return m.vertices + offset, m.faces


def test_mesh_report_on_fixtures():
    v, f = cube()
    rep = mesh_report(v, f)
    assert (
        rep["watertight"] and rep["components"] == 1 and rep["faces"] == 12 and rep["height_m"] == 1
    )
    holed = mesh_report(v, f[2:])  # one side of the cube is open: 4 boundary edges
    assert holed["boundary_edges"] == 4 and not holed["watertight"]
    v2, f2 = cube((3, 0, 0))
    assert mesh_report(np.vstack([v, v2]), np.vstack([f, f2 + len(v)]))["components"] == 2
    fan = np.array([[0, 1, 2], [0, 1, 3], [0, 1, 4]])  # three faces on one edge
    assert mesh_report(np.random.default_rng(0).random((5, 3)), fan)["non_manifold_edges"] == 1
    torus = trimesh.creation.torus(
        0.5, 0.2
    )  # a hole through a closed surface: the cat's back after remesh
    rep = mesh_report(torus.vertices, torus.faces)
    assert rep["tunnels"] == 1 and mesh_report(v, f)["tunnels"] == 0 and holed["tunnels"] is None
    assert validate.checklist(rep, None, 100_000)["no_tunnels"] == {"status": "warn", "value": 1}


def per_face_atlas(v, f, p=None):
    n = math.ceil(math.sqrt(len(f)))
    lv, lf = v[f].reshape(-1, 3), np.arange(3 * len(f)).reshape(-1, 3)
    cell = np.arange(len(f))
    x0, y0, m = (cell % n) / n, (cell // n) / n, 0.1 / n
    uvs = np.stack(
        [
            np.stack([x0 + m, y0 + m], 1),
            np.stack([x0 + 1 / n - m, y0 + m], 1),
            np.stack([x0 + m, y0 + 1 / n - m], 1),
        ],
        1,
    )
    return lv, lf, uvs.reshape(-1, 2)


def test_uv_report():
    v, f = cube()
    lv, lf, uvs = per_face_atlas(v, f)
    rep = uv_report(uvs, lf, lv)
    assert rep["islands"] == 12 and rep["in_0_1"] and rep["texel_density_p10_p90"] == [1.0, 1.0]


def version(path: Path, mesh_file: Path, parent=None):
    path.mkdir()
    shutil.copy(mesh_file, path / "mesh.glb")
    (path / "meta.json").write_text(
        json.dumps({"version": path.name, "parent": parent, "outputs": {"mesh": "mesh.glb"}})
    )
    return path


def test_clean_remesh_uv_validate_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(clean, "_cumesh_clean", lambda v, f, *_: (v, f, 0))
    monkeypatch.setattr(remesh, "_cumesh_simplify", lambda v, f, target, *_: (v, f[:target]))
    monkeypatch.setattr(uv, "_cumesh_unwrap", lambda v, f, p: per_face_atlas(v, f))
    v, f = cube((5, 2, 0))
    write_glb(tmp_path / "in.glb", v, f)
    src = version(tmp_path / "010_upload", tmp_path / "in.glb")

    def step(module, name, parent, params):
        out = tmp_path / name
        out.mkdir()
        res = module.run("run", str(parent), str(out), params)
        (out / "meta.json").write_text(
            json.dumps(
                {
                    "version": name,
                    "parent": parent.name,
                    "outputs": res["outputs"],
                    "metrics": res["metrics"],
                }
            )
        )
        return out, res

    c, res = step(clean, "020_clean", src, {"height_m": 2.0})
    t = np.array(res["metrics"]["transform"])
    np.testing.assert_allclose(t[:3, :3], np.eye(3) * 2)  # 1 m cube → 2 m
    cv, _, _ = read_glb(c / "mesh.glb")
    np.testing.assert_allclose(
        [cv[:, 1].min(), cv[:, 1].max()], [0, 2], atol=1e-6
    )  # base on the floor
    np.testing.assert_allclose((cv.min(0) + cv.max(0))[[0, 2]] / 2, [0, 0], atol=1e-6)
    r, res = step(remesh, "030_remesh", c, {"faces": 100})
    assert res["metrics"]["faces"] == 12  # already under budget: untouched
    u, res = step(uv, "040_uv", r, {})
    assert res["metrics"]["islands"] == 12 and read_glb(u / "mesh.glb")[2] is not None
    _, res = step(validate, "050_validate", u, {})
    items = json.loads((tmp_path / "050_validate" / "checklist.json").read_text())["items"]
    assert (
        res["metrics"]["fail"] == 0
        and items["scale_metres"]["value"] == 2.0
        and items["has_uvs"]["status"] == "pass"
    )
    _, res = step(validate, "060_validate", src, {})
    assert res["metrics"]["fail"] == 2  # the upload has no UVs: has_uvs and uvs_in_0_1


def test_validate_fixture_glb():
    v, f, uvs = read_glb(FIXTURE)
    assert len(f) == 2 and uvs is not None and uv_report(uvs, f, v)["in_0_1"]


def test_fill_holes_recomputes_bad_hole_centres():
    import pytest

    torch = pytest.importorskip("torch")
    from steps._mesh import fill_holes

    square = torch.tensor([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=torch.float32)

    class Fake:  # CuMesh's hole fill on our Windows build: a fan around a centre with z ≈ 1e17
        def __init__(self):
            self.v, self.f = square, torch.zeros((0, 3), dtype=torch.int32)

        num_vertices = property(lambda self: len(self.v))

        def fill_holes(self, max_hole_perimeter):
            self.v = torch.cat([self.v, torch.tensor([[0.5, 0.5, 9.6e16]])])
            self.f = torch.tensor([[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]], dtype=torch.int32)

        def read(self):
            return self.v, self.f

        def init(self, v, f):
            self.v, self.f = v, f

    m = Fake()
    fill_holes(m, 1.0)
    assert torch.allclose(m.v[4], torch.tensor([0.5, 0.5, 0.0])) and len(m.f) == 4


def test_uv_refuses_an_unknown_unwrapper(tmp_path):
    with pytest.raises(StepError) as e:
        uv.run("run", str(tmp_path), str(tmp_path), {"unwrapper": "uvgami"})
    assert e.value.code == "bad_params"


def test_weld_joins_uv_seams():
    from steps._mesh import weld

    # a quad split at its diagonal as glTF does at a UV seam: 6 vertices, the diagonal looks open
    v = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 0, 0], [1, 1, 0], [0, 1, 0]], float)
    f = np.array([[0, 1, 2], [3, 4, 5]])
    assert mesh_report(v, f)["boundary_edges"] == 6
    wv, wf = weld(v, f)
    assert len(wv) == 4 and mesh_report(wv, wf)["boundary_edges"] == 4


def test_spread_votes_decides_enclosed_faces_from_their_neighbours():
    from steps._mesh import spread_votes

    box = trimesh.creation.box().subdivide().subdivide()
    f = box.faces.copy()
    top = box.triangles_center[:, 2] > 0.49
    f[top] = f[top][:, ::-1]  # the lid wound inward
    votes = np.zeros(len(f))
    votes[np.flatnonzero(top)[0]] = -1  # one decided face on the lid ...
    votes[np.flatnonzero(~top)[0]] = 1  # ... and one elsewhere; every other face undecided
    assert (spread_votes(f, votes) == np.where(top, -1, 1)).all()


def test_drop_small_parts_follows_shared_vertices():
    import pytest

    torch = pytest.importorskip("torch")
    from steps._mesh import drop_small_parts

    v = torch.tensor(
        [
            [0, 0, 0],
            [1, 0, 0],
            [0, 1, 0],
            [1, 1, 0],
            [2, 1, 0],
            [1, 2, 0],
            [5, 5, 0],
            [5.01, 5, 0],
            [5, 5.01, 0],
        ],
        dtype=torch.float32,
    )
    # a bowtie (two triangles meeting at vertex 3 only: no shared edge) and a tiny far triangle
    f = torch.tensor([[0, 1, 3], [3, 4, 5], [6, 7, 8]], dtype=torch.int32)

    class Fake:
        def read(self):
            return v, f

        def init(self, v2, f2):
            self.f = f2

    m = Fake()
    drop_small_parts(m, 0.1)
    assert m.f.tolist() == [[0, 1, 3], [3, 4, 5]]  # the bowtie stays whole, the speck goes


def test_solid_hull_closes_a_perforated_box():
    """M18: an open box with a pinhole comes back closed, outward, with the box's volume (Blender's voxel
    remesh made a hollow sheet of such input)."""
    import trimesh

    from steps.remesh import solid_hull

    box = trimesh.creation.box((1, 1, 1))
    for _ in range(5):  # faces ~1.2 voxels: the hole is a pinhole, as in a raw Faithful mesh
        box = box.subdivide()
    keep = np.ones(len(box.faces), bool)
    keep[np.argmax(box.triangles_center[:, 2])] = False  # one small face of the top: a hole
    v, f = solid_hull(box.vertices, box.faces[keep], res=40)
    rep = mesh_report(v, f)
    hull = trimesh.Trimesh(v, f, process=False)
    assert rep["boundary_edges"] == 0 and rep["components"] == 1
    assert 0.95 < hull.volume < 1.2  # positive: faces point out


def test_solid_hull_fills_a_shell_through_its_holes():
    """A thin shell with a hole a quarter of its size (the cat's back) is filled to its volume, a bowl
    (its opening as wide as itself) is not, and `fill` off keeps the shell a shell."""
    from steps.remesh import solid_hull

    ball = trimesh.creation.icosphere(subdivisions=4, radius=0.5)

    def cut(
        top,
    ):  # the faces above `top` off, and their vertices (solid_hull voxelizes every vertex)
        m = ball.submesh([ball.triangles_center[:, 2] < top], append=True)
        return m.vertices, m.faces

    for top, filled in ((0.48, True), (0.0, False)):  # a hole of radius ~0.14 m; the top half off
        info = {}
        v, f = solid_hull(*cut(top), res=40, fill=True, info=info)
        volume = trimesh.Trimesh(v, f, process=False).volume
        assert (info["filled_gap_cm"] > 0) == filled
        assert 0.45 < volume < 0.62 if filled else volume < 0.15, (top, volume)
    v, f = solid_hull(*cut(0.48), res=40, fill=False)
    assert trimesh.Trimesh(v, f, process=False).volume < 0.15


def test_fill_loops_closes_a_pinched_hole():
    """Two holes sharing one vertex (a figure 8: CuMesh's fill_holes skips it) are both filled."""
    from steps._mesh import fill_loops

    box = trimesh.creation.box((1, 1, 1)).subdivide()
    v, f = np.asarray(box.vertices), np.asarray(box.faces)
    c = v[f].mean(1)
    top = np.flatnonzero(c[:, 1] > 0.49)
    corner = np.argmin(
        np.abs(v[:, 0]) + np.abs(v[:, 1] - 0.5) + np.abs(v[:, 2])
    )  # the top's middle
    around = [i for i in top if corner in f[i]]
    a, b = around[0], next(i for i in around[1:] if len(set(f[i]) & set(f[around[0]])) == 1)
    g = np.delete(f, [a, b], 0)  # two triangles touching only at the middle vertex
    assert mesh_report(v, g)["boundary_edges"] == 6
    v2, f2 = fill_loops(v, g, max_perimeter=10.0)
    rep = mesh_report(v2, f2)
    assert rep["boundary_edges"] == 0 and rep["non_manifold_edges"] == 0
    assert trimesh.Trimesh(v2, f2, process=False).is_winding_consistent


def _mesh(mesh):
    """A trimesh as the two CuMesh calls drop_enclosed_parts makes (torch tensors)."""
    import torch

    class M:
        def __init__(self):
            self.v = torch.tensor(mesh.vertices, dtype=torch.float32)
            self.f = torch.tensor(mesh.faces, dtype=torch.int32)

        def read(self):
            return self.v, self.f

        def init(self, v, f):
            self.v, self.f = v, f

    return M()


def test_drop_enclosed_parts_keeps_a_sphere_poking_through_a_tilted_torus():
    """PIPE-001: the sphere's six extremes lie inside the torus's tube while its sides stand out through the
    ring's holes: an intersecting, visible part, kept; a bubble truly inside is still dropped."""
    import pytest

    pytest.importorskip("torch")
    from steps._mesh import drop_enclosed_parts
    from steps._rig import winding

    ring = trimesh.creation.torus(
        major_radius=1.4, minor_radius=1.0, major_sections=48, minor_sections=24
    )
    ring.apply_transform(trimesh.geometry.align_vectors([0, 0, 1], np.ones(3) / np.sqrt(3)))
    ball = trimesh.creation.icosphere(subdivisions=2, radius=1.5)
    extremes = ball.vertices[np.unique(np.r_[ball.vertices.argmin(0), ball.vertices.argmax(0)])]
    assert (
        np.abs(winding(ring.vertices, ring.faces, extremes)) > 0.5
    ).all()  # the trap: the extremes alone
    assert (
        np.abs(winding(ring.vertices, ring.faces, ball.vertices)) < 0.5
    ).any()  # but not every vertex
    m = _mesh(trimesh.util.concatenate([ring, ball]))
    assert drop_enclosed_parts(m) == 0 and len(m.f) == len(ring.faces) + len(ball.faces)
    on_ring = (
        1.4 * np.array([1, -1, 0]) / np.sqrt(2)
    )  # a point of the tilted major circle: in the tube
    bubble = trimesh.creation.icosphere(subdivisions=1, radius=0.2).apply_translation(on_ring)
    m = _mesh(trimesh.util.concatenate([ring, bubble]))
    assert drop_enclosed_parts(m) == 1 and len(m.f) == len(ring.faces)


def test_the_blocked_torch_winding_matches_the_numpy_one():
    """drop_enclosed_parts' winding (torch, blocks of points) = steps._rig's (numpy, a point at a time): a
    closed torus, an open one and one wound inward, points inside, outside and in the hole."""
    import pytest

    pytest.importorskip("torch")
    from steps._mesh import _winding
    from steps._rig import winding

    ring = trimesh.creation.torus(major_radius=1.4, minor_radius=0.5)
    pts = np.random.default_rng(0).uniform(-2.2, 2.2, (300, 3))
    v = ring.vertices
    for f in [ring.faces, ring.faces[40:], ring.faces[:, ::-1]]:
        np.testing.assert_allclose(_winding(v, f, pts), winding(v, f, pts), atol=1e-9)
    assert len(_winding(ring.vertices, ring.faces, np.empty((0, 3)))) == 0


def test_drop_enclosed_parts_keeps_a_ring_around_another_part():
    import pytest

    pytest.importorskip("torch")
    from steps._mesh import drop_enclosed_parts

    ring = trimesh.creation.torus(major_radius=2.0, minor_radius=0.2)
    ball = trimesh.creation.icosphere(subdivisions=2, radius=0.5)
    m = _mesh(
        trimesh.util.concatenate([ring, ball])
    )  # the mean vertex of the ring lies inside the ball
    assert drop_enclosed_parts(m) == 0 and len(m.f) == len(ring.faces) + len(ball.faces)
    m = _mesh(
        trimesh.util.concatenate([ball, trimesh.creation.icosphere(subdivisions=1, radius=0.1)])
    )
    assert drop_enclosed_parts(m) == 1 and len(m.f) == len(ball.faces)  # a bubble inside goes


def test_drop_enclosed_parts_near_drops_the_rebuilds_inner_copy_only():
    """The bollard (agent 2026-10-13): the rebuild's inner copy of a solid, 0.4 mm inside, poked through the
    outer shell by 0.15 mm in places and was kept as a crossing part. With `near` it goes and the outer stays;
    a part well inside a hollow (a candle in a lantern: a ball in a thick tube's bore) stays."""
    import pytest

    pytest.importorskip("cumesh")
    from steps._mesh import drop_enclosed_parts

    outer = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
    inner = outer.copy()
    inner.vertices *= 0.996
    inner.vertices[::50] *= 1.0 / 0.996 * 1.001  # a few vertices poke out by a hair
    inner.invert()  # the rebuild's inner shell faces the solid's inside
    m = _mesh(trimesh.util.concatenate([outer, inner]))
    assert drop_enclosed_parts(m) == 0  # without near: a crossing part, kept (as before)
    assert drop_enclosed_parts(m, near=0.01) == 1 and len(m.f) == len(outer.faces)
    assert np.allclose(np.linalg.norm(m.v.numpy()[np.unique(m.f.numpy())], axis=1), 1.0, atol=1e-5)
    tube = trimesh.creation.annulus(r_min=0.6, r_max=1.0, height=2.0)
    ball = trimesh.creation.icosphere(subdivisions=2, radius=0.3)
    m = _mesh(trimesh.util.concatenate([tube, ball]))
    assert drop_enclosed_parts(m, near=0.01) == 0 and len(m.f) == len(tube.faces) + len(ball.faces)


def test_clean_straighten_levels_the_floor_and_squares_the_footprint():
    """A box 4 x 1 x 0.4 turned 30 deg about Y and tilted 6 deg about Z, as a wall from a three-quarter picture
    comes out: straighten_transform brings its floor level and its long side onto X."""
    from steps._templates import _yaw
    from steps.clean import straighten_transform

    box = trimesh.creation.box(extents=(4, 1, 0.4))
    tilt = trimesh.transformations.rotation_matrix(np.radians(6), [0, 0, 1])[:3, :3]
    v = box.vertices @ tilt.T @ _yaw(30)
    m, tilt_deg, yaw_deg = straighten_transform(v)
    out = v @ m
    assert abs(tilt_deg - 6) < 1 and abs(abs(yaw_deg) - 30) < 1
    assert np.allclose(
        np.ptp(out, 0), [4, 1, 0.4], atol=0.02
    )  # axis-aligned again, the long side on X
    low = out[out[:, 1] <= out[:, 1].min() + 0.02]
    assert np.ptp(low[:, 1]) < 0.02 and len(low) >= 4  # the whole floor at one height
    assert np.allclose(straighten_transform(box.vertices)[0], np.eye(3))  # nothing to do: identity
    # a square footprint (a chest from a three-quarter picture) is squared too; a round one is left alone
    cube = trimesh.creation.box(extents=(1, 1, 1)).vertices @ _yaw(35)
    m, _, yaw = straighten_transform(cube)
    assert abs(abs(yaw) - 35) < 1 and np.allclose(np.ptp(cube @ m, 0), [1, 1, 1], atol=0.01)
    drum = trimesh.creation.cylinder(radius=0.5, height=1, sections=64)  # along Z: stand it up on Y
    drum = (
        drum.vertices @ trimesh.transformations.rotation_matrix(np.radians(90), [1, 0, 0])[:3, :3].T
    )
    drum = drum @ _yaw(20)
    assert straighten_transform(drum)[2] == 0.0


def test_straighten_stands_a_leaning_post_on_its_shaft():
    """The bollard (agent 2026-10-13): a 1 m post on a 0.24 m foot leaning 11° has no floor covering 60 % of
    its footprint; it is stood upright on its shaft. An upright lamp column with an arm at the top stays."""
    from steps._templates import _yaw
    from steps.clean import straighten_transform

    def along_y(mesh, y0):
        r = trimesh.transformations.rotation_matrix(np.radians(-90), [1, 0, 0])[:3, :3]
        return mesh.subdivide_to_size(0.03).vertices @ r.T + [
            0,
            y0,
            0,
        ]  # vertices along the shaft too

    post = np.r_[
        along_y(trimesh.creation.cylinder(radius=0.12, height=0.06, sections=48), 0.03),
        along_y(trimesh.creation.cylinder(radius=0.09, height=0.94, sections=48), 0.53),
    ]
    lean = trimesh.transformations.rotation_matrix(np.radians(11), [0, 0, 1])[:3, :3]
    v = post @ lean.T @ _yaw(30)
    m, tilt, _ = straighten_transform(v)
    w = v @ m
    assert abs(tilt - 11) < 0.5
    assert np.ptp(w[:, [0, 2]], 0).max() < 0.25  # upright: the foot's 0.24 m is the widest again
    arm = np.r_[post, trimesh.creation.box((0.6, 0.06, 0.06)).vertices + [0.3, 0.95, 0]]
    assert straighten_transform(arm)[1] == 0.0


def test_straighten_does_not_level_onto_a_sliver():
    """The fence (2026-10-11): a tapered 5 mm panel's largest downward hull plane was an 18° sliver of 23 % of
    the footprint, and the panel got levelled onto it. A twisted underside (its back edge rising, its front
    one flat: two half-footprint facets) is turned square but not levelled; a plain tilted box still is."""
    from steps._templates import _yaw
    from steps.clean import straighten_transform

    panel = trimesh.creation.box((3.0, 1.5, 0.15)).subdivide().subdivide()
    v = panel.vertices.copy()
    t = (v[:, 0] + 1.5) / 3
    v[:, 1] += (
        0.25 * t * (v[:, 2] > 0)
    )  # the back edge rises 25 cm over the length, the front one stays
    v = v @ _yaw(40)
    m, tilt, yaw = straighten_transform(v)
    w = v @ m
    assert tilt == 0.0 and abs(abs(yaw) - 40) < 1.5
    assert (
        np.ptp(w[:, 0]) > 2.9 and np.ptp(w[:, 2]) < 0.4
    )  # the long side on X, the panel still a panel


def test_detaper_squares_a_tapered_panel():
    """A 3 × 1.5 × 0.15 panel drawn in perspective: one end 29 % shorter, the foot rising; its depth even, as the
    reconstruction measures it. detaper levels the top and bottom within a centimetre, keeps the tall end and
    stretches the length, and leaves the depth alone (agent finding 1: scaled with the height, a wedge)."""
    from steps._mesh import detaper

    box = trimesh.creation.box((3.0, 1.5, 0.15)).subdivide().subdivide().subdivide().subdivide()
    v = box.vertices.copy()
    t = (v[:, 0] + 1.5) / 3  # 0 at the tall end, 1 at the far end
    k = 1 - 0.29 * t
    v[:, 1] = (v[:, 1] + 0.75) * k + 0.245 * t  # scaled about the foot, the foot rising
    out, info = detaper(v)
    assert info and 27 < info["taper_pct"] < 31 and info["length_gain_pct"] > 10
    for x in np.linspace(out[:, 0].min() + 0.1, out[:, 0].max() - 0.1, 6):
        band = out[np.abs(out[:, 0] - x) < 0.2]
        assert abs(band[:, 1].max() - 1.5) < 0.015 and abs(band[:, 1].min()) < 0.015
        assert abs(np.ptp(band[:, 2]) - 0.15) < 1e-6  # as thick at both ends
    assert abs(out[:, 0].min() - v[:, 0].min()) < 1e-6  # the tall end (x = -1.5) stayed
    assert detaper(box.vertices)[1] is None  # nothing to undo on a square one
    from steps._templates import _yaw

    assert (
        detaper(v @ _yaw(45))[1] is None
    )  # still turned 45°: not a panel along X or Z, left alone


def test_solid_hull_close_shuts_the_slits_between_boards():
    """remesh `close`: five 5 mm boards with 5 mm slits become one closed solid at a 12 mm close (res from the
    pitch, as remesh derives it), about the boards' own volume."""
    from steps.remesh import solid_hull

    boards = [
        trimesh.creation.box((0.1, 1.0, 0.005)).apply_translation((0.105 * i, 0, 0))
        for i in range(5)
    ]
    m = trimesh.util.concatenate(boards)
    close = 0.012
    res = int(np.ceil(np.ptp(m.vertices, 0).max() / (close / 2)))
    v, f = solid_hull(m.vertices, m.faces, res=res, close=2, fill=True)
    rep = mesh_report(v, f)
    assert rep["components"] == 1 and rep["boundary_edges"] == 0
    assert (
        0.002 < trimesh.Trimesh(v, f, process=False).volume < 0.012
    )  # one slab about the boards' size


def test_uv_overlap_counts_texels_two_faces_claim():
    """Two triangles on one UV triangle: every covered texel is claimed twice; a proper unwrap: none.
    validate turns the share into a percent: a warning from 1 %, a failure from 10 %."""
    from steps._mesh import uv_overlap
    from steps.validate import checklist

    f = np.array([[0, 1, 2], [0, 2, 3]])
    apart = np.array([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]])
    stacked = np.array(
        [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.9, 0.1]]
    )  # the second over the first
    assert uv_overlap(apart, f) < 0.005  # the shared diagonal's texels are nobody's
    assert uv_overlap(stacked, f) > 0.98
    rep = {
        "non_manifold_edges": 0,
        "boundary_edges": 0,
        "components": 1,
        "degenerate_faces": 0,
        "faces": 2,
        "height_m": 1.0,
    }
    uv = {"in_0_1": True, "overlap": 0.7}
    assert checklist(rep, uv, 100).get("uv_overlap") == {"status": "fail", "value": 70.0}
    assert checklist(rep, uv | {"overlap": 0.03}, 100)["uv_overlap"]["status"] == "warn"
    assert checklist(rep, uv | {"overlap": 0.0}, 100)["uv_overlap"]["status"] == "pass"
    assert "uv_overlap" not in checklist(rep, None, 100)


def test_self_intersection_and_parts_inside_by_ray_parity():
    """mesh-gates: a surface folded through itself and a part inside another are geometric, not topological."""
    from steps._mesh import parts_inside, self_intersection

    v, f = cube()
    assert self_intersection(v, f)["pct"] == 0 and parts_inside(v, f) is None
    sph = trimesh.creation.icosphere(subdivisions=3, radius=0.5)
    pushed = sph.vertices.copy()
    pushed[
        pushed[:, 0] > 0.3, 0
    ] = -0.9  # a cap pushed out through the far side: closed, folded through itself
    assert self_intersection(pushed, sph.faces)["pct"] > 10
    assert self_intersection(v, f[2:]) is None  # open: parity means nothing, so nothing is claimed
    assert (
        self_intersection(v, f[:, ::-1])["pct"] == 0
    )  # wound inward (an imported piece, a cavity): not a fold
    small = trimesh.creation.box(extents=[0.3, 0.3, 0.3])

    def two(dx):
        b = small.copy().apply_translation([dx, 0, 0])
        return np.vstack([v, b.vertices]), np.vstack([f, b.faces + len(v)])

    assert parts_inside(*two(0))["pct"] == 100  # a shell inside a shell
    assert parts_inside(*two(0.5))["pct"] == 50  # half sunk
    assert parts_inside(*two(3))["pct"] == 0  # apart
    hat = small.copy().apply_translation(
        [0, 0.65, 0]
    )  # flush on the top face: touching is not penetrating
    assert (
        parts_inside(np.vstack([v, hat.vertices]), np.vstack([f, hat.faces + len(v)]))["pct"] == 0
    )
    rep = mesh_report(v, f) | {"self_intersection": {"pct": 2.5}, "parts_inside": {"pct": 50.0}}
    items = validate.checklist(rep, None, 100_000)
    assert items["self_intersection"] == {"status": "warn", "value": 2.5}
    assert items["parts_apart"] == {"status": "warn", "value": 50.0}
    skip = validate.checklist(mesh_report(v, f), None, 100_000)
    assert skip["self_intersection"] == skip["parts_apart"] == {"status": "skip", "value": None}


def test_joint_loops_counts_the_bands_along_the_bone():
    """mesh-gates: a tube of N rings around a knee: the rings within 0.35 of the bone's length are its loops."""
    from steps._rig import joint_loops

    def tube(rings):
        ang = np.linspace(0, 2 * np.pi, 16, endpoint=False)
        ys = np.linspace(0, 1, rings)
        return np.array([[0.1 * np.cos(a), y, 0.1 * np.sin(a)] for y in ys for a in ang])

    joints = [
        {"name": "Hips", "parent": None, "position": [0, 0, 0]},
        {"name": "LeftLeg", "parent": "Hips", "position": [0, 0.5, 0]},  # the knee
        {
            "name": "LeftFoot",
            "parent": "LeftLeg",
            "position": [0, 0.52, 0],
        },  # a 2 % bone, and no bend: left out
    ]
    assert joint_loops(tube(3), joints) == {"bands": {"LeftLeg": 1}, "few": ["LeftLeg"]}
    assert joint_loops(tube(9), joints) == {"bands": {"LeftLeg": 3}, "few": []}
    rng = np.random.default_rng(0)
    tilted = tube(3)
    tilted[:, 1] += rng.uniform(
        -0.02, 0.02, len(tilted)
    )  # a ring that is not planar is still one band
    assert joint_loops(tilted, joints)["bands"]["LeftLeg"] == 1
    irregular = np.c_[
        rng.uniform(-0.1, 0.1, 400), rng.uniform(0, 1, 400), rng.uniform(-0.1, 0.1, 400)
    ]
    assert joint_loops(irregular, joints) == {
        "bands": {"LeftLeg": 7},
        "few": [],
    }  # a dense mesh bends


def test_joint_loops_leaves_a_joint_alone_when_its_window_holds_no_surface():
    """A spine joint inside a wide torso: no vertex within 0.35 of the bone of its axis, so nothing to judge (every
    bench character's three spine joints were flagged as "few loops" before, the laptop's 16 bench rigs)."""
    from steps._rig import joint_loops

    ang = np.linspace(0, 2 * np.pi, 24, endpoint=False)
    torso = np.array(
        [[0.5 * np.cos(a), y, 0.5 * np.sin(a)] for y in np.linspace(0, 1, 11) for a in ang]
    )
    joints = [
        {"name": "Hips", "parent": None, "position": [0, 0, 0]},
        {
            "name": "Spine",
            "parent": "Hips",
            "position": [0, 0.2, 0],
        },  # radius 0.07: the torso is at 0.5
    ]
    assert joint_loops(torso, joints) == {"bands": {}, "few": []}


def test_joint_loops_judges_the_elbows_and_knees_only():
    """The bends by name: a human's ForeArm and Leg, an animal's Lower bones; not UpLeg, a spine, a jaw."""
    from steps._rig import BENDS

    for n in ("LeftForeArm", "RightLeg", "Front_Leg_Lower_L", "back_leg_lower_r", "LowerLeg_L"):
        assert BENDS.search(n), n
    for n in (
        "LeftUpLeg",
        "Spine1",
        "Neck",
        "Head",
        "LeftShoulder",
        "LeftArm",
        "mouth_lower",
        "Front_Leg_Upper_L",
    ):
        assert not BENDS.search(n), n


def test_level_stands_tilted_generated_props_upright():
    """The owner, 2026-10-13: "most all the models generated come out in a tilted way". A Faithful model keeps the
    picture's camera, looking down 10-15°. level_transform finds the floor even when it covers far less than the
    tilted footprint (a barrel's bottom under its bulge: 53 %; a drone on thin legs under wide rotors: 39 %), and
    leaves an upright model alone."""
    from steps.clean import level_transform

    def tilted(mesh, deg):
        return (
            mesh.vertices
            @ trimesh.transformations.rotation_matrix(np.radians(deg), [1, 0, 0])[:3, :3].T
        )

    # a barrel: a bulging drum standing on Y, its bottom narrower than its belly
    ring = [
        (0.36, 0.0),
        (0.43, 0.25),
        (0.45, 0.5),
        (0.43, 0.75),
        (0.36, 1.0),
    ]  # floor: half the belly's square
    barrel = trimesh.creation.revolve([(0.0, 0.0), *ring, (0.0, 1.0)], sections=48)
    barrel.vertices = barrel.vertices[:, [0, 2, 1]]  # revolve builds along Z: stand it on Y
    # a drone: a wide flat body and rotors on four thin legs
    body = trimesh.creation.box(extents=(2.2, 0.15, 1.7))
    body.apply_translation((0, 0.5, 0))
    legs = [trimesh.creation.box(extents=(0.05, 0.45, 0.05)) for _ in range(4)]
    for leg, (x, z) in zip(legs, [(-0.8, -0.6), (0.8, -0.6), (-0.8, 0.6), (0.8, 0.6)]):
        leg.apply_translation((x, 0.225, z))
    drone = trimesh.util.concatenate([body, *legs])
    for mesh, deg in [(barrel, 10), (drone, 18)]:
        v = tilted(mesh, deg)
        m, tilt = level_transform(v)
        assert abs(tilt - deg) < 1.5, (deg, tilt)
        out = v @ m
        low = out[out[:, 1] <= out[:, 1].min() + 0.01]
        assert np.ptp(low[:, 1]) < 0.01 and len(low) >= 4  # standing on its whole floor again
    assert np.allclose(
        level_transform(barrel.vertices)[0], np.eye(3)
    )  # upright already: nothing to do


def test_only_a_generated_model_is_levelled_by_default(tmp_path):
    """clean's `level: "auto"`: a model whose chain has a trellis2 version stands upright; an imported one keeps
    the placement its author gave it."""
    from steps.clean import _generated

    def meta(name, step, parent, outputs=None):
        (tmp_path / name).mkdir()
        (tmp_path / name / "meta.json").write_text(
            json.dumps({"version": name, "step": step, "parent": parent, "outputs": outputs or {}})
        )
        return tmp_path / name

    meta("001_upload", "upload", None)
    meta("002_prep", "prep", "001_upload")
    meta("003_trellis2", "trellis2", "002_prep", {"mesh": "hi.glb", "ovoxel": "ovoxel.npz"})
    assert _generated(meta("004_blender", "blender", "003_trellis2"))
    meta("010_upload", "upload", None)
    assert not _generated(meta("011_import_model", "import_model", "010_upload"))


def test_level_finds_a_floor_under_a_loop_that_props_the_hull_up():
    """Agent finding 17: the barrier block stood on a lifting eye mirrored under it; the eye props the convex hull
    up into slanted facets, so the hull offers no floor, and the mesh's own lowest faces (the eye a few cm below
    them ignored) give it: 14.7° on the library's block. A tall thin post stands on its shaft before any floor."""
    from steps.clean import level_transform

    block = trimesh.creation.box(extents=(0.6, 0.9, 0.5)).subdivide_to_size(
        0.03
    )  # dense all over, as generated
    block.apply_translation((0, 0.45, 0))
    eye = trimesh.creation.torus(
        major_radius=0.06, minor_radius=0.015, major_sections=16, minor_sections=8
    )
    eye.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    eye.apply_translation((0, -0.05, 0))  # its loop hangs 8 cm under the block's floor
    model = trimesh.util.concatenate([block, eye])
    tilt = trimesh.transformations.rotation_matrix(np.radians(14), [1, 0, 0])[:3, :3]
    v, f = model.vertices @ tilt.T, model.faces
    assert level_transform(v)[1] == 0.0  # the hull alone: no floor
    m, deg = level_transform(v, f)
    assert abs(deg - 14) < 1.5
    out = (v @ m)[: len(block.vertices)]
    low = out[out[:, 1] <= out[:, 1].min() + 0.01]
    assert np.ptp(low[:, 1]) < 0.01 and len(low) >= 4  # the block's floor level again


def test_a_figure_whose_middle_fakes_a_lean_keeps_its_floor():
    """The review, 2026-10-13: an arm or a staff across a figure's middle third moves the slab centres and fakes a
    lean in _shaft; the figure's floor, level, disagrees with that shaft by more than 3° and wins: nothing is
    turned. The bollard's floor (its flange) agrees with its shaft within 3°, and the shaft wins there."""
    from steps.clean import _shaft, level_transform

    body = trimesh.creation.box(extents=(0.3, 1.8, 0.3))
    body.apply_translation((0, 0.9, 0))
    feet = trimesh.creation.box(extents=(0.6, 0.05, 0.4))
    feet.apply_translation((0, 0.025, 0))
    staff = trimesh.creation.box(extents=(0.05, 0.9, 0.05))
    staff.apply_transform(trimesh.transformations.rotation_matrix(np.radians(-12), [0, 0, 1]))
    staff.apply_translation(
        (0.2, 0.9, 0)
    )  # held close, leaning across the middle third: a 6° fake lean
    figure = trimesh.util.concatenate([body, feet, staff]).subdivide_to_size(
        0.03
    )  # dense, as generated
    assert _shaft(figure.vertices) is not None  # the slabs alone see a lean
    m, tilt = level_transform(figure.vertices, figure.faces)
    assert tilt == 0.0 and np.allclose(m, np.eye(3))


def test_press_to_floor_flattens_a_loop_under_a_base_and_leaves_rims_and_legs():
    """The owner's agent (2026-10-13, findings 15 and 19): the barrier block's lifting eye came back mirrored
    underneath, 3.8-5.2 cm below its base, and once grounded the block stood on it. press_to_floor presses a small
    part under the middle of a broad flat base onto it; a rim round the outline (a barrel's staves under its sunken
    head) and a chair's legs (no broad base) stay."""
    from steps.clean import press_to_floor

    block = trimesh.creation.box(extents=(0.6, 0.9, 0.5)).subdivide_to_size(0.03)
    block.apply_translation((0, 0.45 + 0.05, 0))  # its base at 5 cm
    eye = trimesh.creation.torus(
        major_radius=0.04, minor_radius=0.01, major_sections=16, minor_sections=8
    )
    eye.apply_translation(
        (0, 0.05, 0)
    )  # upright (the ring in the XY plane), its lower half under the base, to 0
    model = trimesh.util.concatenate([block, eye])
    v, pressed = press_to_floor(model.vertices, model.faces)
    assert (
        pressed and 4 <= pressed["depth_cm"] <= 6 and v[:, 1].min() >= 0.05 - 0.005
    )  # it stands on its base

    drum = trimesh.creation.cylinder(radius=0.3, height=0.8, sections=48).subdivide_to_size(0.03)
    drum.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    drum.apply_translation((0, 0.42, 0))  # its head at 2 cm
    rim = trimesh.creation.annulus(r_min=0.27, r_max=0.3, height=0.02, sections=48)
    rim.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    rim.apply_translation((0, 0.01, 0))  # the staves' ends round the outline, 0 to 2 cm
    barrel = trimesh.util.concatenate([drum, rim])
    assert press_to_floor(barrel.vertices, barrel.faces)[1] is None
    legs = [trimesh.creation.box(extents=(0.04, 0.45, 0.04)) for _ in range(4)]
    for leg, (x, z) in zip(legs, [(-0.2, -0.2), (0.2, -0.2), (-0.2, 0.2), (0.2, 0.2)]):
        leg.apply_translation((x, 0.225, z))
    seat = trimesh.creation.box(extents=(0.45, 0.04, 0.45))
    seat.apply_translation((0, 0.47, 0))
    chair = trimesh.util.concatenate([seat, *legs])
    assert press_to_floor(chair.vertices, chair.faces)[1] is None


def test_a_box_rolled_past_the_floor_search_stands_on_its_own_axes():
    """Agent finding 21: the raised bed came back rolled 30.5° (shot from above, as its prompt asked), past the
    floors' 30° window, and nothing levelled it. A box-like model's faces gather round three axes at right angles:
    the one nearest +Y stands it up. A round model is no box."""
    from steps.clean import _box_up, level_transform

    bed = trimesh.creation.box(extents=(1.2, 0.4, 0.8)).subdivide_to_size(0.05)
    roll = trimesh.transformations.rotation_matrix(np.radians(35), [0, 0, 1])[:3, :3]
    v, f = bed.vertices @ roll.T, bed.faces
    m, tilt = level_transform(v, f)
    assert abs(tilt - 35) < 1
    assert np.allclose(
        sorted(np.ptp(v @ m, 0)), [0.4, 0.8, 1.2], atol=0.01
    )  # square on its axes again
    ball = trimesh.creation.icosphere(subdivisions=3)
    assert _box_up(ball.vertices, ball.faces) is None


def _ring_with(*bits, rings=((0, 0, 0),)):
    """Dense rings lying flat (each about 2 m² of surface) and small balls (0.25 % of a ring's surface each)."""
    parts = []
    for c in rings:
        ring = trimesh.creation.torus(0.5, 0.1, major_sections=128, minor_sections=32)
        ring.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
        parts.append(ring.subdivide_to_size(0.02))
        parts[-1].apply_translation(c)
    for c in bits:
        parts.append(trimesh.creation.icosphere(subdivisions=2, radius=0.02))
        parts[-1].apply_translation(c)
    m = trimesh.util.concatenate(parts)
    return np.asarray(m.vertices), np.asarray(m.faces)


def test_drop_floating_takes_a_flap_off_the_outside_and_keeps_trim_and_a_flame():
    """Agent finding 19: a bottle bank's 13 cm rubber flap (0.2 % of the surface) hung clear of its front and stayed
    a part of its own. A small part goes when it is clear of the body and outside it; one touching it (a crate's
    corner trim) or floating inside its hull (a lantern's flame) stays, and so does all of a model of several large
    parts (a drone's rotors)."""
    from steps.clean import drop_floating

    flap, trim, flame = (0.8, 0, 0), (0.0, 0, 0.6), (0, 0, 0)
    v, f = _ring_with(flap, trim, flame)
    v2, f2, out = drop_floating(v, f)
    assert out == {"parts": 1, "faces": 320, "surface_pct": 0.25}
    assert len(f2) == len(f) - 320 and v2[:, 0].max() < 0.7 and len(v2) == len(np.unique(f2))
    assert drop_floating(*_ring_with(flap, rings=((0, 0, 0), (0, 0, 3))))[2] is None
    sheet = trimesh.creation.box(extents=(1, 1, 1)).subdivide_to_size(0.05)
    sheet = trimesh.Trimesh(sheet.vertices * [1, 0, 1], sheet.faces)  # flat: no hull
    ball = trimesh.creation.icosphere(subdivisions=1, radius=0.01)
    ball.apply_translation((2, 0, 0))
    flat = trimesh.util.concatenate([sheet, ball])
    assert drop_floating(np.asarray(flat.vertices), np.asarray(flat.faces))[2] is None
    # a coarse body: a piece on the middle of a large face is near no vertex but touches the surface
    box = trimesh.creation.box(extents=(1, 1, 1))
    chip = trimesh.creation.icosphere(subdivisions=1, radius=0.02)
    chip.apply_translation((0, 0, 0.51))  # its middle just outside, 1 cm sunk into the face
    coarse = trimesh.util.concatenate([box, chip])
    assert drop_floating(np.asarray(coarse.vertices), np.asarray(coarse.faces))[2] is None


def test_clean_drops_floating_parts_of_a_generated_model_only(tmp_path, monkeypatch):
    """`drop_floating: "auto"`: on for a model with the generator's volume up its chain, off for an import."""
    monkeypatch.setattr(clean, "_cumesh_clean", lambda v, f, *_: (v, f, 0))
    write_glb(tmp_path / "in.glb", *_ring_with((0.8, 0, 0)))
    for name, outputs in (("010_trellis2", {"ovoxel": "ovoxel.npz"}), ("010_upload", {})):
        src = version(tmp_path / name, tmp_path / "in.glb")
        meta = json.loads((src / "meta.json").read_text())
        (src / "meta.json").write_text(json.dumps(meta | {"outputs": meta["outputs"] | outputs}))
        for params, dropped in (({}, bool(outputs)), ({"drop_floating": True}, True)):
            out = tmp_path / f"{name}_{len(params)}"
            out.mkdir()
            res = clean.run("run", str(src), str(out), params)
            assert ("dropped_parts" in res["metrics"]) is dropped, (name, params)


def test_remesh_shuts_the_slits_when_its_surface_folds_through_itself(tmp_path, monkeypatch):
    """Agent finding 19: remesh 3000 left 17.5 % of a bottle bank's faces folded through each other; `close` at the
    length / 150 took it to 0. Without a close, a prop's tri remesh over 5 % folded is done again with one; `close: 0`
    keeps the first, and a character's stays (a close joins its fingers). Finding 6: a close too fine or too coarse is
    refused with the range the model takes."""
    hulls = []

    def solid_hull(v, f, res=160, close=1, fill=False, info=None):
        hulls.append(res)
        info |= {"filled_gap_cm": 0.0, "pitch": 1 / res}
        return v, f

    monkeypatch.setattr(remesh, "solid_hull", solid_hull)
    monkeypatch.setattr(remesh, "_filled", lambda hull, pitch, out: hull)
    write_glb(tmp_path / "in.glb", *cube())
    for category in ("prop", "character"):
        (tmp_path / category).mkdir()
        (tmp_path / category / "meta.json").write_text(
            json.dumps({"step": "brief", "parent": None, "params": {"category": category}})
        )
    src = version(tmp_path / "010_clean", tmp_path / "in.glb", parent="prop")
    figure = version(tmp_path / "011_clean", tmp_path / "in.glb", parent="character")
    failed = StepError("failed", "no Blender")
    cases = (  # the version, params, what self_intersection says, then: hulls made, the result's fold, kept
        (src, {}, [17.5, 0.0], 2, 0.0, True),
        (src, {}, [17.5, 20.0], 2, 17.5, False),  # folds more: the first stands
        (src, {}, [17.5, failed], 2, 17.5, False),  # fails: the first stands, and says why
        (src, {"close": 0}, [17.5], 1, 17.5, None),
        (figure, {}, [17.5], 1, 17.5, None),
    )
    for k, (d, params, says, made, si, kept) in enumerate(cases):
        answers = iter(says)

        def folds(v, f, answers=answers):
            x = next(answers)
            if isinstance(x, Exception):
                raise x
            return {"pct": x}

        monkeypatch.setattr(remesh, "self_intersection", folds)
        hulls.clear()
        out = tmp_path / f"020_remesh_{k}"
        out.mkdir()
        m = remesh.run("run", str(d), str(out), {"faces": 100} | params)["metrics"]
        assert len(hulls) == made and m["self_intersection"] == si, k
        assert (out / "mesh.glb").is_file() and not (out / "_closed").exists()
        if kept is None:
            assert "closed" not in m
        else:
            assert hulls[1] < 400 and m["closed"]["kept"] is kept, k
            assert m["closed"]["close_m"] == 0.0067
            assert m["closed"]["self_intersection_before"] == 17.5
            assert ("error" in m["closed"]) is (says[1] is failed)
    with pytest.raises(StepError, match="this is 500: use 0.005 to 0.2"):
        remesh.run("run", str(src), str(tmp_path / "x"), {"close": 0.004})


def test_validate_holds_the_model_to_the_briefs_size(tmp_path):
    """Agent finding 2: the hoarding's brief asked 3.2 m long and 2.1 m tall; the picture and the detaper made it 4.14 m
    long, and nothing said so. The size the prompt gives is held against the model's height and long side, turned or
    not; the thin side (a room to fit in: the panel came out 0.19 m for "0.3 m deep") is reported, not judged."""
    from steps.brief import dims
    from steps.validate import size_drift

    prompt = "a site hoarding, real-world size approximately 3.2 m long, 0.3 m deep, 2.1 m tall"
    assert dims(prompt) == {"plan": [3.2, 0.3], "long": 3.2, "height": 2.1}
    assert dims("a crate 45 cm wide") == {"plan": [0.45]} and dims("a dwarf blacksmith") == {}
    assert dims("a 2 m-tall door 0.9 metres wide") == {"height": 2.0, "plan": [0.9]}
    assert dims("a pole .5 m tall") == {"height": 0.5}
    assert (
        dims("0 m tall") == dims("1,8 m tall") == {}
    )  # nothing to divide by; a comma is not read as 8 m
    card = np.array([[0, 0, 0], [1, 0, 0], [0, 2, 0], [1, 2, 0.0]])  # on edge: a plan with no area
    assert size_drift(card, {"height": 2.0})["drift_pct"] == 0.0
    figure = trimesh.creation.box(extents=(1.6, 1.8, 0.3)).vertices  # arms out
    assert (
        size_drift(figure, {"height": 1.8, "plan": [0.5]})["drift_pct"] == 0.0
    )  # one width is no length
    assert size_drift(figure, {"plan": [0.5]})["drift_pct"] is None
    (tmp_path / "001_brief").mkdir()
    (tmp_path / "001_brief" / "meta.json").write_text(
        json.dumps({"step": "brief", "parent": None, "params": {"prompt": prompt}})
    )
    for name, extents, turn, status in (
        ("002_long", (4.14, 2.1, 0.19), 30, "warn"),
        ("003_fitted", (3.2, 2.1, 0.19), 0, "pass"),
    ):
        panel = trimesh.creation.box(extents=extents)
        panel.apply_transform(trimesh.transformations.rotation_matrix(np.radians(turn), [0, 1, 0]))
        write_glb(tmp_path / "in.glb", panel.vertices, panel.faces)
        d = version(tmp_path / name, tmp_path / "in.glb", parent="001_brief")
        out = tmp_path / f"{name}_validate"
        out.mkdir()
        validate.run("run", str(d), str(out), {})
        check = json.loads((out / "checklist.json").read_text())
        assert check["items"]["size_drift_pct"]["status"] == status, name
        assert check["report"]["size"]["model_m"]["plan"][1] == 0.19  # reported, not judged
    assert check["items"]["size_drift_pct"]["value"] == 0.0
    out = tmp_path / "x_validate"
    out.mkdir()
    validate.run("run", str(version(tmp_path / "x", tmp_path / "in.glb")), str(out), {})
    assert "size_drift_pct" not in json.loads((out / "checklist.json").read_text())["items"]


def test_smooth_normals_agree_across_a_uv_seam():
    """Vertices split at a UV seam (same position, two copies) get the same, smooth normal."""
    from steps._mesh import smooth_normals

    s = trimesh.creation.icosphere(subdivisions=2)
    v, f = s.vertices, s.faces.copy()
    seam = f[: len(f) // 2]
    extra = np.unique(seam)
    copy = {old: len(v) + i for i, old in enumerate(extra)}
    v2 = np.r_[v, v[extra]]
    f2 = np.r_[np.vectorize(copy.get)(seam), f[len(f) // 2 :]]  # half the sphere on its own copies
    n = smooth_normals(v2, f2)
    radial = v2 / np.linalg.norm(v2, axis=1, keepdims=True)
    assert np.allclose(n[extra], n[[copy[e] for e in extra]], atol=1e-9)  # both sides alike
    assert (np.abs((n * radial).sum(1)) > 0.99).all()  # smooth: close to the sphere's normal


def test_denoise_surface_smooths_noise_keeps_a_crease():
    from steps._mesh import denoise_surface

    box = trimesh.creation.box(extents=(1, 1, 1))
    for _ in range(4):
        box = box.subdivide()
    rng = np.random.default_rng(3)
    v0 = box.vertices + rng.normal(0, 0.003, box.vertices.shape)
    v1 = denoise_surface(v0, box.faces, 10, 10)
    top = (box.vertices[:, 1] > 0.49) & (np.abs(box.vertices[:, [0, 2]]).max(1) < 0.3)
    assert np.std(v1[top, 1]) < 0.5 * np.std(v0[top, 1])  # the noise flattened
    edge = (box.vertices[:, 1] > 0.49) & (box.vertices[:, 0] > 0.49)
    assert np.abs(v1[edge] - box.vertices[edge]).max() < 0.02  # the box's edge still where it was


def test_clean_rebuilds_a_solid_only_when_asked(tmp_path, monkeypatch):
    """`cleanup` is opt-in, for a generated model too: on a character it melted the face and dropped loose parts."""
    monkeypatch.setattr(clean, "_cumesh_clean", lambda v, f, *_: (v, f, 0))
    monkeypatch.setattr(clean, "solid_cleanup", lambda v, f, out, res: (v, f, {"parts_dropped": 0}))
    write_glb(tmp_path / "in.glb", *cube())
    src = version(tmp_path / "010_trellis2", tmp_path / "in.glb")
    meta = json.loads((src / "meta.json").read_text())
    outputs = meta["outputs"] | {"ovoxel": "ovoxel.npz"}
    (src / "meta.json").write_text(json.dumps(meta | {"outputs": outputs}))
    for params, rebuilt in (({}, False), ({"cleanup": True}, True)):
        out = tmp_path / f"out_{len(params)}"
        out.mkdir()
        res = clean.run("run", str(src), str(out), params)
        assert ("hi_clean" in res["outputs"]) is rebuilt and (
            "cleanup" in res["metrics"]
        ) is rebuilt
    with pytest.raises(StepError, match="cleanup"):
        clean.run("run", str(src), str(out), {"cleanup": "auto"})
