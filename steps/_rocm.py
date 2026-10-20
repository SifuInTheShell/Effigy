"""AMD ROCm on Windows (torch 2.9.1+rocm7.2.1, RX 9070 XT gfx1201) workarounds for every step.

The GEMMs behind matmul / F.linear return wrong numbers once the left operand has more than 2**19 rows
(fp32; 2**20-2**22 for fp16/bf16), with hipBLASLt and rocBLAS alike: TRELLIS's per-voxel layers run over
3-4M voxels, so the decoders put out garbage and NaN (the mesh vertices, the texture). Above ROWS rows the
product is computed a block of rows at a time. Measured in docs/bench/amd-rocm.md; no-op on CUDA and CPU.
"""

import functools
import importlib.abc
import importlib.util
import sys

ROWS = 2**18  # half the smallest failing size measured


def install() -> None:
    """Patch torch now if it is imported, else right after its import: steps without torch (validate,
    export, _echo in the backend's venv) never pay for importing it."""
    if "torch" in sys.modules:
        _patch()
    elif not any(isinstance(f, _AfterTorch) for f in sys.meta_path):
        sys.meta_path.insert(0, _AfterTorch())


class _AfterTorch(importlib.abc.MetaPathFinder):
    """One-shot: torch's own spec, its loader running `_patch` once the module has executed."""

    def find_spec(self, name, path=None, target=None):
        if name != "torch":
            return None
        sys.meta_path.remove(self)
        spec = importlib.util.find_spec("torch")
        if spec is not None and spec.loader is not None:
            run = spec.loader.exec_module

            def exec_module(module):
                run(module)
                _patch()

            spec.loader.exec_module = exec_module
        return spec


def _patch() -> None:
    import torch

    if not torch.version.hip or getattr(torch.matmul, "effigy_rows", False):
        return
    F = torch.nn.functional
    F.linear = _rows(F.linear, "linear")
    torch.matmul = _rows(torch.matmul, "matmul")
    torch.mm = _rows(torch.mm, "mm")
    torch.addmm = _rows(torch.addmm, "addmm")
    torch.Tensor.__matmul__ = _rows(torch.Tensor.__matmul__, "matmul")
    torch.Tensor.matmul = _rows(torch.Tensor.matmul, "matmul")
    torch.Tensor.mm = _rows(torch.Tensor.mm, "mm")


def _rows(fn, kind: str):
    import torch

    @functools.wraps(fn)
    def inner(*args, **kwargs):
        # only the plain positional calls are split; anything else (input=, out=, beta=) goes through
        if kind == "addmm":
            if not (len(args) == 3 and not kwargs and _big(args[1]) and args[2].dim() == 2):
                return fn(*args, **kwargs)
            bias, a, b = args
            rows = bias.dim() == 2 and bias.shape[0] == a.shape[0]
            return torch.cat(
                [
                    fn(bias[s : s + ROWS] if rows else bias, a[s : s + ROWS], b)
                    for s in range(0, a.shape[0], ROWS)
                ]
            )
        if kind == "linear":  # linear(input, weight, bias=None): input [..., K]
            ok = 2 <= len(args) <= 3 and not kwargs.keys() - {"bias"}
        else:  # matmul / mm: [..., K] times one 2-D matrix
            ok = (
                len(args) == 2
                and not kwargs
                and isinstance(args[1], torch.Tensor)
                and args[1].dim() == 2
            )
        if not (ok and _big(args[0])):
            return fn(*args, **kwargs)
        a, b, rest = args[0], args[1], args[2:]
        flat = a.reshape(-1, a.shape[-1])
        out = torch.cat(
            [fn(flat[s : s + ROWS], b, *rest, **kwargs) for s in range(0, flat.shape[0], ROWS)]
        )
        return out.reshape(*a.shape[:-1], out.shape[-1])

    inner.effigy_rows = True
    return inner


def _big(t) -> bool:
    import torch

    return (
        isinstance(t, torch.Tensor) and t.is_cuda and t.dim() >= 2 and t.shape[:-1].numel() > ROWS
    )
