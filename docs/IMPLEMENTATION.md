# Effigy: Implementation Plan and Guard Rails

> Status: 2026-10-03, written after the review round. **This is the document coding agents follow.**
> It fixes the repo layout, the contracts between parts, and the order of milestones, each with a
> runnable acceptance check. Design rationale lives in [PLAN.md](PLAN.md), [ARCHITECTURE.md](ARCHITECTURE.md),
> [WORKFLOW.md](WORKFLOW.md) and [`research/`](research/). When this document and those disagree, **this
> document wins for code; PLAN.md wins for product decisions.** Change either by editing the doc in
> the same commit as the code.

---

## 0. How an agent works in this repo

**Read order before any task:** `CLAUDE.md` → this document §0–§3 → the milestone you're assigned
(§4) → the research note it links. Nothing else is required reading.

**Rules (non-negotiable):**
1. **Ponytail (full)** applies to every line of code (`.claude/skills/ponytail/SKILL.md`): smallest
   working change, stdlib and already-installed deps first, no abstractions for one use, no
   scaffolding "for later". Deliberate corners get a `# ponytail:` comment naming the ceiling.
2. **One milestone per branch/PR.** Don't start the next milestone in the same PR. Don't touch files
   outside the milestone's "Files" list unless the acceptance check needs it; say so in the PR.
3. **No new dependency without a row in `licenses.yaml`** (`commercial: true` for anything in the
   default profile). `python -m pytest tests/test_licenses.py` must pass. **The owner ships the assets
   in a commercial game**: every component must allow commercial use of its outputs. Never add:
   nvdiffrast, nvdiffrec, xformers, flash-attn, RMBG-2.0, FLUX.1/2-dev, Qwen-Image-2.1, anything
   Hunyuan or built on Hunyuan **outside the experimental engine (MX1)**, Make-It-Animatable, pymeshlab,
   MeshLib, PartField/PartUV, SMPL-family models (full list: PLAN §6 "Never used").
4. **Never commit**: model weights, `library/`, `models/`, `.venv*`, `blender/`, `uv` caches, licence
   keys, `.env`, anything under `scratch/`. `.gitignore` enforces it; don't weaken it.
5. **GPL boundary**: everything that imports `bpy` lives in `blender_ops/` or `blender_addon/`
   (GPL-3.0-or-later, own `LICENSE`). Nothing in `server/`, `steps/`, `ui/`, `mcp/` imports from
   there or from any GPL package. SkinTokens and UniRig run only in `.venv-skin` as subprocesses.
6. **Reference machine first.** Code is validated on the RTX 5080 Laptop (sm_120, 16 GB, Windows 11,
   32 GB RAM). A step may plan **≤ 14.5 GB VRAM** (M45: the app enforces the budget, what is free at
   the job's start − 0.3 GB CUDA context − 0.5 GB margin, ~14.7 GB on the idle laptop; a new GPU step gets a row in `server/presets.VRAM_GB` from its bench). If you can't run on the machine, say so and leave
   the GPU-dependent acceptance check unticked rather than claiming it passed.
7. **Every non-trivial piece of logic ships with one small check** (`tests/test_*.py`, `assert`-based,
   no fixtures beyond `tests/fixtures/`). Run `uv run pytest` before every commit. Trivial
   one-liners need no test.
8. **Contracts (§3) are frozen.** Changing a contract is its own PR that updates this doc, the code
   and every caller together. Don't "slightly extend" a contract inside a feature PR.
9. **No telemetry, no network at runtime** except model downloads, the Claude CLI (opt-in per run)
   and the Blender MCP Pro licence check (not our code).
10. **Report faithfully.** The PR description states: milestone, what was skipped (and the trigger
    to add it), acceptance checks run with their output, VRAM/time measurements if any. If a check
    failed, say so with the output.

**Definition of done for a milestone:** every acceptance check in its section passes (or is
explicitly marked "needs GPU, not run" when the agent has no GPU), `uv run pytest` and
`uv run ruff check . && uv run ruff format --check .` pass, `npm run build` passes if `ui/` changed,
and the docs touched by the change are updated in the same commit.

---

## 1. Repository layout (fixed)

```
Effigy/
  CLAUDE.md  README.md  LICENSE (MIT)  licenses.yaml  pyproject.toml  uv.lock  run.bat  run.sh
  .mcp.json                      # effigy + blender-mcp-pro servers for Claude Code / Desktop
  .claude/skills/ponytail/
  docs/                          # PLAN, ARCHITECTURE, WORKFLOW, IMPLEMENTATION, research/
  server/                        # MIT. FastAPI backend (no torch)
    app.py                       # routes, SSE, static mounts
    jobs.py                      # job table, global GPU lock, subprocess + resident-worker runner
    library.py                   # library folders, meta.json, version chain
    blender_client.py            # ~30-line client for the Blender MCP Pro add-on socket
    claude_runner.py             # spawns `claude -p`, forwards stream-json → SSE
    presets.py                   # pipeline presets (which steps, which params) and prompt templates
  steps/                         # MIT. Python ML steps, run in .venv-ml (or .venv-skin)
    _common.py                   # arg parsing, PROGRESS/RESULT protocol, OOM handling, frame conversion
    brief.py  concepts.py  prep.py  trellis2.py  clean.py  remesh.py  uv.py  bake.py
    rig_fit.py  rig_skin.py  anim.py  validate.py  export.py
  engine/
    trellis2/                    # vendored microsoft/TRELLIS.2 + Pixal3D + patches (MIT + Apache NOTICE)
      PATCHES.md                 # every patch: origin (localmesh / PR #173 / PR #185 / SPZ), file, reason
      third_party/               # vendored localmesh files with NOTICE
    hunyuan/                     # EXPERIMENTAL (MX1). NO Tencent code or weights in git: only
      README.md                  #   pinned upstream commit + install script + our patch notes
      install.py                 #   (Hunyuan code is pip-installed from upstream into .venv-hunyuan)
  blender_ops/                   # GPL-3.0-or-later. One file per headless op. LICENSE inside.
    _lib.py  import_glb.py  quadriflow.py  unwrap.py  bake_normal_ao.py  export_fbx.py  export_usd.py
    retarget.py  skin_proxy.py  render_thumb.py  validate_mesh.py
  mcp/
    effigy_server.py                # MIT. stdio MCP server wrapping the REST API
  ui/                            # MIT. Vite + React + TypeScript + three.js
    index.html  package.json  vite.config.ts  src/{main.tsx, api.ts, events.ts, viewer/Viewer.ts, panels/*.tsx}
    public/decoders/             # meshopt + draco decoders copied from three
  tests/                         # pytest; tests/fixtures/ (tiny meshes, images, a 2-tri GLB)
  tools/                         # smoke scripts, bench.py, download_models.py
  wheels/                        # sm_120 CUDA wheels committed by wheels.yml (private repo: no Release assets)
  prompts/                       # claude presets + blender_agent.md system prompt addition
  .github/workflows/             # wheels.yml (sm_120 CUDA wheels → wheels/), ci.yml (lint+tests)
  library/  models/  blender/  .venv*  scratch/      # runtime, git-ignored
```

Three Python environments, all created by `uv`:
- **`.venv`** (Python 3.12): `server/`, `mcp/`, `tests/`, `tools/`. Deps: fastapi, sse-starlette,
  uvicorn, mcp, pyyaml, huggingface_hub, numpy, trimesh, pygltflib, pytest, ruff.
- **`.venv-ml`** (Python 3.12, torch 2.8.0+cu128): `steps/` and `engine/`. Our sm_120 wheels
  (cumesh, flex_gemm, o_voxel), natten, diffusers, transformers, BiRefNet, opencv-headless.
- **`.venv-skin`** (torch 2.7 pin): SkinTokens-NoBlender (+ UniRig only if ever needed). Subprocess only.
- **`.venv-hunyuan`** (EXPERIMENTAL, created only when the user enables the engine; MX1): Hunyuan3D-2.1
  from upstream at a pinned commit, torch ≥ 2.7 cu128, our sm_120 builds of its `custom_rasterizer` /
  mesh-inpaint extensions. Subprocess only (`steps/hunyuan.py`).

Node 22 LTS for `ui/`. Blender 5.2.2 LTS portable under `blender/` (downloaded on first run).

---

## 2. Conventions

- **Python**: 3.12, type hints on public functions, `ruff` (lint + format, default rules + `I`),
  no classes where a function does, no async in `steps/`. Logging to stderr via `logging`.
- **TypeScript**: `strict`, no `any` in `src/` except `window.__viewer` debug exposure. No ESLint
  until it earns its place; `tsc --noEmit` is the lint.
- **Naming**: step modules are verbs (`clean`, `bake`); library folders are `NNN_<step>`; JSON keys
  `snake_case`; files inside a version use the **fixed names in §3.2**.
- **Units and axes**: see §3.4. Never guess; call `_common.to_library_frame()` /
  `from_library_frame()`.
- **Errors**: steps exit non-zero with one `ERROR <json>` line on stdout (see §3.1); a `RESULT` followed by a
  non-zero exit is a failure too (2026-10-11, BE-005: a crash in the step's cleanup is a crash). The backend never
  parses stderr.
- **Commits**: `<area>: <imperative summary>` (`steps: add clean step`, `ui: weight heatmap`), body
  lists acceptance checks run. One milestone per PR.
- **Docs**: a milestone that changes behaviour updates `IMPLEMENTATION.md` (this file) and, if a
  product decision changed, `PLAN.md`.

---

## 3. Contracts (frozen; change only via a contract PR)

### 3.1 Step CLI

```
python -m steps.<name> --in <version_dir> --out <new_version_dir> --params '<json>' [--serve]
```

- Runs in `.venv-ml` (or `.venv-skin` for `rig_skin`). Reads only from `--in` (and earlier versions
  through `meta.json.parent` if it needs e.g. `ovoxel.npz`), writes only into `--out`.
- `--params @<path>` (M37): the params are read from that JSON file instead; the backend passes params this
  way when their JSON is long (an animation edit's correction layer), the command line staying under Windows'
  32k limit. The file lies in `--out`, named `_params.json`, and is removed after the run.
- `--in` is omitted for an asset's first step (no input version). In one-shot mode the operation is
  `params["op"]` (default `"run"`), the same value a `--serve` request carries in its `op` field, so one
  handler `run(op, in_dir, out_dir, params)` serves both modes.
- `_common` moves the process's fd 1 to stderr at import and keeps a private handle for protocol
  lines, so prints from libraries (TRELLIS prints a lot, CUDA extensions write to fd 1) can't corrupt
  the protocol.
- **stdout protocol** (one JSON-safe line each; nothing else on stdout):
  - `PROGRESS <float 0..1> <text>` — any number of times.
  - `RESULT <json>` — exactly once on success: `{"outputs": {<name>: <relative path>}, "metrics": {...}}` (a
    path names a file, or a non-empty folder of them: the 2D track's `frames`),
    optionally `"models": [{"id", "revision"}]`, `"licenses": [str]`, `"note": str` (copied into `meta.json`;
    the backend adds the parent chain's licences; `metrics.frame`, if present, becomes `meta.frame`).
  - `ERROR <json>` — exactly once on failure: `{"code": "oom"|"bad_params"|"missing_input"|"restricted"|"failed", "message": str}`.
- **Exit codes**: `0` success, `2` bad params / missing input, `3` CUDA OOM (after `torch.cuda.empty_cache()`
  and one retry is **not** attempted by the step; the backend decides), `1` anything else.
- **Environment**: `EFFIGY_LIBRARY` (library root), `EFFIGY_MODELS` (models root, always set by the backend: its own
  `EFFIGY_MODELS`, else the folder the user chose in the app (`settings.json`, M35c), else `models/`; `HF_HUB_CACHE` = `<EFFIGY_MODELS>/hub`),
  `EFFIGY_BLENDER` (Blender executable, for `steps/_blender.run_blender`),
  `EFFIGY_GLTFPACK` (gltfpack executable, for `steps/export.py`; M13), `EFFIGY_VRAM_BUDGET_GB` (M45: set for a GPU
  step, empty for the rest; `_common.main` caps torch at it with `set_per_process_memory_fraction` before the
  handler runs, so going over raises an OOM, exit 3, instead of spilling into shared memory).
  Steps never read other env vars.
- **Cancellation**: the backend sends SIGTERM (Windows: `TerminateProcess`); the step needs no handler.
- **`--serve` (heavy families only, currently `steps.trellis2`)**: after loading models, print
  `READY`, then loop: read one line from stdin `{"id": str, "op": str, "in": str, "out": str, "params": {},
  "budget": GB | null}` (the budget: this run's VRAM cap from the backend's pre-flight, applied with
  `torch.cuda.set_per_process_memory_fraction` before the request runs; absent or null keeps the cap; 2026-10-11),
  emit `PROGRESS` lines prefixed with the id (`PROGRESS <id> <float> <text>`), then one
  `RESULT <id> <json>` or `ERROR <id> <json>`. `{"op": "quit"}` exits 0. One request at a time.
- **`_common.py`** provides `main(run)` (both modes, exit codes, OOM detection), `progress()`, `StepError(code,
  message)` and (M2) the ovoxel/frame helpers. Every step ends in `main(run)`; no step re-implements the protocol.
- **Acceptance for the contract**: `tests/test_step_protocol.py` runs `python -m steps._echo` (a
  10-line test step) and asserts the line grammar, exit codes and `--serve` round trip.

### 3.2 Library, versions, fixed file names

```
library/<asset_id>/
  asset.json                  # {"id", "name", "created", "brief": {...}}
  <NNN>_<step>/               # NNN = 3 digits, strictly increasing per asset; step = module name
    meta.json
    <fixed outputs>
  exports/<profile>/<files>
  inbox/                      # drop zone for pulls from Blender (consumed into a version)
```

`meta.json` (all keys required unless marked optional):

```json
{
  "schema": 1,
  "asset_id": "dwarf_blacksmith",
  "version": "030_trellis2",
  "step": "trellis2",
  "parent": "020_prep",                   // null for the first version
  "created": "2026-10-03T12:00:00Z",
  "params": {"weights": "fast", "pipeline_type": "1024_cascade", "seed": 42},
  "outputs": {"mesh": "hi.glb", "ovoxel": "ovoxel.npz"},
  "metrics": {"seconds": 171.2, "peak_vram_gb": 13.1, "faces": 1840221},
  "models": [{"id": "microsoft/TRELLIS.2-4B", "revision": "<sha>"}],
  "licenses": ["microsoft/TRELLIS.2-4B", "facebook/dinov3-vitl16-pretrain-lvd1689m"],  // licenses.yaml row names that touched this version or an ancestor
  "frame": {"library": "gltf_yup_m", "to_library_from_source": [[...4x4...]]},   // optional
  "provenance": "step" | "upload" | "blender_pull" | "claude_run",                // who made it
  "restricted": false,                    // true if this version or ANY ancestor came from an experimental
                                          // engine with output restrictions (computed by server/library.py)
  "note": ""                              // optional, free text (e.g. Claude run summary)
}
```

Fixed output names per step (the next step reads these names; don't invent others):

| Step | Required outputs in the version folder | Notes |
|---|---|---|
| `upload` (user file, no subprocess) | `image.<ext>`, `mesh.glb` or `video.<ext>` (M34); a model file (M39): `source/<file>` (output `model`), a `.zip` extracted into `source/` (output `model` = the chosen model's path) | `provenance: upload`; a root version (no parent); `params.name` = the user's file name; a `.glb` keeps `mesh.glb` (output `mesh`) |
| `import_model` | static: `mesh.glb`; a recognised skeleton: `rigged.glb`, `skeleton.json`, `qa.json`; with clips also `rigged_anim.glb`, `clips.json` (outputs in that order: `rigged_anim` first) | M39: in: an `upload` version with a `model` (or a `.glb`'s `mesh`); Blender `blender_ops/import_model.py`; everything in the library frame, the rest pose's base centre at the origin; params `scale` (> 0, ≤ 1000, default 1, on top of the file's own units), `rig` (default true; false: static even with a known skeleton); `skeleton.json`, `qa.json`, `clips.json` as `rig_skin`'s / `anim`'s (`qa.json` `picked: "import"`); metrics below (M39) |
| `brief` | `brief.yaml` | copied into `asset.json.brief` |
| `concepts` | `cand_<k>.png`, `scores.json` | `scores.json`: per candidate `{gates: {name: pass/fail/value}, rank}` |
| `prep` | `image.png` (RGBA, BiRefNet alpha), `image_rgb.png`, `crop.json` | multi-view: `view_<k>.png` + `cameras.json` (Pixal3D `transforms.json` convention) |
| `trellis2` (generate) | `hi.glb`, `ovoxel.npz`, `preview.png` | `hi.glb` is in the **library frame**; `ovoxel.npz` in the **TRELLIS frame** with the transform in `meta.frame`; param `background` (`auto`/`black`/`grey`/`white`, behind the prep's cut-out `image.png`) → `metrics.background`, `metrics.dark_share` |
| `trellis2` (texture) | `textured.glb` | input mesh keeps its UVs |
| `clean` | `mesh.glb`, `report.json` | watertight flag, components, holes filled; `level` (true \| false \| `"auto"`, default `"auto"` = true for a model with the generator's volume (`ovoxel`) up its chain, 2026-10-13): stood upright (`level_transform`: the floor levelled, the hull's or else the mesh's own lowest faces, else a box-like model's own axes; a post on its shaft when it has no floor or the floor is within 3° of the shaft), folded into `metrics.transform`, `metrics.levelled` = `{tilt_deg}`; `straighten` (bool, default false, 2026-10-11): the floor (the convex hull's largest downward plane) turned level (3–45° off) and a long footprint's axis turned onto X, before the re-ground, both folded into `metrics.transform`; `metrics.straightened` = `{tilt_deg, yaw_deg}`; a thin footprint (depth under 15 % of the length) is only turned, never levelled (2026-10-12: the fence leaned 18°); without a floor, or with one within 3° of it, a post over twice as tall as wide is stood on its shaft (2026-10-13); `press_to_floor` (bool, default false, 2026-10-13): a small part hanging just under the middle of a broad flat base (up to a tenth of the height) pressed flat onto it before the re-ground, `metrics.pressed` = `{vertices, depth_cm}`; `metrics.enclosed_dropped`: parts removed inside another (the rebuild's inner copy, bubbles); `drop_floating` (true \| false \| `"auto"`, default `"auto"` as `level`, 2026-10-13): with one part over 0.5 % of the surface, the parts under it lying over 1 % of the diagonal clear of it with their middle outside its convex hull dropped before the levelling, `metrics.dropped_parts` = `{parts, faces, surface_pct}` |
| `transform` | each `.glb` rewritten, the pictures copied | 2026-10-13 (agent findings 11, 18; light, CPU): a still mesh (one mesh node, no skin; refused with a skeleton, markers or parts) stood up (`level`), squared (`square`), its front turned to an axis (`face`), turned (`rotate_deg` [x, y, z]), scaled (`scale`, a number or [x, y, z] after the turn, or `height_m`), its base centre onto the origin (`ground`, default true) and moved (`translate_m`); UVs, materials and textures as they were; reports, a volume and quads.obj left out (`metrics.dropped`); `metrics.transform` (the 4×4 back to the parent's frame, bake follows it), `size_m`, `scale`, `levelled` |
| `remesh` | `mesh.glb`, `report.json` | `report.topology: "tri"|"quad"`, faces; `metrics.filled_gap_cm` (a hollow shell filled through holes that wide, 0: none; param `fill`, default true); `close` (metres, 2026-10-12): slits up to that width shut before simplifying (thin boards tore), length / 200 to length / 5 (the refusal names the range); tri: `metrics.self_intersection` (%, as validate's); unset `close` on a prop (the chain's root a `brief` with category `prop`) over 5 % folded: done again with `close` = length / 150 beside the first, kept when it folds less, `metrics.closed` = `{close_m, self_intersection_before, kept, error?}` (2026-10-13; `close: 0` never) |
| `uv` | `mesh.glb`, `uv_report.json` | charts, stretch, texel density |
| `bake` | `mesh.glb` (textured), `basecolor.png`, `orm.png`, `normal.png` | ORM = R:AO G:rough B:metal; normal = OpenGL +Y tangent space; M43a: params `texture_size`, `normal_map`, `supersample` (1 or 2, default 2; `texture_size × supersample` ≤ 4096), `ao` (default true; GPU only, CPU runs write R = 255); the GLB declares the ORM image as `occlusionTexture` when `metrics.ao`; AO rays go against the version's own mesh (per face, `_bake.ao_faces` from the share of its own rays that escape on either side: the ray normal turned to the open side; a face no rays leave either way is *enclosed*, an inner shell, and its texels get AO 1, not 0), reach 0.1 × bbox diagonal; then `_bake.fill_dark_islands`: a connected patch of texels below 0.25, smaller than 0.0003 of the atlas and far below its surrounding ring, takes the ring's median (an inner shell showing through a torn outer one); metrics `ao_enclosed`, `ao_filled` (shares of the covered texels); UV seams colour-levelled; M62: with no `ovoxel.npz` up the chain (an imported model) the colour is read from the model's own materials instead (the nearest version up the chain whose main glb has textures, this one first, else the chain's root `import_model`/`upload`; its meshes mapped through the chain's `metrics.transform`): each texel takes the closest source point (cuBVH on the GPU, an approximate sampled nearest on the CPU), base colour = sRGB(linear texture × baseColorFactor), alpha only from MASK/BLEND materials (the output then carries `alphaMode` MASK), roughness/metallic = factor × the metallicRoughness texture, normals from the source (its normal map applied); same outputs; metrics `source_from`, `source_materials` replace `volume_from`; a source that is the input itself needs clean UVs (inside 0..1, overlap ≤ 0.01) or `missing_input` "run uv first"; no materials at all: `missing_input` "nothing to bake from"; not read: vertex colours, emissive, source AO, KHR_texture_transform, TEXCOORD_1, wrap modes; metrics `supersample`, `ao`, `ao_mean`, `ao_seconds`, `seam_delta_before`, `seam_delta`, `vram_peak_gb`; `detaper` (bool, 2026-10-12): after sampling, a flat prop's perspective taper undone along its long side, the tall end kept, the depth left as it was (2026-10-13: scaled with the height it made a wedge) (`metrics.detapered`, null when the model is not a flat panel along X or Z or has no taper; straighten it in clean first) |
| `project` | `mesh.glb` (textured), `basecolor.png`, `orm.png`, `normal.png`, `overlay.png` | M23: the generated image projected onto the base colour where the camera sees the surface; `orm`/`normal` copied from the input; `overlay.png` = the image with the mesh silhouette and the region band |
| `refine` | `mesh.glb` (textured), `basecolor.png`, `orm.png`, `normal.png`, `views.png` | M43b: in: any version with a UV-mapped, textured `.glb` (bake, project, refine); zoomed orthographic views redrawn by Z-Image-Turbo + Fun-ControlNet Tile 2.1 and painted back into the base colour; `orm`/`normal` copied from the input; `views.png` = a grid of each view before and after (for the panel and `look`); the mesh and UVs unchanged. M43c: param `delight` (0–1, default 0): before the views, the base colour is divided by the share of its brightness the input's baked AO explains (the GLB's `occlusionTexture`, required when `delight` > 0: `missing_input` otherwise), hue kept; `views: []` (only with `delight` > 0) skips the redraw and the model (seconds, no `views.png`, `models` empty); metrics `delight`, `ao_luma_corr_before`, `ao_luma_corr` (the correlation of the colour's luminance with the AO over the covered texels, both lightly blurred; null when the input has no AO); a view in `views` may carry its own `prompt`, used for it instead of the asset's (2026-10-13, the owner's agent: one side of another material) |
| `parts` | views: `mesh.glb`, `views/` (12 × `color_NNNN.webp`, `normal_NNNN.webp`, `depth_NNNN.exr`, `meta.json`, `mesh.glb`); segment: `mesh.glb` (one node per part, the rest `Body`), `parts.json`, optional `click.png`; rename: `mesh.glb`, `parts.json` (M31b) | M31: `parts.json` = `{"views", "labels": one int per face of the views' mesh (0 = none), "parts": [{"id", "name", "faces"}]}`; `metrics.views` names the views version a segment builds on | Op `views` also reports `metrics.holes` = {view: background pixels inside the silhouette} (mesh-gates).
| `rig_fit` | `skeleton.json`, `markers.json` (people only) | `skeleton.json` `{"template", "frame", "joints": [{name, parent, position}]}`: `template` `mixamo52` (the 52-joint Mixamo skeleton, positions in library frame; optional per-joint `twist`, a unit vector, the bone's front / the palm's direction: M22b) or a creature's (M36: `fox`, `horse`, `bird`, `dragon`, `shark`, `kaiju`, `spider`, `snake`, steps/_templates.py), which adds `yaw` (degrees about the vertical from the template's +Z front: the animal's lean plus a quarter turn, e.g. 323.5), `sides` (its side suffixes, e.g. `["_L", "_R"]`) and per joint `tail` (the bone's end) and `to` (the child the bone points at, if any), and `level` (3x3, `v @ level`: the turn that stood a tilted flat mesh up, identity if none; the written `mesh.glb` is then that leveled copy, not the input, with `metrics.transform` (4x4, p' = level.T @ p + shift) and `metrics.leveled_deg`; only for the `spider` and `snake`; a Turn re-run keeps the source's `level`; skipped with `metrics.level_skipped` (the reason) for a mesh with a transform, a parent node, skin, morph targets or several meshes); params `template`, `yaw`; no `markers.json` for a creature; a parts version's `parts.json` carried along, its names in `metrics.parts` (M32) |
| `rig_skin` | `rigged.glb`, `skeleton.json`, `qa.json` | ≤4 influences, normalized; `qa.json` from §3.9 checks; `metrics.template` (M36: a creature skins with bone heat only); a parts version: one skinned object per part, `parts.json` carried along, `params.rigid` = `{part: bone or ""}` → `metrics.rigid` = `{part: bone}` (M32) |
| `import_motion` | `clip.glb` | 2026-10-12 (owner): in: an upload version holding an animation made elsewhere (`.fbx`, `.glb`, `.gltf`, `.bvh`); params `clip` (which of the file's animations), `name`, `for`; blender_ops/anim_clip keeps the armature and that one animation, renamed; `metrics.skeleton` mixamo\|ue5\|soma (`_maps.kind`; anything else is refused), `clips`, `picked`, `file`. `anim` takes it like a motion version; the retarget reads Mixamo names (any `mixamorig:` prefix) through `_maps.MIXAMO`. No lineage: the file's licence is the user's |
| `motion` | `clip.glb`, `motion.bvh`, `prompt.json` | M26: a motion from `prompt` (Kimodo, SOMA 77 joints), `clip.glb` as a pack clip's (anim takes `NNN_motion`). **M65 connect clips**: in: an animated version (its `rigged_anim.glb`, a person on the 52-joint template); params `from` and/or `to` (that version's clip names), `seconds` 0.5–8 (default 1.5), `travel` (metres straight on to where `to` starts; default from the clips' speeds), `turn` (degrees `to` faces, left positive, default 0), `prompt` optional; the clip starts at `from`'s last pose and ends at `to`'s first, starting over the origin facing +Z; its name "A to B" ("After A", "Into B"); `metrics` add `from`, `to`, `seam_cm` (per held end, the SOMA joints' mean distance from the held pose), `height_scale` |
| `mocap` | `clip.glb`, `motion.bvh`, `preview.mp4` | M34: in: an upload version with `video.<ext>`; `clip.glb` as a motion's (anim takes `NNN_mocap` like `NNN_motion`); `metrics.name` the clip's name. M64: params `person` (0 the largest at the start, one run per person) and `camera: "still" \| "panning"`; `metrics.people` the people at the first frame with people (at most 8), `metrics.person`, `metrics.camera`; a panning camera adds `camera_unread` (frames whose turn could not be read) and `camera_turn_deg` |
| `anim` | `rigged_anim.glb`, `clips/<name>.glb` | clip names from the brief; a creature (M36) takes its template's own clips (`GET /api/clips?template=<id>`, default `mixamo52`), no motion versions; `parts.json` carried along (M32) |
| `anim_edit` | `rigged_anim.glb`, `clips.json`, `edit.json`; `skeleton.json`, `parts.json` carried along | M37: in: an `anim` or `anim_edit` version; params `{"clips": {<clip>: <layer>}}` (the edit layer below), applied to the **base** (the nearest `anim` up the chain, or an `import_model` with `rigged_anim.glb` (M39), so edits never compound) and baked into the clips it names, every other clip as it was; `edit.json` = `{"base": "NNN_anim", "fps", "clips": {...}}` (what the editor re-opens); `clips.json` as `anim`'s with the edited clips' frames and seconds; `metrics.clips` the edited clip names. **M44 `from`**: a layer may carry `from: <base clip>`; a layer whose clip is not in the base and has `from` is a copy, written as a new animation (see M44; `from` a base clip, at most 64 copies; one absent from the next save is gone). **A copy's name**: 1–64 code points, no space at either end, none of `/ \ : | , [ ] ;` or control characters, unique among the base's clips and the copies ignoring case (`steps/anim_edit.NAME_BAD`, `clip.ts` `nameOk`). **Layer** (per clip; bones by their glTF node name): `keys: {bone: [{"f": base frame, "q": [x, y, z, w] bone-local rotation delta, w ≥ 0, "falloff": frames ≥ 0}]}`, `offsets: {bone: [{"f", "v": [x, y, z] metres in the bone's parent space, "falloff"}]}` (bones with a translation channel), `trim: [in, out]` (base frames, inclusive), `speed` (> 0), `loop` (frames blended at the end towards the first frame), `ground` (metres added to the top-most translated bones' Y), `in_place` (their linear X/Z drift from the first to the last frame removed), `smooth: {bone: sigma in frames}`, `feet: [{"bone", "from", "to", "blend"}]` (M38: foot locks, base frames inclusive, `blend` frames ≥ 0; the bone, its parent and its grandparent rotate; M64: a hand resting on the floor is locked the same way, the editor's Find hand contacts), `ik: [{"bone", "chain", "space"?, "keys": [{"f", "p": [x, y, z], "swivel"?: 0, "w"?: 1, "falloff"?: 0}]}]` (M63: IK controls, at most 64, one per bone: `bone` reaches `p`, the `chain` (2–8) bones above it turning, every one of them and `bone` rotate; `p` in skeleton space, or with `space` (a bone) in that bone's frame at the frame (`P = p_space + W_space·p`, the target riding on it); `swivel` degrees in [−180, 180] turn the chain about its root–tip line (where the knee or elbow points); `w` in [0, 1] the weight; a control's keys at least one, whole frames, one per frame). **Evaluation**, in this order (`steps/_anim.py` and `ui/src/anim/clip.ts`, one fixture `tests/fixtures/anim_edit/` both pass): (1) dense base, `F = round(duration·fps) + 1` frames at `f/fps`, every rotation channel and every translation that moves (by its values) sampled there (a STEP channel held); (2) smooth: per bone, quaternions made sign-consistent along time, a Gaussian (±3σ, edges held) per component, normalised; translations the same filter; (3) keys: per bone Δ(f) from its keys sorted by f: between two keys `slerp(q_i, q_i+1, s((f−f_i)/(f_i+1−f_i)))`, before the first `slerp(I, q_1, s(1−(f_1−f)/falloff))` within `falloff` frames (identity beyond; `falloff` 0: the key's own frame only), after the last the same mirrored, `s(t) = t²(3−2t)`, slerp shortest-path; `rot' = rot · Δ` (bone-local, three.js `bone.quaternion` order); offsets the same with lerp, `pos' = pos + o`; (3b, M38) feet, each lock in order (below); (3c, M63) ik, each control in order: per base frame its target from its keys sorted by f (between two keys `p`, `swivel` and `w` lerped with `s((f−f_i)/(f_i+1−f_i))`; before the first and after the last the key's `p` and `swivel` held, its `w` times `s(1 − d/falloff)` within `falloff` frames, 0 beyond, `falloff` 0: the key's own frame only; a frame whose weight is 0 is left), on the pose after the earlier steps: `P = lerp(c, target, w)` with `c` the bone's world position, the chain (its top U … the bone B) first turned about normalise(`c − a`) (`a` U's position; skipped below 1e-12) by `swivel·w` degrees, every chain bone but B (their world turns and positions about `a`), then solved: two bones by the foot lock's two-bone solve, more by FABRIK, 16 rounds of (B's joint at P, each joint back along its bone; U's joint at `a`, each joint out along its bone), then from U down each bone's new world turn = the shortest turn from its turned direction to its new one · its world turn under its parent's new one, B keeping its world turn; a zero-length chain bone or `|P − a| < 1e-12` leaves the frame; (4) trim to `[in, out]`; (5) `in_place`: top-most translated bones (no translated ancestor: Hips; a creature's root) `pos.xz −= (g/(G−1))·(pos.xz[G−1] − pos.xz[0])`; (6) `ground`: their `pos.y += ground`; (7) `loop` n: frames `G−n … G−1` blend towards frame 0 with weight `s((g−(G−n)+1)/n)`: rotations slerp, translations lerp (the top-most bones' X/Z only when `in_place`); (8) speed: `round((G−1)/speed)+1` frames, frame g samples `g·speed` (slerp/lerp between neighbours). Edited clips are written dense and LINEAR, a channel that stays constant as 2-key STEP over the new duration (its new value: a whole-clip key on a still bone), scale too; numbers in the layer finite and bounded, a key's frame whole, trim and speed leaving at least 2 frames |
| `validate` | `checklist.json` | pass/warn/fail per item |
| `export` | `<profile>/<asset>.<ext>`, `<profile>/CREDITS.txt`, `<profile>/LICENSES.json` (inside the version folder) | formats per profile in `steps/export.py`; a parts version (with `parts.json`) keeps one object per part, named like it, LODs and hull per part (M31b); credits/licences from `meta.licenses` (names of `licenses.yaml` rows; the backend unions them down the chain) |
| `concepts` (op `image`) | `image_<k>.png`, `preview.png`, `image.png` (= `image_0.png`, so `prep`, `pixelize` and `layers` find it) | 2D track (research 15 §5.2; M50): stills of any aspect on the resident Z-Image-Turbo; params `prompt`, `width`/`height` (multiples of 64, long side ≤ 2048), `count` 1–4, `seed`, `lora` (none, or a bundled Apache-2.0 style), `lora_scale`, `cutout` (BiRefNet into the alpha); no gates, no `scores.json` |
| `concepts` (op `tile`) | `tile.png` (output keys `tile` and `image`: prep and pixelize read `image`), `preview.png` (2×2 tiling), `report.json` `{seam_ratio, seam_ratio_before, pass, size, band}` | M54: a seamless tile: generated, rolled by half, the seam band (`band` = size / 8) drawn again at `mend` (0.3–1, default 0.8) by `ZImageInpaintPipeline` on the resident modules, its colours matched to the picture's there, feathered, rolled back; params `size` (256–1024, multiples of 64) and op `image`'s `prompt`, `seed`, `steps`, `lora`, `lora_scale`; `seam_ratio` (`steps/_pixel.py`) = mean \|∇\| across the wrap over the strongest such step between neighbouring lines inside, pass ≤ 1.1 |
| `pixelize` | `frames/<name>.png`, `sheet.png`, `sheet.json`, `palette.gpl`, `preview.png`, `report.json`; for a single picture also `image.png` | M51 (CPU, `LIGHT`): in: any version with `image`, `image_<k>`, a `frames` folder or `sheet.json`; one pixel grid, one palette and a binary alpha over all frames; `sheet.json` is Aseprite's json-array shape (`frames[].{filename, frame: {x, y, w, h}, duration}` in ms, `meta.{image, size, frameTags[].{name, from, to, direction, repeat}}`), the frames packed as uniform cells; params `scale` (`auto` | int ≥ 1) or `height_px`, `method` (`dominant` \| `median`), `alpha_cut` (0–1), `colors` (int) or `palette` (a bundled name or a `.gpl`/`.hex`/`.pal` path), `dither` (`none` \| `bayer2` \| `bayer4`), `outline` (none \| a colour), `palette_map` (`{"#rrggbb": "#rrggbb"}`); `report.json` `{scale, offset, colors, frames, alpha_fringe}`. A seamless tile in (an input with `tile`, M54): the grid rolled round its edge, a pixel size dividing the tile's (`height_px` = the tile's size in pixels, 8–256), the output keeps `tile` (= `image.png`), a 2×2 `preview.png` and `report.seam_ratio`. Op `edit` (M56, M57): the in-app editor's sheet → the same outputs, `metrics.edited_frames`, `metrics.changed_pixels`; params `frames` (`{name: base64 PNG}`, only the changed or new ones), `order` (the frames' names in order: a new name is a blank frame unless sent, a name left out is deleted), `durations` (`{name: ms}`), `tags` (indices into the order; the input's are kept only while the order is), `palette` (colours or a bundled name; default the input's `palette.gpl`, else any colour and no palette file); without a parent, `size` `[w, h]` (4–512) and `order` make a blank sheet |
| `redraw` | `preview.png` (listed first: the viewers show the first picture), `image.png`, `report.json` | M61 (GPU, one-shot, `.venv-ml`): in: any version with `image` (or `key` `image_<k>`). Op `upscale`: Real-ESRGAN x4 (`factor` 2 \| 4; 2 = x4 then Lanczos down), overlapping windows blended with a raised-cosine weight in RAM (`steps/_sr.Blend`). Op `redraw`: `prompt` (required, exactly as given, no negative prompt), `strength` 0.2–1, `scale` 0.5–1, `factor` 1 \| 2 \| 4 (upscale first), `seed`, `lora`/`lora_scale`, `window` (256–1024, multiple of 64), `overlap` 0–0.5, `protect` (a sibling version whose `image` is a greyscale mask: white keeps the picture); Z-Image-Turbo + the lite Tile in windows, VRAM independent of the picture's size. Alpha kept (drawn on grey, Lanczos back). `report.json`/metrics `{op, factor, size, input_size, windows, drift, pass, thresholds, seconds}`, redraw also `{drift_max, seam_ratio, skipped, seconds_per_window, peak_vram_gb, per_window}`; thresholds drift 12, window drift 25, seam 1.25 (advisory) |
| `sprites` | `frames/<clip>_<yaw>_<NNNN>.png` (the clip's name made safe for a file), `sheet.png`, `sheet.json`, `camera.json`, `preview.png` | M52 (Blender, CPU, `LIGHT`, `blender_ops/render_sprites.py`): in: a version with `rigged_anim.glb` + `clips.json`, or a still `mesh.glb` (one frame per yaw); orthographic at `pitch_deg`, the camera placed per yaw (0 front, 90 facing right, 180 back, 270 facing left); **one pixels-per-metre per asset** kept in `camera.json` and reused by every later run without `height_px`; params `clips`, `height_px`, `cell` (the least; grown to the frames' exact extent), `pitch_deg`, `yaws`, `fps`, `shading` (`textured` \| `flat`), `supersample`; transparent, no anti-aliasing; in place: the camera follows the travelling bone's straight drift exactly (not in whole pixels: nothing else is in the frame, so the body keeps one sub-pixel place); one `frameTag` per clip and yaw; a sheet past 8192 × 8192 px is refused. Later, when asked: `toon`, `outline` (pixelize's 1 px outline does it at the final size), `shadow`, `normal_maps` (`normals/`, `normals.png`) |
| `depth` | `depth.npz` `{disparity: float16 0–1, valid: bool}`, `preview.png` (the disparity in grey), `image.png` (the picture, carried) | M55 (`.venv-moge`, GPU, 8.9 GB): MoGe-3 ViT-g on a version's picture (`key`, default `image`); `resolution_level` 0–9 (9); disparity = 1 / depth scaled to 0–1, 0 where MoGe gives none (the sky) |
| `layers` | `layer_<k>.png` (0 the farthest; RGBA, layer k = its band and everything nearer, drawn in; the farthest opaque), `layers.json` `[{"file", "scroll_scale", "share"}]`, `bands.png`, `preview.png` | M55 (GPU, Z-Image's inpaint): in: a `depth` version; `count` 2–5, `subject` (BiRefNet's subject as the nearest layer), `prompt` (default the brief's `prompt_full`), `seed`, `steps`, `strength` 0.3–1 (0.8: how far Z-Image redraws an OpenCV fill of each layer's own colours); bands by k-means on the disparity, the sky farthest, specks under 0.2 % joined to their surroundings; `scroll_scale` = the band's median disparity over the nearest's, ≥ 0.05 |
| `validate` (a 2D version) | `checklist.json` | M53: for a version with `sheet.json` / `tile.png` / `layers.json`: uniform cells, binary alpha, palette size, stray islands < 2 px, sheet ≤ 4096 and a power of two (warn), every tag ≥ 1 frame, `seamless` for a tile (its `seam_ratio` ≤ 1.1; a raw tile without a sheet gets only this item), `layers` (≥ 2) and `far_layer_whole` (the farthest layer's see-through share, 0 passes) for layers |
| `export` (a 2D version) | `godot-2d/`: `sheet.png`, `sheet.json`, `<asset>.tres` (`SpriteFrames`: one animation per tag, its speed the commonest frame's fps, longer frames as relative `duration`, `loop` off for a tag with `repeat` "1"), `<asset>.tscn` (an `AnimatedSprite2D` standing on its origin, Nearest), `README.txt` (the pixel-perfect settings); `unreal-2d/`: `<asset>.png`, `<asset>.paper2dsprites` (TexturePacker json-hash: a sprite per frame named `<animation>_<NN>`, the pivot at the feet), `README.txt`. A tile (M54): `<asset>.png` + `<asset>.tres` (a `TileSet`). Layers (M55): `layer_<k>.png` + `parallax.tscn` (a `Parallax2D` per layer, `scroll_scale` (s, 1), a `Sprite2D` from its top left); `unreal-2d/` the pictures and the speeds in the README. Later: an `aseprite/` profile for Unity (the Apache-2.0 `aseprite` package; not added yet) | M53: profiles for 2D versions; `CREDITS.txt` and `LICENSES.json` as every export |
| `hunyuan` (EXPERIMENTAL) | `hi.glb`, `preview.png`, optional `textured.glb` (Paint) | **no `ovoxel.npz`**; `meta.restricted = true` |
| Blender pull | `scene.blend`, `edited.glb` | `provenance: blender_pull` |
| Claude run | `scene.blend`, `edited.glb`, `transcript.jsonl` | `provenance: claude_run` |

`server/library.py` is the only module that creates version folders and writes `meta.json`
(the next number is max(NNN) + 1, so an asset holds up to 999 versions; owner decision 2026-10-07, before that the
numbers went in tens). `asset.json` may carry `archived`, (M42) `tags` (a list of the user's words, absent = none) and
(research 15, 2026-10-12) `kind: "2d"` (a 2D asset: pictures, pixel art, sprite sheets; absent = a 3D asset; the UI
picks its stage list by it, `POST /api/assets` and the MCP `create_asset`/`import_file` set it, steps never read
it); `meta.json` may carry `starred` and `label` (M28, the user's marks; steps
never read them)
(steps write outputs; the backend writes `meta.json` from the step's `RESULT`). Version numbers are
allocated by the backend (`max(NNN)+10` so manual insertions remain possible).

### 3.3 `ovoxel.npz` (the re-bake source)

Keys: `coords` int32 `(N,3)` voxel indices; `attrs` float16 `(N,C)`; `layout` JSON string mapping
attribute → `[start, end]` (`base_color`, `metallic`, `roughness`, `alpha`); `resolution` int;
`aabb` float32 `(2,3)` = `[[-0.5,-0.5,-0.5],[0.5,0.5,0.5]]`; `voxel_size` float32 `(3,)`;
`frame` = `"trellis"`. The mesh it was decoded from is saved alongside as `hi_trellis.ply` (binary,
TRELLIS frame) so `bake` can build its BVH without re-deriving the frame. Loader/saver live in
`steps/_common.py` (`save_ovoxel`, `load_ovoxel`); `bake` and `trellis2` use nothing else.

### 3.4 Coordinate frames

- **Library frame** (everything in `*.glb`): glTF convention, **Y up, −Z forward, metres,
  right-handed**, origin at the asset's feet/base centre after `clean`.
- **TRELLIS frame**: unit cube `[-0.5, 0.5]³`, **Z up**. Upstream converts with
  `v' = (x, z, -y)` on export and the inverse on import (see `o_voxel.postprocess.to_glb`).
- `steps/_common.py` has `trellis_to_library(bbox_min, bbox_max, height_m=1.0)` returning the 4×4 that maps
  a mesh with that TRELLIS-frame bbox to the library frame (base centre → origin, `(x, z, -y)`, height in
  metres), and `library_to_trellis(m)`, its inverse. Every step that touches both frames
  uses these two functions and records the matrix in `meta.frame`.
- **Blender** imports/exports glTF with its own Y-up↔Z-up handling; we never pre-rotate for Blender.
- **FBX for Unreal** is written by Blender's exporter with its unit/axis options (research 08 §4);
  no manual scaling elsewhere.
- `tests/test_frames.py`: a point round-trips through both conversions to 1e-6.

### 3.5 Backend API and SSE

All under `http://127.0.0.1:8000` (the port: Setup, App address, or EFFIGY_PORT; since 2026-10-12). JSON bodies; errors `{"error": {"code", "message"}}` with 4xx/5xx.

| Method | Path | Body → Response |
|---|---|---|
| GET | `/api/assets` | → `[asset.json…]`, each with `versions` (count) and `updated` (M28) and (M42) `licenses` (the union of its versions' `meta.licenses`), `restricted` (any version restricted), `imported` (any `import_model`, or an `upload` with a `mesh`) |
| POST | `/api/assets` | `{"name", "brief"?}` → `asset.json` |
| GET | `/api/assets/{id}` | → `{"asset", "versions": [meta.json…]}` |
| PUT | `/api/assets/{id}/upload/{name}` | raw file body (`.png/.jpg/.jpeg/.webp/.glb`; `.mp4/.mov/.webm` since M34; `.gltf/.fbx/.obj/.stl/.ply/.usd/.usda/.usdc/.usdz/.zip` since M39), at most 1 GB counted while streaming to disk → `meta.json` of a new root `NNN_upload`; 400 `bad_request` (extension), `bad_zip` (unreadable, encrypted, unsafe or over a limit; M39), `no_model` (a zip without a model file); 413 `too_large` |
| GET | `/api/steps` | → `{step: {preset_name: params}}` for every `steps/<name>.py` (presets from `server/presets.py`) |
| PATCH | `/api/assets/{id}` | `{"name"?, "archived"?, "tags"?}` → the asset (M28; `tags` M42: replaces the list; each trimmed, 1–32 characters, no comma, at most 20, repeats (ignoring case) dropped, else 400 `bad_params`) |
| PATCH | `/api/assets/{id}/versions/{v}` | `{"starred"?, "label"?}` → the version's meta (M28) |
| POST | `/api/assets/{id}/steps/{step}` | `{"parent": "NNN_step", "params": {}, "dedupe"?: false}` → `{"job_id", "deduped"}` (202); `dedupe` (the MCP bridge, M40): the queued or running job with the same asset, step, parent and params instead of a new one, `deduped: true` then (not the caller's to cancel) |
| POST | `/api/assets/{id}/versions/{v}/fit-check` | `{"joints"?: {name: [x, y, z]}}` (unsaved moves) → `steps/_rig.fit_quality`: `{"score", "joints", "inside", "centred", "bones_outside", "attention": [[joint, why]…]}`; 404 for a version without a skeleton (Check fit) |
| POST | `/api/jobs/{job_id}/cancel` | → `{"ok": true}` |
| GET | `/api/jobs/{job_id}` | → `{"job_id", "asset_id", "status": "queued"|"running"|"done"|"failed"|"cancelled", "version"?, "error"?, "progress"?, "text"?}` |
| GET | `/api/assets/{id}/versions/{v}/look?clip=&size=768` | → PNG (M40): a 2×2 sheet of the model (front, right, back, three-quarter), four frames of `clip`, or the version's pictures in a grid; size 256–1024; cached in the version folder, its path percent-encoded in `X-Effigy-File`; 400 `nothing_to_show` / `bad_clip` |
| GET | `/api/assets/{id}/versions/{v}/turntable` | → animated WebP (M42): the model turning once, 24 frames of 256², 67 ms each, looping; cached as `_turn.webp` in the version folder; a `validate` or `export` version shows the version it was made from; 400 `nothing_to_show` (no model) |
| GET | `/api/assets/{id}/turntable` | → the same for the version the asset's thumbnail shows when that is a model (M42), `Cache-Control: no-cache`; 404 `not_found` (no model) |
| POST | `/api/export/copy` | `{"folder", "items": [{"asset_id", "version"}]}` (each an `export` version) → `{"copied": [{"asset_id", "version", "dir", "files"}], "skipped": [{"asset_id", "version", "reason"}]}` (M42): each export's files into a new `<folder>/<asset_id>_<profile>/`; an existing one is skipped (`exists`), never overwritten; a failed copy leaves no folder (`failed: …`); 400 `bad_path` (not an existing absolute folder), 400 `bad_params` (no items, or not an export version), 404 unknown asset or version |
| GET | `/api/agent/setup` | → `{root, python, server, uv, clients: [{id, name, how, text}]}` (M40): what each agent client needs to start the MCP bridge, this install's paths filled in |
| POST | `/api/agent/test` | → `{ok, tools?, error?}` (M40): the bridge started over stdio as the setup says, its tools listed within 30 s |
| POST | `/api/assets/{id}/blender/open` | `{"version"}` → `{"ok"}`; with Setup's "Start Blender when it is needed" and none answering, the chosen Blender is started with its add-on's server first and waited for (`blender_client.ensure`, up to 90 s, one start at a time; 504 `blender_timeout`) (2026-10-13) |
| GET | `/api/blender/status` | → `{"link": "pro"\|"free"\|null, "start"}`: which add-on answers; `start` (2026-10-13): Open in Blender starts one when none answers (Setup's option on and its `blender.exe` there) |
| GET | `/api/setup/blender` | → `{"start", "exe", "found"}` (2026-10-13): start the user's Blender when needed, which one; `found`, while none is chosen: the connected Blender's `bpy.app.binary_path` |
| POST | `/api/setup/blender` | `{"start", "exe"}` → as GET; 400 `bad_path` when `exe` is not a `blender*` file, or `start` without one; saved in settings.json (`blender_start`, `blender_exe`) |
| POST | `/api/assets/{id}/blender/pull` | `{"version"?}` → `{"version": "NNN_blender"}` (2026-10-12: with several versions of the asset open, `version` says which; else the one opened last, the note naming what else was open) |
| POST | `/api/assets/{id}/blender/close` | `{"version"?}` → `{"closed": [collection names]}` (2026-10-12: that version's collection out of the scene, or all of the asset's) |
| GET | `/api/models` | → `[{"id", "name", "groups", "revision", "where", "installed", "partial", "bytes", "missing_bytes"}…]`, one per `tools/download_models.py` PINNED/FILES row (M14); `installed` = every file the manifest lists there at its size (M35b), `partial` = some files there |
| GET | `/api/setup/machine` | → `[{"id", "level": "ok"\|"warn"\|"bad", "label", "detail"}]` (M35c): GPU and VRAM, driver, RAM, Windows long paths, each with what to do; the `gpu` item also has `budget_gb` and `too_big: [{"step", "param", "value", "need_gb"}]` (M45: the measured variants this GPU can never run) |
| GET | `/api/setup/folder` | → `{"path", "source": "env"\|"settings"\|"default", "bytes", "choices": [{"path", "drive", "free", "total"}]}` (M35c): the models folder and the drives that could hold it |
| GET | `/api/setup/port` | → `{"running", "next", "source": "env"\|"settings"\|"default", "default": 8000}` (2026-10-12): the port this app runs on and the one it starts on next |
| POST | `/api/setup/port` | `{"port"}` → as GET (2026-10-12): 1024–65535, saved in settings.json for the next start (`python -m server`, which run.bat calls); 409 `in_use` when another program answers on it, 409 `env` while EFFIGY_PORT sets it. The MCP bridge follows the saved port unless EFFIGY_API is set |
| POST | `/api/setup/folder` | `{"path"}` → `{"path", "job_id", "left"?}` (M35c): an empty or new folder (not a drive root), or another install's models folder; switches at once (`job_id` null; `left` = the old folder that still holds models) or, when the current folder has models, moves them (a job, step `move_models`: a rename on the same drive, else copy, check, switch, delete); 400 `bad_params` (relative, overlapping the library or the old folder) / `not_writable`, 409 `env` (EFFIGY_MODELS set) / `not_empty` / `busy`, 507 `no_space` (a copy that would not fit) |
| GET | `/api/setup/first-run` | → `{"first_run"}` (M35c): nothing installed and the guided setup neither started nor skipped (`settings.json` `welcomed`): the UI opens `#/welcome` by itself, from the plain start page only |
| POST | `/api/setup/welcomed` | → `{"ok": true}` (M35c): the guided setup was used or skipped; the app stops opening it by itself |
| GET | `/api/setup/presets` | → `[{"id", "name", "what", "groups", "bytes", "missing"}]` (M35c): what you want to make, sizes with shared entries once |
| GET | `/api/hf` | → `{"token", "user", "access": {repo: true\|false\|null}, "gated": {repo: [groups]}}` (M35c): the Hugging Face login and the gated licences, asked through `.venv-ml`'s huggingface_hub |
| POST | `/api/hf/login` | `{"token"}` → `{"user"}` (M35c): checked with Hugging Face, then saved by huggingface_hub's `login()` in its own store (stdin, never argv, logs, the library or a job); 400 `bad_token`, 503 `offline` |
| POST | `/api/hf/logout` | → `{"ok": true}` (M35c) |
| GET | `/api/clips` | `?template=` (default `mixamo52`; M36: `fox`, `horse`, `bird`) → `[{"name", "file"}]`: the clips `anim` accepts for that skeleton (M21: the human pack; a creature's own clips, its rest pose left out); 404 unknown template |
| GET | `/api/environments` | → `[{"name", "state", "groups"}]` (M35b): each feature environment (`.venv-ml`, `-skin`, `-kimodo`, `-parts`, `-gemx`; `-hunyuan` only when present), `state` ok / missing / outdated (`uv sync --frozen --check` differs) / installing / unknown, and the model groups whose steps run in it |
| POST | `/api/models/install` | `{"group", "repair"?: false}` → `{"job_id"}` (202): a job (step `install`, M35b) that sets up the group's environment if it is missing or out of date (`uv sync --frozen`), runs `tools.download_models <group>` in `.venv-ml` and checks every file against `tools/models_manifest.json`; `repair` also test-imports the environment (reinstalling its packages if that fails) and deletes files of the wrong size or SHA-256 first; installs queue, one at a time; progress events `{"p", "text"}` (bytes on disk of the expected); a gated repo fails the job with `error.code = "gated"`; 409 `busy` (that group already queued), 507 `no_space` |
| DELETE | `/api/models/{id}` | → `{"ok": true}`: deletes that row's files only; 404 unknown id; 409 `busy` while a step or a download runs |
| GET | `/api/models/sizes` | → `{group: bytes}` (M35a): what each group puts on disk (`tools/download_models.SIZES`, measured); POST `/api/models/install` answers 507 `no_space` when the rest of a group plus 1 GB does not fit on the models folder's drive |
| GET | `/api/storage` | `?refresh=true` → `{"scanned", "seconds", "items": [{"id", "kind", "name", "bytes", "drive", "action"}], "assets": [{"id", "name", "archived", "bytes"}], "drives": [{"drive", "total", "free", "low"}]}` (M35a): what removing each item would free (single-link files; hard-linked ones once under `shared`); a scan is reused for 5 minutes |
| GET | `/api/storage/drives` | → the drives only, no scan (the top bar's low-space warning; low = under 20 GB or 10 % free) |
| POST | `/api/storage/cleanup` | `{"action"}` (an item's: `trash`, `prune`, `leftovers`, `group:<g>`, `venv:<name>`; or `archived:<asset folder>` for one archived asset) → the storage with `freed` and `failed` (files that could not be deleted: they stay); 409 `busy` while a step or a download runs, while a job is queued on that asset, or when a file is open; 409 `not_archived`; 400 for anything else |
| GET | `/api/licenses` | → `[licenses.yaml row…]` |
| GET | `/events` | SSE (sse-starlette), heartbeat every 15 s |
| GET | `/files/<asset>/<path>` | any file inside `library/<asset>/` via `FileResponse` (Range OK, no gzip, no `..`) |
| GET | `/` | `ui/dist` (mounted last; `index.html` no-cache) |

SSE events (`event:` name, `data:` JSON):
- `progress` `{"job_id", "asset_id", "step", "p", "text"}`
- `log` `{"job_id", "line"}`
- `job` `{"job_id", "asset_id", "status", "version"?, "error"?, "progress"?, "text"?}`
- `version_created` `{"asset_id", "meta"}`
- `gpu` `{"resident": "trellis2"|null, "free_gb": float}`
- `gpu_load` `{"step": str|null, "util": %, "used_gb", "total_gb"}` every 2 s while a job holds the GPU lock,
  then once with `step: null` (nvidia-smi; values `null` without it)

Every request must name `127.0.0.1`, `localhost` or `[::1]` as its Host (else 400 `bad_host`) and carry no
`Origin` but one of those over http (else 403 `bad_origin`): a web page cannot reach the API (M40).
`tests/test_api.py` drives these with `TestClient` and a fake step (`steps._echo`).

### 3.6 Blender

- **Live link** (`server/blender_client.py`): TCP to `127.0.0.1:9877`, frame = 4-byte big-endian
  length + UTF-8 JSON `{"command": str, "params": {}}`; reply `{"status": "ok"|"error", "result"|"message"}`.
  Commands used: `execute_python` (Open and Pull are one snippet each: collection + tags + undo step /
  backup `.blend` + GLB export of the tagged objects) and `get_viewport_screenshot`; later `analyze_mesh`,
  `diagnose_rig`. A handler's own `{"status": "error"}` or `execute_python`'s `{"error": traceback}` inside
  an ok envelope is an error too (502 `blender_error`). Timeout 120 s per command (the add-on's cap).
  If the socket isn't listening, the backend returns `409 {"code": "blender_not_connected"}` with the
  hint "start the add-on's server in Blender's N panel". We **don't** start Blender ourselves in the MVP.
  **Free alternative** (owner's request, 2026-10-04): the MIT `blender-mcp` add-on (ahujasid) on
  `127.0.0.1:9876`, raw JSON `{"type", "params"}` both ways, `execute_code` returning the snippet's stdout
  (`run_python` prints its result after a marker). The client tries the add-on that answered last, Pro
  first; the Claude run gets the free add-on's MCP server (`blender`, `blender-mcp==2.0.0` via `uv tool
  run`) and is told which one is live. Protocol checked against the add-on's source (v1.8) with a fake
  server; not yet run against the real free add-on.
- **Snapshot before any mutating session**: `execute_python` → `bpy.ops.wm.save_as_mainfile(filepath=…, copy=True)`
  + `bpy.ops.ed.undo_push(message="effigy: before <job>")`.
- **Headless ops** (`blender_ops/<op>.py`): `blender -b --factory-startup --offline-mode --python-exit-code 1
  --python blender_ops/<op>.py -- <json_path>`; the JSON holds `{"in": [...], "out_dir", "params"}`;
  the op writes `result.json` into `out_dir` and exits 0/1. stdout/stderr go to `out_dir/blender.log`.
  `blender_ops/_lib.py` holds the shared helpers (arg loading, Cycles device assertion, validators).
  Each op **asserts its own result** (e.g. weights non-empty, device used) and fails loudly.
- GPL: `blender_ops/LICENSE` = GPL-3.0-or-later; the folder has its own header comment in every file.

### 3.7 MCP (agent access)

- `mcp/effigy_server.py` (stdio, Python `mcp` SDK ≥ 2.3, `MCPServer`; calls the app's HTTP API at `EFFIGY_API`, default
  `http://127.0.0.1:8000`). Tool names are frozen once released; M40's set (M9's seven kept, `run_step` and `export`
  extended): `list_assets`, `get_asset`, `get_version`, `describe_steps`, `look`, `read_file`, `job_status`,
  `fit_check` (read-only); `create_asset`, `import_file`, `run_step`, `cancel_job`, `validate`, `export`,
  `open_in_blender`, `pull_from_blender`. Every tool carries correct `readOnlyHint`/`destructiveHint`/
  `idempotentHint` annotations and a description that starts with when to use it. Mistakes the agent can fix
  come back as tool errors (`ToolError`, "code: message, how to fix"), never as protocol errors.
- Long jobs: a tool that starts a job waits at most `wait_s` (default 40, at most 45: Codex's 60 s tool timeout),
  reporting progress, and returns `{"status": "done", "version": {…}}` or `{"status": "running", "job_id",
  "progress"}`; `job_status(job_id, wait_s)` continues. An identical request (asset, step, parent, params) while
  one is queued or running returns that job. Cancelling a call cancels the job that call started.
- Images go out as MCP image content (never base64 inside text), ≤ 1024 px, with the file's path in the text.
- `.mcp.json` (repo root) declares `effigy` with paths relative to the repo (`uv run --frozen --project .
  python mcp/effigy_server.py`), `blender-mcp-pro` (`<EFFIGY_BLENDER_MCP_PRO_DIR>/server.py`, licence from
  `BLENDER_MCP_PRO_LICENSE`) and `blender`. **Never commit a licence key or an absolute path.** Other clients get
  their config from Setup's "Connect an agent" (`GET /api/agent/setup`), the absolute path filled in at runtime.
- No in-app agent runner: the user drives their own agent (research 10 path A; Ask Claude was removed in M40).

### 3.8 `licenses.yaml`

```yaml
- name: microsoft/TRELLIS.2-4B
  kind: weights            # weights | code | tool | data
  license: MIT
  commercial: true
  region_note: ""          # e.g. "excludes EU/UK/KR" (such rows are not allowed at all)
  attribution: ""          # text that must appear in credits, e.g. "Built with DINOv3", or a CC-BY credit line
  flag: ""                 # optional note shown on assets, e.g. "training-data provenance: ripped game models"
  experimental: false      # true only for MX1 engines; such rows may have a region_note
```
There is one profile (commercial; PLAN §6). `tests/test_licenses.py` asserts: every non-experimental
row has `commercial: true` and an empty `region_note`; every row with a `region_note` is
`experimental: true`; no row matches the PLAN §6 "never" list; **no preset in `server/presets.py`
references an experimental engine**; every dependency in `pyproject.toml` / `ui/package.json` has a
row (dependencies of `.venv-hunyuan` are listed in `engine/hunyuan/README.md` instead, since they're
never in our lockfile); attribution strings are rendered on the Models page and collected by the
export step.

### 3.9 Quality checks (shared definitions)

- **Mesh** (`steps/validate.py`, also used by `clean`/`remesh` reports): components, non-manifold
  edge count, boundary edge count, watertight, face count, degenerate faces, bbox, scale sanity
  (height in `[0.05, 50]` m). `validate` alone adds the geometry the topology cannot see (mesh-gates,
  2026-10-12): `self_intersection` (ray parity on points a hair outside the surface, per closed
  component, outward by its signed volume, % of samples; warn from 1 %) and `parts_apart` (the largest share of one component's
  vertices inside another closed one, by ray parity; warn from 5 %); `skip` when no component is closed.
  `size_drift_pct` (2026-10-13, the owner's agent's finding 2): only when the chain's first version is a `brief`
  whose prompt gives a size (`brief.dims`: "N m|cm|metres tall|high|wide|long|deep", "2 m-tall"; zero ignored); the
  model's height and its plan's long side (the smallest-area rectangle; a plan with no area, its box) against the
  brief's height and its length (the size it calls long, else the largest of two or more), the larger miss in %;
  warn from 10 %; no item when neither is given. The thin side is in `report.size`, not judged (a brief's depth was
  a room to fit in).
- **UV**: islands, out-of-bounds, overlap (sample-based), texel density median/spread.
- **Rig** (`rig_skin` → `qa.json`): weights sum to 1 ± 1e-3, ≤ 4 influences, no influence from a
  disconnected island, L/R asymmetry score, pose-perturbation test (10 poses, ±10° per joint: volume
  change %, max per-bone stretch, self-intersection count via BVH), joints inside the mesh;
  `joint_loops` (mesh-gates: the vertex bands along the parent bone within ±0.35 of its length of each
  joint, `steps/_rig.joint_loops`; a joint under 3 bands creases instead of bending, warn; bones under
  5 % of the figure left out; engine-independent, so outside the engines' ranking).
- **Checklist** (`validate` step): the WORKFLOW §⑧ list, each item `pass|warn|fail` with numbers, or
  `skip` (not checked: nothing closed for the ray-parity items).

---

## 4. Milestones

Each milestone: **Goal · Depends · Files · Tasks · Acceptance · Non-goals · Research**. Acceptance
checks are commands or observable results; "needs GPU" marks checks that only run on the reference
machine.

### Phase 0: foundations

#### M0: Scaffold, environments, licence gate
- **Goal**: the repo runs an empty backend + UI with the three venvs and the test gate in place.
- **Depends**: nothing.
- **Files**: `pyproject.toml`, `uv.lock`, `run.bat`, `run.sh`, `.gitignore`, `licenses.yaml`,
  `server/app.py` (health + static), `steps/_common.py`, `steps/_echo.py`, `ui/*` (Vite template with a
  blank canvas), `tests/test_step_protocol.py`, `tests/test_licenses.py`, `.github/workflows/ci.yml`.
- **Tasks**: `uv` workspace with `.venv` and `.venv-ml` groups (ML group installs torch 2.8.0+cu128 from
  the explicit index; CUDA wheels come in M1); `run.bat` does `uv sync --frozen` then starts uvicorn and
  opens the browser; implement §3.1 `_common.py` fully; `_echo` step; CI runs ruff + pytest on
  Linux/Windows (no GPU).
- **Acceptance**: `uv run pytest` green incl. the protocol and licence tests; `run.bat` opens
  `http://127.0.0.1:8000` showing the UI shell; `npm run build` succeeds; CI green.
- **Non-goals**: any ML code, Blender, MCP.

#### M1: sm_120 CUDA wheels
- **Goal**: `.venv-ml` imports `cumesh`, `flex_gemm`, `o_voxel` on the reference laptop.
- **Depends**: M0.
- **Files**: `.github/workflows/wheels.yml`, `wheels/` (+ `README.md`), `engine/trellis2/patches.py`
  (nvdiffrast decoy), `engine/trellis2/o-voxel-windows.patch`, `engine/trellis2/PATCHES.md`,
  `tools/smoke_m1.py`, `tests/test_nvdiffrast_decoy.py`, `pyproject.toml` (path sources to `wheels/`).
- **Tasks**: build in CI on windows-2022 for cp312 / torch 2.8.0+cu128 / CUDA 12.8.1 /
  `TORCH_CUDA_ARCH_LIST="8.9;12.0"`: cumesh (main ≥ 2026-05-09), flex_gemm (main), o_voxel (upstream +
  PR #185's o-voxel MSVC fixes as a patch file). The repo is private, so the workflow commits the wheels
  to `wheels/` and `pyproject.toml` references them as `{ path = "wheels/<wheel>", marker =
  "sys_platform == 'win32'" }`; lock. Register a decoy `nvdiffrast` before `import o_voxel` (it imports
  nvdiffrast at module level). Every patch has a row in `PATCHES.md`.
- **Acceptance**: CI wheels job green, every compiled module of each wheel loads on the runner; on the
  laptop `uv run --group ml python -m tools.smoke_m1` prints the device, `sm_120` in the arch list, and
  simplifies a cube with `cumesh.CuMesh` without a CUDA error (needs GPU); `import nvdiffrast` without the
  decoy **fails** (not installed).
- **Moved out**: vendoring `TRELLIS.2`/`Pixal3D` and the Python patches (uvraster, SDPA shim, BiRefNet,
  staged loading, DINOv3, keep-UV #173, SPZ chunked decode, `EXPLICIT_GEMM`) → M2, where they're first
  used. natten → when the four-view (Pixal3D `_mv`) path is wired; the single-image path never imports it;
  first candidate is localmesh-engine's published cp312/cu128 build (check `natten.HAS_LIBNATTEN`, run one
  `na2d`), else add it to `wheels.yml`. CuMesh int64 `svox2vert` → when a 2048 preset exists.
- **Non-goals**: running the pipeline.
- **As built**: wheels run 37159187583 green (all three compile, every `.pyd` loads on the runner, sm_120
  SASS in each CUDA module); committed to `wheels/` and locked as win32 path sources with their hashes.
  Two patches beyond PR #185: FlexGEMM needs `.template data_ptr<T>()` in generic lambdas for MSVC
  (`flex-gemm-windows.patch`), o-voxel's `plyfile` (GPL-3.0) is made lazy and dropped from its deps.
  `triton-windows` pinned to 3.4 (torch 2.8's Triton; the resolver otherwise takes 3.8). natten is not
  needed at all: M2 runs NAF's `na2d` on plain torch (both single and four-view). Not run (needs GPU):
  `tools.smoke_m1` on the laptop.

#### M2: `steps.trellis2` generate (both weight sets) + `ovoxel.npz`
- **Goal**: image → `hi.glb` + `ovoxel.npz` + `preview.png`, in one-shot and `--serve` modes.
- **Depends**: M1.
- **Files**: `engine/trellis2/**` (vendored, pinned), `engine/pixal3d/**`, `steps/trellis2.py`, `steps/_common.py` (ovoxel + frames), `tools/download_models.py`,
  `tests/test_ovoxel_roundtrip.py`, `tests/test_frames.py`.
- **Tasks**: vendor upstream `microsoft/TRELLIS.2` (pin `75fbf01`) and `TencentARC/Pixal3D` (pin) and apply
  the Python patches moved out of M1, each with a `PATCHES.md` row; ship FlexGEMM's `autotune_cache.json`
  (set `FLEX_GEMM_AUTOTUNE_CACHE_PATH`). `download_models.py` fetches TRELLIS.2-4B, DINOv3 (gated → instruct `hf auth login`),
  BiRefNet, Pixal3D (+`_mv`), MoGe-2 into `models/`; implement `generate` with params
  `{weights: fast|faithful, pipeline_type, steps, seed, texture_size, views?, cameras?}`; persist
  `ovoxel.npz` + `hi_trellis.ply` per §3.3; export `hi.glb` in the library frame with the transform in
  `RESULT.metrics.frame`; `--serve` mode via `run_serve`; OOM → exit 3. Laptop presets:
  fast = `1024_cascade` 12 steps 2K, faithful = Pixal3D 1024 12 steps 2K. Record peak VRAM
  (`torch.cuda.max_memory_allocated`) and seconds in `metrics`.
- **Acceptance** (needs GPU): both presets complete on the laptop with peak VRAM ≤ 14.5 GB; cold-start
  and warm times recorded in `docs/bench/m2.md`; `ovoxel.npz` round-trips through `save/load` (CPU test
  with a synthetic volume); the `remesh_narrow_band_dc` fragmentation check (component count of the raw
  mesh vs. after DC remesh) is run once and its outcome written to `docs/bench/m2.md` with the fallback
  chosen (`remesh=False` if broken); FlexGEMM decoder runs without "device not ready" (else document
  `EXPLICIT_GEMM`).
- **Non-goals**: texturing, bake, UI.
- **As built (fast path)**: upstream `trellis2/` vendored unmodified; all changes are runtime patches
  (`engine/trellis2/patches.py`, table in `PATCHES.md`). `steps/trellis2.py` loads TRELLIS.2-4B from the
  pinned local copy (`effigy_pipeline.json` points the sparse-structure decoder and DINOv3 at `models/`),
  feeds prep's framed `image_rgb.png` with `preprocess_image=False` (keeps the 4 % margin), saves
  `ovoxel.npz` + `hi_trellis.ply`, then CuMesh simplify/unwrap (upstream to_glb's non-remesh branch)
  and `steps/_bake.py` (pure torch, replaces to_glb's nvdiffrast bake) → `hi.glb` in the library frame;
  `preview.png` from Blender (skipped, not fatal, without Blender). Params add `faces` (default 200k)
  and `height_m` (default 1.0). CPU-verified: engine import, attention shim (full + windowed vs
  reference), DINOv3 on transformers 5.18, bake core vs FlexGEMM semantics, the step end to end with the
  model and CuMesh faked. `download_models.py` fetches everything at pinned revisions.
- **As built (faithful path)**: `engine/pixal3d/pixal3d/` is upstream `TencentARC/Pixal3D` at `f7cf384`
  (MIT, LICENSE + NOTICE), the inference closure plus the one trainer module that holds its proj image
  extractor; `engine/pixal3d/naf/` is `valeoai/NAF` at `37f2dfc` (Apache-2.0), only `src/layers` and
  `src/model/naf.py`. Both unmodified; `engine/pixal3d/patches.py` reuses the TRELLIS.2 patches and adds
  NAF from the vendored code with pinned weights (upstream: `torch.hub` from GitHub at run time),
  `natten.na2d` on plain torch (so **no natten wheel**; the four-view path needs nothing more either),
  DINOv3 for transformers 5 in the proj extractor, one DINOv3 shared by the four stage extractors.
  `weights: faithful` builds the four extractors as Pixal3D's `inference.py` does, runs low-VRAM
  `1024_cascade` (Pixal3D has no `512`/`1024`: the preset says `1024_cascade`), camera from a manual
  `fov` (default 0.2 rad; distance = 0.5 / tan(fov/2), which is `distance_from_fov` with no extension).
  MoGe-2 FOV estimation is not wired (needs the `moge` package; add when a manual FOV visibly distorts).
  Pixal3D's volume is glTF's frame already (`PIXAL3D_TO_GLTF` = identity; the 180° turn read from
  `inference.py` showed the model's back on the first laptop run, 2026-10-04);
  `trellis_to_library` takes the engine's axis change, so `ovoxel.npz`/`hi_trellis.ply` stay in the
  engine's own frame and bake follows the recorded transform as before. CPU-verified: na2d vs a
  transcription of NATTEN's window rule, the real NAF weights load and upsample through the shim, the
  proj extractor (tiny DINOv3 + NAF) end to end, the step with the model faked (camera, frame, licences).
  `download_models faithful` also fetches DINOv3, BiRefNet and NAF (SHA-256 pinned); `multiview` adds
  Pixal3D's `_mv` checkpoints (~22 GB).

#### M3: `bake` from `ovoxel.npz` (the re-bake) + normal/AO
- **Goal**: any UV-mapped mesh of the asset gets full PBR + normal map from the persisted volume.
- **Depends**: M2.
- **Files**: `steps/bake.py`, `tests/test_bake_fixture.py`, `tests/fixtures/two_tris.glb`.
- **Tasks**: UV-space rasterization with the vendored `uvraster`; per texel: cage ray-cast along the
  low-poly normal against `hi_trellis.ply` (cuBVH `ray_trace`, fallback `unsigned_distance`); trilinear
  sample of `attrs` (`flex_gemm.grid_sample_3d`); normal map in MikkTSpace tangent frame (OpenGL +Y);
  AO via hemisphere rays (optional param); push-pull gutter fill; ORM pack; write `mesh.glb` with the
  glTF material. Params `{texture_size, ao: bool, cage_distance}`.
- **Acceptance**: fixture test (CPU path or skipped without GPU): a 2-triangle mesh with a synthetic
  volume bakes a flat colour exactly; on the laptop (needs GPU) baking `hi.glb` decimated to 20k tris
  gives a textured GLB whose mean base colour is within 5% of upstream `to_glb`'s on the same mesh;
  normal-map mean ≈ (0.5, 0.5, 1.0) ± 0.05.
- **Non-goals**: text/image retexture (Phase 1).
- **As built**: `steps/bake.py` walks the parent chain to the volume and composes the chain's
  `metrics.transform` (4×4, library → library, recorded by any step that moves geometry) onto the
  volume version's `meta.frame`; tangents are per-vertex, area-weighted, Gram-Schmidt (≈ MikkTSpace),
  bitangent = image-up (OpenGL +Y) with the UV-mirroring sign; gutters by push-pull; `orm.png` R = 255
  until AO lands (ponytail: hemisphere rays on the BVH). GPU projection = **closest point** on
  `hi_trellis.ply` (cuBVH `unsigned_distance`), hi normals turned to the low normal's side; the CPU path
  samples on the mesh itself (tests). CPU tests: flat volume → exact colour and normal mean
  (127.5, 127.5, 255); transform composition; tangent handedness incl. mirrored UVs; GPU test: flipped hi
  winding still bakes flat normals. **On the laptop (2026-10-04)** the planned cage ray-cast failed:
  TRELLIS's hi mesh is a thin double shell, rays hit its outer layer ~4 voxels off the surface (alpha 0,
  contour rings), and its winding is random (49 % of faces outward, so half the normal map pointed
  inward). With closest point: 060_trellis2 → clean → remesh 20k → uv → bake 2048 in 16 s, normal mean
  (127.5, 127.3, 248.0), area-weighted mean base colour within 0.4 % of `hi.glb`'s (substitute for the
  `to_glb` comparison, which needs nvdiffrast). The `cage` param is gone.

#### M4: `clean`, `remesh` (tri), `uv`
- **Goal**: `hi.glb` → cleaned → decimated to a face budget → UV-unwrapped, each a version.
- **Depends**: M1 (cumesh), M2 for real inputs.
- **Files**: `steps/clean.py`, `steps/remesh.py`, `steps/uv.py`, `steps/validate.py` (mesh checks only),
  `tests/test_mesh_checks.py`.
- **Tasks**: `clean`: CuMesh `fill_holes` → `remove_duplicate_faces` → `repair_non_manifold_edges` →
  `remove_small_connected_components` → `unify_face_orientations`; scale to `params.height_m`, origin at
  the base centre; `report.json` from §3.9. `remesh`: `simplify` two-pass to `params.faces`; `report`.
  `uv`: CuMesh `uv_unwrap` with the cone-angle params; `uv_report.json`. No Blender yet.
- **Acceptance**: `tests/test_mesh_checks.py` on fixtures (a cube with a hole, two components); on
  the laptop the knight example mesh goes 224k → clean → 20k faces → UV with 0 non-manifold edges and
  UVs within [0,1] (needs GPU).
- **Non-goals**: quads, seams, Blender ops.
- **As built**: `steps/_mesh.py` holds the §3.9 mesh/UV facts in numpy (union-find components,
  boundary/non-manifold edges, degenerate faces, bbox/height, UV islands, bounds, texel-density spread)
  and the untextured GLB IO; `clean` scales relative to the bbox (hole perimeter 3 % of the diagonal,
  min component 1e-5 × diag²), puts the base centre at the origin, optionally rescales to `height_m` and
  records it as `metrics.transform` (bake composes it); `remesh` skips meshes already under budget;
  `uv` fails if CuMesh returns UVs outside [0, 1]. `validate` now uses the same numpy checks (no Blender,
  no GPU). CPU tests with CuMesh faked: the whole clean → remesh → uv → validate chain.

#### M5: Backend jobs, library, SSE (§3.2, §3.5)
- **Goal**: the API runs steps as jobs, writes `meta.json`, streams progress, keeps the heavy worker
  resident.
- **Depends**: M0; uses `_echo` for tests, real steps for manual checks.
- **Files**: `server/app.py`, `server/jobs.py`, `server/library.py`, `tests/test_api.py`,
  `tests/test_library.py` (`server/presets.py` arrives with the first preset chain/templates, M6/M10;
  the OOM suggestion table `LOWER` lives in `jobs.py` until then).
- **Tasks**: job table in memory + `library/<asset>/jobs.log`; global `threading.Lock` for the GPU;
  subprocess runner parsing §3.1; resident-worker runner for `steps.trellis2 --serve` (LRU of one, killed
  before another heavy step); `meta.json` writer; version numbering; SSE via sse-starlette with heartbeat;
  static mounts; cancel; OOM (exit 3) surfaces as `job.error.code = "oom"` with the suggested lower preset.
- **Acceptance**: `tests/test_api.py`: create asset → run `_echo` → `job done` event → `meta.json`
  valid → parent chain resolves → cancel a sleeping `_echo` → `cancelled`; `tests/test_library.py`:
  numbering and branch from an older parent; manual: `trellis2` runs twice and the second run skips the
  cold start (worker reused; log shows `READY` once).
- **Non-goals**: auth, multi-user, DB.

#### M6: UI shell + viewer
- **Goal**: create an asset from an image, run the preset chain, watch progress, inspect versions in
  the viewer with channel views.
- **Depends**: M5.
- **Files**: `ui/src/**`, `ui/tests/smoke.spec.ts` + `ui/playwright.config.ts` (Playwright resolves from
  `ui/node_modules`), `tests/fixtures/two_tris.glb` (+ `make_two_tris.py`), `server/presets.py`, upload and
  `/api/steps` routes in `server/app.py`.
- **Tasks**: `api.ts` (fetch wrappers), `events.ts` (one `EventSource`), `viewer/Viewer.ts` (imperative
  class: GLTFLoader + MeshoptDecoder + DRACOLoader, OrbitControls, RGBELoader/PMREM with one bundled
  small HDRI, `ViewHelper`, `setViewMode()` channel swaps incl. the ORM `ShaderMaterial`, wireframe, UV
  checker, polycount HUD, `canvas.toBlob()` thumbnail), panels: Create (image drop + preset), Asset
  (version list as a tree + step buttons + params drawer), Jobs (progress + log). React only for panels;
  no router, no state lib.
- **Acceptance**: `npm run build`; Playwright smoke: page loads, WebGL context exists, fixture GLB
  loads (`window.__viewer.loaded === true`), screenshot saved; manual on the laptop: image → generate →
  clean → remesh → uv → bake from the UI, each version viewable, channel views switch.
- **Non-goals**: skeleton/weights views (Phase 2), candidate grid (M10).
- **As built (ponytail)**: three's `RoomEnvironment` instead of a bundled HDRI; `MeshoptDecoder` only (no
  `DRACOLoader`/`public/decoders` until an input is Draco-compressed); panels live in `main.tsx`; the
  thumbnail capture waits for an endpoint that stores it; the Create panel uploads, then optionally runs
  `trellis2` with a preset; later steps are run from the Asset panel on the selected version.

#### M7: Blender headless ops + export profiles
- **Goal**: Blender 5.2.2 downloads itself; QuadriFlow/unwrap/FBX/USD/thumbnail ops run headless.
- **Depends**: M4.
- **Files**: `blender_ops/**` (+ `LICENSE`), `server/blender_install.py`, `steps/export.py`,
  `tests/test_blender_ops.py` (skips if no Blender).
- **Tasks**: first-run download of the pinned zip + SHA-256 + `portable/` folder; `_lib.py` with
  the Cycles device assertion and validators; ops: `import_glb`, `quadriflow` (voxel-first, symmetry,
  watchdog timeout), `unwrap` (Minimum Stretch + Pack Islands), `export_fbx` (Unity/Unreal profiles per
  research 08 §4), `export_usd`, `render_thumb`, `validate_mesh`; `steps/export.py` orchestrates
  profiles `unity|unreal|godot|web|print` into `exports/<profile>/`, and writes **`CREDITS.txt`**
  (every non-empty `attribution` of the components in the version chain's `meta.licenses`) and
  **`LICENSES.json`** (the full component list with licences and flags) next to the files.
- **Acceptance**: `tests/test_export_credits.py`: a version chain whose `meta.licenses` include a
  CC-BY clip and DINOv3 produces a `CREDITS.txt` containing both attribution lines and a `LICENSES.json`
  listing every component; `tests/test_blender_ops.py` on the laptop: a fixture GLB → FBX (Unity profile) →
  re-import in Blender headless → same vertex count; `quadriflow` on the cleaned knight gives > 0 faces,
  0 non-manifold edges, within the watchdog; `render_thumb` writes a PNG; the Unity FBX opens in Unity
  (manual, once).
- **Non-goals**: live link, rig.
- **As built (ponytail)**: ops `export` (one Blender run per profile: unity/unreal FBX, godot GLB, web
  Draco+WebP GLB + USDZ, print STL), `quadriflow`, `unwrap`, `render_thumb` (Cycles CPU), `validate_mesh`
  (report only); importing is a `_lib` helper (`import_mesh`: glb/fbx/obj/stl), not an op. The runner is
  `steps/_blender.py` (timeout = watchdog, log file not pipe). Export output stays in the version folder
  (steps write only to `--out`); a restricted version needs `allow_restricted`. `run.bat` installs Blender
  and syncs `.venv-ml` on first run. `tests/test_blender_ops.py` passed against Blender 5.2.2 on Linux
  (CPU); the Unity import check stays manual.

#### M8: Blender live link (Open / Pull) via the Blender MCP Pro add-on
- **Goal**: "Open in Blender" and "Pull from Blender" round trip through the user's running Blender.
- **Depends**: M5, M7.
- **Files**: `server/blender_client.py`, routes in `server/app.py`, UI panel buttons,
  `tests/test_blender_client.py` (framing against a fake socket).
- **Tasks**: §3.6 client; `open`: `import_file` + `execute_python` snippet creating collection
  `EFFIGY_<asset>_v<N>`, tagging objects, pushing an undo step; `pull`: `execute_python` → save `.blend`
  copy + `export_object` of the tagged objects to `inbox/`, then the backend creates `NNN_blender` with
  `provenance: blender_pull`; `409 blender_not_connected` handling with the UI hint; viewport screenshot
  as the version thumbnail.
- **Acceptance**: `tests/test_blender_client.py` (framing, timeout); manual on the laptop: open v030,
  move a vertex in Blender, pull → new version shows the edit in the viewer; bake (M3) onto the pulled
  mesh works.
- **Non-goals**: live selection sync, our own add-on.
- **As built**: routes `POST /api/assets/{id}/blender/open {version}` → `{ok, objects}` and
  `POST /api/assets/{id}/blender/pull {version?}` → `{version}` and `POST .../blender/close {version?}` →
  `{closed}` (2026-10-12, the agent's fence report item 6: two versions open made a pull ambiguous and there
  was no way to remove one; MCP `pull_from_blender(asset, version)`, `close_in_blender(asset, version)`); Open is idempotent per version (existing
  `EFFIGY_<asset>_v<NNN>` collection is reused); Pull takes every object tagged with the asset, parent = the
  newest tagged version (`note` lists them if several), screenshot is best-effort. Verified against the
  real add-on in interactive Blender 5.2.2 under Xvfb: open, edit a vertex, pull → the edit is in
  `edited.glb`, plus `scene.blend` and `thumb.png`.

#### M9: `effigy` MCP server (`lt2` then) + Claude run (snapshot / accept / revert)
- **Goal** (removed in M40: the user's own agent connects over MCP instead): "Ask Claude…" runs a preset through the user's `claude` CLI against the live scene.
- **Depends**: M8.
- **Files**: `mcp/effigy_server.py`, `.mcp.json`, `prompts/presets.yaml`, `prompts/blender_agent.md`,
  `server/claude_runner.py`, UI Claude panel, `tests/test_mcp_server.py` (tool listing via the SDK client),
  `tests/test_claude_runner.py` (parses a recorded stream-json fixture).
- **Tasks**: §3.7 exactly; `claude --version` check at startup with a clear message if missing or too
  old (`--permission-prompts` needs ≥ 2.1.259); snapshot (pull) before the run; `accept` → `NNN_claude`
  version with `transcript.jsonl`; `revert` → re-open the snapshot version; cost/turn display from the
  `result` event; `auth` setting with the policy note in the UI (research 10 §2).
- **Acceptance**: `tests/test_mcp_server.py` lists the seven tools with correct annotations;
  `tests/test_claude_runner.py` turns the fixture into the expected `claude_event` sequence and extracts
  `session_id`/`total_cost_usd`; manual on the laptop: preset "make game-ready for Unity" on a pulled
  version produces an accepted `NNN_claude` version whose `validate` checklist has no `fail`.
- **Non-goals**: multi-turn chat UI (use Claude Desktop/Code for that), Claude without MCP.
- **As built**: `mcp` SDK 2.x (`MCPServer`; tools raise `ToolError` so Claude sees the reason). `validate`
  runs `steps/validate.py` (mesh + UV + scale checklist, numpy, M4).
  `.mcp.json` launches both servers through `uv` with `${EFFIGY_ROOT}`, `${EFFIGY_BLENDER_MCP_PRO_DIR}` and
  `${BLENDER_MCP_PRO_LICENSE}` (set by the runner / the user's environment; nothing secret committed).
  Routes: `GET /api/claude/presets`, `POST /api/assets/{id}/claude/run`, `POST /api/jobs/{id}/accept|revert`.
  A Claude run is a job that does **not** take the GPU lock (Claude may call `effigy.run_step`, which does).
  The CLI check runs when a run starts (409 `claude_missing`/`claude_too_old`), not at backend startup.
  `local-cli` strips `ANTHROPIC_API_KEY` from the child env; `api-key` requires it and adds `--bare`. Pull
  and Revert select the asset's objects by tag **or** by membership in an `EFFIGY_<asset>_v<NNN>` collection
  (objects Claude creates there are untagged). Revert = remove those objects, open the snapshot. Flag set
  checked against Claude Code 2.1.288 (no unknown-option errors).

#### M10: Concept candidates (prompt → 4 images → pick) + `prep`
- **Goal**: the text path: brief template → Z-Image-Turbo candidates → gates → pick → RGBA → generate.
- **Depends**: M5, M6.
- **Files**: `steps/brief.py`, `steps/concepts.py`, `steps/prep.py`, `server/presets.py` (templates),
  UI candidate grid, `tests/test_gates.py`.
- **Tasks**: `brief`: category templates (character/prop/creature) → `brief.yaml` (no LLM);
  `concepts`: `diffusers` `ZImagePipeline` bf16 with `enable_model_cpu_offload()`, 4 images, 4 prompt
  variants (suffix list), gates from the BiRefNet mask per research 07 §3 → `scores.json`; `prep`:
  BiRefNet RGBA, crop/pad, `crop.json`; UI 2×2 grid with badges, reroll, pick → `prep` → `trellis2`.
- **Acceptance**: `tests/test_gates.py` on fixture masks (cropped subject fails, two components fail,
  clean subject passes); on the laptop: 4 candidates in < 60 s warm with peak VRAM ≤ 14.5 GB, logged in
  `docs/bench/m10.md`; end-to-end "prompt → GLB" from the UI.
- **Non-goals**: LLM brief, VLM scoring, pose control, edits.
- **As built**: gates + framing are pure numpy/OpenCV in `steps/_image.py` (thresholds are named
  calibration constants); BiRefNet (`ZhengPeng7/BiRefNet`, pinned revision, fp16 on GPU / fp32 on CPU) in
  `steps/_birefnet.py`; category templates live in `steps/brief.py` (their only user) and the brief is
  copied into `asset.json`; `prep` frames at alpha 0.8 + 4 % padding, square, black outside, ≤ 1024 px,
  and keeps a user's own alpha (no matting then); `image_rgb.png` = premultiplied on black (what TRELLIS
  sees). The UI chains steps client-side (`waitJob`): image → `prep` (→ `trellis2` preset); text → `brief`
  → `concepts`; grid pick → `prep` (→ `trellis2`); "4 more" = `concepts` again on the brief. Candidates
  appear when all four are done. Verified on CPU: real BiRefNet on a rendered subject (clean matte, all
  gates pass), and `concepts` end to end with the image model stubbed (gates rank clean > noisy > cropped).
  Not run: Z-Image-Turbo itself (needs the GPU; diffusers 0.40 call signature checked).

#### M11: Phase-0 exit: `bench.py` + golden set
- **Goal**: numbers, not estimates, for the whole chain on the laptop.
- **Depends**: M2–M10.
- **Files**: `tools/bench.py`, `tests/fixtures/golden/` (10 images with cleared licences, listed in
  `licenses.yaml` as `kind: data`), `docs/bench/phase0.md`.
- **Tasks**: run preset `game-prop` on the golden set; CSV with success, seconds per step, peak VRAM,
  faces, non-manifold count, UV stretch; update WORKFLOW's timing table with measured values.
- **Acceptance**: ≥ 9/10 succeed; every step's peak VRAM ≤ 14.5 GB; the Fast-vs-Faithful A/B on the
  10 images recorded with a pick for the default in `PLAN.md` §3 row 1.
- **As built (tooling; the run needs the laptop)**: `tools/bench.py` drives the running backend over HTTP
  (same jobs, library, resident worker as the UI), chain `game-prop` in `server/presets.py` `CHAINS`
  (build steps chained, `validate` + `export` on the last build version), `--weights` for the A/B. Peak
  VRAM per step = whole GPU from `nvidia-smi` sampled while the job runs, plus the step's own torch peak
  where it reports one. The golden set is our own prompts + seeds (`tests/fixtures/golden/prompts.yaml`)
  made into images by our concepts step (`--make-golden`): licence-clean, close to real input, but the
  images only exist after one laptop run. CSV header carries commit, GPU + driver, torch, Blender.
  `docs/bench/phase0.md` holds the procedure and an empty results table. CPU-verified: bench against a
  live in-process backend with `_echo` steps (row, failure, header).

### Phase 1: static assets, done (≈ 6 weeks)

- **M12 Retexture**: `steps.trellis2` op `texture` (shape-conditioned, keeps UVs via PR #173; image
  or text→Z-Image→image); acceptance: a pulled Blender mesh gets a new PBR set at 1024 without UV change.
  **As built**: op `texture` on any version with one UV-mapped mesh (several meshes/materials are refused:
  trimesh would merge them and repack their UVs). It maps the mesh into the texturing model's frame
  (centred, longest side 0.99999, Z up: upstream `preprocess_mesh`), runs `Trellis2TexturingPipeline`'s
  stages from the same TRELLIS.2-4B download (`texturing_pipeline.json`, DINOv3 pointed at `models/`), and
  bakes the decoded PBR voxels onto the mesh's **own** UVs with `steps/_bake.py`, so vertices, faces and
  UVs are written back unchanged and no PR #173 patch is needed (upstream's `run()` re-unwraps and uses
  nvdiffrast). The image is a prep version (`params.image`; default: the nearest one up the chain, i.e.
  the picture the mesh came from); text → image is the existing brief → concepts → pick (cut out only)
  chain. The version also stores `ovoxel.npz` + the mesh as `hi_trellis.ply` with its frame, so a later
  remesh/uv can re-bake from it (M3). Preset `trellis2/retexture` (resolution 1024, 12 steps, 2K); the UI's
  generate menus list generate presets only. Licences: TRELLIS.2-4B, DINOv3 + the image version's.
  CPU-verified (`test_texture_keeps_mesh_and_uvs`, model faked): vertices/faces/UVs identical, the model
  sees the mesh normalised and Z up, top/bottom texels get the right colours through the real bake,
  licences, `bake.find_volume` maps the mesh back onto the stored surface. Not run (needs GPU): a real
  pulled mesh at 1024 and its VRAM.
  **Laptop acceptance (2026-10-04)**: the owner's Blender-pulled dwarf (`420_blender`, one vertex moved)
  retextured from another picture of the asset (`image: 030_prep`) at resolution 1024, 12 steps, 2048²:
  38 s, 3.84 GB peak; vertices, faces and UVs bit-identical to the input; the black-silhouette mesh got
  the shaded dwarf's colours on all sides. Fixed on the way: the stages ran with autograd on (upstream's
  `run()` is no_grad, its stages aren't): `encode_shape_slat` kept 25.9 GB of encoder activations, sampling
  crawled at 34 s/step and the bake ran out of memory.
- **M13 Export breadth**: OBJ/STL/3MF via trimesh, `web` profile with gltf-transform (`meshopt`,
  texture resize), LOD chain (`simplify` ×3) + convex-hull collision; acceptance: Godot/Unity/Unreal
  import checklist done once manually and recorded.
  **As built (ponytail)**: **gltfpack** (zeux/meshoptimizer v0.25, MIT; one native binary pinned and
  SHA-256-checked by `server/gltfpack_install.py`, run by `run.bat`/`run.sh`, path in `EFFIGY_GLTFPACK`)
  instead of gltf-transform: no Node at run time (M15 zip). `steps/export.py` profiles: `unity`/`unreal`
  = one FBX whose FBX LOD group `<name>` holds `<name>_LOD0..3` (gltfpack `-si 0.5/0.25/0.1 -se 0.05`,
  attribute-aware, so LODs keep UVs; one shared material); `unreal` adds the convex hull (trimesh/scipy)
  as `UCX_<name>_00`; Unity has no collision naming (Mesh Collider, Convex, hulls it); a rigged source (a
  `skeleton.json` beside its mesh) ships no hull unless `collision: true` is sent (the export panel's default since M32), engines give characters their
  own physics and Unreal 5.8 imported the hull as a stray static mesh (`docs/bench/engine-import.md`); `godot` =
  GLB with hull node `<name>-convcolonly`, no LODs (Godot generates them on import); `web` = gltfpack
  `-cc -tw -tl 1024` (meshopt, WebP ≤ 1024 px, no Pillow pass needed) + USDZ; `print` = STL + 3MF via
  trimesh in **mm, Z up** (was Blender STL in metres); new `obj` = OBJ + MTL + base-colour texture.
  Params `lods`/`collision` (default on; a flat mesh ships without a hull instead of failing). 3MF is
  written with the stdlib (`write_3mf`: three small XML parts), not trimesh's exporter, which needs `lxml`
  (its wheels bundle LGPL libiconv); hulls need `scipy` + `networkx` (project deps, so `.venv-ml` has them). CPU-verified: `tests/test_export_profiles.py` (CI; gltfpack parts skip without
  it) and `tests/test_blender_ops.py::test_engine_profiles` against Blender 5.2.2 + gltfpack on Linux.
  Engine imports: checklist in `docs/bench/m13.md`, **not done**.
- **M14 Models & Licences page**: install/remove weights, show `licenses.yaml`, attribution strings,
  DINOv3 gate instructions; acceptance: page renders every row; attribution "Built with DINOv3" visible.
  **As built (ponytail)**: `server/models.py` + four routes (§3.5). Install is a job (`step: "download_models"`,
  no asset, no GPU lock, cancellable) running `python -m tools.download_models <group>` in `.venv-ml` (the only
  venv with `huggingface_hub`) with the steps' env; its stdout/stderr lines are `log` events (no progress
  fraction); one download at a time. A `gated:` line → `error.code = "gated"`; the page always shows the DINOv3
  steps (accept the licence on huggingface.co, `hf auth login` in a terminal, install again) and never takes a
  token. Sizes come from disk (stdlib): local folders filtered by the same allow/ignore patterns as the download
  (so Pixal3D single- and multi-view are separate rows), HF-cache repos under `EFFIGY_MODELS/hub` (blobs);
  "installed" = files present, not checked against the pinned file list (Install again resumes). Remove holds
  the GPU lock (409 if a step runs), evicts the resident worker (Windows can't delete open weights), then
  deletes that row's files; rows used by several groups (DINOv3, BiRefNet) are tagged "shared" and the confirm
  dialog names every group that loses them. The page is a `#models` overlay on the viewer area (no router;
  the viewer stays mounted; the jobs bar shows the download log). Tests: `tests/test_api.py` (listing, filters,
  HF cache, remove guards, licences, install running the real `tools.download_models` against a fake
  `huggingface_hub`) and the Playwright spec (every `licenses.yaml` row, "Built with DINOv3"). **Real download
  on the laptop (owner, 2026-10-04)**: every group installed from the page, DINOv3 after the gate (accept +
  `hf auth login`); `/api/models` lists all 9 rows installed: TRELLIS.2-4B 16.24 GB, TRELLIS-image-large
  0.15, DINOv3 1.21, BiRefNet 0.44, Pixal3D 24.04 (+ 22.0 multiview), MoGe-2 1.31, Z-Image-Turbo 32.9 (upstream
  ships its transformer in fp32, 23 GB; loaded as bf16), NAF; each model then ran in its step.
- **M15 Windows portable zip**: `tools/package.py` → zip with `uv.exe`, lock, `run.bat`; first run on a
  clean Windows VM reaches the UI; acceptance: documented in `docs/bench/install.md` with timings.
  **As built**: `tools/package.py` (stdlib) zips the tracked files (`git ls-files`) under the top-level
  entries a user needs (`KEEP`: code, `wheels/`, lock, `run.bat`, licences), the built `ui/dist` (npm runs
  only if it's missing) and `uv.exe` 0.8.17 + its MIT text, both SHA-256 pinned, into
  `dist/Effigy-<git describe>-win64.zip` (one `Effigy\` folder, ~25 MB). `run.bat`: a `uv.exe`
  next to it goes first on `PATH` with `UV_PYTHON_INSTALL_DIR=.\python`, `UV_CACHE_DIR=.\cache`,
  `UV_PYTHON_PREFERENCE=only-managed`, so everything stays in the folder (a checkout without `uv.exe` uses
  the `uv` on `PATH` as before); `EFFIGY_NO_BROWSER=1` skips the browser; a clear error if `ui\dist` is
  missing and npm isn't installed. `tests/test_package.py` checks the manifest. The "clean VM" stand-in is
  `.github/workflows/package.yml` (windows-latest, bare `PATH`, no `UV_*`): unzipped `run.bat` until
  `/api/health` and `/` answer, then a warm start; phase timings to the job summary. Not run yet: that
  workflow, and the manual clean-VM / laptop check (`docs/bench/install.md` "Still open").

### Phase 2: characters (≈ 8 weeks)

- **M16 Pose canonicalization**: Qwen-Image-Edit-2511 + AnyPose (Q4/FP4) vs HiDream-O1 bake-off on 10
  images; A-pose gate with DWPose arm angles; acceptance: ≥ 7/10 pass the gate after re-pose.
  **As built (branch `m16-pose`, 2026-10-04)**: HiDream-O1 was dropped before the bake-off (no diffusers
  pipeline, bf16 17–20 GB) for FLUX.2-klein-4B (Apache-2.0). Gate `steps/_pose.py` (DWPose ONNX on CPU,
  person box from the cut-out, arm/elbow angles); step `steps/canon.py` (prep cut-out + `prompts/apose_ref.png`
  → re-posed `image.png` + `gate.json`), then `prep` again → generate; brief category `posed` for inputs
  that keep their pose. Bake-off (`docs/bench/m16.md`): **klein 9/10 at ~27 s, 9.2 GB** (acceptance met);
  Qwen + AnyPose filled the card and never finished a step, so it was removed (code, download group,
  gguf/peft). Not done: UI button for re-posing (after the UI redesign lands), a limb-count check (the gate
  can pass a re-pose that kept extra raised arms). 2026-10-12: `look: "render"` for a flat picture, run by
  Animate this character, with Redraw again in the UI (see "Animate this character and the redraw").
- **M17 Multi-view**: turnaround ingestion (shared crop, `cameras.json`), Pixal3D-MV at the laptop preset;
  acceptance: 4-view run ≤ 14.5 GB, silhouette-height consistency check implemented.
  **As built (2026-10-04)**: `prep` op `views` (sheet split or one upload per view, one crop size,
  height check 5 %, `cameras.json` with c2w per view in Pixal3D's Blender frame); `trellis2` generate
  uses `Pixal3DMVImageTo3DPipeline.run_mv` when a Faithful parent has `cameras.json`. Runtime patches 14
  (multi-view sum without a concat) and 15 (NAF attention in value-channel chunks): 4 views 10.98 GB,
  128 s. Ground-truth turnaround check in `docs/bench/m17.md`.
- **M18 Quad remesh**: `remesh` gains `topology: quad` via `blender_ops/quadriflow` and the AutoRemesher
  1.2 CLI, pick-the-better by §3.9 mesh checks; symmetry; acceptance: 5 characters → pure quads,
  0 non-manifold, Hausdorff vs hi-poly reported.
  **As built (2026-10-04)**: QuadriFlow only (AutoRemesher not added: QuadriFlow passes all 5); input
  closed by `remesh.solid_hull` (Blender's voxel remesh hollows open meshes), QuadriFlow at ¼ budget and
  10× scale, holes filled, one Catmull-Clark level, shrinkwrap onto the input; `quads.obj` keeps the quads.
  Symmetry is opt-in (it mirrors one-handed props). `docs/bench/m18.md`.
- **M19 Rig fit**: `rig_fit` = 52-joint Mixamo template fitted from DWPose keypoints on orthographic
  renders + MHR/SOMA-X proportions; marker UI in the viewer (three-mesh-bvh raycast,
  `TransformControls`, symmetry); `skeleton.json`; acceptance: markers editable, skeleton saved,
  joints-inside-mesh check passes on 5 characters.
  **As built (2026-10-05)**: `steps/rig_fit.py` + `steps/_rig.py` (numpy) + `blender_ops/render_ortho`:
  front and back orthographic renders, DWPose on the side with the face, 13 markers at mid-depth, the 52
  joints derived from them (fingers from hand keypoints or proportions), joints snapped inside thin
  limbs; `skeleton.json`, `markers.json`, `report.json` and the mesh copied in. UI: "Rig" panel (fit,
  report, marker editor with symmetry, the skeleton drawn in the viewer). MHR/SOMA-X priors not added
  (fixed proportions pass all 5). `docs/bench/m19.md`. 2026-10-06 (M22 run): the facing comes from the
  face scores only when readable (≥ 0.5), else from the feet (`_rig.feet_cue`), else +Z; the sides are
  swapped when the pose model's labels contradict the facing; the ankle snaps to 30 % of the foot's
  heel-to-tip extent (the kid's boots) (`docs/bench/m22.md`); M22b: the hands' `twist` written from
  `_rig.palm_normals`, joint/twist overrides from the editor.
- **M20 Skinning**: `.venv-skin` with SkinTokens-NoBlender; `rig_skin` runs `--use_skeleton` on the
  52-joint template, and in parallel `blender_ops/skin_proxy` (voxel proxy bone heat + DATA_TRANSFER);
  §3.9 rig QA picks the better; prune to 4 influences; `rigged.glb` written by the pygltflib helper or
  Blender; acceptance: measured SkinTokens VRAM on the laptop in `docs/bench/m20.md`; 0 unweighted
  vertices; QA report in the UI heatmap view (skeleton helper + weight colours).
  **As built (2026-10-05)**: `steps/rig_skin.py` (runs in `.venv-skin`), engines `blender`
  (`blender_ops/skin_proxy`: armature from skeleton.json, bone heat on a 120-voxel proxy, DATA_TRANSFER
  onto the mesh, empty vertices from the nearest weighted one, hand/finger weights only on mesh connected
  to the hand) and `skintokens` (`steps/_skintokens`: the MIT no-Blender port, skin-only on our 52 joints,
  the rig transferred onto the textured GLB; weights group `skin`, 3.43 GB VRAM); `auto` runs both and
  `blender_ops/rig_qa` (weight sums, influences, L/R asymmetry, 10 poses: volume change, stretch of edges
  ≥ 0.5 % of the height) picks. ≤ 4 influences from both. UI: Skin it, the QA list with the engine
  picked, weights heat map + skeleton helper. `docs/bench/m20.md`.
- **M21 Animation**: bundled clip packs (Quaternius UAL 1/2, KayKit, Mesh2Motion: CC0; 100STYLE-retarget:
  CC-BY, so its `licenses.yaml` row carries the credit line that ends up in `CREDITS.txt`) with bone-name maps → `blender_ops/retarget` using the Retarget extension (installed into the portable
  Blender) → `rigged_anim.glb` + per-clip GLBs; clip player in the viewer; FBX profiles (Mixamo /
  UE5 Manny / Unity Humanoid naming); acceptance: a rigged character plays idle/walk/run in the viewer and
  imports as a Unity Humanoid (manual, recorded).
  **As built (2026-10-05)**: exports: `blender_ops/export.py` merges the per-LOD skeleton copies into
  one armature and names its bones per engine (Unity `mixamorig:`, Unreal UE5 Manny, Godot plain);
  `_lib.import_mesh` drops the importer's bone shapes. Clips: the Mesh2Motion human pack (CC0, 178 clips,
  group `clips`); `blender_ops/retarget` is our own world-space transfer (per bone a frame from joint
  positions, direction + twist reference, our rest turned onto the pack's, then the source's rotation
  away from rest; the body's forward from the toes, not the side names; the twist references from
  `skeleton.json` where present (M22b: the hands' palm direction, and what the user turned), else the
  finger formula; fingers keep the character's pose unless
  `fingers: true`; the Retarget extension carried the rest-pose offset into every frame and
  was dropped); `steps/anim.py` →
  `rigged_anim.glb` (one animation per clip) + `clips.json`; `GET /api/clips`; viewer AnimationMixer and
  Animation menu; the FBX takes and the Godot animations come from the same GLB. Not written: per-clip
  GLBs, the Mixamo/UE5/Unity naming of *clips* beyond the bones, KayKit/Quaternius/100STYLE packs (manual
  downloads). Unity Humanoid import: the owner's manual check, deferred by the owner. `docs/bench/m21.md`.
- **M22 Phase-2 exit bench**: 15 character inputs through `game-character`; ≥ 70% need no marker fix.
  **As built (2026-10-06)**: chain `game-character` (`server/presets.py`), `tools/bench.py` with the rig
  columns, the turnaround sidecar and the no-marker-fix count; 16 inputs (`tests/fixtures/characters`).
  Run on the laptop: **12 of 16 need no marker fix (75 %)**, 13 with the same-day fix for a bone-heat
  failure on the chef's proxy (`skin_proxy` retries coarser proxies; `rig_skin` auto keeps the engine that
  worked). The three that count as "needs markers" are the torso chain on the dwarf and the mage and the
  viking's head (the inside test on hollow or robed torsos), with the joints visually in place.
  `docs/bench/m22.md`. The inside test was corrected on 2026-10-06 (enclosed by surface along two axes
  counts): re-evaluated on each asset's latest skeleton, 14 of 16 read inside everywhere (the kid's
  ring finger, the mage's toes). The number above stays as measured on the day. **Accepted by the owner
  2026-10-06; Phase 2 closes.**

- **M22b Skeleton editor: correct the rig by hand** (owner, 2026-10-06, after the M22 clips: the
  automatic hand orientation was wrong on one of the viking's hands however the rule was turned, and
  the two-click marker placement is too coarse). The automatic fit stays as it is; afterwards the user
  can pick any of the 52 joints in the viewer and move or rotate it with a gizmo, Blender-style, with
  undo/redo and symmetry, then save → skin → animate. A rotation about a bone's own axis sets its
  *twist* (for a hand: which way the palm faces); a rotation about any other axis turns the bone's
  children around the joint.
  - Contract (§3.2): `skeleton.json` joints gain an optional `twist: [x, y, z]`, a unit vector in the
    library frame: the direction the bone's front faces (for `LeftHand` / `RightHand` the direction out
    of the palm; it also serves that side's forearm and fingers). `rig_fit` writes it for the hands from
    the automatic rule (the finger keypoints when they agree with the hand's shape, else the mesh's
    thinnest axis: `_rig.palm_normals`, moved here from the retarget), so the viewer shows what the
    retarget will do. `rig_fit` params gain `joints: {name: [x, y, z]}` and `twists: {name: [x, y, z]}`:
    overrides applied on top of the parent version's skeleton without re-detection, `source: "edited"`.
    `anim` hands `skeleton.json` to the retarget, which takes a bone's `twist` as its twist reference
    (hands in the finger-formula sign convention) and falls back to today's rule where there is none.
  - Viewer (`Viewer.ts`): every joint pickable (click selects, label); a `TransformControls` gizmo on
    the selected joint in the bone's own frame (X along the bone: rotating about X is the twist);
    translate and rotate modes (G / R, Esc cancels a drag); a twist tick on every bone (longer and
    labelled "palm" on the hands) showing the direction the retarget will use; subtree rotation and
    symmetry computed in the UI; `onJoint(name, {position, twist, children})` per finished gesture.
  - Panel (`main.tsx` RigPanel): "Edit skeleton" beside "Edit markers": joint buttons grouped (torso,
    arms, hands, legs), Move / Rotate toggle, symmetry, Undo / Redo (Ctrl+Z / Ctrl+Shift+Z, a history
    of {joints, twists} per gesture), "Reset joint" to the automatic value, "Save skeleton" → `rig_fit`
    with the overrides → the usual Skin it → Add animations.
  - Tests: `rig_fit` overrides (positions and twists written, the rest kept, inside check re-run);
    the retarget honours a hand twist on the block figure; Playwright: pick a joint, move it, undo
    returns it, save posts the overrides.
  - Acceptance: on the viking, turn the wrong hand's palm in with the gizmo, save, skin, animate → the
    clip plays with the hand right; undo and symmetry work; the owner judges in the viewer.
  **As built (2026-10-06, branch `m22b-editor`)**: as planned. `_rig.palm_normals` (the rule moved out of
  the retarget) writes the hands' `twist` in `rig_fit`; `rig_fit` params `joints` / `twists` apply on top
  of the input skeleton with no detection and no snapping (`metrics.source: "edited"`, `metrics.twists`);
  `anim` hands `skeleton.json` to the retarget, whose `frames()` takes a joint's twist as its reference
  (a hand's for its forearm and fingers, converted to the finger formula's sign). Viewer: `setRig(…,
  edit)` with pickable joints, twist ticks, a `TransformControls` gizmo in the bone's frame, `onJoint`
  per finished gesture (translate; rotate about X = the twist; other rings bend the subtree), Esc,
  test hooks `testJoint` / `testMoveJoint`. Panel: Edit skeleton, grouped joint buttons (fingers folded),
  Move/Rotate (G/R), Symmetry, Undo/Redo (Ctrl+Z / Ctrl+Shift+Z, 200 snapshots), Reset joint, Save
  skeleton (posts only what changed). Checked on the viking: `240_rig_fit` writes both hands' twists →
  `260_anim` palm-in; a flipped left-hand twist as an edit → `290_anim` palm-out. Tests: rig_fit
  overrides, `palm_normals`, the retarget with a hand twist on the block figure, Playwright (pick, move,
  undo, redo, save posts `{joints: {LeftArm}, twists: {}}`). Owner feedback the same day: the joints, bones and
  twist ticks follow the gizmo live during a drag (the same `gesture()` maths as the committed result;
  checked with a real mouse drag in Playwright: the joint had moved mid-drag and stayed on release), and
  a "Reset all" button puts every joint and twist back as one undoable step. The marker editor got the same gizmo move (translate only, world axes, the marker and its mirrored
  twin following live), Undo/Redo and Reset all; its two-click placement stays for setting depth fast. After the owner's first
  real edits: multiselect (Shift+click, Select chain; Move shifts the selected, the red ring twists them, the
  other rings swing the selected and their descendants rigidly about the centroid), depth cues (joints
  and bones behind the surface drawn dim, a plumb line from the active joint to the ground, an X-ray
  checkbox, X/Y/Z fields with a height readout), joints the inside check flags drawn red, smaller
  labels, Save greyed out until something changed, an "Edited by hand" line. Not done: Ctrl+Y.
  **Accepted by the owner 2026-10-06** after their own edits on the viking (`380_rig_fit` → `410_anim`).

- **M23 Faces from the image: project the generated image onto the texture** (owner, 2026-10-06, next
  after M22b: "the models have big trouble with faces; make them look like the generated images"). The
  bake samples a 1024 volume, which gives a face about 60 voxels across: the nurse's baked face is a
  smear with black eye holes while her conditioning image has a readable face at about 150 px. The
  Faithful generator (Pixal3D) is pixel-aligned with its conditioning image under a known camera, so the
  image can be projected onto the final low-poly UVs with a visibility test and no model (PLAN §7 "direct
  front-image projection", research 02 §7 item 4). **Verified 2026-10-06** on four bench assets by
  projecting the hi mesh with that camera onto the image: perspective, `fov` 0.2 rad (`metrics.fov`),
  camera at `0.5 / tan(fov / 2)` on +Z of the source frame (glTF axes, unit cube), NDC y up; the
  silhouette bounding boxes agree within 4 px (nurse, knight, viking, kid).
  - Step `project` (`steps/project.py`, `.venv-ml`, torch, no new dependency; evicts the resident worker
    like `bake`). Input: a textured UV-mapped version (bake's `mesh.glb`). Image: the prep version the
    mesh was generated from (`trellis2._image_version`), `params.image` overrides. Camera: Faithful →
    Pixal3D's from the generator version's `metrics.fov` and the chain's frame (`bake.find_volume`,
    whose matrix maps the source frame to this version's library frame); other weights (TRELLIS Fast
    keeps no facing and no camera) → skipped with a note: a silhouette cannot tell a face from the back
    of a head, so no fitted camera (review finding).
  - Per texel (the UV raster, as `bake`): position and normal → image coordinates; visibility from a
    z-buffer drawn with the same rasterizer in image space (`_bake.rasterize_uv` gains an optional
    per-vertex `depth` z-test). Weight = `strength` × smoothstep(cos of the view angle, 0.25 → 0.6) ×
    visible × the image's alpha × region. Region `head` (default): the band above the shoulder line read
    by the pose model on the image (DWPose, as `rig_fit`), full above the chin, fading to zero at the
    shoulders; fallback the top 16 % of the matte when no shoulders read (`metrics.head_from`). Region
    `front`: everything the camera sees. Colour: the mean colour of the projected pixels moved onto the
    mean of the baked texels under them (a per-channel offset over weight > 0.5, `params.match`, default
    on), so the lit image leaves no tone seam against the delit albedo and keeps its own contrast (a
    fitted gain flattened the faces); composite; gutters re-filled.
  - Gate: silhouette IoU (the z-buffer's coverage against the matte) below 0.8 → the textures are copied
    unchanged, `metrics.skipped` and a `note` say why (a Fast asset, a moved frame, a wrong image).
  - Outputs (§3.2): `mesh.glb`, `basecolor.png`, `orm.png`, `normal.png` (the two copied), `overlay.png`
    (the image with the mesh silhouette edge and the region band). Metrics: `camera`, `fov`, `iou`,
    `region`, `head_from`, `strength`, `changed` (fraction of the covered atlas with weight > 0.5),
    `match`, `image_from`, `skipped`. Models / licences: DWPose when the head region was read.
  - Chain: `game-character` gets `("project", {})` after `bake`; `game-prop` does not (props have no
    face; a "front" projection on props is a product decision for later).
  - UI: on a textured version, "Face from image" (region Head / Whole front, strength) → `project`; the
    version's panel shows `overlay.png` and one line ("Face from the image: silhouette match 0.93, 9 % of
    the atlas", or the skip note). Playwright: the button posts `["project", {region, strength}]`.
  - Tests: `rasterize_uv` with depth (the near triangle wins); the step on a textured box in a fake chain
    (generator meta with an identity frame, `fov` 0.2, `weights: faithful`; a prep image with a colour
    where the front face projects): the front face takes the colour, the back keeps the bake, IoU high;
    the gate on a mismatched image copies the textures.
  - Acceptance: the nurse's face from the front reads like the image (eyes, mouth), no visible tone seam
    at the neck; the owner validates on the bench characters after `project` ran on their bake versions.
  - Not in M23: a second (back) view, delighting, upscaling the image first (the face is about 150 px in
    the image; an upscaler would sharpen it further), any camera for Fast.
    **M23b** (next in the owner's order): heads and hands geometry (the kid's torn hair, the hiker's stub
    fingers): find the cause along generate → clean → remesh first.
  **As built (2026-10-06, branch `m23-faces`)**: as planned. `steps/project.py` (camera, head band,
  overlay, the texel pass), `_bake.rasterize_uv(..., depth=)` as the z-buffer, `("project", {})` after
  `bake` in `game-character`, the UI's "Face from image" (region, strength) and the result line with
  `overlay.png`. Two fixes from the first runs: inward-wound patches on the remeshed bake mesh (the
  nurse's face) made the cosine weight drop whole triangles, so the weight uses smooth normals rebuilt
  from face normals turned towards the camera; the z-test takes the farthest depth of the pixel's 3×3
  neighbours plus 1 % of the depth extent. Run on eight bench characters on their bake versions: silhouette
  match 0.92–0.96, 2–7 % of the atlas, 6–8 s, 1.3 GB VRAM; the faces read like the images (nurse, kid,
  farmer, goblin, mage; `docs/bench/m23.md`, `m23_faces.jpg`). Tests: the z-test, the box chain, the gate
  (`tests/test_project.py`); Playwright 10/10. **Accepted by the owner 2026-10-06** ("good for now").

- **M23b Heads and hands geometry** (owner, 2026-10-06; the kid's torn hair, the hiker's stub fingers).
  **Investigated 2026-10-06** (`docs/bench/m23.md` "M23b", `m23b_stages.jpg`, `m23b_rebuild.jpg`): the
  hair tear is a pit left where `clean`'s 512 surface rebuild merged separate hair strands, widened by
  the 15k remesh into a dent that shows the scalp's colour from the volume; a finer rebuild (1024) leaves
  the strands apart and makes holes. The fingers are mitten-and-spike at generation and stubs after the
  15k decimation. No cheap fix: a closing of the narrow band before contouring (not exposed by CuMesh),
  a region-wise face budget for hands, or the 1536 generator profile; the owner decides. Done: `bake`
  fills texels whose sample finds no volume from their neighbours instead of black (a robustness fix,
  tested; it does not change the kid's dent).
  **Owner decision 2026-10-07: accepted as is**; the options are noted for a version 2 of the app in
  PLAN Phase 4 and HANDOVER §9.

- **M24 Faces from the image, round two: sharper and without the baked lighting** (owner, 2026-10-06,
  "proceed with the next items" after M23). Two limits of M23: the face is about 150 px tall in the
  conditioning image, and the image is lit while the albedo is not.
  - **Upscale.** Real-ESRGAN x4plus (BSD-3-Clause, the v0.1.0 release file, 67 MB; research 04 names it
    and spandrel; `steps/_sr.py` writes the RRDBNet out instead, no basicsr or realesrgan package and no
    new dependency) runs on the premultiplied image, in 256-px tiles with 16 px overlap, before the
    projection samples it; the alpha is resized bilinearly. New model group `texture`
    (`tools/download_models.py`, a Setup card "Face detail"); when the weights are not installed the
    step projects the plain image and says so in `note`, `metrics.upscaled` and the panel; the installed
    group is the switch, there is no parameter.
  - **Local colour match** (`params.match: "local"`, the new default; `"mean"` is M23's, `false` none):
    the bake as the camera sees it (the z-buffer's surface points looked up in the atlas) and the image
    are both blurred over about a nose's length (σ = 8 % of the figure's height, at 1/8 resolution with
    the masks carried along) and their difference, per pixel, is added to the projected image: the
    image's large-scale shading becomes the bake's flat albedo, the image's detail stays. Zero models.
  - **Tried and dropped**: Marigold-IID appearance v1.1 (OpenRAIL++, 2.6 GB, 13 s, 4 GB VRAM) as a
    delighting model: its albedo of the nurse's head was a flat painting, the eyes and lips reduced to
    shapes, exactly the detail the owner wants kept (`docs/bench/m24.md`). The weights were removed.
  - Tests: the RRDBNet's key set and 4× shape, the tiled upscale, the missing-weights message
    (`tests/test_sr.py`); in `tests/test_project.py` the match modes on a gradient image (mean keeps the
    gradient, local flattens it), the upscaled image being what gets projected (a faked 4× upscaler, the
    model declared), the not-installed note, a bad `match`.
  - Acceptance: the nurse's eyes and lips sharper than M23's, the face tone even, no new seam; the owner
    judges on the bench characters (`m24_faces.jpg`).
  **As built (2026-10-06, branch `m24-faces2`)**: as planned; the `upscale` parameter dropped (the
  installed model group is the switch), `rasterize_uv` made deterministic on shared edges (one triangle
  per texel), the knight's visor found to be a generation recess (`docs/bench/m24.md`). Nurse 8–10 s,
  1.9 GB; eight characters on the sheet. **Accepted by the owner 2026-10-06** ("good for now").

- **M25 UX pass: readable panel texts, placement help for joints and markers** (owner, 2026-10-06,
  after using the editor: "the step descriptors above the buttons can be made more readable; for rigging
  a small help about where each joint and marker should sit will help inexperienced users").
  - Every panel's instruction text rewritten for a newcomer: second person, one idea per sentence, the
    button named, no internal terms (picture, model, skeleton, animation instead of version, step,
    chain); set slightly larger with a comfortable line length.
  - A `PLACEMENT` table in the UI: one plain sentence per joint (the 52) and per marker (the 13) saying
    where it belongs ("The elbow, inside the arm at the crease"), shown as the button tooltip, as a
    "Where it sits" line for the selected joint or marker, and with four general rules in a collapsible
    help at the top of Edit skeleton (inside the body where the bone turns; check depth from the Side and
    Top views or with X-ray; the character's own left and right; markers may sit on the surface).
  - Tests: Playwright asserts the line for a selected joint and a marker. No backend change.
  **As built (2026-10-06, branch `m25-ux`)**: as planned. 30 panel texts rewritten (Reference, Generate,
  Prepare, Rig, Animate, Export, Setup, New asset, Blender, Claude; the Export profile lines in
  `workflow.ts`); `p.muted` / `ul.muted` at 14 px, line-height 1.45, 60 ch; `PLACEMENT` (52 joints, the
  fingers generated per side) and `MARKER_PLACEMENT` (13) in `main.tsx`, as button tooltips, a "Where it
  sits" line for the selected joint or marker, and the four rules in "How to place joints"; the long
  editor help paragraphs became bullet lists. Playwright 10/10 (the line asserted for LeftArm and Chin);
  checked in the browser on the viking. **Accepted by the owner 2026-10-06** ("looks good").

- **M27 One tab per step** (owner, 2026-10-07: "each step, as it already is, shall have its own upper-right
  tab in workflow order, with optional steps marked"). **As built (2026-10-07, branch `m27-tabs`)**:
  `STAGES` in `ui/src/workflow.ts` is a typed list of twelve tabs in pipeline order (Reference, Generate,
  Clean, Simplify, Unwrap, Bake, Face, Rig, Skin, Animate, Check, Export; `stageOf` maps a version by its
  step, `phaseOf` keeps the six-phase logic the panel sections use); Face, Rig, Skin and Animate are
  marked optional (dashed border, an "optional" caption, a tooltip "Optional: for characters"), enabled
  when they can run from the current version; the header fits one row at 1500 px. On a Clean, Simplify,
  Unwrap or Bake version the panel offers that step's own button with its parameters first ("Run this
  step again"), the whole recipe below. Playwright 11/11 (the twelve tabs in order, the optional marks,
  Bake navigates to the bake version, Simplify posts `["remesh", {faces}]`); checked in the browser on
  the nurse's simplify version. **Accepted by the owner 2026-10-07.**

- **M26 Animate from a text prompt (Kimodo)** (owner, 2026-10-07: the next item; Kimodo is NVIDIA's
  text-to-motion model, the owner's clone sits at `D:\CODE\kimodo`, upstream `nv-tlabs/kimodo` at
  `1aece8c`, unmodified). PLAN Phase 3 "Kimodo text-to-motion (SOMA → canonical retarget)".
  **Decided 2026-10-07** (owner: "go through the kimodo repo, the licence question is answered there;
  follow their implementation, also for the encoder"): the Llama 3 Community License row in PLAN §6, the
  text encoder on the CPU as upstream advises under 16 GB VRAM, no quantisation.
  - **What it is**: Kimodo-SOMA-RP-v1.1 (NVIDIA Open Model License: commercially usable, no claim on
    outputs, attribution only when the model itself is distributed; a 282 M-parameter denoiser, 1.1 GB,
    trained on Bones Rigplay 1, 700 h of commercial mocap) generates 30 fps motion of up to 10 s per
    prompt on the SOMA 77-joint skeleton (Y up, +Z forward, metres; BVH export in centimetres with a
    standard T-pose). Its text encoder is LLM2Vec on **Meta-Llama-3-8B-Instruct** (gated on Hugging Face,
    Llama 3 Community License; the LLM2Vec adapters are MIT): about 16 GB in bf16, run on the CPU
    (`TEXT_ENCODER_DEVICE=cpu`, VRAM under 3 GB) or quantised to 4 bit on the GPU (about 5.5 GB).
  - **Decision 1 (licence, PLAN §6)**: the Llama 3 Community License is not on the allowed list. It
    permits commercial use below 700 M monthly users, requires "Built with Meta Llama 3" on a product
    that uses the weights and a copy of the licence when the weights are redistributed (we never
    redistribute: the user downloads from Hugging Face after accepting the gate, as with DINOv3). The
    generated motions are outputs, not Llama Materials. Proposed row: allowed, flagged, attribution
    "Built with Meta Llama 3" shown on the Setup card and in CREDITS.txt. Without it there is no Kimodo:
    the model has no other text encoder.
  - **Decision 2 (where the encoder runs)**: (a) bf16 on the CPU: 16 GB of the laptop's 32 GB RAM while
    a prompt is encoded, tens of seconds per prompt, no new dependency; (b) 4-bit on the GPU with
    bitsandbytes (MIT; Windows wheels exist): about 5.5 GB VRAM plus the 1 GB denoiser, seconds per
    prompt, quantised by us at load from the official weights (no third-party quantised checkpoint).
    Recommendation: (b), with (a) as the fallback when bitsandbytes does not load.
  - **Architecture**: its own venv `.venv-kimodo` (Python 3.12, torch 2.8 cu128; `kimodo` installed with
    `--no-deps` from the pinned upstream commit plus only what generation needs: hydra-core, omegaconf,
    transformers==5.1.0 (pinned upstream; the ML venv has 5.18), peft, einops, scipy, pydantic, bvhio;
    not gradio, viser, py-soma-x, MotionCorrection). Upstream is Linux-tested, Windows "via Docker":
    the first task is a native install and one generation on the laptop.
  - **Step `motion`** (`steps/motion.py`, runs in `.venv-kimodo`, evicts the resident worker; GPU lock):
    op `generate`, params `{prompt, seconds ≤ 10, seed, steps (default 100)}` → `kimodo_gen --bvh
    --bvh_standard_tpose` → `motion.bvh`, then Blender (`blender_ops/bvh_clip.py`) imports the BVH and
    writes `clip.glb`: an armature in the SOMA names with the one clip, in the library frame (metres, Y
    up). §3.2 row: `motion` → `clip.glb`, `motion.bvh`, `prompt.json`. Metrics: seconds of motion,
    frames, generation time, VRAM peak, encoder device. Models/licences: the three rows.
  - **Retarget**: `blender_ops/_lib.py` gains a `SOMA` name map beside `UE5` (Hips, Spine→Spine1,
    Spine1→Spine2, Spine2→Chest, Neck→Neck1, Head; Shoulder, Arm, ForeArm, Hand; UpLeg→Leg, Leg→Shin,
    Foot, ToeBase; Thumb1–3; Index/Middle/Ring/Pinky 1–3 → SOMA 2–4, SOMA's joint 1 being the
    metacarpal, to be checked against the T-pose); `retarget.py` takes `params.source` (`ue5` | `soma`)
    for the map and the pelvis name; the palm reference from the mapped finger joints is verified on the
    T-pose before the first clip.
  - **`anim`**: `params.clips` entries may name a `motion` version of this asset (`"310_motion"`) beside
    pack clip names; `catalogue()` lists the asset's motion versions with their prompts. The outputs do
    not change.
  - **UI (Animate tab)**: a text field "Describe a motion", seconds, Generate → a `motion` version with
    a playable preview (the SOMA clip on a stick figure is enough: the viewer plays `clip.glb`); the
    generated clips appear in the clip list with their prompt and go through Add like the pack's. Setup:
    a "Text to motion" card for the `kimodo` model group (denoiser + adapters; the Llama weights need
    `hf auth login` after accepting the gate, as DINOv3 does), with the "Built with Meta Llama 3" line.
  - **Tests**: the SOMA map covers every one of our 52 names or deliberately leaves it out (fingers'
    metacarpal); the BVH → GLB conversion on the standard T-pose BVH (joint count, frame, units); the
    retarget with `source: soma` on the block figure; `anim` accepting a motion version; the licence rows.
  - **Bench (`docs/bench/m26.md`)**: native install result; three prompts ("walks forward", "waves
    with the right hand", "jumps once") on the nurse at 5 s: generation time, VRAM and RAM peaks, the
    clips rendered (hands checked as in M21) and the owner's judgement.
  - **Not in M26**: constraints (keyframes, paths, waypoints: the Kimodo demo's timeline), multi-prompt
    sequences, the real-time ARDY follow-up, SAM 3D Body video-to-motion.
  **As built (2026-10-07, branch `m26-motion`)**: as planned, with the bone maps moved to a bpy-free
  `blender_ops/_maps.py` (tests import them) and the retarget telling a pack from a Kimodo clip by its
  bones (no `source` param). `.venv-kimodo` from the `kimodo` dependency group (declared conflicting
  with `ml` and `skin`: Kimodo pins transformers 5.1); model group `kimodo` (the SOMA-RP v1.1 denoiser,
  the two LLM2Vec adapters, the gated Llama 3 8B Instruct encoder as safetensors, 16 GB); `steps/motion.py`
  runs `kimodo_gen --bvh --bvh_standard_tpose` with `TEXT_ENCODER_DEVICE=cpu`; `blender_ops/bvh_clip.py`
  → `clip.glb` (78 bones, one animation named after the version, hips at 1.0 m); `anim` takes motion
  versions in `clips`. UI: "Make an animation from a sentence" and "Your motions" in the Animate panel,
  a motion version's panel, the step registry, the "Text to motion" Setup card. Checks: Kimodo's standard
  T-pose BVH through `bvh_clip` and the real `anim` step poses the nurse in a clean T-pose (rendered);
  tests in `tests/test_motion.py`; Playwright 11/11. Bench (`docs/bench/m26.md`): generation 37–48 s per 5 s clip (the 16 GB encoder loaded per prompt on the CPU), the three prompts read correctly on the nurse (walk 2.9 m forward, a right-hand wave to 0.95 m, a 12 cm jump).
  **Accepted by the owner 2026-10-07** ("looks good"). The owner's first run (viking `520_anim`)
  worked; the animation is since named from its prompt, unique per asset (`motion.clip_name`). M27 merged
  into this branch (motion versions on the Animate tab).

- **M28 Library browser for many generations** (owner, 2026-10-04 and 2026-10-07: "work through the
  list"; the sketch in HANDOVER §11). Seventeen bench characters with dozens of versions each made the
  flat lists unreadable.
  - Contract (§3.2): `asset.json` gains `archived: bool` (hidden from the grid's default view, nothing
    else changes); `meta.json` gains `starred: bool` and `label: str` (the user's own mark and name on a
    version). `GET /api/assets` items carry `versions` (count) and `updated` (the newest version's
    time); `PATCH /api/assets/{id}` takes `{name?, archived?}`; new `PATCH /api/assets/{id}/versions/{v}`
    takes `{starred?, label?}`.
  - Library grid: a toolbar with search (id and name), sort (newest, oldest, name), a filter (all, mine,
    bench, golden, archived) and a count; Archive/Unarchive on the cards beside Rename and Delete; the
    version count and the updated date on each card.
  - History: the intermediate steps (clean, simplify, unwrap) fold into one "N steps" row between a
    model and its bake; milestones stay as rows; a star toggle and an editable label per version; a
    "Starred only" filter.
  - Tests: the library (archived, starred, label kept; the listing's count and time), the API routes,
    Playwright for the grid toolbar and the folded history.
  - Not in M28: comparing two versions side by side (the sketch's last item), turntable thumbnails.
  **As built (2026-10-07, branch `m28-library`)**: as planned. Backend: `library.update_asset`
  (name, archived), `library.update_version` (starred, label), the listing's `versions` and `updated`;
  the two PATCH routes. UI: the grid toolbar (search, Newest/Oldest/Name, All/Mine/Bench/Golden/Archived,
  "N of M assets"), Archive/Unarchive on the cards with the version count and date; History folds
  clean/simplify/unwrap into an "N steps" row (open when it holds the current version), a ☆/★ per row,
  a pencil for the label (a browser prompt), "Starred only". Tests: test_library, test_api; Playwright
  14/14 (the toolbar's search, sort and filters, Archive's PATCH, the folded chain, the star's PATCH);
  checked against the running app with 103 assets. **Accepted by the owner 2026-10-07** ("looks very good").

- **M29 Setup page: readable and friendly** (owner, 2026-10-07: "make the setup section more user
  friendly and better readable"). A summary line in plain words; the capability cards in workflow order
  with a coloured status dot and words ("Installed · 27 GB", "Not installed · 27 GB to download",
  "Needs a Hugging Face login first", "Downloading…") and one button; the gated-model instructions as a
  small "Before you install" expander inside the card (DINOv3, Meta Llama 3) instead of a dense
  paragraph; sections "What this app can do", "Tools" (Blender), "Credits for your game", "Advanced"
  (the install-per-group buttons, the model files table with readable headers, the full licences).
  Tests: Playwright on a mocked listing (installed, missing, gated-missing). UI only.
  **As built (2026-10-07, branch `m29-setup`)**: as planned; the summary line is computed from the
  listing ("Everything for pictures, 3D models, faces and animation from text is installed (134 GB on
  disk)." or "N of 5 features need files.", plus the Blender state once known); cards wrap at four per
  row; gltfpack has no state API and stays out of Tools; "Needs a Hugging Face login first" shows for any
  missing gated model. Playwright 15/15 (the summary, the three card states, the expander, the credits,
  the Advanced table); checked against the running app at 1500 px. **Accepted by the owner 2026-10-07.**

- **M30 Kimodo resident: prompts in seconds** (the M26 open item; owner 2026-10-07: "continue with
  the list"). `motion` joins the resident families (`jobs.SERVE`): the step process stays alive after a
  prompt with the loaded model in `motion.MODEL` (the denoiser on the GPU, the text encoder on the CPU),
  so only the first prompt pays the load; the server evicts it after its idle time (5 min) like the
  generator, freeing the 16 GB of RAM. The step calls Kimodo's Python API the way its CLI does (the
  same `model(...)` call and BVH export, copied from `generate.py`) instead of spawning the CLI per
  prompt; the library's prints go to stderr so the step protocol on stdout stays clean.
  **As built (2026-10-07, branch `m30-motion-resident`)**: measured in one process on the nurse's three
  prompts: the first 30.7 s (load included), the next two 3.7 s each (`metrics.model_was_loaded`).
  Tests: the step around a faked generator and Blender, the gated-encoder message from the loader, the
  rest as M26. The app needs a restart for the new resident family.

- **M31 Parts: click a part of the model** (PLAN Phase 3 "part segmentation for rigid accessories and
  print split"; owner 2026-10-07: "continue with the list"). **Survey 2026-10-07** (web, recorded here
  because `docs/research` had only one table row): SegViGen (the plan's candidate) needs Linux, 24 GB
  VRAM, flash-attn, nvdiffrast and nvdiffrec, all outside our rules: out. SAMPart3D trains per object on
  PTv3 (flash-attn, spconv): out. SAMesh (SAM 2 on multi-view renders) has no licence file in its repo:
  out until it has one. **GeoSAM2** (VAST-AI, CVPR 2026; Apache-2.0 code and checkpoint, 154 M
  parameters, 616 MB (294 MB in bf16), built on Meta's SAM 2, Apache-2.0): renders 12 views of the mesh with its
  own headless Blender script, takes a click or a mask on one view, propagates across the views and
  back-projects to per-face labels; the CUDA extension is optional (`GEOSAM2_BUILD_CUDA=0`, only a
  post-process). "Linux only" in its README, nothing in it that needs Linux. **HoloPart** (VAST-AI, MIT
  code and weights, 3.7 GB) completes the hidden side of a segmented part later, from the labels.
  - **Spike first** (laptop, no feature code): GeoSAM2 in a scratch venv, the checkpoint, its render
    script on our Blender 5.2, one click on the viking's helmet and one on the hiker's backpack, the
    labels rendered: time, VRAM, label quality. **Spike done 2026-10-07 (laptop)**: GeoSAM2 runs on Windows with torch 2.8 cu128 in a scratch venv (its CUDA extension skipped; its runtime C++ mode helper replaced by 12 lines of torch; OpenCV 4 for the EXR depth, OpenCV 5 refuses it; the twelve views from our own Blender 5.2 script, since its render script needs Blender 4's compositor). On the viking (15k faces): three clicks on the helmet in the front view → the helmet as one label (887 faces, prompt-only lift with post-processing 15 s; raw lift 9 s; the single-view click step 5 s; the renders about 60 s once per mesh); the full mode (the opposite view auto-segmented, 66 s) decomposed the whole figure into helmet, head, shoulder pads, torso with arms, belt and strap, skirt, legs (`docs/bench/m31_spike_viking.jpg`). VRAM a few hundred MB. Go.
  - Step `parts` (`steps/parts.py`, `.venv-ml` unless its pins conflict (they do: `.venv-parts`, below); evicts the resident worker):
    op `views` renders the 12 views (`blender_ops/parts_views.py`, GeoSAM2's conventions, cached in the
    version), op `segment` with `{view, points: [[x, y, label]]}` or a mask → `parts.json` (per-face
    labels, the parts' names from the user) and `mesh.glb` (one node per part, textures kept; the
    existing single-mesh readers merge it back, so nothing downstream breaks). Licence rows:
    VAST-AI/GeoSAM2, facebook SAM 2 (Apache-2.0).
  - UI: an optional "Parts" tab: the 12 views as thumbnails, click a point on one (or on the model:
    the click maps to the nearest view), "Find part" → the part tinted in the viewer, a parts list with
    names, "Split" writes the multi-mesh version. Later milestones: rigid-bind a part to a bone
    (accessories), HoloPart completion, the print split through the export.
  - Risks: Windows (their render script and sam2 on Blender 5.2 / torch 2.8), label quality on
    stylised characters, the multi-mesh GLB through export and skinning (merge-everything readers).
  - **As built (2026-10-07, branch `m31-parts`)**: GeoSAM2 as our own pure-Python wheel
    (`wheels/geosam2-0.1.0-py3-none-any.whl`: upstream b5de23c, no CUDA extension, its `opencv-python`
    and `ninja` requirements dropped from the metadata) in its own `.venv-parts` (dependency group
    `parts`, declared conflicting with ml/skin/kimodo: torch 2.8 cu128 and OpenCV 4 headless, since
    OpenCV 5 cannot read its EXR depth maps and `.venv-ml` has 5); its two scripts, LICENSE and NOTICE
    in `engine/geosam2` with a torch twin of its compiled per-row mode helper (`PATCHES.md`); the
    checkpoint as model group `parts` (`models/GeoSAM2/geosam2.pt`, 616 MB). Its per-row mode helper
    counts over the distinct labels only (a dense table of rows × 1000 labels took 4 GB of RAM at 100k
    faces: step-reviewer). Step `parts`
    (`steps/parts.py`, one-shot, evicts the resident worker): op `views` has Blender render the twelve
    views into `views/` of the version (`blender_ops/parts_views.py`: GeoSAM2's cameras, RGBA WEBP
    colour and normals, float32 EXR depth through Cycles at one sample because EEVEE's half-float film
    quantises depth to 1/1024, and the mesh re-exported with its vertices unmerged and its parents
    cleared (a mesh under a parent node was never normalised: step-reviewer) as the face order the
    labels follow) and copies that mesh as `mesh.glb`; above 200k faces it asks for a simplified mesh
    (measured to 149k: a click in 67 s); op `segment` runs the clicks through GeoSAM2's
    single-view predictor, then its lift with post-processing (`PA` 0.02), merges the labels with the
    parent's `parts.json` (a clicked part takes faces from older parts and replaces a part of the same
    name; `full: true` auto parts only fill free faces and slivers under 2 % are dropped) and has
    Blender split the views' mesh into one textured node per part plus `Body`
    (`blender_ops/parts_split.py`, bmesh copies, materials and UVs kept) as `mesh.glb`, beside
    `parts.json` and `click.png`; "Body" is reserved for the rest, and a part GeoSAM2 drops as too small
    (under 64 pixels in the view, or 2 % of the largest part) says so. Measured on the viking (15 401
    faces): views 12 s, a click 16–20 s (peak VRAM 1.8 GB), the full decomposition 74–76 s; rig_fit and
    rig_skin on the split versions match the unsplit bake (`docs/bench/m31.md`). Trimesh's and Blender's
    face orders agree on multi-primitive GLBs (step-reviewer's probes, and a Blender test). UI: the optional Parts tab
    (the views as thumbnails, clicks with a name → Find part, All parts, the parts tinted in the
    viewer, matched by the glTF node names three.js keeps in `userData`), a Setup row for the model
    group. Tests: the merge rules, the views lookup, the params (Body), the face cap, the views op around
    a faked Blender, the segment op around a faked lift and split, the mode twin against the C++ rule
    (in `.venv-parts`), and in `test_blender_ops.py` the two real Blender ops on a two-material model
    under a rotated, scaled parent (normalised, cut along trimesh's face order). Later: rigid-bind a part
    to a bone, HoloPart, the print split. Not checked: skinning a part split off at a hand (a shield, a
    glove): `skin_proxy` keeps hand weights only on mesh connected to the hand.

- **M31b The parts in the game files** (owner 2026-10-07, after asking what a parts version is good for:
  the export and the skinning joined the parts back into one mesh). Export keeps one object per part,
  named like it, for Godot, Unreal, Unity, OBJ and web; print stays one solid until M33. Rename a part
  from the Parts list without the GPU. Acceptance: the viking's `522_parts`, renamed, in the Godot
  headless import and the Unreal FBX import with one mesh per part and the names.
  - **As built (2026-10-08, branch `m31b-parts-export`)**: `steps/export.py` sees a parts version by
    its `parts.json` and hands `blender_ops/export.py` the object name `"{}"` (`"{}_LOD<n>"` with LODs):
    such an input is imported with `_lib.import_meshes` (the file's objects as they are, split out of
    `import_mesh`) and each object named by the template with its own name; LOD groups form per
    `<base>_LOD<n>` prefix, so every part gets its own group (`Helmet` → `Helmet_LOD0..3`). gltfpack gets
    `-kn` for parts (without it the LODs and the web GLB come out as one unnamed mesh: measured), the hull
    is made per part (`UCX_<part>_00`, `<part>-convcolonly`; a flat part ships without one), OBJ is
    written from the trimesh scene (one `o` per part; single-mesh versions too, one `o`). `parts` op
    `rename` (`{"names": {old: new}}`): `parts.json` and the GLB's node and mesh names rewritten in its
    JSON chunk (the binary chunk byte for byte), no Blender; `server/jobs.py` `LIGHT` takes `step:op`
    entries so `parts:rename` keeps the resident worker. Part names (segment and rename) are checked by
    `name_ok`: up to 56 bytes, not Body, no `{ } / \` or control characters, no `_LOD<n>` ending, since
    they become Blender and engine object names (`UCX_<name>_00` within Blender's 63 bytes; the
    export's `"{}"` templates; an LOD group) (step-reviewer). UI: the parts list's names are editable, "Rename
    parts" sends the changed ones; the Setup card says what parts do today. Measured on the viking
    (`docs/bench/m31.md` M31b): Godot 4.7.1 five named meshes with a convex body each, Unreal 5.8 five
    static meshes with 4 LODs and a hull each; Unreal 5.8 turns Nanite on at import (LOD0 then reads as
    the fallback; the same on the unsplit mesh). Tests: rename (names, binary chunk, a rename on a rename,
    bad names), the parts naming per profile with gltfpack's `-kn` and the OBJ objects, and a real
    Blender + gltfpack export of a two-part version (an LOD group and a `UCX_` per part in the FBX, the
    parts and their hulls in the Godot GLB). Not done: deeper LODs on meshes gltfpack stops at a third
    (`-se`), Godot's generated collision body names.

- **M32 Rigid parts on the skeleton** (owner's order 2026-10-07): the parts stay separate objects through
  rig_fit, rig_skin and anim; a part marked rigid (helmet, shield, weapon) gets all its weight on one
  bone, by default the one nearest its centre (Helmet → Head), changeable in the Rig panel; the other
  parts skinned as before. Acceptance: the viking's helmet moves solid with the head in Walk and the
  custom motion (measured and rendered), exported to Godot and Unreal.
  - **As built (2026-10-08, branch `m32-rigid-parts`)**: `parts.json` rides along rig_fit (with
    `metrics.parts` for the panel), rig_skin and anim, so the export keeps the parts on rigged and animated
    versions (M31b's rule). `blender_ops/skin_proxy` tags each face with its part (a face attribute),
    skins the joined mesh as before and splits it back into skinned objects named like the parts (only for a parts version: rig_skin passes `parts`; any other model of several objects stays joined, step-reviewer);
    SkinTokens' transfer already kept the input's nodes. rig_skin `params.rigid` `{part: bone or ""}`:
    `""` → `steps/_rig.nearest_bone` (the body bone, no finger, whose segment joint → child passes nearest
    the part's mean vertex; a leaf continues its parent bone), then `make_rigid` rewrites the part's
    `JOINTS_0`/`WEIGHTS_0` in place in each engine's GLB (`steps/_glb`: the GLB's chunks and accessor
    views, numpy; no Blender round trip) before `rig_qa` judges it; `rig_qa` joins the parts of its copy.
    `steps/_glb` also carries the parts rename now. UI: under Skin it, one choice per part (bends / rigid
    on the nearest bone / rigid on a named body bone); the skin result names the rigid parts and their
    bones. Measured on the viking (`docs/bench/m32.md`, `m32_helmet.jpg`): the helmet's vertices against
    the Head bone's motion 0.00000 m in all 121 + 41 frames (the same rig without `rigid`: up to 1.6 cm);
    Godot 4.7.1: six skinned meshes on one skeleton, the helmet on Head alone; Unreal 5.8 combines skinned
    meshes into one skeletal mesh named after the first part unless "Combine Skeletal Meshes" is Do Not
    Combine (then six on one skeleton). Tests: `nearest_bone` (helmet, held sword, forearm shield, toe),
    the rigid flow and its params around faked engines, rig_fit carrying `parts.json`, a real Blender
    skin_proxy on a two-part block figure with the helmet made rigid and judged by rig_qa, Playwright for
    the choices. A `make_rigid` failure counts against that engine only (auto keeps the other). Known limit (`ponytail:` in skin_proxy): a held prop split off as a part that is not rigid loses the hand's weights; make it rigid on the hand. Rigged exports' static collision hulls (since M21): the export panel's existing Collision hull tickbox now defaults to off for a rig_skin or anim version, with a line why, on for static models (owner 2026-10-08: the user decides).

### Phase 3 (PLAN §7): motion (M26/M30 Kimodo, M34 GEM-X, M64 more from a video, M65 connect clips), parts (M31/M32/M33; SegviGen and HoloPart dropped on
platform and licence), textures (M43), creatures (M36/M41), the batch mode (`tools.bench --export`,
2026-10-10). Dropped by the owner: multi-colour 3MF and the export breadth (2026-10-07), the own bridge add-on (no
trigger yet, PLAN §7), generic rigs (M59, dropped 2026-10-13 by the owner (SkinTokens' training data with no bone-heat alternative)). Each item got its own milestone section when scheduled.

- **M34 Motion from video** (PLAN Phase 3; owner 2026-10-08: "go for your recommendation", GEM-X may ship;
  research 12 with the spike). A video of a person becomes a clip on the character, beside text-to-motion.
  - Engine: NVIDIA GEM-X in its own venv `.venv-gemx` (group `gemx`, conflicting with the others: torch 2.10
    cu128; the spike's install without Detectron2 or PyTorch3D), GEM-X, SOMA and SAM 3D Body at pinned commits
    (git sources like `kimodo`, or our own wheel like GeoSAM2 where the package layout needs it: SAM 3D Body is not
    a package, GEM-X reaches it through `third_party/`). Weights as model group `mocap` (~6.8 GB:
    `gem_soma.ckpt`, `vitpose.pth`, SAM 3D Body + config and MHR from `nvidia/GEM-X` at a pinned revision, SOMA's
    assets, the YOLOX person detector), nothing fetched at run time (DINOv3's code from torch.hub and the YOLOX
    ONNX from openmmlab today: pinned and hashed instead). Patches (`engine/gemx/PATCHES.md`): DDIM really on
    (upstream sets the flag on the wrong module), YOLOX on the GPU (ONNX Runtime's CUDA provider did not load).
  - Step `mocap` (one-shot, takes the GPU lock, evicts): in: a video upload version of the asset
    (`NNN_upload` learns `.mp4`/`.mov`/`.webm`, scaled to ≤ 720 px on the long side before GEM-X: RAM);
    params `{mode: quick|best|keypoints, static_cam: true, start, end, person, in_place, foot_lock, smooth}`;
    out: `motion.bvh` (the spike's upright-rest SOMA writer: world offsets, `body_pose` as channels, grounded,
    the first frame facing +Z from the origin), `clip.glb` (`blender_ops/bvh_clip`, as M26), `preview.mp4`
    (2D keypoints on the video), `metrics` (seconds, VRAM, 2D fit, jitter, foot slide). `anim` takes a mocap
    version like a motion version (its regex learns `_mocap`), so it lands in the Animate tab's "Your motions".
  - Cleanup on the SOMA motion before the BVH: smoothing (a one-euro or Savitzky–Golay filter on joint rotations
    and the root, strength `smooth`), foot lock (contact from foot height and speed; the root shifted so a planted
    foot stays put, a ponytail before IK), in place (the root's horizontal travel removed). Defaults from the
    acceptance clips, not guessed.
  - Modes (research 12): Best (DDIM, default), Quick (one pass, the closest 2D fit), Keypoints only (no SAM 3D
    Body: no gated weights or SAM licence in the chain; wanders more). Licence rows per mode (`licenses.yaml`:
    GEM-X weights NVIDIA Open Model License, code Apache-2.0, SOMA Apache-2.0, SAM 3D Body SAM License, MHR
    Apache-2.0, ViTPose's DINOv3 backbone DINOv3 License, YOLOX Apache-2.0, ByteTrack MIT; the keypoint model's
    and the YOLOX "humanart" model's training data checked before the merge).
  - UI: in the Animate tab "Motion from video": upload or drop a clip, the trim, the mode, the cleanup ticks,
    Make motion; the result as a motion with its preview. Setup card "Motion from video" (the group and the venv).
  - Acceptance: the owner's dance clip and two more (a walk across the room, a sword swing or kick) on the viking
    in all three modes: renders of body and hands next to the video, foot slide and drift with the cleanup on and
    off; Godot and Unreal exports carry the clip. Later, its own milestone if wanted: SAM 3D Body + SAM-Body4D as a
    second engine, an Auto that keeps the better result (as rig_skin does).
  - **As built (2026-10-08, branch `m34-video-motion`; accepted by the owner after a run on the nurse, merged with
    M32; the walk and sword-swing clips and a video motion's Godot/Unreal export were not run)**: GEM-X's `gem`
    and configs and SAM 3D Body's package vendored unmodified in `engine/gemx` (README, PATCHES.md: every
    difference from upstream's demo lives in `steps/mocap.py`); `.venv-gemx` (group `gemx`: torch 2.10 cu128,
    `py-soma-x` at `e0f8ff0`, `onnxruntime-gpu < 1.24` for CUDA 12; `GIT_LFS_SKIP_SMUDGE=1` when syncing); model
    group `mocap`: `nvidia/GEM-X` at `5ccf5ca` in GEM-X's `inputs/` layout under `models/GEM-X`, SOMA's assets
    from its Git LFS at `e0f8ff0` (Hugging Face only has newer asset versions this code cannot read), DINOv3's code
    at `6876159` for ViTPose's backbone class (unpacked where torch.hub looks), YOLOX-L from `yzd-v/DWPose` as the
    person detector instead of mmpose's "humanart" model (its training data's terms unverified); nothing fetched
    at run time, every cache under `models/GEM-X`. Step `mocap`: the clip at 30 fps and at most 720 × 1280; YOLOX
    (decoded and NMS'd by us, tracked by overlap, `person` = the n-th largest at the start); GEM-X's ViTPose, SAM
    3D Body's features (not in Keypoints only), GEM-X with DDIM really on (Best, Keypoints only); the SOMA motion
    grounded, turned to face +Z from the origin, smoothed (Savitzky–Golay on quaternions and the root), its
    planted feet held by moving the root, optionally in place; the upright-rest BVH; `clip.glb`; a preview of
    the boxes and keypoints; metrics (time, VRAM, foot slide before and after the lock, jitter). The library
    takes `.mp4`/`.mov`/`.webm` uploads; `anim` takes `_mocap` versions and now carries every motion version's
    licences into the animated version (M26 dropped Kimodo's and Llama 3's, so an export's CREDITS lacked
    "Built with Meta Llama 3": fixed). UI: "Make an animation from a video" in the Animate tab (a file, the
    quality, the trim, foot lock, in place, smoothing; the upload, then the step on it), video motions under
    Your motions, the motion's page with its preview, a Setup card. Measured (`docs/bench/m34.md`): the dance
    in Best, 105 s, 4.31 GB, foot slide 0.167 → 0.110 m/s with the lock. Tests: the BVH read back against the
    forward kinematics, the cleanup (floor, facing, foot lock, in place, smoothing), the tracker, the clip
    preparation, the params; anim's licences; Playwright for the video flow. After the step-reviewer: clips over
    30 s refused (GEM-X and SAM 3D Body each hold every frame in RAM), every model file checked first and Hugging
    Face offline (a missing file would have been downloaded mid-job), DINOv3's code loaded as a local repo (no
    GitHub request per run), a WebM's timebase taken for its frame rate replaced by 30, the paths resolved before
    the engine's chdir (the upload's name was lost), the clip pack's names reserved, uploads capped at 1 GB, a
    retry reuses the upload, a video preview kept out of the thumbnails (PIL raised), the §3 tables updated.

- **M35 Setup for everyone** (owner 2026-10-08, before opening the app to the public: "where space is located
  on which model, cleanup options if space gets almost used up, automatic (re)install for users, easy guided
  setup"). Three parts, one PR each:
  - **M35a Space**: `server/storage.py` sizes every big item in a background thread, cached with a time: each
    model group (entries shared by groups counted once, under "shared"), each venv (`.venv*`; hardlinks to the uv
    cache counted once by file id, so the cache shows only what no venv uses), the uv `cache/`, `python/`,
    `blender/`, the library (per asset, the archived ones, `_trash/`), HF-cache leftovers; free and total space of
    every drive involved (`shutil.disk_usage`; `EFFIGY_MODELS` may sit on another drive). `GET /api/storage`. Setup
    page: a "Space" section, a bar per drive and a sorted list. Low space (under 10 % or 20 GB free on a drive the
    app writes to): a banner on Setup and in the top bar. Expected sizes per group become data
    (`tools/download_models.py`), and an install checks the free space first.
  - **Cleanup** in M35a: empty the library trash, delete archived assets (M28), remove a whole model group or the
    venv of a feature you do not use (both reinstallable), prune the uv cache; each with its size and a confirm,
    nothing removed while a job uses it.
  - **M35b Install and repair**: a card's Install does everything the feature needs: the venv
    (`uv sync --frozen --group …` as a job with its log; `.venv-kimodo`, `.venv-parts`, `.venv-gemx` are created
    by hand today), the model group, Blender or gltfpack; progress as bytes on disk against the expected size.
    Repair = verify (the venv imports its step's modules; every pinned file present with its size, from a file
    manifest written when a group is pinned, hashes where we have them) and re-fetch only what is missing or
    wrong; Reinstall = remove + install. "Installed" stops meaning "any file present".
  - **M35c First run**: a guided page when nothing is installed: the machine check (GPU, VRAM, driver, free space,
    Windows long paths), where the models go (a drive with room), what you want to make (presets of groups:
    props, game characters, characters with motion, everything) with total size and an honest time range, the
    Hugging Face steps for gated models, then one install queue. Docs: SETUP.md rewritten around it.
  - Acceptance: a fresh folder on this laptop (and the clean-VM check still open from M15) gets to a first model
    with no terminal command except the Hugging Face login; the space view's totals within a few % of Explorer's;
    a deleted venv and a truncated model file are found by Repair and fixed.
  - **M35a as built (2026-10-08, branch `m35a-space`, accepted and merged the same day)**: `server/storage.py` sizes every big item in one pass
    (6.7 s on the reference laptop with 132 GB of models, 6 environments, 22 GB of library: on request, cached for
    5 minutes): each model group from its pinned entries' own files (entries several groups need listed as
    shared by them), the rest of the models folder ("other": unpacked code, kernel caches, old revisions), each
    environment, the app's own uv cache (the portable zip's `cache/`; a dev checkout uses the user's), Python,
    Blender, gltfpack, the assets, the archived ones, the trash; a size is what removing it frees: single-link
    files only, hard-linked ones (the environments share torch through uv's cache) counted once under "shared".
    Cleanups: empty the trash, delete an archived asset (one by one, by its folder, re-checked as archived and
    as the asset its `asset.json` names, with no job queued on it), `uv cache prune` (only the app's own cache),
    remove a group's own entries (shared entries stay), remove a feature's environment (skin, kimodo, parts,
    gemx, hunyuan; never `.venv` or `.venv-ml`); the guarded ones hold the GPU lock (every step runs under it),
    stop the resident worker and refuse during a download. An asset or an environment is renamed out of the way
    first (atomic: an open file fails it whole, nothing half-deleted stays in use), then deleted; files that
    could not be deleted are counted and stay (in the trash, or as "removed environments not fully deleted",
    their own cleanup). Junctions and symlinks are never followed when measuring or deleting; a drive is the
    drive letter on Windows, the mount point elsewhere, found even before the folder exists. After the
    step-reviewer: the per-asset deletion (a folder copied with another asset's id could have deleted the live
    one; a stale scan could have deleted an asset the user never saw listed), the rename-first deletes, the
    junction and prune guards, the honest texts. `SIZES` per group (measured with everything installed) give the cards' download
    sizes and the install's free-space check (507 `no_space`; the folder's drive even before the folder exists:
    a first draft refused every fresh install). UI: Setup's "Space on disk" (a bar per drive, the list biggest
    first with each safe cleanup and its question, Measure again, Freed …), a "Low disk space" link in the top bar
    (drives checked every 5 minutes). Tests: the sizes on a fake install with a hard-linked package, every
    cleanup, the busy guard, the low rule, the install refusal; Playwright for the section and the warning.
  - **M35b as built (2026-10-08, branch `m35b-install`, accepted and merged the same day)**: `server/install.py`. A card's Install is one job:
    the group's environment (`.venv-ml` for most, `-skin`, `-kimodo`, `-parts`, `-gemx`) is synced when
    `uv sync --frozen --check` says it is missing or out of date (80 ms; also catches an app update that changed
    `uv.lock`), then `tools.download_models`, then every file checked against `tools/models_manifest.json`
    (`python -m tools.download_models --manifest`: each PINNED row's files from the Hub's tree at its pinned
    revision, filtered as `snapshot_download` filters, with size and LFS SHA-256; FILES rows by their pinned
    SHA-256; 192 files; `SIZES` is derived from it, the measured table is gone). "Installed" = every file there
    at its size. Repair also test-imports the environment's packages (reinstalls them with `--reinstall` if that
    fails) and deletes files whose size or SHA-256 is wrong (an HF-cache link with its blob) before the
    download. Syncs and deletes hold the GPU lock (steps wait; the resident worker is stopped). Installs queue
    in order; the space check counts the queued ones. Progress: bytes on disk (present files and `.incomplete`
    parts) of the expected, every 2 s. Setup cards: Installed / Needs repair (with why) / Not installed (with
    the exact size) / Queued / Installing with a bar, Repair, Check and repair. Blender and gltfpack: run.bat
    checks and installs them at every start, no card. Found on this laptop on the first run: GeoSAM2's README
    never fetched, and `.venv-parts` out of step with its local wheel (a git checkout rewrote its date).
    Acceptance run (2026-10-08, the real models and environments, a second backend): `.venv-parts` moved
    away and RealESRGAN truncated to 1 MB were both reported, Repair of parts and texture (queued) fixed both
    in 30 s (environment imports with CUDA, the file's SHA-256 matches), 34 of 34 rows complete. After the
    step-reviewer: Repair reads and hashes without the GPU lock (cancellable every 8 MB) and takes it only to
    delete; it never deletes behind a link or outside the models folder (a dev junction may point into another
    checkout); `job.proc` is cleared after each command (a cancel between commands could have killed a reused
    process id); an environment uv cannot check (`--check` exit 2, no uv) is left alone instead of resynced;
    the duplicate check is atomic; a cancel in the last moment stays a cancel; the manifest takes a FILES row's
    size only from the pinned file. Cards: status and action level along each row, Cancel on a queued one.
  - **M35c as built (2026-10-08, branch `m35c-first-run`, accepted and merged the same day)**: `#/welcome` ("Get started"). The very first start
    opens it by itself (owner, after a first version without: the server says `first_run` while nothing is
    installed and the user neither started an install there nor clicked Skip, kept as `welcomed` in
    `settings.json`; only from the plain start page, never over a link to an asset); later a card in the
    library (while nothing is installed) and a card with a button at the top of Setup lead there (owner: a
    line of text was easy to overlook). `EFFIGY_SETTINGS` points at another settings file: the UI tests use one
    that marks the first run done. 1 the machine check (`server/machine.py`: nvidia-smi, GlobalMemoryStatusEx / sysconf, the
    LongPathsEnabled key; driver 575 as in SETUP step 1, 16 GB VRAM good, 12 GB warn with "use Fast", 30 GB
    RAM good); 2 the models folder (`server/settings.py`: `settings.json` in the app folder, gitignored; every
    reader goes through `settings.models_root()`, the steps get it as EFFIGY_MODELS; the choices: the current
    folder and one per drive of 64 GB or more; moving: `install.move_models`, the install queue's turn and
    the GPU lock, a rename on the same drive, else a copy checked file by file, the setting switched, then the
    old copy deleted; refused up front when the copy would not fit); 3 presets (`install.PRESETS`, after the
    PLAN §5 chains: props 59.9 GB, characters 77.7, motion 102.4, everything 141.4; download time at 500 and
    50 Mbit/s; a "does not fit" note); 4 Hugging Face (`server/hf.py`: status, whoami and `auth_check` per
    gated repo in 0.9 s; the token in a password field, cleared once sent, through stdin to
    `huggingface_hub.login()`; a test proves it reaches only the store: not a response, an event,
    settings.json or the library); 5 one install queue for the preset's missing groups (M35b's queue),
    with a note when a licence is not accepted yet. Setup gets "Where the models are" and a link back.
    SETUP.md rewritten around it (steps 5 and 6 became "Get started"). Checked on the laptop with the real
    answers (every check green, logged in, DINOv3 accepted, every preset installed: "Ready"). After the
    step-reviewer: a copy goes into `<new>.effigy-partial` and is renamed into place only when whole (a failed or
    cancelled one is removed: no half copy can be adopted as "a models folder already"); the copy and its
    size check do not follow junctions; the space check also runs when a same-drive rename falls back to a
    copy; once the setting switched, a late cancel still reports done; no install while the models move; a
    token-store failure and timeouts/proxies are told apart from a bad token; request-validation errors name
    the field, never echo its value (a body may carry a token); a drive root is refused;
    `tools.download_models` from a shell uses the chosen folder too.
  - Owner decisions 2026-10-08: order M34, then M35, then M33; **the Hugging Face login moves into the UI** (the
    user pastes a token; it goes straight to huggingface_hub's own token store, never into the library, logs,
    job params or the browser's storage, and the page only shows whether a login exists and as whom); **the user
    chooses the model folder** in the first run (and can move it later), e.g. on another drive.

- **M33 Print split** (Phase 3 "print split"; owner's order 2026-10-08: after M35). The 3D-print profile of a
  parts version (M31) writes one STL per part, all of them in one STL, and a 3MF with one named object per
  part; HoloPart to complete a part's hidden side was to be a spike first.
  - **HoloPart: out on its licence (2026-10-08, before any download)**: its `NOTICE` and the header of
    `holopart/models/transformers/triposg_transformer.py` say the transformer is "based on Tencent
    HunyuanDiT … licensed under the Tencent Hunyuan Community License … portions copied or adapted", and its
    attention carries FlashVDM (Tencent Hunyuan FlashVDM Community License). That licence gives no rights in
    the EU, UK and South Korea (PLAN §6): not for the shipping profile. TripoSG (PLAN's alternative
    generator) has the same file and header: out too (PLAN §6 updated). Its other dependencies (diso,
    torch-cluster, pymeshlab) were not looked at further.
  - **As built (2026-10-08, branch `m33-print-split`, accepted and merged the same day)**: a part cut from a generated model is a piece of its
    skin (the viking's parts: 3 to 183 holes and 1 to 52 loose islands each; the whole mesh isn't watertight
    either), so per part hole-filling and a voxel remesh fail (tried: the skirt and the legs fell apart). The
    cut is made in the volume instead (`steps/_print.py`, numpy + scipy, already in `.venv-ml`): the parts'
    surfaces sampled into a voxel grid (300 voxels over the model's height, `print_detail`), the skin
    thickened by 2 voxels until its holes shut, the inside filled, thinned back; every voxel to the part
    whose surface is nearest (a KD-tree); each part's voxels → their outer faces (closed by construction);
    crumbs under 1 % of a part's largest piece dropped. `blender_ops/print_parts.py`: a voxel remesh at the
    grid's size (manifold where voxels met at an edge) and a light Laplacian smoothing (shrinks a little)
    round the steps off. The parts fit together like a jigsaw (the helmet a solid cap over the top of the head); they
    are about half a voxel larger on every side (1.7 mm on a 1 m model). `write_3mf` writes named objects.
    The viking (`527_parts`, six parts): 7.4 s, every STL watertight, 0 non-manifold edges, 1 to 5 pieces
    a part, 1.1 to 9.4 MB per STL (26 MB the whole), rendered beside the original: the same figure. Tests:
    a box's skin cut in two open halves → two closed solids of half the box each; the print export of a
    two-part version with a real Blender (STLs watertight, the 3MF's named objects). Not run: a real
    print, a slicer import (PrusaSlicer, Bambu Studio). After the step-reviewer: every part keeps at least its
    largest piece and a part missing after Blender fails the export (the parts travel through Blender as
    part0, part1…: it shortens names over 63 bytes); `print_detail` bounded to 100–600 (the viking at 600:
    35 s); the corner merge on one integer key per corner; the winding right by construction (an assert, no
    silent flip); a model whose fill adds nothing (a hole wider than the closing: an open box 0 %, the viking
    6.6 % of the thickened skin) gets a note on the version that the parts are hollow shells. Known limits,
    written down: gaps under about 4 voxels fuse (13 mm on a 1 m figure: fingers, an arm near the body),
    the remesh resamples on Blender's grid (one-voxel-thin features can thin out), neighbours fit to about a
    voxel, `<name>.stl` is the parts side by side, not their union.

- **M36 Creature skeletons** (PLAN Phase 3 "quadruped/bird/creature templates"; owner's order 2026-10-08:
  after M33). Mesh2Motion (pinned 79f3f61, CC0 art) has nine rigs, each with its own clips on the same
  skeleton (bone names checked identical): fox 49 bones and 14 clips, horse 56 and 14, bird 55 and 5; dragon,
  snake, spider, shark, kaiju later the same way. Survey (effigy-explorer): the human pipeline keys everything off
  joint names (the 52-joint template, DWPose markers, the skinning's hand and torso rules, the retarget's
  reference bones, the editor's groups and mirror, the FBX bone renames); `skeleton.json`'s `template` field
  was written and never read. Spike (D:\lt2-spike\creatures, Mesh2Motion's dog mesh with its own rig removed):
  the fox template stretched onto it, bone heat, the fox's clips: a dog that walks and sits. Go.
  - **As built (2026-10-08, branch `m36-creatures`, merged the same day)**: `steps/_templates.py` (bpy-free): the templates' bones
    (`steps/templates/<id>.json`: name, parent, head, tail in the glTF frame, from Blender's reading of the rig:
    `tools/templates.py` + `blender_ops/template_bones.py`, rigs SHA-256-pinned), their side suffixes (fox
    `_L`/`_R`, horse `_l`/`_r`), torso and limb bones. The fit (no pose model reads animals): the facing by
    proportions in quarter turns (the template's box against the mesh's) and the head at the end that rises
    higher (16 of 16 right on four example animals turned four ways), the template's box onto the mesh's,
    then the middle line (spine, neck, head, tail) onto the body's centre line: the mesh sampled by area (a
    low-poly model has too few vertices on its centre plane), each slice's surface crossings paired into solid
    spans, traced from the hips outwards to the span nearest the last (an open mouth splits a slice); the
    head's own joints keep their shape, side joints follow their spine joint by height (an ear fully, a foot
    on the ground not at all); joints a little outside snapped in (`_rig.snap_inside`). On the dog: 42 of 49
    joints inside (the root on the ground, ear tips and the front legs' upper joints outside). `rig_fit`
    `{"template", "yaw"?}` writes `skeleton.json` `{"template", "yaw", "sides", "joints": [{name, parent,
    position, tail, to?}]}` (no markers); editor moves keep the template and move a leaf's tail along.
    `rig_skin`: a creature skins with bone heat only (SkinTokens knows the 52 joints: refused), its bones
    pointing where the template's do (`to`: the child it points at, else the fitted `tail`; the root deforms
    nothing), the limb-off-torso rule with the template's torso and limbs, the QA's symmetry by suffix across
    the creature's own middle (x, or z when it faces sideways). Dog: every check passes (symmetry 0.012, test
    poses 2.8 % volume, stretch 2.2). Clips: `clips/mesh2motion/creatures/<id>-animations.glb` (group
    `clips`, SHA-256-pinned); `blender_ops/creature_clips.py` moves each bone by the template bone's turn from
    its rest in the world (our bones' local axes differ: Blender's roll, pointed at their child), turned into
    our facing (the hips-to-head direction of each skeleton: a dog generated sideways got legs swinging the
    wrong way before), the root and hips also travel, scaled by the hips' height; pose bones solved from the
    wanted armature-space matrices parent first, no depsgraph per bone (3 clips in 1.1 s). Rendered: Walk a
    four-legged walk, Sit on its haunches, Run a gallop. `anim` takes the template's clips (`catalogue(t)`,
    its rest pose left out; GET /api/clips?template=), refuses Kimodo and video motions for animals. Export
    keeps a creature's bone names in FBX (no `mixamorig:`/Manny renames: a generic rig). UI: Rig "What is
    it?" (person, four-legged, hoofed, bird), Turn around / Turn a quarter for a creature, the editor's
    joints in Middle / Left / Right groups, mirroring by the template's suffixes across the creature's middle,
    no markers or fingers; the Animate list shows the animal's clips and hides the person-only motions.
    `server/presets.py` CHAINS `game-creature` (no canon, the fox, Idle/Walk/Run). Tests: every template one
    skeleton with paired sides; the facing four ways on a box animal; the spine in the body and the tail in
    the tail; `rig_fit` and an edit; the clip list; a box animal skinned (no fails) and walking through a real
    Blender; Playwright for the flow. Also rendered: Mesh2Motion's horse (rig removed, turned 270°: the facing
    found, 38 of 56 joints inside, every skin check passing) walks and trots; the eagle flaps and glides.
    After the step-reviewer: the clips turn by the fit's `yaw` (from skeleton.json), not by the bones (an
    upright bird's hips-to-head is vertical); only the trunk (hips to head, the tail) goes onto the centre
    line, a belly or rib cage hanging off it keeps its place under it; a template without a head bone and a
    `yaw` other than a quarter turn are refused; the root is not snapped; clips solved parents first by
    depth; §3 rows for `rig_fit`, `rig_skin`, `anim` and `/api/clips`. The facing guess was checked on four
    animals standing, not on an upright bird (the proportions decide less there): Turn fixes it.
    Not run: a creature generated with the app (the owner's GPU), engine imports of a creature.

- **Generation background** (owner 2026-10-08: "a cat, both Faithful and Fast leave big holes on the cat's
  back"): the models see the cut-out on a flat background, black in their preprocessing (`premultiply_black`
  in prep; TRELLIS.2 and Pixal3D upstream do the same), so near-black parts read as background: the cat (35 %
  of its pixels under 40/255) with Fast, seed 7, lost its whole black body on black (1.0 m² of surface) and
  was whole on grey (2.3 m²); Faithful was whole on both (it projects its voxels into the picture), its raw
  holes closed by Clean (1 left). `trellis2` generate `background`: auto (grey for Fast on a subject over
  15 % near-black, black otherwise: 32 of the library's 99 preps are that dark, their Faithful results stay
  as they were), black, grey, white; read from the prep's `image.png` (a prep without it: its black
  composite). UI: "Background behind your picture" on Generate, and a note on a generated version that the
  raw model's small holes close in Prepare model, a missing area calls for grey. Multi-view generation keeps
  black (its views are composited by prep).

- **The cat: holes after Prepare, a skeleton that broke** (owner 2026-10-09: "011 checks did run, but those
  still left quite big holes open, which i can directly see in wireframe. then i tried to map a skeleton to
  it, which completely broke"). Holes: the Faithful cat's back came out a thin shell with holes up to 7 cm,
  its inside open to the outside; Clean closed it into a thick shell, the quad remesh made tunnels where the
  holes were (7 through the baked cat), and validate counted only open edges: "watertight", passed. The
  library had the same on both dwarf turnarounds (52 and 65 tunnels). `remesh` now fills a hollow shell
  through its holes first (`fill_inside`: the air the outside reaches only through passages narrower than
  2r voxels and that lies deeper than r + 1, the smallest r that finds an inside half the skin's size; a gap
  between two legs is sealed too but thin, so it stays air), from holes of 3 voxels (4 % of the size; the
  46 of 66 library models with smaller ones keep their old path) to 10 (1/8): tri simplifies the filled
  solid (print_parts' voxel remesh and smoothing), quad shrinkwraps onto it (onto the holey input it pinned
  quads to the hole rims: 75 non-manifold edges; onto both, the input's noise came through). The cat: one
  watertight piece, 0 tunnels, both topologies (rendered). `fill: false` keeps a model meant to be open
  inside. `mesh_report` counts `tunnels` (Euler characteristic, closed meshes) and validate warns on them
  (`no_tunnels`). Skeleton: the cat stands at 36° (a three-quarter picture), and quarter turns can't face
  it: every turn put head and legs outside; "the head end rises higher" also chose the upright tail. The fit
  now turns the mesh back by its lean (PCA of the footprint, 0 under 3° or for a roundish one) and fits in
  that frame, `yaw` = lean + quarter turn; the head end is the one with more of the body above the back
  (with next to nothing above it, a bird, still the one reaching higher); along the body the template's
  feet go onto the mesh's (piecewise with the box's ends: box onto box put the cat's hips under its belly,
  the fox's long tail lengthens only the template's box) and the tail chain follows the mesh's own tail
  when the template's sticks out of the solid (the thin part an opening leaves, nearest the tail base, off
  the floor, its joints spread by the distance along it). rig_qa mirrors across the plane the yaw turns,
  the editor too. Checked on the cat (40 of 49 joints inside, skin passing, Walk and Idle rendered from its
  side) and the spike's seven Mesh2Motion animals four ways turned (the same yaw each time; joints inside
  as before or more: fox 43 → 47, panda 33 → 44). The owner's cat needs Prepare again from `007_clean`,
  then Rig.
  Second round (owner, on the refit `020_rig_fit`: "still seems off"; "the automated skel should apply as good
  as possible to the asset"; "will those checks also be valid for any other animal, like dog, horse, mouse"):
  the cat looks at the camera, its head 33° off its body, and the fox's face (chin, nose, an ear) stuck out of
  it. A head turned aside now turns its face by the line between its ear tips (within 1.5× the head's reach:
  the highest point and the highest a quarter of that away, with a dip between them an eighth of their
  height deep; a domed head is highest in its middle, so a panda, a horse, a bird stay straight), each ear
  chain from its base to its tip (the turn from 10°, under it uneven ears: Mesh2Motion's horse read 8°;
  tips far from the template's, horns or a raised tail at the wrong end, turn nothing). A mouse of boxes (a long thin tail on the floor) showed two more: its tail
  counted as back feet (the hips went behind its rump), so floor streaks three times as long as wide are not
  feet (a panda's long soles still are), and the centre-line trace now takes a lone short run on the floor as
  a span (a thin tail's walls fill the trace's band). The root, on the floor by design, is no longer reported
  outside (a creature's `inside` count is one higher than before for that alone). The cat: 46 of 49 joints inside (chin tip and two sole joints out), skin passing (stretch 6.0 →
  3.5), Walk rendered; the seven Mesh2Motion animals four ways turned and the mouse: the same yaw each turn,
  joints inside as before or more (horse 20 → 23, mouse 30 → 43).

- **Check fit** (owner 2026-10-09, after fixing the cat's skeleton by hand in `029_rig_fit`: "a helper button
  or something, so you can check if my manual edits now do fit the model better"): `steps/_rig.fit_quality`
  scores a skeleton in its mesh: per joint how centred it sits across its limb (the lower of the two shortest
  axis-parallel wall-to-wall spans through it, 1 the middle, 0 the skin or outside; end joints, a toe or ear
  tip, only checked for being inside; a root on the floor left out), per bone the share of it outside the
  body (nine samples), `score` the joints' mean centring in % (an end joint outside counts 0) times the
  bones' mean share inside, and `attention`, one line per joint, the worst first (outside, a bone into it through the air, close to the skin under 0.25). `POST
  /api/assets/{id}/versions/{v}/fit-check {"joints": {name: [x, y, z]}}` scores a rig version's skeleton
  with the editor's unsaved moves on top (0.5 s on the cat). UI: Check fit under a skeleton (against the
  version before it, when that has one) and in the skeleton editor (the unsaved moves against the saved
  skeleton): "Fit 54 · before (028_rig_fit) 42 · better", the joints to look at (a click opens the editor on
  one). The owner's cat: the automatic fit 42 (6 joints outside, 4 bones through the air), the hand-edited
  54 (all inside; Spine_3 to Spine_4 a third outside, a front ankle's bone half). Also: run.bat built the
  UI only when `ui/dist` was missing, so a restart after a pull kept the old UI (the owner's was from
  10-08, without the day's editor and checklist changes, and the Playwright suite ran against it too); now
  `tools/ui_stale.py` rebuilds it when `ui/src` is newer (npm ci first when the lock changed; without npm the
  old build is served), the same in `tools/package.py`. The editor's left-right mirror direction is rounded
  to 1e-12 (a quarter turn mirrored to -0.2999999999999999).

- **M37 Animation editor** (owner 2026-10-09: "once animations are either generated or just put onto the
  skeleton from our preset library, I can check the animation, but if for some reason it does look 'off', I
  will need to go through the animation, check keyframes and such with a time slider (like most animation
  editing tools, perhaps along Blender's animation UI) and then be able to completely fine-tune the animation
  itself"). Research (three agents: the codebase, animation editing practice, web tooling): our clips are
  baked, a key on every frame at 24 fps (`clips.json` fps; Kimodo/GEM-X 30 fps resampled by retarget), every
  bone's rotation plus the Hips' (a creature's root and Hips') translation, one glTF animation per clip;
  editing one frame of a dense clip pops, so animators keep the base clip and edit a correction layer on top
  (Maya/MotionBuilder/UE layers, Blender NLA Combine; Kovar's footskate cleanup spreads a fix over its
  neighbours). Theatre.js studio is AGPL, animation-timeline-js stale: our own canvas timeline, no new
  dependency. The viewer could only loop a clip (no pause, scrub or time).
  - **Data**: the base clip stays read-only; the user's edit is a sparse layer (§3.2 `anim_edit`: bone-local
    rotation keys with falloff, translation offsets, trim, speed, loop match, ground, in place, smoothing),
    evaluated identically in TypeScript (the live preview: the viewer plays the evaluated clip) and in numpy
    (the bake on save, straight into the GLB's animation accessors: the same node-local space three.js
    animates, no Blender, the rest of the file untouched). An `anim_edit` version keeps its layer in
    `edit.json` and re-opens on the base, so edits never stack on a bake; export takes it like any animated
    version (the first `.glb` of its outputs, one animation per clip).
  - **37a Check**: under an animated version, a time bar: play/pause (Space), the playhead dragged or typed
    to a frame, ←/→ a frame, Home/End, loop, 0.25×/0.5×/1× speed, frame and time shown; the viewer's
    `play/pause/seek` (an action at `time` + `mixer.update(0)`, LoopOnce + clampWhenFinished while scrubbing).
  - **37b Edit**: Edit animation opens the editor on a clip: the skeleton drawn on the moving model, a click
    on a joint selects its bone (a list too), the rotate gizmo (local) poses it at the playhead and keys the
    correction there (auto-key; I keys the bone as it is, X / Delete removes the selected keys); the dope
    sheet: a row per bone group (Root, Spine and head, arms, hands, legs; a creature's by its template),
    opening to its bones, a strip where the base clip moves, diamonds for the correction keys (click, shift
    and box select, drag in time), the playhead and trim handles; the inspector: the key's X/Y/Z degrees in
    the bone's own axes, falloff (frames), reset bone, whole clip; Original / Edited to compare; undo/redo
    (Ctrl+Z); Save as a new `anim_edit` version.
  - **37c Clip tools**: trim, speed, loop match (n frames), ground (with Snap to ground: the lowest foot over
    the clip), in place, smoothing per bone group (video mocap jitter).
  - **Later**: foot locking and leg IK (contact detection), dragging hands/feet by IK, a curve view of the
    corrections, motion paths, onion skins, mirror left/right, hand pose presets.
  - **Acceptance**: the TypeScript and numpy evaluations agree on the fixture (1e-5); a correction baked by
    `anim_edit` plays back in the viewer as it previewed; an identity layer leaves the clip's samples
    unchanged; export of an `anim_edit` version for Godot/Unity/Unreal works; Playwright: scrub, key, save
    posts the layer; the owner's cat and a person checked by eye (renders).
  - **As built (2026-10-09, branch `m37-anim-editor`)**: research by three agents in parallel, the Python side by
    an `effigy-worker` in its own worktree (`steps/_anim.py`: GLB animation read, `evaluate`, the accessors
    rewritten, 23 tests and the fixture `tests/fixtures/anim_edit/case.json`; `steps/anim_edit.py`; `--params
    @file` in `_common` and `jobs`, `anim_edit` light), the UI here: `ui/src/anim/` (`clip.ts` the same
    evaluation, `Transport.tsx` the time bar, `DopeSheet.tsx` a canvas, `Editor.tsx` the panel), the viewer's
    `play/pause/seek/useClip/fps` and pose mode (the skeleton on the moving model, a joint picked within 14 px,
    TransformControls rotate in the bone's local axes on the bone itself; the mixer leaves a bone alone while
    it turns), `ui/src/hooks.ts` (useBusy, useHistory shared). Numbers: the TypeScript evaluation equals the
    fixture to 1e-5 (Playwright, Node side); on the owner's cat a key saved through the app (a test backend
    over a copy of the library) baked into `032_anim_edit`, played back at the keyed frame within 1.4e-8 of
    the editor's preview, and re-opened with its key; an identity layer leaves the cat's and the nurse's
    samples within 9e-8 (float32); the nurse's 14 s video clip (340 frames, 52 bones): a key, smoothing and
    the clip tools update at once, Snap to ground lifted it 2.8 cm. The edited GLB imports in Blender 5.2.2
    (52 bones, 4 actions). `tests/fixtures/make_anim_glb.py` writes a skinned 3-bone column with Idle and Walk
    for the UI test (time bar, a key, speed and undo, Save posts the layer). Saving goes through the step
    chain, so the view lands on the new version. Export of the saved cat for Godot, Unity and Unreal runs as
    for any animated version; in the Unity FBX the keyed leg differs from the original's by 39° at the keyed
    frame (Blender's FBX import samples a little off the key's peak), Idle by 0.0°. Not done: zoom in the
    dope sheet; offsets (hips translation keys) have no gizmo yet (the layer and both evaluations carry them).

- **M38 Animation tools** (owner 2026-10-09, on M37's list of what comes later: "your suggestions also sound very
  nice and useful, so go ahead and bring them in"): foot locking with leg IK, dragging hands and feet by IK,
  moving the hips (the translation offsets M37's layer already carries), a curve view of the corrections, motion
  paths and onion skins, zoom in the dope sheet.
  - **Skeleton space** (both evaluations): a bone's world transform is its parents' composed from the root bone
    down, `W = W_parent · rot`, `p = p_parent + W_parent · t` with `t` its translation at the frame (a moving
    one's sample, else its constant `offset`: the base clip carries `offset` for every bone without a moving
    translation, its constant channel's value, else its node's translation, else 0); scale 1; the armature's
    own node transform left out (the same in both). The clip's bones are those it turns or places; a joint
    with neither is left out, its children hanging from its nearest kept ancestor (ours carry every joint).
  - **Foot lock** (§3.2 step 3b), per lock `{bone B, from, to, blend}`, L = B's parent, U = L's parent: the anchor
    `T` = B's world position at frame `a = floor((from + to) / 2)` (the pose after steps 2–3b's earlier locks);
    for every f in `[from − blend, to + blend]` ∩ `[0, F−1]` a weight `w` (1 inside `[from, to]`; before:
    `s(1 − (from − f)/blend)`, after: `s(1 − (f − to)/blend)`, `blend` 0: none outside), the target
    `P = lerp(c, T, w)` with `a, b, c` the world positions of U, L, B at f, `l1 = |b − a|`, `l2 = |c − b|`, `d`
    = `|P − a|` clamped to `[|l1 − l2| + 1e-6, l1 + l2 − 1e-6]` (`l1·l2 < 1e-12` or `|P − a| < 1e-12`: the frame
    left as it is); the knee: `n` = normalise(`(a − b) × (c − b)`) (if its length < 1e-8: `(a − b) × (1, 0, 0)`,
    then `× (0, 1, 0)`, then the frame left), `θ = acos((a − b)·(c − b) / (l1·l2))`,
    `θ' = acos(clamp((l1² + l2² − d²) / (2·l1·l2), −1, 1))`, `K` = the turn about `n` by `θ' − θ`; the hip: `c'`
    = `b + K·(c − b)`, `H` = the shortest turn taking normalise(`c' − a`) to normalise(`P − a`) (opposite: 180°
    about normalise(`u × (1,0,0)`), else `× (0,1,0)`); `W'(U) = H·W(U)`, `W'(L) = H·K·W(L)`, B keeps its world
    turn; back to local: `rot'(U) = W(parent U)⁻¹·W'(U)`, `rot'(L) = W'(U)⁻¹·W'(L)`, `rot'(B) = W'(L)⁻¹·W(B)`,
    each normalised; quaternion turns as `(axis·sin(θ/2), cos(θ/2))`, the shortest turn as
    normalise(`(u × v, 1 + u·v)`). Validation: B, L and U exist and rotate, `0 ≤ from ≤ to ≤ F−1`,
    `0 ≤ blend ≤ F`, at most 64 locks.
  - **UI**: Find foot contacts (per leg the topmost bone within 20 % of the skeleton's height above its lowest in
    the rest pose, a bone's height the mean of its own and its mirror's (Left/Right, the template's sides) so
    both sides pick the same bone, tails left out, whose parent and grandparent turn; in skeleton space before
    the locks: a contact = slower than 15 % of the body's height a second and at most half its height range,
    2 % of the body's height at least, above its lowest, runs of 3 frames or more, a lock each with a blend of
    3, the longest 64 kept; a walk or run in place slides its feet on purpose and finds none; a turn or IK drag
    on a leg a lock holds says the lock undoes it), the locks listed (from–to, blend) and
    drawn as bars on their rows; a bone with a moving translation (the hips) moved with a translate gizmo
    (Rotate/Move/IK drag), its offset keyed like a turn, on a row of its own, X/Y/Z in cm; a hand or foot
    dragged by IK (the same two-bone solve on the moving pose, keyed on the three bones at the frame); Curves:
    the selected bone's correction (X/Y/Z degrees; in Move its offset in cm) over the frames, its keys as
    points dragged up and down; Motion path (the selected bone's track over the clip, before the playhead
    orange, after it green) and Onion skin (ghosts 3 and 6 frames before and after); Ctrl+wheel or the zoom
    slider zooms the dope sheet and the curves, which scroll sideways.
  - **As built (2026-10-09, branch `m38-anim-tools`)**: the Python side (`_anim._lock`, the `offset` field,
    `feet` validation, the fixture with a lock) by a worker in parallel with the UI. On the nurse (a library
    copy, through a test backend): Find foot contacts found 31 still stretches on the 14 s video clip in 84 ms and
    took the worst foot slide over them from 2.78 cm to 1.32 cm (what is left: spans whose anchor is out of the
    straight leg's reach), Idle_A 0.23 cm to 0, Walk and Jog none (in place); a hand pulled 14 cm and a foot
    lifted 6 cm by IK land on their targets (0.000 cm), the hips moved 5 cm; the bake equals the preview (the
    drift per lock to 0.01 cm), the knees' direction turns no more per frame than in the original (13.3°), no
    foot jolt beyond the original's; Blender renders show the locked foot still and the knees natural. The cat:
    both back feet (Back_Leg_Foot) and front (Front_Leg_Ankle) found alike, 4 locks on Idle, none on its walk
    in place.

- **M39 Import a model** (owner 2026-10-09: "import already existing models, since we're already able to EXPORT all
  kind of formats, why not also be able to IMPORT too? should go at the same level as the 'new asset', and once
  imported into library, we can work on it as it were a generated asset"; decisions: keep rig and animations when
  the app knows the skeleton, else a static mesh; no licence/source tracking). Today: New asset's "Import a 3D
  model" takes a GLB only, held in RAM, stored as it is (no units, axes or content check). Checked on the pinned
  Blender 5.2.2: importers `import_scene.gltf`, `import_scene.fbx` (and the new `wm.fbx_import`), `wm.obj_import`
  (Y up), `wm.stl_import` / `wm.ply_import` (Z up), `wm.usd_import` (`apply_unit_conversion_scale`); no COLLADA.
  Our Unity FBX of the dwarf (`120_anim`) comes back through both FBX importers with 52 `mixamorig:` bones at the
  glTF's joint positions (to 1e-4 m) and its 3 clips (`Armature|…|Idle_A`, frames 1–76 for the glTF's 0–75); the
  anim fixture written as a centimetre FBX comes back in metres (armature scale 0.01). Both importers apply the
  file's units; FBX drops the ORM texture (expected: our FBX ships basecolor and normal).
  - **Upload** (`server/app.py`, `server/library.py`): one route, `PUT /api/assets/{id}/upload/{name}`, one file;
    several files (a .gltf with its .bin and textures, an .obj with .mtl and textures, an asset-store download)
    come as one `.zip` (no multipart dependency; Windows zips a folder from its context menu). The body streams
    from `request.stream()` into `library/<asset>/inbox/<uuid>.part`, bytes counted as written: over 1 GB → 413
    `too_large`, the part deleted (Content-Length stays as the early check, never trusted alone); then
    `library.import_file(asset_id, name, path)` (bytes → a path; images, videos and a `.glb` land as before)
    moves it into the new `NNN_upload`. A model file: `source/<name>` (name made safe: `[A-Za-z0-9._ -]`, ≤ 100
    chars), output `model`. A `.zip` (stdlib `zipfile`) is checked before anything is written, else 400
    `bad_zip` naming the member: ≤ 2000 members, a member path ≤ 200 chars after `\` → `/`, no absolute path,
    drive letter, `:` or `..` component, not encrypted, no `.zip` inside, the sum of `file_size` ≤ 2 GB and the
    bytes really written ≤ 2 GB (read in chunks, stopped at the cap: `file_size` can lie); directories,
    `__MACOSX/` and dot-files skipped; extracted into `source/`. **The model**: among members with a model
    extension, by extension `.glb`, `.gltf`, `.fbx`, `.usdz`, `.usdc`, `.usd`, `.usda`, `.obj`, `.ply`, `.stl`,
    then the shallowest path, then the largest; none → 400 `no_model`. Output `model` = `source/<its path>`. The
    zip itself is not kept (its extracted files are). A failure deletes the version folder (nothing half-made is
    listed).
  - **Step `import_model`** (`steps/import_model.py`, `.venv-ml`, in `jobs.LIGHT`; not `import`, a Python
    keyword): in = the upload version (output `model`,
    else a `.glb` `mesh`); a `.gltf` whose `buffers`/`images` URIs (not `data:`) are missing beside it →
    `missing_input` "the .gltf needs scene.bin: upload its folder as a .zip". Params `scale` (number, 0 < s ≤
    1000, default 1), `rig` (bool, default true); anything else → `bad_params`. The step passes the known
    skeletons to the op (`{"templates": {"mixamo52": [[name, parent]…] from `_rig.template()`, "<creature>": …
    from `_templates.bones`}}`, so the op needs nothing from `steps/`), runs `run_blender("import_model",
    [source], out/"_import", params, timeout=900)`, moves its files up, and for a rig runs `rig_qa` on
    `rigged.glb` (human: no symmetry params; a creature: `sides`, `yaw`) → `qa.json` = `{"picked": "import",
    "items": rig_skin.checklist(report), "report", "engines": {"import": report}}`. The source stays in the
    upload: re-importing (another scale, or `rig: false`) is another `import_model` on the same upload.
  - **`blender_ops/import_model.py`** (`_lib.IMPORT` gains `.gltf` as glTF, `.ply`, `.usd/.usda/.usdc/.usdz` with
    cameras and lights off; FBX stays `import_scene.fbx`): import; keep meshes and armatures (cameras, lights,
    empties dropped, their transforms kept on the children); the world scaled by `scale`. **Skeleton**: exactly
    one armature deforming the meshes; its bone names with everything up to the last `:` removed (`mixamorig:Hips`,
    `mixamorig1:Hips`) and UE5 names mapped back through `_maps.UE5` (our Unreal export); known = every bone of a
    template present by name: `mixamo52` (all 52, fingers included: retarget's palm frame reads Thumb1, Middle1,
    Pinky1) or one of our creature templates (`fox`, `horse`, `bird`: our own export keeps their names, M36), and
    each such bone's nearest present ancestor is its template parent. Otherwise, or `rig: false`, or two
    armatures: the static path, the reason in `metrics.skeleton_note` ("no skeleton", "2 skeletons", "missing:
    LeftHandThumb1, … (12)", "LeftArm hangs from Spine2, not LeftShoulder", "rig: false"). **Extra bones** (end
    bones, twist bones, a root above Hips, UE5's spine_04/05): their vertex weights added to the nearest kept
    ancestor (none: the template's root), then deleted (`metrics.merged_bones`). Blender trap: applying scale to
    an armature does not scale its location keys, so clips are **recorded, then re-keyed**: per clip (an action
    with pose-bone channels), per frame g at 24 fps (`G − 1 = round(seconds · 24)`, source frame
    `f0 + g · fps_src / 24`, subframes allowed), every kept bone's world location and normalised world rotation,
    `scale` and the re-centring folded in; then the rest pose, extra bones deleted, transforms applied (identity
    object transforms, rest bones in metres), and each clip re-keyed from the recorded world matrices, parents
    first (`Bone.convert_local_to_pose`): `rotation_quaternion` on every kept bone every frame, `location` on the
    top bone (Hips; a creature's root) every frame and on any other bone whose location moves > 1e-5 m, no scale
    keys; one NLA track per clip, exported like `retarget` (`NLA_TRACKS`). **Clip names**: the action name after
    its last `|`; `mixamo.com`, `Take 001`, `Scene` or empty → the source file's stem; characters outside
    `[A-Za-z0-9_-]` → `_`, ≤ 48 chars, repeats get `_2`, `_3`. **Re-centring**: the rest-pose meshes' bbox base
    centre (x, z centre, lowest y) to the origin (§3.4). **Writes**: static: the meshes (armature modifiers
    removed: the bind pose) joined, `mesh.glb`; a rig: `rigged.glb` (rest pose, no animation), `rigged_anim.glb`
    if any clip, `skeleton.json` `{"template", "frame": "gltf_yup_m", "joints": [{name, parent: the template's,
    position: the bone head in the library frame}]}`, a creature also `tail` per joint, `to` from the template,
    `sides` from `_templates.TEMPLATES` and `yaw` (set by the step: the 2-D Procrustes turn about +Y of the
    template's centred joint heads onto the imported ones, degrees in [0, 360), 0.1°); `clips.json`
    `{"clips": [{"name", "frames": G − 1, "seconds"}], "fps": 24, "missing": [], "height_scale": 1}` (as anim's).
    Materials and textures go through the glTF exporter (images embedded); an image Blender could not load is
    listed in `metrics.missing_textures` (the import still succeeds). An image's alpha wired to the material's is unwired (the
    material opaque) when every pixel is opaque, and for an FBX whose material links no texture as
    `TransparentColor`/`TransparencyFactor`: Blender's FBX importer wires any 32-bit colour image's alpha, and a
    BLEND material shows the inside of a head through its face where our bakes leave transparent texels.
  - **Metrics** (`import_model`): `format` (the extension), `height_m`, `triangles`, `meshes` (before joining),
    `materials`, `textures`, `missing_textures` ([names]), `skeleton` (template id or null), `skeleton_note`
    (why not; "" when known), `merged_bones`, `clips` ([names]), `dropped_meshes` (a rig's meshes its skeleton
    does not deform: left out, named), `turned` (a person who faced away), `template` and `engine: "import"` (a rig only, as
    rig_skin's), `scale_hint` only when `height_m` is outside validate's [0.05, 50]: of 0.001, 0.01, 0.0254, 100,
    1000 the factor that brings the height nearest 1 m in log scale.
  - **Downstream**: an `import_model` with `mesh.glb` is any imported mesh (Clean onward; no Bake: no `ovoxel.npz`); with
    `rigged.glb` + `skeleton.json` a skinned model (`anim` re-runs our clips on it: it reads those two files);
    with `rigged_anim.glb` an animated one: export takes the first `.glb` output (`rigged_anim`), the template
    gives humanoid or creature bone names; `steps/anim_edit._base_of` takes an `import_model` holding
    `rigged_anim.glb` as a base (one condition), so Edit animation works at once. Re-rig in the app: import again
    with `rig: false`, then Rig.
  - **UI** (`ui/src/main.tsx`, `ui/src/workflow.ts`, `ui/src/api.ts`): nav "Import" after "New asset"
    (`#/import`); New asset loses its "Import a 3D model" mode. The Import page: name (optional), one file by
    picker or drop (`accept` the extensions above), its size shown, "Keep it as a still model (ignore its
    skeleton)" → `rig: false`; Import = create the asset, upload, chain `[["import_model", {rig}]]`, open the new
    version. `workflow.ts` `as(m)`: an `import_model` acts as `anim` with `rigged_anim`, as `rig_skin` with `rigged`,
    else as an imported mesh (Clean); `stageOf`, `phaseOf`, `isImported` and the rigged checks in the Export,
    Rig and Animate panels go through it; labels "Imported model" / "Imported rig" / "Imported animation", an
    upload holding `model` and no `.glb` "Original file" in Reference. Under an `import_model` version: what came in
    (format, height, triangles, materials, textures and the missing ones, the skeleton or why not, the clips);
    with `scale_hint` or a height outside 0.05–50 m: "This looks N× too big/small" and buttons Centimetres
    (0.01), Millimetres (0.001), Inches (0.0254), ×100, ×1000, the hint first, each chaining `import_model` with that
    `scale` (times the current one) on the same upload; a rig: "Import again as a still model".
  - **Tests**: `tests/test_import.py`: zips built with `zipfile` at test time: traversal (`../a.obj`, `/a.obj`,
    `C:/a.obj`), a nested zip, an encrypted flag, too many members and too many bytes (the caps monkeypatched
    small), no model, not a zip → the 400 codes and no version folder left; the model chosen (`.glb` over `.obj`,
    the shallowest of two); an upload over the cap → 413, no `.part` left. With Blender (skipped without, as
    `test_blender_ops`): `tests/fixtures/anim.glb` (3 bones) → static, `skeleton_note` "missing: …", its mesh
    kept; `tests/fixtures/make_rig52.py` (Blender at test time: a box body on the 52-joint template, weighted,
    one 12-frame clip) saved as a centimetre FBX with `mixamorig:` names and leaf bones → `skeleton:
    "mixamo52"`, `merged_bones` = the leaves, joints within 1 mm of the template's positions, the bones'
    rotations within 1e-3 rad of the source at every frame, `anim_edit` with an identity layer on it succeeds; a
    `.gltf` without its `.bin` → `missing_input`; `scale: 0` → `bad_params`. Playwright (`ui/tests/import.spec.ts`,
    mocked routes as `smoke.spec.ts`): nav Import, a file chosen, the PUT then `steps/import_model` posted with the
    upload as parent, the new version opened; a mocked animated `import_model` shows Edit animation; a mocked static
    one with `scale_hint` 0.01 posts `import_model` `{scale: 0.01}` on the upload.
  - **Acceptance** (the owner's inputs, outside git, the session's `imp39/`): `nurse_unity.zip` → an animated
    `mixamo52` import, 4 clips, the 341-frame one 340 frames (± 1), joints within 1 mm of the nurse's
    `skeleton.json`, opens in Edit animation, a key saves an `anim_edit`; `nurse_godot.glb` → the same, joints
    within 1e-4 m; `mixamo_nurse_cm.fbx` → the nurse's height within 1 %, leaf bones merged, clips kept;
    `nurse_web.usdz` → a textured mesh at the nurse's height (rigged if Blender's USD skeleton import keeps our
    bone names: check at build); `cat_obj.zip` (model in a subfolder) and `cat_gltf_sep.zip` → static with their
    textures in `mesh.glb`; `cat.stl`, `cat.ply` → static, standing (Z up), no material; `cat_static_notex.fbx`
    → static, `missing_textures` listed; `cat_rigged_unity.zip` → `skeleton: "fox"`, `yaw` within 1° of the
    cat's `skeleton.json`, `anim` with the fox's Walk runs on it (rendered); round trip: the imported nurse
    exported for Unity and imported again → the same joints (1 mm) and clips; refusals: a renamed text file as
    `.fbx` (the job fails with Blender's message), a zip with `../x.obj` (400 `bad_zip`), a zip without a model
    (400 `no_model`), a 1.1 GB body (413); Playwright passes; `uv run pytest`, ruff, `npm run build`.
  - **As built (2026-10-09, branch `m39-import`)**: an expert wrote this spec; two workers built the upload and
    the step in parallel, the UI was built beside them. The review found: shape keys kept the old size (a shaped
    mesh evaluates from its key blocks: `Mesh.transform(…, shape_keys=True)`); clips are recorded in the file's
    own space with the world change applied to the matrices (an FBX keys the armature object in every take,
    which undid a world change set on the object); a person facing away (the left leg on −X here, −Y in
    Blender) is turned round (`turned`); the hands' `twist` is written as rig_fit writes it
    (`_rig.palm_normals`); an upload's `model` is not a mesh to work on (`hasGlb` skips it); Mixamo's
    `…|Layer0` clip names take the file's name. Blender's FBX importer wires the alpha of any 32-bit colour
    image (the nurse's face drew the inside of her head): unwired unless the file links a transparency
    texture. Tests: `tests/test_upload_import.py`, `tests/test_import_model.py` (shape keys, facing away, FBX
    alpha, the 52-joint rig in cm), the UI flow in `ui/tests/smoke.spec.ts`. On the owner's files through the
    app (a test backend, a fresh library): the nurse's Unity zip, Godot GLB and a Mixamo-style cm FBX come in
    animated (4 clips; every joint over every frame within 1.5 mm of the original, the GLB exact), its USDZ
    rigged, the cat's FBX on the fox template (4.7 mm), OBJ/glTF zips, STL, PLY and an FBX without its
    textures static at 0.99 m; an imported clip edits and saves; a traversal zip and a zip without a model are
    refused with their messages; 1.5–6 s an import.
  - **Non-goals** (ponytail): a skeleton missing fingers or another humanoid naming (Rigify, Daz, UE5 Manny's
    full rig beyond our names) → static, "rig it in the app"; add a joint map or finger fill when the owner
    brings such files. No up-axis or front override (a Y-up STL lies down: fix it in Blender; add a turn param
    when it happens). No multi-file picker (zip it). Shape keys ride along in the GLB, nothing edits them.

- **M40 Agent access over MCP; Ask Claude removed** (owner 2026-10-09: "make the app available to claude or other
  agents via mcp. this also means, we then can drop the 'ask claude' section in the ui completely"; research 13).
  The stdio bridge stays (every client launches it, Claude Desktop reaches local servers only this way); it grows
  from a thin pass-through into a tool set built around the workflow.
  - **Tools** (`mcp/effigy_server.py`; ids are the library's; results compact JSON as text, `structuredContent` the
    same object):
    - `list_assets(query="", limit=30, offset=0)` → `{"assets": [{id, name, created, versions, newest: {version,
      label}}], "total"}`, newest first, `query` matching name or id.
    - `get_asset(asset_id)` → `{id, name, versions: [{version, step, label, parent, created, outputs: [names],
      metrics: scalars only}]}`; the UI's labels (`workflow.ts` `label`), mirrored in Python as a small table.
    - `get_version(asset_id, version)` (M9) → its `meta.json`.
    - `describe_steps(step="")` → without a step: the guide's summary and every runnable step with its first
      docstring line, its input and its presets; with a step: its whole module docstring (the params) and presets.
      Read from `steps/<name>.py` with `ast.get_docstring` (nothing imported).
    - `look(asset_id, version, clip="", size=768)` → one image: a mesh version a 2×2 sheet (front, right, back,
      three-quarter; textured where it has textures), a picture version its pictures in a grid (concepts' four),
      with `clip` four frames of that clip spread over it (front three-quarter); size 256–1024. Rendered by the app
      (`GET /api/assets/{id}/versions/{v}/look?clip=&size=`, Blender Workbench, `blender_ops/render_look.py`,
      composed with Pillow as `thumbs._fit` does) and cached in the version folder (`_look.png`,
      `_look_<clip>.png`, like `_thumb.png`).
    - `read_file(asset_id, version, name)` → a JSON or text output of that version (`report.json`,
      `skeleton.json`, `clips.json`, `qa.json`, `checklist.json`, `edit.json`, `metrics`…), at most 200 KB;
      anything else: "use look, or export".
    - `job_status(job_id, wait_s=0)` → `{job_id, status, progress, text, version?, error?}`, waiting as above.
    - `fit_check(asset_id, version)` → the M36 fit-check numbers (`POST …/fit-check`).
    - `create_asset(name)` → `{asset_id}`: a description starts with `run_step(asset, null, "brief", …)`.
    - `import_file(path, name="", still=false, wait_s=40)` → an absolute local path of a model, a `.zip`, a
      picture or a video (the M39 extensions; ≤ 1 GB; a folder or a missing file refused) streamed through
      the upload route into a new asset; a model then runs `import_model` (`rig: !still`), a picture `prep`;
      returns `{asset_id, upload: version, status, …}` as `run_step`.
    - `run_step(asset_id, parent, step, params={}, wait_s=40)` (M9, now bounded) → as above.
    - `cancel_job(job_id)` → the job's status after cancelling.
    - `validate(asset_id, version)` (M9) → the checklist.
    - `export(asset_id, version, profile, options={}, to_folder="")` (M9 + `options`: `lods`, `collision`;
      `to_folder`: the files also copied there, into a new `<asset>_<profile>` subfolder; an existing one is
      refused, never overwritten) → `{version, files: [absolute paths]}`.
    - `open_in_blender`, `pull_from_blender` (M9) unchanged.
    No deletes: the library stays append-only for agents; the owner deletes in the app.
  - **Jobs** (`server/jobs.py`): `submit(…, dedupe=True)` from the MCP route returns the queued or running job with
    the same asset, step, parent and params (the app's own UI keeps submitting as before). The bridge polls
    `/api/jobs/{id}` each second, `ctx.report_progress(p, 1, text)`, checks for cancellation
    (`anyio.from_thread.check_cancelled()`) and on it cancels the job it started.
  - **Instructions and guide**: server instructions ≤ 512 characters (what the app is, versions and parents, the
    usual order, jobs and `job_status`, `look`, `describe_steps` for the rest); `mcp/SKILL.md` the full guide
    (pipelines from a description, a picture and a file; each step's purpose and typical params; anim_edit
    layers; checking with `look`, `validate`, `fit_check`; exporting into a game project; one GPU job at a time;
    what not to do), served as the resource `effigy://guide` and summarised by `describe_steps()`.
  - **Setup "Connect an agent"** (replaces the Claude account part): `GET /api/agent/setup` → the install's
    absolute paths and, per client, the text to paste: Claude Code (`claude mcp add -s user effigy -- uv run
    --frozen --project <root> python <root>\mcp\effigy_server.py`), Claude Desktop (`claude_desktop_config.json`
    entry, with uv's absolute path: a GUI app does not inherit PATH), Codex (`config.toml` table with
    `tool_timeout_sec = 120`), Cursor and VS Code (`mcp.json`), Gemini (`settings.json`); a Copy button each;
    "Test" (`POST /api/agent/test`): the bridge started over stdio by the SDK's client, its tools listed (count
    and names) or the error. The repo's `.mcp.json` uses relative paths (no `EFFIGY_ROOT`).
  - **Ask Claude removed**: `server/claude_runner.py`, the routes `/api/claude/presets`,
    `/api/assets/{id}/claude/run`, `/api/jobs/{id}/accept`, `/api/jobs/{id}/revert`, the 409 for a run awaiting
    review, `blender_client.revert()`, `prompts/presets.yaml`, `prompts/blender_agent.md`; UI: the panel in the
    Blender section, Keep/Revert and cost on job rows, the `claude_event` event, `api.ts`'s four calls, the
    "Needed for … Ask Claude" text; tests `test_claude_runner.py`, `fixtures/claude_stream.jsonl`, the Claude
    parts of `test_api.py` and `smoke.spec.ts` (the asset A/B race test kept without them); docs (PLAN D12,
    ARCHITECTURE §6, WORKFLOW, HANDOVER's `EFFIGY_ROOT`). The "Claude edit" label stays for versions already on disk.
  - **The API closed to web pages** (`server/app.py`, a small middleware): a request whose Host is not
    `127.0.0.1`, `localhost` or `[::1]` (any port) → 400; one carrying an `Origin` that is not
    `http://127.0.0.1:*` or `http://localhost:*` → 403. Requests without an Origin (the bridge, curl, tests) pass.
  - **Tests**: `tests/test_mcp_server.py` grows: the tool list with annotations; a live uvicorn with the `_echo` step
    taught a `sleep` param: `run_step` with `wait_s=1` returns `running` + `job_id`, `job_status` finishes it, a
    second identical `run_step` returns the same job, `cancel_job` cancels; `import_file` of a temp OBJ zip runs
    `import_model` (Blender: skipped without); `look` returns image content (Blender: skipped without);
    `read_file` refuses a path outside the version and a big file; `export` with `to_folder` copies and refuses an
    existing folder; `describe_steps` covers every step. `tests/test_api.py`: the Host and Origin checks; the
    agent setup text holds the install path. Playwright: Setup shows "Connect an agent" with its commands; no
    Ask Claude anywhere.
  - **Acceptance**: an agent session driven by the SDK client against the real app on a library copy: import
    `cat_obj.zip`, `look`, `run_step` clean (a running result, then `job_status`), `validate`, `export` godot
    to a folder; the same with the nurse's `nurse_godot.glb` through `look(clip=…)` and an `anim_edit` layer;
    Claude Code connected with the Setup command lists the tools; full pytest and Playwright; ruff.
  - **As built (2026-10-09, branch `m40-agent-access`)**: research by three agents (research 13); two workers in
    parallel (the bridge: `mcp/effigy_server.py`, `mcp/SKILL.md`, `jobs.submit(dedupe)`; the app: `server/look.py`,
    `blender_ops/render_look.py`, `server/agent.py` with the setup texts, the test and the `LocalOnly`
    middleware), the removal and the UI beside them. `look` answers with the cached file's path in `X-Effigy-File`.
    Previews: 2.9–3.0 s a render (Blender Workbench, the nurse and the cat, plain or 4 frames of a clip), 0.5 s a
    picture grid, 0.24 s from the cache. Acceptance through the bridge started as Setup prints it (uv, absolute
    paths) against the app on a fresh library: 16 tools, instructions 501 characters; `import_file` of
    `cat_obj.zip` (2 s, an imported model), `look`, `run_step` clean with `wait_s` 1 (running, the same request
    the same job, `job_status` done), `validate`, `export` godot into a folder (copied; again: refused);
    `nurse_godot.glb` imported animated, `read_file` clips.json, `look` with `clip` Jog, an `anim_edit` layer
    (speed 0.5, ground 0.02) and `look` again; `cancel_job` on a remesh. Setup's Test lists the tools in ~2 s.
  - **Non-goals** (ponytail): a `/mcp` HTTP endpoint inside the app (later, only if clients must share live
    state; with Host/Origin checks and a token), the tasks extension (no client), MCP Apps (an embedded viewer),
    `.mcpb` bundles, deletes over MCP, foot-contact finding in Python (the editor's).
- **M42 Library rework, the rest** (owner 2026-10-09: "Go for ... the rest of the library rework"; HANDOVER §11's
  sketch and PLAN §5 "Library: search, tags, thumbnails, licence badges, batch export"; M28 built search, sort,
  filter, archive, stars, labels and the folded History; thumbnails exist since 2026-10-05). Five pieces, each
  small, all on what is already there: turntables on the grid, comparing two versions, licence badges, tags,
  batch export. Contract changes (§3.2 `tags`; §3.5 the listing's fields, PATCH `tags`, the two turntable routes,
  `POST /api/export/copy`) ride in this milestone as M28's did.
  - **Turntables** (decided: an animated WebP rendered once by Blender, cached, loaded on hover; not a CSS
    sprite strip, which needs frame bookkeeping in CSS for the same pixels, and not live viewers, one WebGL
    context and one GLB download per card). Measured on the dwarf (`a_dwarf_blacksmith_with_hammer_2/480_bake`,
    pinned Blender 5.2.2, `render_look.py` with the change below): 24 Workbench frames of 256² in 2.9 s wall (one
    Blender start; M40's 4-frame look also took 2.9 s), Pillow writes the animated WebP in 0.45 s, 64 KB.
    - `blender_ops/render_look.py` gains `params.turn` (frames, default 0 = as now): N cameras at azimuth
      `2π·i/N` around the model (`(sin a, −cos a, 0.35)`, starting at the front), the current frame, the sheet's
      framing (its ortho scale already covers any turn about the vertical); writes `look_0 … look_<N−1>.png`,
      `done` lists them all (today it lists 4).
    - `server/look.py` `turntable(asset_id, version) -> Path`: `_turn.webp` in the version folder (fresh when
      newer than the GLB, as `look`); a `validate` or `export` version uses the nearest ancestor that is neither
      (as `thumbs.version_thumb` does); the first `.glb` output (an upload's `model` is not one), else
      `ApiError(400, "nothing_to_show")`; `run_blender("render_look", [glb], tmp, {"size": 256, "turn": 24})`
      under look's `_LOCK`; frames → `Image.save(dst, save_all=True, append_images=…, duration=67, loop=0,
      quality=80)` (Pillow 12 has WebP; a temp name then `replace`).
    - `server/thumbs.py`: the asset's choice moves into `asset_version(asset_id) -> str | None` (the first of
      textured models, models, newest: what `asset_thumb` already loops over); `asset_thumb` keeps its behaviour.
      The asset route renders the turntable of that version when it has a model, else 404.
    - When: lazily, the first hover (≈ 3 s, then from the cache). The card keeps its still thumbnail until the
      WebP has loaded, then swaps `src`; leaving the card swaps back. Hover intent 400 ms (a sweep across the grid
      starts nothing); keyboard focus does the same; `prefers-reduced-motion: reduce` never loads one. The asset
      URL carries `?v=<updated>` so a new version is not hidden by the browser's cache.
    - Not in History: its thumbnails are 36 px and the viewer beside it already shows the model live.
  - **Compare two versions** (History → "Compare" on a row → the viewer area splits into two synced viewers).
    - History rows gain a ⇆ button (`aria-label` "Compare <label> <NNN> with the shown version", absent on the
      shown version's row). It closes the drawer and puts the workspace into compare mode: the `ViewerPane` is
      replaced by `Compare` (left: the shown version, right: the picked one; the side panel stays as it is);
      "Swap" and "Close" (also Escape) in its header; picking another version in History ends compare mode.
    - `Compare` (`ui/src/Compare.tsx`, new): two equal halves, each the version's label · NNN, then its model (a
      canvas with its own `Viewer`, the GLB chosen as the workspace does: `source()`, `mainFile`) or its picture
      (an `<img>` of its main picture). Under both, one table, left and right
      columns mirrored: Triangles, Vertices, Meshes, Materials, Height (m), Bones, Clips (from the viewers'
      `Stats`), Checks (the newest `validate` whose parent is that version: "passed" or "N failed"; "not checked"),
      Licences (count; "restricted"). A row whose values differ is marked (bold). No inspect modes, weights or
      clips in compare (open the version for those).
    - `ui/src/viewer/Viewer.ts`: `Stats` gains `materials` (distinct materials) and `height` (the model's box
      height, metres) at load; `link(other: Viewer): () => void`: on this viewer's controls `change`, copy its
      camera position and controls target into `other` (and the reverse from `other`), a guard flag so the copy
      does not echo; returns the unlink. After both models have loaded the right one takes the left one's view.
      The second viewer does not set `window.__viewer`; compare sets `window.__compare = [left, right]` for tests.
  - **Licence badges** (derived only; nothing new to type in. M39 decided imports carry no licence or source
    tracking: "Your file" says exactly that).
    - The listing (`library.list_assets`, which already reads every `meta.json`) adds `licenses` (union),
      `restricted` (any), `imported` (any `import_model`, or an `upload` with a `mesh` output). Conservative on
      purpose: a card stands for the whole asset, so one restricted branch marks it.
    - Card: one row of chips under the name, always the same height (empty when there is nothing to say, so the
      cards stay even): "Restricted" (red), "Your file" (neutral; title "Imported: Effigy does not know this
      file's licence"), "Credit" (amber; title: the attribution lines of its `licenses` rows from
      `GET /api/licenses`, fetched once by the Library). Commercial-clean with no credit line: no chip (that is
      every default component, PLAN §6).
    - Workspace: a "Licences" button after the version's label in the title bar opens a small panel for the shown
      version: each `meta.licenses` row (name, licence, its attribution line), "Your file …" when `isImported`,
      the restricted sentence when `restricted`: what `LICENSES.json`/`CREDITS.txt` would hold, before exporting.
      `api.ts` `Meta` gains `licenses: string[]` and `models?: {id: string; revision?: string}[]`.
  - **Tags** (`asset.json` `tags`, owner-defined words on an asset; versions keep stars and labels).
    - `library.update_asset(…, tags=None)` validates as §3.5 says and stores the cleaned list; `AssetPatch` gains
      `tags: list[str] | None`, the "nothing to change" check counts it.
    - Grid: a "Tags" button beside Rename (a prompt "Tags, separated by commas", pre-filled: as Rename and the
      version label do), the asset's tags as muted chips on the card (one line, cut with an ellipsis); the toolbar
      gains a "Tag" select ("Any tag", then every tag in the shown assets, A–Z, with its count) next to Show;
      search also matches tags.
  - **Batch export** (decided: a selection mode in the grid, the UI posting one `export` per asset through the
    existing route, then one server route copying into a folder; no server batch route: jobs already queue on the
    GPU lock one at a time, and a copy needs no job).
    - Toolbar "Select" toggles selection mode: each card shows a checkbox over its thumbnail and a click toggles it
      instead of opening the asset; the toolbar shows "N selected", "Select all shown", "Clear", "Export…",
      "Done".
    - "Export…" opens a dialog: the destination (`EXPORTS`, as `ExportPanel`), LODs, "Collision hull (not for
      rigged characters)"; a row per selected asset: thumbnail, name, the version to export (a select of its
      versions with a GLB, newest first, ★ marked), a status cell; "Also copy into folder" (an absolute path,
      remembered in `localStorage`); "Export N".
    - The version picked by default: `workflow.ts` `exportPick(versions): Meta | null`: of the versions with a
      GLB whose `phaseOf ≥ 2` and that are not `validate`/`export` (a raw generated mesh is not a game asset), the
      newest starred one, else the newest one; none → the row says "Not ready: open it" and is left out.
    - Run, one asset after the other (`GET /api/assets/{id}` for its versions): an `export` version with the same
      parent and params is reused ("already exported", as the MCP `export` does); else `api.run(id, "export",
      version, params)`, `track`, `waitJob`; a restricted version is skipped ("Restricted: export it from its own
      page", where the MX1 §5(c) confirmation is: never confirmed in bulk); a failure is shown on its row and the
      batch goes on. "Stop" ends the loop after the running job (and cancels it). Params per asset: `{profile,
      lods?, collision?}` as `ExportPanel` builds them; collision only when ticked and the version is not rigged.
    - Then, with a folder: `POST /api/export/copy` with the export versions that finished; each row shows the folder
      it went to or why not (`exists`, `failed: …`). `library.export_copy(folder, items)` (≈ 25 lines): `folder`
      absolute and an existing directory, else `bad_path`; each item `version_dir` (404) whose meta's step is
      `export` (else `bad_params`); target `<folder>/<asset_id>_<metrics.profile>` (characters outside
      `[A-Za-z0-9_-]` → `_`, as the MCP tool); `mkdir()` (exists → skipped), every output copied (`shutil.copy2`,
      paths below the profile folder kept), an `OSError` removes the target and reports `failed: …`.
  - **Files**: `server/library.py` (tags, listing fields, `export_copy`), `server/app.py` (`AssetPatch.tags`, the
    turntable and copy routes), `server/look.py` (`turntable`), `server/thumbs.py` (`asset_version`),
    `blender_ops/render_look.py` (`turn`); `ui/src/main.tsx` (grid: tags, chips, hover, selection, the batch
    dialog; History's ⇆; the Licences panel), `ui/src/Compare.tsx` (new), `ui/src/viewer/Viewer.ts` (`link`,
    `Stats`), `ui/src/workflow.ts` (`exportPick`), `ui/src/api.ts` (types, `patchTags`, `exportCopy`),
    `ui/src/styles.css`; tests below; docs (this section's "As built", HANDOVER §11 "Still open" emptied).
  - **Work split** (two workers in parallel; the interfaces are §3.5's rows above, nothing else is shared):
    backend = everything under `server/` and `blender_ops/` plus `tests/`; UI = everything under `ui/` against
    mocked routes in Playwright (shapes exactly as §3.5). Joined for the acceptance.
  - **Tests**: `tests/test_library.py`: tags cleaned (trim, repeats ignoring case dropped) and refused (empty,
    comma, 33 characters, 21 tags), kept across a rename; the listing's `licenses` (union), `restricted`,
    `imported`; `export_copy` copies an export's files under `<asset>_<profile>/`, skips an existing folder
    untouched, refuses a relative or missing folder and a non-export version. `tests/test_api.py`: PATCH `tags`;
    `POST /api/export/copy` codes; the turntable of a picture version 400, of an asset without a model 404; with
    Blender (skipped without, as `test_blender_ops`): a fixture GLB's turntable is a WebP with `n_frames == 24`
    of 256², the second request served from the cache (same mtime). Playwright (`ui/tests/smoke.spec.ts`, mocked
    routes): the grid's chips (restricted, your file, credit with its line in the title), the Tag filter and the
    Tags PATCH; hovering a card past 400 ms requests `/turntable?v=` and swaps the image, leaving swaps back;
    selection mode → Export… for four mocked assets (a starred ready one, a ready one with that export already
    made, a restricted one, a raw one): one export posted (the starred version), one reused, two skipped, then
    `POST /api/export/copy` with both export versions and the typed folder, the result rows shown; History ⇆ →
    two canvases, both viewers loaded, an orbit on the left moves the right camera (positions equal), the table
    shows both triangle counts, Escape returns to the single viewer; the Licences panel lists the version's rows.
  - **Acceptance** (a copy of the owner's library, 106 assets, outside git; the app on it with
    `EFFIGY_LIBRARY`): hover five cards (bench, golden, an import, an animated one, a picture-only asset): the
    first four turn, first render time and cache time recorded, the picture-only one stays still; compare the
    dwarf's `100_bake` with `480_bake` (synced, the table's numbers against `meta.metrics`/the viewer's stats);
    tag three assets, filter by a tag, reload (kept); batch-export five assets for Godot into a new folder: five
    `<asset>_godot` folders with a GLB, `CREDITS.txt` ("Built with DINOv3" for a generated one) and
    `LICENSES.json`; again into the same folder: all five skipped `exists`, nothing overwritten; a bench asset
    shows "Credit", an imported one "Your file"; full pytest, ruff, `npm run build`, Playwright.
  - **Check at build**: Workbench `TEXTURE` shading on a dark-textured model looks dark (the dwarf's frames at
    256²): compare with `_thumb.png` and brighten (world colour, `STUDIO` light) only if the owner finds it too
    dark. Blender Workbench in `-b` uses the GPU: measure its VRAM while rendering; if a turntable during a 14.5 GB
    generate risks an OOM, answer 409 `busy` while a `SERVE` step runs (the card stays still), as a `ponytail:`
    note until measured (M40's `look` shares this).
  - **Non-goals** (ponytail): a zip download or the browser's folder picker (`showDirectoryPicker`, Chromium only)
    instead of the folder field; a server-side batch route (add when a batch must outlive the browser tab: today
    the jobs already submitted finish and a re-run reuses them); comparing across assets; turntables of clips or
    in History; pre-rendering turntables after each job (add if the 3 s first hover annoys); tags on versions;
    batch tag, archive or delete; licence fields for imported files (M39's decision); scrubbing the turntable with
    the mouse.
  - **As built (2026-10-09)**: a turntable never waits: the cached `_turn.webp` is served at once, otherwise 503
    `busy` while the GPU lock or look's lock is held (the card keeps its still; the next hover retries), so a hover
    sweep can't queue renders or meet a heavy job; a turntable that loads after the pointer left is dropped.
    `export_copy` refuses a folder inside the library, copies through its own `mkdtemp` folder, accepts an empty
    list (the dialog's folder check before any export runs) and skips `no_profile`; the batch reuses an export only
    when its params are exactly `{profile}`, and judges `restricted` on the picked version. Cards in a row are one
    height (the buttons line up under long names). On a copy of six of the owner's assets: first hovers 2.9–3.6 s,
    cached 0.5 s (the dense cat clean 36 s, measured by the backend worker); tags + filter kept; a Godot batch of
    five: four copied in 11 s, the lantern (concepts only) skipped "nothing prepared", the second run all
    skipped "exists"; the dwarf's `480_bake` against `100_bake` with linked cameras. pytest 285, Playwright 33.

- **M44 Duplicate clips** (owner 2026-10-09: "a way to copy / duplicate an existing animation so that copy can be
  modified to something else"). A layer gains `from: <base clip>`: a layer whose clip is not in the base and has
  `from` is a **copy**, sampled from that base clip, evaluated with its layer and written as a new animation
  (`steps/anim_edit.run` appends a deep copy of the source animation under the new name, then `write_glb` re-points
  it as for any edit); `clips.json` gets its entry (the source's, with the copy's frames and seconds). `bad_params`:
  name 1-64 characters, trimmed, not a base clip; `from` a base clip (never a copy: the UI resolves a copy of a copy
  to the root and copies the layer); a layer without `from` names a base clip; at most 64 copies. A copy absent from
  the next save is gone (edits restart from the base): that is the delete. UI (`Editor.tsx`): Duplicate (prompt,
  "<clip> copy", "<clip> copy 2"), and for a copy Rename and Delete; the history is the layers state, so undo covers
  all three; Save sends every edited clip and every copy; `Viewer.dropClip` removes the copies from the viewer on
  close. Export takes every animation of `rigged_anim.glb`, so the copy rides along. `clip.ts` maths unchanged
  (`Layer.from` only).
  - **As built** (2026-10-09): `tests/test_anim_edit.py` (copy written, frames/seconds, source unchanged, a re-edit
    drops the copy, 9 invalid cases), Playwright (duplicate, rename, edit, save posts `from`; delete, undo). Real
    check on a copy of `bench_faithful_nurse` (315_anim): "Walk limp" = Walk with speed 0.6, a hips lift and a left
    arm key, `clips.json` 67 frames; the godot GLB export holds all five animations; four frames each rendered in
    Blender: the copy differs as edited, Walk is unchanged.

- **Audit fixes, the editors (2026-10-11, UI-001 + UI-002, `ui/src/main.tsx` RigPanel, `ui/src/anim/Editor.tsx`)**:
  a save in the skeleton, marker and animation editors closed the editor and dropped its history whatever
  the step's answer (the rig panel fired the POST and closed; the animation editor closed after any result of
  `onSave`, which resolves null on a refusal). The three saves go through the chain and close only on a
  version: a refused or failed step leaves the editor open with its edits, undo history and the error shown.
  The animation editor's Save enables on any change since the version was opened (not only while something is
  still edited): with every edit undone or the last copy deleted it sends one base clip's empty layer, which
  `anim_edit` turns into the base as it was (its contract wants a non-empty layer and starts from the base), so
  a saved copy or correction can be taken back. Browser tests: a 400 on `rig_fit` and on `anim_edit` keeps each
  editor open with its edit, the next save goes through and closes it; duplicate → delete → save posts
  `{"clips": {"Idle": {}}}`.
- **Audit fixes, the backend's Mediums (2026-10-11, BE-002 + BE-004 + BE-005)**: `library.new_version` scans
  and makes the folder under one lock (a job and an upload allocating at once took the same number with two
  suffixes; `mkdir` only caught the same name); `update_asset`, `update_version`, `write_meta`'s brief copy and
  `settings.write` read, change and write under a lock (two patches at once kept only the later one's fields),
  and every writer's temp file carries its thread id; `_run_oneshot` fails a step that exits non-zero after its
  `RESULT` (the exit code was read only without a reply). Tests: two threads allocating with a slowed scan get
  24 distinct numbers; two patches with a slowed read both land, no temp file left; two settings writes both
  land; a fake step printing RESULT then exiting 1 is an ERROR, exiting 0 a RESULT, an ERROR keeps its code.
- **Audit fixes, the frontend's Mediums and the Low (2026-10-11, UI-003 .. UI-008)**: after a dropped event
  stream the App's `reconcile` re-reads the asset and, when the shown version was deleted meanwhile, moves to
  its nearest surviving ancestor, else the newest (it stayed selected and the page read as empty); a tracked
  job the server no longer knows (a restart: 404 `not_found`) becomes `failed` with a message in the banner and
  the Jobs list, so its Cancel goes and the asset is not "busy" any more (any other poll failure still only
  says the session ended). Compare's Checks row reads each side's own or latest `checklist.json` and counts it
  as the Checklist panel does (it read `metrics.fail` alone: warnings and a missing count said "passed"). The
  clip picker's `<select multiple>` takes the native change event (keyboard, assistive technology) into the
  same chosen set as the option clicks (it ignored it). The viewer's `clear` stops playback and posing and
  drops the rig and gizmo (the render loop went on every frame and a cleared skeleton view kept its gizmo);
  `dispose` also disposes the skeleton gizmo and the key listener. Playwright: a fake `EventSource` drops
  and reopens the stream; the Compare table with a mixed checklist; a keyboard selection posts; a clear and a
  disposal through `window.__viewer`.
- **M45 GPU safeguards** (owner 2026-10-10: "implement those safeguards in general, when the app runs on other
  systems with different gpus"). The full Tile of M43b (15.6 GB reserved) restarted the 16 GB laptop when run alone
  (bugcheck 0x20001); earlier freezes came from several GPU jobs at once. On Windows (WDDM) CUDA spills past VRAM
  into shared memory instead of raising an OOM, and torch ignores `expandable_segments` there. Three guards, one
  table:
  - **The table** (`server/presets.VRAM_GB`): what each GPU step's own process peaks at on the laptop, cited from
    `docs/bench/` (torch's peak, or its reserved pool where measured), with a param that picks the model where
    there is one (`trellis2` `weights`: fast 9.0, faithful 13.3); `concepts` 8.4, `canon` 9.2, `prep` and `bake`
    3.3, `project` 1.3, `parts` 1.8, `rig_skin` 3.4, `mocap` 4.3, `motion` 1.0, `rig_fit` 0. Light steps
    (`jobs.LIGHT`) and test steps need none; any other step counts as needing the whole budget until measured.
  - **The budget**: as much as is safe, measured (2026-10-10, owner: "use as much mem as possible without
    bringing the system down"): what nvidia-smi says is free when the job starts (Windows and the desktop held
    0.47 GB) − 0.3 GB for the CUDA context + cuBLAS workspace (measured 0.27 GB, outside torch's cap) − a 0.5 GB
    margin. Idle laptop: 15.92 − 0.47 − 0.8 = 14.65 GB (the full refine Tile reserved 15.57 and overflowed the
    card by ~0.4 GB). Without a live reading (Setup's check) a typical 0.5 GB desktop is assumed: total − 1.3.
    `EFFIGY_VRAM_RESERVE_GB` fixes the reserve instead.
  - **RAM, like VRAM** (the generate tests, 2026-10-10, docs/bench/m45.md): `presets.RAM_GB` = the host RAM a
    step's fresh process peaks at (trellis2 20: Faithful 19.3–21.7, Fast 18.4 measured); the pre-flight refuses
    a load (`oom`, "needs about 20 GB of free memory (RAM) … close other programs", no lower preset: Fast needs
    it too) while less is free (`presets.ram_gb()`, MEMORYSTATUSEX). A resident worker holds one step AND one
    model (`jobs._key`, `_resident`): a switch of `weights` evicts it and loads in a fresh process, because
    swapping in place peaked at 21.3 GB (the freed model's heap stays with the process) and left the 32 GB
    laptop paging: one worker died after sampling, another crawled at 21 GB with the GPU idle. The lower
    preset is suggested only for the step's own OOM, never for a refusal.
  - **Pre-flight** (`jobs._preflight`, under the GPU lock, after the resident worker is evicted): nvidia-smi's
    total and free; a need over the budget fails at once (`oom`, "needs about 13.3 GB …; this GPU has 12.0 GB and
    keeps 1.5 GB …, or use weights fast", `suggest` the largest variant that fits); a need over what other
    programs leave free fails with "the GPU is busy: other programs use N of its T GB". The resident worker's own
    family skips the busy check (its VRAM is ours). No nvidia-smi: no checks.
  - **The cap** (`steps/_common._cap_vram`, §3.1 `EFFIGY_VRAM_BUDGET_GB`): every GPU step process, one-shot or
    `--serve`, sets `torch.cuda.set_per_process_memory_fraction(budget / total)` before its handler; going over is
    a clean OOM, which the backend words as "<step> ran out of video memory: it may use X GB of this GPU, it needed
    about Y GB on the 16 GB reference laptop (…)", `suggest` from `LOWER` as before.
  - **UI**: Setup's GPU card comes from the table (ok: all fit; warn: names what is refused and why; bad: no 3D
    model fits), replacing `VRAM_GOOD`/`VRAM_LOW`; the Generate panel disables a generator that never fits, with
    its need and the budget.
  - **When M43 merges**: `refine` becomes a row, `"refine": ("tile", "lite", {"full": 15.6, "lite": 12.3})`
    (`docs/bench/m43b.md` peak reserved), with `LOWER["refine"] = {"tile": "lite"}`; `FULL_MIN_GB` and its check in
    `steps/refine.py` go (the pre-flight refuses full under ~17.4 GB and says "use tile lite"); `bake` becomes 4.8
    (m43a: +1.5 GB for the AO).
  - **Not done**: the driver's "CUDA - Sysmem Fallback Policy" (NVIDIA Control Panel, per program "Prefer No
    Sysmem Fallback") would make a spill an OOM for non-torch allocations too; it is a driver profile, not
    something the app can set: a SETUP.md line if the owner wants it.
  - **As built** (2026-10-10, code only, no GPU run): `tests/test_gpu_safeguards.py` (the table, the budget and
    reserve, the pre-flight decisions on mocked nvidia-smi numbers, a refused job never spawns, the OOM message,
    the cap with a fake torch, the env), `test_first_run`'s machine check, `tsc`. Still to run on the laptop, one
    at a time: a GPU step with a small `EFFIGY_VRAM_BUDGET_GB` ends in `oom` (the cap under `cudaMallocAsync`),
    Faithful generate under the cap (13.3 < 14.3), the resident worker after a refusal, the Generate panel.

  - **Audit fixes (2026-10-11, BE-006 + BE-007, `server/jobs.py`, `steps/_common.py`)**: a warm resident run
    took every used GB on the GPU for the worker's own models (nvidia-smi cannot tell processes apart on
    Windows) and the worker kept the cap it was started with, so a game that came meanwhile was invisible.
    Now the worker keeps the free VRAM the pre-flight saw before its cold start (`Job.free_gb`,
    `Worker.free_cold`) and what the GPU showed when it last went idle (`Worker.idle_free`, read 0.5 s after
    each RESULT, after the worker's cache release); a warm pre-flight reads other programs' share as the
    change since (`free = free_cold + (free_now − idle_free)`, capped at total − desktop; a worker without a
    baseline yet is taken to hold everything, as before) and the refusals apply; the serve request carries
    `budget` and the worker re-caps itself per request. A cancel that landed while the resident process was
    starting (no process bound yet) left the fresh worker alive and installed; `_run_resident` binds the
    process first and, finding the job cancelled, evicts a fresh worker (a warm one stays for the next job)
    and returns nothing. Tests: the warm arithmetic on a faked worker (nothing changed: the cold budget; a
    game took 7 GB: "busy"; no baseline: the old assumption), the request line's `budget` and a `--serve` loop
    re-capping a fake torch, a cancel injected into the spawn through the API (`_echo --serve`: the job
    cancelled, the process dead, no worker; then a warm worker surviving the same injection).
- **M41 More creature skeletons** (PLAN Phase 3 "dragon, snake, spider, shark, kaiju the same way later"; owner
  2026-10-09: "Go for the more creature skeletons"). M36's way, one creature after another, simplest first; each
  lands as its own PR (M41a–e) and the owner can stop after any of them.
  - **Source (checked 2026-10-09)**: Mesh2Motion at the pinned commit 79f3f61 (main 653a969 has the same
    `static/rigs` and `static/animations`) ships all five: `static/rigs/rig-<id>.glb`,
    `static/animations/<id>-animations.glb` (the clips on the rig's skeleton: the same bone names and rest
    poses, checked in Blender 5.2.2), `static/models/model-<id>.glb` (an unrigged sample mesh). Licence: README
    "The art assets (3d models, rigs, animations) are all licensed under CC0", code MIT: the existing
    `licenses.yaml` row `Mesh2Motion/mesh2motion-app` covers them (its flag text gets the five names). Not yet
    downloaded (the clips group holds the human pack and fox/horse/bird only). The five:

    | id | bones, sides | clips (frames as `creature_clips` counts them; `Rest Pose` left out as M36) |
    |---|---|---|
    | kaiju | 58, `_L`/`_R` | Attack 25, Death 108, Hit 21, Idle 56, Roar 144, Swim 66, Tail Attack 65, Walk 73, Walk RM 80 |
    | shark | 33, `_l`/`_r` | Bite 71, Dead Floating 79, Hit 19, Idle 60, Swim Horizontal 32, Swim Vertical 79 |
    | dragon | 99¹, `_l`/`_r` (`horn_tip_L`/`_R` upper case) | Fly Flap 17, Fly Glide 47, Idle 47, Walk 31 |
    | spider | 56, `_l`/`_r` | Attack 25, Bite 25, Death 31, Death 2 24, Eating 53, Hit 16, Idle 77, Jump 32, Walk 23 |
    | snake | 28, none | Bite 29, Coiled 39, Dance 47, Death 53, Hit 24, Idle 54, Side winding 44 |

    Pins (SHA-256): rig dragon `4c31e7b483672a84e2692badbea4f3d53106bd0e0c84deba9abde102ea74d74b`, snake
    `6d3fdf0449ec8bb3f91136ba0c88cf9592ad02416e3ad9d356d6bd93e37e57e0`, spider
    `c68da203fb89c7ab91dac5e2ff8617e9497e1b5dbfbfbad6318cea56f65e36cc`, shark
    `0a9e72b814a079eba7742039442ced5fd54cbcf88332695d47148b06ebb76962`, kaiju
    `65b19290123d5ced8d04ace3ebc362c1a8f6c8d9b247f486dde9c8b3f6a22764`; animations dragon
    `73262041b09aa0499778818162b34955e76e17548212825548799643d5c925b3`, snake
    `795e4c62ba1e02f3f33e900832cdc6a1ca5a4aa4f6fe426fccd3aaa0f7f6ac25`, spider
    `a321c1617c7f74cf86c050694bc254e74409c6c69db26fe542338f2f7c43d07a`, shark
    `918321bbcab9439456fcc5838d745c810ea6fd5adb658ccc3651b5d4244f9107`, kaiju
    `675a098d8cdddf5247c55d49fa04712556cd0493facec0be586b6bb9aaeed410`; models dragon
    `4ea7600bdad99d234bfb21396e597b41ba36732ee22592a7894b96664fd01c39`, snake
    `07fd75c7b6645247f4d0545e2355aadf1ddf5357387041a1aa4d5eaa12668cdc`, spider
    `7c2e298c101f74956153b5932e3667b537c824f28e472f6c8e3df0c45f1950c7`, shark
    `cc947584ebb08024c8fc49dc207ff6162c93554fb17b0028ee4eb6131fe809a6`, kaiju
    `720fa706d11815c7360b7ac9781bcc2564d77177ca2b3dba5c4ef5db0dd0927a`. ¹ The dragon's clips file has one more
    bone than its rig, an unparented `DRV_root` (a driver helper): `creature_clips` maps by our bones' names, so it
    is ignored (check at build: no clip moves the dragon through it).
    Skeletons in short (glTF frame, the template's front +Z): **kaiju** root, Hips, Spine_1…Spine_4, Head, Nose,
    Chin, `Head.tip`, arms `Front_Leg_Shoulder/Upper/Lower`, `Hand`, thumb/index/pinky, legs
    `Back_Leg_Pelvis/Upper/Lower/Ankle/Foot/Foot_1/Foot_2/Tip`, `Stomach`, `Tail_Base`, `Tail_Mid…`, `Tail_Tip`
    (38 names shared with the fox: same rigger). **Shark** root, pelvis, neck, head, head_tip, mouth_base/1/tip,
    tail_1…tail_8, tail_tip, `front_fin_0…tip` off tail_1 and `back_fin_0…tip` off tail_4 (pectoral and pelvic;
    the dorsal and the tail fin have no bones: tail and spine weights); its fins and mouth hang below its root
    (the rig floats: front fin tips at y −0.97). **Dragon** root, hips, spine_0…spine_4, head, mouth upper/lower,
    horns off spine_4, `stomach`, legs `front_leg_collar/leg/ankle/foot/toes/toes_tip`, `back_leg_hip/…`, wings
    `shoulder` → `wing_1…wing_5`, `wing_tip`, five membrane fingers `wing_feather_<k>_0/1/tip` per wing (60 of the
    99 bones are wings), tail_1…tail_6, tail_tip; spread wings make its box wider (9.6) than long (6.5).
    **Spider** root, hips, spine_1, spine_2, head, head_tip, `teeth`, `legs_anchor` → four legs a (front) … d
    (back) per side, `leg_<x>_1/2/3/tip` (knees above the body: leg_a_2 at y 0.68, the body at 0.11), the abdomen
    as `tail_1…tail_tip`. **Snake** root, head (the root's child: the clips' "hips"), head_tip, mouth, `neck`,
    tail01…tail20, tail_tip, straight along −Z, no limbs.
  - **Baseline (measured)**: the unchanged M36 fit with the five templates on Mesh2Motion's own sample meshes
    (base centred, turned 0/90/180/270°), joints inside before rig_fit's snap / `fit_quality` score: fox 47/49,
    61 (reference); kaiju 36/58, 26; shark 18/33, 19; dragon 23/99, 3; spider 0/56, 0; snake 0/28, 0. The facing
    is right four of four turns for all but the spider (180° off four of four: its abdomen stands higher than its
    head, so "more body above the back" picks the abdomen). Kaiju is close; shark and dragon lose their fins and
    wings (thin parts the stretched box misses); spider and snake need their own placement.
  - **What the fit gets (shared, in `steps/_templates.py`; every change re-checked on the M36 animals)**:
    1. **One group of feet** (kaiju, bird): `_feet` returns the group's middle when there is no gap; the
       piecewise map along the body runs through three points (the box's ends, the feet) instead of four.
    2. **Limbs onto the mesh's own thin parts**: M36's tail rule for every limb chain (a chain from a trunk
       joint to a leaf: a leg, a wing, a fin, an arm) that sticks out of the solid: `_tail` generalised to
       `_limb(vox, surf, root, fractions, floor_ok, taken)`: the thin part (the same opening by a ball a third
       of the body's depth) nearest the chain's root and not taken by another chain, its joints at the
       template's bone-length fractions along it (not evenly: a knee where the template has it); a leg may
       reach the floor (the template's tip on its own floor: `floor_ok`), a tail, a wing, a fin may not.
       Joints hanging off a moved chain (the dragon's membrane fingers, the kaiju's fingers, toes) move with
       their chain bone rigidly (its turn from the template's direction and its length ratio), as the head's
       joints do. A chain wholly inside stays (M36's animals: their legs inside, nothing changes).
    3. **The body's axis from its thick part** for a template wider than long (the dragon's spread wings, the
       spider's legs, the bird: the box's long axis is not the body's): the horizontal PCA of the opened
       solid, then the quarter turn that puts the template's hips→head along it. Others keep the proportions.
    4. **Head end for the spider**: `HEAD_LOW = {"spider"}` takes the end with *less* body above the back.
       ponytail: one set, not a rule per template; a tarantula with a flat abdomen goes Turn around.
    5. **The snake: one chain along its body** (a template with no limbs, `limbs()` empty): the voxel solid's
       longest path (farthest voxel from any, then farthest from that; distances as in `_tail`), the head at
       the thicker end (the distance transform's mean over the first and last tenth), head→neck→tail01…tail_tip
       at the template's bone-length fractions along it, the head's own joints (mouth, head_tip) turned to the
       path's heading at the head; `yaw` = that heading. ponytail: a coil whose loops touch merges in the voxels
       and the path short-cuts; the brief asks for "stretched out, gentle S curve", the editor fixes the rest.
    The rest stays generic: lean (PCA footprint), centre line onto the trunk, the head's ear turn (no template
    here has ears: it does nothing), `snap_inside`, mirroring by suffix.
  - **Skinning and clips**: `rig_skin` unchanged (bone heat; `TEMPLATES` names each creature's torso and limbs:
    kaiju `Hips…Spine_4` / `Front_Leg_`, `Back_Leg_`; shark `pelvis`, `neck`, `tail_1…tail_3` / `front_fin`,
    `back_fin`; dragon `hips`, `spine_0…spine_4` / `front_`, `back_`, `collar_`, `hips_anchor`, `shoulder_`,
    `wing_`; spider `hips`, `spine_1`, `spine_2` / `legs_anchor`, `leg_`; snake every chain bone / none).
    `blender_ops/creature_clips.py`: the travel scale is the hips' height **above the skeleton's lowest joint**,
    ours over the template's (the shark's rig floats: its hips' height alone over-scales its swim 2–3×; the
    M36 rigs stand at 0, so nothing changes for them). The snake's "hips" is its head (the root's child), which
    carries the travel. Check at build: the snake's Side winding and Bite on an S-curved snake (the clips turn
    each bone by the template's world turn from its *straight* rest; turns about the vertical add to our curve,
    a head lift far from the head turns sideways); only if that looks wrong, each middle-line bone gets its own
    heading instead of the one `yaw`.
  - **Import (M39)**: `blender_ops/import_model.py` maps UE5 names whenever a bone is called `pelvis`; the
    shark's `pelvis` and `head` would turn into `Hips`/`Head` and the shark go unrecognised: map UE5 only when
    `spine_01` is there too. No template's bone set is inside another's (checked: the kaiju shares 38 names with
    the fox, not all), so recognition stays unambiguous.
  - **Files**: `steps/_templates.py` (RIGS, TEMPLATES rows, the fit changes above), `steps/templates/<id>.json`
    (`python -m tools.templates`, needs Blender), `tools/download_models.py` (five `clips` rows, pinned as
    above; `SIZES["clips"]` +3.2 MB), `tools/models_manifest.json`, `licenses.yaml` (the row's flag text),
    `blender_ops/creature_clips.py` (travel scale), `blender_ops/import_model.py` (UE5 gate), `ui/src/main.tsx`
    (RIG_KINDS), `ui/src/Import.tsx` (SKELETONS), `tools/creature_bench.py` (new: the baseline above as a tool:
    the pinned sample meshes of all eight Mesh2Motion animals, four turns each, prints yaw / inside / score per
    template; its table into `docs/bench/`), `tests/test_templates.py`. No API, preset or contract-shape change:
    `/api/clips`, `rig_fit`, `anim` and `import_model` take any id in `TEMPLATES`.
  - **§3 rows at build** (text only, the shape stays): `rig_fit` "a creature's (M36: `fox`, `horse`, `bird`; M41:
    `kaiju`, `shark`, `dragon`, `spider`, `snake`)" (the snake's `sides` `_l`/`_r` match none of its bones);
    `/api/clips` the same list; `rig_skin` / `anim` unchanged.
  - **UI**: RIG_KINDS and Import's SKELETONS gain, as each creature lands: "A two-legged monster with a tail
    (kaiju, dinosaur)", "A shark or fish", "A dragon (four legs, wings)", "A spider (eight legs)", "A snake (no
    legs)". The editor's groups and mirror work by `sides` already; the snake shows one group (Middle). The
    dragon's `horn_tip_L`/`_R` sit in Middle and do not mirror (two leaf bones; measure the skin QA's symmetry
    with them; only if it warns, suffixes compare case-insensitively in `twin`, rig_qa's `swap`, `twinOf`,
    `animGroups`).
  - **Test models** (the owner's library, made through the app by the coordinator 2026-10-09: brief `{prompt,
    category: "creature"}` → concepts → best concept → prep → `trellis2` generate Faithful → clean): `test kaiju`,
    `test shark`, `test dragon` (a dragon on four legs, wings folded, long neck and tail; `005_clean` 180k faces,
    watertight, one piece), `test spider`, `test snake` (stretched out, a gentle S curve). Fits run on library
    copies, never the owner's versions. Check at build: the brief's creature suffix says "all legs visible and
    apart, standing pose" for a snake and a shark too; if their concepts came out odd, the suffix drops the leg
    words when the prompt names no legs (one line in `steps/brief.py`).
  - **Tests** (`tests/test_templates.py`): the template test for every id (the snake allowed no sides and no
    limbs); a box kaiju (legs, a tail on the floor) faces right four turns; `_limb` puts a box spider's eight
    legs into its eight thin parts, the front pair `leg_a`, and a fin chain into a box shark's fin; the spider's
    facing four turns; a box snake bent in an S: its chain inside and the head at the thick end; `import_model`
    keeps a skeleton with `pelvis` and `head` but no `spine_01` unmapped (fake, no Blender); the clip list for each
    new id (`catalogue`, files present: skipped without). Existing M36 tests unchanged and passing.
  - **Acceptance per creature** (each its own PR; numbers on the Mesh2Motion sample, four turns, and on the
    owner's `test <id>` copy; Blender renders looked at, side view, as M36):
    | | sample: facing / inside after snap / score | test asset: inside / score | skin QA | clips |
    |---|---|---|---|---|
    | M41a kaiju | 4/4, ≥ 52/58, ≥ 45 | ≥ 50/58, ≥ 35 | no fail | all 9 bake, Walk and Tail Attack rendered |
    | M41b shark | 4/4, ≥ 29/33, ≥ 40 | ≥ 28/33, ≥ 35 | no fail | all 6, Swim Horizontal rendered |
    | M41c dragon | 4/4, ≥ 85/99, ≥ 40 | ≥ 80/99, ≥ 30 | no fail | all 4, Walk and Fly Flap rendered |
    | M41d spider | 4/4, ≥ 48/56, ≥ 40 | ≥ 46/56, ≥ 30 | no fail | all 9, Walk rendered (eight legs stepping) |
    | M41e snake | 4/4, ≥ 25/28, ≥ 45 | ≥ 24/28, ≥ 35 | no fail | all 7, Side winding and Bite rendered |

    Plus, every PR: `tools/creature_bench.py` for fox, horse, bird (and the cat's copy): yaw the same, inside and
    score not lower than before the PR (the table before/after in the PR); `Check fit` in the app on the test
    asset; full pytest, ruff, Playwright for the Rig "What is it?" list. Godot import of one animated creature
    (M36's headless recipe): optional, once, after M41c. Targets are first guesses (the cat's automatic fit
    scored 42, hand-edited 54): a creature under its numbers goes to the owner with the renders, not forced.
  - **Order and split**: M41a kaiju (shared data for all five first: rigs, JSONs, clip rows, manifest, the bench
    tool; then fit item 1, the travel scale, the UE5 gate, the kaiju's rows) → M41b shark (item 2: `_limb`, fins)
    → M41c dragon (items 2's hangers and 3) → M41d spider (`_limb` for legs on the floor, item 4) → M41e snake
    (item 5). In parallel inside M41a: a worker on the data rows + `tools.templates` + manifest + bench tool, a
    second on the travel scale + UE5 gate + their tests; the fit items are one function, one agent at a time
    (expert for items 2, 3, 5; a worker for 1, 4, the UI rows and the renders).
  - **Non-goals** (ponytail): insects with six legs (no template), wings that fold in the clips beyond
    Mesh2Motion's, a fish template beyond the shark, per-bone headings for curved bodies (only if M41e's renders
    ask), a keypoint model for animals (none licence-clean yet), new presets (`game-creature` stays the fox).

- **M43 Professional textures** (owner 2026-10-09: "do the texture refinements next, I need a high quality solution
  for making textures apply on the generated assets to look them professional"; research 14, its spike images
  `docs/research/14_*.jpg`). Today's bake samples the 1024³ voxel colour field once per texel: mushy buttons and
  buckles, smeared cloth, no AO (ORM R = 255), UV seams unhealed. M43 keeps the asset's mesh and UVs and the four
  texture files, and makes what is in them better, in three sub-PRs the owner can judge one by one.
  **Contract changes** (each in the sub-PR that needs it, §3 updated with the code): §3.2 `bake` row (new params and
  metrics, ORM R now AO, the GLB's `occlusionTexture`); a new §3.2 row `refine`; `licenses.yaml` and
  `tools/models_manifest.json` rows for the Tile weights. The files every later step reads (`mesh.glb`,
  `basecolor.png`, `orm.png`, `normal.png`) keep their names and meaning.
  - **M43a Cheap wins in `bake`** (no model; `steps/bake.py`, `steps/_bake.py`, `tests/test_bake.py`):
    - `supersample` (int, 1 | 2, default 2): `rasterize_uv` at 2× the size, the four texels' samples box-filtered
      into one (coverage = any of the four); gutters filled after the downsample. 4096 × 2 is refused (8192² raster)
      unless measured under budget: `bad_params` with the reason.
    - `ao` (bool, default true): per texel 32 cosine-weighted hemisphere rays from the texel's point on the version's own mesh
      (`cumesh.cuBVH.ray_trace`; NOT the hi mesh: its inner shell layers made black blotches, see docs/bench/m43a.md), offset by 0.5 voxel along the normal,
      occluded within `0.1 ×` the bbox diagonal; AO = 1 − occluded share, written into ORM R and declared as the
      glTF `occlusionTexture` (same image as metallicRoughness; `write_glb` grows an `occlusion` flag; since 2026-10-11 one image and one texture for both slots, trimesh listed the picture once per slot over one buffer view). CPU runs
      (tests) write R = 255 as today. `metrics.ao_mean`, `metrics.ao_seconds`.
    - **Seam healing** (always on): for each UV seam edge (two edges at the same 3D positions with different UVs: glTF splits the
      vertices there) the texels within 2 px of it on both sides take the mean of their own colour and the colour sampled
      at the same 3D point on the other side's chart; base colour only (normal and ORM are continuous already).
      `metrics.seam_delta` = mean |Δ| (0–255) across seams after, `seam_delta_before` before.
    - The existing gutter push-pull stays; padding is the uv step's.
    - Tests: a quad split into two charts with a colour step across the seam → `seam_delta` drops; supersample 2
      on a 1-texel checker gives the box-filtered value; AO of a flat plane is 1, of the inside of a box < 0.3
      (CPU: R = 255 and `ao: false` in metrics).
    - **Acceptance**: the bench set (§ below) re-baked: seam delta, AO visible in crevices in Godot (the export's
      ORM), seconds and peak VRAM; the owner's A/B of old vs new bake on three assets.
  - **M43b Detail pass: new step `refine`** (`steps/refine.py`, `steps/_views.py` (orthographic camera, unlit view
    render and back-projection, lifted from `project.py`; `project.py` then imports them), `server/presets.py`,
    `tools/download_models.py`, `tools/models_manifest.json`, `licenses.yaml`, UI: `workflow.ts`, `main.tsx`;
    `tests/test_refine.py`):
    - **Contract row** §3.2: `refine` | `mesh.glb` (textured), `basecolor.png`, `orm.png`, `normal.png`, `views.png` |
      in: any version with a UV-mapped, textured `.glb` (bake, project, refine); `orm`/`normal` copied from the
      input; `views.png` = a grid of each view before and after (for the panel and `look`); the mesh and UVs
      unchanged.
    - **Params**: `strength` (0.3–1.0, default 1.0), `face_strength` (default 0.5), `scale` (control scale 0.5–1.0,
      default 0.85), `prompt` (default: the asset's brief `prompt_full`, else the asset name), `zoom` (`"auto"` |
      a number ≥ 0.5: view pixels per atlas texel, default auto = 1.25), `views` (`"auto"` | a list of
      `{yaw, pitch, center: [x, y, z], extent}` in the library frame), `seed`, `texture_size` (keep | 4096: the
      atlas upsized first, for close-up assets).
      **Built (docs/bench/m43b.md)**: `tile` (`"full"` | `"lite"`, the fallback); the texels `project` changed are
      kept (redrawn at 0.5 the face became another man); seams levelled after the views (`heal_seams`); the
      prompt defaults to the brief's own words. The full Tile reserves 15.6 GB on Windows (expandable segments
      unsupported there) and **restarted the reference laptop when run alone (2026-10-10, bugcheck 0x20001)**: `lite`
      (peak 12.4 GB) is the default and the only file the `refine` group downloads (2.0 GB); `full` is not part of the download (the step says so); M45's VRAM table holds `refine` at 12.3 GB (lite).
    - **Stages, in order**:
      1. **Plan the views** (auto): the atlas's area-weighted texel density d (texels/m: UV area vs 3D area);
         window extent `e = 1024 / (zoom · d)`; the mesh's height cut into windows of `e` with 25 % overlap; at
         each window 8 yaws (0, 45, …, 315) at pitch 0, plus, for a model taller than wide, a top view (pitch −60) of the topmost window (2026-10-11: on a wall's coping and a chest's lid the redraw invented tiles); a
         window that sees no surface is dropped. Order: front first, then alternating sides, the back last.
         (Pirate, 1 m, 2K: d = 1136/m → e ≈ 0.72 m → 2 windows × 8 + 1 = 17 views.)
      2. **Render** each view from the **current** texture (the earlier views already painted in): unlit base
         colour, 2048² with `rasterize_uv(depth=…)`, box-filtered to 1024², on 50 % grey.
      3. **Generate**: Z-Image-Turbo + Fun-ControlNet **Tile 2.1-2601-8steps (full)**, `ZImageControlNetPipeline`
         (diffusers 0.40, no new package), 8 steps, guidance 0, the view as `control_image`, `strength` 1.0 = from
         noise (measured: partial denoise paints ornament on flat cloth); a view that sees the face (texels the
         chain's `project` changed: its base colour vs its input's, |Δ| > 8) runs img2img at `face_strength`
         (the schedule entered at step `round(8·(1−s))`, the render's latent noised to that σ; spike code in
         research 14). The prompt + ", even flat lighting, plain grey background" + the window's part
         ("close-up of the upper body, front view").
      4. **Colour match**: the view gets `blur(render) − blur(generated)` inside the silhouette (`project.py`'s
         `shading_offset` normalised convolution, σ = 2.5 % of the view): the bake's colours, the model's detail.
      5. **Back-project** into the atlas: weight = smoothstep((cos − 0.25)/0.35) × z-test (3×3 farthest depth +
         1 % slop) × the silhouette eroded by 6 px; per texel the views blend with weight⁴ normalised (the best
         view wins, no ghosting from equal averaging); texels no view reaches keep their colour; `fill_gutters`.
      6. Write the GLB with the input's ORM and normal; `views.png`.
    - **Metrics**: `views`, `seconds`, `seconds_load`, `seconds_per_view`, `peak_vram_gb`, `refined` (share of
      covered texels with weight > 0.5), `drift` (mean |low-pass Δ| vs the input's render, 0–255, over the
      refined texels), `detail` (band-pass energy 2–8 px in the views, after / before; since 2026-10-10 weighted per
      pixel by the gradients' coherence, the structure tensor's anisotropy: white noise weighs about a tenth, blurred
      blotches about a third, strokes, edges and stitching fully (synthetic images); the plain energy fell on four of
      the five bench assets and rose on the lantern while every render gained structure, docs/bench/m43b.md; the new
      number is not yet measured on the bench; `detail_metric: "coherence"` marks versions that carry it), `seam_delta`,
      `face_views`.
      `models`: Z-Image-Turbo and the Tile weights with revisions; `licenses` both rows.
    - **Models**: `Tongyi-MAI/Z-Image-Turbo` (Apache-2.0, revision `f332072a…`, already the `concepts` group) and
      `alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1` file `Z-Image-Turbo-Fun-Controlnet-Tile-2.1-2601-8steps.safetensors`
      (Apache-2.0, revision `5155fc56d17821007d6f62ac192c09e0f0e72016`, 6 712 485 600 B, SHA-256 in the manifest);
      new group **`refine`** = both (Z-Image shared with `concepts`, counted once), Setup card "Texture detail".
      Loaded as `ZImageControlNetModel(control_layers_places=[0, 2, …, 28], control_refiner_layers_places=[0, 1],
      add_control_noise_refiner="control_noise_refiner", control_in_dim=33)` + `load_state_dict` (not
      `from_single_file`, which mis-detects the lite build). PLAN §6: both Apache, SDPA (diffusers' default), no
      nvdiffrast (our `rasterize_uv`).
    - **VRAM plan** (one model resident, ≤ 14.5 GB): DiT fp8 storage (`enable_layerwise_casting`), the control
      net's own `control_layers` + `control_noise_refiner` fp8 (`apply_layerwise_casting` on those two only: the
      rest is tied to the DiT); prompts encoded first with the text encoder alone (8.3 GB), then off the card;
      measured 11.95 GB allocated, 15.12 GB reserved per 1024² view → the step sets
      `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` and asserts `max_memory_reserved` ≤ 14.5 GB on the bench;
      if it does not hold, the lite Tile (10.34 GB, measured; drifts more) replaces the full one. No 2048² views
      (17.8 GB, 127 s: spills). Runs one-shot (not in `SERVE`): `concepts` is the resident Z-Image and two
      residents never coexist; ponytail: ~60 s load per run, make it a `concepts` op if the owner refines often.
    - **UI**: a new optional stage **"Detail"** (`workflow.ts` STAGES, after Face, `steps: ["refine"]`, hint
      "Optional: sharper, more detailed textures; about 4–5 minutes"), the step label "Refine texture"; the panel:
      one button **"Refine texture"** (the quick preset: defaults), a strength slider, the prompt prefilled from
      the brief, `views.png` shown when done; before/after with M42's compare (the refined version against its
      parent, one camera); until M42 lands, the version switch. Presets: `PRESETS["refine"] = {"detail": {}}`;
      **not** in `CHAINS` until the owner accepts it on the bench.
    - Tests (CPU, no model): view planning from a known texel density (window count and extents), the
      back-projection of a constant-colour view onto a cube (painted texels = the view's colour, hidden faces
      untouched, grazing faces partly), the weight⁴ blend picks the frontal view, the colour match removes a
      constant offset, the face-mask from a `project` version, bad params; the generator faked (identity).
    - **Acceptance**: the bench (below) with `refine` after bake (characters after project): per asset seconds,
      peak VRAM, `refined`, `drift` ≤ 4, `detail` > 1.5×, `seam_delta` no worse than the input's; a sheet per asset
      (front, three-quarter, back, head close-up; before/after); the owner's blind A/B in Godot at game distance
      and close-up: bake vs refine.
  - **M43c Delight and PBR** (`steps/refine.py` op or params, `tests/test_refine.py`):
    - **AO divide** (no model, `delight` 0–1, default 0 until accepted): over covered texels, the base colour's
      luminance regressed on the baked AO (M43a) at low frequency; `albedo /= lerp(1, fit(ao), delight)`, hue kept.
      Metric: `ao_luma_corr` before / after (should fall towards 0) under three Godot HDRIs.
    - **SuperMat gate** (research 14 §4): a scratch spike, not code: single-image SuperMat (MIT code and weights;
      SD 2.1 base OpenRAIL++, `sd2-community/stable-diffusion-2-1`) on 6 orthogonal 512² views of three bench
      assets with SDPA (a stub `xformers` module; never the package), in `.venv-ml` only if its diffusers 0.40 runs
      it, else a scratch venv. **Into M43c only if**: ≤ 6 GB, ≤ 10 s per view, and its albedo, used as a
      low-frequency correction (M24's method: its blur minus the bake's blur), beats the AO divide on
      `ao_luma_corr` and in the owner's A/B; its rough/metal replace TRELLIS's only if the owner prefers them.
      Then a group `pbr`, `licenses.yaml` rows for SuperMat and SD 2.1.
    - **Acceptance**: the bench sheets with delight 0 / 0.5 / 1; the owner picks the default.
    - **As built (branch `m43c`, 2026-10-10): the AO divide** in `steps/refine.py` (`delight`, `ao_luma_corr`,
      `_low`): the AO is the input GLB's `occlusionTexture` (M43a's ORM R; trimesh reads it back), resized to the
      atlas, its UV seams levelled (`heal_seams`); over the covered texels a least-squares line of the luminance
      on the raw per-texel AO, L ≈ α + β·ao, `fit = clamp((α + β·ao)/(α + β), 0.25, 1)`, each covered texel's RGB
      divided by `1 − delight·(1 − fit)` (one factor per texel: the hue stays); β ≤ 0 (no shadows in the colour)
      divides by nothing. The raw AO, not a blurred one: it is what an engine multiplies back in, and a blur in UV
      space leaks across chart borders. Only the metric blurs (both at σ = size/512, `_low`). Done before the views, so the Tile draws on the flatter colours; the metrics' "before" and the
      `views.png` left halves are the delit texture (what the views started from), `seam_delta_before` and
      `ao_luma_corr_before` the input's. `views: []` is the no-model path (the step never constructs the
      generator; `peak_vram_gb` is the raster's). Validation: `delight` a number 0–1; `[]` views only with
      `delight` > 0 (else `bad_params` as before). UI (`main.tsx` DetailPanel): a "Remove baked shadows" slider
      (0–1, step 0.25) sent with any refine as `delight` when > 0, and a "Shadows only" button (`views: []`,
      enabled when the slider is > 0); a refine version with `views` 0 reads "No redraw." and the correlation
      before → after. Tests (`tests/test_refine.py`): a known albedo × a crevice AO comes back within 6 levels
      of the albedo with the correlation < 0.1, the hue ratio unchanged to 1e-3, 0.5 half way, 0 the identity,
      an unshadowed albedo untouched; a delight-only run on a box baked with AO loads no model, keeps the
      occlusion texture and drops the correlation; a bake without AO is `missing_input`. **Not done**: the
      SuperMat gate (a spike; only if the owner wants more than the divide), the three-HDRI version of the metric
      (the texture-space correlation stands in), `delight` in `PRESETS`/`CHAINS` (the owner picks the default on
      the bench). **Found and fixed on the bench (docs/bench/m43c.md)**: (1) `refine`'s closing pass (seam
      levelling, gutter fill) used the texel-centre coverage while bake's is the 2× raster's "any quarter", so
      the gutter fill rewrote bake's edge ring on every refine and the seam delta rose ×3 on an unchanged texture
      (the chest: 2.29 → 6.20; M43b's bench had blamed the views): refine now rasterizes bake's mask (`wide`,
      1× at 4096) for those passes (chest 2.29 → 1.16). (2) the divide amplifies a seam's two sides differently
      where their AO differs, so the AO channel's seams are levelled (`heal_seams`) before dividing; the
      remaining rise (lantern 1.3 → 5.7 at delight 1: crevices divide by up to 4) is in the base colour only, an
      engine multiplies the AO back in. **The pirate's black AO triangles, fixed in bake (branch `ao-enclosed`,
      2026-10-10; docs/bench/m43c.md)**: 24.5 % of its bright texels were at AO < 0.1: inner-shell faces (open
      within 5 mm, closed within 2 cm, a wall 0.7 mm behind) seen where the outer shell is torn, the colour
      hiding them because it is sampled from the volume. No per-face rule reached the visible ones (four tried:
      orientation votes, enclosure, a lift from the face in front, a lift from the edge neighbours; the bench
      page has each one's number). The fix is in the AO map: `_bake.fill_dark_islands` fills connected patches
      below 0.25 that are smaller than 0.0003 of the atlas (≈ 7 faces of a 14k-face mesh) and more than 0.3
      below the median of the covered two-texel ring around them, with that median: a hemisphere integral is
      smooth along one surface, so such an island is another surface; a crevice is an extended region and stays.
      With it `_bake.ao_faces` (64-ray escape shares on both sides of each face's mean vertex normal) replaces
      `inward_faces`' two-ray vote for the ray normals and marks faces no ray leaves either way *enclosed* (AO
      1: hidden, or standing in for a torn surface). The M43 bench set (six characters, four props;
      docs/bench/m43c_ao.jpg): on the characters the bright texels at AO < 0.1 fall from 15–43 % to 0.3–3.3 %
      (pirate 24.5 → 0.3, farmer 23.0 → 0.5, knight 27.3 → 2.9, nurse 14.9 → 0.7, viking 30.3 → 3.3, kid 42.9 →
      2.2), `ao_enclosed` 0.24–0.46, `ao_filled` 0.05–0.06; the props have no inner shell and change by nothing
      (chest, barrel, anvil) or little (lantern 5.8 → 3.0: the globe's rim through its open top); AO +0.3–0.5 s. Tests: the box cases for `ao_faces` (GPU),
      the island/crevice/dark-region image for `fill_dark_islands` (CPU). The torn double shell itself stays a
      clean/remesh item.
  - **Bench set** (`docs/bench/m43.md`, `m43_*.jpg`): 6 characters (pirate, farmer, knight, nurse, viking, kid) and
    4 props (chest, barrel, lantern, anvil) from the library, each: bake (old), bake (M43a), refine, refine +
    delight; the numbers above per stage; `tools/bench.py` gains nothing (one script under `tools/` only if the
    sheets need it).
  - **Build order**: M43a (no model, a day) → M43b (the owner sees the detail; the main PR) → M43c (AO divide,
    then the SuperMat gate). Each is its own branch and PR, judged before the next.
  - **Non-goals** (ponytail): a text restyle (depth control + SyncMVD UV-latent sync: own milestone when asked),
    masked edits (Qwen-Edit is too slow on 16 GB; Z-Image inpaint is the candidate), SR on views (the Tile pass
    replaces it; Real-ESRGAN stays in `project`), LaMa (push-pull suffices), 2048² views, 4K by default, normal-map
    detail from the views, re-estimating rough/metal without the gate.

- **M59 Generic rigs: SkinTokens' full rig for an object or a creature without a template**: **dropped 2026-10-13 by the owner (SkinTokens' training data with no bone-heat alternative); the code, the Rig panel's "Something else", the tests and the docs removed (`drop-generic-rig`). As it was:** (PLAN Phase 3
  "SkinTokens full-rig mode for generic objects", the last Phase-3 rigging item; built 2026-10-10 in the cloud, code only,
  as "M46"; renumbered M59 when the branch met main's M46a–c, the bigger GPUs).
  `rig_fit` fits a template (a person, eight animals); anything else (a lamp, a tree, a robot arm, a six-legged
  insect) had no way to a skeleton. SkinTokens' own mode (no `--use_skeleton`) makes a skeleton and its skin from the
  mesh alone (research 05 §0: `tokenskin-rig`, "full rig only"); the same model and venv as the skin-only engine
  (3.43 GB measured there; research 05: "`--use_skeleton` isn't lighter").
  - **Files**: `steps/_skintokens.py` (`rig`: `glb_io.load_asset` → `infer.rig_asset` → `transfer.transfer_rigging`
    onto the textured input GLB; the port's class token stays at its default `articulation`: the checkpoint's other
    tokens are unverified here), `steps/_glb.py` (`joints`: the rest skeleton of the skin a mesh node uses, names,
    parents and world positions from the composed node transforms, from any skinned GLB), `steps/rig_skin.py` (`full`,
    `seed`; `skeleton.json` `template: "generic"` = `_templates.GENERIC`; the QA's left/right item neither shown nor
    counted; the one engine is named, so a missing port or weights fails with its own message), `steps/anim.py` (a
    generic rig refuses clips: `bad_params`), `ui/src/main.tsx` (Rig panel "Something else …" → `rig_skin {full:
    true}`, the model's provenance named there; the Skin panel says no clip fits and offers another seed instead of
    the skeleton editor), `tests/test_rig_skin.py`, `ui/tests/smoke.spec.ts`, §3.2 `rig_skin` row, PLAN §6,
    `licenses.yaml`.
  - **Owner decision pending (PLAN §6)**: SkinTokens was accepted because the QA always offers the Blender bone-heat
    result with no training-data provenance; a generic rig has no such alternative. The option ships with that said in
    the panel; dropping it is one `RIG_KINDS` row.
  - **Not done** (ponytail): a relabel of a humanoid result onto Mixamo names (the port has one; rig_fit covers people),
    a skeleton editor for the generic rig (re-run with another `seed` instead), the port's other class tokens (a param
    once a GPU run shows one helps), UniRig as a fallback, clips for generic rigs, a preset. Export: FBX keeps the
    model's bone names (`blender_ops/export` `humanoid: false`, as a creature). Time unmeasured (skin-only took 27–37 s).
  - **Acceptance**: `tests/test_rig_skin.py` (the full path around a faked model, the refusals; `_glb.joints` on
    `tests/fixtures/anim.glb`) and the smoke test pass on the CPU. **Needs the GPU, not run**: `rig_skin {full: true}`
    on a prop with moving parts (a lamp, the bench chest) and on an insect: the rigged.glb opens in the viewer with its
    skeleton, the QA has 0 unweighted vertices, the FBX import in Godot/Unreal shows the bones; the time and the VRAM
    peak against `presets.VRAM_GB["rig_skin"]` 3.4; a missing `skin` weights group ends in its download message.

### Experimental track

#### MX1: Hunyuan3D-2.1 experimental engine (restricted outputs)
- **Goal**: generate with Hunyuan3D-2.1 for comparison and experiments, fully local, without any Hunyuan
  output silently reaching a shipped game asset.
- **Depends**: M3 (bake), M5 (library/jobs), M7 + M13 (export), M14 (Models page). Can start any time
  after Phase 1.
- **Files**: `engine/hunyuan/{README.md, install.py}`, `steps/hunyuan.py`, `steps/bake.py` (contract PR:
  `params.source`), `server/library.py` (`restricted` propagation), `server/app.py` (enable/confirm
  routes), `steps/export.py` (restricted gate), `ui/src/panels/Models.tsx`, version badge in the asset
  panel, `licenses.yaml` (row with `experimental: true`, `region_note`), `tests/test_restricted.py`,
  `.github/workflows/wheels.yml` (Hunyuan CUDA extensions for sm_120).
- **Tasks**:
  1. **Enable flow** (Models page): show the licence's opening territory sentence and §5(b), §5(c), §4
     verbatim with a link to the full text; require ticking *"I am located outside the EU, the UK and
     South Korea, and I accept the Tencent Hunyuan 3D 2.1 Community License"*; store
     `{accepted_at, licence_url, commit}` in `settings.json`. No geo-IP, no network call of ours. Only
     then: `engine/hunyuan/install.py` creates `.venv-hunyuan`, pip-installs Hunyuan3D-2.1 from upstream
     git at the pinned commit (training-only deps such as deepspeed dropped after checking imports),
     installs our sm_120 builds of its CUDA extensions, and downloads `tencent/Hunyuan3D-2.1` from HF.
     Disabling deletes `.venv-hunyuan` and the weights.
  2. **`steps/hunyuan.py`** (our MIT wrapper, runs in `.venv-hunyuan`, §3.1 protocol): op `generate`
     (shape; params `seed`, `octree_resolution`, `steps`) → `hi.glb` in the library frame; op `paint`
     (optional) → `textured.glb`, run with Hunyuan's `--low_vram_mode`/offloading. If Paint's measured
     peak exceeds 14.5 GB on the laptop, the UI disables Paint on 16 GB cards and offers the TRELLIS.2
     shape-conditioned texturing (M12) on the Hunyuan shape instead (output still restricted).
  3. **Contract PR**: `bake` gains `params.source: "ovoxel" | "textured_mesh"` (default: `ovoxel` if the
     chain has `ovoxel.npz`, else `textured_mesh`): cage ray-cast onto the textured hi-poly mesh and
     sample its texture by barycentric UV at the hit point. Hunyuan outputs have no voxel volume.
  4. **Restricted propagation** (`server/library.py`): `restricted = parent.restricted or step in
     EXPERIMENTAL_STEPS`; Blender pulls and Claude runs inherit it from the version they started from.
  5. **Export gate**: `export` refuses a restricted version with `ERROR {"code": "restricted"}` unless
     `params.allow_restricted = true`, which the UI only sends after a dialog quoting §5(c) that the user
     confirms for this export. Restricted exports get `LICENSES.json` with the restriction spelled out
     and the licence's Notice text; `CREDITS.txt` notes the encouraged "Powered by Tencent Hunyuan"
     marking as optional.
  6. **UI**: red "Hunyuan — restricted outputs" badge on every restricted version and in the candidate
     comparison view; the engine never appears in presets, only as an explicit choice on the generate step.
- **Acceptance**: `tests/test_restricted.py` (no GPU): a chain `hunyuan → clean → remesh → bake →
  blender_pull` marks every version restricted; export without the flag fails with `restricted`; with
  the flag it writes `LICENSES.json` containing the restriction; `tests/test_licenses.py` fails if any
  preset references `hunyuan` or if the `hunyuan` row lacks `experimental: true`. On the laptop (needs
  GPU): enable flow → install → `generate` on 3 golden images with peak VRAM ≤ 14.5 GB (shape) recorded
  in `docs/bench/mx1.md`; Paint peak measured and the 16 GB decision recorded; one restricted asset
  baked via `source: textured_mesh`; A/B vs TRELLIS.2/Pixal3D topology (non-manifold edges, components,
  QuadriFlow success) recorded.
- **Non-goals**: Hunyuan3D-2mv/Omni/Part, HY-Motion, UltraShape, any redistribution of Tencent code or
  weights, using Hunyuan outputs in presets or for any model training.

### Import an animation (owner 2026-10-12)

- An animation made elsewhere goes onto a character skinned here. `import_motion` (§3.2) turns an upload (`.fbx`,
  `.glb`, `.gltf`, `.bvh`; uploads take `.bvh` since, stored under `source/` like a model) into a motion version, as
  a text or a video one: blender_ops/anim_clip keeps the armature and one animation (`clip`, default the first),
  drops the mesh, names the clip after the animation (the file's name for a generic take such as Mixamo's
  `mixamo.com`; a name in use in the asset or the clip pack gets the version's number) and reports the skeleton
  (`_maps.kind`); `anim` takes `NNN_import_motion` like `NNN_motion`. The retarget reads Mixamo's own names
  (`_maps.MIXAMO`, our 52 joints, any `mixamorig:` prefix kept by `source_map`) beside UE5 and SOMA; SOMA and
  Mixamo share Hips and Spine1, SOMA's Shin and Chest tell them apart.
- UI: Import page, "An animation for a character" (a character, a file: upload, `import_motion`, then `anim` onto
  its newest skinned version, which opens); the Animate panel's "Use an animation from a file" (onto this one,
  through the motion page); an imported motion's page brings in the file's other animations ("Bring it in too")
  and puts it on the model. Its licence is the user's, as an imported model's.
- Measured: the block figure's Walk as a GLB comes back exact, as a "mixamorig:" FBX within 2 mm a frame later
  (`test_an_animation_file_comes_in_and_goes_back_onto_the_rig`); on the farmer, a Kimodo BVH, the Mesh2Motion
  pack's Jog (one of 87) and the viking's animated GLB, each in 1–2 s and onto the rig together in 2 s, looked at
  in four-frame sheets. Not done: agents (`import_file` makes a new asset; an animation needs the character's).

### Mesh gates (2026-10-12, `mesh-gates`)

Four checks taken as ideas from `img2threejs` (Apache-2.0, bundled in the Mr. Mak Workspace repo) and that
repo's viewer (MIT), reimplemented on numpy and three.js; nothing copied, no new dependency.

- `validate`: `self_intersection` and `parts_apart` (§3.9 Mesh; `steps/_mesh.self_intersection`,
  `parts_inside`, `_crossings`: the projected barycentric ray test of `_rig.hits` for many points at once,
  a per-chunk box prefilter, shared-edge hits counted once; faces × samples capped at 5e7, ≈ 3 s on an
  80k-face sphere). The UI names them in `CHECKS`/`FIXES`.
- `rig_skin`: `joint_loops` beside the picked engine's items (§3.9 Rig; `qa.loops` has every joint's count,
  `metrics.joints_few_loops`).
- `parts` op `views`: `metrics.holes` = {view: background pixels inside the silhouette} from a flood fill of
  each colour render's alpha (`steps/parts.enclosed_background`); the Parts panel says which views show a
  hole through the model (a handle or an arm on the hip is fine, a torn shell is not).
- The viewer: Wireframe on a quad mesh (the nearest `remesh` ancestor with `topology: "quad"`) draws the quads
  as lines over the shaded surface (`Viewer.quadOutline`: a quad's two triangles are consecutive in the
  GLB and share their diagonal, as Blender's exporter writes them; under 90 % such pairs, or a skinned
  mesh, keeps the triangle wireframe). Checked on a synthetic cube (`tests/fixtures/quad_box.glb`), not yet
  on a Blender-written QuadriFlow mesh: the laptop's first quad remesh is the real check.
- Checks: `tests/test_mesh_checks.py` (a folded box, a shell in a shell, half sunk, the tube's rings),
  `tests/test_parts.py` (a ring's middle), the smoke test `Wireframe: a quad mesh…`.

### Agent findings 2026-10-13 (bollard): 9, 10 and 11, and a generated model stands upright by default

As built (`bollard-fixes`), from the owner's MCP agent's BY04 street bollard:
- **10, a doubled shell.** Clean's rebuild (CuMesh narrow-band dual contouring) works on an *unsigned* distance,
  so a solid comes back as two shells a voxel either side of its surface, 0.2 voxel apart after `project_back` 0.9.
  `drop_enclosed_parts` removes the inner one, but the projection makes it poke through the outer by a hair
  (bollard: 2 of 2 004 probes, 0.15 mm at a 2 mm voxel, in 2 of 4 runs), so it was kept as a crossing part; remesh
  then split 2454 faces over two coincident shells (`ao_enclosed` 0.32). `drop_enclosed_parts(m, near=voxel)` now
  counts a probe within one rebuild voxel of the rest's surface as inside (two separate surfaces that close would
  have merged in the band), smallest part first, so the inner shell goes; `metrics.enclosed_dropped`. Bollard,
  3 of 3 runs: 1 part; remesh 2500 → 2392 faces, one shell; bake `ao_enclosed` 0.0; validate all pass. On six
  multi-part library cleans the guard would drop only pieces of 0.5-8 cm lying 90-100 % inside the body (poking
  out by at most 0.5 mm); the lamp column's, cat's and shark's parts stay. Remesh is unchanged (its input is now
  one shell); a chain cleaned before this keeps its copy until clean is re-run.
- **9, a leaning post.** Straighten's floor rule needs a downward hull plane covering 60 % of the footprint's
  rectangle; a 0.24 m foot under a 1 m post leaning 11° has a 0.41 m plan. Without such a floor `_shaft` fits a line
  through the box centres of ten horizontal slabs over the middle third of the height and stands the model on it
  when it leans 3-45° and is then over twice as tall as wide. Bollard: tilt 0.0 → 10.9, 0.24 × 1.0 × 0.24 m, the
  bake's white band in the same height bands as before. The fence, both walls, the hoarding and the lamp column
  (an arm at the top) straighten exactly as before.
- **Upright by default** (the owner, 2026-10-13: "most all the models generated come out in a tilted way, but for
  asset usage it would be better if the placement would be like rectangular to the ground").
  - **Why they tilt**: a Faithful model keeps the picture's camera, which looks down 10–15°.
  - **`level_transform`** is straighten's levelling on its own: no turn about the up axis. The squaring turned a chair
    90° and characters by 4–18°.
  - **The floor**: the downward hull faces within 8° of the area-weighted normal, refined 3 times. Before, exact
    normals split a generated, noisy floor into slivers.
  - **The 60 % rule**: it now needs 35 % of the footprint as it stands once levelled. Measured on the tilted model,
    a barrel's footprint took in its leaning top: its round floor covered 53 %, a drone's feet under its rotors 39 %.
  - **The survey** of the library's 78 generated models (`clean.level_transform` on each first clean, silhouettes
    before and after):
    - Before: 2 of the tilted ones levelled.
    - After: 24 levelled, every one right by eye. That is all the Faithful props (anvils, barrels, chairs, chests,
      crates and drones at 9–19°, lanterns on their shaft at 6–7°), plus the coiled snake 22°, the mage, robot and
      cat 5–11°, and the brick wall 9° (its lean, not its taper).
    - The timber fence and the 54 upright models are untouched.
  - **`clean` `level: "auto"`** (default) levels a model with a `trellis2` version up its chain (`_generated`). An
    imported one keeps its placement. Metrics `levelled`.
  - **Prepare (UI)**:
    - "Stand it upright on the ground" is on for a generated model.
    - "Also turn it square to the axes" is the old straighten.
  - **Live**: the bench barrel, its tilted generated model through Prepare in the app. Clean levelled it 9.9°, and the
    bake's hoops are level in front and side renders, the texture in place (the test versions deleted).
- **11, a headless transform step** (`steps/transform.py`, light, CPU).
  - **Input**: a still mesh version.
  - **Params**: `level`, `square`, `face` (findings 22), `rotate_deg` [x, y, z] (X then Y then Z), `scale` (a number
    or [x, y, z]) or `height_m`,
    `ground` (default true:
    the base centre onto the origin), `translate_m`.
  - **What it rewrites**: every `.glb` output in place (`_glb.rotate`, now with a uniform `scale`: positions moved,
    normals and tangents turned). UVs, materials and textures stay as they are, pictures and reports are copied, and
    no volume.
  - **Metrics**:
    - `transform`, the 4×4 back to the parent's frame (as clean's), so a later bake finds and follows the
      generator's volume,
    - `size_m`, `scale`, `levelled`.
  - **Refused**: a rigged or multi-mesh file ("use Blender"), and bad params.
  - **History label**: "Moved model". **Where to run it**: Advanced, run any step: "Transform: stand up, turn, scale,
    place".
  - **Tests**: `test_transform.py` (the matrix as bake composes it, normals unit, UVs and textures as they were, the
    ground, a tilted box stood up, the refusals).
  - **Live**: the barrel's tilted unwrapped version, `transform {level, height_m: 1.2}` in the app (1.2 s), then
    bake. Upright at 1.2 m, the hoops level, the texture in place.
- **The owner's agent's retest** (its findings 12–18, docs/audit/2026-10-13-agent-hoarding), and what changed:
  - **Retaining wall: fixed** (12.7° → 1.9°, base level).
  - **Bollard: 2° short** (11.0° → 2.1°). The 35 % floor rule now took its foot flange as the floor, and the flange
    is 2° off square to the shaft. `level_transform` takes the floor first (the hull's, else the mesh's). The shaft
    stands a post that has no floor, or refines a floor within 3° of it: 11.0° on the bollard. A floor that disagrees
    wins: the review found that an arm or a staff across a figure's middle third fakes a lean in the slabs, a 6° one
    in the test, and its level floor keeps it untouched.
  - **Barrier block: not levelled** (finding 17). A lifting eye mirrored under it props the hull up into slanted
    facets. The fallback (faces given) takes the mesh's own downward faces near its bottom: the 5th percentile of
    the model's height along the normal, so a few cm of eye below it do not count; the search starts from the
    direction holding the most area. 14.7° on the library's block. Its eye still sticks out below the floor
    (finding 15: pressing it flat is a judgement, left to Blender).
  - **Fence**: its base is now levelled (6.1°: the base line flat, the top's slope the taper `detaper` undoes).
  - **The survey again**: 27 of 78 levelled, every one right by eye. New: the block, a potion bottle at 13.8°, the
    fence's base. The lantern now stands on its shaft at 8.5°.
  - **`transform` (finding 18)**: `scale` takes [x, y, z] too, after the turn (the hoarding's x × 0.77, the block's
    0.96 / 0.97 / 1.10). Under it normals take the inverse scale and tangents the scale, both made unit again
    (`_glb.rotate`). Region edits (the lamp's head narrowed, the wall's back pressed flat) stay Blender's.
  - **The brief (findings 12, 14)**: the `prop` and `creature` templates add "the camera level at mid height" as a
    phrase of its own. A concept shot from above makes a model that leans; with that phrase the agent's lamp came
    out upright and the wall level. A prompt that names its own view or camera ("view", "elevation", "camera",
    "above") drops the template's view and camera.
  - **`create_asset` (finding 16)**: its description says the id is the name cut at 36 characters (`_2` when taken).
  - **Review fixes**:
    - `transform` refuses a version holding a `skeleton`, `markers` or `parts`: they would no longer fit the moved
      mesh. It carries only the meshes and the pictures, and names the rest in `metrics.dropped` (reports, a volume,
      quads.obj).
    - clean checks `level` before its rebuild.
    - `_generated` reads the generator's volume (`ovoxel`) up the chain, as the UI's `generated` does, so a
      retexture of an imported mesh is not levelled.
    - The brief's own view or camera is matched by whole words and phrases: "an overview map", "a security camera"
      and "a sign above a door" keep the template's camera.
    - The survey again after the floor-first order: the same 27, the same angles.
  - **Finding 19 (15 again), the block stood on its loop**: the mirrored lifting eye hung 3.8–5.2 cm below its base,
    and grounding put the loop on the floor.
    - clean `press_to_floor` (opt-in; Prepare: "Press what hangs just under its base onto it"; `press_to_floor`
      in clean) finds the base: the faces facing down within 8° near the bottom, at their area-weighted height,
      covering 35 % of the footprint.
    - It presses up what hangs below the base's own unevenness (more than 0.5 % of the height or twice its
      spread), at most a tenth of the height. The part must be small (its plan at most a quarter of the footprint)
      and under the middle (within 60 % of the base's radius): a chair's legs, a rim round an outline and a lamp's
      pedestal edge stay.
    - On all 82 cleans in the library it would press 2: the block (581 vertices, 5.0 cm) and a barrel's sagging
      bottom (1.4 cm, flat after).
    - Live in the app: the block levelled 14.7°, pressed 5.5 cm, standing on its whole base (deleted after).
    - Tests: a block on a loop pressed, a rimmed drum and a chair untouched.
  - **MCP**: the version labels match the UI's (`transform`: Moved model, `import_motion`: Animation from a file),
    with a test that keeps them in step.
  - **Findings 20–22 (the raised bed, the bench)**:
    - **20, "straighten skips the new levelling"**: it does not. `straighten_transform` levels with
      `level_transform(v, f)` first; on the bed nothing was found (21), and straighten reports its `tilt_deg` 0 under
      `straightened`.
    - **21, a box rolled past the floors' window**: the bed's prompt asked "from slightly above", it came back
      rolled 30.5°, past the 30° the floors search, its underside never seen and a cane on top.
      - `_box_up` is the last fallback: face normals (area-weighted, |cos| > 0.95) round three axes at right angles
        holding half the area, the frame nearest all three by their areas (an SVD). Anchored on the largest group,
        the soil, it gave 25.5°; the boards say 30.5°. The axis nearest +Y stands it up.
      - The bed: 30.2° (live in the app, a plain clean: 30.3°, one part). The survey: the same 27 plus the bed.
    - **22, `transform` takes the turns**:
      - `square: true` turns the plan square to the axes by the smallest turn (clean's `_square`), the front (the
        camera's side) then facing +Z.
      - `face: "+z"|"-z"|"+x"|"-x"` turns that front to the axis (the turn found by where +Z lands, not by a sign
        convention).
      - Live, the bench's bake with `{square, face: "-z", height_m: 0.9}`: 1.9145 × 0.9 × 0.733 m, its vertices
        0.7 cm (median) from the agent's hand-computed `rotate_deg [0, −161.52, 0]`.
    - **Tests**: a box rolled 35° stood on its axes, a ball no box (`test_mesh_checks` 30); square and face to each
      of four axes (`test_transform` 5).
  - **Finding 19 (the SR10 bottle bank), two defaults for a generated prop** (`loose-parts`):
    - **A loose flap**: a 13 cm rubber flap (756 faces, 0.21 % of the surface) hung clear of the bank's front as a
      part of its own; `min_component` 0.005 took it. The agent's rule, "under 0.5 % of the surface when the rest is
      one solid", would also take what the library's cleans hold on purpose: a crate's corner trim (0.16 %), a
      figure's face piece (0.20 %), a lamp column's collar ring and a lantern's flame (0.2 %, floating 1–3 % of the
      diagonal off the frame). `clean.drop_floating` adds two tests: over 1 % of the diagonal clear of the body
      (touching pieces measure under 0.15 %; the flap 8.9 %) and its middle outside the body's convex hull (a flame,
      a ring, an inner piece stay). The agent's "one solid" guard stays: a drone's rotor blades lie outside the
      hull beside several large parts. Default `"auto"` (a generated model), before the levelling.
    - Of the library's 123 generated cleans it takes the flap, two scraps beside an anime figure's hands and one
      speck (14–54 faces) off three others. Live (the bank's trellis2 version, defaults): one part, 738 faces
      dropped, 6.9° levelled as before, 25 s.
    - **A folded remesh**: remesh 3000 left 17.5 % folded through itself; `close` 0.012 (length / 150) took it to
      0. The library's tri remeshes without `close`: 23 of 58 measured over 5 %, most of them figures (5–11 %,
      the knight 80 %), where 1.2 cm would join fingers or an arm to the side. So only a prop (the chain's brief) is
      done again: the bank, the fence (twice) and the wall, the four the agent closed. Live: the bank 19.8 → 0.0 %
      in 15 s, slot and holes kept; the wall 14.2 → 0.0 %, watertight, 0 tunnels, the brick relief softer (the
      normal map keeps it).
    - **Review fixes**: the second remesh runs beside the first and replaces it only when it folds less; a failure
      keeps the first and says why (`closed.kept`, `closed.error`). The gap is measured to the body's surface
      (its vertices plus samples every 0.5 % of the diagonal), not its vertices alone: on a coarse model a piece
      sunk into a large face is near none of them. A flat body (no hull) drops nothing.
    - **Finding 6** (same audit): a refused `close` names the range, "use 0.0178 to 0.71" for the 3.55 m fence.
      The UI's help said length / 400; the gate is length / 200.
    - **Tests** (`test_mesh_checks` 33): a ring's outside flap dropped, a touching piece and a flame in its middle
      kept, two rings keep all, a flat sheet and a coarse box with a sunk chip drop nothing; clean's `"auto"` on a
      generated chain and off for an upload; remesh redoes a folded prop once and keeps the better (the second
      folding more or failing leaves the first), `close: 0` and a character's chain do not, and the refusal's range.
  - **The hoarding's findings 1, 2, 3, 7 and 8** (`hoarding-fixes`; 4, starting Blender, and 5, the default port,
    wait for the owner):
    - **8, `close_in_blender` without a version**: `blender_client.run_python` bound its arguments with `json.dumps`,
      so a version left out arrived as `null`, a NameError in Blender; a pull without a version too (and a bool
      would have been `true`). Now `repr`, Python's literals.
    - **1, detaper made a wedge**: it scaled the depth with the height, `H / h(x)`; the reconstruction had the
      depth even (0.16–0.18 m) and the short end came out 0.224 m. The depth is now left as it is. Live, the
      hoarding's 007_uv baked again with `detaper`: depth 0.180 / 0.156 / 0.165 m at the ends and the middle,
      the remesh's own; the height levelled as before.
    - **2, nothing compared the result with the brief's size**: `brief.dims` reads "N m|cm tall|wide|long|deep"
      from the prompt; validate's `size_drift_pct` holds the height and the plan's long side against it (the
      thin side reported only: the hoarding's "0.3 m deep" was a room, the panel 0.19 m). Read from the chain's
      first version (`_common.root_meta`, which remesh's prop test now uses), so it works on chains made before.
      On the library: the hoarding's long version 29.5 % (fitted: 0.0), the lamp before its head was narrowed
      77 %, the barrier block 40 % (0.6 m for 1.0 m), the bottle bank 44 % (0.78 m deep for 1.4 m, unnoticed);
      the fence, walls, bench and bollard pass. The raised bed reads 410 %: its canes stand 2 m, the prompt's
      0.4 m is the box's.
    - **3, one refine prompt for every view**: a view in `views` takes its own `prompt`; the guide says to
      name the material, not the place ("construction site" drew a scene into the paint).
    - **7, invented marks on unseen sides**: a guide note (look at the back; redraw it with a plain-material
      view prompt).
    - **Tests**: `test_blender_client` (the header as Python literals), `test_mesh_checks` (detaper keeps the
      depth; the size check warn / pass / absent), `test_refine` (a view's own prompt, a blank one refused).
  - **4, start Blender** (`blender-start`, the owner: "make an option for the user to start blender
    automatically"): Setup's "Start Blender" section, a checkbox "Start Blender when it is needed" and the user's
    `blender.exe` (filled in from the connected Blender's `bpy.app.binary_path`; the bundled 5.2.2 has no add-on in
    its config). `blender_client.ensure` (Open in Blender, the API's and the agent's): when no add-on answers and the
    option is on, the Blender is started detached (it stays open with the app's window gone) with `--python-expr`
    `START`: a timer that calls `bmpro.start_server`, else `blendermcp.start_server`, each second for a minute while
    the add-ons load; then waits for the port up to 90 s (504 `blender_timeout`). One Blender at a time: the process
    it started is kept, and while it runs a second Open, or an agent's, waits for it instead of starting another; one
    that exits early is said at once (502 `blender_start_failed` with its exit code, as a launch that fails). The
    path must be `blender.exe` itself (`blender-launcher.exe` exits at once). Not detected: a Blender the user opened
    with its server off (a second one starts; no psutil for a process list). The panel's Open stays enabled while the option is on ("Starting
    Blender…"); the refusal without it names the option. Live, 2026-10-13, the owner's Blender closed:
    `open_version` on the bollard's 006_remesh started D:\APPS\Blender\blender.exe (5.1), MCP Pro's server came up
    and the model was in the scene 9.4 s after the call; Blender stayed open after the calling process ended (the
    test collection closed again). Tests:
    `test_blender_client` (off: the refusal names the option; on: two Opens at once start one Blender with the add-on
    script and both go on; the path's checks; the status's `start`), the smoke test "Setup: Blender starts when it is
    needed" (the section saves, the panel opens with none running).
  - **5, the default port**: stays 8000 (the owner, 2026-10-13); Setup's App address changes it.
- **Tests (9–11 and upright)**:
  - `test_mesh_checks.py`: a tilted barrel and drone levelled, an upright one untouched, `_generated`, a block on
    an eye under it (the hull alone: no floor; with its faces 14°).
  - `test_transform.py`: a per-axis scale, normals unit.
  - `test_brief.py`: the level camera, and the prompt's own camera.
  - 27 + 4 + 2 pass.

### UX review 2026-10-12 (`docs/ux-review/2026-10-12/`)

- **Pass 1 (2026-10-12, `ux-pass1`; UX-01..04, UX-08)**: unsaved editor work is guarded (`ui/src/hooks.ts`
  `useUnsaved` + `leave`: the animation, skeleton and marker editors register while they hold changes and no save
  runs; Close/Cancel, a `navigate`, an outside hash change (reverted with `replaceState`) and a reload ask first);
  History and Licences are one docked `region` in a grid column of `.ws-body.with-side` (no overlay, nothing
  covered, Escape on the window closes it, the trigger gets the focus back); the panel's grids shrink
  (`grid-auto-columns: minmax(0, 1fr)`); the editor's timeline is `flex: 0 1 280px` (min 150, max 50 %), the canvas
  keeps 180 px, the transport wraps, compact screens scroll. Browser regressions at the end of `smoke.spec.ts`;
  the overflow one fails by 81 px without the CSS.
  Follow-ups after review (`ux-pass1-fixes`): the editor's 180 px minimum applies to the 3D view only
  (`.preview.editing > canvas`; it stretched the timeline's 24 px ruler to 180 px and put clicks off the keys); a
  result that arrives while an editor holds unsaved work is offered with View result instead of opened (`show`
  checks `unsaved()`: it asked out of nowhere and keeping the edits dropped the result). The docked panel stays
  `min(360px, 36%)` between 900 and 1199 px: narrower cut History's labels to one letter; the view keeps 272 px.
- **Pass 4 (2026-10-12, `ux-pass4`; UX-13, UX-14, UX-15)**: text fields, selects and text areas get the `--field`
  outline (3:1 or better against the page, a panel and their fill); file errors are alerts tied to their field
  (`aria-invalid`, `aria-describedby`); "Skip to the content" focuses `<main id="main">`; the asset name is the h1,
  History and Licences h2; the enlargeable pictures are toggle buttons. The editor's `KeyList` (`anim/DopeSheet.tsx`)
  reaches every key from the keyboard with the timeline's own callbacks (select and seek, `onMove(±1)`, `onCurve`).
  Library cards keep management in a `details` "More"; "Select assets" / "Done selecting"; `library.trash()` lists
  `_trash/<when>_<asset>` (a whole asset) and `_trash/<when>_<asset>_<first version>` (a branch), `library.restore`
  moves one back under `_alloc` (which `create_asset` now takes too) with `os.rename`, whole or not at all (a file in
  use: 409 and a branch's moved versions go back), refuses a taken id or number or a missing asset or parent (409),
  and skips storage's half-deleted `<id>_deleted` leftovers; the app's
  "Recently deleted" uses `GET /api/trash` and `POST /api/trash/{name}/restore` (which publishes `version_created` for a
  branch). A task that is not reachable is `aria-disabled` with its reason as its description, so it keeps the focus.
- **Pass 3 (2026-10-12, `ux-pass3`; UX-10, UX-11, UX-12)**: the export result saves the whole package into a folder
  through `/api/export/copy` (`SaveExport`, the main action for a several-file export, the folder remembered on this
  computer) and says in one sentence how the destination imports it (`EXPORTS[].next`); the batch export lists what
  each asset will export before it runs. The library's view is in the address (`#/?q=&sort=&filter=&tag=`, replaced,
  not pushed) and remembered with its scroll position; Clear filters; the sort is "Recently changed". Setup: cards for
  the standing pose, the skeleton, learned skinning and the ready-made animations; `#/setup?cap=<group>` (parse's
  `cap`) lands on a card, which every blocked action's link uses; one Hugging Face login (`HfLogin`) for Get started and
  the gated cards; the summary says what the guided setup's sets are ready for and the rest is optional.
- **Pass 2 (2026-10-12, `ux-pass2`; UX-05, UX-06, UX-07, UX-09, UX-15; design and as-built notes in
  `docs/ux-review/2026-10-12/PASS2-NAVIGATION.md`)**: the row shows five tasks (`workflow.ts` `TASKS_3D` Reference ·
  Generate · Prepare · Character · Finish, `taskOf`; `TASKS_2D` for `asset.json.kind === "2d"`, its panels with M50–M58)
  instead of fourteen steps, an "Imported model" chip for imports, and a Tools ▾ menu (Face, Texture detail, Parts, Edit
  in Blender, Advanced) whose entries open their section of the panel; a task button opens a checkpoint or the task's
  form, never runs a step, and is disabled only with a "Needs …" title. `Actions` leads every checkpoint with its main
  action (Check, then Export, before the tools and the rig form; Export first on a checked mesh; the animation source
  as radios, ready-made by default) and computes a clean/simplify/unwrap/bake checkpoint's state with `prepState`
  (completed · active · interrupted · historical) so a checked chain never says it stopped. Readiness: a known-missing
  capability makes the setup link the main action (concepts, the generator), a restricted export is disabled. Copy:
  "Attach to skeleton", "Remove baked shadows", the specialist fields under Advanced with visible help, the long help
  under Help. Checks: `ui/tests/workflow.spec.ts` (the rules on fixture lineages), four browser tests in `smoke.spec.ts`.

---

### The 2D track (M50–M58; research 15 §8)

Pictures, pixel art, sprite sheets from the 3D characters, parallax layers, tiles, an in-app pixel editor and
2D exports for Godot; everything local, no video models, no external editor (owner, 2026-10-12). The §3.2 rows
above are the contract; each milestone's "As built" note lands here.

- **8.0 Contract** (2026-10-12): the §3.2 rows for `concepts` (`image`, `tile`), `pixelize`, `sprites`,
  `layers`, the 2D `validate` items and export profiles; `asset.json.kind` (`server/library.create_asset`,
  `POST /api/assets`, MCP `create_asset(kind=)` and `import_file(kind=)`: a 2D picture is only uploaded, no
  `prep`). No step, no UI (the UX audit runs; the 2D tabs come after it).
- **M51 Pixelize** (2026-10-12, as built): `steps/pixelize.py` (`LIGHT`; the §3.2 row's params; output keys
  `frames`, `sheet`, `data` (= `sheet.json`), `palette`, `preview`, `report`, `image` for one frame),
  `steps/_pixel.py` (the grid from the autocorrelation of the column and row edge profiles, summed over the
  frames: the smallest lag that is a local maximum near the best, peak ≥ 0.3 (`MIN_PEAK`, a knob), the cell
  offset from where the edges sit; `dominant`/`median` per cell with transparent padding for partial cells;
  a median-cut palette over a sample of every frame's opaque pixels; nearest colour by CIEDE2000 (sRGB → Lab,
  `_pixel.delta_e00`, after img2threejs's `color_metrics`; `2d-lab-palette`, 2026-10-12) in row bands; Bayer 2/4
  with a spread of one palette step; a 4-connected 1-px outline; exact recolours; `.gpl`/`.hex`/`.pal`
  readers, a `.gpl` writer), `steps/_sheet.py` (uniform cells, bottom-centred, a near-square grid; Aseprite
  json-array; `contact`: the largest whole zoom under 1024 px, nearest), `steps/palettes/pico-8.gpl` (CC0,
  `licenses.yaml`; a step that uses it lists the row). `server/thumbs.PIXEL_STEPS` (`pixelize`, `sprites`):
  their thumbnails and `look` zoom by whole numbers and shrink nearest; `look` shows a version's own
  `preview` alone when there are no candidates. No UI panel yet (runnable through the API and MCP:
  `mcp/SKILL.md` "2D"). Tests `tests/test_pixelize.py` (the grid of a ×6 picture off the grid by (3, 2)
  recovered exactly, no grid on noise or a ramp, one palette over frames, a still dither, outline and swap,
  the palette files, the sheet round trip, the step on a picture, a frames folder and a sheet), `test_thumbs.py`
  (a crisp pixel thumbnail). Open: a bench on real Z-Image `lora: pixel` output (the grid detector was built on
  synthetic pictures), the `edit` op (M56). The bench ran with M50 (`docs/bench/m50.md`): the grid found on a
  pixel-style close-up (8, peak 0.80) and one full figure (7, 0.36), missed on another (0.19); `MIN_PEAK` kept.
  After it: `key` (one picture of a set), `palette: "input"` (a recolour on the input's own palette: nothing
  re-quantised), and the nearest colour by CIEDE2000 (`2d-lab-palette`, merged the same day).
- **M50 Pictures** (2026-10-12, as built; `docs/bench/m50.md`): `steps/concepts.py` op `image` (the §3.2 row:
  `prompt` or the parent brief's `prompt_full`, `width`/`height` multiples of 64 from 256 to 2048, `count` 1–4,
  `seed`, `steps`, `lora` one of `STYLES` (`pixel`, `painting`, `drawing`: Apache-2.0 LoRAs pinned in group
  `styles`, each with its trigger prepended), `lora_scale` (0, 2], `cutout`); no gates; the VAE decodes in tiles
  above 1024 px; the fp8-stored Linears carry `compute_dtype = bf16` so peft keeps a LoRA in bf16; the style is
  unloaded in a `finally`). `peft` in the `ml` group. `steps/brief.py`: 2D kinds `wallpaper`, `background`,
  `portrait`, `icon`, `item`. `server/jobs._check_outputs`: an output may be a non-empty folder (`frames`).
  `VRAM_GB["concepts"]` 8.5. UI: New asset's "A 2D picture or sprite" (from a description: the kind, a size
  preset, 1–4 pictures, a style, a cut-out → `brief` + `concepts` op `image`; from a file: uploaded as it is);
  a 2D asset (`asset.kind`, carried in the selection with its versions) gets `ui/src/pixel/View.tsx` (a picture,
  a picture set with a strip to pick from, a sprite sheet played at its own durations with its tags; Fit or a
  whole-number zoom, nearest, a checkerboard) and `ui/src/pixel/Panels.tsx` (`Actions2D`: more pictures from the
  description, Make pixel art (`PixelizeForm`: the grid, a height or a pixel size; colours or PICO-8; dither;
  outline; advanced method and alpha cut), the result's palette and a recolour, the files to download); Setup's
  "2D styles" card; MCP `SKILL.md` 2D. Tests: `tests/test_concepts_image.py` (a stand-in pipeline: sizes and
  styles refused, the trigger, the LoRA unloaded even on a failure, tiles above 1024, the cut-out's alpha),
  `test_brief.py`, `test_pixelize.py` (`key`, a recolour), `test_api.py` (a folder output); Playwright: a 2D
  asset from a description and from a file, and the 2D workspace (pick, pixelize, play, zoom, recolour). Not
  done: the Layers, Tiles and Edit tasks (M54–M57), the 2D exports (M53).
- **M52 Sprites** (2026-10-12, as built; `docs/bench/m52.md`): `blender_ops/render_sprites.py` (GPL; Workbench,
  orthographic, `render_aa` off, a transparent film; yaw 0 front, 90 facing right, 180 back, 270 left; the camera
  follows the root's straight drift from a clip's first frame to its last (in place, the sway kept) and holds the
  ground under the root a few pixels above the cell's bottom; pass 1 measures every frame's screen extent so one cell
  fits the sheet; the scale from `height_px` over the rest pose's height, a flat model by its largest side; studio
  light at +0.5 stops, `flat` unlit; frames resampled to `fps` by subframes) and `steps/sprites.py` (`LIGHT`; the
  §3.2 row's params but `toon`, `outline`, `shadow` and `normal_maps`, left for later; the clips checked against
  `clips.json`; `camera.json` reused by later runs on the asset; one tag per clip and yaw; `frames/`, `sheet.png`,
  `sheet.json`, `camera.json`, `preview.png`). UI: a Sprites tool (the Tools menu's "Sprites for 2D") on an animated
  or a prepared still version (`workflow.spritesFrom`): the clips, the views, the height, fps, the look, advanced
  cell and tilt; a sprites version in a 3D asset gets the pixel viewer and `Actions2D` (its summary, the files, Make
  pixel art starting at scale 1, 15 colours, an outline), both under the Character task. Tests:
  `tests/test_sprites.py` (refusals; with Blender: uniform cells, binary alpha, a transparent border, the tags, the
  scale kept and reset, a still model per yaw), `ui/tests/workflow.spec.ts`, a Playwright test (the form posts the
  params, the sheet plays, pixel art's defaults). Measured: the viking's five clips (two from Kimodo text) as 126
  frames in 3.5 s.
- **M53 Godot 2D and Paper2D** (2026-10-12, as built; `docs/bench/engine-import.md`, its 2D section):
  `steps/_engine2d.py` (the text writers: `godot_sprite_frames`, `godot_scene` with the feet row found over every
  frame, `paper2d`, the READMEs, and the 2D `checklist`), `steps/export.py` profiles `godot-2d` and `unreal-2d` for a
  version with `sheet` + `data`, `steps/validate.py` the 2D checklist (`uniform_cells`, `hard_alpha`, `palette` ≤
  `max_colors` 32, `stray_pixels`, `sheet_size` ≤ 4096, `animations`). The sprites step marks a clip that ends away
  from its first pose (more than 0.2 % of the height, the vertices against each other) as played once (`repeat`
  "1"; the Mr. Mak review's loop flag): the pack's clips end within 0.02 %, Kimodo's moves 0.8–31 %. UI: a sheet's
  Finish (`Finish2D`: Check sprites, then the 3D export panel with `EXPORTS_2D`), a sheet's check or export shown with
  its sheet, the 2D checks' names and fixes; MCP `export` takes the 2D profiles. Tests: `tests/test_export_2d.py`,
  `test_sprites.py` (the loop flag), a Playwright test. Godot 4.7.1 imports and plays the viking's sheet headless;
  Unreal's Paper2D importer needs the editor (the owner's check).
- **M54 Tiles and props** (2026-10-13, as built; `docs/bench/m54.md`).
  - **`concepts` op `tile`**:
    - `size` 256–1024; `mend` 0.3–1, default 0.8.
    - The picture as op `image` makes it, rolled by half, the cross (an eighth of the size) drawn again.
      `ZImageInpaintPipeline(**pipe.components)` runs on the resident modules, never `from_pipe`: that casts to
      float32 by default, the fp8 DiT to 24 GB, an OOM.
    - The band's colours are matched to the picture's there, then it is feathered and rolled back.
    - Outputs `tile.png` (also `image`), a 2 × 2 `preview.png` and `report.json`.
    - On the bench, 1.0 drew the band afresh: a darker frame round every tile, larger stones in a cobbled floor.
  - **`brief` category `tile`**: "a flat lay seen straight from above, uniform scale…". "Flat straight-on view"
    drew the floor in perspective.
  - **`_pixel.seam_ratio`**: the step across the wrap over the strongest step between neighbouring lines inside.
    - A 90th-percentile baseline flagged every brick or cobble tile: their own mortar lines reach 1.3–1.9 times it.
    - Pass ≤ 1.1: a cut-out piece scores 3.6, a mended cobble floor 0.86 (1.47 before).
  - **`pixelize` on a tile**: the grid is rolled round the edge, never padded. Its size divides the tile's
    (`height_px` is the tile's size in pixels; a size that does not divide is refused with the list). The `tile`
    key is kept, with a 2 × 2 preview and `seam_ratio`.
  - **`validate`**: `seamless` for a raw or pixelized tile.
  - **`export`**: `godot-2d` writes `<asset>.png` + `<asset>.tres`, a `TileSet` with the tile as its one atlas
    tile. `unreal-2d` writes the texture and the README's Create Tile Set.
  - **`CHAINS["game-prop-2d"]`**: prep → … → bake → sprites (yaw 90, 64 px) → pixelize.
  - **UI**:
    - New asset's kind "Seamless tile" (256 / 512 / 1024, one picture, no cut-out).
    - A Custom picture size: width × height, snapped to 64 between 256 and 2048 on leaving the field (the owner,
      2026-10-13).
    - The viewer lays a tile 3 × 3 (or one); the panel says whether its edges meet.
    - Pixel art in whole tiles: the sizes that divide it, default an eighth, the middle colour unless the tile
      was drawn pixel-style. A 512 tile's bricks melted at 32 px with the most frequent colour, and stayed bricks
      at 64 px with the middle one.
    - The tile's own check and export (`View.is2D`).
  - **MCP**: `SKILL.md` has the tile and the 2D prop chain.
  - **Tests**: `tests/test_tiles.py` and two Playwright tests.
  - **Godot 4.7.1** loads the `TileSet` headless, and a `TileMapLayer` painted 20 × 6 saves and loads back with
    120 cells.
- **M55 Parallax layers** (2026-10-13, as built; `docs/bench/m55.md`).
  - **Depth model**: MoGe-3 ViT-g (MIT, 5.0 GB, the model group `layers`). The owner asked for the best quality that
    stays commercial. Out: Depth Anything 3 Large/Giant (CC-BY-NC), Depth Pro (Apple's research licence), VGGT
    (CC-BY-NC). Depth Anything 3 Mono Large is Apache, but its package pulls in xformers, open3d and pycolmap.
    MoGe-3 over MoGe-2 ViT-L (already on disk): cleaner bands, the facades without specks.
  - **`.venv-moge`** (group `moge`): MoGe-3 needs FlexGEMM at b2fadb2 (pure Triton, `flex_gemm.nn`), while
    `.venv-ml` keeps the CUDA wheel TRELLIS.2 runs on. Hence its own venv, a per-group `flex-gemm` source, and
    MoGe's metadata without its demo app (gradio). `.venv-ml` is unchanged (`uv sync --dry-run`: no changes).
  - **Two steps, one venv each**: `depth` in `.venv-moge` (the picture → `depth.npz`), `layers` in `.venv-ml` (bands,
    Z-Image's inpaint). The UI chains them.
  - **What the bench taught**:
    - Equal-share bands cut a receding road or facade into stripes, so the bands are k-means clusters on the
      disparity.
    - The inpaint at strength 1 with "the empty scenery behind" drew a photographic street behind a painted one.
      Now: an OpenCV fill of the layer's own colours, redrawn at 0.8 with the brief's `prompt_full` (its kind's look).
  - **The owner's check**:
    - A car parked along the street was cut in two, its roof across a cut. The cuts now follow the depth's edges: a
      watershed near each cut (`MARGIN` 0.35 of the gap) on the disparity's gradient.
    - What the nearer bands hide is drawn with ", empty ground, nothing standing on it" (`EMPTY`). Without it, the
      description's cars came back behind.
    - Left open: a standing object's contact (a car's tyres) goes with the ground. Object masks prompted at the cuts
      would fix it; SAM 2.1's automatic masks missed the car.
  - **Export**: `godot-2d` writes `parallax.tscn`. Godot 4.7.1 headless: with the camera moved 1000 px, the four
    layers move 50 / 281 / 536 / 1000 px.
  - **Validate**: `layers` and `far_layer_whole`.
  - **UI**:
    - "Parallax layers" on a picture: open for a background or a wallpaper, a disclosure for the rest. It holds the
      count, a figure as its own layer, and Make parallax layers (depth, then layers).
    - A depth version shows Picture or Depth.
    - A layers version scrolls in the view (a pretend camera, Play/Pause, a Camera range), or Show picks one layer
      alone or the Bands; the
      panel lists each layer's speed and file, its check and export, and Make the layers again.
    - Setup's Parallax layers card.
  - **Tests**: `tests/test_layers.py` (the leaning car among them), one Playwright test.
  - **The Picture kind** (the owner, 2026-10-13): New asset's 2D kinds start with Picture, the description as it is,
    `brief` category `picture` with nothing added. Wallpaper is one of the kinds, no longer the default.
- **M56 The pixel editor, part 1** (2026-10-13, as built).
  - **`pixelize` op `edit`** (`_edit`): the frames the editor changed, as base64 PNGs, over the input sheet's (only
    those travel: job params land in `meta.json`, which the library loads for every version).
    - Refused (`bad_params`): an unknown frame, another cell size, a colour outside the palette (the input's
      `palette.gpl`, or one sent), something not a PNG.
    - The tags, durations and palette stay the input's unless sent.
    - A sprites sheet without a palette takes any colour; it gets a palette file when it has 256 colours or fewer.
    - Outputs as op run, through the shared `_write`; a tile stays a tile. Report `{edited_frames, changed_pixels,
      colors, frames, cell}`.
  - **UI**:
    - `ui/src/pixel/tools.ts`: pure functions on a frame's bytes. Pencil (Bresenham, pixel-perfect: L-corners
      dropped), line, rectangle and ellipse (outline or filled), fill (touching area or every pixel of the colour),
      a 1-px outline, and copy / clear / paste / flip / turn of a selection.
    - `Editor.tsx`: `usePixelEditor`, held by the workspace. `EditorCanvas` takes the view's place (whole-pixel
      zoom up to 4096 px of canvas, a grid from 8×, the selection dashed); `EditorPanel` takes the panel's (tools
      with Aseprite's letters, mirror, filled, the palette's colours or a colour input, the selection's actions,
      Undo/Redo and Ctrl+Z/Y, the frame with , and ., Save, Close).
    - One stroke is one undo step (`useHistory` over the frames' buffers, the unchanged ones shared). Save closes
      the editor on the new version, a refused save keeps the edits (UI-001), and unsaved edits ask before leaving
      (UX-01).
    - Opened from a pixel art or sprites version (Edit the pixels) or the Edit task.
  - **Tests**: `test_pixelize.py` (the edit op), two Playwright tests. One of them draws a line and compares the
    saved PNG with the original pixel for pixel: exactly the line's 8 pixels.
  - **Live**: the bench barrel's 128 px pixel art edited in a browser against the app. The new version differed in
    exactly the 41 pixels of the line, in the palette colour picked, the palette unchanged (deleted after).
  - **Not in v1** (`ponytail:`): layers and blend modes, a lasso, dither and gradient brushes, tile mode, brushes over
    1 px. The frames strip, tags, durations, onion skin and play are M57.
- **M57 The pixel editor, part 2: animation** (2026-10-13, as built).
  - **`pixelize` op `edit`** grows:
    - `order`: add, duplicate, delete, reorder. A new name is a blank frame unless its pixels are sent, a name left out
      is deleted.
    - `tags`: indices into the new order. The input's tags are dropped when the order changes and none are sent,
      since their indices meant other frames.
    - A sheet with no parent: `size` and `order`, and a palette (a bundled name) or any colour.
    - Refused: names that could leave `frames/`, duplicate names, an empty or duplicate tag name, a tag outside the
      frames, durations that are not whole ms 1–60000.
    - A sheet in any colour stays without a palette file. M56 made one from its colours, which locked the next edit
      to them.
    - A tile with more frames than one is a sheet (`tile` kept only for one frame).
  - **UI**: undo now covers a `Doc` (names, frames, durations, tags), not just the pixels. `Timeline.tsx` holds:
    - The pure ops `insert` (a tag holding the frame grows), `remove` (a one-frame tag goes with it) and `swap` (the
      tags stay on their positions, Aseprite's way).
    - `FramesStrip` under the canvas: thumbnails, durations, a bar over a tag's frames, its name on the first and ↻
      or ⇥ on the last, the loop-point marker.
    - `FramesPanel`: Add a frame, Duplicate, Delete, Move, the shown frame's duration, the animations (name, first
      and last frame, Loop, Remove). A field is one undo step when it is left, and a refused value goes back.
  - **The view's toolbar**: Play (the shown frame's tag, or every frame, each frame for its time; a play-once tag
    stops on its last), the onion skin (1–3 frames each side, red before, blue after, fainter farther), keys 1–5 for
    the zooms.
  - **New asset**: 2D, Draw it yourself (size, frames, PICO-8 or any colour) makes a blank sheet (`001_pixelize`,
    no parent). The workspace opens a blank sheet straight in the editor.
  - Save sends only what changed: the changed or new frames' pixels, the durations that differ, the order and the
    tags when they differ.
  - **Fixed on the way**: a `<div>` row directly in a panel `<details>` is laid out as a grid, so Selection's
    buttons (M56) stood one per line. Both sections wrap their rows now.
  - **Godot**: `export godot-2d` already wrote per-frame durations (M53, `SpriteFrames` relative durations), so
    nothing changed there.
  - **Tests**: `test_pixelize.py` (reorder, duplicate, delete, add, the tags after a new order, a sheet from
    nothing, the refusals). Playwright: add, duplicate and move frames, a duration, a play-once tag, the onion skin,
    a zoom key, play to the end, Save's params; Draw it yourself straight into the editor.
  - **Live** (the running app):
    - A 4-frame 32 px hit spark was drawn from nothing in PICO-8, timed 50/60/70/80 ms, tagged `spark`, play once
      (`m57_hit_spark`, kept as a sample).
    - Exported `godot-2d` and loaded in headless Godot 4.7.1: one animation, 4 frames, `loop` false, 20 fps,
      durations 1/1.2/1.4/1.6, nearest filtering. It finished on its last frame at 0.26 s.
    - The viking's 122-frame `017_sprites` re-timed: one Walk frame 83 → 166 ms. The new version's params were
      that one duration (meta.json 982 bytes), every frame's pixels and the tags identical, saved in 0.6 s (deleted
      after).
  - **Review fixes**:
    - Malformed params are refused, not crashes: a list in `order`, a frame that is not a string, a palette of
      non-strings.
    - A new frame's name must make a Windows file name: no device name (CON, NUL, COM1...), no trailing dot or space,
      distinct ignoring case. The input's own names always pass, so an old sheet can still be re-timed.
    - A one-frame loop keeps playing (play's own clock).
    - The auto-open follows the asset too: version names repeat across assets.
  - **Speed**: the onion skin was drawn at the canvas's size on every pointer move: 70 ms a move (viking 128 px at
    24×, 3 frames each side, headless Chromium). Now it is put together at the frame's size once per frame shown and
    scaled once: 16.5 ms, one screen frame, the same as without it.
  - **Not in v1** (`ponytail:`): drag-and-drop reorder (Move buttons), a duration for a whole tag at once, ping-pong
    and reverse playback (a tag's `direction` plays forward in the editor; `sheet.json` keeps it), cels linked across
    frames.
- **The standing pose from the Generate panel and the user manual** (2026-10-13; the owner: "yes, the button to put
  in standing pose please").
  - **`GeneratePanel`**: **Put it in a standing pose** on a character's cut-out. That is a brief of category
    `character`, or no brief at all (a picture of your own). It does not show for an object, a creature, a
    turnaround (`prep` op `views`) or a cut-out whose parent is already a `canon`, which shows **Redraw again**
    instead.
    - It chains `canon {}` and `prep {}` on the cut-out.
    - Without `canon-flux` or `pose` it links to that Setup card.
    - Before, the standing pose was reachable only through Advanced (and the 2D Animate this character).
    - Playwright: the chain and Redraw again on a character; no button on an object.
  - **`MANUAL.md`**: the user manual, every page, panel and tool, linked from the README, SETUP.md and
    docs/howto.
- **Setup's Advanced section, one way to do each thing** (2026-10-13, the owner: "do we still need the model install
  and remove buttons?").
  - **Advanced**: "Model files and licences" is a reference now: which file each feature uses, its folder, revision,
    size and licence, and every licence. Its "Install <group>" row and Remove column are gone (`api.removeModel`
    too).
    - Install is on the feature cards, which also have Repair and Check and repair.
    - Remove is under Space on disk. `DELETE /api/models/{id}` stays for scripts.
  - **"3D model from four views"** (`multiview`, 22 GB) has a card of its own: it was the one group only that row
    could install, besides Everything.
  - **Space on disk**: a row of files several features share has a Remove too (`group:<g>+<g>`, the files exactly
    those features share; the question names both). It is the only thing the table's Remove did that Space could not:
    Z-Image-Turbo (Concept images and Texture detail), DINOv3 and BiRefNet (Fast and Faithful).
  - **Tests**: `test_storage.py` (a group's own files go and the shared ones stay; the shared row removes them; only
    known groups); Playwright (no buttons in the table, the four-view card).
- **M46c Setup offers what the card fits** (2026-10-13, as built; research 16 §6).
  - **Download group `refine-full`**: the full Tile 2601 8-step file (6.7 GB, same repo, revision and folder as the
    lite one; Apache-2.0). In `tools/download_models.py` and the manifest; licence row updated.
  - **Its need**: `VRAM_GB["refine"]` is keyed on `tile` (lite 12.3, full 15.6: m43b's reserved peak, the run that
    restarted the laptop before the cap).
    - `presets.GROUP_NEED` maps a group to the (step, params) whose need decides: `{"refine-full": ("refine", {"tile":
      "full"})}`.
    - `LOWER["refine"]` adds `tile: lite`.
  - **`server/install`**:
    - `too_big(group)`: why this card cannot use the group, from the idle budget.
    - `install` refuses such a group (409, code `oom`).
    - `presets()` leaves it out of every set, Everything included, so a 16 GB user never downloads 6.7 GB they cannot
      run.
  - **`machine.check`**: a `too_big` entry carries the `group` it needs.
  - **Setup**: a "Texture detail, full" card. On a card that cannot use it: "Needs a bigger card · about 15.6 GB of
    video memory, this one allows 14.7 GB", no Install button.
  - **`refine`**: without a `tile` param it takes the full Tile when its file is there and the budget holds 15.6 GB
    (`_tile`), else lite. Asked for one, it loads that one, and the pre-flight refuses full where it cannot fit.
    Metrics `tile`.
  - **`tools/gpu_report`**: `refine` runs lite on every card, `refine-full` where the budget is ≥ 15.6 GB and the
    group is installed.
  - **Kept for later** (the owner): Qwen-Image-Edit-2511 and HiDream-O1 as `canon` engines, after a bake-off on a
    user's big card.
  - **Tests**:
    - `test_gpu_safeguards.py`: the Tile's two needs, the card layouts, the group on the entry.
    - `test_first_run.py`: Everything without `refine-full` on 16 GB and with it on 24 GB, the install refused.
    - `test_refine.py` (in `.venv-ml`): lite while not downloaded, full on 22.6 GB, lite on 14.66, an asked Tile as
      asked.
    - Playwright: Setup's card on a 16 and a 24 GB card.
- **M46b Bigger presets, the best fit as the default** (2026-10-13, as built; research 16 §6).
  - **Presets** (`server/presets.PRESETS["trellis2"]`, best first; UI menus list them in this order):
    - `faithful-hd`: 1536_cascade, 16 steps, 4K. For 24 GB cards.
    - `faithful-hq`: 20 steps, 4K. Measured to fit 16 GB, about 25 % slower.
    - `faithful` and `fast` as before.
  - **`presets.best(step, gb)`**: the first generate preset whose need fits the budget. `{"preset": "best"}`
    resolves against this card when the job is submitted (`jobs.submit`, the card's idle budget). The
    `hero-character` chain (PLAN §5: canon, the best Faithful, quads 40k, 4K, rig) uses it.
  - **Needs at 1536** (`HD_GB`, by weights; `need_gb` takes the larger):
    - Fast 15.1: a lower bound, the laptop's whole card at 15.5 of 15.9 GB.
    - Faithful 19.4 (est.): Fast's plus Faithful's extra at 1024.
    - Host RAM 28 GB (`ram_need`, est.).
    - `lighter(step, gb, params)` steps a refused 1536 job back to 1024 first, then to a lighter model.
  - **`machine.check`**: `too_big` lists the 3D model by its presets (`param: "preset"`). What the reference card
    (15.92 GB, `REFERENCE_GB`) runs and this one cannot is a warning; more than that is for bigger cards only. So
    the laptop is `ok`, with `faithful-hd` greyed out in the menu.
  - **Compute capability** (`machine.sm()`, `MIN_SM` 8.9; the owner skipped an 8.6 build):
    - Our CUDA wheels (cumesh, flex_gemm, o_voxel) are built for 8.9 and 12.0 with no PTX.
    - An RTX 30 card (3090, A5000, A6000) gets a `warn`: "The 3D model steps are not supported: the 3D models need
      an RTX 40 or 50 series card ... Pictures, pixel art, motion and the rest run". Every preset is greyed out
      with that `reason`.
    - The pre-flight refuses `trellis2`, `clean`, `remesh`, `uv` and `bake` with a new error code, `unsupported`
      (`depth`'s FlexGEMM is the pure-Triton build and runs). `sm()` is read once per app run.
  - **API shape**: `GET /api/setup/machine`'s `too_big` names the 3D model by preset (`param: "preset"`, `value`
    the preset's name), no longer by `weights`. A card the 3D models do not support adds `reason` (and `need_gb` null).
  - **UI (`GeneratePanel`)**:
    - The default is the first preset this card runs, marked "Recommended for this GPU".
    - The user's pick is kept (`localStorage` `effigy.generator`, this browser) unless the card cannot run it.
    - A preset's download is its weights' group.
    - A greyed-out preset says why: its need, or the unsupported reason.
  - **Live on the laptop** (the Playwright server's real machine check): Faithful, high quality is recommended and
    Faithful HD is greyed out ("Needs about 19.4 GB ... this GPU allows 14.6 GB").
  - **Tests**:
    - `test_gpu_safeguards.py`: the card layouts by preset; the best fit on 30.5, 22.6, 14.66 and 10.42 GB and with no
      GPU; 1536's needs and RAM; the step-down; `{"preset": "best"}` on a 24 and a 16 GB card; an RTX 3090 told
      and refused.
    - Playwright: the 16 GB default; a 24 GB card's default; the user's pick kept over a reload; an RTX 30 card's
      reason with Generate disabled.
  - **`tools/bench`**: labels a chain by its preset when it names no weights.
  - **The quality check** (`docs/bench/m46.md` "M46b", `m46b_2k_vs_4k.jpg`):
    - Chest and lantern, 12 steps + 2K against 20 steps + 4K: whole views alike, crisper edges and finer streaks at
      4×. Torch at 9.2 GB in every run; 71–150 s.
    - One 20 + 4K run stalled in xatlas's chart packing (CPU) for 25+ minutes and was cancelled. The same settings
      took 150 s on a second try, and the texture size does not reach the packing.
- **M46a The budget skips the offload** (2026-10-13, as built; research 16 §6).
  - **`steps/_common`**:
    - `budget_gb()` returns this run's budget: `EFFIGY_VRAM_BUDGET_GB`, or a `--serve` request's, as `_cap_vram`
      sets it.
    - `offload(params, gpu_gb)`: params `offload` true or false decides; else offload only on a budget below what
      the whole model needs on the GPU.
  - **`canon`**: `GPU_GB` 17.0 (est.). The FLUX.2 klein pipeline goes on the GPU whole (`pipe.to("cuda")`) instead
    of `enable_model_cpu_offload()`. Metrics `offload`.
    - Measured on the laptop (`docs/bench/m46.md`): offloaded, 9.17 GB torch, 9.8 GB the whole card, 47 s. Whole,
      out of memory under a 12.5 GB cap while loading.
    - Its weights are 14.9 GB (text encoder 7.49, transformer 7.22, VAE 0.16), so 24 GB cards and up. Research 16's
      "~13 GB" was a claim.
  - **`motion`**: `ENCODER_GPU_GB` 18.0, from Kimodo's README "~17GB ... entirely on GPU" (claimed).
    `TEXT_ENCODER_DEVICE` is set per load. Metrics `encoder_device`.
    - `encoder_device`: the resident worker reloads only when it must. A smaller budget moves the encoder off the
      GPU; a worker loaded on the CPU stays there unless a request asks (`offload: false`), so a budget hovering at
      18 GB does not reload it (30–60 s) each time. A reload collects the old model first.
    - On a 24 GB card an idle resident Kimodo with the encoder on the GPU holds ~17 GB until it idles out or the next
      heavy job evicts it.
    - Live on the laptop: CPU as before; the first prompt 57 s with the load (30.8 s on a second try), the next 3.1 s
      (no reload).
  - **`server/presets.LOWER`**: canon and motion suggest `{"offload": true}` after a step's own OOM, and only when the
    run did not have it already (`jobs._run_locked`).
  - **Dropped**: `EFFIGY_PRESET_TIER` (read by nothing).
  - **`tools/gpu_report`**: canon and motion run offloaded on every card. `canon-gpu` and `motion-gpu` (offload off)
    run at a budget ≥ 17 / 18 GB (`NEED`), and each row records `offload` / `encoder_device` and the load or edit
    seconds, so a 24 GB user's report measures both thresholds.
  - **trellis2's `low_vram` stays on**: no card has measured it off yet (research 16 §3.1); a user's report decides.
  - **Tests**: `test_gpu_safeguards.py` (the budget a step reads, the offload decision, LOWER), `test_motion.py` (the
    encoder moves only when it must, one load per device), `test_api.py` (no suggestion an OOM already tried). The
    8 GB card layout
    now expects `depth` and `layers` refused (M55's 9.0 and 7.8 GB). That test had failed since M55, which ran
    only targeted tests.
- **Move sets** (2026-10-12; `docs/bench/m52.md`, "The Double Dragon set"): `workflow.MOVE_SETS` (Brawler, Sword
  fighter, Basic: the pack's names, only those the installed pack has) as the clip picker's Move set, and
  `FIGHT_PROMPTS` (six fighting moves the pack lacks) as buttons that fill the text source's sentence.
  `steps/sprites.ONCE`: a pack attack, hit, fall, jump start or landing, throw plays once though it ends in its start
  pose (Godot's `loop` off). Tests: `test_sprites.py` (the names), a Playwright test (the set and the presets).
- **The Mr. Mak follow-ups and the joint loops** (2026-10-12): `anim` writes `clips.json` `still` (and
  `metrics.still`) for a clip whose every bone turns less than 1° over the clip (`_anim.turns`: a missed bone map;
  real clips turn 8° and more, the quietest of the viking's 21 and the nurse's 3), the animated version's panel names
  them; a GLB the reader cannot read skips the gate. The loop flag is the sprites step's (M53). `_rig.joint_loops`
  judges the elbows and knees only (`BENDS`): no bench rig is flagged now, every elbow and knee has 5–7 bands.
- **Animate this character and the redraw** (2026-10-12; the owner's female viking).
  - **The finding**: a flat picture (pixel art, a drawing) gives TRELLIS no shading to read volume from. Her
    pixel-art picture made a standee 0.45 as deep as wide at the waist, printed front and back.
  - **`canon`**: `look: "render"` (`canon.LOOKS`, put into `PROMPT`'s `{look}`) redraws the picture as a softly lit
    3D render, keeping the clothes' length and footwear and drawing a face. Without those two clauses the
    mannequin's bare legs came through on 4 of 4 seeds of her floor-length dress, and 3 of 9 faces were blank. The
    default prompt is unchanged. A Faithful model from a redraw is 1.0 as deep as wide.
  - **UI**:
    - `AnimateIt` chains prep → canon `{look: "render"}` → prep on the new 3D asset, and links to Setup without
      `canon-flux`.
    - It is an open section, the main action on a still character's pixel art (`Panels.animatable`: an item or an
      uploaded picture, not a sheet).
    - `GeneratePanel` offers Redraw again on a cut-out whose parent is a `canon`.
    - The Tools menu names each tool's stage when it is not open, and says "3D models only" in a 2D asset.
  - **MCP**: `import_file(library_file=…)` takes a library picture through the API. `SKILL.md` has the move sets
    and fighting sentences as `workflow.MOVE_SETS` / `FIGHT_PROMPTS` have them.
  - **Tests**: `test_canon.py` (the look's prompt, a bad look refused), `test_mcp_server.py` (library_file), and
    Playwright (Animate's three steps and place, Redraw again, a wallpaper without Animate, the Tools menu's stages).
- **M61 Redraw: upscale and redraw a picture at any size** (2026-10-16; `steps/redraw.py`).
  - **Goal**: enlarge and/or draw an existing 2D picture again (the owner's wide 8352 x 2400 background at x4) without
    seams and without the card caring about the picture's size.
  - **Scope**: step `redraw`, ops `upscale` and `redraw` (§3.2 row). `steps/_sr.upscale` now blends overlapping windows
    on the CPU (`Blend`, `plan`, `u8`; was hard-cut tiles accumulated on the GPU, 3.8 GB at 321 MP); `refine._Tile`
    takes any h x w (multiples of 16); `concepts.style_file` shared. `Image.MAX_IMAGE_PIXELS` raised to 600 MP in the step only
    (output capped at 500 MP): thumbnails and Look read `preview.png` (2048 px). UI: a `RedrawForm` disclosure ("Upscale or redraw") in `ui/src/pixel/Panels.tsx` on any picture version (op, factor, description, strength slider, seed, style, mask select + upload, a window/time hint) and a `RedrawInfo` section (size, drift, seam, downloads) on a redraw version, built from the existing 2D panel controls; it shows as a Picture version.
    `presets.VRAM_GB["redraw"]`. Protect is a post-blend (ceiling in `_mix`). MCP: `run_step`/`describe_steps` take
    it as they find it; `import_file(asset_id=…)` uploads a file as a new version of an existing asset (the protect
    mask), nothing run; `mcp/SKILL.md` (effigy://guide) has the how-to.
  - **Acceptance**: (1) `tests/test_redraw.py` (CPU): plan covers the axis, a constant/identical blend comes back
    unchanged, protect, alpha, params. (2) **GPU**: 1536 x 1024 with alpha, x2, protect mask: upscale 7.6 s; redraw 12
    windows at 10.3 s each, torch peak 9.3 GB, card 13.6 GB, seam_ratio 0.86, drift 3.6 (2026-10-16). (3) **GPU, owner's
    terminal**: the full C2 job, 8352 x 2400 at factor 4 (33408 x 9600), ~572 windows of 1024 at overlap 0.25
    (44 x 13; ~1.7 h at 10 s), peak unchanged, RAM an estimate until this run (float32 accumulator 3.8 GB at 321 MP, plus the picture, its x4 copy and the mask; the text encoder is dropped after encoding); output capped at 500 MP (`MAX_PX`); `report.pass` all true. (4) **GPU, done 2026-10-16**: `painting` LoRA on 1536 x 1024: window 768 = 6 windows, 3.6 s each, torch peak 8.4 GB (reserved 9.75, card 10.6), seam 0.78, drift 2.8; window 1024 with the LoRA peaked at 9.5 GB torch but the card reached 15.9 GB and a window took 105 s (shared-memory spill): so with a `lora` the window defaults to and is capped at 768 unless the GPU budget is 17 GB or more (`LORA_WINDOW`, `LORA_ROOM_GB`). LOWER suggests 768 after an OOM.

- **M62 Bake for imported models** (2026-10-16; `steps/bake.py`).
  - **Goal**: a user's own model in → optional clean / remesh / uv → its own textures baked onto the new mesh → rig →
    anim → export. A skeleton that is not one of ours stays dropped (we rig with our templates).
  - **Scope**: `bake` takes its colour from the nearest `import_model` version (or textured GLB) up the chain when there
    is no generator volume: closest point on the source (`find_source`, `load_source`, `_closest`, `_Hi` on arrays),
    `_bake.bake_source` (every material, factor-only ones too, into one texture set), `write_glb(alpha_mode)`,
    `_views` keeps `alphaMode`. `_mesh.write_glb`: a UV-only GLB no longer carries trimesh's placeholder texture, so
    `textured()` is true only for real textures. UI: `bakeable` (workflow.ts) offers Bake in the Prepare panel for an
    import with materials, with the existing controls. No new param, op or control. Contract change: §3.2 `bake` row.
  - **Acceptance**: (1) `tests/test_bake_import.py` (CPU, GPU where present): colour per region and side, a factor-only
    material, flat normals, one material out, the refusals; `workflow.spec.ts` (bake offered for an import with
    materials, not for one without). (2) **GPU, done 2026-10-16** (`docs/bench/m62.md`): a 3-material dressed character
    and a 50k-triangle single-texture one, import → remesh → uv → bake 2048 in 7-8 s at 2.6-3.0 GB, colour within
    1.9 and 0.54 / 255 on average; then rig_fit → rig_skin → anim → Godot export, imported by Godot 4.7.1 without a
    warning.
  - **Not done**: the ceilings in the `bake` row; thin or doubled parts may take colour from the wrong side (upgrade: a
    ray along −n within a band, closest point as fallback). `clean` crashes in CuMesh `fill_holes` on a 50k-triangle
    import (CUDA error 9): a separate fix.

- **M63 Character IK in the animation editor** (owner 2026-10-18: "I need to be able to really fine tune animations,
  specifically for characters, so that body, limbs etc can be correctly edited ... how the IK is displayed and applied
  ... bring in the Blender functionality on how it handles 3D character animations, directly showing how bones and
  joints interact with each other"). Two PRs: the contract (`m63-ik-contract`, rule 8) and the editor (`m63-ik-editor`).
  - **What was missing**: the pose overlay was a dot per joint and a line to its parent (no direction, no roll, no sign
    of which bones a tool moves); IK was M38's transient drag of exactly two bones, baked into turn keys at one frame, the
    knee or elbow going wherever the solve put it; a hand held on something over time took a turn key on three bones
    at every frame.
  - **Contract** (§3.2 `anim_edit`, step 3c): `ik: [{bone, chain, space?, keys: [{f, p, swivel, w, falloff}]}]`, Blender's
    IK constraint with its target and pole made keyable in the layer: `chain` 2–8 bones above `bone` turn so it reaches
    `p`; `space` a bone (the target rides on it, Blender's Child Of) or skeleton space; `swivel` degrees about the
    chain's root–tip line (a pole angle relative to the clip's own bend, which survives an animated base better than a
    pole point); `w` blends FK and IK; keys interpolate like offsets, the weight fading over the falloff. Two bones by
    the foot lock's solve (lifted out of `_lock` unchanged), longer chains by FABRIK (16 rounds: a spine, a neck, a
    tail bends along its length). Both evaluations and the fixture (a hand and a fingertip added, a FABRIK chain of
    three and a two-bone chain in the hips' space).
  - **Viewer** (`Viewer.ts` pose mode): the armature as Blender draws it in pose mode: an octahedron per bone from its
    head to its main child's (the template's, else the child most of the skeleton hangs from), a leaf half its parent's
    length on (measured in the rest pose, kept in its own frame), the flat sides along the bone's own X so its roll
    shows; dashed relationship lines to a parent a bone does not join; grey, the selected bone blue with its name, the
    bones an IK control bends yellow, keys at the frame orange edges; picking by joint, else by the bone's body. Per IK
    control a box at the target, a diamond at the pole (out from the knee/elbow), dashed lines to both; IK target mode
    puts the gizmo on the box (a drag previews the solve live and keys the target) or the diamond (previews the swing,
    keys the swivel); IK drag takes any chain length (`ikChain`, the same solver). The target is reported with the bone
    as it showed when the drag began, and the editor keys `at + (handle − shown)` with `at` the bone's place in the
    layer at that frame, so ground, in place and the loop blend (applied after the IK) shift handle and bone alike.
  - **Editor**: the IK target tool (a bone without a control shows the chain it would bend; the first drag or Pin it
    here makes the control), the key's X/Y/Z cm, swivel, weight %, falloff; chain and space per control (a space change
    re-expresses every key at its own frame, so the keys stay put; the space offered: bones the IK does not move); the IK controls list; an IK row per control in the dope
    sheet and the key list (diamonds moved and deleted with the rest, a bar from the first key to the last); Curves of
    the target; a note when a control bends the selected bone; Chain for the IK drag; X-ray; Blender's R and G, Page
    Up/Down for the chain.
  - **Acceptance**: `tests/test_anim_edit.py` (two and three bones reach, the hand keeps its turn, weight and falloff,
    swivel 90° turns the elbow a quarter turn about shoulder–hand with the hand still, a target in Root's space follows
    Root, validation); the fixture both sides (`anim.spec.ts`); Playwright `IK controls (M63)` (the armature's heads,
    tails and colours, a target keyed at 8 and 16 with the head on it and halfway at 12, the pole swung 45° with the
    head kept, weight 0 and undo, the list and the row, Save posts `ik`); the street brawler (a library copy, real
    gizmo drags in the browser): the hand dragged 24.8 cm up lands there (keyed and re-evaluated), the pole swings the
    elbow with the hand still, a chain-3 IK drag bends Neck, Spine2 and Spine1.
  - **Not done** (ponytail): per-joint rotation limits (a FABRIK spine bends any way; add when a chain bends the wrong
    way); the target's own rotation (the hand keeps its clip's world turn; add when hands must hold an angle on a
    prop); a pole drag's preview swings about the shown chain, the bake about the clip's, so a big swing settles a
    little on release; FABRIK's 16 rounds have no early stop; a target or pole dragged between keys whose weight is
    below 1 makes a key of weight 1 (the hand goes where it was dragged; lower it in the Weight field).

- **M64 Motion from video: the other people, hands on the floor, a panning camera** (owner 2026-10-29, after the
  assessment of toyxyz/toyxyz_RGBMoCap: "Build the per-person loop and hand contacts … build and include the rest
  what's worth taking". That app's capture models (ExMR, FlowHMR, GVHMR, MAMMA) are SMPL-family bodies under
  noncommercial terms, PLAN §6's never column: nothing of them is used; three of its options are rebuilt on GEM-X.
  Built blind in the cloud session, branch `ccr-3bd14478-pui9wd`, **not run on a GPU**).
  - **People**: `mocap` reports `metrics.people` (the people at the first frame with people, capped at
    `MAX_PEOPLE` = 8, what `person` takes) and `metrics.person`. The motion's page says "N people at the start of
    the video; this is person k" and offers "Make the other N's motion too": one `mocap` job per person no motion of
    that video has yet (the same params, `person: i`), queued at once (the GPU lock runs them in turn; the button
    stays busy until every job has ended, so a second click cannot double them); the page shown at the end is the
    last job's, the others sit in Your motions. MCP: `run_step mocap {"person": 1}`. ponytail: people are counted
    at the start (one who walks in later is not offered); `track` is overlap-only (M34), so two people crossing can
    swap; YOLOX at 0.5 counts a poster or a bystander.
  - **Hands**: the editor's **Find hand contacts** (beside Find foot contacts): the bones named `…Hand` with an arm
    above them (`chain`); a still stretch by the foot rule (`contacts`, M38) that lies within 10 % of the body's size
    above the clip's floor (the lowest any bone gets in the clip) becomes a lock in `feet`, the same two-bone solve on
    Arm, ForeArm and Hand; a hand held still in the air is not locked (as a foot it would be: a foot's rule is
    relative to its own lowest). The size is the skeleton's longest extent over the clip (its height standing, its
    length in a plank), not frame 0's height: a clip that starts in a push-up is a fifth as tall as it is long, and a
    palm's wrist sits 6–9 cm above the floor. Contract unchanged: `feet` has taken any bone whose parent and
    grandparent turn since M38. ponytail: the floor only; a raised support (a table, a rail) would need its height;
    the 10 % is a first threshold, no bench row yet.
  - **Camera** `camera: "still" | "panning"` (default still; the form's Camera: held still / turning on the spot to
    follow). `camera_path`: corners outside every detected person's box (padded 10 %), followed into the next frame
    by LK flow, a RANSAC homography (3 px), read through GEM-X's K (`estimate_K`) as the camera's turn:
    `R_rel = K⁻¹HK` scaled to det 1 and projected onto SO(3) by SVD, `R_w2c[t] = R_rel·R_w2c[t−1]`, exactly GEM-X's
    `cam_angvel` definition (`R·R_w2c[t] = R_w2c[t+1]`); a frame with under 12 corners, tracks or inliers keeps the
    last turn and is counted (`metrics.camera_unread`); more than half unreadable (a blank wall, the dark, people
    filling the frame) fails the run with "choose Camera: held still"; `metrics.camera_turn_deg` is the whole turn.
    GEM-X gets `R_w2c`, `T_w2c` (that rotation, no translation) and `cam_angvel`: its global trajectory turns the
    camera's heading out (`get_body_params_w_Rt_v2`), and `cam_angvel` is a trained input (`has_cam_mask`); the
    `static_cam` flag it also receives is read by nothing in this release. `cam_tvel` stays zero. ponytail: pure
    rotation; a camera that walks (a dolly) reads as a turn, so the person drifts (visual odometry, DPVO, if dolly
    shots matter); a still camera never runs it (no random-walk drift from noise). CPU work before ViTPose: the
    reviewer's run on the cloud container took about 27 ms a frame at 720 × 1280 (about 12 s for a 15 s clip),
    unmeasured on the laptop.
  - **Not taken from RGB MoCap**: Kimodo's MotionCorrection (Apache-2.0, a C++ build per venv; the editor's locks
    already correct feet, and now hands, by IK); multi-view capture (MAMMA, restricted; GEM-X is monocular); its
    retargeting and FBX, VRM and VMD exports (ours exist); its editable contact timeline (M38's locks are that).
  - **Acceptance**: run here (the cloud container, its own Chromium), pass: `tests/test_mocap.py` (`camera` validated
    and defaulted; `camera_path` on a synthetic scene turning 0.5° a frame for 10 frames: every frame's turn within
    0.3° of the truth, a still scene under 0.05°, the same pan with a walking figure inside a declared box, a blank
    wall refused; the faked run returns `people`, `person`, `camera`, `camera_unread`), `ui/tests/anim.spec.ts` (a
    palm still on the floor locks, a hand still at hip height does not, a clip that starts in a plank), `tsc`, the
    smoke tests of the foot locks and the video form (the Camera field, "Make the other one's motion too" posts
    `mocap` for person 1 on the upload); the step-reviewer's pass (the camera maths against GEM-X's conventions, the
    OpenCV types and edge cases, the fan-out) and its findings fixed.
    **Needs the laptop** (not run): (1) a clip with two people: "Make the other one's motion too" queues person 2,
    its `preview.mp4` follows the other person, both land in Your motions; (2) a push-up or crawl clip on the viking:
    Find hand contacts locks the palms, the arms bend, the bake equals the preview (no new slide metric: look);
    (3) a phone clip panned to follow a walk, once "still" and once "panning": the walker crosses the room in
    "panning" and walks in place or curves in "still"; `turned_deg`, foot slide and `root_jitter_mm` no worse than
    a still clip's; time and peak VRAM beside M34's (the camera path is CPU work before ViTPose: seconds to record).
    (4) `docs/bench/m34.md` gets the numbers.

- **M65 Connect clips: a transition by Kimodo, from one clip's end into another's start** (owner 2026-10-30, after
  the survey of AI animation projects: "Specifically the connect clips is interesting … chose one or 2 animations and
  let kimodo fill the gaps"; "Build connect clips transition-only first, joined clip later together with the new
  viewer for paths"). Built blind in the cloud session, branch `ccr-3bd14478-pui9wd`, **not run on a GPU**.
  - **What it is**: the `motion` step with `from` and/or `to`, clips of an animated version. Kimodo's own way of
    joining two prompts (`_multiprompt`: the next segment's first 5 frames held to the last 5 of the one before, the
    full body and the hands' and feet's turns) with our clips' frames instead: A's last 5 frames held on frames 0–4,
    B's first 5 on the last 5; then cut so the transition starts on A's last frame and ends on B's first (in a game
    it plays between the two; the duplicated frame is the seam). One side empty continues a clip ("After A") or leads
    into one ("Into B"). The prompt is optional ("A person moves naturally from one pose to the next.").
  - **The pose onto Kimodo's skeleton** (`steps/_connect.py`, numpy only): our clip sampled at the seam frames from
    `rigged_anim.glb` (the node tree with its armature node, a "mixamorig:" prefix dropped), placed so the seam faces
    +Z over the origin (Kimodo's heading: the hip line); B turned by `turn` and landed at `travel` straight on, or by
    default at the mean of A's end velocity and B's start velocity over the transition. Then the exact inverse of
    `blender_ops/retarget` (which brings every Kimodo clip onto our rigs): the retarget poses our bone at
    `Δ_src · align · rest` (`align` from both rests' frames: the bone's direction and the palm or the body's forward;
    the torso and the clavicles unaligned), so SOMA's bone gets `Δ = (our world turn · our rest⁻¹) · align⁻¹`, in
    Kimodo's frame `Tᵀ·Δ·T` (T: the retarget's turn of SOMA to face our way); SOMA's 30 joints follow from its own
    rest offsets (the second neck joint riding on the first, the jaw and eyes on the head, the finger tips on the
    hand), the hips by our travel from rest over the height ratio. SOMA's rest is Kimodo's standard T-pose with the
    hips 1 m up, as its BVH writer puts them (so the retarget measures travel from there). The name map and the
    retarget's MAIN and KEEP are copied (steps/ may not import blender_ops); a test holds them equal.
  - **UI**: the animated model's panel, "Connect two clips": From and To (its clips, or nothing), what happens in
    between, Length, and with a To: Turn and Travel. The transition's page ("Transition") says what it holds and how
    far its ends are from the held poses, and "Put this motion on the model with its clips" runs `anim` on the
    skinned model under it with the source's clips plus the transition (an `anim_edit` source: its base animation's
    clips, its hand edits staying where they are, said on the page). MCP: `run_step(asset, <anim version>, "motion",
    {"from": "Idle_A", "to": "Walk"})`.
  - **Not done** (ponytail): the joined clip (A, the gap and B as one clip with the seams blended on our skeleton: an
    `anim_edit` or `anim` contract change, its own PR, with the path viewer, owner 2026-10-30); paths and waypoints
    (the same viewer); sideways travel (`travel` is straight on; the path viewer places B anywhere); the exact height
    ratio (from joints on both rigs, the retarget measures Blender's bones with their tails: a big-headed
    character's hips may come back a few per cent of their travel off at the seams; the exact ratio needs a Blender
    run on both rigs); creatures (Kimodo is human only: the box shows only on the 52-joint template).
  - **Acceptance**: run here, pass: `tests/test_connect.py` (the copied tables equal the retarget's; the GLB read
    through its armature node and a prefix; a random SOMA pose put on our rig by the retarget's formula comes back
    exactly, every joint's position and turn; our A-pose rest lands on SOMA with our arm directions and SOMA's own
    torso; placing, the landing, the plan's frames, cut and held poses from a standing clip into a walk; the cut,
    the start over the origin and the seam error; the step's params, defaults and name), `tests/test_motion.py`,
    the smoke test `Connect clips (M65)`. With the pinned Kimodo (1aece8c) on the CPU in a scratch venv (its skeleton
    assets, no weights): a plan on its real SOMA rest builds its `FullBodyConstraintSet` and
    `EndEffectorConstraintSet`, its post-processing reads them, and its own forward kinematics of our joint turns
    gives our joint positions exactly (0.0 m); A's last pose stood over the origin at heading 0.07°, its lowest joint
    3 mm under the floor; B for a 30° turn after a stand-to-walk landed at heading 30.07°.
    **Needs the laptop** (not run): (1) on the viking (`Idle_A` → `Walk`, 1.5 s, and `Walk` → `Idle_A`): the
    transition clip generated, put on with its clips, its first frame on Idle's last and its last on Walk's first
    by eye in the viewer, `seam_cm` and `generate_seconds` recorded; (2) one side empty both ways; (3) a turn of 90°
    and a travel of 2 m; (4) the Godot export of the three clips plays Idle → transition → Walk without a pop;
    (5) Kimodo's post-processing: `post_processing=True` as text-to-motion has it (its C++ corrector,
    `motion_correction`, is not in `.venv-kimodo` per M26; if text-to-motion runs, so does this; if a run fails on
    it, the fix is in both).

## 5. Bench procedure (reference laptop)

1. Plugged in, "max" power profile, hybrid graphics (iGPU drives the display), browser set to
   power-saving GPU, no other GPU apps, Blender closed unless the milestone needs it.
2. `uv run python -m tools.bench --preset <p> --set tests/fixtures/golden --out docs/bench/<name>.csv`.
3. Record `nvidia-smi` driver version, `torch.version.cuda`, Blender version, commit SHA in the CSV header.
4. Commit the CSV and a 5-line summary `.md`. Numbers in WORKFLOW/PLAN are replaced by measured ones,
   never rounded up.

## 6. Things agents must not do

- Replace the contracts in §3 "while they're in there".
- Add a database, a job scheduler, a plugin system, a second frontend framework, websockets, or a
  message broker. The triggers that would justify each are in ARCHITECTURE §10.
- Add any model or package without `licenses.yaml`; add anything from PLAN §6's "never" column.
- Enable the Hunyuan engine by default, put it in a preset, commit or re-host any Tencent code or
  weights, skip the enable-flow confirmation, or export a restricted version without the explicit
  §5(c) confirmation (MX1).
- Import `bpy` outside `blender_ops/`/`blender_addon/`; import SkinTokens/UniRig outside `.venv-skin`.
- Start Blender GUI processes from the backend, or write into the user's `.blend` in place.
- Read, log, store or forward `ANTHROPIC_API_KEY`, `BLENDER_MCP_PRO_LICENSE` or Claude credentials.
- Claim a GPU acceptance check passed without having run it on the reference machine.
- Round estimates into the docs as if measured.

## 7. PR template (`.github/pull_request_template.md` puts it into every pull request)

```
Milestone: M<n> <title>
Scope: <files>
Skipped (ponytail): <what> → add when <trigger>
Acceptance:
  - [ ] <check 1> — output: …
  - [ ] <check 2> — needs GPU, not run / run on laptop: …
Measurements: peak VRAM …, time …, commit …
Docs updated: IMPLEMENTATION.md §… / PLAN.md §… / none
```
