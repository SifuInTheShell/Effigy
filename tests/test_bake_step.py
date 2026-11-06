"""M3: steps/bake.py re-bakes a UV-mapped version from the chain's volume (CPU path: no projection)."""

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

torch = pytest.importorskip("torch")
trimesh = pytest.importorskip("trimesh")
from steps import bake
from steps._common import TRELLIS_TO_GLTF, StepError, save_ovoxel

FIXTURE = (
    Path(__file__).parent / "fixtures" / "two_tris.glb"
)  # 1 m quad on the library floor (y = 0)


def library(tmp_path, transform=None):
    """010_trellis2 (volume, frame = pure axis swap) → 020_uv (the fixture quad, optionally scaled)."""
    gen, uv = tmp_path / "010_trellis2", tmp_path / "020_uv"
    gen.mkdir()
    uv.mkdir()
    g = np.stack(np.meshgrid(*[np.arange(16)] * 3, indexing="ij"), -1).reshape(-1, 3)
    attrs = np.tile([0.8, 0.4, 0.2, 0.0, 0.6, 1.0], (len(g), 1))
    layout = {"base_color": [0, 3], "metallic": [3, 4], "roughness": [4, 5], "alpha": [5, 6]}
    save_ovoxel(gen / "ovoxel.npz", g, attrs, layout, 16)
    frame = {"library": "gltf_yup_m", "to_library_from_source": TRELLIS_TO_GLTF.tolist()}
    meta = {
        "version": gen.name,
        "parent": None,
        "outputs": {"ovoxel": "ovoxel.npz"},
        "frame": frame,
    }
    (gen / "meta.json").write_text(json.dumps(meta))
    # the hi surface the GPU path projects onto: the quad itself, subdivided (cuBVH needs > 8 triangles)
    hi = trimesh.load(FIXTURE, force="mesh").subdivide().subdivide()
    hi.apply_transform(np.linalg.inv(TRELLIS_TO_GLTF))
    hi.export(gen / "hi_trellis.ply")
    shutil.copy(FIXTURE, uv / "mesh.glb")
    metrics = {"transform": transform} if transform else {}
    (uv / "meta.json").write_text(
        json.dumps(
            {
                "version": uv.name,
                "parent": gen.name,
                "outputs": {"mesh": "mesh.glb"},
                "metrics": metrics,
            }
        )
    )
    return uv


def test_flat_volume_bakes_flat_colour_and_flat_normals(tmp_path):
    src, out = library(tmp_path), tmp_path / "030_bake"
    out.mkdir()
    res = bake.run("run", str(src), str(out), {"texture_size": 256})
    assert set(res["outputs"]) == {"mesh", "basecolor", "orm", "normal"}
    base = np.asarray(trimesh.load(out / "mesh.glb", force="mesh").visual.material.baseColorTexture)
    assert (base == [204, 102, 51, 255]).all()  # exactly the volume's colour, gutters included
    orm = np.asarray(Image.open(out / "orm.png"))
    assert (orm == [255, 153, 0]).all()  # AO is flat (CPU: none baked, GPU: a flat quad), rough 0.6
    assert res["metrics"]["supersample"] == 2 and res["metrics"]["seam_delta"] == 0
    assert np.allclose(res["metrics"]["normal_mean"], [127.5, 127.5, 255], atol=1.5)
    assert res["metrics"]["coverage"] > 0.5 and res["metrics"]["volume_from"] == "010_trellis2"


def test_texture_edits_override_generator_until_explicitly_selected(tmp_path, monkeypatch):
    from steps import _textures as tx

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    uv = library(tmp_path)
    edited = tmp_path / "030_textures"
    edited.mkdir()
    tx.patch_glb(
        uv / "mesh.glb", edited / "mesh.glb", {"basecolor": Image.new("RGB", (16, 16), "red")}, 0
    )
    (edited / "meta.json").write_text(
        json.dumps(
            {
                "version": edited.name,
                "step": "textures",
                "parent": uv.name,
                "outputs": {"mesh": "mesh.glb"},
            }
        )
    )
    for choice in ("auto", "current", "generator"):
        out = tmp_path / choice
        out.mkdir()
        result = bake.run(
            "run",
            str(edited),
            str(out),
            {"texture_size": 256, "supersample": 1, "source": choice, "ao": False},
        )
        if choice == "generator":
            assert result["metrics"]["volume_from"] == "010_trellis2"
        else:
            assert result["metrics"]["source_from"] == edited.name
            assert "volume_from" not in result["metrics"]


@pytest.mark.skipif(not torch.cuda.is_available(), reason="the hi-surface projector needs CUDA")
def test_flipped_hi_winding_still_bakes_flat_normals(tmp_path):
    """TRELLIS's hi mesh has random winding: its normals must be turned to the low mesh's side."""
    src, out = library(tmp_path), tmp_path / "030_bake"
    out.mkdir()
    hi = trimesh.load(tmp_path / "010_trellis2" / "hi_trellis.ply", process=False)
    hi.invert()
    hi.export(tmp_path / "010_trellis2" / "hi_trellis.ply")
    res = bake.run("run", str(src), str(out), {"texture_size": 256})
    assert res["metrics"]["projected"]
    assert np.allclose(res["metrics"]["normal_mean"], [127.5, 127.5, 255], atol=1.5)


def test_chain_transforms_compose():
    pytest.importorskip("trimesh")
    scale = np.diag([2.0, 2.0, 2.0, 1.0])
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        src = library(Path(d), transform=scale.tolist())
        vol_dir, frame = bake.find_volume(src)
    assert vol_dir.name == "010_trellis2"
    np.testing.assert_allclose(frame, scale @ TRELLIS_TO_GLTF)


def test_tangent_frames_follow_the_uv_layout():
    v = torch.tensor([[0.0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]])
    f = torch.tensor([[0, 1, 2], [0, 2, 3]])
    n = torch.tensor([[0.0, 0, 1]]).expand(4, 3)
    uv = torch.tensor(
        [[0.0, 1], [1, 1], [1, 0], [0, 0]]
    )  # glTF: v down, so +y in space is image-up
    tan, sign = bake.vertex_frames(v, f, uv, n)
    torch.testing.assert_close(tan, torch.tensor([[1.0, 0, 0]]).expand(4, 3))
    assert (sign == 1).all()  # bitangent = N × T = +y = image up (OpenGL normal maps)
    mirrored = uv.clone()
    mirrored[:, 0] = 1 - mirrored[:, 0]
    tan, sign = bake.vertex_frames(v, f, mirrored, n)
    assert torch.allclose(tan[:, 0], torch.full((4,), -1.0)) and (sign == -1).all()


def test_supersample_budget_and_values_are_checked(tmp_path):
    src, out = library(tmp_path), tmp_path / "030_bake"
    out.mkdir()
    for bad in ({"texture_size": 4096, "supersample": 2}, {"supersample": 3}):
        with pytest.raises(StepError) as e:
            bake.run("run", str(src), str(out), bad)
        assert e.value.code == "bad_params"
    res = bake.run("run", str(src), str(out), {"texture_size": 256, "supersample": 1})
    assert res["metrics"]["supersample"] == 1


@pytest.mark.skipif(not torch.cuda.is_available(), reason="AO rays are cuBVH")
def test_gpu_bake_declares_the_ao_in_the_glb(tmp_path):
    from steps._mesh import write_glb

    src, out = library(tmp_path), tmp_path / "030_bake"
    out.mkdir()
    g = np.mgrid[0:4, 0:4].reshape(2, -1).T / 3.0  # a 4 x 4 grid: 18 triangles, what cuBVH needs
    v = np.stack([g[:, 0] - 0.5, np.zeros(16), g[:, 1] - 0.5], 1)
    quads = [
        (4 * i + j, 4 * i + j + 1, 4 * i + j + 5, 4 * i + j + 4) for i in range(3) for j in range(3)
    ]
    f = np.array([t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))])
    write_glb(src / "mesh.glb", v, f, 0.05 + 0.9 * g)
    res = bake.run("run", str(src), str(out), {"texture_size": 256})
    assert res["metrics"]["ao"] and res["metrics"]["ao_mean"] > 0.99  # nothing shades a flat quad
    assert trimesh.load(out / "mesh.glb", force="mesh").visual.material.occlusionTexture is not None
    off = tmp_path / "040_bake"
    off.mkdir()
    res = bake.run("run", str(src), str(off), {"texture_size": 256, "ao": False})
    assert not res["metrics"]["ao"]
    assert trimesh.load(off / "mesh.glb", force="mesh").visual.material.occlusionTexture is None
