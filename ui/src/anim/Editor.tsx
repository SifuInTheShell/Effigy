// M37b/c, M38, M63: the animation editor on an animated version. The base clip (the nearest anim version's) stays as it is; the
// user's correction layer (docs/IMPLEMENTATION.md §3.2 anim_edit) is evaluated on top and played live in the viewer; the
// dope sheet (or the curves) lies under the view (#timeline-slot), the selected bone's key, the foot locks and the clip
// tools in the panel; Save makes an anim_edit version. M63: the skeleton drawn as Blender draws an armature, IK controls
// (a hand or foot held on a keyed target, the knee or elbow swung by its pole) and an IK drag over any chain length.
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { fileUrl, type Meta } from "../api";
import { leave, useBusy, useHistory, useUnsaved } from "../hooks";
import type { IkShow, Viewer } from "../viewer/Viewer";
import {
  conj,
  contacts,
  delta,
  chainOf,
  evaluate,
  fromDegrees,
  ikAt,
  fromThree,
  I,
  mul,
  norm,
  offset,
  pose,
  positive,
  smoothed,
  toDegrees,
  toThree,
  turn,
  NAME_RULE,
  nameOk,
  type Clip,
  type Ik,
  type IkKey,
  type Key,
  type Layer,
  type Lock,
  type Offset,
  type Q,
  type V3,
} from "./clip";
import { KeyList, DopeSheet, type Curves, type Row } from "./DopeSheet";
import { Transport, useFrame, useTransportKeys } from "./Transport";

const FALLOFF = 10; // frames a new key fades in and out over
const EMPTY: Layer = {}; // one object: a clip without edits must not look changed on every render
const AXES = ["#e06464", "#6cc070", "#5a9cf0"]; // X, Y, Z in the curves
const MAX_LOCKS = 64; // anim_edit's limit
type Layers = Record<string, Layer>;
type Bone = { name: string; node: string; parent: string | null; rest: V3 }; // three.js name, glTF node name, parent's three.js name
type Base = { fps: number; base: string; clips: Record<string, { data: Clip; keep: THREE.KeyframeTrack[] }>; bones: Bone[]; groups: [string, string[]][]; sides?: [string, string] };
type Gizmo = "rotate" | "move" | "ik" | "target";
const MAX_CHAIN = 8; // anim_edit's longest IK chain

export const hasEdits = (l?: Layer) =>
  !!l &&
  (Object.values(l.keys ?? {}).some((k) => k.length) ||
    Object.values(l.offsets ?? {}).some((o) => o.length) ||
    !!l.trim ||
    (l.speed ?? 1) !== 1 ||
    !!l.loop ||
    !!l.ground ||
    !!l.in_place ||
    Object.values(l.smooth ?? {}).some((s) => s > 0) ||
    !!l.feet?.length ||
    !!l.ik?.length);

/** The dope sheet's groups of glTF node names: a person's body, arms, hands and legs; a creature's middle and sides. */
export function animGroups(nodes: string[], template: string, sides?: [string, string]): [string, string[]][] {
  if (template !== "mixamo52" && sides) {
    const side = (s: string) => nodes.filter((n) => n.endsWith(s));
    return [
      ["Middle", nodes.filter((n) => !n.endsWith(sides[0]) && !n.endsWith(sides[1]))], // root, spine, head, tail
      ["Left side", side(sides[0])],
      ["Right side", side(sides[1])],
    ];
  }
  const by = (re: RegExp) => nodes.filter((n) => re.test(n));
  const groups: [string, string[]][] = [["Body", by(/^(Hips|Spine\d?|Neck|Head)$/)]];
  for (const s of ["Left", "Right"])
    groups.push([`${s} arm`, by(new RegExp(`^${s}(Shoulder|Arm|ForeArm|Hand)$`))], [`${s} hand`, by(new RegExp(`^${s}Hand.+`))], [`${s} leg`, by(new RegExp(`^${s}(UpLeg|Leg|Foot|ToeBase)$`))]);
  const seen = new Set(groups.flatMap(([, n]) => n));
  groups.push(["Other", nodes.filter((n) => !seen.has(n))]);
  return groups.filter(([, n]) => n.length);
}

// a key's id in the dope sheet: "node@frame" for a turn, "node:move@frame" for a move (an offset), "node:ik@frame" for
// an IK control's key
const parse = (id: string) => {
  const i = id.lastIndexOf("@");
  const n = id.slice(0, i);
  const move = n.endsWith(":move");
  const ik = n.endsWith(":ik");
  return { n: move ? n.slice(0, -5) : ik ? n.slice(0, -3) : n, f: +id.slice(i + 1), move, ik };
};
const tag = (move: boolean, ik: boolean) => (move ? ":move" : ik ? ":ik" : "");
const setIn = <T extends { f: number }>(all: Record<string, T[]> | undefined, node: string, k: T) => ({
  ...all,
  [node]: [...(all?.[node] ?? []).filter((x) => x.f !== k.f), k].sort((a, b) => a.f - b.f),
});
const setKey = (l: Layer, node: string, k: Key): Layer => ({ ...l, keys: setIn(l.keys, node, k) });
const setOffset = (l: Layer, node: string, o: Offset): Layer => ({ ...l, offsets: setIn(l.offsets, node, o) });
// an IK key on the bone's control (made with `c`'s chain and space when the bone has none yet)
const setIk = (l: Layer, c: Omit<Ik, "keys">, k: IkKey): Layer => {
  const all = l.ik ?? [];
  const had = all.find((x) => x.bone === c.bone);
  const keys = [...(had?.keys ?? []).filter((x) => x.f !== k.f), k].sort((a, b) => a.f - b.f);
  return { ...l, ik: had ? all.map((x) => (x === had ? { ...x, keys } : x)) : [...all, { ...c, keys }] };
};
// a control left without keys goes (anim_edit wants one at least); a move of keys keeps it, in its place, for a moment
const dropKeys = (l: Layer, picked: string[], keepEmpty = false): Layer => {
  const keep = <T extends { f: number }>(all: Record<string, T[]> | undefined, tag: string) =>
    Object.fromEntries(
      Object.entries(all ?? {})
        .map(([n, ks]) => [n, ks.filter((k) => !picked.includes(`${n}${tag}@${k.f}`))] as const)
        .filter(([, ks]) => ks.length),
    );
  const ik = (l.ik ?? []).map((c) => ({ ...c, keys: c.keys.filter((k) => !picked.includes(`${c.bone}:ik@${k.f}`)) })).filter((c) => keepEmpty || c.keys.length);
  return { ...l, keys: keep(l.keys, ""), offsets: keep(l.offsets, ":move"), ik: ik.length ? ik : undefined };
};
const cm = (m: number) => Math.round(m * 1000) / 10; // metres to cm, a millimetre's precision
const r5 = (v: V3) => v.map((x) => Math.round(x * 1e5) / 1e5) as V3;
const wrap = (d: number) => ((((d + 180) % 360) + 360) % 360) - 180;

export function AnimEditor(props: { asset: string; version: Meta; viewer: Viewer; onSave: (clips: Layers) => Promise<unknown>; onClose: () => void }) {
  const v = props.viewer;
  const ver = props.version;
  const [ready, setReady] = useState<Base | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hist = useHistory<Layers>();
  const layers = hist.h?.now ?? {};
  const [picked, setClip] = useState("");
  // the clip being edited: the picked one while it exists (undo can remove a copy), else the first base clip
  const clip = ready && (picked in ready.clips || layers[picked]?.from) ? picked : Object.keys(ready?.clips ?? {})[0] ?? "";
  const [bone, setBone] = useState<string | null>(null); // a glTF node name
  const [sel, setSel] = useState<string[]>([]); // keys as "node@frame" / "node:move@frame"
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [original, setOriginal] = useState(false);
  const [frame, setFrame] = useState(0); // the base frame under the playhead
  const [mode, setMode] = useState<Gizmo>("rotate");
  const [chainN, setChainN] = useState(2); // IK drag's chain, and a new IK control's
  const [handle, setHandle] = useState<"target" | "pole">("target"); // what the IK target gizmo holds
  const [xray, setXray] = useState(false);
  const [curvesOn, setCurvesOn] = useState(false);
  const [path, setPath] = useState(false);
  const [onion, setOnion] = useState(false);
  const [found, setFound] = useState<string | null>(null); // what Find foot contacts did
  const [busy, run] = useBusy();
  const dirty = (hist.h?.past.length ?? 0) > 0; // something changed since the version was opened (what Save sends)
  useUnsaved("animation", dirty && !busy); // not while a save runs: its result opens the new version (UX-01)
  const loaded = useRef(new Map<string, THREE.AnimationClip>()); // the viewer's clips as they were: put back on close
  const added = useRef(new Set<string>());

  // the base: this version's own animations, or for an anim_edit its base's; the clips' fps; the user's earlier layer
  useEffect(() => {
    let live = true;
    (async () => {
      const get = (version: string, file: string) => fetch(fileUrl(props.asset, version, file)).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${version}/${file}: ${r.status}`))));
      const meta = await get(ver.version, ver.outputs.clips ?? "clips.json");
      const edit = ver.step === "anim_edit" ? await get(ver.version, ver.outputs.edit ?? "edit.json") : null;
      const base: string = edit?.base ?? ver.version;
      const skel = await get(ver.version, ver.outputs.skeleton ?? "skeleton.json").catch(() => ({})); // the template names the groups
      let anims: THREE.AnimationClip[];
      if (base === ver.version) anims = meta.clips.map((c: { name: string }) => v.getClip(c.name)).filter(Boolean);
      else {
        const gltf = await new GLTFLoader().loadAsync(fileUrl(props.asset, base, "rigged_anim.glb"));
        anims = gltf.animations;
        gltf.scene.traverse((o) => {
          const m = o as THREE.Mesh;
          m.geometry?.dispose();
          for (const mat of [m.material ?? []].flat() as THREE.MeshStandardMaterial[]) {
            for (const t of [mat.map, mat.normalMap, mat.roughnessMap, mat.metalnessMap, mat.aoMap, mat.emissiveMap]) t?.dispose();
            mat.dispose();
          }
        });
      }
      const bones = v.boneNames();
      const fps: number = meta.fps ?? 24;
      const clips = Object.fromEntries(anims.map((a) => [a.name, { data: fromThree(a, fps, bones), keep: a.tracks.filter((t) => t.name.endsWith(".scale")) }]));
      if (!live) return;
      const groups = animGroups(
        bones.map((b) => b.node),
        skel.template ?? "mixamo52",
        skel.sides,
      );
      setReady({ fps, base, clips, bones, groups, sides: skel.sides });
      hist.start(edit?.clips ?? {});
      const playing = v.clipName();
      setClip(playing && (clips[playing] || edit?.clips?.[playing]) ? playing : Object.keys(clips)[0]);
    })().catch((e) => live && setError((e as Error).message));
    return () => {
      live = false;
    };
  }, [props.asset, ver.version]);

  const rootOf = (c: string) => layers[c]?.from ?? c; // M44: a copy plays and edits on its source clip's samples
  const data = ready?.clips[rootOf(clip)];
  const layer = layers[clip] ?? EMPTY;
  const F = data?.data.frames ?? 1;
  const trim = layer.trim ?? [0, F - 1];
  const speed = layer.speed ?? 1;
  const evaluated = useMemo(() => data && evaluate(data.data, layer), [data, layer]);
  const three = (node: string) => ready?.bones.find((b) => b.node === node)?.name;
  const node = (name: string) => ready?.bones.find((b) => b.name === name)?.node ?? name;
  const track = (n: string) => data?.data.bones.find((b) => b.name === n);
  // a move needs a moving translation (the hips); a lock or an IK a turning bone whose parent and grandparent turn
  const canMove = (n: string | null) => !!n && !!track(n)?.translation;
  const above = (n: string | null) => {
    let b = n ? track(n) : undefined;
    let k = 0;
    if (!b?.rotation) return 0;
    while (k < MAX_CHAIN && b?.parent && track(b.parent)?.rotation) (k++, (b = track(b.parent)));
    return k; // the turning bones above it, in a row (an IK chain can take that many)
  };
  const chain = (n: string | null) => above(n) >= 2;
  const gizmo: Gizmo = (mode === "move" && !canMove(bone)) || ((mode === "ik" || mode === "target") && !chain(bone)) ? "rotate" : mode;
  const reach = Math.min(chainN, Math.max(above(bone), 2)); // the chain the IK drag (and a new control) takes here
  const ctl = bone ? layer.ik?.find((c) => c.bone === bone) : undefined;
  // M63: the clip in base frames with every correction up to the IK (trim, speed and the rest left out): where the layer
  // puts each bone, what an IK target and a space change are measured in
  const solved = useMemo(() => (data && (layer.ik?.length || gizmo === "target") ? evaluate(data.data, { keys: layer.keys, offsets: layer.offsets, smooth: layer.smooth, feet: layer.feet, ik: layer.ik }) : null), [data, layer, gizmo === "target"]);
  const solvedPose = useMemo(() => solved && pose(solved.bones, Math.min(frame, F - 1)), [solved, frame]);
  const atIn = (n: string, space?: string, m = solvedPose): V3 | null => {
    const b = m?.get(n);
    const s = space ? m?.get(space) : undefined;
    return !b ? null : s ? turn(conj(s.W), [b.p[0] - s.p[0], b.p[1] - s.p[1], b.p[2] - s.p[2]]) : b.p;
  };

  // the viewer plays the evaluated clip (or the original), from where it was; the motion path and the ghosts follow it
  useEffect(() => {
    if (!ready || !data || !evaluated) return;
    if (!loaded.current.has(clip) && !added.current.has(clip)) {
      const was = v.getClip(clip);
      if (was) loaded.current.set(clip, was);
      else added.current.add(clip); // a copy the viewer did not have: dropped on close
    }
    v.useClip(toThree(original ? data.data : evaluated, clip, ready.bones, data.keep));
  }, [evaluated, original, clip, ready]);
  // a copy that is gone from the layers (renamed, deleted, undone) leaves the viewer; the clip now playing was set above
  useEffect(() => {
    for (const c of added.current) if (!layers[c]) (v.dropClip(c), added.current.delete(c));
  }, [layers]);
  useEffect(() => void (ready && v.setMotionPath(path && bone ? (three(bone) ?? null) : null)), [path, bone, evaluated, original, clip, ready]);
  useEffect(() => void (ready && v.setOnion(onion ? [-6, -3, 3, 6] : null)), [onion, evaluated, original, clip, ready]);
  useEffect(() => {
    v.setXray(xray);
    return () => v.setXray(false);
  }, [xray]);
  // put the viewer back as it was (unless it shows another model by now)
  useEffect(() => {
    const model = v.stats;
    return () => {
      if (v.stats !== model) return;
      v.setPoseEdit(null);
      v.setMotionPath(null);
      v.setOnion(null);
      for (const c of added.current) v.dropClip(c);
      for (const c of loaded.current.values()) v.useClip(c);
    };
  }, []);

  const baseFrame = () => {
    const g = Math.round(v.time() * (ready?.fps ?? 24));
    return original ? Math.min(g, F - 1) : Math.min(trim[1], trim[0] + Math.round(g * speed));
  };
  const seekBase = (f: number) => {
    v.pause();
    const out = original ? f : (Math.min(Math.max(f, trim[0]), trim[1]) - trim[0]) / speed;
    v.seek(out / (ready?.fps ?? 24));
  };
  const commit = (f: (l: Layer) => Layer) => hist.commit((all) => ({ ...all, [clip]: f(all[clip] ?? {}) }));

  // a turn in the view: the key at this frame that makes the bone look like that (the delta from the smoothed base)
  const keyTurns = (turns: Record<string, Q>) => {
    if (!data) return;
    const f = baseFrame();
    const sm = smoothed(data.data, layer);
    const ds = Object.entries(turns).flatMap(([name, q]) => {
      const n = node(name);
      const base = sm.bones.find((b) => b.name === n)?.rotation?.[f];
      return base ? [[n, positive(norm(mul(conj(base), q)))] as const] : [];
    });
    commit((l) => ds.reduce((acc, [n, d]) => setKey(acc, n, { f, q: d, falloff: l.keys?.[n]?.find((k) => k.f === f)?.falloff ?? FALLOFF }), l));
    setSel(ds.map(([n]) => `${n}@${f}`));
  };
  const onPose = (name: string, q: Q) => (keyTurns({ [name]: q }), setBone(node(name)));
  // a move in the view: the offset at this frame grows by how far the bone went
  const onMove = (name: string, from: V3, to: V3) => {
    const n = node(name);
    const f = baseFrame();
    const os = layer.offsets?.[n];
    const now = os?.length ? offset(os, f) : [0, 0, 0];
    commit((l) => setOffset(l, n, { f, v: now.map((x, i) => Math.round((x + to[i] - from[i]) * 1e5) / 1e5) as V3, falloff: os?.find((o) => o.f === f)?.falloff ?? FALLOFF }));
    setBone(n);
    setSel([`${n}:move@${f}`]);
  };
  // M63: an IK key at this frame: where the control's target is to be (or how far its knee/elbow swings); a bone without a
  // control gets one, with the chain the panel says; what is not given stays as the control has it here
  const keyIk = (n: string, k: { p?: V3; swivel?: number }) => {
    const f = baseFrame();
    const c = layer.ik?.find((x) => x.bone === n);
    const here = c?.keys.find((x) => x.f === f);
    const now = c && ikAt(c.keys, f);
    const p = k.p ?? here?.p ?? now?.p ?? (solved && atIn(n, c?.space, pose(solved.bones, f))) ?? [0, 0, 0];
    const swivel = k.swivel ?? here?.swivel ?? now?.swivel ?? 0;
    commit((l) => setIk(l, c ?? { bone: n, chain: Math.min(chainN, Math.max(above(n), 2)) }, { f, p: r5(p), swivel: Math.round(swivel * 100) / 100, w: here?.w ?? 1, falloff: here?.falloff ?? FALLOFF }));
    setBone(n);
    setSel([`${n}:ik@${f}`]);
  };
  const onSwivel = (name: string, d: number) => {
    const n = node(name);
    const c = layer.ik?.find((x) => x.bone === n);
    const f = baseFrame();
    const here = c?.keys.find((x) => x.f === f);
    // the evaluation swings by swivel × weight: a key that weighs less takes the more for the swing the drag showed
    keyIk(n, { swivel: wrap((here?.swivel ?? (c && ikAt(c.keys, f))?.swivel ?? 0) + d / Math.max(here?.w ?? 1, 0.05)) });
  };
  const keyed = useMemo(
    () =>
      [...Object.entries(layer.keys ?? {}), ...Object.entries(layer.offsets ?? {}), ...(layer.ik ?? []).map((c) => [c.bone, c.keys] as const)]
        .filter(([, ks]) => ks.some((k) => k.f === frame))
        .map(([n]) => three(n) ?? n),
    [layer, frame, ready],
  );
  // the IK controls as the viewer draws them (and, in IK target, the one a drag would make on the selected bone)
  const shows: IkShow[] = !data
    ? []
    : [...(layer.ik ?? []), ...(gizmo === "target" && bone && !ctl ? [{ bone, chain: reach, keys: [] as IkKey[] }] : [])].map((c) => ({
        bone: three(c.bone) ?? c.bone,
        chain: chainOf(data.data.bones, c.bone, c.chain).map((n) => three(n) ?? n),
        space: c.space ? (three(c.space) ?? c.space) : null,
        p: c.keys.length ? (ikAt(c.keys, frame)?.p ?? null) : null,
        at: atIn(c.bone, c.space),
      }));
  useEffect(() => {
    if (!ready) return;
    v.setPoseEdit(
      original
        ? null
        : {
            selected: bone ? (three(bone) ?? null) : null,
            keyed,
            mode: gizmo,
            chain: reach,
            ik: shows,
            handle,
            onSelect: (b) => setBone(node(b)),
            onPose,
            onMove,
            onIK: keyTurns,
            onHandle: (b, h) => (setBone(node(b)), setMode("target"), setHandle(h)),
            onTarget: (b, h, e) => {
              const n = node(b);
              const at = solved && atIn(n, layer.ik?.find((c) => c.bone === n)?.space, pose(solved.bones, baseFrame()));
              keyIk(n, { p: at ? [at[0] + h[0] - e[0], at[1] + h[1] - e[1], at[2] + h[2] - e[2]] : h });
            },
            onSwivel,
          },
    );
  });

  // I keys the selected bone as it is now; X / Delete removes the selected keys; Ctrl+Z undoes
  useTransportKeys(v);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest?.("input,textarea,select")) return;
      if (e.ctrlKey || e.metaKey) {
        if (e.key.toLowerCase() === "z") (e.preventDefault(), e.shiftKey ? hist.redo() : hist.undo());
        return;
      }
      if (e.key === "i" || e.key === "I") insertKey();
      else if (e.key === "x" || e.key === "Delete") deleteKeys();
      // Blender's keys: R turns, G grabs (moves the hips, drags anything else by IK); Page Up/Down lengthen and shorten
      // the IK chain
      else if (e.key === "r" || e.key === "R") setMode("rotate");
      else if ((e.key === "g" || e.key === "G") && bone) setMode(canMove(bone) ? "move" : "ik");
      else if ((e.key === "PageUp" || e.key === "PageDown") && bone && (gizmo === "ik" || (gizmo === "target" && !ctl))) {
        e.preventDefault();
        setChainN(Math.min(Math.max(reach + (e.key === "PageUp" ? 1 : -1), 2), Math.max(above(bone), 2)));
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });
  const insertKey = () => {
    if (!bone || original) return;
    if (gizmo === "target") return keyIk(bone, {});
    const f = baseFrame();
    if (gizmo === "move") {
      const os = layer.offsets?.[bone];
      commit((l) => setOffset(l, bone, { f, v: os?.length ? offset(os, f) : [0, 0, 0], falloff: os?.find((o) => o.f === f)?.falloff ?? FALLOFF }));
      return setSel([`${bone}:move@${f}`]);
    }
    if (!track(bone)?.rotation) return;
    const ks = layer.keys?.[bone];
    commit((l) => setKey(l, bone, { f, q: ks?.length ? positive(delta(ks, f)) : I, falloff: ks?.find((k) => k.f === f)?.falloff ?? FALLOFF }));
    setSel([`${bone}@${f}`]);
  };
  const deleteKeys = () => {
    if (!sel.length) return;
    commit((l) => dropKeys(l, sel));
    setSel([]);
  };
  const dropIkKey = (n: string, f: number) => (commit((l) => dropKeys(l, [`${n}:ik@${f}`])), setSel([]));
  const moveKeys = (df: number) => {
    const moved: string[] = [];
    let out = dropKeys(layer, sel, true);
    for (const s of sel) {
      const { n, f, move, ik } = parse(s);
      const to = Math.min(Math.max(f + df, 0), F - 1);
      const k = layer.keys?.[n]?.find((x) => x.f === f);
      const o = layer.offsets?.[n]?.find((x) => x.f === f);
      const c = layer.ik?.find((x) => x.bone === n);
      const ki = c?.keys.find((x) => x.f === f);
      if (move && o) out = setOffset(out, n, { ...o, f: to });
      else if (ik && c && ki) out = setIk(out, c, { ...ki, f: to });
      else if (!move && !ik && k) out = setKey(out, n, { ...k, f: to });
      else continue;
      moved.push(`${n}${tag(move, ik)}@${to}`);
    }
    commit(() => out);
    setSel(moved);
  };

  // the curves: the selected bone's turn correction in degrees (its moves in cm in Move), its keys as points; built when
  // they change, not on every frame of playback
  const curves = useMemo((): Curves | null => {
    if (!curvesOn || !bone) return null;
    const frames = Array.from({ length: F }, (_, f) => f);
    const ks = layer.keys?.[bone];
    const os = layer.offsets?.[bone];
    // an IK control's target in its space (cm), held between its keys and beyond them (where its weight fades)
    const held = ctl && ctl.keys.map((k) => ({ ...k, w: 1, falloff: Infinity }));
    if (gizmo === "target")
      return ctl
        ? {
            unit: "cm",
            lines: ["X", "Y", "Z"].map((a, i) => ({ label: a, color: AXES[i], values: frames.map((f) => cm(ikAt(held!, f)!.p[i])) })),
            points: ctl.keys.map((k) => ({ id: `${bone}:ik@${k.f}`, f: k.f, values: k.p.map(cm) })),
          }
        : null;
    return gizmo === "move"
      ? {
          unit: "cm",
          lines: ["X", "Y", "Z"].map((a, i) => ({ label: a, color: AXES[i], values: frames.map((f) => (os?.length ? cm(offset(os, f)[i]) : 0)) })),
          points: (os ?? []).map((o) => ({ id: `${bone}:move@${o.f}`, f: o.f, values: o.v.map(cm) })),
        }
      : {
          unit: "°",
          lines: ["X", "Y", "Z"].map((a, i) => ({ label: a, color: AXES[i], values: frames.map((f) => (ks?.length ? toDegrees(delta(ks, f))[i] : 0)) })),
          points: (ks ?? []).map((k) => ({ id: `${bone}@${k.f}`, f: k.f, values: toDegrees(k.q) })),
        };
  }, [curvesOn, bone, gizmo, layer, F, ctl]);
  // the bones the base clip moves (a strip on their rows)
  const movingSet = useMemo(
    () =>
      new Set(
        (data?.data.bones ?? [])
          .filter((t) => !!t.translation || !!t.rotation?.some((q, f, r) => f > 0 && Math.abs(q[0] * r[f - 1][0] + q[1] * r[f - 1][1] + q[2] * r[f - 1][2] + q[3] * r[f - 1][3]) < 0.99999))
          .map((t) => t.name),
      ),
    [data],
  );

  if (error) return <p className="note warn">Could not open the animation: {error}</p>;
  if (!ready || !data) return <p className="muted">Loading the animation…</p>;

  // the dope sheet's rows: groups, their bones when open (a moving bone's moves on a row of their own)
  const moving = (n: string) => movingSet.has(n);
  const keysOf = (n: string) => (layer.keys?.[n] ?? []).map((k) => k.f);
  const movesOf = (n: string) => (layer.offsets?.[n] ?? []).map((o) => o.f);
  const ikOf = (n: string) => (layer.ik?.find((c) => c.bone === n)?.keys ?? []).map((k) => k.f);
  const locks = layer.feet ?? [];
  const spans: Record<string, [number, number][]> = {};
  for (const [g, nodes] of ready.groups) {
    for (const lk of locks)
      if (nodes.includes(lk.bone)) {
        (spans[lk.bone] ??= []).push([lk.from, lk.to]);
        (spans[`g:${g}`] ??= []).push([lk.from, lk.to]);
      }
    for (const c of layer.ik ?? []) // an IK control's bar: from its first key to its last
      if (nodes.includes(c.bone) && c.keys.length > 1) (spans[`${c.bone}:ik`] ??= []).push([c.keys[0].f, c.keys[c.keys.length - 1].f]);
  }
  const rows: Row[] = ready.groups.flatMap(([g, nodes]) => [
    { id: `g:${g}`, label: g, group: true, open: open.has(g), keys: [...new Set(nodes.flatMap((n) => [...keysOf(n), ...movesOf(n), ...ikOf(n)]))], moving: nodes.some(moving) },
    ...(open.has(g)
      ? nodes.flatMap((n) => [
          { id: n, label: n, group: false, keys: keysOf(n), moving: moving(n), active: n === bone && gizmo !== "move" && gizmo !== "target" },
          ...(canMove(n) ? [{ id: `${n}:move`, label: `${n} · move`, group: false, keys: movesOf(n), moving: true, active: n === bone && gizmo === "move" }] : []),
          ...(ikOf(n).length ? [{ id: `${n}:ik`, label: `${n} · IK`, group: false, keys: ikOf(n), moving: true, active: n === bone && gizmo === "target" }] : []),
        ])
      : []),
  ]);
  // every keyed bone, its group folded or not: the key list (UX-13) does not depend on what the timeline unfolded
  const keyRows: Row[] = ready.groups.flatMap(([, nodes]) =>
    nodes.flatMap((n) => [
      { id: n, label: n, group: false, keys: keysOf(n), moving: false },
      ...(canMove(n) ? [{ id: `${n}:move`, label: `${n} · move`, group: false, keys: movesOf(n), moving: true }] : []),
      ...(ikOf(n).length ? [{ id: `${n}:ik`, label: `${n} · IK`, group: false, keys: ikOf(n), moving: true }] : []),
    ]),
  );
  const pick = (n: string, move: boolean, ik = false) => {
    setBone(n);
    if (move) setMode("move");
    else if (ik) (setMode("target"), setHandle("target"));
    else if (mode === "move" || mode === "target") setMode("rotate");
  };
  const onSelectKeys = (ids: string[], add: boolean) => {
    const keys = ids.flatMap((id) => {
      if (!id.startsWith("g:")) return [id];
      const [g, f] = [id.slice(2, id.lastIndexOf("@")), +id.slice(id.lastIndexOf("@") + 1)];
      return (ready.groups.find(([name]) => name === g)?.[1] ?? []).flatMap((n) => [
        ...(keysOf(n).includes(f) ? [`${n}@${f}`] : []),
        ...(movesOf(n).includes(f) ? [`${n}:move@${f}`] : []),
        ...(ikOf(n).includes(f) ? [`${n}:ik@${f}`] : []),
      ]);
    });
    setSel((s) => (add ? [...new Set([...s, ...keys])] : keys));
    if (keys[0]) pick(parse(keys[0]).n, parse(keys[0]).move, parse(keys[0]).ik);
  };

  const here = bone ? layer.keys?.[bone]?.find((k) => k.f === frame) : undefined;
  // a lock whose leg (its bone, parent, grandparent) holds the selected bone at this frame
  const heldBy = locks.find((lk) => {
    const b = track(lk.bone);
    const l = b?.parent ? track(b.parent) : undefined;
    return frame >= lk.from - lk.blend && frame <= lk.to + lk.blend && [lk.bone, b?.parent, l?.parent].includes(bone ?? undefined);
  });
  // an IK control whose chain turns the selected bone here: a turn on it is redone by the IK while the control weighs
  const drivenBy = bone ? layer.ik?.find((c) => c.bone !== bone && ikAt(c.keys, frame) && chainOf(data.data.bones, c.bone, c.chain).includes(bone)) : undefined;
  const hereIk = ctl?.keys.find((k) => k.f === frame);
  const setIkKey = (k: IkKey) => commit((l) => setIk(l, ctl!, k));
  // a control's space changed: each key keeps its place at its own frame, re-expressed in the new space (between the
  // keys the target now moves with the new space)
  const setSpace = (c: Ik, space: string) => {
    const conv = (k: IkKey): IkKey => {
      const m = pose(solved!.bones, k.f);
      const from = c.space ? m.get(c.space) : undefined;
      const w: V3 = from ? (turn(from.W, k.p).map((x, i) => x + from.p[i]) as V3) : k.p;
      const to = space ? m.get(space) : undefined;
      return { ...k, p: r5(to ? turn(conj(to.W), [w[0] - to.p[0], w[1] - to.p[1], w[2] - to.p[2]]) : w) };
    };
    commit((l) => ({ ...l, ik: (l.ik ?? []).map((x) => (x.bone === c.bone ? { bone: x.bone, chain: x.chain, ...(space ? { space } : {}), keys: x.keys.map(conv) } : x)) }));
  };
  // n is `top` or hangs from it: the IK moves it, so it cannot carry the target
  const below = (n: string, top: string): boolean => n === top || (!!track(n)?.parent && below(track(n)!.parent!, top));
  const setChain = (c: Ik, n: number) => commit((l) => ({ ...l, ik: (l.ik ?? []).map((x) => (x.bone === c.bone ? { ...x, chain: n } : x)) }));
  const dropIk = (c: Ik) => commit((l) => ({ ...l, ik: (l.ik ?? []).filter((x) => x.bone !== c.bone).length ? (l.ik ?? []).filter((x) => x.bone !== c.bone) : undefined }));
  const hereMove = bone ? layer.offsets?.[bone]?.find((o) => o.f === frame) : undefined;
  const deg = here ? toDegrees(here.q) : null;
  const setDeg = (i: number, x: number) => here && commit((l) => setKey(l, bone!, { ...here, q: fromDegrees(deg!.map((d, j) => (j === i ? x : d)) as V3) }));
  const setCm = (i: number, x: number) => hereMove && commit((l) => setOffset(l, bone!, { ...hereMove, v: hereMove.v.map((d, j) => (j === i ? x / 100 : d)) as V3 }));
  const smoothOf = (g: string) => Math.max(0, ...(ready.groups.find(([name]) => name === g)?.[1] ?? []).map((n) => layer.smooth?.[n] ?? 0));
  const setSmooth = (g: string, s: number) =>
    commit((l) => ({ ...l, smooth: { ...l.smooth, ...Object.fromEntries((ready.groups.find(([name]) => name === g)?.[1] ?? []).filter((n) => track(n)?.rotation).map((n) => [n, s])) } }));
  const num = (x: string, lo: number, hi: number) => (Number.isFinite(+x) && x.trim() !== "" ? Math.min(Math.max(+x, lo), hi) : null);
  const snap = () => {
    const feet = v.feet();
    const low = v.lowest(feet.names);
    commit((l) => ({ ...l, ground: Math.round(((l.ground ?? 0) + feet.rest - low) * 1000) / 1000 }));
  };
  const edited = Object.keys(layers).filter((c) => hasEdits(layers[c]) || layers[c].from); // copies always: they exist only in the layer
  const isCopy = !!layers[clip]?.from;
  const names = (except = "") => [...Object.keys(ready?.clips ?? {}), ...Object.keys(layers)].filter((n) => n !== except);
  const ask = (msg: string, def: string, except = "") => {
    const n = window.prompt(msg, def)?.replace(/^ +| +$/g, "");
    if (!n) return null;
    if (!nameOk(n, names(except))) {
      window.alert(`${NAME_RULE}: "${n}" is not allowed or is taken.`);
      return null;
    }
    return n;
  };
  const duplicate = () => {
    let n = `${clip} copy`;
    for (let i = 2; !nameOk(n, names()); i++) n = `${clip} copy ${i}`;
    const name = ask("Name of the copy", n);
    if (!name) return;
    hist.commit((all) => ({ ...all, [name]: { ...structuredClone(all[clip] ?? {}), from: rootOf(clip) } }));
    setClip(name);
    setSel([]);
  };
  const rename = () => {
    const name = ask("New name", clip, clip);
    if (!name) return;
    hist.commit(({ [clip]: l, ...rest }) => ({ ...rest, [name]: l }));
    setClip(name);
  };
  const drop = () => {
    if (!window.confirm(`Delete the copy ${clip}?`)) return;
    const to = rootOf(clip);
    hist.commit(({ [clip]: _, ...rest }) => rest);
    setClip(to);
    setSel([]);
  };

  // the foot locks: found on the clip as edited so far, before the locks (the space they work in); a bone's rest height is
  // its own and its mirror's mean, so both sides pick the same bone of a skeleton fitted a little unevenly. M64 hands: the
  // bones named Hand with an arm above them, locked where a palm rests on the floor
  const findContacts = (kind: "feet" | "hands" = "feet") => {
    const { heights } = v.feet();
    const mirror = (n: string) =>
      n.startsWith("Left") ? "Right" + n.slice(4) : n.startsWith("Right") ? "Left" + n.slice(5) : ready.sides?.map((s, i) => (n.endsWith(s) ? n.slice(0, -s.length) + ready.sides![1 - i] : "")).find(Boolean);
    const height = (b: Bone) => {
      const m = ready.bones.find((x) => x.node === mirror(b.node));
      return m ? (heights[b.name] + heights[m.name]) / 2 : heights[b.name];
    };
    const near = new Set(ready.bones.filter((b) => height(b) <= 0.2).map((b) => b.name));
    const feet =
      kind === "hands"
        ? ready.bones.filter((b) => /hand$/i.test(b.node) && chain(b.node)).map((b) => b.node)
        : ready.bones.filter((b) => near.has(b.name) && !(b.parent && near.has(b.parent)) && !/tail/i.test(b.node) && chain(b.node)).map((b) => b.node);
    const pre = evaluate(data.data, { keys: layer.keys, offsets: layer.offsets, smooth: layer.smooth });
    const others = (layer.feet ?? []).filter((lk) => !feet.includes(lk.bone));
    const all = kind === "hands" ? contacts(pre, [], feet) : contacts(pre, feet);
    const room = Math.max(MAX_LOCKS - others.length, 0); // anim_edit takes 64: the longest stretches first
    const hits = [...all]
      .sort((a, b) => b.to - b.from - (a.to - a.from))
      .slice(0, room)
      .sort((a, b) => a.bone.localeCompare(b.bone) || a.from - b.from);
    commit(() => ({ ...layer, feet: [...others, ...hits] }));
    setFound(
      !feet.length
        ? kind === "hands"
          ? "No hands found (bones named Hand with an arm above them)."
          : "No feet found near the floor."
        : all.length
          ? `${all.length} still stretches on ${feet.join(", ")}${hits.length < all.length ? `; the ${hits.length} longest locked (${MAX_LOCKS} at most)` : ""}.`
          : kind === "hands"
            ? `No hand rests on the floor in this clip (${feet.join(", ")}).`
            : `No foot stands still in this clip (${feet.join(", ")}): a walk or run in place slides its feet on purpose.`,
    );
  };
  const setLock = (i: number, lk: Lock | null) => commit((l) => ({ ...l, feet: (l.feet ?? []).flatMap((x, j) => (j !== i ? [x] : lk ? [lk] : [])) }));

  const onCurve = (id: string, i: number, x: number) => {
    const { n, f, move } = parse(id);
    const k = layer.keys?.[n]?.find((y) => y.f === f);
    const o = layer.offsets?.[n]?.find((y) => y.f === f);
    const c = layer.ik?.find((y) => y.bone === n);
    const ki = c?.keys.find((y) => y.f === f);
    if (move && o) commit((l) => setOffset(l, n, { ...o, v: o.v.map((d, j) => (j === i ? x / 100 : d)) as V3 }));
    else if (parse(id).ik && c && ki) commit((l) => setIk(l, c, { ...ki, p: ki.p.map((d, j) => (j === i ? x / 100 : d)) as V3 }));
    else if (!move && k) commit((l) => setKey(l, n, { ...k, q: fromDegrees(toDegrees(k.q).map((d, j) => (j === i ? x : d)) as V3) }));
  };

  return (
    <div className="stack anim-editor">
      <label>
        Clip
        <select aria-label="Clip to edit" value={clip} onChange={(e) => (setClip(e.target.value), setSel([]), setFound(null))}>
          {[...Object.keys(ready.clips), ...Object.keys(layers).filter((c) => layers[c].from)].map((c) => (
            <option key={c} value={c}>
              {c}
              {layers[c]?.from ? ` (copy of ${layers[c].from})` : ""}
              {hasEdits(layers[c]) ? " (edited)" : ""}
            </option>
          ))}
        </select>
      </label>
      <div className="row">
        <button className="small" onClick={duplicate}>
          Duplicate
        </button>
        {isCopy && (
          <>
            <button className="small" onClick={rename}>
              Rename
            </button>
            <button className="small" onClick={drop}>
              Delete
            </button>
          </>
        )}
      </div>
      <div role="group" aria-label="Compare" className="row">
        <button aria-pressed={!original} onClick={() => setOriginal(false)}>
          Edited
        </button>
        <button aria-pressed={original} onClick={() => setOriginal(true)}>
          Original
        </button>
      </div>
      <details>
        <summary>Help</summary>
        <ul className="muted">
        <li>Click a joint in the view (or a bone in the timeline) to select it, then turn it with the rings: that keys it at this frame.</li>
        <li>Move shifts the hips with arrows.</li>
        <li>
          IK drag and IK target both bend an arm or a leg so the hand or foot reaches a place (Chain: how many bones above it bend, 2 for an arm or a leg; Page Up/Down). They differ in what they leave
          behind:
        </li>
        <li>
          IK drag poses this one frame: drag the hand or foot and the bones above it turn to follow; each of them gets an ordinary turn key here, as if you had turned them one by one. Nothing stays
          attached: on other frames the hand goes where the clip takes it. Use it to fix a pose at a moment (a punch that falls short, an elbow too low).
        </li>
        <li>
          IK target pins the hand or foot to a target (yellow box) that the IK reaches on every frame it covers. Select the bone, press IK target, then drag the box or press Pin it here (I): that
          makes the bone's IK control with a key at this frame. Go to another frame and drag again (or Key it here) for a second key; between keys the target glides from one to the next, past the
          first and last key it lets go over the falloff. Use it to hold a foot on the ground, a hand on a wall or a weapon on the other hand while the body moves.
        </li>
        <li>
          On an IK control: the diamond (Pole) swings the knee or elbow around the line to the target, as Swivel does in degrees; Weight blends the clip (0 %) and the target (100 %) at each key;
          Space keeps the target still in the world, or carries it on a bone (a hand resting on the hips moves with the hips). Remove IK control drops it and all its keys; Ctrl+Z brings it back.
        </li>
        <li>The skeleton is drawn as Blender draws an armature: the selected bone blue, the bones an IK control bends yellow, a key at this frame orange; dashed lines join a bone to a parent it does not touch. Click a joint or a bone.</li>
        <li>A key fades in and out over its falloff, so the motion around it stays smooth. Keys are diamonds; drag them in time, or switch the timeline to Curves.</li>
        <li>Keys: Space plays, ←/→ a frame, I keys the bone as it is, X deletes the selected keys, R rotates, G grabs (moves the hips, IK-drags the rest), Ctrl+Z undoes; Ctrl+wheel zooms the timeline.</li>
        <li>Timeline: Keys shows where each bone is keyed; Curves, with a bone selected, draws its turn over the clip as lines whose points you drag.</li>
        <li>Clip tools work on the whole clip: Trim keeps the frames from first to last, Loop match blends the last frames into the first so the clip loops without a jump, Ground lifts or lowers it.</li>
        <li>Without a mouse: Keys as a list (under Clip tools) selects a key, moves it a frame at a time, and in Curves edits its values.</li>
        <li>Show: Motion path draws the selected bone's track over the clip; Onion skin shows the model a few frames before (blue) and after (green).</li>
        </ul>
      </details>
      <section className="stack" aria-label="Bone">
        <h4>Bone {bone ? `· ${bone}` : ""}</h4>
        {!bone && <p className="muted">No bone selected.</p>}
        {bone && (
          <div role="group" aria-label="Gizmo" className="modes">
            <button aria-pressed={gizmo === "rotate"} onClick={() => setMode("rotate")}>
              Rotate
            </button>
            <button aria-pressed={gizmo === "move"} disabled={!canMove(bone)} title={canMove(bone) ? "" : "This bone does not move in the clip"} onClick={() => setMode("move")}>
              Move
            </button>
            <button aria-pressed={gizmo === "ik"} disabled={!chain(bone)} title={chain(bone) ? "Pose this frame: drag the hand or foot, the bones above it get turn keys here" : "Needs two turning bones above it"} onClick={() => setMode("ik")}>
              IK drag
            </button>
            <button aria-pressed={gizmo === "target"} disabled={!chain(bone)} title={chain(bone) ? "Pin the hand or foot to a target held over frames (an IK control)" : "Needs two turning bones above it"} onClick={() => (setMode("target"), setHandle("target"))}>
              IK target
            </button>
          </div>
        )}
        {bone && (gizmo === "ik" || (gizmo === "target" && !ctl)) && (
          <label title="How many bones above it bend (Page Up/Down): 2 for an arm or a leg, more for a spine, a neck or a tail">
            Chain{" "}
            <input
              type="number"
              min={2}
              max={Math.max(above(bone), 2)}
              aria-label="Chain bones"
              key={`ch${reach}${bone}`}
              defaultValue={reach}
              style={{ width: "4em" }}
              onBlur={(e) => {
                const x = num(e.target.value, 2, Math.max(above(bone), 2));
                if (x !== null) setChainN(Math.round(x));
              }}
              onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
            />{" "}
            bones · {chainOf(data.data.bones, bone, reach).slice(0, -1).reverse().join(" ← ")}
          </label>
        )}
        {/* why a tool is off, said where a keyboard and a touch screen see it too (UX-15) */}
        {bone && (!canMove(bone) || !chain(bone)) && (
          <p className="muted">
            {[!canMove(bone) && "Move: this bone does not move in the clip.", !chain(bone) && "IK drag and IK target: need two turning bones above it."].filter(Boolean).join(" ")}
          </p>
        )}
        {bone && drivenBy && gizmo !== "target" && (
          <p className="note">
            The IK control on {drivenBy.bone} bends this bone here: a turn or IK drag on it is redone by the IK while the control weighs (set its weight to 0 here, or work in IK target on {drivenBy.bone}).
          </p>
        )}
        {bone && gizmo === "target" && !ctl && (
          <p className="muted">
            No IK control on this bone. Drag the yellow box in the view: the bone gets one keyed at frame {frame}, its {reach} bones above bending to reach it on every frame it covers (to pose
            just this frame, use IK drag).{" "}
            <button className="small" onClick={insertKey} disabled={original}>
              Pin it here (I)
            </button>
          </p>
        )}
        {bone && gizmo === "target" && ctl && (
          <>
            <div className="row" role="group" aria-label="IK control">
              <label>
                Chain{" "}
                <input
                  type="number"
                  min={2}
                  max={Math.max(above(bone), 2)}
                  aria-label="IK chain bones"
                  key={`icn${ctl.chain}${bone}`}
                  defaultValue={ctl.chain}
                  style={{ width: "4em" }}
                  onBlur={(e) => {
                    const x = num(e.target.value, 2, Math.max(above(bone), 2));
                    if (x !== null && Math.round(x) !== ctl.chain) setChain(ctl, Math.round(x));
                  }}
                  onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                />
              </label>
              <label title="World: the target stays put while the body moves. A bone: it rides on that bone (a hand on the hips or the chest).">
                Space{" "}
                <select aria-label="IK space" value={ctl.space ?? ""} onChange={(e) => setSpace(ctl, e.target.value)}>
                  <option value="">World</option>
                  {ready.bones
                    .map((b) => b.node)
                    .filter((n) => track(n) && !below(n, chainOf(data.data.bones, bone, ctl.chain)[0]))
                    .map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                </select>
              </label>
              <div role="group" aria-label="IK handle" className="row">
                <button aria-pressed={handle === "target"} onClick={() => setHandle("target")} title="The gizmo on the target (yellow box)">
                  Target
                </button>
                <button aria-pressed={handle === "pole"} onClick={() => setHandle("pole")} title="The gizmo on the pole (yellow diamond): swings the knee or elbow">
                  Pole
                </button>
              </div>
            </div>
            {!hereIk && (
              <p className="muted">
                No IK key at frame {frame}.{" "}
                <button className="small" onClick={insertKey} disabled={original}>
                  Key it here (I)
                </button>
              </p>
            )}
            {hereIk && (
              <>
                <div className="row" role="group" aria-label="IK target at this key">
                  {(["X", "Y", "Z"] as const).map((a, i) => (
                    <label key={a + frame + bone + hereIk.p[i]}>
                      {a}{" "}
                      <input
                        type="number"
                        step="0.5"
                        aria-label={`IK ${a} cm`}
                        defaultValue={cm(hereIk.p[i])}
                        style={{ width: "5em" }}
                        onBlur={(e) => {
                          const x = num(e.target.value, -1e5, 1e5);
                          if (x !== null && Math.abs(x - cm(hereIk.p[i])) > 1e-3) setIkKey({ ...hereIk, p: hereIk.p.map((d, j) => (j === i ? x / 100 : d)) as V3 });
                        }}
                        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                      />
                      cm
                    </label>
                  ))}
                </div>
                <div className="row">
                  {(
                    [
                      ["Swivel", "°", "IK swivel degrees", hereIk.swivel, -180, 180, (x: number) => ({ swivel: x })],
                      ["Weight", "%", "IK weight percent", Math.round(hereIk.w * 100), 0, 100, (x: number) => ({ w: x / 100 })],
                      ["Falloff", "frames", "IK falloff frames", hereIk.falloff, 0, F, (x: number) => ({ falloff: Math.round(x) })],
                    ] as const
                  ).map(([label, unit, aria, value, lo, hi, set]) => (
                    <label key={label + frame + bone + value}>
                      {label}{" "}
                      <input
                        type="number"
                        aria-label={aria}
                        defaultValue={value}
                        style={{ width: "4.5em" }}
                        onBlur={(e) => {
                          const x = num(e.target.value, lo, hi);
                          if (x !== null && x !== value) setIkKey({ ...hereIk, ...set(x) });
                        }}
                        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                      />{" "}
                      {unit}
                    </label>
                  ))}
                </div>
                <div className="row">
                  <button onClick={() => dropIkKey(bone, frame)}>Delete IK key</button>
                  <button onClick={() => dropIk(ctl)}>Remove IK control</button>
                </div>
              </>
            )}
          </>
        )}
        {bone && heldBy && (
          <p className="note">
            The foot lock on {heldBy.bone} (frames {heldBy.from}–{heldBy.to}) holds this leg here: a turn or IK drag on it is undone by the lock.
          </p>
        )}
        {bone && gizmo !== "move" && gizmo !== "target" && !here && (
          <p className="muted">
            No key at frame {frame}.{" "}
            <button className="small" onClick={insertKey} disabled={original}>
              Key it here (I)
            </button>
          </p>
        )}
        {bone && gizmo !== "move" && gizmo !== "target" && here && deg && (
          <>
            <div className="row" role="group" aria-label="Turn at this key">
              {(["X", "Y", "Z"] as const).map((a, i) => (
                <label key={a + frame + bone + deg[i].toFixed(1)}>{/* the value too: a curve drag or the key list changes it */}
                  {a}{" "}
                  <input
                    type="number"
                    step="1"
                    aria-label={`${a} degrees`}
                    defaultValue={deg[i].toFixed(1)}
                    style={{ width: "5em" }}
                    onBlur={(e) => {
                      const x = num(e.target.value, -180, 180);
                      if (x !== null && Math.abs(x - deg[i]) > 1e-3) setDeg(i, x);
                    }}
                    onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                  />
                  °
                </label>
              ))}
            </div>
            <label>
              Falloff{" "}
              <input
                type="number"
                min={0}
                max={F}
                aria-label="Falloff frames"
                key={`fo${frame}${bone}`}
                defaultValue={here.falloff}
                style={{ width: "5em" }}
                onBlur={(e) => {
                  const x = num(e.target.value, 0, F);
                  if (x !== null && Math.round(x) !== here.falloff) commit((l) => setKey(l, bone, { ...here, falloff: Math.round(x) }));
                }}
              />{" "}
              frames
            </label>
            <div className="row">
              <button
                title="This turn from the first frame to the last (two equal keys at the ends)"
                onClick={() => (commit((l) => setKey(setKey(dropKeys(l, (l.keys?.[bone] ?? []).map((k) => `${bone}@${k.f}`)), bone, { f: 0, q: here.q, falloff: 0 }), bone, { f: F - 1, q: here.q, falloff: 0 })), setSel([]))}
              >
                Whole clip
              </button>
              <button onClick={() => (commit((l) => dropKeys(l, [`${bone}@${frame}`])), setSel([]))}>Delete key</button>
            </div>
          </>
        )}
        {bone && gizmo === "move" && !hereMove && (
          <p className="muted">
            No move at frame {frame}: drag the arrows in the view, or{" "}
            <button className="small" onClick={insertKey} disabled={original}>
              key it here (I)
            </button>
          </p>
        )}
        {bone && gizmo === "move" && hereMove && (
          <>
            <div className="row" role="group" aria-label="Move at this key">
              {(["X", "Y", "Z"] as const).map((a, i) => (
                <label key={a + frame + bone + hereMove.v[i]}>
                  {a}{" "}
                  <input
                    type="number"
                    step="0.5"
                    aria-label={`${a} cm`}
                    defaultValue={cm(hereMove.v[i])}
                    style={{ width: "5em" }}
                    onBlur={(e) => {
                      const x = num(e.target.value, -1000, 1000);
                      if (x !== null && Math.abs(x - cm(hereMove.v[i])) > 1e-3) setCm(i, x);
                    }}
                    onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                  />
                  cm
                </label>
              ))}
            </div>
            <label>
              Falloff{" "}
              <input
                type="number"
                min={0}
                max={F}
                aria-label="Move falloff frames"
                key={`mfo${frame}${bone}`}
                defaultValue={hereMove.falloff}
                style={{ width: "5em" }}
                onBlur={(e) => {
                  const x = num(e.target.value, 0, F);
                  if (x !== null && Math.round(x) !== hereMove.falloff) commit((l) => setOffset(l, bone, { ...hereMove, falloff: Math.round(x) }));
                }}
              />{" "}
              frames
            </label>
            <button onClick={() => (commit((l) => dropKeys(l, [`${bone}:move@${frame}`])), setSel([]))}>Delete move</button>
          </>
        )}
        {bone && ((layer.keys?.[bone]?.length ?? 0) > 0 || (layer.offsets?.[bone]?.length ?? 0) > 0) && (
          <button
            onClick={() =>
              (commit((l) => dropKeys(l, [...(l.keys?.[bone] ?? []).map((k) => `${bone}@${k.f}`), ...(l.offsets?.[bone] ?? []).map((o) => `${bone}:move@${o.f}`)])), setSel([]))
            }
          >
            Reset bone (all its keys)
          </button>
        )}
      </section>
      <details>
        <summary>Foot locks{locks.length ? ` (${locks.length})` : ""}</summary>
        <div className="stack">
          <p className="muted">A lock holds a foot still over its frames (stops sliding); the leg bends to keep it there. A hand resting on the floor the same way.</p>
          <div className="row">
            <button onClick={() => findContacts()} disabled={original}>
              Find foot contacts
            </button>
            <button onClick={() => findContacts("hands")} disabled={original}>
              Find hand contacts
            </button>
            {bone && chain(bone) && (
              <button
                disabled={original || locks.length >= MAX_LOCKS}
                onClick={() => commit((l) => ({ ...l, feet: [...(l.feet ?? []), { bone, from: Math.max(frame - 2, 0), to: Math.min(frame + 2, F - 1), blend: Math.min(3, F) }] }))}
              >
                Lock {bone} here
              </button>
            )}
          </div>
          {found && <p className="muted">{found}</p>}
          {locks.map((lk, i) => (
            <div key={`${i}${lk.bone}${lk.from}${lk.to}${lk.blend}`} className="row" role="group" aria-label={`Lock ${i + 1}`}>
              <button className="small" onClick={() => (setBone(lk.bone), seekBase(lk.from))}>
                {lk.bone}
              </button>
              {(["from", "to", "blend"] as const).map((k) => (
                <input
                  key={k}
                  type="number"
                  min={0}
                  max={F - 1}
                  aria-label={`Lock ${i + 1} ${k}`}
                  title={k === "blend" ? "Frames it eases in and out over" : `${k === "from" ? "First" : "Last"} frame`}
                  defaultValue={lk[k]}
                  style={{ width: "3.6em" }}
                  onBlur={(e) => {
                    const x = num(e.target.value, 0, k === "blend" ? F : F - 1);
                    if (x === null || Math.round(x) === lk[k]) return;
                    const n = { ...lk, [k]: Math.round(x) };
                    if (n.from <= n.to) setLock(i, n);
                    else e.target.value = String(lk[k]); // from after to: refused
                  }}
                />
              ))}
              <button aria-label={`Remove lock ${i + 1}`} onClick={() => setLock(i, null)}>
                ✕
              </button>
            </div>
          ))}
          {locks.length > 0 && <p className="muted">Frames from, to, and the blend frames on each side.</p>}
        </div>
      </details>
      <details>
        <summary>IK controls{layer.ik?.length ? ` (${layer.ik.length})` : ""}</summary>
        <div className="stack">
          <p className="muted">A control holds a bone on a target keyed over time, the bones above it bending (select a hand or foot, IK target). Applied in this order, after the locks.</p>
          {(layer.ik ?? []).map((c, i) => (
            <div key={c.bone} className="row" role="group" aria-label={`IK control ${i + 1}`}>
              <button className="small" onClick={() => (pick(c.bone, false, true), seekBase(c.keys[0].f))}>
                {c.bone}
              </button>
              <span className="muted">
                {c.chain} bones · {c.space ?? "world"} · {c.keys.length} key{c.keys.length > 1 ? "s" : ""} ({c.keys[0].f}
                {c.keys.length > 1 ? `–${c.keys[c.keys.length - 1].f}` : ""})
              </span>
              <button aria-label={`Remove IK control ${i + 1}`} onClick={() => dropIk(c)}>
                ✕
              </button>
            </div>
          ))}
        </div>
      </details>
      <details>
        <summary>Clip tools</summary>
        <div className="stack">
          <div className="fields">
          <label>
            Trim
            <span className="row" role="group" aria-label="Trim">
            {[0, 1].map((i) => (
              <input
                key={`t${i}${trim[i]}`}
                type="number"
                aria-label={i ? "Last frame" : "First frame"}
                defaultValue={trim[i]}
                style={{ width: "5em" }}
                onBlur={(e) => {
                  const x = num(e.target.value, 0, F - 1);
                  if (x === null) return;
                  const t: [number, number] = i ? [trim[0], Math.max(Math.round(x), trim[0] + 1)] : [Math.min(Math.round(x), trim[1] - 1), trim[1]];
                  commit((l) => ({ ...l, trim: t[0] === 0 && t[1] === F - 1 ? undefined : t }));
                }}
              />
            ))}
            </span>
          </label>
          <label>
            Speed
            <span className="row">
            <input
              type="number"
              step="0.05"
              min={0.1}
              max={10}
              aria-label="Speed"
              key={`s${speed}`}
              defaultValue={speed}
              style={{ width: "5em" }}
              onBlur={(e) => {
                const x = num(e.target.value, 0.1, 10);
                if (x !== null && x !== speed) commit((l) => ({ ...l, speed: x === 1 ? undefined : x }));
              }}
            />
            ×
            </span>
          </label>
          <label title="The last frames blend into the first, so the clip loops without a jump">
            Loop match
            <span className="row">
            <input
              type="number"
              min={0}
              aria-label="Loop frames"
              key={`l${layer.loop ?? 0}`}
              defaultValue={layer.loop ?? 0}
              style={{ width: "5em" }}
              onBlur={(e) => {
                const x = num(e.target.value, 0, trim[1] - trim[0]);
                if (x !== null && Math.round(x) !== (layer.loop ?? 0)) commit((l) => ({ ...l, loop: Math.round(x) || undefined }));
              }}
            />
            frames
            </span>
          </label>
          <label>
            Ground
            <span className="row">
              <input
                type="number"
                step="0.005"
                aria-label="Ground offset"
                key={`gr${layer.ground ?? 0}`}
                defaultValue={layer.ground ?? 0}
                style={{ width: "6em" }}
                onBlur={(e) => {
                  const x = num(e.target.value, -10, 10);
                  if (x !== null && x !== (layer.ground ?? 0)) commit((l) => ({ ...l, ground: x || undefined }));
                }}
              />
              m
            </span>
          </label>
          <button style={{ gridColumn: 2, justifySelf: "start" }} onClick={snap}>
            Snap to ground
          </button>
          </div>
          <label className="check">
            <input type="checkbox" checked={!!layer.in_place} onChange={(e) => commit((l) => ({ ...l, in_place: e.target.checked || undefined }))} />
            In place (no travel)
          </label>
          <fieldset>
            <legend>Smoothing (steadies jitter, e.g. from video)</legend>
            {ready.groups.map(([g]) => (
              <label key={g} className="smooth-row">
                <span>{g}</span>
                <Slider value={smoothOf(g)} label={`Smooth ${g}`} onCommit={(x) => setSmooth(g, x)} />
              </label>
            ))}
          </fieldset>
        </div>
      </details>
      <KeyList rows={keyRows} selected={sel} curves={curves} onSelect={onSelectKeys} onSeek={seekBase} onMove={moveKeys} onCurve={onCurve} />
      <div className="row">
        <button disabled={busy || !hist.h?.past.length} onClick={hist.undo}>
          Undo
        </button>
        <button disabled={busy || !hist.h?.future.length} onClick={hist.redo}>
          Redo
        </button>
      </div>
      <div className="row">
        <button
          className="primary"
          disabled={busy || !ready || !hist.h?.past.length}
          title="Saves what changed since this version was opened; with every edit undone or the last copy deleted, the base comes back as it was"
          onClick={() =>
            run(async () => {
              // what changed since the load is what to save, also when nothing is left edited: the step wants a
              // non-empty layer and starts from the base, so one base clip's empty layer is that reset (UI-002).
              // The editor closes on a new version only; a refused or failed save keeps the edits (UI-001)
              const clips = edited.length ? Object.fromEntries(edited.map((c) => [c, layers[c]])) : { [Object.keys(ready!.clips)[0]]: {} };
              if (await props.onSave(clips)) props.onClose();
            })
          }
        >
          Save as a new version
        </button>
        <button disabled={busy} onClick={() => leave() && props.onClose()}>
          Close
        </button>
        {dirty && <span className="muted">Unsaved changes</span>}
      </div>
      {edited.length > 0 && <p className="muted">Edited: {edited.join(", ")}. The original animation stays as it is.</p>}
      {document.getElementById("timeline-slot") &&
        createPortal(
          <EditorTimeline
            viewer={v}
            frames={F}
            trim={trim}
            baseFrame={baseFrame}
            rows={rows}
            spans={spans}
            curves={curves}
            selected={sel}
            onFrame={setFrame}
            onSeek={seekBase}
            onSelect={onSelectKeys}
            onMove={moveKeys}
            onCurve={onCurve}
            onTrim={(t) => commit((l) => ({ ...l, trim: t[0] === 0 && t[1] === F - 1 ? undefined : t }))}
            onRow={(id) => {
              if (!id.startsWith("g:")) return pick(parse(`${id}@0`).n, id.endsWith(":move"), id.endsWith(":ik"));
              const g = id.slice(2);
              setOpen((o) => new Set(o.has(g) ? [...o].filter((x) => x !== g) : [...o, g]));
            }}
            tools={
              <>
                <div role="group" aria-label="Timeline view" className="tb-section">
                  <span className="tb-head">Timeline</span>
                  <button aria-pressed={!curvesOn} onClick={() => setCurvesOn(false)}>
                    Keys
                  </button>
                  <button aria-pressed={curvesOn} disabled={!bone} title={bone ? "" : "Select a bone first"} onClick={() => setCurvesOn(true)}>
                    Curves
                  </button>
                </div>
                <div role="group" aria-label="Show in the view" className="tb-section">
                  <span className="tb-head">Show</span>
                  <label className="check" title="The selected bone's track over the clip">
                    <input type="checkbox" checked={path} onChange={(e) => setPath(e.target.checked)} />
                    Motion path
                  </label>
                  {path && !bone && <span className="muted">select a joint</span>}
                  <label className="check" title="Ghosts of the model a few frames before (blue) and after (green)">
                    <input type="checkbox" checked={onion} onChange={(e) => setOnion(e.target.checked)} />
                    Onion skin
                  </label>
                  <label className="check" title="The model see-through, so the skeleton inside it reads">
                    <input type="checkbox" checked={xray} onChange={(e) => setXray(e.target.checked)} />
                    X-ray
                  </label>
                </div>
              </>
            }
          />,
          document.getElementById("timeline-slot")!,
        )}
    </div>
  );
}

// a smoothing slider: it moves freely, the layer changes once, on release (one undo step, one evaluation)
function Slider(p: { value: number; label: string; onCommit: (x: number) => void }) {
  const [x, setX] = useState<number | null>(null);
  const done = () => x !== null && (x !== p.value && p.onCommit(x), setX(null));
  return (
    <>
      <input type="range" min={0} max={4} step={0.5} aria-label={p.label} value={x ?? p.value} onChange={(e) => setX(+e.target.value)} onPointerUp={done} onKeyUp={done} onBlur={done} />
      <span>{(x ?? p.value) ? `${x ?? p.value} frames` : "off"}</span>
    </>
  );
}

// the transport and the dope sheet under the view; the playhead follows the viewer every animation frame
function EditorTimeline(p: {
  viewer: Viewer;
  frames: number;
  trim: [number, number];
  baseFrame: () => number;
  rows: Row[];
  spans: Record<string, [number, number][]>;
  curves: Curves | null;
  selected: string[];
  tools: ReactNode;
  onFrame: (f: number) => void;
  onSeek: (f: number) => void;
  onSelect: (keys: string[], add: boolean) => void;
  onMove: (df: number) => void;
  onCurve: (id: string, line: number, value: number) => void;
  onTrim: (t: [number, number]) => void;
  onRow: (id: string) => void;
}) {
  useFrame();
  const f = p.baseFrame();
  const last = useRef(-1);
  useEffect(() => {
    if (f !== last.current) {
      last.current = f;
      p.onFrame(f);
    }
  });
  return (
    <>
      <Transport viewer={p.viewer}>
        <span className="grow" />
        {p.tools}
      </Transport>
      <DopeSheet
        frames={p.frames}
        frame={f}
        trim={p.trim}
        rows={p.rows}
        spans={p.spans}
        curves={p.curves}
        selected={p.selected}
        onFrame={p.onSeek}
        onSelect={p.onSelect}
        onMove={p.onMove}
        onCurve={p.onCurve}
        onTrim={p.onTrim}
        onRow={p.onRow}
      />
    </>
  );
}
