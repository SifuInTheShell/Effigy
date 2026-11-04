"""Re-bake (IMPLEMENTATION.md M3): full PBR + a normal map for any UV-mapped version, from the volume (M62: or, for an import, its own materials).

params: {texture_size: 2048, normal_map: true, supersample: 2, ao: true, detaper: false}
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
    """Per-vertex tangents and bitangent signs from UVs (area-weighted, Gram-Schmidt): ≈ MikkTSpace."""
    import torch

    p0, p1, p2 = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    t0, t1, t2 = uv[f[:, 0]], uv[f[:, 1]], uv[f[:, 2]]
    e1, e2, d1, d2 = p1 - p0, p2 - p0, t1 - t0, t2 - t0
    r = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
    r = torch.where(r.abs() < 1e-20, torch.full_like(r, 1e-20), r)
    du = (e1 * d2[:, 1:] - e2 * d1[:, 1:]) / r[:, None]  # ∂P/∂u
    up = (
        -(e2 * d1[:, :1] - e1 * d2[:, :1]) / r[:, None]
    )  # -∂P/∂v: glTF v runs down the image, "up" is -v
    tan, bit = torch.zeros_like(v), torch.zeros_like(v)
    for k in range(3):
        tan.index_add_(0, f[:, k], du)
        bit.index_add_(0, f[:, k], up)
    tan = torch.nn.functional.normalize(tan - (tan * n).sum(1, keepdim=True) * n, dim=1)
    sign = torch.where((torch.cross(n, tan, dim=1) * bit).sum(1) >= 0, 1.0, -1.0)
    return tan, sign


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
    hi, hi_n, project, p = None, nrm, None, None
    if source is not None:
        progress(0.5, "sampling the source's materials")
        hit, w_hit = _closest(_Hi(source.v, source.f, source.n), pts)
        rgba, mr, m, hi_n = bake_source(source, hit, w_hit, nrm, mask, ss)
        del hit, w_hit
    else:
        if dev == "cuda":
            hi = _Hi(*_load_ply(vol_dir / "hi_trellis.ply"))
            hi_n, p = _project(hi, pts, nrm)
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
    del pts, nrm, corners, w, hi_n, project, p, hi  # the big per-texel tensors: AO needs the VRAM

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
        ao = ao_texels(bvh, p1, n1, voxel, 0.1 * diag)
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


def _load_ply(path: Path):
    import torch
    import trimesh

    hi = trimesh.load(path, process=False)
    return (
        torch.tensor(hi.vertices, dtype=torch.float32),
        torch.tensor(hi.faces, dtype=torch.long),
        torch.tensor(hi.vertex_normals, dtype=torch.float32),
    )


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
    normals = torch.nn.functional.normalize((hi.n[corners] * w[..., None]).sum(1), dim=1)
    normals = torch.where((normals * nrm).sum(1, keepdim=True) < 0, -normals, normals)
    return normals, p


if __name__ == "__main__":
    main(run)
