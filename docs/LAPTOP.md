# Laptop checks: M64 to M68 (built in the cloud, 2026-10-29 and 2026-10-30)

For the agent or the maintainer on the reference laptop. All five milestones were built in a cloud session
without a GPU, Blender, Kimodo's weights, GEM-X or the Tile model. Everything that could run there passed (the unit tests, the Playwright
suite, Kimodo's own skeleton and constraint code on the CPU). Nothing below has run yet. Tick the boxes, record the
numbers, and never tick a check that was not run (IMPLEMENTATION §6).

What each milestone is, and why: IMPLEMENTATION.md, sections M64 to M68 (near the end of §4).

## 0. Get the code

- [ ] M64 and M65 came in through pull request #12, M66 and M67 through #13, both merged: they are on `main`.
  M68 is on the branch `ccr-3bd14478-pui9wd` until its pull request is merged; use the branch while it is open:
  ```
  git fetch origin ccr-3bd14478-pui9wd
  git switch ccr-3bd14478-pui9wd
  ```
  Once merged: `git switch main`, then `git pull`.
- [ ] No new package, model or venv. Rebuild the UI and restart the app (`run.bat`): step code changed, and a
  resident Kimodo or GEM-X worker would keep the old code until it is evicted.
  ```
  cd ui && npm ci && npm run build && cd ..
  ```
- [ ] The local checks, as CONTRIBUTING.md lists them:
  ```
  uv run pytest -q
  uv run ruff check . && uv run ruff format --check .
  cd ui && npx playwright test
  ```
  Known and not ours: `tests/test_blender_client.py::test_open_starts_blender_when_setup_says_so` can fail in the
  full run and passes alone (order-dependent).

## 1. M65 Connect clips (the transition by Kimodo)

Needs the Setup card **Text to motion** installed. Use the viking, or any person rigged on the 52-joint template,
with an animated version holding `Idle_A` and `Walk` (Character → Ready-made, if it has none).

- [ ] **Check 0, first: Kimodo's C++ corrector.** Kimodo's post-processing needs it, and text-to-motion already
  uses that post-processing. Kimodo's `setup.py` builds it with the package, so this should print nothing:
  ```
  .venv-kimodo\Scripts\python -c "import motion_correction"
  ```
  If it fails, text-to-motion fails too: report it before going on.
- [ ] **Check 1: Idle_A into Walk, and back.**
  1. Open the animated version. Under **Connect two clips** choose From `Idle_A`, To `Walk`, Length 1.5 s, then
     **Make transition**.
  2. The transition's page opens when the job ends (or pick its version), headed **Transition**. Record from its
     version's `meta.json` metrics:
     `seam_cm`, `height_scale`, `generate_seconds`, `encoder_device`.
  3. Click **Put this motion on the model with its clips**. The new animated version must hold `Idle_A`, `Walk`
     and `Idle_A to Walk`.
  4. Measure the seams on our own rig: our joints' mean distance between the transition's first frame and
     `Idle_A`'s last, and its last frame and `Walk`'s first (cm). Use the new animated version's folder (under
     `library/`, or wherever `EFFIGY_LIBRARY` points):
     ```
     uv run python -m steps._connect library/<asset>/<new anim version>/rigged_anim.glb "Idle_A to Walk" Idle_A Walk
     ```
  5. Play the three clips in the viewer: the transition starts on Idle's pose and ends on Walk's, with no limb
     flipping or twisting.
  6. The same for `Walk` into `Idle_A`.

  No pass number exists yet (a threshold comes from these runs). As a guide, not a rule: a few centimetres is what
  Kimodo's soft constraints give; tens of centimetres, or a visible pop, means the inverse retarget is off.
- [ ] **Check 2: one side empty.** From `Idle_A` with To empty makes "After Idle_A". To `Walk` with From empty
  makes "Into Walk". Both generate; the held end's `seam_cm` is recorded (one number each).
- [ ] **Check 3: turn and travel.** `Idle_A` into `Walk` with Turn 90 and Travel 2: Walk starts turned 90° to the
  left, about 2 m ahead of where Idle ended.
- [ ] **Check 4: in a game.** Export the version from check 1 for Godot. In Godot, play `Idle_A`, then the
  transition, then `Walk` (an `AnimationPlayer` queue): no pop at either switch.
- [ ] **Record** the runs in a new `docs/bench/m65.md`: the character, the clips, `seam_cm`, the seam check's
  `from_cm` and `to_cm`, `generate_seconds`, and what you saw.

## 2. M64 Motion from video: the other people, hand contacts, a panning camera

Needs the Setup card **Motion from video** installed.

- [ ] **Check 1: two people in a clip.** Film or pick a clip with two people, head to toe. Make motion from video
  (Best). The motion's page says "2 people at the start of the video; this is person 1". Click **Make the other
  one's motion too**: a second job runs, its `preview.mp4` follows the other person, and both are listed under
  Your motions.
- [ ] **Check 2: hands on the floor.** A push-up or crawl clip, as a motion, put on the viking. Open **Edit
  animation**, then Foot locks, then **Find hand contacts**: the palms lock while they rest on the floor and the
  arms bend to keep them there. Save; the saved clip plays as the preview did.
- [ ] **Check 3: a panning camera.** A phone clip panned to follow someone walking across a room, made twice:
  once with Camera "held still", once "turning on the spot to follow (panning)". In "panning" the walker crosses
  the room; in "held still" they walk in place or curve. Record for both from the mocap versions' metrics:
  `turned_deg`, `foot_slide_mps`, `root_jitter_mm`, `total_seconds`, `peak_vram_gb`, and for "panning" also
  `camera_unread` and `camera_turn_deg`. Doubt this one first: a camera that also walks (a dolly) reads as a turn, so the person drifts.
- [ ] **Record** the numbers in `docs/bench/m34.md`, in an "M64" section.

## 3. M66 Refine sees more: coverage views, no colour next to a depth step

Needs the Setup card **Texture detail** installed (the Tile model). No new download. The chest and a character of
the M43b bench (the pirate, or the viking), each from its `bake` version (or the version M43b's refine had as parent).

- [ ] **Check 1: the defaults.** Run Texture detail on the character's bake with the defaults. From the new refine
  version's `meta.json` metrics record `coverage_views`, `seen_before`, `seen_after`, `views`, `views_planned`,
  `seconds`, `refined`, `seam_delta`. Open `views.png`: the added views are its last pairs (as many as
  `coverage_views`). They should look onto what the side views miss (the shoulders' tops, the hat's brim, between
  the arms and the body), never from below and never into the floor.
- [ ] **Check 2: no halo next to a depth step.** Put the new texture beside the earlier refine of the same parent
  (made before M66): no arm-coloured band on the torso where an arm crosses it in the front and side views, none on
  the legs behind the hands. `refined` may be a little lower than before (texels next to steps are left to the
  other views or kept). Look at the shoulders and the head's top too: seen_after above seen_before should show as
  more redrawn detail there, not as blotches.
- [ ] **Check 3: the chest.** The same on the chest's bake: it is wider than tall, so nothing is added from above
  (in `views_used`, every pitch is 0 or above −15). The lid keeps the input's colour, as before M66 (no invented
  tiles).
- [ ] **Check 4: the cost.** The character again with `coverage` 0 (over MCP:
  `run_step(asset, <bake version>, "refine", {"coverage": 0})`): `views` equals `views_planned` and has no
  `coverage_views`; `seconds` about check 1's minus `coverage_views` × `seconds_per_view`.
- [ ] **Record** it all in a new `docs/bench/m66.md`: the asset, the numbers above, and what you saw in checks 2
  and 3.

## 4. M67 Arm clearance: the arms kept out of the body

No new package or model; it runs inside the `anim` step (the Animate panel's clips, or `run_step anim`).

- [ ] **Check 1: a wide character.** The dwarf, or any stout character on the 52-joint skeleton, animated with
  `Idle_A`, `Walk` and `Jog`. The animated version's page says "Arms opened so the hands clear the body: …" with
  the clips and degrees (from its `meta.json`: `arms_opened`). Play the three: the hands pass beside the hips and
  the belly, not through them; the arms open before they reach the body and close after, without a jerk; a looping
  clip has no jump where it starts over.
- [ ] **Check 2: without it.** The same character with "Keep the arms out of the body" cleared in the Animate
  panel (over MCP: `run_step(asset, <skinned version>, "anim", {"clips": ["Idle_A", "Walk", "Jog"], "clearance":
  false})`): the clips as before M67, for the comparison. The page has no "Arms opened" line.
- [ ] **Check 3: a slim character, and a robed one.** The viking with the same clips: `arms_opened` absent, or a
  few degrees. A character in a robe, a cape or big pauldrons: if its arms open by tens of degrees in every clip,
  the clothing widened the torso (its vertices ride on the spine bones); say so in the bench file.
- [ ] **Check 4: in a game and the editor.** Export check 1's version for Godot: the opened clips play. Open it in
  Edit animation: the clips there are the opened ones, as the viewer plays them.
- [ ] **Record** in a new `docs/bench/m67.md`: the characters, `arms_opened` per clip, any clip with `uncleared`
  frames, and what you saw (a screenshot of a walk with and without, if it shows).

## 5. M68 A painted part of the texture redrawn

Needs the Setup card **Texture detail** (Z-Image comes with it). No new download.

- [ ] **Check 1: the chest's lock.** Open the chest's bake (or refine) version, Tools, Texture detail. Type "a gold
  lion's-head lock" under **It becomes**, click **Paint the part to change** and paint over the lock (drag on it;
  Shift erases; turn the view by dragging beside the model, or stop painting to turn it freely). Click **Redraw
  the painted part**. The new version's page says how many views drew it and how much they saw. In the viewer, the
  lock is the new one from every side that sees it; compare with the version before it in History: nothing else
  changed (its `meta.json`: `changed_outside` 0).
- [ ] **Check 2: a belt all round.** On a character, paint its belt all the way round (turn the model as you go),
  "a red leather belt with a brass buckle": the back of the belt changes too. Record `views`, `region_seen`,
  `region_seen_planned`.
- [ ] **Check 3: less strength.** Check 1 again with the strength slider at 0.5: the lock keeps more of its old
  shape.
- [ ] **Check 4: the cost.** From check 1 and 2 record `seconds`, `seconds_per_view`, `peak_vram_gb`.
- [ ] **Record** in a new `docs/bench/m68.md`: the assets, the prompts, the numbers, and a before | after picture.

## 6. When done

- Commit the bench files and this file's ticks on a branch of your own, and open a pull request; the template fills
  itself in. Put the measured numbers in the PR's Measurements line.
- A failure with a small fix: fix it on that branch, one milestone per PR (IMPLEMENTATION §0 and §7). Anything
  larger: describe it (the job list at the bottom of the app shows the error, the `run.bat` window has more) and stop.
- After all five pass: tick the acceptance in IMPLEMENTATION M64 to M68, update HANDOVER §1 and §2, and move
  this file to `docs/archive/`.
