# Pass 2 design: task navigation, panel order, the 2D track (for the owner's go)

Written 2026-10-12 after Pass 1 (`32f24f2`). Covers UX-05, UX-06, UX-07, UX-09, UX-15 and where research 15's 2D
track plugs in. **Built 2026-10-12 on `ux-pass2` as one PR** (the owner's decisions in §7); §8 lists where the build
departs from the text above.

## 1. What is wrong today, in one paragraph

`ui/src/workflow.ts` `STAGES` is a row of fourteen buttons, one per pipeline step, six of them optional, with chevrons
that promise one ordered chain. A button with a checkpoint opens it; one without either scrolls to a form (Face,
Detail, Parts, Rig, Skin, Animate) or is disabled (Check, Export) even when its form already sits lower in the panel.
`Actions` (`main.tsx:1848`) orders every checkpoint's panel by pipeline position, so a checked prop meets the nine
rig templates before its Export button (y = 1355 in a 864 px panel), an imported model meets Parts before Check, a
skinned character meets the text and video forms before the ready-made clips. Every clean/remesh/UV/bake checkpoint
opens with "Run this step again" and says "Preparation stopped partway" whatever happened after it.

## 2. The task model

A **task** is what the user is doing; a **checkpoint** is a version; a **tool** is an optional operation on the
shown checkpoint. The row shows tasks; History shows checkpoints; tools live in a menu and in collapsed sections.

### 3D asset

| Task | Steps it groups (`stageOf` keeps the fine index for History and labels) | Opens |
|---|---|---|
| Reference | `upload` (picture), `brief`, `concepts`, `prep`, `canon` | the newest reference checkpoint on the branch |
| Generate | `trellis2` (generate) | the raw model, or Reference's Generate form when none exists |
| Prepare | `clean`, `remesh`, `uv`, `bake`, retexture, Blender pulls of a prepared mesh; the tools Face, Detail, Parts | the newest prepared checkpoint, or the raw model's Prepare form |
| Character *(optional)* | `rig_fit`, `rig_skin`, `anim`, `anim_edit`, `motion`, `mocap` | the newest character checkpoint, or the rig form (no run) |
| Finish | `validate`, `export` | the newest check or export, or the Check form |

- **Imported model**: no Reference and no Generate; the row starts with one chip "Imported model" (the state the
  review asks for), then Prepare · Character · Finish. `isImported` already knows.
- **Tools ▾**: a native `<details>` menu at the row's end: Face from image, Texture detail, Parts, Edit in Blender,
  Advanced: run any step. An entry scrolls to its section in the panel and opens it. Entries show their state
  (available · has a checkpoint · needs a setup item, with the link). Nothing is removed.
- A task button never runs a step: it opens a checkpoint, or the task's configuration. It is disabled only when a
  prerequisite is missing (a title says which: "needs a prepared model"), never because it lies "in the future".
- Hash URLs, Back/Forward and branch-specific checkpoints stay exactly as they are (`navigate`, `assetHash`).

### 2D asset (`asset.json.kind === "2d"`, research 15)

| Task | Steps | Note |
|---|---|---|
| Picture | `upload` (picture), `brief`, `concepts` (op `image`) | the stills |
| Pixelize | `pixelize` | M51, built, no panel yet |
| Layers · Tiles | `layers`, `concepts` (op `tile`) | M54, M55 |
| Edit | `pixelize` (op `edit`) | the in-app editor, M56/M57 |
| Finish | `validate`, `export` (`godot-2d`, `aseprite`, `unreal-2d`) | M53 |

A `sprites` checkpoint (M52) of a 3D asset belongs to Character (its "Sprites" tool); the viewer picks the frame
view by the version's outputs, not by the asset's kind. `TASKS_3D` and `TASKS_2D` are two lists in `workflow.ts`;
`taskOf(versions, m)` maps a version to its task the way `phaseOf` does today.

## 3. The panel: one main outcome first, the rest below

The order per checkpoint, from the review's table, mapped onto `Actions`' branches:

| Checkpoint | First | Main action | Below, collapsed |
|---|---|---|---|
| Description | readiness line | **Generate 4 concepts**, or **Set up concept images** (a link) when the files are missing (UX-09) | edit description / category |
| Concepts | the grid, the selection | **Use this image** | Generate 4 more |
| Cut-out reference | generator recommendation | **Generate 3D**, or **Set up Faithful / Fast** when missing | Advanced: background, seed, generator |
| Raw model | budget, texture size, height | **Prepare model** | Advanced: topology, level and square, close slits, square the taper |
| Prepared mesh, imported model | the import summary / one line what it is | **Check model** | Export (open, secondary) · Tools: Parts, Face, Detail · Character: Fit a skeleton · Simplify geometry (imported) |
| Checked mesh | the checklist summary | **Export package** (destination, LODs, collision) | warning detail · Character · Tools |
| Clean / Simplify / Unwrap / Bake | the state line (§4) | **Continue preparation** (only when steps remain) or **View checked result** / Export | Run this step again (with its settings) · Tools · Character |
| Skeleton | fit summary, outside joints | **Attach to skeleton** (today "Skin it"; UX-15) | Edit skeleton, Edit markers, Turn |
| Skinned model | the QA summary | animation source as radios: **Ready-made** (default when clips are installed) · From a sentence · From a video; one form shown; **Add N animations** | Check / Export |
| Animated model | the clips, play | **Edit animation** | Check / Export |
| Export result | destination, file count | Download (Pass 3 makes "Save all files" primary, UX-10) | the files · export for another destination |

The primary stays reachable without scrolling at 1280×720: the main section is first in the panel; nothing sticky
(the panel scrolls on its own, a sticky bar would cover focused fields).

## 4. Preparation state (UX-07)

For a clean/remesh/UV/bake checkpoint `v`, in `Actions`:

- **completed**: a `validate` descendant of `v` exists on this branch → "Textures baked and checked" (or "Model
  prepared") + **View checked result** (pick it) + Export; no "stopped".
- **active**: a job of this asset runs on this branch (Workspace's `active`, passed down) → "Checking… step 4 of 5"
  from the job's chain position; no "Continue".
- **interrupted**: steps of `RECIPE` remain after `v` and nothing runs → "Prepared up to Unwrap; Bake and Check
  remain" + **Continue preparation** from `v`.
- **historical**: a later prepared checkpoint exists on this branch but no check → "An earlier step; the newest is
  Textured model 012" + Open it.
- "Run this step again" moves under a `<details>` in every state.

## 5. Readiness (UX-09) and copy (UX-15)

- `missing(group)` already exists. Where it is true the primary becomes the setup link for that capability
  (`#/setup#<card>`), the draft stays, and the run button is not rendered; when the group is installed the button
  comes back. A restricted version's Export is disabled with the explanation beside it.
- Copy: one outcome sentence per form ("Generate a 3D model from this reference", "Simplify and texture the model for
  your project", "Attach the mesh to the skeleton"); long help under a `<details>Help</details>`; "Shadows only" →
  "Remove baked shadows"; "Back" names where it goes. The two creation modes in two equal columns (`.modes`).

## 6. Build order (two PRs)

1. **Pass 2a, navigation and state** (`ui/src/workflow.ts`: `TASKS_3D`, `TASKS_2D`, `taskOf`, `prepState`;
   `main.tsx` Workspace: the row, the Imported chip, Tools ▾; `Actions`: the preparation states of §4; `styles.css`):
   Playwright: `taskOf` and `prepState` on fixture lineages (as `anim.spec.ts` tests pure functions); the row has
   ≤ 6 items and one line at 1280 px; an imported asset shows the chip; a bake with a later check never says
   "stopped"; a tool entry opens its section; hash and Back unchanged (the existing smoke test).
2. **Pass 2b, panel order, readiness, copy** (`Actions` and the panels; `GeneratePanel`, `PrepareForm`,
   `ClipPicker` (the source radios), `NewAsset`): Playwright: the review's acceptance walk at 1280×720 (a checked prop
   reaches Export without the creature templates in view; an imported model reaches Check before Parts; a skinned
   character reaches the clip list without the text/video forms); missing concepts → the setup link, no Generate
   button; `Attach to skeleton` posts `rig_skin`.

Both keep plain React, native controls, the viewer and every API as they are. After 2b the 2D milestones add their
panels into the same `Actions` dispatch (M50 `image`, M51 `pixelize`), and `TASKS_2D` is already in place.

## 7. Decisions for the owner

**Decided 2026-10-12 (owner)**: (1) the task names approved; (2) Tools as a menu, tried first, tabs as the fallback
if it does not work out; (3) both renames approved; (4) **one PR** for Pass 2 (2a and 2b together); (5) the 2D row
approved as listed. Build it as one branch off `main`.

1. Task names and count: **Reference · Generate · Prepare · Character · Finish** (Character as a task of its own,
   since rig → skin → animate is a chain; the review had the four tasks with the character tools under Tools).
2. **Tools ▾** as a menu at the row's end (recommended; one line at laptop width) rather than tabs.
3. The renames: "Skin it" → "Attach to skeleton", "Shadows only" → "Remove baked shadows".
4. Two PRs (2a navigation + state, 2b panels) or one.
5. The 2D row: **Picture · Pixelize · Layers · Tiles · Edit · Finish** (or Layers and Tiles as one task).

## 8. As built (2026-10-12)

- `workflow.ts`: `TASKS_3D`, `TASKS_2D` (`{id, label, needs, optional?}`; `needs` is the disabled button's title), `taskOf(versions, m, kind?)`
  (the 2D branch inside, by step: `pixelize` op `edit` → Edit, `concepts` op `tile` → Tiles; `mocap` → Character, since its parent is
  the video's upload), `prepState(versions, v, running?)` with `running` = the asset's running job's input version (`Job.input`),
  `remaining` = the recipe steps after `v` (no bake for an imported model). `STAGES` and `stageOf` are gone; `phaseOf` stays.
- The row (`Workspace`): a task button opens the newest checkpoint of its task on the branch; without one, Generate opens the
  newest cut-out, Prepare the newest raw model, Character and Finish reveal their section of the shown panel (`#character`,
  `#finish`); otherwise it is disabled with "Needs …". The Imported chip is a `span.chip`. **Tools ▾** is a `details.tools`
  after the row: it closes when an entry is chosen or the focus leaves it (no Escape handling); each entry says
  available · has a checkpoint · needs a setup item (a link to Setup) · not for this checkpoint, and `reveal(id)` opens every
  `details` around the target and scrolls to it. Face, Texture detail and Parts sit in `details#tools` under the main action,
  open when the shown checkpoint is that tool's own; Edit in Blender and Advanced keep their disclosures (`#blender`, `#advanced`).
- `Actions` takes `pick` and `running` (the running job's input, only for a recipe step). The preparation section says
  "Textures baked; checked." / "Model prepared; 2 checks failed." (View checked result + Export, for the mesh the check is
  of), "An earlier step; the chain was checked later (Checks 060)." (View checked result, no export of an unchecked mesh),
  "Preparation is running.", "Prepared up to Unwrap; Bake and Check remain." (Continue preparation) or "An earlier step; the
  newest is Textured model 012." (Open …). Run this step again is a disclosure in every state; one step run again shows all
  of its settings flat.
- Readiness (UX-09): the brief, New asset and the generator show the setup link as the primary (`a.button.primary` to
  `#/setup`, not to a card anchor: Setup has none); Edit description's run is disabled meanwhile; the restricted export is
  disabled beside its sentence.
- The checklist's items and the skin checks are under a Details disclosure, open when any item is not a pass.
- Copy: the two renames; Back → "Back to the skinned model" / "Back to the previous animation"; the long help of the raw
  model, the fit form, the skeleton, marker and animation editors under Help; one outcome sentence per form. The old "tab"
  wording (Rig tab, Animate tab, Parts tab) says Character / the Parts tool.
- The §4 "Checking… step 4 of 5" line is the panel's existing job block at the top; the preparation section itself says
  "Preparation is running." A `sprites` checkpoint (M52, pictures only, no `.glb`) needs its own line in `taskOf` when it
  arrives: today it would land in Reference, not Character.
- Not built: a sticky action bar (none needed), the 2D panels (M50–M58 add them into `Actions`), tabs as the menu's fallback.

## 9. Superseded (M71)

The structure in §8 (a title row with the task row and a Tools menu, one long panel of folded sections, Blender and
Advanced folded at its end) was replaced by M71: the app bar carries the asset's name and a Blender group; the task row
became underlined tabs; a tool strip under it lists the tools of the tab; the panel shows only the chosen tool. The Tools
menu, `details#tools`, `#character`, `#blender` and `#advanced` are gone; Advanced is "More settings"; Run any step is a
developer option. See IMPLEMENTATION.md §4, M71.
