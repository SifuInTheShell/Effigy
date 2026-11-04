"""Clean a mesh (IMPLEMENTATION.md M4): CuMesh repairs, then optional rescale with the base centre at the origin.

params: {height_m: float | null, level: "auto"|true|false (stand it upright: its floor levelled, a post on its
shaft; "auto", the default, is on for a generated model and off for an imported one), straighten: false (also
square it: a prop from a three-quarter picture, its long side turned onto X), press_to_floor: false (what hangs
just under a broad flat base, up to a tenth of the height, pressed flat onto it: a lifting eye mirrored under a
block), drop_floating: "auto"|true|false (the small parts floating off the outside of a model that is otherwise one
solid dropped, metrics.dropped_parts; "auto": a generated model's), hole: 0.03, min_component: 1e-5,
rebuild: "auto"|true|false, cleanup: "auto"|true|false (2026-11-04: the model as a clean solid, see solid_cleanup;
"auto": on for a generated model; writes hi_clean.ply, the surface bake reads its normals from),
cleanup_resolution: 400,
rebuild_resolution: 512}  (hole and min_component are fractions of the bounding-box diagonal / its
square). Output is geometry only (UVs and textures come later from uv/bake); a rescale is recorded as
metrics.transform so bake can map the mesh back into the volume.

rebuild: a raw Faithful mesh can have most of its edges shared by 3+ faces once welded (the robot:
80k of 217k), and the repairs' only way to make those manifold is to drop faces: 183k → 92k faces and
82k open edges, holes all over the 12k game mesh. Instead the surface is rebuilt by CuMesh's narrow-band
dual contouring (what upstream to_glb does with remesh=True: closed, manifold) and simplified back to
the input's face count. "auto": when more than 1 % of the edges are non-manifold.
"""

import json
from pathlib import Path

import numpy as np

from steps._common import StepError, main, progress
from steps._mesh import (
    components,
    drop_enclosed_parts,
    drop_small_parts,
    fill_holes,
    make_manifold,
    mesh_report,
    orient,
    read_glb,
    weld,
    write_glb,
)


def _rebuild(m, resolution: int) -> float:
    """Replace m's surface by narrow-band dual contouring at `resolution`, 90 % snapped back onto the
    old surface, simplified back to the old face count; returns the voxel. The field is unsigned, so a
    solid comes back as two shells a voxel either side of its surface, 0.2 voxel apart once snapped back:
    the inner one is a copy that drop_enclosed_parts takes out."""
    import cumesh

    v, f = m.read()
    lo, hi = v.min(0).values, v.max(0).values
    scale = (hi - lo).max().item()
    rv, rf = cumesh.remeshing.remesh_narrow_band_dc(
        v,
        f,
        center=(lo + hi) / 2,
        scale=(resolution + 3) / resolution * scale,
        resolution=resolution,
        band=1,
        project_back=0.9,
        bvh=cumesh.cuBVH(v, f),
    )
    n = m.num_faces
    m.init(rv, rf)
    if m.num_faces > n:
        m.simplify(n)
    return (resolution + 3) / resolution * scale / resolution


def _cumesh_clean(v, f, hole: float, min_area: float, rebuild: bool, resolution: int):
    import cumesh
    import torch

    m = cumesh.CuMesh()
    m.init(torch.tensor(v, dtype=torch.float32).cuda(), torch.tensor(f, dtype=torch.int32).cuda())
    voxel = _rebuild(m, resolution) if rebuild else 0.0
    fill_holes(m, hole)
    m.remove_duplicate_faces()
    drop_small_parts(m, min_area)
    # the inner copy may poke through by under a voxel; two separate surfaces that close would have
    # merged in the rebuild's band, so nothing else lies that near another part all over
    dropped = drop_enclosed_parts(m, near=voxel) if rebuild else 0
    make_manifold(m, close=hole)
    orient(m, trust=rebuild)  # rebuilt: consistent winding; else face_outward's votes
    v2, f2 = m.read()
    return v2.cpu().numpy(), f2.cpu().numpy(), dropped


def straighten_transform(
    v: np.ndarray, f: np.ndarray | None = None
) -> tuple[np.ndarray, float, float]:
    """(M [3,3] for `v @ M`, tilt°, yaw°) levelling and squaring a prop generated from a three-quarter picture
    (the owner's second agent, 2026-10-11: a wall 45° off the axes, its base rising 0.33 m over 3 m): levelled
    (level_transform), then the footprint's smallest bounding
    rectangle (rotating calipers) turned square to the axes, its long side onto X (the broad side then looks
    along Z, the frame's front), when the footprint is long (aspect 1.5) or the rectangle fits it like a box
    (its area within 15 % of the footprint's hull: a round footprint, 27 % over, has no sides to square; an
    irregular one, an anvil, keeps its turn). Identity when there is nothing to do."""
    from scipy.spatial import QhullError

    from steps._templates import _yaw

    m, tilt = level_transform(v, f)
    yaw = 0.0
    try:
        a = _square((v @ m)[:, [0, 2]])
        if a is not None and abs(a) >= 3:
            m = m @ _yaw(-a)
            if abs(_square((v @ m)[:, [0, 2]]) or 0.0) > 1:  # turned the wrong way round: the other
                m = m @ _yaw(2 * a)
            yaw = a
        if a is not None and np.ptp((v @ m)[:, 0]) < np.ptp((v @ m)[:, 2]):  # the long side onto X
            m = m @ _yaw(90)
            yaw = (yaw + 90 + 90) % 180 - 90
    except QhullError:
        pass
    return m, round(tilt, 1), round(float(yaw), 1)


def _floor(v: np.ndarray, normal: np.ndarray, area: np.ndarray, low: np.ndarray | None = None):
    """(n, m, tilt) of a floor among downward faces (`normal` pointing into the model, `area`), or None: from the
    largest face, the faces within 8° of the area-weighted normal, a few rounds (a generated floor is noisy: exact
    normals split a barrel's bottom into slivers none of which counts), covering 35 % of the footprint's rectangle
    as it stands once levelled (measured on the tilted model a barrel's footprint takes in its leaning top: its round
    floor covered 53 %, a drone's feet under its rotors 39 %; a ragged or twisted underside, the fence of 2026-10-11,
    offers slivers, its largest an 18° diagonal of 23 %). `low` (the mesh's own faces): each face's centre; a face
    counts only within 3 % of the model's height of its bottom, the 5th percentile along the normal (an overhang's
    underside is no floor; a lifting eye mirrored under a block, a few cm below its floor, is no bottom), and the
    search starts from the direction holding the most area (the largest single face may be any facet)."""
    import trimesh

    from steps._templates import _turn

    n, near = normal[np.argmax(area)], np.zeros(len(area), bool)
    if (
        low is not None
    ):  # the densest direction: normals binned by their sideways part, 0.1 (about 6°) a bin
        key = np.round(normal[:, [0, 2]] / 0.1).astype(int)
        _, idx = np.unique(key, axis=0, return_inverse=True)
        best = np.argmax(np.bincount(idx, area))
        n = (normal[idx == best] * area[idx == best, None]).sum(0)
        n /= np.linalg.norm(n)
    for _ in range(3):
        near = normal @ n >= np.cos(np.radians(8))
        if low is not None:
            bottom = np.percentile(v @ n, 5)
            near &= low @ n <= bottom + 0.03 * np.ptp(v @ n)
        if not near.any():
            return None
        n = (normal[near] * area[near, None]).sum(0)
        n /= np.linalg.norm(n)
    m = _turn(n, np.array([0.0, 1.0, 0.0])).T
    footprint = float(np.prod(trimesh.bounds.oriented_bounds_2D((v @ m)[:, [0, 2]])[1]))
    if area[near].sum() < 0.35 * footprint:
        return None
    return n, m, float(np.degrees(np.arccos(min(n[1], 1.0))))


def _box_up(v: np.ndarray, f: np.ndarray) -> np.ndarray | None:
    """The up axis of a box-like model whose floor was never seen (the owner's agent, 2026-10-13, finding 21: a
    raised bed shot from above came back rolled 30° with a cane on it, and neither floor was found): its face
    normals (area-weighted, |cos| > 0.95) gather round three axes at right angles holding half its area; the one of
    the six directions nearest +Y. None for a model that is not box-like (a figure, a barrel, a rock)."""
    tri = v[f]
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area = 0.5 * np.linalg.norm(cross, axis=1)
    keep = area > 0
    n, a = cross[keep] / (2 * area[keep, None]), area[keep]

    def axis(
        seed: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:  # the main direction of the faces near ±seed
        d = seed
        for _ in range(3):
            near = np.abs(n @ d) > 0.95
            if not near.any():
                break
            _, e = np.linalg.eigh((n[near] * a[near, None]).T @ n[near])
            d = e[:, -1]
        return d, np.abs(n @ d) > 0.95

    d1, in1 = axis(n[np.argmax(a)])
    side = (np.abs(n @ d1) < 0.3) & ~in1
    if not side.any():
        return None
    d2, in2 = axis(n[side][np.argmax(a[side])])
    d3, in3 = axis(np.cross(d1, d2) / max(np.linalg.norm(np.cross(d1, d2)), 1e-12))
    if a[in1 | in2 | in3].sum() < 0.5 * a.sum():
        return None
    # the frame at right angles nearest all three, each weighted by its area (the soil on a bed is not quite square
    # to its boards: anchored on the largest group alone it tilted the bed 25.5°, the boards say 30.5°)
    d = np.array([d1, d2, d3])
    d *= (
        np.sign(np.diag(d))[:, None] + (np.diag(d) == 0)[:, None]
    )  # one sign each, near the frame's own axes
    u, _, vt = np.linalg.svd(d.T @ np.diag([a[in1].sum(), a[in2].sum(), a[in3].sum()]))
    frame = (u @ vt).T
    six = np.concatenate([frame, -frame])
    return six[np.argmax(six[:, 1])]


def level_transform(v: np.ndarray, f: np.ndarray | None = None) -> tuple[np.ndarray, float]:
    """(M [3,3] for `v @ M`, tilt°) standing a generated model upright, its turn about the up axis untouched
    (clean's default for a generated model; straighten squares it to the axes too). The owner, 2026-10-13: "most
    all the models generated come out in a tilted way": a Faithful model keeps the picture's camera, looking down
    10–15°. The floor: the largest downward plane of the convex hull (_floor), else the mesh's own lowest downward
    faces (`f` given: the barrier block stood on a lifting eye mirrored under it, which props the hull up into
    slanted facets, agent finding 17), else a box-like model's own axes (_box_up: a raised bed shot from above, its
    underside never seen, finding 21), turned flat when 3–45° off. A tall thin model that leans stands on its shaft
    (_shaft) when it has no floor, or when the floor is within 3° of the shaft (the bollard's foot flange is 2° off
    square to it: levelled on the flange it still leaned); a floor that disagrees wins (a figure's arm or staff can
    fake a lean in the slabs). Identity when there is nothing to do."""
    from scipy.spatial import ConvexHull, QhullError

    from steps._templates import _turn

    up = np.array([0.0, 1.0, 0.0])
    found = None
    try:
        hull = ConvexHull(v)
        if (down := hull.equations[:, 1] <= -np.cos(np.radians(30))).any():
            tri = v[hull.simplices[down]]
            area = 0.5 * np.linalg.norm(
                np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1
            )
            found = _floor(v, -hull.equations[down, :3], area)
    except QhullError:
        pass
    if found is None and f is not None and len(f):
        tri = v[f]
        cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        area = 0.5 * np.linalg.norm(cross, axis=1)
        normal = (
            -cross / np.maximum(2 * area, 1e-12)[:, None]
        )  # into the model, as the hull's floor normals
        down = (normal[:, 1] >= np.cos(np.radians(30))) & (area > 0)
        if down.any():
            found = _floor(v, normal[down], area[down], tri[down].mean(1))
    shaft = _shaft(v)
    # the shaft stands a post with no floor, or refines a floor within 3° of it (the bollard's flange, 2° off
    # square); a figure's slabs can fake a lean (an arm, a staff), so a floor that disagrees wins
    if shaft is not None and (
        found is None or np.degrees(np.arccos(min(abs(found[0] @ shaft), 1.0))) <= 3
    ):
        return _turn(shaft, up).T, float(np.degrees(np.arccos(shaft[1])))
    if found is None and f is not None and len(f) and (up_axis := _box_up(v, f)) is not None:
        found = (
            up_axis,
            _turn(up_axis, up).T,
            float(np.degrees(np.arccos(min(up_axis[1], 1.0)))),
        )  # a box's sides
    if found is None:
        return np.eye(3), 0.0
    _, m, tilt = found
    return (m, tilt) if 3 <= tilt <= 45 else (np.eye(3), 0.0)


def _shaft(v: np.ndarray) -> np.ndarray | None:
    """The up axis of a tall thin model that leans, for straighten (the bollard, agent 2026-10-13: a 0.24 m
    foot under a 1 m post leaning 11° covers far less than 60 % of its 0.41 m footprint, so no floor):
    a line through the box centres of ten horizontal slabs over the middle third of its height (a slab of a
    leaning post is centred on its axis; a lamp's arm or a sign at the top stays out of it). None when it
    leans under 3° or over 45°, or when, stood on that axis, the model is not over twice as tall as it is
    wide (every squat prop keeps the floor rule)."""
    from steps._templates import _turn

    slab = ((v[:, 1] - v[:, 1].min()) / max(np.ptp(v[:, 1]), 1e-12) * 30).astype(int)
    s = [v[slab == k] for k in range(10, 20) if (slab == k).any()]
    c = np.array([(x.min(0) + x.max(0)) / 2 for x in s])  # midrange: indifferent to vertex density
    if len(c) < 3:
        return None
    dx, dz = np.polyfit(c[:, 1], c[:, [0, 2]], 1)[0]
    n = np.array([dx, 1.0, dz]) / np.linalg.norm([dx, 1.0, dz])
    up = v @ _turn(n, np.array([0.0, 1.0, 0.0])).T
    if (
        not 3 <= np.degrees(np.arccos(n[1])) <= 45
        or np.ptp(up[:, 1]) <= 2 * np.ptp(up[:, [0, 2]], 0).max()
    ):
        return None
    return n


def _square(xz: np.ndarray) -> float | None:
    """The turn (degrees in [-45, 45)) that squares the footprint's smallest bounding rectangle to the axes;
    None for a roundish footprint the rectangle does not fit like a box (a long one has a direction whatever
    its ends: a wall with tapered ends is 27 % over its hull)."""
    import trimesh
    from scipy.spatial import ConvexHull

    t, rect = trimesh.bounds.oriented_bounds_2D(xz)
    # the 2D hull's "volume" is its area
    if rect.max() < 1.5 * rect.min() and rect.prod() > 1.15 * ConvexHull(xz).volume:
        return None
    return (np.degrees(np.arctan2(t[1, 0], t[0, 0])) + 45) % 90 - 45


def _generated(version: Path) -> bool:
    """Whether the version descends from a generated model, the generator's volume (ovoxel) up its chain, as the UI's
    `generated` reads it: imported models keep the placement their author gave them (a retexture of one too)."""
    while (meta := version / "meta.json").is_file():
        m = json.loads(meta.read_text(encoding="utf-8"))
        if "ovoxel" in m.get("outputs", {}):
            return True
        if not m.get("parent"):
            return False
        version = version.parent / m["parent"]
    return False


def press_to_floor(v: np.ndarray, f: np.ndarray) -> tuple[np.ndarray, dict | None]:
    """(v, what was pressed) with whatever hangs just below a broad flat base pressed flat onto it (the owner's
    agent, 2026-10-13, findings 15 and 19: the barrier block's lifting eye came back mirrored underneath and hung
    3.8-5.2 cm below its base, so once grounded the block stood on the loop). The base: the faces facing straight
    down (within 8°) near the bottom (the 5th percentile of the heights, plus 3 % of the height), at their
    area-weighted height, covering 35 % of the footprint (the levelling's rule: a chair's feet are no such base, and
    nothing is pressed). Only a small part under the middle of the base moves (its plan at most a quarter of the
    footprint, within 60 % of the base's radius from its centre: a rim at the outline is the model's edge, a barrel's
    staves hang 1.4 cm under its sunken head) hanging at most a tenth of the model's height below the base: anything deeper is a part of
    the model. `v` stands upright (Y up). None when nothing was pressed."""
    import trimesh

    tri = v[f]
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area = 0.5 * np.linalg.norm(cross, axis=1)
    centre = tri.mean(1)
    height = np.ptp(v[:, 1])
    low = np.percentile(v[:, 1], 5) + 0.03 * height
    base = (-cross[:, 1] >= np.cos(np.radians(8)) * 2 * area) & (area > 0) & (centre[:, 1] <= low)
    footprint = float(np.prod(trimesh.bounds.oriented_bounds_2D(v[:, [0, 2]])[1]))
    if area[base].sum() < 0.35 * footprint:
        return v, None
    floor = float((centre[base, 1] * area[base]).sum() / area[base].sum())
    # below the base's own unevenness (a generated floor is not flat: half of it lies under its mean)
    spread = np.sqrt((((centre[base, 1] - floor) ** 2) * area[base]).sum() / area[base].sum())
    hang = (v[:, 1] < floor - max(0.005 * height, 2 * spread)) & (v[:, 1] >= floor - 0.1 * height)
    # a small part under the base, not a rim round it: a barrel's staves hang 1.4 cm under its sunken head and
    # a lamp's pedestal edge 3.2 cm, both all round the outline, and stay
    span = np.ptp(v[hang][:, [0, 2]], 0).prod() if hang.sum() > 2 else 0.0
    # and under the middle of the base, not at its edge: within 60 % of the base's radius (the radius of a disc of its
    # area) from its centre. Tilted, a barrel's rim hangs on one side only, a small part, but out at the edge
    mid = (centre[base][:, [0, 2]] * area[base, None]).sum(0) / area[base].sum()
    reach = np.sqrt(area[base].sum() / np.pi)
    inside = (
        hang.any() and float(np.linalg.norm(v[hang][:, [0, 2]] - mid, axis=1).max()) <= 0.6 * reach
    )
    if not inside or span > 0.25 * footprint:
        return v, None
    deepest = float(floor - v[hang, 1].min())
    v = v.copy()
    v[hang, 1] = floor
    return v, {"vertices": int(hang.sum()), "depth_cm": round(deepest * 100, 1)}


def drop_floating(v: np.ndarray, f: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict | None]:
    """(v, f, what was dropped) without the small parts floating off a model that is otherwise one solid (the owner's
    agent, 2026-10-13, finding 19: a bottle bank's 13 cm rubber flap, 756 faces, 0.2 % of the surface, hung clear of
    its front, and nothing downstream drops it). A part goes when it holds under 0.5 % of the surface, lies over 1 % of
    the diagonal clear of the body (the one part over 0.5 %) and has its middle outside the body's convex hull. Each
    test keeps something the library has: a crate's corner trim and a figure's face piece touch the body, a
    lantern's flame and a lamp column's collar ring float inside it, a drone's rotor blades are pieces beside several
    large parts. Of the library's 123 generated cleans, besides the bank it took two scraps floating beside an anime
    figure's hands and one speck (14-54 faces) off three others. None when nothing went."""
    import trimesh
    from scipy.spatial import ConvexHull, QhullError, cKDTree

    tri = v[f]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    vlab = components(len(v), f)
    lab = vlab[f[:, 0]]
    share = np.bincount(lab, weights=area, minlength=len(v)) / area.sum()
    big, small = np.flatnonzero(share >= 0.005), (share > 0) & (share < 0.005)
    if len(big) != 1 or not small.any():
        return v, f, None
    try:
        eq = ConvexHull(v[np.unique(f[lab == big[0]])]).equations
    except QhullError:  # a flat body has no outside to float off
        return v, f, None
    # the gap to the body's surface, not only its vertices: on a coarse model a piece lying on a large face is
    # near none of them. Samples every 0.5 % of the diagonal or closer (capped at 200k), against a gap of 1 %
    body = trimesh.Trimesh(v, f[lab == big[0]], process=False)
    diag = float(np.linalg.norm(np.ptp(v, 0)))
    n = int(min(2e5, 4 * body.area / (0.005 * diag) ** 2))
    surface = np.r_[v[np.unique(body.faces)], trimesh.sample.sample_surface(body, n, seed=0)[0]]
    other = np.flatnonzero(small[vlab])
    gap = np.full(len(v), np.inf)
    np.minimum.at(gap, vlab[other], cKDTree(surface).query(v[other])[0])
    count = np.bincount(vlab[other], minlength=len(v))
    mid = np.stack([np.bincount(vlab[other], v[other, k], len(v)) for k in range(3)], 1)
    outside = (mid / np.maximum(count, 1)[:, None] @ eq[:, :3].T + eq[:, 3]).max(1) > 0
    drop = small & (gap > 0.01 * diag) & outside
    if not drop.any():
        return v, f, None
    gone = drop[lab]
    used, f = np.unique(f[~gone], return_inverse=True)
    return (
        v[used],
        f.reshape(-1, 3),
        {
            "parts": int(drop.sum()),
            "faces": int(gone.sum()),
            "surface_pct": round(100 * float(share[drop].sum()), 2),
        },
    )


def base_transform(v, height_m: float | None) -> np.ndarray:
    """4×4 putting the base centre at the origin and, if asked, scaling to height_m (library frame, Y up)."""
    lo, hi = v.min(0), v.max(0)
    s = height_m / (hi[1] - lo[1]) if height_m else 1.0
    m = np.diag([s, s, s, 1.0])
    m[:3, 3] = -s * np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2])
    return m


def solid_cleanup(v: np.ndarray, f: np.ndarray, out: Path, res: int = 400):
    """(vertices, faces, info) of the model as a clean solid: its voxel solid at `res` along the longest side
    (cracks up to ~2 voxels closed, the inside filled even through openings: remesh.solid_hull with fill), the
    voxel steps rounded off by Blender (remesh._filled), then edge-aware denoised (_mesh.denoise_surface: bends
    under ~60° smoothed, creases kept) and its largest part kept.
    The rebuild alone leaves a generated model as a hollow double skin (two layers 0.1 mm apart, joined where the
    surface is open, so drop_enclosed_parts keeps the inner one); frayed rims, cuts and specks (cracks, not noise)
    go too. About a minute at 400."""
    import time

    import trimesh

    from steps._mesh import denoise_surface
    from steps.remesh import _filled, solid_hull

    t0, info = time.time(), {}
    hull = solid_hull(v, f, res=res, close=1, fill=True, info=info)
    sv, sf = _filled(hull, info["pitch"], out)
    sv = denoise_surface(sv, sf, 20, 20, sin2=0.8)
    m = max(
        trimesh.Trimesh(sv, sf, process=False).split(only_watertight=False), key=lambda x: x.area
    )
    return (
        np.asarray(m.vertices),
        np.asarray(m.faces),
        {
            "resolution": res,
            "voxel_mm": round(info["pitch"] * 1000, 2),
            "filled_gap_cm": info["filled_gap_cm"],
            "hi_faces": len(m.faces),
            "volume_l": round(abs(float(m.volume)) * 1000, 3),
            "watertight": bool(m.is_watertight),
            "seconds": round(time.time() - t0, 1),
        },
    )


def run(op, in_dir, out_dir, params):
    if not in_dir:
        raise StepError("missing_input", "clean needs a parent version with a mesh")
    meta = json.loads((Path(in_dir) / "meta.json").read_text(encoding="utf-8"))
    glb = next((f for f in meta["outputs"].values() if f.endswith(".glb")), None)
    if not glb:
        raise StepError("missing_input", f"{meta['version']} has no .glb")
    v, f = weld(*read_glb(Path(in_dir) / glb)[:2])
    # a generated model stands upright by default (the owner, 2026-10-13); straighten also squares it to the axes
    level, floating, cleanup = (
        params.get(k, "auto") for k in ("level", "drop_floating", "cleanup")
    )
    if not isinstance(params.get("press_to_floor", False), bool):
        raise StepError("bad_params", "press_to_floor: true or false")
    if any(x not in (True, False, "auto") for x in (level, floating, cleanup)):
        raise StepError(
            "bad_params",
            'level, drop_floating and cleanup: true, false or "auto" (on for a generated model)',
        )
    if "auto" in (level, floating, cleanup):
        generated = _generated(Path(in_dir))
        level, floating, cleanup = (
            generated if x == "auto" else x for x in (level, floating, cleanup)
        )
    diag = float(np.linalg.norm(v.max(0) - v.min(0)))
    before = mesh_report(v, f)
    rebuild = params.get("rebuild", "auto")
    if rebuild == "auto":
        rebuild = (
            before["non_manifold_edges"] > 0.01 * 1.5 * before["faces"]
        )  # ≈ 1.5 edges per face
    progress(0.2, f"{'rebuilding' if rebuild else 'repairing'} {len(f)} faces")
    v, f, dropped = _cumesh_clean(
        v,
        f,
        params.get("hole", 0.03) * diag,
        params.get("min_component", 1e-5) * diag**2,
        bool(rebuild),
        int(params.get("rebuild_resolution", 512)),
    )
    floated = None
    # before the levelling: a flap off the front is no part of the model's floor or hull
    if floating:
        v, f, floated = drop_floating(v, f)
    straight, m = None, np.eye(3)
    if params.get("straighten", False):
        m, tilt, yaw = straighten_transform(v, f)
        straight = {"tilt_deg": tilt, "yaw_deg": yaw}
    elif level:
        m, tilt = level_transform(v, f)
        straight = {"tilt_deg": round(tilt, 1)} if tilt else None
    v = v @ m
    pressed = None
    if params.get(
        "press_to_floor", False
    ):  # what hangs just under the base, pressed onto it (then grounded on it)
        v, pressed = press_to_floor(v, f)
    t = base_transform(v, params.get("height_m"))
    v = v @ t[:3, :3].T + t[:3, 3]
    # one transform back to the parent's frame, the levelling included (bake follows it)
    t[:3, :3] = t[:3, :3] @ m.T
    outputs = {"mesh": "mesh.glb", "report": "report.json"}
    cleaned = None
    if cleanup:
        import trimesh

        from steps.remesh import _cumesh_simplify

        res = int(params.get("cleanup_resolution", 400))
        progress(0.7, f"cleaning up: a solid at {res} voxels, denoised")
        hv, hf, cleaned = solid_cleanup(v, f, Path(out_dir), res)
        # bake reads its normals off this one (the raw generator surface keeps the colour positions)
        trimesh.Trimesh(hv, hf, process=False).export(Path(out_dir) / "hi_clean.ply")
        outputs["hi_clean"] = "hi_clean.ply"
        v, f = (hv, hf) if len(hf) <= len(f) else _cumesh_simplify(hv, hf, len(f), True)
    write_glb(Path(out_dir) / "mesh.glb", v, f)
    rep = mesh_report(v, f)
    (Path(out_dir) / "report.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    metrics = {
        "faces": rep["faces"],
        "watertight": rep["watertight"],
        "components": rep["components"],
        "rebuilt": bool(rebuild),
        "enclosed_dropped": dropped,  # parts inside another (the rebuild's inner copy, bubbles)
        "non_manifold_in": before["non_manifold_edges"],
    }
    if straight:
        metrics["straightened" if params.get("straighten") else "levelled"] = straight
    if pressed:
        metrics["pressed"] = pressed
    if floated:
        metrics["dropped_parts"] = floated
    if cleaned:
        metrics["cleanup"] = cleaned
    if not np.allclose(t, np.eye(4)):
        metrics["transform"] = t.round(9).tolist()
    return {"outputs": outputs, "metrics": metrics}


if __name__ == "__main__":
    main(run)
