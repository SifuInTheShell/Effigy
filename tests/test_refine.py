"""refine (M43b): view planning, the back-projection weights, the weight⁴ paint, the colour match, the face
mask, a whole run with the generator faked (identity), the params; the real model only with
EFFIGY_GPU_TESTS=1, CUDA and the Tile weights installed (it loads ~20 GB and fills the card)."""

import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
trimesh = pytest.importorskip("trimesh")
from PIL import Image

from steps import refine
from steps._bake import heal_seams, rasterize_uv, seams, write_glb
from steps._common import StepError
from steps._views import depth_edges, ortho, render, texel_weights, zbuffer
from tests.test_project import _box

ATLAS = 96


def test_views_are_planned_from_the_texel_density():
    v, f, uv, _, _ = _box()
    v = v * [1, 4, 1] + [0, 1, 0]  # 0.5 × 2 × 0.5 m, standing on the ground
    size = 2048
    # UV cells cover 6 × 0.3 × 0.45 of the atlas; 3D area 4 × (0.5 × 2) + 2 × 0.25 m²
    d = size * math.sqrt(6 * 0.3 * 0.45 / 4.5)
    assert refine.texel_density(v, f, uv, size) == pytest.approx(d, rel=1e-3)
    e = 1024 / (1.25 * d)  # 0.94 m windows
    n = math.ceil((2 - e) / (0.75 * e)) + 1
    views = refine.plan_views(v, f, uv, size)
    assert n == 3 and len(views) == 8 * n + 1
    assert [w["yaw"] for w in views[: n + 1]] == [0] * (n + 1) and views[n]["pitch"] == -60
    assert {w["yaw"] for w in views[-n:]} == {180}  # the back last
    tops = sorted({round(w["center"][1], 3) for w in views if w["pitch"] == 0})
    assert tops == pytest.approx([e / 2, 1.0, 2 - e / 2], abs=1e-3)
    assert all(w["extent"] >= e - 1e-9 for w in views)


def test_a_wide_window_is_widened_to_fit():
    v, f, uv, _, _ = _box()
    v = v * [8, 1, 1]  # 4 m wide, 0.5 m tall: wider than one window at this density
    views = refine.plan_views(v, f, uv, 256)
    front = views[0]
    pix, _ = ortho(0, 0, front["center"], front["extent"])["project"](v)
    assert pix.min() >= 0 and pix.max() <= 1
    assert not any(
        w["pitch"] for w in views
    )  # wider than tall: no view from above onto its flat top


def test_a_ledge_below_the_top_window_gets_a_view_from_above():
    v, f, uv, _, _ = _box()
    # a 1 × 0.4 × 1 m plinth under a 0.3 × 0.3 × 2 m post: taller than wide, four height windows
    vv = np.r_[v * [2, 0.8, 2] + [0, 0.2, 0], v * [0.6, 4, 0.6] + [0, 1.4, 0]]
    ff = np.r_[f, f + len(v)]
    uu = np.r_[uv, uv]
    views = refine.plan_views(vv, ff, uu, 2048)
    # the planned view from above: the top window only
    assert [w["pitch"] for w in views].count(-60) == 1
    e = refine.VIEW / (refine.ZOOM * refine.texel_density(vv, ff, uu, 2048))
    fine = trimesh.remesh.subdivide(*trimesh.remesh.subdivide(vv, ff))  # 16 faces a side
    added, seen = refine.coverage_views(*fine, views, 3, e)
    # the most squarely onto the plinth's top first; the post's shadow in it (0.3 × 0.25 m, 1.2 %) from another
    assert len(added) == 2 and added[0]["pitch"] < -80 and added[1]["pitch"] < -30
    assert all(w["extent"] >= e - 1e-9 for w in added)
    # 6.18 m² in all; unseen before: the plinth's top (1) and floor side (1), the post's foot (0.09, on the plinth);
    # seen after, by the windows as fitted: the top round the post (0.91)
    assert seen["seen_before"] == pytest.approx(4.09 / 6.18, abs=0.01)
    assert seen["seen_after"] == pytest.approx(5.0 / 6.18, abs=0.01)


def test_a_coverage_window_keeps_its_zoom():
    """Ledges far apart on a pole, all seen from one side: no window wider than about 2e (half the zoom); the far
    ones are left to the next views."""
    v, f, uv, _, _ = _box()
    ledges = [v * [2, 0.16, 2] + [dx, y, 0] for dx, y in ((0.25, 0.4), (-0.25, 1.0), (0.25, 1.6))]
    vv = np.concatenate([v * [0.4, 8, 0.4] + [0, 1, 0], *ledges])
    ff = np.concatenate([f + 24 * i for i in range(4)])
    uu = np.concatenate([uv] * 4)
    e = refine.VIEW / (refine.ZOOM * refine.texel_density(vv, ff, uu, 4096))
    fine = trimesh.remesh.subdivide(*trimesh.remesh.subdivide(vv, ff))
    added, seen = refine.coverage_views(*fine, refine.plan_views(vv, ff, uu, 4096), 3, e)
    assert len(added) == 3 and all(e - 1e-9 <= w["extent"] <= 2.1 * e for w in added)
    assert seen["seen_after"] > seen["seen_before"] + 0.1


def test_a_flat_top_wider_than_tall_is_left_alone():
    v, f, uv, _, _ = _box()
    # 4 × 0.5 × 0.5 m: no view from above, planned or added (a chest's lid, a wall's coping)
    v = v * [8, 1, 1]
    e = refine.VIEW / (refine.ZOOM * refine.texel_density(v, f, uv, 256))
    added, seen = refine.coverage_views(v, f, refine.plan_views(v, f, uv, 256), 3, e)
    assert added == [] and seen["seen_before"] == seen["seen_after"]
    assert seen["seen_after"] == pytest.approx(
        4.5 / 8.5, abs=1e-3
    )  # the top and the floor side unseen


def test_the_cube_needs_no_more_views():
    v, f, uv, _, _ = _box()
    added, seen = refine.coverage_views(v, f, refine.plan_views(v, f, uv, ATLAS), 3, 1.0)
    assert added == [] and seen["seen_before"] == seen["seen_after"]
    assert seen["seen_after"] == pytest.approx(5 / 6, abs=1e-3)  # all but the floor side


def _cube_texels(dev="cpu"):
    v, f, uv, _, cells = _box()
    ft = torch.tensor(f, dtype=torch.long)
    uvt = torch.tensor(uv, dtype=torch.float32)
    face_id, bary = rasterize_uv(uvt, ft, ATLAS)
    covered = face_id >= 0
    corners, w3 = ft[face_id[covered]], bary[covered][..., None]
    pts = (torch.tensor(v, dtype=torch.float32)[corners] * w3).sum(1).numpy().astype(float)
    return v, f, ft, uvt, covered, corners, w3, pts, cells


def test_back_projection_paints_what_the_view_sees():
    v, f, ft, uvt, covered, corners, w3, pts, cells = _cube_texels()
    cam = ortho(30, 0, [0, 0, 0], 1.2)  # turned 30° towards +X: the front at cos 0.87, +X at 0.5
    base = torch.full((ATLAS, ATLAS, 3), 128.0)
    _, a, _, (_, _, zbuf, _, depth_v) = render(base, uvt, ft, v, cam, 256)
    wt, _ = texel_weights(cam, a, zbuf, depth_v, pts, v, f, corners, w3)
    img = torch.zeros(ATLAS, ATLAS)
    img[covered] = wt

    def at(cell):
        u, w = cells[cell]["centre"]
        return float(img[int(w * ATLAS), int(u * ATLAS)])

    s = (0.5 - 0.25) / 0.35
    assert at((2, 1)) == pytest.approx(1.0)  # the front: full weight
    assert at((0, 1)) == pytest.approx(s * s * (3 - 2 * s), abs=0.02)  # grazing: partly
    assert at((2, -1)) == 0 and at((0, -1)) == 0  # the back and the far side: untouched


def test_nothing_is_painted_next_to_a_depth_step():
    """A small box standing 0.15 m off the cube's front face: the face around it is the view's blend of the two
    surfaces, so it takes nothing from the view there; further out it takes all."""
    v, f, _, uvt, _, corners, w3, pts, _ = _cube_texels()
    vv = np.r_[v, v * 0.4 + [0, 0, 0.5]]
    ff = np.r_[f, f + len(v)]
    cam = ortho(0, 0, [0, 0, 0], 1.2)
    _, a, _, (_, _, zbuf, _, depth_v) = render(
        torch.full((ATLAS, ATLAS, 3), 128.0),
        torch.tensor(np.r_[uvt, uvt], dtype=torch.float32),
        torch.tensor(ff),
        vv,
        cam,
        256,
    )
    wt, _ = texel_weights(cam, a, zbuf, depth_v, pts, vv, ff, corners, w3)
    front = np.abs(pts[:, 2] - 0.25) < 1e-6
    r = np.abs(pts[:, :2]).max(1)  # the small box covers r < 0.1; 6 view px are 0.028 m
    assert (wt[front & (r > 0.1) & (r < 0.12)] == 0).all()
    assert (wt[front & (r > 0.15) & (r < 0.2)] == 1).all()


@pytest.mark.parametrize("az", [0, 45])  # tilted along the pixel grid and across its diagonal
def test_a_surfaces_own_slope_is_no_depth_step(az):
    """A square turned 75° from the view (cos 0.26: the steepest that still paints) has no depth step; at 84° it is
    one all over (its 3×3 neighbourhoods span more than 16 pixel widths)."""
    sq = np.array([[-0.4, -0.4, 0], [0.4, -0.4, 0], [0.4, 0.4, 0], [-0.4, 0.4, 0]])
    f = torch.tensor([[0, 1, 2], [0, 2, 3]])
    cam = ortho(0, 0, [0, 0, 0], 1.0)
    for deg, flagged in ((75, 0.0), (84, 1.0)):
        k = np.array(
            [-math.sin(math.radians(az)), math.cos(math.radians(az)), 0.0]
        )  # the turn's axis
        kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
        t = math.radians(deg)
        rot = np.eye(3) + math.sin(t) * kx + (1 - math.cos(t)) * kx @ kx  # Rodrigues
        pix, depth = (torch.tensor(x, dtype=torch.float32) for x in cam["project"](sq @ rot.T))
        zbuf = zbuffer(pix, depth, f, 512)[2]
        drawn = torch.isfinite(zbuf).float()
        inner = -torch.nn.functional.max_pool2d(-drawn[None, None], 3, 1, 1)[0, 0] > 0
        assert float(depth_edges(zbuf, 1.0, 0)[inner].mean()) == flagged, (deg, az)


def test_the_yaw_names_the_diagonals_only():
    names = {y: refine._yaw_name(y, 0) for y in (0, 10, 30, 45, 60, 80, 90, 315)}
    assert [y for y, name in names.items() if name == "three-quarter view"] == [30, 45, 60, 315]
    assert set(names.values()) == {"", "three-quarter view"}
    assert refine._yaw_name(137.5, -40) == "from above"


def test_the_best_view_wins_and_fades_into_the_original():
    orig = torch.zeros(2, 3)
    acc, wsum, wmax = torch.zeros(2, 3), torch.zeros(2), torch.zeros(2)
    red, blue = torch.tensor([[255.0, 0, 0]] * 2), torch.tensor([[0, 0, 255.0]] * 2)
    out = refine.paint(acc, wsum, wmax, torch.tensor([1.0, 0.5]), red, orig)
    assert out[0].tolist() == [255, 0, 0] and out[1].tolist() == pytest.approx([127.5, 0, 0])
    out = refine.paint(acc, wsum, wmax, torch.tensor([0.5, 1.0]), blue, orig)
    assert out[0, 0] > 15 * out[0, 2]  # the frontal red, a 1/16 trace of blue
    assert out[1, 2] > 15 * out[1, 0]


def test_the_colour_match_removes_a_constant_offset():
    yy, xx = torch.meshgrid(torch.linspace(0, 1, 128), torch.linspace(0, 1, 128), indexing="ij")
    rgb = torch.stack([xx * 0.5, yy * 0.5, xx * 0], -1) + 0.2
    rgb = rgb + 0.1 * ((xx * 40).sin() * (yy * 40).sin())[..., None]  # detail
    a = torch.ones(128, 128)
    out = refine.colour_match(rgb, a, rgb + 0.15, 3.0)[0].permute(1, 2, 0)
    assert (out - rgb)[8:-8, 8:-8].abs().max() < 0.02


def _version(root: Path, name: str, step: str, parent, rgb, outputs=None):
    d = root / name
    d.mkdir(parents=True)
    Image.fromarray(np.asarray(rgb, np.uint8)).save(d / "basecolor.png")
    meta = {
        "version": name,
        "step": step,
        "parent": parent,
        "outputs": outputs or {"basecolor": "basecolor.png"},
    }
    (d / "meta.json").write_text(json.dumps(meta))
    return d


def test_the_face_mask_is_what_project_changed(tmp_path):
    grey = np.full((32, 32, 3), 120)
    face = grey.copy()
    face[4:10, 20:28] = (200, 150, 130)
    face[20, 5] = (125, 120, 120)  # a 5-level wobble is not face
    bake = _version(tmp_path, "030_bake", "bake", None, grey)
    proj = _version(tmp_path, "040_project", "project", "030_bake", face)
    ref = _version(tmp_path, "050_refine", "refine", "040_project", grey)
    for src in (proj, ref):
        m = refine.face_mask(src, 32)
        assert m.sum() == 6 * 8 and m[4:10, 20:28].all()
    assert refine.face_mask(proj, 64).sum() == 4 * 6 * 8  # an upsized atlas
    assert refine.face_mask(bake, 32) is None


class _Identity:
    models = ()

    def encode(self, prompts):
        self.prompts = prompts
        return {p: None for p in prompts}

    def __call__(self, ctrl, emb, strength, scale, seed):
        self.calls = getattr(self, "calls", []) + [strength]
        return ctrl


def _bake(tmp_path):
    v, f, uv, n, _ = _box()
    bake = tmp_path / "asset" / "030_bake"
    bake.mkdir(parents=True)
    yy, xx = np.mgrid[0:ATLAS, 0:ATLAS]
    rgba = np.stack([xx * 2, yy * 2, np.full_like(xx, 90), np.full_like(xx, 255)], -1).astype(
        np.uint8
    )
    rng = np.random.default_rng(0)
    orm = rng.integers(0, 255, (ATLAS, ATLAS, 3), dtype=np.uint8)
    normal_map = rng.integers(0, 255, (ATLAS, ATLAS, 3), dtype=np.uint8)
    write_glb(bake / "mesh.glb", v, f, uv, rgba, orm, n, normal_map)
    meta = {"version": "030_bake", "step": "bake", "parent": None, "outputs": {"mesh": "mesh.glb"}}
    (bake / "meta.json").write_text(json.dumps(meta))
    brief = {"name": "box", "brief": {"prompt_full": "a wooden box"}}
    (tmp_path / "asset" / "asset.json").write_text(json.dumps(brief))
    return bake, rgba, orm, normal_map


def test_a_run_with_an_identity_generator_keeps_the_texture(tmp_path, monkeypatch):
    bake, rgba, orm, normal_map = _bake(tmp_path)
    gen = _Identity()
    monkeypatch.setattr(refine, "VIEW", 256)
    monkeypatch.setattr(refine, "_generator", lambda tile: gen)
    out = tmp_path / "asset" / "040_refine"
    out.mkdir()
    res = refine.run("run", str(bake), str(out), {"seed": 3})
    assert set(res["outputs"]) == {"mesh", "basecolor", "orm", "normal", "views"}
    assert all((out / x).is_file() for x in res["outputs"].values())
    m = res["metrics"]
    assert m["views"] == 9 and m["face_views"] == 0 and gen.calls == [1.0] * 9
    # all but the floor side seen, no views added
    assert m["coverage_views"] == 0 and m["seen_after"] == pytest.approx(5 / 6, abs=1e-3)
    assert m["detail_metric"] == "coherence"
    assert (
        m["drift"] < 1.5 and m["refined"] > 0.5 and 0.5 < m["detail"] < 1.5
    )  # a smooth gradient: little band energy
    assert all(p.startswith("a wooden box, even flat lighting") for p in gen.prompts)
    assert not any(w in p for p in gen.prompts for w in ("front view", "back view", "side view"))
    got = np.asarray(Image.open(out / "basecolor.png"), int)
    # identity in, the texture out: the input with its UV seams levelled (the step's last pass, M43a's)
    v, f, uv, _, _ = _box()
    cov = (
        rasterize_uv(torch.tensor(uv, dtype=torch.float32), torch.tensor(f), ATLAS)[0].numpy() >= 0
    )
    want = heal_seams(rgba[..., :3], cov, seams(v, f, uv, ATLAS)).astype(int)
    assert np.abs(got[..., :3] - want)[cov].mean() < 3
    assert (np.asarray(Image.open(out / "orm.png")) == orm).all()
    assert (np.asarray(Image.open(out / "normal.png")) == normal_map).all()
    glb = trimesh.load(out / "mesh.glb", force="mesh", process=False)
    assert len(glb.faces) == 12


@pytest.mark.parametrize("coverage", [3, 0])
def test_a_run_adds_the_coverage_views(tmp_path, monkeypatch, coverage):
    bake, *_ = _bake(tmp_path)
    gen, asked = _Identity(), []
    top = {"yaw": 0, "pitch": -85, "center": [0, 0, 0], "extent": 1.0, "part": ""}

    def more(v, faces, views, extra, e, dev):
        asked.append((len(views), extra))
        return [top], {"seen_before": 0.5, "seen_after": 0.9}

    monkeypatch.setattr(refine, "VIEW", 256)
    monkeypatch.setattr(refine, "_generator", lambda tile: gen)
    monkeypatch.setattr(refine, "coverage_views", more)
    out = tmp_path / "asset" / "040_refine"
    out.mkdir()
    m = refine.run("run", str(bake), str(out), {"coverage": coverage})["metrics"]
    if coverage:
        assert asked == [(9, 3)] and m["views"] == 10 and m["views_used"][-1]["pitch"] == -85
        assert (m["coverage_views"], m["seen_before"], m["seen_after"]) == (1, 0.5, 0.9)
    else:
        assert asked == [] and m["views"] == 9 and "coverage_views" not in m


def test_a_view_takes_its_own_prompt(tmp_path, monkeypatch):
    """The owner's agent, 2026-10-13: one prompt for a hoarding's painted front and its bare plywood back drew blue
    panels on every back view. A view's `prompt` replaces the asset's for that view only."""
    bake, *_ = _bake(tmp_path)
    gen = _Identity()
    monkeypatch.setattr(refine, "VIEW", 256)
    monkeypatch.setattr(refine, "_generator", lambda tile: gen)
    out = tmp_path / "asset" / "040_refine"
    out.mkdir()
    views = [
        {"yaw": 0, "center": [0, 0, 0], "extent": 1.2},
        {"yaw": 180, "center": [0, 0, 0], "extent": 1.2, "prompt": "bare weathered plywood"},
    ]
    refine.run("run", str(bake), str(out), {"views": views})
    assert sorted(p.split(",")[0] for p in gen.prompts) == [
        "a wooden box",
        "bare weathered plywood",
    ]


def test_the_detail_metric_counts_strokes_not_speckle():
    """The structure-weighted band energy: stripes (coherent gradients) count, noise of the same band energy
    (incoherent) nearly not; a flat image is 0."""
    n = 96
    xx = torch.arange(n).expand(n, n)
    mask = torch.ones(n, n, dtype=torch.bool)
    flat = torch.full((n, n, 3), 0.5)
    stripes = flat + 0.2 * torch.sin(xx.float() / 1.2)[..., None]  # 2–8 px scale strokes
    g = torch.Generator().manual_seed(0)
    noise = flat + 0.2 * torch.randn(n, n, 1, generator=g)
    e_flat, e_stripes, e_noise = (refine._band_energy(x, mask) for x in (flat, stripes, noise))
    assert e_flat < 1e-6 and e_stripes > 20 * e_noise > 0


def _shadowed(n=128):
    """An albedo with detail, darkened by a known AO (a crevice down the middle), and that AO."""
    yy, xx = torch.meshgrid(torch.linspace(0, 1, n), torch.linspace(0, 1, n), indexing="ij")
    albedo = torch.stack([0 * xx + 180, 0 * xx + 140, 0 * xx + 90], -1)
    albedo = (
        albedo + 20 * ((xx * 60).sin() * (yy * 60).sin())[..., None]
    )  # detail, unrelated to the AO
    ao = 1 - 0.7 * torch.exp(-(((xx - 0.5) / 0.15) ** 2))
    return albedo, ao, albedo * ao[..., None]


def test_delight_divides_the_baked_shadows_out():
    albedo, ao, rgb = _shadowed()
    cov = torch.ones(ao.shape, dtype=torch.bool)
    cov[:, :4] = False  # a gutter: left alone
    assert refine.ao_luma_corr(rgb, ao, cov) > 0.9
    out = refine.delight(rgb, ao, cov, 1.0)
    inner = (slice(8, -8), slice(8, -8))
    assert (out - albedo)[inner].abs().mean() < 6  # the crevice comes back up to the albedo
    assert abs(refine.ao_luma_corr(out, ao, cov)) < 0.1
    assert (out[:, :4] == rgb[:, :4]).all()
    ratio = out[inner][..., 0] / out[inner][..., 2]  # one factor per texel: the hue stays
    assert (ratio - (rgb[inner][..., 0] / rgb[inner][..., 2])).abs().max() < 1e-3
    half = refine.delight(rgb, ao, cov, 0.5)
    assert ((half - rgb)[inner] >= -1e-3).all() and ((out - half)[inner] >= -1e-3).all()
    assert torch.equal(refine.delight(rgb, ao, cov, 0.0), rgb)
    lit = albedo.clone()  # no shadows in the colour: nothing to take out
    assert (refine.delight(lit, ao, cov, 1.0) - lit)[inner].abs().max() < 1


def _bake_with_ao(tmp_path):
    v, f, uv, n, _ = _box()
    bake = tmp_path / "asset" / "030_bake"
    bake.mkdir(parents=True)
    xx = np.mgrid[0:ATLAS, 0:ATLAS][1]
    ao = 1 - 0.6 * (xx > ATLAS // 2)  # every cell's right part occluded
    rgba = np.stack([160 * ao, 120 * ao, 80 * ao, np.full_like(ao, 255)], -1).astype(np.uint8)
    orm = np.stack([ao * 255, np.full_like(ao, 200), np.zeros_like(ao)], -1).astype(np.uint8)
    write_glb(bake / "mesh.glb", v, f, uv, rgba, orm, n, None, occlusion=True)
    meta = {"version": "030_bake", "step": "bake", "parent": None, "outputs": {"mesh": "mesh.glb"}}
    (bake / "meta.json").write_text(json.dumps(meta))
    return bake


def test_a_delight_only_run_loads_no_model(tmp_path, monkeypatch):
    bake = _bake_with_ao(tmp_path)
    monkeypatch.setattr(refine, "_generator", lambda tile: pytest.fail("no model without views"))
    out = tmp_path / "asset" / "040_refine"
    out.mkdir()
    res = refine.run("run", str(bake), str(out), {"views": [], "delight": 1.0})
    assert set(res["outputs"]) == {"mesh", "basecolor", "orm"} and res["models"] == []
    m = res["metrics"]
    assert m["views"] == 0 and m["delight"] == 1.0
    assert m["ao_luma_corr_before"] > 0.5 and abs(m["ao_luma_corr"]) < 0.25
    got = trimesh.load(out / "mesh.glb", force="mesh", process=False).visual.material
    assert got.occlusionTexture is not None  # the AO still goes to the engine


def test_delight_needs_the_bakes_ao(tmp_path):
    bake, *_ = _bake(tmp_path)  # no occlusionTexture: a pre-M43a or CPU bake
    with pytest.raises(StepError) as e:
        refine.run("run", str(bake), str(tmp_path / "out"), {"views": [], "delight": 0.5})
    assert e.value.code == "missing_input"


@pytest.mark.parametrize(
    "params",
    [
        {"delight": 1.5},
        {"delight": "yes"},
        {"strength": 0.1},
        {"face_strength": 0},
        {"scale": 2},
        {"zoom": 0.1},
        {"zoom": "big"},
        {"texture_size": 8192},
        {"seed": "x"},
        {"prompt": " "},
        {"views": [{"yaw": 0}]},
        {"views": [{"yaw": 0, "center": [0, 0, 0], "extent": 1, "prompt": " "}]},
        {"views": []},
        {"tile": "huge"},
        {"coverage": 9},
        {"coverage": 1.5},
        {"coverage": True},
    ],
)
def test_bad_params(tmp_path, params):
    bake, *_ = _bake(tmp_path)
    with pytest.raises(StepError) as e:
        refine.run("run", str(bake), str(tmp_path / "out"), params)
    assert e.value.code == "bad_params"


@pytest.mark.skipif(
    not (
        os.environ.get("EFFIGY_GPU_TESTS")
        and torch.cuda.is_available()
        and (
            Path(os.environ.get("EFFIGY_MODELS", Path(__file__).resolve().parents[1] / "models"))
            / refine.TILE_DIR
            / refine.TILES["lite"][0]
        ).is_file()
    ),
    reason="the real Tile model: EFFIGY_GPU_TESTS=1, CUDA and the `refine` group installed",
)
def test_the_real_model_draws_one_view(tmp_path):
    bake, *_ = _bake(tmp_path)
    front = {"yaw": 0, "pitch": 0, "center": [0, 0, 0], "extent": 0.7}
    out = tmp_path / "asset" / "040_refine"
    out.mkdir()
    res = refine.run("run", str(bake), str(out), {"views": [front], "seed": 1, "tile": "lite"})
    m = res["metrics"]
    assert m["views"] == 1 and m["peak_reserved_gb"] <= 14.5 and m["drift"] < 8
    assert [x["id"] for x in res["models"]] == ["Tongyi-MAI/Z-Image-Turbo", refine.TILE]


def test_the_full_tile_when_it_is_downloaded_and_fits(tmp_path, monkeypatch):
    """M46c: without a "tile" param refine takes the full Tile when its file is there and the budget holds its
    15.6 GB; asked for one, it loads that one (the pre-flight refuses full where it cannot fit)."""
    from steps import _common

    monkeypatch.setenv("EFFIGY_MODELS", str(tmp_path))
    monkeypatch.setattr(_common, "_budget", 22.6)
    assert refine._tile(None) == "lite"  # not downloaded
    full = tmp_path / refine.TILE_DIR / refine.TILES["full"][0]
    full.parent.mkdir(parents=True)
    full.write_bytes(b"")
    assert refine._tile(None) == "full"
    monkeypatch.setattr(_common, "_budget", 14.66)  # the 16 GB laptop
    assert refine._tile(None) == "lite"
    assert refine._tile("full") == "full" and refine._tile("lite") == "lite"
