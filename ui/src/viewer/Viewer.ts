// The 3D viewport (ARCHITECTURE.md §3). Imperative; React only calls load()/setViewMode()/setWireframe()/frame().
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { ViewHelper } from "three/addons/helpers/ViewHelper.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { clone as cloneSkinned } from "three/addons/utils/SkeletonUtils.js";
import { ikChain } from "../anim/clip";

export const VIEW_MODES = ["pbr", "albedo", "normal", "ao", "roughness", "metallic", "uv"] as const;
export type ViewMode = (typeof VIEW_MODES)[number];
export type Stats = { triangles: number; vertices: number; meshes: number; materials: number; height: number; bones: string[]; clips: string[] };
export type Vec3 = [number, number, number];
export type Joint = { name: string; parent: string | null; position: Vec3; twist?: Vec3 };
/** The skeleton editor (M22b): a click on a joint selects it (shift adds or removes); a finished gizmo gesture reports the new
 * position and/or twist of every joint it moved (the selected ones, and for a bend everything that hangs off them). */
export type SkeletonEdit = {
  mode: "translate" | "rotate";
  selected: string[]; // the active joint first
  onSelect: (name: string, add: boolean) => void;
  onJoints: (changes: Moved) => void;
};

/** M37: the animation editor's pose mode: the skeleton drawn on the moving model, a click near a joint selects its bone, the
 * rotate gizmo (the bone's own axes) turns it; a finished turn reports the bone's new local rotation (x, y, z, w).
 * M63: the skeleton drawn as Blender draws an armature (an octahedron per bone, head to tail, turning with the bone's roll;
 * dashed lines to a parent it does not join), the IK controls' target and pole handles. */
export type PoseEdit = {
  selected: string | null; // a bone's three.js name
  keyed?: string[]; // bones with a correction key at this frame: drawn in the key colour
  // M38: turn the bone; move it (the hips); drag it, `chain` bones above it solving (IK drag, keyed as turns);
  // M63 target: drag its IK control's target (or, `handle` pole, swing the knee/elbow)
  mode?: "rotate" | "move" | "ik" | "target";
  chain?: number; // IK drag's chain: the bones above the dragged one that turn (2 by default)
  ik?: IkShow[];
  handle?: "target" | "pole";
  onSelect: (bone: string) => void;
  onPose: (bone: string, q: Q4) => void;
  onMove?: (bone: string, from: Vec3, to: Vec3) => void; // its local position before and after
  onIK?: (turns: Record<string, Q4>) => void; // the bone and the chain above it: their new local turns
  onHandle?: (bone: string, handle: "target" | "pole") => void; // a click on an IK control's handle
  onTarget?: (bone: string, handle: Vec3, shown: Vec3) => void; // a target dragged: the handle and the bone as shown, in the control's space
  onSwivel?: (bone: string, degrees: number) => void; // a pole dragged: how far the knee/elbow swung about the chain
};
/** M63: an IK control as the viewer draws it: three.js names; `p` the target in `space`'s frame (null: skeleton space)
 * at this frame, null when no key weighs on it here (its handle then waits at the bone); `at` where the layer puts the
 * bone at this frame, in the same frame (the handle is drawn as far from the bone as `p` is from `at`). */
export type IkShow = { bone: string; chain: string[]; space: string | null; p: Vec3 | null; at: Vec3 | null };
type Q4 = [number, number, number, number];
// a Blender octahedral bone: head at 0, tail at +Y 1, the waist a tenth of the way up and a tenth wide
function octahedron(): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0, 0, 1, 0, 0.1, 0.1, 0, 0, 0.1, 0.1, -0.1, 0.1, 0, 0, 0.1, -0.1], 3));
  g.setIndex([0, 3, 2, 0, 4, 3, 0, 5, 4, 0, 2, 5, 1, 2, 3, 1, 3, 4, 1, 4, 5, 1, 5, 2]);
  return g;
}
const BONE = { solid: 0x6a707a, edge: 0xb4bac4 }; // Blender's pose mode: grey, the active bone blue, IK yellow, keys orange
const ACTIVE = { solid: 0x3aa0ff, edge: 0xb8eeff };
const IK = { solid: 0xc8a020, edge: 0xffe070 };
const KEYED = 0xf0a030;

const V = (p: Vec3) => new THREE.Vector3(...p);
const arr = (v: THREE.Vector3): Vec3 => [v.x, v.y, v.z];
const wq = (o: THREE.Object3D) => o.getWorldQuaternion(new THREE.Quaternion()).toArray() as Q4;
const wp = (o: THREE.Object3D) => o.getWorldPosition(new THREE.Vector3()).toArray() as Vec3;
export type Moved = Record<string, { position: Vec3; twist?: Vec3 }>;
const boneLines = (joints: Joint[]) => joints.flatMap((j) => (at(joints, j.parent ?? "") ? [...at(joints, j.parent!)!.position, ...j.position] : []));
// a twist tick from the middle of each bone (the hands' longer)
function twistTicks(joints: Joint[], size: number): number[] {
  return joints.flatMap((j) => {
    const c = mainChild(joints, j.name);
    if (!c) return [];
    const a = V(j.position).add(V(c.position)).multiplyScalar(0.5);
    return [...arr(a), ...arr(a.addScaledVector(twistOf(joints, j.name), (/Hand$/.test(j.name) ? 0.05 : 0.025) * size))];
  });
}
// a bright depth-tested pass and a dim see-through one: what is behind the surface reads dim, what is in front solid
const overlayLines = (geo: THREE.BufferGeometry, color: number) =>
  [true, false].map((depth) =>
    Object.assign(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color, depthTest: depth, transparent: !depth, opacity: depth ? 1 : 0.35 })), {
      renderOrder: 10,
      frustumCulled: false,
    }),
  );
const dropLine = (p: Vec3, floor: number) => [p[0], p[1], p[2], p[0], floor, p[2]];
// A quad mesh (remesh, Topology: Quads) keeps its quads as triangle pairs in the GLB: Blender's exporter writes a quad's
// two triangles one after the other, sharing one edge (its diagonal). The quad's outline is the pair's edges used once.
// null when the geometry is not indexed or fewer than 90 % of the pairs share an edge: not a quad mesh after all, the
// triangle wireframe then. After mr-mak-workspace's viewer (MIT), which paired by a shared first index instead.
function quadOutline(geo: THREE.BufferGeometry): THREE.BufferGeometry | null {
  const index = geo.index;
  const pos = geo.attributes.position as THREE.BufferAttribute;
  if (!index || !pos) return null;
  const tris = Math.floor(index.count / 3);
  const out: number[] = [];
  const push = (u: number, v: number) => out.push(pos.getX(u), pos.getY(u), pos.getZ(u), pos.getX(v), pos.getY(v), pos.getZ(v));
  const tri = (t: number) => [index.getX(3 * t), index.getX(3 * t + 1), index.getX(3 * t + 2)];
  let paired = 0;
  for (let t = 0; t < tris; ) {
    const a = tri(t);
    const b = t + 1 < tris ? tri(t + 1) : [];
    const shared = a.filter((i) => b.includes(i));
    const pair = shared.length === 2; // else a stray triangle: drawn alone, the next one may still start a pair
    if (pair) paired++;
    for (const x of pair ? [a, b] : [a])
      for (let k = 0; k < 3; k++) {
        const u = x[k];
        const v = x[(k + 1) % 3];
        if (!pair || !(shared.includes(u) && shared.includes(v))) push(u, v); // the diagonal stays out
      }
    t += pair ? 2 : 1;
  }
  if (paired < 0.9 * Math.floor(tris / 2)) return null; // ponytail: one number[] for the lines; a worker if a 1M-quad mesh stalls the click
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(out, 3));
  return g;
}
const MAIN: Record<string, string> = { Hips: "Spine", Spine2: "Neck", LeftHand: "LeftHandMiddle1", RightHand: "RightHandMiddle1" }; // else the first child
const at = (joints: Joint[], n: string) => joints.find((j) => j.name === n);
const mainChild = (joints: Joint[], n: string) => {
  const k = joints.filter((j) => j.parent === n);
  return k.find((j) => j.name === MAIN[n]) ?? k[0];
};
// the bone's X: toward its main child; a leaf continues its parent's direction
function boneAxis(joints: Joint[], n: string): THREE.Vector3 {
  const j = at(joints, n)!;
  const c = mainChild(joints, n);
  const par = at(joints, j.parent ?? "");
  const d = c ? V(c.position).sub(V(j.position)) : par ? V(j.position).sub(V(par.position)) : new THREE.Vector3();
  return d.lengthSq() ? d.normalize() : new THREE.Vector3(0, 1, 0);
}
// the direction a bone's front faces: its own, else the retarget's default (forward; up for the feet; the hand's for forearm and fingers)
function twistOf(joints: Joint[], n: string): THREE.Vector3 {
  const own = at(joints, n)?.twist ?? (/^(Left|Right)(ForeArm|Hand)/.test(n) ? at(joints, n.replace(/^(Left|Right).*/, "$1Hand"))?.twist : undefined);
  if (own) return V(own).normalize();
  const dir = (a: string, b: string) => V(at(joints, a)?.position ?? [0, 0, 0]).sub(V(at(joints, b)?.position ?? [0, 0, 0]));
  if (/Foot|ToeBase/.test(n)) {
    const up = dir("Head", "Hips");
    return up.lengthSq() ? up.normalize() : new THREE.Vector3(0, 1, 0);
  }
  const f = dir("LeftToeBase", "LeftFoot").add(dir("RightToeBase", "RightFoot")).setY(0); // forward: where the toes point
  return f.lengthSq() > 1e-12 ? f.normalize() : new THREE.Vector3(0, 0, 1);
}
// the bone's own frame: X along the bone, Y the twist made perpendicular, Z = X x Y
function boneFrame(joints: Joint[], n: string): THREE.Quaternion {
  const x = boneAxis(joints, n);
  const perp = (v: THREE.Vector3) => v.addScaledVector(x, -v.dot(x));
  let y = perp(twistOf(joints, n));
  if (y.lengthSq() < 1e-8) y = perp(new THREE.Vector3(...(Math.abs(x.y) < 0.9 ? [0, 1, 0] : [1, 0, 0])));
  y.normalize();
  return new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(x, y, x.clone().cross(y)));
}

// AO / roughness / metallic: one channel of the packed ORM texture (glTF: R = AO, G = rough, B = metal).
const ORM_SHADER = {
  vertexShader: `#include <skinning_pars_vertex>
    varying vec2 vUv;
    void main() {
      vUv = uv;
      #include <skinbase_vertex>
      #include <begin_vertex>
      #include <skinning_vertex>
      #include <project_vertex>
    }`,
  fragmentShader: `uniform sampler2D map; uniform bool hasMap; uniform int channel; uniform float value;
    varying vec2 vUv;
    void main() { float v = hasMap ? texture2D(map, vUv)[channel] : value; gl_FragColor = vec4(vec3(v), 1.0); }`,
};

function checkerTexture(): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const g = c.getContext("2d")!;
  for (let y = 0; y < 8; y++)
    for (let x = 0; x < 8; x++) {
      g.fillStyle = (x + y) % 2 ? "#e8e8e8" : "#d0402b";
      g.fillRect(x * 32, y * 32, 32, 32);
    }
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export class Viewer {
  loaded = false; // read by the Playwright smoke test through window.__viewer
  stats: Stats = { triangles: 0, vertices: 0, meshes: 0, materials: 0, height: 0, bones: [], clips: [] };
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(40, 1, 0.01, 1000);
  private controls: OrbitControls;
  private helper: ViewHelper;
  private clock = new THREE.Clock();
  private model: THREE.Object3D | null = null;
  private mixer: THREE.AnimationMixer | null = null; // M21: the loaded GLB's clips
  private clips: THREE.AnimationClip[] = [];
  private originals = new WeakMap<THREE.Mesh, THREE.Material | THREE.Material[]>();
  private checker = checkerTexture();
  private mode: ViewMode = "pbr";
  private tint: string[] | null = null; // part names: each such node gets its own flat colour
  private wireframe = false;
  private quads = false; // the shown model is a quad mesh: Wireframe draws its quads (mesh-gates, 2026-10-12)
  private quadLines = new Map<THREE.Mesh, THREE.LineSegments | null>(); // per mesh, built once; null = not a quad mesh
  quadEdges = 0; // how many quad edges the wireframe draws now (the tests read it)
  private loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
  private pmrem: THREE.PMREMGenerator;
  private resizer = new ResizeObserver(() => this.resize());
  private downAt: [number, number] | null = null;
  private onPointerDown = (e: PointerEvent) => (this.downAt = [e.clientX, e.clientY]);
  private onPointerUp = (e: PointerEvent) => {
    this.helper.handleClick(e);
    const d = this.downAt;
    this.downAt = null;
    const click = d && Math.hypot(e.clientX - d[0], e.clientY - d[1]) < 5; // a click, not an orbit
    if ((this.onMarker || this.edit) && click) this.pick(e);
    else if (this.pose && click && !this.poseTc?.axis) this.pickBone(e);
  };
  // the skeleton overlay (M19): bones, joints, and the markers the user places
  private rig: THREE.Group | null = null;
  private markers = new Map<string, THREE.Mesh>();
  private selected: string | null = null;
  private onMarker: ((name: string, p: Vec3 | null) => void) | null = null;
  private raycaster = new THREE.Raycaster();
  // M68: a brush painting a mask in the texture's own layout (white = redrawn), shown on the model as a veil
  private brush: {
    canvas: HTMLCanvasElement;
    g: CanvasRenderingContext2D;
    tex: THREE.CanvasTexture;
    veils: THREE.Mesh[];
    radius: number;
    size: number; // the model's diagonal, metres
    down: boolean;
    last: [number, number] | null; // the pointer at the last stamp
    painted: boolean;
    rev: number; // counts the strokes and clears: an unchanged paint need not be uploaded again
  } | null = null;
  /** The brush erases instead of painting (the panel's Erase); Shift flips it either way. */
  paintErase = false;
  private onBrushDown = (e: PointerEvent) => {
    if (!this.brush || e.button !== 0 || !this.stamp(e.clientX, e.clientY, e.shiftKey !== this.paintErase)) return; // off the model: the orbit as ever
    this.brush.down = true;
    this.brush.last = [e.clientX, e.clientY];
    this.controls.enabled = false; // runs before the orbit's own listener (capture): it does not start
    this.canvas.setPointerCapture(e.pointerId);
  };
  private onBrushMove = (e: PointerEvent) => {
    const b = this.brush;
    if (!b?.down || !b.last) return;
    // stamps between the last pointer and this one, 4 px apart: a fast stroke stays a stroke
    const [x0, y0] = b.last;
    const n = Math.min(32, Math.max(1, Math.ceil(Math.hypot(e.clientX - x0, e.clientY - y0) / 4)));
    for (let i = 1; i <= n; i++) this.stamp(x0 + ((e.clientX - x0) * i) / n, y0 + ((e.clientY - y0) * i) / n, e.shiftKey !== this.paintErase);
    b.last = [e.clientX, e.clientY];
  };
  private onBrushUp = () => {
    if (!this.brush?.down) return;
    this.brush.down = false;
    this.brush.last = null;
    this.controls.enabled = true;
  };
  private edit: SkeletonEdit | null = null;
  private joints: Joint[] = [];
  private tc: TransformControls | null = null; // the gizmo, made on first use; it moves `proxy`, set in the selected bone's frame
  private proxy = new THREE.Object3D();
  private from = { p: new THREE.Vector3(), q: new THREE.Quaternion() }; // the proxy when the gesture began
  private cancelled = false;
  private xray = false;
  private xsaved = new WeakMap<THREE.Material, { transparent: boolean; opacity: number; depthWrite: boolean }>();
  private twin: ((name: string, p: Vec3) => [string, Vec3] | null) | null = null;
  private committed: Record<string, Vec3> = {}; // the markers as the panel holds them
  private live: { bones: THREE.BufferAttribute; ticks: THREE.BufferAttribute; drop: THREE.BufferAttribute; floor: number; size: number } | null = null; // the overlay's line attributes, rewritten while dragging
  private onKey = (e: KeyboardEvent) => {
    const tc = this.poseTc?.dragging ? this.poseTc : this.tc;
    if (e.key !== "Escape" || !tc?.dragging) return;
    tc.reset(); // back to where it began; the release then changes nothing
    this.cancelled = true;
  };
  private helperSkel: THREE.SkeletonHelper | null = null;
  private grid = new THREE.GridHelper(1, 40, 0x5a6068, 0x33383d); // the ground: under the model's lowest point, cells a 10th of its size
  private heat = new WeakMap<THREE.BufferGeometry, { orig?: THREE.BufferAttribute | THREE.InterleavedBufferAttribute; buf: THREE.BufferAttribute }>(); // setWeights: the geometry's own colours + the reusable heatmap
  // M37: the clip under the time bar, and the animation editor's pose mode
  private action: THREE.AnimationAction | null = null;
  private speed = 1;
  private bones: THREE.Bone[] = [];
  private pose: PoseEdit | null = null;
  // the armature overlay (M63): an octahedron per bone, the relationship lines, the IK handles; redrawn every frame
  private poseGroup: THREE.Group | null = null;
  private poseBones: { bone: THREE.Bone; mesh: THREE.Mesh; main: THREE.Bone | null; tip: THREE.Vector3 | null }[] = []; // tip: a leaf's tail in its own frame
  private poseTag: { name: string; sprite: THREE.Sprite } | null = null; // the selected bone's name
  private poseSize = 1;
  private ikGroup: { key: string; group: THREE.Group } | null = null; // the IK controls' handles, rebuilt when the controls change
  private poseTc: TransformControls | null = null;
  private posing = false; // a gizmo turn in progress: the mixer leaves the bones alone
  private poseFrom = new THREE.Vector3(); // the moved bone's local position when the drag began
  private ikTarget = new THREE.Object3D(); // the IK gizmo's handle: at the dragged bone (IK drag) or the control's target
  private poleHandle = new THREE.Object3D(); // the swivel gizmo's handle, at the knee/elbow's pole
  private poleStart = new THREE.Vector3();
  private shownStart = new THREE.Vector3(); // the dragged bone where it showed when the drag began (the preview moves it)
  private ikStart: THREE.Quaternion[] | null = null; // the chain's turns when the drag began
  private ikNow: THREE.Bone[] = []; // the chain being dragged, its top first
  private swivelNow = 0; // degrees the pole drag swung the chain
  private tails = new Map<string, THREE.Vector3>(); // each bone's tail in world space (picking)
  private targets = new Map<string, THREE.Vector3>(); // each IK control's target handle in world space, by its bone
  private poles = new Map<string, THREE.Vector3>(); // and its pole handle
  private path: { group: THREE.Group; pos: Float32Array; col: THREE.BufferAttribute; now: THREE.Points; frame: number } | null = null; // M38: the selected bone's track
  private ghosts: { root: THREE.Object3D; mixer: THREE.AnimationMixer; action: THREE.AnimationAction; frames: number }[] = []; // onion skins
  private dirty = true; // render on demand: set by anything that changes the picture
  private playing = false; // a clip runs: render every frame
  private token = 0; // the latest load() wins; earlier ones resolve to null and free what they loaded

  constructor(private canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2)); // laptop: 4K panels at 2x are enough
    this.renderer.autoClear = false; // the axis gizmo draws over the scene
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    // ponytail: procedural room light instead of a bundled HDRI; add RGBELoader + .hdr if looks matter
    this.pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = this.pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.background = new THREE.Color(0x1c1f22);
    this.grid.visible = false;
    this.scene.add(this.grid);
    this.camera.position.set(1.5, 1, 2.5);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.addEventListener("change", () => (this.dirty = true));
    this.helper = new ViewHelper(this.camera, canvas);
    canvas.addEventListener("pointerdown", this.onPointerDown);
    canvas.addEventListener("pointerup", this.onPointerUp);
    this.resizer.observe(canvas);
    this.renderer.setAnimationLoop(() => this.render());
  }

  /** null when a later load() or clear() superseded this one. */
  async load(url: string): Promise<Stats | null> {
    const token = ++this.token;
    this.loaded = false;
    let gltf;
    try {
      gltf = await this.loader.loadAsync(url);
    } catch (e) {
      if (token !== this.token) return null;
      throw e;
    }
    if (token !== this.token) {
      this.free(gltf.scene);
      return null;
    }
    this.drop();
    this.model = gltf.scene;
    this.clips = gltf.animations ?? [];
    this.mixer = this.clips.length ? new THREE.AnimationMixer(this.model) : null;
    this.scene.add(this.model);
    this.stats = { triangles: 0, vertices: 0, meshes: 0, materials: 0, height: 0, bones: [], clips: [] };
    const mats = new Set<THREE.Material>();
    this.model.traverse((o) => {
      if (!(o as THREE.Mesh).isMesh) return;
      const mesh = o as THREE.Mesh;
      const g = mesh.geometry;
      this.originals.set(mesh, mesh.material);
      this.stats.meshes++;
      for (const m of [mesh.material].flat()) mats.add(m);
      this.stats.vertices += g.attributes.position.count;
      this.stats.triangles += (g.index ? g.index.count : g.attributes.position.count) / 3;
      const sk = (mesh as THREE.SkinnedMesh).skeleton;
      if (sk && !this.stats.bones.length) {
        this.stats.bones = sk.bones.map((b) => b.name);
        this.bones = sk.bones;
        for (const b of this.bones) b.userData.rest = b.position.toArray(); // the node's own translation, before any clip
      }
    });
    this.stats.materials = mats.size;
    this.stats.height = new THREE.Box3().setFromObject(this.model).getSize(new THREE.Vector3()).y;
    this.frameModel();
    this.setViewMode(this.mode);
    this.playing = false;
    this.stats.clips = this.clips.map((c) => c.name);
    this.loaded = true;
    return this.stats;
  }

  /** M42: two viewers share one camera (position + target) while linked; returns the unlink. */
  link(other: Viewer): () => void {
    let busy = false;
    const copy = (a: Viewer, b: Viewer) => () => {
      if (busy) return;
      busy = true;
      b.camera.position.copy(a.camera.position);
      b.controls.target.copy(a.controls.target);
      b.controls.update();
      b.dirty = true;
      busy = false;
    };
    const ab = copy(this, other), ba = copy(other, this);
    this.controls.addEventListener("change", ab);
    other.controls.addEventListener("change", ba);
    ab();
    return () => {
      this.controls.removeEventListener("change", ab);
      other.controls.removeEventListener("change", ba);
    };
  }

  clear() {
    this.token++;
    this.drop();
    this.setRig(null, null); // a cleared skeleton view kept its rig and gizmo in the scene (UI-008)
    this.loaded = false;
    this.dirty = true;
  }

  /** Unmount: stop rendering and release the GPU resources and listeners this viewer owns. */
  dispose() {
    this.clear();
    this.renderer.setAnimationLoop(null);
    this.resizer.disconnect();
    this.canvas.removeEventListener("pointerdown", this.onPointerDown);
    this.canvas.removeEventListener("pointerup", this.onPointerUp);
    this.setRig(null, null);
    this.poseTc?.dispose();
    this.tc?.dispose(); // the skeleton gizmo, made on first use (UI-007: it kept its pointer listeners)
    this.controls.dispose();
    this.helper.dispose();
    this.scene.environment?.dispose();
    this.pmrem.dispose();
    this.checker.dispose();
    this.renderer.dispose();
  }

  /** What the camera frames now, as a refine view window (steps/_views.ortho: yaw 0 looks at +Z from +Z and turns
   * towards +X, pitch negative looks down, `extent` metres across the square view, centred on the orbit target). */
  view(): { yaw: number; pitch: number; center: [number, number, number]; extent: number } {
    const t = this.controls.target;
    const d = this.camera.position.clone().sub(t);
    const dist = d.length() || 1;
    const deg = (r: number) => Math.round((r * 180) / Math.PI);
    const height = 2 * dist * Math.tan((this.camera.fov * Math.PI) / 360); // what the view shows at the target
    return {
      yaw: deg(Math.atan2(d.x, d.z)),
      pitch: 0 - deg(Math.asin(Math.max(-1, Math.min(1, d.y / dist)))), // 0 - …: never -0
      center: [t.x, t.y, t.z].map((x) => Math.round(x * 1e4) / 1e4) as [number, number, number],
      extent: Math.round(Math.min(height, height * this.camera.aspect) * 1e4) / 1e4,
    };
  }

  /** M68: painting on (a left drag on the model paints, with Shift it erases; a drag beside it still orbits) or
   * off. `radius`: the brush's radius as a share of the model's size. The paint stays until clearPaint() or another
   * model. */
  setPaint(on: boolean, radius = 0.03): boolean {
    if (on && this.model && !this.brush) {
      // the canvas has the base colour texture's size, at most 2048 (the step resizes it to the atlas); glTF textures
      // are not flipped: (u, v) is the canvas' (x, y)
      let size = 1024;
      this.model.traverse((o) => {
        const map = ((o as THREE.Mesh).material as THREE.MeshStandardMaterial | undefined)?.map?.image as { width?: number } | undefined;
        if (map?.width) size = Math.min(map.width, 2048);
      });
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = size;
      const g = canvas.getContext("2d")!;
      g.fillStyle = "#000";
      g.fillRect(0, 0, size, size);
      const tex = new THREE.CanvasTexture(canvas);
      tex.flipY = false;
      tex.generateMipmaps = false; // re-uploaded at every stamp: no mip chain to rebuild
      tex.minFilter = THREE.LinearFilter;
      const veils: THREE.Mesh[] = [];
      this.model.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (!mesh.isMesh || mesh.userData.veil || !mesh.geometry.attributes.uv) return;
        const mat = new THREE.MeshBasicMaterial({ color: 0xff2bd6, transparent: true, opacity: 0.6, alphaMap: tex, depthWrite: false });
        Object.assign(mat, { polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 });
        const veil = new THREE.Mesh(mesh.geometry, mat);
        veil.userData.veil = true;
        veil.raycast = () => undefined; // the brush hits the model under it
        mesh.add(veil);
        veils.push(veil);
      });
      const size3 = new THREE.Box3().setFromObject(this.model).getSize(new THREE.Vector3()).length();
      this.brush = { canvas, g, tex, veils, radius, size: size3, down: false, last: null, painted: false, rev: 0 };
    }
    if (this.brush) this.brush.radius = radius;
    const c = this.canvas;
    const ends = ["pointerup", "pointercancel", "lostpointercapture"] as const;
    if (on && this.brush) {
      c.addEventListener("pointerdown", this.onBrushDown, { capture: true });
      c.addEventListener("pointermove", this.onBrushMove);
      ends.forEach((ev) => c.addEventListener(ev, this.onBrushUp));
    } else {
      this.onBrushUp();
      c.removeEventListener("pointerdown", this.onBrushDown, { capture: true });
      c.removeEventListener("pointermove", this.onBrushMove);
      ends.forEach((ev) => c.removeEventListener(ev, this.onBrushUp));
    }
    this.dirty = true;
    return on && !!this.brush;
  }

  /** M68: nothing painted any more. */
  clearPaint() {
    if (!this.brush) return;
    const { g, canvas } = this.brush;
    g.fillStyle = "#000";
    g.fillRect(0, 0, canvas.width, canvas.height);
    this.brush.tex.needsUpdate = true;
    this.brush.painted = false;
    this.brush.rev++;
    this.dirty = true;
  }

  /** M68: changes with every stroke and clear (the panel uploads a paint once). */
  paintRev(): number {
    return this.brush?.rev ?? 0;
  }

  /** M68: whether anything is painted. */
  painted(): boolean {
    return !!this.brush?.painted;
  }

  /** M68: the painted mask as a PNG in the texture's layout (white = redrawn), or null when nothing is painted. */
  paintMask(): Promise<Blob | null> {
    const b = this.brush;
    if (!b?.painted) return Promise.resolve(null);
    return new Promise((done) => b.canvas.toBlob(done, "image/png"));
  }

  /** Paint (Shift: erase) a disc of the brush's radius round where the pointer meets the model; false: it missed. */
  private stamp(x: number, y: number, erase: boolean): boolean {
    const b = this.brush;
    if (!b || !this.model) return false;
    const r = this.canvas.getBoundingClientRect();
    this.raycaster.setFromCamera(new THREE.Vector2(((x - r.left) / r.width) * 2 - 1, -((y - r.top) / r.height) * 2 + 1), this.camera);
    const hit = this.raycaster.intersectObject(this.model, true).find((h) => h.uv && h.face);
    if (!hit) return false;
    // the brush's radius in the texture: metres times this face's texels per metre (its UV area over its 3D area)
    const mesh = hit.object as THREE.Mesh;
    const pos = mesh.geometry.attributes.position;
    const uv = mesh.geometry.attributes.uv;
    const { a, b: bi, c } = hit.face!;
    const P = [a, bi, c].map((i) => mesh.localToWorld(new THREE.Vector3(pos.getX(i), pos.getY(i), pos.getZ(i))));
    const U = [a, bi, c].map((i) => new THREE.Vector2(uv.getX(i), uv.getY(i)));
    const area3 = P[1].clone().sub(P[0]).cross(P[2].clone().sub(P[0])).length() / 2;
    const e1 = U[1].clone().sub(U[0]);
    const e2 = U[2].clone().sub(U[0]);
    const areaUv = Math.abs(e1.x * e2.y - e1.y * e2.x) / 2;
    const ruv = area3 > 1e-12 ? b.radius * b.size * Math.sqrt(areaUv / area3) : 0.01;
    const px = Math.max(1, ruv * b.canvas.width);
    b.g.fillStyle = erase ? "#000" : "#fff";
    b.g.beginPath();
    b.g.arc(hit.uv!.x * b.canvas.width, hit.uv!.y * b.canvas.height, px, 0, 2 * Math.PI);
    b.g.fill();
    b.tex.needsUpdate = true;
    b.painted ||= !erase;
    b.rev++;
    this.dirty = true;
    return true;
  }

  /** Frame the whole model, or look at it from the front (+Z), the side (+X) or the top (+Y). */
  frame(side?: "front" | "side" | "top") {
    if (!this.model) return;
    this.frameModel();
    this.dirty = true;
    if (!side) return;
    const box = new THREE.Box3().setFromObject(this.model);
    const dir = { front: [0, 0, 1], side: [1, 0, 0], top: [0, 1, 1e-4] }[side] as [number, number, number];
    const dist = box.getSize(new THREE.Vector3()).length() * 1.2 || 1;
    this.camera.position.copy(this.controls.target).add(new THREE.Vector3(...dir).normalize().multiplyScalar(dist));
  }

  /** Draw a skeleton over the model (drawn through it), with draggable markers when onMarker is given:
   * a click on a marker selects it (onMarker(name, null)); then a click on the model moves it to the
   * middle of the body under the cursor (onMarker(name, position)). */
  setRig(
    joints: Joint[] | null,
    markers: Record<string, Vec3> | null,
    onMarker?: (name: string, p: Vec3 | null) => void,
    selected?: string | null,
    label?: (name: string) => string,
    edit?: SkeletonEdit,
    twin?: (name: string, p: Vec3) => [string, Vec3] | null, // marker editor: where the mirrored marker goes while one is dragged
    outside?: string[], // joints the inside check flags: drawn red
  ) {
    if (this.rig) {
      this.scene.remove(this.rig);
      this.rig.traverse((o) => {
        const m = o as THREE.Mesh;
        m.geometry?.dispose();
        ((m.material as THREE.SpriteMaterial | undefined)?.map as THREE.Texture | undefined)?.dispose();
        (m.material as THREE.Material | undefined)?.dispose();
      });
    }
    this.rig = null;
    this.dirty = true;
    this.markers.clear();
    this.onMarker = onMarker ?? null;
    this.selected = selected ?? null;
    this.edit = edit ?? null;
    this.twin = twin ?? null;
    this.committed = markers ?? {};
    this.joints = joints ?? [];
    window.removeEventListener("keydown", this.onKey);
    if (!joints || !edit) this.tc?.detach();
    if (!joints) return;
    const size = this.model ? new THREE.Box3().setFromObject(this.model).getSize(new THREE.Vector3()).y || 1 : 1;
    const g = new THREE.Group();
    g.renderOrder = 10;
    const lines = new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(boneLines(joints), 3));
    this.live = null;
    g.add(...overlayLines(lines, 0xffd400));
    const dot = (p: Vec3, r: number, color: number, name?: string) => {
      const geo = new THREE.SphereGeometry(r * size, 12, 8);
      const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color }));
      const dim = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color, depthTest: false, transparent: true, opacity: 0.35 })); // the see-through pass, moves with m
      m.add(dim);
      m.position.set(...p);
      m.renderOrder = dim.renderOrder = 11;
      g.add(m);
      if (name) {
        m.userData.marker = dim.userData.marker = name; // the pick() hit list is shared with the markers
        this.markers.set(name, m);
      }
      return m;
    };
    for (const j of joints) {
      const color = edit?.selected.includes(j.name) ? 0xff8a00 : outside?.includes(j.name) ? 0xff2020 : 0x40ff70; // red: the inside check flags it
      dot(j.position, edit ? 0.01 : 0.006, color, edit ? j.name : undefined);
    }
    if (edit) this.editRig(g, size, label, lines.attributes.position as THREE.BufferAttribute);
    for (const [name, p] of Object.entries(markers ?? {})) {
      dot(p, 0.014, name === this.selected ? 0xff8a00 : 0x3aa0ff, name);
      if (name === this.selected && label) g.add(this.tag(label(name), p, size));
    }
    this.rig = g;
    this.scene.add(g);
    if (this.onMarker) this.markerGizmo();
  }

  // the editor's extras: a twist tick from the middle of each bone (the hands' longer), the gizmo at the selected joint
  private editRig(g: THREE.Group, size: number, label: ((name: string) => string) | undefined, bones: THREE.BufferAttribute) {
    for (const j of this.joints)
      if (/Hand$/.test(j.name) && j.name === this.selected && mainChild(this.joints, j.name)) {
        const t = twistTicks([j, mainChild(this.joints, j.name)!], size); // this hand's tick: its end is the palm label's place
        g.add(this.tag("palm", [t[3], t[4], t[5]], size));
      }
    const lines = new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(twistTicks(this.joints, size), 3));
    g.add(...overlayLines(lines, 0x00d4ff));
    const sel = this.selected ? at(this.joints, this.selected) : undefined;
    const floor = this.model ? new THREE.Box3().setFromObject(this.model).min.y : 0;
    // the active joint's plumb line to the ground
    const drop = new THREE.BufferGeometry().setAttribute(
      "position",
      new THREE.Float32BufferAttribute(sel ? dropLine(sel.position, floor) : [0, 0, 0, 0, 0, 0], 3),
    );
    g.add(
      Object.assign(new THREE.LineSegments(drop, new THREE.LineBasicMaterial({ color: 0xffffff, depthTest: false, transparent: true, opacity: 0.6 })), {
        renderOrder: 10,
        frustumCulled: false,
      }),
    );
    this.live = { bones, ticks: lines.attributes.position as THREE.BufferAttribute, drop: drop.attributes.position as THREE.BufferAttribute, floor, size };
    if (sel && label) g.add(this.tag(label(sel.name), sel.position, size));
    const tc = this.gizmo();
    if (!sel) return void tc.detach();
    this.proxy.position.copy(this.centroid(this.selection())); // the pivot of every gesture; the axes are the active bone's
    this.proxy.quaternion.copy(boneFrame(this.joints, sel.name));
    tc.setSpace("local");
    tc.setMode(this.edit!.mode);
    tc.attach(this.proxy);
  }

  // the gizmo, made on first use; it moves `proxy`
  private gizmo(): TransformControls {
    window.addEventListener("keydown", this.onKey);
    if (this.tc) return this.tc;
    const tc = (this.tc = new TransformControls(this.camera, this.canvas));
    this.scene.add(this.proxy, tc.getHelper());
    tc.addEventListener("change", () => (this.dirty = true));
    tc.addEventListener("dragging-changed", (e) => (this.controls.enabled = !e.value));
    tc.addEventListener("mouseDown", () => {
      this.cancelled = false;
      this.from.p.copy(this.proxy.position);
      this.from.q.copy(this.proxy.quaternion);
    });
    tc.addEventListener("objectChange", () => (this.edit ? this.preview(this.gesture()) : this.moveMarker(this.proxy.position)));
    tc.addEventListener("mouseUp", () => this.finish());
    return tc;
  }

  // marker editor: the gizmo (translate, world axes) at the selected marker
  private markerGizmo() {
    const tc = this.gizmo();
    const p = this.selected && this.committed[this.selected];
    if (!p) return void tc.detach();
    this.proxy.position.set(...p);
    this.proxy.quaternion.identity();
    tc.setSpace("world");
    tc.setMode("translate");
    tc.attach(this.proxy);
  }

  // the selected marker's sphere (and its mirrored twin's) at p, or back at the committed positions when p is null
  private moveMarker(p: THREE.Vector3 | null) {
    const n = this.selected;
    if (!n || !this.committed[n]) return;
    const to = p ? arr(p) : this.committed[n];
    this.markers.get(n)?.position.set(...to);
    const t = this.twin?.(n, to);
    if (t) this.markers.get(t[0])?.position.set(...(p ? t[1] : (this.committed[t[0]] ?? t[1])));
    this.dirty = true;
  }

  private selection = () => (this.edit?.selected ?? []).filter((n) => at(this.joints, n));
  private centroid = (names: string[]) => names.reduce((c, n) => c.add(V(at(this.joints, n)!.position)), new THREE.Vector3()).divideScalar(names.length || 1);

  // what a gesture (a shift dp, a turn dw about the pivot) does to the joints: the one place for the maths, shared by the live preview and the final report
  private delta(dp: THREE.Vector3, dw: THREE.Quaternion, mode: string, axis: string | null): Moved | null {
    const names = this.selection();
    if (!names.length) return null;
    const moved: Moved = {};
    if (mode === "translate") {
      if (dp.length() < 1e-7) return null;
      for (const n of names) moved[n] = { position: arr(V(at(this.joints, n)!.position).add(dp)) };
      return moved;
    }
    if (Math.abs(dw.w) > 1 - 1e-9) return null;
    const turn = (t?: Vec3) => t && arr(V(t).applyQuaternion(dw));
    if (axis === "X") {
      for (const n of names) moved[n] = { position: at(this.joints, n)!.position, twist: arr(twistOf(this.joints, n).applyQuaternion(dw)) }; // about the bone: the twists
      return moved;
    }
    const pivot = this.centroid(names); // a bend: the selected joints and all that hangs off them swing rigidly around the pivot
    const swing = (n: string) => {
      const j = at(this.joints, n)!;
      moved[n] = { position: arr(V(j.position).sub(pivot).applyQuaternion(dw).add(pivot)), twist: turn(j.twist) };
      this.joints.filter((c) => c.parent === n).forEach((c) => swing(c.name));
    };
    names.forEach(swing);
    return moved;
  }

  private gesture(): Moved | null {
    if (!this.tc?.dragging) return null;
    const { p: p0, q: q0 } = this.from;
    return this.delta(this.proxy.position.clone().sub(p0), this.proxy.quaternion.clone().multiply(q0.clone().invert()), this.tc.mode, this.tc.axis);
  }

  // the overlay follows the gesture in place (no setRig per frame); null puts it back to the committed joints
  private preview(moved: Moved | null) {
    if (!this.live) return;
    const js = this.joints.map((j) => (moved?.[j.name] ? { ...j, position: moved[j.name].position, twist: moved[j.name].twist ?? j.twist } : j));
    js.forEach((j) => this.markers.get(j.name)?.position.set(...j.position));
    const { bones, ticks, drop, floor, size } = this.live;
    bones.array.set(boneLines(js));
    ticks.array.set(twistTicks(js, size));
    const act = this.selected && at(js, this.selected);
    if (act) drop.array.set(dropLine(act.position, floor));
    bones.needsUpdate = ticks.needsUpdate = drop.needsUpdate = true;
    this.dirty = true;
  }

  // a gizmo gesture ended: report it as joint data (the panel owns the state and redraws)
  private finish() {
    if (!this.edit) {
      const n = this.selected;
      const p = this.proxy.position.clone();
      this.proxy.position.copy(this.from.p);
      if (!n || this.cancelled || p.distanceTo(this.from.p) < 1e-7) return this.moveMarker(null);
      return this.onMarker?.(n, arr(p));
    }
    const moved = this.cancelled ? null : this.gesture();
    if (!moved) {
      this.proxy.position.copy(this.from.p);
      this.proxy.quaternion.copy(this.from.q);
      return this.preview(null);
    }
    this.edit.onJoints(moved);
  }

  /** Playwright hooks: a joint sphere's position, a joint moved as the gizmo would, the whole selection shifted. */
  testJoint = (name: string) => this.markers.get(name)?.position.toArray() as Vec3 | undefined;
  testMoveJoint = (name: string, position: Vec3) => this.edit?.onJoints({ [name]: { position } });
  testMoveSelected = (d: Vec3) => this.edit?.onJoints(this.delta(V(d), new THREE.Quaternion(), "translate", null) ?? {});
  testSelection = () => this.edit?.selected;
  /** M37: a bone turned as the pose gizmo would (its new local rotation), and a pose-mode click on a bone. */
  testPose = (bone: string, q: [number, number, number, number]) => this.pose?.onPose(bone, q);
  testPickBone = (bone: string) => this.pose?.onSelect(bone);
  /** M38: a hips move and an IK drag as the gizmo would end them; the motion path's points and the ghosts. */
  testMove = (bone: string, to: Vec3) => this.pose?.onMove?.(bone, this.bones.find((b) => b.name === bone)!.position.toArray() as Vec3, to);
  testIK = (to: Vec3) => {
    const B = this.bones.find((b) => b.name === this.pose?.selected);
    const ch = B && this.chainOf(B, this.pose?.chain ?? 2);
    if (!ch) return;
    this.ikNow = ch;
    this.ikStart = ch.map((b) => b.quaternion.clone());
    this.ikTarget.position.set(...to);
    this.solveIK();
    this.pose?.onIK?.(Object.fromEntries(ch.map((b) => [b.name, b.quaternion.toArray() as Q4])));
  };
  /** M63: an IK control's target dragged to a world position, its pole swung by degrees, as the gizmo would end them;
   * the armature as drawn (each bone's head, tail and colour) and the handles. */
  testTarget = (bone: string, to: Vec3) => {
    const c = this.pose?.ik?.find((x) => x.bone === bone);
    if (c) this.pose?.onTarget?.(bone, ...this.targetIn(bone, V(to)));
  };
  testSwivel = (bone: string, degrees: number) => this.pose?.onSwivel?.(bone, degrees);
  testArmature = () => {
    this.drawPose();
    return this.poseBones.map(({ bone, mesh }) => ({
      bone: bone.name,
      head: arr(bone.getWorldPosition(new THREE.Vector3())),
      tail: arr(this.tails.get(bone.name)!),
      color: (mesh.material as THREE.MeshBasicMaterial).color.getHex(),
    }));
  };
  testHandles = () => ({ targets: Object.fromEntries([...this.targets].map(([b, p]) => [b, arr(p)])), poles: Object.fromEntries([...this.poles].map(([b, p]) => [b, arr(p)])) });
  testPath = () => (this.path ? Array.from(this.path.pos) : null);
  testGhosts = () => this.ghosts.map((g) => g.frames);
  worldOf = (bone: string) => this.bones.find((b) => b.name === bone)?.getWorldPosition(new THREE.Vector3()).toArray() as Vec3 | undefined;
  testMoveMarker = (name: string, position: Vec3) => this.onMarker?.(name, position);

  /** The model's lowest point: the ground the grid and the joints' height are measured from. */
  ground = () => (this.model ? new THREE.Box3().setFromObject(this.model).min.y : 0);

  /** Semi-transparent model, so a skeleton drawn inside it reads as inside; the materials' own values come back on off. */
  setXray(on: boolean) {
    this.xray = on;
    this.dirty = true;
    this.model?.traverse((o) => {
      if (!(o as THREE.Mesh).isMesh || o.userData.veil) return; // M68: the brush's veil is its own
      for (const m of [(o as THREE.Mesh).material].flat()) {
        if (on && !this.xsaved.has(m)) this.xsaved.set(m, { transparent: m.transparent, opacity: m.opacity, depthWrite: m.depthWrite });
        const was = this.xsaved.get(m);
        if (!was) continue;
        m.transparent = on || was.transparent;
        m.opacity = on ? 0.3 : was.opacity;
        m.depthWrite = on ? false : was.depthWrite;
        m.needsUpdate = true;
        if (!on) this.xsaved.delete(m);
      }
    });
  }

  // a name over the selected marker: which dot is the character's left shoulder isn't obvious
  private tag(text: string, p: Vec3, size: number): THREE.Sprite {
    const c = document.createElement("canvas");
    c.width = 512;
    c.height = 96;
    const g = c.getContext("2d")!;
    g.font = "bold 56px system-ui, sans-serif";
    g.fillStyle = "rgba(20,20,24,0.8)";
    g.fillRect(0, 0, Math.min(512, g.measureText(text).width + 40), 96);
    g.fillStyle = "#ff8a00";
    g.fillText(text, 20, 68);
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, depthTest: false, transparent: true }));
    s.center.set(0, 0); // its corner at the marker, the text up and to the right
    s.scale.set(0.088 * size, 0.0165 * size, 1); // about half the size it had
    s.position.set(p[0] + 0.012 * size, p[1] + 0.012 * size, p[2]);
    s.renderOrder = 12;
    return s;
  }

  private pick(e: PointerEvent) {
    if (this.tc?.axis) return; // a click on the gizmo, not on a joint
    const r = this.canvas.getBoundingClientRect();
    this.raycaster.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), this.camera);
    const hit = this.raycaster.intersectObjects([...this.markers.values()])[0];
    if (hit) return this.edit ? this.edit.onSelect(hit.object.userData.marker, e.shiftKey) : this.onMarker!(hit.object.userData.marker, null);
    if (this.edit || !this.selected || !this.model) return;
    // both sides, or the exit surface is culled; the displayed materials get their side back right after
    const mats = new Set<THREE.Material>();
    this.model.traverse((o) => (o as THREE.Mesh).isMesh && [(o as THREE.Mesh).material].flat().forEach((m) => mats.add(m)));
    const sides = [...mats].map((m) => m.side);
    mats.forEach((m) => (m.side = THREE.DoubleSide));
    let hits: THREE.Intersection[];
    try {
      hits = this.raycaster.intersectObject(this.model, true);
    } finally {
      [...mats].forEach((m, i) => (m.side = sides[i]));
    }
    if (!hits.length) return;
    // the middle of the body under the cursor: entry and the next surface the ray leaves through
    const a = hits[0].point;
    const b = hits.find((h) => h.distance > hits[0].distance + 1e-4)?.point ?? a;
    const mid = a.clone().add(b).multiplyScalar(0.5);
    this.onMarker!(this.selected, [mid.x, mid.y, mid.z]);
  }

  private drop() {
    if (this.brush) {
      this.setPaint(false);
      this.brush.veils.forEach((v) => (v.removeFromParent(), (v.material as THREE.Material).dispose()));
      this.brush.tex.dispose();
      this.brush = null;
    }
    this.setPoseEdit(null);
    this.setMotionPath(null);
    this.setOnion(null);
    this.action = null;
    this.playing = this.posing = false; // nothing to play: the render loop went on every frame (UI-008)
    this.bones = [];
    this.helperSkel?.removeFromParent();
    this.helperSkel?.dispose();
    this.helperSkel = null;
    this.grid.visible = false;
    this.dirty = true;
    if (!this.model) return;
    this.scene.remove(this.model);
    this.mixer?.stopAllAction();
    this.mixer = null;
    this.clips = [];
    this.free(this.model);
    this.model = null;
  }

  // geometry, the glTF's own materials and their textures; channel-view materials share those textures
  private free(root: THREE.Object3D) {
    const skeletons = new Set<THREE.Skeleton>();
    root.traverse((o) => {
      const mesh = o as THREE.SkinnedMesh;
      if (!mesh.isMesh) return;
      if (mesh.skeleton) skeletons.add(mesh.skeleton);
      mesh.geometry.dispose();
      const lines = this.quadLines.get(mesh);
      if (lines) {
        lines.geometry.dispose();
        (lines.material as THREE.Material).dispose();
      }
      this.quadLines.delete(mesh);
      const own = [this.originals.get(mesh) ?? mesh.material].flat();
      for (const m of [mesh.material].flat()) if (!own.includes(m)) m.dispose();
      for (const m of own) {
        for (const v of Object.values(m)) if (v instanceof THREE.Texture) v.dispose();
        m.dispose();
      }
    });
    skeletons.forEach((s) => s.dispose());
  }

  setViewMode(mode: ViewMode) {
    this.mode = mode;
    this.dirty = true;
    this.model?.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh || mesh.userData.veil) return; // M68: the brush's veil keeps its own material
      const orig = this.originals.get(mesh)!;
      if (mesh.material !== orig) (mesh.material as THREE.Material).dispose(); // the previous channel view
      const src = (Array.isArray(orig) ? orig[0] : orig) as THREE.MeshStandardMaterial;
      // the glTF node's own name (GLTFLoader keeps it in userData; `name` has spaces turned into "_");
      // a multi-primitive node: the mesh sits in a group named like the part
      const named = (o?: THREE.Object3D | null) => (o?.userData?.name as string | undefined) ?? o?.name;
      const part = this.tint ? this.tint.findIndex((n) => n === named(mesh) || n === named(mesh.parent)) : -1;
      mesh.material =
        part >= 0
          ? new THREE.MeshStandardMaterial({ color: new THREE.Color().setHSL((part * 0.381966) % 1, 0.7, 0.55), roughness: 0.8 })
          : mode === "pbr"
            ? orig
            : this.channelMaterial(mode, src);
    });
    this.setWireframe(this.wireframe);
    this.setXray(this.xray);
  }

  /** M31: colour the nodes named like these parts (null: back to the textures). */
  setTint(names: string[] | null) {
    this.tint = names;
    this.setViewMode(this.mode);
  }

  /** M20 weights view: each skinned vertex coloured by its weight for `bone` (blue 0 → red 1); null
   * restores the view mode. With `skeleton`, the bones are drawn too. */
  setWeights(bone: string | null, skeleton = false) {
    this.helperSkel?.removeFromParent();
    this.helperSkel?.dispose();
    this.helperSkel = null;
    this.dirty = true;
    if (skeleton && this.model && this.stats.bones.length) {
      this.helperSkel = new THREE.SkeletonHelper(this.model);
      (this.helperSkel.material as THREE.LineBasicMaterial).depthTest = false;
      this.helperSkel.renderOrder = 10;
      this.scene.add(this.helperSkel);
    }
    this.model?.traverse((o) => {
      const mesh = o as THREE.SkinnedMesh;
      if (!mesh.isSkinnedMesh) return;
      const g = mesh.geometry;
      let h = this.heat.get(g);
      if (!bone) {
        // back to the geometry's own colours
        if (h) h.orig ? g.setAttribute("color", h.orig) : g.deleteAttribute("color");
        return this.heat.delete(g);
      }
      h ??= { orig: g.attributes.color, buf: new THREE.BufferAttribute(new Float32Array(g.attributes.skinIndex.count * 3), 3) };
      this.heat.set(g, h);
      const idx = g.attributes.skinIndex,
        wt = g.attributes.skinWeight;
      const b = mesh.skeleton.bones.findIndex((x) => x.name === bone);
      const c = new THREE.Color();
      for (let i = 0; i < idx.count; i++) {
        let w = 0;
        for (let k = 0; k < 4; k++) if (idx.getComponent(i, k) === b) w += wt.getComponent(i, k);
        c.setHSL((1 - w) * 0.66, 1, 0.5); // blue (0) → red (1)
        h.buf.setXYZ(i, c.r, c.g, c.b);
      }
      h.buf.needsUpdate = true;
      g.setAttribute("color", h.buf);
      if (mesh.material !== this.originals.get(mesh)) (mesh.material as THREE.Material).dispose();
      mesh.material = new THREE.MeshBasicMaterial({ vertexColors: true });
    });
    if (!bone) this.setViewMode(this.mode);
  }

  /** Play one of the GLB's animation clips (looping), or null to return to the rest pose. */
  setClip(name: string | null) {
    if (!this.mixer) return;
    this.mixer.stopAllAction();
    this.dirty = true;
    const clip = name ? this.clips.find((c) => c.name === name) : null;
    this.playing = !!clip;
    this.action = clip ? this.mixer.clipAction(clip).reset().play() : null;
    if (!clip) this.model?.traverse((o) => (o as THREE.SkinnedMesh).isSkinnedMesh && (o as THREE.SkinnedMesh).skeleton.pose());
  }

  // M37: the time bar's controls over the playing clip (the paused action holds its pose; a seek re-applies it)
  clipName = () => this.action?.getClip().name ?? null;
  duration = () => this.action?.getClip().duration ?? 0;
  time = () => this.action?.time ?? 0;
  isPaused = () => !this.action || this.action.paused;
  play() {
    if (!this.action) return;
    if (this.action.time >= this.duration() - 1e-4) this.action.time = 0;
    this.action.paused = false;
    this.playing = true;
  }
  pause() {
    if (!this.action) return;
    this.action.paused = true;
    this.playing = false;
    this.dirty = true;
  }
  seek(t: number) {
    if (!this.action || !this.mixer) return;
    this.action.time = Math.min(Math.max(t, 0), Math.max(this.duration() - 1e-4, 0)); // the end itself would wrap to 0
    this.mixer.update(0);
    this.dirty = true;
  }
  setSpeed(s: number) {
    this.speed = s;
  }
  /** Frames per second of the playing clip: its tracks' key spacing (glTF stores none; ours are baked at 24). */
  fps(): number {
    const dt = Math.min(...(this.action?.getClip().tracks ?? []).filter((t) => t.times.length > 2).map((t) => t.times[1] - t.times[0]));
    return Number.isFinite(dt) && dt > 0 ? Math.round(1 / dt) : 24;
  }
  /** The animation editor's evaluated clip in place of the one with its name, at the same time and pause. */
  useClip(clip: THREE.AnimationClip) {
    if (!this.mixer) return;
    const t = this.action?.time ?? 0;
    const paused = this.action ? this.action.paused : true;
    const i = this.clips.findIndex((c) => c.name === clip.name);
    this.mixer.stopAllAction();
    if (i >= 0 && this.clips[i] !== clip) this.mixer.uncacheClip(this.clips[i]);
    if (i >= 0) this.clips[i] = clip;
    else this.clips.push(clip);
    this.action = this.mixer.clipAction(clip).play();
    this.action.paused = paused;
    this.playing = !paused;
    this.seek(t);
  }
  /** The model's bones: three.js name → the glTF node's own name (the loader drops dots and colons) and the parent's. */
  boneNames = () =>
    this.bones.map((b) => ({
      name: b.name,
      node: (b.userData.name as string | undefined) ?? b.name,
      parent: (b.parent as THREE.Bone)?.isBone ? b.parent!.name : null,
      rest: b.userData.rest as Vec3, // its node's translation as loaded
    }));
  /** A bone's local rotation now (x, y, z, w). */
  boneQuat = (name: string) => this.bones.find((b) => b.name === name)?.quaternion.toArray() as [number, number, number, number] | undefined;
  /** World positions of bones over a clip's frames (Snap to ground): the lowest Y of `names`, the clip left at its time. */
  lowest(names: string[]): number {
    const pick = this.bones.filter((b) => names.includes(b.name));
    if (!this.action || !this.mixer || !pick.length) return 0;
    const t0 = this.action.time;
    const n = Math.max(2, Math.round(this.duration() * this.fps()) + 1);
    let low = Infinity;
    const w = new THREE.Vector3();
    for (let i = 0; i < n; i++) {
      this.seek((i / (n - 1)) * this.duration());
      this.model!.updateMatrixWorld(true);
      for (const b of pick) low = Math.min(low, b.getWorldPosition(w).y);
    }
    this.seek(t0);
    return low;
  }
  /** M44: forget a clip (the editor's copies, when it closes). */
  dropClip(name: string) {
    const i = this.clips.findIndex((c) => c.name === name);
    if (i < 0) return;
    if (this.action?.getClip() === this.clips[i]) (this.action.stop(), (this.action = null), (this.playing = false));
    this.mixer?.uncacheClip(this.clips.splice(i, 1)[0]);
  }
  /** A clip as loaded (the animation editor puts it back when it closes). */
  getClip = (name: string) => this.clips.find((c) => c.name === name);
  /** The feet: the bones within 5 % of the skeleton's height above its lowest in the rest pose, and that lowest height
   * (Snap to ground: the clip's lowest foot back to it); every bone's rest height as a share of the skeleton's (Find foot
   * contacts). The pose is put back after. */
  feet(): { names: string[]; rest: number; heights: Record<string, number> } {
    let mesh: THREE.SkinnedMesh | undefined;
    this.model?.traverse((o) => (mesh ??= (o as THREE.SkinnedMesh).isSkinnedMesh ? (o as THREE.SkinnedMesh) : undefined));
    if (!mesh || !this.bones.length) return { names: [], rest: 0, heights: {} };
    const ys = this.restPose(() => this.bones.map((b) => b.getWorldPosition(new THREE.Vector3()).y));
    const lo = Math.min(...ys);
    const hi = Math.max(...ys);
    const heights = Object.fromEntries(this.bones.map((b, i) => [b.name, (ys[i] - lo) / (hi - lo || 1)]));
    return { names: this.bones.filter((b) => heights[b.name] <= 0.05).map((b) => b.name), rest: lo, heights };
  }

  setPoseEdit(edit: PoseEdit | null) {
    this.pose = edit;
    this.dirty = true;
    if (!edit) {
      this.poseTc?.detach();
      this.ikTarget.removeFromParent();
      this.poleHandle.removeFromParent();
      if (this.poseGroup) {
        this.scene.remove(this.poseGroup);
        this.poseGroup.traverse((o) => {
          (o as THREE.Mesh).geometry?.dispose();
          ((o as THREE.Mesh).material as THREE.SpriteMaterial | undefined)?.map?.dispose();
          ((o as THREE.Mesh).material as THREE.Material | undefined)?.dispose();
        });
      }
      this.poseGroup = this.poseTag = this.ikGroup = null;
      this.poseBones = [];
      this.tails.clear();
      this.targets.clear();
      this.poles.clear();
      return;
    }
    if (!this.poseGroup && this.model) this.buildArmature();
    // the IK handles: a box at each target, a diamond at each pole, dashed lines to them from the bone and the knee/elbow
    const key = (edit.ik ?? []).map((c) => c.bone).join();
    if (this.poseGroup && this.ikGroup?.key !== key) {
      if (this.ikGroup) {
        this.poseGroup.remove(this.ikGroup.group);
        this.ikGroup.group.traverse((o) => ((o as THREE.Mesh).geometry?.dispose(), ((o as THREE.Mesh).material as THREE.Material | undefined)?.dispose()));
      }
      const group = new THREE.Group();
      const s = this.poseSize;
      for (const c of edit.ik ?? []) {
        const line = (geo: THREE.BufferGeometry, role: string, dashed = false) =>
          Object.assign(
            new THREE.LineSegments(
              geo,
              dashed
                ? new THREE.LineDashedMaterial({ color: IK.edge, dashSize: 0.012 * s, gapSize: 0.008 * s, depthTest: false, transparent: true })
                : new THREE.LineBasicMaterial({ color: IK.edge, depthTest: false, transparent: true }),
            ),
            { renderOrder: 13, frustumCulled: false, userData: { ik: c.bone, role } },
          );
        group.add(
          line(new THREE.EdgesGeometry(new THREE.BoxGeometry(0.035 * s, 0.035 * s, 0.035 * s)), "target"),
          line(new THREE.EdgesGeometry(new THREE.OctahedronGeometry(0.014 * s)), "pole"),
          line(new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(new Float32Array(12), 3)), "dash", true),
        );
      }
      this.poseGroup.add(group);
      this.ikGroup = { key, group };
    }
    // colours: the selected bone blue, the bones an IK control turns yellow, a key at this frame orange edges
    const keyed = new Set(edit.keyed ?? []);
    const inIk = new Set((edit.ik ?? []).flatMap((c) => c.chain.slice(0, -1)));
    for (const { bone, mesh } of this.poseBones) {
      const look = bone.name === edit.selected ? ACTIVE : inIk.has(bone.name) ? IK : BONE;
      const m = mesh.material as THREE.MeshBasicMaterial;
      m.color.set(look.solid);
      m.opacity = bone.name === edit.selected ? 0.65 : 0.4;
      ((mesh.children[0] as THREE.LineSegments).material as THREE.LineBasicMaterial).color.set(keyed.has(bone.name) ? KEYED : look.edge);
    }
    this.ikGroup?.group.children.forEach((o) => (((o as THREE.LineSegments).material as THREE.LineBasicMaterial).opacity = o.userData.ik === edit.selected ? 1 : 0.45));
    if (this.poseGroup && (this.poseTag?.name ?? null) !== edit.selected) {
      if (this.poseTag) {
        this.poseGroup.remove(this.poseTag.sprite);
        this.poseTag.sprite.material.map?.dispose();
        this.poseTag.sprite.material.dispose();
      }
      this.poseTag = edit.selected ? { name: edit.selected, sprite: this.tag(edit.selected, [0, 0, 0], this.poseSize) } : null;
      if (this.poseTag) this.poseGroup.add(this.poseTag.sprite);
    }
    this.drawPose();
    const bone = this.bones.find((b) => b.name === edit.selected);
    const mode = edit.mode ?? "rotate";
    const tc = this.poseGizmo();
    if (tc.dragging) return;
    tc.setMode(mode === "rotate" ? "rotate" : "translate");
    tc.setSpace(mode === "rotate" ? "local" : "world");
    if (!bone || (mode === "ik" && !this.chainOf(bone, edit.chain ?? 2))) tc.detach();
    else if (mode === "ik") this.attach(tc, this.ikTarget, bone.getWorldPosition(new THREE.Vector3()));
    else if (mode === "target" && edit.handle === "pole" && this.poles.has(bone.name)) this.attach(tc, this.poleHandle, this.poles.get(bone.name)!);
    else if (mode === "target") this.attach(tc, this.ikTarget, this.targets.get(bone.name) ?? bone.getWorldPosition(new THREE.Vector3()));
    else tc.attach(bone);
  }

  private attach(tc: TransformControls, handle: THREE.Object3D, at: THREE.Vector3) {
    handle.position.copy(at);
    if (!handle.parent) this.scene.add(handle);
    tc.attach(handle);
  }

  // the armature, built once per model: an octahedron (and its edges) per bone from its head to its main child's; a
  // leaf's tail continues its parent's line by half that bone's length, measured in the rest pose and kept in the leaf's
  // own frame, so a leaf the clip turns still points its own way
  private buildArmature() {
    const size = (this.poseSize = new THREE.Box3().setFromObject(this.model!).getSize(new THREE.Vector3()).length() || 1);
    const g = (this.poseGroup = new THREE.Group());
    const geo = octahedron();
    const edges = new THREE.EdgesGeometry(geo);
    const kids = (b: THREE.Object3D) => b.children.filter((c) => (c as THREE.Bone).isBone) as THREE.Bone[];
    const count = (b: THREE.Object3D): number => kids(b).reduce((n, c) => n + 1 + count(c), 0);
    // the main child: the template's (MAIN), else the one most of the skeleton hangs from (the spine, not a leg)
    const mainOf = (b: THREE.Bone): THREE.Bone | null => kids(b).find((c) => c.name === MAIN[b.name]) ?? [...kids(b)].sort((x, y) => count(y) - count(x))[0] ?? null;
    this.poseBones = this.restPose(() =>
      this.bones.map((bone) => {
        const head = bone.getWorldPosition(new THREE.Vector3());
        let main = mainOf(bone);
        if (main && main.getWorldPosition(new THREE.Vector3()).distanceTo(head) < 1e-4 * size) main = null; // a child on the head: no length
        const up = (bone.parent as THREE.Bone)?.isBone ? bone.parent!.getWorldPosition(new THREE.Vector3()) : null;
        const along = up && up.distanceTo(head) > 1e-6 ? head.clone().sub(up).multiplyScalar(0.5) : new THREE.Vector3(0, 0.03 * size, 0);
        const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: BONE.solid, transparent: true, opacity: 0.4, depthTest: false, depthWrite: false, side: THREE.DoubleSide }));
        mesh.add(new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color: BONE.edge, depthTest: false, transparent: true })));
        mesh.matrixAutoUpdate = false;
        mesh.renderOrder = 10;
        mesh.children[0].renderOrder = 11;
        mesh.frustumCulled = mesh.children[0].frustumCulled = false;
        g.add(mesh);
        return { bone, mesh, main, tip: main ? null : bone.worldToLocal(head.add(along)) };
      }),
    );
    // dashed lines from a bone to a parent it does not join (to the nearer of the parent's head and tail)
    const loose = this.poseBones.filter((p) => (p.bone.parent as THREE.Bone)?.isBone && this.poseBones.find((q) => q.bone === p.bone.parent)?.main !== p.bone).map((p) => p.bone);
    const rel = new THREE.LineSegments(
      new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(new Float32Array(loose.length * 6), 3)),
      new THREE.LineDashedMaterial({ color: BONE.edge, dashSize: 0.008 * size, gapSize: 0.006 * size, depthTest: false, transparent: true, opacity: 0.6 }),
    );
    Object.assign(rel, { renderOrder: 10, frustumCulled: false, userData: { loose } });
    g.add(rel);
    this.scene.add(g);
  }

  /** `fn` run with the skeleton in its rest pose, the pose put back after. */
  private restPose<T>(fn: () => T): T {
    let mesh: THREE.SkinnedMesh | undefined;
    this.model?.traverse((o) => (mesh ??= (o as THREE.SkinnedMesh).isSkinnedMesh ? (o as THREE.SkinnedMesh) : undefined));
    const saved = this.bones.map((b) => [b.position.clone(), b.quaternion.clone(), b.scale.clone()] as const);
    mesh?.skeleton.pose();
    this.model!.updateMatrixWorld(true);
    try {
      return fn();
    } finally {
      this.bones.forEach((b, i) => (b.position.copy(saved[i][0]), b.quaternion.copy(saved[i][1]), b.scale.copy(saved[i][2])));
      this.model!.updateMatrixWorld(true);
    }
  }

  /** A bone and the `n` bones above it, the top first; null if it has fewer. */
  private chainOf(b: THREE.Bone, n: number): THREE.Bone[] | null {
    const out = [b];
    while (out.length <= n) {
      const up = out[0].parent as THREE.Bone;
      if (!up?.isBone) return null;
      out.unshift(up);
    }
    return out;
  }

  // skeleton space (the layer's) is the root bone's parent's frame; an IK control's space a bone's
  private spaceOf = (space: string | null): THREE.Object3D =>
    (space && this.bones.find((b) => b.name === space)) || this.bones.find((b) => !(b.parent as THREE.Bone)?.isBone)?.parent || this.scene;
  private toSpace = (w: THREE.Vector3, space: string | null): Vec3 => arr(this.spaceOf(space).worldToLocal(w.clone()));

  // the IK drag (and a target drag's preview): the chain back where it began, then solved putting its last bone at the handle
  private solveIK() {
    const ch = this.ikNow;
    if (!ch.length || !this.ikStart) return;
    ch.forEach((b, i) => b.quaternion.copy(this.ikStart![i]));
    ch[0].updateWorldMatrix(true, true);
    const turns = ikChain(wq(ch[0].parent!), ch.map(wq), ch.map(wp), arr(this.ikTarget.position), 0);
    turns?.forEach((q, i) => ch[i].quaternion.fromArray(q));
    this.dirty = true;
  }
  // a pole drag: the chain swung about its root–tip line by the angle the handle went round that line
  private solveSwivel() {
    const ch = this.ikNow;
    if (!ch.length || !this.ikStart) return;
    ch.forEach((b, i) => b.quaternion.copy(this.ikStart![i]));
    ch[0].updateWorldMatrix(true, true);
    const a = ch[0].getWorldPosition(new THREE.Vector3());
    const c = ch[ch.length - 1].getWorldPosition(new THREE.Vector3());
    const ax = c.clone().sub(a).normalize();
    const flat = (p: THREE.Vector3) => {
      const v = p.clone().sub(a);
      return v.addScaledVector(ax, -v.dot(ax));
    };
    const u = flat(this.poleStart);
    const v = flat(this.poleHandle.position);
    this.swivelNow = (Math.atan2(ax.dot(u.clone().cross(v)), u.dot(v)) * 180) / Math.PI;
    const turns = ikChain(wq(ch[0].parent!), ch.map(wq), ch.map(wp), arr(c), this.swivelNow);
    turns?.forEach((q, i) => ch[i].quaternion.fromArray(q));
    this.dirty = true;
  }

  /** M38: the selected bone's track over the playing clip: a line and a dot a frame, before the playhead orange, after it
   * green, the playhead's frame a white dot (a bone that hardly moves is still a visible cluster); null: none. */
  setMotionPath(bone: string | null) {
    if (this.path) {
      this.scene.remove(this.path.group);
      this.path.group.traverse((o) => {
        (o as THREE.Points).geometry?.dispose();
        ((o as THREE.Points).material as THREE.Material | undefined)?.dispose();
      });
      this.path = null;
    }
    const b = this.bones.find((x) => x.name === bone);
    if (!b || !this.action) return;
    const fps = this.fps();
    const n = Math.round(this.duration() * fps) + 1;
    const t0 = this.action.time;
    const pos = new Float32Array(n * 3);
    const w = new THREE.Vector3();
    for (let i = 0; i < n; i++) {
      this.seek(i / fps);
      this.model!.updateMatrixWorld(true);
      b.getWorldPosition(w).toArray(pos, i * 3);
    }
    this.seek(t0);
    const col = new THREE.BufferAttribute(new Float32Array(n * 3), 3);
    const geo = new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(pos, 3)).setAttribute("color", col);
    const over = { depthTest: false, transparent: true, sizeAttenuation: false };
    const now = new THREE.Points(
      new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(new Float32Array(3), 3)),
      new THREE.PointsMaterial({ color: 0xffffff, size: 11, ...over }),
    );
    const group = new THREE.Group().add(
      new THREE.Line(geo, new THREE.LineBasicMaterial({ vertexColors: true, depthTest: false, transparent: true })),
      new THREE.Points(geo, new THREE.PointsMaterial({ vertexColors: true, size: 6, ...over })), // the line's geometry
      now,
    );
    group.children.forEach((o, i) => Object.assign(o, { renderOrder: 12 + i, frustumCulled: false }));
    this.scene.add(group);
    this.path = { group, pos, col, now, frame: -1 };
    this.dirty = true;
  }

  /** M38: ghosts of the model `frames` away from the playhead (e.g. [-8, -4, 4, 8]: before blue, after green); null: none. */
  setOnion(frames: number[] | null) {
    for (const g of this.ghosts) {
      this.scene.remove(g.root);
      g.mixer.stopAllAction();
      g.mixer.uncacheRoot(g.root);
      g.root.traverse((o) => {
        ((o as THREE.Mesh).material as THREE.Material | undefined)?.dispose();
        (o as THREE.SkinnedMesh).skeleton?.dispose(); // its bone texture
      });
    }
    this.ghosts = [];
    this.dirty = true;
    if (!frames?.length || !this.model || !this.action) return;
    for (const k of frames) {
      const root = cloneSkinned(this.model);
      const mat = new THREE.MeshBasicMaterial({ color: k < 0 ? 0x5a8cff : 0x5adc7a, transparent: true, opacity: 0.22, depthWrite: false });
      root.traverse((o) => {
        if ((o as THREE.Mesh).isMesh) (o as THREE.Mesh).material = mat.clone();
      });
      mat.dispose();
      const mixer = new THREE.AnimationMixer(root);
      const action = mixer.clipAction(this.action.getClip()).play();
      action.paused = true;
      this.scene.add(root);
      this.ghosts.push({ root, mixer, action, frames: k });
    }
  }

  // the motion path's colours and the ghosts' times follow the playhead
  private follow() {
    if (!this.action || (!this.path && !this.ghosts.length)) return;
    const fps = this.fps();
    const now = Math.round(this.action.time * fps);
    if (this.path && this.path.frame !== now) {
      const { pos, col } = this.path;
      for (let i = 0; i < col.count; i++) col.setXYZ(i, ...((i <= now ? [1, 0.6, 0.15] : [0.35, 0.9, 0.45]) as Vec3));
      col.needsUpdate = true;
      const at = (this.path.now.geometry.attributes.position as THREE.BufferAttribute).copyArray(pos.subarray(3 * Math.min(now, col.count - 1), 3 * Math.min(now, col.count - 1) + 3));
      at.needsUpdate = true;
      this.path.frame = now;
      this.dirty = true;
    }
    for (const g of this.ghosts) {
      const t = Math.min(Math.max(this.action.time + g.frames / fps, 0), Math.max(this.duration() - 1e-4, 0));
      if (Math.abs(g.action.time - t) > 1e-6) {
        g.action.time = t;
        g.mixer.update(0);
      }
    }
  }

  private poseGizmo(): TransformControls {
    window.addEventListener("keydown", this.onKey);
    if (this.poseTc) return this.poseTc;
    const tc = (this.poseTc = new TransformControls(this.camera, this.canvas));
    tc.setMode("rotate");
    tc.setSpace("local");
    tc.setSize(0.7);
    this.scene.add(tc.getHelper());
    tc.addEventListener("change", () => (this.dirty = true));
    tc.addEventListener("mouseDown", () => {
      this.cancelled = false;
      const o = tc.object;
      this.ikStart = null;
      if (o !== this.ikTarget && o !== this.poleHandle) return void (o && this.poseFrom.copy(o.position));
      // the chain a handle drags: its IK control's, else (IK drag, a first target drag) the panel's chain length
      const B = this.bones.find((b) => b.name === this.pose?.selected);
      const c = this.pose?.mode === "target" ? this.pose.ik?.find((x) => x.bone === B?.name) : undefined;
      const ch = c ? c.chain.map((n) => this.bones.find((b) => b.name === n)!) : B && this.chainOf(B, this.pose?.chain ?? 2);
      if (!ch || ch.some((b) => !b)) return;
      this.ikNow = ch;
      this.ikStart = ch.map((b) => b.quaternion.clone());
      this.poleStart.copy(this.poleHandle.position);
      ch[ch.length - 1].getWorldPosition(this.shownStart);
      this.swivelNow = 0;
    });
    tc.addEventListener("objectChange", () => (tc.object === this.ikTarget ? this.solveIK() : tc.object === this.poleHandle && this.solveSwivel()));
    tc.addEventListener("dragging-changed", (e) => {
      this.controls.enabled = !e.value;
      this.posing = !!e.value;
      if (e.value || this.cancelled || !this.pose) return;
      const o = tc.object;
      const sel = this.pose.selected;
      if ((o === this.ikTarget || o === this.poleHandle) && (!this.ikStart || !sel)) return;
      if (o === this.ikTarget && this.pose.mode === "target") this.pose.onTarget?.(sel!, ...this.targetIn(sel!, this.ikTarget.position, this.shownStart));
      else if (o === this.ikTarget) this.pose.onIK?.(Object.fromEntries(this.ikNow.map((b) => [b.name, b.quaternion.toArray() as Q4])));
      else if (o === this.poleHandle) this.pose.onSwivel?.(sel!, this.swivelNow);
      else if (o && this.pose.mode === "move") this.pose.onMove?.(o.name, this.poseFrom.toArray() as Vec3, o.position.toArray() as Vec3);
      else if (o) this.pose.onPose(o.name, (o as THREE.Bone).quaternion.toArray() as Q4);
    });
    return tc;
  }

  // a target handle and its bone as shown, in the control's space: the editor keys the bone's place in the layer plus
  // their difference (the clip tools after the IK, ground and in place, shift both alike)
  private targetIn(bone: string, w: THREE.Vector3, shown = this.bones.find((b) => b.name === bone)!.getWorldPosition(new THREE.Vector3())): [Vec3, Vec3] {
    const space = this.pose?.ik?.find((x) => x.bone === bone)?.space ?? null;
    return [this.toSpace(w, space), this.toSpace(shown, space)];
  }

  // a click: a joint or an IK control's handle, then the body of the nearest bone
  private pickBone(e: PointerEvent) {
    const r = this.canvas.getBoundingClientRect();
    const [mx, my] = [e.clientX - r.left, e.clientY - r.top];
    const screen = (p: THREE.Vector3) => {
      const q = p.clone().project(this.camera);
      return q.z < 1 ? ([((q.x + 1) / 2) * r.width, ((1 - q.y) / 2) * r.height] as const) : null;
    };
    const near = (p: THREE.Vector3) => {
      const s = screen(p);
      return s ? Math.hypot(s[0] - mx, s[1] - my) : Infinity;
    };
    let best: [number, string] | null = null;
    for (const b of this.bones) {
      const d = near(b.getWorldPosition(new THREE.Vector3()));
      if (d < 14 && (!best || d < best[0])) best = [d, b.name];
    }
    // the nearest handle, unless a joint is about as near (a target reached sits on its own joint: the joint wins)
    let hit: [number, string, "target" | "pole"] | null = null;
    for (const [handles, which] of [
      [this.targets, "target"],
      [this.poles, "pole"],
    ] as const)
      for (const [bone, p] of handles) {
        const d = near(p);
        if (d < 14 && (!hit || d < hit[0])) hit = [d, bone, which];
      }
    if (hit && (!best || hit[0] + 4 < best[0])) return this.pose?.onHandle?.(hit[1], hit[2]);
    for (const b of best ? [] : this.bones) {
      const a = screen(b.getWorldPosition(new THREE.Vector3()));
      const t = this.tails.has(b.name) ? screen(this.tails.get(b.name)!) : null;
      if (!a || !t) continue;
      const [dx, dy] = [t[0] - a[0], t[1] - a[1]];
      const u = Math.min(Math.max(((mx - a[0]) * dx + (my - a[1]) * dy) / (dx * dx + dy * dy || 1), 0), 1);
      const d = Math.hypot(a[0] + u * dx - mx, a[1] + u * dy - my);
      if (d < 8 && (!best || d < best[0])) best = [d, b.name];
    }
    if (best) this.pose?.onSelect(best[1]);
  }

  // the armature follows the moving bones, the IK handles their targets and poles, the gizmo its handle between drags
  private drawPose() {
    const g = this.poseGroup;
    if (!g || !this.pose) return;
    this.model?.updateMatrixWorld(true);
    const head = (b: THREE.Object3D) => new THREE.Vector3().setFromMatrixPosition(b.matrixWorld);
    const x = new THREE.Vector3();
    const z = new THREE.Vector3();
    for (const { bone, mesh, main, tip } of this.poseBones) {
      const h = head(bone);
      const t = main ? head(main) : bone.localToWorld(tip!.clone());
      this.tails.set(bone.name, t);
      const y = t.clone().sub(h);
      const len = y.length();
      const yn = y.clone().divideScalar(len || 1);
      // the octahedron's flat sides follow the bone's own X (else Z): its roll shows as the bone twists
      for (const col of [0, 2]) {
        x.setFromMatrixColumn(bone.matrixWorld, col);
        x.addScaledVector(yn, -x.dot(yn));
        if (x.lengthSq() > 1e-12) break;
      }
      x.normalize();
      z.crossVectors(x, yn);
      mesh.matrix.makeBasis(x.multiplyScalar(len), y, z.multiplyScalar(len)).setPosition(h);
      mesh.matrixWorldNeedsUpdate = true;
    }
    const rel = g.children.find((o) => o.userData.loose) as THREE.LineSegments | undefined;
    if (rel) {
      const pos = rel.geometry.attributes.position as THREE.BufferAttribute;
      (rel.userData.loose as THREE.Bone[]).forEach((b, i) => {
        const h = head(b);
        const ph = head(b.parent!);
        const pt = this.tails.get(b.parent!.name) ?? ph;
        pos.setXYZ(2 * i, ...(h.distanceTo(ph) < h.distanceTo(pt) ? ph : pt).toArray());
        pos.setXYZ(2 * i + 1, ...h.toArray());
      });
      pos.needsUpdate = true;
      rel.computeLineDistances();
    }
    const sel = this.bones.find((b) => b.name === this.pose!.selected);
    if (this.poseTag && sel) this.poseTag.sprite.position.copy(head(sel)).addScalar(0.012 * this.poseSize);
    // the IK controls: the target where the layer puts it (shifted as the bone is), the pole out from the knee/elbow
    this.targets.clear();
    this.poles.clear();
    for (const c of this.pose.ik ?? []) {
      const ch = c.chain.map((n) => this.bones.find((b) => b.name === n));
      if (ch.some((b) => !b)) continue;
      const [a, end, m] = [head(ch[0]!), head(ch[ch.length - 1]!), head(ch[Math.ceil((ch.length - 1) / 2)]!)];
      const mine = this.posing && c.bone === this.pose.selected;
      const toW = (v: Vec3) => this.spaceOf(c.space).localToWorld(V(v));
      const target =
        mine && this.pose.mode === "target" && this.poseTc?.object === this.ikTarget ? this.ikTarget.position.clone() : c.p && c.at ? end.clone().add(toW(c.p).sub(toW(c.at))) : null;
      const ax = end.clone().sub(a).normalize();
      const out = m.clone().sub(a);
      out.addScaledVector(ax, -out.dot(ax));
      const pole =
        mine && this.poseTc?.object === this.poleHandle ? this.poleHandle.position.clone() : out.lengthSq() > 1e-12 ? m.clone().addScaledVector(out.normalize(), m.distanceTo(a)) : null;
      if (target) this.targets.set(c.bone, target);
      if (pole) this.poles.set(c.bone, pole);
      for (const o of this.ikGroup?.group.children ?? []) {
        if (o.userData.ik !== c.bone) continue;
        if (o.userData.role === "target") o.position.copy(target ?? end);
        else if (o.userData.role === "pole") (o.visible = !!pole) && o.position.copy(pole!);
        else {
          const pos = (o as THREE.LineSegments).geometry.attributes.position as THREE.BufferAttribute;
          [end, target ?? end, m, pole ?? m].forEach((p, i) => pos.setXYZ(i, p.x, p.y, p.z));
          pos.needsUpdate = true;
          (o as THREE.LineSegments).computeLineDistances();
        }
      }
    }
    const o = this.poseTc?.object;
    if (!this.posing && sel && o === this.poleHandle && this.poles.has(sel.name)) this.poleHandle.position.copy(this.poles.get(sel.name)!);
    else if (!this.posing && sel && o === this.ikTarget) this.ikTarget.position.copy((this.pose.mode === "target" && this.targets.get(sel.name)) || head(sel));
  }

  setWireframe(on: boolean, quads = this.quads) {
    this.wireframe = on;
    this.quads = quads;
    this.dirty = true;
    this.quadEdges = 0;
    this.model?.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh || mesh.userData.veil) return; // M68: the brush's veil keeps its offset
      // a quad mesh shows its quads as lines over the shaded surface; a skinned mesh keeps the triangle wireframe (its
      // vertices move with the bones, a still line set would not)
      const lines = on && quads && !(mesh as THREE.SkinnedMesh).isSkinnedMesh ? this.quadLinesOf(mesh) : null;
      for (const m of [mesh.material].flat()) {
        (m as THREE.MeshBasicMaterial).wireframe = on && !lines;
        m.polygonOffset = !!lines; // the surface a hair back, so the lines on it win the depth test
        m.polygonOffsetFactor = m.polygonOffsetUnits = lines ? 1 : 0;
      }
      const had = this.quadLines.get(mesh);
      if (had && had !== lines) mesh.remove(had);
      if (lines) {
        if (lines.parent !== mesh) mesh.add(lines);
        this.quadEdges += lines.geometry.attributes.position.count / 2;
      }
    });
  }

  private quadLinesOf(mesh: THREE.Mesh): THREE.LineSegments | null {
    if (!this.quadLines.has(mesh)) {
      const geo = quadOutline(mesh.geometry);
      this.quadLines.set(
        mesh,
        geo && Object.assign(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: 0x202020, transparent: true, opacity: 0.9 })), { frustumCulled: false }),
      );
    }
    return this.quadLines.get(mesh)!;
  }

  private channelMaterial(mode: ViewMode, src: THREE.MeshStandardMaterial): THREE.Material {
    // ponytail: a new material per mesh per switch; cache in a WeakMap if switching gets slow
    if (mode === "albedo") return new THREE.MeshBasicMaterial({ map: src.map, color: src.color });
    if (mode === "normal") return new THREE.MeshNormalMaterial({ normalMap: src.normalMap });
    if (mode === "uv") return new THREE.MeshBasicMaterial({ map: this.checker });
    const orm: Record<string, [number, THREE.Texture | null, number]> = {
      ao: [0, src.aoMap, 1],
      roughness: [1, src.roughnessMap, src.roughness],
      metallic: [2, src.metalnessMap, src.metalness],
    };
    const [channel, map, value] = orm[mode];
    return new THREE.ShaderMaterial({
      ...ORM_SHADER,
      uniforms: { map: { value: map }, hasMap: { value: !!map }, channel: { value: channel }, value: { value } },
    });
  }

  private frameModel() {
    const box = new THREE.Box3().setFromObject(this.model!);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3()).length() || 1;
    this.grid.position.set(center.x, box.min.y, center.z);
    this.grid.scale.setScalar(4 * size);
    this.grid.visible = true;
    this.controls.target.copy(center);
    this.helper.center.copy(center);
    this.camera.position.copy(center).add(new THREE.Vector3(0.6, 0.4, 1).multiplyScalar(size));
    this.camera.near = size / 1000;
    this.camera.far = size * 100;
    this.camera.updateProjectionMatrix();
  }

  private resize() {
    const { clientWidth: w, clientHeight: h } = this.canvas;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  private render() {
    const dt = this.clock.getDelta();
    if (!this.posing) this.mixer?.update(dt * this.speed); // a gizmo turn owns its bone until it ends
    if (this.poseGroup && (this.dirty || this.playing || this.posing)) this.drawPose(); // the bones moved
    this.follow();
    const animating = this.helper.animating;
    if (animating) this.helper.update(dt);
    else this.controls.update(); // fires "change" (dirty) while damping still moves the camera
    if (!this.dirty && !this.playing && !animating && !this.posing) return;
    this.dirty = false;
    this.renderer.clear();
    this.renderer.render(this.scene, this.camera);
    this.helper.render(this.renderer);
  }
}
