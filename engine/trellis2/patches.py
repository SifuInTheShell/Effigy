"""Runtime patches for the vendored TRELLIS.2 (each one has a row in PATCHES.md).

The vendored tree (`engine/trellis2/trellis2/`) stays byte-identical to upstream; everything we change
is applied here, at import time, so re-pulling upstream never loses a fix. Entry point: `prepare()`.
"""

import contextlib
import importlib.machinery
import os
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent


def prepare(cache_dir: str | os.PathLike | None = None):
    """Make `import trellis2` work on our stack and return the package. Idempotent.

    cache_dir: writable folder for FlexGEMM's autotune cache (default: EFFIGY_MODELS).
    """
    install_nvdiffrast_decoy()
    install_attention_shims()
    _flex_gemm_cache(cache_dir or os.environ.get("EFFIGY_MODELS", HERE))
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import trellis2

    replace_rembg()
    fix_dinov3_features()
    skip_random_init(trellis2.models)
    explicit_gemm_on_rocm("trellis2")
    return trellis2


def _module(name: str, package: bool = False) -> types.ModuleType:
    """A synthetic module with a real __spec__ (find_spec() raises on a module whose __spec__ is None)."""
    m = types.ModuleType(name)
    m.__spec__ = importlib.machinery.ModuleSpec(name, None, is_package=package)
    if package:
        m.__path__ = []
    return m


# --- 1. nvdiffrast decoy ----------------------------------------------------------------------


def install_nvdiffrast_decoy() -> None:
    """Let `import o_voxel` work without nvdiffrast (NVIDIA licence: non-commercial).

    o_voxel/postprocess.py imports nvdiffrast at module level but only calls it inside to_glb,
    which we replace (steps/_bake.py). Call before the first `import o_voxel`.
    """
    if "nvdiffrast" in sys.modules:
        return

    def refuse(name):
        if name.startswith(
            "__"
        ):  # inspect/hasattr probe dunders on every module: stay a plain module
            raise AttributeError(name)
        raise ImportError(
            f"nvdiffrast.{name} called: nvdiffrast is excluded (non-commercial licence, PLAN §6); "
            "use the pure-torch UV rasterizer (steps/_bake.py, engine/trellis2/PATCHES.md)"
        )

    root, torch_mod = _module("nvdiffrast", package=True), _module("nvdiffrast.torch")
    torch_mod.__getattr__ = refuse  # PEP 562: any attribute access fails loudly
    root.torch = torch_mod
    sys.modules.update({"nvdiffrast": root, "nvdiffrast.torch": torch_mod})


# --- 2. attention: no xformers, no flash-attn (neither works on sm_120 Windows) ----------------


class BlockDiagonalMask:
    """Sequence lengths of packed variable-length batches; never materialised as a tensor."""

    def __init__(self, q_seqlen, kv_seqlen=None):
        self.q_seqlen = [int(n) for n in q_seqlen]
        self.kv_seqlen = [int(n) for n in (q_seqlen if kv_seqlen is None else kv_seqlen)]
        if len(self.q_seqlen) != len(self.kv_seqlen):
            raise ValueError(
                f"{len(self.q_seqlen)} q sequences vs {len(self.kv_seqlen)} kv sequences"
            )

    @classmethod
    def from_seqlens(cls, q_seqlen, kv_seqlen=None):
        def as_list(x):  # windowed attention passes a tensor of thousands of window sizes
            return x.tolist() if hasattr(x, "tolist") else list(x)

        return cls(as_list(q_seqlen), None if kv_seqlen is None else as_list(kv_seqlen))


def memory_efficient_attention(query, key, value, attn_bias=None, p: float = 0.0, scale=None, **_):
    """xformers.ops.memory_efficient_attention on torch SDPA. q/k/v: [B, T, H, C].

    With a BlockDiagonalMask (B == 1, sequences packed along T) attention stays inside each sequence:
    sequences of equal (q_len, kv_len) run as one batched SDPA call, so windowed attention (thousands of
    same-size windows) is one or two calls and no [T, T] mask is ever built.
    """
    import torch
    import torch.nn.functional as F

    def sdpa(q, k, v):  # [B, L, H, C] in and out
        out = F.scaled_dot_product_attention(
            q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2), dropout_p=p, scale=scale
        )
        return out.transpose(1, 2)

    if attn_bias is None:
        return sdpa(query, key, value)
    if query.shape[0] != 1:
        raise ValueError("packed variable-length attention expects batch size 1")
    q, k, v = query[0], key[0], value[0]
    lq, lkv = attn_bias.q_seqlen, attn_bias.kv_seqlen
    if sum(lq) != q.shape[0] or sum(lkv) != k.shape[0]:
        raise ValueError(
            f"sequence lengths cover {sum(lq)}/{sum(lkv)} tokens, tensors have {q.shape[0]}/{k.shape[0]}"
        )
    if (
        len(lq) == 1
    ):  # one sequence (cross-attention to a handful of tokens): plain SDPA, no index building,
        return sdpa(
            query, key, value
        )  # which cost 6× the attention itself (code review 2026-10-05)
    q_start = torch.tensor([0, *lq], device=q.device).cumsum(0)[:-1]
    kv_start = torch.tensor([0, *lkv], device=q.device).cumsum(0)[:-1]
    groups: dict[tuple[int, int], list[int]] = {}
    for i, shape in enumerate(zip(lq, lkv)):
        groups.setdefault(shape, []).append(i)
    out = torch.empty(q.shape[0], *q.shape[1:-1], v.shape[-1], dtype=q.dtype, device=q.device)
    for (nq, nkv), members in groups.items():
        idx = torch.tensor(members, device=q.device)
        qi = q_start[idx, None] + torch.arange(nq, device=q.device)  # [G, nq] token rows
        ki = kv_start[idx, None] + torch.arange(nkv, device=q.device)
        out[qi] = sdpa(q[qi], k[ki], v[ki]).to(out.dtype)
    return out[None]


def install_attention_shims() -> None:
    """Dense attention: upstream's own 'sdpa' backend. Sparse (varlen) attention only knows xformers /
    flash-attn, so `xformers.ops` resolves to the two functions above. Must run before `import trellis2`
    (its config modules read the env at import)."""
    os.environ["ATTN_BACKEND"] = "sdpa"
    os.environ["SPARSE_ATTN_BACKEND"] = "xformers"
    if getattr(sys.modules.get("xformers.ops"), "effigy_sdpa", False):
        return
    fmha = _module("xformers.ops.fmha")
    fmha.BlockDiagonalMask = BlockDiagonalMask
    ops = _module("xformers.ops", package=True)
    ops.memory_efficient_attention, ops.fmha, ops.effigy_sdpa = (
        memory_efficient_attention,
        fmha,
        True,
    )
    root = _module("xformers", package=True)
    root.ops = ops
    sys.modules.update({"xformers": root, "xformers.ops": ops, "xformers.ops.fmha": fmha})


# --- 3. no RMBG-2.0 (non-commercial weights) ----------------------------------------------------


class NoRembg:
    """pipeline.json names briaai/RMBG-2.0; upstream builds it in from_pretrained. We never need it:
    steps.prep hands TRELLIS a BiRefNet RGBA cut-out, and upstream skips background removal on RGBA."""

    def __init__(self, *args, **kwargs):
        pass

    def to(self, *args, **kwargs):
        return self

    cuda = cpu = to

    def __call__(self, image):
        raise RuntimeError(
            "background removal belongs to steps.prep (BiRefNet): pass its RGBA image"
        )


def replace_rembg() -> None:
    from trellis2.pipelines import rembg

    rembg.BiRefNet = NoRembg


# --- 4. DINOv3 on transformers 5 ---------------------------------------------------------------


def _dinov3_blocks(model):
    """transformers 5 moved the blocks from DINOv3ViTModel.layer to .model.layer."""
    for holder in (model, getattr(model, "model", None), getattr(model, "encoder", None)):
        if holder is not None and hasattr(holder, "layer"):
            return holder.layer
    raise AttributeError(f"no transformer blocks found on {type(model).__name__}")


def dinov3_features(self, image):
    """Upstream's extract_features iterates `model.layer`, which transformers 5 no longer has. Same
    computation, blocks found where they live now. Not `last_hidden_state`: that applies DINOv3's learned
    final norm, and TRELLIS.2 was trained on a parameter-free layer norm (PR #185: empty grids 7/40 → 0/40).
    Pixal3D's proj extractor has the identical method (engine/pixal3d/patches.py)."""
    import torch.nn.functional as F

    image = image.to(self.model.embeddings.patch_embeddings.weight.dtype)
    hidden = self.model.embeddings(image, bool_masked_pos=None)
    rope = self.model.rope_embeddings(image)
    for block in _dinov3_blocks(self.model):
        hidden = block(hidden, position_embeddings=rope)
        if isinstance(hidden, tuple):
            hidden = hidden[0]
    return F.layer_norm(hidden, hidden.shape[-1:])


def fix_dinov3_features() -> None:
    from trellis2.modules import image_feature_extractor as ife

    ife.DinoV3FeatureExtractor.extract_features = dinov3_features


# --- 5. FlexGEMM autotune cache -----------------------------------------------------------------


def _flex_gemm_cache(cache_dir) -> None:
    """FlexGEMM's tuned-kernel cache defaults to ~/.flex_gemm; keep it with the models. Its shipped cache
    only covers A100/MI300X, so it tunes on the first run on the laptop and saves the result here."""
    os.environ.setdefault(
        "FLEX_GEMM_AUTOTUNE_CACHE_PATH", str(Path(cache_dir) / "flex_gemm_autotune_cache.json")
    )


# --- 17. AMD/ROCm: FlexGEMM's explicit GEMM ---------------------------------------------------


def explicit_gemm_on_rocm(package: str) -> None:
    """AMD: on a ROCm torch (no Triton on Windows + AMD) FlexGEMM's implicit-GEMM kernels, which are
    Triton, cannot run; its EXPLICIT_GEMM path is gather + torch matmul. No-op on CUDA."""
    import importlib

    import torch

    # read by every sparse conv's forward, so setting it after import is enough
    if torch.version.hip:
        importlib.import_module(
            f"{package}.modules.sparse.conv.config"
        ).FLEX_GEMM_ALGO = "explicit_gemm"
        chunk_explicit_gemm()


IM2COL_CHUNK = 256 * 2**20  # bytes of gathered neighbour features per matmul


def submconv_explicit_chunked(feats, neighbor_cache, weight, bias=None):
    """FlexGEMM's explicit submanifold conv, a block of rows at a time: upstream gathers all N x 27 neighbour
    features first (2.9 GB at the mesh decoder's resolution: the Fast anvil OOMed at 10.9 GB on the
    9070 XT, peak 4.4 GB on the NVIDIA laptop's implicit GEMM). Same matmul per row, so the same result."""
    import torch

    n, (co, kw, kh, kd, ci) = feats.shape[0], weight.shape
    v = kw * kh * kd
    pad = torch.cat([feats, feats.new_zeros(1, ci)])  # row n: the missing neighbour (0xffffffff)
    nmap = neighbor_cache["neighbor_map"]
    w = weight.reshape(co, v * ci).t()
    out = feats.new_empty(n, co)
    # at most 2**18 rows too: ROCm's GEMMs go wrong above 2**19 (steps/_rocm.py)
    rows = max(1, min(2**18, IM2COL_CHUNK // (v * ci * feats.element_size())))
    for s in range(0, n, rows):
        idx = nmap[s : s + rows].long()
        idx[idx == 0xFFFFFFFF] = n
        col = pad[idx.view(-1)].view(idx.shape[0], v * ci)
        out[s : s + rows] = col @ w if bias is None else torch.addmm(bias, col, w)
    return out


def chunk_explicit_gemm() -> None:
    """Route FlexGEMM's explicit forward through `submconv_explicit_chunked` (inference; backward untouched)."""
    from flex_gemm.ops import spconv
    from flex_gemm.ops.spconv.submanifold_conv3d import SubMConv3dFunction as F

    if getattr(F._sparse_submanifold_conv_forward, "effigy_chunked", False):
        return
    upstream = F._sparse_submanifold_conv_forward

    def forward(feats, neighbor_cache, weight, bias=None):
        if spconv.ALGORITHM != spconv.Algorithm.EXPLICIT_GEMM:
            return upstream(feats, neighbor_cache, weight, bias)
        return submconv_explicit_chunked(feats, neighbor_cache, weight, bias)

    forward.effigy_chunked = True
    F._sparse_submanifold_conv_forward = staticmethod(forward)


# --- 13. no random init before load_state_dict ----------------------------------------------------


@contextlib.contextmanager
def no_random_init():
    """torch.nn.init's in-place initialisers become no-ops (Linear/Conv/LayerNorm reset through them)."""
    import torch

    names = [n for n in dir(torch.nn.init) if n.endswith("_") and not n.startswith("_")]
    saved = {n: getattr(torch.nn.init, n) for n in names}
    for n in names:
        setattr(torch.nn.init, n, lambda tensor, *_, **__: tensor)
    try:
        yield
    finally:
        for n, f in saved.items():
            setattr(torch.nn.init, n, f)


def skip_random_init(models_module) -> None:
    """Upstream builds each model in fp32 with random init, then loads every weight over it (strict=False;
    only the computed `rope_phases` buffer is absent from the checkpoints): ~11 s per 1.3B model wasted."""
    if getattr(models_module.from_pretrained, "effigy_patched", False):
        return
    load = models_module.from_pretrained

    def from_pretrained(path, **kwargs):
        with no_random_init():
            return load(path, **kwargs)

    from_pretrained.effigy_patched = True
    models_module.from_pretrained = from_pretrained
