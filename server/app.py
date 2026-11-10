"""FastAPI backend (IMPLEMENTATION.md §3.5)."""

import asyncio
import base64
import json
import queue
import urllib.parse
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import yaml
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from server import (
    agent,
    batch,
    blender_client,
    hf,
    install,
    jobs,
    library,
    look,
    machine,
    models,
    settings,
    storage,
    textures,
    thumbs,
)
from server.library import ApiError
from server.presets import PRESETS
from tools.download_models import SIZES

ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def lifespan(app):
    yield
    jobs.shutdown()


app = FastAPI(title="Effigy", lifespan=lifespan)
app.add_middleware(agent.LocalOnly)


@app.exception_handler(ApiError)
def api_error(request: Request, e: ApiError):
    return JSONResponse({"error": {"code": e.code, "message": str(e)}}, status_code=e.status)


@app.exception_handler(RequestValidationError)
def bad_body(request: Request, e: RequestValidationError):
    # where and what, never the value: a body may carry a Hugging Face token (M35c)
    msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
    return JSONResponse({"error": {"code": "bad_request", "message": msg}}, status_code=400)


class NewAsset(BaseModel):
    name: str
    brief: dict | None = None
    kind: str | None = None  # "2d" (research 15); absent = a 3D asset


class RunStep(BaseModel):
    parent: str | None = None
    params: dict = {}
    dedupe: bool = (
        False  # the MCP bridge: an identical queued or running job is returned, not doubled
    )


class ExtractTexture(BaseModel):
    material: int = 0
    name: str = ""


@app.get("/api/assets/{asset_id}/versions/{version}/textures")
def texture_inventory(asset_id: str, version: str):
    return textures.inspect(asset_id, version)


@app.get("/api/assets/{asset_id}/versions/{version}/textures/image")
def texture_image(asset_id: str, version: str, material: int = 0, role: str = "basecolor"):
    return Response(
        textures.image(asset_id, version, material, role),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@app.get("/api/assets/{asset_id}/versions/{version}/textures/uv")
def texture_uv(asset_id: str, version: str, material: int = 0):
    return textures.uv(asset_id, version, material)


@app.post("/api/assets/{asset_id}/versions/{version}/textures/extract", status_code=201)
def extract_texture(asset_id: str, version: str, body: ExtractTexture):
    return textures.extract(asset_id, version, body.material, body.name)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/assets")
def list_assets():
    return library.list_assets()


@app.post("/api/assets")
def create_asset(body: NewAsset):
    return library.create_asset(body.name, body.brief, body.kind)


@app.get("/api/assets/{asset_id}")
def get_asset(asset_id: str):
    return library.get_asset(asset_id)


def _idle(asset_id: str, versions: set[str] | None = None) -> None:
    """409 while a job works on this asset (on these versions, if given): it would write into a folder
    that's gone, or recreate one."""
    for j in jobs.JOBS.values():
        if j.asset_id != asset_id:
            continue
        if j.status in ("queued", "running") and (versions is None or j.parent in versions):
            raise ApiError(
                409, "busy", f"{j.step} is still running on {asset_id}: wait or cancel it first"
            )


class AssetPatch(BaseModel):
    name: str | None = None
    archived: bool | None = None
    tags: list[str] | None = None


@app.patch("/api/assets/{asset_id}")
def update_asset(asset_id: str, body: AssetPatch):
    if body.name is None and body.archived is None and body.tags is None:
        raise ApiError(400, "bad_params", "nothing to change: name, archived or tags")
    return library.update_asset(asset_id, body.name, body.archived, body.tags)


class ExportItem(BaseModel):
    asset_id: str
    version: str


class ExportCopy(BaseModel):
    folder: str
    items: list[ExportItem]


@app.post("/api/export/copy")
def export_copy(body: ExportCopy):
    return library.export_copy(body.folder, [(i.asset_id, i.version) for i in body.items])


class VersionPatch(BaseModel):
    starred: bool | None = None
    label: str | None = None


@app.patch("/api/assets/{asset_id}/versions/{version}")
def update_version(asset_id: str, version: str, body: VersionPatch):
    if body.starred is None and body.label is None:
        raise ApiError(400, "bad_params", "nothing to change: starred or label")
    return library.update_version(asset_id, version, body.starred, body.label)


@app.get("/api/trash")
def trash():
    """What deleting moved into the trash, newest first (UX-14)."""
    return library.trash()


@app.post("/api/trash/{name}/restore")
def restore(name: str):
    """A deleted asset or branch back where it was."""
    r = library.restore(name)
    for v in r["versions"]:
        meta = library._read(library.version_dir(r["asset_id"], v) / "meta.json")
        jobs.publish("version_created", {"asset_id": r["asset_id"], "meta": meta})
    return r


@app.delete("/api/assets/{asset_id}")
def delete_asset(asset_id: str):
    _idle(asset_id)
    library.delete_asset(asset_id)
    jobs.publish("asset_deleted", {"asset_id": asset_id})
    return {"ok": True}


@app.delete("/api/assets/{asset_id}/versions/{version}")
def delete_version(asset_id: str, version: str):
    """The version and every version derived from it (a branch of the history tree)."""
    _idle(asset_id, set(library.subtree(asset_id, version)))
    gone = library.delete_versions(asset_id, version)
    jobs.publish("versions_deleted", {"asset_id": asset_id, "versions": gone})
    return {"deleted": gone}


@app.post("/api/assets/{asset_id}/steps/{step}", status_code=202)
def run_step(asset_id: str, step: str, body: RunStep):
    job, new = jobs.submit(asset_id, step, body.parent, body.params, body.dedupe)
    return {
        "job_id": job.id,
        "deduped": not new,
    }  # a deduped job is someone else's: not the caller's to cancel


MAX_UPLOAD = 2**30  # 1 GB: a few minutes of 4K phone video


@app.put("/api/assets/{asset_id}/upload/{name}")
async def upload(asset_id: str, name: str, request: Request):
    """Raw body (no multipart dependency), streamed to `inbox/<uuid>.part` → a root `NNN_upload` version."""
    inbox = library.asset_dir(asset_id) / "inbox"
    too_large = ApiError(413, "too_large", f"uploads up to {MAX_UPLOAD // 2**20} MB")
    if int(request.headers.get("content-length") or 0) > MAX_UPLOAD:
        raise too_large
    inbox.mkdir(exist_ok=True)
    part = inbox / f"{uuid.uuid4().hex}.part"
    try:
        size = 0
        # a chunk at a time, the body never in memory; anyio comes with FastAPI (Starlette is built on it)
        async with await anyio.open_file(part, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise too_large
                await f.write(chunk)
        return await run_in_threadpool(library.import_file, asset_id, name, part)
    finally:
        part.unlink(missing_ok=True)


class Batch(BaseModel):
    source: str  # the absolute path of a manifest (effigy-import/1, parkour-asset-library/1) or a folder
    only: list[str] | None = None
    limit: int | None = None
    license: str = ""  # the licence of items that name none (a folder's: all of them)
    dry_run: bool = False


@app.post("/api/import/batch")
def import_batch(body: Batch):
    """Research 18: the model files of a manifest or a folder into assets, one import_model at a time; `dry_run` only
    lists them."""
    args = body.source, body.only, body.limit, body.license
    return {"items": batch.plan(*args)} if body.dry_run else batch.start(*args)


class Pick(BaseModel):
    kind: str  # folder | file


@app.post("/api/import/pick")
def import_pick(body: Pick):
    """The system's folder or file dialog on this computer (the browser can't give a path): {"path": str | null}."""
    return {"path": batch.pick(body.kind)}


@app.get("/api/import/batch/{batch_id}")
def import_batch_status(batch_id: str):
    return batch.public(batch.get(batch_id))


@app.post("/api/import/batch/{batch_id}/cancel")
def import_batch_cancel(batch_id: str):
    batch.cancel(batch_id)
    return {"ok": True}


class Version(BaseModel):
    version: str


class OpenVersion(BaseModel):
    version: str | None = (
        None  # a Blender pull or close: one of the open versions, else the last opened / all
    )


@app.post("/api/assets/{asset_id}/blender/open")
def blender_open(asset_id: str, body: Version):
    version, objects = blender_client.open_version(asset_id, body.version)
    return {"ok": True, "version": version, "objects": objects}


@app.get("/api/blender/status")
def blender_status():
    """Which Blender add-on answers: "pro", "free" or null (the UI's connection line); `start`: Open in Blender starts
    one when none answers (Setup)."""
    s = settings.read()
    start = bool(s.get("blender_start") and Path(s.get("blender_exe") or "").is_file())
    try:
        return {"link": blender_client.link(), "start": start}
    except ApiError:
        return {"link": None, "start": start}


@app.post("/api/assets/{asset_id}/blender/pull")
def blender_pull(asset_id: str, body: OpenVersion | None = None):
    meta = blender_client.pull(asset_id, version=body.version if body else None)
    jobs.publish("version_created", {"asset_id": asset_id, "meta": meta})
    return {"version": meta["version"]}


@app.post("/api/assets/{asset_id}/blender/close")
def blender_close(asset_id: str, body: OpenVersion | None = None):
    """The asset's open versions out of the Blender scene (one, or all): the names removed."""
    return {"closed": blender_client.close_version(asset_id, body.version if body else None)}


class FitCheck(BaseModel):
    joints: dict[str, list[float]] = {}  # the editor's unsaved moves: name → [x, y, z]


@app.post("/api/assets/{asset_id}/versions/{version}/fit-check")
def fit_check(asset_id: str, version: str, body: FitCheck):
    """How well a rig version's skeleton sits in its mesh, with the editor's unsaved moves on top
    (steps/_rig.fit_quality): the skeleton editor's Check fit."""
    import math

    from steps._mesh import read_glb, weld
    from steps._rig import fit_quality

    d = library.version_dir(asset_id, version)
    outputs = json.loads((d / "meta.json").read_text(encoding="utf-8")).get("outputs", {})
    if "skeleton" not in outputs or "mesh" not in outputs:
        raise ApiError(404, "not_found", f"{version} has no skeleton")
    if any(len(p) != 3 or not all(math.isfinite(x) for x in p) for p in body.joints.values()):
        raise ApiError(400, "bad_params", "joints: name → [x, y, z], finite numbers")
    skel = json.loads((d / outputs["skeleton"]).read_text(encoding="utf-8"))
    joints = [j | {"position": body.joints.get(j["name"], j["position"])} for j in skel["joints"]]
    v, f = weld(*read_glb(d / outputs["mesh"])[:2])
    return fit_quality(v, f, joints)


@app.get("/api/clips")
def clips(template: str = "mixamo52"):
    """The bundled animation clips (M21): names the anim step accepts, by pack file; a creature
    template's own clips with ?template=fox (M36)."""
    from steps._templates import HUMAN, TEMPLATES
    from steps.anim import catalogue

    if template != HUMAN and template not in TEMPLATES:
        raise ApiError(404, "not_found", f"no skeleton template {template!r}")
    return catalogue(template)


@app.get("/api/steps")
def steps():
    """Runnable steps (steps/*.py, test steps excluded) with their named presets."""
    names = sorted(p.stem for p in (ROOT / "steps").glob("[a-z]*.py"))
    return {name: PRESETS.get(name, {}) for name in names}


class Install(BaseModel):
    group: str
    repair: bool = False  # also test the environment and the files' sizes and hashes (M35b)


@app.get("/api/models")
def list_models():
    return models.listing()


@app.post("/api/models/install", status_code=202)
def install_models(body: Install):
    return {"job_id": install.install(body.group, body.repair).id}


@app.get("/api/environments")
def environments():
    return install.environments()


# M35c: the first run (machine check, models folder, presets) and the Hugging Face login
class Folder(BaseModel):
    path: str


class Token(BaseModel):
    token: (
        str  # straight to huggingface_hub's token store (server/hf.py); never logged or kept here
    )


@app.get("/api/setup/machine")
def machine_check():
    return machine.check()


@app.get("/api/setup/folder")
def models_folder():
    return machine.folder()


@app.post("/api/setup/folder")
def choose_models_folder(body: Folder):
    return machine.choose_folder(body.path)


class Port(BaseModel):
    port: int


@app.get("/api/setup/port")
def app_port(request: Request):
    """The port this app runs on and the one it starts on next."""
    return machine.port_info(request.scope["server"][1])


@app.post("/api/setup/port")
def choose_app_port(body: Port, request: Request):
    """Save the port the app starts on next (a restart applies it)."""
    return machine.choose_port(body.port, request.scope["server"][1])


class BlenderStart(BaseModel):
    start: bool
    exe: str = ""


@app.get("/api/setup/blender")
def blender_setup():
    """Setup's Blender card: start the user's Blender when it is needed, and which one."""
    return blender_client.setup()


@app.post("/api/setup/blender")
def choose_blender(body: BlenderStart):
    return blender_client.choose(body.start, body.exe)


@app.get("/api/setup/first-run")
def first_run():
    return {"first_run": machine.first_run()}


@app.post("/api/setup/welcomed")
def welcomed():
    settings.write(welcomed=True)
    return {"ok": True}


@app.get("/api/setup/presets")
def setup_presets():
    return install.presets()


@app.get("/api/hf")
def hf_status():
    return hf.status()


@app.post("/api/hf/login")
def hf_login(body: Token):
    return hf.login(body.token)


@app.post("/api/hf/logout")
def hf_logout():
    return hf.logout()


@app.delete("/api/models/{model_id}")
def remove_model(model_id: int):
    models.remove(model_id)
    return {"ok": True}


@app.get("/api/models/sizes")
def model_sizes():
    """What each model group puts on disk (M35a), so a card can say what an install downloads."""
    return SIZES


@app.get("/api/storage")
def get_storage(refresh: bool = False):
    """Where the disk space goes (M35a): items, assets, drives; a scan is reused for 5 minutes."""
    return storage.storage(refresh)


@app.get("/api/storage/drives")
def get_drives():
    """Free space only, quick: the top bar's low-space warning."""
    return storage.drives()


class Cleanup(BaseModel):
    action: str


@app.post("/api/storage/cleanup")
def cleanup(body: Cleanup):
    """One cleanup action from a storage item (trash, archived, prune, group:<g>, venv:<name>)."""
    return storage.cleanup(body.action)


@app.get("/api/licenses")
def licenses():
    return yaml.safe_load((ROOT / "licenses.yaml").read_text(encoding="utf-8"))


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return jobs.get(job_id).public()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    jobs.cancel(job_id)
    return {"ok": True}


@app.get("/events")
async def events():
    q = jobs.subscribe()

    async def stream():
        try:
            while True:
                try:
                    event, data = q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(
                        0.1
                    )  # ponytail: 100 ms poll; asyncio queues if latency matters
                    continue
                yield {"event": event, "data": json.dumps(data)}
        finally:
            jobs.unsubscribe(q)

    return EventSourceResponse(stream(), ping=15)


# a 1×1 transparent PNG: what a thumbnail route answers when there is nothing to show (a brief, a check, no
# Blender to render a mesh), so an <img> in the library or the history shows nothing instead of a broken
# picture, and the browser logs no 404 (the smoke tests count console errors; CI has no Blender)
BLANK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _thumb(path):
    if path is None:
        return Response(BLANK_PNG, media_type="image/png", headers={"Cache-Control": "no-cache"})
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@app.get("/api/assets/{asset_id}/thumb")
def asset_thumb(asset_id: str):
    """The library grid's picture: changes as versions are added, so revalidated every time."""
    library.asset_dir(
        asset_id
    )  # 404 for an unknown asset, a blank for one with nothing to show yet
    return _thumb(thumbs.asset_thumb(asset_id))


@app.get("/api/assets/{asset_id}/versions/{version}/thumb")
def version_thumb(asset_id: str, version: str):
    library.version_dir(asset_id, version)
    if not (t := thumbs.version_thumb(asset_id, version)):
        return _thumb(None)
    return FileResponse(
        t, headers={"Cache-Control": "no-cache"}
    )  # revalidated (304): fixes reach the browser


@app.get("/api/assets/{asset_id}/versions/{version}/turntable")
def version_turntable(asset_id: str, version: str):
    return FileResponse(look.turntable(asset_id, version), media_type="image/webp")


@app.get("/api/assets/{asset_id}/turntable")
def asset_turntable(asset_id: str):
    """The card's version turning (M42); changes as versions are added, so revalidated every time."""
    try:
        v = thumbs.asset_version(asset_id)
        m = next((m for m in library.versions(asset_id) if m["version"] == v), None)
        # only the version the still shows, and only if it has a model itself (no ancestor's)
        ok = m and m.get("step") not in ("validate", "export") and thumbs._glbs(asset_id, m)
        t = look.turntable(asset_id, v) if ok else None
    except ApiError as e:
        if e.code != "nothing_to_show":
            raise
        t = None
    if not t:
        raise ApiError(404, "not_found", f"{asset_id!r} has no model to turn")
    return FileResponse(t, media_type="image/webp", headers={"Cache-Control": "no-cache"})


@app.get("/api/assets/{asset_id}/versions/{version}/look")
def version_look(asset_id: str, version: str, clip: str = "", size: int = 768):
    """What a version looks like (M40): a sheet of the model, four frames of a clip, or its pictures."""
    f = look.look(asset_id, version, clip, size)
    where = urllib.parse.quote(
        str(f.resolve())
    )  # a header is latin-1: a non-latin path percent-encoded
    return FileResponse(
        f, headers={"Cache-Control": "no-cache", "X-Effigy-File": where}
    )  # the bridge names it


@app.get("/api/agent/setup")
def agent_setup():
    return agent.setup()


@app.post("/api/agent/test")
def agent_test():
    return agent.test()


# a user's file is never a page of this app: an HTML or SVG companion in an imported zip, opened at its
# /files URL, would otherwise run scripts in the API's own origin (audit 2026-10-10, BE-001). `sandbox` makes
# a document inert (no scripts, no origin); subresources (images, GLBs, videos the UI loads) are untouched
INERT = {"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"}


@app.get("/files/{asset_id}/{path:path}")
def files(asset_id: str, path: str):
    """Anything inside one asset folder, served inert (INERT); FileResponse handles Range, no gzip."""
    base = library.asset_dir(asset_id).resolve()
    f = (base / path).resolve()
    if not f.is_relative_to(base) or not f.is_file():
        raise ApiError(404, "not_found", f"no file {path!r}")
    return FileResponse(f, headers=INERT)


# Mounted last so /api routes win. index.html must not be cached (it names the hashed bundles).
if (ROOT / "ui" / "dist").is_dir():

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(
            ROOT / "ui" / "dist" / "index.html", headers={"Cache-Control": "no-cache"}
        )

    app.mount("/", StaticFiles(directory=ROOT / "ui" / "dist"), name="ui")
