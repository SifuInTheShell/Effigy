"""Library folders: one folder per asset in asset.json, set and cleared through PATCH /api/assets/{id}."""

from fastapi.testclient import TestClient

from server.app import app


def test_an_asset_goes_into_a_folder_and_out_again(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    with TestClient(app) as c:
        a = c.post("/api/assets", json={"name": "Crate"}).json()["id"]
        r = c.patch(f"/api/assets/{a}", json={"folder": "  Props  "})
        assert r.status_code == 200 and r.json()["folder"] == "Props", r.text
        assert c.get("/api/assets").json()[0]["folder"] == "Props"
        assert c.patch(f"/api/assets/{a}", json={"tags": ["wood"]}).json()["folder"] == "Props"
        assert "folder" not in c.patch(f"/api/assets/{a}", json={"folder": ""}).json()
        assert c.patch(f"/api/assets/{a}", json={"folder": "x" * 65}).status_code == 400
        assert c.patch(f"/api/assets/{a}", json={"folder": "a\nb"}).status_code == 400
        assert c.patch(f"/api/assets/{a}", json={}).status_code == 400
