"""Runtime patches for the vendored Pixal3D and NAF (each one has a row in engine/trellis2/PATCHES.md).

Pixal3D is a TRELLIS.2 fork, so it takes the same patches (engine/trellis2/patches.py) plus its own: NAF
(the feature upsampler its proj conditioning uses) from the vendored copy with pinned weights, natten's
`na2d` on plain torch, one shared DINOv3. `pixal3d/` and `naf/` stay byte-identical to upstream.
"""

import os
import sys
import weakref
from pathlib import Path

from engine.trellis2 import patches as t2

HERE = Path(__file__).resolve().parent
NAF_WEIGHTS = "NAF/naf_release.pth"  # under EFFIGY_MODELS (tools/download_models.py)


def prepare(models_dir: str | os.PathLike | None = None):
    """Make `import pixal3d` work on our stack and return the package. Idempotent."""
    models_dir = Path(models_dir or os.environ.get("EFFIGY_MODELS", HERE))
    t2.install_nvdiffrast_decoy()
    t2.install_attention_shims()
    t2._flex_gemm_cache(models_dir)
    install_natten_shim()
    for p in (HERE, HERE / "naf"):  # naf/ holds NAF's own top-level `src` package
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import pixal3d
    from pixal3d.pipelines import rembg
    from pixal3d.trainers.flow_matching.mixins import image_conditioned_proj as proj

    t2.skip_random_init(pixal3d.models)
    t2.explicit_gemm_on_rocm("pixal3d")
    rembg.BiRefNet = t2.NoRembg
    proj.DINOv3ViTModel = SharedDinov3
    proj.DinoV3ProjFeatureExtractor.extract_features = t2.dinov3_features
    proj.DinoV3ProjFeatureExtractor._load_naf = _naf_loader(models_dir / NAF_WEIGHTS)
    from src.layers import attentions  # NAF's (naf/ is on sys.path now)

    if attentions.CrossAttention.forward is not naf_attention_chunked:
        naf_attention_chunked.upstream = attentions.CrossAttention.forward
        attentions.CrossAttention.forward = naf_attention_chunked
    if proj.DinoV3ProjMultiViewFeatureExtractor.forward is not mv_forward_lean:
        mv_forward_lean.upstream = proj.DinoV3ProjMultiViewFeatureExtractor.forward
        proj.DinoV3ProjMultiViewFeatureExtractor.forward = mv_forward_lean
    return pixal3d


# --- NAF's upsampling attention a slice of value channels at a time ------------------------------


def _taps(t, rows, cols, r0, r1):
    """Kernel taps of query rows r0:r1, gathered from a low-resolution [B, hk, wk, C] map through the taps'
    low-resolution indices: [B, r1 - r0, W, kh·kw, C]."""
    r = rows[r0:r1, :, None, None]  # [h, kh, 1, 1]; cols[None, None]: [1, 1, W, kw]
    return t[:, r, cols[None, None]].permute(0, 1, 3, 2, 4, 5).flatten(3, 4)


def naf_attention_chunked(
    self, q, k, v, image=None, return_weights=False, chunk: int = 64, **kwargs
):
    """NAF CrossAttention.forward, same result, a fraction of the memory and work. A head's softmax weights
    come from its queries and keys only: they are computed once per head (query rows a block at a time)
    and applied to its value channels `chunk` at a time. Keys and values are never upsampled: nearest-exact
    upsampling by an integer factor is `index // factor`, so the taps come straight from the low-resolution
    maps (NATTEN's border rule, `_window`, in full-resolution indices). Upstream holds the keys, the values
    and the output at full resolution: at 1024² with 1024 DINOv3 channels three 4 GiB buffers (the texture
    stage reserved 17.4 GB on 16). The M17 version chunked the values but still upsampled each chunk and
    recomputed the weights for every chunk of a head, 4× (code review 2026-10-05). The output feeds the
    projection directly; nothing else changes."""
    import torch
    from einops import rearrange
    from src.layers import attentions

    (hq, wq), (hk, wk) = q.shape[-2:], k.shape[-2:]
    if return_weights or not attentions.NATTEN_RECENT or hq % hk or wq % wk:
        return naf_attention_chunked.upstream(self, q, k, v, image, return_weights, **kwargs)
    dh, dw = self.dilation = (hq // hk, wq // wk)
    ks = self.kernel_size
    kh, kw = ks if isinstance(ks, (tuple, list)) else (ks, ks)
    n = self.num_heads
    q = rearrange(q, "b (n d) h w -> b h w n d", n=n)
    k = rearrange(k, "b (n d) h w -> b h w n d", n=n).to(q.dtype)
    v = v.permute(0, 2, 3, 1).to(q.dtype)  # [B, hk, wk, C]
    rows, cols = _window(hq, kh, dh, q.device) // dh, _window(wq, kw, dw, q.device) // dw
    B, C = v.shape[0], v.shape[-1]
    per_head, budget = C // n, 1 << 27  # elements gathered per block of rows
    out = q.new_empty(B, C, hq, wq)
    for j in range(n):
        qj, kj = q[..., j, :], k[..., j, :]
        w = q.new_empty(B, hq, wq, kh * kw)  # the head's softmax weights
        step = max(1, budget // (B * wq * kh * kw * qj.shape[-1]))
        for r0 in range(0, hq, step):
            r1 = min(r0 + step, hq)
            taps = _taps(kj, rows, cols, r0, r1).transpose(-1, -2)
            logits = torch.matmul(qj[:, r0:r1, :, None, :], taps)[..., 0, :]
            w[:, r0:r1] = torch.softmax(logits.float() * self.scale, -1).to(w.dtype)
        for c0 in range(j * per_head, (j + 1) * per_head, chunk):
            c1 = min(c0 + chunk, (j + 1) * per_head)
            step = max(1, budget // (B * wq * kh * kw * (c1 - c0)))
            for r0 in range(0, hq, step):
                r1 = min(r0 + step, hq)
                o = torch.matmul(w[:, r0:r1, :, None, :], _taps(v[..., c0:c1], rows, cols, r0, r1))
                out[:, c0:c1, r0:r1] = o[..., 0, :].permute(0, 3, 1, 2)
    return out


# --- four-view conditioning without a view-sized concat beside the running sum ------------------


def mv_forward_lean(
    self, image, camera_angle_x=None, distance=None, mesh_scale=None, transform_matrix=None
):
    """DinoV3ProjMultiViewFeatureExtractor.forward for "average" fusion, same result: each view's low- and
    high-res projections are added into their slices of the sum instead of concatenated first (upstream
    holds sum + [lr | hr] + lr + hr: at grid 64 that is 2 GiB more than one view, and the four-view run
    peaked at 15.5 GB on a 16 GB card and crawled in shared memory)."""
    import torch
    from pixal3d.trainers.flow_matching.mixins.image_conditioned_proj import (
        compute_relative_calc_mat,
    )

    if self.multiview_fusion != "average":
        return mv_forward_lean.upstream(
            self, image, camera_angle_x, distance, mesh_scale, transform_matrix
        )
    B, V = image.shape[:2]
    calc = compute_relative_calc_mat(
        transform_matrix, distance, self.proj_grid.front_view_transform_matrix
    )
    calc = calc.reshape(B * V, 4, 4)
    img = image.reshape(B * V, *image.shape[2:])
    with torch.no_grad():
        z = self.extract_features(self.transform(img))
        reg = getattr(self.model.config, "num_register_tokens", 4)
        tokens = z[:, 1 + reg :].reshape(B * V, self.patch_number, self.patch_number, -1)
        cam, dist = camera_angle_x.reshape(B * V), distance.reshape(B * V)
        scale = mesh_scale[:, None].expand(B, V).reshape(B * V)
        acc = None
        for v in range(V):
            i = torch.arange(v, B * V, V, device=z.device)
            cams = (cam[i], dist[i], scale[i], calc[i])
            lr = self.proj_grid(tokens[i], *cams)
            n = lr.shape[-1]
            if acc is None:
                acc = torch.empty(
                    *lr.shape[:-1],
                    n * (2 if self.use_naf_upsample else 1),
                    dtype=lr.dtype,
                    device=lr.device,
                )
                acc[..., :n] = lr
            else:
                acc[..., :n] += lr
            del lr
            if self.use_naf_upsample:
                self._load_naf()
                hr = self.naf_model(img[i], tokens[i].permute(0, 3, 1, 2), self.naf_target_size)
                hr = self.proj_grid(hr, *cams, BHWC=False)
                if v == 0:
                    acc[..., n:] = hr
                else:
                    acc[..., n:] += hr
                del hr
        z_global = torch.cat([z[:, 0:1], z[:, 1 : 1 + reg]], dim=1)
        z_global = z_global.reshape(B, V, *z_global.shape[1:]).mean(dim=1)
    return z_global, acc.div_(V)


# --- natten.na2d (NAF's cross-scale attention) on plain torch -----------------------------------


def _window(n: int, k: int, d: int, device):
    """[n, k] key indices per query index: k taps every d pixels, shifted inward at the borders. Same as
    NATTEN's get_window_start: each dilation group (i % d) is its own image of ceil((n - g) / d) pixels."""
    import torch

    if k * d > n:
        raise ValueError(f"kernel {k} × dilation {d} exceeds the input size {n}")
    i = torch.arange(n, device=device)
    g, p = i % d, i // d
    size = (n - g + d - 1) // d
    start = torch.minimum((p - k // 2).clamp(min=0), size - k)
    return g[:, None] + d * (start[:, None] + torch.arange(k, device=device))


def na2d(query, key, value, kernel_size, stride=1, dilation=1, is_causal=False, scale=None, **_):
    """natten.na2d (heads-last layout [B, H, W, heads, D]): every query attends to its kernel window.
    ponytail: gathers each window (chunked by rows) and runs SDPA on it, so memory is bounded but it reads
    every key kh·kw times; NAF calls it once per stage. A fused kernel only if it shows up in a profile."""
    import torch.nn.functional as F

    def pair(x):
        return (x, x) if isinstance(x, int) else tuple(x)

    (kh, kw), (dh, dw) = pair(kernel_size), pair(dilation)
    if pair(stride) != (1, 1) or is_causal:
        raise NotImplementedError("na2d stand-in: stride 1, non-causal only")
    B, H, W, N, _ = query.shape
    rows, cols = _window(H, kh, dh, query.device), _window(W, kw, dw, query.device)
    out = query.new_empty(B, H, W, N, value.shape[-1])
    per_row = B * W * kh * kw * N * (key.shape[-1] + value.shape[-1])
    step, c = max(1, (1 << 27) // per_row), cols[None, None]  # c: [1, 1, W, kw]
    for r0 in range(0, H, step):
        r = rows[r0 : r0 + step, :, None, None]  # [h, kh, 1, 1]
        # [B, h, kh, W, kw, N, C] → [B, h, W, N, kh·kw, C]
        kn, vn = (t[:, r, c].permute(0, 1, 3, 5, 2, 4, 6).flatten(4, 5) for t in (key, value))
        q = query[:, r0 : r0 + step, :, :, None]  # [B, h, W, N, 1, D]
        out[:, r0 : r0 + step] = F.scaled_dot_product_attention(q, kn, vn, scale=scale)[..., 0, :]
    return out


def install_natten_shim() -> None:
    """NAF tries `natten.functional` (old API) and falls back to `natten.na2d`; only the latter exists
    here. No natten wheel for sm_120 Windows is needed for the single-image path."""
    if getattr(sys.modules.get("natten"), "effigy_torch", False):
        return
    natten = t2._module("natten", package=True)
    natten.na2d, natten.effigy_torch = na2d, True
    sys.modules["natten"] = natten


# --- NAF from the vendored code + pinned weights (upstream: torch.hub from GitHub at run time) ---


def _naf_loader(weights: Path):
    def _load_naf(self):
        if self.naf_model is None:
            import torch
            from src.model.naf import NAF

            if not weights.is_file():
                raise FileNotFoundError(
                    f"{weights} missing: run `uv run --group ml python -m tools.download_models faithful`"
                )
            naf = NAF()
            naf.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))
            device = next(self.model.parameters()).device
            self.naf_model = naf.to(device).eval().requires_grad_(False)

    return _load_naf


# --- one DINOv3 for the four proj extractors ------------------------------------------------------


class SharedDinov3:
    """Stands in for DINOv3ViTModel inside image_conditioned_proj: the four stage extractors load the same
    frozen DINOv3-L, so they share one copy (saves ~3.6 GB of RAM next to ~24 GB of Pixal3D weights).
    Safe because the stages run one after another, each moving its extractor on and off the GPU."""

    _loaded: "weakref.WeakValueDictionary" = weakref.WeakValueDictionary()

    @classmethod
    def from_pretrained(cls, name, **kwargs):
        from transformers import DINOv3ViTModel

        model = cls._loaded.get(str(name))
        if model is None:
            model = cls._loaded[str(name)] = DINOv3ViTModel.from_pretrained(name, **kwargs)
        return model
