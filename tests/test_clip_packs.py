"""Clip packs as data (docs/clip_packs_spec.md): models/clips/<pack>/pack.json + GLBs; their clips in the catalogue
and the anim step, their move sets in /api/clip-packs, their licence and restriction on what is made from them."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import library, settings
from server.app import app
from steps import anim, export
from steps._common import StepError
from tests.test_motion import _glb


def _pack(root: Path, pack: str, glbs: dict, manifest: dict | None = None) -> None:
    d = root / pack
    d.mkdir(parents=True)
    for name, clips in glbs.items():
        (d / name).write_bytes(_glb(clips))
    if manifest is not None:
        (d / "pack.json").write_text(json.dumps(manifest), encoding="utf-8")


REF = {
    "schema": "effigy-clip-pack/1",
    "title": "Reference",
    "license": "",
    "credit": "Animations: a game, personal reference use only",
    "move_sets": [
        {"id": "cop", "title": "Patrol cop", "clips": ["Cop · Idle", "Cop · Walk", "Missing"]}
    ],
}


@pytest.fixture
def root(tmp_path, monkeypatch):
    root = tmp_path / "clips"
    _pack(root, "mesh2motion", {"human-base-animations.glb": ["Idle_A", "Walk", "Jog"]})
    _pack(
        root, "ref", {"cop.glb": ["Cop · Idle", "Cop · Walk", "Walk"]}, REF
    )  # "Walk": mesh2motion's
    _pack(root, "junk", {"x.glb": ["X"]})  # no pack.json
    _pack(root, "own", {"mine.glb": ["Mine · Wave"]}, REF | {"license": "own", "move_sets": []})
    monkeypatch.setattr(anim, "CLIPS", root)
    return root


def test_the_catalogue_reads_every_pack(root):
    names = {c["name"]: c for c in anim.catalogue()}
    assert names["Walk"] == {
        "name": "Walk",
        "file": "mesh2motion/human-base-animations.glb",
        "pack": "mesh2motion",
    }
    assert names["Cop · Walk"]["file"] == "ref/cop.glb" and "X" not in names
    by = {p["id"]: p for p in anim.packs()}
    assert [p["id"] for p in anim.packs()] == [
        "mesh2motion",
        "junk",
        "own",
        "ref",
    ]  # the built-in first
    assert by["junk"]["problems"] == ["no pack.json"] and by["junk"]["clips"] == []
    assert any("'Walk'" in p for p in by["ref"]["problems"])  # the duplicate, skipped
    assert by["ref"]["move_sets"] == [
        {"id": "cop", "title": "Patrol cop", "clips": ["Cop · Idle", "Cop · Walk"]}
    ]
    assert any("Missing" in p for p in by["ref"]["problems"])
    assert (
        by["ref"]["restricted"]
        and not by["own"]["restricted"]
        and not by["mesh2motion"]["restricted"]
    )
    # "restricted": false never lifts an unknown licence
    (root / "ref" / "pack.json").write_text(
        json.dumps(REF | {"restricted": False}), encoding="utf-8"
    )
    assert next(p for p in anim.packs() if p["id"] == "ref")["restricted"]


def _run(tmp_path, monkeypatch, clips):
    skin = tmp_path / "asset" / "020_rig_skin"
    skin.mkdir(parents=True, exist_ok=True)
    (skin / "rigged.glb").write_bytes(b"glb")
    seen = {}

    def fake_blender(op, inputs, outdir, params, timeout=None):
        seen["inputs"] = [Path(i) for i in inputs]
        Path(outdir).mkdir(parents=True, exist_ok=True)
        (Path(outdir) / "rigged_anim.glb").write_bytes(b"glb")
        return {"report": {"clips": [{"name": c, "seconds": 1.0} for c in params["clips"]]}}

    monkeypatch.setattr(anim, "run_blender", fake_blender)
    monkeypatch.setattr(anim._anim, "turns", lambda p: None)
    out = tmp_path / "asset" / f"0{len(clips)}1_anim"
    out.mkdir(parents=True, exist_ok=True)
    res = anim.run("run", str(skin), str(out), {"clips": clips, "clearance": False})
    return res, seen, out


def test_a_restricted_pack_restricts_the_animation_and_credits_itself(root, tmp_path, monkeypatch):
    res, seen, out = _run(tmp_path, monkeypatch, ["Walk", "Cop · Walk"])
    assert seen["inputs"][1:] == [
        root / "mesh2motion" / "human-base-animations.glb",
        root / "ref" / "cop.glb",
    ]
    assert res["restricted"] is True
    assert res["licenses"] == ["Mesh2Motion/mesh2motion-app", "clip-pack/ref"]
    [row] = res["metrics"]["clip_packs"]
    assert (
        row["name"] == "clip-pack/ref"
        and row["commercial"] is False
        and "reference use" in row["attribution"]
    )

    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    (tmp_path / "asset" / "asset.json").write_text(json.dumps({"id": "asset", "name": "asset"}))
    library.write_meta(
        tmp_path / "asset" / "020_rig_skin", None, {}, {"outputs": {"rigged": "rigged.glb"}}
    )
    meta = library.write_meta(out, "020_rig_skin", {}, res)
    assert meta["restricted"] is True
    # export: refused without the flag; with it, the pack's row comes from the version itself
    with pytest.raises(StepError) as e:
        export.run("run", str(out), str(tmp_path / "x"), {"profile": "unity"})
    assert e.value.code == "restricted"
    text, used = export.credits(
        meta["licenses"], [{"name": "Mesh2Motion/mesh2motion-app"}] + export.pack_rows(out)
    )
    assert "reference use only" in text and [u["name"] for u in used] == [
        "Mesh2Motion/mesh2motion-app",
        "clip-pack/ref",
    ]


def test_mesh2motion_alone_is_unchanged(root, tmp_path, monkeypatch):
    res, _, _ = _run(tmp_path, monkeypatch, ["Idle_A", "Walk"])
    assert res["licenses"] == ["Mesh2Motion/mesh2motion-app"]
    assert res["models"] == [{"id": "Mesh2Motion/mesh2motion-app"}]
    assert "restricted" not in res and "clip_packs" not in res["metrics"]
    clean, _, _ = _run(tmp_path, monkeypatch, ["Mine · Wave"])
    assert clean["licenses"] == ["clip-pack/own"] and "restricted" not in clean


def test_the_api(root, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "_cache", None)
    monkeypatch.setenv("EFFIGY_MODELS", str(root.parent))
    c = TestClient(app)
    clips = c.get("/api/clips").json()
    assert {
        "name": "Jog",
        "file": "mesh2motion/human-base-animations.glb",
        "pack": "mesh2motion",
    } in clips
    packs = {p["id"]: p for p in c.get("/api/clip-packs").json()}
    assert [s["id"] for s in packs["mesh2motion"]["move_sets"]] == ["brawler", "sword", "basic"]
    assert packs["mesh2motion"]["move_sets"][2]["clips"] == ["Idle_A", "Walk", "Jog"]
    assert (
        packs["ref"]["move_sets"][0]["clips"] == ["Cop · Idle", "Cop · Walk"]
        and packs["ref"]["restricted"]
    )
    assert "clips" not in packs["ref"] and packs["junk"]["problems"] == ["no pack.json"]


def test_clean_matches_the_batch_import():
    from server import batch

    assert anim.CLEAN == batch.CLEAN


def test_a_clearance_lifts_the_pack_and_what_was_made_with_it(root, tmp_path, monkeypatch):
    """The owner's clearance (free text, clearance.json) lifts the pack and the open library's versions made with it,
    except where something else restricts them; an empty one puts the restriction back."""
    monkeypatch.setattr(settings, "FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "_cache", None)
    monkeypatch.setenv("EFFIGY_MODELS", str(root.parent))
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path / "lib"))
    hero = library.create_asset("hero")["id"]

    def version(step, parent, result):
        d = library.new_version(hero, step)
        return library.write_meta(d, parent, {}, {"outputs": {}} | result)["version"]

    skin = version("rig_skin", None, {})
    own = anim._credits(
        [p for p in anim.packs() if p["id"] == "ref"]
    )  # what the anim step returns for the pack
    made = version(
        "anim",
        skin,
        {
            "metrics": {"clip_packs": own["clip_packs"]},
            "licenses": own["licenses"],
            "restricted": True,
        },
    )
    edit = version("anim_edit", made, {})
    shipped = version("export", made, {})
    flags = lambda: {m["version"]: m["restricted"] for m in library.versions(hero)}
    assert flags() == {skin: False, made: True, edit: True, shipped: True}

    c = TestClient(app)
    r = c.post(
        "/api/clip-packs/ref/clearance",
        json={"license": "Written permission from the studio, 2026-11-13"},
    ).json()
    assert r["pack"]["restricted"] is False and r["pack"]["license"].startswith(
        "Written permission"
    )
    assert sorted(r["lifted"]) == [f"{hero}/{made}", f"{hero}/{edit}"]
    assert r["kept"] == [
        {
            "version": f"{hero}/{shipped}",
            "reason": "its files were written restricted: export it again",
        }
    ]
    assert flags() == {skin: False, made: False, edit: False, shipped: True}
    [row] = library.versions(hero)[1]["metrics"]["clip_packs"]
    assert row["commercial"] is True and "Written permission" in row["license"]
    assert json.loads((root / "ref" / "clearance.json").read_text())["license"].startswith(
        "Written"
    )
    # a new animation with the pack is clean now
    assert not next(p for p in anim.packs() if p["id"] == "ref")["restricted"]

    r = c.post("/api/clip-packs/ref/clearance", json={"license": ""}).json()
    assert r["pack"]["restricted"] and sorted(r["restricted"]) == [
        f"{hero}/{made}",
        f"{hero}/{edit}",
    ]
    assert (
        all(v for k, v in flags().items() if k != skin)
        and not (root / "ref" / "clearance.json").exists()
    )
    assert c.post("/api/clip-packs/mesh2motion/clearance", json={"license": "x"}).status_code == 404
