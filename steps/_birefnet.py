"""BiRefNet matte (MIT, pinned revision): RGB → alpha in 0..1. Only steps that matte import this (torch)."""

import numpy as np
import torch
import torch.nn.functional as F

MODEL = "ZhengPeng7/BiRefNet"
REVISION = "e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4"  # trust_remote_code runs this exact code
SIZE = 1024
MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)


def load(device: str | None = None):
    from transformers import AutoModelForImageSegmentation

    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForImageSegmentation.from_pretrained(
        MODEL, revision=REVISION, trust_remote_code=True
    )
    # fp16 on the GPU (half the VRAM); fp32 on the CPU, where fp16 convs are slow or missing.
    # AMD: fp32 on ROCm too: in fp16 the matte comes back all zeros (gfx1201, torch 2.9.1+rocm7.2.1)
    half = device == "cuda" and not torch.version.hip
    return model.to(device, torch.float16 if half else torch.float32).eval()


@torch.no_grad()
def matte(model, rgb: np.ndarray) -> np.ndarray:
    p = next(model.parameters())
    x = torch.tensor(rgb).permute(2, 0, 1)[None].float() / 255  # a copy: inputs may be read-only
    x = F.interpolate(x, (SIZE, SIZE), mode="bilinear", antialias=True, align_corners=False)
    pred = model(((x - MEAN) / STD).to(p.device, p.dtype))[-1].sigmoid().float()
    pred = F.interpolate(pred, rgb.shape[:2], mode="bilinear", align_corners=False)
    return pred[0, 0].clamp(0, 1).cpu().numpy()
