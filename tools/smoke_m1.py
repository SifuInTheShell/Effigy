"""M1 acceptance on the reference laptop (needs GPU).

UV_PROJECT_ENVIRONMENT=.venv-ml uv run --group ml python -m tools.smoke_m1
"""

import importlib.util

from engine.trellis2.patches import install_nvdiffrast_decoy

assert importlib.util.find_spec("nvdiffrast") is None, "real nvdiffrast is installed; remove it"
install_nvdiffrast_decoy()

import cumesh
import flex_gemm  # noqa: F401
import o_voxel  # noqa: F401
import torch

print(torch.__version__, torch.version.cuda, torch.cuda.get_device_name(0))
print("arch", torch.cuda.get_device_capability(0), "built for", torch.cuda.get_arch_list())

# unit cube (12 triangles) simplified to 8: proves this card's kernels (sm_86/89/120) load and run
v = torch.tensor([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], dtype=torch.float32)
f = torch.tensor(
    [
        [0, 1, 3],
        [0, 3, 2],
        [4, 6, 7],
        [4, 7, 5],
        [0, 4, 5],
        [0, 5, 1],
        [2, 3, 7],
        [2, 7, 6],
        [0, 2, 6],
        [0, 6, 4],
        [1, 5, 7],
        [1, 7, 3],
    ],
    dtype=torch.int32,
)
m = cumesh.CuMesh()
m.init(v.cuda(), f.cuda())
m.simplify(8)
torch.cuda.synchronize()
print("cumesh ok:", m.num_vertices, "vertices,", m.num_faces, "faces")
