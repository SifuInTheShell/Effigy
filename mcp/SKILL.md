---
name: effigy
description: Make game-ready 3D assets with Effigy from a description, a picture or a file - generate, clean, rig, animate, check, export - as versions in a local library, one GPU job at a time.
---

# Effigy for agents

An **asset** is a folder of **versions** (`001_upload`, `002_clean`, ...). Every step reads a parent
version and writes a new one; nothing is overwritten and **you cannot delete**: the library is append-only
for agents (the owner deletes in the app). Pick the parent yourself: any version can be the parent of
several (try two remeshes, keep both). `get_asset` shows the tree with labels as the app shows them.

## Working rules

- **One GPU job at a time.** Jobs queue; do not start a second heavy step while one runs. A tool waits at
  most `wait_s` (default 40, max 45) and then returns `{"status": "running", "job_id"}`: call
  `job_status(job_id, wait_s=40)` until `done`. Generating takes minutes, a first run loads models (~35 s).
- Running the identical request (asset, step, parent, params) while it runs returns that job: retrying is safe.
- Cancelling your call cancels the job it started. `cancel_job(job_id)` stops any job.
- Errors read `code: message, how to fix`; fix the cause and run the step again (a new version).
- `describe_steps(step)` gives a step's whole docstring (every param); this guide gives the usual ones.
- Look at results (`look`) before moving on; a bad version is cheaper to drop than to export.

## Pipelines

**From a description.** `create_asset(name)`, `run_step(asset, null, "brief", {"prompt": "dwarf blacksmith",
"category": "character"|"prop"|"creature"})`, `concepts` on the brief (4 candidate images, ranked; `look` them),
`prep` on the concepts version with `{"pick": k}` (cut-out), then generate (below). Say what you want, not
what you don't: "no end post" draws posts; "a section cut out of a long wall, cut off square" does not.

**From a picture.** `import_file("<abs path>.png")` runs `prep` for you (cut-out). A character that will be
rigged: `canon` on the prep version re-poses it to an A-pose first (`{"seed", "steps"}`), then `prep` on the result.
A flat picture (pixel art, a drawing) needs `{"look": "render"}` too: it is redrawn shaded, or its model comes out
flat as a cardboard standee.

**From a file.** `import_file("<abs path>.glb|.fbx|.obj|.zip ...")` uploads and runs `import_model`
(`{"rig": true, "scale": 1}`): a known skeleton is kept (use `still=true` to drop it), the result has
`mesh.glb` or `rigged.glb` (+ clips). Then continue below at the step you need: an imported rigged model can
go straight to `anim_edit` or `export`. A video is only uploaded: `run_step mocap` on it makes a clip of the
largest person (`metrics.people` counts the people at the start; `{"person": 1}` makes the next; `{"camera":
"panning"}` when the camera turned to follow). An imported file is a new asset with no lineage: its credits start
empty. To edit a generated model and keep its licences, go through Blender (`open_in_blender`, edit,
`pull_from_blender`), not through a file. With no Blender
running, `open_in_blender` starts the owner's when Setup's "Start Blender when it is needed" is on; otherwise it
answers `blender_not_connected`: ask the owner, and never start a Blender yourself (two Blenders race for the
add-on's port, and the one the owner sees may lose). A move needs no
Blender: `run_step(asset, <a still mesh version>, "transform", {"level": true, "rotate_deg": [0, 90, 0], "height_m":
1.0})` stands it up, turns it, scales it and grounds it in about a second, its textures kept (`scale: [x, y, z]`
fits a brief per axis, `translate_m` moves it; a rigged model is refused). `{"square": true, "face": "-z"}` turns it
square to the axes and its front (the side the picture's camera saw) toward -Z, the street side: no angles to work
out. A later bake follows it. A detail mirrored
under a thing that sits on its base (a block's lifting eye) holds it up off the ground: clean `{"press_to_floor":
true}` presses a small part under the middle of a broad flat base onto it. A concept for a
prop asks for "the camera level at mid height" by itself (a concept shot from above makes a model that leans);
naming your own view or camera in the prompt replaces it. With two
versions of one asset open, `pull_from_blender(asset, version)` says which one to take and `close_in_blender(asset,
version)` removes the other (a pull without `version` takes the one opened last and says what else was open). In Blender,
weld first (Mesh > Clean Up > Merge by Distance, `remove_doubles` in bmesh): a pulled-in glTF is split along its
UV seams, and a hole fill or a cut on the unwelded mesh caps the seam loops too.

**Generate** (picture to 3D): `trellis2` on a prep version, `{"weights": "fast"|"faithful", "seed", "texture_size"}`
or a preset's name, `{"preset": "faithful"}` (describe_steps lists them; your own keys override the preset's);
`faithful` follows the picture closest and is slower. `{"preset": "best"}` is the best this card runs (the
app's default): `faithful-hd` (1536, 4K) on a 24 GB card, `faithful-hq` (20 steps, 4K) on 16 GB. Texture detail
(`refine`) takes the full Tile by itself where it is installed and fits; `{"tile": "lite"}` forces the light
one. Without `op` it generates; retexture an existing mesh
with `{"op": "texture"}`.

**Game-ready preparation, in order**, each on the previous version:
`clean` (`{"height_m": 1.8}` rescales; repairs holes and non-manifold edges; a prop generated from a
three-quarter picture comes out turned 30–45° and may lean. Clean stands a generated model upright by itself (`level`,
default `"auto"`: on when a trellis2 version is up the chain; `{"level": false}` keeps it as it came) and drops small
scraps floating off its outside (`drop_floating`, the same `"auto"`; `metrics.dropped_parts`); `{"straighten":
true}` also squares it to the axes: it levels its floor (a post with no floor to
stand on, over twice as tall as wide, on its shaft's axis) and turns its
long side onto X, the broad side facing +Z; a thin panel is only turned) -> `remesh` (`{"faces": 20000,
"topology": "tri"|"quad", "symmetry": false|"x"}`; check `metrics.hausdorff_pct`; thin boards with slits tear
at any budget: `"close": 0.012` (metres) shuts the slits first; grooves narrower than it smooth out, the normal
map keeps them; without `close` a generated prop that comes back over 5 % folded through itself is remeshed again
with its length / 150, `metrics.closed`; `"close": 0` never) -> `uv` -> `bake`
(`{"texture_size": 2048, "normal_map": true}`: PBR + normal map from the generator's volume, or, for an imported model, from its own
materials and textures (recipe: `import_file` -> `clean` -> `remesh` -> `uv` -> `bake` -> `rig_fit` -> `rig_skin` ->
`anim` -> `export`; a foreign skeleton is dropped, ours is fitted; no materials at all: `missing_input`); a flat prop from a picture in perspective has one end shorter: `"detaper": true` squares it
after the texture, the tall end kept; straighten it in clean first, `metrics.detapered` null means nothing was done) -> optional `project` (`{"region": "head"|"front"}`, sharper face from the picture) ->
optional `refine` (`{"strength": 1}`, 4–5 min: the texture's fine detail redrawn from close-up views; the side
the picture never showed is where a bake is weakest; a model wider than tall gets no view from above, which
invented tiles on flat tops; `views` takes your own list, each view with its own `prompt` if a side is of another
material. Describe the material, not the place: "construction site" in a hoarding's prompt drew a scene into its
paint, and its painted front's prompt put blue panels on its plywood back. A side no concept showed can carry
invented marks, a stencil or letters: look at the back, and redraw it with a view whose prompt names the plain
material).
`parts` (`{"op": "views"}`, then `{"op": "segment", "view", "points": [[x, y, 1|0]], "name"}`) names pieces
such as a helmet so export keeps them as objects.

**An animation made elsewhere** (FBX, GLB, BVH on a Mixamo, Unreal or Kimodo skeleton) comes in through the app's
Import page (Put an animation on one of your characters) or the Animate panel: `import_motion` on its upload, then
`anim` with that version's name in `clips`.

**Character rig and animation.** `rig_fit` (`{"template": "mixamo52"|"fox"|"horse"|"bird"|"dragon"|"shark"|"kaiju"|"spider"|"snake"}`, A-posed mesh facing +Z;
`{"markers": {name: [x,y,z]}}` to correct) -> `fit_check` on it (joints outside the mesh? fix markers, re-run
`rig_fit`) -> `rig_skin` (`{"engine": "auto"}`; `{"rigid": {"Helmet": "Head"}}` for parts that do not bend)
-> `anim` (`{"clips": ["Idle_A", "Walk", "Jog"]}`: names from `GET /api/clips`, run `describe_steps("anim")`;
creatures have their own clips) or `motion` (`{"prompt": "A person waves.", "seconds": 5}`, text to motion,
human skeletons only) or `mocap` from a video. Read `qa.json` (`read_file`) after `rig_skin`.

## anim_edit: correcting a clip

`run_step(asset, <anim or anim_edit version>, "anim_edit", {"clips": {"Walk": layer}})`. The layer is applied
to the base `anim` (edits never compound), baked into that clip; other clips stay as they were. Layer fields
(bones by glTF node name, frames are base frames):

- `keys: {bone: [{"f": 12, "q": [x, y, z, w], "falloff": 6}]}` rotation delta in the bone's local space (w >= 0),
  blended over `falloff` frames either side.
- `offsets: {bone: [{"f", "v": [x, y, z], "falloff"}]}` translation in metres in the parent's space.
- `trim: [in, out]` frames kept; `speed` > 0 (2 = twice as fast); `loop: n` blends the last n frames into the first.
- `ground: metres` added to the top-most translated bones (Hips); `in_place: true` removes the X/Z drift
  (walk in place).
- `smooth: {bone: sigma_frames}` Gaussian smoothing of a jittery bone.
- `feet: [{"bone": "LeftFoot", "from": 4, "to": 11, "blend": 3}]` locks a foot to the ground over those frames (a hand
  resting on the floor the same way: the bone, its parent and grandparent turn).
- `ik: [{"bone": "LeftHand", "chain": 2, "space": "Hips", "keys": [{"f": 10, "p": [x, y, z], "swivel": 0, "w": 1,
  "falloff": 4}]}]` an IK control: the hand reaches `p` (metres, skeleton space, or in the `space` bone's frame so it
  rides on that bone), the 2 bones above it bending; `swivel` degrees swings the elbow/knee, `w` 0–1 the weight;
  `chain` 3+ (a spine, a tail) solves by FABRIK.

Check with `look(asset, version, clip="Walk")` (four frames of the clip).

## Checking

- `look(asset, version, clip="")`: the picture of a version (mesh sheet, picture grid, or clip frames). Use it after
  every step that changes shape or colour.
- `validate(asset, version)`: the checklist, each item pass|warn|fail. Fix fails (usually `clean`, `remesh`,
  `uv`) and validate the new version before export. A size in the brief's prompt ("2.1 m tall, 3.2 m long", cm
  too) is held against the model: `size_drift_pct` warns from 10 % on the height or the long side (the thin side
  is only reported, `report.size`); `transform`'s `scale: [x, y, z]` fits it.
- `fit_check`: skeleton in mesh. `read_file`: `report.json`, `skeleton.json`, `qa.json`, `clips.json`, `edit.json`
  of a version (JSON or text, 200 KB at most).

## Exporting into a game project

`export(asset, version, profile, options, to_folder)`: `profile` godot|unreal|unity|web|print|obj;
`options` `{"lods": true, "collision": true}` (unreal and unity make LODs, godot and unreal take a collision hull,
`web` is a small GLB + USDZ, `print` STL + 3MF in mm). Every export carries `CREDITS.txt` and `LICENSES.json`:
ship them. `to_folder` must be an existing absolute directory (e.g. `<project>/assets/models`); the files are copied
into a new `<asset>_<profile>` folder inside it, and an existing folder is refused (choose another or have the
owner remove it). The result lists the absolute paths. Export the **checked** version; a restricted version
(Hunyuan experiments) is refused by the app.

## Do not

- Do not guess params: `describe_steps(step)`. Do not run `export` before `validate` on the version.
- Do not start a second GPU step while a job is running; do not poll faster than `job_status(wait_s=40)`.
- Do not look for delete tools or edit files in the library folder: add a new version instead.

## 2D: pictures and pixel art

- A 2D asset holds pictures, not models: `create_asset(name, kind="2d")`, or `import_file(path, kind="2d")`
  (the picture is only uploaded: no cut-out, no 3D).
- Pictures from words: `run_step(asset, null, "brief", {"prompt": ..., "category": "wallpaper"})` (or
  `picture` (the prompt as it is, nothing added), `background`, `portrait`, `icon`, `item`, `tile`), then `run_step(asset, <brief>, "concepts", {"op": "image", "width":
  1920, "height": 1088, "count": 4})`: sizes are multiples of 64 from 256 to 2048; `"lora": "pixel"` (or
  `painting`, `drawing`; Setup's 2D styles) gives the look, `"cutout": true` a transparent background (an item or
  a sprite). Outputs `image_<k>.png` and `image.png` (= the first); about 30 s a 1920 × 1088 picture warm.
  `pixelize` takes one of them with `"key": "image_2"`.
- A seamless tile (a wall, a floor): `brief` with `"category": "tile"`, then `concepts` `{"op": "tile",
  "size": 512}` (256 to 1024, square; `"lora": "pixel"` for a pixel-style one): the picture's edges are drawn again
  so they continue each other. Outputs `tile.png` (also the `image` pixelize reads), `preview.png` (2 × 2) and
  `report.json`: `seam_ratio` 1.1 or less means the edges meet (`seam_ratio_before` was the raw picture's); above,
  make another. `pixelize` keeps it a tile: `{"height_px": 32}` is the tile's size in pixels and must divide the
  tile's (a 512 tile: 8, 16, 32, 64, 128 or 256); `scale: "auto"` picks the nearest that does. `validate` checks
  the seams (`seamless`); `export` `godot-2d` writes `<asset>.png` + `<asset>.tres`, a TileSet a TileMapLayer paints
  with (`unreal-2d`: the texture; its Tile Set is made in the editor).
- Parallax layers (a stage that scrolls): `run_step(asset, <a picture>, "depth", {})` (MoGe-3, about 30 s;
  Setup's Parallax layers), then `run_step(asset, <the depth>, "layers", {"count": 4})` (2-5; `"subject": true` puts
  the picture's figure in a layer of its own). Outputs `layer_<k>.png` far to near (each holds its band and everything
  nearer, what the nearer bands hide drawn in; the farthest is opaque), `layers.json` (`scroll_scale` per layer: the
  share of the camera's move it follows), `bands.png`. `export` `godot-2d` writes `parallax.tscn` (one Parallax2D per
  layer). A background picture (`category: "background"`, 2048 x 768) is the usual input.
- Upscale or redraw a picture (any size): `run_step(asset, <a picture>, "redraw", {"op": "upscale",
  "factor": 4})` (Real-ESRGAN, 2 or 4), or `{"op": "redraw", "prompt": ..., "strength": 0.35, "factor": 4,
  "seed": 1}`: upscaled first, then drawn again in overlapping 1024 px windows that fade into each other (about
  10 s a window: an 8352 x 2400 picture at 4x is 572 windows, near 2 hours, run nothing else meanwhile). The
  prompt is used as given (no negative prompt); `lora` as in concepts; `strength` 0.2-1 is how far it may wander.
  Alpha is kept. A protect mask (glass, mist, water the model would invent in): `import_file(<mask.png>,
  asset_id=<this asset>)` adds it as a version beside the picture, then `"protect": "<its version>"` (white = keep
  the picture, black = redraw). Outputs `image.png` (full size), `preview.png` (2048 px, what `look` shows) and
  `report.json`: `drift` (0-255, the result shrunk back vs the input), `drift_max` and `per_window` (find the
  windows that changed too much), `seam_ratio` (about 1 = no seam), `pass`.
- A prop for a 2D game, end to end (the chain `game-prop-2d` in `tools/bench.py`): prep, `trellis2`, clean, remesh,
  uv, bake, then `sprites` `{"yaws": [90], "height_px": 64}` on the bake and `pixelize` `{"scale": 1, "colors": 15,
  "outline": "darkest"}`.
- `run_step(asset, upload, "pixelize", {...})`: the picture's own pixel grid is found (`scale: "auto"`, a model's
  "pixel art" is drawn 6–8 picture pixels per drawn pixel) or set (`scale: 6`; `height_px: 64` for a sprite),
  one palette over every frame (`colors: 16`, or `palette: "pico-8"`), a binary alpha; optional `dither:
  "bayer2"`, `outline: "darkest"`, `palette_map: {"#ff004d": "#29adff"}` (an exact recolour: an enemy variant).
  Outputs `frames/`, `sheet.png` + `sheet.json` (Aseprite's json-array shape, tags and durations), `palette.gpl`,
  `preview.png` (`look` shows it with whole pixels), `report.json` (`scale`, `colors`; `read_file` reads it).
  `scale: "auto"` reports `scale` 1 when it found no grid (a full-body pixel-style picture can miss it): run
  again with `height_px`. A recolour of pixel art: `pixelize` on the pixelize version with `{"scale": 1,
  "palette": "input", "palette_map": {...}}` (its own palette: nothing re-quantised).
- A version with frames in (a sheet, an earlier pixelize) keeps its tags and durations.
- Pixels by hand: the app's pixel editor saves `pixelize` `{"op": "edit", "frames": {"<frame name>": "<base64
  PNG>"}}` on a sheet version, only the frames that changed (the names are `sheet.json`'s `filename`s). Each keeps its
  cell size, and every opaque pixel is one of the version's `palette.gpl` colours, or the edit is refused. Optional
  `durations` `{"<name>": ms}`.
- Frames and animations, same op:
  - `"order": [names]` adds, deletes or reorders frames. A new name is a blank frame unless its pixels are sent.
  - `"tags": [{"name", "from", "to", "direction": "forward", "repeat": "0"}]`, indices into the order; `repeat`
    `"1"` plays once. Send them again with a new order.
  - With no parent (`run_step(asset, None, "pixelize", {"op": "edit", "size": [32, 32], "order": ["frame_1",
    ...], "palette": "pico-8"})`) it makes a blank sheet to draw on. Leave out `palette` for any colour.
- Sprites of a 3D character: `run_step(asset, <an anim version>, "sprites", {"height_px": 104, "yaws":
  [90]})` (every clip by default, or `"clips": [...]`; yaw 90 faces right, 270 left, 0 front, 180 back; `fps` 12;
  `"shading": "flat"` for the texture unlit) renders each clip in place, in whole pixels, transparent, in seconds; a
  later run on the asset keeps the first run's scale. Then `pixelize` with `{"scale": 1, "colors": 15, "outline":
  "darkest"}`. A still `mesh` version gives one frame per yaw (a prop).
- Animate a 2D character (the app's Animate this character): `import_file(library_file="<2D asset>/<version>/
  image_1.png")` makes a 3D asset and cuts the picture out; `canon` on that prep with `{"look": "render"}` redraws it
  shaded and standing, hands empty (about a minute; without it a flat picture, pixel art or a drawing, comes out as a
  cardboard standee; `look` it: a blank face or other clothes, run `canon` again with another `seed`); `prep` on the
  canon; then `trellis2` Faithful, the preparation, `rig_fit`, `rig_skin`, `anim`, and `sprites` with `"shading":
  "flat"` (its texture already has the picture's shading). A model without `anim` gives one still frame per view.
- The app's move sets (Character → Move set), as `anim` `clips`: Brawler ("Fighting Idle", "Walk", "Jog",
  "Fighting Left Jab", "Fighting Right Jab", "Punch_Cross", "Melee_Hook", "Kick_Breach", "Jump_Start", "Jump_air",
  "Jump_Land", "Hit_Head", "Hit_Chest", "Hit_Knockback", "LayToIdle", "Death_A", "Defend", "PickUp_Table", "Throw
  Object", "Victory"); Sword fighter ("Idle_Sword", "Walk", "Jog", "Sword_Regular_A", "Sword_Regular_B",
  "Sword_Regular_C", "Sword_Regular_Combo", "Sword_Attack", "Sword_Attack_Air_Vertical", "Sword_Block", "Jump_Start",
  "Jump_air", "Jump_Land", "Hit_Chest", "Hit_Knockback", "LayToIdle", "Death_B", "Spell_Simple_Shoot", "Victory");
  Basic ("Idle_A", "Walk", "Jog"). Names the installed pack lacks are refused: check `GET /api/clips`.
- The fighting moves the pack lacks, as `motion` sentences (`"seconds": 2.5`, one each): "A person does a high
  roundhouse kick with the right leg, then returns to a fighting stance." · "A person jumps forward and does a flying
  kick with the right leg, then lands in a fighting stance." · "A person throws a rising uppercut with the right fist,
  then returns to a fighting stance." · "A person grabs someone in front by the collar with both hands and knees them
  in the stomach." · "A person grabs someone in front and throws them over the right shoulder." · "A person throws a
  backward elbow strike with the right arm, then turns back to a fighting stance." Attacks, hits and falls play once
  in the sheet's tags; a clip that never moves is named in the anim's `clips.json` (`still`).
- Into the game: `validate` on a sheet version gives the 2D checklist; `export(asset, <sheet version>,
  "godot-2d")` writes a SpriteFrames `.tres` and an AnimatedSprite2D `.tscn` (drop it into a scene; README.txt has the
  pixel-perfect settings), `"unreal-2d"` a Paper2D `.paper2dsprites`. A move that does not end where it began plays
  once. Parallax layers, tiles and the in-app editor follow.
