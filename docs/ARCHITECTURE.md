# Effigy: System Architecture

> Status: the design the code follows (written 2026-10-03, kept current); the build state is in
> [IMPLEMENTATION.md](IMPLEMENTATION.md) §4.
> **Owner decisions:**
> - our own **React + three.js** interface (not Gradio)
> - **full interactive Blender**, live-linked to the tool
> - **Claude (the owner's subscription) driving Blender over MCP** to work on assets automatically
>
> Everything else stays minimal. Each cut item is listed in §10 with the trigger that brings it back.
> Model choices are in [PLAN.md](PLAN.md) and [`docs/research/`](research/).

## 1. Hard constraints

1. **Local and offline** for generation. Claude is the only networked part, and it's opt-in
   per action.
2. **16 GB VRAM**, reference machine **RTX 5080 Laptop (sm_120), Windows 11, 32 GB RAM**. Only one
   heavy model on the GPU at a time. **Blender's viewport and Cycles also use VRAM**, so the GPU
   lock covers Blender bakes too, and interactive Blender stays light while a model step runs.
3. **Conflicting ML stacks**: TRELLIS.2 needs our own sm_120 CUDA wheels, Blender has its own Python,
   SkinTokens pins torch 2.7, and later models pin other versions.
4. **Non-destructive**: every step, including every Blender session and agent call, writes a new
   version and never overwrites.
5. **glTF/GLB in metres, Y-up** in the library. Conversion happens only at the edges
   (TRELLIS `[-0.5,0.5]³` Z-up, Blender Z-up, FBX cm).

## 2. Shape

```
 ┌──────────── ui/ (Vite + React + TypeScript + three.js) ────────────┐
 │ Create · candidate grid · 3D viewer · stepper · versions · Claude   │
 └───────────────▲───────────────────────────────▲─────────────────────┘
                 │ fetch (JSON)                  │ EventSource (SSE: progress, logs, Claude events)
 ┌───────────────┴───────────────────────────────┴─────────────────────┐
 │ server/ (FastAPI, no torch): serves ui/dist + /api + /events        │
 │ global GPU lock · library folders · spawns subprocesses             │
 └──┬─────────────────────────┬────────────────────────┬───────────────┘
    │ subprocess per step     │ localhost TCP (JSON)   │ subprocess: user's own `claude` CLI
 ┌──▼──────────────────┐  ┌───▼──────────────────────┐ │ (their subscription login)
 │ .venv-ml            │  │ Blender (interactive GUI) │ │   with MCP config:
 │ python -m steps.X   │  │ + Blender MCP Pro add-on  │◄┼── Blender MCP Pro server
 │ (torch 2.8 cu128)   │  │   (socket :9877, runs     │ │   (owner's licence)
 └──┬──────────────────┘  │   commands on main thread)│ └── effigy MCP server (library,
    │                     └───┬──────────────────────┘      steps, validate, export)
 ┌──▼─────────────────────────▼────────────────────────────────────────┐
 │ library/<asset>/<NNN_step>/ {files…, meta.json}                      │
 └──────────────────────────────────────────────────────────────────────┘
```

## 3. Frontend (`ui/`)

- **Vite + React + TypeScript + plain three.js (r186, `WebGLRenderer`).** An imperative `Viewer`
  class owns the canvas; React renders only panels. three's own `addons` cover the viewer:
  `GLTFLoader` + `MeshoptDecoder` (prefer meshopt over Draco: faster decode, no worker), `KTX2Loader`
  only if ever needed, `OrbitControls`, `RGBELoader` + `PMREMGenerator`, `SkeletonHelper`,
  `AnimationMixer`, `ViewHelper` (axis gizmo), `TransformControls`, `math/Lut`. One extra dependency:
  **three-mesh-bvh** (MIT) for raycasting rig markers on 1M-triangle meshes (three's own BVH example
  uses it). No react-three-fiber/drei (reviewed: the laborious parts, channel views, heatmap, timeline,
  markers, capture, are custom either way and harder through a declarative layer), no UI kit, no state
  library, no router (`# ponytail: add r3f/zustand when the viewer or state outgrows one component`).
  **Not WebGPU**: `WebGPURenderer` doesn't support `ShaderMaterial`, which the channel views use, and
  nothing here needs compute.
- Viewer features and how (all small, no libraries):
  - **Channel views**: `setViewMode()` swaps materials per mesh (originals kept in a `WeakMap`): albedo
    → `MeshBasicMaterial({map})`, normal → `MeshNormalMaterial`, wireframe flag, UV checker →
    `CanvasTexture`; AO/roughness/metallic → one ~20-line `ShaderMaterial` with a `channel` uniform
    reading the packed ORM texture.
  - **Weight heatmap**: read `skinIndex`/`skinWeight`, write a `color` attribute (dominant bone, or the
    selected bone through `Lut`), render with `vertexColors: true`; skinning still works.
  - **Clip player**: `AnimationMixer` + `<input type="range">` bound to `action.time` + a `<select>`.
  - **Markers**: spheres parented to the mesh + `TransformControls`; BVH built once per load.
  - **Thumbnails/turntable**: render, `canvas.toBlob()`, POST PNGs; Blender headless render for
    reproducible ones.
- Views on one page:
  1. **Create**: prompt + category, reference upload.
  2. **Candidate grid**: 2×2, progressive, gate badges, reroll/"4 more", pick.
  3. **Asset**: version timeline (tree from `meta.json.parent`) + stepper with quick/advanced params.
  4. **Viewer**: PBR with HDRI; modes for albedo / roughness / metallic / normal / wireframe /
     UV checker / skeleton / weights heatmap; animation clip player; polycount and VRAM HUD.
  5. **Blender panel**: open in Blender, sync status, pull the edit back as a version. Agents connect
     over MCP (§6) and Setup's "Connect an agent" gives their config.
- Progress via **one `EventSource` (SSE) stream `/events`** multiplexing all jobs (HTTP/1.1 allows
  only 6 connections per origin). Events: `progress`, `log`, `version_created`, … (an agent's jobs
  show like any other). Cancel is a POST.
- **Rig marker placement** (Phase 2) is done in the three.js viewer: click on the mesh → raycast →
  draggable markers.

## 4. Backend (the `server/` package)

- FastAPI (`server/app.py` and its siblings). Endpoints: `GET /api/assets`, `GET /api/assets/{id}`,
  `POST /api/assets/{id}/steps/{name}`, `POST /api/jobs/{id}/cancel`, `GET /events` (SSE via
  **sse-starlette**, BSD-3: heartbeats for long silent ML steps + disconnect detection),
  `POST /api/blender/{open|pull}`, `GET /api/agent/setup`. Static: `library/` mounted as
  `StaticFiles` on `/files` (Range requests work; **no gzip on GLBs**) and `ui/dist` mounted on `/`
  **after** the API routes, `index.html` served with `Cache-Control: no-cache` (hashed `/assets/*`
  are immutable). No router in the UI, so no SPA fallback is needed.
- **A step = a CLI module:** `python -m steps.<name> --in <dir> --out <dir> --params '<json>'`.
  - Progress is printed as `PROGRESS <0..1> <text>` and relayed to SSE.
  - Cancel = kill the process. VRAM is freed when the process exits.
  - **Resident workers for heavy families.** `steps.trellis2` (generate / texture / bake for both
    weight sets) also runs as `python -m steps.trellis2 --serve`: one JSON request per stdin line,
    one JSON reply per stdout line, same `PROGRESS` lines in between. The backend keeps **at most one**
    heavy worker alive (LRU of size 1) and kills it before another heavy step takes the GPU lock.
    Reason: ~13 GB (TRELLIS.2) / ~24 GB (Pixal3D) of weights cold-load in tens of seconds to minutes
    plus FlexGEMM's Triton autotune; every mature base keeps the pipeline resident
    (`# ponytail: LRU of one; a scheduler only if a second heavy family needs to interleave`).
- **One GPU job at a time:** a single lock, also held for Blender bakes
  (`# ponytail: global lock; it's one laptop GPU`).
- **One ML venv** (`.venv-ml`: **Python 3.12**, torch 2.8.0 cu128, our sm_120 wheels, diffusers,
  BiRefNet). It gets split only when a model's pins actually conflict; SkinTokens always has its own
  (torch 2.7 + GPL-provenance isolation). **Experimental engines** (currently only Hunyuan3D-2.1) get
  their own venv too, created only after the user enables them, and every version they produce is
  marked `restricted` down the whole chain (IMPLEMENTATION MX1).
- **The TRELLIS.2 engine** (`engine/trellis2/`) = vendored upstream `microsoft/TRELLIS.2` and
  `TencentARC/Pixal3D` + a patch set, in this order of provenance: **localmesh-engine** (Apache-2.0,
  NOTICE kept: `uvraster.py` pure-torch UV rasterizer + `leurre_nvdiffrast.py` decoy so the stock
  `o_voxel` (the colour volume TRELLIS.2 generates, kept so any later mesh can be re-baked) imports, `sdpa_backend.py` for sm_120, BiRefNet swap, staged/meta-device loading, the
  DINOv3/Transformers-5 fix), upstream **PR #173** (keep UVs), **PR #185** (o-voxel C++ fixes), the
  int64 `svox2vert` fix, and the SPZ fork's chunked-decode commits + `EXPLICIT_GEMM` fallback.
  **No nvdiffrast, no nvdiffrec, no xformers, no flash-attn.**
- **Library = folders + `meta.json`** (step, parent, params, seed, model ids, timings, peak VRAM,
  licence tags). Branch = new folder whose `parent` points back.
- **Writing GLB**: trimesh (MIT) writes static meshes only (its exporter has no skins/animations).
  **Skinned/animated GLBs are written by Blender's bundled glTF exporter** (headless op) or, for the
  rig step, by a ~100-line **pygltflib** helper (the MIT SkinTokens-NoBlender port has one to copy
  from). Post-processing (LOD chains via attribute-aware `simplify`, `meshopt`, WebP + texture resize)
  uses **gltfpack** (zeux/meshoptimizer, MIT): one pinned native binary, so no Node at run time
  (IMPLEMENTATION M13); skip KTX2.

```
library/dwarf_blacksmith/
  brief.yaml
  010_concepts/  020_pick/  030_generate/ (hi.glb, ovoxel.npz)  040_clean/  050_remesh/
  060_bake/  070_rig/  080_anim/
  090_blender/   scene.blend  edited.glb  meta.json       # each Blender pull = new version
  exports/
```

## 5. Interactive Blender (live link)

Details: [research 08](research/08-blender-integration.md).

- **Blender 5.2.2 LTS** (Python 3.13, supported until Jul 2028; RTX 50 OK; OptiX needs driver ≥ 575).
  The official portable zip is **downloaded on first run**, SHA-256 verified, and extracted with a
  `portable/` folder so the user's own prefs and add-ons don't leak in.
- **The live link reuses the owner's Blender MCP Pro add-on** (MIT add-on, `localhost:9877`,
  length-prefixed JSON; [research 08](research/08-blender-integration.md)). The backend talks to the
  add-on socket directly with a ~30-line client (`import_file`, `export_object`, `execute_python`,
  `get_viewport_screenshot`, `analyze_mesh`, `diagnose_rig`). It needs no licence key; Claude uses
  the owner's licensed Pro MCP server against the same add-on:
  - **"Open in Blender"**: import version N's GLB/.blend, tag objects with `effigy_asset_id`/`effigy_version`.
  - **"Pull from Blender"**: save a `.blend` copy + export GLB → new `090_blender` version.
  - **No add-on of our own in the MVP**
    (`# ponytail: own GPL bridge add-on only for live selection/material sync or an in-Blender "Send back" button`).
- After a pull, later steps run on the edited mesh: re-bake from `ovoxel.npz`, rig QA, validate, export.
- **Headless operations** run one subprocess per op (Blender starts in 0.5–2 s, so there's no
  persistent worker):
  `blender -b --factory-startup --offline-mode --python-exit-code 1 --python blender_ops/<op>.py -- <json>`.
  - `blender_ops/` is **GPL-3.0-or-later** in its own folder. The MIT backend never imports it.
  - Each op validates its own result, because Blender has silent failures: bone heat returns
    FINISHED with zero weights, and Cycles falls back to CPU without saying so.
- **Bakes default to CPU** (4K normal ≈ 18 s on 4 vCPU). GPU bakes only under the global GPU lock,
  in a short-lived process.

## 6. Agents via MCP (M40)

- **`effigy`** (`mcp/effigy_server.py`, stdio, Python `mcp` SDK) bridges the app's REST API for any MCP client: a
  tool set around the workflow (list and inspect, describe the steps, `look` at a version as one image, import a
  local file, run a step, wait on or cancel a job, validate, export into a folder, Blender open/pull). Long jobs
  wait at most 45 s, then return a job id to poll; images are real image content. Contract: IMPLEMENTATION §3.7.
- **The owner's Blender MCP Pro** (and the free blender-mcp) stay separate servers for the live Blender scene.
- The user drives their own agent (Claude Code or Desktop, Codex, Cursor, VS Code, Gemini); the app runs no
  agent itself (M9's "Ask Claude" runner was removed in M40). Setup's "Connect an agent" prints each client's
  config with this install's path, and tests the bridge.
- **Safety**: every step writes a new version, agents get no deletes, and the API answers only requests for
  `127.0.0.1`/`localhost` without a foreign `Origin` (a web page cannot reach it). The Blender add-on binds to
  `localhost` without a token (`# ponytail: acceptable on a single-user laptop; add a token if that changes`).

## 7. Models and licences

- Downloads: `huggingface_hub.snapshot_download` into `models/`. DINOv3 is gated, so the user runs
  `hf auth login` once.
- `licenses.yaml` + `test_licenses.py` asserts every default component is `commercial: true`.
  Licence tags are copied into `meta.json`.
- No NC component anywhere: nvdiffrast/nvdiffrec are replaced by localmesh's pure-torch rasterizer
  from day one, RMBG-2.0 by BiRefNet.
- Claude usage is governed by the owner's own Claude plan and terms. We never store or proxy their
  credentials, and we only invoke their local `claude` binary.

## 8. Install and packaging (Windows first)

- **`uv` + python-build-standalone**: ship `uv.exe`, `pyproject.toml`, `uv.lock`, `run.bat`. First run:
  `UV_PYTHON_INSTALL_DIR=.\python UV_CACHE_DIR=.\cache uv sync --frozen --python-preference only-managed`
  (downloads CPython **3.12** `install_only_stripped`, ~25 MB). **Python 3.12** because we build the
  CUDA wheels ourselves and the only reusable sm_120 natten wheels are cp312.
- **Torch 2.8.0** from an explicit `[[tool.uv.index]]` (cu128; Blackwell needs ≥ cu128). **Our CUDA
  wheels** (cumesh from main ≥ 2026-05-09, flex_gemm, o_voxel; natten only if no verified community
  cp312/cu128 build) are built in GitHub Actions with `TORCH_CUDA_ARCH_LIST="8.6;8.9;12.0"` and, because the
  repo is private (uv can't fetch private Release assets), **committed to `wheels/`** by the workflow.
  `tool.uv.sources` references them as `{ path = "wheels/<wheel>", marker = "sys_platform == 'win32'" }`,
  so `uv.lock` pins them. (If the repo goes public: move them to Release assets and `url =` sources.)
- **Models**: `huggingface_hub.snapshot_download` into `models/` with `HF_HUB_CACHE` inside the app dir (not `HF_HOME`: that also moves the login token);
  resume is automatic. Expose `HF_HUB_DISABLE_XET=1` as a fallback toggle (2026 Windows reports of xet
  downloads hanging at 0 bytes).
- **Blender**: pinned zip URL + SHA-256, extracted on first run (§5).
- **Linux**: run from source with the same `uv sync`; no installer until someone needs one.
- Rejected: PyInstaller/Nuitka (0.5–1.5 GB one-file, CUDA DLL path pain), pixi (good CUDA story but
  our deps are pip wheels, so it's a second solver for nothing), ComfyUI-style `python_embeded`
  (ComfyUI itself now says "not recommended for regular users"; ComfyUI Desktop moved to uv).
- **Later shell**: Tauri 2 (mature; sidecar = single exe, so the Python env stays a uv-managed
  directory spawned from Rust; updater replaces only the ~10 MB shell, never the env). Electron only
  if Linux WebKitGTK WebGL bites. Keep the shell dumb: it starts/stops the backend and opens a window.

## 9. Tests and dev loop
- One small `test_*.py` per non-trivial piece of logic: `meta.json` parent chain, licence check,
  coordinate conversion round-trip, a bake on a 2-triangle fixture, and the Blender socket client's
  length-prefixed framing. pytest + FastAPI `TestClient` (`client.stream()` for SSE).
- **One Playwright smoke spec** (`ui/tests/smoke.spec.ts`, Apache-2.0): page loads, canvas has a WebGL context, a fixture GLB
  loads (viewer state exposed on `window`), screenshot. Headless Chromium uses SwiftShader (slow but
  works); `channel: 'msedge'` headed on the laptop for the real GPU. No vitest, no component tests.
- Dev: `vite.config.ts` `server.proxy` for `/api`, `/events`, `/files` → `127.0.0.1:8000`
  (http-proxy streams SSE unbuffered); `uvicorn server:app --reload`.
- `bench.py`: the golden image set through a preset on the laptop → CSV (success, time, peak VRAM, tris).

## 10. Deliberately deferred (add when…)

| Cut | Add when |
|---|---|
| react-three-fiber/drei, state library, router, UI kit | the viewer or app state outgrows plain components (reviewed Oct 2026: not lazier for our viewer features) |
| Tauri/Electron shell | a native window or file associations are actually needed (the browser tab works meanwhile) |
| Keep-warm for *light* steps (T2I, BiRefNet) | measured reload time hurts the concept loop (> 30 s per batch); the heavy TRELLIS.2 worker is already resident (§4) |
| GPU scheduler with priorities, VRAM tiers, auto-detect | supporting GPUs other than the reference laptop |
| SQLite, content-addressed blobs | library search is slow (thousands of assets) |
| Plugin/engine manifest system | a third generator engine exists |
| `trellis2.c` native backend | the Windows sm_120 Python wheels can't be made to work |
| Public REST API docs, Meshy-shaped endpoints, StableProjectorz shim, ComfyUI interop | another tool needs to drive Effigy |
| Linux installer, Docker | someone runs it on Linux (from source works meanwhile) |
