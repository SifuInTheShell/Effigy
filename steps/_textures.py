"""Texture sets and GLB image edits. No scene reconstruction: geometry, rigs and clips stay intact."""

import base64
import copy
import hashlib
import io
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from steps import _glb
from steps._common import StepError

ROLES = ("basecolor", "roughness", "metallic", "normal", "ao", "height", "emissive")
MAX_PIXELS = 4096 * 4096
SLOTS = {
    "basecolor": "baseColorTexture",
    "normal": "normalTexture",
    "ao": "occlusionTexture",
    "emissive": "emissiveTexture",
}


def safe_file(folder: Path, name: str) -> Path:
    p = (folder / name).resolve()
    if not p.is_relative_to(folder.resolve()) or not p.is_file():
        raise StepError("missing_input", "a texture file is missing or outside its version")
    return p


def open_image(data) -> Image.Image:
    try:
        image = Image.open(data)
        if image.width * image.height > MAX_PIXELS:
            raise StepError(
                "bad_params", "texture images may contain at most 16 megapixels (4096 × 4096)"
            )
        image.load()
        return image.copy()
    except (OSError, ValueError, Image.DecompressionBombError) as e:
        raise StepError("bad_params", "use a readable PNG, JPEG, WebP or TIFF texture") from e


def metadata(folder: Path) -> dict:
    return json.loads((folder / "meta.json").read_text(encoding="utf-8"))


def mesh_file(folder: Path) -> str | None:
    outputs = metadata(folder)["outputs"]
    return next(
        (
            outputs[k]
            for k in ("rigged_anim", "rigged", "mesh", "model")
            if outputs.get(k, "").endswith(".glb")
        ),
        None,
    )


def image_bytes(folder: Path, doc: dict, blob: bytearray, index: int) -> bytes:
    item = doc["images"][index]
    if "bufferView" in item:
        view = doc["bufferViews"][item["bufferView"]]
        if view.get("buffer", 0) != 0:
            raise StepError(
                "missing_input", "external GLB buffers are not supported for texture editing"
            )
        start = view.get("byteOffset", 0)
        return bytes(blob[start : start + view["byteLength"]])
    uri = item.get("uri", "")
    if uri.startswith("data:image/") and ";base64," in uri:
        return base64.b64decode(uri.split(",", 1)[1], validate=True)
    if ":" in uri or "\\" in uri:
        raise StepError("missing_input", "external texture URLs are not supported")
    from urllib.parse import unquote

    return safe_file(folder, unquote(uri)).read_bytes()


def _slot(mat: dict, role: str) -> dict | None:
    if role in ("roughness", "metallic"):
        return mat.get("pbrMetallicRoughness", {}).get("metallicRoughnessTexture")
    return (mat.get("pbrMetallicRoughness", {}) if role == "basecolor" else mat).get(SLOTS[role])


def _image(folder: Path, doc: dict, blob: bytearray, info: dict) -> Image.Image:
    texture = doc["textures"][info["index"]]
    if "source" not in texture:
        raise StepError(
            "missing_input", "this compressed texture needs conversion to PNG before editing"
        )
    return open_image(io.BytesIO(image_bytes(folder, doc, blob, texture["source"])))


def settings(mat: dict) -> dict:
    pbr = mat.get("pbrMetallicRoughness", {})
    if not isinstance(pbr, dict):
        raise StepError("bad_params", "invalid PBR material settings")

    def scalar(value, name):
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise StepError(
                "bad_params", f"invalid material {name}: expected a finite number between 0 and 1"
            )
        return value

    def vector(value, n, name):
        if not isinstance(value, list) or len(value) != n:
            raise StepError("bad_params", f"invalid material {name}: expected {n} components")
        return [scalar(x, name) for x in value]

    return {
        "baseColorFactor": vector(pbr.get("baseColorFactor", [1, 1, 1, 1]), 4, "baseColorFactor"),
        "roughnessFactor": scalar(pbr.get("roughnessFactor", 1), "roughnessFactor"),
        "metallicFactor": scalar(pbr.get("metallicFactor", 1), "metallicFactor"),
        "alphaMode": mat.get("alphaMode", "OPAQUE"),
        "alphaCutoff": scalar(mat.get("alphaCutoff", 0.5), "alphaCutoff"),
        "doubleSided": mat.get("doubleSided", False),
        "emissiveFactor": vector(mat.get("emissiveFactor", [0, 0, 0]), 3, "emissiveFactor"),
    }


def load_map(folder: Path, material: int, role: str) -> Image.Image:
    """Decode only the requested map for HTTP previews, rather than the entire material set."""
    name = mesh_file(folder)
    if name and role != "height":
        doc, blob = _glb.read(safe_file(folder, name))
        if not 0 <= material < len(doc.get("materials", [])):
            raise StepError("bad_params", "choose an existing material")
        info = _slot(doc["materials"][material], role)
        if info is None:
            raise StepError("missing_input", "this material has no such map")
        image = _image(folder, doc, blob, info)
        return (
            image.convert("RGB").getchannel({"roughness": "G", "metallic": "B", "ao": "R"}[role])
            if role in ("roughness", "metallic", "ao")
            else image
        )
    meta = metadata(folder)
    if stored := meta["outputs"].get("material"):
        manifest = json.loads(safe_file(folder, stored).read_text(encoding="utf-8"))
        if (not name or manifest.get("material", 0) == material) and role in manifest["maps"]:
            return open_image(safe_file(folder, manifest["maps"][role]))
        raise StepError("missing_input", "this material has no such map")
    maps, _ = load_set(folder, material)
    if role not in maps:
        raise StepError("missing_input", "this material has no such map")
    return maps[role]


def load_set(folder: Path, material: int = 0) -> tuple[dict[str, Image.Image], dict]:
    meta = metadata(folder)
    if (name := meta["outputs"].get("material")) and not mesh_file(folder):
        manifest = json.loads(safe_file(folder, name).read_text(encoding="utf-8"))
        return {
            r: open_image(safe_file(folder, f)) for r, f in manifest["maps"].items() if r in ROLES
        }, manifest
    if name := mesh_file(folder):
        doc, blob = _glb.read(safe_file(folder, name))
        mats = doc.get("materials", [])
        if not 0 <= material < len(mats):
            raise StepError("bad_params", "choose an existing material")
        mat, maps = mats[material], {}
        for role in ROLES:
            if role == "height":
                continue
            info = _slot(mat, role)
            if info is not None:
                image = _image(folder, doc, blob, info)
                maps[role] = (
                    image.convert("RGB").getchannel(
                        {"roughness": "G", "metallic": "B", "ao": "R"}[role]
                    )
                    if role in ("roughness", "metallic", "ao")
                    else image
                )
        manifest = {
            "settings": settings(mat),
            "tileable": False,
            "name": mat.get("name", f"Material {material + 1}"),
            "source": {"asset": folder.parent.name, "version": folder.name, "material": material},
        }
        if stored := meta["outputs"].get("material"):
            data = json.loads(safe_file(folder, stored).read_text(encoding="utf-8"))
            if data.get("material", 0) == material:
                manifest = {**data, **manifest}
                if height := data.get("maps", {}).get("height"):
                    maps["height"] = open_image(safe_file(folder, height))
        return maps, manifest
    outputs = meta["outputs"]
    name = (
        outputs.get("image")
        or outputs.get("tile")
        or next(
            (
                f
                for f in outputs.values()
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".tga"))
            ),
            None,
        )
    )
    if not name:
        raise StepError("missing_input", "this version has no texture image or material")
    return {"basecolor": open_image(safe_file(folder, name))}, {
        "settings": {"roughnessFactor": 0.8, "metallicFactor": 0},
        "tileable": "tile" in outputs,
    }


def inventory(folder: Path) -> dict:
    name = mesh_file(folder)
    if not name:
        maps, manifest = load_set(folder)
        return {
            "model": False,
            "materials": [
                {
                    "index": 0,
                    "name": manifest.get("name", "Texture set"),
                    "maps": describe(maps),
                    "settings": manifest.get("settings", {}),
                    "tileable": manifest.get("tileable", False),
                    "can_generate": False,
                    "can_upscale": True,
                }
            ],
            "manifest": manifest,
        }
    doc, _ = _glb.read(safe_file(folder, name))
    materials = []
    for i, mat in enumerate(doc.get("materials", [])):
        reason = None
        try:
            maps, manifest = load_set(folder, i)
        except StepError as e:
            maps, manifest, reason = {}, {"settings": settings(mat)}, str(e)
        primitives = [
            p
            for mesh in doc.get("meshes", [])
            for p in mesh.get("primitives", [])
            if p.get("material") == i
        ]
        uv = bool(primitives) and all("TEXCOORD_0" in p.get("attributes", {}) for p in primitives)
        complex_uv = any(
            (_slot(mat, r) or {}).get("texCoord", 0) != 0
            or "KHR_texture_transform" in (_slot(mat, r) or {}).get("extensions", {})
            for r in ROLES
            if r != "height"
        )
        if mat.get("extensions"):
            reason = (
                reason
                or "This material uses extensions; inspect/export is available, but convert it to standard PBR before AI editing."
            )
        materials.append(
            {
                "index": i,
                "name": mat.get("name", f"Material {i + 1}"),
                "maps": describe(maps),
                "settings": manifest["settings"],
                "tileable": False,
                "uv": uv,
                "reason": reason,
                "can_upscale": uv and not complex_uv and not reason,
                "can_generate": uv
                and not complex_uv
                and not reason
                and len(doc.get("materials", [])) == 1
                and len(doc.get("meshes", [])) == 1,
            }
        )
    return {
        "model": True,
        "mesh": name,
        "materials": materials,
        "rigged": bool(doc.get("skins")),
        "animated": bool(doc.get("animations")),
    }


def describe(maps: dict) -> dict:
    return {
        r: {
            "width": im.width,
            "height": im.height,
            "mode": im.mode,
            "color_space": "sRGB" if r in ("basecolor", "emissive") else "linear",
        }
        for r, im in maps.items()
    }


def write_set(out: Path, maps: dict, manifest: dict, preview: bool = True) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for role, image in maps.items():
        if role not in ROLES:
            raise StepError("bad_params", f"unknown map role {role}")
        image.save(out / f"{role}.png")
        files[role] = f"{role}.png"
    data = {
        **manifest,
        "schema": 1,
        "maps": files,
        "dimensions": describe(maps),
        "normal_convention": "OpenGL",
    }
    (out / "material.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    outputs = {"material": "material.json", **files}
    if preview and "basecolor" in maps:
        preview = maps["basecolor"].convert("RGBA")
        preview.thumbnail((1024, 1024))
        preview.save(out / "preview.png")
        outputs = {"preview": "preview.png", **outputs}
    return outputs


def patch_glb(src: Path, dst: Path, maps: dict, material: int, values: dict | None = None) -> None:
    """Append new images and rebind only this material's slots, preserving the original binary/accessors."""
    doc, blob = _glb.read(src)
    if not 0 <= material < len(doc.get("materials", [])):
        raise StepError("bad_params", "choose an existing material")
    mat = doc["materials"][material]
    pbr = mat.setdefault("pbrMetallicRoughness", {})

    def bind(image, container, slot, source=None):
        old = copy.deepcopy(container.get(slot, {}))
        source = source or old
        sampler = (
            doc.get("textures", [])[source["index"]].get("sampler") if "index" in source else None
        )
        data = io.BytesIO()
        image.save(data, format="PNG")
        blob.extend(b"\0" * (-len(blob) % 4))
        offset = len(blob)
        blob.extend(data.getvalue())
        views = doc.setdefault("bufferViews", [])
        images = doc.setdefault("images", [])
        textures = doc.setdefault("textures", [])
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data.getvalue())})
        images.append({"bufferView": len(views) - 1, "mimeType": "image/png"})
        textures.append(
            {"source": len(images) - 1, **({"sampler": sampler} if sampler is not None else {})}
        )
        container[slot] = {**old, "index": len(textures) - 1}

    for role in ("basecolor", "normal", "emissive"):
        if role in maps:
            bind(maps[role], pbr if role == "basecolor" else mat, SLOTS[role])
    if any(r in maps for r in ("roughness", "metallic", "ao")):
        # AO can use a different image and UV transform. Keep its binding independent of MR.
        if any(r in maps for r in ("roughness", "metallic")):
            old_info = pbr.get("metallicRoughnessTexture")
            old = _image(src.parent, doc, blob, old_info).convert("RGB") if old_info else None
            size = next(maps[r].size for r in ("roughness", "metallic") if r in maps)
            rough = (
                maps.get("roughness", old.getchannel("G") if old else Image.new("L", size, 255))
                .convert("L")
                .resize(size)
            )
            metal = (
                maps.get("metallic", old.getchannel("B") if old else Image.new("L", size, 255))
                .convert("L")
                .resize(size)
            )
            bind(
                Image.merge("RGB", (Image.new("L", size, 255), rough, metal)),
                pbr,
                "metallicRoughnessTexture",
            )
        if "ao" in maps:
            ao = maps["ao"].convert("L")
            bind(Image.merge("RGB", (ao, ao, ao)), mat, "occlusionTexture")
    if values:
        for k, value in values.items():
            if k in ("baseColorFactor", "roughnessFactor", "metallicFactor"):
                pbr[k] = value
            elif k in ("alphaMode", "alphaCutoff", "doubleSided", "emissiveFactor"):
                mat[k] = value
    # Untouched external images must travel with the new version too.
    for image in doc.get("images", []):
        if "uri" in image:
            index = doc["images"].index(image)
            data = image_bytes(src.parent, doc, blob, index)
            blob.extend(b"\0" * (-len(blob) % 4))
            offset = len(blob)
            blob.extend(data)
            doc.setdefault("bufferViews", []).append(
                {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
            )
            image.pop("uri")
            image["bufferView"] = len(doc["bufferViews"]) - 1
            image.setdefault(
                "mimeType", "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
            )
    if doc.get("buffers"):
        doc["buffers"][0]["byteLength"] = len(blob)
    else:
        doc["buffers"] = [{"byteLength": len(blob)}]
    _glb.write(dst, doc, blob)


def geometry_id(path: Path) -> str:
    doc, blob = _glb.read(path)
    h = hashlib.sha256()
    for mesh in doc.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            for key in ("POSITION", "TEXCOORD_0"):
                if key in primitive.get("attributes", {}):
                    h.update(_glb.accessor(doc, blob, primitive["attributes"][key]).tobytes())
            if "indices" in primitive:
                h.update(_glb.accessor(doc, blob, primitive["indices"]).tobytes())
    return h.hexdigest()


def uv_layout(folder: Path, material: int) -> list:
    name = mesh_file(folder)
    if not name:
        raise StepError("missing_input", "a standalone image has no UV layout")
    doc, blob = _glb.read(safe_file(folder, name))
    triangles = []
    for mesh in doc.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            if primitive.get("material") != material or primitive.get("mode", 4) != 4:
                continue
            if "TEXCOORD_0" not in primitive.get("attributes", {}):
                continue
            uv = _glb.accessor(doc, blob, primitive["attributes"]["TEXCOORD_0"])
            indices = (
                _glb.accessor(doc, blob, primitive["indices"]).ravel()
                if "indices" in primitive
                else np.arange(len(uv))
            )
            if len(indices) // 3 + len(triangles) > 100_000:
                raise StepError(
                    "bad_params",
                    "UV overlay supports up to 100,000 triangles; use the 3D UV checker on larger models",
                )
            triangles.extend(uv[indices.reshape(-1, 3)].round(6).tolist())
    return triangles


def uv_islands(path: Path, material: int, size: tuple[int, int]) -> np.ndarray:
    """Rasterize disconnected UV charts. Never mix a neighbouring chart into an SR window."""
    import cv2
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    doc, blob = _glb.read(path)
    width, height = size
    labels = np.zeros((height, width), np.int32)
    label = 0
    for mesh in doc.get("meshes", []):
        for p in mesh.get("primitives", []):
            if p.get("material") != material:
                continue
            if p.get("mode", 4) != 4 or "TEXCOORD_0" not in p.get("attributes", {}):
                raise StepError(
                    "missing_input", "AI atlas upscaling needs triangle meshes with UVs"
                )
            try:
                uv = _glb.accessor(doc, blob, p["attributes"]["TEXCOORD_0"]).astype(float)
                indices = (
                    _glb.accessor(doc, blob, p["indices"]).ravel()
                    if "indices" in p
                    else np.arange(len(uv))
                )
            except (KeyError, ValueError) as e:
                raise StepError(
                    "missing_input", "unpack sparse/compressed mesh data before AI atlas upscaling"
                ) from e
            if uv.min() < -1e-5 or uv.max() > 1.00001:
                raise StepError(
                    "missing_input",
                    "this material uses repeating UVs; upscale it as a standalone tileable material",
                )
            f = indices.reshape(-1, 3)
            # Shared UV edges, including duplicate vertices, connect triangles in the same chart.
            edges, a, b = {}, [], []
            for i, tri in enumerate(f):
                for x, y in ((0, 1), (1, 2), (2, 0)):
                    edge = tuple(
                        sorted((tuple(np.round(uv[tri[x]], 7)), tuple(np.round(uv[tri[y]], 7))))
                    )
                    if edge in edges:
                        a.append(i)
                        b.append(edges[edge])
                    else:
                        edges[edge] = i
            n, chart = connected_components(
                coo_matrix((np.ones(len(a)), (a, b)), shape=(len(f), len(f))), directed=False
            )
            if label + n > 4096:
                raise StepError(
                    "bad_params",
                    "this atlas has more than 4096 charts; use view-based detail enhancement instead",
                )
            pix = np.round(uv * [width - 1, height - 1]).astype(np.int32)
            for tri, island in zip(f, chart, strict=True):
                cv2.fillConvexPoly(labels, pix[tri], int(island + label + 1))
            label += n
    if not label:
        raise StepError("missing_input", "the selected material has no UV-mapped triangles")
    if label > 4096:
        raise StepError(
            "bad_params",
            "this atlas has more than 4096 charts; use view-based detail enhancement instead",
        )
    return labels


def resize_map(image: Image.Image, size: tuple[int, int], role: str) -> Image.Image:
    if any(n <= 0 for n in size) or size[0] * size[1] > MAX_PIXELS:
        raise StepError("bad_params", "the output texture would exceed 16 megapixels")
    if role != "normal":
        return image.resize(size, Image.Resampling.LANCZOS)
    vectors = (
        np.asarray(image.convert("RGB").resize(size, Image.Resampling.BILINEAR), np.float32) / 127.5
        - 1
    )
    vectors /= np.linalg.norm(vectors, axis=2, keepdims=True).clip(1e-8)
    return Image.fromarray(np.clip((vectors + 1) * 127.5 + 0.5, 0, 255).astype(np.uint8))
