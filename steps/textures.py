"""Versioned texture authoring for models and standalone materials (research 17).

Input: parent GLB, texture-set version or image upload; create/generate can start without a parent.
Every operation saves copied maps and material.json. Model edits retain original geometry, UVs, rigs and clips.
Roles: basecolor, roughness, metallic, normal (OpenGL +Y), ao, height, emissive. material selects a GLB slot.

op=create: color (#808080), size (width 64-4096), tileable (bool); standalone material.
op=generate: prompt, size, tileable, seed; standalone colour, <=1K tile / <=2K image. For repeated generation
  the concepts resident worker (op=image|tile), followed by textures op=settings, avoids reloading weights.
op=import|replace: uploads={role: same-asset upload version}, normal_convention=OpenGL|DirectX.
  Optional packed_upload and packing=orm (R AO/G rough/B metal), unity_mask (R metal/G AO/A smooth),
  unity_metallic (R metal/A smooth). Smoothness is inverted; separate maps override packed channels.
op=adjust: role, brightness=1, contrast=1 (0-2); colour saturation=1 (0-2), hue=0 (-180..180).
op=settings: roughness, metallic (0-1), alpha_mode=OPAQUE|MASK|BLEND, tileable.
op=resize: role, size; normal vectors are renormalized, height precision retained.
op=upscale: role=basecolor|emissive, size greater than current width, tileable for standalone wrapped borders.
  Atlas charts are isolated. Output <=16MP; chart count <=4096.
op=enhance: prompt, strength=.5 (.3-1), size; model view-based refinement, standalone image redraw.
op=edit: prompt, region=uploaded frozen mask version, mask_source=original model version, strength.
  Mesh/UV identity must match mask_source. Existing refine view/coverage/zoom/tile options are accepted.
op=project: image=reference version (optional), region=head|front, strength, match; compatible model face projection.
op=retexture: image=prepared same-asset reference version (image_rgb.png), size, seed; model colour/rough/metal.
op=surface: strength, roughness, metallic; estimate missing maps from image brightness, not physical recovery.
op=seamless: role=basecolor|emissive; deterministic edge blend for standalone maps.
op=apply|extract: source_asset, source_version, source_material=0; pinned copies with source credits/restrictions.
  apply also takes roles (optional list), use_settings=true, mapping=atlas|repeat, repeat=1, rotation=0 degrees.
  Repeating mapping needs a tileable source. extract creates a map-only version, including from an uploaded GLB.
Export with step export, profile=textures, material=0 for a ZIP, individual maps, metadata and credits.
"""

import gc
import json
import math
import os
import re
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

from steps import _textures as tx
from steps._common import StepError, main, progress

OPS = {
    "import",
    "replace",
    "adjust",
    "settings",
    "resize",
    "upscale",
    "generate",
    "enhance",
    "edit",
    "project",
    "retexture",
    "apply",
    "extract",
    "surface",
    "create",
    "seamless",
}


def version(root: Path, asset: str, name: str) -> Path:
    if not re.fullmatch(r"[a-z0-9_]{1,40}", asset) or not re.fullmatch(r"\d{3}_[a-z0-9_]+", name):
        raise StepError("bad_params", "choose a valid asset and version")
    folder = root / asset / name
    if not (folder / "meta.json").is_file():
        raise StepError("missing_input", "the selected source version no longer exists")
    return folder


def number(params, name, default, lo, hi):
    value = params.get(name, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or not lo <= value <= hi
    ):
        raise StepError("bad_params", f"{name} must be between {lo} and {hi}")
    return value


def resized(image, target, role):
    return tx.resize_map(image, (target, max(1, round(image.height * target / image.width))), role)


def upscale_image(image: Image.Image, target: int, tileable: bool, draw, labels=None):
    """Wrapped context for tiles; isolated chart context for atlases; alpha stays separate."""
    from scipy import ndimage

    size = (target, max(1, round(image.height * target / image.width)))
    if size[0] * size[1] > tx.MAX_PIXELS:
        raise StepError("bad_params", "the output texture would exceed 16 megapixels")
    rgb, alpha = (
        np.asarray(image.convert("RGB")),
        image.getchannel("A") if "A" in image.getbands() else None,
    )

    def sr(patch, dims):
        result = Image.fromarray(draw(patch))
        return result.resize(dims, Image.Resampling.LANCZOS)

    if labels is None:
        if tileable:
            pad = min(32, image.width, image.height)
            padded = np.pad(rgb, ((pad, pad), (pad, pad), (0, 0)), mode="wrap")
            # Crop at the native 4× grid before a non-native final resize, keeping periodic context.
            big = Image.fromarray(draw(padded)).crop(
                (pad * 4, pad * 4, (pad + image.width) * 4, (pad + image.height) * 4)
            )
            result = big.resize(size, Image.Resampling.LANCZOS)
        else:
            result = sr(rgb, size)
    else:
        result = image.convert("RGB").resize(size, Image.Resampling.LANCZOS)
        canvas = np.asarray(result).copy()
        scaled = np.asarray(Image.fromarray(labels).resize(size, Image.Resampling.NEAREST))
        regions = ndimage.find_objects(labels)
        for i, region in enumerate(regions, 1):
            if region is None:
                continue
            y, x = region
            mask = labels[y, x] == i
            if not mask.any():
                continue
            patch = rgb[y, x].copy()
            nearest = ndimage.distance_transform_edt(
                ~mask, return_distances=False, return_indices=True
            )
            patch[~mask] = patch[tuple(nearest[:, ~mask])]
            pad = 16
            patch = np.pad(patch, ((pad, pad), (pad, pad), (0, 0)), mode="edge")
            generated = Image.fromarray(draw(patch)).crop(
                (pad * 4, pad * 4, (pad + x.stop - x.start) * 4, (pad + y.stop - y.start) * 4)
            )
            x0, x1 = round(x.start * size[0] / image.width), round(x.stop * size[0] / image.width)
            y0, y1 = round(y.start * size[1] / image.height), round(y.stop * size[1] / image.height)
            if x1 <= x0 or y1 <= y0:
                continue
            part = np.asarray(generated.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS))
            allowed = scaled[y0:y1, x0:x1] == i
            canvas[y0:y1, x0:x1][allowed] = part[allowed]
            progress(0.1 + 0.75 * i / len(regions), f"Upscaling UV island {i} of {len(regions)}")
        coverage = scaled > 0
        if coverage.any():
            nearest = ndimage.distance_transform_edt(
                ~coverage, return_distances=False, return_indices=True
            )
            canvas[~coverage] = canvas[tuple(nearest[:, ~coverage])]
        result = Image.fromarray(canvas)
    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha.resize(size, Image.Resampling.LANCZOS))
    return result


def _inputs(src: Path, params: dict) -> tuple[dict, list, bool]:
    maps = {}
    licenses, restricted = set(), False
    uploads = params.get("uploads", {})
    if not isinstance(uploads, dict) or (not uploads and not params.get("packed_upload")):
        raise StepError("bad_params", "select at least one map to import")

    def read(name):
        nonlocal restricted
        if not isinstance(name, str):
            raise StepError("bad_params", "choose an uploaded map version")
        folder = version(src.parent.parent, src.parent.name, name)
        meta = tx.metadata(folder)
        licenses.update(meta.get("licenses", []))
        restricted |= bool(meta.get("restricted"))
        image = meta["outputs"].get("image")
        if not image:
            raise StepError("missing_input", "the selected upload is not an image")
        return tx.open_image(tx.safe_file(folder, image))

    if packed := params.get("packed_upload"):
        packing = params.get("packing", "orm")
        if packing not in ("orm", "unity_mask", "unity_metallic"):
            raise StepError("bad_params", "packing must be orm, unity_mask or unity_metallic")
        image = read(packed).convert("RGBA")
        if packing == "orm":
            maps.update(
                {
                    "ao": image.getchannel("R"),
                    "roughness": image.getchannel("G"),
                    "metallic": image.getchannel("B"),
                }
            )
        else:
            maps.update(
                {
                    "metallic": image.getchannel("R"),
                    "roughness": ImageOps.invert(image.getchannel("A")),
                }
            )
            if packing == "unity_mask":
                maps["ao"] = image.getchannel("G")
    for role, name in uploads.items():
        if role not in tx.ROLES:
            raise StepError("bad_params", "unknown texture map role")
        maps[role] = read(name)
    return maps, sorted(licenses), restricted


def run(op, in_dir, out_dir, params):
    if op not in OPS:
        raise StepError("bad_params", f"unknown texture operation {op}")
    t0, out = time.time(), Path(out_dir)
    src = Path(in_dir) if in_dir else None
    material = int(number(params, "material", 0, 0, 999))
    target = int(number(params, "size", 2048, 64, 4096))
    role = params.get("role", "basecolor")
    if role not in tx.ROLES:
        raise StepError("bad_params", "choose a known map role")
    maps, manifest = (
        {},
        {"settings": {"roughnessFactor": 0.8, "metallicFactor": 0}, "tileable": False},
    )
    mesh = tx.mesh_file(src) if src else None
    if src and op not in ("generate", "create"):
        maps, manifest = tx.load_set(src, material)
    # Operation previews belong to their own version, not to later edits.
    manifest.pop("previews", None)
    if "tileable" in params:
        if not isinstance(params["tileable"], bool):
            raise StepError("bad_params", "tileable must be true or false")
        manifest["tileable"] = params["tileable"]
    values = dict(manifest.get("settings", {}))
    models, licenses, restricted, changed = [], [], False, set()
    operation_metrics = {}
    origin_meta = tx.metadata(src) if src else {}
    if op == "import" and origin_meta.get("step") == "upload":
        maps, manifest = (
            {},
            {
                "settings": {"roughnessFactor": 1, "metallicFactor": 1},
                "tileable": params.get("tileable", False),
            },
        )
        values = dict(manifest["settings"])
    progress(0.03, "Reading the texture set")
    if op in ("import", "replace"):
        if not src:
            raise StepError("missing_input", "import needs an uploaded image")
        added, licenses, restricted = _inputs(src, params)
        if params.get("normal_convention", "OpenGL") == "DirectX" and "normal" in added:
            a = np.asarray(added["normal"].convert("RGB")).copy()
            a[..., 1] = 255 - a[..., 1]
            added["normal"] = Image.fromarray(a)
        maps.update(added)
        changed.update(added)
        if "emissive" in added and not any(values.get("emissiveFactor", [0, 0, 0])):
            values["emissiveFactor"] = [1, 1, 1]
        if op == "import":
            values["roughnessFactor"] = 1 if "roughness" in added else 0.8
            values["metallicFactor"] = 1 if "metallic" in added else 0
        if (
            "basecolor" in added
            and "A" in added["basecolor"].getbands()
            and added["basecolor"].getchannel("A").getextrema()[0] < 255
        ):
            values["alphaMode"] = "BLEND"
    elif op == "create":
        if mesh:
            raise StepError(
                "bad_params", "create a standalone texture before applying it to a model"
            )
        try:
            maps = {
                "basecolor": Image.new("RGBA", (target, target), params.get("color", "#808080"))
            }
        except ValueError as e:
            raise StepError("bad_params", "choose a valid colour") from e
        changed.add("basecolor")
    elif op == "generate":
        from steps import concepts

        if mesh:
            raise StepError("bad_params", "use Retexture to generate textures for a model")
        tileable = manifest.get("tileable", False)
        prompt = str(params.get("prompt", "")).strip()
        if not prompt:
            raise StepError("bad_params", "describe the texture you want")
        p = {"prompt": prompt, "count": 1, "seed": int(number(params, "seed", 42, 0, 2**31 - 1))}
        p |= (
            {"size": min(target, 1024)}
            if tileable
            else {"width": min(target, 2048), "height": min(target, 2048)}
        )
        with tempfile.TemporaryDirectory(prefix="effigy-texture-") as tmp:
            result = concepts.run("tile" if tileable else "image", None, tmp, p)
            maps = {"basecolor": tx.open_image(Path(tmp) / result["outputs"]["image"])}
        models, licenses = result.get("models", []), result.get("licenses", [])
        operation_metrics = result.get("metrics", {})
        manifest["prompt"] = prompt
        changed.add("basecolor")
    elif op in ("apply", "extract"):
        root = Path(os.environ.get("EFFIGY_LIBRARY", out.parent.parent))
        other = version(
            root, str(params.get("source_asset", "")), str(params.get("source_version", ""))
        )
        source_material = int(number(params, "source_material", 0, 0, 999))
        incoming, source_info = tx.load_set(other, source_material)
        if op == "extract" and not incoming:
            # Constant-only GLB materials still have a usable appearance through their factors.
            incoming = {"basecolor": Image.new("RGBA", (64, 64), "white")}
        other_meta = tx.metadata(other)
        selected = params.get("roles", list(incoming))
        if (
            not isinstance(selected, list)
            or any(r not in incoming for r in selected)
            or not selected
        ):
            raise StepError("bad_params", "select at least one existing source map")
        maps.update({r: incoming[r] for r in selected})
        changed.update(selected)
        licenses, restricted = other_meta.get("licenses", []), other_meta.get("restricted", False)
        manifest["source"] = {
            "asset": other.parent.name,
            "version": other.name,
            "material": source_material,
        }
        if op == "extract" or params.get("use_settings", True):
            values.update(source_info.get("settings", {}))
        if op == "extract" or not mesh:
            manifest["tileable"] = source_info.get("tileable", False)
        elif params.get("mapping", "atlas") == "repeat":
            if not source_info.get("tileable"):
                raise StepError("bad_params", "choose a tileable material for repeating mapping")
            manifest["mapping"] = {
                "repeat": number(params, "repeat", 1, 0.1, 100),
                "rotation": number(params, "rotation", 0, -180, 180),
            }
    elif op == "settings":
        pass
    elif op == "adjust":
        if role not in maps:
            raise StepError("missing_input", "the selected map is missing")
        if role not in ("basecolor", "emissive", "roughness", "metallic", "ao", "height"):
            raise StepError(
                "bad_params", "normal maps use vector processing; import a normal map instead"
            )
        image = maps[role]
        alpha = image.getchannel("A") if "A" in image.getbands() else None
        brightness = number(params, "brightness", 1, 0, 2)
        contrast = number(params, "contrast", 1, 0, 2)
        if role not in ("basecolor", "emissive"):
            depth16 = role == "height" and image.mode in ("I", "I;16", "I;16L", "I;16B")
            limit, dtype = (65535, np.uint16) if depth16 else (255, np.uint8)
            values_array = np.asarray(image if depth16 else image.convert("L"), np.float32)
            values_array = np.clip(values_array * brightness, 0, limit)
            mean = values_array.mean()
            image = Image.fromarray(
                np.clip((values_array - mean) * contrast + mean, 0, limit).round().astype(dtype)
            )
        else:
            image = image.convert("RGB")
            image = ImageEnhance.Brightness(image).enhance(brightness)
            image = ImageEnhance.Contrast(image).enhance(contrast)
            image = ImageEnhance.Color(image).enhance(number(params, "saturation", 1, 0, 2))
            hue = number(params, "hue", 0, -180, 180)
            if hue:
                hsv = np.asarray(image.convert("HSV")).copy()
                hsv[..., 0] = ((hsv[..., 0].astype(float) + hue * 255 / 360) % 256).astype(np.uint8)
                image = Image.fromarray(hsv, "HSV").convert("RGB")
        if alpha is not None:
            image = image.convert("RGBA")
            image.putalpha(alpha)
        maps[role] = image
        changed.add(role)
    elif op in ("resize", "upscale"):
        if role not in maps:
            raise StepError("missing_input", "the selected map is missing")
        image = maps[role]
        if op == "resize":
            maps[role] = resized(image, target, role)
        else:
            if role not in ("basecolor", "emissive"):
                raise StepError(
                    "bad_params", "AI upscale supports colour maps; use Resize for data maps"
                )
            if target <= image.width:
                raise StepError(
                    "bad_params", "choose an output width larger than the current texture"
                )
            from steps import _sr

            labels = tx.uv_islands(tx.safe_file(src, mesh), material, image.size) if mesh else None
            net = _sr.load()
            try:
                maps[role] = upscale_image(
                    image,
                    target,
                    manifest.get("tileable", False),
                    lambda a: _sr.upscale(net, a),
                    labels,
                )
            finally:
                del net
                gc.collect()
            models, licenses = [{"id": _sr.MODEL}], [_sr.MODEL]
        changed.add(role)
    elif op == "surface":
        if "basecolor" not in maps:
            raise StepError(
                "missing_input", "a base-colour image is needed to estimate surface maps"
            )
        from scipy import ndimage

        height = np.asarray(maps["basecolor"].convert("L"), np.float32) / 255
        strength = number(params, "strength", 1, 0, 4)
        mode = "wrap" if manifest.get("tileable") else "nearest"
        dx, dy = ndimage.sobel(height, axis=1, mode=mode), ndimage.sobel(height, axis=0, mode=mode)
        normal = np.stack((-dx * strength, dy * strength, np.ones_like(height)), -1)
        normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
        generated = {
            "height": Image.fromarray((height * 65535).astype(np.uint16)),
            "normal": Image.fromarray(np.uint8(np.clip((normal + 1) * 127.5, 0, 255))),
            "roughness": Image.new(
                "L", maps["basecolor"].size, round(number(params, "roughness", 0.8, 0, 1) * 255)
            ),
            "metallic": Image.new(
                "L", maps["basecolor"].size, round(number(params, "metallic", 0, 0, 1) * 255)
            ),
        }
        added = {
            k: v
            for k, v in generated.items()
            if params.get("replace_existing", False) or k not in maps
        }
        maps.update(added)
        changed.update(added)
        if "roughness" in added:
            values["roughnessFactor"] = 1
        if "metallic" in added:
            values["metallicFactor"] = 1
        manifest["surface_method"] = (
            "Estimated height/normal from luminance; constant roughness and metallic, not physical recovery"
        )
    elif op == "seamless":
        if mesh or role not in maps or role not in ("basecolor", "emissive"):
            raise StepError("bad_params", "seam repair is for standalone colour textures")
        # Periodic cross-fade of an offset image. Explicitly a deterministic repair, not AI recovery.
        a = np.asarray(maps[role].convert("RGBA"), np.float32)
        for axis in (0, 1):
            n = a.shape[axis]
            weight = np.sin(np.pi * (np.arange(n) + 0.5) / n) ** 2
            shape = [1, 1, 1]
            shape[axis] = n
            a = a * weight.reshape(shape) + np.roll(a, n // 2, axis=axis) * (
                1 - weight.reshape(shape)
            )
        maps[role] = Image.fromarray(np.uint8(np.clip(a, 0, 255)))
        manifest["tileable"] = True
        changed.add(role)
    elif op in ("enhance", "edit", "project", "retexture"):
        prompt = str(params.get("prompt", "")).strip()
        shadows_only = op == "enhance" and params.get("views") == 0 and params.get("delight", 0)
        if not prompt and op != "project" and not shadows_only:
            raise StepError("bad_params", "describe the texture or detail you want")
        if mesh:
            capabilities = tx.inventory(src)["materials"][material]
            if not capabilities["can_generate"]:
                raise StepError(
                    "missing_input",
                    capabilities.get("reason")
                    or "AI generation currently needs one mesh and material with UV0",
                )
            if op == "edit":
                mask_source = version(
                    src.parent.parent, src.parent.name, str(params.get("mask_source", ""))
                )
                mask_mesh = tx.mesh_file(mask_source)
                if not mask_mesh or tx.geometry_id(
                    tx.safe_file(mask_source, mask_mesh)
                ) != tx.geometry_id(tx.safe_file(src, mesh)):
                    raise StepError(
                        "bad_params",
                        "this painted region belongs to a different mesh or UV layout; paint a new region",
                    )
        with tempfile.TemporaryDirectory(prefix="effigy-texture-ai-") as tmp:
            work = Path(tmp)
            if mesh:
                from steps import project, refine, trellis2

                if op in ("enhance", "edit"):
                    refine_params = {
                        k: val
                        for k, val in params.items()
                        if k
                        in (
                            "views",
                            "region",
                            "seed",
                            "zoom",
                            "coverage",
                            "face_strength",
                            "tile",
                            "scale",
                            "delight",
                            "texture_size",
                        )
                    }
                    refine_params = {
                        "prompt": prompt,
                        "strength": number(params, "strength", 0.5, 0.3, 1),
                        "delight": 0,
                        "texture_size": 4096 if target == 4096 else "keep",
                        **refine_params,
                    }
                    result = refine.run("edit" if op == "edit" else "run", src, work, refine_params)
                elif op == "project":
                    result = project.run(
                        "run",
                        src,
                        work,
                        {
                            k: val
                            for k, val in params.items()
                            if k in ("image", "region", "strength", "match")
                        },
                    )
                else:
                    result = trellis2.texture(
                        src,
                        work,
                        {
                            "image": params.get("image"),
                            "texture_size": target,
                            "seed": int(number(params, "seed", 42, 0, 2**31 - 1)),
                        },
                    )
            else:
                from steps import redraw

                if "basecolor" not in maps:
                    raise StepError("missing_input", "enhancement needs a colour texture")
                # A wrapped border provides periodic context, without a model or UV atlas.
                image = maps["basecolor"]
                pad = min(64, image.width, image.height) if manifest.get("tileable") else 0
                if pad:
                    image = Image.fromarray(
                        np.pad(
                            np.asarray(image.convert("RGBA")),
                            ((pad, pad), (pad, pad), (0, 0)),
                            mode="wrap",
                        )
                    )
                image.save(work / "input.png")
                (work / "meta.json").write_text(
                    json.dumps({"outputs": {"image": "input.png"}}), encoding="utf-8"
                )
                result = redraw.run(
                    "redraw",
                    work,
                    work,
                    {
                        "prompt": prompt,
                        "strength": number(params, "strength", 0.35, 0.2, 1),
                        "factor": 1,
                    },
                )
                edited = tx.open_image(work / result["outputs"]["image"])
                if pad:
                    edited = edited.crop((pad, pad, edited.width - pad, edited.height - pad))
                maps["basecolor"] = edited
            if mesh:
                for role_name in ("basecolor", "normal"):
                    if role_name in result["outputs"] and (
                        op == "retexture" or role_name == "basecolor"
                    ):
                        maps[role_name] = tx.open_image(work / result["outputs"][role_name])
                        changed.add(role_name)
                if op == "retexture" and "orm" in result["outputs"]:
                    orm = tx.open_image(work / result["outputs"]["orm"]).convert("RGB")
                    maps.update({"roughness": orm.getchannel("G"), "metallic": orm.getchannel("B")})
                    changed.update(("roughness", "metallic"))
                    values.update({"roughnessFactor": 1, "metallicFactor": 1})
            else:
                changed.add("basecolor")
            models, licenses = result.get("models", []), result.get("licenses", [])
            operation_metrics = result.get("metrics", {})
            if mesh and params.get("image"):
                reference = tx.metadata(
                    version(src.parent.parent, src.parent.name, params["image"])
                )
                licenses = sorted(set(licenses) | set(reference.get("licenses", [])))
                restricted |= bool(reference.get("restricted"))
            for name in ("views", "overlay"):
                if filename := result.get("outputs", {}).get(name):
                    import shutil

                    shutil.copy2(work / filename, out / Path(filename).name)
                    manifest.setdefault("previews", {})[name] = Path(filename).name
    if not maps:
        raise StepError("missing_input", "add a map or create a texture first")
    if "roughness" in params and op != "surface":
        values["roughnessFactor"] = number(params, "roughness", 0.8, 0, 1)
    if "metallic" in params and op != "surface":
        values["metallicFactor"] = number(params, "metallic", 0, 0, 1)
    if params.get("alpha_mode") in ("OPAQUE", "MASK", "BLEND"):
        values["alphaMode"] = params["alpha_mode"]
    manifest["settings"] = values
    manifest["material"] = material
    manifest["operation"] = op
    progress(0.93, "Saving a new texture version")
    outputs = tx.write_set(out, maps, manifest, preview=not mesh or op == "extract")
    outputs.update(manifest.get("previews", {}))
    if mesh and op != "extract":
        key = next(k for k, f in origin_meta["outputs"].items() if f == mesh)
        tx.patch_glb(
            tx.safe_file(src, mesh),
            out / "mesh.glb",
            {r: maps[r] for r in changed if r != "height"},
            material,
            values,
        )
        if mapping := manifest.get("mapping"):
            from steps import _glb

            doc, blob = _glb.read(out / "mesh.glb")
            mat = doc["materials"][material]
            samplers = doc.setdefault("samplers", [])
            samplers.append({"wrapS": 10497, "wrapT": 10497})
            for r in changed:
                if r == "height":
                    continue
                info = tx._slot(mat, r)
                if info is not None:
                    doc["textures"][info["index"]]["sampler"] = len(samplers) - 1
                    info.setdefault("extensions", {})["KHR_texture_transform"] = {
                        "scale": [mapping["repeat"]] * 2,
                        "rotation": math.radians(mapping["rotation"]),
                    }
            doc["extensionsUsed"] = list(
                dict.fromkeys([*doc.get("extensionsUsed", []), "KHR_texture_transform"])
            )
            _glb.write(out / "mesh.glb", doc, blob)
        outputs = {key: "mesh.glb", **outputs}
        # Keep sidecar animation/rig data needed by later character operations.
        import shutil

        for key, name in origin_meta["outputs"].items():
            if (
                key not in outputs
                and not name.endswith(".glb")
                and key not in tx.ROLES
                and key
                not in ("material", "preview", "views", "overlay", "ovoxel", "hi_trellis", "orm")
            ):
                path = src / name
                if path.is_file():
                    path = tx.safe_file(src, name)
                    (out / name).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, out / name)
                    outputs[key] = name
    metrics = {
        **operation_metrics,
        "operation": op,
        "seconds": round(time.time() - t0, 2),
        "material": material,
        "maps": tx.describe(maps),
        "tileable": manifest.get("tileable", False),
        "source": manifest.get("source"),
        "character_step": origin_meta.get("metrics", {}).get("character_step")
        or (
            origin_meta.get("step")
            if origin_meta.get("step") in ("rig_skin", "anim", "anim_edit", "import_model")
            else None
        ),
    }
    import sys

    if (torch := sys.modules.get("torch")) is not None and torch.cuda.is_initialized():
        metrics["peak_vram_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 3)
        metrics["peak_reserved_gb"] = round(torch.cuda.max_memory_reserved() / 2**30, 3)
    if manifest.get("tileable") and "basecolor" in maps:
        from steps._pixel import seam_ratio

        metrics["seam_ratio"] = round(seam_ratio(np.asarray(maps["basecolor"].convert("RGBA"))), 3)
    return {
        "outputs": outputs,
        "metrics": metrics,
        "models": models,
        "licenses": licenses,
        "restricted": restricted,
    }


if __name__ == "__main__":
    main(run)
