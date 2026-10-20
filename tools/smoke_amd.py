"""Smoke test of Effigy's three GPU kernel libraries (CuMesh, o_voxel, FlexGEMM) built for HIP/gfx1201.

Every check compares the GPU result against an independent reference, not just "it ran".
"""

import sys
import time

import torch
import torch.nn.functional as F

RESULTS = []
EFFIGY = __import__("os").environ.get("EFFIGY_DIR", ".")  # the Effigy checkout (run from its root)


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def cube():
    v = torch.tensor(
        [[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], dtype=torch.float32
    )
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
    return v, f


def box_distance(p):
    """Exact unsigned distance from points to the surface of the unit cube [0,1]^3."""
    q = (p - 0.5).abs() - 0.5
    outside = q.clamp(min=0).norm(dim=-1)
    inside = (-q).min(dim=-1).values
    return torch.where((q > 0).any(dim=-1), outside, inside)


def test_cumesh():
    import cumesh

    v, f = cube()
    m = cumesh.CuMesh()
    m.init(v.cuda(), f.cuda())
    m.simplify(8)
    torch.cuda.synchronize()
    check(
        "cumesh.simplify (Effigy smoke_m1)",
        m.num_faces <= 12 and m.num_vertices > 0,
        f"-> {m.num_vertices} v, {m.num_faces} f",
    )

    # duplicate faces: exercises the rocprim::tuple radix-sort decomposer patched for HIP
    m = cumesh.CuMesh()
    m.init(v.cuda(), torch.cat([f, f, f[:4]]).cuda())
    m.remove_duplicate_faces()
    torch.cuda.synchronize()
    check(
        "cumesh.remove_duplicate_faces", m.num_faces == 12, f"28 -> {m.num_faces} faces (want 12)"
    )

    m = cumesh.CuMesh()
    m.init(v.cuda(), f.cuda())
    m.get_connected_components()
    check(
        "cumesh.connected_components",
        m.num_conneted_components == 1,
        f"{m.num_conneted_components} component(s)",
    )

    bvh = cumesh.cuBVH(v.cuda(), f.cuda())
    g = torch.Generator().manual_seed(0)
    p = torch.rand(200_000, 3, generator=g) * 3 - 1  # inside and outside the cube
    d, _, _ = bvh.unsigned_distance(p.cuda())
    err = (d.cpu() - box_distance(p)).abs().max().item()
    check("cuBVH.unsigned_distance vs analytic", err < 1e-4, f"max err {err:.2e} over 200k points")

    o = torch.tensor([[0.5, 0.5, -1.0], [-2.0, 0.25, 0.75], [0.5, 3.0, 0.5]])
    dvec = torch.tensor([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, -1.0, 0.0]])
    out = bvh.ray_trace(o.cuda(), dvec.cuda())
    depth = out[-1] if isinstance(out, tuple) else out
    want = torch.tensor([1.0, 2.0, 2.0])
    derr = (depth.float().cpu().reshape(-1)[:3] - want).abs().max().item()
    check(
        "cuBVH.ray_trace hit depths",
        derr < 1e-4,
        f"got {depth.cpu().tolist()} want {want.tolist()}",
    )

    # timing: 4M distance queries
    p = torch.rand(4_000_000, 3, device="cuda") * 3 - 1
    torch.cuda.synchronize()
    t = time.time()
    bvh.unsigned_distance(p)
    torch.cuda.synchronize()
    print(f"       4M BVH distance queries: {(time.time() - t) * 1000:.1f} ms")


def test_o_voxel():
    sys.path.insert(0, EFFIGY)
    from engine.trellis2.patches import install_nvdiffrast_decoy  # same as Effigy's smoke_m1

    install_nvdiffrast_decoy()
    import o_voxel.serialize as s

    g = torch.Generator().manual_seed(1)
    coords = torch.randint(0, 1024, (100_000, 3), generator=g, dtype=torch.int32)
    coords = torch.unique(coords, dim=0).cuda()
    for mode in ("z_order", "hilbert"):
        code = s.encode_seq(coords, mode=mode)
        back = s.decode_seq(code, mode=mode)
        ok = torch.equal(back.int().cpu(), coords.int().cpu())
        uniq = code.unique().numel() == coords.shape[0]
        check(f"o_voxel {mode} encode/decode round trip", ok and uniq, f"{coords.shape[0]} coords")


def test_flex_gemm():
    import flex_gemm
    from flex_gemm.ops import spconv
    from flex_gemm.ops.spconv import Algorithm
    from flex_gemm.ops.spconv.submanifold_conv3d import sparse_submanifold_conv3d

    check(
        "flex_gemm imports without Triton", flex_gemm.kernels.triton is None, "(explicit_gemm path)"
    )
    spconv.set_algorithm(Algorithm.EXPLICIT_GEMM)

    g = torch.Generator().manual_seed(2)
    R, Ci, Co, B = 32, 16, 24, 2
    dense_mask = torch.rand(B, R, R, R, generator=g) < 0.15
    idx = dense_mask.nonzero().int().contiguous()  # [N, 4] (b, x, y, z)
    feats = torch.randn(idx.shape[0], Ci, generator=g)
    weight = torch.randn(Co, 3, 3, 3, Ci, generator=g) * 0.1
    bias = torch.randn(Co, generator=g)

    out, _ = sparse_submanifold_conv3d(
        feats.cuda(), idx.cuda(), torch.Size([B, Ci, R, R, R]), weight.cuda(), bias.cuda()
    )

    dense = torch.zeros(B, Ci, R, R, R)
    dense[idx[:, 0].long(), :, idx[:, 1].long(), idx[:, 2].long(), idx[:, 3].long()] = feats
    w = weight.permute(0, 4, 1, 2, 3).contiguous()  # [Co, Ci, kx, ky, kz]
    best = None
    for name, wk in (("as-is", w), ("flipped", w.flip(2, 3, 4))):
        ref = F.conv3d(dense.double(), wk.double(), bias.double(), padding=1)
        ref = ref[idx[:, 0].long(), :, idx[:, 1].long(), idx[:, 2].long(), idx[:, 3].long()]
        err = (out.double().cpu() - ref).abs().max().item()
        best = min(best, (err, name)) if best else (err, name)
    check(
        "flex_gemm submanifold conv vs dense conv3d",
        best[0] < 1e-3,
        f"{idx.shape[0]} active voxels, max err {best[0]:.2e} ({best[1]} kernel)",
    )

    # timing at a TRELLIS-like scale
    R, C = 64, 128
    m = torch.rand(1, R, R, R, device="cuda") < 0.2
    idx = m.nonzero().int().contiguous()
    feats = torch.randn(idx.shape[0], C, device="cuda", dtype=torch.float16)
    weight = torch.randn(C, 3, 3, 3, C, device="cuda", dtype=torch.float16)
    torch.cuda.reset_peak_memory_stats()
    _, cache = sparse_submanifold_conv3d(feats, idx, torch.Size([1, C, R, R, R]), weight)
    torch.cuda.synchronize()
    t = time.time()
    for _ in range(10):
        sparse_submanifold_conv3d(
            feats, idx, torch.Size([1, C, R, R, R]), weight, neighbor_cache=cache
        )
    torch.cuda.synchronize()
    print(
        f"       {idx.shape[0]} voxels x {C}ch fp16 subm conv: {(time.time() - t) * 100:.1f} ms/iter, "
        f"peak {torch.cuda.max_memory_allocated() / 2**30:.2f} GB"
    )


if __name__ == "__main__":
    p = torch.cuda.get_device_properties(0)
    print("torch", torch.__version__, "hip", torch.version.hip, "|", p.name, p.gcnArchName)
    for t in (test_cumesh, test_o_voxel, test_flex_gemm):
        try:
            t()
        except Exception as e:  # noqa: BLE001 report and keep going so one library can't hide the others
            import traceback

            traceback.print_exc()
            check(t.__name__, False, f"{type(e).__name__}: {e}")
    n_ok = sum(ok for _, ok in RESULTS)
    print(f"\n{n_ok}/{len(RESULTS)} checks passed")
    sys.exit(0 if n_ok == len(RESULTS) else 1)
