# SPDX-License-Identifier: GPL-3.0-or-later
"""in: [rigged.glb, pack.glb, …]; params: {"clips": ["Idle_A", …], "fingers": false, "skeleton": path} →
rigged_anim.glb (one NLA track per clip) + report.

Clips from a pack (a GLB of animations on a UE5-named skeleton: Mesh2Motion) onto our rigged character
(M21, research 05 §retargeting). The rest poses differ (the farmer: A-pose, elbows bent forward, palms
backward; the pack: T-pose, palms down), so per bone a frame built from joint positions is matched first:
the bone's direction (head to its main child) and a twist reference: the palm's normal (thumb, pinky and
middle finger) for the hand, the fingers and the forearm, the body's forward (or up, for the feet) for
everything else. Then, per frame in world space, the source bone's rotation away from its rest goes on
top; hip travel is scaled by the height ratio. The torso chain and the clavicles skip the rest matching:
they keep the character's modelled posture and take only the clip's changes (the pack's clavicles and
spine run their own way and would drag the shoulders back and the head forward). Bone rolls play no part (ours are arbitrary). The name map
is UE5 → ours (_maps.UE5), SOMA → ours for a Kimodo clip (M26), or Mixamo's own names for an imported
animation (_maps.MIXAMO, 2026-10-12), told apart by the bones; bones the pack
lacks keep their rest pose. Keys on every frame, one NLA track
per clip, the GLB exported in track mode (the glTF importer leaves every clip's track playing at once and
an active action on top: both silenced first).
"""

import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).parent))
from _lib import done, job, source_map

j = job()
p = j["params"]
wanted = list(p["clips"])
bpy.ops.wm.read_factory_settings(use_empty=True)


def import_glb(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    arm = next(o for o in new if o.type == "ARMATURE")
    if arm.animation_data:
        arm.animation_data.action = None
        for t in arm.animation_data.nla_tracks:
            t.mute = True
    return arm, new


def height(arm):
    pts = [arm.matrix_world @ b.head_local for b in arm.data.bones]
    pts += [arm.matrix_world @ b.tail_local for b in arm.data.bones]
    return max(q.z for q in pts) - min(q.z for q in pts)


def joints(arm, name_of):
    """Our joint name → world rest position of that joint in `arm` (named through `name_of`)."""
    return {
        n: arm.matrix_world @ arm.data.bones[name_of(n)].head_local
        for n in ours_bones
        if name_of(n) in arm.data.bones
    }


def refs(pos):  # mirrored in steps/_connect.py (M65 inverts this retarget): change both
    """The body's forward and up, and the palm normal per side, from joint positions."""
    up = (pos["Head"] - pos["Hips"]).normalized()
    # forward from the feet (toes point the way the body faces), not from which side is called Left:
    # a pose model can swap the sides (the dwarf, M22), and that must not turn every clip around
    toes = sum((pos[f"{s}ToeBase"] - pos[f"{s}Foot"] for s in ("Left", "Right")), Vector())
    forward = toes - up * toes.dot(up)
    if forward.length < 1e-6:  # no toes to go by: the hip line
        forward = up.cross((pos["RightUpLeg"] - pos["LeftUpLeg"]).normalized())
    forward.normalize()
    palm = {
        s: (pos[f"{s}HandMiddle1"] - pos[f"{s}Hand"])
        .cross(pos[f"{s}HandThumb1"] - pos[f"{s}HandPinky1"])
        .normalized()
        for s in ("Left", "Right")
    }
    return forward, up, palm


def frame(primary, secondary):
    """Rotation whose columns are the primary direction, the secondary made perpendicular, their cross."""
    x = primary.normalized()
    y = secondary - x * secondary.dot(x)
    y = y.normalized() if y.length > 1e-6 else x.orthogonal().normalized()
    return Matrix((x, y, x.cross(y))).transposed()


ours, ours_objs = import_glb(j["in"][0])
ours_bones = [b.name for b in ours.data.bones]  # parents before children: Blender keeps that order
rest_world = {n: (ours.matrix_world @ ours.data.bones[n].matrix_local).to_3x3() for n in ours_bones}
to_arm = ours.matrix_world.inverted()
for pb in ours.pose.bones:
    pb.rotation_mode = "QUATERNION"
MAIN = {  # mirrored in steps/_connect.py (a test compares them)
    "Hips": "Spine",
    "Spine2": "Neck",
    "Neck": "Head",
    "LeftHand": "LeftHandMiddle1",
    "RightHand": "RightHandMiddle1",
}
KEEP = {  # mirrored in steps/_connect.py (a test compares them)
    "Hips",
    "Spine",
    "Spine1",
    "Spine2",
    "Neck",
    "Head",
    "LeftShoulder",
    "RightShoulder",
}  # the posture stays as modelled


def along(name):
    """The two joints whose difference is the bone's direction: head to its main child; a leaf continues its parent."""
    b = ours.data.bones[name]
    if name in MAIN:
        return name, MAIN[name]
    return (name, b.children[0].name) if b.children else (b.parent.name, name)


def frames(pos, twist, twists=None):  # mirrored in steps/_connect.py: change both
    """Our bone → its rest frame in `pos` (a rig's joint positions), with the twist reference chosen per
    bone. `twists` (skeleton.json, M22b: the hands' palm direction from rig_fit's rule, and whatever the
    user turned in the editor) replace the automatic references: a hand's twist serves its forearm and
    fingers too, in the finger formula's sign convention; any other joint's twist is its own reference."""
    forward, up, palm = refs(pos)
    twists = twists or {}
    for s in ("Left", "Right"):
        if f"{s}Hand" in twists:
            palm[s] = twists[f"{s}Hand"] if s == "Left" else -twists[f"{s}Hand"]
    out = {}
    for n in ours_bones:
        a, b = along(n)
        if a not in pos or b not in pos:
            continue
        side = "Left" if n.startswith("Left") else "Right"
        if n in twists and not n.endswith("Hand"):
            sec = twists[n]
        elif "Hand" in n or "ForeArm" in n:
            sec = palm[side]
        else:
            sec = up if twist[n] == "up" else forward
        out[n] = frame(pos[b] - pos[a], sec)
    return out


def read_twists(path):
    """skeleton.json's per-joint `twist` vectors (library frame, glTF Y-up) in Blender's frame."""
    if not path or not Path(path).is_file():
        return {}
    import json

    out = {}
    for x in json.loads(Path(path).read_text(encoding="utf-8")).get("joints", []):
        if x.get("twist"):
            t = x["twist"]
            out[x["name"]] = Vector(
                (t[0], -t[2], t[1])
            ).normalized()  # Y up → Z up, as the importer
    return out


pos_ours = joints(ours, lambda n: n)
fwd_ours, _, _ = refs(pos_ours)
twist = {}  # forward unless the bone runs along it (the feet): then up; the same choice on both rigs
for n in ours_bones:
    a, b = along(n)
    twist[n] = (
        "up" if abs((pos_ours[b] - pos_ours[a]).normalized().dot(fwd_ours)) > 0.7 else "forward"
    )
twists_ours = read_twists(p.get("skeleton"))
frame_ours = frames(pos_ours, twist, twists_ours)


def transfer(src, pairs, align, scale, frame):  # steps/_connect.soma_pose is its inverse
    """Pose our bones for the source's current frame; keys on rotation (and the hips' location)."""
    bpy.context.scene.frame_set(frame)
    desired = {}  # our bone → its wanted rotation in world space (3×3)
    for name, sname in pairs:
        sb, spb = src.data.bones[sname], src.pose.bones[sname]
        s_rest = (src.matrix_world @ sb.matrix_local).to_3x3()
        s_pose = (src.matrix_world @ spb.matrix).to_3x3().normalized()
        desired[name] = s_pose @ s_rest.inverted() @ align[name] @ rest_world[name]
    posed = {}  # our bone → pose rotation in armature space, parents first
    for name in ours_bones:
        pb, bone = ours.pose.bones[name], ours.data.bones[name]
        rest, parent = bone.matrix_local.to_3x3(), bone.parent
        if name in desired:
            rot = to_arm.to_3x3() @ desired[name]
        elif parent:  # not in the pack: the rest orientation relative to the parent
            rot = posed[parent.name] @ parent.matrix_local.to_3x3().inverted() @ rest
        else:
            rot = rest
        posed[name] = rot
        if parent:
            rel = parent.matrix_local.to_3x3().inverted() @ rest
            basis = rel.inverted() @ posed[parent.name].inverted() @ rot
        else:
            basis = rest.inverted() @ rot
        pb.rotation_quaternion = basis.to_quaternion()
        pb.keyframe_insert("rotation_quaternion", frame=frame)
    hips, shname = pairs[0]
    sb, spb = src.data.bones[shname], src.pose.bones[shname]
    travel = (src.matrix_world @ spb.head) - (src.matrix_world @ sb.head_local)
    bone = ours.data.bones[hips]
    target = to_arm @ (ours.matrix_world @ bone.head_local + travel * scale)  # armature space
    pb = ours.pose.bones[hips]
    pb.location = bone.matrix_local.to_3x3().inverted() @ (target - bone.matrix_local.translation)
    pb.keyframe_insert("location", frame=frame)


baked, missing = [], list(wanted)
scale = 1.0
for pack in j["in"][1:]:
    names_before = {a.name for a in bpy.data.actions}
    src, src_objs = import_glb(pack)
    here = [c for c in missing if c in bpy.data.actions and c not in names_before]
    if not here:
        for o in src_objs:
            bpy.data.objects.remove(o, do_unlink=True)
        continue
    MAP, root = source_map(src)  # UE5 (the Mesh2Motion pack) or SOMA (a Kimodo clip, M26)
    inverse = {v: k for k, v in MAP.items()}  # source name → ours
    pairs = [("Hips", root)] + [
        (inverse[sb.name], sb.name)
        for sb in src.data.bones
        if sb.name in inverse and inverse[sb.name] != "Hips" and inverse[sb.name] in ours.data.bones
    ]
    # the pack faces our way (a turn about Z), then its rest frames
    fwd_src, _, _ = refs(joints(src, lambda n, m=MAP: m[n]))
    turn = Vector((fwd_src.x, fwd_src.y, 0)).rotation_difference(
        Vector((fwd_ours.x, fwd_ours.y, 0))
    )
    src.matrix_world = turn.to_matrix().to_4x4() @ src.matrix_world
    frame_src = frames(joints(src, lambda n, m=MAP: m[n]), twist)
    # the torso and the clavicles keep the character's own posture and take the clip's changes only (the pack's
    # clavicles run out and back from the chest front, its spine curves its own way: forcing those directions
    # pushed the farmer's shoulders back 15 % of the torso height and the head forward); limbs match fully
    align = {
        n: Matrix.Identity(3) if n in KEEP else frame_src[n] @ frame_ours[n].transposed()
        for n, _ in pairs
        if n in frame_src
    }
    pairs = [(n, s) for n, s in pairs if n in align]
    if not p.get(
        "fingers"
    ):  # the pack's finger curl mashes a generated hand (voxel-proxy weights): the hand keeps its own
        pairs = [(n, s) for n, s in pairs if "Hand" not in n or n.endswith("Hand")]
    scale = height(ours) / height(src)
    for name in here:
        act = bpy.data.actions[name]
        src.animation_data.action = act
        if act.slots:
            src.animation_data.action_slot = act.slots[0]
        f0, f1 = (int(x) for x in act.frame_range)
        new_act = bpy.data.actions.new(name)
        slot = new_act.slots.new(id_type="OBJECT", name=name)
        ours.animation_data_create()
        ours.animation_data.action = new_act
        ours.animation_data.action_slot = slot
        for f in range(f0, f1 + 1):
            transfer(src, pairs, align, scale, f)
        ours.animation_data.action = None
        new_act.use_fake_user = True
        baked.append((name, new_act, f1 - f0))
        missing.remove(name)
    src.animation_data.action = None
    for o in src_objs:
        bpy.data.objects.remove(o, do_unlink=True)

assert baked, f"none of {wanted} found in the packs"
for pb in ours.pose.bones:
    pb.rotation_quaternion = (1, 0, 0, 0)
    pb.location = (0, 0, 0)
fps = bpy.context.scene.render.fps
for name, act, _ in baked:
    track = ours.animation_data.nla_tracks.new()
    track.name = name
    track.strips.new(name, int(act.frame_range[0]), act)
bpy.ops.object.select_all(action="DESELECT")
for o in ours_objs:
    o.select_set(True)
out = Path(j["out_dir"]) / "rigged_anim.glb"
bpy.ops.export_scene.gltf(
    filepath=str(out),
    export_format="GLB",
    use_selection=True,
    export_animations=True,
    export_animation_mode="NLA_TRACKS",
    export_skins=True,
)
report = {
    "clips": [{"name": n, "frames": f, "seconds": round(f / fps, 3)} for n, _, f in baked],
    "fps": fps,
    "missing": missing,
    "height_scale": round(scale, 3),
}
done(j, {"outputs": {"rigged_anim": "rigged_anim.glb"}, "report": report})
