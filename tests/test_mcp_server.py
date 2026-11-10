"""M9/M40: the effigy MCP bridge through the SDK client against a live uvicorn: the tool list and its annotations,
bounded waits and job_status, the same request returning the same job, cancel, read_file, export to_folder,
describe_steps, import_file (Blender) and look (Blender, and the app's look endpoint)."""

import contextlib
import json
import socket
import sys
import threading
import time
import zipfile
from pathlib import Path

import anyio
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from server import jobs, library
from server.app import app
from server.blender_install import blender_exe
from tests.conftest import glb_path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp" / "effigy_server.py"
BLENDER = blender_exe()
needs_blender = pytest.mark.skipif(not BLENDER.is_file(), reason="no Blender (EFFIGY_BLENDER)")
needs_ml = pytest.mark.skipif(
    not jobs.venv_python(".venv-ml").is_file(), reason="no .venv-ml (the export step runs there)"
)
READ_ONLY = {
    "list_assets",
    "get_asset",
    "get_version",
    "describe_steps",
    "look",
    "read_file",
    "job_status",
    "fit_check",
    "batch_status",
    "inspect_textures",
    "texture_map",
}
ALL = READ_ONLY | {
    "create_asset",
    "close_in_blender",
    "import_file",
    "import_batch",
    "run_step",
    "cancel_job",
    "validate",
    "export",
    "open_in_blender",
    "pull_from_blender",
    "edit_texture",
    "extract_material",
    "import_texture_set",
}


@contextlib.asynccontextmanager
async def session(api: str = "http://127.0.0.1:1"):
    params = StdioServerParameters(
        command=sys.executable, args=[str(SERVER)], env={"EFFIGY_API": api}
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
        init = await s.initialize()
        s.init_result = init
        yield s


async def call(s, tool: str, **args):
    r = await s.call_tool(tool, args)
    return r, (
        [] if r.is_error else json.loads(r.content[0].text) if r.content[0].type == "text" else []
    )


def test_tools_annotations_and_instructions():
    async def go():
        async with session() as s:
            return (
                s.init_result.instructions,
                (await s.list_tools()).tools,
                (await s.list_resources()).resources,
            )

    instructions, tools, resources = anyio.run(go)
    assert len(instructions) <= 512
    tools = {t.name: t for t in tools}
    assert set(tools) == ALL
    for name, tool in tools.items():
        a = tool.annotations
        assert a.read_only_hint is (name in READ_ONLY), name
        # only cancelling throws work away; everything else adds versions (the library is append-only)
        assert a.destructive_hint is (name == "cancel_job") and a.open_world_hint is False, name
        assert tool.description.startswith("Use this") and len(tool.description) < 2048, name
    assert tools["run_step"].input_schema["required"] == ["asset_id", "parent", "step"]
    assert [str(r.uri) for r in resources] == ["effigy://guide"]


def test_every_texture_operation_is_exposed_by_mcp():
    import importlib.util

    from steps.textures import OPS

    spec = importlib.util.spec_from_file_location("effigy_texture_contract", SERVER)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    assert bridge.TEXTURE_OPS == OPS


@pytest.fixture
def live(tmp_path, monkeypatch):
    """The real app on a free port over a temp library; yields (api url, asset id with an upload)."""
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path / "lib"))
    monkeypatch.setenv("EFFIGY_BLENDER", str(BLENDER))
    asset = library.create_asset("crate")["id"]
    library.import_file(asset, "m.glb", glb_path())
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", asset
    server.should_exit = True


def test_bounded_wait_dedupe_status_and_cancel(live):
    api, asset = live
    args = {"asset_id": asset, "parent": "001_upload", "step": "_echo"}

    async def go():
        async with session(api) as s:
            _, a = await call(s, "run_step", **args, params={"sleep": 4}, wait_s=1)
            _, b = await call(s, "run_step", **args, params={"sleep": 4}, wait_s=0)
            _, done = await call(s, "job_status", job_id=a["job_id"], wait_s=30)
            _, c = await call(s, "run_step", **args, params={"sleep": 60}, wait_s=0)
            _, gone = await call(s, "cancel_job", job_id=c["job_id"])
            bad, _ = await call(s, "run_step", **args, params={"fail": "nope"})
            return a, b, done, gone, bad

    a, b, done, gone, bad = anyio.run(go)
    assert a["status"] == "running" and a["job_id"] == b["job_id"]
    assert done["status"] == "done" and done["version"]["step"] == "_echo"
    assert gone["status"] == "cancelled"
    assert bad.is_error and "nope: asked to fail" in bad.content[0].text


def test_read_file_and_describe_steps(live):
    api, asset = live

    async def go():
        async with session(api) as s:
            _, ran = await call(s, "run_step", asset_id=asset, parent="001_upload", step="_echo")
            v = ran["version"]["version"]
            d = library.root() / asset / v
            (d / "big.json").write_text("[" + "0," * 150_000 + "0]")
            meta = json.loads((d / "meta.json").read_text())
            (d / "meta.json").write_text(json.dumps(meta | {"outputs": {"big": "big.json"}}))
            ok = await call(s, "read_file", asset_id=asset, version=v, name="meta.json")
            out = [
                await call(s, "read_file", asset_id=asset, version=v, name=n)
                for n in ("../../asset.json", "echo.json", "big.json")
            ]
            steps = await call(s, "describe_steps")
            one = await call(s, "describe_steps", step="export")
            listed = await call(s, "list_assets", query="CRA")
            got = await call(s, "get_asset", asset_id=asset)
            return ok, out, steps[1], one[1], listed[1], got[1]

    ok, out, steps, one, listed, got = anyio.run(go)
    assert not ok[0].is_error
    assert all(r.is_error for r, _ in out)  # outside the version, not an output, over 200 KB
    assert "too_large" in out[2][0].content[0].text
    assert set(steps["steps"]) == {p.stem for p in (ROOT / "steps").glob("[a-z]*.py")}
    assert all(v["does"] for v in steps["steps"].values()) and steps["guide"]
    assert "profile" in one["doc"] and "godot" in one["doc"]
    assert listed["total"] == 1 and listed["assets"][0]["newest"]["label"] == "_echo"
    assert [v["label"] for v in got["versions"]][:1] == ["Imported model"]


@needs_ml
def test_texture_workflow_through_mcp(live, tmp_path):
    from PIL import Image

    from steps import _textures as tx

    endpoint, _ = live
    packed = tmp_path / "unity-mask.tga"
    Image.new("RGBA", (64, 64), (80, 160, 0, 200)).save(packed)
    model = tmp_path / "material.glb"
    tx.patch_glb(
        ROOT / "tests/fixtures/two_tris.glb",
        model,
        {"basecolor": Image.new("RGB", (64, 64), "red")},
        0,
    )

    async def go():
        async with session(endpoint) as s:
            _, imported = await call(
                s,
                "import_texture_set",
                name="Engine cloth",
                packed_path=str(packed),
                packing="unity_mask",
                tileable=True,
            )
            asset, version = imported["asset_id"], imported["version"]["version"]
            _, info = await call(s, "inspect_textures", asset_id=asset, version=version)
            image, _ = await call(
                s, "texture_map", asset_id=asset, version=version, role="roughness"
            )
            _, resized = await call(
                s,
                "edit_texture",
                asset_id=asset,
                parent=version,
                op="resize",
                params={"role": "roughness", "size": 128},
            )
            _, saved = await call(
                s,
                "extract_material",
                asset_id=asset,
                version=resized["version"]["version"],
                name="Saved cloth",
            )
            _, applied = await call(
                s,
                "edit_texture",
                asset_id=asset,
                parent=version,
                op="apply",
                params={
                    "source_asset": saved["asset"]["id"],
                    "source_version": saved["version"]["version"],
                },
            )
            _, exported = await call(
                s,
                "export",
                asset_id=asset,
                version=applied["version"]["version"],
                profile="textures",
            )
            _, listed = await call(s, "list_assets", kind="texture")
            _, model_import = await call(s, "import_file", path=str(model), kind="texture")
            _, model_info = await call(
                s,
                "inspect_textures",
                asset_id=model_import["asset_id"],
                version=model_import["version"]["version"],
            )
            _, docs = await call(s, "describe_steps", step="textures")
            bad, _ = await call(s, "edit_texture", asset_id=asset, parent=version, op="invented")
            return imported, info, image, resized, exported, listed, model_info, docs, bad

    imported, info, image, resized, exported, listed, model_info, docs, bad = anyio.run(go)
    assert imported["status"] == "done"
    assert not info["model"] and set(info["materials"][0]["maps"]) == {
        "roughness",
        "metallic",
        "ao",
    }
    assert image.content[0].type == "image"
    assert resized["version"]["metrics"]["maps"]["roughness"]["width"] == 128
    assert exported["status"] == "done" and "textures.zip" in exported["version"]["outputs"]
    assert listed["total"] == 2 and all(a["kind"] == "texture" for a in listed["assets"])
    assert not model_info["model"] and "basecolor" in model_info["materials"][0]["maps"]
    assert "packed_upload" in docs["doc"] and "mask_source" in docs["doc"]
    assert bad.is_error


@needs_ml
def test_export_to_folder(live, tmp_path):
    api, asset = live
    folder = tmp_path / "game"
    folder.mkdir()
    # the upload holds a fake glb; an echo version with a real mesh is what export reads
    d = library.new_version(asset, "clean")
    (d / "mesh.glb").write_bytes((ROOT / "tests/fixtures/two_tris.glb").read_bytes())
    library.write_meta(d, "001_upload", {}, {"outputs": {"mesh": "mesh.glb"}})

    async def go():
        async with session(api) as s:
            args = {
                "asset_id": asset,
                "version": d.name,
                "profile": "obj",
                "to_folder": str(folder),
            }
            first = await call(s, "export", **args)
            again = await call(s, "export", **args)
            rel = await call(s, "export", **args | {"to_folder": "relative"})
            return first[1], again[0], rel[0]

    first, again, rel = anyio.run(go)
    target = folder / f"{asset}_obj"
    assert first["status"] == "done" and (target / "CREDITS.txt").is_file()
    assert {Path(f) for f in first["files"]} == {p for p in target.rglob("*") if p.is_file()}
    assert again.is_error and "exists" in again.content[0].text
    assert rel.is_error and "bad_path" in rel.content[0].text


@needs_blender
def test_import_file_obj_zip(live, tmp_path):
    api, _ = live
    z = tmp_path / "tri.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("tri.obj", "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")

    async def go():
        async with session(api) as s:
            bad = await call(s, "import_file", path="relative.glb")
            _, out = await call(s, "import_file", path=str(z), wait_s=45)
            return bad[0], out

    bad, out = anyio.run(go)
    assert bad.is_error and "bad_path" in bad.content[0].text
    assert out["upload"].endswith("_upload")
    assert out["status"] == "running" or out["version"]["step"] == "import_model"


@needs_blender
def test_look_returns_an_image(live):
    api, asset = live
    d = library.new_version(asset, "clean")
    (d / "mesh.glb").write_bytes((ROOT / "tests/fixtures/two_tris.glb").read_bytes())
    library.write_meta(d, "001_upload", {}, {"outputs": {"mesh": "mesh.glb"}})

    async def go():
        async with session(api) as s:
            return await s.call_tool("look", {"asset_id": asset, "version": d.name, "size": 256})

    r = anyio.run(go)
    assert not r.is_error, r.content[0].text
    assert r.content[0].type == "image" and r.content[0].mime_type == "image/png"
    assert r.content[1].text.startswith("saved at ") and r.content[1].text.endswith("_look_256.png")


def test_the_bridge_follows_the_app_port(tmp_path, monkeypatch):
    """The MCP bridge's API address: EFFIGY_API, else EFFIGY_PORT, else the port chosen in Setup, else 8000."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("effigy_bridge_port", SERVER)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    for k in ("EFFIGY_API", "EFFIGY_PORT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("EFFIGY_SETTINGS", str(tmp_path / "settings.json"))
    assert bridge._api() == "http://127.0.0.1:8000"
    (tmp_path / "settings.json").write_text('{"port": 8001}', encoding="utf-8")
    assert bridge._api() == "http://127.0.0.1:8001"
    monkeypatch.setenv("EFFIGY_PORT", "8002")
    assert bridge._api() == "http://127.0.0.1:8002"
    monkeypatch.setenv("EFFIGY_API", "http://127.0.0.1:9000")
    assert bridge._api() == "http://127.0.0.1:9000"


def test_import_file_takes_a_picture_from_the_library(live):
    """Animate this character for an agent: a 2D asset's picture into a new asset, through the API (no library path)."""
    api, _ = live
    two = library.create_asset("viking", kind="2d")["id"]
    d = library.new_version(two, "concepts")
    picture = (ROOT / "tests/fixtures/two_tris.glb").read_bytes()[
        :64
    ]  # the bytes travel as they are
    (d / "image_1.png").write_bytes(picture)
    library.write_meta(d, None, {"op": "image"}, {"outputs": {"image_1": "image_1.png"}})

    async def go():
        async with session(api) as s:
            bad = await call(s, "import_file", library_file=f"{two}/{d.name}")
            gone = await call(s, "import_file", library_file=f"{two}/{d.name}/image_9.png")
            _, out = await call(
                s, "import_file", library_file=f"{two}/{d.name}/image_1.png", kind="2d"
            )
            return bad[0], gone[0], out

    bad, gone, out = anyio.run(go)
    assert bad.is_error and "bad_path" in bad.content[0].text
    assert gone.is_error
    assert out["status"] == "uploaded"
    new = library.get_asset(out["asset_id"])
    assert new["asset"]["name"] == "viking (2D)"
    upload = library.asset_dir(out["asset_id"]) / out["upload"]
    assert [f.read_bytes() for f in upload.glob("*.png")] == [picture]


def test_import_file_into_an_existing_asset(live, tmp_path):
    """redraw's protect mask: a picture uploaded beside the asset's own as a new version, nothing run on it."""
    api, _ = live
    two = library.create_asset("background", kind="2d")["id"]
    before = len(library.list_assets())
    mask = tmp_path / "mask.png"
    mask.write_bytes(b"not really a png")

    async def go():
        async with session(api) as s:
            bad = await call(s, "import_file", path=str(mask), asset_id="nope")
            _, first = await call(s, "import_file", path=str(mask), asset_id=two)
            _, second = await call(s, "import_file", path=str(mask), asset_id=two)
            return bad[0], first, second

    bad, first, second = anyio.run(go)
    assert bad.is_error
    assert first["asset_id"] == second["asset_id"] == two
    assert first["status"] == "uploaded" and first["upload"] != second["upload"]
    assert len(library.list_assets()) == before


def test_an_agent_sees_the_versions_named_as_the_app_names_them():
    """The version labels the MCP server gives get_asset are the UI's (ui/src/workflow.ts STEPS): a step the UI names
    and the server does not would show an agent its raw name (transform, 2026-10-13, was one)."""
    import importlib.util
    import re

    spec = importlib.util.spec_from_file_location("effigy_bridge_labels", SERVER)
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    ts = (ROOT / "ui" / "src" / "workflow.ts").read_text(encoding="utf-8")
    block = re.search(r"const STEPS: Record<string, string> = \{(.*?)\n\};", ts, re.DOTALL)[1]
    ui = dict(re.findall(r'^\s*(\w+): "([^"]+)",', block, re.MULTILINE))
    assert ui and ui == server.STEP_LABELS
