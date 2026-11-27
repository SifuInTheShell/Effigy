# 20. Spine 2D skeletal animation for slot games: research and roadmap

Status: proposal, 2026-11-27, nothing built. In scope since the owner's revision of research 15 §5.7 (2026-11-27):
skeletal animation of Effigy's own 2D assets is in, video-model animation stays out (§2). The milestones are named
**SP1–SP7** here and get their M numbers when each one starts (M72 is taken by the Blender unwrapper, M73–M77 by research 19's low-VRAM plan).

## 1. Bottom line

- **What it is:** Spine animation in slot games is 2D skeletal animation: symbols, characters, backgrounds and win
  effects are cut into image parts that bones move, rotate, scale and bend at runtime, instead of rendering every
  frame as an image. Small files, smooth at any frame rate, easy to re-time. It is the de facto standard in slots;
  the tool is Spine 2D by Esoteric Software.
- **What Effigy would do:** generate the art *and* the rig *and* the animations, and export Spine's own files
  (skeleton JSON + texture atlas) that the Spine editor opens and the game's Spine runtime plays.
- **Licence shape:** Effigy writes Spine's documented file format and never ships Spine's code. The game that plays
  the files needs a Spine Editor licence (that is the owner's, for the game). Effigy's preview is our own renderer.
- **Effigy's head start:** concepts and cut-outs for symbols, the `layers` step for backgrounds (already a depth-split,
  inpainted layer stack), and rigged, animated 3D characters, which can be turned into clean 2D part layers with a
  projected skeleton and converted clips: the hardest step of 2D rigging (splitting a picture into parts and painting
  what is hidden) disappears for characters made in 3D.
- **Order:** format core + symbols (SP1–SP2) → backgrounds (SP3) → win effects (SP4) → 3D characters (SP5) →
  other engines (SP7). Picture-only characters (SP6) are parked (§2).
- **Fit:** in scope after the owner's revision of research 15 §5.7; a new 2D task **Animate** and a `spine` export
  profile in Finish; characters through the 3D Character task (§2). No in-app animation editor.

## 2. Where it fits in Effigy's workflow

Checked against WORKFLOW.md, PLAN §1 and research 15 (2026-11-27):

- **Scope:** research 15 §5.7 was revised by the owner: skeletal animation (bones, meshes, keyframed clips) of
  Effigy's own pictures, layers and characters is in scope; video models stay out. PLAN §1's non-goal still holds:
  Effigy is not a DCC tool, so it generates the rig and clips and lets the user choose and tune them (template,
  intensity, duration, loop); a timeline, curve or weight editor is the Spine editor's job, as Blender is for 3D
  (WORKFLOW ⑦).
- **3D asset journey** (Reference → Generate → Prepare → Textures → Character → Finish): SP5 sits in **Character**
  beside `sprites` (the existing 3D → 2D route: an animated character drawn as 2D), its export in **Finish**.
- **2D asset journey** (Picture → Pixelize → Layers → Tiles → Edit → Finish): a new task **Animate** between Edit and
  Finish (`ui/src/workflow.ts` `TASKS_2D`) for symbols (from Picture), backgrounds (from Layers) and effects; the
  Spine export in **Finish** beside `godot-2d`, `unreal-2d` and `aseprite`.
- **Agents:** the same steps over MCP (`describe_steps`, `run_step`), like every step.

| Part | Fit | Where |
|---|---|---|
| SP1 Spine export core | fits | Finish (export profile `spine`) + the first Animate task |
| SP2 symbols, SP3 backgrounds, SP4 win effects | fits (after the §5.7 revision) | 2D: Animate → Finish |
| SP5 characters from 3D | fits best: research 15's "2D characters from the 3D rig", skeletal instead of frames | 3D: Character → Finish |
| SP6 characters from a single picture | **parked**: research 15 §4.4's identity finding and the 3D route (SP5) cover characters better | – |
| SP7 Godot `Skeleton2D` (later Unity) | fits | Finish |
| In-app clip/curve/weight editing | out (not a DCC) | the Spine editor |

Worth building only if the game uses Spine (the owner's Spine licence); otherwise SP7 gives the same rigs to Godot
without a licence.

## 3. What slot games animate

| Content | Typical animations | Spine features used |
|---|---|---|
| **Symbols** (fruit, gems, cards, letters, wilds, scatters) | idle (breathe, float, glint), land/drop on reel stop, anticipation, small win, big win, transform (wild expand) | a few parts, 1–5 bones, scale/rotate/translate keys, slot colour (flash), clipping (shine sweep), additive glow slot |
| **Characters** (mascots, hosts) | idle, react to win, celebrate, talk, point | 10–30 bones, weighted meshes (bending limbs, belly), IK, draw-order changes, skins |
| **Backgrounds** | slow drift, parallax, foliage/water sway, clouds, flicker | a few large layers, translate loops, mesh deform on foliage, additive light layers |
| **Win effects** | light rays, glow pulse, coin/star bursts, sparkles, flames, "BIG WIN" text pop | additive slots, many short-lived bones, **sequence attachments** (frame loops inside Spine, 4.1+), events for sound |

Spine has no particle system of its own: slot games either fake particles with many bones or use the engine's
particles beside Spine. Frame-by-frame pieces (flames, sparkles) live inside Spine as sequence attachments.

## 4. Licence

- **The game:** any product that integrates the Spine Runtimes needs a valid Spine Editor licence at integration time
  (Spine Runtimes License, 2025-04-05; Spine Editor License §2). Forum answers from Esoteric: Essential below
  ~US$500k annual revenue, Enterprise above; the trial grants no runtime rights. This is the owner's purchase for the
  game, not Effigy's.
- **Effigy:** the JSON (and binary) format is publicly documented, and Spine imports data from other tools in it.
  Writing those files is our own code (MIT). Effigy must **not** bundle or depend on spine-runtimes (spine-ts,
  spine-core, spine-player), including as a dev or test dependency: the runtime licence ties every use to an editor
  licence. Our preview renders our own data model (§6).
- **Generated art:** the same models and licence profile as the rest of the 2D studio (PLAN §6; research 15): Z-Image,
  BiRefNet, our Blender renders; credits and `LICENSES.json` carried into the export like every other export.
- **Research 15** listed "Spine runtimes need a Spine Editor licence → no": still true for Effigy using Spine's code;
  this plan only writes files a licensed game plays.

## 5. What Effigy has today

| Need | Existing piece | Notes |
|---|---|---|
| Symbol and prop pictures | `concepts` (op `image`, `cutout`), `prep` (BiRefNet) | stills of any aspect, transparent background |
| Background layer stacks | `depth` (MoGe-3) → `layers` (`layer_<k>.png` far→near, inpainted; `layers.json` with `scroll_scale`) | the closest existing feature to Spine output |
| Characters | `trellis2` → prepare → `rig_fit`/`rig_skin` (`rigged.glb`, `skeleton.json`, ≤ 4 influences) → `anim`/`motion`/`mocap` clips | 52-joint template, clips in `rigged_anim.glb` |
| Orthographic renders of a rigged model | `sprites` / `blender_ops/render_sprites.py` (toon or flat, per yaw, per frame) | the base for SP5's per-part renders |
| 2D export plumbing | `steps/_engine2d.py`, `export` (2D profiles: `godot-2d`, `unreal-2d`, sheets) | text writers, no engine called; Spine becomes another 2D profile |
| Pose keypoints | DWPose (rig_fit, mocap) | for SP6's 2D skeleton from a picture |
| Inpainting | Z-Image inpaint (used by `layers`, `refine`) | for SP6's hidden part regions |
| Viewer | three.js viewer (`ui/src`) | SP1 adds a 2D rig player beside it |

Missing: a 2D rig data model, a Spine JSON writer, an atlas packer, 2D meshes and weights, 2D keyframe clips, a 2D rig
player, and part splitting for flat pictures.

## 6. Design

- **One internal format, `rig2d.json`** (ours, versioned): bones (name, parent, length, rest x/y/rotation/scale),
  slots (name, bone, attachment, blend `normal|additive|multiply|screen`, colour, draw order), attachments (region:
  image + size + offset; mesh: image, uvs, triangles, hull, vertices bound to bones with weights; clipping; sequence:
  frame images + fps), and animations (per bone: rotate/translate/scale keys with Bézier or stepped curves; per slot:
  colour/attachment keys; draw-order keys; events). Spine JSON, a Godot `Skeleton2D` scene (SP7) and our preview are
  writers/readers of this one model, so no step depends on Spine's version details.
- **Steps** (each a new version, append-only like every step):
  - `rig2d`: builds `rig2d.json` + part images from an input version (a picture, a `layers` version, a rigged 3D
    version); `params.kind` = `symbol|background|effect|character`, a template, and options.
  - `clips2d`: adds or replaces animations in a `rig2d` version from the clip library (§7, SP2/SP4) or converts 3D
    clips (SP5). Separate from `rig2d`, so re-timing never rebuilds the art.
  - `export` profile **`spine`**: `<asset>.json` (Spine JSON, pinned version), `<asset>.atlas` + page PNGs, optional
    `.skel` later, `CREDITS.txt`, `LICENSES.json`, `README.txt` (runtime version, premultiplied alpha or not, scale).
- **Atlas:** our packer (MaxRects, rotation off by default, power-of-two pages up to 2048 or 4096, padding and edge
  bleed against filtering seams, optional premultiplied alpha), writing the libgdx-style atlas text Spine reads.
  Pure Python + Pillow; no new dependency.
- **2D meshes:** from each part's alpha: outline (marching squares, simplified), interior points on a grid, then a
  triangulation (Blender's `bmesh` triangle fill in a `blender_ops/` op, as our other geometry runs in Blender; or a
  permissive ear-clipping/Delaunay library added through `licenses.yaml`; decided in SP3/SP5 by measurement). Never
  Shewchuk's Triangle (non-commercial distribution terms).
- **Preview:** a small canvas/WebGL player in the UI for `rig2d.json` (bones → world transforms, weighted mesh
  skinning, slot colour, blend modes, clipping, sequences, timeline sampling). Our code, our data; it is also the
  test oracle for "what we wrote is what we meant". The owner's check in Spine and the game runtime stays the
  ground truth for "what Spine reads is what we wrote".
- **MCP and UI:** both steps and the export profile through the existing surfaces (describe_steps, run_step, the 2D
  task list); a "Spine" task in the 2D studio with kind, template, clip picker and preview.

## 7. Roadmap

Effort is for code and tests here; every milestone ends with a check in the Spine editor and the target runtime on
the owner's machine (needs the owner's Spine licence).

### SP1: Spine format core (symbol with one part)

- `rig2d.json` model + validation (`steps/_rig2d.py`); Spine JSON writer for one pinned version (`steps/_spine.py`);
  atlas packer (`steps/_atlas.py`); `export` profile `spine`.
- `rig2d` step, `kind: symbol`, single part: any version with `image` → one root bone, one slot, one region.
  Three built-in clips to prove timelines: `idle` (scale breathe), `win` (bounce + flash), `land` (drop + squash).
- Preview player v1 (regions, bone transforms, rotate/translate/scale/colour timelines, Bézier curves).
- **First task:** pin the Spine version (4.2 unless the owner's runtime says otherwise) and read the official JSON
  format page and spine-core 4.2's `SkeletonJson` *documentation* (not code into our repo) for every field we write:
  skeleton header (`spine` version string, size, images path), bones, slots, skins (4.x: an array of skins with
  `attachments`), region/mesh attachment fields, timeline key format (4.x: `time`, `value`, `curve` arrays), weighted
  vertex encoding, atlas page/region syntax and `pma`.
- **Acceptance here:** golden-file tests of the JSON and atlas for a fixture symbol; schema checks (every slot's bone
  exists, parents before children, keys sorted, curves valid, atlas regions inside pages, no overlap); the preview
  samples the same transforms as a Python reference sampler; ruff, tsc, Playwright (Spine task, export).
  **Owner:** opens the export in Spine (Import Data), plays the three clips; loads it in the game's runtime.
- Effort: 4–6 days.

### SP2: Symbols and the slot clip library

- Part options for symbols: whole image, or split into body + highlight + shadow + outline (from the alpha and
  luminance), or user-masked parts (a mask per part from the UI's painter).
- Template skeletons: `single`, `pendulum` (hangs and swings), `stack` (letters/cards), `pivot-bottom` (stands and
  squashes).
- Clip library (procedural, parameterised by intensity, duration, loop, easing): idle breathe/float/glint, land,
  anticipation (tremble), small win, big win, transform/expand (wild), dim (losing symbols), plus a **shine sweep**
  (clipping attachment + a moving additive bar) and an **additive glow** slot.
- `clips2d` step (add/replace/re-time clips); naming conventions slot developers expect (`idle`, `win`, `land`, …,
  configurable).
- **Acceptance:** each clip's keys checked (loops close: first = last pose; durations; easing), a golden symbol set
  (the golden props as symbols) exported; Playwright for the clip picker and preview. **Owner:** a reel of symbols
  played in the runtime at game speed.
- Effort: 5–7 days.

### SP3: Backgrounds from `layers`

- `rig2d kind: background` from a `layers` version: one bone + slot per layer (far → near draw order), the layer's
  `scroll_scale` kept as metadata for engine parallax.
- Clips: `ambient` (slow drift per layer by its depth), `sway` (mesh deform on a selected layer: foliage, cloth,
  water; a bone chain with weights falling off from the anchor edge), `flicker`/`pulse` (additive light layer),
  seamless loops (all timelines end where they start).
- First 2D meshes and weights (§6), shared with SP5.
- **Acceptance:** loop seams zero (sampled start = end), mesh checks (no flipped triangles at rest and at extreme
  keys, every vertex weighted, ≤ 4 bones per vertex, weights sum to 1), atlas size within the chosen page limit.
  **Owner:** a background behind the reels in the runtime, frame rate checked on the target device.
- Effort: 5–7 days.

### SP4: Win effects

- Effect kit, each as a `rig2d kind: effect` asset or a slot group to attach to a symbol: light rays (procedural
  rays image, additive, rotating), glow pulse, coin/star burst (N bones fanned out with spread, gravity-like
  arcs, scale-in/fade-out, randomised but seeded), sparkle and flame loops as **sequence attachments** (frames from
  `concepts` + cut-out, or rendered; frame interpolation via RIFE if research 15's row is built), "BIG WIN" text pop
  (text image from the brief, squash, glow), **events** for sound cues (`coin`, `whoosh`, `ding`).
- **Acceptance:** blend modes and sequences round-trip in our preview; bone counts and atlas size reported against a
  per-effect budget (slot games run many effects at once on phones). **Owner:** effects layered over the reels in
  the runtime, frame rate on the target device.
- Effort: 5–8 days.

### SP5: Characters from 3D (Effigy's shortcut)

- Input: a rigged (and optionally animated) version (`rigged.glb`, `skeleton.json`, clips).
- **Parts by skin weights:** group the 52 joints into 2D parts (head, torso, pelvis, upper/lower arm, hand,
  upper/lower leg, foot per side; tail/wings for creatures), assign faces by dominant weight, and render each part
  alone with the same orthographic front camera (`blender_ops/render_parts2d.py`, built on `render_sprites.py`:
  toon or flat, outline, same resolution). Every part is complete; nothing needs inpainting. Overlap margins at the
  joints (render each part with its neighbours' faces near the joint) so bends do not open gaps.
- **Skeleton:** the 3D joints projected to the screen plane; 2D bone lengths and rest rotations from the projection.
- **Meshes and weights:** §6's meshes per part; weights from the projected 3D skin weights sampled at the mesh
  vertices (or 2D heat diffusion where the projection is ambiguous).
- **Clips:** each 3D clip sampled per frame, joint rotations projected to in-plane angles, root motion to translate,
  keys reduced (curve fitting within a tolerance), **draw-order keys** where depth order changes (an arm crossing in
  front of the body). Text-to-motion and mocap clips become Spine clips through the same converter.
- Views: front first; side and three-quarter as separate skeletons later (Spine skins or separate exports).
- **Acceptance:** projected joint positions match the 3D render's within a pixel; part coverage (union of parts =
  the full-body render's alpha, no holes at rest and at sampled poses); key reduction error below tolerance; draw
  order follows depth. **Owner:** a mascot idle and celebrate clip in Spine and the runtime.
- Effort: 8–12 days.

### SP6: Characters from a single picture (2D-native art): parked

- **Parked (§2):** characters come from 3D (SP5); research 15 §4.4 found 2D-native characters drift in identity.
  Reopen only if the owner wants characters from flat art that has no 3D model. Research-gated; the model question
  is open. Pipeline: pose keypoints (DWPose) → part masks (SAM 2-class
  promptable segmentation with point prompts per limb, licence per PLAN §6; See-through and Stretchy Studio as
  references) → hidden-region inpainting per part (Z-Image inpaint, as `layers`) → SP5's skeleton, mesh, weight
  and clip machinery with 2D bones from the keypoints.
- **Gate:** a spike on 5 golden characters judged by the owner (part edges, inpaint quality under arms and legs)
  before building the step.
- Effort: spike 3–5 days; build 8–12 days if it passes.

### SP7: Other targets from the same `rig2d.json`

- Godot 4 `Skeleton2D` + `Bone2D` + `Polygon2D` scene with an `AnimationPlayer` (MIT runtime, no licence needed),
  then Unity 2D Animation if wanted. Only after SP1–SP3 prove the model.
- Effort: 3–5 days per engine.

## 8. Validation

- **Here, every milestone:** schema and golden-file tests of the JSON and atlas, the preview against a Python
  reference sampler (same transforms at the same times), mesh and loop checks, Playwright for the UI.
- **Owner's machine:** a checklist in `docs/bench/spine.md` per milestone: import into the Spine editor without
  warnings, every clip plays as previewed, export from Spine and load in the game's runtime, frame rate and memory
  on the target device, atlas page count.
- **Version pin:** one Spine version at a time (`spine` field in the JSON); a change of version is its own small
  milestone with the format diff checked.

## 9. Risks

- **Format drift between Spine versions** (3.8 → 4.0 → 4.1 → 4.2 changed timelines and skins): pin one, test against
  the owner's editor and runtime.
- **Look of procedural clips:** generic bounce/pulse can feel cheap; the clip library needs the owner's taste
  (intensity presets, easing) and a few hand-tuned reference clips.
- **2D meshes:** bad triangulation shows as tearing at bends; SP3 measures it before SP5 relies on it.
- **3D → 2D characters** (SP5): limbs foreshortened in the front view, crossing arms needing draw-order keys,
  stylised characters reading too "3D": toon/flat rendering and the part overlap margins are the mitigations.
- **Picture → parts** (SP6): the open model question; gated by a spike.
- **Performance budget on phones:** bone, vertex and draw-call counts reported per asset; limits set by the owner.

## 10. Decisions for the owner

1. Spine version and runtime: which Spine version does the game use (4.2?), and which runtime/engine (PixiJS,
   Phaser, Unity, Godot via spine-godot, Cocos)?
2. Who holds the Spine licence for the game (needed for the owner's validation runs from SP1 on)?
3. Premultiplied alpha on or off (the runtime's setting), atlas page limit (2048 or 4096), target resolution of
   symbols (e.g. 256 or 512 px) and of backgrounds.
4. Order after SP1: symbols (SP2), then backgrounds (SP3) and effects (SP4), then characters (SP5)? Or characters
   earlier? (SP6 is parked, §2.)
5. Clip naming conventions and default durations the game's code expects.

## Sources

- Spine Runtimes License Agreement (2025-04-05): https://raw.github.com/EsotericSoftware/spine-runtimes/master/LICENSE
- Spine Editor License Agreement: https://en.esotericsoftware.com/spine-editor-license
- Esoteric, "Our new licensing explained": https://us.esotericsoftware.com/blog/Our-new-licensing-explained
- Purchase Spine: https://esotericsoftware.com/spine-purchase
- Forum, licence for runtimes: https://ru.esotericsoftware.com/forum/d/17246-license-for-runtimes ;
  runtimes and licensing: https://ar.esotericsoftware.com/forum/d/4984-runtimes-ampamp-licensing
- Spine JSON export format: https://esotericsoftware.com/spine-json-format ; binary format:
  https://esotericsoftware.com/spine-binary-format ; export: https://esotericsoftware.com/spine-export
- spine-core 4.2 `SkeletonJson` (reference for field meanings only, not to be vendored):
  https://app.unpkg.com/@esotericsoftware/spine-core@4.2.84/files/dist/SkeletonJson.js
- Research 15 §4.3 (2D cutout rig, engine runtimes), §4.4 (identity drift), §5.7 (revised 2026-11-27), WORKFLOW.md,
  PLAN §1 (non-goals), PLAN §6 (licence policy), IMPLEMENTATION §3 (`layers`,
  `sprites`, `rig_skin`, 2D `export` contracts).
