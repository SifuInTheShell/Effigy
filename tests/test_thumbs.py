"""Library thumbnails (server/thumbs.py): pictures, the concepts grid, a parent's for non-visual versions."""

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from server import thumbs
from steps import _glb


def _version(asset, name, parent, outputs, files=()):
    d = asset / name
    d.mkdir()
    for f in files:
        Image.new("RGBA", (64, 96), (200, 30, 30, 255)).save(d / f)
    (d / "meta.json").write_text(
        json.dumps({"version": name, "parent": parent, "outputs": outputs})
    )


def test_thumbnails(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    a = tmp_path / "lamp"
    a.mkdir()
    (a / "asset.json").write_text(json.dumps({"id": "lamp", "name": "lamp"}))
    _version(a, "010_brief", None, {"brief": "brief.yaml"})
    cands = {f"cand_{k}": f"cand_{k}.png" for k in range(4)}
    _version(a, "020_concepts", "010_brief", cands, cands.values())
    _version(a, "030_validate", "020_concepts", {"report": "report.json"})

    assert thumbs.version_thumb("lamp", "010_brief") is None  # nothing to show, no parent
    grid = Image.open(thumbs.version_thumb("lamp", "020_concepts"))
    assert grid.size == (thumbs.SIZE, thumbs.SIZE)
    assert grid.getpixel((64, 64))[0] > 150 and grid.getpixel((192, 192))[0] > 150  # 4 cells filled
    assert thumbs.version_thumb("lamp", "030_validate") == a / "020_concepts" / thumbs.CACHE
    # an export's GLB holds the collision hull and LODs: it shows its parent's, and isn't the asset's model
    (a / "040_export" / "godot").mkdir(parents=True)
    (a / "040_export" / "godot" / "lamp.glb").write_bytes(b"glTF")
    meta = {"version": "040_export", "parent": "020_concepts", "step": "export"}
    meta["outputs"] = {"lamp.glb": "godot/lamp.glb"}
    (a / "040_export" / "meta.json").write_text(json.dumps(meta))
    assert thumbs.version_thumb("lamp", "040_export") == a / "020_concepts" / thumbs.CACHE
    assert thumbs.asset_thumb("lamp") == a / "020_concepts" / thumbs.CACHE


def test_a_video_preview_is_not_a_picture(tmp_path, monkeypatch):
    """M34: a mocap version's preview.mp4 must not reach the image code (PIL raised: a 500)."""
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    a = tmp_path / "dancer"
    a.mkdir()
    (a / "asset.json").write_text(json.dumps({"id": "dancer", "name": "dancer"}))
    _version(a, "010_bake", None, {"preview": "preview.png"}, ["preview.png"])
    _version(a, "020_mocap", "010_bake", {"preview": "preview.mp4"})
    (a / "020_mocap" / "preview.mp4").write_bytes(b"\0\0\0ftypmp42")
    assert thumbs.version_thumb("dancer", "020_mocap") == a / "010_bake" / thumbs.CACHE


def test_motion_only_glb_uses_parent_thumbnail_without_rendering(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    a = tmp_path / "dancer"
    a.mkdir()
    (a / "asset.json").write_text(json.dumps({"id": "dancer", "name": "dancer"}))
    _version(a, "001_upload", None, {"preview": "preview.png"}, ["preview.png"])
    _version(a, "002_mocap", "001_upload", {"clip": "clip.glb", "preview": "preview.mp4"})
    clip = a / "002_mocap" / "clip.glb"
    _glb.write(
        clip,
        {
            "asset": {"version": "2.0"},
            "nodes": [{"name": "hips"}],
            "animations": [{"channels": [], "samplers": []}],
        },
        bytearray(),
    )
    (clip.parent / "preview.mp4").write_bytes(b"video")

    def unexpected_render(*args):
        raise AssertionError("A motion-only GLB must never reach Blender's mesh renderer")

    monkeypatch.setattr(thumbs, "_render", unexpected_render)
    assert not thumbs._has_mesh(clip)
    assert thumbs._glbs("dancer", {"version": "002_mocap", "outputs": {"clip": "clip.glb"}}) == []
    assert thumbs.asset_thumb("dancer") == a / "001_upload" / thumbs.CACHE


def test_render_selects_mesh_even_when_motion_clip_is_first(tmp_path, monkeypatch):
    _glb.write(
        tmp_path / "clip.glb",
        {"asset": {"version": "2.0"}, "nodes": [{"name": "hips"}]},
        bytearray(),
    )
    shutil.copyfile(Path(__file__).parent / "fixtures/two_tris.glb", tmp_path / "mesh.glb")
    rendered = []
    monkeypatch.setattr(thumbs, "_render", lambda path, dst: rendered.append(path.name) or True)
    assert thumbs._make(tmp_path, {"clip": "clip.glb", "mesh": "mesh.glb"}, tmp_path / "thumb.png")
    assert rendered == ["mesh.glb"]


def test_material_thumbnail_prefers_extracted_maps_over_source_model(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    a = tmp_path / "cloth"
    a.mkdir()
    (a / "asset.json").write_text(json.dumps({"id": "cloth", "name": "Cloth", "kind": "texture"}))
    _version(a, "001_upload", None, {"mesh": "mesh.glb"})
    shutil.copyfile(Path(__file__).parent / "fixtures/two_tris.glb", a / "001_upload" / "mesh.glb")
    _version(a, "002_textures", "001_upload", {"preview": "preview.png"}, ["preview.png"])
    monkeypatch.setattr(
        thumbs,
        "_render",
        lambda *args: pytest.fail("material thumbnails must not render the source model"),
    )
    assert thumbs.asset_thumb("cloth") == a / "002_textures" / thumbs.CACHE


def test_pixel_art_thumbnails_keep_whole_pixels(tmp_path, monkeypatch):
    """Research 15: a pixelize version's preview is zoomed by a whole number, nearest neighbour, never blurred."""
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    a = tmp_path / "hero"
    a.mkdir()
    (a / "asset.json").write_text(json.dumps({"id": "hero", "name": "hero", "kind": "2d"}))
    d = a / "001_pixelize"
    d.mkdir()
    im = Image.new("RGBA", (2, 2), (30, 200, 30, 255))
    im.putpixel((0, 0), (200, 30, 30, 255))
    im.putpixel((1, 1), (30, 30, 200, 255))
    im.save(d / "preview.png")
    meta = {"version": "001_pixelize", "parent": None, "step": "pixelize"}
    (d / "meta.json").write_text(json.dumps(meta | {"outputs": {"preview": "preview.png"}}))
    t = Image.open(thumbs.version_thumb("hero", "001_pixelize")).convert("RGB")
    assert t.getpixel((10, 10)) == (200, 30, 30) and t.getpixel((200, 200)) == (30, 30, 200)
    assert t.getpixel((127, 127)) == (200, 30, 30) and t.getpixel((128, 128)) == (30, 30, 200)
