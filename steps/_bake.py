"""Volume → PBR textures on a UV-mapped mesh, without nvdiffrast (IMPLEMENTATION.md M2/M3).

Pure torch, so it runs on the GPU in production and on the CPU in tests. The CuMesh parts (simplify,
unwrap, BVH projection) stay in the steps that call this. Conventions:
- UVs are glTF UVs (origin top-left); texel (row y, column x) has its centre at ((x+.5)/S, (y+.5)/S).
  No flips anywhere: the UVs we rasterize are the UVs we write.
- The volume is TRELLIS's sparse attribute grid: voxel i of a resolution-R grid spans
  [-0.5 + i/R, -0.5 + (i+1)/R) and is sampled at its centre, trilinearly, missing neighbours dropped
  and weights renormalised (FlexGEMM grid_sample_3d semantics).
"""

import numpy as np
import torch


def rasterize_uv(
    uv: torch.Tensor, faces: torch.Tensor, size: int, budget: int = 1 << 23, depth=None
):
    """Which triangle covers each texel centre, and where: (face_id [S,S] long, -1 = empty; bary [S,S,3]).
    With `depth` (per vertex) the nearest triangle wins at each pixel: a z-buffer for a view drawn with
    projected positions in place of UVs (M23)."""
    dev = uv.device
    face_id = torch.full((size, size), -1, dtype=torch.long, device=dev)
    bary = torch.zeros(size, size, 3, dtype=torch.float32, device=dev)
    zbuf = None if depth is None else torch.full((size * size,), float("inf"), device=dev)
    tri = uv.float()[faces.long()] * size  # [F, 3, 2] in texel units
    lo = (
        torch.ceil(tri.amin(1) - 0.5).clamp(0, size - 1).long()
    )  # first texel whose centre may be inside
    hi = torch.floor(tri.amax(1) - 0.5).clamp(0, size - 1).long()
    wh = (hi - lo + 1).clamp(min=0)
    count = wh[:, 0] * wh[:, 1]
    ends = count.cumsum(0)
    start = 0
    while start < len(faces):  # chunks of ~budget (triangle, texel) pairs
        stop = int(
            torch.searchsorted(ends, ends[start] - count[start] + budget, right=True).clamp(
                min=start + 1
            )
        )
        f = torch.arange(start, stop, device=dev)
        f = f[count[f] > 0]
        start = stop
        if len(f) == 0:
            continue
        t = torch.repeat_interleave(f, count[f])
        offset = count[f].cumsum(0) - count[f]  # each triangle's first pair within this chunk
        local = torch.arange(len(t), device=dev) - torch.repeat_interleave(offset, count[f])
        x = lo[t, 0] + local % wh[t, 0]
        y = lo[t, 1] + local // wh[t, 0]
        p = torch.stack([x, y], 1).float() + 0.5
        a, b, c = tri[t, 0], tri[t, 1], tri[t, 2]

        def cross(u, v):
            return u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]

        area = cross(b - a, c - a)
        w = (
            torch.stack([cross(c - b, p - b), cross(a - c, p - c), cross(b - a, p - a)], 1)
            / area[:, None]
        )
        inside = (area.abs() > 1e-12) & (w >= -1e-6).all(1)
        sel = torch.nonzero(inside).squeeze(1)  # the (triangle, texel) pairs that cover their texel
        idx = (y * size + x)[sel]
        if depth is not None:
            z = (depth[faces[t[sel]]] * w[sel]).sum(1)
            near = zbuf.clone().scatter_reduce_(0, idx, z, "amin")  # the nearest so far per pixel
            win = z <= near[idx]
            sel, idx, z = sel[win], idx[win], z[win]
        # one pair per texel within the chunk, the lowest triangle among ties (a shared edge, coplanar
        # overlaps): face_id and bary must come from the same triangle, or the texel's position is wrong;
        # separate duplicate-index writes picked their winners independently (seen as a bake texel
        # landing outside its hole, run to run)
        order = torch.arange(len(idx), device=dev)
        first = torch.full((size * size,), len(idx), dtype=torch.long, device=dev)
        first.scatter_reduce_(0, idx, order, "amin")
        keep = first[idx] == order
        sel, idx = sel[keep], idx[keep]
        if depth is not None:
            zbuf[idx] = z[keep]
        face_id[y[sel], x[sel]] = t[sel]
        bary[y[sel], x[sel]] = w[sel]
    return face_id, bary


def sample_volume(coords, attrs, resolution: int, points, chunk: int = 1 << 20):
    """Trilinear samples [P, C] of a sparse voxel grid at TRELLIS-frame points [P, 3]."""
    dev = points.device
    coords, attrs, r = coords.to(dev).long(), attrs.to(dev).float(), resolution
    keys, order = ((coords[:, 0] * r + coords[:, 1]) * r + coords[:, 2]).sort()
    corners = torch.tensor([[i & 1, (i >> 1) & 1, (i >> 2) & 1] for i in range(8)], device=dev)
    out = torch.zeros(len(points), attrs.shape[1], device=dev)
    for s in range(0, len(points), chunk):
        q = (points[s : s + chunk].float() + 0.5) * r  # voxel units: voxel i spans [i, i+1)
        c = torch.floor(q - 0.5).long()[:, None, :] + corners  # [P, 8, 3]
        key = (c[..., 0] * r + c[..., 1]) * r + c[..., 2]
        pos = torch.searchsorted(keys, key).clamp(max=len(keys) - 1)
        found = ((c >= 0) & (c < r)).all(-1) & (keys[pos] == key)
        w = (1 - (q[:, None, :] - c - 0.5).abs()).prod(-1) * found
        acc = (w[..., None] * attrs[order[pos]]).sum(1)
        out[s : s + chunk] = acc / w.sum(1, keepdim=True).clamp(min=1e-12)
    return out


def fill_gutters(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Push-pull: spread texels into the empty atlas so filtering and mips don't bleed black.

    Masked means go down a pyramid; on the way up every hole takes the coarser level's value. Exact for
    flat colour (cv2.inpaint drifts a few levels there) and linear in the texel count.
    """
    color, weight = img.astype(np.float32) * mask[..., None], mask.astype(np.float32)
    levels = []
    while max(weight.shape) > 1:
        levels.append((color, weight))
        h, w = weight.shape
        pad = ((0, h % 2), (0, w % 2))
        color = np.pad(color, (*pad, (0, 0)))
        weight = np.pad(weight, pad)
        color = color[0::2, 0::2] + color[1::2, 0::2] + color[0::2, 1::2] + color[1::2, 1::2]
        weight = weight[0::2, 0::2] + weight[1::2, 0::2] + weight[0::2, 1::2] + weight[1::2, 1::2]
    out = color / max(float(weight.max()), 1e-12)
    for color, weight in reversed(levels):
        up = out.repeat(2, 0).repeat(2, 1)[: weight.shape[0], : weight.shape[1]]
        has = weight[..., None] > 0
        out = np.where(has, color / np.where(has, weight[..., None], 1), up)
    return (out + 0.5).clip(0, 255).astype(np.uint8)


def box_filter(img, w, ss: int):
    """Mean of each ss×ss block of [S,S,C] samples over those where `w` holds; a block is covered when
    any of its samples is. Returns (float image [S/ss,...], covered mask)."""
    n = w.astype(np.float32)

    def blocks(a):
        return a.reshape(a.shape[0] // ss, ss, a.shape[1] // ss, ss, *a.shape[2:]).sum((1, 3))

    cnt = blocks(n)
    return blocks(img.astype(np.float32) * n[..., None]) / np.maximum(cnt, 1)[..., None], cnt > 0


def _fill(img, w, ss: int):
    """box-filter to the output size, then push-pull the gutters: (uint8 image, covered mask)"""
    img, covered = box_filter(img, w, ss)
    return fill_gutters((img + 0.5).clip(0, 255).astype(np.uint8), covered), covered


def bake_volume(
    face_id,
    bary,
    vertices,
    faces,
    coords,
    attrs,
    layout: dict,
    resolution: int,
    project=None,
    ss: int = 1,
):
    """Textures for a mesh (vertices in the TRELLIS frame) from the volume.

    project(points) → points maps texel positions onto the high-res surface (CuMesh BVH on the GPU);
    None keeps them on this mesh. face_id/bary come from a raster `ss` times the output size; the ss×ss
    samples of each output texel are box-filtered (supersampling) before the gutters are filled.
    Returns base colour RGBA and glTF metallicRoughness (G rough, B metal) as uint8 images with gutters
    filled, and the coverage mask (output size).
    """
    mask = face_id >= 0
    tri = vertices.float()[faces.long()[face_id[mask]]]  # [T, 3, 3]
    pts = (tri * bary[mask][..., None]).sum(1)
    if project is not None:
        pts = project(pts)
    vals = sample_volume(coords, attrs, resolution, pts)
    tex = torch.zeros(*face_id.shape, vals.shape[1], device=vals.device)
    tex[mask] = vals
    tex = (tex.clamp(0, 1) * 255 + 0.5).byte().cpu().numpy()
    m = mask.cpu().numpy()

    def chan(name):
        a, b = layout[name]
        return tex[..., a:b]

    fill = lambda img, w: _fill(img, w, ss)

    # a texel whose sample found no volume (alpha ~0: a pit the clean rebuild left between hair strands,
    # the empty gap of a double shell) takes its colour from its neighbours like a gutter would,
    # instead of baking black into the surface (M23b, the kid's hair); the alpha channel keeps the
    # sample, so a cut-out stays a cut-out
    filled = m & (chan("alpha")[..., 0] >= 128)
    zero = np.zeros_like(chan("metallic"))
    color, _ = fill(chan("base_color"), filled)
    alpha, covered = fill(chan("alpha"), m)
    mr, _ = fill(np.concatenate([zero, chan("roughness"), chan("metallic")], -1), filled)
    return np.concatenate([color, alpha], -1), mr, covered


def _srgb(c, to_linear: bool):
    if to_linear:
        return torch.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    c = c.clamp(0, 1)
    return torch.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def bake_source(src, face, w, nrm, mask, ss: int = 1):
    """Textures for the covered texels of `mask` [S,S] from a model's own materials (M62), no volume.

    `face` [T] and `w` [T,3] are each texel's closest point on `src` (arrays v, f, uv, n, tan, sign, fmat, mats: the
    source mesh in this mesh's frame, glTF UVs, a material index per face, trimesh PBRMaterials); `nrm` [T,3] the
    low mesh's normals at the texels. Base colour = sRGB(linear(texture) × factor), alpha only from MASK/BLEND
    materials, roughness/metallic = factor × the metallicRoughness texture's G/B. One material at a time (VRAM).
    Returns (RGBA, ORM with R = 0, covered) as bake_volume does, and the source's normal at each texel [T,3]
    (bent by its normal map where it has one) turned to the side of `nrm`.
    Ceilings (not read): vertex colours, emissive, source AO, KHR_texture_transform, TEXCOORD_1, wrap modes;
    BLEND is baked as MASK at 0.5 (alphaCutoff ignored); trimesh keeps baseColorFactor as uint8 (±1/255);
    each material's textures are converted to a float32 tensor whole (a few hundred MB for several 4K maps).
    # ponytail: the closest point can lie on the wrong side of a thin or doubled part; the upgrade is a ray along
    # -n within a band, the closest point as the fallback.
    """
    dev, n_tex = w.device, len(face)
    corners = src.f[face]
    mix = lambda a, i=slice(None): (a[corners[i]] * w[i][..., None]).sum(1)
    uv, gn = mix(src.uv) % 1, torch.nn.functional.normalize(mix(src.n), dim=1)
    flip = torch.where((gn * nrm).sum(1, keepdim=True) < 0, -1.0, 1.0)
    out = {
        k: torch.ones(n_tex, c, device=dev) for k, c in (("rgb", 3), ("a", 1), ("r", 1), ("m", 1))
    }
    nrm_out = gn.clone()

    def sample(img, q):  # bilinear at uv q [K,2]; image rows run down, like glTF's v
        t = torch.from_numpy(np.asarray(img.convert("RGBA"), np.float32) / 255).permute(2, 0, 1)[
            None
        ]
        grid = (q * 2 - 1)[None, :, None, :]
        return torch.nn.functional.grid_sample(
            t.to(dev), grid, padding_mode="border", align_corners=False
        )[0, :, :, 0].T

    for k, mat in enumerate(src.mats):
        i = torch.nonzero(src.fmat[face] == k).squeeze(1)
        if not len(i):
            continue
        q, g = uv[i], lambda x, d=1.0: d if x is None else x
        factor = torch.tensor(
            np.asarray(g(getattr(mat, "baseColorFactor", None), [255] * 4), np.float32) / 255,
            device=dev,
        )
        tex = getattr(mat, "baseColorTexture", None)
        c = sample(tex, q) if tex is not None else torch.ones(len(i), 4, device=dev)
        lin = _srgb(c[:, :3], True) * factor[:3]
        out["rgb"][i] = _srgb(lin, False)
        if getattr(mat, "alphaMode", None) in ("MASK", "BLEND"):
            out["a"][i] = (c[:, 3] * factor[3])[:, None]
        tex = getattr(mat, "metallicRoughnessTexture", None)
        mr = sample(tex, q) if tex is not None else torch.ones(len(i), 4, device=dev)
        out["r"][i] = (g(getattr(mat, "roughnessFactor", None)) * mr[:, 1])[:, None]
        out["m"][i] = (g(getattr(mat, "metallicFactor", None)) * mr[:, 2])[:, None]
        if (tex := getattr(mat, "normalTexture", None)) is not None:
            t = torch.nn.functional.normalize(mix(src.tan, i), dim=1)
            t = torch.nn.functional.normalize(t - (t * gn[i]).sum(1, keepdim=True) * gn[i], dim=1)
            b = torch.cross(gn[i], t, dim=1) * mix(src.sign[:, None], i).sign()
            d = sample(tex, q)[:, :3] * 2 - 1
            nrm_out[i] = torch.nn.functional.normalize(
                d[:, :1] * t + d[:, 1:2] * b + d[:, 2:] * gn[i], dim=1
            )

    def image(x):
        img = torch.zeros(*mask.shape, x.shape[1], device=dev)
        img[mask] = x
        return (img.clamp(0, 1) * 255 + 0.5).byte().cpu().numpy()

    m = mask.cpu().numpy()
    rgb, a, rough, metal = (image(out[k]) for k in ("rgb", "a", "r", "m"))
    color, _ = _fill(rgb, m, ss)
    alpha, covered = _fill(a, m, ss)
    mr, _ = _fill(np.concatenate([np.zeros_like(rough), rough, metal], -1), m, ss)
    return np.concatenate([color, alpha], -1), mr, covered, nrm_out * flip


def ao_texels(bvh, p, n, voxel: float, reach: float, rays: int = 32, chunk: int = 1 << 16):
    """Ambient occlusion [P] in 0..1: the share of `rays` cosine-weighted hemisphere rays from each point
    p (+ 0.5 voxel along its unit normal n) that hit the BVH's mesh within `reach`. Stratified, seeded."""
    dev = p.device
    out = torch.empty(len(p), device=dev)
    g = torch.Generator(device=dev).manual_seed(0)
    k = torch.arange(rays, device=dev)
    ex, ey = torch.tensor([1.0, 0, 0], device=dev), torch.tensor([0, 1.0, 0], device=dev)
    for s in range(0, len(p), chunk):
        o, nn = p[s : s + chunk], n[s : s + chunk]
        t = torch.nn.functional.normalize(
            torch.cross(nn, torch.where(nn[:, :1].abs() < 0.9, ex, ey).expand_as(nn), dim=1), dim=1
        )
        b = torch.cross(nn, t, dim=1)
        u1 = (k + torch.rand(len(o), rays, device=dev, generator=g)) / rays
        phi = torch.rand(len(o), rays, device=dev, generator=g) * 2 * torch.pi
        r = u1.sqrt()
        d = (r * phi.cos())[..., None] * t[:, None] + (r * phi.sin())[..., None] * b[:, None]
        d = d + (1 - u1).sqrt()[..., None] * nn[:, None]
        origin = (o + nn * 0.5 * voxel)[:, None].expand_as(d)
        _, face, depth = bvh.ray_trace(origin.reshape(-1, 3), d.reshape(-1, 3))
        out[s : s + chunk] = 1 - ((face >= 0) & (depth < reach)).view(len(o), rays).float().mean(1)
    return out


def ao_faces(v, f, n, bvh, voxel: float, margin: float = 0.1, eps: float = 0.02):
    """Per face, from the AO's own rays (GPU): (inward [F] bool, enclosed [F] bool). The share of 64
    hemisphere rays from the face's centre that leave the mesh for good (ao_texels, no reach), along the face's
    mean vertex normal `n` and against it. `inward`: more rays escape against it (a thin sheet open on both
    sides, or a face closed on both, follows its decided neighbours: spread_votes). `enclosed`: next to none
    escape either way: a face of an inner shell, hidden, whose texels get no occlusion rather than darkness."""
    from steps._mesh import spread_votes

    tri = v[f.long()]
    nf = torch.nan_to_num(torch.nn.functional.normalize(n[f.long()].mean(1), dim=1))
    c, far = tri.mean(1), float("inf")
    out_a, out_b = ao_texels(bvh, c, nf, voxel, far, 64), ao_texels(bvh, c, -nf, voxel, far, 64)
    d = (out_a - out_b).cpu().numpy()
    votes = np.where(d > margin, 1, np.where(d < -margin, -1, 0))
    inward = torch.from_numpy(spread_votes(f.cpu().numpy(), votes) < 0).to(f.device)
    return inward, (out_a <= eps) & (out_b <= eps)


def fill_dark_islands(ao, mask, dark: float = 0.25, area: float = 0.0003, jump: float = 0.3):
    """AO image [S,S] 0..1 with small dark islands filled from their surroundings: a connected patch of
    texels below `dark`, smaller than `area` × S² texels and more than `jump` below the median of the covered
    ring around it, takes that median. A hemisphere integral is smooth along a surface, so a tiny island of
    near-zero AO in lit surface is another surface: an inner shell showing where the outer one is torn (a
    torn sleeve came out in black triangles, docs/bench/m43c.md). A crevice is an extended dark region and
    stays. Returns (the image, the share of covered texels filled)."""
    from scipy import ndimage

    labels, n = ndimage.label((ao < dark) & mask)
    if not n:
        return ao, 0.0
    sizes = np.bincount(labels.ravel(), minlength=n + 1)
    small = np.flatnonzero(sizes <= area * ao.shape[0] ** 2)
    small = small[small > 0]
    if not len(small):
        return ao, 0.0
    island = np.isin(labels, small)
    ring = ndimage.binary_dilation(island, iterations=2) & ~island & mask
    ring_labels = np.where(ring, ndimage.grey_dilation(labels, size=5), 0)  # a ring texel's island
    ring_cnt = np.bincount(ring_labels.ravel(), minlength=n + 1)[small]
    ring_med = np.asarray(ndimage.median(ao, ring_labels, small))
    own_med = np.asarray(ndimage.median(ao, labels, small))
    fill = (ring_cnt >= 4) & (ring_med > own_med + jump)
    value, take = np.zeros(n + 1), np.zeros(n + 1, bool)
    value[small[fill]], take[small[fill]] = ring_med[fill], True
    out = np.where(take[labels], value[labels], ao)
    return out, int(sizes[small[fill]].sum()) / max(int(mask.sum()), 1)


def seams(vertices, faces, uv, size: int):
    """UV seam edges: pairs of edges at the same 3D positions with different UVs (where the glTF splits
    its vertices). Per sample point along the seam, in texel units: (pA, pB, inA, inB), the point on each
    side's chart and that side's unit direction into its chart."""
    vertices, faces, uv = np.asarray(vertices, float), np.asarray(faces), np.asarray(uv, float)
    scale = max(float(np.ptp(vertices, axis=0).max()), 1e-9)
    key3 = np.round(vertices / scale * 1e6).astype(np.int64)
    cid = np.unique(key3, axis=0, return_inverse=True)[1].reshape(-1)
    # corner k of a face starts the edge to corner k+1; corner k+2 is opposite
    ia = faces.reshape(-1)
    ib = np.roll(faces, -1, 1).reshape(-1)
    op = np.roll(faces, -2, 1).reshape(-1)
    key = np.minimum(cid[ia], cid[ib]) * len(vertices) + np.maximum(cid[ia], cid[ib])
    order, k2 = np.argsort(key, kind="stable"), np.sort(key)
    i = np.nonzero(
        (k2[:-1] == k2[1:])
        & np.r_[True, k2[:-2] != k2[1:-1]]
        & np.r_[k2[2:] != k2[1:-1], True]  # exactly two edges share the key: a manifold edge
    )[0]
    e1, e2 = order[i], order[i + 1]
    flip = (cid[ia[e2]] != cid[ia[e1]])[:, None]  # the second edge runs the other way round
    a0, a1, ta = uv[ia[e1]] * size, uv[ib[e1]] * size, uv[op[e1]] * size
    b0 = np.where(flip, uv[ib[e2]], uv[ia[e2]]) * size
    b1 = np.where(flip, uv[ia[e2]], uv[ib[e2]]) * size
    tb = uv[op[e2]] * size

    def inward(p0, p1, third):
        e = p1 - p0
        perp = (
            np.stack([-e[:, 1], e[:, 0]], 1) / np.maximum(np.linalg.norm(e, axis=1), 1e-9)[:, None]
        )
        return perp * np.where(((third - p0) * perp).sum(1) >= 0, 1.0, -1.0)[:, None]

    ok = (np.abs(a0 - b0).max(1) > 1e-3) | (np.abs(a1 - b1).max(1) > 1e-3)  # UVs differ: a seam
    ok &= (np.linalg.norm(a1 - a0, axis=1) > 1e-3) & (np.linalg.norm(b1 - b0, axis=1) > 1e-3)
    n_a, n_b = inward(a0, a1, ta), inward(b0, b1, tb)
    a0, a1, b0, b1, n_a, n_b = (x[ok] for x in (a0, a1, b0, b1, n_a, n_b))
    length = np.maximum(np.linalg.norm(a1 - a0, axis=1), np.linalg.norm(b1 - b0, axis=1))
    count = np.ceil(length * 2).astype(int) + 1  # a sample every half texel
    e = np.repeat(np.arange(len(count)), count)
    t = (np.arange(len(e)) - np.repeat(count.cumsum() - count, count) + 0.5) / count[e]
    return (
        a0[e] + t[:, None] * (a1 - a0)[e],
        b0[e] + t[:, None] * (b1 - b0)[e],
        n_a[e],
        n_b[e],
    )


def _bilinear(img, p):
    """Bilinear samples of [S,S,C] at points p [N,2] in texel units (texel centres at +0.5)."""
    s = img.shape[0]
    q = p - 0.5
    x0, y0 = np.floor(q[:, 0]).astype(int), np.floor(q[:, 1]).astype(int)
    fx, fy = (q[:, 0] - x0)[:, None], (q[:, 1] - y0)[:, None]

    def at(y, x):
        return img[y.clip(0, s - 1), x.clip(0, s - 1)].astype(np.float32)

    top = at(y0, x0) * (1 - fx) + at(y0, x0 + 1) * fx
    return top * (1 - fy) + (at(y0 + 1, x0) * (1 - fx) + at(y0 + 1, x0 + 1) * fx) * fy


def seam_delta(img, seam) -> float:
    """Mean |Δ| (0-255) between the two sides' colours along the seams; 0 when there are none."""
    pa, pb = seam[:2]
    return float(np.abs(_bilinear(img, pa) - _bilinear(img, pb)).mean()) if len(pa) else 0.0


def heal_seams(img, mask, seam, reach: float = 2.0):
    """Level the two sides of every seam: each side's colour along the edge moves to the mean of both
    sides, and the shift fades linearly to nothing `reach` texels into the chart. Covered texels only."""
    if not len(seam[0]):
        return img
    pa, pb, na, nb = seam
    ca, cb = _bilinear(img, pa), _bilinear(img, pb)
    mid = (ca + cb) / 2
    s = img.shape[0]
    acc, cnt = np.zeros((s * s, img.shape[2])), np.zeros(s * s)
    for p, nrm, c in ((pa, na, ca), (pb, nb, cb)):
        for d in np.arange(0, reach, 0.5):
            xy = np.floor(p + nrm * d).astype(int).clip(0, s - 1)
            idx = xy[:, 1] * s + xy[:, 0]
            ok = mask.reshape(-1)[idx]
            np.add.at(acc, idx[ok], (1 - d / reach) * (mid - c)[ok])
            np.add.at(cnt, idx[ok], 1)
    shift = (acc / np.maximum(cnt, 1)[:, None]).reshape(img.shape)
    return (img + shift).round().clip(0, 255).astype(np.uint8)


def write_glb(
    path,
    vertices,
    faces,
    uv,
    base_rgba,
    metal_rough,
    normals=None,
    normal_map=None,
    transform=None,
    occlusion=False,
    alpha_mode=None,
):
    """Static textured GLB (trimesh). transform: 4×4 applied to positions/normals (e.g. TRELLIS → library).
    occlusion: declare metal_rough's R channel as the glTF occlusionTexture (ORM packing).
    alpha_mode: the material's glTF alphaMode ("MASK": cut-out at 0.5), None for trimesh's default."""
    import trimesh
    from PIL import Image

    v = np.asarray(vertices, np.float64)
    n = None if normals is None else np.asarray(normals, np.float64)
    if transform is not None:
        m = np.asarray(transform, np.float64)
        v = v @ m[:3, :3].T + m[:3, 3]
        if n is not None:
            n = n @ np.linalg.inv(m[:3, :3])  # normals use the inverse transpose
            n /= np.linalg.norm(n, axis=1, keepdims=True).clip(1e-12)
    orm = Image.fromarray(metal_rough, "RGB")
    material = trimesh.visual.material.PBRMaterial(
        baseColorTexture=Image.fromarray(base_rgba, "RGBA"),
        metallicRoughnessTexture=orm,
        occlusionTexture=orm if occlusion else None,
        normalTexture=None if normal_map is None else Image.fromarray(normal_map, "RGB"),
        metallicFactor=1.0,
        roughnessFactor=1.0,
        alphaMode=alpha_mode,
        alphaCutoff=0.5 if alpha_mode == "MASK" else None,
    )
    uv_trimesh = np.asarray(uv, np.float64).copy()
    uv_trimesh[:, 1] = (
        1 - uv_trimesh[:, 1]
    )  # trimesh keeps OpenGL UVs and flips them back on export
    mesh = trimesh.Trimesh(v, np.asarray(faces), vertex_normals=n, process=False)
    mesh.visual = trimesh.visual.TextureVisuals(uv=uv_trimesh, material=material)
    mesh.export(path, file_type="glb")
    # TANGENT, the frame the normal map was baked in: without it every viewer builds its own (facets, seams)
    tangents = None
    if normal_map is not None:
        nrm = mesh.vertex_normals if n is None else n
        tangents = vertex_tangents(v, np.asarray(faces), np.asarray(uv, np.float64), nrm)
    if occlusion or tangents is not None:
        from pathlib import Path

        from steps import _anim

        doc, blob = _anim.read_glb(path)
        if (
            occlusion
        ):  # trimesh lists the ORM once per slot (two images, two textures, one buffer view): once
            _one_image_per_picture(doc)
        if tangents is not None:
            blob = _add_tangents(doc, blob, tangents)
        Path(path).write_bytes(_anim.pack(doc, blob))


def vertex_tangents(v, f, uv, n) -> np.ndarray:
    """glTF TANGENT [N,4] per vertex (xyz, w = the bitangent's sign): area-weighted ∂P/∂u made square to the
    normal, the frame bake encodes the normal map in. `uv` in glTF's convention (v runs down the image)."""
    p0, p1, p2 = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    t0, t1, t2 = uv[f[:, 0]], uv[f[:, 1]], uv[f[:, 2]]
    e1, e2, d1, d2 = p1 - p0, p2 - p0, t1 - t0, t2 - t0
    r = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
    r = np.where(np.abs(r) < 1e-20, 1e-20, r)
    du = (e1 * d2[:, 1:] - e2 * d1[:, 1:]) / r[:, None]  # ∂P/∂u
    up = -(e2 * d1[:, :1] - e1 * d2[:, :1]) / r[:, None]  # -∂P/∂v: "up" in the image is -v
    tan, bit = np.zeros_like(v), np.zeros_like(v)
    for k in range(3):
        np.add.at(tan, f[:, k], du)
        np.add.at(bit, f[:, k], up)
    n = n / np.linalg.norm(n, axis=1, keepdims=True).clip(1e-12)
    tan -= (tan * n).sum(1, keepdims=True) * n
    length = np.linalg.norm(tan, axis=1, keepdims=True)
    # a vertex whose triangles have no UV area: any unit vector square to its normal (glTF wants unit length)
    spare = np.cross(n, np.where(np.abs(n[:, :1]) < 0.9, [[1.0, 0, 0]], [[0, 1.0, 0]]))
    tan = np.where(
        length > 1e-12,
        tan / length.clip(1e-12),
        spare / np.linalg.norm(spare, axis=1, keepdims=True),
    )
    sign = np.where((np.cross(n, tan) * bit).sum(1) >= 0, 1.0, -1.0)
    return np.concatenate([tan, sign[:, None]], 1).astype(np.float32)


def _add_tangents(doc: dict, blob: bytes, tangents: np.ndarray) -> bytes:
    """Append `tangents` as each primitive's TANGENT (the primitives trimesh wrote: one per mesh, over all the
    vertices); a primitive with another vertex count is left alone. Returns the new BIN chunk."""
    data = np.ascontiguousarray(tangents, "<f4").tobytes()
    doc.setdefault("bufferViews", []).append(
        {"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), "target": 34962}
    )
    doc.setdefault("accessors", []).append(
        {
            "bufferView": len(doc["bufferViews"]) - 1,
            "componentType": 5126,
            "count": len(tangents),
            "type": "VEC4",
        }
    )
    for mesh in doc.get("meshes", []):
        for prim in mesh["primitives"]:
            if doc["accessors"][prim["attributes"]["POSITION"]]["count"] == len(tangents):
                prim["attributes"]["TANGENT"] = len(doc["accessors"]) - 1
    blob += data
    doc["buffers"][0]["byteLength"] = len(blob)
    return blob


def _uniq(items: list) -> tuple[list, dict]:
    """(the distinct items in order, old index → new)."""
    out, index = [], {}
    for i, x in enumerate(items):
        if x not in out:
            out.append(x)
        index[i] = out.index(x)
    return out, index


def _one_image_per_picture(doc: dict) -> None:
    """Images over one buffer view become one image and one texture; the materials' slots follow (a
    picture serving two slots was decoded twice by every loader and came out of Blender as two files)."""
    images, image = _uniq(doc["images"])
    textures, texture = _uniq([t | {"source": image[t["source"]]} for t in doc["textures"]])
    for m in doc["materials"]:
        for slot in (m, m.get("pbrMetallicRoughness", {})):
            for v in (v for k, v in slot.items() if k.endswith("Texture")):
                v["index"] = texture[v["index"]]
    doc["images"], doc["textures"] = images, textures
