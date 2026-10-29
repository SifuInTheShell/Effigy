// M37: the editor's TypeScript evaluation (ui/src/anim/clip.ts) against steps/_anim.py on their shared fixture: what
// the viewer previews is what anim_edit bakes.
import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { contacts, evaluate, I, mul, nameOk, turn, twoBone, type Clip, type Layer, type Q, type V3 } from "../src/anim/clip";

test("Animation editor: the preview's evaluation matches the bake's on the shared fixture", () => {
  const c = JSON.parse(readFileSync(resolve(process.cwd(), "../tests/fixtures/anim_edit/case.json"), "utf-8")) as { base: Clip; layer: Layer; expected: Clip };
  const out = evaluate(c.base, c.layer);
  expect(out.frames).toBe(c.expected.frames);
  let worst = 0;
  for (const want of c.expected.bones) {
    const got = out.bones.find((b) => b.name === want.name)!;
    want.rotation?.forEach((q, f) => {
      const g = got.rotation![f];
      const same = Math.max(...q.map((x, i) => Math.abs(x - g[i])));
      const flip = Math.max(...q.map((x, i) => Math.abs(x + g[i]))); // q and -q are one turn
      worst = Math.max(worst, Math.min(same, flip));
    });
    want.translation?.forEach((v, f) => (worst = Math.max(worst, ...v.map((x, i) => Math.abs(x - got.translation![f][i])))));
  }
  expect(worst).toBeLessThan(1e-5);
});

test("A copy's name: the rule of steps/anim_edit.py", () => {
  const others = ["Walk", "Idle"];
  for (const n of ["Walk limp", "a".repeat(64), "😀".repeat(64), "x-1 (b)"]) expect(nameOk(n, others), n).toBe(true);
  for (const n of ["", " a", "a ", "a".repeat(65), "😀".repeat(65), "a/b", "a\\b", "a:b", "a|b", "a,b", "a[1]", "a;b", "a\tb", "walk", "IDLE"])
    expect(nameOk(n, others), n).toBe(false);
});

test("Find foot contacts: a still stretch of three frames or more, not a slide", () => {
  const F = 12; // a foot still for frames 0–5, then sliding along the floor at 2.4 m/s; a head 1 m up gives the height
  const at = (f: number): V3 => (f < 6 ? [0, 0, 0] : [0.1 * (f - 5), 0, 0]);
  const clip: Clip = {
    fps: 24,
    frames: F,
    bones: [
      { name: "Foot", parent: null, translation: Array.from({ length: F }, (_, f) => at(f)) },
      { name: "Head", parent: null, offset: [0, 1, 0] },
    ],
  };
  expect(contacts(clip, ["Foot"])).toEqual([{ bone: "Foot", from: 0, to: 4, blend: 3 }]);
});

test("Find hand contacts (M64): a palm still on the floor locks, a hand held still at hip height does not", () => {
  const F = 8; // the feet give the floor at 0 and the head 1.7 m up the height
  const still = (v: V3) => Array.from({ length: F }, () => v);
  const clip: Clip = {
    fps: 24,
    frames: F,
    bones: [
      { name: "Foot", parent: null, offset: [0, 0, 0] },
      { name: "Head", parent: null, offset: [0, 1.7, 0] },
      { name: "LeftHand", parent: null, translation: still([0.3, 0.05, 0.2]) }, // on the floor (a push-up)
      { name: "RightHand", parent: null, translation: still([-0.3, 0.9, 0.2]) }, // still, in the air
    ],
  };
  expect(contacts(clip, [], ["LeftHand", "RightHand"])).toEqual([{ bone: "LeftHand", from: 0, to: F - 1, blend: 3 }]);
  expect(contacts(clip, ["RightHand"])).toEqual([{ bone: "RightHand", from: 0, to: F - 1, blend: 3 }]); // as a foot it would: its own lowest counts
  // a clip that starts in a plank: 0.35 m tall, 1.6 m long; the palm 8 cm above the toes still counts (the body's size is its length)
  const plank: Clip = {
    fps: 24,
    frames: F,
    bones: [
      { name: "Foot", parent: null, offset: [0, 0, 0] },
      { name: "Head", parent: null, offset: [1.6, 0.35, 0] },
      { name: "LeftHand", parent: null, translation: still([1.3, 0.08, 0.2]) },
    ],
  };
  expect(contacts(plank, [], ["LeftHand"])).toEqual([{ bone: "LeftHand", from: 0, to: F - 1, blend: 3 }]);
});

test("The two-bone solve: a straight leg bends to reach, a target out of reach stretches it, the foot keeps its turn", () => {
  // a straight leg down from the hip, its world turns all identity: the bones' offsets are b - a and c - b
  const [a, b, c]: V3[] = [
    [0, 0, 0],
    [0, -0.5, 0],
    [0, -1, 0],
  ];
  const foot = (P: V3) => {
    const [ru, rl, rb] = twoBone(I, I, I, I, a, b, c, P)!;
    const knee = turn(ru, [0, -0.5, 0]);
    const ankle = turn(mul(ru, rl), [0, -0.5, 0]);
    return { at: [knee[0] + ankle[0], knee[1] + ankle[1], knee[2] + ankle[2]] as V3, turn: mul(mul(ru, rl), rb) };
  };
  const near = (x: number[], y: number[], tol: number) => x.every((v, i) => Math.abs(v - y[i]) < tol);
  const bent = foot([0.3, -0.8, 0]);
  expect(near(bent.at, [0.3, -0.8, 0], 1e-6)).toBe(true);
  expect(near(bent.turn, I, 1e-9) || near(bent.turn.map((x) => -x) as Q, I, 1e-9)).toBe(true);
  const far = foot([0, -2, 0]); // twice the leg's length: as far as it goes
  expect(far.at.every(Number.isFinite)).toBe(true);
  expect(Math.hypot(...far.at)).toBeCloseTo(1, 5);
  expect(twoBone(I, I, I, I, a, [0, -5e-9, 0], [0, -0.5, 0], [0.1, -0.4, 0])).toBeNull(); // no upper bone: nothing to solve
});
