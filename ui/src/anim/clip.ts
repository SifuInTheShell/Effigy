// M37: an animation clip as plain arrays (a sample per frame), the editor's correction layer, and its evaluation:
// the same maths as steps/_anim.py, both checked against tests/fixtures/anim_edit/case.json (docs/IMPLEMENTATION.md
// §3.2 anim_edit). Rotations are glTF node-local quaternions (x, y, z, w), what three.js animates as bone.quaternion.
import * as THREE from "three";

export type Q = [number, number, number, number];
export type V3 = [number, number, number];
// a bone without a moving translation carries its constant one (`offset`): the skeleton's shape for the foot locks' FK
// M69: `rest` is its node's rest turn and translation (the mirror's reference)
export type Track = { name: string; parent: string | null; rotation?: Q[]; translation?: V3[]; offset?: V3; rest?: { q: Q; t: V3 } };
// M38: a foot (M64: or a hand on the floor) held still over base frames; M69 `reach`: a share of the leg's length past
// which the hips move toward the pinned foot, the least that brings it back
export type Lock = { bone: string; from: number; to: number; blend: number; reach?: number };
export type Clip = { fps: number; frames: number; bones: Track[] };
export type Key = { f: number; q: Q; falloff: number };
export type Offset = { f: number; v: V3; falloff: number };
// M63: an IK control: `bone` reaches its keyed target, the `chain` bones above it turning; `p` in skeleton space, or in
// the `space` bone's frame (the target then moves with that bone); `swivel` turns the knee/elbow about the chain's
// root–tip line (degrees); `w` the weight (0: the clip as it is, 1: on the target)
export type IkKey = { f: number; p: V3; swivel: number; w: number; falloff: number };
export type Ik = { bone: string; chain: number; space?: string; keys: IkKey[] };
export type Layer = {
  keys?: Record<string, Key[]>;
  offsets?: Record<string, Offset[]>;
  trim?: [number, number];
  speed?: number;
  loop?: number;
  ground?: number;
  in_place?: boolean;
  smooth?: Record<string, number>;
  feet?: Lock[];
  ik?: Ik[];
  from?: string; // M44: a copy of this base clip (the layer names no base clip of its own)
  mirror?: boolean; // M69: the clip as its mirror image (step 1b)
  filter?: "gaussian" | "butterworth"; // M69: the smoothing's filter (default gaussian)
};

// M44: a copy's name, the rule of steps/anim_edit.py (no control characters, none of / \ : | , [ ] ;), unique ignoring case
export const NAME_RULE = "1-64 characters, no space at either end, none of / \\ : | , [ ] ; or control characters";
// eslint-disable-next-line no-control-regex
export const nameOk = (n: string, others: string[]) =>
  n === n.replace(/^ +| +$/g, "") && [...n].length >= 1 && [...n].length <= 64 && !/[\x00-\x1f\x7f/\\:|,[\];]/.test(n) && !others.some((o) => o.toLowerCase() === n.toLowerCase());

export const I: Q = [0, 0, 0, 1];
export const mul = (a: Q, b: Q): Q => [
  a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
  a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
  a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
  a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2],
];
export const conj = (q: Q): Q => [-q[0], -q[1], -q[2], q[3]];
const dot = (a: Q, b: Q) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
export const norm = (q: Q): Q => {
  const n = Math.hypot(...q) || 1;
  return [q[0] / n, q[1] / n, q[2] / n, q[3] / n];
};
/** The same rotation with w ≥ 0 (a stored delta). */
export const positive = (q: Q): Q => (q[3] < 0 ? [-q[0], -q[1], -q[2], -q[3]] : q);
/** Shortest-path slerp; nearly equal quaternions by normalised lerp. */
export function slerp(a: Q, b: Q, t: number): Q {
  let c = dot(a, b);
  const s = c < 0 ? -1 : 1;
  c *= s;
  if (c > 0.9995) return norm([0, 1, 2, 3].map((i) => a[i] + (s * b[i] - a[i]) * t) as Q);
  const th = Math.acos(Math.min(c, 1));
  const wa = Math.sin((1 - t) * th) / Math.sin(th);
  const wb = (s * Math.sin(t * th)) / Math.sin(th);
  return norm([0, 1, 2, 3].map((i) => wa * a[i] + wb * b[i]) as Q);
}
const lerp = (a: V3, b: V3, t: number): V3 => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
export const smoothstep = (t: number) => {
  const u = Math.min(Math.max(t, 0), 1);
  return u * u * (3 - 2 * u);
};

// a Gaussian over frames (±3σ, the edges held), per component
function gauss<T extends number[]>(xs: T[], sigma: number): T[] {
  const r = Math.ceil(3 * sigma);
  const w = Array.from({ length: 2 * r + 1 }, (_, i) => Math.exp(-((i - r) ** 2) / (2 * sigma * sigma)));
  const sum = w.reduce((a, b) => a + b, 0);
  const n = xs.length;
  return xs.map((_, f) => {
    const out = xs[0].map(() => 0) as T;
    for (let k = -r; k <= r; k++) {
      const x = xs[Math.min(Math.max(f + k, 0), n - 1)];
      for (let c = 0; c < out.length; c++) out[c] += (w[k + r] / sum) * x[c];
    }
    return out;
  });
}
/** M69: a zero-phase second-order Butterworth low-pass over frames (forward, then backward), its half-power point where a
 * Gaussian of `sigma` frames has it (the cut at 0.1652 / sigma cycles a frame, at most 0.45); the ends padded by odd
 * reflection (at most 30 frames), the filter started at rest on the first sample (steps/_anim.py _butter, the same
 * arithmetic). */
function butter<T extends number[]>(xs: T[], sigma: number): T[] {
  const N = xs.length;
  if (N < 3) return xs.map((x) => [...x] as T);
  const K = Math.tan(Math.PI * Math.min(0.1652 / sigma, 0.45));
  const n = 1 / (1 + Math.SQRT2 * K + K * K);
  const [b0, a1, a2] = [K * K * n, 2 * (K * K - 1) * n, (1 - Math.SQRT2 * K + K * K) * n];
  const P = Math.min(N - 1, 30);
  const C = xs[0].length;
  const pad: number[][] = [];
  for (let i = P; i >= 1; i--) pad.push(xs[0].map((v, c) => 2 * v - xs[i][c]));
  for (const x of xs) pad.push([...x]);
  for (let i = N - 2; i > N - P - 2; i--) pad.push(xs[N - 1].map((v, c) => 2 * v - xs[i][c]));
  const run = (s: number[][]) => {
    const y: number[][] = [];
    const x1 = [...s[0]], x2 = [...s[0]], y1 = [...s[0]], y2 = [...s[0]];
    for (const row of s) {
      const out = row.map((v, c) => b0 * v + 2 * b0 * x1[c] + b0 * x2[c] - a1 * y1[c] - a2 * y2[c]);
      for (let c = 0; c < C; c++) [x2[c], x1[c], y2[c], y1[c]] = [x1[c], row[c], y1[c], out[c]];
      y.push(out);
    }
    return y;
  };
  return run(run(pad).reverse()).reverse().slice(P, P + N) as T[];
}
/** Consecutive quaternions on one side (q and -q are the same turn), then filtered and normalised. */
function smoothQ(qs: Q[], sigma: number, filter = gauss): Q[] {
  const aligned: Q[] = [];
  for (const q of qs) aligned.push(aligned.length && dot(aligned[aligned.length - 1], q) < 0 ? [-q[0], -q[1], -q[2], -q[3]] : [...q]);
  return filter(aligned, sigma).map(norm);
}

/** The delta a bone's keys give at frame f: between two keys a smoothstep slerp, before the first and after the last a
 * fade to identity over the key's falloff (0: the key's own frame only). */
export function delta(keys: Key[], f: number): Q {
  const k = [...keys].sort((a, b) => a.f - b.f);
  const fade = (key: Key, d: number): Q => (d === 0 ? key.q : key.falloff > 0 && d < key.falloff ? slerp(I, key.q, smoothstep(1 - d / key.falloff)) : I);
  if (f <= k[0].f) return fade(k[0], k[0].f - f);
  const last = k[k.length - 1];
  if (f >= last.f) return fade(last, f - last.f);
  const i = k.findIndex((key, j) => key.f <= f && f <= k[j + 1].f);
  return slerp(k[i].q, k[i + 1].q, smoothstep((f - k[i].f) / (k[i + 1].f - k[i].f)));
}
export function offset(offs: Offset[], f: number): V3 {
  const k = [...offs].sort((a, b) => a.f - b.f);
  const zero: V3 = [0, 0, 0];
  const fade = (o: Offset, d: number): V3 => (d === 0 ? o.v : o.falloff > 0 && d < o.falloff ? lerp(zero, o.v, smoothstep(1 - d / o.falloff)) : zero);
  if (f <= k[0].f) return fade(k[0], k[0].f - f);
  const last = k[k.length - 1];
  if (f >= last.f) return fade(last, f - last.f);
  const i = k.findIndex((o, j) => o.f <= f && f <= k[j + 1].f);
  return lerp(k[i].v, k[i + 1].v, smoothstep((f - k[i].f) / (k[i + 1].f - k[i].f)));
}

// --- M38: skeleton space and the two-bone leg solve (docs/IMPLEMENTATION.md M38, the same as steps/_anim.py) ---
const add3 = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub3 = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot3 = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len3 = (a: V3) => Math.hypot(...a);
const unit3 = (a: V3): V3 => {
  const n = len3(a) || 1;
  return [a[0] / n, a[1] / n, a[2] / n];
};
/** v turned by q. */
export const turn = (q: Q, v: V3): V3 => {
  const t = mul(mul(q, [v[0], v[1], v[2], 0]), conj(q));
  return [t[0], t[1], t[2]];
};
const about = (n: V3, th: number): Q => [n[0] * Math.sin(th / 2), n[1] * Math.sin(th / 2), n[2] * Math.sin(th / 2), Math.cos(th / 2)];
/** The shortest turn taking unit u to unit v (opposite: 180° about u × X, else u × Y). */
export function between(u: V3, v: V3): Q {
  const d = dot3(u, v);
  if (1 + d < 1e-8) {
    let ax = cross(u, [1, 0, 0]);
    if (len3(ax) < 1e-8) ax = cross(u, [0, 1, 0]);
    return about(unit3(ax), Math.PI);
  }
  return norm([...cross(u, v), 1 + d] as Q);
}
/** Every bone's world turn and position at frame f, the root bone down (W = W_parent·rot, p = p_parent + W_parent·t). */
export function pose(bones: Track[], f: number): Map<string, { W: Q; p: V3 }> {
  const out = new Map<string, { W: Q; p: V3 }>();
  const by = new Map(bones.map((b) => [b.name, b]));
  const at = (n: string): { W: Q; p: V3 } => {
    const done = out.get(n);
    if (done) return done;
    const b = by.get(n)!;
    const up = b.parent && by.has(b.parent) ? at(b.parent) : { W: I, p: [0, 0, 0] as V3 };
    const t = b.translation?.[f] ?? b.offset ?? [0, 0, 0];
    const me = { W: norm(mul(up.W, b.rotation?.[f] ?? I)), p: add3(up.p, turn(up.W, t)) };
    out.set(n, me);
    return me;
  };
  bones.forEach((b) => at(b.name));
  return out;
}
/** The two-bone solve: U, L and B's new local turns putting B at P (world turns W of U's parent, U, L, B and positions
 * a, b, c of U, L, B), B keeping its world turn; null: nothing to solve (a zero-length bone, a target on the hip). */
export function twoBone(Wpar: Q, WU: Q, WL: Q, WB: Q, a: V3, b: V3, c: V3, P: V3): [Q, Q, Q] | null {
  const l1 = len3(sub3(b, a));
  const l2 = len3(sub3(c, b));
  if (l1 * l2 < 1e-12 || len3(sub3(P, a)) < 1e-12) return null;
  const d = Math.min(Math.max(len3(sub3(P, a)), Math.abs(l1 - l2) + 1e-6), l1 + l2 - 1e-6);
  let n = cross(sub3(a, b), sub3(c, b));
  if (len3(n) < 1e-8) n = cross(sub3(a, b), [1, 0, 0]);
  if (len3(n) < 1e-8) n = cross(sub3(a, b), [0, 1, 0]);
  if (len3(n) < 1e-8) return null; // the upper bone (nearly) zero long
  n = unit3(n);
  const th = Math.acos(Math.min(Math.max(dot3(sub3(a, b), sub3(c, b)) / (l1 * l2), -1), 1));
  const th2 = Math.acos(Math.min(Math.max((l1 * l1 + l2 * l2 - d * d) / (2 * l1 * l2), -1), 1));
  const K = about(n, th2 - th);
  const c2 = add3(b, turn(K, sub3(c, b)));
  const H = between(unit3(sub3(c2, a)), unit3(sub3(P, a)));
  const WU2 = mul(H, WU);
  const WL2 = mul(mul(H, K), WL);
  return [norm(mul(conj(Wpar), WU2)), norm(mul(conj(WU2), WL2)), norm(mul(conj(WL2), WB))];
}
/** M63: a chain's new local turns putting its last bone at P: W and p the world turns and positions of the chain's bones,
 * its top first, the reaching bone last (which keeps its world turn), Wpar the top's parent's world turn. The chain first
 * turns about its root–tip line by `swivel` degrees (the knee or elbow points elsewhere); two bones by the two-bone solve,
 * more by FABRIK (16 rounds); null: nothing to solve. */
export function ikChain(Wpar: Q, W: Q[], p: V3[], P: V3, swivel: number): Q[] | null {
  const n = W.length - 1;
  W = [...W];
  p = [...p];
  const ax = sub3(p[n], p[0]);
  if (swivel !== 0 && len3(ax) > 1e-12) {
    const R = about(unit3(ax), (swivel * Math.PI) / 180);
    for (let i = 0; i < n; i++) {
      W[i] = norm(mul(R, W[i]));
      if (i > 0) p[i] = add3(p[0], turn(R, sub3(p[i], p[0])));
    }
  }
  if (n === 2) return twoBone(Wpar, W[0], W[1], W[2], p[0], p[1], p[2], P);
  const L = p.slice(0, n).map((x, i) => len3(sub3(p[i + 1], x)));
  if (L.some((l) => l < 1e-12) || len3(sub3(P, p[0])) < 1e-12) return null;
  const along = (from: V3, to: V3, l: number): V3 => {
    const u = unit3(sub3(to, from));
    return [from[0] + u[0] * l, from[1] + u[1] * l, from[2] + u[2] * l];
  };
  const q = p.map((x) => [...x] as V3); // ponytail: a fixed 16 rounds, an early stop if long chains get slow
  for (let r = 0; r < 16; r++) {
    q[n] = [...P];
    for (let i = n - 1; i >= 0; i--) q[i] = along(q[i + 1], q[i], L[i]);
    q[0] = [...p[0]];
    for (let i = 1; i <= n; i++) q[i] = along(q[i - 1], q[i], L[i - 1]);
  }
  const out: Q[] = [];
  let up = Wpar;
  let cur = W[0]; // bone i's world turn under its parent's new one
  for (let i = 0; i < n; i++) {
    const t = turn(conj(W[i]), sub3(p[i + 1], p[i])); // the next joint in bone i's own frame
    const Wn = norm(mul(between(unit3(turn(cur, t)), unit3(sub3(q[i + 1], q[i]))), cur));
    out.push(norm(mul(conj(up), Wn)));
    cur = mul(Wn, mul(conj(W[i]), W[i + 1]));
    up = Wn;
  }
  out.push(norm(mul(conj(up), W[n])));
  return out;
}
/** M63: an IK control's target at base frame f: between two keys its position, swivel and weight smoothstep-lerped;
 * before the first and after the last the key's held, its weight fading out over its falloff; null: no IK at f. */
export function ikAt(keys: IkKey[], f: number): { p: V3; swivel: number; w: number } | null {
  const k = [...keys].sort((a, b) => a.f - b.f);
  const fade = (key: IkKey, d: number) => {
    const w = d === 0 ? key.w : key.falloff > 0 && d < key.falloff ? key.w * smoothstep(1 - d / key.falloff) : 0;
    return w > 0 ? { p: key.p, swivel: key.swivel, w } : null;
  };
  if (f <= k[0].f) return fade(k[0], k[0].f - f);
  const last = k[k.length - 1];
  if (f >= last.f) return fade(last, f - last.f);
  const i = k.findIndex((key, j) => key.f <= f && f <= k[j + 1].f);
  const t = smoothstep((f - k[i].f) / (k[i + 1].f - k[i].f));
  const w = k[i].w + (k[i + 1].w - k[i].w) * t;
  return w > 0 ? { p: lerp(k[i].p, k[i + 1].p, t), swivel: k[i].swivel + (k[i + 1].swivel - k[i].swivel) * t, w } : null;
}
/** M63: a bone and the `n` bones above it, the top first. */
export const chainOf = (bones: Track[], bone: string, n: number): string[] => {
  const by = new Map(bones.map((b) => [b.name, b]));
  const out = [bone];
  while (out.length <= n) out.unshift(by.get(out[0])!.parent!);
  return out;
};
/** Step 3c: one IK control on dense bones (in place). */
function solveIk(bones: Track[], c: Ik, F: number) {
  const by = new Map(bones.map((b) => [b.name, b]));
  const chain = chainOf(bones, c.bone, c.chain);
  const top = by.get(chain[0])!;
  for (let f = 0; f < F; f++) {
    const at = ikAt(c.keys, f);
    if (!at) continue;
    const ps = pose(bones, f);
    const s = c.space ? ps.get(c.space) : undefined;
    const T = s ? add3(s.p, turn(s.W, at.p)) : at.p;
    const Wpar = top.parent && ps.has(top.parent) ? ps.get(top.parent)!.W : I;
    const r = ikChain(
      Wpar,
      chain.map((n) => ps.get(n)!.W),
      chain.map((n) => ps.get(n)!.p),
      lerp(ps.get(c.bone)!.p, T, at.w),
      at.swivel * at.w,
    );
    if (r) chain.forEach((n, i) => (by.get(n)!.rotation![f] = r[i]));
  }
}
/** M38 Find foot contacts: each foot's still stretches (slower than 15 % of the body's height a second, at most half its
 * height range, 2 % of the body's height at least, above its lowest; 3 frames or more), a lock each with a blend of 3.
 * M64 hands: the same stillness within 10 % of the body's size above the clip's floor (the lowest any bone gets), so a
 * palm on the floor locks and a hand held still in the air does not. The size is the skeleton's longest extent over the
 * clip (its height when it stands, its length in a plank), not frame 0's height, which a push-up clip would make a
 * fifth of it. ponytail: floor support only; a raised support (a table, a rail) would need its height. */
export function contacts(clip: Clip, feet: string[], hands: string[] = []): Lock[] {
  const F = clip.frames;
  const poses = Array.from({ length: F }, (_, f) => pose(clip.bones, f));
  const ys = [...poses[0].values()].map((x) => x.p[1]);
  const H = Math.max(...ys) - Math.min(...ys) || 1;
  const extent = (m: Map<string, { p: V3 }>, i: number) => Math.max(...[...m.values()].map((x) => x.p[i])) - Math.min(...[...m.values()].map((x) => x.p[i]));
  const size = Math.max(...poses.flatMap((m) => [0, 1, 2].map((i) => extent(m, i)))) || 1;
  const floor = Math.min(...poses.map((m) => Math.min(...[...m.values()].map((x) => x.p[1]))));
  const out: Lock[] = [];
  for (const foot of [...feet, ...hands]) {
    const p = poses.map((m) => m.get(foot)!.p);
    const h = p.map((x) => x[1]);
    const lo = Math.min(...h);
    const top = hands.includes(foot) ? floor + 0.1 * size : lo + Math.max(0.5 * (Math.max(...h) - lo), 0.02 * H);
    const still = p.map((_, f) => {
      const [a, b] = [Math.max(f - 1, 0), Math.min(f + 1, F - 1)];
      return h[f] <= top && (len3(sub3(p[b], p[a])) * clip.fps) / (b - a) < 0.15 * H;
    });
    for (let f = 0; f < F; f++) {
      let e = f;
      while (still[f] && e + 1 < F && still[e + 1]) e++;
      if (still[f] && e - f >= 2) out.push({ bone: foot, from: f, to: e, blend: 3 });
      f = e;
    }
  }
  return out;
}
/** M69: a per-frame need held over ±k frames, blurred (Gaussian, sigma k / 2), never below the need. */
function holdBlur(need: number[], k: number): number[] {
  const held = need.map((_, f) => Math.max(...need.slice(Math.max(0, f - k), f + k + 1)));
  return gauss(held.map((h) => [h]), Math.max(k / 2, 0.5)).map((b, f) => Math.max(b[0], need[f]));
}
/** Step 3b: one foot lock on dense bones (in place); M69 `reach` moves the top translated bone above it (the hips)
 * toward the pinned foot where it is out of reach, eased, before the solve (steps/_anim.py _lock). */
function lock(bones: Track[], l: Lock, F: number) {
  const by = new Map(bones.map((b) => [b.name, b]));
  const B = by.get(l.bone)!;
  const L = by.get(B.parent!)!;
  const U = by.get(L.parent!)!;
  const T = pose(bones, Math.floor((l.from + l.to) / 2)).get(B.name)!.p;
  const weight = (f: number) => (f < l.from ? smoothstep(1 - (l.from - f) / l.blend) : f > l.to ? smoothstep(1 - (f - l.to) / l.blend) : 1);
  const f0 = Math.max(l.from - l.blend, 0);
  const f1 = Math.min(l.to + l.blend, F - 1);
  const chain: string[] = [];
  for (let n: string | null = B.name; n !== null && by.has(n); n = by.get(n)!.parent) chain.unshift(n);
  const hips = chain.map((n) => by.get(n)!).find((b) => b.translation);
  if (l.reach && hips) {
    const need = new Array<number>(F).fill(0);
    let toward: V3 = [0, 0, 0];
    for (let f = f0; f <= f1; f++) {
      const ps = pose(bones, f);
      const [a, b, c] = [ps.get(U.name)!.p, ps.get(L.name)!.p, ps.get(B.name)!.p];
      const P = lerp(c, T, weight(f));
      const d = len3(sub3(P, a));
      const excess = d - l.reach * (len3(sub3(b, a)) + len3(sub3(c, b)));
      if (excess > 0) {
        need[f] = excess;
        toward = add3(toward, sub3(P, a).map((x) => (x / d) * excess) as V3);
      }
    }
    if (need.some((x) => x > 0)) {
      toward = unit3(toward);
      const eased = holdBlur(need, Math.max(l.blend, 1));
      for (let f = 0; f < F; f++) {
        if (eased[f] <= 1e-12) continue;
        const Wp = hips.parent && by.has(hips.parent) ? pose(bones, f).get(hips.parent)!.W : I;
        hips.translation![f] = add3(hips.translation![f], turn(conj(Wp), toward.map((x) => x * eased[f]) as V3));
      }
    }
  }
  for (let f = f0; f <= f1; f++) {
    const w = weight(f);
    const ps = pose(bones, f);
    const [pu, pl, pb] = [ps.get(U.name)!, ps.get(L.name)!, ps.get(B.name)!];
    const Wpar = U.parent && ps.has(U.parent) ? ps.get(U.parent)!.W : I;
    const r = twoBone(Wpar, pu.W, pl.W, pb.W, pu.p, pl.p, pb.p, lerp(pb.p, T, w));
    if (r) [U.rotation![f], L.rotation![f], B.rotation![f]] = r;
  }
}

const partner = (n: string, names: Set<string>) => {
  for (const [a, b] of [
    ["Left", "Right"],
    ["Right", "Left"],
  ]) {
    if (n.includes(a)) {
      const m = n.replaceAll(a, b);
      return names.has(m) ? m : n;
    }
  }
  return n;
};
/** A turn seen in a mirror whose plane has the unit normal n: its axis mirrored, its angle reversed. */
const reflect = (q: Q, n: V3): Q => {
  const v: V3 = [q[0], q[1], q[2]];
  const k = 2 * dot3(v, n);
  return [k * n[0] - v[0], k * n[1] - v[1], k * n[2] - v[2], q[3]];
};
/** M69 step 1b: the clip as its mirror image (steps/_anim.py _mirror, the same rules): each bone's world motion from its
 * rest reflected across the body's middle plane and put on its partner's rest; a root's translation reflected. */
function mirrored(bones: Track[]): Track[] {
  if (bones.some((b) => !b.rest)) return bones;
  const names = new Set(bones.map((b) => b.name));
  const by = new Map(bones.map((b) => [b.name, b]));
  const order: string[] = [];
  const seen = new Set<string>();
  const visit = (n: string) => {
    if (seen.has(n)) return;
    seen.add(n);
    const p = by.get(n)!.parent;
    if (p !== null && names.has(p)) visit(p);
    order.push(n);
  };
  bones.forEach((b) => visit(b.name));
  const Wr = new Map<string, Q>();
  const pr = new Map<string, V3>();
  for (const n of order) {
    const b = by.get(n)!;
    const Wp = b.parent !== null && Wr.has(b.parent) ? Wr.get(b.parent)! : I;
    const pp = b.parent !== null && pr.has(b.parent) ? pr.get(b.parent)! : ([0, 0, 0] as V3);
    Wr.set(n, b.rotation ? mul(Wp, b.rest!.q) : Wp);
    pr.set(n, add3(pp, turn(Wp, b.rest!.t)));
  }
  const pairs = [...names].filter((n) => n.includes("Left") && partner(n, names) !== n).map((n) => [n, partner(n, names)]);
  if (!pairs.length) return bones;
  let side: V3 = [0, 0, 0];
  let mid: V3 = [0, 0, 0];
  for (const [a, b] of pairs) {
    side = add3(side, sub3(pr.get(a)!, pr.get(b)!));
    mid = add3(mid, add3(pr.get(a)!, pr.get(b)!));
  }
  if (len3(side) < 1e-9) return bones;
  const nrm = unit3(side);
  mid = mid.map((x) => x / (2 * pairs.length)) as V3;
  const F = bones.find((b) => b.rotation)?.rotation?.length ?? bones.find((b) => b.translation)?.translation?.length ?? 0;
  const W = new Map<string, Q[]>();
  for (const n of order) {
    const b = by.get(n)!;
    const Wp = b.parent !== null && W.has(b.parent) ? W.get(b.parent)! : null;
    W.set(n, Array.from({ length: F }, (_, f) => (b.rotation ? norm(mul(Wp ? Wp[f] : I, b.rotation[f])) : Wp ? Wp[f] : I)));
  }
  const want = new Map<string, Q[]>();
  for (const n of order) {
    const m = partner(n, names);
    want.set(m, W.get(n)!.map((w) => mul(reflect(mul(w, conj(Wr.get(n)!)), nrm), Wr.get(m)!)));
  }
  const Wn = new Map<string, Q[]>();
  const rot = new Map<string, Q[]>();
  for (const n of order) {
    const b = by.get(n)!;
    const Wp = b.parent !== null && Wn.has(b.parent) ? Wn.get(b.parent)! : null;
    if (b.rotation) {
      rot.set(n, want.get(n)!.map((w, f) => norm(mul(conj(Wp ? Wp[f] : I), w))));
      Wn.set(n, want.get(n)!);
    } else Wn.set(n, Wp ?? Array.from({ length: F }, () => I));
  }
  return bones.map((b) => {
    const out: Track = { ...b, rotation: rot.get(b.name) ?? b.rotation };
    if (b.translation) {
      const src = by.get(partner(b.name, names))?.translation ?? b.translation;
      out.translation =
        b.parent !== null && names.has(b.parent)
          ? src.map((p) => [...p] as V3)
          : src.map((p) => {
              const k = 2 * dot3(sub3(p, mid), nrm);
              return [p[0] - k * nrm[0], p[1] - k * nrm[1], p[2] - k * nrm[2]] as V3;
            });
    }
    return out;
  });
}

/** Steps 1b and 2 alone (the mirrored, smoothed base): the rotation a key's delta is measured from. */
export function smoothed(base: Clip, layer: Layer): Clip {
  const filter = layer.filter === "butterworth" ? butter : gauss;
  return {
    ...base,
    bones: (layer.mirror ? mirrored(base.bones) : base.bones).map((b) => {
      const s = layer.smooth?.[b.name] ?? 0;
      return s > 0 ? { ...b, rotation: b.rotation && smoothQ(b.rotation, s, filter), translation: b.translation && filter(b.translation, s) } : b;
    }),
  };
}

/** The edited clip: §3.2 anim_edit steps 2 to 8 on a dense base (step 1: fromThree). */
export function evaluate(base: Clip, layer: Layer): Clip {
  const copy = (b: Track): Track => ({ ...b, rotation: b.rotation?.map((q) => [...q] as Q), translation: b.translation?.map((v) => [...v] as V3) });
  let bones = smoothed(base, layer).bones.map(copy);
  for (const b of bones) {
    const ks = layer.keys?.[b.name];
    if (ks?.length && b.rotation) b.rotation = b.rotation.map((q, f) => norm(mul(q, delta(ks, f))));
    const os = layer.offsets?.[b.name];
    if (os?.length && b.translation)
      b.translation = b.translation.map((v, f) => {
        const o = offset(os, f);
        return [v[0] + o[0], v[1] + o[1], v[2] + o[2]];
      });
  }
  for (const l of layer.feet ?? []) lock(bones, l, base.frames);
  for (const c of layer.ik ?? []) solveIk(bones, c, base.frames);
  const [i0, i1] = layer.trim ?? [0, base.frames - 1];
  bones = bones.map((b) => ({ ...b, rotation: b.rotation?.slice(i0, i1 + 1), translation: b.translation?.slice(i0, i1 + 1) }));
  const G = i1 - i0 + 1;
  const parent = new Map(bones.map((b) => [b.name, b.parent]));
  const moved = new Set(bones.filter((b) => b.translation).map((b) => b.name));
  const ancestors = (n: string): string[] => {
    const p = parent.get(n);
    return p ? [p, ...ancestors(p)] : [];
  };
  const top = new Set(bones.filter((b) => b.translation && !ancestors(b.name).some((a) => moved.has(a))).map((b) => b.name));
  for (const b of bones) {
    const t = b.translation;
    if (!t || !top.has(b.name)) continue;
    if (layer.in_place && G > 1) {
      const dx = t[G - 1][0] - t[0][0];
      const dz = t[G - 1][2] - t[0][2];
      t.forEach((v, g) => ((v[0] -= (g / (G - 1)) * dx), (v[2] -= (g / (G - 1)) * dz)));
    }
    if (layer.ground) t.forEach((v) => (v[1] += layer.ground!));
  }
  const n = Math.min(layer.loop ?? 0, G - 1);
  if (n > 0)
    for (let g = G - n; g < G; g++) {
      const w = smoothstep((g - (G - n) + 1) / n);
      for (const b of bones) {
        if (b.rotation) b.rotation[g] = slerp(b.rotation[g], b.rotation[0], w);
        const t = b.translation;
        if (!t) continue;
        const to = lerp(t[g], t[0], w);
        t[g] = top.has(b.name) && !layer.in_place ? [t[g][0], to[1], t[g][2]] : to;
      }
    }
  const sp = layer.speed ?? 1;
  if (sp === 1) return { fps: base.fps, frames: G, bones };
  const H = Math.round((G - 1) / sp) + 1;
  const at = <T>(xs: T[], h: number, mix: (a: T, b: T, t: number) => T) => {
    const x = Math.min(h * sp, G - 1);
    const i = Math.floor(x);
    return mix(xs[i], xs[Math.min(i + 1, G - 1)], x - i);
  };
  return {
    fps: base.fps,
    frames: H,
    bones: bones.map((b) => ({
      ...b,
      rotation: b.rotation && Array.from({ length: H }, (_, h) => at(b.rotation!, h, slerp)),
      translation: b.translation && Array.from({ length: H }, (_, h) => at(b.translation!, h, lerp)),
    })),
  };
}

/** A loaded clip sampled at every frame (§3.2 step 1): F = round(duration·fps) + 1 frames at f/fps; bones by their glTF
 * node names (`nodes`: three.js name → node name), their parents too. */
export function fromThree(clip: THREE.AnimationClip, fps: number, bones: { name: string; node: string; parent: string | null; rest: V3; restq?: Q }[]): Clip {
  const F = Math.round(clip.duration * fps) + 1;
  const node = new Map(bones.map((b) => [b.name, b.node]));
  const track = (bone: string, prop: string) => clip.tracks.find((t) => t.name === `${bone}.${prop}`);
  const sample = (t: THREE.KeyframeTrack, size: number) => {
    const ip = (t as unknown as { createInterpolant(): THREE.Interpolant }).createInterpolant(); // set per interpolation mode, not in the types
    return Array.from({ length: F }, (_, f) => Array.from(ip.evaluate(f / fps)).slice(0, size));
  };
  // as sample_clip: the bones the clip turns or places; one without either hangs its children from its nearest kept ancestor
  const kept = bones.filter((b) => track(b.name, "quaternion") || track(b.name, "position"));
  const keep = new Set(kept.map((b) => b.name));
  const up = new Map(bones.map((b) => [b.name, b.parent]));
  const parentOf = (n: string | null): string | null => (n === null || keep.has(n) ? n : parentOf(up.get(n) ?? null));
  return {
    fps,
    frames: F,
    bones: kept.map((b) => {
      const r = track(b.name, "quaternion");
      const p = track(b.name, "position"); // sampled when it moves, by its values (two keys can move a root)
      const moves = p && Array.from(p.values).some((x, i) => Math.abs(x - p.values[i % 3]) > 1e-7);
      const parent = parentOf(b.parent);
      return {
        name: b.node,
        parent: parent ? (node.get(parent) ?? null) : null,
        ...(r && { rotation: sample(r, 4) as Q[] }),
        ...(p && moves && { translation: sample(p, 3) as V3[] }),
        ...(!(p && moves) && { offset: p ? ([p.values[0], p.values[1], p.values[2]] as V3) : b.rest }),
        ...(b.restq && { rest: { q: b.restq, t: b.rest } }),
      };
    }),
  };
}

/** The evaluated clip as a three.js clip under `name` (bones back to their three.js names); the base's scale tracks kept. */
export function toThree(c: Clip, name: string, bones: { name: string; node: string }[], keep: THREE.KeyframeTrack[] = []): THREE.AnimationClip {
  const three = new Map(bones.map((b) => [b.node, b.name]));
  const times = Float32Array.from({ length: c.frames }, (_, f) => f / c.fps);
  const tracks: THREE.KeyframeTrack[] = [...keep];
  for (const b of c.bones) {
    const n = three.get(b.name);
    if (!n) continue;
    if (b.rotation) tracks.push(new THREE.QuaternionKeyframeTrack(`${n}.quaternion`, times, b.rotation.flat()));
    if (b.translation) tracks.push(new THREE.VectorKeyframeTrack(`${n}.position`, times, b.translation.flat()));
  }
  return new THREE.AnimationClip(name, (c.frames - 1) / c.fps, tracks);
}

/** A delta as X/Y/Z degrees about the bone's own axes (display only: small turns stay clear of gimbal lock), and back. */
export const toDegrees = (q: Q): V3 => {
  const e = new THREE.Euler().setFromQuaternion(new THREE.Quaternion(...q), "XYZ");
  return [e.x, e.y, e.z].map((r) => (r * 180) / Math.PI) as V3;
};
export const fromDegrees = (d: V3): Q =>
  positive(new THREE.Quaternion().setFromEuler(new THREE.Euler(...(d.map((x) => (x * Math.PI) / 180) as V3), "XYZ")).toArray() as Q);
