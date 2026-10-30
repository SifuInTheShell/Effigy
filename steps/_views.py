"""Views of a textured mesh and their way back into its texture (M23 `project`, M43b `refine`).

A camera here is anything with `project(points) → (pix [N,2] in [0,1]², u right, v down; depth, nearer is
smaller)` and `towards(points) → [N,3]` (the direction from each point to the camera); `texel_weights` also
takes its `extent` (metres across the view: `ortho`'s, M66). Pure torch + numpy, so it runs on the CPU in tests.
"""

import math
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from steps._bake import rasterize_uv


def ortho(yaw: float, pitch: float, center, extent: float) -> dict:
    """An orthographic camera in the library frame (Y up): at yaw 0 it looks at the model's front (+Z) from
    +Z, yaw turns it round the model (degrees, towards +X), pitch tilts it (negative = looking down).
    `extent` metres of the frame are drawn across the square view, centred on `center`."""
    y, p = math.radians(yaw), math.radians(-pitch)
    ry = np.array([[math.cos(y), 0, -math.sin(y)], [0, 1, 0], [math.sin(y), 0, math.cos(y)]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    r = rx @ ry  # library frame → camera frame (camera on +Z, looking down -Z)
    c = np.asarray(center, float) @ r.T

    def project(pts):
        q = np.asarray(pts, float) @ r.T
        return np.c_[0.5 + (q[:, 0] - c[0]) / extent, 0.5 - (q[:, 1] - c[1]) / extent], -q[:, 2]

    d = r[2]  # the camera's +Z in the library frame
    return {
        "yaw": yaw,
        "pitch": pitch,
        "center": [float(x) for x in center],
        "extent": float(extent),
        "project": project,
        "towards": lambda pts: np.broadcast_to(d, np.shape(pts)),
    }


def zbuffer(pix_v, depth_v, f, size: int):
    """The mesh drawn from a camera: (face id [S,S], bary [S,S,3], depth [S,S], inf where empty)."""
    face_img, bary_img = rasterize_uv(pix_v, f, size, depth=depth_v)
    seen = face_img >= 0
    zbuf = torch.full((size, size), float("inf"), device=pix_v.device)
    zbuf[seen] = (depth_v[f[face_img[seen]]] * bary_img[seen]).sum(1)
    return face_img, bary_img, zbuf


def visible(zbuf, pix, depth, depth_v):
    """Which points [N] at pix/depth the camera sees: the z-test against the farthest drawn depth among the
    pixel's 3×3 neighbours, with a slop of 1 % of the mesh's depth extent, so texels on triangle edges and
    folds are not cut by the pixel grid."""
    size = zbuf.shape[0]
    drawn = torch.where(torch.isfinite(zbuf), zbuf, torch.full_like(zbuf, -float("inf")))
    far = F.max_pool2d(drawn[None, None], 3, 1, 1)[0, 0]
    eps = max(0.01 * float(depth_v.max() - depth_v.min()), 1e-4)
    px = (pix * size).long().clamp(0, size - 1)
    return depth <= far[px[:, 1], px[:, 0]] + eps


def facing_cos(v_np, faces_np, corners, w3, pts, towards) -> np.ndarray:
    """cos [N] between each point's smooth normal and the direction to the camera. Every face is turned
    towards the camera first: a texel that passed the z-test is the front surface whichever way its
    triangle is wound (remeshed bake meshes have inward-wound patches, the nurse's face), and face
    normals alone would facet the blend on curved hair and caps."""
    tri = v_np[faces_np]
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    fn /= np.linalg.norm(fn, axis=1, keepdims=True).clip(1e-12)
    fn = fn * np.sign((fn * towards(tri.mean(1))).sum(1, keepdims=True) + 1e-12)
    vn = np.zeros_like(v_np)
    np.add.at(vn, faces_np.ravel(), np.repeat(fn, 3, axis=0))
    vn = torch.tensor(
        vn / np.linalg.norm(vn, axis=1, keepdims=True).clip(1e-12),
        dtype=torch.float32,
        device=w3.device,
    )
    nrm = F.normalize((vn[corners] * w3).sum(1), dim=1).cpu().numpy()
    t = np.asarray(towards(pts), float)
    t = t / np.linalg.norm(t, axis=1, keepdims=True).clip(1e-12)
    return np.clip((nrm * t).sum(1), 0, 1)


def cos_weight(cos):
    """smoothstep((cos − 0.25) / 0.35): grazing surfaces take nothing from a view."""
    w = np.clip((np.asarray(cos) - 0.25) / 0.35, 0, 1)
    return w * w * (3 - 2 * w)


def blur_premul(x, sigma_px: float, k: int = 8):
    """[1, C+1, H, W] premultiplied colour + weight → both blurred (normalised convolution): at 1/k
    resolution with a Gaussian of `sigma_px`, back up bilinearly."""
    c = x.shape[1]
    small = F.avg_pool2d(x, k)
    s = max(sigma_px / k, 1.0)
    r = int(3 * s)
    g = torch.exp(
        -(torch.arange(-r, r + 1, device=x.device, dtype=torch.float32) ** 2) / (2 * s * s)
    )
    g = g / g.sum()
    small = F.conv2d(
        F.pad(small, (r, r, 0, 0), mode="replicate"),
        g.view(1, 1, 1, -1).expand(c, 1, 1, -1),
        groups=c,
    )
    small = F.conv2d(
        F.pad(small, (0, 0, r, r), mode="replicate"),
        g.view(1, 1, -1, 1).expand(c, 1, -1, 1),
        groups=c,
    )
    return F.interpolate(small, size=x.shape[-2:], mode="bilinear", align_corners=False)


def render(base_t, uv, f, v_np, cam, size: int = 1024, ss: int = 2, extra=None):
    """The unlit base colour as `cam` sees it: drawn at size·ss with the z-buffer and box-filtered down.
    base_t [S,S,C] float 0–255 (C ≥ 3; extra channels such as a face mask come along). Returns
    (rgb [size,size,3] 0–1 on 50 % grey, alpha [size,size] coverage, the hi-res z-buffer triple)."""
    dev = base_t.device
    pix_v, depth_v = cam["project"](v_np)
    pix_v = torch.tensor(pix_v, dtype=torch.float32, device=dev)
    depth_v = torch.tensor(depth_v, dtype=torch.float32, device=dev)
    face_img, bary_img, zbuf = zbuffer(pix_v, depth_v, f, size * ss)
    seen = face_img >= 0
    puv = (uv[f[face_img[seen]]] * bary_img[seen][..., None]).sum(1)
    tex = base_t.permute(2, 0, 1)[None] / 255
    col = F.grid_sample(
        tex,
        (puv * 2 - 1).view(1, 1, -1, 2),
        mode="bilinear",
        padding_mode="border",
        align_corners=False,
    )
    img = torch.zeros(1, tex.shape[1] + 1, size * ss, size * ss, device=dev)
    img[0, :-1][:, seen] = col[0, :, 0]
    img[0, -1][seen] = 1
    img = F.avg_pool2d(img, ss)[0]
    a = img[-1]
    chans = img[:-1] / a.clamp(min=1e-6)
    rgb = chans[:3] * a + 0.5 * (1 - a)
    out = rgb.permute(1, 2, 0).clamp(0, 1)
    return out, a, chans[3:].permute(1, 2, 0), (face_img, bary_img, zbuf, pix_v, depth_v)


def depth_edges(zbuf, extent: float, grow: int) -> torch.Tensor:
    """[S,S] 1 near a depth step inside the view (one surface in front of another, M66, after StableGen's
    depth-edge rejection): drawn pixels whose 3×3 neighbourhood spans more than 16 pixel widths in depth,
    steeper than any surface `cos_weight` lets paint (75°: 11 widths across the window's diagonal); grown by
    `grow` pixels, because the generated view blends the two sides there as it does at the silhouette."""
    drawn = torch.isfinite(zbuf)
    ninf = torch.full_like(zbuf, -float("inf"))
    hi = F.max_pool2d(torch.where(drawn, zbuf, ninf)[None, None], 3, 1, 1)[0, 0]
    lo = -F.max_pool2d(torch.where(drawn, -zbuf, ninf)[None, None], 3, 1, 1)[0, 0]
    edge = (drawn & (hi - lo > 16 * extent / zbuf.shape[0])).float()
    return F.max_pool2d(edge[None, None], 2 * grow + 1, 1, grow)[0, 0]


def texel_weights(cam, a, zbuf, depth_v, pts, v_np, faces_np, corners, w3, erode: int = 6):
    """How much each texel (points pts on the surface) takes from a view: smoothstep of the facing cos ×
    the z-test × the view's silhouette (coverage a [H,W]) eroded by `erode` px, whose edge pixels mix in the
    background, × none within `erode` px of a depth step inside the view (`depth_edges`: an arm in front of the
    torso). Returns (weights [N], the texels' grid for grid_sample on the view)."""
    dev = a.device
    pix, depth = cam["project"](pts)
    pix_t = torch.tensor(pix, dtype=torch.float32, device=dev)
    vis = visible(zbuf, pix_t, torch.tensor(depth, dtype=torch.float32, device=dev), depth_v)
    sil = -F.max_pool2d(-(a > 0.5).float()[None, None], 2 * erode + 1, 1, erode)
    grid = (pix_t * 2 - 1).view(1, 1, -1, 2)

    def at(img):
        return F.grid_sample(img, grid, mode="nearest", padding_mode="zeros", align_corners=False)[
            0, 0, 0
        ]

    edge = depth_edges(zbuf, cam["extent"], erode * max(zbuf.shape[0] // a.shape[0], 1))
    cos = facing_cos(v_np, faces_np, corners, w3, pts, cam["towards"])
    weight = torch.tensor(cos_weight(cos), dtype=torch.float32, device=dev)
    return weight * vis * at(sil) * (1 - at(edge[None, None])), grid


def write_textured(out: Path, mesh, mat, rgba: np.ndarray, extra: dict) -> dict:
    """The textured GLB and the loose maps; the ORM and normal maps are the input's (an AO-bearing ORM keeps
    its occlusionTexture). `extra`: further outputs the step wrote itself."""
    from steps._bake import write_glb

    mr = mat.metallicRoughnessTexture
    mr = (
        np.asarray(mr.convert("RGB"))
        if mr is not None
        else np.full(rgba.shape[:2] + (3,), (255, 255, 0), np.uint8)
    )
    normal = np.asarray(mat.normalTexture.convert("RGB")) if mat.normalTexture is not None else None
    uv = np.asarray(mesh.visual.uv, float).copy()
    uv[:, 1] = 1 - uv[:, 1]  # trimesh's OpenGL UVs → glTF, what write_glb takes
    occlusion = getattr(mat, "occlusionTexture", None) is not None
    write_glb(
        out / "mesh.glb",
        mesh.vertices,
        mesh.faces,
        uv,
        rgba,
        mr,
        mesh.vertex_normals,
        normal,
        occlusion=occlusion,
        alpha_mode=getattr(mat, "alphaMode", None),
    )
    Image.fromarray(rgba, "RGBA").save(out / "basecolor.png")
    Image.fromarray(mr, "RGB").save(out / "orm.png")
    outputs = {"mesh": "mesh.glb", "basecolor": "basecolor.png", "orm": "orm.png"} | extra
    if normal is not None:
        Image.fromarray(normal, "RGB").save(out / "normal.png")
        outputs["normal"] = "normal.png"
    return outputs
