# Effigy user manual

Effigy makes game assets on your own computer: 3D models from a sentence or a picture, cleaned, textured, rigged,
animated and exported for your engine, and 2D art from pictures and pixel art to sprite sheets, seamless tiles and
scrolling backgrounds. Everything runs on your NVIDIA card. Nothing is uploaded, and every model it uses allows
commercial use of what it makes.

This manual explains every part of the app. Installing it is in **[SETUP.md](SETUP.md)**, step by step.
**[docs/howto/](docs/howto/)** has guided walk-throughs, such as [animating a pixel-art
character](docs/howto/animate-a-pixel-art-character.md).

## Contents

1. [Starting the app](#1-starting-the-app)
2. [Finding your way around](#2-finding-your-way-around)
3. [The library](#3-the-library)
4. [An asset's page](#4-an-assets-page)
5. [Making a 3D model](#5-making-a-3d-model)
6. [Characters: skeleton, skin, animation](#6-characters-skeleton-skin-animation)
7. [Checking and exporting a 3D model](#7-checking-and-exporting-a-3d-model)
8. [Bringing in your own files](#8-bringing-in-your-own-files)
9. [Making 2D art](#9-making-2d-art)
10. [The pixel editor](#10-the-pixel-editor)
11. [Checking and exporting 2D art](#11-checking-and-exporting-2d-art)
12. [Editing in Blender](#12-editing-in-blender)
13. [Setup](#13-setup)
14. [Your graphics card and memory](#14-your-graphics-card-and-memory)
15. [Working with an AI agent](#15-working-with-an-ai-agent)
16. [Keyboard and mouse](#16-keyboard-and-mouse)
17. [When something goes wrong](#17-when-something-goes-wrong)
18. [Where your files are](#18-where-your-files-are)
19. [Words used in the app](#19-words-used-in-the-app)

---

## 1. Starting the app

- **Start**: double-click `run.bat` in the app folder. A black window opens; it *is* the app. Your browser opens
  **http://127.0.0.1:8000** (another port if you chose one in Setup, [App address](#app-address)).
- **Stop**: close the black window. A job that was running stops; every finished version stays in the library.
- Only one copy runs at a time. The app answers only your own computer, not the network.

### The first start: Get started

On the very first start the app opens **Get started**: five steps, about ten minutes of your time, then the
downloads run on their own. Everything can be changed later in [Setup](#13-setup).

1. **Your computer**: checks of the graphics card, the driver, the memory and Windows' long file paths. Green is
   fine, yellow works with less (what to do is written next to it), red has to be fixed first.
2. **Where the models go**: the model files are big (60 to 140 GB). Pick a drive with room, or type
   **Another folder**. An SSD loads them faster.
3. **What you want to make**: one of four sets, each with its size and download time.

   | Set | What it can make |
   |---|---|
   | **Props and objects** | pictures from a description, and 3D models from them or from your own pictures |
   | **Game characters** | props, plus humanoid characters: put in a standing pose, rigged, with ready-made animations |
   | **Characters with motion** | game characters, plus new motions from a sentence or from a video of a person |
   | **Everything** | all of it, plus the Fast 3D model, the four-view model, Parts and the 2D extras |

4. **Hugging Face**: a few models are shared only with people who accept their licence on Hugging Face, the site
   the models come from. Make a free account, accept the licences the page links to, then paste a **Read** token
   and click **Log in**. The token goes only into Hugging Face's own login on your computer; Effigy does not keep
   it. Approval of a licence can take a few hours.
5. **Install**: one button. You can close the page; the downloads go on, and the activity bar at the bottom shows
   them. At the end: **Ready. Make your first asset**.

A licence still waiting for approval does not hold the rest up: everything else installs now, and once Hugging
Face approves, click **Install** again for what is left. **Skip for now** leaves the guided setup; Setup has
**Open the guided setup** to come back.

---

## 2. Finding your way around

### The top bar

- **Library**, **New asset**, **Import**, **Setup**: the four pages.
- **GPU: idle**, **GPU: running clean**, and so on: what the graphics card is doing, which model is loaded, its use
  and memory. While a step runs it shows a meter.
- A **triangle with a drive letter** appears when a drive is low on space. It links to Setup, Space on disk.
- **Result ready** with **View result**: a job finished while you were looking at something else. The app opens a
  result by itself only when you have stayed where you started it and no editor holds unsaved work.
- A **red bar** under the top bar shows an error; **Dismiss** closes it.

### The activity bar

The bar at the bottom shows the newest job: its step, its status (**queued**, **running**, **done**, **failed**,
**cancelled**), the asset and version it works on, its progress and, for a chain of steps, **step 2 of 5**. Click
it to see its log. **Cancel** stops a queued or running job. **Earlier activity** lists the older ones.

The app runs **one job at a time**. More jobs wait in a queue, in the order you started them; the asset's panel
says **Waiting for the current operation to finish**.

### Assets and versions

Everything you make is an **asset** (a lantern, a hero, a stage background). An asset holds **versions**: the
picture, the cut-out, the generated model, the cleaned one, the textured one, and so on. Every step makes a new
version from an earlier one. **Nothing is ever overwritten**: you can go back to any version, try other settings
from it, and keep both results. The [History](#history) shows them all.

### Leaving with unsaved work

The animation, skeleton, marker and pixel editors keep their work until you save it. Leaving the page, switching
the version, closing the editor or reloading the browser asks **Discard the unsaved changes?** first.

---

## 3. The library

The **Library** page shows every asset as a card: its picture (a hover plays a turntable), its name, how many
versions it has and when it last changed. Chips on a card:

- **Restricted**: a part of it is not cleared for commercial use, so it cannot be exported.
- **Your file**: you imported it; Effigy does not know its licence.
- **Credit**: a model it used asks for a line in your game's credits (hover to read it).

### Finding assets

- **Search by name, id or tag**.
- Sort: **Recently changed**, **Changed longest ago**, **Name A–Z**.
- Show: **All**, **Mine**, **Archived** (**Bench** and **Golden** are the app's own test assets).
- **Tag**: one of your tags.
- **Clear filters** resets them. The search and filters stay when you open an asset and come back.

### An asset's More menu

**Rename**, **Tags** (separated by commas), **Archive** / **Unarchive** (hides it from the usual list) and
**Delete**. A deleted asset goes to **Recently deleted** at the end of the page, where **Restore** brings it back.
Setup, [Space on disk](#space-on-disk), empties it for good.

### Exporting several assets at once

**Select assets**, tick the ones you want, then **Export selected**. Choose the destination and a **Folder**, then
**Export and copy**. For each asset the app takes its newest starred version that is at least prepared, else its
newest one. An export it already made is reused, a restricted asset is skipped, and a folder that is already there
is not overwritten. The list **What will be exported** shows the choice before you start.

---

## 4. An asset's page

Open an asset from the library. At the top: its name and **Rename**, the shown version, **Licences** and
**History**. Below: the **task bar**, the **view** (left) and the **panel** (right).

### The task bar

The tasks are the stages of making the asset. A task with **✓** is done. Clicking a task opens its newest version,
or the place in the panel where it starts; it never runs anything by itself. A task that cannot start yet says what
it needs.

| 3D asset | 2D asset |
|---|---|
| **Reference** (the picture or description) | **Picture** |
| **Generate** (the 3D model) | **Pixelize** (pixel art) |
| **Prepare** (cleaned, simplified, textured) | **Layers** (parallax) |
| **Character** (optional: skeleton, skin, animation) | **Tiles** (seamless) |
| **Finish** (check and export) | **Edit** (the pixel editor) |
| | **Finish** (check and export) |

A model you imported starts at **Prepare**.

### The panel

The panel shows the **next step** for the shown version as one main button, with the settings you may want to
change under it. Other things you can do are in folded sections below it. The **Tools ▾** menu opens the extra
tools, and its grey text says when each one is available:

| Tool | When |
|---|---|
| **Face from image** | at Prepare, once textured; generated models only |
| **Texture detail** | at Prepare, once textured |
| **Parts** | at Prepare |
| **Sprites for 2D** | at Prepare (one still frame) or after Character (animated) |
| **Edit in Blender** | from Generate on, once there is a 3D model |
| **Advanced: run any step** | always |

A tool whose model is not installed says **needs a setup item** and links to its card in Setup.

While a step runs on the asset, the panel shows what it is doing (**Generating the 3D model…**, **Baking
textures…**) with a **Cancel** button.

### The 3D view

- Drag to orbit, scroll to zoom, right-drag to pan. **Frame model**, **Front**, **Side**, **Top** set the camera.
- **Inspect**: **Material**, **Base color**, **Surface normals**, **Ambient occlusion**, **Roughness**,
  **Metallic** or **UV checker**.
- **Wireframe** shows the mesh (quads as quads). **Tint parts** colours a model's named parts.
- On a rigged model: **Animation** (pick a clip to play it), **Skin weights** (red moves fully with the bone, blue
  not at all) and **Skeleton**.
- Under the view: the model's triangles and vertices, or, while a clip plays, the time bar: play/pause, frame by
  frame, a slider, the frame number and **Speed** 1×, 0.5×, 0.25×.
- On a cut-out, **Cut-out** / **Original** shows the picture it came from. Beside a model, a small reference
  picture enlarges when you click it.

### History

**History** opens a list of all the versions beside the view. Click one to show it. Each row has:

- **☆** to star it (stars keep a version easy to find; **Starred only** shows just those, and a batch export takes
  the newest starred version),
- **⇆** to compare it with the shown version: the two side by side, with their numbers and the differences marked
  (**Swap**, **Close**),
- **✎** to give it a label of your own,
- **×** to delete it and everything made from it. It goes to **Recently deleted** in the library.

Runs of cleaning, simplifying and unwrapping fold into one row, **N steps**; click to unfold. **Escape** closes
the History.

### Licences

**Licences** lists every component the shown version was made with, its licence and any credit line it asks for.
**Your file** means Effigy does not know an imported file's licence: it is yours to check. **Restricted** means a
component is not cleared for commercial use, so the version cannot be exported.

### Advanced: run any step

For when the usual buttons are not enough: choose a step (each says what it does), optionally a preset, write its
settings as JSON (`{}` for the defaults) and **Run**. The result is a new version like any other. The steps' help
texts list their settings. Agents use the same steps (see [section 15](#15-working-with-an-ai-agent)).

One worth knowing: **Transform: stand up, turn, scale, place** moves a still model without Blender, in about a
second. For example `{"level": true, "rotate_deg": [0, 90, 0], "height_m": 1.2}` stands it upright, turns it a
quarter about the up axis and makes it 1.2 m tall, its base on the ground. `"scale": [1.0, 1.0, 0.8]` scales each
axis on its own, to fit a planned width, height and depth. `"square": true` turns it square to the axes, and
`"face": "-z"` (or `"+z"`, `"+x"`, `"-x"`) turns its front, the side the picture showed, toward that axis. Its textures stay as they are. The result
shows as **Moved model** in History.

---

## 5. Making a 3D model

### From a description

1. **New asset → A 3D model → From a description.**
2. Write a **Description**, for example *A worn brass lantern for a stylized adventure game*. **Name** is optional.
3. Pick the **Kind**, which frames the picture for a clean 3D reference:
   - **Object**: centred, three-quarter view, plain grey background.
   - **Character**: full body in an A-pose, front view, plain background.
   - **Creature**: full body, side three-quarter view, legs apart.
4. **Generate 4 concepts** (about 70 seconds). The four pictures appear in the view, ranked **#1** to **#4**.
   Each says **reference checks passed** or what it lacks: one main subject, the subject inside the picture and
   filling it, enough detail, a simple background, visible shading, clean edges. The ranking is only a hint; it
   does not promise a good model.
5. Click the best picture, then **Use this image**. **Generate 4 more** makes others; **Edit description** changes
   the words first.

**Use this image** cuts the subject out of its background, and the asset moves on to [Generate](#generate).

### From your own picture

1. **New asset → A 3D model → From an image.**
2. Drop a **PNG, JPEG or WebP** picture on the page, or click to choose one.
3. **Views in the picture**: **One view**, or a **Turnaround sheet** with the views side by side (**Front · Right ·
   Back · Left** and other orders). Use equal-width columns with the same pose and zoom; the views must be the same
   height within 5 %. "Right" means the character's right side faces you. A turnaround needs the Setup card
   **3D model from four views**.
4. **Remove background**. You can check the cut-out before the model is made (**Cut-out** / **Original** in the
   view). A picture that is already transparent keeps its own cut-out.

The best pictures for 3D show the whole subject, well lit, on a plain background, without other objects. For a
character: standing, arms a little away from the body, both legs visible.

### A character in a standing pose

Characters rig and animate best standing in an A-pose (arms down at about 45°, a little away from the body). A
description of kind **Character** already asks for one. For a character in another pose, the Generate panel on its
cut-out has **Put it in a standing pose** (about a minute): the picture is redrawn standing, with the same clothes
and colours, and cut out again. Generate from that one. A redrawn character shows **Redraw again** instead: a blank
face or other clothes are a seed away.

The button shows on the cut-out of a character from a description, and of any picture of your own (objects,
creatures and turnarounds do not get it). It needs the Setup cards **Standing pose** and **Character skeleton**;
without them it links there.

### Generate

On the cut-out, the panel shows **Generate the 3D model** with the generator the app recommends for your card and
**Generate 3D**. Generating takes about two minutes.

| Generator | What it is |
|---|---|
| **Faithful HD** | Pixal3D at 1536: a finer shape and 4K textures. For 24 GB cards; about 3.5× as long |
| **Faithful, high quality** | Pixal3D with 20 steps and 4K textures; about a quarter longer than Faithful |
| **Faithful** | Pixal3D: follows the picture closely |
| **Fast** | TRELLIS.2: another model, needs less memory |

The best one your card runs is chosen and marked **Recommended for this GPU** (on a 16 GB card: **Faithful, high
quality**). Pick another under **Advanced**; the app remembers your pick. A generator your card cannot run is shown
greyed out with the memory it needs. More under **Advanced**:

- **Background behind your picture**: **Automatic**, **Grey** (for dark subjects such as black fur or dark
  armour), **Black** or **White**. The generator sees your cut-out on a plain background; on black, very dark parts
  can be taken for background and come out missing.
- **Seed**: empty for a random one; a number repeats a result you liked closely.

**Try another variation** generates again with a new seed. Each result is a new version, so you can compare them
in History.

### Prepare

A freshly generated model shows small holes and loose bits, and has far more triangles than a game wants.
**Prepare model** runs **clean → simplify → unwrap → bake → check** in one go:

- **Clean** repairs the surface: closes small holes, removes loose bits, makes it watertight. A generated model is
  also **stood upright on the ground**: the 3D model keeps the picture's camera, which often looks down at the
  subject, so a barrel or a chest comes out leaning 10–15°. Its floor is levelled (a post stands on its shaft).
  Small scraps floating off its outside (a flap hanging in the air in front of it) are dropped; a piece that
  touches it, or floats inside it like a lantern's flame, stays.
- **Simplify** brings it down to the **Triangle budget** (default 10,000).
- **Unwrap** lays out its UVs.
- **Bake** paints the textures at **Texture size** **1K** or **2K**: base colour, roughness, metallic, a normal map
  and ambient occlusion, from the generated model's colour volume. An imported model has no such volume, so the bake paints its own
  materials and textures onto the simplified mesh instead.
- **Check** runs the game-readiness checks (see [section 7](#7-checking-and-exporting-a-3d-model)).

**Height in metres** scales it to its real size (empty keeps it). Under **Advanced**:

- **Topology**: **Triangles** (game-ready, smallest) or **Quads** (clean loops for rigging and sculpting; the budget
  then counts quads).
- **Close slits up to (mm)**: shuts hairline slits, such as between the boards of a fence (12 works well). Left
  empty, a generated prop whose simplified surface comes back folded through itself is simplified again with its
  slits shut, by itself.
- **Stand it upright on the ground**: on for a generated model; off keeps it leaning as it came.
- **Also turn it square to the axes**: a prop from a three-quarter picture also comes out turned 30–45°; this turns
  its long side onto X, so a wall or a fence lines up with a grid. It may turn a chair's front to the side, so it is
  off by default.
- **Press what hangs just under its base onto it**: a detail from the top can come back mirrored underneath (a
  lifting eye under a concrete block) and hold the model up off the ground. This presses a small part under the
  middle of a broad flat base flat onto it; legs and rims are left alone.
- **Square a flat prop's perspective taper**: for a fence, wall or sign drawn in perspective.

If preparing stops halfway, **Continue preparation** picks it up. **Run this step again** repeats one step with
other settings. A model with a whole area missing (a black cat's back) is better generated again with
**Background: Grey**.

### Face from image

Copies the face from your picture onto the texture, so the face is as sharp as the picture. On a prepared,
textured model: **Tools → Face from image**, choose the **Region** (**Head** or **Whole front**) and the
**Strength**, then **Face from image** (a few seconds). If the picture does not line up with the model, it says so
and changes nothing. The Setup card **Face detail** sharpens the picture first (4× upscale); without it the face is
copied as it is.

### Texture detail

Redraws a texture's fine detail (buttons, stitching, wood grain) from close-up views; the shape and the colours
stay. **Tools → Texture detail**, then **Refine texture** (about 4–5 minutes). **Description** guides it (the
asset's own by default), **Detail strength** sets how far it may redraw. Compare the result with the version
before it in History: it can invent detail.

**Remove baked shadows** (a few seconds, no redraw) takes the shading the generator baked into the colours back
out, so your engine's lighting does not double it. **Baked shadows to remove** sets how much.

It needs the Setup card **Texture detail**. On a 24 GB card, the card **Texture detail, full** adds the full model,
which gives cleaner detail and is used by itself once installed.

### Parts

Finds the pieces of a model and names them, so your game files keep each piece as its own object. **Tools →
Parts**:

1. **Render the views** (twelve pictures, about 15 seconds).
2. Click the piece in the big picture: two or three points spread over it. Hold **Shift** (or tick **Next click is
   not the part**) to mark a point just outside it. **Clear points** starts over.
3. Type the **Name of the part** and **Find part**. **All parts** finds every piece at once (about a minute).

**Tint parts** in the view shows them. Named parts can be made rigid on a bone when you rig the character (a helmet
on the head, a sword in the hand), and a print export makes one closed solid per part. It needs the Setup card
**Parts**.

---

## 6. Characters: skeleton, skin, animation

On a prepared model the task **Character** opens **Rig for animation (optional)**.

### Fit a skeleton

1. **What is it?** **A person or humanoid character**, or one of eight animals and creatures: a four-legged animal
   (dog, cat, fox, wolf), a hoofed animal (horse, deer, cow), a bird, a two-legged monster with a tail (kaiju,
   dinosaur), a shark or fish, a winged dragon, a spider or other eight-legged creature, or a snake.
2. **Fit a skeleton** (about 10 seconds). A person works best standing upright with the arms away from the body;
   an animal standing on all its legs.

The result says whether every joint is inside the model, and names the ones that are not, or whose place it was
unsure of. A creature **Facing the wrong way?** has **Turn around** and **Turn a quarter**.

If a joint is wrong:

- **Edit markers** (people): twelve points (chin, groin, and both shoulders, elbows, wrists, knees and ankles).
  Click a marker, then click the model where it belongs. **Symmetry** moves left and right together; **X-ray** sees
  through the model. **Save markers** rebuilds the skeleton from them.
- **Edit skeleton**: move or turn single joints. Pick a joint from the lists (torso, arms, legs, fingers), drag it
  in the view or type its X/Y/Z. **Symmetry** mirrors every edit, **Select chain** picks a joint and the ones after
  it, **Reset joint** and **Reset all** undo, **Save skeleton** keeps it. Keys: **G** move, **R** rotate.
- **Check fit** compares the fit with the one before and lists the joints to look at.

It needs the Setup card **Character skeleton** (the joint finder).

### Attach to skeleton

**Attach to skeleton** (about 10 seconds) gives each vertex its weights, then tests the result in ten poses. Two
engines exist: Blender's bone heat (always there) and **Learned skinning** (SkinTokens, a Setup card); by default
both run and the checks pick the better one.

**Skin checks** lists what the poses showed: vertices no bone moves, weights that do not add up, too many bones on
one vertex (engines take four), a left/right difference, volume lost or gained, the largest stretch, joints with
too few edge loops to bend. Each failure says how to fix it, often **Edit skeleton** and attach again.

Named [parts](#parts) appear here first: for each, **How it moves**: **Bends with the body**, **Rigid, on the
nearest bone**, or rigid on a bone you pick.

Use **Skin weights** and **Skeleton** in the view to see the result.

### Animate

On the skinned model, **Animation source** has four ways:

- **Ready-made**: tick clips from the list (idle, walk, run and dozens more for people; each animal has its own),
  or pick a **Move set**: **Brawler (Double Dragon)**, **Sword fighter (Golden Axe)** or **Basic (idle, walk,
  jog)**. **Add animations** (about 10 seconds each). Needs the Setup card **Ready-made animations**.
- **From a sentence**: describe the motion (*A person waves with the right hand.*), its **Seconds** (0.5 to 10)
  and a **Seed**, then **Generate motion** (about 30 seconds the first time, a few seconds after that). Buttons for
  fighting moves the packs lack (**Roundhouse kick**, **Flying kick**, **Uppercut**, **Grab and knee**, **Throw**,
  **Back elbow**) fill in the sentence. The motion opens on its own page; **Put this motion on the model** adds it.
  Needs the Setup card **Text to motion**, which needs a Hugging Face login with access to Meta Llama 3. For people
  only.
- **From a video**: an MP4, MOV or WebM of one person, head to toe, with a still camera. Set the part to use with
  **Start here** / **End here** (at most 30 seconds; 15 seconds take about 2 minutes). **Quality**: **Best**
  (smooth and accurate), **Quick** (closest to the video, a little shakier) or **Light** (from the body points
  alone, may drift). **Keep planted feet still**, **In place (no travel)** and **Smoothing** clean it up. **Make
  motion from video**. Needs the Setup card **Motion from video**. For people only.
- **From a file**: an FBX, GLB, glTF or BVH animation on a Mixamo, Unreal (Manny, Quinn) or Kimodo skeleton.
  **Import animation**. Its licence is yours to check.

The animated model plays its clips from the view's **Animation** menu. A clip whose bones did not match says it
**never moves**.

### The animation editor

**Edit animation** opens the editor: the timeline under the view, the tools in the panel. It changes a copy;
**Save as a new version** keeps your edits, the original stays as it was.

- **Clip**: pick the clip. **Duplicate** makes a copy to change (to make a variation); **Rename** and **Delete**
  work on copies. **Compare**: **Edited** / **Original**.
- **Timeline**: **Keys** (a dope sheet) or **Curves** (for a selected bone). **Show**: **Motion path**, **Onion
  skin** (ghosts a few frames before and after), **X-ray** (the model see-through, the skeleton inside it clear).
- The skeleton is drawn the way Blender draws an armature: a bone is a long diamond from its joint to the next, its
  flat sides turning as the bone twists; the selected bone is blue, the bones an IK control bends are yellow, a bone
  keyed at this frame has orange edges, and a dashed line joins a bone to a parent it does not touch (the hips to
  the legs). Click a joint or anywhere on a bone to select it; its name shows next to it.
- Select a bone in the view. **Rotate** (**R**), **Move** (for a bone that moves in the clip) or **IK drag** (drag a
  hand or a foot and the arm or leg follows; **G** grabs: Move for the hips, IK drag for the rest). **Chain** says
  how many bones above it bend: 2 for an arm or a leg, more for a spine, a neck or a tail (**Page Up** / **Page
  Down**). **Key it here** (**I**) sets a key at the current frame; the key's angles can be typed. **Falloff**
  blends a change into the frames around it; **Whole clip** changes every frame. **Delete key**, **Reset bone**.
- **IK target** keeps a hand or a foot on a target that you key over time, the bones above it bending to reach it
  (a hand on a table, a foot on a step): drag the yellow box; drag the yellow diamond (**Pole**) to swing the knee
  or the elbow without moving the hand or foot. A key holds the target's place (X/Y/Z), the **Swivel** of the knee
  or elbow, the **Weight** (0 %: the animation as it was, 100 %: on the target) and a **Falloff** over which the
  weight fades before the first key and after the last. **Space**: **World** keeps the target still while the body
  moves; a bone (the hips, the chest) carries it along. **IK controls** lists them; each has a row of its own in the
  timeline. To make one, select the bone, press **IK target**, then drag the box or press **Pin it here** (**I**);
  **Remove IK control** drops it with all its keys (**Ctrl+Z** brings it back).
- **IK drag or IK target?** IK drag poses one frame: the bones above get ordinary turn keys there and nothing stays
  attached, so on other frames the hand goes where the clip takes it (a punch that falls short). IK target holds the
  hand or foot on its target on every frame the control covers, the IK solving each frame (a foot planted while the
  body moves, a weapon in both hands).
- **Foot locks**: **Find foot contacts** finds where a foot should stand still; **Lock … here** keeps it planted
  from one frame to another, with a blend in and out.
- **Clip tools**: **Trim** (first and last frame), **Speed**, **Loop match** (the end blends into the start),
  **Ground** (with **Snap to ground**), **In place (no travel)** and **Smoothing** per body part (steadies jitter,
  for example from a video).
- **Undo**, **Redo**, **Keys as a list** (every key, reachable by keyboard).

Keys: **Space** play, **←** **→** a frame, **Home** **End** first and last frame, **I** key, **X** or **Delete**
delete keys, **R** rotate, **G** grab, **Page Up** / **Page Down** a longer or shorter IK chain, **Ctrl+Z** /
**Ctrl+Shift+Z** undo and redo, **Ctrl+wheel** zooms the timeline.

---

## 7. Checking and exporting a 3D model

### Check model

**Check model** runs the game-readiness checks and lists them as passed, warnings or failed, each failure with how
to fix it: non-manifold edges, open edges, separate parts, holes through the surface, degenerate faces, UVs
(present, inside 0–1, overlap), the triangle count against your budget, the height, a surface folded through
itself, a part inside another. When the description gave a size ("2.1 m tall, 3.2 m long"), it also says how far
the model's height and length are off it. Rigging, animation and print wall thickness are not checked. A later
edit needs a new check.

### Export

Under **Finish → Export**, pick the **Destination**:

| Destination | What you get |
|---|---|
| **Godot** | GLB; Godot makes the LODs itself |
| **Unreal** | FBX with LODs; collision optional |
| **Unity** | FBX with LODs |
| **Web / AR** | a small GLB plus USDZ, textures at most 1024 px |
| **OBJ** | OBJ with its material and colour texture |
| **3D print** | STL and 3MF in millimetres (one solid per named part) |

**LODs** (Unreal and Unity) adds lower-detail copies. **Collision hull** (Godot and Unreal) adds a simple shape for
physics; it is off for rigged characters. **Export for …** makes the files; a model that failed its checks says
**Export anyway**, and a restricted model cannot be exported.

Every export comes with **CREDITS.txt** and **LICENSES.json**: the credit lines and licences your game must carry.
Then:

- **Save all files to the folder**: type your project's folder once (it is remembered); the files go into a new
  `<asset>_<destination>` folder there. An existing one is never overwritten.
- **Download** a single file, or the files one by one.
- **Export for another destination** to make another set.

To export many assets at once, see [the library](#exporting-several-assets-at-once).

---

## 8. Bringing in your own files

### A model

**Import → A model**: FBX, glTF or GLB, OBJ, USD or USDZ, STL and PLY, up to 1 GB. A model that comes with other
files (an OBJ with its .mtl and textures, a .gltf with its .bin) goes in as one **.zip**: right-click its folder,
**Send to**, **Compressed folder**.

A character on a Mixamo-style skeleton, or one exported from this app, keeps its skeleton and animations; anything
else comes in as a still model you can rig here. **Keep it as a still model** ignores a skeleton.

**What came in** shows the file, its height, triangles, materials and textures, skeleton and animations, and what
the import did (turned it round to face forward, parts left out, textures not found). A model of an odd height
(made in centimetres or inches) gets a one-click scale fix: **Centimetres**, **Millimetres**, **Inches**, **×100**,
**×1000**.

An imported model starts at **Prepare**. **Check the model** runs the checks; **Simplify geometry** makes a lighter
copy with its materials baked into one texture set (the Bake step is offered when the model has materials); skin and
animation are not kept. From there it goes on like a generated one: rig, animate, export. A skeleton that is not
one of this app's is dropped, and the model is rigged with this app's own.

### An animation for one of your characters

**Import → An animation for a character**: pick the **Character** (it must be skinned already: Character task,
**Attach to skeleton**), choose an FBX, GLB, glTF or BVH file on a Mixamo, Unreal (Manny, Quinn) or Kimodo skeleton
and **Import animation**. A file with several animations brings in the first; its page offers the others
(**Bring it in too**).

---

## 9. Making 2D art

A 2D asset holds pictures, pixel art, sprite sheets, tiles and layers. Start one with **New asset → A 2D picture
or sprite**.

### A picture from a description

1. **From a description**, then write the **Description**.
2. Pick the **Kind**, which adds framing for the picture:

   | Kind | What it adds | Starting size |
   |---|---|---|
   | **Picture** | nothing: your words as they are | square |
   | **Wallpaper** | a wide scene, no text | 16:9 |
   | **Game background** | a side-scrolling stage with a floor band, no characters | 8:3 |
   | **Portrait** | head and shoulders on a plain background | square |
   | **Icon** | one bold shape on a plain background | square |
   | **Item or sprite** | one object, side view, cut out | square |
   | **Seamless tile** | a material that repeats in every direction: a wall, a floor | 512 px |

3. **Size**: **Wide 16:9**, **Stage strip 8:3**, **Square**, **4:3**, **Tall 9:16** or **Custom** (256 to 2048 px a
   side, in steps of 64; a size in between is rounded when you leave the field).
4. **Pictures**: how many (1 to 4).
5. **Style**: **As described**, **Pixel art**, **Classic painting** or **Children's drawing** (the styles need the
   Setup card **2D styles**).
6. **Cut out the subject** gives a transparent background (on for items and icons).
7. **Make … pictures** (about 30 seconds each). Pick one in the strip under the view.

### A picture you have

**From an image**, drop a PNG, JPEG or WebP and **Add the picture**. It stays as it is (no cut-out); make pixel art
from it next.

### Pixel art

On a picture, **Pixelize** (or the panel's **Make it pixel art**) turns it into true pixel art: whole pixels, one
palette, a clean edge.

- **Pixel size**:
  - **The grid the picture was drawn on** (for pictures that already look like pixel art: the app finds the grid),
  - **A height for the subject** in pixels (8 to 1024; 64 for a sprite, 360 for a wide scene),
  - or **A fixed pixel size** (picture pixels per pixel art pixel).
- **Colours**: **From the picture** (with the **Number of colours**, 2 to 64, default 16) or the **PICO-8 palette
  (16)**.
- **Dither**: **None**, **Ordered 2 × 2** or **Ordered 4 × 4**.
- **Outline**: **None**, **Darkest colour** or **Black**: a one-pixel outline round the figure.
- **Advanced**: **Each pixel's colour** (**The most frequent** for pixel-style pictures, **The middle value** for
  renders and photos) and when a pixel counts as transparent.

**Make pixel art** (seconds). The result gives the **Sprite sheet (PNG)**, the **Sheet data (Aseprite JSON)**, the
**Palette (GIMP .gpl)** and **The picture (PNG)**. If no pixel grid was found it says so: pixelize again with a
height for the subject. **Pixelize again with other settings** keeps the first try.

**Palette and recolour** changes colours without touching a pixel: pick new colours for the swatches and **Make the
recoloured version**. That is how you make an enemy variant or a second player.

### Sprites from a 3D character

A 3D model drawn from the side in whole pixels, every animation in place, makes a sprite sheet for a 2D game. On a
prepared model (one still frame per view) or an animated character, **Tools → Sprites for 2D**:

- tick the animations,
- **Views**: **Facing right**, **Facing left**, **Front**, **Back**,
- **Height in pixels** (104 the first time; the first run's scale is kept for later runs, so every move matches),
- **Frames per second** (12 by default) and **Look**: **Lit (studio light)** or **Flat (the texture unlit)**,
- under **Advanced**, **Least cell (px)** (128 by default) and **Camera tilt (°)** (12 by default, 0 to 45),

then **Render sprites** (seconds) and **Make pixel art** on the result.

**Animate this character** goes the other way: on a still pixel-art picture of a character, **Make a 3D character
from this picture** redraws it as a shaded 3D figure, standing, in a new 3D asset, ready to generate, rig and
animate. Attacks and hits play once in the sheet; walks loop. The whole road, picture to Godot, is in the how-to
**[Animate a pixel-art character](docs/howto/animate-a-pixel-art-character.md)**.

### Seamless tiles

**New asset → A 2D picture or sprite → From a description**, **Kind: Seamless tile**, a **Tile size** (256, 512 or
1024), then **Make a seamless tile**. Its edges are drawn again so they continue each other: the tile repeats in
every direction with no visible seam. The view shows it **Repeated 3 × 3** or as **One tile**.

**Pixelize** keeps it a tile: the pixel size must divide the tile's (a 512 tile: 8, 16, 32, 64, 128 or 256 pixels a
side). The check measures whether the edges meet (1.10 or less is seamless). Export it for Godot as a TileSet.

### Upscale or redraw a picture

On a picture, **Upscale or redraw** (a disclosure in the panel) makes it bigger or draws it again. **Make it bigger**
enlarges it 2× or 4× a side (Real-ESRGAN; needs the Setup card **Face detail**). **Draw it again** repaints it from itself in
overlapping windows, so any size works: give a description (there is no negative prompt), a strength (0.2 to 1: how far
it is redrawn), optionally a style, a bigger size first, and a **mask**, a grey picture in the same asset (white stays,
black is redrawn); **Add the mask** uploads one. The panel says how many windows it will take (about 10 seconds each).
The new version shows how far it moved from the picture and whether the windows meet. **Draw it again** needs the Setup card **Texture detail**.

### Parallax layers

A background picture split into layers from far to near, which scroll at their own speeds as the camera moves: the
classic side-scroller depth. On a picture (a **Game background** or **Wallpaper** shows it first; any picture under
**Parallax layers**):

- **Layers**: 2 to 5 (default 4).
- **A figure in front as a layer of its own**: for a picture with a subject in front.
- **Make parallax layers** (about two minutes). The picture's depth decides the cuts, and what each layer hides
  behind the nearer ones is drawn in, so it can scroll past.

The view plays the layers scrolling (**Play**/**Pause**, the **Camera** slider), or shows them one by one or as the
**Bands** with **Show**. The depth version shows **Picture** / **Depth** (lighter is nearer). Each layer downloads
on its own, with its scroll speed. Needs the Setup cards **Parallax layers** and **Concept images**.

---

## 10. The pixel editor

Draw on pixel art and sprite sheets, frame by frame, in whole pixels. Open it with **Edit the pixels** on any pixel
art or sprites version, or the **Edit** task. To start from an empty sheet: **New asset → A 2D picture or sprite →
Draw it yourself**, set **Width** and **Height** (4 to 512 px), **Frames** (1 to 64) and **Colours** (**PICO-8 (16
colours)** or **Any colour**), then **Start drawing**: the blank sheet opens in the editor.

### Drawing

| Tool | Key | What it does |
|---|---|---|
| **Pencil** | B | pixel by pixel; corners of a diagonal are dropped so curves stay one pixel thick |
| **Eraser** | E | makes pixels transparent |
| **Fill** | G | fills the touching area of one colour (off **Only the touching area**: every pixel of that colour) |
| **Line** | L | a straight line |
| **Rectangle** | U | an outline, or **Filled** |
| **Ellipse** | O | an outline, or **Filled** |
| **Pick a colour** | I | takes the colour under the cursor |
| **Select** | M | a rectangle; drag inside it to move its pixels |

- **Colour**: the version's palette (the editor draws only in its colours, so the sheet stays consistent), or a
  colour picker on a sheet without a palette.
- **Mirror across the middle** draws both halves at once.
- **Outline** adds a one-pixel outline round the drawing, in the current colour.
- **Selection**: **Flip ↔**, **Flip ↕**, **Turn 90°**, **Copy**, **Cut**, **Paste**, **Delete**, **Deselect**.
- **Undo** / **Redo** (**Ctrl+Z**, **Ctrl+Y**); one stroke is one step.
- Zoom with the view's buttons (2× to 24×) or the keys **1** to **5**, which pick the zoom levels in order (a large
  sheet has fewer, the canvas stays at most 4096 px). The grid shows from 8×.

### Frames and animations

The strip under the canvas shows every frame with its duration. A bar over frames marks an animation, its name on
the first frame, and on the last **↻** (it loops) or **⇥** (it plays once). Click a frame to draw on it; **,** and
**.** go to the previous and next frame.

**Frames and animations** in the panel:

- **Add a frame** (blank, after the shown one), **Duplicate**, **Delete**, **◀ Move**, **Move ▶**.
- **Frame N's duration (ms)**: how long it shows (press Enter or click elsewhere to apply).
- **Animations**: each has a name, its first and last frame, and **Loop** (off: it plays once, as an attack or a
  hit). **Add an animation**, **Remove**. Godot plays them by name.

In the view: **Play** plays the shown frame's animation at its real timings (a play-once animation stops on its last
frame), and **Onion skin** shows 1 to 3 frames each side faintly, red before and blue after.

### Saving

**Save as a new version** saves only what changed; the version you opened stays as it was. **Close** leaves without
saving (it asks first if something changed). A save the app refuses keeps your edits in the editor.

---

## 11. Checking and exporting 2D art

On a sheet, a tile or layers, **Finish** has a check and the export.

- **Check the sprites**: cell sizes, half-transparent pixels, the colours, single stray pixels, the sheet size and
  the animations.
- **Check the tile**: whether its edges meet where it repeats.
- **Check the layers**: at least two, and the farthest covers the whole picture.

Each failure says how to fix it.

| Destination | What you get |
|---|---|
| **Godot 2D** | sprites: the sheet, a SpriteFrames resource with one animation per clip (at its frame timings) and an AnimatedSprite2D scene standing on its feet; a tile: a TileSet; layers: a Parallax2D scene. A README lists the settings for crisp pixels |
| **Unreal (Paper2D)** | the sheet and a Paper2D sprite sheet with the pivot at the feet; in Unreal, import it, then **Create Flipbook** |

---

## 12. Editing in Blender

Effigy can open a version in **your own** Blender and take the edited mesh back as a new version. It works with the
Blender add-ons **Blender MCP Pro** or the free **blender-mcp**: open Blender, press **N**, and start the add-on's
server. SETUP.md, step 7, has the details. Or let Effigy do it: turn on **Start Blender when it is needed** in
Setup ([Start Blender](#start-blender)), and **Open … in Blender** starts your Blender and its add-on's server when
none answers.

On a model, **Tools → Edit in Blender**:

- the status, **Connected** or **Not connected** (with **Check again**),
- **Open … in Blender**: the version appears in Blender as its own collection,
- **Save Blender changes as version**: the edited mesh comes back as a new version; the old one stays.

The exports and rig checks use the app's own copy of Blender in the background; you never need to open it for
those. Nor for a move: to stand a model up, turn it, scale it or put it on the ground, use the **Transform** step
under [Advanced: run any step](#advanced-run-any-step).

---

## 13. Setup

### What this app can do

Each feature needs some files on your computer. A card per feature says what it does, how big it is and its status:

| Card | What it does |
|---|---|
| **Concept images** | four pictures from your description; also the 2D pictures |
| **3D model, Faithful (recommended)** | the 3D model from your picture |
| **3D model, Fast (alternative)** | the 3D model with less memory |
| **3D model from four views** | the Faithful model from a four-view turnaround (22 GB more) |
| **Face detail** | sharpens your picture before it is copied onto the face |
| **Texture detail** | redraws a texture's fine detail (2 GB more) |
| **Texture detail, full** | the full model for that: cleaner detail (6.7 GB more), for 24 GB cards |
| **Parts** | finds and names a model's pieces (0.6 GB) |
| **Standing pose** | puts a character picture into a standing A-pose before its 3D model is made |
| **Character skeleton** | finds a character's joints to fit its skeleton (0.1 GB) |
| **Learned skinning** | attaches the mesh to the skeleton with learned weights (optional) |
| **Ready-made animations** | idle, walk, run and dozens more for people and animals (CC0) |
| **Text to motion** | an animation from a sentence; needs a Hugging Face login with access to Meta Llama 3 |
| **Motion from video** | an animation from a video of a person (7 GB) |
| **2D styles** | pixel art, classic painting and children's drawing looks (0.5 GB) |
| **Parallax layers** | the depth model that splits a background into layers (5 GB) |

The status and its button:

- **Not installed** → **Install**. Installs wait their turn (**Queued**, with **Cancel**).
- **Installed** → **Check and repair** checks every file's fingerprint.
- **Needs repair** (a file missing or damaged, or its Python environment out of date after an update) →
  **Repair**.
- **Install failed** → **Try again**.
- **Needs a Hugging Face login first**: log in below, accept the licence, then **Install**.
- **Needs a bigger card**: your card cannot run it, so it is not offered.

### Hugging Face

The same login as in Get started: **Logged in as …** or **Not logged in**, the licences to accept with their state
(**accepted** / **not yet**), the **Hugging Face token** field, **Log in**, **Check again** and **Log out**.

### Where the models are

Shows the models folder. Pick another drive or folder and **Move the models there**: the app copies, checks and
only then deletes; generating waits until it is done.

### Space on disk

What the app keeps on your drives, by kind: **Model files**, **Python environments**, **Your library** and
**Tools**, with a bar per drive. Safe cleanups:

- **Remove** a feature's model files (or files two features share; the question names both) or a Python
  environment. Install it again on its card when you need it.
- **Empty the trash**: deletes Recently deleted for good.
- **Clear unused**: downloaded Python packages no environment uses.
- **Delete for good**: an archived asset.

### Tools

**Blender connection**: whether your Blender's add-on server answers, for [Edit in Blender](#12-editing-in-blender).

### Start Blender

**Start Blender when it is needed**: with it on, **Open … in Blender** (and an agent's `open_in_blender`) starts
your Blender with its add-on's server when none answers, and waits for it. The field below is your
`blender.exe`, the one with the add-on installed; while Blender is connected it is filled in for you. **Save**
keeps both. Effigy starts one Blender at a time: if yours is already open with its server off, press **Start
Server** in its N panel instead of letting a second one start.

### Connect an agent

Gives the setup text for Claude Code, Codex and other AI agents, with **Copy** and **Test the connection**. See
[section 15](#15-working-with-an-ai-agent).

### App address

The port the app runs on (8000 by default). Change it and **Save port**, then close the `run.bat` window and start
it again. Use this if another program needs 8000 (the Unreal editor's MCP plugin does).

### Credits for your game

The credit lines your game must carry, such as "Built with DINOv3". Exports write them into `CREDITS.txt` for you.

### Advanced

**Model files and licences**: every model file, the feature it serves, its folder, its size on disk and its
licence, and every licence the app uses (models, code and data) with what it allows. A reference: install on the
cards, remove under Space on disk.

---

## 14. Your graphics card and memory

### What your card gets

The app measures your card and gives each job a **budget**: what is free on the card now, minus a reserve for
Windows and the display. It picks settings that fit:

| Card | 3D model by default | Also |
|---|---|---|
| 12 GB | **Fast** | Faithful and Texture detail do not fit |
| 16 GB (the reference) | **Faithful, high quality** | Faithful, Fast; everything else |
| 24 GB and up | **Faithful HD** | **Texture detail, full**; the standing pose and the motion text model stay whole on the card, which is faster |

The numbers for 24 and 32 GB cards are first estimates. Measuring your card and sending the report helps: see
SETUP.md, "What's not ready yet". The 3D model steps need an **RTX 40 or 50 series** card; on an older card (an RTX
3090, say) they are refused, while pictures, pixel art and motion still run.

### When the app says no

Before a job starts, the app checks that it fits. It refuses rather than risk a frozen computer:

- *needs about N GB of video memory; this GPU has …*: the setting never fits this card. The message names a lighter
  one, such as **Fast**.
- *the GPU is busy: other programs use …*: close a game, a video or another AI app, and run it again.
- *needs about N GB of free memory (RAM)*: close other programs (a browser with many tabs is the usual one).
- *ran out of video memory*: the step itself needed more than it could have. Your previous version is safe; the
  activity bar shows **try {…}** with lighter settings.

The app runs one job at a time; the rest wait in the queue.

---

## 15. Working with an AI agent

Claude, Codex or another AI agent can use Effigy for you over MCP: it imports your files, runs the steps, looks at
the results as pictures, checks them and exports into your game project. Every step it runs appears in the app as
a new version, so you can watch, compare and undo. **It cannot delete anything.**

To connect one: **Setup → Connect an agent**, pick your agent, copy the text into its configuration and **Test the
connection**. Restart the agent after adding it. The app itself has to be running while the agent works.

Things to ask for: "Import this zip and get it ready for Godot", "Make a stone well from this description, 4,000
triangles, export it into my project's props folder", "Give the viking the Brawler move set and make pixel-art
sprites of it". The agent follows the same rules as you: one GPU job at a time, the same memory checks, restricted
versions cannot be exported.

---

## 16. Keyboard and mouse

| Where | Keys |
|---|---|
| 3D view | drag: orbit · scroll: zoom · right-drag: pan |
| Time bar (3D clips, 2D sheets, animation editor) | **Space** play/pause · **←** **→** a frame · **Home** **End** first/last frame |
| Animation editor | **I** key the bone · **X** / **Delete** delete keys · **Ctrl+Z** / **Ctrl+Shift+Z** undo/redo · **Esc** cancel a drag · **Ctrl+wheel** zoom the timeline |
| Skeleton editor | **G** move · **R** rotate · **Shift+click** add to the selection · **Esc** cancel a drag · **Ctrl+Z** / **Ctrl+Shift+Z** |
| Pixel editor | **B E G L U O I M** the tools · **Ctrl+Z** undo · **Ctrl+Y** / **Ctrl+Shift+Z** redo · **,** **.** previous/next frame · **1–5** the zoom levels in order |
| Parts | click: in the part · **Shift+click**: not the part |
| History, Licences | **Esc** closes them |

Keys do nothing while you type in a field.

---

## 17. When something goes wrong

| You see | What to do |
|---|---|
| **This run ran out of GPU memory** | Your previous version is safe. Use the lighter settings the activity bar suggests (**try {…}**), or close other programs using the card |
| **Another operation is using the GPU** | Wait for it, or cancel it in the activity bar |
| **the GPU is busy: other programs use …** | Close a game, a video or another AI app, then run it again |
| **needs about N GB of free memory (RAM)** | Close other programs, above all browsers with many tabs |
| **This model needs access approval first: see Setup** | Accept the model's licence on Hugging Face and log in (Setup, Hugging Face) |
| **… files weren't found** with **Set up …** | Install that feature's card in Setup; what you typed stays |
| **Needs repair** on a Setup card | **Repair**: it fetches what is missing or damaged |
| **not supported: the 3D models need an RTX 40 or 50 series card** | The 3D model steps cannot run on this card; 2D and motion still work |
| **… is still running on …: wait or cancel it first** | A job is working on that asset; let it finish or cancel it |
| **the app was restarted while it ran** | The job stopped with the app; run it again. Finished versions are in the library |
| A generated model with a whole area missing | Generate again with **Background: Grey** |
| A face that is blank or other clothes after a redraw | **Redraw again** (another seed) |
| A skin check fails | Follow its hint, usually **Edit skeleton**, then **Attach to skeleton** again |
| **No pixel grid was found** | Pixelize again with **A height for the subject** |
| The browser shows nothing | Is the `run.bat` window still open, without a red error? |

More, for installing and starting: SETUP.md, "Troubleshooting". When something fails twice, the activity bar's log
(click the job) says why; send it along with a bug report.

---

## 18. Where your files are

Everything stays inside the app folder (the models may be on another drive, see Setup):

| Folder | What |
|---|---|
| `library\<asset>\` | one asset; each version is a folder in it, `001_upload`, `002_prep`, `003_trellis2`… with its files and a `meta.json` saying how it was made |
| `library\<asset>\NNN_export\` | an export's files, also downloadable from the app |
| `library\_trash\` | Recently deleted |
| `models\` | the AI models (Setup, Where the models are) |

Your own copies of exports go where you save them (**Save all files to the folder**). Uninstalling the app is
deleting its folder.

---

## 19. Words used in the app

| Word | Meaning |
|---|---|
| **Asset** | one thing you make: a prop, a character, a background |
| **Version** | one state of an asset, made by one step from an earlier version; never overwritten |
| **Cut-out** | the reference picture with its background removed; what the 3D model is made from |
| **Turnaround** | one picture with several views of the subject side by side |
| **Preset** | a named set of settings for a step, such as **Faithful, high quality** |
| **Budget** | the triangles a model may have (Prepare), or the video memory a job may use (your card) |
| **Bake** | painting the textures onto the simplified model from the generated one |
| **UVs** | how the texture is laid onto the model |
| **LOD** | a lower-detail copy for when the model is far from the camera |
| **Collision hull** | a simple shape the game's physics uses instead of the model |
| **Rig, skeleton** | the bones that move a character |
| **Skinning, weights** | how much each bone moves each part of the mesh |
| **Clip** | one animation, such as a walk |
| **Move set** | a group of clips for a kind of game, such as a brawler |
| **Sprite sheet** | every frame of a 2D animation in one picture, with its data |
| **Animation (in a sheet)** | a named run of frames that loops or plays once |
| **Palette** | the fixed set of colours pixel art uses |
| **Seamless tile** | a texture that repeats without visible edges |
| **Parallax layers** | a background split far to near, scrolling at different speeds |
| **Restricted** | made with a component not cleared for commercial use; cannot be exported |
| **Starred** | a version you marked to find it again; batch exports prefer it |
