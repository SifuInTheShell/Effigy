// UX Pass 2: the task row and the preparation state are pure rules over the lineage (ui/src/workflow.ts), checked here
// the way anim.spec.ts checks the editor's evaluation: no browser.
import { expect, test } from "@playwright/test";
import type { Meta } from "../src/api";
import { TASKS_2D, TASKS_3D, prepState, taskOf } from "../src/workflow";

const m = (version: string, step: string, parent: string | null, outputs: Record<string, string>, params: Record<string, unknown> = {}): Meta => ({
  version,
  step,
  parent,
  outputs,
  params,
  created: "",
  metrics: {},
  provenance: "step",
  restricted: false,
});
const GLB = { mesh: "mesh.glb" };

test("taskOf: every step of a generated character lands in one of the five tasks; an import starts at Prepare; a 2D asset goes by step", () => {
  const vs = [
    m("010_brief", "brief", null, {}),
    m("020_concepts", "concepts", "010_brief", { cand_0: "c.png" }),
    m("030_prep", "prep", "020_concepts", { image: "i.png" }),
    m("040_trellis2", "trellis2", "030_prep", { mesh: "hi.glb", ovoxel: "ovoxel.npz" }),
    m("050_clean", "clean", "040_trellis2", GLB),
    m("060_bake", "bake", "050_clean", GLB),
    m("070_project", "project", "060_bake", GLB),
    m("080_parts", "parts", "060_bake", GLB),
    m("090_rig_fit", "rig_fit", "060_bake", { ...GLB, skeleton: "skeleton.json" }),
    m("100_rig_skin", "rig_skin", "090_rig_fit", { rigged: "rigged.glb" }),
    m("110_motion", "motion", "100_rig_skin", { clip: "clip.glb" }),
    m("115_upload", "upload", null, { video: "v.mp4" }),
    m("120_mocap", "mocap", "115_upload", { clip: "clip.glb" }),
    m("130_anim", "anim", "100_rig_skin", { rigged_anim: "r.glb" }),
    m("140_validate", "validate", "060_bake", { checklist: "c.json" }),
    m("150_export", "export", "140_validate", { "a.glb": "godot/a.glb" }),
    m("160_trellis2", "trellis2", "060_bake", GLB, { op: "texture" }),
    m("170_sprites", "sprites", "130_anim", { sheet: "sheet.png", data: "sheet.json" }),
    m("180_pixelize", "pixelize", "170_sprites", { sheet: "sheet.png", data: "sheet.json" }),
  ];
  const task = (v: string) => TASKS_3D[taskOf(vs, vs.find((x) => x.version === v)!)].id;
  expect(["010_brief", "020_concepts", "030_prep"].map(task)).toEqual(["reference", "reference", "reference"]);
  expect(task("040_trellis2")).toBe("generate");
  expect(["050_clean", "060_bake", "070_project", "080_parts", "160_trellis2"].map(task)).toEqual(["prepare", "prepare", "prepare", "prepare", "prepare"]);
  expect(["090_rig_fit", "100_rig_skin", "110_motion", "120_mocap", "130_anim"].map(task)).toEqual(["character", "character", "character", "character", "character"]);
  expect(["170_sprites", "180_pixelize"].map(task)).toEqual(["character", "character"]); // M52: the animations drawn for 2D
  expect(["140_validate", "150_export"].map(task)).toEqual(["finish", "finish"]);
  const imp = [m("010_upload", "upload", null, GLB), m("020_import_model", "import_model", "010_upload", GLB)];
  expect(TASKS_3D[taskOf(imp, imp[1])].id).toBe("prepare");
  const two = (step: string, params = {}) => TASKS_2D[taskOf([], m("x", step, null, {}, params), "2d")].id;
  expect([
    two("upload"),
    two("brief"),
    two("concepts", { op: "image" }),
    two("pixelize"),
    two("layers"),
    two("concepts", { op: "tile" }),
    two("pixelize", { op: "edit" }),
    two("validate"),
    two("export"),
  ]).toEqual(["picture", "picture", "picture", "pixelize", "layers", "tiles", "edit", "finish", "finish"]);
});

test("prepState: completed by a later check, active by the running job's input, historical by a later prepared checkpoint, else interrupted with the steps left", () => {
  const vs = [
    m("010_trellis2", "trellis2", null, { mesh: "hi.glb", ovoxel: "ovoxel.npz" }),
    m("020_clean", "clean", "010_trellis2", GLB),
    m("030_remesh", "remesh", "020_clean", GLB),
    m("040_uv", "uv", "030_remesh", GLB),
    m("050_bake", "bake", "040_uv", GLB),
    m("060_validate", "validate", "050_bake", { checklist: "c.json" }),
    m("070_remesh", "remesh", "020_clean", GLB), // a second branch, stopped after the unwrap
    m("080_uv", "uv", "070_remesh", GLB),
  ];
  const at = (v: string) => vs.find((x) => x.version === v)!;
  expect(prepState(vs, at("040_uv"))).toMatchObject({ state: "completed", next: at("060_validate") });
  expect(prepState(vs, at("020_clean"))).toMatchObject({ state: "completed" }); // the check is a descendant down one branch
  expect(prepState(vs, at("080_uv"))).toMatchObject({ state: "interrupted", remaining: ["bake", "check"] });
  expect(prepState(vs, at("080_uv"), "080_uv")).toMatchObject({ state: "active" });
  expect(prepState(vs, at("070_remesh"), "080_uv")).toMatchObject({ state: "active" }); // the job runs on its descendant
  expect(prepState(vs, at("070_remesh"))).toMatchObject({ state: "historical", next: at("080_uv") });
  expect(prepState(vs, at("070_remesh"), "040_uv")).toMatchObject({ state: "historical" }); // a job on another branch is not this one's
  // M62: an uploaded glb bakes its own textures; an import without materials has none to bake
  const imp = [m("010_upload", "upload", null, GLB), m("020_clean", "clean", "010_upload", GLB)];
  expect(prepState(imp, imp[1])).toMatchObject({ state: "interrupted", remaining: ["simplify", "unwrap", "bake", "check"] });
  const bare = [m("010_upload", "upload", null, { model: "source/a.stl" }), { ...m("020_import_model", "import_model", "010_upload", GLB), metrics: { materials: 0 } }];
  const clean = m("030_clean", "clean", "020_import_model", GLB);
  expect(prepState([...bare, clean], clean).remaining).toEqual(["simplify", "unwrap", "check"]);
  const glbUp = [m("010_upload", "upload", null, { model: "source/a.glb" }), bare[1]]; // a .glb upload whose import found no materials
  expect(prepState([...glbUp, clean], clean).remaining).toEqual(["simplify", "unwrap", "check"]);
  const textured = { ...bare[1], metrics: { materials: 2 } };
  expect(prepState([bare[0], textured, clean], clean).remaining).toEqual(["simplify", "unwrap", "bake", "check"]);
});

test("TOOLS: a tool fits by the shown version's lineage, and the next tool is the recommended one", async () => {
  const { TOOLS, recommended } = await import("../src/workflow");
  const vs = [
    m("010_upload", "upload", null, GLB),
    m("020_rig_skin", "rig_skin", "010_upload", { ...GLB, rigged: "rigged.glb", skeleton: "skeleton.json" }),
    m("030_anim", "anim", "020_rig_skin", { rigged_anim: "ra.glb", clips: "clips.json", skeleton: "skeleton.json" }),
  ];
  const fits = (v: Meta) => TOOLS.filter((t) => t.ok({ versions: vs, v })).map((t) => t.id);
  expect(fits(vs[0])).toContain("fit");
  expect(fits(vs[1])).toEqual(expect.arrayContaining(["clips", "text", "video", "file"]));
  expect(fits(vs[1])).not.toContain("editanim");
  expect(fits(vs[2])).toEqual(expect.arrayContaining(["connect", "editanim"]));
  expect(fits(vs[2])).not.toContain("clips");
  expect(recommended({ versions: vs, v: vs[1] })).toBe("clips");
  expect(recommended({ versions: vs, v: vs[2] })).toBe("editanim");
});
