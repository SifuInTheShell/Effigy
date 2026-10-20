# sm_120 CUDA wheels

Built and committed by `.github/workflows/wheels.yml` (windows-2022, CUDA 12.8.1, MSVC 2022, Python 3.12,
torch 2.8.0+cu128, `TORCH_CUDA_ARCH_LIST="8.9;12.0"`). Do not edit by hand: to rebuild, change a pin in
the workflow, run it from the Actions tab (manual only) and pull the commit it pushes.

| Wheel | Source | Pinned commit | Patches | Licence |
|---|---|---|---|---|
| `cumesh` | JeffreyXiang/CuMesh (+ cubvh, xatlas submodules) | `12289e1` (has the Blackwell stream fix) | none | MIT |
| `flex_gemm` | JeffreyXiang/FlexGEMM | `6dd94a8` | `engine/trellis2/flex-gemm-windows.patch` | MIT |
| `o_voxel` | microsoft/TRELLIS.2 `o-voxel/` (+ Eigen submodule, MPL-2.0 headers) | `75fbf01` | `engine/trellis2/o-voxel-windows.patch` | MIT |

Why in git and not Release assets: the repo is private, and uv can't download private Release assets.
`pyproject.toml` points at these files with `marker = "sys_platform == 'win32'"`, so `uv.lock` pins them.

Current files: CI run 37159187583. Their dependencies come from PyPI through the lock; `triton-windows` is
pinned to 3.4 (the Triton that torch 2.8 is built against).

**`geosam2-0.1.0-py3-none-any.whl`** (M31) is not a CUDA wheel and not from the workflow: pure Python, built
by hand from VAST-AI-Research/GeoSAM2 at `b5de23c` (Apache-2.0) with `engine/geosam2/wheel.patch` applied
(upstream's `opencv-python` and `ninja` requirements dropped) and the optional CUDA extension off:
`GEOSAM2_BUILD_CUDA=0 python -m pip wheel --no-deps --no-build-isolation .` in a clean Python 3.12 venv
with pip, setuptools and wheel. Otherwise upstream's `sam2` and `utils` packages, unchanged; our runtime
patches are in `engine/geosam2/PATCHES.md`.

Not here: natten (NAF's `na2d` runs on plain torch, `engine/pixal3d/patches.py`) and FlexGEMM's
`autotune_cache.json` (only A100/MI300X entries; FlexGEMM tunes on the first laptop run and saves under `models/`).

## `rocm/`: HIP wheels for AMD gfx1201

`rocm/cumesh`, `rocm/flex_gemm` and `rocm/o_voxel` are the same three sources at the same pins, built by hand
with HIP for the RX 9070 / 9070 XT (gfx1201) against torch 2.9.1+rocm7.2.1, with the
`engine/trellis2/*-rocm-windows.patch` patches. Not from the workflow and not in `uv.lock`:
`requirements-ml-rocm.txt` installs them. How to rebuild: `docs/howto/amd-rocm-windows.md`.
