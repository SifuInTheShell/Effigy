# Changelog

What changed for people who use Effigy, newest first. Each release's download and install notes are on the
[releases page](https://github.com/SifuInTheShell/Effigy/releases); what was built and measured, milestone by
milestone, is in [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md) §4.

## Unreleased (on `main` since 0.2.0)

M64 to M68 were built in a cloud session without a GPU or Blender; the unit and interface tests pass. On the
reference laptop (RTX 5080, 2026-10-30) so far: connecting clips (M65), the extra detail views (M66) and the arm
clearance (M67) ran on one character. The video checks (M64), painting a part (M68) and the checks in a game
engine have not run yet: [docs/LAPTOP.md](docs/LAPTOP.md) lists what is left.

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

### Textures

- **Texture detail sees more** (M66). Up to three more views where the usual ones see the surface poorly (the top
  of the shoulders, a ledge under a post, hollows), never from above onto the flat top of a wide prop (that
  invented tiles before). No colour is taken from right next to an inner edge any more (an arm in front of the
  body), which removes coloured halos. A run takes about 4 to 6 minutes.
- **Paint a part and redraw it** (M68). In **Texture detail**, **Paint the part to change** on the model, say what
  it should become under **It becomes**, and click **Redraw the painted part**. It is redrawn from every side that
  sees it, and nothing outside the paint changes. **Erase** (or Shift while painting) wipes a stroke that went too
  far; **Clear the paint** starts again; the paint has its own **Strength**.

### Interface

- **Each tool its own block.** Under **Tools**, Face from image, Texture detail, Parts and Sprites are set apart by
  a line, and Texture detail is split into its three tools, each with its own heading: **Refine texture**, **Baked
  shadows** and **Texture paint**.
- **No wrapped rows.** A slider shows its value on the same line; a tool's buttons fill the panel's width, and two
  side by side split it in half (Erase | Clear the paint, Region | Strength in Face from image).

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
