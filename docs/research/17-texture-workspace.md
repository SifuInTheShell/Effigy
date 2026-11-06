# Research 17: a texture workspace and standalone materials

> Research and design proposal, 2026-11-06. Audience: the owner and the engineer planning the next texture work.
> Purpose: choose the workflow, reuse existing capabilities, and identify the experiments required before implementation.
> Evidence: current Effigy source, earlier research 04/11/14/15, and the primary sources linked below.
> No new texture implementation or GPU benchmarks were performed in this pass. Recommendations are proposals.

## 1. Recommendation

Add **Textures** beside the model's **Prepare** and **Character** tasks, and add **Texture / Material** as a standalone asset type in New asset and the Library.
Both entry points should use the same map editor and processing tools. A model is optional.

The owner's requirements are:

- Work with textures already on generated models.
- Create new textures and optionally apply them in place of the current set.
- Increase the fidelity of older textures, including AI upscaling.
- Work on texture images and material sets that have no model attached.
- Test changes locally before publishing. This work is on local branch `work/textures` in the existing checkout.

Use four distinct actions: **Edit maps**, **Upscale**, **Enhance details**, and **Create new**.
Keep creating a new appearance separate from improving resolution: users should know when AI can invent detail.
Every operation produces a version. **Use this result** makes it the current selection; the previous files remain in History.

Start with the models and processing utilities Effigy already ships. A new full-PBR material generator is a separate experiment, not a prerequisite for the workspace.

## 2. What Effigy already has

These are source findings, not promises about the proposed workspace.

| Capability | Current behavior | Reuse and limitation |
|---|---|---|
| Generate a replacement PBR set | TRELLIS.2's texture operation conditions on a mesh and reference image and bakes onto the existing UVs | Strong starting point for model retexturing; one mesh, existing UVs, image-conditioned rather than direct text input |
| Bake at another resolution | Samples the persisted generator volume, or imported materials when no generator volume exists | Useful when a better source survives; it cannot recover details absent from that source |
| Enhance model detail | Z-Image + Tile redraws rendered views and projects them back into the atlas | Existing 2K/4K path, coverage planning, colour matching, seam healing; changes appearance and needs comparison |
| Change a painted region | A frozen UV mask limits the final write in the refinement edit operation | Reuse for local changes; the UI's “Texture paint” is currently mask painting for AI edits, not a general colour brush |
| Project a reference face | Upscales the conditioning picture and projects its visible head/front region | Specialized option for compatible Faithful lineage, not a general material replacement engine |
| Upscale an image | Tiled Real-ESRGAN x4plus, plus the redraw step's image-upscale operation | Already available for standalone images; model atlases need UV-aware handling before reuse |
| Create a seamless image | The concepts tile operation generates an image, offsets it, inpaints the crossing edges, and reports seam quality | Existing standalone colour-texture foundation, 256–1024 px in multiples of 64; does not generate a complete PBR set |
| Inspect/export maps | Viewer channels and loose base-colour, ORM and normal outputs exist | Good foundation; needs an explicit map inventory, import/replace controls and standalone material export |

Important gaps found in the code:

1. Texture tools are mostly gated by step names such as bake/project/refine. A textured import, raw generated GLB or animated version may have usable textures but no matching panel. Availability should come from actual material and UV capabilities.
2. Retexture/refinement rebuild a **static GLB** through the bake writer. They do not preserve the full source scene, skins, animation, morph targets or arbitrary material setup. These paths must not be exposed on animated characters with a preservation promise.
3. The current bake prefers an ancestor generator volume. Re-baking after a projected face or texture edit can therefore restore older generator colours. Source selection needs to be explicit.
4. Refinement currently expects a square base-colour atlas. Imported materials can have rectangular images, multiple slots, tiled UVs or other texture-coordinate conventions.
5. Library kinds currently recognize `2d`, with absent kind meaning 3D. A standalone material should have a distinct kind and workflow; routing it through pixel-art conversion would be misleading.

Source anchors for planning: the TRELLIS.2 texture operation; bake source selection; refinement run/edit and parameter checks; the static bake writer; view texture writer; SR and redraw utilities; concepts tile operation; workflow task mapping and Library kind validation. Recheck these when implementation starts.

## 3. Two entry points, one texture workspace

### A. Textures on a model

Open a model version → Textures → choose a material/map or the whole supported set → edit or generate → compare → use the result → continue Character or Finish.

Show the model with a synchronized before/after camera, a 2D map view with optional UV overlay, material selection, map roles, dimensions and missing maps.
Keep the original rig/animation preview available. Compare under identical neutral lighting and in unlit base-colour mode so lighting does not masquerade as an improvement.

“Create new” offers a reference image or description. The initial text path can create a reference image and feed the existing mesh-conditioned texturer.
This is an image-mediated route; it needs evaluation for back/side consistency and prompt adherence before becoming a default.

### B. Standalone texture or material

New asset → Texture / Material → import images or describe a texture → edit/create → upscale or enhance → inspect repetition and material response → export or apply to a model later.

Allow both a **single texture image** and a **material set** containing several named maps. Neither requires a mesh.
Examples: seamless stone, wood, leather or fabric; a decal with alpha; an old diffuse texture to restore; an imported albedo/roughness/normal set.
Non-tileable images and decals must remain valid: seamless tiling is a choice, not a requirement.

Preview on a plane, sphere or cube, with adjustable repetition, UV rotation, roughness/metallic factors and a moving light. These are preview fixtures, not mandatory model assets.
Use a 2×2/3×3 tiled preview and offset view to reveal borders and repeated motifs. Recheck tiling after every upscale or redraw.
Do not require pixelization; preserve original resolution and bit depth where supported. Use nearest-neighbour scaling for deliberately pixel-art textures.

The first standalone version can be colour-only with editable constant roughness/metallic values. Label that accurately; it is not an AI-generated full PBR set.
Importing real map sets should be possible independently of AI map generation.

### Connecting the two

**Save texture set to Library** extracts a model's maps into a standalone asset. An extracted UV atlas still follows that model's UV layout; it is not automatically a reusable seamless material.

**Apply to model** has two different meanings:

- Replace an existing atlas map: same UV layout and compatible map semantics.
- Apply a reusable tileable material to selected material slots: explicit scale, rotation and mapping settings. Baking that result into the model's atlas is a separate operation when needed.

Pin the material **version** when applying it. Editing the standalone asset later must not silently change every model that used it.
An explicit “Update from material” action can create a fresh model version using a newer set.

## 4. Blender and related references

| Reference | Verified behavior | Implication for Effigy |
|---|---|---|
| Blender Texture Paint | Edits images through the 3D viewport or Image Editor; supports external editing and reloading | Pair surface and map views; make texture round-trips possible without regenerating the mesh. [Manual](https://docs.blender.org/manual/id/5.0/sculpt_paint/texture_paint/introduction.html) |
| Blender paint options | Bleed extends paint outside islands; occlusion and backface controls restrict projected painting; Quick Edit reprojects an external edit | Surface visibility and seam padding belong in the processing contract. [Manual](https://docs.blender.org/UATEST/manual/en/4.5/sculpt_paint/texture_paint/tool_settings/options.html) |
| Blender baking | Bake margins can extend edge pixels or use adjacent faces across UV seams | Evaluate seams at filtered/mipmapped viewing distances, not only on the flat atlas. [Manual](https://docs.blender.org/manual/en/4.4/render/cycles/baking.html) |
| StableGen | Separate generation/restyle/local-edit modes; masked edits and PBR extraction | Reference for the interaction and projection strategy. Its masked-edit source freezes the UV selection and uses reversible layers. [Modes](https://github.com/sakalond/StableGen/wiki/Generation-modes), [masked-edit implementation](https://github.com/sakalond/StableGen/blob/main/stablegen/texturing/masked_edit.py) |
| Dream Textures | Supports independent texture generation, seamless images and image upscaling; its upscale operator creates another image and can rebind the selected texture node | Useful model for “create another result, then use it” rather than destructive replacement. [README](https://github.com/carson-katri/dream-textures), [upscale implementation](https://github.com/carson-katri/dream-textures/blob/main/operators/upscale.py) |
| Material Maker | Authors standalone procedural PBR channels and also supports painting them onto models | Confirms that standalone material authoring and model painting can share concepts without requiring a mesh. Use as a workflow reference; a full node editor is unnecessary for this first version. [Documentation](https://rodzill4.github.io/material-maker/doc/intro.html) |

StableGen's PBR documentation also separates map extraction from baking and records per-map settings. It notes extraction failure cases, including colour loss and normal artifacts. Effigy should retain map provenance and avoid presenting estimated maps as recovered physical truth. [PBR documentation](https://github.com/sakalond/StableGen/wiki/PBR-maps)

Effigy has an MIT application and a GPL Blender-script boundary. Treat GPL add-ons as behavior/method references for application work; any actual code reuse needs to respect that established boundary.

## 5. Upscaling strategy

### Separate resolution from redesign

| Action | Meaning in the UI | Suggested initial route |
|---|---|---|
| Resize | More/fewer pixels with conventional filtering; no learned detail | Explicit non-AI baseline |
| Upscale | Restore/sharpen existing appearance using an SR model; inferred detail may differ | Existing Real-ESRGAN, target 2K or 4K; preserve design by default and provide comparison |
| Enhance details | AI redraws material details such as fabric or grain | Existing Tile refinement for models, image redraw for standalone colour images |
| Create new | Generate another appearance | Mesh-conditioned TRELLIS.2 for model sets; existing image/tile generation for standalone colour; dedicated PBR model only after a spike |

Real-ESRGAN supports tiled processing and arbitrary final output size; its x4 route resizes the learned output for other scales. This does not mean every requested scale has a separately trained model. Effigy already implements tiled x4 inference, so no new dependency is needed for the first experiment. [Upstream](https://github.com/xinntao/Real-ESRGAN)

SwinIR is worth a controlled comparison as a second candidate: it offers classical and real-world SR variants. Its published image benchmarks do not establish superiority on Effigy textures. Do not add a large model picker before comparing concrete pinned checkpoints. [Official repository](https://github.com/JingyunLiang/SwinIR)

### Treat texture layout explicitly

For standalone tileable images, use wrapped padding/context so inference sees the opposite border, then crop and validate the repeated result. Ordinary image tiling only reduces memory; it does not guarantee repeatable output.

For mesh atlases, unrelated UV islands can touch in image space. Feeding the entire atlas to a photo upscaler can mix colours and invent bridges between them. Benchmark:

1. Per-island processing with adequate padding and consistent placement at the target resolution.
2. Unlit surface-view upscaling followed by visibility-tested projection, with a conservative fallback on unseen texels.

The first is more directly tied to existing image content, but chart edges need continuity handling. The second provides coherent image context but risks view-dependent detail and resampling loss.
Neither is established as the winner by this research pass. Compare them on the same assets before selecting a default.
Use mipmap/oblique-view checks after gutter rebuilding; padding alone cannot repair disagreeing island-edge colours.

### Treat map roles explicitly

Apply photographic RGB SR initially to **base colour** only. Keep alpha separate, with appropriate cutout/coverage handling.
Preserve roughness, metallic, AO and normal maps by default. They can retain their original dimensions; sharing a material does not require identical map sizes.

If data maps are resized, use map-specific processing: scalar maps as linear data, normal vectors decoded/resampled/normalized/re-encoded, masks without invented RGB content. Preserve high-bit-depth height data when supported.
Do not feed packed ORM or normals through a colour SR model. Base colour uses sRGB; normal/occlusion data have different semantics. [glTF material specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#materials), [Blender image settings](https://docs.blender.org/manual/id/4.5/editors/image/image_settings.html)

“Enhance colour detail” must not imply improved roughness or geometric normals. Conversely, changing cloth into metal needs material-map changes as well as a new colour image.

## 6. New material generation candidates

| Route | Fit | Decision |
|---|---|---|
| Existing concepts image/tile + existing upscaler | Standalone images, seamless colour textures, decals | First release; already installed and integrated. No claim of full-PBR synthesis |
| Existing TRELLIS.2 texture pipeline | New PBR appearance for a known model from a reference image | First model retexture route after preservation work. Upstream separates texturing from geometry generation. [TRELLIS.2](https://github.com/microsoft/TRELLIS.2) |
| StableMaterials | Text/image-conditioned standalone base colour, normal, height, roughness and metallic; built-in tileability | Best targeted standalone-PBR spike from this shortlist. Published base output is 512²; card still lists refiner/inpainting as forthcoming. Test exact dependency compatibility, memory, map coherence and terms before adoption. [Author's model card](https://huggingface.co/gvecchio/StableMaterials) |
| MatFuse | Multiple conditioning sources and map-level material editing | Research alternative. Documented diffuse/normal/roughness/**specular** output requires conversion decisions for Effigy's metallic/roughness contract; Python 3.10/torch 1.13.1 setup needs isolation. [Official repository](https://github.com/giuvecchio/matfuse) |
| Hunyuan3D-Paint 2.1 | Dedicated mesh-conditioned PBR synthesis | Exclude from the default path: upstream lists 21 GB for texturing, and the model license excludes EU/UK/South Korea. This does not fit the project's 16 GB default and existing distribution policy. [Upstream requirements](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1), [model terms](https://huggingface.co/tencent/Hunyuan3D-2.1/blob/main/LICENSE) |

StableMaterials is not automatically cleared because its card says OpenRAIL: the exact pinned terms and custom pipeline must be checked against the project's accepted licenses. This pass establishes candidate fit, not adoption approval or a measured 16 GB result.
Generating material maps from a photograph is also estimation, not exact recovery of its original lighting-free reflectance.

## 7. Preservation and version contract

Implement a texture-editing layer that copies the source GLB and changes selected image/material references. Append image data and buffer views as needed instead of reconstructing the whole scene from a static mesh.
The existing GLB chunk reader/writer is a starting point, not a complete material replacement API.

For a texture-only operation, preserve geometry, UVs, normals/tangents, nodes/transforms, skins/weights, morph targets, animation accessors, material assignments and supported extensions. Preserve alpha mode/cutoff, double-sided flags, sampler state and texture-coordinate selection.
If one image is shared by several material slots, distinguish “change this slot” from “change every use” before rebinding it.
Unsupported compressed textures, extension-driven materials or coordinate transforms need an explicit capability result; never silently flatten or drop them.

For the initial release, support Effigy's generated single-atlas models thoroughly. Expose inspection/export more broadly while reporting why an unsupported model cannot use a particular edit.
Allow applying colour replacements after rigging only once preservation tests pass. Do not make users rerig for a texture-only change.

Proposed material metadata: map role/path, dimensions, bit depth, colour interpretation, normal convention, tileability, physical tile size when supplied, provenance and selected source asset/version.
Model-atlas masks additionally require mesh/UV identity. Reject stale masks after unwrap/topology changes; do not silently reuse them.
Store pinned source versions and outputs so deleting/moving a source material cannot break an already finished model version.

Use distinct bake choices: **Bake original generator detail** versus **Transfer current textures**. The latter must not fall back to an older volume and erase an edit.

## 8. Suggested implementation order

1. **Inventory and preservation foundation.** Inspect actual maps/UVs/materials, patch GLB images without scene reconstruction, define versioned texture-set metadata and source selection. Prove rig and animation retention.
2. **Useful workspace for both entry points.** Model Textures task plus standalone Texture / Material assets, map import/export/replace, plane/sphere/cube and tiled previews, synchronized comparison and explicit application. Reuse existing standalone image/tile generation without requiring pixel art.
3. **Upscale older textures.** Standalone ordinary and wrapped-image SR; benchmark chart versus surface-view handling for model atlases; support 2K/4K targets, cancellation and accurate memory/storage estimates. Preserve data maps by default.
4. **Consolidate generative editing.** Move existing refine, region edits and compatible face projection into Textures; expose image-conditioned full retexture with clear appearance changes and map selection.
5. **Standalone full-PBR spike.** Compare StableMaterials with the colour-only baseline and imported authored material sets. Adopt only if it improves usable results within the target machine's constraints. Keep this independent of the basic workspace.

Implementation followed in November 2026: the Textures task and standalone material kind now provide the common
workspace, versioned map operations, PBR previews, chart-isolated/wrapped AI upscaling, existing generative model
editing and portable texture sets. See [the texture guide](../howto/work-with-textures.md).
Standalone learned full-PBR generation remains a separate candidate evaluation; current standalone generation
creates colour, imports accept authored PBR sets, and brightness-derived surface estimates are explicitly labelled.

## 9. Evaluation needed before shipping

Use an old blurry atlas, a stylized character/face, fine lettering or a logo, wood/fabric/metal surfaces, a multi-material import, a transparent decal, a skinned animated character, and standalone tileable sets with normal/roughness maps.

Check:

- Same model before/after: geometry/UV/rig/animation invariants remain unchanged; verify playback and export in addition to matching counts.
- Masked edits change nothing outside the allowed region at unchanged resolution.
- Upscaling retains recognizable identity, colour and text; compare to conventional resizing at matched output sizes and intended viewing distances.
- No new UV seams, patch boundaries, repeating-border defects or cutout halos. Inspect multiple mip levels and oblique views.
- Normal maps still produce correct lighting; data channels remain valid; colour-only edits do not silently replace them.
- Applying a standalone material creates a new model version and does not change other models or the source asset.
- Transfer-current-textures retains face/detail edits; explicitly baking generator detail uses the intended older source.
- Record warm/cold runtime, peak allocated/reserved VRAM, host RAM and output storage at 2K/4K on the actual reference machine. Do not extrapolate an image benchmark to whole-model processing.
- Cancellation and failure retain the prior selected version and do not leave partial outputs presented as finished.

Unresolved by this pass: best chart/view upscale method, perceptual quality on the owner's older assets, standalone full-PBR quality under 16 GB, and the first-release level of support for imported multi-material scenes. These require targeted spikes, not more general model surveying.

Related project research: [04: texturing](04-texturing.md), [11: retexturing alternatives](11-retexturing-alternatives.md), [14: professional textures](14-professional-textures.md), [15: 2D studio](15-2d-studio.md).
