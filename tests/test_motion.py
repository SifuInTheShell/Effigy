"""M26/M30 animate from a text prompt: the SOMA name map, the clip names, the motion step around a faked
generator and Blender, the gated-encoder message, and `anim` taking a motion version as a clip."""

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

from steps import _common, _rig, anim, motion
from steps._common import StepError

ROOT = Path(__file__).resolve().parents[1]

# the SOMA 77-joint skeleton's bones, from Kimodo's standard T-pose BVH (somaskel77), end sites left out
SOMA_BONES = {
    "Root",
    "Hips",
    "Spine1",
    "Spine2",
    "Chest",
    "Neck1",
    "Neck2",
    "Head",
    "HeadEnd",
    "Jaw",
    "LeftEye",
    "RightEye",
}
for _s in ("Left", "Right"):
    SOMA_BONES |= {
        f"{_s}{n}"
        for n in ("Shoulder", "Arm", "ForeArm", "Hand", "Leg", "Shin", "Foot", "ToeBase", "ToeEnd")
    }
    SOMA_BONES |= {f"{_s}HandThumb{k}" for k in (1, 2, 3)} | {f"{_s}HandThumbEnd"}
    for _f in ("Index", "Middle", "Ring", "Pinky"):
        SOMA_BONES |= {f"{_s}Hand{_f}{k}" for k in (1, 2, 3, 4)} | {f"{_s}Hand{_f}End"}


def _maps():
    spec = importlib.util.spec_from_file_location("_maps", ROOT / "blender_ops" / "_maps.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _glb(names):
    """A minimal GLB whose JSON chunk lists animations with these names (what clip_names reads)."""
    doc = json.dumps(
        {"asset": {"version": "2.0"}, "animations": [{"name": n} for n in names]}
    ).encode()
    doc += b" " * (-len(doc) % 4)
    return (
        b"glTF"
        + (2).to_bytes(4, "little")
        + (20 + len(doc)).to_bytes(4, "little")
        + len(doc).to_bytes(4, "little")
        + b"JSON"
        + doc
    )


def test_the_soma_map_covers_our_52_joints_with_real_soma_bones():
    maps = _maps()
    ours = [n for n, _ in _rig.template()]
    assert set(maps.SOMA) == set(ours) == set(maps.UE5)
    assert set(maps.SOMA.values()) <= SOMA_BONES
    assert len(set(maps.SOMA.values())) == 52  # no two of ours on one SOMA bone
    assert (
        maps.SOMA["LeftHandIndex1"] == "LeftHandIndex2"
        and maps.SOMA["LeftHandThumb1"] == "LeftHandThumb1"
    )
    assert (
        maps.SOMA["LeftUpLeg"] == "LeftLeg"
        and maps.SOMA["LeftLeg"] == "LeftShin"
        and maps.SOMA["Spine2"] == "Chest"
    )

    class Arm:  # what source_map reads of an imported armature
        def __init__(self, names):
            self.data = type("D", (), {"bones": [type("B", (), {"name": n})() for n in names]})()

    assert maps.source_map(Arm(["pelvis", "spine_01"])) == (maps.UE5, "pelvis")
    assert maps.source_map(Arm(["Root", "Hips", "Chest"])) == (maps.SOMA, "Hips")


def test_clip_name_from_the_prompt():
    assert motion.clip_name("A person waves with the right hand.") == "Waves with the right hand"
    assert motion.clip_name("the character jumps once") == "Jumps once"
    assert motion.clip_name("walk") == "Walk" and motion.clip_name("...") == "..."
    assert len(motion.clip_name("A person " + "runs very fast " * 10)) <= 48


@pytest.fixture
def fake_generation(monkeypatch):
    """generate() writes a BVH without Kimodo; Blender writes a GLB without Blender."""
    seen = {}

    def fake_generate(prompt, seconds, seed, steps, out, params=None, connect=None):
        seen["generate"] = (prompt, seconds, seed, steps)
        (out / "motion.bvh").write_text("HIERARCHY\nROOT Root\n{\n}\nMOTION\nFrames: 150\n")
        return out / "motion.bvh", {}

    def fake_blender(op, inputs, out, params, timeout=None):
        assert op == "bvh_clip" and inputs[0].name == "motion.bvh"
        seen["name"] = params["name"]
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / "clip.glb").write_bytes(_glb([params["name"]]))
        return {"bones": 78, "frames": 150, "fps": 30, "seconds": 5.0, "hips_height_m": 1.0}

    monkeypatch.setattr(motion, "generate", fake_generate)
    monkeypatch.setattr(motion, "run_blender", fake_blender)
    return seen


def test_motion_step_names_the_clip_and_declares_its_models(tmp_path, fake_generation):
    out = tmp_path / "310_motion"
    out.mkdir()
    res = motion.run(
        "generate", None, str(out), {"prompt": "A person waves.", "seconds": 5, "seed": 3}
    )
    assert (
        fake_generation["generate"] == ("A person waves.", 5.0, 3, 100)
        and fake_generation["name"] == "Waves"
    )
    assert set(res["outputs"]) == {"clip", "bvh", "prompt"} and (out / "clip.glb").is_file()
    pj = json.loads((out / "prompt.json").read_text())
    assert pj["prompt"] == "A person waves." and pj["name"] == "Waves" == res["metrics"]["name"]
    assert res["metrics"]["frames"] == 150 and res["metrics"]["encoder_device"] == "cpu"
    assert {m["id"] for m in res["models"]} == {motion.MODEL_ID, motion.ENCODER, *motion.ADAPTERS}
    assert "kimodo" in res["licenses"]


def test_a_second_motion_with_the_same_prompt_gets_the_version_number(tmp_path, fake_generation):
    first = tmp_path / "310_motion"
    first.mkdir()
    (first / "prompt.json").write_text(json.dumps({"prompt": "A person waves.", "name": "Waves"}))
    out = tmp_path / "320_motion"
    out.mkdir()
    motion.run("generate", None, str(out), {"prompt": "A person waves."})
    assert fake_generation["name"] == "Waves 320"


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"prompt": "x", "seconds": 11},
        {"prompt": "x", "seconds": "no"},
        {"prompt": "x", "steps": 0},
    ],
)
def test_motion_bad_params(tmp_path, params):
    with pytest.raises(StepError) as e:
        motion.run("generate", None, str(tmp_path), params)
    assert e.value.code == "bad_params"


def test_a_gated_encoder_tells_the_user_what_to_do(monkeypatch):
    """The model loader is Kimodo's; here a stand-in that fails the way a gated download does."""

    def load_model(name, device=None):
        raise RuntimeError("403 Client Error: gated repo meta-llama/Meta-Llama-3-8B-Instruct")

    for name in ("kimodo", "kimodo.model"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    _fake_kimodo(monkeypatch, load_model)
    with pytest.raises(StepError) as e:
        motion._model("cpu")
    assert e.value.code == "missing_input" and "hf auth login" in str(e.value)


def _fake_kimodo(monkeypatch, load_model):
    for name in ("kimodo", "kimodo.model"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    if (
        "torch" not in sys.modules
    ):  # the main venv has no torch; the loader asks it for the device and frees the cache
        cuda = types.SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None)
        monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=cuda))
    monkeypatch.setitem(
        sys.modules, "kimodo.model.load_model", types.SimpleNamespace(load_model=load_model)
    )
    monkeypatch.setattr(motion, "MODEL", {})
    monkeypatch.delenv("TEXT_ENCODER_DEVICE", raising=False)


def test_the_text_encoder_moves_only_when_it_must(monkeypatch):
    """M46a: on a budget that holds it the encoder goes on the GPU; a smaller budget moves it off (a reload); a worker
    that loaded it on the CPU keeps it there unless asked, so a budget hovering at the threshold does not reload it
    every time."""
    loads = []
    _fake_kimodo(
        monkeypatch,
        lambda name, device=None: loads.append(os.environ["TEXT_ENCODER_DEVICE"]) or object(),
    )
    monkeypatch.setattr(_common, "_budget", 22.7)
    assert motion.encoder_device({}, cuda=False) == "cpu"
    assert motion.encoder_device({}, cuda=True) == "cuda"
    motion._model("cuda")
    motion._model("cuda")
    assert loads == ["cuda"]  # loaded once
    monkeypatch.setattr(_common, "_budget", 14.6)
    assert motion.encoder_device({}, cuda=True) == "cpu"
    motion._model("cpu")
    assert loads == ["cuda", "cpu"]
    monkeypatch.setattr(_common, "_budget", 22.7)
    assert motion.encoder_device({}, cuda=True) == "cpu"  # no reload just to be faster
    assert motion.encoder_device({"offload": False}, cuda=True) == "cuda"


def test_anim_takes_a_motion_version_of_the_asset_as_a_clip(tmp_path, monkeypatch):
    asset = tmp_path / "asset"
    skin, mo, out = asset / "300_rig_skin", asset / "310_motion", asset / "320_anim"
    for d in (skin, mo, out):
        d.mkdir(parents=True)
    (skin / "rigged.glb").write_bytes(b"glb")
    (skin / "skeleton.json").write_text("{}")
    (mo / "clip.glb").write_bytes(_glb(["Waves"]))
    (mo / "meta.json").write_text(
        json.dumps({"licenses": ["kimodo", "meta-llama/Meta-Llama-3-8B-Instruct"]})
    )
    monkeypatch.setattr(anim, "catalogue", lambda: [{"name": "Walk", "file": "pack.glb"}])
    seen = {}

    def fake_blender(op, inputs, outdir, params, timeout=None):
        seen["inputs"], seen["clips"] = [str(i) for i in inputs], params["clips"]
        Path(outdir).mkdir(parents=True, exist_ok=True)
        (Path(outdir) / "rigged_anim.glb").write_bytes(b"glb")
        return {
            "report": {"clips": [{"name": c, "seconds": 5.0} for c in params["clips"]]},
            "baked": params["clips"],
            "missing": [],
        }

    monkeypatch.setattr(anim, "run_blender", fake_blender)
    res = anim.run("run", str(skin), str(out), {"clips": ["Walk", "310_motion"]})
    assert seen["clips"] == ["Walk", "Waves"]  # the motion by its animation's name
    assert seen["inputs"][0].endswith("rigged.glb") and Path(seen["inputs"][-1]).parts[-2:] == (
        "310_motion",
        "clip.glb",
    )
    assert res["metrics"]["clips"] == ["Walk", "Waves"]
    # the motion's components reach the credits through this version (M26 lost Llama 3's attribution)
    assert res["licenses"] == [
        "Mesh2Motion/mesh2motion-app",
        "kimodo",
        "meta-llama/Meta-Llama-3-8B-Instruct",
    ]
    only = anim.run("run", str(skin), str(out), {"clips": ["310_motion"]})
    assert "Mesh2Motion/mesh2motion-app" not in only["licenses"] and only["models"] == []
    with pytest.raises(StepError) as e:
        anim.run("run", str(skin), str(out), {"clips": ["999_motion"]})
    assert e.value.code == "bad_params"


def test_the_retarget_tells_mixamo_from_soma_and_ue5_and_keeps_a_prefix():
    """An animation file (owner 2026-10-12): Mixamo's names are ours, with or without its "mixamorig:" prefix; SOMA
    shares Hips and Spine1 with it, its legs (Leg/Shin) and its Chest tell it apart; anything else is None."""
    maps = _maps()
    ours = [n for n, _ in _rig.template()]
    assert maps.kind(ours) == "mixamo" and maps.kind(["mixamorig:" + n for n in ours]) == "mixamo"
    assert maps.kind(SOMA_BONES) == "soma" and maps.kind(maps.UE5.values()) == "ue5"
    assert maps.kind(["Bip01", "Bip01 Pelvis", "Bip01 Spine"]) is None

    class Arm:
        def __init__(self, names):
            self.data = type("D", (), {"bones": [type("B", (), {"name": n})() for n in names]})()

    m, root = maps.source_map(Arm(["mixamorig1:" + n for n in ours]))
    assert root == "mixamorig1:Hips" and m["LeftHand"] == "mixamorig1:LeftHand" and len(m) == 52
    assert maps.source_map(Arm(ours)) == (maps.MIXAMO, "Hips")
    assert maps.source_map(Arm(["Root", "Hips", "Chest"])) == (maps.SOMA, "Hips")  # as before
