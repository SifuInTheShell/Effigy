# 15 · A 2D studio: pictures, pixel art, sprites and their animation (research + plan)

Owner, 2026-10-12: "make this into a full 2d/3d studio … generate 2d assets as well … pictures / wallpapers … other
2d assets and animate those … think of a local aseprite … sprites, pixelart and the like including animation … as
an example, the assets of the classical Double Dragon brawler." Research only, by five agents in parallel (this repo;
2D stills and editing; pixel art and the brawler spec; 2D animation routes; editors, formats and engine ingestion);
every licence read at its primary source (HF licence field, GitHub LICENSE, EULA page) on 2026-10-12 unless dated.
Owner, later the same day: **everything purely local** — no external editor (Aseprite, Pixelorama and the like are
*orientation* for workflows, never launched, embedded or written to); and 2D assets are *created* here, while
animating a picture (video models) is left to the user's own tools. Sprite animation from the 3D rig and hand-made
frames in the in-app editor stay in scope. Revised 2026-11-27 (§5.7): procedural *skeletal* animation of Effigy's own
assets (Spine and engine 2D skeletons, research 20) is in scope; video-model animation stays out. Decisions the owner must make: §9. The build plan: §8 (proposed
**M50–M58**, not yet in IMPLEMENTATION.md §4; its §3.2 rows are listed in §8.0 as one contract PR). Nothing here has
run on the laptop; every VRAM number is the source's or marked (est.).

## 1. Bottom line

1. **Character sprites come from the 3D characters Effigy already makes.** Render the rigged, animated character
   (`rigged_anim.glb` + the 178 CC0 clips, Kimodo text-to-motion, GEM-X video-to-motion) in headless Blender with an
   orthographic camera at a brawler's tilt, flat shading, no anti-aliasing, one fixed pixels-per-metre, then
   pixelize with one locked palette per character. Every 2D-native alternative (frame-by-frame diffusion with pose
   control, "sprite sheet" LoRAs, video models) drifts in identity across the ~120 frames a brawler hero needs; the
   evidence is in §4.4. This is also the lazy route: a new GPL `blender_ops` renderer around `render_look.py`'s clip
   stepping, ~200 lines, no model, no download, no VRAM.
2. **Pixel art is post-processing, not a model.** No open model emits a true pixel grid; all of them (Z-Image and
   FLUX.2-klein pixel LoRAs included) produce 512–1024 px "faux pixels" that need grid detection, downscale and
   palette quantisation. That is ~100 lines of Pillow + numpy (`Image.quantize(palette=…)`, a Bayer matrix), CPU,
   `LIGHT`, and the same code serves the Blender renders. Add Retro-Diffusion's `pixel-art-fixer` or Astropulse's
   `pixeldetector` (both MIT) only if the in-house grid detector fails on real outputs.
3. **Pictures, wallpapers, backgrounds, UI, portraits: the resident Z-Image-Turbo, as a new `op` of `concepts`.**
   Any aspect ratio, 1–4 images, an optional Apache-2.0 style LoRA (pixel art, painterly), cut-out through BiRefNet.
   Nothing released in 2026 beats it for 1024-class stills on 16 GB; the one new MIT model worth a test slot is
   HiDream-O1 (§4.1). Seamless tiles: generate, roll by half, inpaint the seam with the diffusers-native
   `ZImageInpaintPipeline` (zero downloads), check the seam numerically.
4. **Animating a picture is out of scope (owner).** Effigy creates the 2D assets; a user who wants a background loop or
   a video from a picture uses their own tools. For the record (§4.3): Wan2.2 (Apache-2.0) is the only clean,
   16-GB-feasible video family if that ever changes; LTX-2.x, Stability's SVD and CogVideoX carry revenue or
   registration clauses; ToonCrafter's weights are non-commercial by lineage; everything Hunyuan is out. What stays
   in scope as animation: sprite frames rendered from the 3D rig (item 1) and frames drawn by hand in the editor (item 5).
   Revised 2026-11-27 (§5.7): skeletal animation (bones, meshes, keyframed clips; Spine, Godot `Skeleton2D`) of
   Effigy's own pictures, layers and characters is in scope too (research 20); video models stay out.
5. **"A local Aseprite" = our own pixel editor in the app, purely local (owner).** No embeddable web editor is both
   permissively licensed and alive (Piskel: Apache-2.0, a 2012 grunt app, no embedding API; Lospec: GPL,
   discontinued), and no external program is launched or written to. So: a React + canvas editor on the frames of a
   version (pencil, eraser, fill, line, rect, select/move, mirror, palette-locked drawing, frames timeline, onion
   skin, tags, per-frame durations, play), saving as a new version through the `pixelize` step's `edit` op; estimate
   2.5–4k lines of TypeScript, split over two milestones (M56, M57). Aseprite, Pixelorama and Piskel are studied for
   their workflows and conventions only (§4.5).
6. **Exports for Godot 4.7 first**: PNG sheet + Aseprite JSON (the lingua franca), a `SpriteFrames` `.tres` (format
   verified against a real Godot 4 file), a `TileSet` `.tres`, a `Parallax2D` scene; a `.aseprite` file for Unity's
   official importer (a *file*, written locally with the Apache-2.0 `aseprite` PyPI package; no Aseprite program
   involved); `.paper2dsprites` for Unreal.
   `CREDITS.txt` / `LICENSES.json` as today, the lineage does the rest.

## 2. What the owner's example needs (a Double Dragon-class brawler)

Sourced numbers are in the pixel-art agent's spec; the recommendation column is ours.

| Asset | Classic reference | Recommended Effigy default | Route (§5) |
|---|---|---|---|
| Playfield | arcade 256×240 / CPS-1 384×224 / Genesis 320×224; Fight'N Rage 354×200 | **640×360** base viewport, integer-scaled | – |
| Hero sprite | SOR2 Axel 77 px on 224, SNES Final Fight Cody 88 px on 224 (≈ ⅓ screen) | **100–110 px tall**, 128×128 cell (160 for jump/weapon/throw), 15 + 1 colours (31 + 1 "modern"), 12 fps base, bottom-centre pivot | 3D character chain → `sprites` → `pixelize` |
| Enemies / bosses | same families, bosses 1.5–2× | 90–130 px / 150–200 px; palette swaps for variants | same; `pixelize` `palette_map` |
| Animation set | OpenBOR `e_animations`; a 120-frame/18-anim commercial pack | idle 4–6, walk 6–8, run 6, jump 6–8 + land 2, attack1/2 3–4 each, attack3 6–8, jumpattack 6–8, runattack 4–6, grab 1–2, grabattack 3, throw 5–6, pain 2–3, fall 8–12, rise 6–8, die 6–8, pickup/carry/throw-object 1/7/5, special 6–10, block 2, per weapon 2 × 3–4 | Mesh2Motion clips where they exist; **Kimodo text-to-motion for the rest** ("a straight punch", "a roundhouse kick", "knocked down, gets up"); GEM-X from a video of someone performing the move |
| View | straight-on side view, depth by y-sorting only; no sourced tilt | orthographic, **pitch 10–15°** (start; tune by eye), yaw 90° (right), mirror in the engine; optional front/back | `sprites` params |
| Props, weapons, breakables | bat, knife, whip, oil drum, dynamite, crates, rocks; 32–64 px, 2–3 states + 4–6 debris | the 3D prop chain → `sprites` (1 or N yaws) → `pixelize`; states by hand in the editor | `game-prop` chain + `sprites` |
| Backgrounds | stages 7–24 screens wide (Final Fight R2 2587 px, DD "New York" 5216 px), 3 scroll layers on CPS-1 | 8–16 screens, **3–5 parallax layers**, floor band tiles 32 px | `image` (wide aspect) → `layers` → `tile` |
| Tiles | 8/16/32 px hardware tiles | 32 px tiles, 16 px details | `tile` op → `pixelize` → `TileSet` |
| HUD, portraits, font | 32×32 (Genesis) to 48–64 px portraits; 8×8 bitmap fonts | `image` + `pixelize`; PICO-8's font is CC0 | `image` op |
| VFX | hit spark 16–32 px × 3–4 f, dust 16 px × 3–4 f, KO stars, flash | hand-animated in the in-app editor (frame timeline, onion skin), or `image` stills → `pixelize` as the first frame | editor |

Godot 4 pixel-perfect settings (docs): `display/window/size/viewport_width=640`, `viewport_height=360`, window
override 1280×720; `stretch/mode=viewport`, `aspect=keep`, `scale_mode=integer`;
`rendering/textures/canvas_textures/default_texture_filter=0` (Nearest); `rendering/2d/snap/snap_2d_transforms_to_pixel=true`;
exclusive fullscreen. The `godot-2d` export writes these as a `project.godot` snippet in a README, not into the user's project.

## 3. What we have that a 2D track reuses (repo facts, `file:line` as of 841ca25)

- **An image-only version already flows end to end.** `server/library.py` requires no mesh: `outputs` is a free map,
  `write_meta` (291-326) unions `licenses`/`restricted` over the parent chain, `import_file` (410-451) makes an
  `upload` root with `image.<ext>`. Only export and validate take the first `.glb` (export.py:279, validate.py:39),
  and the UI's `hasGlb`/`mainFile`/`exportPick` (workflow.ts:5-8, 98-101) assume one.
- **Thumbs and `look` fall back to pictures** (thumbs.py:57-74 order: `preview`/`thumb` output → first `.glb` →
  `cand_*` grid → `image` → the parent's; look.py:157-178 tiles picture outputs). Both resize with LANCZOS
  (thumbs.py:27-38), which blurs pixel art: a 2D version writes `preview.png` and both learn NEAREST for it.
- **Z-Image-Turbo is resident only under `concepts`** (jobs.py:24 `SERVE`; `_key` 295-299 is `(step, variant)`);
  `refine` cold-loads its own pipeline (~26 s). `concepts.py` hard-codes 1024² (81-82), 9 steps, guidance 0, no
  negative prompt (Turbo has no CFG), fp8 layerwise casting (49), prompts encoded with the DiT off the card (67-76);
  its gates (`_image.gates`) are tuned to one centred 3D subject. Prompt templates: `brief.py:11-22`,
  `concepts.VARIANTS`/`FRAME` (23-36), mirrored in main.tsx:895 and 2439.
- **A ready ControlNet wrapper**: `refine._Tile` (291-340) builds `ZImageControlNetModel` + `ZImageControlNetPipeline`
  with fp8 casting; `__call__` (342-406) takes a 1024² control image, `strength`, `scale`, `seed`; the `_generator`
  seam (409-411) is how tests fake it. Only the Tile weights are downloaded (group `refine`, 2.0 GB lite).
- **Cut-out, pose, depth, upscale**: `_birefnet.matte` (soft alpha → threshold for pixels), `_pose.keypoints`
  (DWPose, CPU, (133,2) + scores: QA of a generated figure, not conditioning), MoGe-2 (group `faithful`),
  `_sr.upscale` (Real-ESRGAN ×4, RGB only: alpha handled separately; group `texture`).
- **Blender clip rendering**: `blender_ops/render_look.py` imports with `import_meshes(merge=False)` (167), picks a
  clip by action name (173-183), steps `scene.frame_set(f)` (228-229), frames one bound over all shown frames
  (190-205), orthographic camera tracked from a direction (214, 231-232), Workbench with `render_aa="8"` and a
  studio light (221-227). `render_ortho.py:146` and `parts_views.py:105` set `film_transparent`. `clips.json` =
  `{"clips": [{"name", "frames", "seconds"}], "fps"}` (retarget.py:294-295). A step calls Blender through
  `steps/_blender.run_blender` (15-54).
- **Export plumbing**: `steps/export.py` `PROFILES` (48), `run` (268-300) writes `<out>/<profile>/…` then
  `CREDITS.txt` from `credits(meta["licenses"], rows)` (60-67, raises on a name without a `licenses.yaml` row) and
  `LICENSES.json`; `export_copy` (library.py:164-206) and the UI batch export copy a profile folder wholesale.
- **Registering a step** (no registry): `steps/<name>.py` ending in `main(run)` appears in `/api/steps`
  (app.py:264-269) and MCP `describe_steps`; by hand: `jobs.LIGHT`/`SERVE`/`STEP_VENV`, `presets.VRAM_GB` (an
  unmeasured non-LIGHT step claims the whole budget, jobs.py:487), `workflow.ts` `STAGES`/`STEPS`/`label`
  (42-141), `main.tsx` `Actions` (1827-), `mcp/effigy_server.py` `STEP_LABELS` (95-114), `mcp/SKILL.md`,
  IMPLEMENTATION §3.2 and §4, `licenses.yaml`, `tools/download_models.py` groups, `server/install.py`,
  `CAPABILITIES` (main.tsx:4035-4044).
- **Editors that save versions exist**: the skeleton and marker editors save `rig_fit` overrides, the animation
  editor saves `anim_edit` versions through `--params @<file>` (§3.1, M37; the backend writes `_params.json` into
  `--out`): the pixel editor saves the same way (§5.6), no new route.
- Surprises: `canon` re-poses by a reference *image* (`prompts/apose_ref.png`), not by keypoints; MCP `import_file`
  always runs `prep` on a picture (effigy_server.py:452), wrong for wallpapers and tiles; `Import.tsx` accepts
  models only (pictures go through `NewAsset`); `useHistory` (hooks.ts) is a reusable undo stack; `Viewer.ts` has
  no image plane (the image branch of `ViewerPane` is an `<img className="overlay checker">`, main.tsx:3818-3822).

## 4. Landscape, with licences (2026-10-12)

Verdicts follow PLAN §6: *allowed*, *flagged* (allowed with a credit or a provenance note shown on the asset),
*undecided* (a licence family not on the list; owner), *never*.

### 4.1 Stills and editing

| Candidate | What | Code / weights | Verdict | 16 GB | Note |
|---|---|---|---|---|---|
| **Z-Image-Turbo** (in) | T2I 6B, 8–9 steps, no CFG | Apache-2.0 | allowed | 8.4 GB fp8 resident (m10) | the workhorse; any aspect by parametrising concepts.py:81-82 |
| **Z-Image (base)**, 2026-01-27 | the undistilled 6B: CFG, negative prompts, the LoRA-training target | Apache-2.0 | allowed | ≈ Turbo (est.) | add only with LoRA training (M58) |
| Z-Image-Edit / Omni | – | – | **not released** (GitHub "to be released"; HF org holds 2 models) | – | sites offering it run no official checkpoint |
| **FLUX.2-klein-4B** (in) | T2I, instruction edit, multi-reference | Apache-2.0 | allowed | ≈13 GB (BFL) | the editor we have ("remove the hammer", pose by reference) |
| Qwen-Image-Edit-2511 | 20B instruction edit, the last Apache checkpoint | Apache-2.0 | allowed, heavy | GGUF Q4 ≈12 GB transformer + encoder offload (est.); Nunchaku FP4 has Windows wheels only for torch 2.10/2.11 | second-choice editor if klein falls short; Qwen-Image 2.1 is `qwen-research` → **never** |
| HiDream-O1-Image / Dev, 2026-05 | 8–9B pixel-native unified T2I + edit + multi-ref | MIT | allowed, caveat | FP8 ≈10 GB (community claim) | the one new MIT model worth a test; its text encoder may carry Gemma terms (card vs ComfyUI package disagree) |
| Ming-Image-0.1-Design(-Layer), 2026-09 | 6B UI/poster T2I with native RGBA, layer output | MIT | allowed, fit unknown | validated on 80 GiB only | test for UI/icons only if klein + cut-out disappoints |
| SDXL 1.0 | utility base: LayerDiffuse, IP-Adapter, circular padding | OpenRAIL++ | allowed | ≈7 GB | add only for LayerDiffuse (§4.1 transparency) |
| SD3.5, SDXL-Turbo, SVD | – | Stability AI Community License | **undecided → recommend no** | – | free under US$1 M revenue incl. affiliates, terminates the day you exceed it, "Powered by Stability AI" notice mandatory |
| Sana 1.5, Lumina-Image 2.0 | T2I with a Gemma-2 encoder | Apache-2.0 (+ Gemma terms; Sana card says "research purposes only") | flagged | fits | no gain over Z-Image |
| GLM-Image, LongCat-Image, Chroma1-HD, ERNIE-Image, Cosmos-Predict2, JoyAI, HiDream-I1/E1 | – | permissive | allowed, **no fit** or marginal | 17–26 GB with offload | skip |
| **Fun-ControlNet-Union 2.1 (2602 build)** | Canny/Depth/**Pose**/MLSD/HED/Scribble/Gray + **inpaint** for Z-Image-Turbo; lite 1.9 GB | Apache-2.0 | allowed | + ~2–4 GB on Turbo | the Tile mode is in; Pose and inpaint need the other files (same repo, same row) |
| elismasilva z-image-control-turbo-unified-8-steps-v2 | diffusers port of the above incl. inpaint modes | Apache-2.0 | allowed | card: bf16 24 GB+, GGUF 6–8 GB → fp8 on 16 (est.) | or extend `refine._Tile` ourselves |
| IP-Adapter / reference adapters for Z-Image | – | – | **none exist** (HF adapter listing) | – | consistency = LoRAs, klein multi-reference, or SDXL + h94/IP-Adapter (Apache-2.0) |
| ai-toolkit (ostris) | LoRA training: Z-Image Turbo/base, klein-base-4B | MIT | allowed | 12–16 GB with offload | a house style from the owner's own art; OneTrainer is AGPL → separate process only |
| Style LoRAs with a licence field | tarn59 pixel art (Z-Image), renderartist Classic-Painting (Z-Image, trained on public-domain Art Institute works), ostris children's drawings (Z-Image), Limbicnation / adirik pixel art and fal sprite-sheet (klein-4B) | Apache-2.0 | allowed, **flag provenance** where self-asserted | +170 MB each | most HF adapters have no licence field; **Civitai has none → unlicensed → never** |
| LayerDiffuse-SDXL (rootonchair diffusers port MIT) | real partial alpha: glows, smoke, glass, particles | OpenRAIL-M | allowed | SDXL + ~1 GB | the only route to soft alpha; FLUX-LayerDiffuse needs FLUX.1-dev → **never** |
| Qwen-Image-Layered | picture → RGBA layers, 20B | Apache-2.0 | allowed, tight | GGUF Q4 ≈13 GB (third party) | a decomposer; `layers` (§5.5) does it with depth + inpaint instead |
| **stable-diffusion.cpp** `--circular` (2025-12-21) | true seamless tiling for Z-Image-Turbo (RoPE period trick + circular VAE) | MIT | allowed, subprocess | GGUF, small | the correct answer if roll-and-inpaint (§5.4) is not good enough; a second binary + a GGUF download |
| ControlTile (2026-06) | tiling LoRA | **no licence file** | unusable | – | – |
| Upscalers | SeedVR2-3B (Apache-2.0, diffusion SR, FP8 12–16 GB with block swap), DAT/HAT (Apache-2.0), RealPLKSR (MIT), Phhofm models (mostly CC-BY-4.0 → credit); spandrel core (MIT) loads them | allowed / flagged | – | for painterly art; **pixel art is never ML-upscaled** (integer nearest) |
| AuraSR | ×4 GAN | CC-BY-SA-4.0 (GitHub) vs apache-2.0 (v2 HF field) | **undecided** (conflict) | – | not needed |
| Kim2091 UltraSharp etc., SUPIR | – | CC-BY-NC-SA / custom NC | **never** | – | – |
| LaMa / Big-LaMa | smooth inpaint fill | Apache-2.0 (Places2 data → flag) | allowed/flagged | <2 GB | behind-layer fill if `ZImageInpaintPipeline` is not enough |

### 4.2 Pixel art

| Candidate | What | Licence | Verdict | Note |
|---|---|---|---|---|
| Pixel LoRAs on Z-Image-Turbo / klein-4B (above) | faux-pixel style; nerijs says ÷8, PixelArt Perfect ÷6, the klein cards are silent | Apache-2.0 | allowed, flag | every output needs scale detection, never a fixed divisor |
| **In-house pixelize** | grid detect (autocorrelation / gradient profile), dominant-or-median downscale, `Image.quantize(colors, method, palette=<palette image>, dither=NONE)`, Bayer dither in numpy, alpha threshold, 1-px outline by alpha dilation | – | the plan | Pillow's only dithers are NONE and FLOYDSTEINBERG; error diffusion crawls between frames → NONE or Bayer for animation |
| Retro-Diffusion/pixel-art-fixer (MIT, 2026), Astropulse/pixeldetector (MIT), marksverdhei/spritegrid (MIT, shared grid across frames), KennethJAllen/proper-pixel-art (MIT; fixes grid + palette once from sampled frames), PixelOE (Apache-2.0, pip `pixeloe`) | grid detection / pixelization libraries | permissive | allowed, **only if DIY fails** | pyxelate (MIT) drags scikit-image + scikit-learn; unfake.py (MIT) needs a Rust build |
| WuZongWei6/Pixelization ("Make Your Own Sprites") | learned pixelization | custom non-commercial | **never** | – |
| libimagequant / pngquant | best quantizer | GPL-3+ | separate process only; not needed | the stock Pillow wheel is not linked against it |
| Sprite Sheet Diffusion (2412.03685), SD-πXL | research | MIT / MIT | not usable | 150 training pairs and reported consistency failures; "24 GB, a few hours per image" |
| Retro Diffusion (Aseprite extension, ~$65), PixelLab ($12–50/month) | closed competitors | EULA / closed | reference only | PixelLab's own docs recommend "fixed head: always" to hold identity, i.e. even the best closed tool copies pixels |
| Palettes | **PICO-8: CC0** (Lexaloffle FAQ); DB16/DB32, Endesga: no formal licence ("totally free" forum posts) → flag + credit line; hardware palettes (NES, SNES, Genesis, CPS-1) are facts | – | ship PICO-8 + hardware palettes; DB32/Endesga flagged | `.gpl`, `.hex`, JASC `.pal` readers are ten lines each |

### 4.3 Animation of 2D

Researched before the owner's clarification (animating a picture is left to the user's tools): the video rows stay as
a record and as the trigger for a `ponytail:` row; **only the 3D → 2D route, the cutout rig and frame interpolation
of renders are candidates for the plan.**

| Route | Candidate | Licence | Verdict | 16 GB | Note |
|---|---|---|---|---|---|
| **3D → 2D** | in-house `blender_ops/render_sprites.py`: EEVEE Next shader-to-RGB toon or Workbench flat, Film filter 0.01 px, 1 sample (filter 0 renders pixelated but shows blurry in the viewport, bug #130669), Workbench AA "Off", textures "Closest", Freestyle or inverted-hull outline, Normal pass for 2D lighting | GPL (ours, in `blender_ops/`) | the plan | CPU | Sprite Sheet Generator 2.2.3 and Sprite Sheet Maker 5.3.1 (GPL-3, Blender 5.x, 2026) as references for JSON layout and 4-direction armature rotation; Dead Cells did exactly this (3ds Max → in-house batch renderer with a pixel shader, some frames hand-fixed) |
| video, loops/VFX/i2v | **Wan2.2-TI2V-5B** | Apache-2.0 | allowed | fp16 file ~9.3–10 GB; 4090 480p 5 s ≈ 4 min | diffusers `-Diffusers` repo; the first video step |
| video, quality | Wan2.2-I2V/T2V-A14B | Apache-2.0 | allowed | GGUF Q6_K ≈ 12 GB; Q8 runs on a 5060 Ti 16 GB (community) | ComfyUI-first; measure through diffusers GGUF loading |
| pose-driven from one picture | **Wan2.2-Animate-14B** (Nov 2025); Wan2.2-Fun-A14B-Control (pose/depth/canny + reference, tested on a 3060 12 GB with offload + qfloat8, 64 GB download) | Apache-2.0 | allowed, measure | README implies 24 GB; GGUF on 16 GB (community) | identity drift on long clips reported; a character sheet as reference recommended |
| 2D-style video | Index-AniSora V3.x (Wan2.2-based, anime-trained, first/mid/last-frame guidance) | Apache-2.0 + "Additional Restrictions" | flagged | "12 GB version" on ModelScope | the only video model trained for illustration; not diffusers |
| styly-agents/Wan2-2-pixel-animate | LoRA: pixel sprite → animation | Apache-2.0 | flagged (226 videos, provenance unstated) | as the 14B | record only (no video step) |
| LTX-2 / 2.3 / 2.5 | video + audio | LTX-2.x Community License (2026-08-11) | **undecided → recommend no** | distilled fp8 ≤ 4 s at ≤ 720p | royalty-free below US$10 M revenue incl. affiliates; bars products that "directly compete", derivatives share-alike, watermark/disclosure duties, indemnity |
| CogVideoX | – | custom (registration, ≤ 1 M visits/month) | not on the list → no | – | – |
| SVD-based (MimicMotion, StableAnimator, AniDoc) | – | own permissive + Stability base | undecided (Stability) → no | – | – |
| ToonCrafter | cartoon in-betweening | Apache-2.0 on paper; fine-tuned from DynamiCrafter-interp whose card is non-commercial | **treat as never** | 24–27 GB | ToonComposer (MIT) needs ~57 GB |
| UniAnimate-DiT, LVCD | – | **no licence file** | never | – | – |
| Champ | SMPL-guided | MIT | **never** (SMPL) | – | – |
| HunyuanVideo, FramePack, SkyReels-V1, InstantCharacter | – | Hunyuan | **never** | – | SkyReels-V2 (Skywork Community License) not needed |
| frame interpolation | Practical-RIFE 4.26 (MIT code + weights), FILM (Apache-2.0, archived) | allowed | – | <2 GB | for frame-doubling *rendered* sprites before pixelizing; no evidence any interpolator works on hard-edged pixel art; GIMM-VFI is S-Lab NC → never |
| **2D cutout rig** | Meta **Animated Drawings** (archived 2025-09): MIT end to end (detector + pose Apache-2.0 trained on the MIT Amateur Drawings set, numpy ARAP, BVH retarget configs; bundled BVH: CMU (free incl. commercial, acknowledgement) and Meta's own MIT clips) | MIT | allowed | CPU | reuse only the pure-Python ARAP + retarget; keypoints from our DWPose; **our 52-joint clips exported as BVH drive it** (the tool projects to 2D); Py 3.8/TorchServe parts are not needed |
| 2D cutout, anime | Stretchy Studio (MIT: layered PSD → auto-rig via DWPose + mesh deform), See-through (Apache-2.0, SIGGRAPH 2026: single anime image → inpainted part layers, SDXL-based) | allowed | – | SDXL class | later, if 2D-native characters are wanted |
| engine runtimes | Godot Skeleton2D/Bone2D/Polygon2D (MIT), Rive (MIT) | allowed | – | – | Spine: Effigy writes its documented JSON + atlas, never Spine's own code (research 20); DragonBones MIT but abandoned |

### 4.4 Why not 2D-native frame generation for characters (evidence)

Sprite Sheet Diffusion's authors: "struggles with … maintaining subject consistency and finer details";
dx8152 re-trained the Qwen multiple-angles LoRA after users reported instability; Qwen's consistency claims rest on
showcase images; PixelLab (the best closed tool) copies the head pixels to hold identity; Tribute Games hand-animated
every character; no independent 2025–26 post-mortem of a shipped game using per-frame diffusion was found. A brawler
hero is ~120 frames; a 2 % identity drift per frame is visible at 12 fps. The 3D route has none of this: one mesh,
one texture, one palette, one camera. Where diffusion helps characters: concept sheets and portraits (`image`), the
texture itself (`project`, `refine`), and *restyling a rendered frame* at low denoise with Tile + Pose control
(M58 (a), optional).

### 4.5 Editors (orientation only) and formats

Owner: no external editor is launched, embedded or written to. The editors below are studied for what a pixel
workflow needs (tools, frames, tags, onion skin, palettes, sheet conventions); the formats are what we write.

| Tool / format | Licence | Use | Facts an implementer needs |
|---|---|---|---|
| **Aseprite** 1.3.18.6 (2026-09-22) | EULA: source-available, paid binary, "may not distribute copies"; outputs unrestricted | **orientation only**: its tool set, tags, slices and JSON sheet conventions our editor and `sheet.json` mirror | CLI `aseprite -b in.aseprite --sheet out.png --data out.json --format json-array --sheet-type packed --list-tags --list-slices --list-layers` (+ `--split-layers`, `--trim`, `--merge-duplicates`, `--export-tileset`, `--save-as out.aseprite` from a PNG sequence with `{frame}`); Lua API has `WebSocket{}`, `sprite.events:on('change')`, `Sprite:saveCopyAs()`, `app.command.ExportSpriteSheet{…}`, extensions as `.aseprite-extension`; `app.isUIAvailable` is false under `-b` |
| **Pixelorama** 1.2.3 (2026-09-15) | MIT (Godot 4.7.2 app) | **orientation only**: an MIT reference for tool behaviour (pixel-perfect pencil, symmetry, tile mode) | CLI `--export -o out.png --spritesheet --json --frames a-b --scale f`; `.pxo` = zip (`data.json`, `image_data/frames/N/layer_M` raw RGBA8) readable with `zipfile` + Pillow; no event API |
| `aseprite` PyPI (StateOff/aseprite-py) | Apache-2.0 | import in `.venv`, for the `aseprite` export *file* (Unity's importer) | reads + writes `.aseprite`: layers, cels (zlib), tags, palette (chunk 0x2019), slices (9-patch, pivot), tilesets, user data; Python ≥ 3.12 |
| Piskel | Apache-2.0 | **not embedded** | no npm package, no embedding API, README refuses non-trivial PRs; `.piskel` JSON holds base64 PNG chunks |
| Lospec Pixel Editor, LibreSprite | GPL-3 / GPL-2 | no | discontinued / no batch CLI |
| simplepixelart (MIT, Vue), jvalen/pixel-art-react (MIT) | MIT | reference for a canvas core | the editor estimate: 2.5–4k lines TS |
| **Aseprite JSON** (json-array) | – | our sheet format | `frames:[{filename, frame:{x,y,w,h}, rotated, trimmed, spriteSourceSize, sourceSize, duration}]`, `meta:{image, size, scale, frameTags:[{name, from, to, direction, repeat?}], layers, slices:[{name, keys:[{frame, bounds, center?, pivot?}]}]}`; Godot Aseprite Wizard: fps = 1000/min(duration), `repeat != -1` → no loop |
| **Godot `SpriteFrames` `.tres`** | – | written by `godot-2d` | `[gd_resource type="SpriteFrames" format=3]` + `[ext_resource type="Texture2D" path="res://…/sheet.png" id="1"]` + `[sub_resource type="AtlasTexture" id="frame_0"] atlas = ExtResource("1") region = Rect2(x, y, w, h)` + `[resource] animations = [{"frames": [{"duration": 1.0, "texture": SubResource("frame_0")}], "loop": true, "name": &"walk", "speed": 12.0}]` (verified against a real 4.x file; `uid` optional; the 4.7 loop key `"loop"` vs `loop_mode` to diff against one saved by 4.7) |
| **Godot `TileSet` `.tres`** | – | written by `godot-2d` | `[sub_resource type="TileSetAtlasSource"] texture = ExtResource(…) texture_region_size = Vector2i(32, 32)` then `x:y/0 = 0` per tile, `x:y/0/physics_layer_0/polygon_0/points = PackedVector2Array(…)`; `[resource] tile_size = Vector2i(32, 32) sources/0 = SubResource(…)` (atlas part verified in godot#74356) |
| Godot 4.7 | – | – | no native Aseprite import (release notes); `Parallax2D` (4.3+: `scroll_scale`, `repeat_size`, `autoscroll`); `CanvasTexture{diffuse, normal, specular}` for 2D lighting; 9-slice via `NinePatchRect.patch_margin_*`; headless check `godot --headless --path <project> --import` + a GDScript asserting animation names/frames/fps |
| Unity 6 `com.unity.2d.aseprite` 1.2.x (6.1; 1.1.x for 6.0) | Unity Companion | we write the `.aseprite` | clips per tag, loop by default, `Repeat=1` one-shot, events via cel user data `event:Name`, Tile Set mode; **never hand-write `.meta` sprite slicing** |
| Unreal 5.8 Paper2D | engine | we write `.paper2dsprites` | TexturePacker-shape JSON (hash) + PNG → sprites + a flipbook; Tiled JSON → tile set + map; PaperZD v2.2.4 is MIT with 5.2–5.8 builds |
| Tiled 1.12.2 / LDtk 1.5.3 | app GPL, libtiled BSD / MIT | format-only, later | Tiled exports Godot 4 `.tscn` itself; LDtk is stale (2024-01) |
| Previews | Pillow | `preview.webp` animated (`save_all`, `duration`, `loop=0`), GIF, APNG | `look` returns a nearest-neighbour contact-sheet PNG (a model sees one frame of an animation anyway) |

## 5. Design: the 2D track inside Effigy

The same library, versions, jobs, GPU lock, exports and MCP tools; a second stage list in the UI; three new steps
(`pixelize`, `sprites`, `layers`) and two ops of `concepts` (`image`, `tile`), all through `_common.main(run)`,
`pixelize` and `sprites` without a model (`LIGHT`); one editor component.

### 5.1 A 2D asset

`asset.json` gains an optional `kind: "2d"` (additive; §3.2 says `asset.json` "may carry" fields). The **stage bar**
is chosen by `kind`; the **viewer** is chosen by the version's outputs (a `sprites` version inside a 3D asset shows
the frame viewer). 2D stages: Picture → Pixelize → Layers → Tiles → Edit → Check → Export. The 3D track
gets one more optional tab after Animate: **Sprites**. `NewAsset` gets a third mode ("A picture or sprite": prompt
→ `brief` (2D templates) → `concepts` op `image`; or a dropped picture → `upload` **without** `prep`). MCP
`import_file(kind="2d")` skips `prep` too.

### 5.2 `concepts` op `image` (stills; resident Z-Image-Turbo)

One handler, two ops: `run` (today) and `image`. Params: `prompt`, `width`/`height` (multiples of 64, ≤ 2048 on
the long side; aspect presets 16:9, 9:16, 1:1, 4:3), `count` 1–4, `seed`, `lora` (none | a bundled Apache-2.0 LoRA
name: `pixel`, `painting`, `drawing`), `lora_scale`, `cutout` (bool: BiRefNet, written into the alpha). No gates, no
ranking. Outputs `image_<k>.png`, `preview.png` (grid), and `image.png` = `image_0.png` so `prep`, `pixelize`,
`layers` find `image`. The 1024² constant becomes the op's size; the resident worker is the same key. VRAM at
1920×1088 is **not measured** (2.0× the latent area of 1024²; the fp8 DiT is 8.4 GB resident): the bench decides
whether `VRAM_GB["concepts"]` grows or the op caps the size. Wallpapers above that: `upscale` op (`_sr`, ×4 then
LANCZOS down to the target; alpha kept by `_sr`'s caller).

### 5.3 `pixelize` (LIGHT, CPU; `steps/_pixel.py` + `steps/_sheet.py`)

In: any version with `image`, `image_<k>`, `frames/` or `sheet.png` + `sheet.json`. Stages: (1) **scale**: `auto`
(grid period from the autocorrelation of the gradient image, cross-checked across all frames: one grid per version)
or an explicit `scale` or a target `height_px`; (2) **downscale** per cell: `dominant` (mode) for hard edges,
`median` for renders; (3) **alpha**: threshold at `alpha_cut` (default 0.5) → binary; (4) **palette**: `colors` (int,
k-means over *all* frames, median cut via `Image.quantize`) or a named palette (`pico-8`, `nes`, `snes-ish`,
`db32`†, `endesga-32`†) or a `.gpl`/`.hex`/`.pal` path; locked across the version's frames; `dither` `none` |
`bayer2` | `bayer4`; (5) `outline` (none | `#000` | darkest palette entry): 1-px alpha dilation; (6) `palette_map`
(`{"#rrggbb": "#rrggbb"}`) for palette swaps (enemy variants) with no re-quantisation. Out: `frames/<name>.png`
(same names as the input frames, or `image.png` for a single picture), `sheet.png` + `sheet.json` (Aseprite
json-array, tags from the input's clips), `palette.gpl`, `preview.png` (nearest ×4 contact sheet), `report.json`
(grid period, colours, alpha fringe pixels removed, frames). † flagged palettes write their credit line into
`CREDITS.txt` through a `licenses.yaml` `data` row. ~200 lines; tests on synthetic frames and `fixtures/characters`.

### 5.4 `sprites` (LIGHT, Blender CPU; `blender_ops/render_sprites.py`, GPL)

In: a version with `rigged_anim.glb` + `clips.json` (`anim`, `anim_edit`, `import_model`), or a static `mesh.glb`
(a prop: `yaws` frames of a turntable). Params: `clips` (list | all), `height_px` (the character's standing height,
default 104), `cell` (default 128; auto-grown per clip to the union bound, reported), `pitch_deg` (default 12),
`yaws` (default `[90]`; `[90, 270]`, 4 or 8 directions: the **armature** turns, the light stays), `fps` (default 12:
frames resampled from the clip's fps, `frame_set` on fractional frames), `shading` (`flat` | `toon` (EEVEE
shader-to-RGB + colour ramp, 3 bands) | `textured` (Workbench, as `render_look`)), `outline` (bool, Freestyle 1 px),
`shadow` (bool: a flat ground ellipse), `supersample` (1 | 2: render at ×2 and let `pixelize` downscale; default 1),
`normal_maps` (bool: a second view layer with the Normal pass, view space, X+/Y+/Z+; default false). Fixed rules:
orthographic, `ortho_scale` from `height_px` and the rig's standing height → **one pixels-per-metre per asset,
stored in `camera.json` and reused by every later `sprites` run on that asset** (consistent heights across clips and
re-runs); root motion removed (in-place) and the root snapped to the pixel grid per frame (no shimmer); `film_transparent`,
AA off, filter size 0.01, 1 sample, textures "Closest", standard view transform; render at the final size. Out:
`frames/<clip>_<yaw>_<NNNN>.png`, `sheet.png`, `sheet.json` (one `frameTag` per clip and yaw, `duration` = 1000/fps,
`direction: forward`, `repeat` 1 for one-shots the clip metadata marks), `camera.json`, `preview.png`, optional
`normals/` + `normals.png`. Then `pixelize` (palette lock, outline) and `export godot-2d`. Time: `render_look`
renders a Workbench frame in ~0.75 s (3 s for four, M40); a 120-frame hero ≈ 2 min (est.).

### 5.5 `layers` (GPU: MoGe-2 + BiRefNet + inpaint; parallax from one picture) and `concepts` op `tile`

`layers`: depth (MoGe-2, group `faithful`) quantised into `count` (2–5) bands with the subject mask (BiRefNet) as its
own layer; masks dilated; each hole filled with `ZImageInpaintPipeline` (diffusers-native, the resident weights,
no download) and the asset's prompt; out `layer_<k>.png` (far → near), `layers.json` (`scroll_scale` per layer,
0.1 … 1.0 from the depth median), `preview.png`. LaMa (Apache-2.0, flag) only if the Z-Image fill is poor. **`tile`
op** (resident worker): generate at `size` (256–1024 square), roll by (W/2, H/2), inpaint a cross-shaped seam band
(10 % of the size) with the same prompt, roll back; `report.json` `seam_ratio` = mean |∇| on the wrapped seam lines
over the interior mean, pass ≤ 1.1; `preview.png` = 2×2 tiling; then `pixelize` and a `TileSet` export.
stable-diffusion.cpp `--circular` is the fallback if `seam_ratio` stays > 1.1 (a `ponytail:` row).

### 5.6 The pixel editor (in-app, purely local)

`ui/src/pixel/` beside `ui/src/anim/` (the animation editor is the pattern: a timeline, edits as a layer over the
input, `useHistory`, save through a step). **Data model**: the version's `frames/` as RGBA `ImageData` at sprite size
(64–256 px; 512 cap), one flat layer per frame (layers: a later milestone), the version's `palette.gpl` as the only
colours a tool may place (indexed drawing without an indexed file format: the quantiser already ran), `sheet.json`
tags as animations with per-frame durations. **Canvas**: one `<canvas>` per view at integer zoom
(`image-rendering: pixelated`), pan, a pixel grid above ×8, a checkerboard, the onion skin as two translucent
canvases (previous/next frame, tinted), symmetry axes. **Tools** (M56): pencil (pixel-perfect: the corner pixel of
a diagonal dropped, as Aseprite's option), eraser, contiguous and global fill, line, rect and ellipse (outline,
filled), eyedropper, rectangular select → move / flip H/V / rotate 90 / cut / copy / paste, mirror-X drawing, a
1-px outline command, the palette swap (M51's form). Every stroke is one `useHistory` entry (frame index + the
changed pixels, not whole frames). **Animation** (M57): the frames strip with thumbnails, add / duplicate / delete /
reorder / set duration, tags (name, from, to, loop or one-shot), play at the tag's fps in the viewer, onion-skin
range and tint, a loop-point marker; keyboard: `,` `.` frames, `1`–`9` zoom, the usual tool letters. **Save**: the
job `pixelize` with `op: "edit"` and `--params @_params.json` holding `{"frames": {name: base64 PNG}, "tags": [...],
"palette": [...], "durations": {...}}` (a 120-frame 128 px sheet is a few hundred KB; `anim_edit` already passes long
params this way) → a new version `NNN_pixelize` (the outputs of §5.3, `metrics.edited_frames`,
`metrics.changed_pixels`), parent = the opened version, `provenance: step`. The editor keeps its history open until
the version event arrives (UI-001's rule). **Not in v1** (`ponytail:` rows): layers and blend modes, a lasso,
dither and gradient brushes, tile mode, reference layers, 9-slice slices, a brush wider than 1 px.

### 5.7 Animating a picture: skeletal in scope, video out

Owner, 2026-10-12: Effigy creates the 2D assets; animating a background or a picture is the user's own tool's job.
No `video` step, no Wan2.2 download, no video group on the Setup page.

**Revised by the owner, 2026-11-27:** *skeletal* animation of Effigy's own 2D assets is in scope: pictures,
symbols, `layers` stacks and characters cut into parts, rigged with 2D bones and meshes, and given keyframed clips
(procedural or converted from the 3D rig's clips), exported for Spine (its documented JSON + atlas; never Spine's own
code) and engine 2D skeletons (Godot `Skeleton2D`). The plan is
research 20. Still out: video-model animation (the paragraph below), and in-app animation editing beyond choosing
and tuning generated clips: a timeline, curve or weight editor is the Spine editor's job, as Blender is for 3D
(PLAN §1: not a full DCC tool). The research stays in §4.3; the trigger for
a `ponytail:` row is the owner asking for loops or VFX from a prompt (then: Wan2.2-TI2V-5B through diffusers in
`.venv-ml`, measured first). Animated previews of a sheet (`preview.webp`, Pillow `save_all`) are still written by
`_sheet.py` for the browser and the agent.

### 5.8 Export, validate, MCP

`export`: profiles `godot-2d` (`sheet.png`, `sheet.json`, `<asset>.tres` SpriteFrames, `tiles.tres` for a tile
version, `parallax.tscn` for a layers version, `README.txt` with the pixel-perfect project settings), `aseprite`
(`<asset>.aseprite` + `sheet.png` + `sheet.json`: a file for Unity's importer), `unreal-2d` (`<asset>.paper2dsprites`
json-hash + PNG); `CREDITS.txt`/`LICENSES.json` unchanged. `validate` on a 2D version writes `checklist.json`:
uniform cells, binary alpha, palette ≤ N, no stray islands < 2 px, sheet ≤ 4096 and a power of two (warn), every
tag ≥ 1 frame, `seam_ratio` for tiles, layer count for parallax. MCP: `look` shows `preview.png` nearest; `read_file`
reads `sheet.json`, `report.json`, `layers.json`; `describe_steps` picks the new docstrings up; `import_file(kind=)`;
`mcp/SKILL.md` gets "A brawler hero in one session" (concepts → … → anim → sprites → pixelize → export godot-2d).

### 5.9 What is deliberately not in the plan (`ponytail:` rows)

| Cut | Add when |
|---|---|
| Restyling rendered frames with Tile + Pose control, or a 2D cutout rig (Animated Drawings' ARAP) | M52's renders are judged not good enough for a 2D-drawn look (M58) |
| Layers, lasso, dither/gradient brushes, tile mode in the editor | the owner asks after M57 |
| A `video` step (Wan2.2) for loops, VFX, "animate this picture" | the owner reverses the 2026-10-12 decision |
| Any launch of, or file exchange with, an external editor | never (owner: purely local) |
| stable-diffusion.cpp for seamless tiles | roll-and-inpaint's `seam_ratio` stays > 1.1 on real tiles |
| LayerDiffuse-SDXL (soft alpha) | a glow/smoke sprite is needed that a cut-out cannot give (adds SDXL, 7 GB) |
| LoRA training (ai-toolkit) | the owner wants a house style from their own art (needs Z-Image base, 16 GB) |
| Tiled/LDtk level files | a level editor is wanted; the engine's own editors do it today |
| Normal-mapped sprites | 2D lighting is wanted in the game (`normal_maps` param exists, default off) |
| 8-direction sprites | a top-down game; a brawler mirrors one side view |
| HiDream-O1 / Ming-Design trials | klein-4B edits or UI icons disappoint |

## 6. VRAM, RAM, time (what to expect; none measured yet)

| Step | Model | VRAM | Host RAM | Time | Source |
|---|---|---|---|---|---|
| `concepts` `image` 1024² | Z-Image-Turbo fp8, resident | 8.4 GB | – | ~17 s per image warm (m10: 4 in 68 s) | measured for `run` |
| `concepts` `image` 1920×1088 | same | **?** (latent area ×2) | – | ~2–3× | measure |
| `pixelize`, `sprites`, `sheet`, `validate` | none / Blender CPU | 0 | small | seconds; a 120-frame hero ≈ 2 min (est.) | LIGHT |
| `layers` | MoGe-2 + BiRefNet + Z-Image inpaint | ≤ Z-Image resident + 3.3 (prep) | – | ~1 min | measure |
| `tile` | Z-Image-Turbo inpaint | as `image` | – | 2 images | measure |
| editor save (`pixelize` `edit`) | none | 0 | small | seconds | LIGHT |

Rules that already hold: one GPU job at a time; a step not in `VRAM_GB` claims the whole budget until measured;
the resident `concepts` worker is evicted by any non-LIGHT, non-resident step (`layers` loads MoGe-2 and BiRefNet in
its own process, and the next picture pays the Z-Image cold load again: acceptable; a second resident family is a
`ponytail:` row).

## 7. Licence rows this plan adds (`licenses.yaml`)

| Row | Kind | Licence | Flag |
|---|---|---|---|
| `aseprite` (PyPI, StateOff/aseprite-py) | code | Apache-2.0 | writes the `.aseprite` export *file* for Unity's importer; no Aseprite program is involved |
| `tarn59/pixel_art_style_lora_z_image_turbo` | weights | Apache-2.0 | provenance self-asserted → flag |
| `renderartist/Classic-Painting-Z-Image-Turbo-LoRA` | weights | Apache-2.0 | trained on public-domain works |
| `ostris/z_image_turbo_childrens_drawings` | weights | Apache-2.0 | – |
| PICO-8 palette | data | CC0 | – |
| DB32 (DawnBringer), Endesga-32 | data | no formal licence, "free to use" | flag + credit line in `CREDITS.txt` (or drop them) |
| hardware palettes (NES, SNES, Genesis, CPS-1) | data | facts | – |
| `alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1` Pose + inpaint files | weights | Apache-2.0 | existing row, new files (M58 (a) only) |
| M58 candidates: LaMa (Places2 → flag), SeedVR2-3B, Practical-RIFE, Animated Drawings (MIT) | weights / code | Apache-2.0 / MIT | each only with its milestone |

Never (add to the `NEVER` list in `tests/test_licenses.py` where a name pattern fits): Stability Community License
models, LTX-2.x, CogVideoX, ToonCrafter/DynamiCrafter, HunyuanVideo, FramePack, SkyReels-V1, InstantCharacter,
PuLID-FLUX, DreamO, UNO, FLUX.2-klein-9B LoRAs, Pixelization (WuZongWei6), GIMM-VFI, SUPIR, Kim2091 models,
Qwen-Image-2.1, any Civitai upload without a licence field, ControlTile, UniAnimate-DiT, LVCD, Champ.

## 8. Build plan (proposed milestones M50–M58; one per branch and PR; IMPLEMENTATION §7 template)

### 8.0 The contract PR (first, alone)

§3.2 additions, all additive: `asset.json` optional `kind: "2d"`; fixed-output rows for `concepts (image)`,
`concepts (tile)`, `pixelize` (ops `run` and `edit`), `sprites`, `layers`, the 2D `validate` items and the three
export profiles (the names in §5). `library.list_assets` passes
`kind` through; `thumbs` and `look` render a `preview.png` output with NEAREST when `report.json`/`sheet.json` is
beside it (or always for `preview.png` of these steps). Tests: `test_library.py` (kind round trip), `test_thumbs.py`
(nearest). No model, no GPU.

### 8.1 M50 Pictures: `concepts` op `image`, the 2D asset and its viewer (S–M)

- Files: `steps/concepts.py` (the op, size, LoRA, cutout; the gates untouched for `run`), `steps/brief.py` (2D
  templates: wallpaper, background, portrait, icon, tile), `server/presets.py` (`PRESETS["concepts"]` image aspects;
  `VRAM_GB` after the bench), `tools/download_models.py` (the three LoRAs into group `concepts`), `licenses.yaml`,
  `ui/src/workflow.ts` (2D `STAGES`), `ui/src/pixel/View.tsx` (zoom/pan nearest viewer; the frame scrubber lands in M51),
  `ui/src/main.tsx` (`NewAsset` 2D mode without `prep`, `Actions` panel, `ViewerPane` image branch), `mcp/effigy_server.py`
  (`import_file(kind=)`, `STEP_LABELS`), `mcp/SKILL.md`, docs. Tests: the op through `_generator`-style fakes
  (`concepts` gets the same seam `refine` has), the size/aspect validation (`bad_params`), the UI build + a
  Playwright spec (a 2D asset shows the 2D tabs).
- Acceptance (laptop): four 1920×1088 wallpapers from one prompt warm (seconds, peak VRAM recorded → `VRAM_GB`);
  a 1024² portrait with `lora: pixel` and `cutout`; a 2D asset created from the UI and over MCP without a `prep`
  version; the Picture tab shows it crisp at any zoom.

### 8.2 M51 Pixelize and sheets (S–M, CPU only)

- Files: `steps/_pixel.py`, `steps/_sheet.py` (pack, Aseprite JSON write/read, animated WebP preview),
  `steps/pixelize.py`, `steps/palettes/*.gpl` (PICO-8, hardware; DB32/Endesga if the owner keeps them),
  `server/jobs.py` (`LIGHT`), `licenses.yaml` (palette rows), `ui/src/pixel/View.tsx` (frame scrubber, onion skin,
  palette strip, palette-swap form), `workflow.ts`/`main.tsx` (panel), MCP labels, docs. Tests
  (`tests/test_pixelize.py`): a synthetic ×6 faux-pixel image → grid 6 found, exact colours; 3 frames share one
  palette; alpha binary; Bayer deterministic; `palette_map` swaps and keeps the rest; `sheet.json` round trip and
  Godot-Wizard-style tag/duration fields; a `.gpl`/`.hex` reader.
- Acceptance: a Z-Image `lora: pixel` portrait pixelized to 64 px with PICO-8 (before/after sheet in
  `docs/bench/m51.md`); the owner's judgement on `dominant` vs `median`; a palette swap of a sprite in one click.

### 8.3 M52 Sprites from the 3D character (M)

- Files: `blender_ops/render_sprites.py` (GPL), `steps/sprites.py`, `server/jobs.py` (`LIGHT`), `workflow.ts`
  (the Sprites tab after Animate), `main.tsx` (panel: clips multi-select, height, pitch, yaws, shading, outline),
  MCP, docs. Tests: `tests/test_sprites.py` on `fixtures/anim.glb` with Blender (skipped without, as
  `test_blender_ops.py`): frame count = seconds × fps per clip, every frame the same cell, RGBA with a transparent
  border, `camera.json` reused on a second run (same pixels-per-metre), the sheet's tags match `clips.json`.
- Acceptance (laptop): the viking (`bench_faithful_viking`) or the nurse: Idle, Walk, Run and a Kimodo "straight
  punch" and "roundhouse kick" at 104 px, pitch 12, yaw 90 → `pixelize` 15 + 1 colours with outline → the owner
  looks at the sheet and a GIF; no shimmer across Walk (the root snapped); seconds per frame recorded
  (`docs/bench/m52.md`, `m52_sheet.jpg`). This is the brawler-hero proof.

### 8.4 M53 Export `godot-2d`, `aseprite`, `unreal-2d`; 2D validate; the engine check (M)

- Files: `steps/export.py` (the 2D branch before the `.glb` requirement; `.tres` writers ~60 lines; the
  `aseprite` writer through the PyPI package; `.paper2dsprites`), `steps/validate.py` (2D items), `pyproject.toml`
  (`aseprite`), `licenses.yaml`, `workflow.ts` (`EXPORTS`, `CHECKS`, `exportPick` for sheets), MCP `PROFILES`,
  `tests/test_export_2d.py` (the `.tres` text against the verified shape; `.aseprite` re-read gives the frames and
  tags; credits for a sheet whose lineage holds flagged palettes), docs. Acceptance (laptop, `docs/bench/engine-import.md`
  grows a 2D section): Godot 4.7.1 headless `--import` of the hero's `godot-2d` folder + a GDScript asserting
  `SpriteFrames` animations, frame counts and fps; the `.aseprite` re-read by the PyPI package (a Unity import is the
  owner's editor check, deferred as the Unity Humanoid check was); Unreal 5.8 `.paper2dsprites` import → a flipbook
  (editor check, as the UE retarget was).

### 8.5 M54 Props and tiles (M)

- `sprites` on a static `mesh.glb` (turntable `yaws`, default `[90]`; 2–3 "states" are the editor's job); `concepts`
  op `tile` with `report.json` `seam_ratio`; `TileSet .tres` in `godot-2d` for a pixelized tile version; a
  `game-prop-2d` chain in `CHAINS` (prep → trellis2 → clean → remesh → uv → bake → sprites → pixelize) for
  `tools/bench.py`. Tests: the seam metric on a known-seamless vs a cut image; `TileSet` text. Acceptance: the
  bench barrel and a brick-wall tile set (the owner's `by02_wall…` asset) in Godot as a `TileMapLayer`.

### 8.6 M55 Parallax layers from one picture (M)

- `steps/layers.py` (MoGe-2 + BiRefNet + `ZImageInpaintPipeline`), `parallax.tscn` in `godot-2d`, the Layers tab
  with a scroll preview in `pixel/View.tsx` (CSS transforms, no three.js). Tests: band quantisation and `layers.json`
  from a synthetic depth; the `.tscn` text. Acceptance: a 1920×1088 brawler street background → 4 layers, the
  revealed background plausible, scrolled in Godot (`Parallax2D`); seconds and VRAM → `VRAM_GB["layers"]`.

### 8.7 M56 The pixel editor, part 1: drawing and saving (M–L)

- Files: `ui/src/pixel/Editor.tsx`, `Canvas.tsx`, `tools.ts` (pure functions on `ImageData`: pencil with the
  pixel-perfect rule, fill, line, rect, ellipse, select/move/flip/rotate), `palette.ts`; `steps/pixelize.py` op
  `edit` (frames from `--params @file`, the input's tags and palette kept unless sent, `metrics.edited_frames`,
  `changed_pixels`); `main.tsx` (the Edit tab for any version with `frames/` + `sheet.json`, in 2D and 3D assets);
  `workflow.ts`; MCP (`describe_steps` picks the op up; `STEP_LABELS`); docs. Tests: `tests/test_pixelize.py` grows
  the `edit` op (frames in → outputs out, a colour outside the palette refused with `bad_params`, metrics count the
  changed pixels); `ui/tests/pixel.spec.ts` (Playwright on a fixture sheet: draw a line, fill, undo/redo,
  select-move, save → the version appears; the editor keeps its edits when the save is refused, UI-001's rule).
- Acceptance (this checkout suffices, no GPU): a 128 px, 8-frame fixture sheet edited through the UI at ×8 zoom;
  the saved version's pixels differ exactly where drawn (`look` before/after); the canvas stays fluid on the owner's
  laptop at ×16 with onion skin off; the owner draws a hit-spark frame by hand.

### 8.8 M57 The pixel editor, part 2: animation (M)

- Files: `ui/src/pixel/Timeline.tsx` (the frames strip: add / duplicate / delete / reorder / durations), `Tags.tsx`
  (name, from–to, loop or one-shot), the onion skin (range, tint, previous/next canvases), play/stop at the tag's
  fps in the viewer, the loop-point marker, keyboard shortcuts; `steps/_sheet.py` writes the edited tags and
  durations back into `sheet.json`; `export godot-2d` honours per-frame durations (`SpriteFrames` relative
  durations, the Wizard's rule). Tests: tag/duration round trip; a reordered sheet re-packs in order; Playwright:
  add a frame, set a duration, play, save. Acceptance: a 4-frame hit spark and a 3-state breakable barrel drawn
  from scratch in the app, tagged, exported for Godot and shown as an `AnimatedSprite2D`; the hero's `sprites`
  Walk re-timed (one frame held longer) without re-rendering.

### 8.9 M58 Optional: a 2D-drawn look for characters, and a house-style LoRA (L; evidence-gated)

Only if M52's renders are judged too "3D" for the owner's art direction, or a style of the owner's own is wanted.
(a) Restyle M52 frames at low denoise with Tile + Pose control (`refine._Tile` generalised, the 2602 Union files,
the existing licence row), palette-locked afterwards: identity holds because the render, not the model, carries
it. (b) Animated Drawings' ARAP + retarget (MIT, the pure-Python parts vendored) driven by our 52-joint clips
exported as BVH (`blender_ops/bvh_clip.py` reversed), for a character that exists only as a picture (`image` op);
keypoints from our DWPose. (c) ai-toolkit (MIT) as a separate process for a style LoRA on Z-Image base from the
owner's own art. Each has its own bench and licence rows (§7). No video model.

### 8.10 Order and sizing

8.0 → M50 → M51 → **M52** (the proof) → M53 → M54 → M55 → M56 → M57 → (M58). M50–M53 give a brawler hero from
a sentence to a Godot `SpriteFrames` in one session; M54–M57 give the stage, the props and the hand-drawn rest.
Sizes are relative to past milestones: S ≈ M44, M ≈ M23/M31, L ≈ M37. Every GPU acceptance runs on the reference
laptop; this checkout (`C:\CODE\Effigy`, no models, no GPU) can build and test M51, M53's writers, M56, M57 and the UI.

## 9. Decisions for the owner

1. **Characters from 3D, 2D-native only as M58**: confirm (recommended).
2. **Licence families not on the list**: Stability AI Community (SD3.5, SVD-based tools), LTX-2.x Community,
   CogVideoX, AuraSR (conflicting), SkyReels (Skywork Community) — none is needed by this plan; recommended **no**
   on the record, so nobody reopens them.
3. **Palettes**: PICO-8 (CC0) and hardware palettes only, or also DB32/Endesga with a credit line.
4. **The editor's v1 scope**: one flat layer per frame, palette-locked drawing, the M56 tool list; layers, lasso and
   brushes only when asked (§5.6).
5. **Defaults**: 640×360, hero 104 px, 128 px cell, pitch 12°, 12 fps, 15 + 1 colours (§2) — to tune on M52's first sheet.
6. **Model trials worth a slot**: HiDream-O1 (MIT) for stills/edits — or not at all until a need appears.
7. **Where the plan lands**: PLAN §7 gets a "Phase 2D" and §3 a stage row per 2D step; IMPLEMENTATION §3.2 the
   rows of §8.0 and §4 the milestones; `licenses.yaml` the rows of §7. This note stays the rationale.

## 10. Open questions (unverified, to settle in the first milestones)

- Z-Image-Turbo at 1920×1088: VRAM and whether 8–9 steps hold composition at that aspect (M50 bench).
- Workbench AA "Off" / EEVEE filter 0.01 in Blender 5.2's Python names; `frame_set` at fractional frames for fps
  resampling (M52).
- Godot 4.7's serialized `SpriteFrames` loop key (`"loop"` vs `loop_mode`): save one from 4.7 and diff (M53).
- Whether Paper2D accepts a plain TexturePacker json-hash renamed `.paper2dsprites` (M53, editor check).
- HiDream-O1's text encoder licence (Gemma terms or Apache); AuraSR's licence conflict.
- DB32/Endesga: no formal licence text anywhere; "free to use" is a forum statement.
- `aseprite-py`'s release cadence, and whether Unity 6's importer accepts its output unchanged (M53).
- The editor: canvas performance at ×16 with the onion skin on a 256 px frame; whether `--params @file` with ~120
  base64 frames parses well under a second (else a multipart upload route); the pixel-perfect pencil's exact rule
  on direction changes (M56).
- `prep`'s soft BiRefNet alpha on pixel art: the `alpha_cut` default (M51).

## Sources (read 2026-10-12)

Models: huggingface.co/Tongyi-MAI (Z-Image, Z-Image-Turbo), github.com/Tongyi-MAI/Z-Image, huggingface.co/alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1,
huggingface.co/elismasilva/z-image-control-turbo-unified-8-steps-v2, huggingface.co/black-forest-labs/FLUX.2-klein-4B, docs.bfl.ml/flux_2/flux2_klein_training,
huggingface.co/Qwen/Qwen-Image-Edit-2511, huggingface.co/Qwen/Qwen-Image-2.1, huggingface.co/Qwen/Qwen-Image-Layered, huggingface.co/HiDream-ai/HiDream-O1-Image,
docs.comfy.org/tutorials/image/hidream/hidream-o1, huggingface.co/inclusionAI/Ming-Image-0.1-Design, stability.ai/community-license-agreement,
huggingface.co/LayerDiffusion/layerdiffusion-v1, github.com/rootonchair/diffuser_layerdiffuse, github.com/leejet/stable-diffusion.cpp/pull/914,
github.com/junrongh/ControlTile, github.com/numz/ComfyUI-SeedVR2_VideoUpscaler, github.com/fal-ai/aura-sr, github.com/advimman/lama, github.com/ostris/ai-toolkit,
github.com/Nerogar/OneTrainer, huggingface.co/tarn59/pixel_art_style_lora_z_image_turbo, huggingface.co/reverentelusarca/elusarca-pixel-art-style-lora-zimage-turbo,
civitai.com/models/2481158, huggingface.co/Limbicnation/pixel-art-lora, huggingface.co/adirik/pixel-art-lora-flux.2-klein-4B, huggingface.co/fal/flux-2-klein-4b-spritesheet-lora,
huggingface.co/nerijs/pixel-art-xl, huggingface.co/renderartist/Classic-Painting-Z-Image-Turbo-LoRA, huggingface.co/h94/IP-Adapter, huggingface.co/xinsir/controlnet-openpose-sdxl-1.0,
huggingface.co/dx8152/Qwen-Edit-2509-Multiple-angles, github.com/VectorSpaceLab/OmniGen2, huggingface.co/bytedance-research/UNO.
Pixel art: github.com/chenganhsieh/Sprite-Sheet-Diffusion, github.com/AlexandreBinninger/SD-piXL, astropulse.itch.io/retrodiffusion, github.com/Retro-Diffusion/pixel-art-fixer,
github.com/Astropulse/pixeldetector, github.com/painebenjamin/unfake.py, github.com/marksverdhei/spritegrid, github.com/KennethJAllen/proper-pixel-art, github.com/KohakuBlueleaf/PixelOE,
github.com/sedthh/pyxelate, github.com/WuZongWei6/Pixelization, github.com/ImageOptim/libimagequant, pixellab.ai, lexaloffle.com (PICO-8 FAQ), pillow.readthedocs.io (quantize, GIF/APNG/WebP).
Animation: huggingface.co/Wan-AI (Wan2.2-TI2V-5B, A14B, Animate-14B, Animate-2-14B, Wan-Dancer), huggingface.co/alibaba-pai/Wan2.2-Fun-A14B-Control,
huggingface.co/styly-agents/Wan2-2-pixel-animate, github.com/bilibili/Index-anisora, huggingface.co/Lightricks/LTX-2.5, github.com/Lightricks/LTX-2/blob/main/LICENSE-2_x,
huggingface.co/zai-org/CogVideoX1.5-5B, github.com/Doubiiu/ToonCrafter, github.com/TencentARC/ToonComposer, github.com/ali-vilab/UniAnimate-DiT, github.com/Tencent/MimicMotion,
github.com/Francis-Rings/StableAnimator, github.com/fudan-generative-vision/champ, github.com/hzwer/Practical-RIFE, github.com/google-research/frame-interpolation,
github.com/GSeanCDAT/GIMM-VFI, github.com/facebookresearch/AnimatedDrawings, github.com/MangoLion/stretchystudio, github.com/shitagaki-lab/see-through,
github.com/facebookresearch/sam3, extensions.blender.org/add-ons/sprite-sheet-generator, extensions.blender.org/add-ons/sprite-sheet-maker, Vasseur, "Art Design Deep Dive: Dead Cells" (Game Developer),
github.com/woct0rdho/SageAttention/releases, esotericsoftware.com/spine-editor-license, github.com/rive-app/rive-runtime.
Editors and formats: github.com/aseprite/aseprite (EULA.txt, docs/ase-file-specs.md, gpl-palette-extension.md), aseprite.org/docs/cli, github.com/aseprite/api,
github.com/StateOff/aseprite-py, github.com/Orama-Interactive/Pixelorama, github.com/piskelapp/piskel, github.com/lospec/pixel-editor, github.com/LibreSprite/LibreSprite,
github.com/comficker/simplepixelart, github.com/jvalen/pixel-art-react, github.com/viniciusgerevini/godot-aseprite-wizard, docs.unity3d.com/Packages/com.unity.2d.aseprite@1.2,
dev.epicgames.com/documentation/en-us/unreal-engine/import-sprites-in-unreal-engine, github.com/heavybullets/PaperZD, github.com/mapeditor/tiled, github.com/Kiamo2/YATI,
github.com/deepnight/ldtk, github.com/heygleeson/godot-ldtk-importer, github.com/Cammin/LDtkToUnity, godotengine/godot#74356 (TileSet .tres), godotengine/godot#130669 (filter size 0),
docs.godotengine.org (Parallax2D, CanvasTexture, project settings for pixel art).
Brawler spec: pixelatedarcade.com (Double Dragon), nesmaps.com, Wikipedia (CP System, console palettes), gameanim.com (SOR2 / Final Fight heights), vgmaps.com (stage sizes),
OpenBOR `e_animations`, Nintendo Life (River City Girls), Steam community threads (Fight'N Rage 354×200), tvtropes.org (three-quarters view).
