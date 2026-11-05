"""Re-bake (IMPLEMENTATION.md M3): full PBR + a normal map for any UV-mapped version, from the volume (M62: or, for an import, its own materials).

params: {texture_size: 2048, normal_map: true, supersample: 2, ao: true, detaper: false, hi_denoise: 6,
normal_smooth: 0, ao_rays: 256, ao_smooth: 2.0}
`hi_denoise` (2026-11-04): rounds of edge-aware smoothing of the hi mesh's normals (bends under ~30° smoothed, creases
kept), its loose crumbs dropped first; 0: off. `normal_smooth`, `ao_smooth`: a Gaussian over the surface (sigma in
texels at texture_size; 0: off), seam-free as it blurs in 3D (`surface_blur`). A texel's hi normal comes from the
closest hi point, or from a short ray along this mesh's normal when that surface faces it better (the far side of a
thin part); with a cleaned solid up the chain (clean's hi_clean.ply) from that, the colour still from the raw mesh.
`detaper` (2026-10-12): a flat prop's perspective taper undone after the sampling (the texture is read on the
shape the picture gave, then the shape is squared: steps/_mesh.detaper; `metrics.detapered`).
The input version's mesh.glb (library frame) is mapped back into the TRELLIS frame through the chain's
recorded frame and transforms; each texel is projected onto the closest point of hi_trellis.ply (CuMesh
BVH), samples the volume there and takes that surface's normal (turned to this mesh's side: the hi mesh's
winding is random) into this mesh's tangent space (OpenGL +Y). Without CUDA nothing is projected (tests, CPU runs).
M43a: the raster is `supersample`× the size and box-filtered down; AO (hemisphere rays on the hi BVH) goes
into ORM R and the GLB's occlusionTexture (GPU only; CPU writes R = 255); UV seams are colour-levelled.
M62: with no ovoxel up the chain (an imported model) the colour comes from the model's own materials: the nearest
version up the chain whose glb carries textures (this one first), else the chain's root import/upload. Each texel
takes the closest point of that source (steps/_bake.bake_source); same outputs, so nothing downstream changes.
Ceilings: vertex colours, emissive, source AO, KHR_texture_transform, TEXCOORD_1 and sampler wrap modes are not read.
"""

import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from steps._common import StepError, library_to_trellis, load_ovoxel, main, progress
from steps._mesh import detaper, smooth_normals, textured, uv_overlap


def find_source(
    version_dir: Path, hit, what: str = "no ovoxel.npz up the chain of"
) -> tuple[Path, np.ndarray, dict]:
    """The nearest version (self first) where hit(dir, meta), its meta, and the 4×4 taking its frame to this
    version's, composed from the chain's recorded transforms. `what` words the missing_input when none hits."""
    transforms, d = [], version_dir
    while True:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        if hit(d, meta):
            m = np.eye(4)
            for t in reversed(transforms):  # transforms recorded later in the chain apply last
                m = t @ m
            return d, m, meta
        if t := meta.get("metrics", {}).get("transform"):
            transforms.append(np.asarray(t, float))
        if not meta["parent"]:
            raise StepError("missing_input", f"{what} {version_dir.name}")
        d = d.parent / meta["parent"]


def find_volume(version_dir: Path) -> tuple[Path, np.ndarray]:
    """The nearest ancestor (or self) with ovoxel.npz, and the 4×4 TRELLIS → this version's library frame."""
    d, m, meta = find_source(version_dir, lambda _, meta: "ovoxel" in meta["outputs"])
    return d, m @ np.asarray(meta["frame"]["to_library_from_source"], float)


def _main_glb(meta: dict) -> str | None:
    o = meta["outputs"]
    return next((o[k] for k in ("mesh", "model") if o.get(k, "").endswith(".glb")), None) or next(
        (f for f in o.values() if f.endswith(".glb")), None
    )


def load_source(d: Path, meta: dict, m: np.ndarray, dev):
    """The source glb's meshes merged, in the bake mesh's frame, as the arrays bake_source reads."""
    import torch
    import trimesh

    parts = trimesh.load(d / _main_glb(meta), force="scene", process=False).dump()
    mats = [getattr(g.visual, "material", None) for g in parts]
    if all(x is None for x in mats) or not _main_glb(meta):
        raise StepError("missing_input", f"nothing to bake from: {d.name} has no materials")
    v, f, uv, n, fmat, off = [], [], [], [], [], 0
    for k, g in enumerate(parts):
        u = getattr(g.visual, "uv", None)
        u = np.zeros((len(g.vertices), 2)) if u is None else np.array(u, float)
        u[:, 1] = 1 - u[:, 1]  # trimesh's OpenGL UVs → glTF UVs
        v.append(g.vertices)
        f.append(g.faces + off)
        uv.append(u)
        n.append(g.vertex_normals)
        fmat.append(np.full(len(g.faces), k))
        off += len(g.vertices)
    v, f, uv, n, fmat = (np.concatenate(x) for x in (v, f, uv, n, fmat))
    v = v @ m[:3, :3].T + m[:3, 3]
    n = n @ np.linalg.inv(m[:3, :3])
    t = lambda a, dt=torch.float32: torch.tensor(a, dtype=dt, device=dev)
    s = type("Source", (), {})()
    s.mats = [x or trimesh.visual.material.PBRMaterial(metallicFactor=0.0) for x in mats]
    s.fmat, s.uv = t(fmat, torch.long), t(uv)
    s.v, s.f, s.n = t(v), t(f, torch.long), torch.nn.functional.normalize(t(n), dim=1)
    s.tan, s.sign = vertex_frames(s.v, s.f, s.uv, s.n)
    return s


def vertex_frames(v, f, uv, n):
    """Per-vertex tangents and bitangent signs from UVs: _bake.vertex_tangents, the frame the GLB's TANGENT holds."""
    import torch

    from steps._bake import vertex_tangents

    v, uv, n = (x.detach().cpu().numpy().astype(np.float64) for x in (v, uv, n))
    t = vertex_tangents(v, f.detach().cpu().numpy(), uv, n)
    t = torch.from_numpy(t).to(f.device)
    return t[:, :3], t[:, 3]


def run(op, in_dir, out_dir, params):
    import torch
    import trimesh

    from steps._bake import (
        ao_faces,
        ao_texels,
        bake_source,
        bake_volume,
        box_filter,
        fill_dark_islands,
        fill_gutters,
        heal_seams,
        rasterize_uv,
        seam_delta,
        seams,
        write_glb,
    )

    if not in_dir:
        raise StepError("missing_input", "bake needs a parent version with a UV-mapped mesh.glb")
    size = int(params.get("texture_size", 2048))
    # 4096 bakes without supersampling unless asked
    ss = int(params.get("supersample", 1 if size == 4096 else 2))
    if size not in (256, 512, 1024, 2048, 4096) or ss not in (1, 2):
        raise StepError("bad_params", f"bad bake params: {params}")
    # ponytail: 8192² raster unmeasured on 16 GB; lift when a run shows it fits
    if size * ss > 4096:
        raise StepError(
            "bad_params",
            f"texture_size {size} × supersample {ss}: the {size * ss}² raster is over budget",
        )
    src = Path(in_dir)
    meta = json.loads((src / "meta.json").read_text(encoding="utf-8"))
    glb = next((f for f in meta["outputs"].values() if f.endswith(".glb")), None)
    if not glb:
        raise StepError("missing_input", f"{meta['version']} has no .glb")
    mesh = trimesh.load(src / glb, force="mesh", process=False)
    if getattr(mesh.visual, "uv", None) is None:
        raise StepError("missing_input", f"{glb} has no UVs: run the uv step first")
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        vol_dir, frame = find_volume(src)
    except (
        StepError
    ):  # no generator volume up the chain: an import, its own materials are the colour
        vol_dir = None
    if vol_dir:
        vol, source = load_ovoxel(vol_dir / "ovoxel.npz"), None
    else:
        vol = None
        # an import_model version is a source even without textures (factor-only colours): it also fixes the frame,
        # which the raw upload before it does not share (import scales and grounds the model)
        d, sm, smeta = find_source(
            src,
            lambda d, m: (
                m.get("step") == "import_model"
                or ((g := _main_glb(m)) is not None and textured(d / g))
            ),
            "nothing to bake from: no generator volume or imported model up the chain of",
        )
        if d == src:  # the bake's own UVs are the texture's: they must be a clean atlas
            u = np.asarray(mesh.visual.uv)
            if u.min() < -1e-4 or u.max() > 1 + 1e-4 or uv_overlap(u, mesh.faces) > 0.01:
                raise StepError("missing_input", "the model's UVs overlap or tile: run uv first")
        source = load_source(d, smeta, sm, dev)
        vol_dir, frame = d, np.eye(4)

    progress(0.1, "mapping the mesh into the source's frame")
    to_trellis = library_to_trellis(frame)
    v = torch.tensor(
        mesh.vertices @ to_trellis[:3, :3].T + to_trellis[:3, 3], dtype=torch.float32, device=dev
    )
    f = torch.tensor(mesh.faces, dtype=torch.long, device=dev)
    uv = torch.tensor(mesh.visual.uv, dtype=torch.float32, device=dev)
    uv[:, 1] = 1 - uv[:, 1]  # trimesh's OpenGL UVs → glTF UVs (what the file holds)
    n = torch.tensor(
        smooth_normals(mesh.vertices, mesh.faces) @ np.linalg.inv(to_trellis[:3, :3]),
        dtype=torch.float32,
        device=dev,
    )
    n = torch.nn.functional.normalize(n, dim=1)

    def texels(s):  # the raster at s² and each covered texel's position and normal on this mesh
        face_id, bary = rasterize_uv(uv, f, s)
        corners, w = f[face_id[face_id >= 0]], bary[face_id >= 0][..., None]
        return (
            face_id,
            bary,
            corners,
            w,
            (v[corners] * w).sum(1),
            torch.nn.functional.normalize((n[corners] * w).sum(1), dim=1),
        )

    progress(0.2, f"rasterizing {size * ss}² texels")
    face_id, bary, corners, w, pts, nrm = texels(size * ss)
    mask = face_id >= 0
    hi, hi_n, project, p, normals_from, clean = None, nrm, None, None, None, None
    if source is not None:
        progress(0.5, "sampling the source's materials")
        hit, w_hit = _closest(_Hi(source.v, source.f, source.n), pts)
        rgba, mr, m, hi_n = bake_source(source, hit, w_hit, nrm, mask, ss)
        del hit, w_hit
    else:
        if dev == "cuda":
            rounds = int(params.get("hi_denoise", 6))
            clean = _clean_hi(src, to_trellis, rounds)
            # colour positions on the raw surface (the volume's); normals off Clean's solid when there is one
            hi = _Hi(*_load_ply(vol_dir / "hi_trellis.ply", 0 if clean else rounds))
            hi_n, p = _project(hi, pts, nrm)
            if clean is not None:
                progress(0.45, f"normals from {clean[0]}'s cleaned surface")
                hi_n, _ = _project(clean[1], pts, nrm)
                normals_from = clean[0]
            project = lambda _, p=p: p
        progress(0.5, "sampling the volume")
        rgba, mr, m = bake_volume(
            face_id,
            bary,
            v,
            f,
            torch.from_numpy(vol["coords"]),
            torch.from_numpy(vol["attrs"]).float(),
            vol["layout"],
            vol["resolution"],
            project,
            ss,
        )
    normal_map = None
    if params.get("normal_map", True):
        if sigma := float(params.get("normal_smooth", 0)) * texel_size(v, f, uv, size):
            hi_n = torch.nn.functional.normalize(surface_blur(pts, hi_n, hi_n, sigma), dim=1)
        tan, sign = vertex_frames(v, f, uv, n)
        t = torch.nn.functional.normalize((tan[corners] * w).sum(1), dim=1)
        t = torch.nn.functional.normalize(t - (t * nrm).sum(1, keepdim=True) * nrm, dim=1)
        b = torch.cross(nrm, t, dim=1) * (sign[corners] * w[..., 0]).sum(1, keepdim=True).sign()
        local = torch.stack([(hi_n * t).sum(1), (hi_n * b).sum(1), (hi_n * nrm).sum(1)], 1)
        img = torch.zeros(size * ss, size * ss, 3, device=dev)
        img[..., 2] = 1  # flat where no texel landed
        img[mask] = local
        img, covered = box_filter((img * 0.5 + 0.5).mul(255).cpu().numpy(), mask.cpu().numpy(), ss)
        normal_map = fill_gutters((img + 0.5).clip(0, 255).astype(np.uint8), covered)
    del (
        pts,
        nrm,
        corners,
        w,
        hi_n,
        project,
        p,
        hi,
        clean,
    )  # the big per-texel tensors: AO needs the VRAM

    progress(0.7, "levelling the UV seams")
    uv_np = uv.cpu().numpy()
    seam = seams(mesh.vertices, mesh.faces, uv_np, size)
    before = seam_delta(rgba[..., :3], seam)
    rgba[..., :3] = fill_gutters(heal_seams(rgba[..., :3], m, seam), m)
    metrics = {
        "texture_size": size,
        "supersample": ss,
        "coverage": round(float(m.mean()), 3),
        "normal_mean": normal_map[m].mean(0).round(1).tolist() if normal_map is not None else None,
        "projected": dev == "cuda" or source is not None,
        "seam_delta_before": round(before, 3),
        "seam_delta": round(seam_delta(rgba[..., :3], seam), 3),
        "ao": False,
    }

    orm = mr.copy()
    orm[..., 0] = 255  # R = AO (none on the CPU), G = roughness, B = metallic
    if dev == "cuda" and params.get("ao", True) and len(f) > 8:  # cuBVH needs more than 8 triangles
        progress(0.8, "baking ambient occlusion")
        t0 = time.time()
        fid, _, _, _, p1, n1 = texels(size)
        m1 = fid >= 0
        # rays go against this (clean, remeshed) mesh, not the hi one: TRELLIS's hi mesh is a double shell
        # with interior layers, and rays from its inner layer hit the outer one (black blotches, M43a bench)
        import cumesh

        bvh = cumesh.cuBVH(v, f)
        diag = float((v.amax(0) - v.amin(0)).norm())
        voxel = 1 / vol["resolution"] if vol else diag / 1024
        # a few faces of the low mesh face inward (turned); an inner shell's faces are enclosed (hidden: no
        # occlusion); where a torn outer shell shows the inner one, the AO map holds a tiny dark island (filled)
        inward, enclosed = ao_faces(v, f, n, bvh, voxel)
        n1[inward[fid[m1]]] *= -1
        ao = ao_texels(bvh, p1, n1, voxel, 0.1 * diag, rays=int(params.get("ao_rays", 256)))
        # 32 rays a texel left a visible grain: more rays, then a seam-free blur over the surface
        if sigma := float(params.get("ao_smooth", 2.0)) * texel_size(v, f, uv, size):
            ao = surface_blur(p1, n1, ao[:, None], sigma)[:, 0]
        hidden = enclosed[fid[m1]]
        ao[hidden] = 1
        img = torch.ones(size, size, device=dev)
        img[m1] = ao
        img, filled = fill_dark_islands(img.cpu().numpy(), m1.cpu().numpy())
        ao_img = fill_gutters((img * 255 + 0.5).astype(np.uint8)[..., None], m1.cpu().numpy())
        orm[..., 0] = ao_img[..., 0]
        metrics |= {
            "ao": True,
            "ao_mean": round(float(ao.mean()), 3),
            "ao_enclosed": round(float(hidden.float().mean()), 3),
            "ao_filled": round(float(filled), 3),
            "ao_seconds": round(time.time() - t0, 1),
        }
    if dev == "cuda":
        metrics["vram_peak_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 2)

    progress(0.9, "writing mesh.glb")
    out = Path(out_dir)
    Image.fromarray(rgba, "RGBA").save(out / "basecolor.png")
    Image.fromarray(orm, "RGB").save(out / "orm.png")
    outputs = {"mesh": "mesh.glb", "basecolor": "basecolor.png", "orm": "orm.png"}
    if normal_map is not None:
        Image.fromarray(normal_map, "RGB").save(out / "normal.png")
        outputs["normal"] = "normal.png"
    verts, normals = mesh.vertices, smooth_normals(mesh.vertices, mesh.faces)
    if params.get(
        "detaper"
    ):  # after the sampling: the texture was read on the shape the picture gave
        verts, taper = detaper(np.asarray(mesh.vertices, np.float64))
        metrics["detapered"] = taper  # null: not a flat panel along X or Z, or no taper to undo
        if taper:
            normals = smooth_normals(verts, mesh.faces)
    opaque = source is None or all(
        getattr(x, "alphaMode", None) in (None, "OPAQUE") for x in source.mats
    )
    write_glb(
        out / "mesh.glb",
        verts,
        mesh.faces,
        uv_np,
        rgba,
        orm,
        normals,
        normal_map,
        occlusion=metrics["ao"],
        alpha_mode=None if opaque else "MASK",
    )
    if source is None:
        origin = {"volume_from": vol_dir.name}
        if normals_from:
            origin["normals_from"] = normals_from
    else:
        origin = {"source_from": vol_dir.name, "source_materials": len(source.mats)}
    return {"outputs": outputs, "metrics": metrics | origin}


class _Hi:
    """A surface to project onto: vertices, faces, vertex normals; its BVH on the GPU (cuBVH needs > 8 faces)."""

    def __init__(self, v, f, n):
        import torch

        dev = "cuda" if torch.cuda.is_available() else "cpu"
        self.v, self.f, self.n = (x.to(dev) for x in (v, f, n))
        self.bvh = None
        if dev == "cuda":
            import cumesh

            if (
                len(f) <= 8
            ):  # cuBVH needs more: pad with far, tiny triangles that are never the closest
                far = self.v.new_tensor(
                    [[1e6, 1e6, 1e6], [1e6 + 1e-3, 1e6, 1e6], [1e6, 1e6 + 1e-3, 1e6]]
                )
                v = torch.cat([self.v, far])
                f = torch.cat([self.f, (self.f.new_tensor([0, 1, 2]) + len(self.v)).expand(9, 3)])
                self.bvh = cumesh.cuBVH(v, f)
            else:
                self.bvh = cumesh.cuBVH(self.v, self.f)


def _clean_hi(version_dir: Path, to_trellis: np.ndarray, rounds: int):
    """(its version's name, _Hi) of the nearest hi_clean.ply up the chain (Clean's cleaned solid, high-res) in the
    TRELLIS frame, its normals smoothed `rounds` more (bake's hi_denoise); None when the chain has none (an import, an older Clean). The raw generator mesh still gives the colour positions: the volume is
    aligned to it, the solid lies up to a voxel off (sampling there read empty voxels: rings)."""
    import torch
    import trimesh

    try:
        d, m, meta = find_source(
            version_dir,
            lambda _, meta: "hi_clean" in meta["outputs"],
            "no hi_clean.ply up the chain of",
        )
    except StepError:
        return None
    hi = trimesh.load(d / meta["outputs"]["hi_clean"], process=False)
    t = to_trellis @ m  # that version's frame → this one's → TRELLIS
    v = torch.tensor(np.asarray(hi.vertices) @ t[:3, :3].T + t[:3, 3], dtype=torch.float32)
    f = torch.tensor(np.asarray(hi.faces), dtype=torch.long)
    n = axis_normals(v, f)
    return d.name, _Hi(v, f, denoise_normals(v, f, n, rounds) if rounds > 0 else n)


def _load_ply(path: Path, denoise: int = 0):
    """(vertices, faces, vertex normals) of the hi mesh; with `denoise` > 0 its loose crumbs dropped and its
    normals smoothed that many rounds, edge-aware (bake's `hi_denoise`)."""
    import torch
    import trimesh

    hi = trimesh.load(path, process=False)
    v = torch.tensor(hi.vertices, dtype=torch.float32)
    f = torch.tensor(hi.faces, dtype=torch.long)
    if denoise > 0:
        f = drop_crumbs(v, f)
    n = axis_normals(v, f)
    return v, f, denoise_normals(v, f, n, denoise) if denoise > 0 else n


def drop_crumbs(v, f, share: float = 5e-5):
    """`f` without the faces of the parts under `share` of the whole area: the loose crumbs a generated mesh carries
    (its real small parts are far bigger)."""
    import scipy.sparse as sp
    import torch
    from scipy.sparse.csgraph import connected_components

    vn, fn = v.numpy().astype(np.float64), f.numpy()
    g = sp.coo_matrix(
        (np.ones(2 * len(fn), bool), (np.r_[fn[:, 0], fn[:, 1]], np.r_[fn[:, 1], fn[:, 2]])),
        shape=(len(vn), len(vn)),
    )
    k, label = connected_components(g, directed=False)
    a, b, c = (vn[fn[:, i]] for i in range(3))
    area = np.linalg.norm(np.cross(b - a, c - a), axis=1)
    part = np.bincount(label[fn[:, 0]], weights=area, minlength=k)
    return f[torch.from_numpy(part[label[fn[:, 0]]] >= share * area.sum())]


def denoise_normals(v, f, n, rounds: int, sin2: float = 0.2, chunk: int = 1 << 22):
    """Edge-aware smoothing of vertex normals whose signs are arbitrary (axis_normals): each round a vertex takes
    the main axis of its own and its neighbours' n nᵀ, a neighbour weighted exp(-sin²θ / sin2) by the angle θ
    between the two (sign free: n and -n are the same line). With sin2 0.2 a 15° bend weighs 0.72, 30° 0.29, 60°
    0.02: the generator's faceting and small bumps are smoothed, creases kept. A round reaches one ring further."""
    import torch

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    f, n = f.to(dev), n.to(dev)
    e = torch.cat([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]).sort(dim=1).values
    key = torch.unique(e[:, 0] * len(n) + e[:, 1])
    e0, e1 = key // len(n), key % len(n)
    del e, key
    for _ in range(rounds):
        acc = n[:, :, None] * n[:, None, :]
        for s in range(0, len(e0), chunk):
            i, j = e0[s : s + chunk], e1[s : s + chunk]
            a, b = n[i], n[j]
            wt = torch.exp(-(1 - (a * b).sum(1).square()) / sin2)[:, None, None]
            acc.index_add_(0, i, wt * b[:, :, None] * b[:, None, :])
            acc.index_add_(0, j, wt * a[:, :, None] * a[:, None, :])
        x = n  # power iteration from the old normal keeps its sign
        for _ in range(4):
            x = torch.nn.functional.normalize(torch.bmm(acc, x[:, :, None]).squeeze(2), dim=1)
        n = x
    return n.cpu()


def texel_size(v, f, uv, size: int) -> float:
    """The length on the mesh (its frame) one texel of a size² map covers: the median over the faces of
    √(3D area / UV area), divided by the map's size."""
    import torch

    tri, t = v[f], uv[f]
    a3 = torch.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0], dim=1).norm(dim=1)
    d1, d2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    a2 = (d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]).abs()
    ok = a2 > 1e-12
    return float(torch.sqrt(a3[ok] / a2[ok]).median()) / size if ok.any() else 0.0


def surface_blur(pts, normals, values, sigma: float):
    """A Gaussian (sigma in the mesh's units) of per-texel values [K,C] over the surface: each texel takes the texels
    near it in 3D whatever UV island they are in, so both sides of a seam blur alike, weighted 0 (60° or more) to 1
    (30° or less) by how their normals agree with its own (a thin part's far side stays out, a crease keeps its edge).
    Texels are pooled into cubes of side sigma, each reading the 27 around its own; one with no match keeps its value."""
    import torch

    q = torch.floor(pts / sigma).long()
    q -= q.min(0).values - 1  # ≥ 1: a neighbour one cube down stays ≥ 0
    span = int(q.max()) + 2

    def key(c):
        return (c[:, 0] * span + c[:, 1]) * span + c[:, 2]

    cubes, inv = torch.unique(key(q), return_inverse=True)
    dev, m = pts.device, len(cubes)
    total = values.new_zeros(m, values.shape[1]).index_add_(0, inv, values)
    nsum = torch.zeros(m, 3, device=dev).index_add_(0, inv, normals)
    count = torch.zeros(m, device=dev).index_add_(0, inv, torch.ones_like(inv, dtype=pts.dtype))
    # ponytail: a cube's texels weighted at its centre, not one by one; per-texel pairs if that ever shows
    centre = torch.zeros(m, 3, device=dev).index_add_(0, inv, pts) / count[:, None]
    mean = torch.nn.functional.normalize(nsum, dim=1)
    out = torch.zeros(len(pts), values.shape[1], device=dev, dtype=values.dtype)
    weight = torch.zeros(len(pts), device=dev)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                k = key(q + q.new_tensor([dx, dy, dz]))
                i = torch.searchsorted(cubes, k).clamp(max=len(cubes) - 1)
                w = torch.exp(-((pts - centre[i]) ** 2).sum(1) / (2 * sigma * sigma))
                agree = ((mean[i] * normals).sum(1) - 0.5) / (0.87 - 0.5)  # 60° → 0, 30° → 1
                w = w * (cubes[i] == k) * agree.clamp(0, 1)
                out += w[:, None] * total[i]
                weight += w * count[i]
    return torch.where(weight[:, None] > 0, out / weight.clamp_min(1e-12)[:, None], values)


def axis_normals(v, f, chunk: int = 1 << 21):
    """Per-vertex normals that ignore winding: the main axis of the area-weighted outer products n nᵀ of the
    faces around each vertex, sign arbitrary (_project turns each corner to the low mesh's side). A TRELLIS hi
    mesh's winding is random: summed, as trimesh's vertex_normals does, neighbours cancel and every vertex gets a
    near-random normal, an even grain all over the baked map."""
    import torch

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    v, f = v.to(dev), f.to(dev)
    acc = torch.zeros(len(v), 3, 3, device=dev)
    for s in range(0, len(f), chunk):
        c = f[s : s + chunk]
        n = torch.cross(v[c[:, 1]] - v[c[:, 0]], v[c[:, 2]] - v[c[:, 0]], dim=1)  # |n| = 2 × area
        outer = n[:, :, None] * n[:, None, :] / n.norm(dim=1).clamp_min(1e-30)[:, None, None]
        for k in range(3):
            acc.index_add_(0, c[:, k], outer)
    # the dominant eigenvector by power iteration (cuSOLVER's batched eigh refuses millions of 3×3 at once),
    # started from the matrix's longest column: on a surface the normal's eigenvalue dwarfs the other two
    x = acc.gather(2, acc.norm(dim=1).argmax(1)[:, None, None].expand(-1, 3, 1)).squeeze(2)
    for _ in range(12):
        x = torch.nn.functional.normalize(torch.bmm(acc, x[:, :, None]).squeeze(2), dim=1)
    return x.cpu()


def _closest(hi: _Hi, pts):
    """(face [P] long, barycentric weights [P,3]) of each point's closest point on the surface."""
    import torch

    if hi.bvh is not None:
        _, face, w = hi.bvh.unsigned_distance(pts, return_uvw=True)
        return face.long(), w
    # ponytail: approximate (nearest of up to 2M sampled surface points, then the barycentrics of the point on that
    # face), CPU runs only (no CUDA); the GPU path is exact. Upgrade: a brute-force triangle distance in torch.
    import trimesh
    from scipy.spatial import cKDTree

    m = trimesh.Trimesh(hi.v.cpu().numpy(), hi.f.cpu().numpy(), process=False)
    s, fi = trimesh.sample.sample_surface(m, min(max(100_000, 4 * len(pts)), 2_000_000), seed=0)
    q = pts.cpu().numpy()
    face = fi[cKDTree(s).query(q)[1]]
    w = trimesh.triangles.points_to_barycentric(m.triangles[face], q).clip(0)
    w /= w.sum(1, keepdims=True).clip(1e-12)
    return torch.from_numpy(face).long().to(pts.device), torch.from_numpy(w).float().to(pts.device)


def _project(hi: _Hi, pts, nrm):
    """GPU: the hi normal at, and the closest hi-surface point for, each texel, on the side of `nrm`.
    Not a cage ray-cast: TRELLIS meshes are a thin double shell, and rays from outside hit the outer
    layer ~4 voxels off the surface the low mesh follows, where the volume is empty (alpha 0, rings)."""
    import torch

    face, w = _closest(hi, pts)
    corners = hi.f[face]
    p = (hi.v[corners] * w[..., None]).sum(1)
    normals = _hi_normal(hi, corners, w, nrm)
    if hi.bvh is not None:
        # facing: a thin part has its far side within reach too, and the closest point can be there,
        # its normal at a steep angle to this surface's.
        # A short ray along this mesh's normal, either way, finds the near side; its normal is taken when it
        # faces this surface clearly better than the closest point's. The colour still comes from `p`.
        reach = (
            2 * (p - pts).norm(dim=1) + 2e-3
        )  # TRELLIS frame: the model about 1 across, a voxel 1/1024
        best = (normals * nrm).sum(1)
        for way in (1.0, -1.0):
            hit, fh, t = hi.bvh.ray_trace(pts, way * nrm)
            ok = (fh >= 0) & (t <= reach)
            if not ok.any():
                continue
            c = hi.f[fh.clamp_min(0)]
            nh = _hi_normal(hi, c, _barycentric(hi.v[c], hit), nrm)
            score = (nh * nrm).sum(1)
            take = ok & (score > best + 0.1)
            normals = torch.where(take[:, None], nh, normals)
            best = torch.where(take, score, best)
    return normals, p


def _hi_normal(hi: _Hi, corners, w, nrm):
    """The hi normal at barycentric `w` of the faces `corners` [K,3], on the side of `nrm`: axis_normals' signs
    are arbitrary, so each corner is turned to the texel's side before they are blended."""
    import torch

    cn = hi.n[corners]
    cn = torch.where((cn * nrm[:, None]).sum(-1, keepdim=True) < 0, -cn, cn)
    out = torch.nn.functional.normalize((cn * w[..., None]).sum(1), dim=1)
    return torch.where((out * nrm).sum(1, keepdim=True) < 0, -out, out)


def _barycentric(tri, p):
    """Barycentric weights [K,3] of points p [K,3] in triangles tri [K,3,3] (clipped to the triangle)."""
    import torch

    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    v0, v1, v2 = b - a, c - a, p - a
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
    den = (d00 * d11 - d01 * d01).clamp_min(1e-30)
    y = (d11 * d20 - d01 * d21) / den
    z = (d00 * d21 - d01 * d20) / den
    w = torch.stack([1 - y - z, y, z], 1).clamp_min(0)
    return w / w.sum(1, keepdim=True).clamp_min(1e-12)


if __name__ == "__main__":
    main(run)
