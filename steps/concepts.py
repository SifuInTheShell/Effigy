"""brief → N candidate images, gated and ranked (IMPLEMENTATION.md M10). Needs the GPU (Z-Image-Turbo).

op run: params {"n": 4, "seed": int, "steps": 9}. Outputs cand_<k>.png + scores.json (research 07 §3 gates).
op image (the 2D track, research 15 §5.2, M50): pictures of any aspect, no gates: params
  prompt: the text (default: the parent brief's prompt_full; one of the two is needed)
  width, height: multiples of 64 from 256 to 2048 (default 1024 × 1024; 1920 × 1088 is 16:9)
  count: 1-4 (default 1), seed: int, steps: default 9
  lora: a style ("pixel", "painting", "drawing": Apache-2.0 LoRAs, the group "styles"), lora_scale: (0, 2], default 1
  cutout: true = BiRefNet's matte into the alpha (a sprite or an item on its own)
  Outputs image_<k>.png, image.png (= image_0.png: prep, pixelize and layers find it), preview.png.
op tile (M54, research 15 §5.5): one seamless square texture: the picture made as op image, rolled by half its size so
  its edges meet in a cross through the middle, the cross inpainted with the same prompt (ZImageInpaintPipeline on
  the resident weights), rolled back. Params: prompt, seed, steps, lora, lora_scale as op image; size: a multiple of
  64 from 256 to 1024 (default 512); mend: 0.3-1 (default 0.8), how far the band is drawn again. Outputs tile.png (also the `image` the 2D steps read), preview.png (2 × 2),
  report.json {seam_ratio, seam_ratio_before, pass, size, band}; pass when seam_ratio <= 1.1 (steps/_pixel.py).
Runs resident (server/jobs.py SERVE). ponytail: candidates appear when all are done; stream them if waiting hurts.
"""

import json
import random
import time
from pathlib import Path

import numpy as np
import torch
import yaml
from PIL import Image

from steps import _birefnet, _pixel
from steps._common import StepError, main, progress
from steps._image import gates, rank

MODEL = "Tongyi-MAI/Z-Image-Turbo"  # Apache-2.0
REVISION = "f332072aa78be7aecdf3ee76d5c247082da564a6"
VARIANTS = [
    "",
    ", full body, whole figure in frame",  # "silhouette" drew black cut-outs
    ", studio product shot",
    ", crisp detail, matte materials",
]
FRAME = {
    "prop": ", the whole object in frame"
}  # a prop has no body (the owner's second agent, 2026-10-11)


# 2D styles (research 15 §7, M50): repo, revision, file, the trigger the LoRA was trained with (prepended)
STYLES = {
    "pixel": (
        "tarn59/pixel_art_style_lora_z_image_turbo",
        "0a5092d1619664d94a5a36784f92db84b3ae62bd",
        "pixel_art_style_z_image_turbo.safetensors",
        "Pixel art style. ",
    ),
    "painting": (
        "renderartist/Classic-Painting-Z-Image-Turbo-LoRA",
        "e873cc474ce517d49d09ed1f64358ff43660b276",
        "Classic_Painting_Z_Image_Turbo_v1_renderartist_1750.safetensors",
        "class1cpa1nt ",
    ),
    "drawing": (
        "ostris/z_image_turbo_childrens_drawings",
        "7fcd66a99149c58741990fca28562a4a581af7a9",
        "z_image_turbo_childrens_drawings.safetensors",
        "",
    ),
}


def variants(category: str) -> list[str]:
    """The four candidates' prompt suffixes for a brief's category."""
    return [FRAME.get(category, VARIANTS[1]) if i == 1 else v for i, v in enumerate(VARIANTS)]


_PIPE = []  # resident worker (--serve): loaded once, ~26 s saved on every later run


def _pipeline():
    if not _PIPE:
        from diffusers import ZImagePipeline, ZImageTransformer2DModel

        progress(0.02, "loading Z-Image-Turbo")
        # Quantize the large DiT before loading the text encoder. Loading both in bf16 first
        # leaves little RAM for mapped checkpoint pages on a 32 GB machine (especially external disks).
        transformer = ZImageTransformer2DModel.from_pretrained(
            MODEL,
            subfolder="transformer",
            revision=REVISION,
            dtype=torch.bfloat16,
            disable_mmap=True,
        )
        # bf16 DiT is ~12 GB: with the desktop's VRAM Windows pages it to shared memory (~3 s/step)
        transformer.enable_layerwise_casting(
            storage_dtype=torch.float8_e4m3fn, compute_dtype=torch.bfloat16
        )
        # peft puts a LoRA in its base layer's `compute_dtype` when the layer has one (its rule for quantised
        # layers), else in the weight's dtype: fp8, which no matmul takes. The 2D styles (M50) compute in bf16.
        for m in transformer.modules():
            if isinstance(m, torch.nn.Linear) and m.weight.dtype == torch.float8_e4m3fn:
                m.compute_dtype = torch.bfloat16
        pipe = ZImagePipeline.from_pretrained(
            MODEL, revision=REVISION, dtype=torch.bfloat16, transformer=transformer
        )
        _PIPE.append(pipe)
    return _PIPE[0]


def style_file(name: str) -> str:
    """The downloaded LoRA file of a 2D style (STYLES); StepError when Setup has not fetched it."""
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    repo, revision, file, _ = STYLES[name]
    try:
        return hf_hub_download(repo, file, revision=revision, local_files_only=True)
    except LocalEntryNotFoundError:
        raise StepError(
            "missing_input", f"the {name} style is not installed: Setup, 2D styles"
        ) from None


def _image_params(in_dir, params) -> dict:
    brief = {}
    if in_dir and (Path(in_dir) / "brief.yaml").is_file():
        brief = yaml.safe_load((Path(in_dir) / "brief.yaml").read_text(encoding="utf-8"))
    p = {
        "prompt": str(params.get("prompt") or brief.get("prompt_full") or "").strip(),
        "width": params.get("width", 1024),
        "height": params.get("height", 1024),
        "count": params.get("count", 1),
        "seed": params.get("seed", random.randrange(2**31)),
        "steps": params.get("steps", 9),
        "lora": params.get("lora"),
        "lora_scale": params.get("lora_scale", 1.0),
        "cutout": params.get("cutout", False),
    }
    ok = (
        bool(p["prompt"])
        and all(
            isinstance(x, int) and x % 64 == 0 and 256 <= x <= 2048
            for x in (p["width"], p["height"])
        )
        and isinstance(p["count"], int)
        and 1 <= p["count"] <= 4
        and isinstance(p["seed"], int)
        and 0 <= p["seed"] < 2**53  # torch's generator takes 64 bits; the seed + k stays inside
        and isinstance(p["steps"], int)
        and 1 <= p["steps"] <= 50
        and (p["lora"] is None or p["lora"] in STYLES)
        and isinstance(p["lora_scale"], int | float)
        and 0 < p["lora_scale"] <= 2
        and isinstance(p["cutout"], bool)
    )
    if not ok:
        raise StepError(
            "bad_params",
            f"image: a prompt (or a brief parent); width and height multiples of 64 from 256 to 2048; count 1-4; "
            f"steps 1-50; lora one of {sorted(STYLES)} or none, lora_scale in (0, 2]; cutout true or false",
        )
    return p


def _tile_params(in_dir, params) -> dict:
    size, mend = params.get("size", 512), params.get("mend", MEND)
    if not (isinstance(size, int) and size % 64 == 0 and 256 <= size <= 1024):
        raise StepError("bad_params", "tile: size a multiple of 64 from 256 to 1024")
    if not (isinstance(mend, int | float) and 0.3 <= mend <= 1):
        raise StepError("bad_params", "tile: mend from 0.3 to 1")
    extra = {"width": size, "height": size, "count": 1, "cutout": False}
    rest = {k: v for k, v in params.items() if k not in ("size", "mend")}
    return _image_params(in_dir, rest | extra) | {"mend": mend}


MEND = 0.8  # how far the seam band is drawn again: 1 a fresh draw (its tone and shapes stand out), less keeps them


def _seamless(pipe, picture: Image.Image, embeds, p: dict) -> Image.Image:
    """The picture rolled by half so its edges meet in the middle, that cross (an eighth of the size wide) drawn
    again by the inpaint pipeline on the same weights and prompt, feathered into the rest, rolled back: the edges now
    continue each other. The pipe's text encoder is off the card, its transformer and VAE on it."""
    from diffusers import ZImageInpaintPipeline
    from PIL import ImageFilter

    size = picture.width
    half, band = size // 2, size // 8
    rolled = np.roll(np.asarray(picture.convert("RGB")), (half, half), axis=(0, 1))
    mask = np.zeros((size, size), np.uint8)
    mask[half - band // 2 : half + band // 2] = 255
    mask[:, half - band // 2 : half + band // 2] = 255
    # the resident modules as they are: from_pipe casts them to float32 by default (the fp8 DiT to 24 GB, an OOM)
    redrawn = ZImageInpaintPipeline(**pipe.components)(
        image=Image.fromarray(rolled),
        mask_image=Image.fromarray(mask),
        prompt_embeds=[embeds[0]],
        height=size,
        width=size,
        strength=float(p["mend"]),
        num_inference_steps=p["steps"],
        guidance_scale=0.0,
        generator=torch.Generator("cpu").manual_seed(p["seed"] + 1),
    ).images[0]
    # the band's tone as the picture had it there: a fresh draw comes out darker or lighter, a frame round every tile
    new, core = np.asarray(redrawn.convert("RGB"), np.float32), mask > 0
    old = rolled[core].astype(np.float32)
    new = (new - new[core].mean(0)) / (new[core].std(0) + 1e-6) * old.std(0) + old.mean(0)
    # the mask's own edges feathered: the seam it mends sits in the band's middle, where the weight is still 1
    soft = np.asarray(Image.fromarray(mask).filter(ImageFilter.GaussianBlur(band / 8)), np.float32)
    soft = (soft / 255)[..., None]
    mixed = np.clip(rolled * (1 - soft) + new * soft, 0, 255)
    return Image.fromarray(np.roll(mixed.round().astype(np.uint8), (-half, -half), axis=(0, 1)))


def _grid(images: list[Image.Image], height: int = 384) -> Image.Image:
    """The pictures side by side at one height, a transparent one on mid grey: the version's thumbnail."""
    small = [im.resize((max(1, round(im.width * height / im.height)), height)) for im in images]
    out = Image.new(
        "RGB", (sum(s.width for s in small) + 8 * (len(small) - 1), height), (40, 44, 48)
    )
    x = 0
    for s in small:
        if s.mode == "RGBA":
            bg = Image.new("RGBA", s.size, (128, 128, 128, 255))
            s = Image.alpha_composite(bg, s)
        out.paste(s.convert("RGB"), (x, 0))
        x += s.width + 8
    return out


def _image(in_dir, out: Path, params, tile: bool = False) -> dict:
    p = _tile_params(in_dir, params) if tile else _image_params(in_dir, params)
    t0 = time.time()
    pipe = _pipeline()
    style = STYLES.get(p["lora"]) if p["lora"] else None
    loaded = False
    # above 1024 px the VAE decodes in 1024 px tiles: a 1920 x 1088 picture in one piece reserved 15.3 GB
    big = max(p["width"], p["height"]) > 1024
    try:
        if style:
            progress(0.03, f"style: {p['lora']}")
            path = style_file(p["lora"])
            loaded = True  # before: a load that fails halfway leaves layers to take out
            pipe.load_lora_weights(path, adapter_name=p["lora"])
            pipe.set_adapters([p["lora"]], [float(p["lora_scale"])])
        if big:
            pipe.vae.enable_tiling()
        text = (style[3] if style else "") + p["prompt"]
        progress(0.05, "encoding the prompt")
        pipe.transformer.to("cpu")
        pipe.text_encoder.to("cuda")
        with torch.inference_mode():
            embeds, _ = pipe.encode_prompt([text], device="cuda", do_classifier_free_guidance=False)
        pipe.text_encoder.to("cpu")
        torch.cuda.empty_cache()
        pipe.transformer.to("cuda")
        pipe.vae.to("cuda")
        images = []
        for k in range(p["count"]):
            images.append(
                pipe(
                    prompt_embeds=[embeds[0]],
                    height=p["height"],
                    width=p["width"],
                    num_inference_steps=p["steps"],
                    guidance_scale=0.0,
                    generator=torch.Generator("cpu").manual_seed(p["seed"] + k),
                ).images[0]
            )
            progress(0.1 + 0.8 * (k + 1) / p["count"], f"picture {k + 1}/{p['count']}")
        if tile:
            before = _pixel.seam_ratio(np.asarray(images[0].convert("RGBA")))
            progress(0.92, "mending the seams")
            images = [_seamless(pipe, images[0], embeds, p)]
    finally:
        try:
            if loaded:
                pipe.unload_lora_weights()  # the resident pipe goes back to plain Z-Image for the next job
        finally:  # all of it, even when the unload raised
            # a failed encode left the text encoder on the card: the next job's DiT would not fit beside it
            pipe.text_encoder.to("cpu")
            if big:
                pipe.vae.disable_tiling()
            pipe.transformer.to("cpu")
            torch.cuda.empty_cache()
    models = [{"id": MODEL, "revision": REVISION}]
    licenses = [MODEL]
    if style:
        models.append({"id": style[0], "revision": style[1]})
        licenses.append(style[0])
    if p["cutout"]:
        progress(0.92, "cut-out")
        model = _birefnet.load()
        rgbs = [np.asarray(im.convert("RGB")) for im in images]
        images = [
            Image.fromarray(
                np.dstack([rgb, (_birefnet.matte(model, rgb) * 255).round().astype(np.uint8)])
            )
            for rgb in rgbs
        ]
        del model
        torch.cuda.empty_cache()
        models.append({"id": _birefnet.MODEL, "revision": _birefnet.REVISION})
        licenses.append(_birefnet.MODEL)
    if tile:
        return _tile_result(images[0], before, out, p, time.time() - t0, models, licenses)
    outputs = {}
    for k, im in enumerate(images):
        im.save(out / f"image_{k}.png")
        outputs[f"image_{k}"] = f"image_{k}.png"
    images[0].save(out / "image.png")
    _grid(images).save(out / "preview.png")
    peak = torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() else 0.0
    return {
        "outputs": outputs | {"image": "image.png", "preview": "preview.png"},
        "metrics": {
            "seconds": round(time.time() - t0, 1),
            "peak_vram_gb": round(peak, 2),
            "seed": p["seed"],
            "width": p["width"],
            "height": p["height"],
        },
        "models": models,
        "licenses": licenses,
    }


def _tile_result(tile: Image.Image, before: float, out: Path, p, seconds, models, licenses) -> dict:
    rgba = np.asarray(tile.convert("RGBA"))
    ratio = _pixel.seam_ratio(rgba)
    report = {
        "seam_ratio": round(ratio, 3),
        "seam_ratio_before": round(before, 3),
        "pass": ratio <= _pixel.SEAM_PASS,
        "size": tile.width,
        "band": tile.width // 8,
    }
    tile.save(out / "tile.png")
    Image.fromarray(np.tile(rgba, (2, 2, 1))).save(out / "preview.png")
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    peak = torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() else 0.0
    return {
        "outputs": {
            "tile": "tile.png",
            "image": "tile.png",
            "preview": "preview.png",
            "report": "report.json",
        },
        "metrics": {
            "seconds": round(seconds, 1),
            "peak_vram_gb": round(peak, 2),
            "seed": p["seed"],
            "size": tile.width,
            "seam_ratio": report["seam_ratio"],
            "seam_ratio_before": report["seam_ratio_before"],
        },
        "models": models,
        "licenses": licenses,
    }


def run(op, in_dir, out_dir, params):
    if op in ("image", "tile"):
        return _image(in_dir, Path(out_dir), params, tile=op == "tile")
    if op != "run":
        raise StepError(
            "bad_params", f"op {op!r}: run (concept candidates), image (pictures) or tile"
        )
    if not in_dir or not (Path(in_dir) / "brief.yaml").is_file():
        raise StepError("missing_input", "concepts needs a brief version")
    brief = yaml.safe_load((Path(in_dir) / "brief.yaml").read_text(encoding="utf-8"))
    n, seed = params.get("n", 4), params.get("seed", random.randrange(2**31))
    t0, out = time.time(), Path(out_dir)
    pipe = _pipeline()
    suffixes = variants(brief.get("category", "prop"))
    prompts = {k: brief["prompt_full"] + suffixes[k % len(suffixes)] for k in range(n)}
    # ~4B text encoder + ~6B DiT don't fit together under 14.5 GB: encode every prompt once with the DiT
    # off the card, then the DiT on the GPU for all images (model offload swapped both per image: ~60 s each)
    progress(0.05, "encoding prompts")
    pipe.transformer.to("cpu")
    pipe.text_encoder.to("cuda")
    with torch.inference_mode():
        embeds, _ = pipe.encode_prompt(
            list(prompts.values()), device="cuda", do_classifier_free_guidance=False
        )
    pipe.text_encoder.to("cpu")
    torch.cuda.empty_cache()
    pipe.transformer.to("cuda")
    pipe.vae.to("cuda")
    for k in range(n):
        image = pipe(
            prompt_embeds=[embeds[k]],
            height=1024,
            width=1024,
            num_inference_steps=params.get("steps", 9),
            guidance_scale=0.0,  # Turbo is distilled without CFG
            generator=torch.Generator("cpu").manual_seed(seed + k),
        ).images[0]
        image.save(out / f"cand_{k}.png")
        progress(0.1 + 0.8 * (k + 1) / n, f"candidate {k + 1}/{n}")
    pipe.transformer.to("cpu")  # resident: the card is free for the gates and the next steps
    torch.cuda.empty_cache()

    progress(0.92, "gates")
    model = _birefnet.load()
    scored = {}
    for k in range(n):
        rgb = np.asarray(Image.open(out / f"cand_{k}.png").convert("RGB"))
        scored[f"cand_{k}.png"] = gates(rgb, _birefnet.matte(model, rgb))
    ranked = rank(scored)
    for k in range(n):
        ranked[f"cand_{k}.png"] |= {"prompt": prompts[k], "seed": seed + k}
    (out / "scores.json").write_text(json.dumps(ranked, indent=2), encoding="utf-8")
    peak = torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() else 0.0
    outputs = {f"cand_{k}": f"cand_{k}.png" for k in range(n)} | {"scores": "scores.json"}
    return {
        "outputs": outputs,
        "metrics": {
            "seconds": round(time.time() - t0, 1),
            "peak_vram_gb": round(peak, 2),
            "seed": seed,
        },
        "models": [
            {"id": MODEL, "revision": REVISION},
            {"id": _birefnet.MODEL, "revision": _birefnet.REVISION},
        ],
        "licenses": [MODEL, _birefnet.MODEL],
    }


if __name__ == "__main__":
    main(run)
