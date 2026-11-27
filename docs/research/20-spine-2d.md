# 20. Spine 2D skeletal animation for slot games: research and roadmap

Status: proposal, 2026-11-27, nothing built. In scope since the owner's revision of research 15 §5.7 (2026-11-27):
skeletal animation of Effigy's own 2D assets is in, video-model animation stays out (§2). The milestones are named
**SP0–SP7** here and get their M numbers when each one starts (M72 is taken by the Blender unwrapper, M73–M77 by
research 19's low-VRAM plan).

## 1. Bottom line

- **What it is:** Spine animation in slot games is 2D skeletal animation: symbols, characters, backgrounds and win
  effects are cut into image parts that bones move, rotate, scale and bend at runtime, instead of rendering every
  frame as an image. Small files, smooth at any frame rate, easy to re-time. It is the de facto standard in slots;
  the tool is Spine 2D by Esoteric Software.
- **What Effigy would do:** generate the art *and* the rig *and* the animations, as Spine files (skeleton JSON +
  texture atlas) that the Spine editor opens and a Spine runtime plays, and as Godot 4 `Skeleton2D` scenes.
- **Format only:** Effigy writes Spine's documented file format and never includes Spine's own code (§4).
- **Two targets from SP1, one model:** every rig lives in our own `rig2d.json`. The Spine exporter and the Godot
  exporter both write from it, and the preview plays it. Godot is MIT and runs headless, so the tests can import and
  play every Godot export automatically; that proves the rig and clips before the Spine translation is checked by
  hand.
- **Effigy's head start:** concepts and cut-outs for symbols, the `layers` step for backgrounds (already a depth-split,
  inpainted layer stack), and rigged, animated 3D characters, which turn into clean 2D part layers with a projected
  skeleton and converted clips: for characters made in 3D, the hardest step of 2D rigging (splitting a picture into
  parts and painting what is hidden) disappears.
- **Order:** SP0 contracts → SP1 core + both exporters → SP2 symbols and the slot clip library → SP3 backgrounds →
  SP4 win effects → SP5 characters from 3D → SP7 optional targets. SP6 (characters from a single picture) is parked.
  About 35–50 working days in all.
- **Fit:** a new 2D task **Animate** before Finish, the `spine` and `godot-skeleton2d` export profiles in Finish,
  characters through the 3D Character task (§2). No in-app animation editor.

## 2. Where it fits in Effigy's workflow

Checked against WORKFLOW.md, PLAN §1 and research 15 (2026-11-27):

- **Scope:** research 15 §5.7 was revised by the owner: skeletal animation (bones, meshes, keyframed clips) of
  Effigy's own pictures, layers and characters is in scope; video models stay out. PLAN §1's non-goal still holds:
  Effigy is not a DCC tool, so it generates the rig and clips and lets the user choose and tune them (template,
  intensity, duration, loop); a timeline, curve or weight editor is the Spine editor's (or Godot's) job, as Blender is
  for 3D (WORKFLOW ⑦).
- **3D asset journey** (Reference → Generate → Prepare → Textures → Character → Finish): SP5 sits in **Character**
  beside `sprites` (the existing 3D → 2D route: an animated character drawn as 2D), its export in **Finish**.
- **2D asset journey** (Picture → Pixelize → Layers → Tiles → Edit → Finish): a new task **Animate** between Edit and
  Finish (`ui/src/workflow.ts` `TASKS_2D`) for symbols (from Picture), backgrounds (from Layers) and effects; the
  exports in **Finish** beside `godot-2d`, `unreal-2d` and `aseprite`.
- **Agents:** the same steps over MCP (`describe_steps`, `run_step`), like every step.

| Part | Fit | Where |
|---|---|---|
| SP1 core, Spine + Godot exporters, preview | fits | 2D Animate (first kind: symbol) + Finish |
| SP2 symbols, SP3 backgrounds, SP4 win effects | fits (after the §5.7 revision) | 2D: Animate → Finish |
| SP5 characters from 3D | fits best: research 15's "2D characters from the 3D rig", skeletal instead of frames | 3D: Character → Finish |
| SP6 characters from a single picture | **parked**: research 15 §4.4's identity finding and the 3D route (SP5) cover characters better | – |
| SP7 DragonBones, Lottie, Unity | optional, on demand | Finish |
| In-app clip/curve/weight editing | out (not a DCC) | the Spine editor, Godot |

## 3. What slot games animate

| Content | Typical animations | Features used |
|---|---|---|
| **Symbols** (fruit, gems, cards, letters, wilds, scatters) | idle (breathe, float, glint), land/drop on reel stop, anticipation, small win, big win, transform (wild expand), dim | a few parts, 1–5 bones, scale/rotate/translate keys, slot colour (flash), clipping (shine sweep), additive glow slot |
| **Characters** (mascots, hosts) | idle, react to win, celebrate, talk, point | 10–30 bones, weighted meshes (bending limbs, belly), draw-order changes, skins |
| **Backgrounds** | slow drift, parallax, foliage/water sway, clouds, flicker | a few large layers, translate loops, mesh deform on foliage, additive light layers |
| **Win effects** | light rays, glow pulse, coin/star bursts, sparkles, flames, "BIG WIN" text pop | additive slots, many short-lived bones, sequence attachments (frame loops inside Spine, 4.1+), events for sound |

Spine has no particle system of its own: slot games either fake particles with many bones or use the engine's
particles beside Spine. Frame-by-frame pieces (flames, sparkles) live inside Spine as sequence attachments.

## 4. Boundary: Spine's format, not Spine's code

- Effigy writes Spine's publicly documented JSON (and later binary) format and its atlas text format; Spine imports
  data from other tools in this format. All of it is our own code.
- Effigy never contains, depends on or calls Spine's own code: no spine-runtimes (spine-ts, spine-core,
  spine-player, or wrappers that contain them), not as a dependency, not in tests, not in the preview. Our preview
  renders our own data model (§6). Checking the files in the Spine editor and the game's runtime happens outside
  Effigy (§8).
- Godot (MIT) has no such boundary: the tests may run it headless.
- Generated art follows the rest of the 2D studio (PLAN §6; research 15): Z-Image, BiRefNet, our Blender renders;
  `CREDITS.txt` and `LICENSES.json` carried into the export like every other export.

## 5. What Effigy has today

| Need | Existing piece | Notes |
|---|---|---|
| Symbol and prop pictures | `concepts` (op `image`, `cutout`), `prep` (BiRefNet) | stills of any aspect, transparent background |
| Background layer stacks | `depth` (MoGe-3) → `layers` (`layer_<k>.png` far→near, inpainted; `layers.json` with `scroll_scale`) | the closest existing feature to this output |
| Characters | `trellis2` → prepare → `rig_fit`/`rig_skin` (`rigged.glb`, `skeleton.json`, ≤ 4 influences) → `anim`/`motion`/`mocap` clips | 52-joint template, clips in `rigged_anim.glb` |
| Orthographic renders of a rigged model | `sprites` / `blender_ops/render_sprites.py` (toon or flat, per yaw, per frame) | the base for SP5's per-part renders |
| 2D export plumbing | `steps/_engine2d.py`, `export` (2D profiles: `godot-2d`, `unreal-2d`, `aseprite`) | text writers, no engine called |
| Engine checks | `docs/bench/engine-import.md`: Godot 4.7.1 `--headless --import` + an `extends SceneTree` script, on the laptop | SP1 brings the Godot part into the test suite |
| Pose keypoints | DWPose (rig_fit, mocap) | only for the parked SP6 |
| Inpainting | Z-Image inpaint (used by `layers`, `refine`) | only for the parked SP6 |
| Viewer | three.js viewer (`ui/src`) | SP1 adds a 2D rig player beside it |

Missing: a 2D rig data model, the Spine and Godot writers, an atlas packer, 2D meshes and weights, 2D keyframe clips,
a 2D rig player.

## 6. Design

### 6.1 `rig2d.json`, the one model

Ours, versioned (`"rig2d": 1`), y up, pixels as units, origin at the asset's anchor (a symbol's centre or base, a
background's bottom-left, a character's feet):

- `bones`: `name`, `parent`, `length`, rest `x`, `y`, `rotation` (degrees), `scale_x`, `scale_y`; parents before
  children.
- `slots` (draw order = list order): `name`, `bone`, `attachment` (default), `blend` (`normal|additive|multiply|
  screen`), `color` (RGBA).
- `attachments` per slot: `region` (image, width, height, offset, rotation), `mesh` (image, `uvs`, `triangles`,
  `hull`, vertices either unweighted on the slot's bone or weighted `[(bone, x, y, weight), …]`, ≤ 4 per vertex),
  `clipping` (polygon + end slot), `sequence` (frame images, fps, mode `loop|once|pingpong`).
- `animations`: per bone `rotate`/`translate`/`scale` keys (`time`, value, curve `linear|stepped|bezier[4]`); per slot
  `color` and `attachment` keys; `draw_order` keys; `events` (`name`, `time`, optional string/int).
- `meta`: source version, kind, template, generator params, the atlas policy (page size, padding, premultiplied).

### 6.2 What each target can show

| `rig2d` feature | Spine JSON | Godot 4 scene | Preview |
|---|---|---|---|
| bones, rest pose | `bones` | `Skeleton2D` + `Bone2D` (rest transform) | yes |
| region | region attachment + atlas region | `Sprite2D` (atlas texture) under the bone | yes |
| weighted mesh | mesh attachment (weighted vertices) | `Polygon2D` with `skeleton` + per-bone weights | yes |
| blend modes | slot `blend` | `CanvasItemMaterial.blend_mode` (mix, add, mul; **screen has no Godot mode**: mapped to add, flagged in `README.txt`) | yes |
| clipping | clipping attachment | parent `clip_children` (clip only) | yes |
| sequence | sequence attachment | `Sprite2D` `frame`/texture keys on an `AnimationPlayer` track | yes |
| colour keys | slot `rgba` timeline | `modulate` track | yes |
| attachment keys | slot `attachment` timeline | `visible`/`texture` tracks | yes |
| draw-order keys | `drawOrder` timeline | `z_index` tracks | yes |
| events | `events` + event keys | method-call track (`emit_signal("rig_event", name)`) | listed |
| Bézier curves | 4.x `curve` arrays | baked keys at 30 fps (Godot's Bézier tracks set handles per key, not per segment as Spine does; baking keeps both targets identical) | yes |

Anything a target cannot show is written as the closest equivalent and listed in that export's `README.txt`; the
validation step warns about it.

### 6.3 Steps, outputs, params (the SP0 contract rows)

- **`rig2d`** (CPU, `LIGHT`; SP5's renders in Blender): in: a version with `image` (symbol, effect), a `layers`
  version (background), a rigged version (character). Out: `rig2d.json`, `parts/<slot>.png`, `preview.webp`,
  `report.json` (bone/slot/vertex/triangle counts, atlas estimate, warnings). Params: `kind`
  (`symbol|background|effect|character`), `template`, `parts` (`whole|split|masks`), kind-specific options.
- **`clips2d`** (CPU, `LIGHT`): in: a `rig2d` version (and, for characters, its animated source). Out: the same files
  with `animations` replaced or added, `clips.json` (name, duration, loop, events). Params: `clips`
  (`[{"name", "template", "intensity", "duration", "loop", "easing"}]`), `replace` (bool), for characters
  `from_3d: [clip names]`. Re-timing never rebuilds the art.
- **`export`** new 2D profiles:
  - `spine`: `<asset>.json` (pinned version), `<asset>.atlas`, `<asset>.png` (and `<asset>_2.png`…),
    `README.txt` (Spine version, premultiplied alpha or not, scale, anything not shown), `CREDITS.txt`,
    `LICENSES.json`.
  - `godot-skeleton2d`: `<asset>.tscn` (root `Node2D` → `Skeleton2D` + parts, an `AnimationPlayer`), `<asset>.png`
    atlas (or loose PNGs), `README.txt`, `CREDITS.txt`, `LICENSES.json`. Godot 4.7.
- **`validate`** on a `rig2d` version: the checklist of §8 into `checklist.json`.

### 6.4 Atlas, meshes, preview

- **Atlas:** our packer (MaxRects, rotation off by default, power-of-two pages up to 2048 or 4096, padding and edge
  bleed against filtering seams, optional premultiplied alpha), writing the libgdx-style atlas text Spine reads; the
  Godot export uses the same page PNG through `AtlasTexture`s. Pure Python + Pillow; no new dependency.
- **2D meshes:** from each part's alpha: outline (marching squares, simplified), interior points on a grid, then a
  triangulation (Blender's `bmesh` triangle fill in a `blender_ops/` op, as our other geometry runs in Blender; or
  a permissive ear-clipping/Delaunay library added through `licenses.yaml`; decided in SP3 by measurement). Never
  Shewchuk's Triangle (non-commercial distribution terms).
- **Preview:** a canvas/WebGL player in the UI (`ui/src/rig2d.ts`) for `rig2d.json`: bones → world transforms,
  weighted mesh skinning, slot colour, blend modes, clipping, sequences, timeline sampling. It is also the test
  oracle: a Python reference sampler (`steps/_rig2d.py`) computes the same world transforms, and both must agree.
- **UI:** the 2D task **Animate**: kind, template, parts, the clip picker with intensity, duration and loop,
  the preview with play/scrub and clip switch, and the budget counts from `report.json`. In the 3D Character task,
  "2D skeleton (Spine / Godot)" beside "Sprites".

## 7. Roadmap

| Milestone | Delivers | Effort (days) | Depends on |
|---|---|---|---|
| SP0 | contract rows, schema, decisions recorded | 1–2 | the owner's §10 answers |
| SP1 | `rig2d.json`, atlas, Spine + Godot exporters, headless Godot harness, preview v1, one-part symbol with 3 clips | 7–10 | SP0 |
| SP2 | symbol parts, templates, the slot clip library, `clips2d`, Animate task | 5–7 | SP1 |
| SP3 | backgrounds from `layers`, the first meshes and weights, seamless loops | 5–7 | SP1 (SP2 for `clips2d`) |
| SP4 | win effects: rays, glow, bursts, sequences, text pop, events | 5–8 | SP2 |
| SP5 | characters from 3D: part renders, projected skeleton, weights, converted clips, draw order | 8–12 | SP3 (meshes) |
| SP6 | characters from a single picture | parked | – |
| SP7 | DragonBones, Lottie, Unity exporters | 3–5 each, on demand | SP1 |

Every milestone is one PR (IMPLEMENTATION §7: acceptance checks in the description), tests here, the headless Godot
checks in the suite, and a short owner check in the Spine editor and the game's runtime, recorded in
`docs/bench/<milestone>.md`.

### SP0: contracts and decisions (1–2 days)

- IMPLEMENTATION §3 rows for `rig2d`, `clips2d`, the `spine` and `godot-skeleton2d` export profiles and the 2D
  `validate` checklist (§6.3); `rig2d.json`'s schema (`docs/schemas/rig2d.schema.json`) with a fixture.
- The owner's §10 answers recorded: Spine version (4.2 assumed), runtime, premultiplied alpha, page size,
  resolutions, clip names and durations.
- Confirm the Spine 4.2 JSON fields against the official format page and the editor's own export of a hand-made
  test skeleton (the owner exports one from Spine; its JSON becomes a reference fixture of what the editor writes).
- **Exit:** the contract PR merged; nothing behind it changes later without a decision.

### SP1: core, both exporters, preview (7–10 days)

- **Code:** `steps/_rig2d.py` (model, validation, reference sampler), `steps/_atlas.py`, `steps/_spine.py` (writer,
  one pinned version), `steps/_godot2d.py` (`.tscn` writer), `steps/rig2d.py` (`kind: symbol`, `parts: whole`: one
  root bone, one slot, one region), built-in clips `idle` (scale breathe), `win` (bounce + flash), `land`
  (drop + squash), `export` profiles `spine` and `godot-skeleton2d`, `ui/src/rig2d.ts` (preview v1: regions, bone
  transforms, rotate/translate/scale/colour timelines, Bézier sampling), MCP descriptions.
- **Headless Godot harness:** `tools/godot_check.py` + `tests/godot/check_rig.gd` (an `extends SceneTree` script):
  import the export into a scratch project, instantiate the scene, list bones/parts/animations, play each animation
  and sample bone transforms at set times, print JSON. Godot 4.7.1 pinned by SHA-256 like gltfpack
  (`server/godot_install.py`, `EFFIGY_GODOT` to override); tests skip without it, as the Blender tests do.
- **Tests here:** golden-file tests of the Spine JSON and atlas for a fixture symbol; schema checks (every slot's
  bone exists, parents before children, keys sorted, curves valid, atlas regions inside pages, no overlap); the
  reference sampler against hand-computed transforms; **Godot's sampled transforms equal the reference sampler's**
  (within 0.01 px / 0.01°) for every clip; the preview's sampled transforms equal the reference sampler's
  (Playwright); ruff, tsc.
- **Owner:** the Spine export opens in the Spine editor (Import Data) and the three clips play as in the preview; the
  game's runtime loads it; the Godot scene plays in the editor.
- **Exit:** both exporters write the same rig; Godot proves it automatically; Spine confirmed once by hand.

### SP2: symbols and the slot clip library (5–7 days)

- **Parts:** `whole`, `split` (body + highlight + shadow + outline from the alpha and luminance), `masks` (a mask per
  part painted in the UI's painter).
- **Templates:** `single`, `pendulum` (hangs and swings), `stack` (letters, cards), `pivot-bottom` (stands and
  squashes).
- **Clip library** (`steps/_clips2d.py`, procedural, parameterised by intensity, duration, loop, easing): idle
  breathe/float/glint, land, anticipation (tremble), small win, big win, transform/expand (wild), dim (losing
  symbols), **shine sweep** (clipping + a moving additive bar), **additive glow** slot.
- **`clips2d` step** (add/replace/re-time); clip names from SP0's conventions.
- **UI:** the **Animate** task in `TASKS_2D`, kind `symbol`; the clip picker; Finish offers both exports.
- **Tests:** each clip's keys (loops close: first pose = last pose; durations; easing in range); Godot plays every
  clip of a golden symbol set (the golden props cut out as symbols); Playwright for the Animate task and the picker.
- **Owner:** a reel of golden symbols played in the game's runtime at game speed; the clip library's look judged
  (intensity presets tuned from it).
- **Exit:** a symbol from a picture to both exports in one task, with the full clip set.

### SP3: backgrounds from `layers` (5–7 days)

- `rig2d kind: background` from a `layers` version: one bone + slot per layer (far → near), each layer's
  `scroll_scale` kept in `meta` for engine parallax.
- **2D meshes and weights** (§6.4), the triangulation chosen here by measurement (tearing, vertex count, speed).
- **Clips:** `ambient` (slow drift by depth), `sway` (mesh deform on a selected layer: foliage, cloth, water; a bone
  chain with weights falling off from the anchor edge), `flicker`/`pulse` (an additive light layer), all seamless.
- **Tests:** loop seams zero (sampled start = end); mesh checks (no flipped triangles at rest and at extreme keys,
  every vertex weighted, ≤ 4 bones per vertex, weights sum to 1); Godot's skinned `Polygon2D` vertex positions equal
  the reference sampler's at sampled times; atlas within the page limit.
- **Owner:** a background behind the reels in the runtime; frame rate on the target device.
- **Exit:** a `layers` version becomes an animated, looping background in both targets.

### SP4: win effects (5–8 days)

- `rig2d kind: effect`, or a slot group attached to a symbol: light rays (procedural rays image, additive,
  rotating), glow pulse, coin/star burst (N bones fanned out, arcs, scale-in/fade-out, seeded), sparkle and flame
  loops as **sequence attachments** (frames from `concepts` + cut-out, or rendered), "BIG WIN" text pop (a text
  image, squash, glow), **events** for sound cues (`coin`, `whoosh`, `ding`).
- **Tests:** blend modes, clipping, sequences and events in Spine JSON and Godot (Godot's method-call track fires the
  events in order, checked by the harness); budget counts (bones, vertices, attachments, atlas pages) reported and
  checked against a per-effect budget.
- **Owner:** effects layered over the reels in the runtime; frame rate with several effects at once on the target
  device.
- **Exit:** a slot win sequence (symbols + background + effects) plays in both targets.

### SP5: characters from 3D (8–12 days)

- In: a rigged, optionally animated version (`rigged.glb`, `skeleton.json`, clips). Where: the 3D **Character** task.
- **Parts by skin weights:** the 52 joints grouped into 2D parts (head, torso, pelvis, upper/lower arm, hand,
  upper/lower leg, foot per side; tail/wings for creatures); faces assigned by dominant weight; each part rendered
  alone with the same orthographic front camera (`blender_ops/render_parts2d.py`, built on `render_sprites.py`: toon
  or flat, outline, same resolution), with overlap margins at the joints so bends open no gaps.
- **Skeleton:** the 3D joints projected to the screen plane; 2D lengths and rest rotations from the projection.
- **Meshes and weights:** SP3's meshes per part, weights from the projected 3D skin weights sampled at the mesh
  vertices (2D heat diffusion where the projection is ambiguous).
- **Clips:** each 3D clip sampled per frame, joint rotations projected to in-plane angles, root motion to
  translate, keys reduced by curve fitting within a tolerance, draw-order keys where depth order changes.
  Text-to-motion (Kimodo) and mocap (GEM-X) clips go through the same converter.
- **Views:** front first; side and three-quarter later as separate exports.
- **Tests:** projected joints within a pixel of the 3D render's; part coverage (union of parts = the full-body
  render's alpha, no holes at rest and at sampled poses); key-reduction error below tolerance; draw order follows
  depth; Godot plays the converted clips and its bone transforms match the reference sampler's.
- **Owner:** a mascot's idle and celebrate in Spine and the runtime.
- **Exit:** a 3D character from Effigy becomes a 2D skeletal character with its clips, in both targets.

### SP6: characters from a single picture: parked

Characters come from 3D (SP5); research 15 §4.4 found 2D-native characters drift in identity. Reopen only if the
owner wants characters from flat art with no 3D model. Then: a 3–5-day spike (DWPose keypoints → promptable part
masks, licence per PLAN §6 → Z-Image inpainting of hidden regions → SP5's skeleton, mesh, weight and clip code) on 5
golden characters, judged by the owner before any build (8–12 days).

### SP7: optional targets (3–5 days each, on demand)

From the same `rig2d.json`, only when a project needs them: **DragonBones** JSON (MIT runtimes, abandoned upstream),
**Lottie** (Bodymovin JSON, image layers with transforms, no meshes: web symbols and UI pops), **Unity 2D
Animation** (tied to the Unity editor's asset files; last).

## 8. Validation

- **Here, every milestone:** schema and golden-file tests of the JSON, atlas and `.tscn`; the reference sampler
  against hand-computed values; the preview and **headless Godot** against the reference sampler (same transforms
  at the same times); mesh, loop and budget checks; Playwright for the UI.
- **`validate` on a `rig2d` version** (`checklist.json`): parents before children, ≤ 4 weights per vertex summing to
  1, no flipped triangles at rest or at extreme keys, loops closed, atlas pages within the limit, budget counts,
  features a target cannot show (§6.2).
- **Owner's machine** (`docs/bench/<milestone>.md`): import into the Spine editor without warnings, every clip plays
  as previewed, load in the game's runtime, frame rate and memory on the target device, atlas page count.
- **Version pin:** one Spine version at a time (`spine` field); a change of version is its own small milestone with
  the format diff checked. Godot pinned at 4.7.1 with Effigy's other Godot checks.

## 9. Risks

- **Spine format details** (4.2's fields, curves, weighted vertices): SP0's reference export from the owner's editor
  and SP1's hand check catch them early; everything else is proven through Godot.
- **Feature mismatch between targets** (§6.2: screen blend, Bézier curves, events): written as the closest
  equivalent and listed; the Spine export stays the full one.
- **Look of procedural clips:** generic bounce/pulse can feel cheap; the clip library needs the owner's taste
  (intensity presets, easing) and a few hand-tuned reference clips (SP2).
- **2D meshes:** bad triangulation shows as tearing at bends; SP3 measures it before SP5 relies on it.
- **3D → 2D characters** (SP5): limbs foreshortened in the front view, crossing arms needing draw-order keys,
  stylised characters reading too "3D": toon/flat rendering and the part overlap margins are the mitigations.
- **Performance on phones:** bone, vertex, attachment and page counts reported per asset; limits set by the owner.
- **Godot in the test suite:** a ~70 MB pinned download for the dev/test setup only, never in the app's install.

## 10. Decisions for the owner

1. Spine version and runtime: which Spine version does the game use (4.2?), and which runtime/engine (PixiJS,
   Phaser, Unity, Godot via spine-godot, Cocos)?
2. Premultiplied alpha on or off (the runtime's setting), atlas page limit (2048 or 4096), target resolution of
   symbols (e.g. 256 or 512 px) and of backgrounds.
3. Order after SP1: symbols (SP2), then backgrounds (SP3) and effects (SP4), then characters (SP5)? Or characters
   earlier? (SP6 is parked, §2.)
4. Clip naming conventions and default durations the game's code expects.

## Sources

- Spine JSON export format: https://esotericsoftware.com/spine-json-format ; binary format:
  https://esotericsoftware.com/spine-binary-format ; export: https://esotericsoftware.com/spine-export
- spine-core 4.2 `SkeletonJson` (reference for field meanings only, not to be vendored):
  https://app.unpkg.com/@esotericsoftware/spine-core@4.2.84/files/dist/SkeletonJson.js
- Godot 4 docs: `Skeleton2D`, `Bone2D`, `Polygon2D` (skeleton and weights), `AnimationPlayer`, `CanvasItem`
  (`clip_children`, `CanvasItemMaterial` blend modes): https://docs.godotengine.org/en/stable/
- Effigy: research 15 §4.3 (2D cutout rig, engine runtimes), §4.4 (identity drift), §5.7 (revised 2026-11-27);
  WORKFLOW.md; PLAN §1 (non-goals), PLAN §6 (licence policy); IMPLEMENTATION §3 (`layers`, `sprites`, `rig_skin`,
  2D `export` contracts); `docs/bench/engine-import.md` (headless Godot 4.7.1 recipe).
