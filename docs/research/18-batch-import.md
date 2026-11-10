# Research 18: batch import of an external asset library

> Note, 2026-11-10. Audience: the owner and the agent who implements it. Purpose: record a missing capability found
> while connecting the Parkour asset library to Effigy, and sketch how to build it. Evidence: Effigy 0.4.0 source
> (`4029d45`), the Parkour library format (`Parkour/docs/asset_library.md`). Status 2026-11-10: §2 items 1–5 and 7
> are built (IMPLEMENTATION §4 "Batch import"), plus a folder as the source (no manifest); item 6 (the import
> options) is not.

## 1. The gap

Effigy imports one file into one asset. The Parkour project now produces an asset library: 459 static meshes,
one self-contained `.glb` each (glTF core, metallic-roughness, OpenGL normals, metres, +Y up, main maps on
`TEXCOORD_0`, lightmap UV on `TEXCOORD_1`), plus a catalog `library.json` (schema `parkour-asset-library/1`) with
name, category, tags, bounds, collision hulls and per-material notes. Bringing it in today means a client loop
(`Parkour/tools/asset_library/effigy_import.py`): `import_file` per file through MCP, wait for each job, then
`PATCH /api/assets/{id}` for the tags. It works but leaves these gaps:

| Gap | Where (0.4.0) | Effect on a library import |
|---|---|---|
| No batch or manifest import | `mcp/effigy_server.py` `import_file`; `PUT /api/assets/{id}/upload/{name}` (`server/app.py:226`) | one MCP call, one wait and one tag request per asset; no progress over the whole set; a client must keep its own resume state |
| No origin, source or licence on an asset | `asset.json` (`id, name, created, brief, kind, tags, archived`); `licenses.yaml` lists only Effigy's own components | nothing records that an asset is third-party, where it came from, or under which terms |
| `restricted` only for engine outputs | `RESTRICTED_STEPS = {"hunyuan"}` (`server/library.py:17`) | an imported third-party asset exports as if it were clean; Effigy's assets ship in the owner's commercial game, so this matters |
| Import recentres the model | `blender_ops/import_model.py:107-140` | the source pivot, which level placements rely on, is lost |
| Import drops collision | `blender_ops/import_model.py:39-41`; export regenerates one hull (`steps/export.py:121-129`) | hand-made convex hulls (the library's `collision.hulls`, or `UCX_` meshes) are lost |
| Import joins all meshes | `blender_ops/import_model.py:418-420` | multi-part kits lose their parts |
| Second UV set / custom attributes not guaranteed | only `TEXCOORD_0` is inspected (`steps/bake.py:21`) | the lightmap UV may not survive the join and re-export (untested) |

## 2. Proposal

One milestone ("batch import"), small enough for one PR, plus a contract PR for the frozen parts (§3 of
`docs/IMPLEMENTATION.md`).

1. **Manifest.** Accept a generic manifest
   `{"schema": "effigy-import/1", "defaults": {...}, "items": [{"file", "name"?, "tags"?, "category"?,
   "source"?, "license"?, "restricted"?, "keep_pivot"?, "collision"?}]}`, `file` relative to the manifest. Add one
   adapter that reads `parkour-asset-library/1` directly (`id` -> name, `category` + `tags` -> tags (at most 20),
   `file`, `collision.hulls`), so the library imports without a conversion step. Other formats get adapters later,
   only when needed.
2. **API.** `POST /api/import/batch {"manifest": <absolute path>, "only"?: [...], "limit"?: n, "dry_run"?: bool}`
   -> `{"batch_id", "items": n}`; `GET /api/import/batch/{id}` -> per item `queued | running | done | skipped |
   failed` with `asset_id` and `version`. The server creates the assets, uploads from disk (no streaming through the
   client), sets tags, and queues `import_model` jobs through the existing queue, one at a time like every other
   job (import is Blender on the CPU, but keep the one-job rule; measure before allowing parallel imports).
3. **MCP.** `import_batch(manifest, only?, limit?, dry_run?)` and `batch_status(batch_id, wait_s)`, mirroring
   `import_file` / `job_status`. Tool names are frozen once released (§3), so name them deliberately.
4. **Idempotence.** Store `origin` in `asset.json`: `{"library", "source_key", "sha256", "license", "restricted"}`.
   A rerun skips an item whose `source_key` and `sha256` match an existing asset, and reports `skipped: exists`.
5. **Licence and restriction.** An item with `restricted: true` (the default for a manifest whose `license` is not
   a known clean licence) marks its upload and every version derived from it `restricted`, through the existing
   ancestor rule (`server/library.py:420-422`). Export then refuses it unless `allow_restricted` is set, as for the
   experimental engine. The Parkour library is third-party material for local use only: it must import as
   restricted.
6. **Import options** for `import_model`, each optional and off by default so single-file imports do not change:
   - `keep_pivot`: skip the recentring; store the offset Effigy would have applied in `metrics`.
   - `collision`: hull point lists from the manifest (or the file's `UCX_` / `-convcolonly` meshes) saved as
     `collision.json` beside the mesh; export uses them instead of regenerating one hull.
   - `keep_parts`: no join; one mesh per source mesh.
7. **UI.** Library: "Import folder or manifest...", a preview list (name, tags, size, restricted), then progress per
   item. No new page.

## 3. How an agent should tackle it

- Read `CLAUDE.md`, then `docs/IMPLEMENTATION.md` §0 (read order), §3 (frozen contracts), §6 (never-do) and §7 (PR
  acceptance). Use the `ponytail` skill (full) for all code. Route through `effigy-explorer` first (confirm the line
  references above against the current source), then `effigy-worker`; `effigy-expert` only for the contract
  changes (`asset.json.origin`, the import options, the restricted default).
- Order: contract PR (manifest schema, `origin`, new endpoints and MCP tools, import options) -> server batch runner
  on the existing job queue -> MCP tools -> import options in `blender_ops/import_model.py` -> UI.
- Reuse, do not duplicate: the upload path of `PUT .../upload/{name}`, `library.update_asset` for tags, the job
  queue and `job_status` waiting, the restricted ancestor rule, `server/thumbs.py` for thumbnails.
- Tests: a small synthetic fixture under `tests/fixtures/` (three tiny `.glb` files and a manifest, one restricted,
  one with hulls). **Never commit the Parkour library files**: they are third-party, local only.
- Acceptance checks:
  - a dry run lists every item and its tags without creating anything;
  - the fixture imports, a rerun skips all items, a changed file re-imports;
  - a restricted item's export is refused without `allow_restricted`;
  - `keep_pivot` and `collision` round-trip through export (Godot profile: the `-convcolonly` node matches the hulls);
  - `TEXCOORD_1` survives import and export, or the gap is documented;
  - on the reference laptop: the Parkour library (459 files, about 630 MB) imports with time per asset and peak
    memory recorded, one job at a time.
- Until this lands, `Parkour/tools/asset_library/effigy_import.py` remains the stopgap: `import_file` with
  `still=true` per asset, tags via `PATCH`, resume state in `asset_library/effigy_import.json`. It cannot set
  `restricted`, so those assets must not be exported for shipping.
