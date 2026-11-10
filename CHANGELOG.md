# Changelog

What changed for people who use Effigy, newest first. Each release's download and install notes are on the
[releases page](https://github.com/SifuInTheShell/Effigy/releases); what was built and measured, milestone by
milestone, is in [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md) §4.

## 0.4.0 (2026-11-06)

### Texture workspace

- **A dedicated Textures task** alongside model and character editing. Inspect each material and map, compare
  versions, overlay UVs, and preview a model, flat map, repeated tile or lit material surface. Edit maps, Upscale,
  Enhance, Create new and Library are top-level workflow tabs.
- **Standalone textures and materials**, with no model required. Start from a colour, generate an image or a
  seamless tile, import maps, or extract a material from a model. The main Library filters models, 2D art and
  textures/materials, with direct material creation, import and batch export.
- **Paint texture maps** with round and square brushes, size, opacity and hardness controls, eraser, colour picker,
  pen pressure, undo/redo and keyboard shortcuts. Zoom with the wheel or buttons, pan with the Hand tool,
  Space-drag or middle-drag, and expand the painter for more room. Save keeps the original resolution and creates
  a new version; failed saves retain the painting for retry.
- **Improve older textures** with AI upscaling, resizing, colour adjustments, detail enhancement and retexturing.
  Atlas upscaling isolates UV islands, while tileable maps use wrapped context. Data maps stay separate from colour
  upscaling; normal-map resizing normalizes vectors and 16-bit height images retain their precision.
- **Reusable material sets** pinned to a source version. Apply a library material to a model using its UV atlas or
  repeating coordinates. Texture changes preserve the original model's geometry, skeleton and animation.
- **Import separate or packed maps**: PNG, JPEG, WebP, TIFF and TGA; glTF/Unreal ORM, Unity HDRP Mask and Unity
  metallic/smoothness packing; OpenGL or DirectX normals. Import a named material from a GLB, including materials
  defined only by colour factors. Native Unity `.mat` and Unreal `.uasset` files need exported maps or a GLB.
- **Export texture packages** with maps, material metadata, credits and licences. Brightness-derived surface maps
  are explicitly labelled estimates. Painting normal maps and 16-bit height maps uses external image replacement
  rather than the browser's 8-bit colour painter.

### Setup and reliability

- Installed feature tiles say **Ready to use**. **Verify installation** explicitly checks local files; complete
  files on an external model drive no longer invoke the downloader just because Hugging Face metadata is absent.
  Shared model groups update together after verification.
- Z-Image-Turbo quantizes its transformer before loading the text encoder, reducing peak host-memory pressure.
  First loads from external drives can still take several minutes; later image generations reuse the resident worker.
- Animation-only GLBs are skipped when rendering model thumbnails, avoiding Blender's "no mesh" startup errors.
- Re-baking automatically retains newer texture edits; an explicit source selector can use the original generated
  volume. Texture controls and previews fit above the activity display, with an expanded painting view available.

### For agents

- MCP tools `inspect_textures`, `texture_map`, `edit_texture`, `extract_material` and `import_texture_set` expose
  texture inspection, import, generation, editing, enhancement, upscaling and material reuse. `list_assets` filters
  by asset kind; texture exports use `profile="textures"`. Image replacement supports externally painted maps.
- All texture operations use versioned jobs and carry source licence/restriction metadata. See
  [the texture guide](docs/howto/work-with-textures.md) and [MCP guide](mcp/SKILL.md).

Live checks on the reference NVIDIA laptop covered standalone and animated-model upscaling, seamless generation
from the external model drive, and preservation of animated geometry in Blender. Generative enhancement and
retexturing reuse the existing AI steps; their new wrappers have regression tests, with broader visual assessment
still ongoing. No new model dependencies were added.

## 0.3.0 (2026-11-05)

M64 to M68 were built in a cloud session without a GPU or Blender; the unit and interface tests pass. On the
reference laptop (RTX 5080, 2026-10-30) so far: connecting clips (M65), the extra detail views (M66) and the arm
clearance (M67) ran on one character. The video checks (M64), painting a part (M68) and the checks in a game
engine have not run yet: [docs/LAPTOP.md](docs/LAPTOP.md) lists what is left.

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
- **Paint a part and redraw it** (M68). In **Texture detail**, **Paint the part to change** on the model, say what
  it should become under **It becomes**, and click **Redraw the painted part**. It is redrawn from every side that
  sees it, and nothing outside the paint changes. **Erase** (or Shift while painting) wipes a stroke that went too
  far; **Clear the paint** starts again; the paint has its own **Strength**.
- **Cleaner normal maps** (this and the three below contributed by @rndmbt in #17). The baked normal map no longer shows facets and breaks at every UV seam: the model now
  carries the tangent frame its map was baked in, so every viewer and engine reads it the same way. The even grain
  all over it is gone too: the generated surface's triangles face random ways, which made the normals it was baked
  from random; they are now read whatever way a triangle faces. Thin parts read their own side, small loose bits
  of the generated surface are skipped, and its faceting is smoothed while creases stay (`hi_denoise`, default 6;
  0 turns it off). Optional: `normal_smooth` (texels, default 0) softens the normals further, without opening the
  seams again.
- **Smooth shading everywhere.** Cleaned, remeshed and unwrapped models were shown with flat facets (their files
  had no normals, or one per quad), and the textured model had a shading crease along every UV seam. Every step
  now writes smooth normals that agree across seams.
- **Less grainy ambient occlusion.** The AO is traced with 256 rays a texel instead of 32 and smoothed over the
  surface (`ao_rays`, `ao_smooth`, default 2 texels; 0 turns it off): 1.3 s instead of 0.4 s at 1024².
- **Rebuild a model as one solid (optional).** Clean leaves a generated model as a hollow double skin (two surfaces
  a tenth of a millimetre apart, joined at its openings), which remeshing and baking then struggle with. Tick
  **Rebuild it as one solid** in Clean (`cleanup`, off by default; up to a minute more) and it comes back as one
  closed solid: cracks closed, the inside filled, the surface smoothed where it is noisy and kept where it is
  creased. The bake then reads its normals from that surface. It suits simple props: fine detail goes (a character's
  face melts) and only the largest part stays (`metrics.cleanup.parts_dropped`).

### Interface

- **Each tool its own block.** Under **Tools**, Face from image, Texture detail, Parts and Sprites are set apart by
  a line, and Texture detail is split into its three tools, each with its own heading: **Refine texture**, **Baked
  shadows** and **Texture paint**.
- **No wrapped rows.** A slider shows its value on the same line; a tool's buttons fill the panel's width, and two
  side by side split it in half (Erase | Clear the paint, Region | Strength in Face from image).

### Install

- **The zip's first run no longer needs Git.** On a computer without it, `run.bat` stopped at the skinning environment. It and most optional features now download pinned archives instead. Still needed: Git for Parallax layers, CMake and a C++ compiler for text to motion ([SETUP.md](SETUP.md), Troubleshooting).

### Rigging

- **Better fingers** (M70). Fitting a skeleton now renders each hand close up from eight sides and reads its
  finger joints with Google's MediaPipe hand model, where before they came from one full-body picture and were
  often off. Needs the model download in Setup (the pose models).

### For agents

- New step parameters, all described in [mcp/SKILL.md](mcp/SKILL.md):
  - `mocap`: `person` and `camera`;
  - `motion`: `from`, `to`, `seconds`, `travel` and `turn`, to connect clips;
  - `anim`: `clearance`;
  - `refine`: `coverage`, and `op: "edit"` with `region`, `prompt` and `strength`;
  - `clean`: `cleanup`;
  - `bake`: `hi_denoise`, `normal_smooth`, `ao_rays` and `ao_smooth`.

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
