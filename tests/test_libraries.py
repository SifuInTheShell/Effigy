"""Several library folders, one open at a time."""

import json

import pytest
from fastapi.testclient import TestClient

from server import jobs, library, settings
from server.app import app


@pytest.fixture
def c(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "_cache", None)
    monkeypatch.delenv("EFFIGY_LIBRARY", raising=False)
    monkeypatch.setenv("EFFIGY_MODELS", str(tmp_path / "models"))
    monkeypatch.chdir(tmp_path)  # the default library/ lands here
    with TestClient(app) as client:
        yield client


def _asset(folder, name):
    (folder / name).mkdir(parents=True)
    (folder / name / "asset.json").write_text(json.dumps({"id": name, "name": name, "created": ""}))


def test_switch_between_libraries(c, tmp_path):
    _asset(tmp_path / "library", "knight")
    other = tmp_path / "F" / "lib2"
    _asset(other, "dragon")
    assert [a["id"] for a in c.get("/api/assets").json()] == ["knight"]

    r = c.post("/api/libraries", json={"path": str(other)}).json()
    assert r["path"] == str(other) and r["source"] == "settings"
    assert {f["path"]: f["assets"] for f in r["folders"]} == {str(tmp_path / "library"): 1, str(other): 1}
    assert [a["id"] for a in c.get("/api/assets").json()] == ["dragon"]

    assert c.post("/api/libraries/forget", json={"path": str(other)}).status_code == 409  # open
    c.post("/api/libraries", json={"path": str(tmp_path / "library")})
    assert [a["id"] for a in c.get("/api/assets").json()] == ["knight"]
    r = c.post("/api/libraries/forget", json={"path": str(other)}).json()
    assert [f["path"] for f in r["folders"]] == [str(tmp_path / "library")]
    assert (other / "dragon" / "asset.json").is_file()  # forgetting never deletes


def test_refusals(c, tmp_path, monkeypatch):
    stranger = tmp_path / "docs"
    stranger.mkdir()
    (stranger / "x.txt").write_text("x")
    assert c.post("/api/libraries", json={"path": str(stranger)}).json()["error"]["code"] == "not_empty"
    assert c.post("/api/libraries", json={"path": "relative"}).status_code == 400
    inside = tmp_path / "models" / "lib"
    assert c.post("/api/libraries", json={"path": str(inside)}).status_code == 400

    job = jobs.Job("a", "echo", None, {})
    job.status = "running"
    monkeypatch.setitem(jobs.JOBS, job.id, job)
    assert c.post("/api/libraries", json={"path": str(tmp_path / "new")}).json()["error"]["code"] == "busy"

    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path / "envlib"))
    r = c.get("/api/libraries").json()
    assert r["source"] == "env" and [f["path"] for f in r["folders"]] == [str(tmp_path / "envlib")]
    assert library.root() == tmp_path / "envlib"
