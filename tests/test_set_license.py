"""Setting a batch-imported asset's licence: lifts or restores its restriction on every version; a re-import keeps
it; Hunyuan outputs, restricted materials from elsewhere and assets that were not imported are refused."""

import json
import shutil

from server import library
from tests.test_batch_import import GLB, client, finish  # noqa: F401 - the fixture


def test_set_license(client, tmp_path):  # noqa: F811
    src = tmp_path / "src"
    src.mkdir()
    shutil.copy(GLB, src / "hero.glb")
    manifest = src / "manifest.json"
    doc = {"schema": "effigy-import/1", "items": [{"file": "hero.glb"}]}
    manifest.write_text(json.dumps(doc), encoding="utf-8")
    finish(
        client, client.post("/api/import/batch", json={"source": str(manifest)}).json()["batch_id"]
    )
    hero = client.get("/api/assets").json()[0]["id"]
    flags = lambda: [m["restricted"] for m in client.get(f"/api/assets/{hero}").json()["versions"]]
    assert flags() == [True, True]

    plain = library.create_asset("plain")["id"]
    r = client.post("/api/assets/license", json={"ids": [hero, plain], "license": "own"}).json()
    assert r["changed"] == [hero] and r["refused"][0]["id"] == plain
    assert flags() == [False, False]
    origin = client.get(f"/api/assets/{hero}").json()["asset"]["origin"]
    assert origin["license"] == "own" and origin["restricted"] is False and origin["license_set"]

    # a changed file re-imports with the licence the user set (the manifest names none)
    (src / "hero.glb").write_bytes(GLB.read_bytes() + b"\0\0\0\0")
    finish(
        client, client.post("/api/import/batch", json={"source": str(manifest)}).json()["batch_id"]
    )
    assert flags() == [False, False, False, False]

    # an unknown licence again, then the owner's own words for one they got
    client.post("/api/assets/license", json={"ids": [hero], "license": ""})
    assert all(flags())
    terms = "  Bought from the artist, commercial use  "
    r = client.post("/api/assets/license", json={"ids": [hero], "license": terms}).json()
    assert r["changed"] == [hero]
    assert not any(flags())
    assert client.get(f"/api/assets/{hero}").json()["asset"]["origin"]["license"] == terms.strip()
    client.post("/api/assets/license", json={"ids": [hero], "license": " "})  # blank: unknown
    assert all(flags())

    # a Hunyuan version keeps the asset restricted
    v = library.new_version(hero, "hunyuan")
    library.write_meta(v, None, {}, {"outputs": {}})
    r = client.post("/api/assets/license", json={"ids": [hero], "license": "CC0-1.0"}).json()
    assert r["changed"] == [] and "experimental engine" in r["refused"][0]["message"]
    assert all(flags())
