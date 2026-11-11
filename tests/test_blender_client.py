import ast
import re
from pathlib import Path

"""M8: the add-on socket framing against a fake Blender, error mapping, Open/Pull wiring."""

import contextlib
import io
import json
import socket
import struct
import threading
import time

import pytest

from server import blender_client, library
from server.library import ApiError
from tests.conftest import glb_path


def _closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()


def fake_blender(monkeypatch, handler):
    """One-shot-per-connection server speaking the add-on's framing; handler(request) -> reply."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    monkeypatch.setattr(blender_client, "PRO", srv.getsockname())
    monkeypatch.setattr(blender_client, "FREE", _closed_port())
    seen = []

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                n = struct.unpack(">I", conn.recv(4))[0]
                data = b""
                while len(data) < n:
                    data += conn.recv(n - len(data))
                req = json.loads(data)
                seen.append(req)
                reply = handler(req)
                if reply is None:  # a hung Blender: keep the connection open, say nothing
                    time.sleep(1)
                    continue
                body = json.dumps(reply).encode()
                conn.sendall(struct.pack(">I", len(body)) + body)

    threading.Thread(target=serve, daemon=True).start()
    return srv, seen


def test_framing_and_errors(monkeypatch):
    replies = iter(
        [
            {"status": "ok", "result": {"stdout": "", "result": [1, 2]}},
            {"status": "error", "message": "Unknown command"},
            {"status": "ok", "result": {"status": "error", "message": "File not found: x"}},
            {"status": "ok", "result": {"stdout": "", "error": "Traceback ... AssertionError"}},
        ]
    )
    srv, seen = fake_blender(monkeypatch, lambda req: next(replies))
    assert blender_client.run_python("__result__ = [1, 2]", a="x", version=None, on=True) == [1, 2]
    assert seen[0]["command"] == "execute_python"
    code = seen[0]["params"]["code"]
    assert code.startswith("a = 'x'\nversion = None\non = True\n__result__ = [1, 2]\n")
    # Python literals, not JSON: a version left out was `null`, a NameError in Blender (agent finding 8)
    header = dict(line.split(" = ", 1) for line in code.split("__result__")[0].splitlines())
    assert {k: ast.literal_eval(x) for k, x in header.items()} == {
        "a": "x",
        "version": None,
        "on": True,
    }
    for _ in range(3):
        with pytest.raises(ApiError) as e:
            blender_client.call("anything")
        assert e.value.status == 502 and e.value.code == "blender_error"
    srv.close()


def test_not_connected_and_timeout(monkeypatch):
    srv, _ = fake_blender(monkeypatch, lambda req: None)  # never answers
    monkeypatch.setattr(blender_client, "TIMEOUT", 0.3)
    with pytest.raises(ApiError) as e:
        blender_client.call("get_scene_info")
    assert e.value.code == "blender_timeout"
    srv.close()
    with pytest.raises(ApiError) as e:
        blender_client.call("get_scene_info")
    assert e.value.status == 409 and e.value.code == "blender_not_connected"


def test_pull_creates_a_blender_version(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    asset = library.create_asset("crate")["id"]
    up = library.import_file(asset, "m.glb", glb_path())

    def handler(req):
        code = req["params"].get("code", "")
        if (
            "inbox" in code
        ):  # PULL: pretend Blender wrote the files, into the version folder it was given
            inbox = tmp_path / asset / "002_blender"
            (inbox / "scene.blend").write_bytes(b"blend")
            (inbox / "edited.glb").write_bytes(b"glb2")
            return {
                "status": "ok",
                "result": {"result": {"versions": [up["version"]], "open": None}},
            }
        return {"status": "error", "message": "no screenshot in tests"}

    srv, _ = fake_blender(monkeypatch, handler)
    meta = blender_client.pull(asset)
    assert meta["version"] == "002_blender" and meta["parent"] == up["version"]
    assert meta["provenance"] == "blender_pull" and meta["outputs"] == {
        "mesh": "edited.glb",
        "blend": "scene.blend",
    }
    assert (tmp_path / asset / "002_blender" / "edited.glb").read_bytes() == b"glb2"
    srv.close()


def test_pull_by_version_and_close(tmp_path, monkeypatch):
    """The owner's agent (2026-10-11), two versions of one asset open: pull takes the one asked for (the
    snippet gets `version`), the note names what else was open, and close removes a version's collection."""
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    asset = library.create_asset("crate")["id"]
    up = library.import_file(asset, "m.glb", glb_path())
    sent = []

    def handler(req):
        code = req["params"].get("code", "")
        sent.append(code)
        if "inbox" in code:
            inbox = tmp_path / asset / "002_blender"
            (inbox / "scene.blend").write_bytes(b"blend")
            (inbox / "edited.glb").write_bytes(b"glb2")
            open_all = [f"EFFIGY_{asset}_v001", f"EFFIGY_{asset}_v003"]
            return {
                "status": "ok",
                "result": {
                    "result": {
                        "versions": [up["version"]],
                        "open": open_all[0],
                        "open_all": open_all,
                    }
                },
            }
        if "closed" in code:
            return {"status": "ok", "result": {"result": [f"EFFIGY_{asset}_v003"]}}
        return {"status": "error", "message": "no screenshot in tests"}

    srv, _ = fake_blender(monkeypatch, handler)
    meta = blender_client.pull(asset, version=up["version"])
    assert f"version = {up['version']!r}" in sent[0]  # the snippet's choice
    assert meta["note"] == f"pulled EFFIGY_{asset}_v001; also open in Blender: EFFIGY_{asset}_v003"
    assert blender_client.close_version(asset, "003_x") == [f"EFFIGY_{asset}_v003"]
    assert "version = '003_x'" in sent[-1] and "bpy.data.collections.remove" in sent[-1]
    srv.close()


def test_pull_keeps_the_lineage_of_replaced_and_mixed_sources(tmp_path, monkeypatch):
    """BE-003: objects without a tag (the user's own, in an opened version's collection) take that version as
    the parent; objects from two versions give one parent and the union of the licences and restrictions."""
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    asset = library.create_asset("crate")["id"]
    library.import_file(asset, "m.glb", glb_path())  # 001_upload
    library.write_meta(
        library.new_version(asset, "hunyuan"), None, {}, {"licenses": ["restricted-source"]}
    )
    library.write_meta(
        library.new_version(asset, "clean"), None, {}, {"licenses": ["independent-source"]}
    )
    answers = iter(
        [
            {"versions": [], "open": f"EFFIGY_{asset}_v002"},
            {"versions": ["002_hunyuan", "003_clean"], "open": None},
        ]
    )

    def handler(req):
        code = req["params"].get("code", "")
        if "inbox" in code:
            inbox = Path(ast.literal_eval(re.search(r"^inbox = (.*)$", code, re.MULTILINE)[1]))
            (inbox / "scene.blend").write_bytes(b"blend")
            (inbox / "edited.glb").write_bytes(b"glb")
            return {"status": "ok", "result": {"result": next(answers)}}
        return {"status": "error", "message": "no screenshot in tests"}

    srv, _ = fake_blender(monkeypatch, handler)
    replaced = blender_client.pull(asset)
    assert replaced["parent"] == "002_hunyuan" and replaced["restricted"] is True
    assert replaced["licenses"] == ["restricted-source"]
    mixed = blender_client.pull(asset)
    assert mixed["parent"] == "003_clean" and mixed["restricted"] is True
    assert mixed["licenses"] == ["independent-source", "restricted-source"]
    srv.close()


def test_free_blender_mcp_addon(monkeypatch):
    """The free add-on (port 9876): raw JSON both ways, execute_code returns the snippet's stdout."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    monkeypatch.setattr(blender_client, "PRO", _closed_port())
    monkeypatch.setattr(blender_client, "FREE", srv.getsockname())
    seen = []

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                data = conn.recv(65536)
                # link()'s connect, or the probe for Lab's add-on: no answer
                if data in (b"", b"\0"):
                    continue
                req = json.loads(data)
                seen.append(req)
                out = io.StringIO()
                if req["type"] == "execute_code":
                    with contextlib.redirect_stdout(out):
                        exec(req["params"]["code"], {})  # noqa: S102 - plays the add-on
                reply = {
                    "status": "success",
                    "result": {"executed": True, "result": out.getvalue()},
                }
                body = json.dumps(reply).encode()
                conn.sendall(body[:10])  # arrives in pieces
                conn.sendall(body[10:])

    threading.Thread(target=serve, daemon=True).start()
    assert blender_client.link() == "free"
    assert blender_client.run_python("print('noise')\n__result__ = {'n': n}", n=3) == {"n": 3}
    assert seen[-1]["type"] == "execute_code"
    srv.close()


def test_blender_lab_mcp_addon(monkeypatch):
    """Blender's own MCP add-on (Blender Lab, also on 9876; its mcp_to_blender_server.py): one request per
    connection, JSON + a null byte both ways, `stdout` in the reply; an empty request is answered at once
    (an error), which tells it from blender-mcp. The screenshot is a Python snippet there."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    monkeypatch.setattr(blender_client, "PRO", _closed_port())
    monkeypatch.setattr(blender_client, "FREE", srv.getsockname())
    seen = []

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                buf = b""
                while b"\0" not in buf and (chunk := conn.recv(65536)):
                    buf += chunk
                if not buf:  # a connect without a request
                    continue
                data = buf.split(b"\0", 1)[0]
                if not data:
                    reply = {"status": "error", "message": "JSONDecodeError"}
                else:
                    req = json.loads(data)
                    seen.append(req)
                    out = io.StringIO()
                    try:
                        with contextlib.redirect_stdout(out):
                            exec(req["code"], {"result": {}})  # noqa: S102 - plays the add-on
                        reply = {"status": "ok", "result": {}, "stdout": out.getvalue()}
                    except Exception as e:  # noqa: BLE001 - the add-on sends any traceback back
                        reply = {"status": "error", "message": repr(e)}
                body = json.dumps(reply).encode() + b"\0"
                conn.sendall(body[:10])  # arrives in pieces
                conn.sendall(body[10:])

    threading.Thread(target=serve, daemon=True).start()
    assert blender_client.link() == "lab"
    assert blender_client.run_python("print('noise')\n__result__ = {'n': n}", n=3) == {"n": 3}
    assert seen[-1]["type"] == "execute" and seen[-1]["strict_json"] is False
    # bpy is not here: the snippet's error comes back as Blender's
    with pytest.raises(ApiError) as e:
        blender_client.call("get_viewport_screenshot", filepath="C:/t.png")
    assert e.value.code == "blender_error" and "screenshot_area" in seen[-1]["code"]
    assert "filepath = 'C:/t.png'" in seen[-1]["code"]
    srv.close()


def test_pull_with_nothing_exported_leaves_no_version(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    asset = library.create_asset("crate")["id"]
    library.import_file(asset, "m.glb", glb_path())
    # Blender answers ok but writes no files (an export can come back CANCELLED without an exception)
    srv, _ = fake_blender(
        monkeypatch,
        lambda req: {"status": "ok", "result": {"result": {"versions": [], "open": None}}},
    )
    with pytest.raises(ApiError, match="wrote no edited.glb, scene.blend"):
        blender_client.pull(asset)
    assert sorted(p.name for p in (tmp_path / asset).iterdir() if p.is_dir()) == ["001_upload"]
    srv.close()


def test_open_starts_blender_when_setup_says_so(tmp_path, monkeypatch):
    """The owner's agent, 2026-10-13 (finding 4): Open in Blender with no Blender running failed, and the agent's own
    start raced the owner's (two Blenders, one port). With Setup's "Start Blender when it is needed" on, a Blender is
    started with its add-on's server, once however many Opens ask at the same time, and they go on when it answers;
    off, the refusal says where to turn it on."""
    from fastapi.testclient import TestClient

    from server import settings
    from server.app import app

    monkeypatch.setattr(settings, "FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "_cache", None)
    monkeypatch.setattr(blender_client, "_started", [None])
    monkeypatch.setattr(blender_client, "PRO", _closed_port())
    monkeypatch.setattr(blender_client, "FREE", _closed_port())
    with pytest.raises(ApiError, match="Start Blender when it is needed") as e:
        blender_client.ensure()  # off: nothing started
    assert e.value.code == "blender_not_connected"
    compile(blender_client.START, "start", "exec")  # what the started Blender runs

    exe = tmp_path / "Blender 5.1" / "blender.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    launcher = exe.with_name("blender-launcher.exe")  # exits at once by design
    launcher.write_bytes(b"")
    for bad in (
        (True, ""),
        (False, str(tmp_path / "nothing.exe")),
        (True, str(tmp_path)),
        (True, str(launcher)),
    ):
        with pytest.raises(ApiError) as e:
            blender_client.choose(*bad)
        assert e.value.code == "bad_path", bad
    monkeypatch.setattr(
        blender_client, "run_python", lambda code, **a: "D:/APPS/Blender/blender.exe"
    )
    assert (
        blender_client.setup()["found"] == "D:/APPS/Blender/blender.exe"
    )  # the connected one, a suggestion
    assert blender_client.choose(True, f'"{exe}"') == {
        "start": True,
        "exe": str(exe),
        "found": None,
    }
    with TestClient(app) as c:
        assert c.get("/api/blender/status").json() == {"link": None, "start": True}

    up, started = [], []

    def link():  # Windows takes ~2 s to refuse a closed port: none here
        if not up:
            raise ApiError(409, "blender_not_connected", "none answers")
        return "pro"

    monkeypatch.setattr(blender_client, "link", link)

    class Running:
        returncode = None

        def poll(self):
            return self.returncode

    def popen(args, **kw):  # the add-on's server answers a second later
        started.append(args)
        threading.Timer(1.0, lambda: up.append(1)).start()
        return Running()

    monkeypatch.setattr(blender_client.subprocess, "Popen", popen)
    opens = [
        threading.Thread(target=lambda: started.append(blender_client.ensure())) for _ in range(2)
    ]
    for t in opens:
        t.start()
    for t in opens:
        t.join(30)
    launches = [a for a in started if isinstance(a, list)]
    assert len(launches) == 1 and launches[0][:2] == [str(exe), "--python-expr"]
    assert "bmpro" in launches[0][2] and started.count("pro") == 2  # both Opens went on

    up.clear()  # that Blender closed at once (a broken install): said then, not after the whole wait
    blender_client._started[0].returncode = 1
    monkeypatch.setattr(
        blender_client.subprocess, "Popen", lambda args, **kw: blender_client._started[0]
    )
    t0 = time.monotonic()
    with pytest.raises(ApiError, match="exit code 1") as e:
        blender_client.ensure()
    assert e.value.code == "blender_start_failed" and time.monotonic() - t0 < 5
