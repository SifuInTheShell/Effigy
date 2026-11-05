# SPDX-License-Identifier: GPL-3.0-or-later
"""in: [closed mesh.glb, surface.glb (shrinkwrap target; default: the first)]; params: {"target_faces": 5000, "symmetry": false, "voxels": [120, 100, 80, 60]}
→ mesh.glb (quads as triangle pairs, no UVs) + quads.obj + report (quads counted before the export triangulates).

Voxel remesh (watertight; QuadriFlow fails on non-manifold input) → QuadriFlow to target/4 at 10x scale
(Blender's pre-check rejects edges shorter than 1e-4 in absolute units: a 1 m TRELLIS character had 103)
→ fill QuadriFlow's holes (it drops faces it can't make quads: on the bench mage 19-194 small loops, most
of 3 edges) → one Catmull-Clark level (every face, filled ones too, becomes quads: pure quads at ~target)
→ shrinkwrap onto the surface (detail back). Voxel resolutions are tried finest first and the run
with the fewest holes wins (0 stops the search). Finer than ~120 voxels QuadriFlow crashed Blender.
"""

import sys
from pathlib import Path

import bmesh
import bpy

sys.path.insert(0, str(Path(__file__).parent))
from _lib import done, export_glb, import_mesh, job, mesh_report

SCALE = 10.0

j = job()
p = j["params"]
obj = import_mesh(j["in"][0])
original = obj.data.copy()
size = max(obj.dimensions)
tried, best = [], None
for voxels in p.get("voxels", [120, 100, 80, 60]):
    obj.data = original.copy()
    obj.data.remesh_voxel_size = size / voxels
    bpy.ops.object.voxel_remesh()
    obj.data.use_mirror_x = bool(p.get("symmetry"))  # use_mesh_symmetry mirrors the mesh's own axes
    obj.scale = (SCALE,) * 3
    bpy.ops.object.transform_apply(scale=True)
    r = bpy.ops.object.quadriflow_remesh(
        target_faces=max(p.get("target_faces", 5000) // 4, 100),
        use_mesh_symmetry=p.get("symmetry", False),
        seed=0,
    )
    obj.scale = (1 / SCALE,) * 3
    bpy.ops.object.transform_apply(scale=True)
    rep = mesh_report(obj)
    tried.append({"voxels": voxels, "result": sorted(r), "holes_edges": rep["boundary_edges"]})
    if (
        r == {"FINISHED"}
        and rep["non_manifold_edges"] == 0
        and (best is None or rep["boundary_edges"] < best[1])
    ):
        best = (obj.data.copy(), rep["boundary_edges"], voxels)
        if rep["boundary_edges"] == 0:
            break
assert best, f"quadriflow found no usable result: {tried}"
obj.data, holes, voxels = best

bm = bmesh.new()
bm.from_mesh(obj.data)
bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary], sides=0)
bm.to_mesh(obj.data)
bm.free()
if len(j["in"]) > 1:
    surface = import_mesh(j["in"][1], new_scene=False)
else:
    surface = bpy.data.objects.new("surface", original)
    bpy.context.scene.collection.objects.link(surface)
bpy.ops.object.select_all(action="DESELECT")
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
obj.modifiers.new("subdivide", "SUBSURF").levels = 1
wrap = obj.modifiers.new("wrap", "SHRINKWRAP")
wrap.target, wrap.wrap_method = surface, "NEAREST_SURFACEPOINT"
for m in list(obj.modifiers):
    bpy.ops.object.modifier_apply(modifier=m.name)
bpy.data.objects.remove(surface)

rep = mesh_report(obj)
assert rep["faces"] == rep["quads"] and rep["non_manifold_edges"] == 0, f"not clean quads: {rep}"
# smooth shading: otherwise the glTF exporter writes one normal per quad (flat)
for poly in obj.data.polygons:
    poly.use_smooth = True
export_glb(Path(j["out_dir"]) / "mesh.glb", export_materials="NONE")
bpy.ops.wm.obj_export(
    filepath=str(Path(j["out_dir"]) / "quads.obj"),
    export_selected_objects=True,
    export_materials=False,
    export_uv=False,
)
done(
    j,
    {
        "outputs": {"mesh": "mesh.glb"},
        "report": rep
        | {"topology": "quad", "voxels": voxels, "holes_filled_edges": holes, "tried": tried},
    },
)
