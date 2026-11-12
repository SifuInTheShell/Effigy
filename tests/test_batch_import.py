"""Research 18 batch import: dry run, import, restricted, rerun skips, a changed file re-imports, Parkour's catalogue,
a folder scanned for model files."""

import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import batch
from server.app import app

GLB = Path(__file__).parent / "fixtures" / "two_tris.glb"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setattr(batch, "IMPORT_STEP", "_echo")
    with TestClient(app) as c:
        yield c


def finish(c, batch_id, timeout=30):
    end = time.time() + timeout
    while (b := c.get(f"/api/import/batch/{batch_id}").json())["status"] == "running":
        assert time.time() < end, b
        time.sleep(0.05)
    return b


def test_batch_import(client, tmp_path):
    src = tmp_path / "src"
    (src / "props").mkdir(parents=True)
    shutil.copy(GLB, src / "props" / "crate.glb")
    shutil.copy(GLB, src / "props" / "vent.glb")
    manifest = src / "manifest.json"
    items = [
        {
            "file": "props/crate.glb",
            "name": "Crate",
            "tags": ["wood"],
            "license": "CC0-1.0",
            "rig": True,
        },
        {"file": "props/vent.glb", "category": ["Ventilation", "a,b"], "tags": ["x" * 40]},
        {"file": "props/gone.glb"},
    ]
    doc = {"schema": "effigy-import/1", "defaults": {"library": "kit"}, "items": items}
    manifest.write_text(json.dumps(doc), encoding="utf-8")
    body = {"source": str(manifest)}

    dry = client.post("/api/import/batch", json=body | {"dry_run": True}).json()["items"]
    assert [(i["name"], i["action"]) for i in dry] == [
        ("Crate", "new"),
        ("vent", "new"),
        ("gone", "skip"),
    ]
    assert dry[1]["tags"] == ["Ventilation", "a b", "x" * 32] and dry[1]["restricted"] is True
    assert dry[0]["restricted"] is False
    assert client.get("/api/assets").json() == []  # a dry run creates nothing

    b = finish(client, client.post("/api/import/batch", json=body).json()["batch_id"])
    assert [i["status"] for i in b["items"]] == ["done", "done", "skipped"], b
    assets = {a["name"]: a for a in client.get("/api/assets").json()}
    vent = assets["vent"]
    assert vent["origin"]["library"] == "kit" and len(vent["origin"]["sha256"]) == 64
    assert vent["tags"] == ["Ventilation", "a b", "x" * 32]
    # the third-party file and the version made from it are restricted; the CC0 one is not
    assert [
        m["restricted"] for m in client.get(f"/api/assets/{vent['id']}").json()["versions"]
    ] == [True, True]
    assert assets["Crate"]["restricted"] is False
    # the item that asks for its rig keeps it, the others come in still
    for name, rig in (("Crate", True), ("vent", False)):
        versions = client.get(f"/api/assets/{assets[name]['id']}").json()["versions"]
        assert versions[1]["params"]["rig"] is rig

    again = client.post("/api/import/batch", json=body | {"dry_run": True}).json()["items"]
    assert [i["action"] for i in again] == ["skip", "skip", "skip"]
    client.patch(f"/api/assets/{assets['Crate']['id']}", json={"tags": ["mine", "wood"]})
    (src / "props" / "crate.glb").write_bytes(GLB.read_bytes() + b"\0\0\0\0")
    b = finish(client, client.post("/api/import/batch", json=body).json()["batch_id"])
    assert [i["status"] for i in b["items"]] == ["done", "skipped", "skipped"], b
    crate = client.get(f"/api/assets/{assets['Crate']['id']}").json()
    assert len(crate["versions"]) == 4  # a new upload and import in the same asset
    assert crate["asset"]["tags"] == ["mine", "wood"]  # an update keeps the tags given since
    assert len(client.get("/api/assets").json()) == 2

    bad = client.post("/api/import/batch", json={"source": "manifest.json"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_path"


def test_parkour_catalogue_reads_as_is(tmp_path):
    lib = {
        "schema": "parkour-asset-library/1",
        "assets": [
            {
                "id": "airvent_01",
                "source": "P_Ventilation.AirVent_01.S_AirVent_01",
                "category": ["P_Ventilation", "AirVent_01"],
                "tags": ["air", "vent", "airvent_01"],
                "file": "assets/airvent_01.glb",
            }
        ],
    }
    (tmp_path / "library.json").write_text(json.dumps(lib), encoding="utf-8")
    [it] = batch.read(str(tmp_path / "library.json"))
    assert it["name"] == "airvent_01" and it["library"] == "parkour"
    # airvent_01 once, ignoring case
    assert it["tags"] == ["P_Ventilation", "AirVent_01", "air", "vent"]
    assert it["source_key"] == "P_Ventilation.AirVent_01.S_AirVent_01"
    assert it["restricted"] is True and it["file"] == str(
        (tmp_path / "assets/airvent_01.glb").resolve()
    )


def test_a_folder_is_scanned_for_model_files(tmp_path):
    kit = tmp_path / "Kit"
    for rel in ("crate.glb", "props/Vent/vent.fbx", "props/a.gltf", "notes.txt", ".hidden/x.glb"):
        (kit / rel).parent.mkdir(parents=True, exist_ok=True)
        (kit / rel).write_bytes(b"x")
    items = batch.read(str(kit), license="own")
    assert [(i["name"], i["tags"], i["source_key"]) for i in items] == [
        ("crate", [], "crate.glb"),
        ("vent", ["props", "Vent"], "props/Vent/vent.fbx"),
    ]
    assert {i["library"] for i in items} == {"Kit"} and not any(i["restricted"] for i in items)
    assert all(i["restricted"] for i in batch.read(str(kit)))  # no licence: restricted


def test_pick_returns_the_path_or_none(monkeypatch):
    """The dialog's process stood in by a script: what it prints is the path, nothing is a cancel."""
    monkeypatch.setattr(
        batch, "PICK", "import sys; print('C:/kits/props' if sys.argv[1] == 'folder' else '')"
    )
    assert batch.pick("folder") == str(Path("C:/kits/props"))
    assert batch.pick("file") is None
    monkeypatch.setattr(batch, "PICK", "raise SystemExit('no display')")
    with pytest.raises(batch.ApiError) as e:
        batch.pick("folder")
    assert e.value.code == "no_dialog"


def test_a_batch_is_refused_before_anything_is_made_without_the_import_tools(
    client, tmp_path, monkeypatch
):
    (tmp_path / "kit").mkdir()
    shutil.copy(GLB, tmp_path / "kit" / "crate.glb")
    monkeypatch.setattr(batch, "IMPORT_STEP", "import_model")
    monkeypatch.setattr(batch.jobs, "venv_python", lambda venv: tmp_path / "none" / "python.exe")
    r = client.post("/api/import/batch", json={"source": str(tmp_path / "kit")})
    assert r.status_code == 409 and r.json()["error"]["code"] == "missing_env", r.text
    assert client.get("/api/assets").json() == []
    dry = client.post("/api/import/batch", json={"source": str(tmp_path / "kit"), "dry_run": True})
    assert dry.status_code == 200  # a check still works
