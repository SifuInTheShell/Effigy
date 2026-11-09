# Changelog

What changed for people who use Effigy, newest first. Each release's download and install notes are on the
[releases page](https://github.com/SifuInTheShell/Effigy/releases); what was built and measured, milestone by
milestone, is in [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md) §4.

## Unreleased (on `main` since 0.2.0)

Built in a cloud session without a GPU or Blender. The unit and interface tests pass. None of it has run on a GPU
or in Blender yet: the checks for that are in [docs/LAPTOP.md](docs/LAPTOP.md).

### Interface

- **A new workspace layout** (M71). Three thin rows above the view, no taller than the old two: the app bar (with the
  Blender group), the step tabs (with the asset's name) and a **tool strip** with the tools of the chosen
  tab. The panel on the right shows the one tool you chose, with a line on what it does. A tool that does not fit
  the shown version is greyed out and says why; the next one is marked with a dot. The **Tools** menu and the
  folded panel sections are gone. Clean, Simplify, Unwrap and Bake are tools of their own.
- **Blender on every asset page**, 3D or 2D: a status dot, **Open 004** and **Save back** in
  the top bar, and a **Blender** button on each History row.
- **More settings** replaces **Advanced** in the panels that had it.
- **Run any step** is a developer option: turn on **Developer options** in Setup to show it.
- The activity bar is one slim line.

### Animation

- **Everyone in a video** (M64). A motion made from a video with more than one person says how many it found
  ("2 people at the start of the video; this is person 1"). **Make the other one's motion too** makes the others,
  each its own motion.
- **Hands on the floor** (M64). **Find hand contacts** in the animation editor holds a palm where it rests on the
  floor (a push-up, a crawl), the arm bending to keep it there, the way a foot lock holds a foot.
- **A panning camera** (M64). **Camera: panning** for a video filmed by turning on the spot to follow the person:
  the walker crosses the room instead of walking in place. A camera that also walks (a dolly) is not covered.
- **Connect two clips** (M65). On an animated person, Kimodo makes the motion from the last pose of one clip into
  the first pose of another, with a turn and a distance if you want them, or continues a clip, or leads into one.
  **Put this motion on the model with its clips** adds it next to the clips it connects; in a game it plays
  between the two.
- **Arms kept out of the body** (M67). After an animation is put on a person, the arms open just enough wherever a
  clip would swing the forearms or hands through the body (stout and stylised characters). On by default; untick
  **Keep the arms out of the body** in the Animate panel to keep the clips as they come. The animated model's page
  names the clips that were opened and by how much.
- **Duplicate mirrored** (M69): a copy of a clip with left and right swapped, a left-handed swing from a right-handed
  one.
- **Butterworth smoothing** (M69): beside the Gaussian; at the same setting it keeps quick moves larger and a clip's
  ends straight.
- **Move the hips so a locked leg never goes straight** (M69): with foot locks, the hips follow a planted foot just
  enough to keep the knee bent.

### Textures

- **Texture detail sees more** (M66). Up to three more views where the usual ones see the surface poorly (the top
  of the shoulders, a ledge under a post, hollows), never from above onto the flat top of a wide prop (that
  invented tiles before). No colour is taken from right next to an inner edge any more (an arm in front of the
  body), which removes coloured halos. A run takes about 4 to 6 minutes.
- **Paint a part and redraw it** (M68). In **Texture detail**, **Paint the part to change** on the model (Shift
  erases), say what it should become under **It becomes**, and click **Redraw the painted part**. It is redrawn
  from every side that sees it, and nothing outside the paint changes.

### Rigging

- **Better fingers** (M70). Fitting a skeleton now renders each hand close up from eight sides and reads its
  finger joints with Google's MediaPipe hand model, where before they came from one full-body picture and were
  often off. Needs the model download in Setup (the pose models).

### For agents

- New step parameters, all described in [mcp/SKILL.md](mcp/SKILL.md):
  - `mocap`: `person` and `camera`;
  - `motion`: `from`, `to`, `seconds`, `travel` and `turn`, to connect clips;
  - `anim`: `clearance`;
  - `refine`: `coverage`, and `op: "edit"` with `region`, `prompt` and `strength`.

## 0.2.0 (2026-10-20)

- **Upscale or redraw a 2D picture at any size** (M61), keeping parts of it unchanged with a mask.
- **Bring your own model** (M62): import a textured model, bake its own textures, then rig and animate it like a
  generated one.
- **IK in the animation editor** (M63): the armature drawn the way Blender draws it, with IK targets and poles to
  drag and pin, saved with the clip.
- **AMD GPU support, the Fast engine only** (contributed by @LukeLikesGit in #9): an AMD Radeon RX 9070 / 9070 XT
  on Windows; `run.bat` recognises the card and installs the AMD (ROCm) environment.
- `run.bat` writes `run.log` next to itself: which GPU it found and, on AMD, the install's output.
- Refreshed README, SETUP, MANUAL and the agent guide.

## 0.1.0 (2026-10-14)

The first public release: a local game-asset pipeline on your own NVIDIA GPU, nothing sent to the cloud.

- 3D from a description or a picture: concepts checked for 3D, an A-pose redraw for characters, a textured model.
- Game-ready in one click: cleaned, simplified, UV-unwrapped, PBR-baked and checked.
- Auto-rig: the skeleton fitted and the skin weights painted.
- Animation: 178 ready-made moves, motion from a sentence or from a video, and an animation editor.
- 2D: sprite sheets from your 3D moves, pixel art with a fixed palette, a pixel editor.
- Export to Godot, Unreal, Unity, the web and 3D printing, each export with its credits and licences.
- Commercial-clean: every model and tool allows commercial use of what it makes.
