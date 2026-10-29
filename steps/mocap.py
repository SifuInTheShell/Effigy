"""Motion from video (IMPLEMENTATION.md M34): NVIDIA GEM-X (engine/gemx) in .venv-gemx, one-shot.

in: a version with a video (an upload: video.mp4|mov|webm); params: {"mode": "best"|"quick"|"keypoints",
"start": 0.0, "end": null (seconds), "person": 0, "camera": "still"|"panning", "smooth": 0.5, "foot_lock": true,
"in_place": false, "name": the clip's name (else the video's)} → motion.bvh (SOMA, 78 joints with Root, an upright
rest as Kimodo's BVHs), clip.glb (blender_ops/bvh_clip, as M26), preview.mp4 (the 2D keypoints on the clip). anim
takes the version like a motion version, so it lands among "Your motions". `metrics.people` counts the people at
the start of the clip: one run makes one person's motion (`person`: 0 the largest), the others are more runs (M64).
A "panning" camera turns on the spot to follow (M64): its turn is read off the background and given to GEM-X.

The pipeline is GEM-X's demo (research 12), driven here: the clip at 30 fps, at most 720 px → the person
(YOLOX-L from DWPose's repo, tracked by overlap: `person` = 0 the largest at the start) → 77 2D keypoints
(GEM-X's ViTPose) → SAM 3D Body's image features (not for "keypoints") → GEM-X, one regression pass ("quick")
or 50 DDIM steps ("best", "keypoints"); a still camera, or a panning one's rotation from the background
(`camera_path`; GEM-X's own camera tracking is not in its release). Then the SOMA pose becomes a BVH with an
upright rest (`bvh_text`), after the cleanup (`clean`).
"""

import json
import os
import shutil
import sys
import time
import types
from pathlib import Path

import numpy as np

from steps._blender import BlenderError, run_blender
from steps._common import StepError, main, progress

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine" / "gemx"
MODELS = Path(os.environ.get("EFFIGY_MODELS", ROOT / "models")).resolve()  # the step chdirs
GX = MODELS / "GEM-X"  # GEM-X's inputs/ layout (tools/download_models.py, group mocap)
YOLOX = MODELS / "DWPose" / "yolox_l.onnx"
DINOV3 = "dinov3-6876159"  # the pinned code zip in GX, unpacked where torch.hub looks
MODES = ("best", "quick", "keypoints")
CAMERAS = ("still", "panning")
FPS = 30  # GEM-X's rate
# the short and long side at most: GEM-X reads the whole clip into RAM (4K: ~10 GB); the spike's size
SIDES = (720, 1280)
# the longest clip: GEM-X and SAM 3D Body each hold every frame in RAM (720 × 1280: 2.8 MB a frame, so 30 s
# ≈ 2.5 GB each); ponytail: a longer one is trimmed by the user, chunked runs if a game needs minutes
MAX_SECONDS = 30
VIDEO = (".mp4", ".mov", ".webm")
# every mode: GEM-X, SOMA, the keypoint model's DINOv3 backbone, the person detector; with image features
# also SAM 3D Body and MHR
_ALL = [
    "nvidia/GEM-X",
    "NVlabs/GEM-X",
    "NVlabs/SOMA-X",
    "facebook/dinov3-vitl16-pretrain-lvd1689m",
    "yzd-v/DWPose",
]
_SAM = ["facebook/sam-3d-body", "facebookresearch/MHR"]
LICENSES = {"best": _ALL + _SAM, "quick": _ALL + _SAM, "keypoints": _ALL}


def params_of(params: dict) -> dict:
    try:
        p = {
            "mode": str(params.get("mode", "best")),
            "start": float(params.get("start") or 0),
            "end": None if params.get("end") in (None, "") else float(params["end"]),
            "person": int(params.get("person", 0)),
            "camera": str(params.get("camera", "still")),
            "smooth": float(params.get("smooth", 0.5)),
            "foot_lock": bool(params.get("foot_lock", True)),
            "in_place": bool(params.get("in_place", False)),
            "name": str(params.get("name") or "").strip()[:48],
        }
    except (TypeError, ValueError):
        raise StepError("bad_params", f"bad mocap params: {params}") from None
    if (
        p["mode"] not in MODES
        or p["start"] < 0
        or (p["end"] is not None and p["end"] <= p["start"])
        or not 0 <= p["person"] < 8
        or p["camera"] not in CAMERAS
        or not 0 <= p["smooth"] <= 1
    ):
        raise StepError(
            "bad_params",
            f"bad mocap params: {params} (mode {'|'.join(MODES)}; 0 ≤ start < end seconds; person 0–7; "
            f"camera {'|'.join(CAMERAS)}; smooth 0–1)",
        )
    return p


# --- the video and the person ------------------------------------------------------------------------


def prepare_video(src: Path, dst: Path, start: float, end: float | None) -> tuple[int, int, int]:
    """The clip from start to end at 30 fps (by time: a frame is repeated or dropped), at most 720 px on the short side and 1280 on the long → dst (mp4); frames, width, height."""
    import cv2

    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not 1 <= fps <= 240:  # NaN, 0, or a WebM's timebase (1000) instead of its rate
        fps = FPS
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if not cap.isOpened() or not w or not h:
        raise StepError("bad_params", f"{src.name}: not a video OpenCV can read")
    k = min(1.0, SIDES[0] / min(w, h), SIDES[1] / max(w, h))
    size = (round(w * k / 2) * 2, round(h * k / 2) * 2)
    writer = cv2.VideoWriter(str(dst), cv2.VideoWriter_fourcc(*"mp4v"), FPS, size)
    n, i, frame = 0, -1, None
    while True:
        t = start + n / FPS
        if end is not None and t >= end:
            break
        if n == MAX_SECONDS * FPS:  # one frame more is coming: too long
            cap.release()
            writer.release()
            raise StepError(
                "bad_params",
                f"the clip is longer than {MAX_SECONDS} s: choose a part of it (From / to seconds)",
            )
        while i < round(t * fps):  # read up to the source frame at time t
            ok, f = cap.read()
            if not ok:
                break
            i, frame = i + 1, f
        else:
            writer.write(cv2.resize(frame, size, interpolation=cv2.INTER_AREA))
            n += 1
            continue
        break
    cap.release()
    writer.release()
    if n < FPS:
        raise StepError("bad_params", f"{n} frames: the clip needs at least a second of video")
    return n, *size


def yolox_boxes(session, frame: np.ndarray, score: float = 0.5) -> np.ndarray:
    """People in one BGR frame: (n, 5) x0 y0 x1 y1 score, by YOLOX-L (DWPose's yolox_l.onnx: raw output,
    decoded and NMS'd here as YOLOX's own demo does)."""
    import cv2

    size = 640
    r = min(size / frame.shape[0], size / frame.shape[1])
    img = np.full((size, size, 3), 114, np.uint8)
    rs = cv2.resize(frame, (int(frame.shape[1] * r), int(frame.shape[0] * r)))
    img[: rs.shape[0], : rs.shape[1]] = rs
    out = session.run(
        None, {session.get_inputs()[0].name: img.transpose(2, 0, 1)[None].astype(np.float32)}
    )[0][0]
    grids, strides = [], []
    for s in (8, 16, 32):
        g = size // s
        yv, xv = np.meshgrid(np.arange(g), np.arange(g), indexing="ij")
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((g * g, 1), s))
    grids, strides = np.concatenate(grids), np.concatenate(strides)
    xy = (out[:, :2] + grids) * strides
    wh = np.exp(out[:, 2:4]) * strides
    conf = out[:, 4] * out[:, 5]  # objectness × the person class
    keep = conf > score
    boxes = np.concatenate([xy - wh / 2, xy + wh / 2], 1)[keep] / r
    conf = conf[keep]
    if not len(conf):
        return np.zeros((0, 5))
    pick = cv2.dnn.NMSBoxes(
        [[float(a), float(b), float(c - a), float(d - b)] for a, b, c, d in boxes],
        conf.tolist(),
        score,
        0.45,
    )
    pick = np.array(pick).reshape(-1)
    return np.concatenate([boxes[pick], conf[pick, None]], 1)


def iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    lo, hi = np.maximum(a[:2], b[:, :2]), np.minimum(a[2:4], b[:, 2:4])
    inter = np.clip(hi - lo, 0, None).prod(1)
    area = lambda x: (x[..., 2] - x[..., 0]) * (x[..., 3] - x[..., 1])
    return inter / (area(a) + area(b) - inter + 1e-9)


def track(dets: list[np.ndarray], person: int) -> np.ndarray:
    """One box per frame for one person: at the first frame with people, the `person`-th largest; then the
    box overlapping the last one most; a frame without it keeps the last box (the first frames take the
    first found). ponytail: overlap only, no motion model; ByteTrack if people cross."""
    first = next((i for i, d in enumerate(dets) if len(d) > person), None)
    if first is None:
        raise StepError(
            "failed",
            f"no person {person + 1} found in the clip"
            if person
            else "no person found in the clip",
        )
    d = dets[first]
    cur = d[np.argsort(-(d[:, 2] - d[:, 0]) * (d[:, 3] - d[:, 1]))[person], :4]
    out = np.zeros((len(dets), 4))
    for i in range(first, len(dets)):
        if len(dets[i]):
            o = iou(cur, dets[i])
            if o.max() > 0.3:
                cur = dets[i][o.argmax(), :4]
        out[i] = cur
    out[:first] = out[first]
    return out


def camera_path(frames: np.ndarray, boxes: np.ndarray, K: np.ndarray) -> np.ndarray:
    """R_w2c per frame (L, 3, 3) of a camera that turns on the spot: the background's motion from each frame
    to the next (corners outside the person's box, followed by LK flow, a RANSAC homography) read through K as
    the rotation GEM-X's `cam_angvel` is made of (R_w2c[t] = R @ R_w2c[t-1]). A frame too poor to read keeps
    the turn. ponytail: pure rotation; a camera that walks (a dolly) reads as a turn, so the person drifts."""
    import cv2

    L = len(frames)
    R = np.tile(np.eye(3), (L, 1, 1))
    Ki = np.linalg.inv(K)
    prev = cv2.cvtColor(frames[0], cv2.COLOR_RGB2GRAY)
    for t in range(1, L):
        cur = cv2.cvtColor(frames[t], cv2.COLOR_RGB2GRAY)
        mask = np.full(prev.shape, 255, np.uint8)
        x0, y0, x1, y1 = boxes[t - 1]
        pad = 0.1 * max(x1 - x0, y1 - y0)  # the person's own motion is not the camera's
        cv2.rectangle(mask, (int(x0 - pad), int(y0 - pad)), (int(x1 + pad), int(y1 + pad)), 0, -1)
        rel = np.eye(3)
        pts = cv2.goodFeaturesToTrack(prev, 400, 0.01, 8, mask=mask)
        if pts is not None and len(pts) >= 12:
            nxt, ok, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None)
            ok = ok.reshape(-1) == 1
            if ok.sum() >= 12:
                H, inl = cv2.findHomography(pts[ok], nxt[ok], cv2.RANSAC, 3.0)
                if H is not None and inl.sum() >= 12 and np.linalg.det(M := Ki @ H @ K) > 0:
                    U, _, Vt = np.linalg.svd(M / np.cbrt(np.linalg.det(M)))  # the nearest rotation
                    rel = U @ np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))]) @ Vt
        R[t] = rel @ R[t - 1]
        prev = cur
    return R


# --- GEM-X -------------------------------------------------------------------------------------------


def _engine():
    """GEM-X importable, its relative inputs/ paths resolving in models/GEM-X, every cache there."""
    os.chdir(GX)
    os.environ["HF_HUB_OFFLINE"] = "1"  # every file is checked first: never a download mid-job
    os.environ["TORCH_HOME"] = str(GX / "torch")
    os.environ.setdefault("WARP_CACHE_PATH", str(GX / "warp"))
    hub = GX / "torch" / "hub"
    repo_dir = hub / "facebookresearch_dinov3_main"  # our pinned commit, unpacked once
    if not (repo_dir / "hubconf.py").is_file():
        import zipfile

        zip_path = GX / f"{DINOV3}.zip"
        if not zip_path.is_file():
            raise StepError(
                "missing_input",
                "the `mocap` model group is not installed (Setup → Motion from video)",
            )
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(hub)
        top = next(hub.glob("dinov3-*"))
        shutil.rmtree(repo_dir, ignore_errors=True)
        top.rename(repo_dir)
    # ViTPose asks torch.hub for DINOv3's class from GitHub: the pinned copy, loaded as a local repo (torch.hub
    # would still ask GitHub about the branch on every load otherwise)
    import torch.hub

    if not getattr(torch.hub.load, "effigy_local", False):
        load = torch.hub.load

        def local_dinov3(repo, model, *args, **kwargs):
            if repo == "facebookresearch/dinov3":
                kwargs = {k: v for k, v in kwargs.items() if k in ("pretrained", "weights")}
                return load(str(repo_dir), model, *args, source="local", **kwargs)
            return load(repo, model, *args, **kwargs)

        local_dinov3.effigy_local = True
        torch.hub.load = local_dinov3
    # wandb: only GEM-X's training helpers use it (engine/gemx/PATCHES.md)
    wandb = types.ModuleType("wandb")
    wandb.run = None
    wandb.sdk = types.SimpleNamespace(wandb_run=types.SimpleNamespace(Run=type("Run", (), {})))
    sys.modules.setdefault("wandb", wandb)
    if str(ENGINE) not in sys.path:
        sys.path.insert(0, str(ENGINE))


def _check_models(mode: str):
    """Every file the run reads (GEM-X fetches a missing one from Hugging Face mid-job otherwise)."""
    need = [
        GX / "inputs/pretrained/gem_soma.ckpt",
        GX / "inputs/checkpoints/vitpose/vitpose.pth",
        GX / "inputs/soma_data/scale_mean.pth",
        GX / "inputs/soma_data/scale_comps.pth",
        *(
            GX / "inputs/soma_assets" / f
            for f in ("SOMA_neutral.npz", "correctives_model.pt", "MHR/base_body_lod6.obj")
        ),
        GX / "inputs/soma_assets/MHR/SOMA_wrap_lod1.obj",
        GX / "inputs/soma_assets/MHR/mhr_model_lod6.pt",
        GX / f"{DINOV3}.zip",
        YOLOX,
    ]
    if mode != "keypoints":
        need += [
            GX / "inputs/checkpoints/sam-3d-body-dinov3/sam3d_body.ckpt",
            GX / "inputs/checkpoints/sam-3d-body-dinov3/model_config.yaml",
            GX / "inputs/mhr_data/mhr_model.pt",
        ]
    if missing := [p for p in need if not p.is_file()]:
        raise StepError(
            "missing_input",
            f"the `mocap` model group is not installed (Setup → Motion from video): {missing[0].name}",
        )


def gem(video: Path, mode: str, person: int, work: Path, camera: str = "still"):
    """GEM-X on the prepared clip: (its prediction, the 2D keypoints (L, 77, 3), the boxes (L, 4), peak VRAM,
    the people at the start of the clip)."""
    _engine()
    import cv2
    import onnxruntime
    import torch
    from gem.utils.cam_utils import estimate_K, get_a_pred_cam
    from gem.utils.geo_transform import compute_cam_angvel, compute_cam_tvel, get_bbx_xys_from_xyxy
    from gem.utils.kp2d_utils import smooth_bbx_xyxy
    from gem.utils.net_utils import detach_to_cpu
    from gem.utils.video_io_utils import get_video_lwh, read_video_np
    from gem.utils.vitpose_extractor import VitPoseExtractor

    torch.cuda.reset_peak_memory_stats()
    progress(0.15, "finding the person")
    # onnxruntime-gpu < 1.24 is built for CUDA 12: torch's own CUDA and cuDNN DLLs serve it (10 ms a frame
    # against 110 on the CPU, research 12)
    onnxruntime.preload_dlls(directory=str(Path(torch.__file__).parent / "lib"))
    session = onnxruntime.InferenceSession(
        str(YOLOX), providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
    )
    cap, dets = cv2.VideoCapture(str(video)), []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        dets.append(yolox_boxes(session, frame))
    cap.release()
    del session
    L, W, H = get_video_lwh(str(video))
    if len(dets) != L:  # OpenCV and PyAV read the prepared clip: they must agree on its frames
        raise StepError("failed", f"the clip reads as {len(dets)} frames in OpenCV, {L} in PyAV")
    xyxy = torch.from_numpy(track(dets, person)).float()
    people = next(len(d) for d in dets if len(d))  # track() found a frame with people
    xyxy = smooth_bbx_xyxy(xyxy, window=5)
    xyxy[:, [0, 2]] = xyxy[:, [0, 2]].clamp(0, W - 1)
    xyxy[:, [1, 3]] = xyxy[:, [1, 3]].clamp(0, H - 1)
    bbx_xys = get_bbx_xys_from_xyxy(xyxy, base_enlarge=1.2).float()
    K = estimate_K(W, H)

    frames = read_video_np(str(video))
    eye = torch.eye(4)[None].repeat(L, 1, 1)
    T_w2c = eye.clone()
    if camera == "panning":
        progress(0.25, "the camera's turn")
        T_w2c[:, :3, :3] = torch.from_numpy(camera_path(frames, xyxy.numpy(), K.numpy())).float()
    progress(0.3, "2D keypoints")
    vitpose = VitPoseExtractor(device="cuda:0", pose_type="soma").extract(
        frames, bbx_xys, img_ds=1.0, path_type="np"
    )
    vitpose = vitpose[0] if isinstance(vitpose, tuple) else vitpose
    del frames
    torch.cuda.empty_cache()
    K = K.repeat(L, 1, 1)
    if mode == "keypoints":
        f_imgseq, pred_cam, has_img = torch.zeros(L, 1024), None, torch.zeros(L).bool()
    else:
        progress(0.45, "image features (SAM 3D Body)")
        from gem.utils.sam3db_extractor import SAM3DBExtractor

        res = SAM3DBExtractor(device="cuda:0").extract_video_features(
            str(video), bbx_xys, render_mhr=False
        )
        f_imgseq, pred_cam = res["pose_tokens"], get_a_pred_cam(res["transls"], bbx_xys, K)
        has_img = torch.ones(L).bool()
        torch.cuda.empty_cache()

    progress(0.65, "GEM-X: the 3D motion" + (" (50 steps)" if mode != "quick" else ""))
    import hydra
    from hydra import compose, initialize_config_dir

    with initialize_config_dir(version_base="1.3", config_dir=str(ENGINE / "configs")):
        cfg = compose(
            config_name="demo_soma",
            overrides=[
                "exp=gem_soma_regression",
                f"video_name={video.stem}",
                f"video_path={video.as_posix()}",
                f"output_root={work.as_posix()}",
                f"static_cam={'true' if camera == 'still' else 'false'}",
                "use_wandb=false",
                "task=test",
            ],
        )
    model = hydra.utils.instantiate(cfg.model, _recursive_=False)
    model.load_pretrained_model(str(GX / "inputs/pretrained/gem_soma.ckpt"))
    model = model.eval().cuda()
    if (
        mode != "quick"
    ):  # DDIM: the flag the sampler reads sits on the diffusion module (research 12)
        for m in model.modules():
            if hasattr(m, "regression_only"):
                m.regression_only = False
    data = {
        "meta": [{"vid": video.stem}],
        "length": torch.tensor(L),
        "bbx_xys": bbx_xys,
        "kp2d": vitpose,
        "K_fullimg": K,
        "cam_angvel": compute_cam_angvel(T_w2c[:, :3, :3]),
        "cam_tvel": compute_cam_tvel(eye[:, :3, 3]),  # a panning camera stays where it is
        "R_w2c": T_w2c[:, :3, :3],
        "T_w2c": T_w2c,
        "f_imgseq": f_imgseq,
        "noisy_pred_cam": pred_cam,
        "has_text": torch.tensor([False]),
        "mask": {
            "valid": torch.ones(L).bool(),
            "has_img_mask": has_img,
            "has_2d_mask": torch.ones(L).bool(),
            "has_cam_mask": torch.ones(L).bool(),
            "has_audio_mask": torch.zeros(L).bool(),
            "has_music_mask": torch.zeros(L).bool(),
        },
    }
    with torch.no_grad():
        pred = detach_to_cpu(model.predict(data, static_cam=camera == "still", postproc=True))
    peak = torch.cuda.max_memory_allocated() / 2**30
    return pred, np.asarray(vitpose), xyxy.numpy(), round(peak, 2), people


# --- SOMA → an upright-rest BVH, and the cleanup ------------------------------------------------------


def skeleton(identity, scale) -> tuple[list[str], list[int], np.ndarray]:
    """SOMA's 78 joints (Root first): names, parents (-1 for Root), bind-pose positions in metres."""
    import torch
    from gem.utils.soma_utils.soma_layer import SomaLayer

    rig = np.load(GX / "inputs/soma_assets/SOMA_neutral.npz", allow_pickle=False)
    names = [str(n) for n in rig["joint_names"]]
    parents = [-1] + [int(p) for p in rig["joint_parent_ids"][1:]]
    soma = SomaLayer(
        data_root="inputs/soma_assets",
        low_lod=True,
        device="cpu",
        identity_model_type="mhr",
        mode="warp",
    )
    with torch.no_grad():
        p77 = soma.get_skeleton(
            torch.as_tensor(identity[:1]).float(), torch.as_tensor(scale[:1]).float()
        )[0]
    return names, parents, np.concatenate([np.zeros((1, 3)), p77.cpu().numpy()]).astype(float)


def fk(rot, transl, names, parents, bind):
    """World joint positions (L, J, 3): rot (L, J) scipy Rotations per joint (Root identity), the Hips at
    transl, the bones as long as in the bind pose (`bind`: the joints' positions, as `skeleton` gives them)."""
    from scipy.spatial.transform import Rotation

    L = len(transl)
    world, pos = [None] * len(names), np.zeros((L, len(names), 3))
    for j, p in enumerate(parents):
        if p < 0:
            world[j] = Rotation.identity(L)
            continue
        world[j] = world[p] * rot[j]
        pos[:, j] = (
            transl
            if names[j] == "Hips"
            else pos[:, p] + world[p].apply(np.tile(bind[j] - bind[p], (L, 1)))
        )
    return pos


def clean(
    rotvec: np.ndarray, transl: np.ndarray, names, parents, offsets, p: dict
) -> tuple[np.ndarray, np.ndarray, dict]:
    """The motion grounded, facing +Z from the origin on its first frame (as Kimodo's clips start), smoothed,
    its planted feet held, optionally in place. rotvec (L, 77, 3) for joints 1..77, transl (L, 3) the Hips.
    ponytail: the foot lock moves the root so a planted foot stays put (no IK: a lifted knee keeps its bend)."""
    from scipy.signal import savgol_filter
    from scipy.spatial.transform import Rotation

    L = len(transl)
    rotvec, transl = rotvec.copy(), transl.astype(float).copy()
    if p["smooth"] > 0 and L >= 7:
        window = min(L - (1 - L % 2), 3 + 2 * round(p["smooth"] * 6))  # odd, 3..15 frames at 30 fps
        q = Rotation.from_rotvec(rotvec.reshape(-1, 3)).as_quat().reshape(L, -1, 4)
        for t in range(1, L):  # one hemisphere per joint over time, so averaging is meaningful
            flip = (q[t] * q[t - 1]).sum(-1) < 0
            q[t, flip] *= -1
        q = savgol_filter(q, window, 2, axis=0)
        rotvec = Rotation.from_quat(q.reshape(-1, 4)).as_rotvec().reshape(L, -1, 3)
        transl = savgol_filter(transl, window, 2, axis=0)
    rot = lambda: (
        [Rotation.identity(L)]
        + [Rotation.from_rotvec(rotvec[:, j]) for j in range(rotvec.shape[1])]
    )
    pos = fk(rot(), transl, names, parents, offsets)
    feet = [i for i, n in enumerate(names) if n.endswith(("Foot", "ToeBase"))]
    transl[:, 1] -= np.percentile(pos[:, feet, 1].min(1), 5)  # the floor at y = 0
    toe = sum(
        pos[0, names.index(f"{s}ToeBase")] - pos[0, names.index(f"{s}Foot")]
        for s in ("Left", "Right")
    )
    yaw = Rotation.from_rotvec([0, -np.arctan2(toe[0], toe[2]), 0])
    rotvec[:, 0] = (yaw * Rotation.from_rotvec(rotvec[:, 0])).as_rotvec()  # joint 1 = Hips
    transl = yaw.apply(transl - transl[0] * [1, 0, 1])
    info = {"turned_deg": round(float(np.degrees(np.arctan2(toe[0], toe[2]))), 1)}
    pos = fk(rot(), transl, names, parents, offsets)
    info["foot_slide_mps_before_lock"] = quality(pos, names)["foot_slide_mps"]
    if p["foot_lock"]:
        shift, held = np.zeros((L, 3)), 0
        ft = [names.index(n) for n in ("LeftToeBase", "RightToeBase")]
        h = pos[:, ft, 1]
        v = np.r_[
            np.zeros((1, 2)),
            np.linalg.norm(np.diff(pos[:, ft][..., [0, 2]], axis=0), axis=-1) * FPS,
        ]
        down = (h < np.percentile(h, 10, axis=0) + 0.03) & (v < 0.5)
        for t in range(1, L):
            d = [
                pos[t, f, [0, 2]] - pos[t - 1, f, [0, 2]]
                for k, f in enumerate(ft)
                if down[t, k] and down[t - 1, k]
            ]
            step = np.mean(d, 0) if d else np.zeros(2)
            shift[t] = shift[t - 1] - [step[0], 0, step[1]]
            held += bool(d)
        transl += shift
        info["foot_locked_frames"] = held
    if p["in_place"]:
        transl[:, [0, 2]] = 0
    return rotvec, transl, info


def bvh_text(rotvec, transl, names, parents, offsets) -> str:
    """The BVH: offsets in world axes from the bind pose (the rest skeleton upright, standing on the floor),
    each joint's channels its body_pose rotation (the same posed positions as GEM-X's own export, whose
    offsets in SOMA's joint-orient frames make a rest that lies along X: research 12), centimetres, 30 fps."""
    from scipy.spatial.transform import Rotation

    off = np.array([pos - offsets[p] if p >= 0 else pos for pos, p in zip(offsets, parents)], float)
    hips = names.index("Hips")
    feet = [i for i, n in enumerate(names) if n.endswith(("Foot", "ToeBase"))]
    off[hips] = [0.0, offsets[hips, 1] - offsets[feet, 1].min(), 0.0]
    kids = [[] for _ in names]
    for j, p in enumerate(parents):
        if p >= 0:
            kids[p].append(j)
    eul = [None] + [
        Rotation.from_rotvec(rotvec[:, j]).as_euler("ZYX", degrees=True)
        for j in range(rotvec.shape[1])
    ]
    lines, order = ["HIERARCHY"], []

    def joint(j, depth):
        ind = "  " * depth
        order.append(j)
        lines.append(f"{ind}{'ROOT' if parents[j] < 0 else 'JOINT'} {names[j]}")
        lines.append(f"{ind}{{")
        o = off[j] * 100
        lines.append(f"{ind}  OFFSET {o[0]:.5f} {o[1]:.5f} {o[2]:.5f}")
        six = parents[j] < 0 or j == hips
        lines.append(
            f"{ind}  CHANNELS {'6 Xposition Yposition Zposition' if six else '3'} Zrotation Yrotation Xrotation"
        )
        for c in kids[j]:
            joint(c, depth + 1)
        if not kids[j]:
            lines.extend([f"{ind}  End Site", f"{ind}  {{", f"{ind}    OFFSET 0 0 0", f"{ind}  }}"])
        lines.append(f"{ind}}}")

    joint(0, 0)
    lines += ["MOTION", f"Frames: {len(transl)}", f"Frame Time: {1 / FPS:.6f}"]
    for t in range(len(transl)):
        v = []
        for j in order:
            if parents[j] < 0:
                v += [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
                continue
            if j == hips:
                v += list(transl[t] * 100)
            v += list(eul[j][t])
        lines.append(" ".join(f"{x:.4f}" for x in v))
    return "\n".join(lines) + "\n"


def quality(pos: np.ndarray, names: list[str]) -> dict:
    """Foot slide while a foot is down (m/s, median) and jitter (the root's mean second difference, mm)."""
    ft = [names.index(n) for n in ("LeftToeBase", "RightToeBase")]
    h = pos[:, ft, 1]
    v = np.linalg.norm(np.diff(pos[:, ft][..., [0, 2]], axis=0), axis=-1) * FPS
    down = (h[1:] < np.percentile(h, 10, axis=0) + 0.03) & (
        h[:-1] < np.percentile(h, 10, axis=0) + 0.03
    )
    hips = pos[:, names.index("Hips")]
    return {
        "foot_slide_mps": round(float(np.median(v[down])), 3) if down.any() else None,
        "root_jitter_mm": round(
            float(np.linalg.norm(np.diff(hips, 2, axis=0), axis=1).mean() * 1000), 2
        ),
    }


# --- the step ----------------------------------------------------------------------------------------


def taken_names(asset: Path) -> set:
    """The clip names in use: the asset's motions (their clip.glb's first animation) and the clip pack's
    (anim picks clips by name)."""
    from steps.anim import catalogue, clip_names

    motions = {
        n
        for d in asset.glob("*_mo*")
        if (d / "clip.glb").is_file()
        for n in clip_names(d / "clip.glb")[:1]
    }
    return motions | {c["name"] for c in catalogue()}


def run(op, in_dir, out_dir, params):
    p = params_of(params)
    if not in_dir:
        raise StepError("missing_input", "mocap needs a video version (Animate: Motion from video)")
    src, out = Path(in_dir).resolve(), Path(out_dir).resolve()  # before the engine's chdir
    video = next((f for f in sorted(src.iterdir()) if f.suffix.lower() in VIDEO), None)
    if video is None:
        raise StepError("missing_input", f"{src.name} has no video ({', '.join(VIDEO)})")
    _check_models(p["mode"])
    meta = (
        json.loads((src / "meta.json").read_text(encoding="utf-8"))
        if (src / "meta.json").is_file()
        else {}
    )
    upload = (meta.get("params") or {}).get("name") or video.name  # the user's file name
    name = p["name"] or Path(upload).stem[:48] or "video motion"
    if name in taken_names(out.parent):
        name = f"{name} {out.name[:3]}"
    t0 = time.time()
    work = out / "_work"
    work.mkdir(parents=True, exist_ok=True)
    progress(0.05, "preparing the clip")
    frames, w, h = prepare_video(video, work / "clip.mp4", p["start"], p["end"])
    pred, kp2d, boxes, peak, people = gem(
        work / "clip.mp4", p["mode"], p["person"], work, p["camera"]
    )
    t_gem = time.time() - t0

    progress(0.8, "cleanup and the BVH")
    bp = {k: np.asarray(v, dtype=np.float64) for k, v in pred["body_params_global"].items()}
    names, parents, offsets = skeleton(bp["identity_coeffs"], bp["scale_params"])
    L = len(bp["transl"])
    rotvec = np.concatenate([bp["global_orient"][:, None], bp["body_pose"].reshape(L, -1, 3)], 1)
    rotvec, transl, info = clean(rotvec, bp["transl"], names, parents, offsets, p)
    (out / "motion.bvh").write_text(
        bvh_text(rotvec, transl, names, parents, offsets), encoding="utf-8"
    )
    from scipy.spatial.transform import Rotation

    rot = [Rotation.identity(L)] + [
        Rotation.from_rotvec(rotvec[:, j]) for j in range(rotvec.shape[1])
    ]
    q = quality(fk(rot, transl, names, parents, offsets), names)

    progress(0.9, "the clip for the rig")
    try:
        res = run_blender(
            "bvh_clip", [out / "motion.bvh"], out / "_clip", {"name": name, "fps": FPS}, timeout=300
        )
    except BlenderError as e:
        raise StepError("failed", str(e)) from e
    shutil.move(str(out / "_clip" / "clip.glb"), str(out / "clip.glb"))
    shutil.rmtree(out / "_clip", ignore_errors=True)
    preview(work / "clip.mp4", kp2d, boxes, out / "preview.mp4")
    shutil.rmtree(work, ignore_errors=True)
    return {
        "outputs": {"clip": "clip.glb", "bvh": "motion.bvh", "preview": "preview.mp4"},
        "metrics": {
            "name": name,
            "mode": p["mode"],
            "camera": p["camera"],
            "person": p["person"],
            "people": people,
            "frames": frames,
            "seconds": round(frames / FPS, 2),
            "size": [w, h],
            "gem_seconds": round(t_gem, 1),
            "total_seconds": round(time.time() - t0, 1),
            "peak_vram_gb": peak,
            "bones": res.get("bones"),
            **info,
            **q,
        },
        "models": [{"id": "nvidia/GEM-X"}],
        "licenses": LICENSES[p["mode"]],
    }


def preview(video: Path, kp2d: np.ndarray, boxes: np.ndarray, dst: Path) -> None:
    """The clip with the person's box and its 2D keypoints (confidence > 0.5), for the Animate panel."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(str(dst), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (w, h))
    for t in range(len(kp2d)):
        ok, frame = cap.read()
        if not ok:
            break
        x0, y0, x1, y1 = boxes[t].astype(int)
        cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 200, 255), 2)
        for x, y, c in kp2d[t]:
            if c > 0.5:
                cv2.circle(frame, (int(x), int(y)), 3, (60, 220, 60), -1)
        writer.write(frame)
    cap.release()
    writer.release()


if __name__ == "__main__":
    main(run)
