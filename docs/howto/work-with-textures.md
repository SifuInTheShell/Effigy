# Work with textures and reusable materials

Open a model and choose **Textures**, beside Prepare and Character. You can edit its materials before or after
rigging and animation. Each completed operation saves a new version. Select an earlier version in History to go
back or compare it with the current result.

For textures without a model, choose **New asset → Texture / Material**. Generate a colour texture from a
description, import an existing map set, or start with a solid colour. These assets have their own library filter
and never require a mesh.

The main Library offers **Models**, **2D art** and **Textures / Materials** alongside All assets. **New material**
opens standalone creation; **Import material** opens the material import page. Material cards have their own kind
badge, and batch export packages selected materials as texture sets.

Import separate PNG, JPEG, WebP, TIFF or TGA maps, or select a packed layout: glTF/Unreal ORM (R ambient occlusion,
G roughness, B metallic), Unity HDRP Mask (R metallic, G ambient occlusion, A smoothness), or Unity metallic/smoothness
(R metallic, A smoothness). Smoothness is inverted into roughness. Separate maps override corresponding packed
channels. Use the normal convention selector for DirectX maps. **From model GLB** copies a selected material slot
from a model exported with embedded textures. Native `.mat` and `.uasset` files need their image maps exported first.

The workspace's top tabs select **Edit maps**, **Upscale**, **Enhance**, **Create new** and **Library**; the right
panel follows that selection. The view buttons stay together, and the shorter preview keeps its lighting and
mapping controls above the activity display on laptop screens. All these workflows are available to MCP agents;
see [Textures and materials in the agent guide](../../mcp/SKILL.md#textures-and-materials).

## Inspect and edit

The map list identifies base colour, roughness, metallic, normal, ambient occlusion, height and emissive maps.
For a model with several materials, select the material first. The surface preview shows the actual model;
standalone materials can be previewed on a plane, sphere or cube with adjustable light and repeating mapping.
Use the map view to inspect individual images and overlay a model's UV layout.

**Edit maps** lets you replace maps, adjust colour or scalar values, paint directly on the image, and change
roughness, metallic and transparency settings. Painting offers round and square brushes, adjustable size,
opacity and hardness, an eraser, a colour picker, and undo/redo. Wheel to zoom; use Fit map or 100% to reset
the view. Expand painter gives the canvas more screen space; Restore painter or Escape returns to the workspace.
Space-drag, middle-drag, or the Hand tool pans the canvas. Shortcuts inside the painter:
B brush, E eraser, I colour picker, H hand, Alt-click pick colour, brackets change brush size,
Ctrl+Z undo, Ctrl+Shift+Z or Ctrl+Y redo, +/− zoom and 0 fit. Pen pressure controls opacity.
Painting and saving retain the original map resolution; Save painted map creates a new version.
The eraser removes alpha; the material's alpha mode controls whether that transparency appears on the model.
Undo stores touched map sections with a 64 MiB history target (the latest stroke is always retained).
PNG, JPEG, WebP and
TIFF imports are supported, up to 16 megapixels per image. Import DirectX normal maps with that convention selected;
Effigy converts them to OpenGL (+Y). Height PNGs retain 16-bit samples.

## Improve an older texture

Choose a colour or emissive map, then **Upscale**. Pick a larger width and run AI upscale. Existing normal,
roughness, metallic and AO maps keep their resolution. Use **Resize without AI** to resize data maps; normal
vectors are normalized after resampling. Outputs are limited to 16 megapixels, with a maximum selected width of 4K.

Standalone repeating textures use wrapped context. Model atlases process disconnected UV charts separately,
then fill the gutters, keeping unrelated surfaces out of each chart's AI input. Atlases with repeating UVs or
more than 4,096 charts need conversion or a different method. AI can invent detail: compare text, faces and seams
before accepting a result.

**Enhance** redraws colour detail using your description. Compatible single-material models also expose the
existing region-painting tools and face projection. Geometry, UVs, skin weights and animation remain those of the
selected model. Enhancement can change identity or colours, so use History to compare.

## Create a different appearance

For a model, **Create new** generates a reference picture, cuts it out, then generates a PBR texture set on the
existing UVs. This requires the concept-image and 3D-generation models. The original appearance stays in History.
Generative model editing currently requires one mesh and one standard PBR material with UV0; unsupported imports
show a capability explanation. Map replacement and export work with individual material slots.

For a standalone material, Create new generates a colour image: up to 1K for repeating textures or 2K for ordinary
images. **Estimate surface maps** derives height and normals from image brightness and adds constant roughness
and metallic maps where missing. These are artistic estimates, not recovered physical material properties.
Import authored PBR maps when those properties matter. **Blend repeating edges** is a deterministic edge blend;
inspect the repeated preview afterward.

## Reuse and export

**Library → Save set to Library** extracts the selected model material into a standalone asset. On another model,
choose a material asset and its exact version, then apply it. Atlas mapping requires a matching UV layout;
repeating mapping requires a material marked as repeating. Repeat count and rotation affect material mapping,
without rewriting the model's UVs. Applying a material copies its maps and credits into the new version, so later
source edits do not alter that model.

**Export texture set** creates a ZIP containing individual maps, material metadata and license credits. Height
remains a separate map because core glTF has no height slot. Model versions also retain their updated GLB for the
normal model export workflow and Blender.

When preparing a model again, the bake settings offer **Transfer current textures** and **Bake original generator
detail**. Automatic selection keeps later texture edits. Choosing the generator explicitly restores its earlier
appearance. Preparation can change topology; use the Textures workspace for edits that should retain a rig.
