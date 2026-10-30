"""Texture detail pass (IMPLEMENTATION.md M43b): zoomed views of the textured mesh redrawn by Z-Image-Turbo +
Fun-ControlNet Tile 2.1 and painted back into the asset's own UVs (research 14).

Input: any version with a UV-mapped, textured .glb (bake, project, refine). The views are orthographic, unlit
renders of the base colour, one after another, each drawn from the texture as the earlier views left it; each
generated view is colour-matched to its render at low frequency and back-projected (cos × z-test × eroded
silhouette, none next to a depth step inside the view; the best view per texel wins by weight⁴). Mesh, UVs,
ORM and normal map stay the input's. M66: with views "auto", up to `coverage` more views where the planned ones
leave surface poorly seen (`coverage_views`).
params: {strength: 1.0, face_strength: 0.5, scale: 0.85, prompt, zoom: "auto" | ≥ 0.5, views: "auto" | [{yaw,
pitch, center, extent, prompt?}] (a view's own prompt instead of the asset's: one side of another material; name
the material, not the place: "construction site" drew a scene into a hoarding's paint, the owner's agent
2026-10-13) | [] (no redraw, no model: only `delight`), seed, tile: "full" | "lite" (default: full when
it is downloaded and the budget holds 15.6 GB, else lite; M46c), texture_size: "keep" | 4096,
delight: 0–1 (M43c: the bake's AO divided out of the base colour first), coverage: 0–8 (M66: the most views
added where the auto views see too little; default 3, 0 off)}. The prompt is `brief.prompt`, then
`prompt_full`. Outputs: mesh.glb, basecolor.png, orm.png,
normal.png, views.png (each view before | after; none without views). Runs one-shot (not resident: `concepts` is the resident
Z-Image, and two never share the card). ponytail: ~60 s load per run, make it a `concepts` op if the owner
refines often.
"""

import json
import math
import os
import random
import time
from pathlib import Path

import numpy as np
from PIL import Image

from steps._common import StepError, budget_gb, main, progress
from steps._image import asset_prompt

TILE = "alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1"  # Apache-2.0
TILE_REVISION = "5155fc56d17821007d6f62ac192c09e0f0e72016"
TILE_FILE = "Z-Image-Turbo-Fun-Controlnet-Tile-2.1-2601-8steps.safetensors"
# lite (3 control blocks, 2.0 GB, peak 12.4 GB) is in the `refine` download: the full build peaked at 15.5 of 16 GB
# and restarted the reference laptop (bugcheck 0x20001, 2026-10-10). M46c: the full one is its own download group
# (`refine-full`), offered on cards that hold it; without a "tile" param refine takes it when it is there and fits
FULL_GB = 15.6  # server/presets.VRAM_GB["refine"]["full"]: m43b's 15.57 GB reserved
TILES = {
    "full": (TILE_FILE, list(range(0, 30, 2))),
    "lite": ("Z-Image-Turbo-Fun-Controlnet-Tile-2.1-lite-2601-8steps.safetensors", [0, 10, 20]),
}
TILE_DIR = (
    "Z-Image-Turbo-Fun-Controlnet-Union-2.1"  # under EFFIGY_MODELS (tools/download_models.py)
)
VIEW = 1024  # view pixels; 2048² views spill past 16 GB (research 14)
ZOOM = 1.25  # view pixels per atlas texel
YAWS = [0, 45, 315, 90, 270, 135, 225, 180]  # front first, then alternating sides, the back last
SUFFIX = ", even flat lighting, plain grey background"
FACE_DELTA = 8  # a texel `project` changed by more than this (0–255) is face
ERODE = 6  # view pixels of the silhouette's edge that never paint
COVER = 0.5  # M66: a face is seen when a view's paint weight on it reaches this (coverage_views)


def _yaw_name(yaw: float, pitch: float) -> str:
    """Only what holds for any facing: the model may face -Z, and "front view" on its back invites a face.
    "three-quarter view" when the yaw is nearer a diagonal than a side (M66: coverage views and a user's views
    come at any yaw; before, any yaw off a multiple of 90°)."""
    if pitch <= -30:
        return "from above"
    return "three-quarter view" if abs(yaw % 90 - 45) < 22.5 else ""


def _part(k: int, n: int) -> str:
    if n == 1:
        return "the whole object"
    return "close-up of the " + (
        "upper part" if k == 0 else "lower part" if k == n - 1 else "middle part"
    )


def _tall(lo, hi) -> bool:
    """Taller than wide: the views from above are planned (and added, M66) only then."""
    return hi[1] - lo[1] >= max(hi[0] - lo[0], hi[2] - lo[2])


def texel_density(v: np.ndarray, faces: np.ndarray, uv: np.ndarray, size: int) -> float:
    """Atlas texels per metre, area-weighted: sqrt(UV area in texels² / 3D area in m²)."""
    t = uv[faces] * size
    e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    a_uv = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
    p = v[faces]
    a3 = 0.5 * np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
    return float(np.sqrt(a_uv.sum() / max(a3.sum(), 1e-12)))


def _fit(yaw, pitch, center, extent, pts):
    """center/extent widened and re-centred so the points (a window's band) fit the square view."""
    from steps._views import ortho

    pix, _ = ortho(yaw, pitch, center, extent)["project"](pts)
    lo, hi = pix.min(0), pix.max(0)
    span = float((hi - lo).max())
    cam = ortho(yaw, pitch, [0, 0, 0], 1.0)
    # the camera's right and up axes in the library frame: where the view's u and v move the centre
    right = cam["project"](np.eye(3))[0][:, 0] - 0.5
    up = 0.5 - cam["project"](np.eye(3))[0][:, 1]
    du, dv = (lo + hi) / 2 - 0.5
    c = np.asarray(center, float) + extent * (du * right - dv * up)
    return c.tolist(), extent * max(1.0, span * 1.05)


def plan_views(
    v: np.ndarray, faces: np.ndarray, uv: np.ndarray, size: int, zoom: float = ZOOM
) -> list[dict]:
    """The auto views: the height cut into windows of e = VIEW / (zoom · texel density) with 25 % overlap,
    8 yaws at pitch 0 each, and, for a model taller than wide, one view from above (pitch −60) of the top
    window (on a flat top, a wall's coping or a chest's lid, the redraw invented tiles: the owner's second agent,
    2026-10-11); a window is widened when the model is wider than it (less zoom there). Ordered front first,
    the back last."""
    e = VIEW / (zoom * texel_density(v, faces, uv, size))
    lo, hi = v.min(0), v.max(0)
    h = hi[1] - lo[1]
    n = 1 if h <= e else math.ceil((h - e) / (0.75 * e)) + 1
    ys = [(lo[1] + hi[1]) / 2] if n == 1 else list(np.linspace(hi[1] - e / 2, lo[1] + e / 2, n))
    cx, cz = (lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2
    out = []
    fy = v[faces][..., 1]

    def band(y0, y1):  # the vertices of the triangles reaching into [y0, y1], clipped to it
        b = v[faces[(fy.max(1) >= y0) & (fy.min(1) <= y1)]].reshape(-1, 3)
        b[:, 1] = b[:, 1].clip(y0, y1)
        return b

    for yaw in YAWS:
        for k, cy in enumerate(ys):
            pts = band(cy - e / 2, cy + e / 2)
            if not len(pts):
                continue
            c, ext = _fit(yaw, 0, [cx, cy, cz], e, pts)
            out.append({"yaw": yaw, "pitch": 0, "center": c, "extent": ext, "part": _part(k, n)})
        if yaw == 0 and _tall(lo, hi):  # from above, after the front: hats, shoulders
            c, ext = _fit(0, -60, [cx, ys[0], cz], e, band(ys[0] - e / 2, hi[1]))
            out.append({"yaw": 0, "pitch": -60, "center": c, "extent": ext, "part": _part(0, n)})
    return out


def coverage_views(
    v, faces, views, extra: int, e: float, dev: str = "cpu"
) -> tuple[list[dict], dict]:
    """M66 (after StableGen's greedy coverage, rebuilt on our z-buffer): up to `extra` views where the planned
    ones leave surface poorly seen. A face is seen when a view's paint weight on it (the facing cos weight × the
    z-test of its centroid, at 256²; not the silhouette's erosion or the depth steps `texel_weights` also leaves
    out, so `seen_after` is an upper bound) reaches COVER. Candidates: 64 directions spread evenly over the upper
    half of the sphere (no views from below: a prop's floor side is not seen in a game, and a redraw from below
    invents; add when undersides show), on a model wider than tall only up to 14.5° above level (y ≤ 0.25:
    plan_views' rule, no redraw from above onto a chest's lid or a wall's coping, where it invented tiles; a flat
    top takes nothing from there, cos_weight's 0.25). Each round the one that makes the most poorly seen surface
    seen, Σ area · (weight − best) · cos over those faces (the more squarely, the more texels its redraw has),
    while that is 1 % of the surface or more; its window fitted to them, at least the auto windows' size e and at
    most about 2e (half their zoom: the faces further than e from their gain-weighted middle are left to the next
    rounds). Returns the added views and the area share seen before and after them."""
    import torch

    from steps._views import cos_weight, ortho, visible, zbuffer

    tri = v[faces]
    cr = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area = 0.5 * np.linalg.norm(cr, axis=1)
    nrm = cr / (2 * area[:, None]).clip(1e-12)
    mid = tri.mean(1)
    f_t = torch.tensor(faces, dtype=torch.long, device=dev)

    def weights(yaw, pitch, center, extent):
        """[F] each face's paint weight in the view (either winding), and its cos."""
        cam = ortho(yaw, pitch, center, extent)
        pix_v, depth_v = (
            torch.tensor(x, dtype=torch.float32, device=dev) for x in cam["project"](v)
        )
        zbuf = zbuffer(pix_v, depth_v, f_t, 256)[2]
        pix, depth = cam["project"](mid)
        seen = visible(
            zbuf,
            torch.tensor(pix.clip(0, 1), dtype=torch.float32, device=dev),
            torch.tensor(depth, dtype=torch.float32, device=dev),
            depth_v,
        )
        cos = np.abs((nrm * cam["towards"](mid)).sum(1))
        return cos_weight(cos) * seen.cpu().numpy() * ((pix >= 0) & (pix <= 1)).all(1), cos

    total = float(area.sum()) or 1.0
    best = np.zeros(len(faces))
    for w in views:
        best = np.maximum(best, weights(w["yaw"], w["pitch"], w["center"], w["extent"])[0])
    before = float(area[best >= COVER].sum() / total)
    seen = {"seen_before": round(before, 3), "seen_after": round(before, 3)}
    if area[best < COVER].sum() < 0.01 * total:  # no view can add 1 %
        return [], seen
    lo, hi = v.min(0), v.max(0)
    centre, span = (lo + hi) / 2, 1.1 * float(np.linalg.norm(hi - lo))
    top = 1.0 if _tall(lo, hi) else 0.25
    n = 64
    cands = []
    # a Fibonacci spiral over the upper hemisphere (up to y = top): d is the direction from the model to the camera
    for i in range(n):
        y = (i + 0.5) / n * top
        phi = i * math.pi * (3 - math.sqrt(5))
        d = (math.sqrt(1 - y * y) * math.cos(phi), y, math.sqrt(1 - y * y) * math.sin(phi))
        yaw, pitch = math.degrees(math.atan2(d[0], d[2])) % 360, -math.degrees(math.asin(d[1]))
        cands.append((yaw, pitch, *weights(yaw, pitch, centre, span)))
    added = []
    for _ in range(extra):
        poor = best < COVER
        gains = [area * (q - best) * cos * (poor & (q >= COVER)) for _, _, q, cos in cands]
        k = int(np.argmax([g.sum() for g in gains]))
        if gains[k].sum() < 0.01 * total:
            break
        g = gains[k]
        yaw, pitch, *_ = cands.pop(k)
        # the faces it makes seen within e of their gain-weighted middle, on the view's plane
        xy = ortho(yaw, pitch, [0, 0, 0], 1.0)["project"](mid)[0]
        near = (g > 0) & (np.abs(xy - (g[:, None] * xy).sum(0) / g.sum()).max(1) <= e)
        if not near.any():  # the middle in a gap between them: round the face that gains most
            near = (g > 0) & (np.abs(xy - xy[np.argmax(g)]).max(1) <= e)
        # fitted to their centroids: a long face's corners would widen it past 2e
        c, ext = _fit(yaw, pitch, mid[near].mean(0), e, mid[near])
        added.append(
            {"yaw": round(yaw, 1), "pitch": round(pitch, 1), "center": c, "extent": ext, "part": ""}
        )
        # what the fitted window itself sees: the faces outside it are not covered by it
        best = np.maximum(best, weights(yaw, pitch, c, ext)[0])
    seen["seen_after"] = round(float(area[best >= COVER].sum() / total), 3)
    return added, seen


def face_mask(src: Path, size: int) -> np.ndarray | None:
    """Texels the chain's `project` changed (its base colour vs its input's, |Δ| > FACE_DELTA), if the chain
    from `src` up to it changed no mesh (only project/refine versions); else None."""
    d = src
    while True:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        if meta["step"] == "project":
            break
        if meta["step"] != "refine" or not meta.get("parent"):
            return None
        d = d.parent / meta["parent"]
    parent = d.parent / meta["parent"]
    pmeta = json.loads((parent / "meta.json").read_text(encoding="utf-8"))
    if "basecolor" not in meta["outputs"] or "basecolor" not in pmeta["outputs"]:
        return None
    a = np.asarray(Image.open(d / meta["outputs"]["basecolor"]).convert("RGB"), np.int16)
    b = np.asarray(Image.open(parent / pmeta["outputs"]["basecolor"]).convert("RGB"), np.int16)
    if a.shape != b.shape:
        return None
    m = np.abs(a - b).max(-1) > FACE_DELTA
    if m.shape[0] != size:
        m = np.asarray(Image.fromarray(m).resize((size, size), Image.NEAREST))
    return m if m.any() else None


def _params(params: dict) -> dict:
    p = {
        "strength": params.get("strength", 1.0),
        "face_strength": params.get("face_strength", 0.5),
        "scale": params.get("scale", 0.85),
        "zoom": params.get("zoom", "auto"),
        "views": params.get("views", "auto"),
        "texture_size": params.get("texture_size", "keep"),
        "tile": params.get("tile"),  # None: full when it is downloaded and fits, else lite (_tile)
        "seed": params.get("seed", random.randrange(2**31)),
        "prompt": params.get("prompt"),
        "delight": params.get("delight", 0.0),
        "coverage": params.get("coverage", 3),
    }
    bad = []
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    if not (num(p["delight"]) and 0 <= p["delight"] <= 1):
        bad.append("delight 0–1")
    if not (type(p["coverage"]) is int and 0 <= p["coverage"] <= 8):
        bad.append("coverage 0–8 views")
    if not (num(p["strength"]) and 0.3 <= p["strength"] <= 1.0):
        bad.append("strength 0.3–1.0")
    if not (num(p["face_strength"]) and 0.1 <= p["face_strength"] <= 1.0):
        bad.append("face_strength 0.1–1.0")
    if not (num(p["scale"]) and 0.5 <= p["scale"] <= 1.0):
        bad.append("scale 0.5–1.0")
    if p["zoom"] != "auto" and not (num(p["zoom"]) and p["zoom"] >= 0.5):
        bad.append('zoom "auto" or ≥ 0.5')
    if p["tile"] is not None and p["tile"] not in TILES:
        bad.append('tile "full" or "lite"')
    if p["texture_size"] not in ("keep", 4096):
        bad.append('texture_size "keep" or 4096')
    if not isinstance(p["seed"], int) or isinstance(p["seed"], bool):
        bad.append("seed an integer")
    if p["prompt"] is not None and not (isinstance(p["prompt"], str) and p["prompt"].strip()):
        bad.append("prompt a text")
    views = p["views"]
    if views != "auto":
        ok = (
            isinstance(views, list)
            and (views or (num(p["delight"]) and p["delight"] > 0))  # [] only delights
            and all(
                isinstance(w, dict)
                and num(w.get("yaw"))
                and num(w.get("pitch", 0))
                and isinstance(w.get("center"), list)
                and len(w["center"]) == 3
                and all(num(x) for x in w["center"])
                and num(w.get("extent"))
                and w["extent"] > 0
                and (
                    w.get("prompt") is None or isinstance(w["prompt"], str) and w["prompt"].strip()
                )
                for w in views
            )
        )
        if not ok:
            bad.append(
                'views "auto" or a list of {yaw, pitch, center: [x, y, z], extent > 0, prompt?} ([] only with '
                "delight > 0)"
            )
    if bad:
        raise StepError("bad_params", f"bad refine params ({'; '.join(bad)}): {params}")
    return p


def colour_match(rgb, a, g, sigma: float):
    """The bake's colours, the model's detail: g + blur(render) − blur(g) inside the silhouette a (normalised
    convolution, `project.shading_offset`'s method). rgb, g [H,W,3] 0–1, a [H,W] → [1,3,H,W]."""
    import torch

    from steps._views import blur_premul

    a1 = a[..., None]
    lb = blur_premul(torch.cat([rgb * a1, a1], -1).permute(2, 0, 1)[None], sigma)
    lg = blur_premul(torch.cat([g * a1, a1], -1).permute(2, 0, 1)[None], sigma)
    shift = lb[:, :3] / lb[:, 3:].clamp(min=1e-3) - lg[:, :3] / lg[:, 3:].clamp(min=1e-3)
    return (g.permute(2, 0, 1)[None] + shift).clamp(0, 1)


LUMA = (0.299, 0.587, 0.114)


def _low(x, cov):
    """[H,W] lightly blurred inside the covered texels (σ = size/512: the 32-ray AO is noisy)."""
    import torch

    from steps._views import blur_premul

    c = cov.float()
    b = blur_premul(torch.stack([x * c, c])[None], cov.shape[0] / 512, k=1)
    return b[0, 0] / b[0, 1].clamp(min=1e-3)


def ao_luma_corr(rgb, ao, cov) -> float:
    """M43c's metric: the correlation of the base colour's luminance with the AO over the covered texels, both
    lightly blurred; shadows baked into the colour make it positive, 0 = none left."""
    x = _low((rgb * rgb.new_tensor(LUMA)).sum(-1), cov)[cov]
    y = _low(ao, cov)[cov]
    x, y = x - x.mean(), y - y.mean()
    return float((x * y).sum() / (x.square().sum() * y.square().sum()).sqrt().clamp(min=1e-12))


def delight(rgb, ao, cov, amount: float):
    """M43c: the shadows the bake took into the base colour, divided out again. Over the covered texels the
    luminance is regressed on the AO: L ≈ α + β·ao; each texel is divided by lerp(1, fit, amount),
    fit = (α + β·ao) / (α + β) the share of its brightness the occlusion explains (1 where nothing occludes,
    never below 0.25); one factor for R, G and B keeps the hue. The raw per-texel AO, not a blurred one: it is
    what the engine multiplies back in, and a blur in UV space leaks across chart borders (seam delta ×3 on the
    bench). rgb [H,W,3] 0–255, ao [H,W] 0–1, cov [H,W] bool → rgb'."""
    import torch

    a, lum = ao, (rgb * rgb.new_tensor(LUMA)).sum(-1)
    am, lm = a[cov].mean(), lum[cov].mean()
    beta = ((a[cov] - am) * (lum[cov] - lm)).sum() / (a[cov] - am).square().sum().clamp(min=1e-12)
    alpha = lm - beta * am
    fit = torch.ones_like(a)
    if beta > 0 and alpha + beta > 0:  # darker where occluded; else nothing to take out
        fit = ((alpha + beta * a) / (alpha + beta)).clamp(0.25, 1)
    out = rgb.clone()
    out[cov] = (rgb[cov] / (1 - amount * (1 - fit[cov]))[:, None]).clamp(0, 255)
    return out


def paint(acc, wsum, wmax, wt, col, orig):
    """One view into the texels (in place on the accumulators): per texel the views blend by weight⁴ (the
    best view wins, no ghosting from equal averaging), faded into the original by the best weight so far."""
    w4 = wt**4
    acc += w4[:, None] * col
    wsum += w4
    wmax.copy_(wmax.maximum(wt))
    blend = acc / wsum.clamp(min=1e-12)[:, None]
    return orig * (1 - wmax[:, None]) + blend * wmax[:, None]


class _Tile:
    """Z-Image-Turbo + the Tile control net on the GPU, one model resident (research 14 §4): DiT fp8 storage,
    the control net's own layers fp8, prompts encoded with the text encoder alone on the card."""

    def __init__(self, tile: str = "lite"):
        import torch
        from diffusers import ZImageControlNetModel, ZImageControlNetPipeline
        from diffusers.hooks import apply_layerwise_casting
        from safetensors.torch import load_file

        from steps.concepts import MODEL, REVISION

        if not torch.cuda.is_available():
            raise StepError("failed", "this step needs a CUDA GPU")
        root = Path(os.environ.get("EFFIGY_MODELS", Path(__file__).resolve().parents[1] / "models"))
        file, places = TILES[tile]
        path = root / TILE_DIR / file
        if not path.is_file():  # no path in the message: it ends up in the library's meta.json
            raise StepError(
                "missing_input",
                "the full Tile is not installed (Setup → Texture detail, full: for cards with 24 GB of video "
                "memory); use tile lite"
                if tile == "full"
                else "the `refine` model group is not installed (Setup → Texture detail)",
            )
        # an explicit config + load_state_dict: from_single_file picks the wrong config (research 14 §4)
        cn = ZImageControlNetModel(
            control_layers_places=places,
            control_refiner_layers_places=[0, 1],
            add_control_noise_refiner="control_noise_refiner",
            control_in_dim=33,
        )
        cn.load_state_dict(load_file(path), strict=True)
        cn = cn.to(torch.bfloat16)
        try:
            pipe = ZImageControlNetPipeline.from_pretrained(
                MODEL, revision=REVISION, controlnet=cn, torch_dtype=torch.bfloat16
            )
        except OSError as e:  # not downloaded: no path in the message (it ends up in meta.json)
            raise StepError(
                "missing_input", "the Z-Image model is not installed (Setup → the `concepts` group)"
            ) from e
        f8, bf = torch.float8_e4m3fn, torch.bfloat16
        pipe.transformer.enable_layerwise_casting(storage_dtype=f8, compute_dtype=bf)
        # the control net's own layers only: casting the whole net clashes with hooks it shares
        for mod in (pipe.controlnet.control_layers, pipe.controlnet.control_noise_refiner):
            apply_layerwise_casting(mod, storage_dtype=f8, compute_dtype=bf)
        pipe.set_progress_bar_config(disable=True)
        self.pipe, self.torch = pipe, torch
        self.models = [
            {"id": MODEL, "revision": REVISION},
            {"id": TILE, "revision": TILE_REVISION, "file": file},
        ]

    def encode(self, prompts: list[str]) -> dict:
        torch, pipe = self.torch, self.pipe
        pipe.text_encoder.to("cuda")
        out = {}
        with torch.inference_mode():
            for p in prompts:  # one at a time: the encoder's peak stays that of one prompt
                out[p] = pipe.encode_prompt([p], device="cuda", do_classifier_free_guidance=False)[
                    0
                ][0]
        pipe.text_encoder.to("cpu")
        torch.cuda.empty_cache()
        for m in (pipe.transformer, pipe.controlnet, pipe.vae):
            m.to("cuda")
        return out

    def __call__(
        self, ctrl: np.ndarray, emb, strength: float, scale: float, seed: int
    ) -> np.ndarray:
        """ctrl [h, w, 3] uint8 (h, w multiples of 16) → the generated picture, uint8. strength < 1: img2img, the 8-step schedule
        entered at step round(8·(1−s)) with the render's latent noised to that σ."""
        from diffusers.pipelines.z_image import pipeline_z_image_controlnet as P

        torch, pipe = self.torch, self.pipe
        img = Image.fromarray(ctrl)
        h, w = ctrl.shape[:2]
        n, latents, sigmas = 8, None, None
        if strength < 1.0:
            k = round(n * (1 - strength))
            sigmas = P.get_default_z_image_sigmas(n)[k:]
            cfg = pipe.scheduler.config
            mu = P.calculate_shift(
                (h // 16) * (w // 16),
                cfg.get("base_image_seq_len", 256),
                cfg.get("max_image_seq_len", 4096),
                cfg.get("base_shift", 0.5),
                cfg.get("max_shift", 1.15),
            )
            pipe.scheduler.set_timesteps(sigmas=sigmas, device="cuda", mu=mu)
            sig = float(
                pipe.scheduler.sigmas[0]
            )  # the same shifted schedule the pipeline sets again
            with torch.inference_mode():
                x = pipe.image_processor.preprocess(img, height=h, width=w).to(
                    "cuda", pipe.vae.dtype
                )
                x0 = pipe.vae.encode(x).latent_dist.mode()
                x0 = (x0 - pipe.vae.config.shift_factor) * pipe.vae.config.scaling_factor
            g = torch.Generator("cuda").manual_seed(seed)
            noise = torch.randn(x0.shape, generator=g, device="cuda", dtype=torch.float32)
            latents = sig * noise + (1 - sig) * x0.float()
        out = pipe(
            prompt_embeds=[emb],
            height=h,
            width=w,
            num_inference_steps=n if sigmas is None else len(sigmas),
            sigmas=sigmas,
            guidance_scale=0.0,  # Turbo is distilled without CFG
            control_image=img,
            controlnet_conditioning_scale=scale,
            latents=latents,
            generator=torch.Generator("cpu").manual_seed(seed),
        ).images[0]
        return np.asarray(out.convert("RGB"))


def _tile(asked: str | None) -> str:
    """The Tile to load: the one asked for, else full when its file is there and this run's budget holds it."""
    if asked is not None:
        return asked
    root = Path(os.environ.get("EFFIGY_MODELS", Path(__file__).resolve().parents[1] / "models"))
    full = (root / TILE_DIR / TILES["full"][0]).is_file()
    return "full" if full and (budget_gb() or 0) >= FULL_GB else "lite"


def _generator(tile: str = "lite"):
    """The view generator; tests replace it (an identity) to run everything else on the CPU."""
    return _Tile(tile)


def _band_energy(x, mask):
    """Structure-weighted band energy of the luminance inside mask: the band-pass (σ 1 − σ 4 px: detail at
    2–8 px scales) squared, weighted per pixel by how coherent the gradients around it are (the structure
    tensor's anisotropy over σ 2 px: about 0.1 for white noise and 0.3 for blurred blotches, 1 for a stroke,
    an edge, a seam of stitching, measured on synthetic images). The plain mean band energy counted the bake's
    speckle as detail: it fell on four of the five bench assets when the redraw replaced speckle with drawn
    structure (docs/bench/m43b.md); this one is measured on the bench renders next."""
    import torch

    from steps._views import blur_premul

    if not bool(mask.any()):
        return 0.0
    y = (x[..., :3] * torch.tensor([0.299, 0.587, 0.114], device=x.device)).sum(-1)
    a = mask.float()
    t = torch.stack([y * a, a])[None]
    f1, f4 = blur_premul(t, 1.0, k=1), blur_premul(t, 4.0, k=1)
    band = f1[0, 0] / f1[0, 1].clamp(min=1e-3) - f4[0, 0] / f4[0, 1].clamp(min=1e-3)
    gy, gx = torch.gradient(band)  # the products below carry the mask: no step at its edge
    j = blur_premul(torch.stack([gx * gx * a, gy * gy * a, gx * gy * a, a])[None], 2.0, k=1)[0]
    jxx, jyy, jxy = (j[i] / j[3].clamp(min=1e-3) for i in range(3))
    tr = jxx + jyy
    gap = ((jxx - jyy) ** 2 + 4 * jxy**2).sqrt()  # λ1 − λ2
    coherence = (gap / tr.clamp(min=1e-12)) ** 2
    return float((band**2 * coherence)[mask].mean())


def run(op, in_dir, out_dir, params):
    import torch
    import torch.nn.functional as F
    import trimesh

    from steps._bake import fill_gutters, heal_seams, rasterize_uv, seam_delta, seams
    from steps._views import blur_premul, ortho, render, texel_weights, write_textured

    if not in_dir:
        raise StepError(
            "missing_input", "refine needs a textured, UV-mapped version (bake, project, refine)"
        )
    p = _params(params)
    t0 = time.time()
    src, out = Path(in_dir), Path(out_dir)
    meta = json.loads((src / "meta.json").read_text(encoding="utf-8"))
    glb = next((f for f in meta["outputs"].values() if f.endswith(".glb")), None)
    if not glb:
        raise StepError("missing_input", f"{meta['version']} has no .glb")
    mesh = trimesh.load(src / glb, force="mesh", process=False)
    mat = getattr(mesh.visual, "material", None)
    if getattr(mesh.visual, "uv", None) is None or getattr(mat, "baseColorTexture", None) is None:
        raise StepError(
            "missing_input", f"{glb} has no UVs or no base colour texture: run uv and bake first"
        )
    base = mat.baseColorTexture.convert("RGBA")
    if base.width != base.height:
        raise StepError("missing_input", f"{glb}: the base colour texture is not square")
    # M43a's bake declares its ORM as the occlusion texture when it has AO
    occ = getattr(mat, "occlusionTexture", None)
    if p["delight"] and occ is None:
        raise StepError(
            "missing_input",
            f"{glb} has no ambient occlusion to take the shadows out with: bake it again (AO on)",
        )
    if p["texture_size"] == 4096 and base.width < 4096:
        base = base.resize((4096, 4096), Image.LANCZOS)
    base = np.asarray(base).copy()
    size = base.shape[0]
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    v_np = np.asarray(mesh.vertices, float)
    faces_np = np.asarray(mesh.faces)
    uv_np = np.asarray(mesh.visual.uv, float).copy()
    uv_np[:, 1] = 1 - uv_np[:, 1]  # trimesh's OpenGL UVs → glTF UVs (what the file holds)
    prompt = (p["prompt"] or asset_prompt(src)).strip()
    zoom = ZOOM if p["zoom"] == "auto" else float(p["zoom"])
    views = plan_views(v_np, faces_np, uv_np, size, zoom) if p["views"] == "auto" else p["views"]
    seen = {}
    if p["views"] == "auto" and p["coverage"]:
        progress(0.01, "planning views for the surface the others miss")
        e = VIEW / (zoom * texel_density(v_np, faces_np, uv_np, size))
        more, seen = coverage_views(v_np, faces_np, views, p["coverage"], e, dev)
        views += more
        seen["coverage_views"] = len(more)
        if dev == "cuda":
            torch.cuda.empty_cache()  # the planning's z-buffers, before the model's 12–15.6 GB
    for w in views:
        w.setdefault("pitch", 0)
        w.setdefault(
            "part", ""
        )  # a user's window (Redraw this view) names no part; the auto views do

    gen, tile = None, None
    if views:
        tile = _tile(p["tile"])
        progress(0.02, f"loading the detail model ({len(views)} views planned, the {tile} Tile)")
        gen = _generator(tile)
    seconds_load = time.time() - t0
    if dev == "cuda":
        torch.cuda.reset_peak_memory_stats()
    # a view's own prompt for a side made of something else (the hoarding's bare plywood back under a prompt for its
    # painted front: blue panels on every back view)
    prompts = {
        i: ", ".join(
            filter(
                None,
                [
                    f"{(w.get('prompt') or prompt).strip()}{SUFFIX}",
                    w["part"],
                    _yaw_name(w["yaw"], w["pitch"]),
                ],
            )
        )
        for i, w in enumerate(views)
    }
    if views:
        progress(0.15, "encoding the prompts")
        embeds = gen.encode(sorted(set(prompts.values())))

    f = torch.tensor(faces_np, dtype=torch.long, device=dev)
    uv = torch.tensor(uv_np, dtype=torch.float32, device=dev)
    face_id, bary = rasterize_uv(uv, f, size)
    covered = face_id >= 0
    # the closing passes (seams, gutters) use bake's coverage (M43a: the raster at 2×, a texel covered when any
    # quarter is; 1× at 4096): with the texel-centre mask the gutter fill rewrote bake's edge ring and the seam
    # delta rose ×3 on an unchanged texture (docs/bench/m43c.md)
    ss = 1 if size == 4096 else 2
    wide = (rasterize_uv(uv, f, size * ss)[0] >= 0).view(size, ss, size, ss).any(3).any(1)
    cov = wide.cpu().numpy()
    seam = seams(v_np, faces_np, uv_np, size)
    corners, w3 = f[face_id[covered]], bary[covered][..., None]
    pts = (
        (torch.tensor(v_np, dtype=torch.float32, device=dev)[corners] * w3)
        .sum(1)
        .cpu()
        .numpy()
        .astype(float)
    )
    fmask = face_mask(src, size)
    keep = torch.ones(len(pts), device=dev)
    if fmask is not None:
        keep[torch.from_numpy(fmask).to(dev)[covered]] = 0
    face_t = torch.zeros(size, size, 1, device=dev)
    if fmask is not None:
        face_t[torch.from_numpy(fmask).to(dev)] = 255
    cur = torch.tensor(base, dtype=torch.float32, device=dev)
    ao = None
    if occ is not None:
        ao = np.asarray(occ.convert("RGB").resize((size, size), Image.BILINEAR))[..., :1]
        # the two sides of a UV seam get one factor: the AO levelled there as the colours are
        if p["delight"]:
            ao = heal_seams(ao, cov, seam)
        ao = torch.tensor(ao[..., 0] / 255, dtype=torch.float32, device=dev)
    if p["delight"]:  # before the views: they draw on the flatter colours, as the prompt asks
        cur[..., :3] = delight(cur[..., :3], ao, covered, p["delight"])
    # drift, detail and the views' "before": what the views started from
    base_lit = cur.round().clamp(0, 255).to(torch.uint8).cpu().numpy()
    orig = cur[covered][:, :3].clone()
    n_t = len(orig)
    acc, wsum, wmax = (
        torch.zeros(n_t, 3, device=dev),
        torch.zeros(n_t, device=dev),
        torch.zeros(n_t, device=dev),
    )
    done, face_views, t_views = [], 0, time.time()
    sigma = 0.025 * VIEW
    pool = 2 * ERODE + 1  # the metrics' inner silhouette

    for i, w in enumerate(views):
        progress(0.2 + 0.7 * i / len(views), f"view {i + 1}/{len(views)}")
        cam = ortho(w["yaw"], w["pitch"], w["center"], w["extent"])
        rgb, a, extra, (_, _, zbuf, _, depth_v) = render(
            torch.cat([cur[..., :3], face_t], -1), uv, f, v_np, cam, VIEW
        )
        if float(a.mean()) < 0.005:  # this window sees no surface
            continue
        face = fmask is not None and float((extra[..., 0] * a).sum()) > 0.003 * float(a.sum())
        face_views += face
        ctrl = (rgb * 255 + 0.5).byte().cpu().numpy()
        if (
            dev == "cuda"
        ):  # the renders' and the last view's blocks back to the driver: the model needs them
            torch.cuda.empty_cache()
        g = gen(
            ctrl,
            embeds[prompts[i]],
            p["face_strength"] if face else p["strength"],
            p["scale"],
            p["seed"] + i,
        )
        g = torch.tensor(g, dtype=torch.float32, device=dev) / 255
        matched = colour_match(rgb, a, g, sigma)
        wt, grid = texel_weights(cam, a, zbuf, depth_v, pts, v_np, faces_np, corners, w3)
        wt = (
            wt * keep
        )  # the projected face stays: redrawn, it became another person (docs/bench/m43b.md)
        col = (
            F.grid_sample(
                matched, grid, mode="bilinear", padding_mode="border", align_corners=False
            )[0, :, 0].T
            * 255
        )
        cur[covered] = torch.cat([paint(acc, wsum, wmax, wt, col, orig), cur[covered][:, 3:]], 1)
        done.append(w)
    seconds_views = time.time() - t_views
    if views and not done:
        raise StepError("failed", "no view saw the surface")

    progress(0.92, "measuring and writing")
    rgba = cur.round().clamp(0, 255).to(torch.uint8).cpu().numpy()
    # views paint the two sides of a UV seam from different pixels: level them as bake does (M43a)
    rgba[..., :3] = heal_seams(rgba[..., :3], cov, seam)
    rgba = fill_gutters(rgba, cov)
    w_img = torch.zeros(size, size, 1, device=dev)
    w_img[covered] = wmax[:, None] * 255
    before_t = torch.cat(
        [torch.tensor(base_lit, dtype=torch.float32, device=dev)[..., :3], w_img], -1
    )
    after_t = torch.cat([torch.tensor(rgba, dtype=torch.float32, device=dev)[..., :3], w_img], -1)
    thumbs, drift, e_before, e_after = [], [], 0.0, 0.0
    for w in done:
        cam = ortho(w["yaw"], w["pitch"], w["center"], w["extent"])
        r0, a0, ex, _ = render(before_t, uv, f, v_np, cam, VIEW)
        r1, _, _, _ = render(after_t, uv, f, v_np, cam, VIEW)
        m = (a0 > 0.99) & (ex[..., 0] > 0.5)
        if bool(m.any()):
            a1 = a0[..., None]
            l0 = blur_premul(torch.cat([r0 * a1, a1], -1).permute(2, 0, 1)[None], sigma)
            l1 = blur_premul(torch.cat([r1 * a1, a1], -1).permute(2, 0, 1)[None], sigma)
            d = (
                (l0[0, :3] / l0[0, 3:].clamp(min=1e-3) - l1[0, :3] / l1[0, 3:].clamp(min=1e-3))
                .abs()
                .mean(0)
            )
            drift.append(float(d[m].mean()) * 255)
            inner = -F.max_pool2d(-(a0 > 0.5).float()[None, None], pool, 1, ERODE)[0, 0] > 0.5
            e_before += _band_energy(r0, inner & m)
            e_after += _band_energy(r1, inner & m)
        thumbs.append([(x * 255 + 0.5).byte().cpu().numpy() for x in (r0, r1)])
    if thumbs:
        _views_png(thumbs, out / "views.png")

    def corr(img):  # None: the input has no AO (pre-M43a or a CPU bake)
        if ao is None:
            return None
        t = torch.tensor(img[..., :3], dtype=torch.float32, device=dev)
        return round(ao_luma_corr(t, ao, covered), 3)

    peak = torch.cuda.max_memory_allocated() / 2**30 if dev == "cuda" else 0.0
    reserved = torch.cuda.max_memory_reserved() / 2**30 if dev == "cuda" else 0.0
    metrics = {
        "views": len(done),
        "views_planned": len(views),
        "face_views": int(face_views),
        "seconds": round(time.time() - t0, 1),
        "seconds_load": round(seconds_load, 1),
        "seconds_per_view": round(seconds_views / len(done), 1) if done else None,
        "peak_vram_gb": round(peak, 2),
        "peak_reserved_gb": round(reserved, 2),
        "refined": round(float((wmax > 0.5).float().mean()), 3),
        "drift": round(float(np.mean(drift)), 2) if drift else None,
        "detail": round(e_after / e_before, 2) if e_before > 0 else None,
        "detail_metric": "coherence",  # since 2026-10-10 (_band_energy); older versions: the plain band energy
        "seam_delta_before": round(seam_delta(base[..., :3], seam), 3),
        "seam_delta": round(seam_delta(rgba[..., :3], seam), 3),
        "texture_size": size,
        "tile": tile,  # M46c: the control net used (None: no views, no model)
        "zoom": zoom,
        "seed": p["seed"],
        "prompt": prompt,
        "views_used": [{k: w[k] for k in ("yaw", "pitch", "center", "extent")} for w in done],
        "delight": p["delight"],
        "ao_luma_corr_before": corr(base),
        "ao_luma_corr": corr(rgba),
        **seen,  # M66: coverage_views, seen_before, seen_after
    }
    outputs = write_textured(out, mesh, mat, rgba, {"views": "views.png"} if thumbs else {})
    models = getattr(gen, "models", [])
    return {
        "outputs": outputs,
        "metrics": metrics,
        "models": models,
        "licenses": [m["id"] for m in models],
    }


def _views_png(thumbs: list, path: Path, cell: int = 256, cols: int = 4) -> None:
    """Each view before | after, `cols` pairs a row, on the renders' grey."""
    rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (cols * 2 * cell + (cols - 1) * 8, rows * cell), (40, 40, 40))
    for i, pair in enumerate(thumbs):
        x, y = (i % cols) * (2 * cell + 8), (i // cols) * cell
        for j, im in enumerate(pair):
            sheet.paste(Image.fromarray(im).resize((cell, cell), Image.LANCZOS), (x + j * cell, y))
    sheet.save(path)


if __name__ == "__main__":
    main(run)
