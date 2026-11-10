"""Library on disk (IMPLEMENTATION.md §3.2). The only module that creates version folders and meta.json."""

import json
import os
import re
import shutil
import tempfile
import threading
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

ASSET_ID = re.compile(r"^[a-z0-9_]{1,40}$")
VERSION = re.compile(r"^(\d{3})_([a-z0-9_]+)$")
RESTRICTED_STEPS = {"hunyuan"}  # experimental engines with output restrictions (MX1)


class ApiError(Exception):
    """Becomes {"error": {"code", "message"}} with `status` (§3.5)."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def root() -> Path:
    return Path(os.environ.get("EFFIGY_LIBRARY", "library"))


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


# one allocation and one metadata change at a time in this process (the backend is the only writer): two
# steps allocating at once took the same number, two patches at once lost one (audit 2026-10-10, BE-002, BE-004)
_alloc = threading.Lock()
_meta = threading.RLock()


def _write(p: Path, data: dict) -> None:
    tmp = p.with_name(
        f"{p.name}.{threading.get_ident()}.tmp"
    )  # a writer's own file, never another's
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)  # readers never see half a file


def asset_dir(asset_id: str) -> Path:
    """Path of an existing asset; 404 if the id is malformed or unknown (ids come from URLs)."""
    if not ASSET_ID.match(asset_id) or not (root() / asset_id / "asset.json").is_file():
        raise ApiError(404, "not_found", f"no asset {asset_id!r}")
    return root() / asset_id


def version_dir(asset_id: str, version: str) -> Path:
    """Path of a finished version (has meta.json); 404 otherwise."""
    d = asset_dir(asset_id) / version
    if not VERSION.match(version) or not (d / "meta.json").is_file():
        raise ApiError(404, "not_found", f"no version {version!r} in {asset_id!r}")
    return d


KINDS = {"2d", "texture"}  # standalone material sets share the version store; absent = a 3D asset


def create_asset(name: str, brief: dict | None = None, kind: str | None = None) -> dict:
    if kind is not None and kind not in KINDS:
        raise ApiError(400, "bad_params", f"kind: one of {sorted(KINDS)} or none, not {kind!r}")
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:36] or "asset"
    root().mkdir(parents=True, exist_ok=True)
    asset_id, n = base, 2
    with _alloc:  # a restore from the trash checks and takes an id under the same lock
        while True:
            try:
                (root() / asset_id).mkdir()
                break
            except FileExistsError:
                asset_id, n = f"{base}_{n}", n + 1
    asset = {"id": asset_id, "name": name, "created": _now(), "brief": brief or {}}
    if kind:
        asset["kind"] = kind
    _write(root() / asset_id / "asset.json", asset)
    return asset


def list_assets() -> list[dict]:
    """Every asset with `versions` (how many) and `updated` (the newest version's time, else created):
    what the library grid sorts and filters on (M28)."""
    if not root().is_dir():
        return []
    out = []
    for p in sorted(root().glob("*/asset.json")):
        a = _read(p)
        metas = [_read(m) for m in p.parent.glob("[0-9][0-9][0-9]_*/meta.json")]
        a |= {
            "versions": len(metas),
            "updated": max((m.get("created", "") for m in metas), default=a.get("created", "")),
            "licenses": sorted({lic for m in metas for lic in m.get("licenses", [])}),
            "restricted": any(m.get("restricted") for m in metas),
            "imported": any(
                m.get("step") == "import_model"
                or (m.get("step") == "upload" and "mesh" in m.get("outputs", {}))
                for m in metas
            ),
        }
        out.append(a)
    return out


def versions(asset_id: str) -> list[dict]:
    d = asset_dir(asset_id)
    return [_read(p) for p in sorted(d.glob("[0-9][0-9][0-9]_*/meta.json"))]


def get_asset(asset_id: str) -> dict:
    return {"asset": _read(asset_dir(asset_id) / "asset.json"), "versions": versions(asset_id)}


def _trash(src: Path, name: str) -> None:
    """Deleting moves into library/_trash/ (no asset.json at its top: listings skip it): a misclick is
    undone in the app (`trash`, `restore`); Setup's storage list empties it for good."""
    t = root() / "_trash"
    t.mkdir(exist_ok=True)
    shutil.move(src, t / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{name}")


def delete_asset(asset_id: str) -> None:
    _trash(asset_dir(asset_id), asset_id)


TRASHED = re.compile(
    r"^(\d{8}T\d{6})_([a-z0-9_]+)$"
)  # <when>_<asset> or <when>_<asset>_<first version>


def trash() -> list[dict]:
    """What deleting moved into library/_trash, newest first (UX review 2026-10-12, UX-14: an in-app way back):
    a whole asset ({kind: asset, asset_id, asset_name, versions: count}) or a branch of versions ({kind: versions,
    asset_id, asset_name, versions: [names], first: the branch's first meta}); anything else there is not listed."""
    t = root() / "_trash"
    out: list[dict] = []
    for d in sorted(t.iterdir(), reverse=True) if t.is_dir() else []:
        m = TRASHED.match(d.name)
        if not d.is_dir() or not m:
            continue
        when = datetime.strptime(m[1], "%Y%m%dT%H%M%S").replace(tzinfo=UTC).isoformat()
        if (d / "asset.json").is_file():
            a = _read(d / "asset.json")
            if a.get("id") != m[2]:  # not <when>_<its id>: e.g. storage's half-deleted <id>_deleted
                continue
            n = sum(1 for v in d.iterdir() if VERSION.match(v.name))
            row = {
                "kind": "asset",
                "asset_id": a.get("id", m[2]),
                "asset_name": a.get("name", m[2]),
                "versions": n,
            }
        else:
            vs = sorted(
                v.name for v in d.iterdir() if VERSION.match(v.name) and (v / "meta.json").is_file()
            )
            first = next((v for v in vs if m[2].endswith("_" + v)), None)
            if not first:
                continue
            asset_id = m[2][: -len(first) - 1]
            here = root() / asset_id / "asset.json"
            name = _read(here).get("name", asset_id) if here.is_file() else asset_id
            row = {
                "kind": "versions",
                "asset_id": asset_id,
                "asset_name": name,
                "versions": vs,
                "first": _read(d / first / "meta.json"),
            }
        out.append({"name": d.name, "deleted": when} | row)
    return out


def restore(name: str) -> dict:
    """A trash entry back where it was; refused when something has taken its place since (an asset of that id, a
    version of that number) or what it hangs from is gone (the asset, the branch's parent)."""
    entry = next((e for e in trash() if e["name"] == name), None)
    if entry is None:
        raise ApiError(404, "not_found", f"nothing called {name!r} in the trash")
    src = root() / "_trash" / name
    dst = root() / entry["asset_id"]
    if not ASSET_ID.match(entry["asset_id"]):
        raise ApiError(400, "bad_params", f"{entry['asset_id']!r} is not an asset id")
    if entry["kind"] == "asset":
        with _alloc:  # create_asset takes ids under it: nothing claims this one between the check and the move
            if dst.exists():
                raise ApiError(
                    409,
                    "exists",
                    f"an asset {entry['asset_id']!r} is there again: rename or delete it first",
                )
            try:
                os.rename(src, dst)  # whole or not at all (both under the library root)
            except OSError as e:
                raise ApiError(
                    409, "busy", f"{entry['asset_name']} is in use (a file is open): {e}"
                ) from e
        return {"asset_id": entry["asset_id"], "versions": []}
    if not (dst / "asset.json").is_file():
        raise ApiError(
            409, "asset_gone", f"{entry['asset_name']} is deleted too: restore the asset first"
        )
    with _alloc:  # no new version takes one of these numbers meanwhile
        if taken := [v for v in entry["versions"] if (dst / v).exists()]:
            raise ApiError(
                409, "exists", f"{', '.join(taken)} is there again in {entry['asset_name']}"
            )
        parent = entry["first"].get("parent")
        if parent and not (dst / parent).is_dir():
            raise ApiError(409, "parent_gone", f"{parent}, which it was made from, is gone")
        moved: list[str] = []
        try:
            for v in entry["versions"]:
                os.rename(src / v, dst / v)  # each whole or not at all
                moved.append(v)
        except OSError as e:  # a file open elsewhere: the branch goes back to the trash, all of it
            for w in reversed(moved):
                os.rename(dst / w, src / w)
            raise ApiError(
                409, "busy", f"{entry['asset_name']} is in use (a file is open): {e}"
            ) from e
        try:
            src.rmdir()  # only when empty: anything else left there stays in the trash
        except OSError:
            pass
    return {"asset_id": entry["asset_id"], "versions": entry["versions"]}


def _tags(tags: list[str]) -> list[str]:
    out: dict[str, str] = {}
    for t in tags:
        t = t.strip() if isinstance(t, str) else ""
        if not 1 <= len(t) <= 32 or "," in t:
            raise ApiError(400, "bad_params", f"a tag is 1-32 characters without a comma: {t!r}")
        out.setdefault(t.casefold(), t)
    if len(out) > 20:
        raise ApiError(400, "bad_params", "at most 20 tags")
    return list(out.values())


def update_asset(
    asset_id: str,
    name: str | None = None,
    archived: bool | None = None,
    tags: list[str] | None = None,
    origin: dict | None = None,
    folder: str | None = None,
) -> dict:
    """Rename, archive (M28: hidden from the grid's default view, nothing else), tag (M42: replaces the list),
    record where it came from (`origin`, a batch import: research 18) and/or file it in a library folder (one per asset,
    "" takes it out)."""
    if name is not None and not name.strip():
        raise ApiError(400, "bad_params", "a name can't be empty")
    if folder is not None:
        folder = folder.strip()
        if len(folder) > 64 or not folder.isprintable():
            raise ApiError(
                400, "bad_params", f"a folder name is up to 64 printable characters: {folder!r}"
            )
    new_tags = None if tags is None else _tags(tags)
    path = asset_dir(asset_id) / "asset.json"
    with _meta:  # read, change and write as one: a patch beside another kept both (BE-004)
        asset = _read(path)
        if new_tags is not None:
            asset["tags"] = new_tags
        if name is not None:
            asset["name"] = name.strip()
        if archived is not None:
            asset["archived"] = bool(archived)
        if origin is not None:
            asset["origin"] = origin
        if folder:
            asset["folder"] = folder
        elif folder is not None:
            asset.pop("folder", None)
        _write(path, asset)
    return asset


def export_copy(folder: str, items: list[tuple[str, str]]) -> dict:
    """Each export version's files into a new `<folder>/<asset>_<profile>/` (M42); never overwrites,
    restricted versions are not copied out, a failed copy leaves no folder."""
    dest = Path(folder)
    if not dest.is_absolute() or not dest.is_dir():
        raise ApiError(400, "bad_path", f"{folder!r} is not an existing absolute folder")
    if (r := root().resolve()) == dest.resolve() or r in dest.resolve().parents:
        raise ApiError(400, "bad_path", "export outside the library, not into it")
    plan = []
    for asset_id, version in items:
        meta = _read(version_dir(asset_id, version) / "meta.json")
        if meta.get("step") != "export":
            raise ApiError(400, "bad_params", f"{asset_id}/{version} is not an export version")
        plan.append((asset_id, version, meta))
    copied, skipped = [], []
    for asset_id, version, meta in plan:
        profile = (meta.get("params") or {}).get("profile") or (meta.get("metrics") or {}).get(
            "profile"
        )
        row = {"asset_id": asset_id, "version": version}
        if not profile:
            skipped.append(row | {"reason": "no_profile"})
            continue
        target = dest / f"{asset_id}_{profile}"
        if meta.get("restricted"):
            skipped.append(row | {"reason": "restricted"})
        elif target.exists():
            skipped.append(row | {"reason": "exists"})
        else:
            src = asset_dir(asset_id) / version / str(profile)
            tmp = Path(tempfile.mkdtemp(dir=dest, prefix="."))  # ours alone: safe to remove
            try:
                shutil.copytree(src, tmp / "x")
                (tmp / "x").rename(target)
            except OSError as e:
                skipped.append(row | {"reason": f"failed: {e}"})
                continue
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            copied.append(
                row | {"dir": str(target), "files": sorted(p.name for p in target.iterdir())}
            )
    return {"copied": copied, "skipped": skipped}


def update_version(
    asset_id: str, version: str, starred: bool | None = None, label: str | None = None
) -> dict:
    """A star (keep this one) and a label of the user's own on a version (M28); meta.json keeps them."""
    path = version_dir(asset_id, version) / "meta.json"
    with _meta:
        meta = _read(path)
        if starred is not None:
            meta["starred"] = bool(starred)
        if label is not None:
            meta["label"] = label.strip()[:80]
        _write(path, meta)
    return meta


def subtree(asset_id: str, version: str) -> list[str]:
    """`version` and everything derived from it (children, grandchildren...), oldest first."""
    version_dir(asset_id, version)
    metas = versions(asset_id)
    keep = {version}
    for m in metas:  # numbered in creation order: parents come before their children
        if m.get("parent") in keep:
            keep.add(m["version"])
    return [m["version"] for m in metas if m["version"] in keep]


def delete_versions(asset_id: str, version: str) -> list[str]:
    """Remove a version and its whole branch; returns what was removed."""
    gone = subtree(asset_id, version)
    d = asset_dir(asset_id)
    t = root() / "_trash" / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{asset_id}_{version}"
    t.mkdir(parents=True)
    for v in gone:
        shutil.move(d / v, t / v)
    return gone


def new_version(asset_id: str, step: str) -> Path:
    """Create the next version folder: max(NNN)+1, independent of the parent (branching is free).
    Owner decision 2026-10-07: every number counts (001, 002, …), 999 versions per asset; assets numbered
    in tens before that simply continue (…, 520, 521)."""
    d = asset_dir(asset_id)
    with (
        _alloc
    ):  # the scan and the mkdir as one: a job and an upload at once took one number (BE-002)
        while True:
            nums = [int(m[1]) for p in d.iterdir() if (m := VERSION.match(p.name))]
            n = max(nums, default=0) + 1
            if (
                n > 999
            ):  # NNN_step (§3.2) ends at 999: a 1000_ folder is one nothing here can read, and the
                # allocation would try it again for ever (code review 2026-10-05)
                raise ApiError(
                    409,
                    "full",
                    f"{asset_id} has used up its version numbers (999): delete branches in History or "
                    "start a new asset",
                )
            out = d / f"{n:03d}_{step}"
            try:
                out.mkdir()
                return out
            except (
                FileExistsError
            ):  # a folder of that name from outside this process: the next number
                continue


def chain(asset_id: str, version: str | None) -> list[dict]:
    """meta.json of `version` and all its ancestors, nearest first."""
    out = []
    while version:
        meta = _read(version_dir(asset_id, version) / "meta.json")
        out.append(meta)
        version = meta["parent"]
    return out


def write_meta(
    out: Path, parent: str | None, params: dict, result: dict, provenance: str = "step"
) -> dict:
    """meta.json for a finished version folder from the step's RESULT (§3.1). A `restricted` flag in the
    result marks the version restricted beside its parent chain (a pull with a second, restricted source)."""
    asset_id, step = out.parent.name, VERSION.match(out.name)[2]
    ancestors = chain(asset_id, parent)
    meta = {
        "schema": 1,
        "asset_id": asset_id,
        "version": out.name,
        "step": step,
        "parent": parent,
        "created": _now(),
        "params": params,
        "outputs": result.get("outputs", {}),
        "metrics": result.get("metrics", {}),
        "models": result.get("models", []),
        "licenses": sorted(
            {*result.get("licenses", []), *(lic for m in ancestors for lic in m["licenses"])}
        ),
        "provenance": provenance,
        "restricted": step in RESTRICTED_STEPS
        or bool(result.get("restricted"))
        or any(m["restricted"] for m in ancestors),
        "note": result.get("note", ""),
    }
    if "frame" in meta["metrics"]:
        meta["frame"] = meta["metrics"]["frame"]
    _write(out / "meta.json", meta)
    if step == "brief":  # §3.2: the brief is also the asset's
        with _meta:
            asset = _read(out.parent / "asset.json")
            asset["brief"] = yaml.safe_load((out / "brief.yaml").read_text(encoding="utf-8"))
            _write(out.parent / "asset.json", asset)
    return meta


UPLOAD_KINDS = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".tif": "image",
    ".tiff": "image",
    ".tga": "image",
    ".glb": "mesh",
    ".mp4": "video",  # M34 motion from video
    ".mov": "video",
    ".webm": "video",
}


MODEL_EXTS = (
    ".glb",
    ".gltf",
    ".fbx",
    ".usdz",
    ".usdc",
    ".usd",
    ".usda",
    ".obj",
    ".ply",
    ".stl",
)  # zip pick order
ZIP_MEMBERS = 2000
ZIP_BYTES = 2 * 2**30


def _bad_zip(msg: str) -> ApiError:
    return ApiError(400, "bad_zip", msg)


def _zip_plan(zf: zipfile.ZipFile) -> tuple[list[tuple[zipfile.ZipInfo, str]], str]:
    """Check the whole archive before anything is written: ([(member, relative path)], the model's path)."""
    infos = zf.infolist()
    if len(infos) > ZIP_MEMBERS:
        raise _bad_zip(f"the zip has {len(infos)} members, at most {ZIP_MEMBERS}")
    keep = []
    for i in infos:
        path = i.filename.replace("\\", "/")
        parts = [p for p in path.split("/") if p not in ("", ".")]  # "./model.obj" is model.obj
        if len(path) > 200:
            raise _bad_zip(f"{path[:60]}...: path longer than 200 characters")
        if path.startswith("/") or ":" in path or ".." in parts:
            raise _bad_zip(f"{path}: absolute or parent path")
        if i.is_dir() or "__MACOSX" in parts or any(p.startswith(".") for p in parts):
            continue
        if i.flag_bits & 1:
            raise _bad_zip(f"{path}: encrypted")
        if path.lower().endswith(".zip"):
            raise _bad_zip(f"{path}: a zip inside the zip")
        keep.append((i, "/".join(parts)))
    if sum(i.file_size for i, _ in keep) > ZIP_BYTES:
        raise _bad_zip(f"the zip unpacks to more than {ZIP_BYTES // 2**20} MB")
    models = [(i, p) for i, p in keep if Path(p).suffix.lower() in MODEL_EXTS]
    if not models:
        raise ApiError(400, "no_model", f"no model file ({', '.join(MODEL_EXTS)}) in the zip")
    best = min(
        models,
        key=lambda m: (
            MODEL_EXTS.index(Path(m[1]).suffix.lower()),
            m[1].count("/"),
            -m[0].file_size,
        ),
    )
    return keep, best[1]


def _unzip(zf: zipfile.ZipFile, keep: list, dest: Path) -> None:
    left = ZIP_BYTES
    for info, path in keep:
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as f:
            while chunk := src.read(1 << 20):
                left -= len(chunk)
                if left < 0:  # file_size can lie: count what is really written
                    raise _bad_zip(f"{path}: the zip unpacks to more than {ZIP_BYTES // 2**20} MB")
                f.write(chunk)


def import_file(asset_id: str, name: str, path: Path, restricted: bool = False) -> dict:
    """A user's file on disk (moved away) becomes a root version `NNN_upload`: `image.<ext>`, `mesh.glb`,
    `video.<ext>`, or (M39) a model under `source/` (a .zip is unpacked there). `restricted`: third-party material
    (a batch import's, research 18), so it and everything made from it is restricted."""
    ext = Path(name).suffix.lower()
    if ext not in UPLOAD_KINDS and ext not in MODEL_EXTS and ext not in (".zip", ".bvh"):
        raise ApiError(
            400,
            "bad_request",
            "upload an image, .glb/.gltf/.fbx/.obj/.stl/.ply/.usd/.usda/.usdc/.usdz, a .zip, "
            f".mp4/.mov/.webm or a .bvh animation, not {ext or name!r}",
        )
    zf = None
    if ext == ".zip":
        try:
            zf = zipfile.ZipFile(path)
            keep, model = _zip_plan(zf)
        except zipfile.BadZipFile:
            raise _bad_zip(f"{name} is not a zip file") from None
        except BaseException:
            if zf:
                zf.close()
            raise
    out = new_version(asset_id, "upload")
    try:
        if zf:
            try:
                _unzip(zf, keep, out / "source")
            except (OSError, zipfile.BadZipFile, RuntimeError) as e:
                raise _bad_zip(f"cannot unpack the zip: {e}") from e
            outputs = {"model": f"source/{model}"}
        elif ext in UPLOAD_KINDS:
            outputs = {UPLOAD_KINDS[ext]: f"{UPLOAD_KINDS[ext]}{ext}"}
            shutil.move(path, out / outputs[UPLOAD_KINDS[ext]])
        else:
            safe = re.sub(r"[^A-Za-z0-9._ -]", "_", name)
            safe = (
                safe[: 100 - len(ext)] + safe[len(safe) - len(ext) :] if len(safe) > 100 else safe
            )
            (out / "source").mkdir()
            shutil.move(path, out / "source" / safe)
            outputs = {"model": f"source/{safe}"}
        result = {"outputs": outputs, "restricted": restricted}
        return write_meta(out, None, {"name": name}, result, provenance="upload")
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    finally:
        if zf:
            zf.close()
