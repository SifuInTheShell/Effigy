# Laptop checks: M64 and M65 (built in the cloud, 2026-10-29 and 2026-10-30)

For the agent or the maintainer on the reference laptop. Both milestones were built in a cloud session without a
GPU, Blender, Kimodo's weights or GEM-X. Everything that could run there passed (the unit tests, the Playwright
suite, Kimodo's own skeleton and constraint code on the CPU). Nothing below has run yet. Tick the boxes, record the
numbers, and never tick a check that was not run (IMPLEMENTATION §6).

What each milestone is, and why: IMPLEMENTATION.md, sections M64 and M65 (near the end of §4).

## 0. Get the code

- [ ] The work is on branch `ccr-3bd14478-pui9wd`, not on `main` (protected: changes go through pull requests).
  ```
  git fetch origin ccr-3bd14478-pui9wd
  git switch ccr-3bd14478-pui9wd
  ```
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

## 3. When done

- Commit the bench files and this file's ticks on a branch of your own, and open a pull request; the template fills
  itself in. Put the measured numbers in the PR's Measurements line.
- A failure with a small fix: fix it on that branch, one milestone per PR (IMPLEMENTATION §0 and §7). Anything
  larger: describe it (the job list at the bottom of the app shows the error, the `run.bat` window has more) and stop.
- After both pass: tick the acceptance in IMPLEMENTATION M64 and M65, update HANDOVER §1 and §2, and move this
  file to `docs/archive/`.
