"""Batch import (research 18): model files from a manifest or a folder become assets, one import_model at a time.

The source is a manifest, `effigy-import/1` or Parkour's `parkour-asset-library/1` (read as is), or a folder: every model
file under it, named after the file, tagged with its subfolders. Each asset remembers where it came
from in `asset.json.origin` `{library, source_key, sha256, license, restricted}`: a rerun skips an unchanged file and
imports a changed one as a new version of the same asset, keeping the tags given to it since. An item without a known
clean licence imports restricted (meta.restricted on its upload and everything made from it), so export refuses it
without `allow_restricted`. A model comes in still unless its item says `"rig": true` (a known skeleton is kept).
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from server import blender_install, jobs, library
from server.library import ApiError

SCHEMA, PARKOUR = "effigy-import/1", "parkour-asset-library/1"
# SPDX ids that ship in a commercial game as they are, and "own": the user's own work
CLEAN = {"CC0-1.0", "MIT", "Apache-2.0", "own"}
# a folder scan's model files: a .gltf is left out, its .bin and textures would stay behind (zip its folder)
SCAN = {*library.MODEL_EXTS, ".zip"} - {".gltf"}
IMPORT_STEP = "import_model"  # the tests run steps._echo instead
BATCHES: dict[str, dict] = {}
_one = threading.Lock()  # one batch runs at a time; a second waits for it


def _tags(words: list) -> list[str]:
    """Words fitted to library._tags' rule (1-32 characters, no comma, at most 20): a catalogue's are not."""
    out: dict[str, str] = {}
    for w in words:
        if t := str(w).replace(",", " ").strip()[:32].strip():
            out.setdefault(t.casefold(), t)
    return list(out.values())[:20]


def read(source: str, license: str = "") -> list[dict]:
    """The items of a manifest or a folder: {file (absolute), name, tags, library, source_key, license, restricted,
    rig}.
    `license`: the licence of the items that name none."""
    path = Path(source)
    if not path.is_absolute() or not path.exists():
        raise ApiError(
            400, "bad_path", f"{source!r} is not the absolute path of a manifest or a folder"
        )
    if path.is_dir():
        rows = []
        for f in sorted(path.rglob("*")):
            rel = f.relative_to(path)
            hidden = any(p.startswith(".") for p in rel.parts)
            if f.suffix.lower() in SCAN and f.is_file() and not hidden:
                rows.append({"file": rel.as_posix(), "category": list(rel.parent.parts)})
        return _items(path, {"license": license}, rows)
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ApiError(400, "bad_manifest", f"{path.name} is not JSON: {e}") from None
    schema = m.get("schema") if isinstance(m, dict) else None
    # Parkour's catalogue as is: id → name, category + tags → tags, no licence (so restricted)
    if schema == PARKOUR:
        defaults = {"library": "parkour", "license": license}
        rows = [
            {
                "file": a["file"],
                "name": a["id"],
                "tags": [*a.get("category", []), *a.get("tags", [])],
                "source": a.get("source") or a["id"],
            }
            for a in m.get("assets", [])
        ]
    elif schema == SCHEMA:
        defaults, rows = {"license": license} | (m.get("defaults") or {}), m.get("items") or []
    else:
        raise ApiError(400, "bad_manifest", f"schema: {SCHEMA!r} or {PARKOUR!r}, not {schema!r}")
    return _items(path.parent, defaults, rows)


def _items(base: Path, defaults: dict, rows: list) -> list[dict]:
    """Manifest rows (files relative to `base`) as items."""
    items = []
    for i, row in enumerate(rows):
        row = defaults | row
        if not isinstance(row.get("file"), str) or not row["file"]:
            raise ApiError(400, "bad_manifest", f"item {i}: no file")
        f = (base / row["file"]).resolve()
        if f.suffix.lower() not in library.MODEL_EXTS + (".zip",):
            raise ApiError(400, "bad_manifest", f"item {i}: {row['file']} is not a model file")
        lic = row.get("license") or ""
        items.append(
            {
                "file": str(f),
                "name": row.get("name") or f.stem,
                "tags": _tags([*(row.get("category") or []), *(row.get("tags") or [])]),
                "library": row.get("library") or base.name,
                "source_key": row.get("source") or row["file"],
                "license": lic,
                "restricted": bool(row.get("restricted", lic not in CLEAN)),
                "rig": row.get("rig") is True,
            }
        )
    return items


def _origins() -> dict[tuple, dict]:
    """(library, source_key) → the asset that came from it."""
    out = {}
    for a in library.list_assets():
        if o := a.get("origin"):
            out[(o.get("library"), o.get("source_key"))] = a
    return out


def _sha(f: str) -> str:
    h = hashlib.sha256()
    with open(f, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def plan(
    source: str, only: list[str] | None = None, limit: int | None = None, license: str = ""
) -> list[dict]:
    """The items to import (`only`: names or source keys containing one of these), each with `action`: new, update
    (a changed file of an imported asset) or skip (`reason`: exists, missing)."""
    items = read(source, license)
    if only:
        items = [i for i in items if any(s in i["name"] or s in i["source_key"] for s in only)]
    items = items[:limit] if limit else items
    have = _origins()
    for it in items:
        a = have.get((it["library"], it["source_key"]))
        if not Path(it["file"]).is_file():
            it |= {"action": "skip", "reason": "missing"}
        elif a and a["origin"].get("sha256") == _sha(it["file"]):
            it |= {"action": "skip", "reason": "exists", "asset_id": a["id"]}
        else:
            it |= {"action": "update", "asset_id": a["id"]} if a else {"action": "new"}
    return items


def _import(it: dict, b: dict) -> None:
    """One item: its asset (new or the one it came into before), the upload, its tags, import_model."""
    sha = _sha(it["file"])  # before the copy: a file changing meanwhile re-imports next time
    origin = {k: it[k] for k in ("library", "source_key", "license", "restricted")}
    if asset_id := it.get(
        "asset_id"
    ):  # an update keeps the tags given since, the catalogue's added after them
        asset = library.get_asset(asset_id)["asset"]
        kept = asset.get("tags", [])
        was = asset.get("origin", {})
        # the licence the user set, unless the item names one
        if not it["license"] and was.get("license_set"):
            origin |= {k: was[k] for k in ("license", "restricted", "license_set")}
            it["restricted"] = was["restricted"]
    else:
        asset_id, kept = library.create_asset(it["name"])["id"], []
    it["asset_id"] = asset_id
    tags = _tags([*kept, *it["tags"]])
    library.update_asset(asset_id, tags=tags, origin=origin)  # sha256 only once imported
    inbox = library.asset_dir(asset_id) / "inbox"
    inbox.mkdir(exist_ok=True)
    part = inbox / f"{uuid.uuid4().hex}.part"
    try:
        # import_file moves its file away: a copy, so the source stays where it is
        shutil.copyfile(it["file"], part)
        name = Path(it["file"]).name
        up = library.import_file(asset_id, name, part, restricted=it["restricted"])
    finally:
        part.unlink(missing_ok=True)
    jobs.publish("version_created", {"asset_id": asset_id, "meta": up})
    job, _ = jobs.submit(asset_id, IMPORT_STEP, up["version"], {"rig": it.get("rig", False)})
    it["job_id"] = job.id
    while job.status in ("queued", "running"):
        if b["cancel"]:
            jobs.cancel(job.id)
        time.sleep(0.2)
    if job.status != "done":
        raise ApiError(500, job.status, (job.error or {}).get("message", job.status))
    it["version"] = job.version
    library.update_asset(asset_id, origin=origin | {"sha256": sha})


def _run(b: dict) -> None:
    with _one:
        for it in b["items"]:
            if b["cancel"]:
                it["status"] = "cancelled"
                continue
            if it["action"] == "skip":
                it["status"] = "skipped"
                continue
            it["status"] = "running"
            jobs.publish("batch", public(b))
            try:
                _import(it, b)
                it["status"] = "done"
            except Exception as e:  # noqa: BLE001 - one bad file never stops the rest
                it |= {"status": "failed", "error": str(e)}
        b["status"] = "cancelled" if b["cancel"] else "done"
        jobs.publish("batch", public(b))


def start(
    source: str, only: list[str] | None = None, limit: int | None = None, license: str = ""
) -> dict:
    items = plan(source, only, limit, license)
    ready()
    b = {
        "batch_id": uuid.uuid4().hex[:12],
        "source": source,
        "status": "running",
        "cancel": False,
    }
    b["items"] = [it | {"status": "queued"} for it in items]
    BATCHES[b["batch_id"]] = b
    threading.Thread(target=_run, args=(b,), daemon=True).start()
    return {"batch_id": b["batch_id"], "items": len(items)}


def ready() -> None:
    """409 before anything is created when the import step can't run: a batch would leave every asset with only its
    upload."""
    try:
        jobs._python(IMPORT_STEP)
    except RuntimeError as e:
        raise ApiError(409, "missing_env", f"importing needs the model tools: {e}") from None
    if IMPORT_STEP == "import_model" and not blender_install.blender_exe().is_file():
        raise ApiError(409, "missing_env", "importing needs Blender: run.bat installs it")


def get(batch_id: str) -> dict:
    if batch_id not in BATCHES:
        raise ApiError(404, "not_found", f"no batch {batch_id!r}")
    return BATCHES[batch_id]


def public(b: dict) -> dict:
    counts: dict[str, int] = {}
    for it in b["items"]:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    return {k: b[k] for k in ("batch_id", "source", "status", "items")} | {"counts": counts}


def cancel(batch_id: str) -> None:
    """The running item's job is cancelled, the items after it are not started."""
    get(batch_id)["cancel"] = True


# the system's own folder / file dialog: the browser can't hand the server a path, but the server runs on this
# computer. tkinter is in the standard library (uv's Pythons ship it); its own process keeps Tk off the server's threads
PICK = """import sys, tkinter as tk
from tkinter import filedialog as fd
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
if sys.argv[1] == "folder":
    p = fd.askdirectory(title="Choose a folder of models")
else:
    p = fd.askopenfilename(title="Choose a manifest", filetypes=[("Manifest", "*.json"), ("All files", "*.*")])
print(p or "")"""


def pick(kind: str) -> str | None:
    """A folder or a manifest file the user picks in the system dialog; None when they cancel."""
    if kind not in ("folder", "file"):
        raise ApiError(400, "bad_params", f"kind: folder or file, not {kind!r}")
    try:
        r = subprocess.run(
            [sys.executable, "-c", PICK, kind],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=os.environ | {"PYTHONIOENCODING": "utf-8"},
            timeout=600,
            check=False,  # the return code is read below
        )
    except subprocess.TimeoutExpired:
        return None
    if r.returncode:
        raise ApiError(
            409, "no_dialog", f"no file dialog on this computer: type the path ({r.stderr[-200:]})"
        )
    return str(Path(p)) if (p := r.stdout.strip()) else None


def _mixed(asset_id: str, m: dict) -> str | None:
    """Why version `m` may hold a restricted source other than this asset's own imports: Hunyuan, a material from
    another asset that is restricted, a Blender pull of several versions. None: only this asset's own uploads."""
    if m["step"] in library.RESTRICTED_STEPS:
        return f"{m['version']} came from {m['step']} (experimental engine)"
    p = m.get("params", {})
    if m["step"] == "textures" and p.get("source_asset") not in (None, "", asset_id):
        try:
            other = library._read(
                library.version_dir(p["source_asset"], str(p.get("source_version"))) / "meta.json"
            )
        except ApiError:
            return f"{m['version']} took a material from {p['source_asset']}, which is gone"
        if other.get("restricted"):
            return f"{m['version']} took a material from {p['source_asset']}, which is restricted"
    if m["step"] == "blender_pull" and str(m.get("note", "")).startswith("pulled objects from"):
        return f"{m['version']} pulled objects from several versions in Blender"
    return None


def set_license(asset_id: str, license: str) -> dict:
    """The user states an imported asset's licence: one of CLEAN, or their own words for another (bought, a written
    permission, a custom licence), lifts its restriction from every version; "" (unknown) puts it back on every
    version. Refused for an asset that holds a restricted source of another kind (`_mixed`): Hunyuan outputs stay
    restricted (PLAN §6, IMPLEMENTATION MX1)."""
    license = license.strip()[:300]
    origin = library.get_asset(asset_id)["asset"].get("origin")
    if not origin:
        raise ApiError(
            400, "not_imported", f"{asset_id} was not batch-imported: it has no licence to set"
        )
    restricted = not license
    versions = library.versions(asset_id)
    if not restricted and (
        why := next(filter(None, (_mixed(asset_id, m) for m in versions)), None)
    ):
        raise ApiError(409, "restricted", f"{asset_id} stays restricted: {why}")
    if any(
        j.asset_id == asset_id and j.status in ("queued", "running")
        for j in list(jobs.JOBS.values())
    ):
        raise ApiError(
            409, "busy", f"a job is running on {asset_id}; set the licence after it ends"
        )
    with library._meta:
        for m in versions:
            path = library.version_dir(asset_id, m["version"]) / "meta.json"
            meta = library._read(path)
            if meta.get("restricted") != restricted:
                library._write(path, meta | {"restricted": restricted})
    set_at = library._now()
    return library.update_asset(
        asset_id,
        origin=origin | {"license": license, "restricted": restricted, "license_set": set_at},
    )
