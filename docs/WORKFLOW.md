# Effigy: End-to-End Workflow (prompt → production-ready asset)

> This describes the **user journey** the tool is built around; [MANUAL.md](../MANUAL.md) is the reference
> for what the app does today:
> *"I prompt for the asset I need → it generates images → I choose the best → it becomes a 3D asset
> → it's optimized (with Blender) until it's production-ready."* Model choices per step are
> referenced from [PLAN.md](PLAN.md) and [`docs/research/`](research/). The whole flow must fit a
> **16 GB consumer GPU**: the stages run one after another, never with two heavy models resident.

## 0. Overview

```
 ①Prompt ─► ②Brief ─► ③Concept candidates ─► ④Pick & refine ─► ⑤3D candidates ─► ⑥Production pass ─► ⑦Blender polish ─► ⑧Validate & export
  (text,     (LLM      (N images, auto-scored,   (variations,       (1–3 seeds /        (preset: clean,      (round-trip via      (checklist, engine
   refs)      expands   grid, "more like this")   edits, A-pose,     weight sets,        remesh, UV, bake,    MCP Pro add-on, or    profiles, LODs)
              request)                            side/back views)   pick best)          rig, animate)        headless scripts)
```

Every arrow is a **checkpoint**. The user can stop, branch or go back. Every artefact is stored as a
version (ARCHITECTURE §5), so "try again from the concept image with another seed" never loses
work.

## ① Prompt

- Free text ("a stylized dwarf blacksmith for a mobile RPG"), optionally with **reference
  images** (style refs, a sketch, a photo of an object) and a **target** (Unity mobile, Unreal,
  Godot, VRChat, 3D print).
- Or skip ①–③: drop your own image or turnaround sheet straight into ④.
- Or bring a finished model (FBX, glTF, OBJ, USD, a zip): it joins at ⑥. It can be cleaned and simplified, its
  own textures baked onto the simpler mesh, then rigged and animated. A skeleton that isn't one of Effigy's
  templates is dropped and the model is rigged with Effigy's own.

## ② Brief (prompt enhancement)

The request becomes a structured **asset brief**, which the user can edit as a form.
- **MVP:** the user picks a category (character / prop / creature) and the text is dropped into
  that category's **prompt template** (see [research 07 §3](research/07-prompt-to-image-frontend.md)).
- **Later:** a small local LLM (**Qwen3.5-4B**, Apache-2.0) fills the brief and writes 4 prompt
  variants. That also fixes Z-Image-Turbo's low seed diversity.

```yaml
name: dwarf_blacksmith
category: character/humanoid        # → enables pose normalization + humanoid rig
style: stylized, hand-painted, chunky proportions
image_prompt: "full-body stylized dwarf blacksmith, A-pose, arms 45° away from body, hands open and empty, front view, plain white background, soft even lighting, no shadows, game asset concept"
negative_prompt: "cropped, cut off feet, holding objects, dramatic lighting, background scene, multiple characters"
pose: a-pose
views: [front]                      # or [front, side, back] → multi-view generation
budget: {triangles: 15000, texture: 2048, materials: 1}
rig: humanoid                        # humanoid | quadruped | generic | none
animations: [idle, walk, run, attack_melee, hit, death]
export: {engine: unity, formats: [fbx, glb], scale_m: 1.4}
```

The **brief drives the presets** of every later stage: pose normalization, polycount, texture size,
rig template, animation set, export profile. When the LLM is added, it runs as its own step
(GPU burst, then exit) or on CPU, so the 16 GB budget isn't affected.

## ③ Concept candidates

- Generate **4 candidates** (more on demand) with **Z-Image-Turbo** (Apache-2.0) via `diffusers`.
  Each image appears as it finishes, ~20–30 s for 4 warm (estimate).
- **Auto-scoring** sorts the grid, so the best options appear first:
  - **MVP:** 3D-readiness gates computed from the BiRefNet mask: one connected subject, margin to the
    frame edges (nothing cropped), fill ratio, uniform background.
  - **Later:** VLM yes/no checks (Qwen3.5-4B: "full body? A-pose? hands empty? matches the
    brief?"), DWPose arm angles for characters, and an aesthetic tie-breaker.
- Grid UX: pick ⭐, **"more like this"** (same seed family / image-to-image variation), lock seed and
  change prompt, compare side by side.

## ④ Pick & refine

On the chosen image:
- **Edits**: inpaint or instruction edits ("remove the hammer", "make the beard red"), background
  cleanup, upscale.
- **Canonicalize for rigging** (characters): re-pose to A/T-pose if needed, open the hands, remove
  held props. These can be regenerated later as separate attached assets.
- **Extra views** for multi-view generation: generate side/back views, then a **consistency check**
  (silhouette heights, colours) with side-by-side review. Hidden views are invented by the model,
  so the user must confirm them.
- Output: one 3D-ready image, or a framed view set with cameras (shared crop, see
  [research 02 §4](research/02-pixal3d-algorithms.md)).

## ⑤ 3D candidates

- Generate **1–3 3D candidates** (different seeds, and optionally both weight sets: "Fast" =
  TRELLIS.2, "Faithful" = Pixal3D; see research 09). A third, **experimental Hunyuan3D-2.1
  engine** is designed but not built (PLAN §6): when it exists, its candidates will carry a red "restricted outputs" badge, and
  anything derived from them stays restricted (export asks for explicit confirmation). Start with a fast low-res preview, then
  refine only the chosen one at full resolution.
- Viewer comparison: turntable side by side, a silhouette overlay vs. the concept image, and
  quick stats (watertight? face count, thin-part warnings).
- Every generation persists the **PBR voxel volume** (where the generator provides one), so
  later re-bakes are lossless.

## ⑥ Production pass (preset-driven, automatic)

Driven by the brief's budget, rig type and target. Each step is visible and re-runnable:

| Step | What happens | Default tech |
|---|---|---|
| Clean | fill holes, remove floaters, fix non-manifold, scale to `scale_m`, origin at the feet, face +Z/−Y per target | CuMesh |
| Remesh | tri decimation to budget, or quad remesh for characters (symmetry, edge loops around joints) | [research 03](research/03-retopology-uv-baking.md) |
| UV | auto charts, seams on hidden edges for characters, texel density target | CuMesh/xatlas · Blender |
| Texture | **re-bake PBR from the voxel volume** + normal/AO from the hi-poly; optional retexture or 2D refine pass; a painted part redrawn on its own (M68) | [research 04](research/04-texturing.md) |
| Rig | template skeleton fit + marker correction + skin weights + deformation QA | [research 05](research/05-rigging-animation.md) |
| Animate | brief's clip list retargeted from bundled CC0 libraries, the arms kept out of the body (M67); text-to-motion or a video for custom clips; transitions between clips (M65) | [research 05](research/05-rigging-animation.md) |

## ⑦ Blender polish: by hand, or by Claude

The asset can be opened in a **live, interactive Blender** at any version
([ARCHITECTURE §5–6](ARCHITECTURE.md#5-interactive-blender-live-link)).

1. **By hand**:
   - "Open in Blender" loads the version into your running Blender.
   - You sculpt, retopo, fix UVs, paint weights or edit the rig.
   - **"Pull from Blender"** stores the scene as a new version.
   - The tool can then re-bake textures from the voxel volume onto your edited mesh, re-run rig QA,
     validate and export.
2. **By an agent** (Claude Code or Desktop, Codex, Cursor, VS Code, Gemini: your own, through MCP):
   - Setup → "Connect an agent" gives the line or config to paste into your agent, with a Test button.
   - The agent then works the library like you do: imports a file, runs steps, looks at the result as a
     picture, validates and exports straight into your game project; with Blender MCP Pro it also edits
     the live Blender scene.
   - Every step it runs is a new version you see appear in the app; nothing it does overwrites or deletes.
3. **Automatic, reproducible jobs** (QuadriFlow, Smart UV, FBX/USD export, bake fallback) still run
   in background Blender, so presets are repeatable regardless of what's open in your session.

## ⑧ Validate & export

- **Production checklist**, auto-evaluated and shown as pass/warn/fail:
  - polycount vs. budget; texel density; texture sizes are powers of two
  - no non-manifold edges or open borders (where required); the surface not folded through itself, no
    part inside another (ray parity)
  - real-world scale; origin and forward axis; applied transforms
  - ≤4 bone influences, normalized weights
  - deformation QA pass; animation names
  - **licence paper trail**: every export folder gets `CREDITS.txt` (credit lines your game must show,
    e.g. for CC-BY clips) and `LICENSES.json` (every component that touched the asset). Non-commercial
    components can't touch an asset at all: the tool doesn't contain any (PLAN §6).
- **Export profiles**: Unity (FBX + Humanoid-ready rig), Unreal (FBX, UE5 Manny names, cm scale),
  Godot (GLB), Web (GLB + Draco/KTX2), VRChat/VRM (later), 3D print (STL/3MF, watertight, wall
  thickness).
- **LODs** (e.g. 100/50/25/10%) and a collision mesh.

## 2D pictures and pixel art

The same library also makes pictures for 2D games ([MANUAL.md](../MANUAL.md) has every panel):

- **Pictures from words**: wallpapers, backgrounds, portraits, icons and items, optionally in a pixel-art,
  painting or children's-drawing style, cut out if wanted; seamless **tiles** and **parallax layers** (a
  stage that scrolls).
- **Upscale or redraw**: a picture 2× or 4× bigger, or redrawn at any size in overlapping windows, with an
  optional mask of what to keep.
- **Pixel art**: any picture or sheet reduced to its own pixel grid and one palette, then touched up in the
  pixel editor, with frames, timings and tags.
- **Animate a 2D character**: draw an animated 3D character from the side as pixel sprites and export a
  Godot `SpriteFrames` scene or a Paper2D sheet
  ([how-to](howto/animate-a-pixel-art-character.md)).

## Timing budget on the 16 GB reference card (targets to validate in Phase 0)

| Stage | Target | Measured (2026-10-04, max power) |
|---|---|---|
| ② Brief | < 10 s | ~1 s (template, no model) |
| ③ 4 candidates | < 60 s | 80–96 s incl. ~26 s model load (`docs/bench/m10.md`) |
| ④ Edits / re-pose / extra views | < 30 s each | not measured |
| ⑤ 3D preview (512) / refine (1024) | < 30 s / < 3 min | Fast 1024_cascade: median 195 s, 98–705 s (`docs/bench/phase0.md`) |
| ⑥ Production pass (prop: clean → remesh → uv → bake → validate) | < 5 min | ~45 s; whole chain incl. generate 258 s median |
| ⑧ Export | < 30 s | 2–6 s per profile |

Targets were design guesses; the third column is measured on the reference card (characters:
Phase 2).
