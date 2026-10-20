# Run Effigy on an AMD GPU (Windows, ROCm)

Developer setup for an AMD Radeon RX 9070 / 9070 XT (gfx1201) on native Windows. Tested with the Fast
chain only; results and caveats in [docs/bench/amd-rocm.md](../bench/amd-rocm.md). The zip and `run.bat`
stay NVIDIA-only.

## Install

From a Git checkout (SETUP.md appendix), in `C:\Effigy`:

```bat
uv sync --frozen
uv venv --python 3.12 .venv-ml
uv pip install --python .venv-ml -r requirements-ml-rocm.txt
uv pip uninstall --python .venv-ml triton-windows
.venv-ml\Scripts\rocm-sdk init
.venv\Scripts\python -m server.blender_install && .venv\Scripts\python -m server.gltfpack_install
.venv-ml\Scripts\python -m tools.download_models fast
set EFFIGY_NO_BROWSER=1 && .venv\Scripts\python -m server
```

- **Never `run.bat`** on AMD: it runs `uv sync --group ml`, which puts CUDA torch back into `.venv-ml`.
- `requirements-ml-rocm.txt` replaces pyproject's `ml` group: torch from AMD's Windows ROCm repo
  (`repo.radeon.com/rocm/windows/rocm-rel-7.2.1`), the three kernel wheels from `wheels/rocm/`, no
  `triton-windows` (FlexGEMM runs its `EXPLICIT_GEMM` path), onnxruntime on the CPU.
- DINOv3 is gated: accept its licence on Hugging Face before `download_models`.

Check the kernels: `.venv-ml\Scripts\python tools\smoke_amd.py` (9 checks, each against an independent
reference).

## Rebuild the HIP kernel wheels

`wheels/rocm/` holds CuMesh, FlexGEMM and o_voxel built with HIP for gfx1201 (cp312, win_amd64). Built by
hand, not by `wheels.yml`. To rebuild (or build for another architecture: change `GPU_ARCHS` and
`PYTORCH_ROCM_ARCH` in `tools/build_rocm_wheel.bat`):

1. Toolchain: a build folder with a `venv-rocm` (Python 3.12, torch from AMD's Windows ROCm repo plus
   `rocm_sdk_devel`, `rocm-sdk init` done) and VS 2022 Build Tools for the linker and Windows SDK. The
   compiler is **clang-cl from the ROCm SDK** (`CXX`): MSVC `cl` can't link against AMD's clang-built
   `c10.dll` (unresolved `c10::ValueError` inherited constructor).
2. Clone the sources at the pins in `.github/workflows/wheels.yml`: JeffreyXiang/CuMesh `12289e1` (with
   submodules), JeffreyXiang/FlexGEMM `6dd94a8`, microsoft/TRELLIS.2 `75fbf01` (with
   `o-voxel/third_party/eigen`).
3. Apply `engine/trellis2/flex-gemm-windows.patch` and `o-voxel-windows.patch` first, then the three
   `engine/trellis2/*-rocm-windows.patch`. Copy Eigen into `CuMesh/third_party/cubvh/third_party/eigen`.
4. `set SP=<build folder>`, then `tools\build_rocm_wheel.bat <source dir> <log name>` for each library;
   wheels land in `%SP%\dist`.

All three upstream repos already have a `BUILD_TARGET=rocm` switch. The ROCm patches only fix Windows + HIP
gaps:

- nvcc-only flags (`-Xcompiler=…`, `--extended-lambda`) skipped for HIP; `-Wno-c++11-narrowing` for clang-cl
- CuMesh: `cuda::std::tuple` → `rocprim::tuple` in one radix-sort decomposer; `Vec3f()` made host+device
  (hipCUB builds the zero value on the host); the pybind file compiled through hipcc; `src/io.cu` copies
  without `hipMemcpy2D`, which truncates at ~2^20 rows on Windows HIP
- cubvh: a pre-hipified copy (`third_party/cubvh_hip`), since torch's hipify doesn't reach its `<gpu/…>`
  headers
- FlexGEMM: the Triton import made optional
