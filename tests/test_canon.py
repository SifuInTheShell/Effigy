"""M16 canon step plumbing with the edit model, matting and DWPose stubbed (CPU)."""

import json

import numpy as np
import pytest

torch = pytest.importorskip("torch")
from PIL import Image

from steps import _pose, canon
from steps._common import StepError


def test_canon_writes_the_image_and_its_gate(tmp_path, monkeypatch):
    src, out = tmp_path / "020_prep", tmp_path / "030_canon"
    src.mkdir()
    out.mkdir()
    rgba = np.zeros((64, 64, 4), np.uint8)
    rgba[16:48, 24:40] = (200, 50, 50, 255)
    Image.fromarray(rgba, "RGBA").save(src / "image.png")
    seen = {}

    class Pipe:
        def __call__(self, image, prompt, num_inference_steps, generator, **extra):
            seen.update(n=len(image), steps=num_inference_steps, extra=extra, prompt=prompt)
            return type("R", (), {"images": [image[0]]})

    monkeypatch.setattr(canon, "_load", lambda off=False: Pipe())
    monkeypatch.setattr(canon, "REFERENCE", src / "image.png")
    monkeypatch.setattr(canon._birefnet, "load", lambda: None)
    monkeypatch.setattr(canon._birefnet, "matte", lambda m, rgb: np.ones(rgb.shape[:2]))
    monkeypatch.setattr(_pose, "load", lambda: None)
    monkeypatch.setattr(_pose, "keypoints", lambda s, rgb, a: (np.zeros((133, 2)), np.zeros(133)))

    res = canon.run("run", str(src), str(out), {"seed": 3})
    assert "image 1, but standing" in seen.pop("prompt")
    assert seen == {
        "n": 2,
        "steps": 4,
        "extra": {"guidance_scale": 1.0},
    }  # character + A-pose reference
    assert res["outputs"] == {"image": "image.png", "gate": "gate.json"}
    assert (out / "image.png").is_file() and res["metrics"]["pose"] == "unsure"
    assert json.loads((out / "gate.json").read_text())["ok"] is False
    assert "black-forest-labs/FLUX.2-klein-4B" in res["licenses"]
    with pytest.raises(StepError):
        canon.run("run", str(tmp_path), str(out), {})  # no prep image

    # a flat picture (pixel art) is redrawn as a shaded 3D figure, or its model is a cardboard standee
    canon.run("run", str(src), str(out), {"seed": 3, "look": "render"})
    assert "image 1, drawn as a smooth, softly lit 3D game character render" in seen["prompt"]
    with pytest.raises(StepError, match="look"):
        canon.run("run", str(src), str(out), {"look": "photo"})
