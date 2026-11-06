"""Texture inspection and extraction. Processing uses the normal versioned job runner."""

import io
import shutil
from pathlib import Path

from server import jobs, library
from server.library import ApiError
from steps import _textures as tx
from steps._common import StepError


def inspect(asset: str, version: str) -> dict:
    try:
        return tx.inventory(library.version_dir(asset, version))
    except (StepError, OSError, ValueError, KeyError, IndexError) as e:
        raise ApiError(400, "missing_input", str(e)) from e


def image(asset: str, version: str, material: int, role: str) -> bytes:
    if role not in tx.ROLES or material < 0:
        raise ApiError(400, "bad_params", "choose a known map and material")
    try:
        image = tx.load_map(library.version_dir(asset, version), material, role)
        data = io.BytesIO()
        image.save(data, format="PNG")
        return data.getvalue()
    except (StepError, OSError, ValueError, KeyError, IndexError) as e:
        raise ApiError(400, "missing_input", str(e)) from e


def uv(asset: str, version: str, material: int) -> dict:
    try:
        return {"triangles": tx.uv_layout(library.version_dir(asset, version), material)}
    except (StepError, OSError, ValueError, KeyError, IndexError) as e:
        raise ApiError(400, "missing_input", str(e)) from e


def extract(asset: str, version: str, material: int, name: str) -> dict:
    from steps import textures

    library.version_dir(asset, version)
    inspect(asset, version)
    created = library.create_asset(name.strip() or f"{asset} material", kind="texture")
    out: Path | None = None
    try:
        out = library.new_version(created["id"], "textures")
        params = {
            "op": "extract",
            "source_asset": asset,
            "source_version": version,
            "source_material": material,
        }
        result = textures.run("extract", None, out, params)
        meta = library.write_meta(out, None, params, result)
    except Exception as e:
        target = library.asset_dir(created["id"]).resolve()
        if target.is_relative_to(library.root().resolve()):
            shutil.rmtree(target)
        if isinstance(e, StepError):
            raise ApiError(400, e.code, str(e)) from e
        raise
    jobs.publish("version_created", {"asset_id": created["id"], "meta": meta})
    return {"asset": created, "version": meta}
