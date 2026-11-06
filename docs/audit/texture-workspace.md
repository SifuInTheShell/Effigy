# Texture workspace implementation review

Implementation branch: `work/textures`. November 2026. Prepared for the 0.4.0 public release on 2026-11-06.

## Boundaries reviewed

The browser sends material indices and version references to the local-only API. Image/UV inspection is read-only;
CPU operations, GPU operations and exports use the existing job/version system. Extraction creates a new asset.
GLB replacement appends images and rebinds one material, retaining the original mesh and animation buffer prefix.
Immutable version copies own their maps; they do not depend on a mutable source material asset.

Two independent read-only security passes covered source paths/provenance and processing resources. Findings fixed:

- Secondary map imports now carry every source image's license identifiers and restriction flag. Explicit image
  references in generative operations also carry restrictions. The normal step protocol preserves that flag.
- Aspect-derived output dimensions are checked against the 16-megapixel limit before image allocation, including
  extreme input aspect ratios. Source image dimensions are checked before decoding.
- UV overlay rejects more than 100,000 triangles before converting triangle arrays to Python lists. Atlas SR rejects
  more than 4,096 charts before rasterization and neural processing.
- Imported GLB material numeric settings must be finite numbers with valid component counts. Invalid settings
  return an API error, and the UI additionally avoids formatting non-finite numeric values.
- Version/file references reject traversal; file reads resolve within their version. Failed extraction cleanup
  verifies the resolved new asset lies inside the library before removing it.

No new remote image URLs, public endpoints, shell execution inputs or model dependencies were introduced.
Existing local-only request checks, GPU scheduling, cancellation and export restriction checks remain in use.

## Validation evidence

- Focused texture regressions cover per-slot isolation, map channels, 16-bit height, transparency, DirectX normals,
  normalized normal resampling, pinned copies, credits/restrictions, safe paths, resource limits and malformed input.
- AI wrapper tests verify enhancement, retexturing and face projection patch the original animated GLB instead of
  returning the intermediate static mesh. Normal maps remain intact; retexturing updates roughness and metallic.
- Bake regression checks automatic/current source selection retains texture edits, while an explicit generator
  selection uses the older volume.
- Real browser flows create, paint, resize, estimate, export, apply to an animated GLB and extract a material.
  The full browser suite passed all 99 tests. The backend suite passed 685 tests with 12 skips; subsequent focused
  runs covered the final loader and texture changes. Existing backend teardown/thread warnings remain in that suite.
- Live Real-ESRGAN: standalone 256 → 1024 in 12.34 s; animated model atlas 2048 → 4096 across 2,883 UV charts in
  105.92 s. Both measured 0.719 GiB peak allocated / 0.969 GiB peak reserved GPU memory. These are observed runs,
  not promised timings; model loading and external-drive throughput vary.
- Live seamless colour generation from the F: cache completed at 512² in 378.74 s including loading,
  with 7.692 GiB peak allocated / 7.750 GiB peak reserved and seam ratio 0.739. The loader now quantizes the
  transformer before loading the text encoder and reads transformer shards without mmap; this avoids holding
  both full models during loading. Browser generation reuses the existing resident image worker between requests.
- The real animated model retained identical meshes/accessors/nodes/skins/animations/scenes and its original
  binary prefix. Blender imported both GLBs; its evaluated 29,550 character vertices were identical at frames 1
  and 20 (maximum difference 0.0), while the animation moved vertices by up to 0.041735 m between those frames.

Standalone generation creates colour images. Brightness-derived surface maps are explicitly labelled estimates.
Dedicated learned standalone full-PBR synthesis remains outside the adopted first-release model set, as specified
in research 17. The owner's visual acceptance, especially identity, lettering and seams on older textures, remains
the next validation step.

## Texture workspace follow-up

The preview view controls remain on one row, the paint save action uses the primary accent, and Back to model is
prominent. Editing workflows are keyboard-operable top-level tabs. The shorter preview was visually inspected
and tested at 1280×720 and 900×720, including the Surface/Repeat/Rotation/Moving light controls above activity.
The full browser suite passed 101 tests; focused tests cover the final GLB material picker.

The Library now exposes model/2D/material categories, material badges, direct creation/import and texture-set
batch exports. Imports support separate images (including TGA), glTF/Unreal ORM, Unity HDRP Mask and Unity
metallic/smoothness packing, with explicit normal conventions. GLB imports show material names and copy only
the selected material into a standalone version; constant-only materials retain their material factors.

MCP exposes inspect_textures, texture_map, edit_texture, extract_material and import_texture_set, with asset-kind
filters and export profile textures. The SDK client exercised real CPU import/inspection/map-preview/resize/
extract/apply/export jobs and GLB material import. A conformance test checks that every texture operation is
exposed; the agent guide and step documentation describe all workflows. No additional public endpoints or
native engine container parsing were added. Engine .mat/.uasset inputs require exported images or GLB.

## Map painter

Round and square brushes, opacity, hardness, pen pressure, eraser, colour picker, zoom/pan, expanded view and
keyboard shortcuts operate on native image pixels. History stores touched 128-pixel sections with a 64 MiB target;
the latest complete stroke is retained even if larger. Undo to the saved revision clears the dirty flag. Save
failure retains the edited canvas for retry. Browser checks compare exact pixels for paint/erase/undo/redo,
soft edges, opacity, cursor-anchored zoom, painting while zoomed, pan without mutation, and saved dimensions.
The expanded view was visually inspected at 1280×720, including layering above the material panel.

## 0.4.0 release checks (2026-11-06)

- Full backend suite in the ML environment: **695 passed, 12 skipped** in 464.26 seconds. Four warnings:
  existing asynchronous test-job teardown/file logging warnings and a trimesh degenerate-volume warning.
  The skips retain their normal hardware/optional-feature gates.
- Production UI build passed. Full browser suite: **103 passed** in 2.8 minutes. The sprite-export check now
  waits for the completed export to open before navigating to an earlier version, removing a test timing race.
- Ruff lint and format checks passed. The packaging manifest regression includes the texture runtime modules
  and user/MCP guides.
- A candidate portable ZIP passed integrity, file-scope and extracted-application smoke checks: built UI and API,
  CPU material creation/resizing/export, and MCP operation parity. The smoke reused installed Python dependencies;
  a complete first-install download was not repeated. Models, library assets, settings and promo captures are
  excluded. The final tagged archive is checked again before publication.
