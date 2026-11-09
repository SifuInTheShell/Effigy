"""Fetch the models the steps use into EFFIGY_MODELS (default models/), at pinned revisions.

    uv run --group ml python -m tools.download_models [fast] [faithful] [multiview] [concepts] [styles] [pose] [canon-flux] [skin] [clips] [texture] [kimodo] [parts] [refine]   (default: all)

Folders under models/ are plain copies (`local_dir`) so the steps load them with no network; repos the
steps open by id (BiRefNet, Z-Image) go into the Hugging Face cache, which HF_HUB_CACHE points at
models/hub (only the cache: HF_HOME would also move the token `hf auth login` saved).
DINOv3 is gated: accept its licence on huggingface.co, then `hf auth login`, then run this again.
Files outside Hugging Face (FILES) are pinned by SHA-256.

    python -m tools.download_models --manifest   (after changing a pin; needs the gated repos' access)

writes models_manifest.json: every file each row fetches, with its size and, where the Hub has it, its
SHA-256 (M35b): what "installed" is checked against, what Repair verifies, the sizes the Setup page shows.
"""

import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

from server.settings import models_root

ROOT = Path(__file__).resolve().parents[1]
MODELS = models_root()  # EFFIGY_MODELS, else the folder chosen in the app (M35c)
os.environ.setdefault("HF_HUB_CACHE", str(MODELS / "hub"))
# (groups, repo, revision, local folder or None = HF cache, snapshot_download filters)
PINNED = [
    (
        "fast",
        "microsoft/TRELLIS.2-4B",
        "af44b45f2e35a493886929c6d786e563ec68364d",
        "TRELLIS.2-4B",
        None,
    ),
    (
        "fast",
        "microsoft/TRELLIS-image-large",
        "25e0d31ffbebe4b5a97464dd851910efc3002d96",
        "TRELLIS-image-large",
        {"allow_patterns": ["ckpts/ss_dec_conv3d_16l8_fp16.*"]},
    ),
    (
        "fast faithful",
        "facebook/dinov3-vitl16-pretrain-lvd1689m",
        "ea8dc2863c51be0a264bab82070e3e8836b02d51",
        "dinov3-vitl16",
        None,
    ),
    # M26 text-to-motion (steps/motion.py in .venv-kimodo resolves these by id from the HF cache): the
    # Kimodo denoiser, the two LLM2Vec adapters, and the gated Llama 3 8B Instruct text encoder (accept its
    # licence on huggingface.co, then `hf auth login`, as DINOv3; the safetensors only, not the legacy copy)
    (
        "kimodo",
        "nvidia/Kimodo-SOMA-RP-v1.1",
        "6c9233af1180b8151e3c4703477104af5dce9dd5",
        None,
        None,
    ),
    (
        "kimodo",
        "McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp",
        "31474e395ada192e8ed1586db6be79fb3b70c9c0",
        None,
        None,
    ),
    (
        "kimodo",
        "McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp-supervised",
        "baa8ebf04a1c2500e61288e7dad65e8ae42601a7",
        None,
        None,
    ),
    (
        "kimodo",
        "meta-llama/Meta-Llama-3-8B-Instruct",
        "8afb486c1db24fe5011ec46dfbe5b5dccdb575c2",
        None,
        {"allow_patterns": ["*.safetensors", "*.json", "tokenizer*", "LICENSE", "USE_POLICY.md"]},
    ),
    # M31 parts: GeoSAM2's checkpoint (Apache-2.0, 616 MB; steps/parts.py in .venv-parts reads the file)
    (
        "parts",
        "VAST-AI/GeoSAM2",
        "ba92f5f50418f2fe9af1078448b63176df13b1ee",
        "GeoSAM2",
        {"allow_patterns": ["geosam2.pt", "README.md"]},
    ),
    (
        "fast faithful",
        "ZhengPeng7/BiRefNet",
        "e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4",
        None,
        None,
    ),
    (
        "faithful",
        "TencentARC/Pixal3D",
        "b0cb2e1b794cab9aa0ac38a95d794a4d9337437f",
        "Pixal3D",
        {"ignore_patterns": ["*_mv*"]},  # single view ≈ 24 GB
    ),
    (  # four-view checkpoints (M17), another ≈ 22 GB
        "multiview",
        "TencentARC/Pixal3D",
        "b0cb2e1b794cab9aa0ac38a95d794a4d9337437f",
        "Pixal3D",
        {"allow_patterns": ["*_mv*"]},
    ),
    (
        "faithful",
        "Ruicheng/moge-2-vitl",
        "39c4d5e957afe587e04eec59dc2bcc3be5ecd968",
        "moge-2-vitl",
        None,
    ),
    (  # M10 concepts; M43b refine draws its views with it (shared, on disk once)
        "concepts refine",
        "Tongyi-MAI/Z-Image-Turbo",
        "f332072aa78be7aecdf3ee76d5c247082da564a6",
        None,
        {"allow_patterns": ["*.json", "*.txt", "*/*"]},
    ),
    # M50 2D styles (research 15 §7): Apache-2.0 LoRAs for Z-Image-Turbo, one file each, into the HF cache
    (
        "styles",
        "tarn59/pixel_art_style_lora_z_image_turbo",
        "0a5092d1619664d94a5a36784f92db84b3ae62bd",
        None,
        {"allow_patterns": ["pixel_art_style_z_image_turbo.safetensors"]},
    ),
    (
        "styles",
        "renderartist/Classic-Painting-Z-Image-Turbo-LoRA",
        "e873cc474ce517d49d09ed1f64358ff43660b276",
        None,
        {"allow_patterns": ["Classic_Painting_Z_Image_Turbo_v1_renderartist_1750.safetensors"]},
    ),
    (
        "styles",
        "ostris/z_image_turbo_childrens_drawings",
        "7fcd66a99149c58741990fca28562a4a581af7a9",
        None,
        {"allow_patterns": ["z_image_turbo_childrens_drawings.safetensors"]},
    ),
    (  # M43b texture detail: Fun-ControlNet Tile 2.1 (Apache-2.0), the lite 2601 8-step file only (2.0 GB; the full
        # one peaked at 15.5 GB and restarted the 16 GB reference laptop)
        "refine",
        "alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1",
        "5155fc56d17821007d6f62ac192c09e0f0e72016",
        "Z-Image-Turbo-Fun-Controlnet-Union-2.1",
        {"allow_patterns": ["Z-Image-Turbo-Fun-Controlnet-Tile-2.1-lite-2601-8steps.safetensors"]},
    ),
    (  # M46c: the full Tile file (6.7 GB, cleaner than the lite one, m43b), for cards that hold its 15.6 GB:
        # Setup offers it only there (server/presets.GROUP_NEED); refine takes it when it is there and fits
        "refine-full",
        "alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1",
        "5155fc56d17821007d6f62ac192c09e0f0e72016",
        "Z-Image-Turbo-Fun-Controlnet-Union-2.1",
        {"allow_patterns": ["Z-Image-Turbo-Fun-Controlnet-Tile-2.1-2601-8steps.safetensors"]},
    ),
    (  # M55 parallax layers: MoGe-3 ViT-g (MIT, 5.0 GB), depth for the bands; steps/depth.py in .venv-moge
        "layers",
        "Ruicheng/moge-3-vitg",
        "6ef26c5a4b4148dab5ccaacdb08b72dc66380475",
        None,
        {"allow_patterns": ["model.pt", "README.md"]},
    ),
    (  # A-pose gate (M16): whole-body keypoints, the ONNX export only (134 MB)
        "pose",
        "yzd-v/DWPose",
        "1a7144101628d69ee7a3768d1ee3a094070dc388",
        "DWPose",
        {"allow_patterns": ["dw-ll_ucoco_384.onnx"]},
    ),
    (  # M16 re-pose (canon): FLUX.2-klein-4B (diffusers folders; the single-file copy is skipped), ~16 GB
        "canon-flux",
        "black-forest-labs/FLUX.2-klein-4B",
        "e7b7dc27f91deacad38e78976d1f2b499d76a294",
        "FLUX.2-klein-4B",
        {"ignore_patterns": ["flux-2-klein-4b.safetensors", "*.jpg"]},
    ),
    (  # M20 learned skinning (rig_skin engine skintokens): TokenRig + skin VAE, 1.5 GB
        "skin",
        "VAST-AI/SkinTokens",
        "79736cad0fd84de384d5eede659b4ebd24effe33",
        "SkinTokens",
        {"allow_patterns": ["experiments/*"]},
    ),
    (  # its Qwen3 backbone's config and tokenizer only (the weights are inside the TokenRig ckpt)
        "skin",
        "Qwen/Qwen3-0.6B",
        "c1899de289a04d12100db370d81485cdf75e47ca",
        "SkinTokens/Qwen3-0.6B",
        {"ignore_patterns": ["*.safetensors", "*.bin"]},
    ),
    # M34 motion from video: NVIDIA GEM-X (weights NVIDIA Open Model License; its copies of SAM 3D Body under
    # the SAM License and of MHR under Apache-2.0), laid out as GEM-X's own inputs/ because steps/mocap runs
    # with models/GEM-X as its working folder; never the SMPL variant (gem_smpl.ckpt) or the ONNX exports
    *(
        (
            "mocap",
            "nvidia/GEM-X",
            "5ccf5ca3746c3620aa4016114f069a5f6ae399cd",
            f"GEM-X/inputs/{folder}",
            {"allow_patterns": files},
        )
        for folder, files in (
            ("pretrained", ["gem_soma.ckpt"]),
            ("checkpoints/vitpose", ["vitpose.pth"]),
            ("checkpoints/sam-3d-body-dinov3", ["sam3d_body.ckpt", "model_config.yaml"]),
            ("mhr_data", ["mhr_model.pt"]),
            ("soma_data", ["scale_mean.pth", "scale_comps.pth"]),
        )
    ),
    # its person detector: YOLOX-L (COCO) from DWPose's repo (Apache-2.0), the revision the A-pose gate pins
    (
        "mocap",
        "yzd-v/DWPose",
        "1a7144101628d69ee7a3768d1ee3a094070dc388",
        "DWPose",
        {"allow_patterns": ["yolox_l.onnx"]},
    ),
]
# (group, url, sha256, path under models/)
FILES = [
    (  # M70: MediaPipe's hand landmark model (Apache-2.0), the fingers from close-up renders of each hand
        "pose",
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
        "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1",
        "MediaPipe/hand_landmarker.task",
    ),
    (
        "faithful",
        "https://github.com/valeoai/NAF/releases/download/model/naf_release.pth",
        "c096c1ab2217a5c3ac136365f721685e2201379cb69d509cfb0261183847c98f",
        "NAF/naf_release.pth",
    ),
    # M24: Real-ESRGAN x4plus (BSD-3-Clause, the v0.1.0 release file), the 4x upscale of the conditioning
    # image before it is projected onto the head (steps/_sr.py writes the network out, no basicsr)
    (
        "texture",
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
        "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1",
        "RealESRGAN/RealESRGAN_x4plus.pth",
    ),
    # M21 clip pack: Mesh2Motion's human animations (CC0), 178 clips in 3 GLBs on a UE5-named skeleton,
    # pinned to the app repo's commit 79f3f61 (2026-09-28); retargeted onto ours by blender_ops/retarget
    *(
        (
            "clips",
            f"https://raw.githubusercontent.com/Mesh2Motion/mesh2motion-app/79f3f61a9852ef70234a5a4a7c13ed87f7a71833/static/animations/{name}.glb",
            sha,
            f"clips/mesh2motion/{name}.glb",
        )
        for name, sha in (
            (
                "human-base-animations",
                "406eb0a8dc4ab366e623b79b6e3005a4951392e1bda78ae39c1099d31147733c",
            ),
            (
                "human-addon-animations",
                "a0d64d555e0d492026b72d58bf8e16c5e86779295f9093e376dcc001915c2c95",
            ),
            (
                "human-mocap-animations",
                "814593d62522f5be8d6bd32df2a8fa3c7feb90a8e1a1ba43f4aaf0048555280d",
            ),
        )
    ),
    # M36, M41 creature clips: each on its own template's skeleton (steps/_templates.py), same commit, CC0
    *(
        (
            "clips",
            f"https://raw.githubusercontent.com/Mesh2Motion/mesh2motion-app/79f3f61a9852ef70234a5a4a7c13ed87f7a71833/static/animations/{name}-animations.glb",
            sha,
            f"clips/mesh2motion/creatures/{name}-animations.glb",
        )
        for name, sha in (
            ("fox", "80e80641a5692a14aba7616a46d2a28d3595479764887c578c766b99dba8a0d0"),
            ("horse", "c6f890c307e457b9aa7cceda1fbc1e39f8e6723ef340f23e3e667e122673b51c"),
            ("bird", "9624b0bf70abe400ed0445c2423c69d5e046bd7d60074e399e2be161763c4f99"),
            ("kaiju", "675a098d8cdddf5247c55d49fa04712556cd0493facec0be586b6bb9aaeed410"),
            ("shark", "918321bbcab9439456fcc5838d745c810ea6fd5adb658ccc3651b5d4244f9107"),
            ("dragon", "73262041b09aa0499778818162b34955e76e17548212825548799643d5c925b3"),
            ("spider", "a321c1617c7f74cf86c050694bc254e74409c6c69db26fe542338f2f7c43d07a"),
            ("snake", "795e4c62ba1e02f3f33e900832cdc6a1ca5a4aa4f6fe426fccd3aaa0f7f6ac25"),
        )
    ),
]


# M34: SOMA's assets at the commit GEM-X pins (py-soma-x 0.1.3, Apache-2.0, Git LFS on GitHub: Hugging Face
# only has 0.2.2+, whose SOMA_neutral.npz this code cannot read), and DINOv3's code at a pinned commit for
# ViTPose's backbone class (its weights are in vitpose.pth; DINOv3 License): steps/mocap unpacks the zip
# where torch.hub looks, so nothing is fetched at run time
FILES += [
    *(
        (
            "mocap",
            f"https://media.githubusercontent.com/media/NVlabs/SOMA-X/e0f8ff0ecfa3edbbb6058b1e0f08822ee2f84ee5/assets/{name}",
            sha,
            f"GEM-X/inputs/soma_assets/{name}",
        )
        for name, sha in (
            (
                "SOMA_neutral.npz",
                "515f7d5bb74be4e370e9adf5e779760ec3581556374c0b33212a32d13ab3b53f",
            ),
            (
                "correctives_model.pt",
                "3b0f82928dc25c952e0a23af59dbc9c02854ccb4c4c1c988f70bbffd7c284636",
            ),
            (
                "MHR/base_body_lod6.obj",
                "cd46620ec46e968658527f25e2b54b8f2292bfd486eac59c596f8b5d5229b9e0",
            ),
            (
                "MHR/SOMA_wrap_lod1.obj",
                "9ab7a2328d5b026c17ef1d09ed692ffeb68f96d3fc74970cba72e667dbe0f728",
            ),
            (
                "MHR/mhr_model_lod6.pt",
                "28839b7ee0eb38eb3943cf26cff61a306de78f86227a0f17bb044252c7bc06ac",
            ),
        )
    ),
    (
        "mocap",
        "https://github.com/facebookresearch/dinov3/archive/6876159a11b4df116f30f667f8c9888617df0751.zip",
        "8f3ba3a5e4f017c4ae209cf3052ac3ffae714bc8308d1e541db7c6050294ece2",
        "GEM-X/dinov3-6876159.zip",
    ),
]


MANIFEST = Path(__file__).with_name("models_manifest.json")


def key(groups: str, repo: str, revision: str, folder: str | None) -> str:
    """A PINNED row's name in the manifest (GEM-X's rows share repo and group, Pixal3D's repo and folder);
    a new pin has none until `--manifest` runs again."""
    return f"{repo}@{revision[:12]} {groups} -> {folder or 'HF cache'}"


def rows() -> list[tuple[str, str]]:
    """(groups, manifest key) of every PINNED and FILES row."""
    return [(g, key(g, r, rev, d)) for g, r, rev, d, _ in PINNED] + [
        (g, f"file:{p}") for g, _, _, p in FILES
    ]


def sizes() -> dict[str, int]:
    """What each group puts on disk, in bytes; a group counts the entries it shares with others in full."""
    m = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {}
    out: dict[str, int] = {}
    for groups, k in rows():
        for g in groups.split():
            out[g] = out.get(g, 0) + sum(size for size, _ in m.get(k, {}).values())
    return out


def write_manifest() -> None:
    from huggingface_hub import HfApi
    from huggingface_hub.hf_api import RepoFile
    from huggingface_hub.utils import filter_repo_objects

    api, out = HfApi(), {}
    for groups, repo, revision, folder, filters in PINNED:
        print(f"{repo} @ {revision[:7]}", flush=True)
        tree = api.list_repo_tree(repo, revision=revision, recursive=True)
        files = {f.path: f for f in tree if isinstance(f, RepoFile)}
        keep = filter_repo_objects(files, **(filters or {}))  # what snapshot_download fetches
        out[key(groups, repo, revision, folder)] = {
            p: [files[p].size, files[p].lfs.sha256 if files[p].lfs else None] for p in sorted(keep)
        }
    for _, _, sha256, path in FILES:
        data = (MODELS / path).read_bytes()  # the pinned file, installed here: its size
        if hashlib.sha256(data).hexdigest() != sha256:
            raise RuntimeError(f"{path} is not the pinned file: install its group again first")
        out[f"file:{path}"] = {"": [len(data), sha256]}
    MANIFEST.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")


SIZES = sizes()


def fetch(url: str, sha256: str, dest: Path) -> None:
    if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == sha256:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = urllib.request.urlopen(url, timeout=120).read()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise RuntimeError(f"{url}: SHA-256 mismatch, not saved")
    dest.write_bytes(data)


def main(groups: list[str]) -> int:
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import GatedRepoError

    failed = []
    for group, repo, revision, folder, filters in PINNED:
        if groups and not set(group.split()) & set(groups):
            continue
        print(f"{repo} @ {revision[:7]} -> {MODELS / folder if folder else 'HF cache'}", flush=True)
        try:
            snapshot_download(
                repo,
                revision=revision,
                local_dir=MODELS / folder if folder else None,
                **(filters or {}),
            )
        except GatedRepoError:
            print(
                f"  gated: accept the licence at https://huggingface.co/{repo}, run `hf auth login`, retry"
            )
            failed.append(repo)
    for group, url, sha256, path in FILES:
        if not groups or group in groups:
            print(f"{url} -> {MODELS / path}", flush=True)
            fetch(url, sha256, MODELS / path)
    return 1 if failed else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--manifest"]:
        sys.exit(write_manifest())
    sys.exit(main(sys.argv[1:]))
