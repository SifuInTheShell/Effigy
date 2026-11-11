"""M42 backend: tags, listing fields, batch export copy, turntables."""

import io
import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server import blender_install, library
from server.app import app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path / "lib"))
    with TestClient(app) as c:
        yield c


def _version(asset, name, step, parent=None, outputs=None, **meta):
    d = library.asset_dir(asset) / name
    d.mkdir()
    m = {"version": name, "step": step, "parent": parent, "outputs": outputs or {}} | meta
    (d / "meta.json").write_text(json.dumps(m))
    return d


def test_tags_validation(lib):
    a = library.create_asset("Lamp")["id"]
    r = lib.patch(f"/api/assets/{a}", json={"tags": [" Prop ", "prop", "Lit", "x" * 32]})
    assert r.json()["tags"] == ["Prop", "Lit", "x" * 32]
    assert lib.get("/api/assets").json()[0]["tags"] == ["Prop", "Lit", "x" * 32]
    assert lib.patch(f"/api/assets/{a}", json={"tags": []}).json()["tags"] == []
    for bad in (["a,b"], [" "], ["x" * 33], [f"t{i}" for i in range(21)]):
        r = lib.patch(f"/api/assets/{a}", json={"tags": bad})
        assert (r.status_code, r.json()["error"]["code"]) == (400, "bad_params")
    assert lib.patch(f"/api/assets/{a}", json={}).status_code == 400


def test_listing_fields(lib):
    a = library.create_asset("Lamp")["id"]
    assert lib.get("/api/assets").json()[0]["imported"] is False
    _version(a, "001_upload", "upload", outputs={"mesh": "mesh.glb"}, licenses=["x"])
    _version(a, "002_clean", "clean", "001_upload", licenses=["y", "x"], restricted=True)
    row = lib.get("/api/assets").json()[0]
    assert (row["licenses"], row["restricted"], row["imported"]) == (["x", "y"], True, True)


def _export(asset, name="020_export", profile="godot", **meta):
    d = _version(asset, name, "export", "010_clean", {}, params={"profile": profile}, **meta)
    (d / profile).mkdir()
    (d / profile / "a.glb").write_bytes(b"glb")
    (d / profile / "CREDITS.txt").write_text("c")
    return d


def test_export_copy(lib, tmp_path):
    a = library.create_asset("Lamp")["id"]
    _version(a, "010_clean", "clean")
    _export(a)
    _export(a, "030_export", "unity", restricted=True)
    out = tmp_path / "out"
    out.mkdir()
    item = lambda v: {"asset_id": a, "version": v}
    body = {"folder": str(out), "items": [item("020_export"), item("030_export")]}
    r = lib.post("/api/export/copy", json=body).json()
    assert r["copied"] == [
        {
            "asset_id": a,
            "version": "020_export",
            "dir": str(out / "lamp_godot"),
            "files": ["CREDITS.txt", "a.glb"],
        }
    ]
    assert r["skipped"] == [{**item("030_export"), "reason": "restricted"}]
    (out / "lamp_godot" / "a.glb").write_bytes(b"mine")
    r = lib.post("/api/export/copy", json=body | {"items": [item("020_export")]}).json()
    assert r["skipped"][0]["reason"] == "exists" and not r["copied"]
    assert (out / "lamp_godot" / "a.glb").read_bytes() == b"mine"
    for folder, items, code in (
        ("relative", [item("020_export")], "bad_path"),
        (str(tmp_path / "nope"), [item("020_export")], "bad_path"),
        (str(out), [item("010_clean")], "bad_params"),
        (str(library.root()), [item("020_export")], "bad_path"),
        (str(library.asset_dir(a)), [item("020_export")], "bad_path"),
    ):
        r = lib.post("/api/export/copy", json={"folder": folder, "items": items})
        assert (r.status_code, r.json()["error"]["code"]) == (400, code)
    r = lib.post("/api/export/copy", json={"folder": str(out), "items": [item("999_export")]})
    assert r.status_code == 404
    r = lib.post("/api/export/copy", json={"folder": str(out), "items": []})
    assert (r.status_code, r.json()) == (200, {"copied": [], "skipped": []})


def test_export_copy_keeps_users_folders_and_skips_no_profile(lib, tmp_path):
    a = library.create_asset("Lamp")["id"]
    _version(a, "010_clean", "clean")
    _export(a)
    _version(a, "040_export", "export", "010_clean", {}, metrics=None)
    out = tmp_path / "out"
    (out / ".lamp_godot.tmp").mkdir(parents=True)
    (out / ".lamp_godot.tmp" / "mine.txt").write_text("m")
    items = [{"asset_id": a, "version": v} for v in ("020_export", "040_export")]
    r = lib.post("/api/export/copy", json={"folder": str(out), "items": items}).json()
    assert [c["version"] for c in r["copied"]] == ["020_export"]
    assert r["skipped"] == [{**items[1], "reason": "no_profile"}]
    assert (out / ".lamp_godot.tmp" / "mine.txt").read_text() == "m"
    assert sorted(p.name for p in out.iterdir()) == [".lamp_godot.tmp", "lamp_godot"]


def test_turntable_busy(lib):
    from server import jobs

    a = library.create_asset("Tris")["id"]
    d = _version(a, "010_clean", "clean", outputs={"mesh": "m.glb"})
    shutil.copy(FIXTURES / "two_tris.glb", d / "m.glb")
    url = f"/api/assets/{a}/versions/010_clean/turntable"
    with jobs.GPU:  # beside a job: not even queued
        r = lib.get(url)
        assert (r.status_code, r.json()["error"]["code"]) == (503, "busy")
        (d / "_turn.webp").write_bytes(b"RIFF")  # cached: served even while busy
        assert lib.get(url).status_code == 200


def test_turntable_renders_on_the_worker_not_in_the_request(lib, monkeypatch):
    """The hover answers at once (503 rendering); the worker renders and says so with a `thumb` event."""
    import threading

    from server import jobs
    from steps import _blender

    a = library.create_asset("Tris")["id"]
    d = _version(a, "010_clean", "clean", outputs={"mesh": "m.glb"})
    shutil.copy(FIXTURES / "two_tris.glb", d / "m.glb")
    gate = threading.Event()

    def render(op, inputs, out, params, timeout):
        gate.wait(5)
        out.mkdir(parents=True, exist_ok=True)
        for i in range(params["turn"]):
            Image.new("RGB", (8, 8), (i * 10, 0, 0)).save(out / f"look_{i}.png")

    monkeypatch.setattr(_blender, "run_blender", render)
    events = jobs.subscribe()
    try:
        url = f"/api/assets/{a}/versions/010_clean/turntable"
        r = lib.get(url)  # the render waits on the gate: this answered without it
        assert (r.status_code, r.json()["error"]["code"]) == (503, "rendering")
        gate.set()
        assert events.get(timeout=5) == ("thumb", {"kind": "turn", "asset_id": a, "version": "010_clean"})
        assert Image.open(io.BytesIO(lib.get(url).content)).n_frames == 24
    finally:
        jobs.unsubscribe(events)


@pytest.mark.skipif(
    not blender_install.blender_exe().is_file(), reason="no Blender (EFFIGY_BLENDER)"
)
def test_turntable(lib, monkeypatch):
    monkeypatch.setenv("EFFIGY_BLENDER", str(blender_install.blender_exe()))
    a = library.create_asset("Tris")["id"]
    d = _version(a, "010_clean", "clean", outputs={"mesh": "m.glb"})
    shutil.copy(FIXTURES / "two_tris.glb", d / "m.glb")
    _version(a, "020_validate", "validate", "010_clean", {"report": "r.json"})
    _version(a, "030_brief", "brief")
    url = f"/api/assets/{a}/versions/010_clean/turntable"
    deadline = time.time() + 180  # the first request queues the render on the worker
    while (r := lib.get(url)).status_code == 503 and time.time() < deadline:
        time.sleep(0.5)
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    im = Image.open(io.BytesIO(r.content))
    assert (im.n_frames, im.size) == (24, (256, 256))
    i = r.content.index(b"ANMF") + 20  # the first frame's duration: 3 bytes, little-endian
    assert int.from_bytes(r.content[i : i + 3], "little") == 67
    t0 = time.time()
    assert lib.get(f"/api/assets/{a}/versions/020_validate/turntable").content == r.content
    assert time.time() - t0 < 1  # cached, via the validate's model ancestor
    assert lib.get(f"/api/assets/{a}/turntable").status_code == 200
    r = lib.get(f"/api/assets/{a}/versions/030_brief/turntable")
    assert (r.status_code, r.json()["error"]["code"]) == (400, "nothing_to_show")
