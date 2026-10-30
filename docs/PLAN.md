# Effigy: Master Plan

> **A fully local, open-source "Meshy.ai": image(s)/text → textured, retopologized, rigged and
> animated 3D assets, built around Microsoft TRELLIS.2, on a consumer NVIDIA GPU.**
>
> Status: decisions and roadmap, kept current; the build state is in [IMPLEMENTATION.md](IMPLEMENTATION.md) §4. Research backing every decision lives in [`docs/research/`](research/).
> The system design is in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. Vision and scope

**Problem.** Meshy, Tripo and Rodin turn a picture into a game-ready, rigged character in minutes,
but they're cloud-only, credit-metered, and the assets live on someone else's server. The open
models to do this locally now exist (TRELLIS.2, Pixal3D, SkinTokens, Kimodo, …). What's missing is
the **product glue**: a guided pipeline, retopology, baking, rigging, retargeting, exports,
VRAM management, packaging and licence hygiene.

**Product.** A desktop app (local web UI) that runs these stages with sensible presets and lets you
step back to any earlier stage:

```
 Create ──► Generate ──► Clean ──► Remesh ──► UV ──► Texture/Bake ──► Rig ──► Animate ──► Export
 (image,    (TRELLIS.2,   (holes,   (tri/quad,  (auto/   (O-Voxel re-bake,   (template  (CC0 clips,  (GLB/FBX/
  multi-     Pixal3D,     scale,    target      seams)   normal/AO, image/   skeleton + text-to-    USDZ/OBJ/
  view,      Pixal3D-MV)  symmetry) polycount)           text retexture)     skinning)  motion)     STL/3MF)
  text)
```

**Primary use case: characters.** A front image or turnaround sheet becomes an A/T-pose character
with clean topology, PBR textures, a Mixamo/UE5/Unity-compatible skeleton, good skin weights and
an animation set, exported for Unity/Unreal/Godot/Blender. Props, creatures and print figures
come along with it.

### Goals
1. **Local and offline** after installation; nothing leaves the machine.
2. **16 GB consumer NVIDIA GPU is the hard target** (RTX 4060 Ti 16G, 4070 Ti Super, 4080/5080,
   5060 Ti 16G, 5070 Ti). **Every default preset must complete on 16 GB**, with one heavy model
   resident at a time. 8–12 GB cards are best effort with reduced presets; 24 GB+ unlocks optional engines.
3. **Windows 11 first** (the reference machine), Linux second. One-click install with no admin
   rights, no Visual Studio and no CUDA toolkit.
4. **Meshy feature parity** for image/multi-image/text → 3D, remesh, retexture, rig, animate and
   export. See the [parity matrix](research/06-meshy-parity-and-landscape.md#1-meshy-as-of-sep-2026-the-spec-we-imitate).
5. **Commercial-clean default profile**: every default component is permissively licensed (§6).
6. **Reproducible, non-destructive**: every step is versioned with its seed and parameters.

### Non-goals (for now)
- Cloud hosting or SaaS, accounts, a community gallery.
- Training or fine-tuning models (we only consume released weights).
- AMD/Apple GPUs in the Python path. *They may come later through the native trellis2.c (Vulkan)
  backend.*
- A full DCC tool. Sculpting and manual retopo stay in Blender; we provide a Blender bridge.

---

## 2. Users and hardware tiers

### 2.1 Reference machine (the owner's hardware; everything is validated on it)

| Component | Spec | Consequence for the design |
|---|---|---|
| GPU | **NVIDIA GeForce RTX 5080 Laptop GPU**: Blackwell GB203, **16 GB GDDR7** (256-bit), 80–150 W TGP (+25 W Dynamic Boost), compute capability **12.0 (sm_120)**, 5th-gen tensor cores with FP8/FP4 | All CUDA code needs **CUDA ≥ 12.8 / PyTorch ≥ 2.7 (we pin torch 2.8 cu128, like the SPZ fork)**. Every custom extension wheel (cumesh, flex_gemm, o_voxel, natten, …) must be **built with sm_120 kernels**: no PTX-JIT fallback for fused ops. FP8 weights (e.g. TRELLIS.2-FP8) are a real speed and VRAM win on Blackwell. |
| Attention | flash-attn 2.x **fails to compile for sm_120** (Dao-AILab #2361); FlashAttention-4 doesn't support sm_120 | **Default attention backend = PyTorch SDPA (cuDNN)**, xformers as an alternative. flash-attn is only an optional, validated community wheel, never a hard dependency. |
| Incompatible stacks | UniRig needs spconv (no CUDA 12.8 builds); Puppeteer/MagicArticulate pin CUDA 11.8 | **SkinTokens** (cu128 build, RTX 50 confirmed) is the learned rigger. UniRig only if we build spconv for sm_120 ourselves. |
| OS | **Windows 11** | **Windows-first** packaging, CI and testing. Linux is second (same code, also used for CI GPU runners). |
| System RAM | **32 GB** | Models move between VRAM, RAM and NVMe. RAM "warm cache" budget **≈ 16 GB** (the rest is for OS, browser and Blender). Prefer **quantized/FP8 safetensors with mmap** for fast reloads. LLM/VLM helpers on CPU are limited to small (≤ 8B, 4-bit) models. Never rely on the Windows page file. |
| Laptop power | Performance drops sharply on battery and at low TGP; long runs throttle | Detect AC/battery and the power limit via NVML. Warn on battery. Offer a "quiet" profile (fewer steps, 1024) vs. a "max" profile. Persist checkpoints between stages so a throttled or suspended laptop doesn't lose work. |
| Display | If the dGPU drives the display (MUX/dGPU-only mode), Windows (WDDM) and the compositor take VRAM | Recommend hybrid/Optimus mode (iGPU drives the display). Set the **browser UI to run on the iGPU** (Windows Graphics settings → "Power saving") so the viewport doesn't compete for VRAM. The scheduler reads *free* VRAM at runtime, not the nominal 16 GB. |
| Blender | Cycles supports RTX 50 from 4.4; **5.2 OptiX needs driver ≥ 575** | **Pinned: Blender 5.2.2 LTS** (portable zip, downloaded on first run; Python 3.13; supported until Jul 2028). [Research 08](research/08-blender-integration.md) |

### 2.2 Tiers

| Tier | Example GPUs | Status | Generation | Texturing | Rigging / animation |
|---|---|---|---|---|---|
| **16 GB (reference)** | 4060 Ti 16G, 4070 Ti Super, 4080, 5060 Ti 16G, 5070 Ti, 5080 | **Hard requirement. All defaults are validated on the reference laptop (`bench.py`)** | TRELLIS.2 `1024_cascade`; `1536_cascade` with low-VRAM offload; Pixal3D/Pixal3D-MV (low-VRAM 1024) opt-in | voxel re-bake; TRELLIS.2 texturing @1024; text→image→texture | **SkinTokens** (≥14 GB, alone on the GPU); Kimodo with the text encoder on CPU (<3 GB) |
| 24–32 GB | 3090, 4090, 5090 | optional engines | everything at max, larger optional engines | + projection refine pass, optional plugins | everything, less offloading |
| 8–12 GB | 3060 12G, 4060, 4070 | best effort, reduced presets | TRELLIS.2 `512` / `1024_cascade` low-VRAM (SPZ-fork patches), FP8/GGUF | voxel re-bake; texturing @512 | template + classic weights (bone heat on proxy + robust transfer); UniRig |

**VRAM budget rule (16 GB):** the global GPU lock guarantees that only one heavy model is resident,
and no single step may plan more than ~14.5 GB. That leaves headroom for the desktop/compositor and the
browser viewport. LLM/VLM helpers run on CPU, or are unloaded before 3D stages.
**On any GPU (M45):** each step process may allocate what is free at its start − 0.8 GB (CUDA context + margin; ~14.7 GB on the idle laptop), and loads its models only while the host RAM they need is free (trellis2 ~20 GB, measured) and gets an
OOM past it instead of a spill; a step variant whose measured peak (`server/presets.VRAM_GB`) never fits is refused
with the lighter one named, and one that doesn't fit beside other programs right now is refused as "GPU busy".

Users: indie game devs, modders, VTuber/VRChat creators, 3D-print hobbyists, and small studios
who need commercial-safe outputs.

---

## 3. Pipeline stages and technology choices

Legend: **Primary** (default), *Fallback* (automatic on failure or low VRAM), `Plugin` (opt-in,
licence-flagged). Details and alternatives are in the linked research notes.

| # | Stage | Primary | Fallback | Plugins / later | Research |
|---|---|---|---|---|---|
| 0 | **Input prep** | BiRefNet (MIT) background removal, auto-crop/centre, shared framing for multi-view sheets, camera fit (MoGe-2, MIT) | manual mask/crop | Real-ESRGAN upscale, StableDelight delight | [02](research/02-pixal3d-algorithms.md), [04](research/04-texturing.md) |
| 0b | **Concept candidates / canonicalization** (prompt → 4 images → pick; pose → A/T-pose; side/back views) | **Z-Image-Turbo** (Apache-2.0) via `diffusers` in the ML venv; category prompt templates; BiRefNet-mask 3D-readiness gates | FLUX.2-klein-4B (Apache-2.0) | Qwen3.5-4B brief/variants + VQA scoring; Z-Image-Fun-ControlNet-Union 2.1 (Apache) with an A-pose skeleton; Qwen-Image-Edit-2511 + AnyPose + Multiple-Angles LoRA (Apache); **HiDream-O1-Image (MIT, skeleton + multi-reference conditioning) to bake off against AnyPose for re-posing** | [07](research/07-prompt-to-image-frontend.md), [05 §7](research/05-rigging-animation.md) |
| 1 | **Generate** (image → 3D) | **TRELLIS.2 runtime with two MIT weight sets: "Fast" = TRELLIS.2-4B, "Faithful" = Pixal3D** (pixel-aligned; ranks above TRELLIS.2 on 3D Arena). **Default: Faithful** (owner, 2026-10-04, after the M11 A/B on the laptop: closer to the image 7/10 vs 2/10, generate median 160 s vs 195 s and steadier; needs ~24 GB system RAM and 13.3 GB VRAM, `docs/bench/phase0.md`); Fast stays selectable and is the low-RAM / OOM fallback. **Vendored upstream `microsoft/TRELLIS.2` + patches donated by `localmesh-engine` (Apache-2.0: nvdiffrast-free `uvraster.py`, sm_120 SDPA shim, BiRefNet, staged loading) + the SPZ fork's chunked-decode commits**; persists the **O-Voxel attribute volume**. 1024 cascade is the ceiling on 16 GB; **Pixal3D preset on the laptop = 1024 cascade, 12 steps, 2K texture (~14 GB measured by its ComfyUI wrapper author; 16 steps/4K needs ~17 GB)** | 512 / FP8 on OOM | ~~TripoSG~~ (out 2026-10-08: HunyuanDiT code, §6) + TRELLIS.2 texturer; **experimental, restricted outputs:** Hunyuan3D-2.1 shape (+ Paint with offload) in an isolated engine (§6, IMPLEMENTATION MX1) | [01](research/01-trellis2-deep-dive.md), [02](research/02-pixal3d-algorithms.md), [09](research/09-generator-models-survey.md) |
| 1b | **Multi-view** (front/side/back) | **Pixal3D-MV** (4 views, explicit cameras) | TRELLIS.2 per-step multi-view conditioning blend (ComfyUI-Trellis2 approach, MIT) | – | [02 §4](research/02-pixal3d-algorithms.md) |
| 2 | **Cleanup** | CuMesh (MIT): fill holes, floaters, non-manifold repair, orientation; real-world scale and origin. **Mandatory**: raw O-Voxel meshes have hundreds of components and open shells | CuMesh DC remesh / Blender voxel remesh to force watertight | symmetry enforcement (mirror + weld) | [03](research/03-retopology-uv-baking.md) |
| 3 | **Remesh** | **Tri:** CuMesh `simplify` to the brief's budget. **Quad (Phase 2):** watertight → Blender **QuadriFlow** with symmetry (verified: 6.7k pure quads, 0 non-manifold on clean input) | **AutoRemesher 1.2 CLI (MIT; Aug 2026 release improved quad extraction)**: run both on organic shapes and keep the better result → CuMesh repair | CC0 **MakeHuman template fitting** for guaranteed joint/face loops; QuadWild-BiMDF (GPL, subprocess); learned retopo once a permissive one exists | [03](research/03-retopology-uv-baking.md) |
| 4 | **UV** | CuMesh GPU charting + xatlas packing (MIT); fine for props | Blender Unwrap **Minimum Stretch** + Pack Islands (subprocess) | seam heuristics from part/weight boundaries for characters | [03](research/03-retopology-uv-baking.md) |
| 5 | **Texture / bake** | **Re-bake PBR from the persisted O-Voxel volume** onto the final UVs (cage ray-cast + trilinear sample) + **normal/AO bake** from hi-poly. UV-space rasterization uses the vendored pure-torch `uvraster` (no nvdiffrast, D5) | closest-point bake (as upstream `to_glb`) | TRELLIS.2 shape-conditioned **retexture** (patched to keep UVs); text→image→texture; projection refine pass | [01 §4–5](research/01-trellis2-deep-dive.md), [04](research/04-texturing.md) |
| 6 | **Rig** | **Canonical skeleton fitting** (exactly the **52-joint Mixamo template**: 22 body + 30 fingers, the only skeleton SkinTokens' skin-only mode handles; keypoints → 3D, marker UI) + skin weights: **SkinTokens `--use_skeleton`** via the MIT **NoBlender** port (SDPA fallback, no flash-attn, no bpy server), in its **own venv + subprocess** because its point encoder is copied from a GPL-3 subtree. README says ≥14 GB but weights are 1.6 GB and a port reports ~4 GB: **measure** with `--num_beams 1` | **Blender: bone heat on a voxel-remeshed proxy → `DATA_TRANSFER` weights** (verified: 0 unweighted vertices; Robust Weight Transfer only if needed); UniRig for generic | none. **Make-It-Animatable is excluded** (v1 is trained on Mixamo data, which Adobe's terms forbid for ML training; v2 is built on Hunyuan) | [05](research/05-rigging-animation.md) |
| 7 | **Animate** | **Blender Retarget extension** (Expy Kit fork, GPL-3, in the Blender subprocess; own retargeter only if it falls short) + **bundled CC0/CC-BY clips** (Quaternius UAL 1/2, **KayKit** 150+, Mesh2Motion, 100STYLE via the CC-BY `100style-retarget` FBX set; each needs a bone-name remap to Mixamo; **Bones-SEED excluded**, not permissive) | – | **Kimodo** text-to-motion (SOMA, commercial OK, text encoder on CPU); **MoCapAnything V2** (MIT code + weights, video → BVH on any rig) for video-to-motion; user-imported Mixamo | [05 §4](research/05-rigging-animation.md) |
| 8 | **Export** | GLB (skinned + clips), OBJ, STL; FBX/USDZ/BLEND via **Blender subprocess**; 3MF via trimesh; profiles Unity/UE5/Godot/Mixamo | – | LODs + collision mesh, multi-colour 3MF, part split | [05 §5](research/05-rigging-animation.md), [06](research/06-meshy-parity-and-landscape.md) |

### The key architectural insight
TRELLIS.2 (and Pixal3D) decode to a **sparse PBR attribute volume**, and upstream's GLB export
just samples that volume at surface points. **We persist this volume with every generation**,
so we can re-bake full PBR onto *any later mesh* (decimated, quad-remeshed, hand-edited in Blender,
re-UV'd) in under a second, with no diffusion and no projection seams. Retopology, UV and texturing
therefore become independent, repeatable steps instead of one fragile chain.

---

## 4. Key decisions (ADR summary)

| ID | Decision | Rationale |
|---|---|---|
| D1 | **Each step is a subprocess** (`python -m steps.<name>`), one GPU step at a time (global lock). **Heavy model families (TRELLIS.2/Pixal3D) run as one resident worker** (`--serve`, JSON lines on stdin/stdout) that the backend keeps alive until another heavy step needs the GPU; everything else is one-shot. **One ML venv** (Python 3.12, torch 2.8 cu128), split only when pins conflict; **SkinTokens always gets its own venv** (torch 2.7 pin + GPL-provenance isolation) | TRELLIS.2's ~13 GB of weights cold-load in tens of seconds to minutes (plus FlexGEMM Triton autotune on first run), which every mature base avoids by staying resident; killing the worker still frees VRAM deterministically |
| D2 | **glTF/GLB is the canonical format**, Y-up, metres | Open, carries PBR + skins + animation; FBX only at export |
| D3 | **Persist the O-Voxel attribute volume** (`ovoxel.npz`) per generation | Enables re-bake onto any mesh (see §3 insight) |
| D4 | **Vendor upstream TRELLIS.2/Pixal3D and patch** (not pip-install, not the SPZ fork): keep-UV (upstream PR #173's two edits), PR #185's o-voxel C++ fixes, the int64 `svox2vert` fix, localmesh's rasterizer/SDPA/BiRefNet/staged-loading patches, SPZ's chunked decode | Upstream is Linux-only and stagnant (no code since Jan 2026; UV-keep and Windows/Blackwell PRs unmerged); the SPZ fork is stale since April with no sm_120 story |
| D5 | **No NC components in defaults, from day one**: RMBG-2.0 → BiRefNet; **nvdiffrast/nvdiffrec dropped entirely** (localmesh's pure-torch `uvraster.py` + a decoy `nvdiffrast` module so the stock `o_voxel` imports); no FLUX-dev; no SMPL/AMASS-derived models | Commercial-clean, and nvdiffrast was also the one component with no sm_120 wheel |
| D6 | **Blender = interactive, live-linked GUI session** (the owner's Blender MCP Pro add-on: `localhost:9877` socket, commands run on the main thread) **plus headless subprocess jobs** (`blender_ops/`, GPL) for reproducible operations (QuadriFlow, Smart UV, FBX/USD export, bake fallback) | Owner decision: work on the generated model in full Blender; every pull from Blender becomes a new version; GPL stays out-of-process |
| D7 | **Template-first humanoid rigging with Mixamo-compatible names** + export naming profiles | Matches Meshy/Tripo; makes animation libraries and engines work out of the box |
| D8 | **Canonicalize characters before 3D** (A-pose re-posing, optional turnaround) | Pose is the #1 cause of rigging failure; TRELLIS.2 keeps the input pose |
| D9 | **Own UI: Vite + React + TypeScript + plain three.js** (three addons for GLTF/HDRI/skeleton/animation), FastAPI backend, SSE for progress | Owner decision: one interface for the whole workflow; plain three.js keeps the dependency count low (r3f only if needed) |
| D10 | **`trellis2.c` native backend is deferred** | Only if the Python path's sm_120 Windows wheels can't be made to work. **We build the three CUDA wheels ourselves** (cumesh from main ≥ 2026-05-09 so the Blackwell stream fix is in, flex_gemm, o_voxel; cp312, torch 2.8 cu128, arch `8.9;12.0`); natten from a verified community cp312/cu128 build or our own |
| D11 | **`licenses.yaml` + one test** asserting defaults are commercial-OK; licence tags copied into each step's `meta.json` | Users must know when an asset touched NC or region-restricted components |
| D12 | **Agents via MCP, driven by the user** (M40 replaced the in-app "Ask Claude"): the app is an MCP server (`effigy`: library, steps, previews, import, export; stdio) that the user's own agent connects to: Claude Code or Desktop, Codex, Cursor, VS Code, Gemini (Setup "Connect an agent" gives each its config). The owner's Blender MCP Pro stays a second server for the live scene. Every step writes a new version, so nothing an agent does overwrites | Owner decision. Credentials are never handled by us. Exact CLI flags, auth and policy confirmed in research 10 |

---

## 5. UX outline

- **Create page**: drop an image (or 2–4 views, or a turnaround sheet that is auto-split), or type a
  prompt. Choose a **preset**:
  - *Game Character*: A-pose normalize → 1024 → quad 8–20k → 2K PBR → humanoid rig → 10 clips → FBX + GLB
  - *Hero Character*: Pixal3D/MV → 1536 → quad 30–50k → 4K → rig
  - *Game Prop*: tri 2–10k, 1–2K, no rig
  - *Print Figure*: watertight, no texture or vertex colour, STL/3MF, wall-thickness check
  - *Raw*: hi-poly only
- **Asset page**: a stepper (Generate → Clean → Remesh → UV → Texture → Rig → Animate → Export), each
  step with a *quick* preset and *advanced* parameters. A version timeline lets you branch from any
  version. The viewport has render modes (PBR, albedo, roughness, metallic, normal, wireframe, UV
  checker, weights heatmap, skeleton) and an animation player.
- **Rig step**: auto-fit, then Mixamo-style **draggable markers** (chin, wrists, elbows, knees,
  groin) with symmetry, then a deformation QA heatmap from a range-of-motion clip.
- **Library**: search, tags, thumbnails, licence badges, batch export.
- **Models & Licences page**: install/remove engines and weights, see licences and attribution
  strings, accept gated terms (DINOv3). One profile only (commercial, §6).

---

## 6. Licence policy

**Owner decision (2026-10-03): the assets go into the owner's own commercial game.** So everything the
tool uses must allow commercial use of its outputs, and there is a single profile: no "everything"
mode, no opt-in non-commercial components.

| Allowed | Allowed, flagged (shown on the asset) | Never used |
|---|---|---|
| MIT, Apache-2.0 (incl. localmesh-engine patches, NOTICE kept), BSD, zlib, CC0, Pixal3D (MIT since ~May 2026; stale EU flag on its HF card, noted), **DINOv3 License** (with "Built with DINOv3" + licence copy), OpenRAIL++ (SDXL; SD 2.x carries the same licence text, owner decision 2026-10-08, research 11), **Llama 3 Community License** (Meta-Llama-3-8B-Instruct as Kimodo's text encoder only; owner decision 2026-10-07, following Kimodo's own setup: gated download by the user, "Built with Meta Llama 3" on the Setup card and in CREDITS, commercial use below 700 M monthly users, the motions are outputs), NVIDIA Open Model License (Kimodo SOMA; **GEM-X** video-to-motion: Apache-2.0 code, synthetic NVIDIA-owned training data, SOMA output; owner decision 2026-10-08, research 12), SAM License | **CC-BY clips** (100STYLE, not bundled yet): fine commercially, **credit required in the game**, so the export would write it into `CREDITS.txt`. **SkinTokens** (MIT, but trained partly on ripped game models and VRoid characters): the rig QA also produces a Blender bone-heat result with no training-data provenance at all, so you can always choose that one. **CMU mocap** (optional download): fine inside a game, no resale of the raw data | RMBG-2.0 (CC BY-NC), nvdiffrast/nvdiffrec (NVIDIA NC), FLUX.1/2-dev + Kontext-dev + klein-9B (NC), Qwen-Image 2.1 (research), SMPL/SMPL-X/AMASS/HumanML3D-derived models, GVHMR, NVIDIA GEM / GENMO (SMPL, OneWay Noncommercial; not GEM-X), RigAnything, PartPacker, FastMesh, MeshAnything, PartField/PartUV, Bandai-Namco/LAFAN1 motion, Bones-SEED, **Make-It-Animatable** (Mixamo-trained), and **Hunyuan anywhere outside the experimental engine** (incl. HY-Motion, BPT, and everything built on Hunyuan: UltraShape, Make-It-Animatable v2, X-Part, SAM3D-Part) |

GPL/AGPL software (Blender, ComfyUI, StableProjectorz, StableGen) is used **only as separate
processes** or as design references. No code is copied.

> **Code derived from Hunyuan counts as Hunyuan (2026-10-08).** VAST-AI's **TripoSG** and **HoloPart** are MIT
> on their front page, but their transformer (`triposg_transformer.py`) is "based on Tencent HunyuanDiT …
> licensed under the Tencent Hunyuan Community License … portions copied or adapted", and HoloPart's
> attention carries FlashVDM (the same licence family). Same terms as below, so neither is used.

> **Hunyuan is experimental only (owner decision, 2026-10-03).** Every Hunyuan3D release (1, 2,
> 2mini, 2mv, 2.1, Omni, Part) and HY-Motion is under the *Tencent Hunyuan Community License*
> (rechecked 2026-10-03; no MIT/Apache relicensing like Pixal3D's). Running it locally or wrapping it in
> MIT code doesn't change that: our MIT licence covers only our own code, never Tencent's code or
> weights. Key terms:
> - The licence **"DOES NOT APPLY IN THE EUROPEAN UNION, UNITED KINGDOM AND SOUTH KOREA"**: users there
>   get no rights at all, not even for private local use.
> - §5(c): *"You must not use, reproduce, modify, distribute, or display the … Works, **Output** or
>   results … outside the Territory"*. So assets made with it can't be shown in a game sold in those
>   regions, wherever the developer lives.
> - §5(b): outputs must not be used to improve other AI models. §4: separate licence above 1M MAU.
> - §3 duties (licence copy, modified-file notices, a `Notice` file) apply when *distributing* the
>   Works. We never vendor or re-host Hunyuan code or weights, so they don't fall on this repo.
>
> **How the experimental engine is fenced** (details: IMPLEMENTATION MX1): its own venv
> (`.venv-hunyuan`), installed from upstream at a pinned commit only after the user enables it on the
> Models page, reads the quoted clauses, and confirms they're outside the EU/UK/South Korea (stored
> locally; no geo-detection, no network calls of ours). Weights come straight from Tencent's Hugging
> Face repos. It's never part of a preset or default chain. Every version derived from a Hunyuan
> output is marked **restricted** all the way down the chain; the UI shows a badge; `LICENSES.json`
> records the restriction; and export refuses restricted versions unless the user explicitly confirms
> the §5(c) text for that export. Intended use: comparisons and experiments, **not shipped game assets**.
>
> **Pixal3D** was relicensed to MIT in ~May 2026 (custom restrictive terms before). Its HF card still
> carries an `extra_gated_eu_disallowed` flag, but the repo isn't gated, so the flag is inert. We
> treat the MIT LICENSE as governing and keep the note visible on the Models page.
>
> **Every export carries its paper trail**: `CREDITS.txt` (attribution strings that apply) and
> `LICENSES.json` (every component that touched the asset, from `meta.json`), so the game's credits
> and any later licence question can be answered from the asset folder alone.

---

## 7. Roadmap

Rough sizing assumes 1–2 developers. Each phase ends with a demo build and a `bench.py` run on the
golden set (ARCHITECTURE §8).

### Phase 0: Foundations and spikes (≈2 weeks)
- Scaffold: `server.py` (FastAPI + SSE), `ui/` (Vite + React + three.js viewer), `steps/` (CLI modules),
  `blender_ops/` (GPL headless scripts), the ~30-line Blender MCP Pro socket client, the `effigy` MCP server,
  `library/` folders, `licenses.yaml` + `test_licenses.py` ([ARCHITECTURE §2](ARCHITECTURE.md#2-shape)).
- **Spike A**: vendored upstream TRELLIS.2 + localmesh/SPZ patches in `.venv-ml` (Python 3.12, torch 2.8
  cu128) on the **reference machine (RTX 5080 Laptop, Windows 11, 32 GB RAM)**. Build cumesh/flex_gemm/
  o_voxel for sm_120, SDPA shim, no nvdiffrast. Record peak VRAM, **cold-start time** and time per
  `pipeline_type`, on AC and on battery; bf16 with staged loading first, FP8 only if bf16 doesn't fit.
  **Day-one checks:** CuMesh `remesh_narrow_band_dc` fragmentation on sm_120 (issue #27; PR #29 may fix
  it) and the FlexGEMM `shape_slat_decoder` crash reported on RTX 50 (#32). Fallbacks: `remesh=False`
  path, `EXPLICIT_GEMM`.
  **A/B "Fast" (TRELLIS.2) vs "Faithful" (Pixal3D)** on 10 golden images; pick the default.
- **Spike B**: write `ovoxel.npz` during generation, then re-bake it onto a decimated mesh
  (normal map included). Validate against upstream `to_glb` output.
- **Spike C**: Blender on Windows: (1) headless GLB → QuadriFlow / Smart UV → FBX; (2) **live link**:
  open a version in GUI Blender, edit, pull back as a new version; (3) **Claude via MCP**: one preset
  ("make game-ready") run headless through the user's `claude` CLI against the live scene, with
  snapshot + accept/revert.
- **Exit:** image → GLB end-to-end from our React UI with progress, opened in Blender and pulled back
  as a new version, on the reference laptop.

### Phase 1: MVP "Image → game-ready static asset" (≈6 weeks)
- Input prep (BiRefNet, crop, shared framing), preview (512) → refine (1024/1536) flow.
- Cleanup (holes, floaters, scale, origin), **triangle remesh with target polycount**, UV, **re-bake
  + normal/AO**, glTF ORM packing.
- Text → concept image (Z-Image-Turbo or FLUX.2-klein-4B) → 3D.
- Exports GLB/OBJ/STL. Asset library with versions. Models & Licences page. Windows portable zip
  (Linux: run from source).
- **Exit:** 30-asset benchmark at ≥90% success. the 16 GB reference machine completes the *Game Prop*
  preset in under 5 min with peak VRAM ≤ 14.5 GB. Outputs open cleanly in Blender, Unity (glTFast) and Godot.

### Phase 2: Characters (≈8 weeks)
- **Pose canonicalization** (Qwen-Image-Edit + AnyPose → A-pose), turnaround generation and
  ingestion (Multiple-angles LoRA, shared framing, consistency check).
- **Multi-view** generation (Pixal3D-MV opt-in; TRELLIS.2 blend fallback).
- **Quad remesh** for characters (symmetry, target face count), seam-aware UVs.
- **Retexture**: TRELLIS.2 shape-conditioned texturing on any mesh (UV-keeping patch), image or
  text driven.
- **Rigging**: canonical skeleton, keypoint auto-fit + marker UI, SkinTokens or Blender proxy-heat +
  `DATA_TRANSFER` weights, 4-influence pruning, deformation QA.
- **Animation**: Blender Retarget extension + bundled CC0/CC-BY library, multi-clip export, FBX export
  profiles (Mixamo, UE5 Manny, Unity Humanoid) via Blender.
- **Exit:** 15 character inputs (photo, illustration, turnaround) → rigged + animated FBX that
  imports as a Unity Humanoid and retargets in UE5 with no manual fixes in ≥70% of cases; the
  rest fixable with markers in under 2 min.

### Phase 3: Breadth and quality (≈8 weeks)
- Quadruped/bird/creature templates (Mesh2Motion CC0; M36: fox, horse, bird, fitted, skinned and animated with
  their own clips; dragon, snake, spider, shark, kaiju the same way later: M41), SkinTokens full-rig mode for generic
  objects (M59, built 2026-10-10, dropped 2026-10-13 by the owner (SkinTokens' training data with no bone-heat alternative)).
- **Kimodo** text-to-motion (SOMA → canonical retarget; M26/M30); video-to-motion with **GEM-X** (research 12: spike passed on the laptop; owner 2026-10-08; M34), SAM 3D Body + SAM-Body4D as a later second engine: **licence checked** 2026-10-08 (research 12, `licenses.yaml`: SAM License, commercial use allowed, no EU block, military/ITAR/nuclear/weapons end uses barred; its code is vendored in `engine/gemx` for GEM-X's image features), **the engine itself not built** (decision 2026-10-10: GEM-X covers video-to-motion; build it when GEM-X fails a class of videos). 2026-10-29/30: every person in a video and a panning camera (M64), transitions between two clips by Kimodo (M65; the joined clip and a path viewer later, owner 2026-10-30), the arms kept out of the body after a retarget (M67).
- Projection refine/edit pass (texture edit with masks, seam healing), delighting, upscaling: M43a seam healing, M43b the detail pass, M43c delighting; 2026-10-10: the masked edit is **Redraw this view** in the Detail panel (the view window the user frames in the 3D view plus a description, through `refine`'s `views`), 4K a checkbox there (`texture_size: 4096`), the detail metric replaced by a structure-weighted one; Qwen-Edit stays out (research 14 §5: too slow on 16 GB). 2026-10-30, after the StableGen assessment (its methods only, GPL): M66 adds views where the auto views see too little and drops colour next to inner depth steps; M68 adds the painted masked edit, a part painted on the model redrawn by Z-Image's inpaint in every view that sees it, the frozen texture mask deciding every write (`refine` op `edit`).
- Part segmentation for rigid accessories and print split: **GeoSAM2 (Apache-2.0)**, a click on a rendered
  view, 1.8 GB (built in M31; SegviGen dropped on 2026-10-07: Linux, 24 GB, nvdiffrast and flash-attn);
  **HoloPart: out** (2026-10-08: its transformer is HunyuanDiT code under the Tencent Hunyuan Community
  License, EU-excluded, as Tencent X-Part); the print split closes the parts in the volume instead (M33). LODs + collision meshes.
- USDZ/BLEND/3MF exports, **multi-colour 3MF**, printability check/repair.
- CLI batch mode (**done** 2026-10-10: `tools.bench --export <profile> --to <folder>`, README "Batch from the command
  line"); optional own Blender bridge add-on (live selection/material sync): **not built** (decision 2026-10-10:
  ARCHITECTURE §4's trigger, live selection/material sync or an in-Blender "Send back" button, has not come up; Open and
  Pull over the Blender MCP Pro add-on cover the live link); public REST API, StableProjectorz shim only if another tool
  needs to drive Effigy (ARCHITECTURE §10; agents have M40's MCP server).

### Experimental track (any time after Phase 1)
- **Hunyuan3D-2.1 engine** (IMPLEMENTATION MX1): shape (~10 GB) and, if offloading keeps it under
  14.5 GB, Paint; outputs restricted (§6). Used to A/B against TRELLIS.2/Pixal3D on topology, the area
  where 3D Arena ranks Hunyuan highest.

### Phase 4: Later / research watch
- AI "artist mesh" retopology once a permissive, character-capable model exists (see [03](research/03-retopology-uv-baking.md)).
- Face rigs and blendshapes (OmniFaceRig-class), spring bones for hair and cloth.
- Native Vulkan backend for AMD (trellis2.c), Apple Silicon (trellis.cpp Metal).
- **Version 2: thin and dark features in generated geometry** (owner decision 2026-10-07: accepted for
  now, revisit in a second iteration of the app). The generator reads thin or dark features as empty
  space (hair strands with gaps, fingers as a mitten with spikes, a helmet's visor slits as deep sockets)
  and the 15k remesh widens the damage (`docs/bench/m23.md` "M23b", `m23b_stages.jpg`). Candidate
  fixes, measured where noted: a higher or region-wise face budget for characters (fingers; one number,
  or a two-pass decimation keeping hands and head dense); a morphological closing of the generator's
  voxel volume before the surface rebuild (hair; a finer rebuild at 1024 made holes instead, so this
  needs its own experiment); the 1536 generator profile (fingers, strands; likely beyond 16 GB, measure
  first; does not fix a visor read as holes); image-side fixes before generation (lighter slits,
  another seed: the only thing that helps the knight).

---

## 8. Top risks

| Risk | Impact | Mitigation |
|---|---|---|
| Windows sm_120 builds of CUDA extensions (cumesh, flex_gemm, o_voxel, natten) and FlexGEMM's Triton half (triton-windows) | install failures, silent kernel errors | our own CI-built wheels (cp312, torch 2.8 cu128, arch 8.9;12.0), hash-locked in `uv.lock`; SDPA shim; `EXPLICIT_GEMM` fallback; native trellis2.c backend (D10) as last resort |
| 16 GB budget exceeded by a single stage (Pixal3D at 16 steps/4K, 1536 runs) | slow/OOM | sequential residency, low-VRAM patches, FP8/GGUF, automatic downgrade on OOM |
| Generated characters in action poses, fused limbs, mitten hands | rigging fails | A-pose canonicalization before 3D, proxy-based weights, marker UI, hand fallback, QA heatmap |
| Licence drift (gates, territory clauses, provenance) | legal exposure | manifest + CI policy check, per-asset licence stamp, opt-in profiles, quarterly review |
| Upstream churn (TRELLIS.2 PRs unmerged, Blender 5 API breaks, HF repos deleted) | breakage | vendor + pin, checksum-verified mirrors where licences allow, contract tests per engine |
| Cold-start cost of 13–24 GB weight sets (TRELLIS.2, Pixal3D) on a laptop with 32 GB RAM | slow iteration | resident worker per heavy family (D1); bf16 + staged loading; measure in Spike A; Pixal3D stays the slower "Faithful" option |
| Prior art moving fast (3DGenStudio, trellis2.c, localmesh-engine, Meshy itself) | relevance | differentiate on MIT licence, commercial-clean defaults, character-first guided flow; reuse MIT projects rather than duplicating them |
| Texture detail ceiling (voxel resolution) | blurry at 4K | 1536 profile, projection refine pass, direct front-image projection |

---

## 9. Owner decisions (all resolved)

1. **Licence posture:** decided by owner: the assets go into a commercial game, so commercial-clean
   only, one profile, no non-commercial components (§6).
2. **Hunyuan:** decided by owner: an **isolated experimental engine** with restricted outputs (§6,
   IMPLEMENTATION MX1), not part of any preset. Pixal3D (MIT) stays the default "Faithful" model.
3. **Backend bet:** Python path first (decided, ponytail); `trellis2.c` only if the Windows sm_120
   wheels fail.
4. **UI:** own React + three.js interface (decided by owner).
5. **Blender + Claude:** decided by owner: interactive Blender with live link; Claude (own subscription) via MCP.

## 10. Immediate next steps
1. Phases 0–2 are closed; §7's Phase 3 list carries each item's state (built, decided, open). What is left of
   Phase 3 after 2026-10-10: the deferred owner checks (clean VM, UE5 retarget, a creature in an engine).
2. Open GPU checks live in [IMPLEMENTATION.md](IMPLEMENTATION.md)'s milestone sections ("As built", "Acceptance")
   and in HANDOVER §1; Phase 4 waits for its triggers (§7).
3. The golden benchmark set is `tests/fixtures/golden` (M11) and `tests/fixtures/characters` (M22).
