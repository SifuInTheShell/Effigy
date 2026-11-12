// Pure lineage/checkpoint rules for the workspace (docs/archive/UI_REDESIGN_PROPOSAL.md §6). No React here.
import type { Meta } from "./api";

// an upload's `model` is the original file (M39), a .glb among them too: not a mesh to work on until imported
export const hasGlb = (m: Meta) => Object.entries(m.outputs).some(([k, f]) => k !== "model" && f.endsWith(".glb"));
export const hasImage = (m: Meta) => Object.values(m.outputs).some((f) => /\.(png|jpe?g|webp)$/.test(f));
export const mainFile = (m: Meta) =>
  Object.values(m.outputs).find((f) => f.endsWith(".glb")) ?? Object.values(m.outputs).find((f) => /\.(png|jpe?g|webp)$/.test(f));

// m, its parent, its parent's parent…; stops at a missing parent or a cycle
export function ancestors(versions: Meta[], m: Meta | null | undefined): Meta[] {
  const out: Meta[] = [];
  while (m && !out.includes(m)) {
    out.push(m);
    m = versions.find((v) => v.version === m!.parent);
  }
  return out;
}

// what a version shows and what checks/exports act on: a report or an export package has no working mesh
// of its own (an export GLB may carry collision helpers), so walk up to the version it was made from
export const source = (versions: Meta[], m: Meta | null) => ancestors(versions, m).find((a) => a.step !== "validate" && a.step !== "export") ?? null;

/** M39: the step a version acts as: an import_model with clips as anim, with a skin as rig_skin; any other as itself. */
export const actsAs = (m: Meta) => (["import_model", "textures"].includes(m.step) ? m.outputs.rigged_anim ? "anim" : m.outputs.rigged ? "rig_skin" : m.step : m.step);

// volume lineage (the generator's ovoxel.npz): only then can textures be baked; imported meshes have none
export const generated = (versions: Meta[], m: Meta) => ancestors(versions, m).some((a) => "ovoxel" in a.outputs);

// M62: a bake needs a colour source up the chain: the generator's volume, an import with materials, or an uploaded glb
export const bakeable = (versions: Meta[], m: Meta) => {
  const line = ancestors(versions, m);
  const imp = line.find((a) => a.step === "import_model"); // the nearest import decides: it knows whether there are materials
  return line.some((a) => "ovoxel" in a.outputs) || (imp ? Number(imp.metrics?.materials) > 0 : line.some((a) => a.step === "upload" && Object.values(a.outputs).some((f) => f.endsWith(".glb"))));
};

// game-ready preparation, in order, with the friendly name of each operation
export const RECIPE: [string, string][] = [
  ["clean", "clean"],
  ["remesh", "simplify"],
  ["uv", "unwrap"],
  ["bake", "bake"],
  ["validate", "check"],
];
const PREPARED = new Set(["clean", "remesh", "uv", "bake", "parts"]);

/** UX Pass 2: the row shows tasks, not steps; a checkpoint's task is `taskOf`, History keeps every step. */
export type Task = { id: string; label: string; needs: string; optional?: boolean };
export const TASKS_3D: Task[] = [
  { id: "reference", label: "Reference", needs: "a picture or a description" },
  { id: "generate", label: "Generate", needs: "a cut-out reference" },
  { id: "prepare", label: "Prepare", needs: "a model to prepare" },
  { id: "textures", label: "Textures", needs: "a model", optional: true },
  { id: "character", label: "Character", needs: "a prepared model", optional: true },
  { id: "finish", label: "Finish", needs: "a prepared model" },
];
// a 2D asset (asset.json.kind "2d": the 2D contract on the 2d-contract branch until merged): its steps arrive with M50-M58; the row is ready for them
export const TASKS_2D: Task[] = [
  { id: "picture", label: "Picture", needs: "a picture or a description" },
  { id: "pixelize", label: "Pixelize", needs: "a picture" },
  { id: "layers", label: "Layers", needs: "a picture" },
  { id: "tiles", label: "Tiles", needs: "a description" },
  { id: "edit", label: "Edit", needs: "pixel art" },
  { id: "finish", label: "Finish", needs: "pixel art, layers or a tile" },
];
const RIG = new Set(["rig_fit", "rig_skin"]);
export const TEXTURED = new Set(["bake", "project", "refine"]);

/** The task (an index in TASKS_3D, or in TASKS_2D for kind "2d") a version belongs to. */
export function taskOf(versions: Meta[], m: Meta, kind?: string): number {
  const step = actsAs(m);
  if (kind === "2d") {
    if (step === "validate" || step === "export") return 5;
    if (step === "pixelize") return m.params?.op === "edit" ? 4 : 1;
    if (step === "layers" || step === "depth") return 2;
    if (step === "concepts" && m.params?.op === "tile") return 3;
    return 0;
  }
  if (step === "validate" || step === "export") return 5;
  if (m.step === "textures" || m.step === "refine" || m.step === "project" || (m.step === "trellis2" && m.params.op === "texture")) return 3;
  if (step === "mocap" || step === "sprites" || step === "pixelize") return 4;
  const phase = phaseOf(versions, m);
  return phase >= 3 ? 4 : phase;
}

export type PrepState = { state: "completed" | "active" | "interrupted" | "historical"; next?: Meta; remaining: string[] };
/** UX-07: what a clean/simplify/unwrap/bake checkpoint is, from its descendants on this branch and the running job's
 * input version; `remaining` names the recipe steps after it (the bake only with a colour source). */
export function prepState(versions: Meta[], v: Meta, running?: string | null): PrepState {
  const below = versions.filter((x) => x !== v && ancestors(versions, x).includes(v));
  const checked = below.filter((x) => x.step === "validate").at(-1);
  const remaining = RECIPE.slice(RECIPE.findIndex(([s]) => s === v.step) + 1)
    .filter(([s]) => s !== "bake" || bakeable(versions, v))
    .map(([, name]) => name);
  if (checked) return { state: "completed", next: checked, remaining: [] };
  if (running && (running === v.version || below.some((x) => x.version === running))) return { state: "active", remaining };
  const later = below.filter((x) => hasGlb(x) && phaseOf(versions, x) === 2).at(-1);
  if (later) return { state: "historical", next: later, remaining };
  return { state: "interrupted", remaining };
}

/** The panel's coarser phase: 0 Reference, 1 Generate, 2 Prepare, 3 Rig, 4 Animate, 5 Export. */
export function phaseOf(versions: Meta[], m: Meta): number {
  const step = actsAs(m);
  if (step === "export") return 5;
  if (step === "anim" || step === "anim_edit" || step === "motion" || step === "import_motion") return 4;
  if (RIG.has(step)) return 3;
  if (step === "validate") {
    const up = source(versions, m);
    const of = up ? actsAs(up) : ""; // a check of a rig is Rig, of an animated model Animate
    return of === "anim" || of === "anim_edit" ? 4 : RIG.has(of) ? 3 : 2;
  }
  if (PREPARED.has(m.step)) return 2;
  if (!hasGlb(m)) return 0;
  // a mesh: still raw from the generator (or a Blender/Claude edit of it) = Generate; prepared before,
  // retextured, or imported = Prepare
  if (m.step === "trellis2" && m.params?.op === "texture") return 2;
  const line = ancestors(versions, m);
  return line.some((a) => PREPARED.has(a.step)) || !generated(versions, m) ? 2 : 1;
}

/** M42: the version a batch exports: the newest starred one that is at least prepared, else the newest such (raw generated meshes and exports left out). */
export function exportPick(versions: Meta[]): Meta | undefined {
  const ok = versions.filter((m) => m.step !== "export" && ((!!m.outputs.material && !hasGlb(m)) || (hasGlb(m) && phaseOf(versions, m) >= 2)));
  return ok.filter((m) => m.starred).at(-1) ?? ok.at(-1);
}

/** M52: sprites are rendered from an animated version, or a prepared still model (a prop, one frame per view). */
export const spritesFrom = (versions: Meta[], m: Meta) =>
  !!m.outputs.rigged_anim || (m.step !== "export" && !!m.outputs.mesh && phaseOf(versions, m) === 2 && m.step !== "rig_fit");

export const isImported = (versions: Meta[], m: Meta) => ancestors(versions, m).some((a) => (a.step === "upload" && hasGlb(a)) || a.step === "import_model");

const STEPS: Record<string, string> = {
  brief: "Description",
  concepts: "Concept images",
  prep: "Cut-out reference",
  canon: "Re-posed reference",
  transform: "Moved model",
  blender: "Blender edit",
  claude: "Claude edit",
  clean: "Cleaned",
  remesh: "Simplified",
  uv: "Unwrapped",
  bake: "Textured model",
  project: "Face from image",
  refine: "Refined texture",
  textures: "Texture edit",
  rig_fit: "Skeleton",
  rig_skin: "Skinned model",
  anim: "Animated model",
  anim_edit: "Edited animation",
  motion: "Motion from text",
  import_motion: "Animation from a file",
  parts: "Parts",
  pixelize: "Pixel art",
  redraw: "Redrawn picture",
  sprites: "Sprites",
};

export function label(m: Meta): string {
  if (m.step === "textures") return ({ upscale: "Upscaled texture", resize: "Resized texture", generate: "Generated texture", retexture: "New texture set", replace: "Replaced map", apply: "Applied material", import: "Imported material", enhance: "Enhanced texture", surface: "Estimated surface maps", extract: "Saved material", adjust: "Adjusted texture", create: "New texture", seamless: "Repeating texture" } as Record<string, string>)[String(m.params.op)] ?? "Texture edit";
  if (m.step === "upload") return hasGlb(m) ? "Imported model" : m.outputs.model ? "Original file" : "Uploaded picture";
  if (m.step === "import_model") return { anim: "Imported animation", rig_skin: "Imported rig" }[actsAs(m)] ?? "Imported model";
  if (m.step === "export") return `Export: ${PROFILE_NAMES[String(m.metrics?.profile)] ?? m.metrics?.profile ?? ""}`;
  if (m.step === "trellis2") return m.params?.op === "texture" ? "Retextured model" : `Generated model (${m.params?.weights ?? ""})`;
  // the step only counts failures; warnings need the report itself, so never say "passed" here
  if (m.step === "parts") {
    if (m.params?.op === "views") return "Parts: views";
    const names = Object.keys((m.metrics?.parts as Record<string, number> | undefined) ?? {}).filter((n) => n !== "Body");
    return names.length ? `Parts: ${names.join(", ")}` : "Parts";
  }
  if (m.step === "validate") return m.metrics?.fail ? `Checks: ${m.metrics.fail} failed` : "Checks";
  if (m.step === "concepts" && m.params?.op === "image") return "Pictures";
  if (m.step === "redraw" && m.params?.op === "upscale") return "Upscaled picture";
  if (m.step === "pixelize" && m.params?.palette_map) return "Recoloured pixel art";
  if (m.provenance === "blender_pull") return STEPS.blender;
  if (m.provenance === "claude_run") return STEPS.claude;
  return STEPS[m.step] ?? m.step;
}

// export profiles (steps/export.py) with what each really produces
export const EXPORTS: { id: string; name: string; what: string; next: string; lods?: boolean; collision?: boolean }[] = [
  {
    id: "godot",
    name: "Godot",
    what: "Saves a GLB file. Godot makes the lower detail levels (LODs) itself.",
    next: "Copy the folder into your Godot project: Godot imports the GLB, its textures and its collision by itself.",
    collision: true,
  },
  {
    id: "unreal",
    name: "Unreal",
    what: "Saves an FBX file with lower detail levels (LODs). Turn collision on if you want a hull for physics.",
    next: "In Unreal, import the FBX from the folder: its textures and the UCX collision come along; untick Nanite to keep the LODs.",
    lods: true,
    collision: true,
  },
  {
    id: "unity",
    name: "Unity",
    what: "Saves an FBX file with lower detail levels (LODs). Add a Mesh Collider in Unity yourself.",
    next: "Drop the folder into your Unity project's Assets: the FBX finds its textures; add a Mesh Collider (Convex) for collision.",
    lods: true,
  },
  {
    id: "web",
    name: "Web / AR",
    what: "Saves a small GLB file and a USDZ file for the web and AR. Textures are at most 1024 pixels.",
    next: "Put the GLB on your website (a model viewer shows it); the USDZ opens in AR on an iPhone or iPad.",
  },
  {
    id: "obj",
    name: "OBJ",
    what: "Saves an OBJ file with its material and a colour texture.",
    next: "Keep the .mtl and the texture next to the .obj: other programs find them there.",
  },
  {
    id: "print",
    name: "3D print",
    what: "Saves STL and 3MF files in millimetres. A model split into parts (the Parts tool) prints as one closed solid per part, made to fit together: an STL for each, and a 3MF with every part named. The wall thickness is not checked.",
    next: "Open the 3MF in your slicer: every part arrives as its own object; the STLs are the same parts, one file each.",
  },
];
// a side-scrolling fighter's moves from the ready-made pack (Mesh2Motion, CC0; research 15 §2's brawler list):
// only the names the installed pack has are picked
export const MOVE_SETS: [string, string, string[]][] = [
  [
    "brawler",
    "Brawler (Double Dragon)",
    ["Fighting Idle", "Walk", "Jog", "Fighting Left Jab", "Fighting Right Jab", "Punch_Cross", "Melee_Hook", "Kick_Breach", "Jump_Start", "Jump_air",
      "Jump_Land", "Hit_Head", "Hit_Chest", "Hit_Knockback", "LayToIdle", "Death_A", "Defend", "PickUp_Table", "Throw Object", "Victory"],
  ],
  [
    "sword",
    "Sword fighter (Golden Axe)",
    ["Idle_Sword", "Walk", "Jog", "Sword_Regular_A", "Sword_Regular_B", "Sword_Regular_C", "Sword_Regular_Combo", "Sword_Attack",
      "Sword_Attack_Air_Vertical", "Sword_Block", "Jump_Start", "Jump_air", "Jump_Land", "Hit_Chest", "Hit_Knockback", "LayToIdle", "Death_B",
      "Spell_Simple_Shoot", "Victory"],
  ],
  ["basic", "Basic (idle, walk, jog)", ["Idle_A", "Walk", "Jog"]],
];
// the moves a fighter needs that the pack lacks: a sentence each for text-to-motion (one-shots, about 2.5 s)
export const FIGHT_PROMPTS: [string, string][] = [
  ["Roundhouse kick", "A person does a high roundhouse kick with the right leg, then returns to a fighting stance."],
  ["Flying kick", "A person jumps forward and does a flying kick with the right leg, then lands in a fighting stance."],
  ["Uppercut", "A person throws a rising uppercut with the right fist, then returns to a fighting stance."],
  ["Grab and knee", "A person grabs someone in front by the collar with both hands and knees them in the stomach."],
  ["Throw", "A person grabs someone in front and throws them over the right shoulder."],
  ["Back elbow", "A person throws a backward elbow strike with the right arm, then turns back to a fighting stance."],
];

// M53: a sprite sheet's destinations (steps/export.py's 2D profiles)
export const EXPORTS_2D: { id: string; name: string; what: string; next: string; lods?: boolean; collision?: boolean }[] = [
  {
    id: "godot-2d",
    name: "Godot 2D",
    what: "Saves the sheet, a SpriteFrames resource with one animation per clip and an AnimatedSprite2D scene standing on its feet.",
    next: "Copy the folder into your Godot project and drop the .tscn into a scene; README.txt has the settings for crisp pixels.",
  },
  {
    id: "unreal-2d",
    name: "Unreal (Paper2D)",
    what: "Saves the sheet and a Paper2D sprite sheet: one sprite per frame, named after its animation, the pivot at the feet.",
    next: "Enable Paper2D, import the .paper2dsprites, then select an animation's sprites and choose Create Flipbook.",
  },
];
export const PROFILE_NAMES: Record<string, string> = { ...Object.fromEntries([...EXPORTS, ...EXPORTS_2D].map((e) => [e.id, e.name])), textures: "Texture set" };

// concepts' gate names (steps/_image.py) in words; hard gates reject, the others only rank
export const GATES: Record<string, string> = {
  one_component: "one main subject",
  margin: "subject stays inside the image",
  size: "subject fills the frame",
  resolution: "enough image detail",
  uniform_background: "simple background",
  shading: "visible shading",
  few_semi_transparent: "clean edges",
};

// validate's checklist items (steps/validate.py) in words, with what the number means
export const CHECKS: Record<string, string> = {
  manifold: "non-manifold edges",
  watertight: "open (boundary) edges",
  single_part: "separate parts",
  no_tunnels: "holes through the surface",
  no_degenerate_faces: "degenerate faces",
  has_uvs: "has UVs",
  uvs_in_0_1: "UVs inside 0–1",
  uv_overlap: "UV overlap, % of the texels",
  triangle_budget: "triangles",
  scale_metres: "height (m)",
  self_intersection: "surface folded through itself, % of samples",
  parts_apart: "a part inside another, % of its points",
  size_drift_pct: "size off the brief's, % (height or long side)",
  // a sprite sheet (M53)
  uniform_cells: "cell sizes",
  hard_alpha: "half-transparent pixels",
  palette: "colours",
  stray_pixels: "single stray pixels",
  sheet_size: "sheet size (px)",
  animations: "animations",
  seamless: "edges meet when repeated (1.10 or less)", // M54: a tile
  layers: "layers", // M55
  far_layer_whole: "the farthest layer's see-through share",
};

/** M71: the tools of a tab (the strip under the tabs), each with the rule that enables it. `ok` takes the shown version
 * and its lineage; a 2D tool's rule needs the 2D panels' helpers, so it reads `f2d` (pixel/Panels.tsx `tools2D`).
 * `needs` completes "Needs …" and is the title of a tool that does not fit the shown version. */
export type ToolCtx = { versions: Meta[]; v: Meta; f2d?: { on: Record<string, boolean>; rec: string } };
export type Tool = { id: string; label: string; task: string; what: string; needs: string; ok: (c: ToolCtx) => boolean; kind?: "2d"; sep?: boolean };
const stage = (c: ToolCtx) => phaseOf(c.versions, c.v);
const mesh = (c: ToolCtx) => hasGlb(c.v) && stage(c) <= 2 && c.v.step !== "validate" && c.v.step !== "export"; // a model the recipe works on
// the mesh the character tools act on: a check or an export of one shows the version it was made from
const base = (c: ToolCtx) => {
  const s = source(c.versions, c.v);
  return s && hasGlb(s) ? s : null;
};
const rigAs = (c: ToolCtx) => {
  const s = base(c);
  return s && stage(c) >= 2 && stage(c) <= 4 ? actsAs(s) : "";
};
const animated = (c: ToolCtx) => ["anim", "anim_edit"].includes(rigAs(c));
const d2 = (id: string) => (c: ToolCtx) => !!c.f2d?.on[id];
const brief = (c: ToolCtx) => c.v.step === "brief" || c.v.step === "concepts";
export const TOOLS: Tool[] = [
  { id: "describe", label: "Describe", task: "reference", what: "Change the description, then make four new concept images from it.", needs: "a description", ok: brief },
  { id: "concepts", label: "Concepts", task: "reference", what: "Four pictures from your description, the best-looking first.", needs: "a description", ok: brief },
  {
    id: "prep",
    label: "Remove background",
    task: "reference",
    what: "Cut out your subject, so the 3D model matches your picture.",
    needs: "a picture",
    ok: (c) => hasImage(c.v) && !hasGlb(c.v) && !["export", "concepts", "prep"].includes(c.v.step),
  },
  { id: "generate", label: "Generate 3D", task: "generate", what: "Make a 3D model from this cut-out reference.", needs: "a cut-out reference", ok: (c) => c.v.step === "prep" },
  {
    id: "variation",
    label: "Another variation",
    task: "generate",
    what: "The same settings with a new seed: another model from the same picture.",
    needs: "a generated model",
    ok: (c) => c.v.step === "trellis2" && ancestors(c.versions, c.v).some((a) => a.step === "prep"),
  },
  { id: "prepare", label: "Prepare model", task: "prepare", what: "Simplify and texture the model for your project, all the steps in a row.", needs: "a model", ok: mesh },
  { id: "clean", label: "Clean", task: "prepare", what: "Close holes, drop loose bits, stand it upright and set its size.", needs: "a model", ok: mesh },
  { id: "remesh", label: "Simplify", task: "prepare", what: "Fewer triangles, or quads, for your game.", needs: "a model", ok: mesh },
  { id: "uv", label: "Unwrap", task: "prepare", what: "Lay the surface out flat so it can carry a texture.", needs: "a model", ok: mesh },
  {
    id: "bake",
    label: "Bake",
    task: "prepare",
    what: "Bake the colours into one texture set.",
    needs: "a generated or textured model",
    ok: (c) => mesh(c) && bakeable(c.versions, c.v),
  },
  {
    id: "face",
    label: "Face from image",
    task: "prepare",
    what: "Copy the face from your picture onto the texture, as sharp as the picture.",
    needs: "a textured, generated model",
    ok: (c) => (c.v.step === "bake" || c.v.step === "project") && generated(c.versions, c.v),
  },
  {
    id: "detail",
    label: "Texture detail",
    task: "prepare",
    what: "Redraw the texture's detail from zoomed views, or a part you paint on the model.",
    needs: "a textured model",
    ok: (c) => TEXTURED.has(c.v.step) && hasGlb(c.v),
  },
  { id: "parts", label: "Parts", task: "prepare", what: "Name the pieces of a model, such as a helmet or a shield.", needs: "a prepared model", ok: (c) => stage(c) === 2 && hasGlb(c.v) && c.v.step !== "export" },
  {
    id: "fit",
    label: "Fit skeleton",
    task: "character",
    what: "Add a skeleton to a character or an animal.",
    needs: "a prepared model without a skeleton",
    ok: (c) => !!base(c) && stage(c) === 2 && !base(c)!.outputs.skeleton,
  },
  { id: "skeleton", label: "Edit skeleton", task: "character", what: "Move the joints, or the markers the skeleton is fitted from.", needs: "a fitted skeleton", ok: (c) => rigAs(c) === "rig_fit" },
  { id: "attach", label: "Attach to skeleton", task: "character", what: "Attach the model to the skeleton, so it moves with it.", needs: "a fitted skeleton", ok: (c) => rigAs(c) === "rig_fit" },
  { id: "clips", label: "Ready-made clips", task: "character", sep: true, what: "Choose animations from the ready-made pack and add them to the model.", needs: "a skinned model", ok: (c) => rigAs(c) === "rig_skin" },
  { id: "text", label: "From a sentence", task: "character", what: "Describe a movement and generate it.", needs: "a skinned model", ok: (c) => rigAs(c) === "rig_skin" },
  { id: "video", label: "From a video", task: "character", what: "Turn a video of a person into a motion.", needs: "a skinned model", ok: (c) => rigAs(c) === "rig_skin" },
  { id: "file", label: "From a file", task: "character", what: "Use an animation made elsewhere.", needs: "a skinned model", ok: (c) => rigAs(c) === "rig_skin" },
  { id: "connect", label: "Connect clips", task: "character", what: "Make the transition that leads from one clip into another.", needs: "an animated model", ok: animated },
  { id: "editanim", label: "Edit animation", task: "character", what: "Correct poses, trim, loop or smooth a clip on a timeline under the view.", needs: "an animated model", ok: animated },
  {
    id: "sprites",
    label: "Sprites",
    task: "character",
    sep: true,
    what: "Render the model, or its animations, as sprites for a 2D game.",
    needs: "an animated model or a prepared still model",
    ok: (c) => spritesFrom(c.versions, c.v) || is2DVersion(c.v),
  },
  { id: "check", label: "Check", task: "finish", what: "Check the model before you export it.", needs: "a prepared model", ok: (c) => c.v.step === "validate" || (hasGlb(c.v) && stage(c) >= 2 && c.v.step !== "export") },
  {
    id: "export",
    label: "Export",
    task: "finish",
    what: "Save the files for your game engine, with lower detail levels, collision, credits and licences.",
    needs: "a prepared model",
    ok: (c) => c.v.step === "export" || c.v.step === "validate" || (!!base(c) && stage(c) >= 2),
  },
  // the 2D track (Actions2D's sections)
  { id: "pictures", label: "Pictures", task: "picture", kind: "2d", what: "Pictures from your description.", needs: "a description", ok: d2("pictures") },
  { id: "redraw", label: "Upscale or redraw", task: "picture", kind: "2d", what: "A bigger copy of the picture, or part of it drawn again.", needs: "a picture (not pixel art, sprites, a tile or layers)", ok: d2("redraw") },
  { id: "pixelize", label: "Pixelize", task: "pixelize", kind: "2d", what: "Turn the picture into pixel art on a grid, with a palette of your choice.", needs: "a picture", ok: d2("pixelize") },
  { id: "recolour", label: "Recolour", task: "pixelize", kind: "2d", what: "Swap the palette of this pixel art.", needs: "pixel art", ok: d2("recolour") },
  { id: "animate", label: "Animate it", task: "pixelize", kind: "2d", what: "Make a 3D character from this picture, to animate and draw back as sprites.", needs: "a still picture of a character", ok: d2("animate") },
  { id: "layers", label: "Layers", task: "layers", kind: "2d", what: "Split the picture into parallax layers, far to near.", needs: "a picture", ok: d2("layers") },
  { id: "tile", label: "Tiles", task: "tiles", kind: "2d", what: "A seamless tile from a description.", needs: "a description", ok: d2("tile") },
  { id: "editor", label: "Edit the pixels", task: "edit", kind: "2d", what: "Draw on the frames in whole pixels with the version's colours.", needs: "pixel art or sprites", ok: d2("editor") },
  { id: "finish2d", label: "Check and export", task: "finish", kind: "2d", what: "Check the sprites, a tile or the layers, then save them for your game engine.", needs: "pixel art, layers or a tile", ok: d2("finish2d") },
];
const is2DVersion = (v: Meta) => !!(v.outputs.sheet && v.outputs.data) || !!v.outputs.tile || !!v.outputs.layers;

/** The tool the shown version asks for next (what the panel offered first before M71): its dot, and the strip's default. */
export function recommended(c: ToolCtx): string {
  const { versions, v } = c;
  if (c.f2d) return c.f2d.rec;
  const st = actsAs(v);
  if (st === "brief" || st === "concepts") return "concepts";
  if (is2DVersion(v)) return "sprites"; // a sheet in a 3D asset: its own panel
  if (v.step === "validate") return "check";
  if (v.step === "export") return "export";
  if (hasImage(v) && !hasGlb(v)) return v.step === "prep" ? "generate" : "prep";
  if (["motion", "mocap", "import_motion"].includes(v.step) || !hasGlb(v)) return "";
  // a tool's own result opens on that tool (a parts, face or texture-detail run, to look at it or go on)
  const own: Record<string, string> = { parts: "parts", project: "face", refine: "detail" };
  if (own[v.step]) return own[v.step];
  if (RECIPE.some(([s]) => s === v.step)) return prepState(versions, v).state === "completed" ? "export" : "prepare";
  const s = stage(c);
  if (s < 2) return "prepare";
  if (st === "rig_fit") return "attach";
  if (st === "rig_skin") return "clips";
  if (st === "anim" || st === "anim_edit") return "editanim";
  return "check";
}

/** The tab (index into `tasks`) a tool lives in. */
export const tabOfTool = (tasks: Task[], id: string) => tasks.findIndex((t) => t.id === TOOLS.find((x) => x.id === id)?.task);

/** How to get the input for a tool; shown when its tab is opened on an unsuitable version. */
export function toolHelp(tool: Tool): string {
  if (["editanim", "connect"].includes(tool.id)) return "Add an animation in Character using Ready-made clips, From a sentence, From a video or From a file. You can also open an animated version in History.";
  if (["clips", "text", "video", "file"].includes(tool.id)) return "Choose a skinned model in History. To make one, use Fit skeleton, then Attach to skeleton in Character. To add more clips to an animated model, return to its skinned version first.";
  if (["skeleton", "attach"].includes(tool.id)) return "Use Fit skeleton in Character first, then open its Skeleton version in History.";
  if (tool.id === "fit") return "Choose a prepared model without a skeleton in History. On a raw generated model, use Prepare model first.";
  if (tool.id === "face" || tool.id === "detail") return "Bake the model's textures in Prepare, then open the textured model in History. Face from image also needs the original generated reference.";
  if (tool.id === "bake") return "Choose a generated model or an imported model with materials in History, before rigging or animation. Use Unwrap in Prepare to create its UVs first.";
  if (tool.task === "prepare") return "Choose a model version from before rigging or animation in History. If you have only a picture, generate or import a 3D model first.";
  if (tool.task === "reference" || tool.id === "pictures" || tool.id === "tile") return "Choose the original picture or description in History. You can start a new asset from a picture or a description if this asset has neither.";
  if (tool.task === "generate") return "Choose a cut-out reference in History. To make one, select a concept picture or upload your own, then use Remove background in Reference.";
  if (tool.id === "sprites") return "Choose an animated character or a prepared still model in History, then render its sprites.";
  if (tool.id === "editor" || tool.id === "recolour") return "Use Pixelize to make pixel art, or render Sprites from a model, then open that version in History.";
  if (tool.id === "redraw" || tool.id === "pixelize" || tool.id === "layers" || tool.id === "animate") return "Choose the original picture in History. A sprite sheet, tile, depth map or layered result may need its earlier picture instead.";
  if (tool.id === "finish2d") return "Create pixel art, a sprite sheet, a seamless tile or parallax layers first, then select that version in History.";
  return "Use Prepare model to finish the model, then choose its prepared version in History to check and export it.";
}
