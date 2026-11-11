"""Live link to the user's running Blender through an MCP add-on's socket (§3.6, M8).

Blender MCP Pro (paid, 127.0.0.1:9877): 4-byte big-endian length + UTF-8 JSON {"command", "params"}; reply
{"status", "result"|"message"}. On 127.0.0.1:9876 either Blender's official MCP add-on (Blender Lab, GPL,
projects.blender.org/lab/blender_mcp, its mcp/blmcp/tools_helpers/connection.py): one request per connection,
UTF-8 JSON {"type": "execute", "code", "strict_json"} + a null byte; reply {"status": "ok"|"error", "result",
"stdout"|"message"} + a null byte; or the free blender-mcp add-on (MIT, ahujasid): raw JSON {"type", "params"};
reply {"status": "success"|"error", "result"|"message"}; `execute_code` returns the snippet's stdout. Both take
stdout back, which run_python reads. Pro is tried first. Open and Pull are one Python snippet each (collection +
tags + undo step / backup + export).
"""

import json
import shutil
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

from server import library, settings
from server.library import ApiError

PRO, FREE = ("127.0.0.1", 9877), ("127.0.0.1", 9876)
TIMEOUT = 125.0  # Pro gives up on a command after 120 s
MARK = (
    "\x1eeffigy:"  # run_python prints its result after this (the free add-on returns stdout only)
)


def _recv(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        if not (chunk := sock.recv(n - len(buf))):
            raise ApiError(502, "blender_error", "Blender closed the connection")
        buf += chunk
    return buf


def _recv_json(sock: socket.socket):
    buf = b""
    while True:
        if not (chunk := sock.recv(65536)):
            raise ApiError(502, "blender_error", "Blender closed the connection")
        buf += chunk
        try:
            return json.loads(buf)
        except ValueError:
            continue  # not all of it yet


_last = [PRO]  # the add-on that answered last: Windows takes ~2 s to refuse a closed port
_kind: dict = {}  # FREE's add-on, "lab" or "free", until its port closes
PROBE = 3.0  # Lab's idle poll is 1 s


def _probe(addr: tuple) -> str:
    """Which add-on listens on 9876: Lab's answers a lone null byte (an empty request) with an error reply;
    blender-mcp waits for a whole JSON object and says nothing."""
    with socket.create_connection(addr, timeout=5) as s:
        s.settimeout(PROBE)
        s.sendall(b"\0")
        try:
            return "lab" if s.recv(1) else "free"
        except TimeoutError:
            return "free"


def _connect() -> tuple[str, socket.socket]:
    for addr in sorted((PRO, FREE), key=lambda a: a != _last[0]):
        try:
            if addr == FREE and addr not in _kind:
                _kind[addr] = _probe(addr)
            sock = socket.create_connection(addr, timeout=5)
        except OSError:
            _kind.pop(addr, None)  # another add-on may answer there next time
            continue
        _last[0] = addr
        return ("pro" if addr == PRO else _kind[addr]), sock
    raise ApiError(
        409,
        "blender_not_connected",
        "start the add-on's server in Blender (Blender's own MCP add-on: Preferences → Add-ons → MCP → Start; "
        "Blender MCP Pro or blender-mcp: the N panel), or turn on Setup's Start Blender when it is needed",
    )


def link() -> str:
    """ "pro", "lab" or "free": which add-on answers (409 if none)."""
    kind, sock = _connect()
    sock.close()
    return kind


WAIT = 90  # seconds for a started Blender's add-on server to answer (the agent's 5.1 took ~6 s)
# run by the started Blender once it is up: the add-on's server (Pro's, else Blender Lab's, else blender-mcp's), retried
# each second for a minute while the add-ons load
START = """
import bpy
def _effigy_start(tries=[60]):
    for addon, op in (("bmpro", "start_server"), ("blmcp", "server_start"), ("blendermcp", "start_server")):
        try:
            getattr(getattr(bpy.ops, addon), op)()
            return None
        except Exception:
            pass
    tries[0] -= 1
    return 1.0 if tries[0] > 0 else None
bpy.app.timers.register(_effigy_start, first_interval=2.0)
"""
_starting = threading.Lock()
_started: list = [None]  # the Blender this app started (Popen), while it runs


def ensure() -> str:
    """The add-on that answers; when none does and Setup's "Start Blender when it is needed" is on, the user's Blender
    started with its add-on's server, and waited for (the owner's agent, 2026-10-13, finding 4: Open in Blender
    failed with no Blender running, and the agent's own start raced the owner's: two Blenders, one port). One Blender
    at a time: while the one it started runs, a second Open, or an agent's, waits for it instead of starting another."""
    try:
        return link()
    except ApiError:
        s = settings.read()
        exe = Path(s.get("blender_exe") or "")
        if not s.get("blender_start") or not exe.is_file():
            raise
    with _starting:
        if _started[0] is None or _started[0].poll() is not None:
            try:  # the user's own Blender as they use it: no --factory-startup, its add-ons are the point
                _started[0] = subprocess.Popen(
                    [str(exe), "--python-expr", START],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    # its own: it stays open when the app's window closes
                    creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
            except OSError as e:
                raise ApiError(502, "blender_start_failed", f"could not start {exe}: {e}") from e
        proc = _started[0]
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        try:
            return link()
        except ApiError:
            if proc.poll() is not None:
                raise ApiError(
                    502,
                    "blender_start_failed",
                    f"{exe} closed (exit code {proc.returncode}) before its add-on's server answered",
                ) from None
            time.sleep(1)
    raise ApiError(
        504,
        "blender_timeout",
        f"Blender started, but its add-on's server did not answer in {WAIT} s: is Blender's MCP add-on, "
        f"Blender MCP Pro or blender-mcp enabled in {exe}?",
    )


def setup() -> dict:
    """Setup's Blender card: start it when needed, and which Blender; `found` while none is chosen: the connected
    Blender's own path (the one with the add-on)."""
    s = settings.read()
    found = None
    if not s.get("blender_exe"):
        try:
            found = run_python("import bpy\n__result__ = bpy.app.binary_path")
        except ApiError:
            pass
    return {"start": bool(s.get("blender_start")), "exe": s.get("blender_exe"), "found": found}


def choose(start: bool, exe: str) -> dict:
    path = Path(exe.strip().strip('"')) if exe.strip() else None
    # blender.exe itself (Blender on Linux and macOS): blender-launcher.exe exits at once, and ensure watches the process
    if path and not (path.is_file() and path.name.lower() in ("blender.exe", "blender")):
        raise ApiError(400, "bad_path", f"no Blender at {exe}: the path of its blender.exe")
    if start and not path:
        raise ApiError(400, "bad_path", "starting Blender needs the path of its blender.exe")
    settings.write(blender_start=start, blender_exe=str(path) if path else None)
    return setup()


# Lab's add-on runs Python only: the viewport screenshot is a snippet there
LAB_SCREENSHOT = """
import bpy
win = bpy.context.window_manager.windows[0]
area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
with bpy.context.temp_override(window=win, area=area):
    bpy.ops.screen.screenshot_area(filepath=filepath)
"""


def _lab(sock: socket.socket, command: str, params: dict) -> dict:
    """One request to Blender Lab's add-on; the reply as the other add-ons give it (the stdout in `result`)."""
    if command == "execute_python":
        code = params["code"]
    elif command == "get_viewport_screenshot":
        code = f"filepath = {params['filepath']!r}\n{LAB_SCREENSHOT}"
    else:
        raise ApiError(502, "blender_error", f"{command}: not a command of Blender's MCP add-on")
    req = {"type": "execute", "code": code, "strict_json": False}
    sock.sendall(json.dumps(req).encode() + b"\0")
    buf = b""
    while b"\0" not in buf:
        if not (chunk := sock.recv(65536)):
            raise ApiError(502, "blender_error", "Blender closed the connection")
        buf += chunk
    reply = json.loads(buf.split(b"\0", 1)[0])
    return {**reply, "result": {"result": reply.get("stdout", "")}}


def call(command: str, **params):
    kind, sock = _connect()
    with sock:
        sock.settimeout(TIMEOUT)
        try:
            if kind == "pro":
                body = json.dumps({"command": command, "params": params}).encode()
                sock.sendall(struct.pack(">I", len(body)) + body)
                reply = json.loads(_recv(sock, struct.unpack(">I", _recv(sock, 4))[0]))
            elif kind == "lab":
                reply = _lab(sock, command, params)
            else:
                kind = {"execute_python": "execute_code"}.get(command, command)
                sock.sendall(json.dumps({"type": kind, "params": params}).encode())
                reply = _recv_json(sock)
        except TimeoutError as e:
            raise ApiError(504, "blender_timeout", f"{command} took over {TIMEOUT:.0f} s") from e
    res = reply.get("result")
    inner = res if isinstance(res, dict) else {}
    ok = reply.get("status") in ("ok", "success")
    if not ok or inner.get("status") == "error" or inner.get("error"):
        msg = reply.get("message") or inner.get("message") or inner.get("error")
        raise ApiError(502, "blender_error", f"{command}: {msg}")
    return res


def run_python(code: str, **args):
    """Python in Blender with `args` bound as literals; the snippet sets __result__ (JSON-able). Python's literals
    (repr): JSON's `null` for a version left out was a NameError in Blender (the owner's agent, 2026-10-13)."""
    header = "".join(f"{k} = {v!r}\n" for k, v in args.items())
    footer = f"\nimport json as _effigy_json\nprint({MARK!r} + _effigy_json.dumps(__result__))\n"
    out = call("execute_python", code=header + code + footer).get("result")
    if isinstance(out, str) and MARK in out:  # the free add-on: the snippet's stdout
        return json.loads(out.rsplit(MARK, 1)[1])
    return out


OPEN = """
import bpy
if bpy.context.object and bpy.context.object.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
coll = bpy.data.collections.get(collection)
if coll is None:
    coll = bpy.data.collections.new(collection)
if coll.name not in bpy.context.scene.collection.children:
    bpy.context.scene.collection.children.link(coll)
# import unless it's already there: an earlier open whose objects were removed (an undo) left the
# collection behind, empty, and every later open skipped the import
if not coll.all_objects:
    bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection.children[coll.name]
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path, merge_vertices=True)
    for o in set(bpy.data.objects) - before:
        o["effigy_asset_id"], o["effigy_version"] = asset_id, version
        if o.name not in coll.objects:  # the importer links to the active collection; make sure
            coll.objects.link(o)
    bpy.ops.ed.undo_push(message=f"effigy: open {version}")
bpy.context.scene["effigy_open_" + asset_id] = coll.name  # Pull takes this version, not every open one
__result__ = [o.name for o in coll.all_objects]
"""

# The asset's objects: tagged by Open, or anything inside its EFFIGY_<asset>_v<NNN> collections
# (objects the user created there have no tag).
ASSET_OBJECTS = """
import re
def asset_objects(scene_objects):
    pat = re.compile(f"EFFIGY_{re.escape(asset_id)}_v[0-9]{{3}}")
    return [o for o in scene_objects
            if o.get("effigy_asset_id") == asset_id
            or any(pat.fullmatch(c.name) for c in o.users_collection)]
def version_of(o):
    return o.get("effigy_version", "")
"""

PULL = """
import bpy, os
objs = asset_objects(bpy.context.view_layer.objects)
pat = re.compile(f"EFFIGY_{re.escape(asset_id)}_v[0-9]{{3}}")
open_all = sorted({c.name for o in objs for c in o.users_collection if pat.fullmatch(c.name)})
if version:  # the caller says which of the open versions (the owner's agent, 2026-10-11: two were open)
    active = next((c for c in open_all if c[-3:] == version[:3]), None)
    assert active, f"{version} is not open in Blender (open: {', '.join(open_all) or 'nothing'})"
else:  # the version opened last; others are alternatives
    active = bpy.context.scene.get("effigy_open_" + asset_id)
if active:
    objs = [o for o in objs if any(c.name == active for c in o.users_collection)
            or active == f"EFFIGY_{asset_id}_v{version_of(o)[:3]}"]
assert objs, f"no object of {asset_id!r} in the scene: open a version first"
if bpy.context.object and bpy.context.object.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(inbox, "scene.blend"), copy=True)
bpy.ops.object.select_all(action="DESELECT")
for o in objs:
    o.select_set(True)
bpy.ops.export_scene.gltf(filepath=os.path.join(inbox, "edited.glb"), export_format="GLB", use_selection=True)
__result__ = {"versions": sorted({version_of(o) for o in objs if version_of(o)}), "open": active, "open_all": open_all}
"""

# The asset's collections out of the scene, one version's or all: the agent removed an older open version
# by hand before every pull (2026-10-11)
CLOSE = """
import bpy, re
pat = re.compile(f"EFFIGY_{re.escape(asset_id)}_v" + (re.escape(version[:3]) if version else "[0-9]{3}"))
closed = []
for coll in [c for c in bpy.data.collections if pat.fullmatch(c.name)]:
    closed.append(coll.name)
    for o in list(coll.all_objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.data.collections.remove(coll)
if bpy.context.scene.get("effigy_open_" + asset_id) in closed:
    del bpy.context.scene["effigy_open_" + asset_id]
if closed:
    bpy.ops.ed.undo_push(message=f"effigy: close {', '.join(closed)}")
__result__ = closed
"""


def _model(asset_id: str, version: str) -> tuple[str, Path]:
    """(version, its mesh file): `version`'s mesh, or the nearest one up the chain, as the viewer shows
    it (a validate or export version has none of its own). 404 for a missing version, 400 for none."""
    for meta in library.chain(asset_id, version):
        mesh = next((f for f in meta["outputs"].values() if str(f).endswith(".glb")), None)
        if mesh:
            return meta["version"], library.version_dir(asset_id, meta["version"]) / mesh
    raise ApiError(400, "bad_request", f"{version} and its parents have no 3D model to open")


def open_version(asset_id: str, version: str) -> tuple[str, list[str]]:
    """(the version opened, its objects); Blender started first when Setup says so (`ensure`)."""
    version, path = _model(asset_id, version)
    ensure()
    return version, run_python(
        OPEN,
        collection=f"EFFIGY_{asset_id}_v{version[:3]}",
        path=str(path.resolve()),
        asset_id=asset_id,
        version=version,
    )


def close_version(asset_id: str, version: str | None = None) -> list[str]:
    """The asset's collections removed from the scene, `version`'s or all; the names removed."""
    return run_python(CLOSE, asset_id=asset_id, version=version)


def pull(
    asset_id: str,
    step: str = "blender",
    provenance: str = "blender_pull",
    parent: str | None = None,
    files: dict[str, str] | None = None,
    metrics: dict | None = None,
    note: str = "",
    version: str | None = None,
) -> dict:
    """Backup .blend + the asset's objects as GLB → a new `NNN_<step>` version.

    `version`: which open version to take when several are; else the one opened last. parent defaults to
    the newest version tagged on the objects, else to the version whose collection is open (objects the
    user made carry no tag); the other tagged versions' licences and restrictions come along (audit
    2026-10-10, BE-003). `files` (name → text) are added.
    """
    out = library.new_version(asset_id, step)  # Blender exports straight into it: two pulls at once
    outputs = {
        "mesh": "edited.glb",
        "blend": "scene.blend",
    }  # shared one staging folder before, and one
    try:  # saved the other's export (code review 2026-10-05); a failure leaves no folder behind
        res = run_python(
            ASSET_OBJECTS + PULL, asset_id=asset_id, inbox=str(out.resolve()), version=version
        )
        versions = res["versions"]
        if not versions and res.get(
            "open"
        ):  # the user's own objects, in an opened version's collection
            n = res["open"][-3:]
            versions = [m["version"] for m in library.versions(asset_id) if m["version"][:3] == n][
                :1
            ]
        if missing := [f for f in outputs.values() if not (out / f).is_file()]:
            # an export can come back CANCELLED without an exception
            raise ApiError(502, "blender", f"Blender wrote no {', '.join(missing)}")
        parent = parent or (versions[-1] if versions else None)
        # one parent (the tree); every other source's lineage rides along as licences and restriction
        others = [m for v in versions if v != parent for m in library.chain(asset_id, v)]
        for name, text in (files or {}).items():
            (out / name).write_text(text, encoding="utf-8")
            outputs[name.split(".")[0]] = name
        try:
            call("get_viewport_screenshot", filepath=str((out / "thumb.png").resolve()))
            outputs["thumb"] = "thumb.png"
        except ApiError:
            pass  # a thumbnail is nice to have; the pulled mesh is what matters
        if len(versions) > 1 and not note:
            note = f"pulled objects from {', '.join(versions)}"
        if (
            len(res.get("open_all", [])) > 1 and not note
        ):  # which one it took, and what else was open
            still = [c for c in res["open_all"] if c != res.get("open")]
            note = f"pulled {res.get('open')}; also open in Blender: {', '.join(still)}"
        result = {
            "outputs": outputs,
            "metrics": metrics or {},
            "note": note,
            "licenses": sorted({lic for m in others for lic in m["licenses"]}),
            "restricted": any(m["restricted"] for m in others),
        }
        return library.write_meta(out, parent, {}, result, provenance=provenance)
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise
