"""AMD: steps/_rocm.py computes big products a block of rows at a time, with the same result."""

import pytest

torch = pytest.importorskip("torch")

from steps import _rocm


@pytest.fixture
def small_blocks(monkeypatch):
    """Blocks of 4 rows, on any device (the real check only fires on a ROCm GPU)."""
    monkeypatch.setattr(_rocm, "ROWS", 4)
    monkeypatch.setattr(
        _rocm,
        "_big",
        lambda t: isinstance(t, torch.Tensor) and t.dim() >= 2 and t.shape[:-1].numel() > 4,
    )


def test_linear_matmul_mm_addmm_match_unblocked(small_blocks):
    torch.manual_seed(0)
    x, x3 = torch.randn(11, 5), torch.randn(3, 7, 5)
    w, b, m = torch.randn(4, 5), torch.randn(4), torch.randn(5, 4)
    F = torch.nn.functional
    cases = [
        (F.linear, "linear", (x, w, b), {}),
        (F.linear, "linear", (x, w), {"bias": b}),
        (F.linear, "linear", (x3, w, b), {}),
        (torch.matmul, "matmul", (x, m), {}),
        (torch.matmul, "matmul", (x3, m), {}),
        (torch.mm, "mm", (x, m), {}),
        (torch.addmm, "addmm", (b, x, m), {}),
        (torch.addmm, "addmm", (torch.randn(11, 4), x, m), {}),
    ]
    for fn, kind, args, kwargs in cases:
        assert torch.allclose(
            _rocm._rows(fn, kind)(*args, **kwargs), fn(*args, **kwargs), atol=1e-6
        ), kind


def test_other_shapes_pass_through(small_blocks):
    calls = []

    def fake(*a, **k):
        calls.append(a)
        return "upstream"

    batched = _rocm._rows(fake, "matmul")
    # bmm-like: not one 2-D matrix
    assert batched(torch.randn(9, 3, 3), torch.randn(9, 3, 3)) == "upstream"
    assert batched(torch.randn(3, 3), torch.randn(3, 3)) == "upstream"  # small
    assert len(calls) == 2


def test_install_is_lazy_and_noop_without_hip():
    _rocm.install()  # torch is imported here already: patches now, or nothing on CUDA/CPU builds
    assert getattr(torch.matmul, "effigy_rows", False) == bool(torch.version.hip)
