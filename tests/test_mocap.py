"""M34 motion from video, without GEM-X: the params, the person tracker, the clip preparation, and the SOMA
motion math on a small skeleton: forward kinematics against the written BVH read back, the cleanup (floor,
facing, foot lock, in place)."""

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from steps import mocap
from steps._common import StepError

# a small SOMA-like skeleton: Root, Hips, a spine with a head, two legs with feet and toes (y up, metres,
# the character facing +Z: toes ahead of the ankles)
NAMES = ["Root", "Hips", "Spine1", "Head", "LeftLeg", "LeftFoot", "LeftToeBase"]
NAMES += ["RightLeg", "RightFoot", "RightToeBase"]
PARENTS = [-1, 0, 1, 2, 1, 4, 5, 1, 7, 8]
BIND = np.array(
    [
        [0, 0, 0],
        [0, 0.95, 0],
        [0, 1.25, 0],
        [0, 1.6, 0],
        [0.1, 0.5, 0],
        [0.1, 0.08, 0],
        [0.1, 0.02, 0.12],
        [-0.1, 0.5, 0],
        [-0.1, 0.08, 0],
        [-0.1, 0.02, 0.12],
    ],
    float,
)


def rotations(rotvec):
    return [Rotation.identity(len(rotvec))] + [
        Rotation.from_rotvec(rotvec[:, j]) for j in range(rotvec.shape[1])
    ]


def read_bvh(text: str) -> np.ndarray:
    """World joint positions per frame from a BVH (ZYX channels; position channels replace the offset, as
    Blender's importer reads them), centimetres → metres."""
    lines = iter(text.splitlines())
    joints, stack, chans = [], [], []
    for line in lines:
        w = line.split()
        if w[0] in ("ROOT", "JOINT"):
            joints.append({"parent": stack[-1] if stack else -1})
        elif w[0] == "End":
            next(lines), next(lines), next(lines)  # {  OFFSET  }
        elif w[0] == "{":
            stack.append(len(joints) - 1)
        elif w[0] == "}":
            stack.pop()
        elif w[0] == "OFFSET":
            joints[-1]["offset"] = np.array(w[1:4], float) / 100
        elif w[0] == "CHANNELS":
            joints[-1]["pos"] = int(w[1]) == 6
            chans.append(int(w[1]))
        elif w[0] == "MOTION":
            break
    next(lines), next(lines)  # Frames, Frame Time
    frames = [np.array(line.split(), float) for line in lines]
    out = np.zeros((len(frames), len(joints), 3))
    for t, v in enumerate(frames):
        i, world = 0, []
        for j, jt in enumerate(joints):
            local = v[i : i + 3] / 100 if jt["pos"] else jt["offset"]
            r = Rotation.from_euler(
                "ZYX", v[i + 3 : i + 6] if jt["pos"] else v[i : i + 3], degrees=True
            )
            i += chans[j]
            p = jt["parent"]
            world.append(r if p < 0 else world[p] * r)
            out[t, j] = local if p < 0 else out[t, p] + world[p].apply(local)
    return out


def test_bvh_reads_back_to_the_same_posed_joints():
    rng = np.random.default_rng(0)
    L = 5
    rotvec = rng.normal(0, 0.4, (L, len(NAMES) - 1, 3))
    transl = rng.normal(0, 0.1, (L, 3)) + [0, 0.95, 0]
    want = mocap.fk(rotations(rotvec), transl, NAMES, PARENTS, BIND)
    got = read_bvh(mocap.bvh_text(rotvec, transl, NAMES, PARENTS, BIND))
    np.testing.assert_allclose(got[:, 1:], want[:, 1:], atol=1e-4)  # Root: the BVH's origin
    # the rest skeleton stands upright on the floor (all channels zero)
    rest = read_bvh(mocap.bvh_text(np.zeros_like(rotvec[:1]), BIND[1:2], NAMES, PARENTS, BIND))[0]
    assert (
        rest[NAMES.index("Head"), 1]
        > rest[NAMES.index("Hips"), 1]
        > rest[NAMES.index("LeftFoot"), 1]
    )


def test_fk_uses_bones_not_positions():
    """The bug the dance run found: bind positions taken as offsets put the feet two hips' heights away."""
    pos = mocap.fk(rotations(np.zeros((1, 9, 3))), BIND[1:2], NAMES, PARENTS, BIND)[0]
    np.testing.assert_allclose(pos[1:], BIND[1:], atol=1e-9)


P = {"smooth": 0.0, "foot_lock": False, "in_place": False}


def test_clean_grounds_turns_to_plus_z_and_starts_at_the_origin():
    L = 10
    rotvec = np.zeros((L, 9, 3))
    rotvec[:, 0] = Rotation.from_euler(
        "y", 180, degrees=True
    ).as_rotvec()  # facing -Z, as GEM-X's world did
    transl = np.tile([0.4, 1.2, -0.3], (L, 1))  # floating 0.25 m, away from the origin
    rv, tr, info = mocap.clean(rotvec, transl, NAMES, PARENTS, BIND, P)
    pos = mocap.fk(rotations(rv), tr, NAMES, PARENTS, BIND)
    feet = [NAMES.index(n) for n in ("LeftFoot", "LeftToeBase", "RightFoot", "RightToeBase")]
    assert abs(pos[:, feet, 1].min()) < 1e-6  # on the floor
    toe = pos[0, NAMES.index("LeftToeBase")] - pos[0, NAMES.index("LeftFoot")]
    assert toe[2] > 0 and abs(toe[0]) < 1e-6  # facing +Z
    np.testing.assert_allclose(tr[0, [0, 2]], 0, atol=1e-9)
    assert abs(abs(info["turned_deg"]) - 180) < 1e-6


def test_foot_lock_holds_a_planted_foot_and_in_place_drops_travel():
    L = 30
    rotvec = np.zeros((L, 9, 3))
    transl = np.tile([0.0, 0.95, 0.0], (L, 1))
    transl[:, 0] = np.linspace(
        0, 0.3, L
    )  # the body drifts 0.3 m sideways with both feet down: slide
    _, tr0, _ = mocap.clean(rotvec, transl, NAMES, PARENTS, BIND, P)
    _, tr1, info = mocap.clean(rotvec, transl, NAMES, PARENTS, BIND, P | {"foot_lock": True})
    toe = NAMES.index("LeftToeBase")
    drift = lambda tr: np.ptp(mocap.fk(rotations(rotvec), tr, NAMES, PARENTS, BIND)[:, toe, 0])
    assert drift(tr0) > 0.29 and drift(tr1) < 0.02 and info["foot_locked_frames"] >= L - 2
    _, tr2, _ = mocap.clean(rotvec, transl, NAMES, PARENTS, BIND, P | {"in_place": True})
    np.testing.assert_allclose(tr2[:, [0, 2]], 0, atol=1e-9)


def test_smoothing_keeps_a_still_pose_and_calms_noise():
    rng = np.random.default_rng(1)
    L = 40
    base = np.tile(np.array([0.3, 0.2, -0.1]), (L, 9, 1))
    noisy = base + rng.normal(0, 0.05, base.shape)
    transl = np.tile([0.0, 0.95, 0.0], (L, 1))
    rv, _, _ = mocap.clean(noisy, transl, NAMES, PARENTS, BIND, P | {"smooth": 1.0})
    jitter = lambda r: np.abs(np.diff(r[:, 2:], 2, axis=0)).mean()
    assert jitter(rv) < 0.3 * jitter(noisy)
    still, _, _ = mocap.clean(base, transl, NAMES, PARENTS, BIND, P | {"smooth": 1.0})
    np.testing.assert_allclose(still[:, 2:], base[:, 2:], atol=1e-6)


def test_track_follows_the_chosen_person():
    a = np.array([[10, 10, 110, 310, 0.9], [300, 20, 350, 120, 0.9]])  # a big and a small person
    dets = [np.zeros((0, 5)), a, a + [5, 0, 5, 0, 0], np.zeros((0, 5)), a + [10, 0, 10, 0, 0]]
    big = mocap.track(dets, 0)
    np.testing.assert_allclose(big[0], a[0, :4])  # before the first detection: the first box
    np.testing.assert_allclose(big[3], big[2])  # a frame without people keeps the last box
    np.testing.assert_allclose(big[4], a[0, :4] + [10, 0, 10, 0])
    assert mocap.track(dets, 1)[4][0] == 310  # the second largest, followed
    with pytest.raises(StepError):
        mocap.track(dets, 2)


@pytest.mark.parametrize(
    "params",
    [
        {"mode": "fast"},
        {"start": -1},
        {"start": 3, "end": 2},
        {"person": 9},
        {"camera": "moving"},
        {"smooth": 2},
        {"end": "x"},
    ],
)
def test_bad_params(params):
    with pytest.raises(StepError) as e:
        mocap.params_of(params)
    assert e.value.code == "bad_params"


def test_params_defaults():
    assert mocap.params_of({}) == {
        "mode": "best",
        "start": 0.0,
        "end": None,
        "person": 0,
        "camera": "still",
        "smooth": 0.5,
        "foot_lock": True,
        "in_place": False,
        "name": "",
    }


def _warped(angles_deg: list[float], K: np.ndarray, rng) -> np.ndarray:
    """A textured scene seen by a camera turning about its vertical axis by each angle: frames (L, H, W, 3)."""
    import cv2

    h, w = 240, 320
    scene = cv2.GaussianBlur(rng.integers(0, 256, (h, w), dtype=np.uint8), (0, 0), 1.5)
    out = []
    for a in angles_deg:
        R = Rotation.from_euler("y", a, degrees=True).as_matrix()
        img = cv2.warpPerspective(
            scene, K @ R @ np.linalg.inv(K), (w, h), borderMode=cv2.BORDER_REFLECT
        )
        out.append(np.repeat(img[..., None], 3, axis=2))
    return np.stack(out)


def test_camera_path_reads_a_pan_off_the_background_and_a_still_camera_as_still():
    K = np.array([[320.0, 0, 160], [0, 320.0, 120], [0, 0, 1]])
    rng = np.random.default_rng(0)
    angles = [0.5 * t for t in range(10)]
    R = mocap.camera_path(_warped(angles, K, rng), np.zeros((10, 4)), K)
    for t, a in enumerate(angles):  # each frame's R_w2c is the camera's turn from the first frame
        off = Rotation.from_matrix(R[t]) * Rotation.from_euler("y", a, degrees=True).inv()
        assert np.degrees(off.magnitude()) < 0.3, (t, np.degrees(off.magnitude()))
    still = mocap.camera_path(_warped([0.0] * 5, K, rng), np.zeros((5, 4)), K)
    assert np.degrees(Rotation.from_matrix(still[-1]).magnitude()) < 0.05


def test_prepare_video_resamples_trims_and_scales(tmp_path):
    cv2 = pytest.importorskip("cv2")
    src = tmp_path / "in.mp4"
    w = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"mp4v"), 15, (1600, 2400))
    for i in range(60):  # 4 s at 15 fps, portrait
        w.write(np.full((2400, 1600, 3), i * 4, np.uint8))
    w.release()
    n, width, height = mocap.prepare_video(src, tmp_path / "out.mp4", 1.0, 3.0)
    assert n == 60 and (width, height) == (720, 1080)  # 2 s at 30 fps; the short side at 720
    with pytest.raises(StepError):
        mocap.prepare_video(src, tmp_path / "short.mp4", 0, 0.5)  # under a second


def test_run_needs_a_video(tmp_path):
    (tmp_path / "image.png").write_bytes(b"png")
    with pytest.raises(StepError) as e:
        mocap.run("run", str(tmp_path), str(tmp_path / "out"), {})
    assert e.value.code == "missing_input"


def test_a_clip_over_the_limit_is_refused(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    monkeypatch.setattr(mocap, "MAX_SECONDS", 2)
    src = tmp_path / "long.mp4"
    w = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"mp4v"), 30, (64, 64))
    for _ in range(90):  # 3 s
        w.write(np.zeros((64, 64, 3), np.uint8))
    w.release()
    with pytest.raises(StepError, match="longer than 2 s"):
        mocap.prepare_video(src, tmp_path / "out.mp4", 0, None)
    assert (
        mocap.prepare_video(src, tmp_path / "part.mp4", 0.5, 2.0)[0] == 45
    )  # a part of it is fine


def test_the_model_check_needs_sam_only_with_image_features(tmp_path, monkeypatch):
    monkeypatch.setattr(mocap, "GX", tmp_path)
    monkeypatch.setattr(mocap, "YOLOX", tmp_path / "yolox_l.onnx")
    every = [
        "inputs/pretrained/gem_soma.ckpt",
        "inputs/checkpoints/vitpose/vitpose.pth",
        "inputs/soma_data/scale_mean.pth",
        "inputs/soma_data/scale_comps.pth",
        "inputs/soma_assets/SOMA_neutral.npz",
        "inputs/soma_assets/correctives_model.pt",
        "inputs/soma_assets/MHR/base_body_lod6.obj",
        "inputs/soma_assets/MHR/SOMA_wrap_lod1.obj",
        "inputs/soma_assets/MHR/mhr_model_lod6.pt",
        f"{mocap.DINOV3}.zip",
        "yolox_l.onnx",
    ]
    for f in every:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes(b"x")
    mocap._check_models("keypoints")  # no SAM 3D Body needed
    with pytest.raises(StepError) as e:
        mocap._check_models("best")
    assert e.value.code == "missing_input" and "sam3d_body.ckpt" in str(e.value)


def test_run_around_a_faked_gem_x(tmp_path, monkeypatch):
    """The flow without GEM-X: a relative input path still finds the upload's name after the engine's chdir,
    a pack clip's name is not reused, the BVH and the clip are written, the licences follow the mode."""
    asset = tmp_path / "asset"
    up = asset / "040_upload"
    up.mkdir(parents=True)
    (up / "video.mp4").write_bytes(b"mp4")
    (up / "meta.json").write_text('{"params": {"name": "Walk.mp4"}}')
    L = 12
    rng = np.random.default_rng(2)
    pred = {
        "body_params_global": {
            "global_orient": rng.normal(0, 0.1, (L, 3)),
            "body_pose": rng.normal(0, 0.1, (L, 8 * 3)),
            "transl": np.tile([0.0, 0.95, 0.0], (L, 1)),
            "identity_coeffs": np.zeros((L, 4)),
            "scale_params": np.zeros((L, 4)),
        }
    }

    def fake_gem(video, mode, person, work, camera):
        monkeypatch.chdir(tmp_path / "elsewhere")  # what the engine does
        assert camera == "panning"
        return pred, np.zeros((L, 77, 3)), np.zeros((L, 4)), 0.5, 3

    def fake_blender(op, inputs, outdir, params, timeout=None):
        assert op == "bvh_clip" and params["name"] == "Walk 050"  # the pack has a Walk
        outdir.mkdir(parents=True)
        (outdir / "clip.glb").write_bytes(b"glb")
        return {"bones": len(NAMES)}

    (tmp_path / "elsewhere").mkdir()
    monkeypatch.setattr(mocap, "_check_models", lambda mode: None)
    monkeypatch.setattr(mocap, "prepare_video", lambda src, dst, s, e: (L, 64, 64))
    monkeypatch.setattr(mocap, "gem", fake_gem)
    monkeypatch.setattr(mocap, "skeleton", lambda i, s: (NAMES, PARENTS, BIND))
    monkeypatch.setattr(mocap, "preview", lambda *a: None)
    monkeypatch.setattr(mocap, "run_blender", fake_blender)
    import steps.anim

    monkeypatch.setattr(steps.anim, "catalogue", lambda: [{"name": "Walk", "file": "pack.glb"}])
    monkeypatch.chdir(tmp_path)
    res = mocap.run(
        "run",
        "asset/040_upload",
        str(asset / "050_mocap"),
        {"mode": "keypoints", "camera": "panning", "person": 1},
    )
    assert res["metrics"]["name"] == "Walk 050" and res["outputs"]["clip"] == "clip.glb"
    assert (res["metrics"]["people"], res["metrics"]["person"], res["metrics"]["camera"]) == (
        3,
        1,
        "panning",
    )
    assert (asset / "050_mocap" / "motion.bvh").read_text().startswith("HIERARCHY")
    assert "facebook/sam-3d-body" not in res["licenses"] and "nvidia/GEM-X" in res["licenses"]
