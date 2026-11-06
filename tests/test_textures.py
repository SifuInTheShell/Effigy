"""Texture versions preserve GLB structure, map semantics and immutable source material versions."""

import copy
import io
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server import library
from server.app import app
from steps import _glb, export, textures
from steps import _textures as tx
from steps._common import StepError


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path / "library"))
    return tmp_path / "library"


def finish(asset, parent=None, op="create", **params):
    out = library.new_version(asset, "textures")
    result = textures.run(op, library.version_dir(asset, parent) if parent else None, out, params)
    library.write_meta(out, parent, {"op": op, **params}, result)
    return out


def standalone(name="material", **params):
    asset = library.create_asset(name, kind="texture")["id"]
    return finish(asset, size=64, **params)


@pytest.fixture
def animated(lib):
    """A real animated fixture, augmented with a PBR material and UVs without touching its skin/clip data."""
    asset = library.create_asset("animated")["id"]
    out = library.new_version(asset, "import_model")
    doc, blob = _glb.read(Path(__file__).parent / "fixtures/anim.glb")
    doc["materials"] = [
        {
            "name": "Cloth",
            "pbrMetallicRoughness": {"roughnessFactor": 0.7, "metallicFactor": 0.1},
            "doubleSided": True,
            "extras": {"keep": "sentinel"},
        }
    ]
    for mesh in doc["meshes"]:
        for primitive in mesh["primitives"]:
            n = len(_glb.accessor(doc, blob, primitive["attributes"]["POSITION"]))
            uv = np.tile([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]], (n // 3 + 1, 1))[:n].astype("<f4")
            blob.extend(b"\0" * (-len(blob) % 4))
            doc["bufferViews"].append(
                {"buffer": 0, "byteOffset": len(blob), "byteLength": uv.nbytes}
            )
            blob.extend(uv.tobytes())
            doc["accessors"].append(
                {
                    "bufferView": len(doc["bufferViews"]) - 1,
                    "componentType": 5126,
                    "count": n,
                    "type": "VEC2",
                }
            )
            primitive["attributes"]["TEXCOORD_0"] = len(doc["accessors"]) - 1
            primitive["material"] = 0
    doc["buffers"][0]["byteLength"] = len(blob)
    _glb.write(out / "seed.glb", doc, blob)
    tx.patch_glb(
        out / "seed.glb",
        out / "mesh.glb",
        {
            "basecolor": Image.new("RGBA", (64, 64), (40, 80, 120, 180)),
            "normal": Image.new("RGB", (64, 64), (128, 128, 255)),
            "roughness": Image.new("L", (32, 32), 150),
            "metallic": Image.new("L", (32, 32), 20),
        },
        0,
    )
    (out / "rig.json").write_text('{"template": "mixamo52"}', encoding="utf-8")
    library.write_meta(out, None, {}, {"outputs": {"rigged_anim": "mesh.glb", "rig": "rig.json"}})
    return out


def test_patch_retains_every_geometry_skin_and_animation_accessor(animated):
    doc, blob = _glb.read(animated / "mesh.glb")
    before = copy.deepcopy(doc)
    out = finish(animated.parent.name, animated.name, "adjust", saturation=0, role="basecolor")
    after, written = _glb.read(out / "mesh.glb")
    for key in ("meshes", "accessors", "skins", "animations", "nodes", "scenes", "scene"):
        assert before.get(key) == after.get(key), key
    assert written[: len(blob)] == blob
    assert after["materials"][0]["extras"] == {"keep": "sentinel"}
    assert after["materials"][0]["doubleSided"]
    assert (out / "rig.json").read_bytes() == (animated / "rig.json").read_bytes()
    meta = tx.metadata(out)
    assert meta["outputs"]["rigged_anim"] == "mesh.glb"
    old, _ = tx.load_set(animated)
    new, _ = tx.load_set(out)
    assert new["normal"].tobytes() == old["normal"].tobytes()
    assert new["roughness"].tobytes() == old["roughness"].tobytes()
    assert new["basecolor"].getchannel("A").tobytes() == old["basecolor"].getchannel("A").tobytes()
    assert doc["skins"] and doc["animations"]


def test_material_inventory_is_per_slot_and_replacement_does_not_change_shared_uses(animated):
    path = animated / "mesh.glb"
    doc, blob = _glb.read(path)
    doc["materials"].append(copy.deepcopy(doc["materials"][0]))
    _glb.write(path, doc, blob)
    out = animated / "replaced.glb"
    tx.patch_glb(path, out, {"basecolor": Image.new("RGB", (16, 16), "red")}, 0)
    new, _ = _glb.read(out)
    assert new["materials"][1] == doc["materials"][1]
    assert (
        new["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]["index"]
        != new["materials"][1]["pbrMetallicRoughness"]["baseColorTexture"]["index"]
    )
    assert len(tx.inventory(animated)["materials"]) == 2


def test_library_apply_pins_source_and_copies_files(lib, animated):
    source = standalone(color="#ff0000", tileable=True)
    out = finish(
        animated.parent.name,
        animated.name,
        "apply",
        source_asset=source.parent.name,
        source_version=source.name,
    )
    maps, _ = tx.load_set(out)
    assert maps["basecolor"].getpixel((0, 0))[:3] == (255, 0, 0)
    source_maps, manifest = tx.load_set(source)
    source_maps["basecolor"] = Image.new("RGB", (64, 64), "green")
    tx.write_set(source, source_maps, manifest)
    assert tx.load_set(out)[0]["basecolor"].getpixel((0, 0))[:3] == (255, 0, 0)
    assert tx.metadata(out)["metrics"]["source"]["version"] == source.name


def test_repeat_mapping_preserves_uv_buffers(lib, animated):
    source = standalone(tileable=True)
    out = finish(
        animated.parent.name,
        animated.name,
        "apply",
        source_asset=source.parent.name,
        source_version=source.name,
        mapping="repeat",
        repeat=3,
        rotation=45,
    )
    doc, _ = _glb.read(out / "mesh.glb")
    transform = doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]["extensions"][
        "KHR_texture_transform"
    ]
    assert transform["scale"] == [3, 3]
    assert tx.geometry_id(animated / "mesh.glb") == tx.geometry_id(out / "mesh.glb")


def test_normal_resize_renormalizes_and_data_maps_avoid_rgb_sr(lib):
    src = standalone()
    maps, manifest = tx.load_set(src)
    maps["normal"] = Image.new("RGB", (2, 2), (200, 200, 200))
    tx.write_set(src, maps, manifest)
    out = finish(src.parent.name, src.name, "resize", size=128, role="normal")
    normal = np.asarray(tx.load_set(out)[0]["normal"], float) / 127.5 - 1
    assert np.max(np.abs(np.linalg.norm(normal, axis=2) - 1)) < 0.01
    with pytest.raises(StepError, match="colour maps"):
        finish(src.parent.name, src.name, "upscale", size=128, role="normal")


def test_standalone_import_preserves_16bit_height_and_converts_directx(lib):
    asset = library.create_asset("imported", kind="texture")["id"]
    uploads = {}
    for role, image in {
        "normal": Image.new("RGB", (64, 64), (128, 200, 255)),
        "height": Image.fromarray(np.full((64, 64), 40000, np.uint16)),
    }.items():
        up = library.new_version(asset, "upload")
        image.save(up / "image.png")
        library.write_meta(up, None, {}, {"outputs": {"image": "image.png"}})
        uploads[role] = up.name
    out = finish(asset, up.name, "import", uploads=uploads, normal_convention="DirectX")
    maps, _ = tx.load_set(out)
    assert set(maps) == {"normal", "height"}
    assert maps["normal"].getpixel((0, 0)) == (128, 55, 255)
    assert np.asarray(maps["height"]).max() == 40000
    adjusted = finish(asset, out.name, "adjust", role="height", brightness=1.1)
    assert np.asarray(tx.load_set(adjusted)[0]["height"]).max() == 44000
    resized = finish(asset, adjusted.name, "resize", role="height", size=128)
    assert np.asarray(tx.load_set(resized)[0]["height"]).max() == 44000


def test_wrapped_upscale_provides_periodic_context_and_preserves_alpha():
    a = np.zeros((8, 8, 4), np.uint8)
    a[..., 0] = np.arange(8)[None] * 25
    a[..., 3] = 120
    seen = []

    def draw(rgb):
        seen.append(rgb.copy())
        return np.repeat(np.repeat(rgb, 4, 0), 4, 1)

    result = textures.upscale_image(Image.fromarray(a), 32, True, draw)
    assert seen[0].shape == (24, 24, 3)
    assert np.array_equal(seen[0][:8, :8], a[..., :3])
    assert np.asarray(result)[..., 3].min() == 120


def test_island_upscale_never_sends_other_chart_colour_to_model():
    a = np.zeros((8, 16, 3), np.uint8)
    a[:, :8] = (255, 0, 0)
    a[:, 8:] = (0, 0, 255)
    labels = np.ones((8, 16), np.int32)
    labels[:, 8:] = 2
    seen = []

    def draw(rgb):
        seen.append(rgb)
        return np.repeat(np.repeat(rgb, 4, 0), 4, 1)

    result = textures.upscale_image(Image.fromarray(a), 64, False, draw, labels)
    assert len(seen) == 2
    assert np.all(seen[0] == (255, 0, 0))
    assert np.all(seen[1] == (0, 0, 255))
    assert result.size == (64, 32)


def test_surface_estimates_keep_authored_maps(lib):
    src = standalone()
    maps, manifest = tx.load_set(src)
    maps["normal"] = Image.new("RGB", (64, 64), (50, 50, 255))
    tx.write_set(src, maps, manifest)
    out = finish(src.parent.name, src.name, "surface")
    result, manifest = tx.load_set(out)
    assert set(result) == {"basecolor", "height", "normal", "roughness", "metallic"}
    assert result["normal"].tobytes() == maps["normal"].tobytes()
    assert "not physical recovery" in manifest["surface_method"]


def test_export_set_has_archive_metadata_and_credits(lib, tmp_path):
    import zipfile

    src = standalone(tileable=True)
    out = tmp_path / "export"
    out.mkdir()
    result = export.run("run", src, out, {"profile": "textures"})
    assert "textures.zip" in result["outputs"]
    with zipfile.ZipFile(out / "textures/textures.zip") as bundle:
        assert {"basecolor.png", "material.json", "CREDITS.txt", "LICENSES.json"} <= set(
            bundle.namelist()
        )


def test_api_inventory_image_extract_and_bad_inputs(lib, animated):
    client = TestClient(app)
    path = f"/api/assets/{animated.parent.name}/versions/{animated.name}/textures"
    response = client.get(path)
    assert response.status_code == 200
    assert response.json()["rigged"] and response.json()["animated"]
    image = client.get(path + "/image?role=basecolor")
    assert image.status_code == 200
    assert Image.open(io.BytesIO(image.content)).size == (64, 64)
    assert client.get(path + "/image?role=../settings").status_code == 400
    assert client.get(path + "/image?material=999").status_code == 400
    extracted = client.post(path + "/extract", json={"name": "Reusable cloth"})
    assert extracted.status_code == 201, extracted.text
    assert extracted.json()["asset"]["kind"] == "texture"
    assert client.get(f"/api/assets/{extracted.json()['asset']['id']}").status_code == 200


def test_source_references_and_texture_paths_cannot_escape_library(lib, tmp_path):
    src = standalone()
    with pytest.raises(StepError, match="valid asset"):
        finish(src.parent.name, src.name, "apply", source_asset="..", source_version=src.name)
    (tmp_path / "secret.png").write_bytes(b"secret")
    with pytest.raises(StepError, match="outside"):
        tx.safe_file(src, "../../../secret.png")


def test_cross_asset_restriction_survives_step_protocol(lib):
    from steps._common import _call

    source = standalone()
    meta = tx.metadata(source)
    meta["restricted"] = True
    (source / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    dest = library.create_asset("dest", kind="texture")["id"]
    out = library.new_version(dest, "textures")
    kind, result = _call(
        textures.run,
        "extract",
        None,
        str(out),
        {"source_asset": source.parent.name, "source_version": source.name},
    )
    assert kind == "RESULT" and result["restricted"]
    final = library.write_meta(out, None, {}, result)
    assert final["restricted"]


def test_secondary_image_import_preserves_restrictions_and_licenses(lib):
    src = standalone()
    upload = library.new_version(src.parent.name, "upload")
    Image.new("RGB", (64, 64), "red").save(upload / "image.png")
    library.write_meta(
        upload,
        None,
        {},
        {"outputs": {"image": "image.png"}, "restricted": True, "licenses": ["CC-BY-4.0"]},
    )
    out = finish(src.parent.name, src.name, "replace", uploads={"basecolor": upload.name})
    meta = tx.metadata(out)
    assert meta["restricted"] and "CC-BY-4.0" in meta["licenses"]


def test_extreme_aspect_ratio_resize_rejected_before_allocation():
    image = Image.new("RGB", (1, 4096))
    with pytest.raises(StepError, match="16 megapixels"):
        textures.resized(image, 4096, "basecolor")


def test_invalid_material_settings_return_readable_api_error(animated):
    path = animated / "mesh.glb"
    doc, blob = _glb.read(path)
    doc["materials"][0]["pbrMetallicRoughness"]["roughnessFactor"] = "broken"
    _glb.write(path, doc, blob)
    response = TestClient(app).get(
        f"/api/assets/{animated.parent.name}/versions/{animated.name}/textures"
    )
    assert response.status_code == 400
    assert "finite number" in response.text


def test_model_height_sidecar_survives_later_texture_edits(animated):
    first = finish(animated.parent.name, animated.name, "surface")
    second = finish(animated.parent.name, first.name, "adjust", saturation=0)
    assert tx.load_set(first)[0]["height"].tobytes() == tx.load_set(second)[0]["height"].tobytes()


def test_import_defaults_nonmetal_and_preserves_transparency(lib):
    asset = library.create_asset("alpha", kind="texture")["id"]
    upload = library.new_version(asset, "upload")
    Image.new("RGBA", (64, 64), (20, 30, 40, 100)).save(upload / "image.png")
    library.write_meta(upload, None, {}, {"outputs": {"image": "image.png"}})
    out = finish(asset, upload.name, "import", uploads={"basecolor": upload.name})
    maps, manifest = tx.load_set(out)
    assert manifest["settings"]["metallicFactor"] == 0
    assert manifest["settings"]["alphaMode"] == "BLEND"
    assert maps["basecolor"].getchannel("A").getextrema() == (100, 100)


def test_previous_operation_previews_are_not_dangling_outputs(lib):
    src = standalone()
    maps, manifest = tx.load_set(src)
    manifest["previews"] = {"views": "old-views.png"}
    tx.write_set(src, maps, manifest)
    out = finish(src.parent.name, src.name, "adjust", brightness=1.2)
    assert "views" not in tx.metadata(out)["outputs"]
    assert "previews" not in tx.load_set(out)[1]


@pytest.mark.parametrize(
    "packing, expected",
    [
        ("orm", {"ao": 20, "roughness": 60, "metallic": 100}),
        ("unity_mask", {"metallic": 20, "ao": 60, "roughness": 75}),
        ("unity_metallic", {"metallic": 20, "roughness": 75}),
    ],
)
def test_engine_packed_maps_unpack_with_credits_and_separate_overrides(lib, packing, expected):
    asset = library.create_asset("engine material", kind="texture")["id"]
    packed = library.new_version(asset, "upload")
    Image.new("RGBA", (64, 64), (20, 60, 100, 180)).save(packed / "image.tga")
    library.write_meta(
        packed,
        None,
        {},
        {"outputs": {"image": "image.tga"}, "restricted": True, "licenses": ["source-license"]},
    )
    out = finish(asset, packed.name, "import", packed_upload=packed.name, packing=packing)
    maps, _ = tx.load_set(out)
    assert {role: image.getpixel((0, 0)) for role, image in maps.items()} == expected
    meta = tx.metadata(out)
    assert meta["restricted"] and "source-license" in meta["licenses"]
    separate = library.new_version(asset, "upload")
    Image.new("L", (64, 64), 220).save(separate / "image.png")
    library.write_meta(separate, None, {}, {"outputs": {"image": "image.png"}})
    out = finish(
        asset,
        separate.name,
        "import",
        packed_upload=packed.name,
        packing=packing,
        uploads={"roughness": separate.name},
    )
    assert tx.load_set(out)[0]["roughness"].getpixel((0, 0)) == 220


@pytest.mark.parametrize("op", ["enhance", "retexture", "project"])
def test_ai_wrappers_keep_original_rig_and_only_replace_requested_maps(animated, monkeypatch, op):
    pytest.importorskip("torch")
    from steps import project, refine, trellis2

    before, blob = _glb.read(animated / "mesh.glb")

    def fake(*args):
        work = Path(args[-2])
        Image.new("RGBA", (64, 64), (100, 150, 200, 180)).save(work / "basecolor.png")
        Image.new("RGB", (64, 64), (255, 80, 40)).save(work / "orm.png")
        return {
            "outputs": {"basecolor": "basecolor.png", "orm": "orm.png"},
            "metrics": {"refined": 0.42, "changed_outside": 0},
        }

    monkeypatch.setattr(refine, "run", fake)
    monkeypatch.setattr(trellis2, "texture", fake)
    monkeypatch.setattr(project, "run", fake)
    out = finish(animated.parent.name, animated.name, op, prompt="fine linen")
    after, written = _glb.read(out / "mesh.glb")
    for key in ("meshes", "skins", "animations", "nodes", "accessors"):
        assert before[key] == after[key]
    assert written[: len(blob)] == blob
    assert tx.metadata(out)["metrics"]["changed_outside"] == 0
    old, _ = tx.load_set(animated)
    maps, _ = tx.load_set(out)
    assert maps["basecolor"].getpixel((0, 0)) == (100, 150, 200, 180)
    assert maps["normal"].tobytes() == old["normal"].tobytes()
    if op == "retexture":
        assert maps["roughness"].getpixel((0, 0)) == 80
        assert maps["metallic"].getpixel((0, 0)) == 40
    else:
        assert maps["roughness"].tobytes() == old["roughness"].tobytes()


def test_extract_constant_only_glb_material(lib):
    asset = library.create_asset("solid material", kind="texture")["id"]
    upload = library.new_version(asset, "upload")
    doc, blob = _glb.read(Path(__file__).parent / "fixtures/two_tris.glb")
    doc["materials"] = [
        {
            "name": "Solid red",
            "pbrMetallicRoughness": {
                "baseColorFactor": [1, 0, 0, 1],
                "roughnessFactor": 0.4,
                "metallicFactor": 0,
            },
        }
    ]
    for primitive in doc["meshes"][0]["primitives"]:
        primitive["material"] = 0
    _glb.write(upload / "mesh.glb", doc, blob)
    library.write_meta(upload, None, {}, {"outputs": {"mesh": "mesh.glb"}})
    out = finish(asset, upload.name, "extract", source_asset=asset, source_version=upload.name)
    maps, manifest = tx.load_set(out)
    assert maps["basecolor"].getpixel((0, 0)) == (255, 255, 255, 255)
    assert manifest["settings"]["baseColorFactor"] == [1, 0, 0, 1]
    assert manifest["settings"]["roughnessFactor"] == 0.4
    assert tx.mesh_file(out) is None
