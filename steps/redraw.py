"""Upscale or redraw an existing picture (IMPLEMENTATION.md M61, 2D track): Real-ESRGAN x4plus, and/or Z-Image-Turbo +
Fun-ControlNet Tile 2.1 (lite) drawing the picture again, window by window, at any size.

Input: any version with a picture (`image`, or `key` = "image_<k>": an upload, concepts, pixelize...).
op upscale: params {factor: 2 | 4 (default 4), key}. Real-ESRGAN x4 in overlapping windows blended with a smooth
  (raised-cosine) weight, accumulated in RAM; factor 2 = x4 then Lanczos down by 2. Needs the `texture` model group.
op redraw: params {prompt (required, used exactly as given; Z-Image-Turbo runs without guidance, so there is no
  negative prompt), strength: 0.2-1 (default 0.35, how far the picture is drawn again; img2img from the picture
  itself), scale: 0.5-1 (default 0.85, the Tile control net's weight), factor: 1 | 2 | 4 (default 1: upscale first,
  then redraw at that size), seed, lora ("pixel" | "painting" | "drawing", concepts' styles) + lora_scale (0, 2],
  window: 256-1024, a multiple of 64 (default 1024; with a lora at most and by default 768 unless the GPU budget is
  17 GB or more: 1024 spilled the 16 GB card to shared memory, 105 s a window), overlap: 0-0.5 of a window (default
  0.25), protect: the name
  of a sibling version of this asset whose `image` is a greyscale mask (white = keep the picture, black = redraw
  fully, grey in between; resized to the output; a window the mask covers wholly is not drawn at all), key}.
  The picture is cut into overlapping windows (the last row and column shifted inward), each drawn from itself (its
  own img2img start and Tile control image) and blended into one picture: the card holds one window, so the VRAM
  does not depend on the picture's size (the lite Tile at 1024 px peaks at 12.4 GB; only the lite one is used).
  A picture smaller than the window is padded to a multiple of 64 and cropped back.
Alpha: kept. A picture with a see-through pixel is drawn on mid-grey, its alpha resized with Lanczos and put back.
Outputs: preview.png (the picture fitted to 2048 px), image.png (the result, any size: pictures past PIL's pixel
limit are fine here), report.json. preview.png is listed first: viewers that show the first picture show the small one.
report.json (and metrics): {op, factor, size [w, h], input_size, windows, seconds, drift, thresholds, pass}; redraw
also {window, overlap, seconds_per_window, peak_vram_gb, drift_max, seam_ratio, skipped, per_window: [{x, y, w, h,
drift}]}.
  drift = mean |blur(output reduced to the input's size) - blur(input)| over the colour channels (0-255; the mask
    keeps its own areas at 0); drift_max = the worst window's; seam_ratio = mean grey step across the lines half way
    through the windows' overlaps over the mean step elsewhere (about 1 = no visible seam).
  pass = {drift <= DRIFT_PASS 12, window_drift <= WINDOW_DRIFT_PASS 25, seam <= SEAM_PASS 1.25}: advisory, a high
    strength fails its drift on purpose.
Runs one-shot (not resident: refine's rule, `concepts` is the resident Z-Image). Real-ESRGAN is freed before
Z-Image loads.
"""

import gc
import json
import os
import random
import re
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageFilter

from steps import _sr, concepts
from steps._common import StepError, budget_gb, main, progress

# a 4x upscale of a wide picture passes PIL's default 179 MP limit (8352 x 2400 x 4 = 321 MP): raised, not lifted
# (PIL refuses past twice this). ponytail: MAX_PX is the output size the RAM holds (float32 accumulator, 12 B/px, ~6 GB
# at 500 MP, ~10 GB in all); a tiled-to-disk blend if a bigger print is ever needed.
MAX_PX = 500_000_000
Image.MAX_IMAGE_PIXELS = 600_000_000

DRIFT_PASS, WINDOW_DRIFT_PASS, SEAM_PASS = 12.0, 25.0, 1.25
GREY = 127  # what a see-through area is drawn on
PREVIEW = 2048
# a style LoRA at a 1024 window filled the 16 GB card (15.9 GB, spilled to shared memory: 105 s a window, 2026-10-16);
# 768 peaked at 10.6. A budget of LORA_ROOM_GB or more (a bigger card, M46a) keeps 1024.
LORA_WINDOW, LORA_ROOM_GB = 768, 17.0


def _roomy(params: dict) -> bool:
    return not params.get("lora") or (budget_gb() or 0) >= LORA_ROOM_GB


def _params(op: str, params: dict) -> dict:
    num = lambda x: isinstance(x, int | float) and not isinstance(x, bool)
    if op not in ("upscale", "redraw"):
        raise StepError("bad_params", f"op {op!r}: upscale or redraw")
    up = op == "upscale"
    p = {
        "factor": params.get("factor", 4 if up else 1),
        "key": params.get("key", "image"),
        "prompt": params.get("prompt"),
        "strength": params.get("strength", 0.35),
        "scale": params.get("scale", 0.85),
        "seed": params.get("seed", random.randrange(2**31)),
        "lora": params.get("lora"),
        "lora_scale": params.get("lora_scale", 1.0),
        "window": params.get("window", 1024 if _roomy(params) else LORA_WINDOW),
        "overlap": params.get("overlap", 0.25),
        "protect": params.get("protect"),
    }
    ok = (
        type(p["factor"]) is int
        and p["factor"] in ((2, 4) if up else (1, 2, 4))
        and isinstance(p["key"], str)
        and p["key"].partition("_")[0] == "image"
        and (up or (isinstance(p["prompt"], str) and p["prompt"].strip()))
        and num(p["strength"])
        and 0.2 <= p["strength"] <= 1
        and num(p["scale"])
        and 0.5 <= p["scale"] <= 1
        and isinstance(p["seed"], int)
        and not isinstance(p["seed"], bool)
        and (p["lora"] is None or p["lora"] in concepts.STYLES)
        and num(p["lora_scale"])
        and 0 < p["lora_scale"] <= 2
        and isinstance(p["window"], int)
        and 256 <= p["window"] <= (1024 if _roomy(params) else LORA_WINDOW)
        and p["window"] % 64 == 0
        and num(p["overlap"])
        and 0 <= p["overlap"] <= 0.5
        and (
            p["protect"] is None
            or (isinstance(p["protect"], str) and p["protect"] == Path(p["protect"]).name != "..")
        )
    )
    if not ok:
        raise StepError("bad_params", f"see the redraw docstring ({op}): {params}")
    return p


def _picture(d: Path, key: str) -> Image.Image:
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    outs = meta.get("outputs", {})
    if key not in outs or not str(outs[key]).lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        raise StepError("missing_input", f"{meta['version']} has no picture {key!r}")
    return Image.open(d / outs[key]).convert("RGBA")


def _split(im: Image.Image) -> tuple[np.ndarray, Image.Image | None]:
    """RGB uint8 (a see-through area on grey) and the alpha as a picture, None when nothing is see-through."""
    a = np.asarray(im)
    if a[..., 3].min() == 255:
        return a[..., :3].copy(), None
    k = a[..., 3:] / 255.0
    return (a[..., :3] * k + GREY * (1 - k) + 0.5).astype(np.uint8), Image.fromarray(a[..., 3])
    # ponytail: the grey bleeds a little into half-see-through edges; premultiplied edge colours if a fringe shows


def _upscale(rgb: np.ndarray, factor: int, tick) -> np.ndarray:
    try:
        net = _sr.load()
    except FileNotFoundError as e:
        raise StepError("missing_input", str(e)) from None
    try:
        hi = _sr.upscale(net, rgb, tick=tick)
    finally:
        del net  # all of it off the card before Z-Image loads
        gc.collect()
        torch.cuda.empty_cache()
    if factor == 2:
        hi = np.asarray(
            Image.fromarray(hi).resize((rgb.shape[1] * 2, rgb.shape[0] * 2), Image.LANCZOS)
        )
    return hi


@contextmanager
def _model(p: dict):
    """(draw(window uint8, seed) -> uint8, the models used): the lite Tile pipeline with the style LoRA and the
    prompt encoded; everything freed on exit. Tests replace this."""
    from steps.refine import _Tile

    style = concepts.STYLES[p["lora"]] if p["lora"] else None
    path = concepts.style_file(p["lora"]) if style else None  # before the minute of loading
    progress(0.3, "loading Z-Image-Turbo and the Tile")
    gen = _Tile("lite")
    try:
        if style:
            for m in (
                gen.pipe.transformer.modules()
            ):  # peft would put the LoRA in fp8 (concepts._pipeline)
                if isinstance(m, torch.nn.Linear) and m.weight.dtype == torch.float8_e4m3fn:
                    m.compute_dtype = torch.bfloat16
            gen.pipe.load_lora_weights(path, adapter_name=p["lora"])
            gen.pipe.set_adapters([p["lora"]], [float(p["lora_scale"])])
        text = (style[3] if style else "") + p["prompt"]
        emb = gen.encode([text])[text]
        gen.pipe.text_encoder = None  # encoded once: its ~8 GB of CPU RAM are not needed again
        models = gen.models + ([{"id": style[0], "revision": style[1]}] if style else [])
        yield (lambda win, seed: gen(win, emb, p["strength"], p["scale"], seed)), models
    finally:
        gen.pipe = None  # the model off the card (draw's closure still holds gen)
        gc.collect()
        torch.cuda.empty_cache()


def _redraw(base: np.ndarray, p: dict, mask: np.ndarray | None):
    """base [H, W, 3] uint8 → (the redrawn picture, [(y, x, h, w)] windows, the seam lines, the models, skipped)."""
    h, w = base.shape[:2]
    # smaller than a window: padded (edge colour) to a multiple of 64, cropped back below
    ph, pw = (-(-d // 64) * 64 if d < p["window"] else d for d in (h, w))
    work = base
    if (ph, pw) != (h, w):
        work = np.pad(base, ((0, ph - h), (0, pw - w), (0, 0)), mode="edge")
    blend = _sr.Blend(ph, pw, p["window"], round(p["window"] * p["overlap"]))
    wins = blend.windows()
    skipped, drawing = 0, 0.0
    with _model(p) as (draw, models):
        for i, (y, x) in enumerate(wins):
            progress(0.35 + 0.55 * i / len(wins), f"window {i + 1}/{len(wins)}")
            win = work[y : y + blend.wh, x : x + blend.ww]
            if mask is not None and mask[y : y + blend.wh, x : x + blend.ww].min() == 255:
                patch, skipped = win, skipped + 1  # wholly protected: nothing to draw
            else:
                t = time.time()
                patch = draw(win, p["seed"] + i)
                drawing += time.time() - t
            blend.add(y, x, patch)
    ys, xs = blend.lines()
    return (
        _sr.u8(blend.result())[:h, :w],
        [(y, x, blend.wh, blend.ww) for y, x in wins],
        (ys, xs),
        models,
        skipped,
        drawing,
    )


def _mask_path(src: Path, name: str, param: str = "protect") -> Path:
    """The sibling version's mask picture (refine's `region` too, M68); StepError when it is not a version of this
    asset with an image."""
    d = src.parent / name
    try:
        if not re.fullmatch(r"\d{3}_\w+", name):  # a version's own name: no path into another asset
            raise ValueError(name)
        outs = json.loads((d / "meta.json").read_text(encoding="utf-8")).get("outputs", {})
        if (path := d / outs["image"]).is_file():
            return path
    except (OSError, KeyError, ValueError, TypeError):
        pass
    raise StepError("bad_params", f"{param} {name!r}: not a version of this asset with an image")


def _protect(path: Path, size: tuple[int, int]) -> np.ndarray:
    """The mask at the output's size, uint8 (255 = keep)."""
    return np.asarray(Image.open(path).convert("L").resize(size, Image.LANCZOS))


def _preflight(src: Path, p: dict) -> Path | None:
    """What a redraw needs on disk, checked before the minutes of upscaling: the mask, the style, the Tile."""
    from steps.refine import TILE_DIR, TILES

    mask = _mask_path(src, p["protect"]) if p["protect"] else None
    if p["lora"]:
        concepts.style_file(p["lora"])
    root = Path(os.environ.get("EFFIGY_MODELS", Path(__file__).resolve().parents[1] / "models"))
    if not (root / TILE_DIR / TILES["lite"][0]).is_file():
        raise StepError(
            "missing_input", "the `refine` model group is not installed (Setup, Texture detail)"
        )
    return mask


def _mix(red: np.ndarray, orig: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """orig where the mask is white, red where black, per pixel in between (in row chunks).
    ponytail: a post-blend, not a latent mask: the model still draws the masked area, its neighbours see that
    drawing as context, so a masked area's edge can echo the redraw; a latent-space mask (inpainting) if that shows."""
    out = np.empty_like(red)
    for r in range(0, len(red), 256):
        m = mask[r : r + 256, :, None] / 255.0
        out[r : r + 256] = np.clip(orig[r : r + 256] * m + red[r : r + 256] * (1 - m) + 0.5, 0, 255)
    return out


def _drift(src: np.ndarray, out: np.ndarray) -> np.ndarray:
    """[h0, w0] mean |blur(out reduced to src's size) - blur(src)| over the channels, 0-255."""
    h0, w0 = src.shape[:2]
    small = (
        out
        if out.shape == src.shape
        else np.asarray(Image.fromarray(out).resize((w0, h0), Image.BOX))
    )
    blur = lambda a: np.asarray(Image.fromarray(a).filter(ImageFilter.GaussianBlur(2)), np.float32)
    return np.abs(blur(small) - blur(src)).mean(-1)


def _seam_ratio(rgb: np.ndarray, ys: list[int], xs: list[int]) -> float:
    """Mean grey step across the given rows and columns over the mean step at 400 other rows and columns each."""
    g = rgb.mean(-1, dtype=np.float32)
    on, off = [], []
    for a, lines in ((g, ys), (g.T, xs)):
        rows = set(lines) | {r + 1 for r in lines}
        rest = [
            r for r in np.linspace(1, len(a) - 1, min(400, len(a) - 1), dtype=int) if r not in rows
        ]
        for idx, acc in ((lines, on), (rest, off)):
            if len(idx):
                idx = np.asarray(idx)
                acc.append(np.abs(a[idx] - a[idx - 1]).mean())
    if not on or not off:
        return 1.0
    return float((np.mean(on) + 1e-3) / (np.mean(off) + 1e-3))


def run(op, in_dir, out_dir, params):
    p = _params(op, params)
    if not in_dir:
        raise StepError("missing_input", "redraw needs a parent version with a picture")
    t0, src, out = time.time(), Path(in_dir), Path(out_dir)
    f, redrawing = p["factor"], op == "redraw"
    mask_file = _preflight(src, p) if redrawing else None
    rgb, alpha = _split(_picture(src, p["key"]))
    h0, w0 = rgb.shape[:2]
    if h0 * w0 * f * f > MAX_PX:
        raise StepError(
            "bad_params",
            f"{w0 * f} x {h0 * f} px is past the {MAX_PX // 10**6} MP output limit: a smaller factor",
        )
    models = []
    share = 0.3 if redrawing else 0.9  # of the progress bar
    n_up = [0]  # Real-ESRGAN's windows
    if f > 1:
        models.append({"id": _sr.MODEL})

        def tick(i, n):
            n_up[0] = n
            progress(0.02 + share * (i + 1) / n, f"upscaling {i + 1}/{n}")

        big = _upscale(rgb, f, tick)
    else:
        big = rgb
    h, w = big.shape[:2]
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    report = {"op": op, "factor": f, "size": [w, h], "input_size": [w0, h0]}
    wins, lines, skipped = [None] * n_up[0], None, 0
    if redrawing:
        mask = _protect(mask_file, (w, h)) if mask_file else None
        res, wins, lines, used, skipped, drawing = _redraw(big, p, mask)
        models += used
        big = _mix(res, big, mask) if mask is not None else res
        del res
    progress(0.92, "measuring and writing")
    drift = _drift(rgb, big)
    report |= {
        "windows": len(wins),  # upscale: Real-ESRGAN's, redraw: Z-Image's
        "upscale_windows": n_up[0],
        "drift": round(float(drift.mean()), 2),
        "pass": {"drift": bool(drift.mean() <= DRIFT_PASS)},
        "thresholds": {"drift": DRIFT_PASS},
    }
    if redrawing:
        per = [
            round(float(drift[y // f : -(-(y + wh) // f), x // f : -(-(x + ww) // f)].mean()), 2)
            for y, x, wh, ww in wins
        ]
        seam = round(_seam_ratio(big, *lines), 3) if len(wins) > 1 else 1.0
        peak = torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() else 0.0
        done = len(wins) - skipped
        report |= {
            "window": p["window"],
            "overlap": p["overlap"],
            "seam_ratio": seam,
            "drift_max": max(per),
            "skipped": skipped,
            "seconds_per_window": round(drawing / done, 1) if done else None,
            "peak_vram_gb": round(peak, 2),
            "peak_reserved_gb": round(torch.cuda.max_memory_reserved() / 2**30, 2)
            if torch.cuda.is_available()
            else 0.0,
            "seed": p["seed"],
            "protect": p["protect"],
            "per_window": [
                {"y": y, "x": x, "h": wh, "w": ww, "drift": d}
                for (y, x, wh, ww), d in zip(wins, per)
            ],
        }
        report["pass"] |= {"window_drift": max(per) <= WINDOW_DRIFT_PASS, "seam": seam <= SEAM_PASS}
        report["thresholds"] |= {"window_drift": WINDOW_DRIFT_PASS, "seam": SEAM_PASS}
    img = Image.fromarray(big)
    if alpha is not None:
        img.putalpha(alpha if alpha.size == (w, h) else alpha.resize((w, h), Image.LANCZOS))
    s = min(1, PREVIEW / max(w, h))
    prev = (
        img if s == 1 else img.resize((round(w * s), round(h * s)), Image.LANCZOS, reducing_gap=2.0)
    )
    prev.save(out / "preview.png")
    img.save(out / "image.png", compress_level=3)
    report["seconds"] = round(time.time() - t0, 1)
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    metrics = {k: v for k, v in report.items() if k != "per_window"}
    return {
        "outputs": {"preview": "preview.png", "image": "image.png", "report": "report.json"},
        "metrics": metrics,
        "models": models,
        "licenses": [m["id"] for m in models],
    }


if __name__ == "__main__":
    main(run)
