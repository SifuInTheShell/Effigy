# SPDX-License-Identifier: GPL-3.0-or-later
"""in: [mesh.glb]; params: {"cone_deg": 60, "min_faces": 30, "margin": 0.002} → mesh.glb with one packed UV layer.

Charts grown over the surface while their normals stay within cone_deg of the chart's mean (largest faces first),
charts under min_faces merged into a neighbour, seams between charts → Minimum Stretch (SLIM) per chart → equal
texel scale → pack. SLIM flattens far bigger charts than an angle-based unwrap without stretch, so a curved organic
mesh comes out in a few dozen islands with short seams (issue #18). A chart SLIM folds onto itself (10 faces or more)
is grown again with a narrower cone (up to 3 times).
"""

import heapq
import math
import sys
from pathlib import Path

import bmesh
import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _lib import done, export_glb, import_mesh, job, mesh_report

j = job()
p = j["params"]
obj = import_mesh(j["in"][0])
me = obj.data
bpy.context.scene.tool_settings.use_uv_select_sync = (
    False  # select_overlap reports in the UV selection
)

bm = bmesh.new()
bm.from_mesh(me)
bm.faces.ensure_lookup_table()
normal = np.array([f.normal[:] for f in bm.faces])
area = np.array([f.calc_area() for f in bm.faces])
adj = [
    [g.index for e in f.edges if len(e.link_faces) == 2 for g in e.link_faces if g != f]
    for f in bm.faces
]
bm.free()
chart = np.zeros(len(normal), int)


# ponytail: per-face Python (heap, one norm per face): ~20k faces/s here, fine for a remesh's 10–50k; a raw
# 500k+ mesh takes minutes and may reach the step's 1800 s timeout. Vectorise grow() over an edge array if needed.
def grow(faces: np.ndarray, cone_deg: float) -> None:
    """Re-chart `faces` (a chart, or all): best-aligned neighbour first, while within the cone of the mean normal."""
    cos_lim, todo = math.cos(math.radians(cone_deg)), set(faces.tolist())
    for seed in faces[np.argsort(-area[faces])]:
        if seed not in todo:
            continue
        label, axis, heap = chart.max() + 1, np.zeros(3), [(0.0, int(seed))]
        while heap:
            _, fi = heapq.heappop(heap)
            mean = axis / (np.linalg.norm(axis) or 1)
            if fi not in todo or (axis.any() and normal[fi] @ mean < cos_lim):
                continue
            todo.discard(fi)
            chart[fi], axis = label, axis + normal[fi] * area[fi]
            for n in adj[fi]:
                if n in todo:
                    heapq.heappush(heap, (-float(normal[n] @ mean), n))


def merge_small(min_faces: int) -> None:
    """Faces of a chart under min_faces join their biggest neighbouring chart; a few sweeps reach chart interiors."""
    for _ in range(3):
        size = np.bincount(chart)
        for fi in np.flatnonzero(size[chart] < min_faces):
            near = {chart[n] for n in adj[fi]} - {chart[fi]}
            if near:
                to = max(near, key=lambda c: size[c])
                size[chart[fi]] -= 1
                size[to] += 1
                chart[fi] = to


def unwrap() -> np.ndarray:
    """Seams on the chart borders, SLIM, equal scale, pack; the faces SLIM folded onto others."""
    bm = bmesh.from_edit_mesh(me)
    for e in bm.edges:
        f = e.link_faces
        e.seam = bool(len(f) != 2 or chart[f[0].index] != chart[f[1].index])
    bmesh.update_edit_mesh(me)
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.unwrap(method="MINIMUM_STRETCH", margin=0, fill_holes=True)
    bpy.ops.uv.select_all(action="DESELECT")
    bpy.ops.uv.select_overlap()  # the folds SLIM made (unwrap packs the islands apart)
    bm = bmesh.from_edit_mesh(me)
    return np.array([f.index for f in bm.faces if f.uv_select], int)


cone, min_faces = float(p.get("cone_deg", 60)), int(p.get("min_faces", 30))
grow(np.arange(len(chart)), cone)
merge_small(min_faces)
bpy.ops.object.mode_set(mode="EDIT")
for _ in range(3):
    folded = unwrap()
    if not len(folded):
        break
    # ponytail: a chart with a few folded faces stays (they cover ~0.02 % of the texels, bake allows 1 %);
    # re-growing every one doubled the islands on the test meshes
    bad, n = np.unique(chart[folded], return_counts=True)
    if not (n >= 10).any():
        break
    cone *= 0.85
    for c in bad[n >= 10]:
        grow(np.flatnonzero(chart == c), cone)
    merge_small(min_faces)
else:
    folded = unwrap()
bpy.ops.uv.select_all(action="SELECT")
bpy.ops.uv.average_islands_scale()
bpy.ops.uv.pack_islands(shape_method="CONCAVE", rotate_method="ANY", margin=p.get("margin", 0.002))
bpy.ops.object.mode_set(mode="OBJECT")
rep = mesh_report(obj)
assert rep["uv_layers"] >= 1 and rep["uv_in_unit_square"], f"unwrap failed: {rep}"
if (
    not me.materials
):  # a GLB without a material reads back with no UVs (trimesh: steps/_mesh.read_glb)
    me.materials.append(bpy.data.materials.new("uv"))
export_glb(Path(j["out_dir"]) / "mesh.glb")
done(
    j,
    {
        "outputs": {"mesh": "mesh.glb"},
        "report": rep,
        "charts": len(np.unique(chart)),
        "folded": len(folded),
    },
)
