# Handover: continuing Effigy on the reference laptop

> Maintainer's working notes; contributors start at [CONTRIBUTING.md](../CONTRIBUTING.md).

For a Claude Code session (and the maintainer) on the RTX 5080 laptop. The GPU bring-up is done: every
generator, the chain, Blender, Claude and the exports have run here and their numbers are in
`docs/bench/`. Phases 0 and 1 are closed, Phase 2 (characters) is closed, Phase 3 is under way.
**Next session: start at section 2, "Next session".**

Read first: `CLAUDE.md` (rules), this file, then `docs/IMPLEMENTATION.md` §0 (rules), §3 (frozen
contracts), §6 (never-do) as needed. Install steps are `SETUP.md`. **This round's laptop checks (M64, M65, M66, M67):
`docs/LAPTOP.md`.** The first laptop session's checklist, done, is `docs/archive/LAPTOP.md`.

---

## 1. Where things stand (2026-10-30: M64 to M67 built blind in the cloud, to validate on the laptop; M61–M63 merged, 0.2.0 out)

- **M67 (branch `ccr-3bd14478-pui9wd`, the cloud session, 2026-10-30; not run in Blender)**: arm clearance, item 2
  of the StableGen assessment. After the retarget, `anim` turns a person's upper arms outward where a clip would
  swing the forearms or hands through the torso (elliptic cylinders fitted to the skinned spine, the need eased in
  time and round a loop; `steps/_clear.py`, param `clearance`, default true; metrics `arms_opened`). Not done: arm
  against arm, legs, pushes that are not sideways. What to run: IMPLEMENTATION M67 "Needs the laptop".
- **M66 (branch `ccr-3bd14478-pui9wd`, the cloud session, 2026-10-30; not run on a GPU)**: `refine` sees more,
  after the assessment of sakalond/StableGen (GPL: its methods only). A texel takes no colour from a view next to a
  depth step inside it (no arm halo on the torso), and up to `coverage` (default 3) views are added where the auto
  views see too little (`coverage_views`: greedy over 64 directions from above; on a model wider than tall only near
  level, keeping the 2026-10-11 rule against redraws onto flat tops). Metrics `coverage_views`, `seen_before`,
  `seen_after`. Not done: a painted masked edit (arm clearance is M67). What to run: IMPLEMENTATION M66 "Needs the laptop".
- **M65 (merged: PR #12, the cloud session, 2026-10-30; not run on a GPU)**: connect clips, transition
  only. On an animated model, "Connect two clips" asks Kimodo for the motion from one clip's last pose into another's
  first (or after one, or into one); the transition goes back on the model with the clips. The pose reaches Kimodo by
  the exact inverse of the retarget (`steps/_connect.py`). The joined clip and the path viewer come later (the
  maintainer, 2026-10-30). What to run: IMPLEMENTATION M65 "Needs the laptop".
- **M64 (merged: PR #12, the cloud session, 2026-10-29; not run on a GPU)**: motion from video gets the
  other people in the clip (`metrics.people`, "Make the other N's motion too" on the motion's page), the editor's
  Find hand contacts (a palm on the floor locked like a foot), and a panning camera (`camera: "panning"`: the camera's
  turn read off the background with OpenCV and given to GEM-X). What to run: IMPLEMENTATION M64 "Needs the laptop".
  Came out of the assessment of toyxyz/toyxyz_RGBMoCap (its models are SMPL-family and noncommercial: not used).
- **M63 (merged: PRs #7 and #8)**: character IK in the animation editor. The armature drawn as Blender draws it (octahedral bones with their roll, relationship lines, the IK chain yellow); IK controls in the `anim_edit` layer (§3.2 step 3c: a hand or foot on a keyed target, chain 2–8, FABRIK past two, a swivel for the knee/elbow, weight, world or bone space) with target and pole handles in the view; IK drag over any chain length; R/G, Page Up/Down. The contract branch merges first.
- **M62 (merged: PRs #3 and #6)**: `bake` of an imported model reads its own materials (no volume needed); chain checked on two imported test models (`docs/bench/m62.md`). A skeleton that is not one of Effigy's templates is dropped and the model is rigged with Effigy's own.
- **M61 (merged: PRs #2 and #5)**: the 2D step `redraw` (upscale 2×/4× with Real-ESRGAN, or redraw at any size in overlapping windows with Z-Image-Turbo + Tile), in a picture's panel and the Tools menu; MCP `run_step redraw` and `import_file(asset_id=…)`.
- **Open issue**: `clean` crashes on a 50k-triangle import (CuMesh `fill_holes`, CUDA error 9). Not diagnosed; a smaller import cleans.

- **2026-10-13: the generic rig dropped** (`drop-generic-rig`; the maintainer): M59's `rig_skin {full: true}`, the Rig
  panel's "Something else", `_skintokens.rig`, `_glb.joints`, `_templates.GENERIC`, their tests and docs removed
  (SkinTokens' training data with no bone-heat alternative). The maintainer's picks from the offered items: the held
  weapon as a separate prop on the hand bone (next); the Sprites warning, Flat by default and Animate this
  character as one chain dropped.
- **2026-10-13: the hoarding findings 1, 2, 3, 7, 8** (`hoarding-fixes`, **merged**, a78b5f3; IMPLEMENTATION "The hoarding's findings").
  - **8**: `close_in_blender` / `pull_from_blender` without a version no longer fail in Blender (checked live against the maintainer's Blender with MCP Pro: the old header's NameError, the new one's None, True and a path with a backslash and an umlaut).
  - **1**: detaper keeps the depth (no wedge).
  - **2**: validate's `size_drift_pct` against the size the brief's prompt gives.
  - **3, 7**: refine's views take their own prompt; guide notes.
  - **4** (`blender-start`, **merged**): Setup's "Start Blender when it is needed" and the user's own `blender.exe`;
    Open in Blender (the agent's too) starts it with its add-on's server when none answers. Live with the maintainer's
    Blender closed: started, server up, the model in the scene in 9.4 s. The option itself is off until the maintainer
    turns it on in Setup.
  - **5**: the port stays 8000 (the maintainer).
- **2026-10-13: the cloud session's branch merged into main** (`ccr-3bd14478-pui9wd`, below): M59 generic rigs
  (since dropped), the CLI batch export, Redraw this view and 4K, the public-repo sweep. Its GPU checks are §2 item 6.
- **2026-10-13: the agent's finding 19** (`loose-parts`, **merged**; IMPLEMENTATION "Finding 19 (the SR10 bottle
  bank)").
  - **Loose scraps**: clean drops small parts floating off a generated model's outside. A piece that touches it
    (crate trim) or floats inside it (a lantern's flame) stays.
  - **Folded remesh**: a generated prop that comes back over 5 % folded through itself is remeshed again with its
    slits shut (length / 150). Figures are left alone: 1.2 cm would join their fingers.
  - **Finding 6**: a refused `close` names the range it takes.
- **2026-10-13: the agent's findings 20–22** (`box-axes`, **merged**, bb7b89e).
  - **Box fallback**: a box rolled past the floors' 30° window stands on its own axes (the raised bed, 30°).
  - **Transform**: `square` and `face` replace the angle sums the agent worked out outside Effigy.
- **2026-10-13: press to floor and the MCP labels** (**merged**).
  - **Press to floor**: clean `press_to_floor` / Prepare "Press what hangs just under its base onto it". The barrier
    block's mirrored eye no longer holds it up.
  - **MCP labels**: the agent server names versions as the app does.
- **2026-10-13: the bollard findings (9–18) and upright by default** (**merged**, 65aa911; IMPLEMENTATION "Agent findings
  2026-10-13 (bollard)").
  - **9**: straighten stands a leaning post on its shaft.
  - **10**: clean drops the rebuild's inner copy of a solid.
  - **11**: a headless `transform` step (stand up, turn, scale, place; textures kept; bake follows it).
  - **Upright by default** (the maintainer): Prepare stands a generated model upright.
    - The library survey: 27 of 78 levelled, every one right.
    - After the agent's retest: a post stands on its shaft first, and a floor propped up by a mirrored eye is found
      from the mesh's own bottom faces.
    - `transform` scales per axis.
    - The prop and creature briefs ask for "the camera level at mid height".
- **2026-10-13: the user manual and the standing-pose button** (merged with the bollard fixes).
  - **The manual**: `MANUAL.md` covers every page, panel and tool.
  - **The button**: **Put it in a standing pose** in the Generate panel on a character's cut-out.
- **2026-10-13: Setup's Advanced section** (**merged**; IMPLEMENTATION "Setup's Advanced section").
  - **Advanced**: the model table and the licences are read-only.
  - **Install**: on the feature cards. "3D model from four views" got its own card.
  - **Remove**: under Space on disk, which can now also remove a file two features share.
- **2026-10-13: M46c Setup offers what the card fits** (**merged** with M46a and M46b, 2d5f55c; IMPLEMENTATION "M46c").
  - **The full refine Tile** is its own optional download, `refine-full` (6.7 GB). Setup offers it only where the
    card holds its 15.6 GB, and Everything leaves it out elsewhere.
  - **Use**: refine takes it by itself once it is there and fits.
  - **On the laptop**: the card says "Needs a bigger card", and nothing changes.
  - **Bigger GPUs (M46a–c) are built**; the 24/32 GB numbers come from users' `gpu_report`s.
  - **Next**: restart the app (server changes: presets, the machine check, the pre-flight). Open: the users' GPU
    reports for the 24/32 GB numbers (the 17/18/19.4/28 GB thresholds are estimates), the one xatlas packing stall
    (`docs/bench/m46.md` "M46b") if it comes back.
- **2026-10-13: M46b bigger presets, the best fit as the default** (merged; IMPLEMENTATION "M46b").
  - **Presets**: `faithful-hd` (1536, 4K, 24 GB cards) and `faithful-hq` (20 steps, 4K, fits 16 GB).
  - **Default**: the best one the card runs is the default and marked "Recommended for this GPU"; the user's pick
    is kept. On the laptop that is `faithful-hq`.
  - **Agents and chains**: `{"preset": "best"}`, and the `hero-character` chain.
  - **RTX 30 cards**: told the 3D steps are not supported (our wheels are 8.9/12.0 only) and refused; the rest runs.
  - **Next**: M46c (Setup offers the full refine Tile on cards that fit it).
- **2026-10-13: M46a the budget skips the offload** (merged; IMPLEMENTATION "M46a").
  - **What it does**: canon (FLUX.2 klein) and motion (Kimodo's text encoder) keep their models whole on the GPU when
    the job's budget holds them: ≥ 17 GB (est.) and ≥ 18 GB (claimed), so 24 GB cards and up. Params `offload`
    true/false override, and an OOM suggests `offload: true`.
    - `tools/gpu_report` probes both ways, so a big card's report measures the thresholds.
  - **Measured**: canon whole does not fit 16 GB (OOM under a 12.5 GB cap while loading; weights 14.9 GB). On the
    laptop nothing changes: motion live, CPU encoder, 57 s then 3.1 s.
  - **Next**: M46b (the bigger presets, quality first by default, the compute-capability check).
- **2026-10-13: M57 the pixel editor, part 2: animation** (**merged 2026-10-13** with M56, b3f24fe;
  IMPLEMENTATION "M57").
  - **What it does**:
    - Frames: add, duplicate, delete, move, each with its duration.
    - Animations (tags: name, frames, loop or play once), play, onion skin.
    - New asset, 2D, Draw it yourself: a blank sheet opened straight in the editor.
  - **Checked live**:
    - A 4-frame hit spark drawn from nothing (`m57_hit_spark`) plays once in headless Godot at its 50–80 ms timings.
    - The viking's Walk re-timed without touching a pixel.
  - **Next**: M46a–c (bigger GPUs, research 16). Maintainer-side: draw a hit spark and a barrel's three states by hand.
- **2026-10-13: M56 the pixel editor, part 1** (merged with M57; IMPLEMENTATION "M56").
  - **What it does**: Edit the pixels on any pixel art or sprites version.
    - The tools: pencil (pixel-perfect), eraser, fills, line, rectangle, ellipse, colour pick, selection
      (move/flip/turn/copy/cut/paste), mirror and outline.
    - The palette is locked to the version's colours. Undo/redo, and Save as a new version (`pixelize` op `edit`,
      only the changed frames).
  - **Checked**: a browser test compares the saved PNG with the original pixel for pixel. Live, the barrel's pixel
    art was edited through the app: exactly the 41 drawn pixels changed.
  - **Next**: M57 (the frames strip, tags, durations, onion skin, play). Maintainer-side: draw a hit spark by hand.

- **2026-10-13: M55 parallax layers** (`m55`, merged into main 2026-10-13; `docs/bench/m55.md`).
  - **What it does**: a picture becomes far-to-near layers that scroll at their own speeds. It runs in two steps,
    `depth` then `layers`. The Godot `parallax.tscn` moves exactly at the layers' speeds, headless.
  - **Depth model**: MoGe-3 ViT-g (MIT), the maintainer's "best quality that is commercially usable". DA3
    Large/Giant are NC; DA3 Mono Large needs xformers. It runs in its own **`.venv-moge`**, because it needs a newer,
    pure-Triton FlexGEMM than TRELLIS.2's wheel. Setup's Parallax layers card installs it (5 GB).
  - **Layers**: bands are k-means on the disparity. What the nearer bands hide is filled from the layer's own colours,
    then redrawn by Z-Image at 0.8 in the picture's style.
  - **Through the app**: depth 19 s and 8.9 GB, layers 103 s and 7.7 GB, 0 check fails.
  - **The maintainer's check**:
    - A car leaning across a cut was cut in two. Now the cuts follow the depth's edges (a watershed), so the car is
      whole.
    - The repaint drew more cars behind, until the ground was named empty.
    - The view's Show menu shows each layer alone.
  - **Left open, skipped on purpose** (the maintainer: "a very specific error cause which we may skip"): a standing
    object's contact, the car's tyres, goes with the road. Object masks prompted at the cuts would fix it.
  - **Also**: New asset's 2D kinds start with **Picture** (the description as it is), the maintainer's request.
  - **After merging**: restart the app; users need Setup's Parallax layers card.

- **2026-10-13: M54 tiles and props** (`m54`, merged into main 2026-10-13; `docs/bench/m54.md`).
  - **Tiles**:
    - A seamless tile from a description: `concepts` op `tile`, the edges' cross drawn again at mend 0.8 with its
      colours kept.
    - Pixel art in whole tiles: an eighth of the tile, the middle colour.
    - A `seamless` check, and a Godot `TileSet`. Godot 4.7.1 paints a `TileMapLayer` with it headless.
  - **`game-prop-2d`**: a 3D prop drawn as a 2D sprite, about 4 minutes from a picture.
  - **The maintainer's request**: a Custom picture size, snapped to 64 between 256 and 2048. 2048 × 2048 fits:
    8.3 GB, 80 s warm.
  - **Found on the way**:
    - Diffusers' `from_pipe` casts to float32: the fp8 DiT went to 24 GB and ran out of memory.
    - A resident worker keeps the step code it started with: stop it after editing a step.
  - **The maintainer's order (2026-10-13)**: finish the 2D roadmap (M55, M56/M57), then bigger GPUs (M46a–c).
    `m46-gpu-report` (`tools/gpu_report.py`, research 16, the laptop measured) is merged too (2026-10-13).
  - After the merge: **restart the app** (`server/jobs.py` changed with m46).

- **2026-10-12 and earlier**: the session log is in [archive/handover-history.md](archive/handover-history.md).

### Milestones

**Phase 0 closed by the maintainer on 2026-10-04.**


| Milestone | Code | GPU / manual acceptance (laptop, 2026-10-04) |
|---|---|---|
| M0 scaffold, licence gate | done | ✅ local equivalent of CI green (pytest, ML pytest, ruff, UI build, Playwright on `main`); a manual `ci.yml` run is the maintainer's call (build minutes) |
| M1 sm_120 wheels (cumesh, flex_gemm, o_voxel) | done, locked in `wheels/` | ✅ `tools.smoke_m1`: torch 2.8.0+cu128, (12, 0), sm_120, cumesh ok. One CuMesh bug worked around (`fill_holes` centres, `steps/_mesh.py`) |
| M2 generate: Fast (TRELLIS.2) + Faithful (Pixal3D) | done | ✅ both run, upright and scaled (`docs/bench/m2.md`, `phase0.md`). ⚠️ VRAM: torch peak ≤ 13.3 GB, whole GPU up to 15.5 GB (allocator cache) |
| M3 bake from the volume + normal map | done | ✅ closest-point projection, normals outward; mean colour within 0.4 % of `hi.glb` |
| M4 clean / remesh / uv (CuMesh) | done | ✅ 0 non-manifold edges after clean → remesh → uv (dwarf, barrel, Faithful anvil); weld seams, own small-part filter, outward rule, `make_manifold` |
| M5 backend jobs, library, SSE | done | – |
| M6 UI + viewer | done | ✅ maintainer used it all day; GPU readout + guided "Next" panel added. Open: library rework (§11) |
| M7 Blender headless ops + export | done | ✅ Blender ops + export tests on Windows Blender 5.2.2 (paths fixed) |
| M8 Blender live link | done | ✅ open → move a vertex → pull → bake onto the pulled mesh → validate 0 fail (maintainer's Blender); free blender-mcp add-on supported, not yet run for real |
| M9 MCP server + Ask Claude | done | ✅ preset "Make game-ready for Unity" on a pulled version → accepted `530_claude`, validate 0 fail (12 turns, $0.62); Claude couldn't view the viewport screenshots in that run |
| M10 brief, concepts, prep, gates | done | ✅ 4 concepts in ~68 s warm (resident), 1024² kept by the maintainer over the < 60 s target (`docs/bench/m10.md`); background gate recalibrated |
| M11 bench + golden set | done | ✅ 10/10 Fast and Faithful, A/B recorded, **default Faithful** (PLAN §3); rerun after the fixes 10/10, 0 non-manifold, chain 192 s; VRAM accepted by the maintainer as is (`docs/bench/phase0.md`) |
| M12 retexture (`trellis2` op `texture`) | done | ✅ pulled Blender mesh retextured at 1024: 38 s, 3.84 GB, UVs bit-identical (autograd leak fixed) |
| M13 export breadth (LODs, collision, OBJ/STL/3MF, web) | done | ✅ Godot 4.7.1 and Unreal 5.8 imports (`docs/bench/m13.md`). Unity (nice-to-have): 6000.4.0f1 is installed but batch mode needs a signed-in Unity Hub ("No valid Unity Editor license"): manual check by the maintainer, export ready in `a_dwarf_blacksmith_with_hammer_2/560_export/unity/` |
| M14 Models & Licences page | done | ✅ real downloads of every group |
| M15 Windows portable zip | done, CI smoke green | ✅ maintainer's first start ~79 s (`docs/bench/install.md`); clean VM open |
| M16 pose canonicalization | done | ✅ DWPose gate + FLUX.2-klein re-pose (`docs/bench/m16.md`); known blind spot: the gate can't count limbs (the A-posed knight has four arms) |
| M18 quad remesh | done | ✅ 5 characters → pure quads, 0 non-manifold, p95 0.44–1.78 %, 4 s each (`docs/bench/m18.md`); UI: Topology select in the prepare form. Seen on the way: `clean` opens Faithful meshes further (mage 11k → 34k open edges: `make_manifold` drops faces), invisible in renders, the quad path closes them again |
| M17 multi-view (Pixal3D-MV) | done | ✅ ground-truth 4-view dwarf turnaround → matches from all four sides; 10.98 GB peak (≤ 14.5), 128 s; height check 5 %; UI "Views in the picture" (`docs/bench/m17.md`) |
| M19 rig fit | done (`m19-rig`) | ✅ 5 characters 52/52 joints inside the mesh, 9 s each, the dwarf facing −Z handled; marker editor in the Rig panel (Front view, labelled marker); shoulders refined for armoured characters; **accepted by the maintainer 2026-10-05** after trying the editor on the robot (`docs/bench/m19.md`) |
| M20 skinning | done | ✅ two engines, Blender bone heat and SkinTokens (3.43 GB VRAM measured, its own `.venv-skin`), `auto` judged by `rig_qa`: 6 characters 0 unweighted, ≤ 4 influences; the robe and the one-handed prop fail their checks on both (`docs/bench/m20.md`); UI Skin it, engine shown, weights view; Rig is a stage in the top bar; **accepted by the maintainer 2026-10-05** (farmer `200_rig_skin` through the app: both engines ran, Blender picked, 6/6 checks) |
| M21 animation | done, **accepted by the maintainer 2026-10-05**, merged | ✅ rigged exports with one skeleton per file (Unity/Unreal FBX, Godot GLB; Godot import checked headless); Mesh2Motion CC0 clips retargeted onto the rig by our own world-space transfer (per-bone frames: direction + twist reference; the Retarget extension carried the rest-pose offset: arms wrong, then hands garbled, both maintainer-reported, fixed), `anim` step, Animate stage, viewer playback, clips in the exports (`docs/bench/m21.md`), ground grid in the viewer; the Unity Humanoid import check is deferred by the maintainer |
| M22 characters bench | **accepted 2026-10-06** (`m22-results`) | ✅ 16 characters through `game-character` on 2026-10-06: **12 need no marker fix (75 %)**, 13 with the same-day skinning fix (bone heat retried on coarser proxies, chef re-run passes); the three others are torso-chain or head joints read as outside on a hollow/robed torso (`docs/bench/m22.md`, `m22_fits.jpg`) |
| M40 Agent access over MCP (+ rename to Effigy) | **merged 2026-10-09** | ✅ a scripted agent session through the bridge started as Setup prints it: import the cat's OBJ zip, `look`, clean (running → `job_status` → done; an identical request the same job), validate, export into a folder (again: the same export reused into another folder, an existing folder refused); an animated test character imported, `look` at four Jog frames, an `anim_edit` layer; `cancel_job`; previews 3 s a render; research 13 |
| M45 GPU and RAM safeguards | **merged 2026-10-10** | ✅ the cap (both allocators, a forced 1 GB budget → a clean oom in 17 s), Faithful under the cap, the GPU-busy and RAM refusals live (0.6 s), Fast → Faithful → Fast in fresh processes; seven card layouts in tests; full suites |
| M41 More creature skeletons | **merged 2026-10-10** | ✅ per creature (docs/bench/m41a–e): sample + a test mesh fitted, skinned, every clip baked and rendered; open: the test spider's touching legs and the test snake's fused snout fail one skin check each (mesh contacts) |
| M43a bake wins, M43b refine | **merged 2026-10-10** | ✅ m43a: 3 assets, seam delta 3.7→1.2 / 10.9→2.3 / 4.0→0.9, +1–3 s; m43b: pirate, farmer, chest, lantern refined with the lite Tile (2.5–4.5 min each, 12.4 GB), before/after sheets; the full Tile restarted the laptop and is out |
| M42 Library rework, the rest | **merged 2026-10-09** | ✅ on a copy of six maintainer assets: first hovers 2.9–3.6 s, cached 0.5 s; tags + filter; a Godot batch of five (four copied in 11 s, the concepts-only lantern skipped), the second run all skipped "exists"; the dwarf's 480_bake vs 100_bake compared |
| M44 Duplicate clips | **merged 2026-10-09** | ✅ on a copy of a test character: "Walk limp" from Walk, exported for Godot with all five clips, rendered: the copy differs as edited, Walk unchanged |
| M39 Import a model | **merged 2026-10-09** | ✅ the maintainer's files through the app: a test Unity zip, Godot GLB and a Mixamo-style cm FBX animated (every joint over every frame within 1.5 mm, the GLB exact), the cat's FBX on the fox template, OBJ/glTF zips, STL, PLY static at 0.99 m; an FBX's guessed alpha no longer makes a see-through face |
| M38 Animation tools | **merged 2026-10-09** | ✅ a test video clip: 31 foot contacts found, the worst slide 2.78 → 1.32 cm (the rest out of the straight leg's reach), the bake equal to the preview, Blender renders with the locked foot still; IK on target |
| M37 Animation editor | **merged 2026-10-09** | ✅ the cat keyed and saved through the app (bake = preview within 1.4e-8), re-opened with its key, exported for Godot/Unity/Unreal with the edit; a 14 s test clip edits at once |
| M30 Kimodo resident | done (`m30-motion-resident`), maintainer check open | ✅ the motion step stays loaded between prompts (first 30.7 s, then 3.7 s), evicted after 5 min idle; the app needs a restart |
| M34 Motion from video | **accepted 2026-10-08** after the maintainer's own run on a test character, merged with M32 | ✅ NVIDIA GEM-X in `.venv-gemx`, three modes, the cleanup, "Put this motion on the model" on the motion's page, a video preview with Start/End here; the dance clip in Best 105–132 s, 4.3 GB, foot slide 0.17 → 0.11 m/s with the lock; not run: the walk and sword-swing clips, a Godot/Unreal export of a video motion (`docs/bench/m34.md`) |
| M32 Rigid parts on the skeleton | **accepted, merged 2026-10-08** (with M34) | ✅ the viking's helmet rigid on Head (`527_parts` → `530_anim`): 0.00000 m off the Head bone's motion in Walk and the custom motion (1.6 cm without); Godot six skinned parts on one skeleton; Unreal combines skinned meshes unless Do Not Combine; `docs/bench/m32.md` |
| M31b Parts in the game files | done, merged 2026-10-08 | ✅ the viking's parts renamed (`523_parts`) and exported (`524_export` Godot, `525_export` Unreal): Godot 4.7.1 five named meshes with a convex body each, Unreal 5.8 five static meshes with 4 LODs and a hull each; Unreal 5.8 turns Nanite on at import (untick it to use our LODs); `docs/bench/m31.md` |
| M31 Parts | done, merged 2026-10-08; maintainer tried it 2026-10-07 on the viking (`521_parts` views, `522_parts` All parts) | ✅ GeoSAM2 in `.venv-parts` (`UV_PROJECT_ENVIRONMENT=.venv-parts uv sync --frozen --group parts`; model group `parts` on the Setup page); on the viking: views 12 s, a click 16–20 s, all parts 74–76 s, rig and skin unchanged on the split versions; up to 200k faces; the Parts tab; the app needs a restart (a new venv route) |
| M29 Setup page | **accepted 2026-10-07** (`m29-setup`, merged) | ✅ a plain-words summary, the feature cards in workflow order with a status dot and one button, gated-model steps as an expander in the card, Tools / Credits for your game / Advanced sections; Playwright 15/15 |
| M28 library browser | **accepted 2026-10-07** (`m28-library`, merged) | ✅ grid toolbar (search, sort, filter incl. archived, count), Archive on the cards, History with folded intermediate steps, a star and a label per version, "Starred only"; backend fields `archived` / `starred` / `label`; Playwright 14/14 |
| M27 one tab per step | **accepted 2026-10-07** (merged via `m26-motion`) | ✅ twelve upper-right tabs in workflow order, Face/Rig/Skin/Animate marked optional, per-step buttons on Clean/Simplify/Unwrap/Bake; Playwright 11/11 |
| M26 animate from a text prompt | **accepted 2026-10-07** (`m26-motion`, merged) | ✅ Kimodo in its own venv, the `motion` step (prompt → SOMA BVH → clip.glb), the SOMA map in the retarget, motion versions as `anim` clips, the Animate-tab UI and a Setup card; the app needs a restart for the new step; `docs/bench/m26.md` |
| M25 UX pass | **accepted 2026-10-06** (`m25-ux`, merged) | ✅ 30 panel texts rewritten for newcomers and set larger; "Where it sits" for every joint and marker (tooltip, line, "How to place joints" rules); Playwright 10/10 |
| M24 faces, round two | **accepted 2026-10-06** (`m24-faces2`, merged) | ✅ Real-ESRGAN 4× upscale of the image before the projection (model group `texture`, BSD-3), a zero-model local colour match that takes the large-scale shading from the bake and the detail from the image; Marigold-IID tried and dropped (too flat); eight characters, 8–9 s each (`docs/bench/m24.md`, `m24_faces.jpg`) |
| M23 faces from the image | **accepted 2026-10-06** (`m23-faces`, merged) | ✅ step `project`: the conditioning image projected onto the head where Pixal3D's camera sees it (silhouette match 0.92–0.96 on eight bench characters, 6–8 s), colour-matched to the albedo, a gate for misaligned images; "Face from image" in the UI; `docs/bench/m23.md`, `m23_faces.jpg` |
| M22b skeleton editor | **accepted 2026-10-06** (`m22b-editor`) | ✅ joints and markers pickable and movable with a gizmo (joints also rotatable: red ring = twist, other rings swing the subtree), multiselect and Select chain, live preview, undo/redo, Reset all, symmetry, depth cues (dim behind the surface, plumb line, X-ray, X/Y/Z fields), joints the inside check flags drawn red, Save → `rig_fit` overrides; the twist reaches the retarget (viking: auto `260_anim` palm-in, flipped edit `290_anim` palm-out; the maintainer's own edits `380`–`410`); Playwright 9/9 |
| M50 Pictures (2D) | **merged 2026-10-12** | ✅ Z-Image op `image`, three style LoRAs (peft), a cut-out: 29 s a 1920 × 1088 picture warm, 8.3 GB torch peak (`docs/bench/m50.md`) |
| M51 Pixelize | **merged 2026-10-12** (the other session's `2d-pixelize` + `2d-lab-palette`) | ✅ grid found or set, one palette (or PICO-8) over every frame, CIEDE2000, dither, outline, an exact recolour |
| M52 Sprites | **merged 2026-10-12** | ✅ the viking's 5 clips as 126 frames in 3.5 s; the 21-move brawler set as 382 frames in 7 s, pixel art in ~21 s (`docs/bench/m52.md`) |
| M53 Godot 2D, Paper2D | **merged 2026-10-12** | ✅ Godot 4.7.1 imports and plays the sheet headless (`docs/bench/engine-import.md`); Paper2D needs the UE editor (the maintainer's check) |
| M57 Pixel editor 2 | **merged 2026-10-13** | ✅ frames, timings, tags, play, onion skin, Draw it yourself; a hand-drawn spark plays once in Godot; the viking's Walk re-timed pixel-exact |
| M56 Pixel editor 1 | **merged 2026-10-13** | ✅ tools, palette lock, undo, Save → `pixelize` op `edit`; a line saved pixel-exact (browser test and live on the barrel) |
| M55 Parallax layers | **merged 2026-10-13** | ✅ the street background → 4 layers (speeds 0.05–1), Godot `Parallax2D` moves each at its speed headless (`docs/bench/m55.md`) |
| M54 Tiles and props | **merged 2026-10-13** | ✅ a seamless brick tile, 64 px pixel art, a Godot `TileSet` painted headless (120 cells); the barrel through `game-prop-2d` in 4 min (`docs/bench/m54.md`) |
| MX1 (Hunyuan, experimental) | not started | – |

## 2. What to do here, in order

### Next session (after 2026-10-16)

Done and recorded elsewhere: the 2D roadmap (M54–M57) and the bigger GPUs (M46a–c: IMPLEMENTATION's M46 sections,
`docs/bench/m46.md`, research 16; what is left there is the users' GPU reports for the 24/32 GB thresholds), the
agent's hoarding findings 1–22 (4 built as Setup's Start Blender; 5 decided: the port stays 8000).

0. **Validate M67** (built in the cloud, 2026-10-30, never run in Blender): `docs/LAPTOP.md` part 4, a wide
   character animated with and without `clearance`.
0. **Validate M66** (built in the cloud, 2026-10-30, never run on a GPU): `docs/LAPTOP.md` part 3, refine on the
   chest and a character with the defaults, beside an earlier refine of the same parent.
0. **Validate M65** (built in the cloud, 2026-10-30, never run on a GPU): `docs/LAPTOP.md` part 1. Check 0
   first (one import, seconds), then the seam numbers from `python -m steps._connect`.
0. **Validate M64** (built in the cloud, 2026-10-29, never run on a GPU): `docs/LAPTOP.md` part 2, the four laptop
   checks in IMPLEMENTATION M64's acceptance (two people, a push-up clip's hand locks, a panned walk in "still" and "panning", the numbers into
   `docs/bench/m34.md`). The panning camera is the one to doubt first: a camera that also walks reads as a turn.
0. **M61's big job** (M61 and M62 are merged): M61 was smoke-tested 2026-10-16; the full 33408 x 9600 redraw job (IMPLEMENTATION M61
   acceptance 3) is the maintainer's to run. Then the `clean` crash above.
1. **M60 The held weapon as a separate prop on the hand bone** (the maintainer's pick, 2026-10-13: the standing-pose redraw
   empties the hands). The other offered items (a Sprites warning without animations, Flat by default, Animate this
   character as one chain) were dropped by the maintainer. **Not started: design first** (an Expert design pass was begun
   2026-10-14 and stopped by the maintainer before it reported; nothing written).
   - **The maintainer's decisions (2026-10-13)**: the weapon stays its **own prop with an attach point** (not baked into
     the character mesh): swappable and droppable in the game. Its model can come from **all three**: a crop of the
     original picture, any prop asset in the library, a text description.
   - **Research facts** (read-only, 2026-10-13):
     - Where the weapon goes: `steps/canon.py:34-40` ("hands open and empty, nothing in the hands"; klein otherwise
       kept the raised sword arms); the character brief template (`steps/brief.py:23-24`, "empty open hands").
     - The original picture with the weapon survives: the first `prep` (canon's parent) and `001_upload`.
       `docs/WORKFLOW.md:72-73` already planned "separate attached assets".
     - A prop from a crop or a description is the normal prop chain (`game-prop` preset, `server/presets.py:214`):
       the core work is attaching a prop version to a rigged character version.
     - Rigid parts (`rig_skin` `rigid`, `make_rigid`) exist but bake into the skinned mesh: not this.
     - mixamo52 hands: `{L,R}Hand` + 15 finger joints each (`steps/_rig.py:16,31-36`); the hand's tail is
       `HandMiddle1`; per-joint `twist` = the palm normal (`_rig.palm_normals`). A grip default: near `HandMiddle1`,
       offset along the palm normal; the prop's long axis needs a rotation in the hand bone's frame (A-pose rest).
     - Export renames bones per profile (`blender_ops/export.py`: godot plain Mixamo, unreal Manny `hand_r`, unity
       `mixamorig:`); `use_armature_deform_only`, no leaf bones; no socket/attachment code anywhere today.
     - Engines (web knowledge, unverified here): Godot attaches with `BoneAttachment3D` (a generated `.tscn`, as M53
       writes text `.tres`/`.tscn`); Unreal skeletal-mesh sockets are not imported from FBX: a socket by hand in the
       editor (exact numbers in UE's frame: cm, Z-up, after the FBX bone-axis conversion) or a UE Python script.
   - **To design**: where the attachment lives (a new step/version on the character, its §3.2 row), the transform
     convention (bone-local, rest pose), the default grip/orientation/scale, the panel (reuse the existing gizmo and
     controls: the maintainer wants one interface, no one-off controls), the viewer showing the prop following the animation, the export per profile
     (prop as its own file + `attachments.json`; Godot `.tscn`; Unreal socket numbers), headless checks in Godot 4.7.1
     (and UE 5.8 if feasible). Out of scope at first: two-handed weapons, sheaths, shields.
2. **GPU checks of the cloud session's work** (merged 2026-10-13), one at a time: "Redraw this view" on the pirate's
   buckle with a description; the 4K checkbox once (its time and VRAM are unmeasured); the m43b five assets again for
   the new `detail` number (docs/bench/m43b.md).
3. **Mesh-gates leftovers**: `parts_apart` on an M43c character with an inner shell; validate on the largest library
   mesh; `joint_loops` still flags necks, heads, shoulders or hips on 10 of 16 bench rigs (tune its window, or limit
   it to limb bends). A flaky Playwright test: one full run of three on 2026-10-13 had one `toBeVisible` failure the
   next two did not repeat (not identified yet: run with `--repeat-each` to catch it).
4. **Maintainer-side**: Unreal's Paper2D import in the editor; NVDA and a real 200 % zoom; the female viking redone through
   Animate this character (her `_2` asset is the cardboard one); turn on Setup's Start Blender if wanted; the agent's
   retest of the 2026-10-13 fixes on a new asset.

### Earlier next-session notes

The notes of 2026-10-06 to 2026-10-12 (all done or superseded) are in [archive/handover-history.md](archive/handover-history.md).

## 3. Repo map

| Path | What |
|---|---|
| `server/` | FastAPI backend: `app.py` routes, `jobs.py` (subprocess jobs, GPU lock, resident worker, env for children), `library.py` (versions, `meta.json`), `models.py` (Models page), `blender_client.py` (live link), `claude_runner.py`, `presets.py` (`PRESETS`, `CHAINS`, `LOWER`), `blender_install.py` / `gltfpack_install.py` (pinned first-run downloads) |
| `steps/` | one process per step (§3.1 protocol in `_common.py`): `brief`, `concepts`, `prep`, `trellis2` (generate + texture), `clean`, `remesh`, `uv`, `bake`, `validate`, `export`; helpers `_bake.py` (UV raster + volume sampling, replaces nvdiffrast), `_mesh.py`, `_image.py`, `_birefnet.py`, `_blender.py` |
| `engine/trellis2/` | vendored upstream TRELLIS.2 (unmodified) + `patches.py` (runtime patches) + `PATCHES.md` (every patch, why, test) + the two Windows wheel patches |
| `engine/pixal3d/` | vendored Pixal3D + NAF (unmodified) + `patches.py` (na2d on torch, NAF loader, shared DINOv3) |
| `blender_ops/` | GPL-3.0 headless Blender scripts (export, quadriflow, unwrap, thumb, validate) |
| `mcp/effigy_server.py`, `.mcp.json`, `prompts/` | the `effigy` MCP server, MCP config (effigy + Blender MCP Pro), Claude presets |
| `ui/` | React + three.js (`src/main.tsx`, `src/viewer/Viewer.ts`), Playwright tests in `ui/tests/` |
| `tools/` | `download_models.py` + `models_manifest.json` (pinned models; the manifest is also read by `server/install.py` and `server/models.py`), `smoke_m1.py` (GPU troubleshooting), `bench.py`, `creature_bench.py` (M41's fit table), `gpu_report.py` (M46's report from other cards), `templates.py` (writes the creature templates' bones, M36), `ui_stale.py` (is `ui/dist` older than the UI source; `run.bat` and `package.py`), `package.py` (zip) |
| `wheels/` | our sm_120 CUDA wheels (built by `wheels.yml`) |
| `docs/` | PLAN, ARCHITECTURE, IMPLEMENTATION (contracts + milestones), WORKFLOW, SETUP, MANUAL, this file, `bench/`, `research/`, `howto/`, `ux-review/`, `archive/` (finished plans and history) |

Library on disk: `library/<asset>/<NNN>_<step>/` with `meta.json` (step, parent, params, metrics,
licences, frame). Models: `models/` (local copies) + `models/hub/` (HF cache: BiRefNet, Z-Image).

## 4. Dev checkout on this laptop (reuse the zip install's downloads)

Needs Git, uv, Node (SETUP.md appendix). Then, in PowerShell:

```powershell
git config --global core.longpaths true
cd C:\
git clone https://github.com/SifuInTheShell/Effigy.git Effigy
cd C:\Effigy
uv sync --frozen
$env:UV_PROJECT_ENVIRONMENT = ".venv-ml"; uv sync --frozen --group ml; Remove-Item Env:UV_PROJECT_ENVIRONMENT
```

Point the checkout at the zip install's models, library, Blender and gltfpack instead of downloading
~80 GB again (set them per shell, or as user variables):

```powershell
$env:EFFIGY_MODELS   = "C:\Effigy\models"
$env:EFFIGY_LIBRARY  = "C:\Effigy\library"
$env:EFFIGY_BLENDER  = "C:\Effigy\blender\blender-5.2.2-windows-x64\blender.exe"
$env:EFFIGY_GLTFPACK = "C:\Effigy\gltfpack\v0.25\gltfpack.exe"
```

Only one app may run at a time (both use port 8000 unless one is moved in Setup, App address): close the zip's `run.bat` window first. Start the
checkout's app with `.\run.bat` (or `uv run uvicorn server.app:app --port 8000` after the first run).

MCP: Claude Code started in the repo reads `.mcp.json` (the `effigy` server with relative paths, M40); any
other agent gets its config from Setup → "Connect an agent". For Blender MCP Pro the maintainer sets
`EFFIGY_BLENDER_MCP_PRO_DIR` and `BLENDER_MCP_PRO_LICENSE` (SETUP.md step 9). Never read, log or commit the
licence key.

## 5. Everyday commands (from `C:\Effigy`)

```powershell
uv run pytest -q                                   # backend, steps without torch (CI's set)
uv run ruff check . ; uv run ruff format --check .
$env:UV_PROJECT_ENVIRONMENT = ".venv-ml"; uv run --frozen --group ml pytest -q; Remove-Item Env:UV_PROJECT_ENVIRONMENT
uv run pytest -q tests/test_blender_ops.py tests/test_export_profiles.py   # with EFFIGY_BLENDER/EFFIGY_GLTFPACK set: no skips
cd ui; npm ci; npm run build; npx playwright test; cd ..
.\.venv-ml\Scripts\python.exe -m tools.smoke_m1    # GPU + CUDA wheels
.\.venv\Scripts\python.exe -m tools.bench --help   # benchmark (app must be running)
```

On this machine the ML suite also runs `tests/test_pixal3d_patches.py::test_pipeline_imports` (skipped
in the cloud: it needs our CUDA wheels).

## 6. GPU bring-up plan

Done: every generator, the chain, Blender and the exports ran on the laptop (`docs/bench/`). The plan, the list of code that had never run and §7 are in [archive/handover-history.md](archive/handover-history.md). A GPU error on a user's machine: `tools.smoke_m1` (SETUP.md, troubleshooting).

## 7. Without DINOv3

Approved and installed (PLAN §6: "Built with DINOv3"); the pre-approval notes are in the archive.

## 8. Decisions already made (don't reopen without the maintainer)

- **Licences**: one commercial-clean profile; check `docs/PLAN.md` §6 and add a `licenses.yaml` row before
  any new model or dependency. No nvdiffrast/nvdiffrec, no RMBG-2.0 (BiRefNet instead), no xformers /
  flash-attn (SDPA + our `xformers.ops` stand-in), no Make-It-Animatable, Hunyuan only in MX1.
- **No natten wheel**: NAF's `na2d` runs on plain torch (`engine/pixal3d/patches.py`). NAF code is vendored
  (Apache-2.0) and its weights are SHA-256-pinned, instead of `torch.hub` at run time.
- **Pixal3D**: cascades only (`1024_cascade`, `1536_cascade`); manual FOV; one shared DINOv3 for its four
  stage extractors (RAM).
- **gltfpack instead of gltf-transform** (no Node at run time); 3MF written with the stdlib (no lxml);
  flat meshes export without a collision hull.
- **Retexture keeps UVs by construction** (pipeline stages + our bake), so upstream PR #173 isn't needed.
- **HF**: we set `HF_HUB_CACHE=<models>/hub`, never `HF_HOME` (that would move the login token).
- **Child processes** get `PYTHONIOENCODING=utf-8` (Windows pipes default to cp1252).
- **First-run downloads** send a User-Agent (download.blender.org returns 403 to urllib's default).
- **Windows zip**: bundled uv 0.8.17, Python/cache/venvs inside the app folder, prebuilt UI, no Node.

## 9. Deferred on purpose (`ponytail:` notes; do when the trigger happens)

| What | Where | Add when |
|---|---|---|
| AO bake | `steps/bake.py` | an asset needs AO |
| MoGe-2 FOV estimation for Faithful | `steps/trellis2.py` | manual FOV visibly distorts |
| SPZ chunked decode, `EXPLICIT_GEMM` | `engine/trellis2/PATCHES.md` | decoder OOM or FlexGEMM "device not ready" |
| int64 `svox2vert` in CuMesh | PATCHES.md | a 2048 preset |
| Collision hull simplification | `steps/export.py` | an engine refuses a big hull |
| Concepts streamed as they finish | `steps/concepts.py` | waiting ~30 s hurts |
| UV overlap / texel-density targets in validate | `steps/validate.py` | a target needs them |
| A Retexture button in the UI | `ui/src/main.tsx` | the maintainer asks (today: Run a step → `trellis2` → preset `retexture`) |
| Per-step VRAM budgets instead of evicting the resident worker | `server/jobs.py` | it over-evicts |
| Character face budget above 15k, or a region-wise two-pass decimation (hands and head dense) | `server/presets.py`, `steps/remesh.py` | version 2, or a character whose fingers must read (`docs/bench/m23.md` M23b) |
| A closing of the voxel volume before the surface rebuild (hair strands → a smooth hood) | `steps/clean.py` | version 2, or hair that must be seamless; a finer rebuild made holes, experiment first |
| The 1536 generator profile for thin features | `steps/trellis2.py`, `server/presets.py` | a card above 16 GB, or a measured fit on the laptop |
| An image-side fix before generation (lighten dark slits, another seed) as a guided step | `steps/prep.py`, UI | a second knight-like case; today by hand in the picture |

## 10. Recording and committing

- Measured numbers only, never rounded up; each bench file says what was run, on which commit, driver,
  torch and Blender (`tools.bench` writes that header itself).
- Per fix: the smallest change at the root cause, a CPU test where one can catch it, `PATCHES.md` row for
  any engine patch, the milestone's "As built" note in `docs/IMPLEMENTATION.md` updated.
- Commit messages: what and why, then **Verified** (exact commands/results) and **Not run**.
- Push milestone branches off `main`; no CI trigger, no PR, no merge into `main` unless the maintainer asks.
- Never commit model weights, `library/`, `models/`, `blender/`, `gltfpack/`, `.venv*`, `.env`, `dist/`,
  credentials, or absolute paths; no model identifiers in repo files.

## 11. After bring-up: the roadmap

Phases 0–2 are closed; what is open is §2 above and PLAN §7. The pre-bring-up roadmap and the maintainer's requests of 2026-10-04 (all built: Kimodo, the library rework) are in [archive/handover-history.md](archive/handover-history.md).

## 12. Gotchas met so far

- Agent worktrees can be created from `main` (initial commit only): check the base before working.
- Test servers must be joined before the next test (`tests/conftest.py::live_server`): the app's
  shutdown kills running jobs.
- MSVC needs `.template data_ptr<T>()` in generic lambdas (FlexGEMM); o-voxel's `plyfile` is GPL and was
  made lazy (wheel patches in `engine/trellis2/`).
- `run.bat` must stay CRLF (`.gitattributes`); the zip ships `docs/`, `tests/fixtures/golden/` and a
  `VERSION` file (no `.git` in a zip install).
