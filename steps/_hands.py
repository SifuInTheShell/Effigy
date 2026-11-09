"""Finger joints from close-up renders of each hand (M70).

DWPose reads the hands off one full-body render, where a hand is about 50 px wide: on the M22 bench its finger
points came out swapped or scattered on about half the characters. Here each hand is rendered on its own: the
faces along its axis (not the thigh beside it), in a plain skin tone lit from the camera on near-white (MediaPipe's hand model learned from
photographs; the Mesh2Motion mannequin's striped texture hid its hand from it, the plain tone did not), from VIEWS
directions round the forearm, at SIZE px. MediaPipe's Hand Landmarker (Apache-2.0, on the CPU) reads its 21 points
(COCO-WholeBody's hand order, as DWPose's) in each view where it finds a hand; each point is the least-squares meeting
of its views' rays (orthographic: parallel rays per view, so two views at an angle fix a point; two opposite views
do not), a view whose rays miss the others by more than MISS of the forearm dropped. Two views at least 30° apart,
the wrist near the fitted wrist and a hand of a hand's length, or nothing (rig_fit then keeps DWPose's points or
the proportions).
"""

import math
import os
from pathlib import Path

import numpy as np

VIEWS = 8  # directions round the forearm, every 45°: an edge-on hand reads as none, the palm and back do
SIZE = 512
CONFIDENCE = 0.5  # MediaPipe's own hand detection and presence thresholds (its defaults)
MISS = 0.12  # of the forearm's length: a view whose rays pass further from the points is dropped
# under EFFIGY_MODELS (tools/download_models.py, group pose)
MODEL = "MediaPipe/hand_landmarker.task"
CREDIT = "google/mediapipe-hand-landmarker"  # its licenses.yaml row
SKIN = (224, 172, 140)


def found(v, f, markers, hands, detect) -> tuple[dict, dict, dict]:
    """rig_fit's hands (M70): per side MediaPipe's points where it places the hand, else DWPose's kept, else none
    (the proportions); a failure here falls back the same way, its reason in the side's `why`. Returns (hands,
    by side the source, by side what the views saw)."""
    hands, by, seen = dict(hands), {}, {}
    for s in ("Left", "Right"):
        if detect is not None:
            try:
                pts, seen[s] = points(v, f, markers[f"{s}Wrist"], markers[f"{s}Elbow"], detect)
            except Exception as e:  # noqa: BLE001 - never lose the fit to this: DWPose's points stand
                pts, seen[s] = None, {"why": f"{type(e).__name__}: {e}"}
            if pts is not None:
                hands[s], by[s] = pts, "mediapipe"
                continue
        by[s] = "dwpose" if s in hands else "proportions"
    return hands, by, seen


def finder():
    """MediaPipe's hand landmarker as detect(rgb uint8 [H, W, 3]) → [21, 2] in 0–1, or None where it finds no hand
    (its detection and presence confidences under CONFIDENCE); None when its model is not downloaded. Its handedness
    is not used: the side is the forearm the views go round."""
    root = Path(os.environ.get("EFFIGY_MODELS", Path(__file__).resolve().parents[1] / "models"))
    if not (root / MODEL).is_file():
        return None
    import mediapipe as mp
    from mediapipe.tasks import python as mpt
    from mediapipe.tasks.python import vision

    lm = vision.HandLandmarker.create_from_options(
        vision.HandLandmarkerOptions(
            base_options=mpt.BaseOptions(model_asset_path=str(root / MODEL)),
            num_hands=1,
            min_hand_detection_confidence=CONFIDENCE,
            min_hand_presence_confidence=CONFIDENCE,
        )
    )

    def detect(img):
        r = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(img)))
        if not r.hand_landmarks:
            return None
        return np.array([[q.x, q.y] for q in r.hand_landmarks[0]])

    detect.close = lm.close
    return detect


def views(wrist, elbow) -> tuple[np.ndarray, float, list[tuple[float, float]]]:
    """The hand's middle, the window's width and VIEWS (yaw, pitch) round the forearm: a hand is about three
    quarters of a forearm long, its middle 0.4 of a forearm past the wrist."""
    u = np.asarray(wrist, float) - elbow
    length = float(np.linalg.norm(u))
    u = u / max(length, 1e-9)
    a = np.cross(u, [0.0, 1.0, 0.0])
    a = a if np.linalg.norm(a) > 0.2 else np.cross(u, [1.0, 0.0, 0.0])
    a /= np.linalg.norm(a)
    b = np.cross(u, a)
    out = []
    for k in range(
        VIEWS
    ):  # half a step off: a level forearm's views above and below are not straight up or down
        t = 2 * math.pi * (k + 0.5) / VIEWS
        d = math.cos(t) * a + math.sin(t) * b
        if abs(d[1]) > 0.999:  # straight up or down: ortho has no roll to look there with
            continue
        out.append((math.degrees(math.atan2(d[0], d[2])) % 360, -math.degrees(math.asin(d[1]))))
    return np.asarray(wrist, float) + 0.4 * length * u, 1.6 * length, out


def shaded(v, f, cam, size: int = SIZE) -> np.ndarray:
    """The faces in a plain skin tone, lit from the camera, on near-white: uint8 [size, size, 3]."""
    import torch

    from steps._views import render

    fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    fn /= np.linalg.norm(fn, axis=1, keepdims=True).clip(1e-12)
    tex = torch.tensor(np.array(SKIN, np.float32)[None, None])
    uv = torch.zeros(len(v), 2)
    rgb, a, _, (face, _, _, _, _) = render(tex, uv, torch.tensor(f), v, cam, size, ss=1)
    face, rgb, a = face.numpy(), rgb.numpy(), a.numpy()[..., None]
    lit = np.ones(face.shape)
    lit[face >= 0] = 0.45 + 0.55 * np.abs(fn[face[face >= 0]] @ cam["towards"](np.zeros((1, 3)))[0])
    img = rgb * lit[..., None] * a + 0.95 * (1 - a)
    return (img * 255 + 0.5).clip(0, 255).astype(np.uint8)


def meet(origins, dirs) -> np.ndarray:
    """The point nearest to every ray (least squares)."""
    A, b = np.zeros((3, 3)), np.zeros(3)
    for o, d in zip(origins, dirs, strict=True):
        P = np.eye(3) - np.outer(d, d)
        A += P
        b += P @ o
    return np.linalg.lstsq(A, b, rcond=None)[0]


def points(v, f, wrist, elbow, detect, size: int = SIZE) -> tuple[np.ndarray | None, dict]:
    """The hand's 21 points in the library frame, or None, and what was seen ({"views", "used", "why"})."""
    from steps._views import ortho

    forearm = float(np.linalg.norm(np.asarray(wrist, float) - elbow))
    if forearm < 1e-6:
        return None, {"views": 0, "used": 0, "why": "no forearm (the wrist on the elbow)"}
    centre, ext, cams = views(wrist, elbow)
    # the hand's faces: centroids within 0.35 of a forearm of its axis (the wrist to 0.9 of a forearm past it), so
    # the thigh beside an A-posed hand stays out of the picture
    u = (np.asarray(wrist, float) - elbow) / forearm
    m = v[f].mean(1) - wrist
    t = (m @ u).clip(-0.1 * forearm, 0.9 * forearm)
    near = np.linalg.norm(m - t[:, None] * u, axis=1) < 0.35 * forearm
    rays = []
    for yaw, pitch in cams:
        cam = ortho(yaw, pitch, centre, ext)
        if (seen := detect(shaded(v, f[near], cam, size))) is not None:
            rays.append((cam["unproject"](seen), cam["towards"](np.zeros((1, 3)))[0]))
    info = {"views": len(cams), "used": len(rays)}
    while len(rays) >= 2:
        pts = np.array([meet([o[k] for o, _ in rays], [d for _, d in rays]) for k in range(21)])
        miss = [
            float(np.mean(np.linalg.norm(np.cross(pts - o, d), axis=1))) for o, d in rays
        ]  # each view's mean distance from the points to its rays
        worst = int(np.argmax(miss))
        if miss[worst] <= MISS * forearm:
            break
        rays.pop(worst)
    info["used"] = len(rays)
    if len(rays) < 2:
        return None, info | {"why": "fewer than two views saw the hand"}
    dirs = np.array([d for _, d in rays])
    if (
        np.abs(dirs @ dirs.T) > math.cos(math.radians(30))
    ).all():  # opposite views' rays are one line: no depth
        return None, info | {"why": "the views that saw it look along one line"}
    if np.linalg.norm(pts[0] - wrist) > 0.5 * forearm:
        return None, info | {"why": "its wrist is far from the fitted wrist"}
    if not 0.2 * forearm <= np.linalg.norm(pts[9] - pts[0]) <= 0.8 * forearm:
        return None, info | {"why": "not a hand's length"}
    return pts, info
