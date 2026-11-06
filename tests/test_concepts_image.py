"""concepts op image (research 15 §5.2, M50) on a stand-in pipeline: sizes and styles checked, the style's trigger
prepended and the LoRA unloaded whatever happens, tiles above 1024 px, the cut-out in the alpha, the outputs."""

from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("torch")
import huggingface_hub

from steps import concepts
from steps._common import StepError


def test_large_transformer_is_quantized_before_loading_remaining_pipeline(monkeypatch):
    import sys

    import torch

    events = []
    linear = torch.nn.Linear(2, 2)
    linear.weight.data = linear.weight.data.to(torch.float8_e4m3fn)

    class Transformer:
        @classmethod
        def from_pretrained(cls, model, **kwargs):
            assert kwargs["subfolder"] == "transformer"
            assert kwargs["disable_mmap"] is True
            events.append("transformer")
            return cls()

        def enable_layerwise_casting(self, **kwargs):
            assert kwargs["storage_dtype"] == torch.float8_e4m3fn
            events.append("quantized")

        def modules(self):
            return [linear]

    class Pipeline:
        @classmethod
        def from_pretrained(cls, model, **kwargs):
            assert events == ["transformer", "quantized"]
            assert isinstance(kwargs["transformer"], Transformer)
            assert linear.compute_dtype == torch.bfloat16
            events.append("pipeline")
            return cls()

    monkeypatch.setitem(
        sys.modules,
        "diffusers",
        SimpleNamespace(ZImagePipeline=Pipeline, ZImageTransformer2DModel=Transformer),
    )
    monkeypatch.setattr(concepts, "_PIPE", [])
    first = concepts._pipeline()
    assert concepts._pipeline() is first
    assert events == ["transformer", "quantized", "pipeline"]


class _Part:
    def to(self, *_):
        return self


class _Vae(_Part):
    def __init__(self):
        self.tiling = []

    def enable_tiling(self):
        self.tiling.append(True)

    def disable_tiling(self):
        self.tiling.append(False)


class _Pipe:
    def __init__(self, fail=False):
        self.transformer, self.text_encoder, self.vae = _Part(), _Part(), _Vae()
        self.texts, self.calls, self.fail = [], [], fail

    def encode_prompt(self, texts, device, do_classifier_free_guidance):
        self.texts += texts
        return [object()], None

    def load_lora_weights(self, path, adapter_name):
        self.calls.append(("load", adapter_name))

    def set_adapters(self, names, scales):
        self.calls.append(("set", names, scales))

    def unload_lora_weights(self):
        self.calls.append(("unload",))

    def __call__(
        self, prompt_embeds, height, width, num_inference_steps, guidance_scale, generator
    ):
        if self.fail:
            raise RuntimeError("out of memory")
        return SimpleNamespace(images=[Image.new("RGB", (width, height), (200, 30, 30))])


@pytest.fixture
def pipe(monkeypatch):
    p = _Pipe()
    monkeypatch.setattr(concepts, "_pipeline", lambda: p)
    monkeypatch.setattr(concepts, "progress", lambda *a, **k: None)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", lambda *a, **k: "style.safetensors")
    return p


def test_bad_sizes_counts_and_styles_are_refused(tmp_path, pipe):
    for bad in (
        {"prompt": "x", "width": 1000},
        {"prompt": "x", "height": 4096},
        {"prompt": "x", "count": 5},
        {"prompt": "x", "lora": "anime"},
        {"prompt": "x", "lora": "pixel", "lora_scale": 3},
        {"prompt": "x", "seed": 2**64},
        {},  # no prompt and no brief
    ):
        with pytest.raises(StepError) as e:
            concepts.run("image", None, str(tmp_path), bad)
        assert e.value.code == "bad_params", bad
    assert pipe.texts == []


def test_a_wallpaper_set_from_a_brief(tmp_path, pipe):
    brief = tmp_path / "brief"
    brief.mkdir()
    (brief / "brief.yaml").write_text(
        "prompt_full: a neon street, wide scenic composition\n", encoding="utf-8"
    )
    out = tmp_path / "out"
    out.mkdir()
    r = concepts.run(
        "image", str(brief), str(out), {"width": 1920, "height": 1088, "count": 3, "seed": 5}
    )
    assert pipe.texts == ["a neon street, wide scenic composition"]
    assert sorted(r["outputs"]) == ["image", "image_0", "image_1", "image_2", "preview"]
    assert Image.open(out / "image_2.png").size == (1920, 1088)
    assert (out / "image.png").read_bytes() == (out / "image_0.png").read_bytes()
    assert pipe.vae.tiling == [True, False]  # tiles above 1024 px, back for the next job
    assert r["licenses"] == [concepts.MODEL] and r["metrics"]["seed"] == 5


def test_a_style_is_triggered_and_unloaded_even_when_the_run_fails(tmp_path, pipe, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    r = concepts.run(
        "image", None, str(out), {"prompt": "a brawler", "lora": "pixel", "lora_scale": 0.8}
    )
    assert pipe.texts == ["Pixel art style. a brawler"]
    assert pipe.calls == [("load", "pixel"), ("set", ["pixel"], [0.8]), ("unload",)]
    assert concepts.STYLES["pixel"][0] in r["licenses"] and pipe.vae.tiling == []

    failing = _Pipe(fail=True)
    monkeypatch.setattr(concepts, "_pipeline", lambda: failing)
    with pytest.raises(RuntimeError):
        concepts.run("image", None, str(out), {"prompt": "x", "lora": "painting"})
    assert failing.calls[-1] == ("unload",)


def test_a_missing_style_says_where_to_install_it(tmp_path, pipe, monkeypatch):
    from huggingface_hub.errors import LocalEntryNotFoundError

    def absent(*a, **k):
        raise LocalEntryNotFoundError("not in the cache")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", absent)
    with pytest.raises(StepError) as e:
        concepts.run("image", None, str(tmp_path), {"prompt": "x", "lora": "drawing"})
    assert e.value.code == "missing_input" and "Setup" in str(e.value)
    assert ("unload",) not in pipe.calls  # nothing was loaded


def test_the_cutout_goes_into_the_alpha(tmp_path, pipe, monkeypatch):
    monkeypatch.setattr(concepts._birefnet, "load", lambda: object())
    monkeypatch.setattr(concepts._birefnet, "matte", lambda m, rgb: np.full(rgb.shape[:2], 0.5))
    r = concepts.run(
        "image",
        None,
        str(tmp_path),
        {"prompt": "a barrel", "width": 256, "height": 512, "cutout": True},
    )
    im = Image.open(tmp_path / "image_0.png")
    assert im.mode == "RGBA" and im.size == (256, 512) and im.getpixel((10, 10))[3] == 128
    assert concepts._birefnet.MODEL in r["licenses"]


def test_op_run_is_untouched_and_other_ops_are_refused(tmp_path, pipe):
    with pytest.raises(StepError) as e:
        concepts.run("tile", None, str(tmp_path), {})
    assert e.value.code == "bad_params"
    with pytest.raises(StepError) as e:
        concepts.run("run", None, str(tmp_path), {})
    assert e.value.code == "missing_input"  # the candidates still need a brief
