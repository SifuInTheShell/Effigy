import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const GLB = readFileSync(new URL("../../tests/fixtures/two_tris.glb", import.meta.url));
const PNG = readFileSync(new URL("../../tests/fixtures/golden/lantern.png", import.meta.url));
const ANIM = readFileSync(new URL("../../tests/fixtures/anim.glb", import.meta.url)); // M37: a skinned column, Idle and Walk

// hermetic: never knock on a Blender that happens to run on this machine
test.beforeEach(async ({ page }) => {
  await page.route("**/api/blender/status", (r) => r.fulfill({ json: { link: null } }));
  await page.route("**/api/setup/blender", (r) => r.fulfill({ json: { start: false, exe: null, found: null } }));
});

const upload = async (request: any, name: string) => {
  const asset = await (await request.post("/api/assets", { data: { name } })).json();
  expect((await request.put(`/api/assets/${asset.id}/upload/two_tris.glb`, { data: GLB })).ok()).toBe(true);
  return asset as { id: string };
};
// Library cards are links named "<asset name> <created>"
const openCard = (page: Page, name: string) => page.getByRole("link", { name: new RegExp(`^${name} `) }).click();
const toLibrary = (page: Page) => page.getByRole("navigation").getByRole("link", { name: "Library" }).click();
const history = (page: Page) => page.getByRole("button", { name: /^History/ }).click();

test("UI loads, WebGL works, a library GLB shows in every view mode", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await upload(request, "smoke");

  await page.goto("/");
  await openCard(page, "smoke");
  await page.locator("canvas").waitFor();
  expect(await page.evaluate(() => !!document.querySelector("canvas")!.getContext("webgl2"))).toBe(true);
  await history(page);
  await page.locator("#versions li").first().click();
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await expect(page.locator("#stats")).toContainText("2 triangles");

  // the labelled Inspect selector replaced the row of channel buttons (proposal §5E)
  for (const mode of ["albedo", "normal", "roughness", "metallic", "ao", "uv", "pbr"]) await page.getByLabel("Inspect").selectOption(mode);
  await page.getByRole("button", { name: "Close" }).click(); // the history drawer
  await page.screenshot({ path: "test-results/smoke-pbr.png" });
  await page.getByLabel("Wireframe").check();
  await page.screenshot({ path: "test-results/smoke-wire.png" });

  // an imported GLB keeps its materials: Check/Export first, geometry-only processing only behind a disclosure
  await expect(page.getByRole("button", { name: "Check model" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Prepare model" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Export for Godot" })).toBeVisible();

  // a step through the UI: job panel + SSE + version list refresh; the job holds the GPU lock, so the
  // backend's gpu_load events reach the header
  await page.locator("#advanced > summary").click();
  await page.getByText("Run a step on 001_upload").click();
  await page.getByLabel("step", { exact: true }).selectOption({ label: "Other step (by name)…" });
  await page.getByPlaceholder("step name").fill("_echo");
  await page.getByLabel("params").fill('{"sleep": 2}');
  await page.getByRole("button", { name: "Run _echo" }).click();
  await expect(page.locator("#gpu")).toContainText("running _echo");
  await expect(page.locator("footer")).toContainText("_echo · done");
  await expect(page.locator("#gpu")).toContainText("idle");

  // Back restores the version that was selected before
  await history(page);
  await expect(page.locator("#versions li")).toHaveCount(2);
  await page.locator("#versions li").nth(1).click();
  await expect(page).toHaveURL(/version=002__echo/);
  await page.goBack();
  await expect(page).toHaveURL(/version=001_upload/);
  await expect(page.locator("#versions button[aria-current]")).toContainText("Imported model");

  // deleting a branch from History: the list shrinks at once (the delete's reply, and the stream's event)
  if (!(await page.locator("#versions").isVisible())) await history(page);
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: /002 and what was made from it$/ }).click();
  await expect(page.locator("#versions li")).toHaveCount(1);
  await expect(page).toHaveURL(/version=001_upload/);
  expect(errors).toEqual([]);
});

test("A late answer for asset A never mixes into B; Ask Claude is gone", async ({ page, request }) => {
  const a = await upload(request, "race-a");
  const b = await upload(request, "race-b");
  const glbs: string[] = [];
  page.on("request", (r) => r.url().includes(".glb") && glbs.push(r.url()));
  await page.route(`**/api/assets/${a.id}`, async (route) => {
    await new Promise((r) => setTimeout(r, 1500)); // A answers after B
    await route.continue();
  });
  await page.goto("/");
  await openCard(page, "race-a");
  await toLibrary(page);
  await openCard(page, "race-b");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.waitForTimeout(2000); // A's answer arrives now and must be dropped
  await expect(page.locator(".ws-title h1")).toHaveText("race-b"); // the title is an h1 since the UX passes
  expect(glbs.length).toBeGreaterThan(0);
  expect(glbs.every((u) => u.includes(`/files/${b.id}/`))).toBe(true);

  // M40: agents connect over MCP; the Blender section has no Ask Claude any more
  await page.unroute(`**/api/assets/${a.id}`);
  await page.route("**/api/blender/status", (r) => r.fulfill({ json: { link: "pro" } }));
  await toLibrary(page);
  await openCard(page, "race-b"); // the Blender panel asks for the link when it mounts
  await page.locator("#blender > summary").click();
  await expect(page.getByRole("button", { name: "Open 001 in Blender" })).toBeEnabled();
  await expect(page.getByText("Ask Claude")).toHaveCount(0);
});

test("A checklist renders its warnings next to the mesh it checked; one click burst = one job", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const a = await upload(request, "checked");
  const report = {
    version: "002_validate",
    step: "validate",
    parent: "001_upload",
    created: "",
    params: { max_triangles: 10000 },
    outputs: { checklist: "checklist.json" },
    metrics: { fail: 1 },
    provenance: "step",
    restricted: false,
  };
  await page.route(`**/api/assets/${a.id}`, async (route) => {
    const d = await (await route.fetch()).json();
    await route.fulfill({ json: { ...d, versions: [...d.versions, report] } });
  });
  await page.route(`**/files/${a.id}/002_validate/checklist.json`, (r) =>
    r.fulfill({
      json: {
        items: {
          manifold: { status: "pass", value: 0 },
          watertight: { status: "warn", value: 6 },
          has_uvs: { status: "fail", value: false },
          triangle_budget: { status: "pass", value: 2 },
        },
      },
    }),
  );
  await page.goto("/");
  await openCard(page, "checked");
  await expect(page.locator("#checklist")).toContainText("2 checks passed · 1 warning · 1 failed");
  await expect(page.locator("#checklist")).toContainText("Warning open (boundary) edges: 6");
  await expect(page.getByRole("button", { name: "Export anyway for Godot" })).toBeVisible();
  await history(page);
  await expect(page.locator("#versions li").nth(1)).toHaveText(/Checks: 1 failed/);
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true); // the report's own mesh
  await expect(page.locator("#stats")).toContainText("2 triangles");

  let posts = 0;
  page.on("request", (r) => r.method() === "POST" && r.url().includes("/steps/validate") && posts++);
  await page.locator("#versions li").first().click();
  await page.getByRole("button", { name: "Check model" }).dblclick();
  await expect(page.locator("footer")).toContainText("validate ·");
  await page.waitForTimeout(500);
  expect(posts).toBe(1);
  expect(errors).toEqual([]);
});

// A fake asset whose steps finish instantly (route mocks: UI behaviour, not inference)
/** M46b: the card Setup reports (`/api/setup/machine`'s gpu item): its budget, the presets it can never run, and a reason
 * for all of them when the 3D models do not support it. */
async function fakeCard(page: Page, budget: number, big: [string, number][], reason?: string) {
  const too_big = big.map(([value, need_gb]) => ({ step: "trellis2", param: "preset", value, need_gb, ...(reason ? { reason } : {}) }));
  await page.route("**/api/setup/machine", (r) => r.fulfill({ json: [{ id: "gpu", level: reason ? "warn" : "ok", label: "a card", detail: "", budget_gb: budget, too_big }] }));
}

async function mockAsset(page: Page, id: string, versions: any[], extra: Record<string, unknown> = {}) {
  const posted: [string, any][] = [];
  const outputs: Record<string, Record<string, string>> = {
    prep: { image: "image.png", image_rgb: "image_rgb.png", crop: "crop.json" },
    trellis2: { mesh: "hi.glb", ovoxel: "ovoxel.npz", preview: "preview.png" },
    clean: { mesh: "mesh.glb", report: "report.json" },
    remesh: { mesh: "mesh.glb", report: "report.json" },
    uv: { mesh: "mesh.glb", uv_report: "uv_report.json" },
    bake: { mesh: "mesh.glb", basecolor: "basecolor.png" },
    validate: { checklist: "checklist.json" },
    project: { mesh: "mesh.glb", basecolor: "basecolor.png", overlay: "overlay.png" },
    refine: { mesh: "mesh.glb", basecolor: "basecolor.png", views: "views.png" },
    parts: { mesh: "mesh.glb", parts: "parts.json" },
    motion: { clip: "clip.glb" },
    mocap: { clip: "clip.glb", bvh: "motion.bvh", preview: "preview.mp4" },
    anim: { rigged: "rigged_anim.glb", clips: "clips.json" },
    anim_edit: { rigged_anim: "rigged_anim.glb", clips: "clips.json", edit: "edit.json", skeleton: "skeleton.json" },
    import_model: { rigged_anim: "rigged_anim.glb", rigged: "rigged.glb", skeleton: "skeleton.json", clips: "clips.json", qa: "qa.json" },
    rig_fit: { mesh: "mesh.glb", skeleton: "skeleton.json", markers: "markers.json", report: "report.json" },
    export: { "lantern.glb": "godot/lantern.glb", "CREDITS.txt": "godot/CREDITS.txt", "LICENSES.json": "godot/LICENSES.json" },
    pixelize: { frames: "frames", sheet: "sheet.png", data: "sheet.json", palette: "palette.gpl", preview: "preview.png", report: "report.json" },
    sprites: { frames: "frames", sheet: "sheet.png", data: "sheet.json", camera: "camera.json", preview: "preview.png" },
    canon: { image: "image.png", gate: "gate.json" },
    depth: { depth: "depth.npz", preview: "preview.png", image: "image.png" },
    layers: { layer_0: "layer_0.png", layer_1: "layer_1.png", layers: "layers.json", bands: "bands.png", preview: "preview.png" },
  };
  const meta = (version: string, step: string, parent: string | null, params: any, out: Record<string, string>) => ({
    version,
    step,
    parent,
    params,
    outputs: out,
    created: "2026-10-04T10:00:00Z",
    // as the steps report them: an export's profile, a pixel edit's frames (0: a blank sheet, M57)
    metrics: step === "export" ? { profile: params.profile } : step === "pixelize" && params.op === "edit" ? { edited_frames: Object.keys(params.frames ?? {}).length } : {},
    provenance: "step",
    restricted: false,
  });
  versions = versions.map((v) => ({ ...meta(v.version, v.step, v.parent, v.params ?? {}, v.outputs), ...(v.metrics && { metrics: v.metrics }) }));
  const jobs: Record<string, string> = {};
  await page.route("**/api/assets", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const real = await (await route.fetch()).json();
    await route.fulfill({ json: [...real, { id, name: "lantern", created: "2026-10-04T10:00:00Z", ...extra }] });
  });
  await page.route(`**/api/assets/${id}`, (r) => r.fulfill({ json: { asset: { id, name: "lantern", created: "", ...extra }, versions } }));
  await page.route(`**/api/assets/${id}/steps/*`, (r) => {
    const step = r.request().url().split("/").pop()!;
    const { parent, params } = r.request().postDataJSON();
    posted.push([step, params]);
    const version = `${String(versions.length * 10 + 10).padStart(3, "0")}_${step}`;
    versions = [...versions, meta(version, step, parent, params, outputs[step])];
    jobs[`job-${version}`] = version;
    return r.fulfill({ status: 202, json: { job_id: `job-${version}` } });
  });
  await page.route("**/api/jobs/job-*", (r) => r.fulfill({ json: { status: "done", version: jobs[r.request().url().split("/").pop()!] } }));
  await page.route(`**/files/${id}/**`, (r) => {
    const url = r.request().url();
    if (url.endsWith(".glb")) return r.fulfill({ body: GLB, contentType: "model/gltf-binary" });
    if (url.endsWith(".png")) return r.fulfill({ body: PNG, contentType: "image/png" });
    if (url.endsWith("checklist.json")) return r.fulfill({ json: { items: { manifold: { status: "pass", value: 0 } } } });
    if (url.endsWith("scores.json"))
      return r.fulfill({
        json: {
          "cand_0.png": { rank: 2, gates: {} },
          "cand_3.png": { rank: 1, gates: {} },
          "cand_1.png": { rank: 3, gates: { margin: { ok: false, hard: true } } },
        },
      });
    return r.fulfill({ body: "" });
  });
  return posted;
}

test("Animation: the time bar steps a clip; the editor keys a bone, retimes the clip, undoes, and saves the layer", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const posted = await mockAsset(page, "mock_anim", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_rig_skin", step: "rig_skin", parent: "010_upload", outputs: { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" } },
    { version: "030_anim", step: "anim", parent: "020_rig_skin", outputs: { rigged_anim: "rigged_anim.glb", clips: "clips.json", skeleton: "skeleton.json" } },
  ]);
  await page.route("**/files/mock_anim/*/rigged_anim.glb", (r) => r.fulfill({ body: ANIM, contentType: "model/gltf-binary" }));
  await page.route("**/files/mock_anim/*/clips.json", (r) =>
    r.fulfill({ json: { clips: [{ name: "Idle", frames: 12, seconds: 0.5 }, { name: "Walk", frames: 24, seconds: 1 }], fps: 24, missing: [] } }),
  );
  await page.route("**/files/mock_anim/*/skeleton.json", (r) => r.fulfill({ json: { template: "mixamo52", joints: [] } }));
  const frame = () => page.evaluate(() => Math.round((window as any).__viewer.time() * 24));
  await page.goto("/#/asset/mock_anim?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  // the time bar: pause, a typed frame, a frame on
  await page.getByRole("combobox", { name: "Animation" }).selectOption({ label: "Play: Walk" });
  await page.getByRole("button", { name: "Pause" }).click();
  await page.getByRole("spinbutton", { name: "Frame number" }).fill("6");
  await expect.poll(frame).toBe(6);
  await page.getByRole("button", { name: "Next frame" }).click();
  await expect.poll(frame).toBe(7);
  // the editor on that clip and frame: the spine turned 30° about its own X keys it there
  await page.getByRole("button", { name: "Edit animation" }).click();
  await expect(page.getByRole("combobox", { name: "Clip to edit" })).toHaveValue("Walk");
  await page.evaluate(() => (window as any).__viewer.testPickBone("Spine"));
  await expect(page.getByRole("heading", { name: "Bone · Spine" })).toBeVisible();
  const q: number[] = await page.evaluate(() => (window as any).__viewer.boneQuat("Spine"));
  const [s, c] = [Math.sin(Math.PI / 12), Math.cos(Math.PI / 12)]; // q · (30° about X)
  const turned = [q[3] * s + q[0] * c, q[1] * c + q[2] * s, q[2] * c - q[1] * s, q[3] * c - q[0] * s];
  await page.evaluate((t) => (window as any).__viewer.testPose("Spine", t), turned);
  await expect(page.getByRole("spinbutton", { name: "X degrees" })).toHaveValue("30.0");
  await expect(page.getByRole("combobox", { name: "Clip to edit" }).locator("option", { hasText: "Walk (edited)" })).toHaveCount(1);
  // half speed: twice as long; Undo: as it was
  await page.getByText("Clip tools", { exact: true }).click();
  await page.getByRole("spinbutton", { name: "Speed" }).fill("0.5");
  await page.getByRole("spinbutton", { name: "Speed" }).blur();
  await expect.poll(() => page.evaluate(() => (window as any).__viewer.duration())).toBeCloseTo(2, 3);
  await page.getByRole("button", { name: "Undo" }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__viewer.duration())).toBeCloseTo(1, 3);
  // a refused save (UI-001): the editor stays open with the layer; then the real save
  await page.route("**/api/assets/mock_anim/steps/anim_edit", (r) => r.fulfill({ status: 400, json: { error: { code: "bad_params", message: "bad layer" } } }), { times: 1 });
  await page.getByRole("button", { name: "Save as a new version" }).click();
  await expect(page.locator(".error, [role=alert]").first()).toContainText("bad layer");
  await expect(page.getByRole("combobox", { name: "Clip to edit" }).locator("option", { hasText: "Walk (edited)" })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Undo" })).toBeEnabled();
  // Save: the edited clip's layer goes to anim_edit
  await page.getByRole("button", { name: "Save as a new version" }).click();
  await expect.poll(() => posted.length).toBe(1);
  const [step, params] = posted[0];
  expect(step).toBe("anim_edit");
  expect(Object.keys(params.clips)).toEqual(["Walk"]);
  expect(params.clips.Walk.keys.Spine).toHaveLength(1);
  expect(params.clips.Walk.keys.Spine[0]).toMatchObject({ f: 7, falloff: 10 });
  expect(params.clips.Walk.speed).toBeUndefined();
  await expect(page.getByRole("button", { name: "Save as a new version" })).toHaveCount(0); // saved: the editor closed
  expect(errors).toEqual([]);
});

test("Connect clips (M65): the animated model asks Kimodo for a transition; its page puts it on with the clips", async ({ page }) => {
  const posted = await mockAsset(page, "mock_connect", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_rig_skin", step: "rig_skin", parent: "010_upload", outputs: { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" } },
    {
      version: "030_anim",
      step: "anim",
      parent: "020_rig_skin",
      params: { clips: ["Idle_A", "Walk"] },
      outputs: { rigged_anim: "rigged_anim.glb", clips: "clips.json", skeleton: "skeleton.json" },
    },
  ]);
  await page.route("**/files/mock_connect/*/rigged_anim.glb", (r) => r.fulfill({ body: ANIM, contentType: "model/gltf-binary" }));
  await page.route("**/files/mock_connect/*/clips.json", (r) =>
    r.fulfill({ json: { clips: [{ name: "Idle_A", frames: 12, seconds: 0.5 }, { name: "Walk", frames: 24, seconds: 1 }], fps: 24, missing: [] } }),
  );
  await page.route("**/files/mock_connect/*/skeleton.json", (r) => r.fulfill({ json: { template: "mixamo52", joints: [] } }));
  await page.route("**/files/mock_connect/020_rig_skin/qa.json", (r) => r.fulfill({ json: { items: {} } }));
  let parent = "";
  await page.route("**/api/assets/mock_connect/steps/*", (r) => ((parent = r.request().postDataJSON().parent), r.fallback()));
  await page.goto("/#/asset/mock_connect?version=030_anim");
  await page.getByText("Connect two clips").click();
  await expect(page.getByLabel("Connect from")).toHaveValue("Idle_A");
  await expect(page.getByLabel("Connect to")).toHaveValue("Walk");
  await page.getByLabel("Turn degrees").fill("90");
  await page.getByLabel("What happens in between").fill("turns left and starts walking");
  await page.getByRole("button", { name: "Make transition" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["motion", { from: "Idle_A", to: "Walk", turn: 90, prompt: "turns left and starts walking", seconds: 1.5 }]);
  expect(parent).toBe("030_anim"); // the clips' version is the input
  // the transition's page: what it holds, and back onto the skinned model under it, next to the clips it was made from
  await page.goto("/#/asset/mock_connect?version=040_motion");
  await expect(page.getByRole("heading", { name: "Transition" })).toBeVisible();
  await expect(page.getByText("it starts at the last pose of Idle_A and ends at the first pose of Walk")).toBeVisible();
  await page.getByRole("button", { name: "Put this motion on the model with its clips" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]).toEqual(["anim", { clips: ["Idle_A", "Walk", "040_motion"] }]);
  expect(parent).toBe("020_rig_skin");
  // leaving one side empty continues a clip: no turn or travel then
  await page.goto("/#/asset/mock_connect?version=030_anim");
  await page.getByText("Connect two clips").click();
  await page.getByLabel("Connect to").selectOption("");
  await expect(page.getByLabel("Turn degrees")).toHaveCount(0);
  await page.getByRole("button", { name: "Make transition" }).click();
  await expect.poll(() => posted.length).toBe(3);
  expect(posted[2]).toEqual(["motion", { from: "Idle_A", seconds: 1.5 }]);
});

test("Animation editor: duplicate, rename, edit and save a copy (from), delete it", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const posted = await mockAsset(page, "mock_anim3", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_rig_skin", step: "rig_skin", parent: "010_upload", outputs: { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" } },
    { version: "030_anim", step: "anim", parent: "020_rig_skin", outputs: { rigged_anim: "rigged_anim.glb", clips: "clips.json", skeleton: "skeleton.json" } },
  ]);
  await page.route("**/files/mock_anim3/*/rigged_anim.glb", (r) => r.fulfill({ body: ANIM, contentType: "model/gltf-binary" }));
  await page.route("**/files/mock_anim3/*/clips.json", (r) =>
    r.fulfill({ json: { clips: [{ name: "Idle", frames: 12, seconds: 0.5 }, { name: "Walk", frames: 24, seconds: 1 }], fps: 24, missing: [] } }),
  );
  await page.route("**/files/mock_anim3/*/skeleton.json", (r) => r.fulfill({ json: { template: "mixamo52", joints: [] } }));
  let answer = "";
  page.on("dialog", (d) => (d.type() === "prompt" ? d.accept(answer || d.defaultValue()) : d.accept()));
  await page.goto("/#/asset/mock_anim3?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.getByRole("combobox", { name: "Animation" }).selectOption({ label: "Play: Walk" });
  await page.getByRole("button", { name: "Edit animation" }).click();
  const sel = page.getByRole("combobox", { name: "Clip to edit" });
  await expect(page.locator(".anim-editor").getByRole("button", { name: "Rename", exact: true })).toHaveCount(0);
  await page.locator(".anim-editor").getByRole("button", { name: "Duplicate", exact: true }).click();
  await expect(sel).toHaveValue("Walk copy");
  await expect(sel.locator("option", { hasText: "Walk copy (copy of Walk)" })).toHaveCount(1);
  answer = "Walk limp";
  await page.locator(".anim-editor").getByRole("button", { name: "Rename", exact: true }).click();
  await expect(sel).toHaveValue("Walk limp");
  await page.getByText("Clip tools", { exact: true }).click();
  await page.getByRole("spinbutton", { name: "Speed" }).fill("0.5");
  await page.getByRole("spinbutton", { name: "Speed" }).blur();
  await page.getByRole("button", { name: "Save as a new version" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(Object.keys(posted[0][1].clips)).toEqual(["Walk limp"]);
  expect(posted[0][1].clips["Walk limp"]).toMatchObject({ from: "Walk", speed: 0.5 });
  expect(errors).toEqual([]);
});

test("Animation editor: Delete removes a copy and Undo brings it back", async ({ page }) => {
  await mockAsset(page, "mock_anim4", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_rig_skin", step: "rig_skin", parent: "010_upload", outputs: { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" } },
    { version: "030_anim", step: "anim", parent: "020_rig_skin", outputs: { rigged_anim: "rigged_anim.glb", clips: "clips.json", skeleton: "skeleton.json" } },
  ]);
  await page.route("**/files/mock_anim4/*/rigged_anim.glb", (r) => r.fulfill({ body: ANIM, contentType: "model/gltf-binary" }));
  await page.route("**/files/mock_anim4/*/clips.json", (r) =>
    r.fulfill({ json: { clips: [{ name: "Idle", frames: 12, seconds: 0.5 }, { name: "Walk", frames: 24, seconds: 1 }], fps: 24, missing: [] } }),
  );
  await page.route("**/files/mock_anim4/*/skeleton.json", (r) => r.fulfill({ json: { template: "mixamo52", joints: [] } }));
  page.on("dialog", (d) => d.accept(d.defaultValue()));
  await page.goto("/#/asset/mock_anim4?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.getByRole("combobox", { name: "Animation" }).selectOption({ label: "Play: Walk" });
  await page.getByRole("button", { name: "Edit animation" }).click();
  const sel = page.getByRole("combobox", { name: "Clip to edit" });
  await page.locator(".anim-editor").getByRole("button", { name: "Duplicate", exact: true }).click();
  await expect(sel).toHaveValue("Walk copy");
  await page.screenshot({ path: process.env.M44_SHOT ?? "test-results/m44.png" });
  await page.locator(".anim-editor").getByRole("button", { name: "Delete", exact: true }).click();
  await expect(sel).toHaveValue("Walk");
  await expect(sel.locator("option")).toHaveCount(2);
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(sel.locator("option")).toHaveCount(3);
  // undo of the duplicate itself: the panel stays, on the base clip; Save is off with nothing to send; redo: the copy again
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(sel.locator("option")).toHaveCount(2);
  await expect(sel).toHaveValue("Walk");
  await expect(page.getByRole("button", { name: "Save as a new version" })).toBeDisabled();
  await page.getByRole("button", { name: "Redo" }).click();
  await expect(sel.locator("option")).toHaveCount(3);
  await sel.selectOption("Walk copy");
  await expect.poll(() => page.evaluate(() => !!(window as any).__viewer.getClip("Walk copy"))).toBe(true);
  await page.locator(".anim-editor").getByRole("button", { name: "Close", exact: true }).click();
  await expect.poll(() => page.evaluate(() => !!(window as any).__viewer.getClip("Walk copy"))).toBe(false);
});

test("Animation tools: IK drag, a foot lock, a hips move, curves, motion path, onion skin, zoom", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const posted = await mockAsset(page, "mock_anim2", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_rig_skin", step: "rig_skin", parent: "010_upload", outputs: { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" } },
    { version: "030_anim", step: "anim", parent: "020_rig_skin", outputs: { rigged_anim: "rigged_anim.glb", clips: "clips.json", skeleton: "skeleton.json" } },
  ]);
  await page.route("**/files/mock_anim2/*/rigged_anim.glb", (r) => r.fulfill({ body: ANIM, contentType: "model/gltf-binary" }));
  await page.route("**/files/mock_anim2/*/clips.json", (r) =>
    r.fulfill({ json: { clips: [{ name: "Idle", frames: 12, seconds: 0.5 }, { name: "Walk", frames: 24, seconds: 1 }], fps: 24, missing: [] } }),
  );
  await page.route("**/files/mock_anim2/*/skeleton.json", (r) => r.fulfill({ json: { template: "mixamo52", joints: [] } }));
  const head = () => page.evaluate(() => (window as any).__viewer.worldOf("Head") as number[]);
  const at = async (f: number) => (await page.getByRole("spinbutton", { name: "Frame number" }).fill(String(f)), head());
  const near = (a: number[], b: number[], tol = 2e-3) => a.every((x, i) => Math.abs(x - b[i]) < tol);
  await page.goto("/#/asset/mock_anim2?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.getByRole("combobox", { name: "Animation" }).selectOption({ label: "Play: Walk" });
  await page.getByRole("button", { name: "Pause" }).click();
  await page.getByRole("spinbutton", { name: "Frame number" }).fill("8");
  await page.getByRole("button", { name: "Edit animation" }).click();
  await expect(page.getByRole("combobox", { name: "Clip to edit" })).toHaveValue("Walk");
  // IK drag: the head pulled aside, the spine and hips bend so it gets there, and stays there (three keys at frame 8)
  await page.evaluate(() => (window as any).__viewer.testPickBone("Head"));
  await page.getByRole("button", { name: "IK drag" }).click();
  const target = (await head()).map((x, i) => x + [0.2, -0.1, 0.05][i]);
  await page.evaluate((t) => (window as any).__viewer.testIK(t), target);
  await expect.poll(async () => near(await head(), target)).toBe(true);
  // a foot lock on the head over 6–10: it holds where it is at 8
  await page.getByText("Foot locks").click();
  await page.getByRole("button", { name: "Lock Head here" }).click();
  await expect(page.getByRole("group", { name: "Lock 1" })).toBeVisible();
  for (const f of [6, 10]) await expect.poll(async () => near(await at(f), target)).toBe(true);
  expect(near(await at(13), target)).toBe(false); // outside the lock and its blend: free again
  // Find foot contacts on a column: no feet near the floor
  await page.getByRole("button", { name: "Find foot contacts" }).click();
  await expect(page.getByText("No feet found near the floor.")).toBeVisible();
  // the hips moved 10 cm along X at frame 8: a move key
  await at(8);
  await page.evaluate(() => (window as any).__viewer.testPickBone("Hips"));
  await page.getByRole("button", { name: "Move", exact: true }).click();
  const hips: number[] = await page.evaluate(() => (window as any).__viewer.bones.find((b: any) => b.name === "Hips").position.toArray());
  await page.evaluate((to) => (window as any).__viewer.testMove("Hips", to), [hips[0] + 0.1, hips[1], hips[2]]);
  await expect(page.getByRole("spinbutton", { name: "X cm" })).toHaveValue("10");
  // the curves of that move; the motion path; the onion skin; zoom
  await page.getByRole("button", { name: "Curves" }).click();
  await expect(page.getByRole("img", { name: /Curves/ }).or(page.locator("canvas[aria-label^='Curves']"))).toHaveCount(1);
  await expect(page.locator(".ds-labels")).toContainText("X (cm)");
  await page.getByRole("checkbox", { name: "Motion path" }).check();
  await expect.poll(() => page.evaluate(() => (window as any).__viewer.testPath()?.length)).toBe(25 * 3);
  await page.getByRole("checkbox", { name: "Onion skin" }).check();
  await expect.poll(() => page.evaluate(() => (window as any).__viewer.testGhosts())).toEqual([-6, -3, 3, 6]);
  await page.getByRole("slider", { name: "Zoom" }).fill("4");
  await expect.poll(() => page.locator(".dopesheet").evaluate((e) => e.scrollWidth > e.clientWidth + 100)).toBe(true);
  await page.screenshot({ path: "test-results/anim-tools.png", animations: "disabled" });
  // Save: the turns, the move and the lock
  await page.getByRole("button", { name: "Save as a new version" }).click();
  await expect.poll(() => posted.length).toBe(1);
  const walk = posted[0][1].clips.Walk;
  expect(Object.keys(walk.keys).sort()).toEqual(["Head", "Hips", "Spine"]);
  expect(walk.offsets.Hips).toMatchObject([{ f: 8, falloff: 10 }]);
  expect(walk.offsets.Hips[0].v[0]).toBeCloseTo(0.1, 4);
  expect(walk.feet).toEqual([{ bone: "Head", from: 6, to: 10, blend: 3 }]);
  expect(errors).toEqual([]);
});

test("IK controls (M63): the armature drawn, a target keyed twice, the pole swung, the weight, undo, saved", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const posted = await mockAsset(page, "mock_anim_ik", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_rig_skin", step: "rig_skin", parent: "010_upload", outputs: { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" } },
    { version: "030_anim", step: "anim", parent: "020_rig_skin", outputs: { rigged_anim: "rigged_anim.glb", clips: "clips.json", skeleton: "skeleton.json" } },
  ]);
  await page.route("**/files/mock_anim_ik/*/rigged_anim.glb", (r) => r.fulfill({ body: ANIM, contentType: "model/gltf-binary" }));
  await page.route("**/files/mock_anim_ik/*/clips.json", (r) =>
    r.fulfill({ json: { clips: [{ name: "Idle", frames: 12, seconds: 0.5 }, { name: "Walk", frames: 24, seconds: 1 }], fps: 24, missing: [] } }),
  );
  await page.route("**/files/mock_anim_ik/*/skeleton.json", (r) => r.fulfill({ json: { template: "mixamo52", joints: [] } }));
  const world = (b: string) => page.evaluate((n) => (window as any).__viewer.worldOf(n) as number[], b);
  // the playhead set through the viewer: a fill on the frame box can race its re-render every frame ("12" + "16")
  const at = async (f: number) => (await page.evaluate((t) => ((window as any).__viewer.pause(), (window as any).__viewer.seek(t)), f / 24), world("Head"));
  const near = (a: number[], b: number[], tol = 2e-3) => a.every((x, i) => Math.abs(x - b[i]) < tol);
  const armature = () => page.evaluate(() => (window as any).__viewer.testArmature() as { bone: string; head: number[]; tail: number[]; color: number }[]);
  await page.goto("/#/asset/mock_anim_ik?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.getByRole("combobox", { name: "Animation" }).selectOption({ label: "Play: Walk" });
  await page.getByRole("button", { name: "Pause" }).click();
  await page.getByRole("spinbutton", { name: "Frame number" }).fill("8");
  await page.getByRole("button", { name: "Edit animation" }).click();
  await expect(page.getByRole("combobox", { name: "Clip to edit" })).toHaveValue("Walk");
  // the armature: an octahedron per bone from its head to its child's (the leaf half its parent's length on)
  await page.evaluate(() => (window as any).__viewer.testPickBone("Head"));
  await expect(page.getByRole("heading", { name: "Bone · Head" })).toBeVisible();
  let bones = await armature();
  const len = (b: { head: number[]; tail: number[] }) => Math.hypot(...b.head.map((x, i) => x - b.tail[i]));
  const by = (n: string) => bones.find((b) => b.bone === n)!;
  expect(near(by("Hips").tail, by("Spine").head, 1e-5) && near(by("Spine").tail, by("Head").head, 1e-5)).toBe(true);
  expect(len(by("Head")) / len(by("Spine"))).toBeCloseTo(0.5, 2);
  expect(by("Head").color).toBe(0x3aa0ff); // selected: blue
  // IK target: the chain a drag would bend drawn yellow before any key; the target dragged keys a control at frame 8
  await page.getByRole("button", { name: "IK target" }).click();
  await expect(page.getByRole("button", { name: "Pin it here (I)" })).toBeVisible();
  bones = await armature();
  expect([by("Spine").color, by("Hips").color]).toEqual([0xc8a020, 0xc8a020]);
  const t8 = (await world("Head")).map((x, i) => x + [0.2, -0.1, 0.05][i]);
  await page.evaluate((t) => (window as any).__viewer.testTarget("Head", t), t8);
  await expect.poll(async () => near(await world("Head"), t8)).toBe(true);
  await expect(page.getByRole("group", { name: "IK target at this key" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as any).__viewer.testHandles().targets.Head as number[])).toBeTruthy();
  expect(near(await page.evaluate(() => (window as any).__viewer.testHandles().targets.Head as number[]), t8)).toBe(true);
  // a second key at 16; at 12 the target is halfway (smoothstep at the middle), the head on it
  const t16 = (await at(16)).map((x, i) => x + [-0.1, -0.2, 0.1][i]);
  await page.evaluate((t) => (window as any).__viewer.testTarget("Head", t), t16);
  await expect.poll(async () => near(await world("Head"), t16)).toBe(true);
  expect(near(await at(12), t8.map((x, i) => (x + t16[i]) / 2))).toBe(true);
  // the pole swung 45° at 16: the head stays on its target, the spine swings, the key says 45
  await at(16);
  const spine = await world("Spine");
  await page.evaluate(() => (window as any).__viewer.testSwivel("Head", 45));
  await expect(page.getByRole("spinbutton", { name: "IK swivel degrees" })).toHaveValue("45");
  await expect.poll(async () => near(await world("Spine"), spine)).toBe(false);
  expect(near(await world("Head"), t16)).toBe(true);
  // weight 0 at 16: the clip as it was there; undo puts the IK back
  await page.getByRole("spinbutton", { name: "IK weight percent" }).fill("0");
  await page.getByRole("spinbutton", { name: "IK weight percent" }).press("Enter");
  await expect.poll(async () => near(await world("Head"), t16)).toBe(false);
  await page.keyboard.press("Control+z");
  await expect.poll(async () => near(await world("Head"), t16)).toBe(true);
  // the control in its list and on a dope sheet row of its own
  await page.getByText("IK controls (1)").click();
  await expect(page.getByRole("group", { name: "IK control 1" })).toContainText("2 bones · world · 2 keys (8–16)");
  await expect(page.getByText("Head · IK").first()).toBeAttached();
  await page.screenshot({ path: "test-results/anim-ik.png", animations: "disabled" });
  await page.getByRole("button", { name: "Save as a new version" }).click();
  await expect.poll(() => posted.length).toBe(1);
  const ik = posted[0][1].clips.Walk.ik;
  expect(ik).toHaveLength(1);
  expect(ik[0]).toMatchObject({ bone: "Head", chain: 2 });
  expect(ik[0].keys.map((k: { f: number; swivel: number; w: number }) => [k.f, k.swivel, k.w])).toEqual([
    [8, 0, 1],
    [16, 45, 1],
  ]);
  expect(errors).toEqual([]);
});

test("Import: a model file from the nav page becomes an asset and is read in; an imported animation edits; a size fix re-imports", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const posted = await mockAsset(page, "mock_imp", [
    { version: "010_upload", step: "upload", parent: null, params: { name: "hero.fbx" }, outputs: { model: "source/hero.fbx" } },
    {
      version: "020_import_model",
      step: "import_model",
      parent: "010_upload",
      params: {},
      outputs: { mesh: "mesh.glb" },
      metrics: { format: ".obj", height_m: 152.4, scale_hint: 0.01, triangles: 1200, meshes: 2, materials: 1, textures: 1, missing_textures: ["wood.png"], skeleton: null, skeleton_note: "no skeleton", merged_bones: 0, clips: [] },
    },
  ]);
  await page.route("**/api/assets", (r) => (r.request().method() === "POST" ? r.fulfill({ json: { id: "mock_imp", name: "hero", created: "" } }) : r.fallback()));
  let put = "";
  await page.route("**/api/assets/mock_imp/upload/*", (r) => {
    put = decodeURIComponent(r.request().url().split("/").pop()!);
    return r.fulfill({ json: { version: "010_upload", step: "upload", parent: null, params: { name: put }, outputs: { model: "source/hero.fbx" } } });
  });
  const parents: string[] = [];
  await page.route("**/api/assets/mock_imp/steps/import_model", (r) => (parents.push(r.request().postDataJSON().parent), r.fallback()));
  await page.route("**/files/mock_imp/*/rigged_anim.glb", (r) => r.fulfill({ body: ANIM, contentType: "model/gltf-binary" }));
  await page.route("**/files/mock_imp/*/clips.json", (r) => r.fulfill({ json: { clips: [{ name: "Walk", frames: 24, seconds: 1 }], fps: 24, missing: [] } }));
  await page.route("**/files/mock_imp/*/skeleton.json", (r) => r.fulfill({ json: { template: "mixamo52", joints: [] } }));
  await page.route("**/files/mock_imp/*/qa.json", (r) => r.fulfill({ json: { items: {} } }));
  // Import sits beside New asset; New asset no longer imports
  await page.goto("/#/new");
  await expect(page.getByRole("button", { name: "Import a 3D model" })).toHaveCount(0);
  await page.getByRole("navigation").getByRole("link", { name: "Import" }).click();
  await expect(page.getByRole("heading", { name: "Import", exact: true })).toBeVisible();
  const pick = page.getByLabel("Model file or .zip");
  await pick.setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("x") });
  await expect(page.getByText(/notes\.txt: this isn't a model file/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Import", exact: true })).toBeDisabled();
  await pick.setInputFiles({ name: "hero.fbx", mimeType: "application/octet-stream", buffer: Buffer.from("fbx") });
  await page.screenshot({ path: "test-results/import-page.png", animations: "disabled" });
  await page.getByRole("button", { name: "Import", exact: true }).click();
  // uploaded, then read in from that upload; the animated result opens by itself and edits like ours
  await expect.poll(() => posted.map(([s]) => s)).toEqual(["import_model"]);
  expect(put).toBe("hero.fbx");
  expect(posted[0][1]).toEqual({});
  expect(parents).toEqual(["010_upload"]);
  await expect(page).toHaveURL(/version=030_import_model/);
  await expect(page.getByRole("button", { name: "Edit animation" })).toBeVisible();
  // a static import 150 m tall: what came in, and Centimetres re-imports the same upload at 0.01
  await page.goto("/#/asset/mock_imp?version=020_import_model");
  const came = page.getByRole("region", { name: "What came in" });
  await expect(came).toContainText("OBJ");
  await expect(came).toContainText("152.40 m");
  await expect(came).toContainText("Textures not found: wood.png");
  await expect(came).toContainText("about 100× too big");
  await page.screenshot({ path: "test-results/import-info.png", animations: "disabled" });
  await came.getByRole("button", { name: "Centimetres" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]).toEqual(["import_model", { scale: 0.01 }]);
  expect(parents[1]).toBe("010_upload");
  // the upload itself: the original file, imported again from here
  await page.goto("/#/asset/mock_imp?version=010_upload");
  await expect(page.getByRole("heading", { name: "Original file" })).toBeVisible();
  await page.getByRole("checkbox", { name: "Keep it as a still model (ignore its skeleton and animations)" }).check();
  await page.getByRole("button", { name: "Import it" }).click();
  await expect.poll(() => posted.length).toBe(3);
  expect(posted[2]).toEqual(["import_model", { rig: false }]);
  expect(errors).toEqual([]);
});

test("An FBX export with PNG textures opens as an export, its files as buttons", async ({ page }) => {
  await mockAsset(page, "mock_fbx", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    {
      version: "020_export",
      step: "export",
      parent: "010_upload",
      params: { profile: "unity" },
      outputs: { "cat.fbx": "unity/cat.fbx", "cat_basecolor.png": "unity/cat_basecolor.png", "CREDITS.txt": "unity/CREDITS.txt" },
    },
  ]);
  await page.goto("/#/asset/mock_fbx?version=020_export");
  await expect(page.getByRole("heading", { name: "Exported for Unity" })).toBeVisible(); // not "Prepare the reference"
  // an FBX with its PNGs is one package: saving it all to a folder is the main action (UX-10); the files stay one by one
  await expect(page.getByRole("button", { name: "Save all files to the folder" })).toHaveClass(/primary/);
  await expect(page.locator("aside.panel")).toContainText("Drop the folder into your Unity project's Assets");
  await page.getByText("Or download the files one by one").click();
  await expect(page.getByRole("link", { name: "cat.fbx", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "cat_basecolor.png" })).toHaveClass(/button/);
});

test("Setup: Connect an agent shows each agent's setup, copies it and tests the server", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/agent/setup", (r) =>
    r.fulfill({
      json: {
        root: "D:/Effigy",
        server: "D:/Effigy/mcp/effigy_server.py",
        uv: "D:/uv/uv.exe",
        clients: [
          { id: "claude-code", name: "Claude Code", how: "Run this in a terminal:", text: "claude mcp add -s user effigy -- uv run ..." },
          { id: "codex", name: "Codex", how: "Add this to ~/.codex/config.toml:", text: "[mcp_servers.effigy] command = uv" },
        ],
      },
    }),
  );
  await page.route("**/api/agent/test", (r) => r.fulfill({ json: { ok: true, tools: ["list_assets", "look", "run_step"] } }));
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/#/setup");
  const sec = page.getByRole("region", { name: "Connect an agent" });
  await expect(sec.getByLabel("Claude Code setup")).toHaveText("claude mcp add -s user effigy -- uv run ...");
  await sec.getByRole("button", { name: "Codex" }).click();
  await expect(sec.getByText("Add this to ~/.codex/config.toml:")).toBeVisible();
  await sec.getByRole("button", { name: "Copy" }).click();
  await expect(sec.getByRole("button", { name: "Copied" })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("[mcp_servers.effigy]");
  await sec.getByRole("button", { name: "Test the connection" }).click();
  await expect(sec.getByText("offers 3 tools (list_assets, look, run_step)")).toBeVisible();
  expect(errors).toEqual([]);
});

test("Image flow: cut-out → Faithful → prepare → check → export, without raw JSON", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/models", (r) => r.fulfill({ json: [] })); // nothing known missing: Generate 3D, not its setup link
  await fakeCard(page, 14.7, [["faithful-hd", 19.4]]); // the 16 GB laptop: the HD preset is for bigger cards
  const posted = await mockAsset(page, "mock_lantern", [
    { version: "010_upload", step: "upload", parent: null, outputs: { image: "image.png" } },
    { version: "020_prep", step: "prep", parent: "010_upload", outputs: { image: "image.png", image_rgb: "image_rgb.png" } },
  ]);
  await page.goto("/#/asset/mock_lantern?version=020_prep");
  // M46b: the best this card runs is the default, before any setting (quality over speed, the owner)
  await expect(page.locator(".panel")).toContainText("Faithful, high quality — Recommended for this GPU");
  await page.getByText("Advanced", { exact: true }).click();
  await expect(page.getByRole("radio", { name: /Faithful, high quality/ })).toBeChecked();
  await expect(page.getByRole("radio", { name: /Faithful HD/ })).toBeDisabled();
  await page.getByRole("button", { name: "Original" }).click(); // compare with the uploaded picture
  await page.getByLabel("Background behind your picture").selectOption("grey"); // a dark subject
  await page.getByRole("button", { name: "Generate 3D" }).click();
  await expect.poll(() => posted.find(([s]) => s === "trellis2")?.[1]).toMatchObject({ background: "grey" });
  await expect(page).toHaveURL(/version=030_trellis2/); // the result opens by itself
  await page.getByText("Help", { exact: true }).click();
  await expect(page.getByText("Prepare model closes them")).toBeVisible();
  await expect(page.getByRole("button", { name: "Try another variation" })).toBeVisible();
  await page.getByRole("button", { name: "Prepare model" }).click();
  await expect(page.locator("#checklist")).toContainText("1 check passed");
  await page.screenshot({ path: "test-results/workspace-checks.png", animations: "disabled" });
  await page.getByRole("radio", { name: "Unreal" }).check();
  await page.getByLabel("Collision hull").uncheck();
  await page.getByRole("button", { name: "Export for Unreal" }).click();
  await expect(page.getByRole("link", { name: "Download lantern.glb" })).toBeVisible();
  await expect(page.getByRole("link", { name: "CREDITS.txt" })).toBeVisible();
  await page.screenshot({ path: "test-results/workspace-export.png", animations: "disabled" });
  await page.setViewportSize({ width: 390, height: 844 }); // phone width: one column, the page scrolls
  await page.screenshot({ path: "test-results/workspace-390.png", fullPage: true, animations: "disabled" });
  expect(posted.map(([s]) => s)).toEqual(["trellis2", "clean", "remesh", "uv", "bake", "validate", "export"]);
  expect(posted[0][1]).toMatchObject({ weights: "faithful", steps: 20, texture_size: 4096 });
  expect(posted[2][1]).toEqual({ faces: 10000 });
  expect(posted[5][1]).toEqual({ max_triangles: 10000 });
  expect(posted[6][1]).toEqual({ profile: "unreal", lods: true, collision: false });
  expect(errors).toEqual([]);
});

test("Concepts: ranked display, selecting doesn't run, prep gets the original key; partial prep continues", async ({ page }) => {
  await page.route("**/api/models", (r) => r.fulfill({ json: [] }));
  const posted = await mockAsset(page, "mock_concepts", [
    { version: "010_brief", step: "brief", parent: null, params: { prompt: "a lantern", category: "prop" }, outputs: { brief: "brief.yaml" } },
    {
      version: "020_concepts",
      step: "concepts",
      parent: "010_brief",
      outputs: { cand_0: "cand_0.png", cand_1: "cand_1.png", cand_3: "cand_3.png", scores: "scores.json" },
    },
  ]);
  await page.goto("/#/asset/mock_concepts?version=020_concepts");
  const cards = page.locator(".grid .cand");
  await expect(cards.first()).toContainText("#1");
  await expect(page.locator("summary", { hasText: "Edit in Blender" })).toHaveCount(0); // pictures: nothing for Blender
  await expect(cards.nth(2)).toContainText("needs: subject stays inside the image");
  await page.screenshot({ path: "test-results/workspace-concepts.png", animations: "disabled" });
  await cards.first().click();
  expect(posted).toEqual([]); // selection only
  await page.getByRole("button", { name: "Use this image" }).click();
  await expect(page).toHaveURL(/version=030_prep/);
  expect(posted[0]).toEqual(["prep", { pick: 3 }]);

  // a run that stopped after clean offers the remaining steps, not the whole recipe again (a failed step leaves no version)
  await page.getByRole("button", { name: "Generate 3D" }).click();
  await expect(page).toHaveURL(/version=040_trellis2/);
  await page.route("**/api/assets/mock_concepts/steps/remesh", (r) => (posted.push(["remesh", r.request().postDataJSON().params]), r.fulfill({ status: 202, json: { job_id: "job-oom" } })));
  await page.route("**/api/jobs/job-oom", (r) => r.fulfill({ json: { status: "failed", error: { code: "oom", message: "CUDA out of memory" } } }));
  await page.getByRole("button", { name: "Prepare model" }).click();
  await expect(page.getByRole("alert")).toContainText("ran out of GPU memory");
  await expect(page).toHaveURL(/version=050_clean/); // the last step that worked
  await expect(page.locator(".panel")).toContainText("Prepared up to Clean; Simplify, Unwrap, Bake and Check remain.");
  await expect(page.getByText("simplify → unwrap → bake → check")).toBeVisible();
  await page.unroute("**/api/assets/mock_concepts/steps/remesh");
  await page.getByRole("button", { name: "Continue preparation" }).click();
  await expect(page.locator("#checklist")).toBeVisible();
  expect(posted.map(([s]) => s).slice(2)).toEqual(["clean", "remesh", "remesh", "uv", "bake", "validate"]);
});

test("Setup: a gated model asks for the login only while this account may not read its repo", async ({ page }) => {
  const dino = { id: 1, name: "facebook/dinov3-vitl16-pretrain-lvd1689m", groups: ["fast", "faithful"], revision: "r", where: "hub", installed: false, partial: false, bytes: 0, missing_bytes: 1.2e9 };
  await page.route("**/api/models", (r) => r.fulfill({ json: [dino] }));
  const gated = { "facebook/dinov3-vitl16-pretrain-lvd1689m": ["fast", "faithful"] };
  let access: Record<string, boolean | null> = {};
  await page.route("**/api/hf", (r) => r.fulfill({ json: { token: !!Object.keys(access).length, user: access ? "owner" : null, access, gated } }));
  const card = page.locator(".card", { hasText: "3D model, Fast" });
  await page.goto("/#/setup");
  await expect(card).toContainText("Needs a Hugging Face login first"); // no token: the licence must be accepted first
  access = { "facebook/dinov3-vitl16-pretrain-lvd1689m": true };
  await page.reload();
  await expect(card).toContainText("Not installed · 1.2 GB to download"); // the account may read it: just install
  access = { "facebook/dinov3-vitl16-pretrain-lvd1689m": false };
  await page.reload();
  await expect(card).toContainText("Needs a Hugging Face login first");
});

test("Setup shows capabilities and renders every licenses.yaml row and the attributions", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

  // one "- " line per row in licenses.yaml (flow or block style); the API serves the parsed rows
  const yamlRows = readFileSync(new URL("../../licenses.yaml", import.meta.url), "utf8")
    .split("\n")
    .filter((l) => l.startsWith("- ")).length;
  const rows: { name: string }[] = await (await request.get("/api/licenses")).json();
  const models: unknown[] = await (await request.get("/api/models")).json();
  expect(rows.length).toBe(yamlRows);

  await page.goto("/#models"); // the old address still lands on Setup
  await expect(page).toHaveURL(/#\/setup$/);
  await toLibrary(page);
  await page.getByRole("navigation").getByRole("link", { name: "Setup" }).click();
  await expect(page.getByText("3D model, Faithful (recommended)")).toBeVisible();
  await expect(page.locator("#licence-table tbody tr")).toHaveCount(yamlRows);
  for (const r of rows) await expect(page.locator("#licence-table tbody")).toContainText(r.name);
  await expect(page.locator("#attributions .attribution", { hasText: "Built with DINOv3" })).toBeVisible();
  await expect(page.locator("#model-table tbody tr")).toHaveCount(models.length);
  await expect(page.locator("#model-table button")).toHaveCount(0); // a reference: Install on the cards, Remove under Space
  await expect(page.locator("#cap-multiview")).toContainText("3D model from four views"); // its own card now
  await page.screenshot({ path: "test-results/models.png", fullPage: true });
  await toLibrary(page);
  await expect(page.locator("#licence-table")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Library" })).toBeVisible();
  await page.screenshot({ path: "test-results/library.png", animations: "disabled" });
  await page.getByRole("navigation").getByRole("link", { name: "New asset" }).click();
  await expect(page.getByRole("heading", { name: "What would you like to make?" })).toBeVisible();
  await page.screenshot({ path: "test-results/new-asset.png", animations: "disabled" });
  expect(errors).toEqual([]);
});

// a rig_fit version with a 5-joint skeleton and two markers, opened in the viewer; `parts`: a parts version's (M32)
async function openRig(page: Page, parts?: Record<string, number>) {
  const rigOutputs = { mesh: "mesh.glb", skeleton: "skeleton.json", markers: "markers.json", report: "report.json" };
  const posted = await mockAsset(page, "mock_rig", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    {
      version: "020_rig_fit",
      step: "rig_fit",
      parent: "010_upload",
      outputs: rigOutputs,
      metrics: { source: "edited", edited_joints: ["Spine", "Head"], edited_twists: [], ...(parts && { parts }) },
    },
  ]);
  const joint = (name: string, parent: string | null, position: number[]) => ({ name, parent, position });
  await page.route("**/files/mock_rig/*/skeleton.json", (r) =>
    r.fulfill({
      json: {
        joints: [
          joint("Hips", null, [0, 1, 0]),
          joint("Spine", "Hips", [0, 1.2, 0]),
          joint("Head", "Spine", [0, 1.6, 0]),
          joint("LeftArm", "Spine", [0.3, 1.4, 0]),
          joint("RightArm", "Spine", [-0.3, 1.4, 0]),
        ],
      },
    }),
  );
  await page.route("**/files/mock_rig/*/markers.json", (r) => r.fulfill({ json: { markers: { Chin: [0, 1.5, 0], Groin: [0, 0.9, 0] } } }));
  await page.route("**/files/mock_rig/*/report.json", (r) => r.fulfill({ json: { joints: 5, inside: 4, outside: ["Head"], low_confidence: [] } }));
  await page.goto("/#/asset/mock_rig?version=020_rig_fit");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  return posted;
}

test("Attach to skeleton with rigid parts: one on the nearest bone, one on a chosen bone, the rest bends", async ({ page }) => {
  const posted = await openRig(page, { Helmet: 1000, Shield: 500, Belt: 300, Body: 9000 });
  await expect(page.locator("summary", { hasText: "Edit in Blender" })).toBeVisible(); // a model: Blender can work on it
  await expect(page.getByLabel("How Body moves")).toHaveCount(0); // the rest of the model always bends
  await page.getByLabel("How Helmet moves").selectOption("nearest");
  await page.getByLabel("How Shield moves").selectOption("LeftForeArm");
  await page.getByRole("button", { name: "Attach to skeleton" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["rig_skin", { rigid: { Helmet: "", Shield: "LeftForeArm" } }]);
});

test("Skeleton editor: pick a joint, move it, undo, save only what changed", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const posted = await openRig(page);
  await expect(page.getByText("Outside the model: Head.")).toContainText("red in the view");
  await expect(page.getByText("Edited by hand: 2 joints moved, 0 twists set.")).toBeVisible();
  await page.getByRole("button", { name: "Edit skeleton" }).click();
  await expect(page.getByRole("button", { name: "Save skeleton" })).toBeDisabled();
  await page.getByLabel("Symmetry", { exact: false }).uncheck();
  await page.getByRole("button", { name: "LeftArm", exact: true }).click();
  await expect(page.getByText("Where it sits: The shoulder ball, where the upper arm rotates: inside the shoulder, not on its surface.")).toBeVisible();
  const at = () => page.evaluate(() => (window as any).__viewer.testJoint("LeftArm"));
  await expect.poll(at).toEqual([0.3, 1.4, 0]);
  await page.evaluate(() => (window as any).__viewer.testMoveJoint("LeftArm", [0.5, 1.3, 0.1]));
  await expect.poll(at).toEqual([0.5, 1.3, 0.1]);
  await page.keyboard.press("Control+z");
  await expect.poll(at).toEqual([0.3, 1.4, 0]);
  await page.keyboard.press("Control+Shift+z");
  await expect.poll(at).toEqual([0.5, 1.3, 0.1]);
  await expect(page.getByRole("button", { name: "Reset all" })).toBeEnabled();
  await page.getByRole("button", { name: "Reset all" }).click();
  await expect.poll(at).toEqual([0.3, 1.4, 0]);
  await expect(page.getByRole("button", { name: "Reset all" })).toBeDisabled();
  await page.keyboard.press("Control+z"); // one history step: the move comes back
  await expect.poll(at).toEqual([0.5, 1.3, 0.1]);
  await expect(page.getByRole("button", { name: "Save skeleton" })).toBeEnabled();
  // a refused save (UI-001): the editor stays open with the edit and its history; the next save goes through
  await page.route("**/api/assets/mock_rig/steps/rig_fit", (r) => r.fulfill({ status: 400, json: { error: { code: "bad_params", message: "a joint outside the model" } } }), { times: 1 });
  await page.getByRole("button", { name: "Save skeleton" }).click();
  await expect(page.locator(".error, [role=alert]").first()).toContainText("a joint outside the model");
  await expect(page.getByRole("button", { name: "Save skeleton" })).toBeEnabled();
  await expect.poll(at).toEqual([0.5, 1.3, 0.1]);
  await expect(page.getByRole("button", { name: "Undo" })).toBeEnabled();
  await page.getByRole("button", { name: "Save skeleton" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted).toEqual([["rig_fit", { joints: { LeftArm: [0.5, 1.3, 0.1] }, twists: {} }]]);
  await expect(page.getByRole("button", { name: "Save skeleton" })).toHaveCount(0); // closed on the new version
  expect(errors).toEqual([]);
});

test("Skeleton editor: a failed save keeps the edits (UI-001)", async ({ page }) => {
  const posted = await openRig(page);
  await page.route("**/api/jobs/job-*", (r) => r.fulfill({ json: { status: "failed", error: { code: "step_failed", message: "no fit" } } }));
  await page.getByRole("button", { name: "Edit skeleton" }).click();
  await page.getByLabel("Symmetry", { exact: false }).uncheck();
  await page.getByRole("button", { name: "LeftArm", exact: true }).click();
  await page.evaluate(() => (window as any).__viewer.testMoveJoint("LeftArm", [0.5, 1.3, 0.1]));
  await page.getByRole("button", { name: "Save skeleton" }).click();
  await expect.poll(() => posted.length).toBe(1);
  await expect(page.getByRole("alert")).toContainText("no fit");
  await expect(page.getByRole("button", { name: "Save skeleton" })).toBeEnabled(); // still editing, the move kept
  await expect.poll(() => page.evaluate(() => (window as any).__viewer.testJoint("LeftArm"))).toEqual([0.5, 1.3, 0.1]);
});

test("Marker editor: gizmo path, undo, reset all, save", async ({ page }) => {
  const posted = await openRig(page);
  await page.getByRole("button", { name: "Edit markers" }).click();
  await page.getByLabel("Symmetry", { exact: false }).uncheck();
  await page.getByRole("button", { name: "Chin", exact: true }).click();
  await expect(page.getByText("Where it sits: The tip of the chin, on the surface.")).toBeVisible();
  const at = () => page.evaluate(() => (window as any).__viewer.testJoint("Chin"));
  const move = (p: number[]) => page.evaluate((p) => (window as any).__viewer.testMoveMarker("Chin", p), p);
  await expect.poll(at).toEqual([0, 1.5, 0]);
  await move([0.1, 1.5, 0.1]);
  await expect.poll(at).toEqual([0.1, 1.5, 0.1]);
  await page.keyboard.press("Control+z");
  await expect.poll(at).toEqual([0, 1.5, 0]);
  await move([0.2, 1.4, 0]);
  await page.getByRole("button", { name: "Reset all" }).click();
  await expect.poll(at).toEqual([0, 1.5, 0]);
  await expect(page.getByRole("button", { name: "Reset all" })).toBeDisabled();
  await move([0.1, 1.5, 0.1]);
  await page.getByRole("button", { name: "Save markers" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0][1].markers.Chin).toEqual([0.1, 1.5, 0.1]);
  await expect(page.getByRole("button", { name: "Save markers" })).toHaveCount(0); // saved: the editor closed
});

test("Creatures: an animal fits its template; its skeleton mirrors by suffix, turns, and has no markers", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const posted = await mockAsset(page, "mock_dog", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    {
      version: "020_rig_fit",
      step: "rig_fit",
      parent: "010_upload",
      outputs: { mesh: "mesh.glb", skeleton: "skeleton.json", report: "report.json" },
      metrics: { template: "fox", yaw: 90, source: "template" },
    },
  ]);
  // the mesh version: What is it? → an animal → its template
  await page.goto("/#/asset/mock_dog?version=010_upload");
  await page.locator("#character > summary").click(); // the rig form waits under Character on a prepared model
  await page.getByRole("radio", { name: /four-legged animal/ }).check();
  await page.getByRole("button", { name: "Fit a skeleton" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["rig_fit", { template: "fox" }]);
  // a creature skeleton: facing +X (yaw 90), sides by suffix, mirrored across z
  const joint = (name: string, parent: string | null, position: number[]) => ({ name, parent, position });
  await page.route("**/files/mock_dog/020_rig_fit/skeleton.json", (r) =>
    r.fulfill({
      json: {
        template: "fox",
        yaw: 90,
        sides: ["_L", "_R"],
        joints: [
          joint("root", null, [0, 0, 0]),
          joint("Hips", "root", [-0.5, 1, 0]),
          joint("Head", "Hips", [0.8, 1.4, 0]),
          joint("Ear_L", "Head", [0.85, 1.6, 0.2]),
          joint("Ear_R", "Head", [0.85, 1.6, -0.2]),
        ],
      },
    }),
  );
  await page.route("**/files/mock_dog/020_rig_fit/report.json", (r) => r.fulfill({ json: { joints: 5, inside: 5, outside: [], low_confidence: [] } }));
  await page.goto("/#/asset/mock_dog?version=020_rig_fit");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await expect(page.getByRole("button", { name: "Edit markers" })).toHaveCount(0); // a person's tool
  await page.getByRole("button", { name: "Turn around" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]).toEqual(["rig_fit", { template: "fox", yaw: 270 }]);
  await page.getByRole("button", { name: "Edit skeleton" }).click();
  await expect(page.getByRole("group", { name: "Left side" })).toContainText("Ear_L");
  await expect(page.getByRole("group", { name: "Middle (spine, head, tail)" })).toContainText("Head");
  await page.getByRole("button", { name: "Ear_L", exact: true }).click();
  await page.evaluate(() => (window as any).__viewer.testMoveJoint("Ear_L", [0.9, 1.7, 0.3]));
  const at = (n: string) => page.evaluate((n) => (window as any).__viewer.testJoint(n), n);
  await expect.poll(() => at("Ear_R")).toEqual([0.9, 1.7, -0.3]); // mirrored across the animal's middle (z)
  expect(errors).toEqual([]);
});

test("Check fit: a skeleton against the one before it, the joints to look at, and unsaved moves against the saved", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await mockAsset(page, "mock_cat", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_rig_fit", step: "rig_fit", parent: "010_upload", outputs: { mesh: "mesh.glb", skeleton: "skeleton.json", report: "report.json" } },
    {
      version: "030_rig_fit",
      step: "rig_fit",
      parent: "020_rig_fit",
      outputs: { mesh: "mesh.glb", skeleton: "skeleton.json", report: "report.json" },
      metrics: { template: "fox", source: "edited", edited_joints: ["Ear_L"] },
    },
  ]);
  const joint = (name: string, parent: string | null, position: number[]) => ({ name, parent, position });
  await page.route("**/files/mock_cat/*/skeleton.json", (r) =>
    r.fulfill({
      json: {
        template: "fox",
        yaw: 0,
        sides: ["_L", "_R"],
        joints: [joint("root", null, [0, 0, 0]), joint("Hips", "root", [0, 1, -0.5]), joint("Head", "Hips", [0, 1.4, 0.8]), joint("Ear_L", "Head", [0.2, 1.6, 0.85]), joint("Ear_R", "Head", [-0.2, 1.6, 0.85])],
      },
    }),
  );
  await page.route("**/files/mock_cat/*/report.json", (r) => r.fulfill({ json: { joints: 5, inside: 5, outside: [], low_confidence: [] } }));
  const checked: [string, any][] = [];
  await page.route("**/api/assets/mock_cat/versions/*/fit-check", (r) => {
    const version = r.request().url().split("/").at(-2)!;
    checked.push([version, r.request().postDataJSON()]);
    const score = { "020_rig_fit": 58, "030_rig_fit": Object.keys(r.request().postDataJSON().joints).length ? 75 : 69 }[version]!;
    r.fulfill({ json: { score, joints: 4, inside: 4, centred: {}, bones_outside: {}, attention: [["Ear_L", "close to the skin"]] } });
  });
  await page.goto("/#/asset/mock_cat?version=030_rig_fit");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.getByRole("button", { name: "Check fit" }).click();
  await expect(page.getByText("Fit 69 · before (020_rig_fit) 58 · better")).toBeVisible();
  await expect(page.getByText("4 of 4 joints inside the model.", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Ear_L" }).first().click(); // the listed joint: the editor opens on it
  await expect(page.getByRole("button", { name: "Save skeleton" })).toBeVisible();
  await expect(page.getByRole("group", { name: "Left side" }).getByRole("button", { name: "Ear_L" })).toHaveAttribute("aria-pressed", "true");
  const save = page.getByRole("button", { name: "Save skeleton" });
  await expect // the editor takes the move once its view is up; the same absolute move again is harmless
    .poll(async () => (await page.evaluate(() => (window as any).__viewer.testMoveJoint("Ear_L", [0.25, 1.65, 0.85])), await save.isEnabled()))
    .toBe(true);
  await page.getByRole("button", { name: "Check fit" }).click();
  await expect(page.getByText("Fit 75 · saved 69 · better")).toBeVisible();
  const sent = checked.findLast(([v, b]) => v === "030_rig_fit" && Object.keys(b.joints).length)![1].joints;
  expect(sent.Ear_L).toEqual([0.25, 1.65, 0.85]);
  expect(sent.Ear_R).toEqual([-0.25, 1.65, 0.85]); // the mirror move goes along
  expect(errors).toEqual([]);
});

test("Skeleton editor: shift-select moves several joints; the XYZ field commits one undoable step", async ({ page }) => {
  await openRig(page);
  await page.getByRole("button", { name: "Edit skeleton" }).click();
  await page.getByLabel("Symmetry", { exact: false }).uncheck();
  const at = (n: string) => page.evaluate((n) => (window as any).__viewer.testJoint(n).map((x: number) => Math.round(x * 1000) / 1000), n);
  await page.getByRole("button", { name: "LeftArm", exact: true }).click();
  await page.getByRole("button", { name: "RightArm", exact: true }).click({ modifiers: ["Shift"] });
  await expect(page.getByRole("button", { name: "LeftArm", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "RightArm", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect.poll(() => page.evaluate(() => (window as any).__viewer.testSelection())).toEqual(["LeftArm", "RightArm"]); // the view has caught up
  await page.evaluate(() => (window as any).__viewer.testMoveSelected([0, 0.1, 0]));
  await expect.poll(() => at("LeftArm")).toEqual([0.3, 1.5, 0]);
  await expect.poll(() => at("RightArm")).toEqual([-0.3, 1.5, 0]);
  await page.keyboard.press("Control+z"); // one gesture, one step
  await expect.poll(() => at("RightArm")).toEqual([-0.3, 1.4, 0]);
  await expect.poll(() => at("LeftArm")).toEqual([0.3, 1.4, 0]);
  await page.getByRole("button", { name: "Select chain" }).click(); // the active joint (LeftArm) and what hangs off it: nothing here
  await page.getByRole("button", { name: "Spine", exact: true }).click(); // a plain click selects just that one
  await expect(page.getByRole("button", { name: "LeftArm", exact: true })).toHaveAttribute("aria-pressed", "false");
  await page.getByRole("button", { name: "Select chain" }).click();
  await expect(page.getByRole("button", { name: "Head", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "LeftArm", exact: true }).click();
  await page.getByLabel("Y", { exact: true }).fill("1.25");
  await page.getByLabel("Y", { exact: true }).press("Enter");
  await expect.poll(() => at("LeftArm")).toEqual([0.3, 1.25, 0]);
  await page.keyboard.press("Control+z");
  await expect.poll(() => at("LeftArm")).toEqual([0.3, 1.4, 0]);
});

test("Face from image: runs project with the defaults; a project version says what it did", async ({ page }) => {
  const posted = await mockAsset(page, "mock_face", [
    { version: "010_trellis2", step: "trellis2", parent: null, outputs: { mesh: "hi.glb", ovoxel: "ovoxel.npz" } },
    { version: "020_bake", step: "bake", parent: "010_trellis2", outputs: { mesh: "mesh.glb", basecolor: "basecolor.png" } },
    {
      version: "030_project",
      step: "project",
      parent: "020_bake",
      outputs: { mesh: "mesh.glb", basecolor: "basecolor.png", overlay: "overlay.png" },
      metrics: { iou: 0.93, region: "head", head_from: "pose", changed: 0.12 },
    },
    {
      version: "040_project",
      step: "project",
      parent: "020_bake",
      outputs: { mesh: "mesh.glb", overlay: "overlay.png" },
      metrics: { iou: 0.41, skipped: "iou", region: "head" },
    },
  ]);
  await page.goto("/#/asset/mock_face?version=020_bake");
  await page.locator("#tools > summary").click(); // the tools wait under the main action
  await page.locator("#face").getByRole("button", { name: "Face from image" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["project", { region: "head", strength: 1 }]);
  await page.goto("/#/asset/mock_face?version=030_project");
  await expect(page.getByText("Face from the image: silhouette match 0.93, 12% of the atlas, head from the pose model.")).toBeVisible();
  await expect(page.getByAltText(/silhouette/)).toBeVisible();
  await page.goto("/#/asset/mock_face?version=040_project");
  await expect(page.getByText("Not applied: the image does not line up with the mesh (silhouette match 0.41).")).toBeVisible();
});

test("Texture detail: Refine texture runs refine; a refine version shows its views", async ({ page }) => {
  const posted = await mockAsset(page, "mock_detail", [
    { version: "010_trellis2", step: "trellis2", parent: null, outputs: { mesh: "hi.glb", ovoxel: "ovoxel.npz" } },
    { version: "020_bake", step: "bake", parent: "010_trellis2", outputs: { mesh: "mesh.glb", basecolor: "basecolor.png" } },
    {
      version: "030_refine",
      step: "refine",
      parent: "020_bake",
      outputs: { mesh: "mesh.glb", basecolor: "basecolor.png", views: "views.png" },
      metrics: { views: 17, seconds: 290, refined: 0.71, drift: 2.1, detail: 2.4 },
    },
    {
      version: "040_refine",
      step: "refine",
      parent: "020_bake",
      outputs: { mesh: "mesh.glb", basecolor: "basecolor.png" },
      metrics: { views: 0, seconds: 4, delight: 0.5, ao_luma_corr_before: 0.62, ao_luma_corr: 0.04 },
    },
  ]);
  // the Refine button is enabled only with the "refine" model group installed (M43 review fix 8)
  const row = (id: number, name: string, groups: string[]) => ({ id, name, groups, revision: "r", where: "hub", installed: true, partial: false, bytes: 1, missing_bytes: 0 });
  await page.route("**/api/models", (r) => r.fulfill({ json: [row(1, "Tongyi-MAI/Z-Image-Turbo", ["concepts", "refine"]), row(2, "alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1", ["refine"])] }));
  await page.goto("/#/asset/mock_detail?version=020_bake");
  await page.locator("#tools > summary").click();
  await page.getByRole("button", { name: "Refine texture" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["refine", { strength: 1 }]);
  // M43c: the baked shadows out, without the model
  await page.reload();
  await page.locator("#tools > summary").click();
  await expect(page.getByRole("button", { name: "Remove baked shadows" })).toBeDisabled();
  await page.getByLabel("Baked shadows to remove").fill("0.5");
  await page.getByRole("button", { name: "Remove baked shadows" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]).toEqual(["refine", { views: [], delight: 0.5 }]);
  await expect(page).toHaveURL(/version=060_refine/); // the save landed on its version: the panel starts afresh there
  // the masked edit: what the 3D view frames now, with the description, at 4K (the window from the viewer's camera)
  await page.locator("#detail").getByLabel("Description").fill("red lacquered steel");
  await page.getByLabel("4K texture", { exact: false }).check();
  await page.getByRole("button", { name: "Redraw this view" }).click();
  await expect.poll(() => posted.length).toBe(3);
  const view = await page.evaluate(() => (window as any).__viewer.view());
  expect(view.extent).toBeGreaterThan(0);
  expect(posted[2]).toEqual(["refine", { strength: 1, prompt: "red lacquered steel", texture_size: 4096, views: [view] }]);
  // the window follows refine's convention (steps/_views.ortho): the front is yaw 0, the side (+X) yaw 90, from above pitch −90
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  const at = (side: string) => page.evaluate((s) => ((window as any).__viewer.frame(s), (window as any).__viewer.view()), side);
  expect(await at("front")).toMatchObject({ yaw: 0, pitch: 0 });
  expect(await at("side")).toMatchObject({ yaw: 90, pitch: 0 });
  expect((await at("top")).pitch).toBe(-90);
  await page.goto("/#/asset/mock_detail?version=030_refine");
  await expect(page.getByText(/17 of 17 views in 5 min: 71% of the texture redrawn, colour drift 2.1\./)).toBeVisible();
  await expect(page.getByAltText(/before \(left\) and after/)).toBeVisible();
  await page.getByRole("button", { name: "Enlarge the views" }).click(); // a labelled button, not a clickable picture (UX-13)
  await expect(page.getByRole("button", { name: "Show the views small" })).toHaveAttribute("aria-pressed", "true");
  await page.goto("/#/asset/mock_detail?version=040_refine");
  await expect(page.getByText(/No redraw\. Baked shadows taken out \(0\.5\): their trace in the colours 0\.62 → 0\.04\./)).toBeVisible();
});

test("Texture detail: a part painted on the model is redrawn by refine's edit (M68)", async ({ page }) => {
  const posted = await mockAsset(page, "mock_paint", [
    { version: "010_trellis2", step: "trellis2", parent: null, outputs: { mesh: "hi.glb", ovoxel: "ovoxel.npz" } },
    { version: "020_bake", step: "bake", parent: "010_trellis2", outputs: { mesh: "mesh.glb", basecolor: "basecolor.png" } },
    {
      version: "040_refine",
      step: "refine",
      parent: "020_bake",
      outputs: { mesh: "mesh.glb", basecolor: "basecolor.png", views: "views.png" },
      metrics: { op: "edit", region: "030_upload", views: 2, region_seen: 0.97, changed_outside: 0 },
    },
  ]);
  const row = (id: number, name: string, groups: string[]) => ({ id, name, groups, revision: "r", where: "hub", installed: true, partial: false, bytes: 1, missing_bytes: 0 });
  await page.route("**/api/models", (r) => r.fulfill({ json: [row(1, "Tongyi-MAI/Z-Image-Turbo", ["concepts", "refine"]), row(2, "alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1", ["refine"])] }));
  const uploads: { url: string; width: number }[] = [];
  await page.route("**/api/assets/mock_paint/upload/*", (r) => {
    const png = r.request().postDataBuffer()!;
    uploads.push({ url: r.request().url(), width: png.readUInt32BE(16) }); // the PNG's IHDR width
    return r.fulfill({ json: { version: "030_upload", step: "upload", parent: null, outputs: { image: "paint.png" } } });
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/#/asset/mock_paint?version=020_bake");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.locator("#tools > summary").click();
  // the asset's description is not what a painted part becomes: the edit waits for its own words
  await expect(page.getByRole("button", { name: "Redraw the painted part" })).toBeDisabled();
  await page.getByLabel("What the painted part becomes").fill("a brass plate");
  await page.getByRole("button", { name: "Redraw the painted part" }).click();
  await expect(page.locator("#detail").getByRole("alert")).toContainText("Paint the part to change on the model first.");
  expect(posted).toHaveLength(0);
  await page.getByRole("button", { name: "Paint the part to change" }).click();
  await expect(page.getByRole("button", { name: "Stop painting" })).toHaveAttribute("aria-pressed", "true");
  // a drag across the middle of the view, where the model is: it paints, the view does not turn
  const before = await page.evaluate(() => (window as any).__viewer.view());
  const box = (await page.locator("#viewport").boundingBox())!;
  const [x, y] = [box.x + box.width / 2, box.y + box.height / 2];
  await page.mouse.move(x - 15, y);
  await page.mouse.down();
  await page.mouse.move(x + 15, y, { steps: 6 });
  await page.mouse.up();
  expect(await page.evaluate(() => (window as any).__viewer.painted())).toBe(true);
  expect(await page.evaluate(() => (window as any).__viewer.view())).toEqual(before);
  // the veil is the brush's own: the Inspect views and the wireframe leave it alone, and the view keeps drawing
  for (const mode of ["albedo", "uv", "pbr"]) await page.getByLabel("Inspect").selectOption(mode);
  await page.getByLabel("Wireframe").check();
  await page.getByLabel("Wireframe").uncheck();
  await page.waitForTimeout(200);
  expect(errors).toEqual([]);
  await page.getByRole("button", { name: "Redraw the painted part" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(uploads).toHaveLength(1);
  expect(uploads[0].url).toMatch(/upload\/paint\.png$/);
  expect(uploads[0].width).toBe(1024); // the texture's size (no texture here: 1024)
  expect(posted[0]).toEqual(["refine", { op: "edit", region: "030_upload", prompt: "a brass plate", strength: 1 }]);
  await page.goto("/#/asset/mock_paint?version=040_refine");
  await expect(page.getByText("The painted part (030_upload) redrawn from 2 views: 97% of it seen, nothing outside it changed.")).toBeVisible();
});

test("Animate: a video becomes a motion (uploaded, then mocap on the upload), and video motions are listed", async ({ page }) => {
  await page.route("**/api/clips?*", (r) => r.fulfill({ json: [{ name: "Walk" }] }));
  const posted = await mockAsset(page, "mock_video", [
    { version: "010_trellis2", step: "trellis2", parent: null, outputs: { mesh: "hi.glb", ovoxel: "ovoxel.npz" } },
    { version: "030_rig_skin", step: "rig_skin", parent: "010_trellis2", outputs: { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" } },
    {
      version: "050_mocap",
      step: "mocap",
      parent: "040_upload",
      params: { mode: "best", person: 0, for: "030_rig_skin" },
      outputs: { clip: "clip.glb", preview: "preview.mp4" },
      metrics: { name: "dance", seconds: 14.2, mode: "best", foot_slide_mps: 0.11, people: 2, person: 0 }, // M64: a second person in the video
    },
  ]);
  await page.route("**/files/mock_video/030_rig_skin/qa.json", (r) => r.fulfill({ json: { items: {} } }));
  let parent = "";
  await page.route("**/api/assets/mock_video/steps/mocap", (r) => {
    parent = r.request().postDataJSON().parent;
    return r.fallback();
  });
  await page.route("**/api/assets/mock_video/upload/*", (r) =>
    r.fulfill({ json: { version: "060_upload", step: "upload", parent: null, outputs: { video: "video.mp4" } } }),
  );
  await page.goto("/#/asset/mock_video?version=030_rig_skin");
  await expect(page.locator('optgroup[label="Your motions"]')).toContainText("dance (video) · 14.2 s");
  await page.getByRole("radio", { name: "From a video" }).check();
  const make = page.getByRole("button", { name: "Make motion from video" });
  await expect(make).toBeDisabled(); // no file yet
  await page.getByLabel("Video file").setInputFiles({ name: "dance.mp4", mimeType: "video/mp4", buffer: Buffer.from("not a real video") });
  await expect(page.getByLabel("Chosen video")).toBeVisible(); // a small preview of the file, before any upload
  await expect(page.getByRole("button", { name: "Start here" })).toBeVisible();
  await page.getByLabel("Video motion quality").selectOption("quick");
  await page.getByLabel("Camera").selectOption("panning"); // M64
  await page.getByLabel("End second").fill("8");
  await make.click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["mocap", { mode: "quick", camera: "panning", foot_lock: true, in_place: false, smooth: 0.5, for: "030_rig_skin", end: 8 }]);
  expect(parent).toBe("060_upload"); // the step runs on the uploaded video, not on the rig
  await page.goto("/#/asset/mock_video?version=050_mocap");
  await expect(page.getByRole("heading", { name: "Motion from video" })).toBeVisible();
  // M64: the other person in the video gets a mocap job of their own, on the same upload with the same params
  await expect(page.getByText("2 people at the start of the video; this is person 1")).toBeVisible();
  await page.getByRole("button", { name: "Make the other one's motion too" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]).toEqual(["mocap", { mode: "best", person: 1, for: "030_rig_skin" }]);
  expect(parent).toBe("040_upload");
  // the next step is on the motion's page: back onto the skinned model it was made for
  let animParent = "";
  await page.route("**/api/assets/mock_video/steps/anim", (r) => {
    animParent = r.request().postDataJSON().parent;
    return r.fallback();
  });
  await page.getByRole("button", { name: "Put this motion on the model" }).click();
  await expect.poll(() => posted.length).toBe(3);
  expect(posted[2]).toEqual(["anim", { clips: ["050_mocap"] }]);
  expect(animParent).toBe("030_rig_skin");
});

test("Animate: pack clips and your motions; Generate motion and Add post the right steps", async ({ page }) => {
  await page.route("**/api/clips?*", (r) => r.fulfill({ json: [{ name: "Idle_A" }, { name: "Walk" }, { name: "Jog" }] }));
  const posted = await mockAsset(page, "mock_motion", [
    { version: "010_trellis2", step: "trellis2", parent: null, outputs: { mesh: "hi.glb", ovoxel: "ovoxel.npz" } },
    { version: "030_rig_skin", step: "rig_skin", parent: "010_trellis2", outputs: { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" } },
    {
      version: "310_motion",
      step: "motion",
      parent: "030_rig_skin",
      outputs: { clip: "clip.glb" },
      metrics: { prompt: "A person waves.", seconds: 5, generate_seconds: 12 },
    },
  ]);
  await page.route("**/files/mock_motion/030_rig_skin/qa.json", (r) => r.fulfill({ json: { items: {} } }));
  await page.goto("/#/asset/mock_motion?version=030_rig_skin");
  await expect(page.getByLabel("Collision hull")).not.toBeChecked(); // a rigged character: off by default (M32)
  await expect(page.locator('optgroup[label="Your motions"]')).toContainText("A person waves. · 5 s");
  await page.getByRole("radio", { name: "From a sentence" }).check();
  await page.getByLabel("Motion prompt").fill("A person bows.");
  await page.getByRole("button", { name: "Generate motion" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["motion", { prompt: "A person bows.", seconds: 5, seed: 0 }]);
  await page.goto("/#/asset/mock_motion?version=030_rig_skin");
  await page.getByRole("radio", { name: "Ready-made" }).check(); // the same address: the picker kept "From a sentence"
  for (const n of ["Idle_A", "Walk", "Jog"]) await page.getByRole("option", { name: new RegExp(n) }).click(); // untick the defaults
  await page.getByRole("option", { name: /A person waves/ }).click();
  await page.getByRole("button", { name: "Add 1 animation" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]).toEqual(["anim", { clips: ["310_motion"] }]);
  await page.goto("/#/asset/mock_motion?version=310_motion");
  // the motion's page says what a motion is and puts it on the skinned model it was made from (its parent)
  await page.getByRole("button", { name: "Put this motion on the model" }).click();
  await expect.poll(() => posted.length).toBe(3);
  expect(posted[2]).toEqual(["anim", { clips: ["310_motion"] }]);
});

test("Tasks: five per 3D asset on one line, a task opens its newest checkpoint or its form, Tools opens a section (UX-06)", async ({ page }) => {
  const posted = await mockAsset(page, "mock_tabs", [
    { version: "010_trellis2", step: "trellis2", parent: null, outputs: { mesh: "hi.glb", ovoxel: "ovoxel.npz" } },
    { version: "020_clean", step: "clean", parent: "010_trellis2", outputs: { mesh: "mesh.glb" } },
    { version: "030_remesh", step: "remesh", parent: "020_clean", outputs: { mesh: "mesh.glb" } },
    { version: "040_uv", step: "uv", parent: "030_remesh", outputs: { mesh: "mesh.glb" } },
    { version: "050_bake", step: "bake", parent: "040_uv", outputs: { mesh: "mesh.glb", basecolor: "basecolor.png" } },
  ]);
  await page.route("**/api/models", (r) => r.fulfill({ json: [] })); // nothing known missing: every tool "available"
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/#/asset/mock_tabs?version=020_clean");
  const tabs = page.locator(".stages li button");
  const names = async () => (await tabs.allInnerTexts()).map((t) => t.replace(/\s+/g, " ").replace("✓ ", "").replace(" optional", "").trim());
  await expect.poll(names).toEqual(["Reference", "Generate", "Prepare", "Character", "Finish"]);
  await expect(page.locator(".stages [aria-current=step]")).toHaveText("Prepare");
  await expect(page.locator(".stages small")).toHaveText("optional"); // Character
  expect(new Set(await tabs.evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top)))).size).toBe(1); // one line at laptop width
  await page.screenshot({ path: "test-results/tasks-1280.png", clip: { x: 0, y: 0, width: 1280, height: 140 } });
  const reference = page.getByRole("button", { name: "Reference" });
  await expect(reference).toBeDisabled(); // nothing behind this model: the button says what it needs, never "in the future"
  await expect(reference).toHaveAttribute("title", "Needs a picture or a description");
  await page.getByRole("button", { name: /Generate$/ }).click(); // the raw model
  await expect(page).toHaveURL(/version=010_trellis2/);
  // Tools on the raw model: what is not open here says at which stage it is
  await page.locator(".tools > summary").click();
  await expect(page.locator(".tools li", { hasText: "Texture detail" })).toContainText("at Prepare, once textured");
  await expect(page.locator(".tools li", { hasText: "Parts" })).toContainText("at Prepare");
  await expect(page.locator(".tools li", { hasText: "Sprites for 2D" })).toContainText("at Prepare (one still frame) or after Character (animated)");
  await expect(page.locator(".tools li", { hasText: "Edit in Blender" })).toContainText("available");
  await expect(page.locator(".tools")).not.toContainText("not for this checkpoint");
  await page.screenshot({ path: "test-results/tasks-tools-where.png", clip: { x: 640, y: 0, width: 640, height: 360 } });
  await page.locator(".tools > summary").click();
  await expect(page.getByRole("button", { name: "Finish" })).toBeDisabled(); // needs a prepared model
  await page.getByRole("button", { name: /Prepare$/ }).click(); // the newest prepared checkpoint on the branch
  await expect(page).toHaveURL(/version=050_bake/);
  await expect(page.getByRole("button", { name: "Finish" })).toBeEnabled(); // the Check form of this checkpoint, no run
  await expect(page.getByRole("button", { name: /Character/ })).toBeEnabled();
  // Tools: Face is available on a bake; its section waits collapsed until the entry opens it; the menu closes
  await expect(page.locator("#face").getByRole("button", { name: "Face from image" })).toBeHidden();
  await page.locator(".tools > summary").click();
  await page.screenshot({ path: "test-results/tasks-tools-menu.png", clip: { x: 640, y: 0, width: 640, height: 360 } });
  await expect(page.locator(".tools li", { hasText: "Face from image" })).toContainText("available");
  await expect(page.locator(".tools li", { hasText: "Upscale or redraw" })).toContainText("2D pictures only");
  await expect(page.locator(".tools li", { hasText: "Parts" })).toContainText("available");
  await page.locator(".tools").getByRole("button", { name: "Face from image" }).click();
  await expect(page.locator("#tools")).toHaveAttribute("open", "");
  await expect(page.locator("#face").getByRole("button", { name: "Face from image" })).toBeVisible();
  await expect(page.locator(".tools")).not.toHaveAttribute("open", "");
  // Run this step again on the Simplify checkpoint: just that step with the chosen budget, under its disclosure
  await page.goto("/#/asset/mock_tabs?version=030_remesh");
  await page.getByText("Run this step again").click();
  await page.getByLabel("Triangle budget").fill("5000");
  await page.locator(".panel").getByRole("button", { name: "Simplify", exact: true }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["remesh", { faces: 5000 }]);
});

test("Preparation states: a checked chain never says it stopped; an earlier step names the newest; an imported model starts with its chip (UX-07, UX-06)", async ({ page }) => {
  await mockAsset(page, "mock_prep", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_clean", step: "clean", parent: "010_upload", outputs: { mesh: "mesh.glb" } },
    { version: "030_remesh", step: "remesh", parent: "020_clean", outputs: { mesh: "mesh.glb" } },
    { version: "040_uv", step: "uv", parent: "030_remesh", outputs: { mesh: "mesh.glb" } },
    { version: "050_validate", step: "validate", parent: "040_uv", outputs: { checklist: "checklist.json" } },
    { version: "060_remesh", step: "remesh", parent: "020_clean", outputs: { mesh: "mesh.glb" } }, // a second branch, stopped
    { version: "070_clean", step: "clean", parent: "010_upload", outputs: { mesh: "mesh.glb" } },
    { version: "080_remesh", step: "remesh", parent: "070_clean", outputs: { mesh: "mesh.glb" } }, // a third, not checked yet
  ]);
  const panel = page.locator(".panel");
  const names = async () => (await page.locator(".stages li button").allInnerTexts()).map((t) => t.replace(/\s+/g, " ").replace("✓ ", "").replace(" optional", "").trim());
  await page.goto("/#/asset/mock_prep?version=040_uv");
  await expect(page.locator(".stages .chip")).toHaveText("Imported model");
  await expect.poll(names).toEqual(["Prepare", "Character", "Finish"]);
  await expect(panel).toContainText("Model prepared; checked.");
  await expect(panel).not.toContainText("stopped");
  await expect(panel.getByRole("button", { name: "Continue preparation" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Export for Godot" })).toBeVisible();
  await page.goto("/#/asset/mock_prep?version=020_clean"); // an ancestor of the check: not the mesh that was checked
  await expect(panel).toContainText("An earlier step; the chain was checked later (Checks 050).");
  await expect(panel.getByRole("button", { name: "Export for Godot" })).toHaveCount(0);
  await page.goto("/#/asset/mock_prep?version=040_uv");
  await panel.getByRole("button", { name: "View checked result" }).click();
  await expect(page).toHaveURL(/version=050_validate/);
  await expect(page.locator(".stages [aria-current=step]")).toHaveText("Finish");
  await page.goto("/#/asset/mock_prep?version=060_remesh");
  await expect(panel).toContainText("Prepared up to Simplify; Unwrap, Bake and Check remain."); // M62: an upload with a glb bakes its own textures
  await expect(panel).toContainText("unwrap → bake → check");
  await expect(panel.getByRole("button", { name: "Continue preparation" })).toBeVisible();
  await page.goto("/#/asset/mock_prep?version=070_clean");
  await expect(panel).toContainText("An earlier step; the newest is Simplified 080.");
  await expect(panel.getByRole("button", { name: "Continue preparation" })).toHaveCount(0);
  await panel.getByRole("button", { name: "Open Simplified" }).click();
  await expect(page).toHaveURL(/version=080_remesh/);
});

test("Acceptance walk at 1280×720: a checked prop reaches Export before the creature templates; an imported model Check before Parts; a skinned character the clips without the text and video forms (UX-05)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const top = (sel: string) => page.locator(sel).evaluate((e) => e.getBoundingClientRect().top);
  await mockAsset(page, "mock_walk", [
    { version: "010_trellis2", step: "trellis2", parent: null, outputs: { mesh: "hi.glb", ovoxel: "ovoxel.npz" } },
    { version: "020_bake", step: "bake", parent: "010_trellis2", outputs: { mesh: "mesh.glb", basecolor: "basecolor.png" } },
    { version: "030_validate", step: "validate", parent: "020_bake", outputs: { checklist: "checklist.json" } },
  ]);
  await page.goto("/#/asset/mock_walk?version=030_validate");
  const exportButton = page.getByRole("button", { name: "Export for Godot" });
  await expect(exportButton).toBeVisible();
  const panel = (await page.locator("aside.panel").boundingBox())!;
  const button = (await exportButton.boundingBox())!;
  expect(button.y + button.height).toBeLessThanOrEqual(panel.y + panel.height); // reachable without scrolling
  await expect(page.getByRole("radio", { name: "A person or humanoid character" })).toBeHidden(); // the templates wait under Character
  expect(await top("#checklist")).toBeLessThan(await top("#character"));
  // an imported model: Check model, then Export, the tools below them
  await mockAsset(page, "mock_walk2", [{ version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } }]);
  await page.goto("/#/asset/mock_walk2?version=010_upload");
  await expect(page.getByRole("button", { name: "Check model" })).toBeVisible();
  expect(await top("#finish")).toBeLessThan(await top("#tools"));
  await expect(page.locator("#parts").getByRole("button", { name: "Render the views" })).toBeHidden();
  await expect(page.locator(".tools li", { hasText: "Parts" })).toBeAttached(); // still there, in the menu
  // a skinned character: the ready-made clips by default, the sentence and video forms only on request
  await page.route(/\/api\/clips\?/, (r) => r.fulfill({ json: [{ name: "Idle_A" }, { name: "Walk" }] }));
  await animMock(page, "mock_walk3");
  await page.goto("/#/asset/mock_walk3?version=020_rig_skin");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await expect(page.getByRole("radio", { name: "Ready-made" })).toBeChecked();
  await expect(page.getByLabel("Clips")).toBeVisible();
  await expect(page.getByRole("button", { name: /^Add \d+ animations?$/ })).toBeVisible();
  await expect(page.getByLabel("Motion prompt")).toHaveCount(0);
  await expect(page.getByLabel("Video file")).toHaveCount(0);
  await page.getByRole("radio", { name: "From a video" }).check();
  await expect(page.getByLabel("Video file")).toBeVisible();
  await expect(page.getByLabel("Clips")).toHaveCount(0);
  await page.screenshot({ path: "test-results/walk-skinned-1280.png", animations: "disabled" });
});

test("Readiness: missing concept files make Set up the main action and keep the draft; a restricted version cannot be exported (UX-09)", async ({ page }) => {
  // the model list is read once when the app starts: mocked before the first page, or this machine's own
  // installed files decide (the test passed only where the concept models were missing)
  const row = { id: 1, name: "Tongyi-MAI/Z-Image-Turbo", groups: ["concepts"], revision: "r", where: "hub", installed: false, partial: false, bytes: 2, missing_bytes: 1 };
  await page.route("**/api/models", (r) => r.fulfill({ json: [row] }));
  const restricted = { version: "010_upload", step: "upload", parent: null, params: {}, outputs: { mesh: "mesh.glb" }, created: "", metrics: {}, provenance: "step", restricted: true };
  await page.route("**/api/assets/mock_restricted", (r) => r.fulfill({ json: { asset: { id: "mock_restricted", name: "r", created: "" }, versions: [restricted] } }));
  await page.goto("/#/asset/mock_restricted?version=010_upload");
  await expect(page.getByRole("button", { name: "Export for Godot" })).toBeDisabled();
  await expect(page.locator(".panel")).toContainText("This model is restricted, so it cannot be exported.");
  const posted = await mockAsset(page, "mock_ready", [
    { version: "010_brief", step: "brief", parent: null, params: { prompt: "a lantern", category: "prop" }, outputs: { brief: "brief.yaml" } },
  ]);
  await page.goto("/#/asset/mock_ready?version=010_brief");
  await expect(page.locator(".panel")).toContainText('"a lantern" · prop');
  await expect(page.getByRole("link", { name: "Set up concept images" })).toHaveAttribute("href", "#/setup?cap=concepts"); // its card (UX-12)
  await expect(page.getByRole("button", { name: "Generate 4 concepts" })).toHaveCount(0);
  await page.goto("/#/new");
  await page.getByLabel("Description").fill("a brass lantern");
  await expect(page.getByRole("button", { name: "Generate 4 concepts" })).toHaveCount(0);
  await page.getByRole("link", { name: "Set up concept images" }).click();
  await expect(page).toHaveURL(/#\/setup/);
  await page.getByRole("navigation").getByRole("link", { name: "New asset" }).click();
  await expect(page.getByLabel("Description")).toHaveValue("a brass lantern"); // the draft survives the trip
  expect(posted).toEqual([]);
});

test("Parts: its section, the views, the parts list, and a click plus a name runs a segment", async ({ page }) => {
  const posted = await mockAsset(page, "mock_parts", [
    { version: "010_bake", step: "bake", parent: null, outputs: { mesh: "mesh.glb", basecolor: "basecolor.png" } },
    { version: "020_parts", step: "parts", parent: "010_bake", params: { op: "views" }, outputs: { mesh: "mesh.glb", views: "views/meta.json" }, metrics: { views: "020_parts" } },
    {
      version: "030_parts",
      step: "parts",
      parent: "020_parts",
      params: { op: "segment" },
      outputs: { mesh: "mesh.glb", parts: "parts.json" },
      metrics: { views: "020_parts", view: 3, parts: { Helmet: 1200, Body: 9000 } },
    },
  ]);
  await page.route("**/files/mock_parts/**/*.webp", (r) => r.fulfill({ status: 200, contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==", "base64") }));
  await page.setViewportSize({ width: 1500, height: 900 });
  await page.goto("/#/asset/mock_parts?version=030_parts");
  await expect(page.locator(".stages button[aria-current=step]")).toContainText("Prepare"); // a parts version is a prepared checkpoint
  await expect(page.locator(".view-thumb")).toHaveCount(12);
  await expect(page.locator(".parts-list li")).toHaveText([/1,200 faces/, /Body\s*9,000 faces/]);
  const rename = page.getByRole("button", { name: "Rename parts" });
  await expect(rename).toBeDisabled();
  await page.getByLabel("Name of Helmet").fill(" Iron helmet ");
  await rename.click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted.shift()).toEqual(["parts", { op: "rename", names: { Helmet: "Iron helmet" } }]);
  const find = page.getByRole("button", { name: "Find part" });
  await expect(find).toBeDisabled();
  await page.locator(".big-view img").click({ position: { x: 50, y: 40 } });
  await page.getByLabel("Name of the part").fill("Helmet");
  await expect(find).toBeEnabled();
  await find.click();
  await expect.poll(() => posted.length).toBe(1);
  const [step, params] = posted[0];
  expect(step).toBe("parts");
  expect(params).toMatchObject({ op: "segment", view: 3, name: "Helmet" });
  expect(params.points).toHaveLength(1);
  expect(params.points[0][2]).toBe(1);
});

test("Library grid: search, sort, filter, archive", async ({ page }) => {
  const list: any[] = [
    { id: "w_zeta", name: "zeta", created: "2026-10-01T10:00:00Z", versions: 3, updated: "2026-10-02T10:00:00Z" },
    { id: "w_alpha", name: "alpha", created: "2026-10-01T10:00:00Z", versions: 1, updated: "2026-10-05T10:00:00Z" },
    { id: "bench_one", name: "bench one", created: "2026-10-01T10:00:00Z", versions: 9, updated: "2026-10-03T10:00:00Z" },
    { id: "w_old", name: "old one", created: "2026-09-01T10:00:00Z", versions: 2, updated: "2026-09-02T10:00:00Z", archived: true },
  ];
  const patches: any[] = [];
  await page.route("**/api/assets", (r) => (r.request().method() === "GET" ? r.fulfill({ json: list }) : r.continue()));
  await page.route("**/api/assets/w_alpha", (r) => {
    patches.push([r.request().method(), r.request().postDataJSON()]);
    list[1].archived = true;
    return r.fulfill({ json: list[1] });
  });
  await page.goto("/#/library");
  const names = () => page.locator(".cards > li strong").allInnerTexts();
  await expect.poll(names).toEqual(["alpha", "bench one", "zeta"]); // newest first; the archived one is hidden
  await expect(page.getByText("3 of 4 assets")).toBeVisible();
  await page.screenshot({ path: "test-results/library-grid.png", animations: "disabled" });
  await page.getByLabel("Sort").selectOption("name");
  await expect.poll(names).toEqual(["alpha", "bench one", "zeta"]);
  await page.getByLabel("Sort").selectOption("old");
  await expect.poll(names).toEqual(["zeta", "bench one", "alpha"]);
  await page.getByLabel("Show").selectOption("Mine");
  await expect.poll(names).toEqual(["zeta", "alpha"]);
  await page.getByLabel("Show").selectOption("All");
  await page.getByLabel("Search assets").fill("ALP");
  await expect.poll(names).toEqual(["alpha"]);
  await page.locator(".cards li").first().getByText("More").click(); // card management sits in a disclosure (UX-14)
  await page.getByRole("button", { name: "Archive", exact: true }).click();
  await expect.poll(() => patches).toEqual([["PATCH", { archived: true }]]);
  await page.getByLabel("Search assets").fill("");
  await page.getByLabel("Show").selectOption("Archived");
  await expect.poll(names).toEqual(["old one", "alpha"]); // still sorted oldest first
  await page.locator(".cards li").first().getByText("More").click();
  await expect(page.getByRole("button", { name: "Unarchive" }).first()).toBeVisible();
});

test("History: intermediate steps fold into one row; star and filter", async ({ page }) => {
  const patches: [string, any][] = [];
  await mockAsset(page, "mock_hist", [
    { version: "001_trellis2", step: "trellis2", parent: null, outputs: { mesh: "hi.glb", ovoxel: "ovoxel.npz" } },
    { version: "002_clean", step: "clean", parent: "001_trellis2", outputs: { mesh: "mesh.glb" } },
    { version: "003_remesh", step: "remesh", parent: "002_clean", outputs: { mesh: "mesh.glb" } },
    { version: "004_uv", step: "uv", parent: "003_remesh", outputs: { mesh: "mesh.glb" } },
    { version: "005_bake", step: "bake", parent: "004_uv", outputs: { mesh: "mesh.glb", basecolor: "basecolor.png" } },
    {
      version: "006_rig_fit",
      step: "rig_fit",
      parent: "005_bake",
      outputs: { mesh: "mesh.glb", skeleton: "skeleton.json", markers: "markers.json", report: "report.json" },
    },
  ]);
  await page.route("**/api/assets/mock_hist/versions/*", (r) => {
    patches.push([r.request().url().split("/").pop()!, r.request().postDataJSON()]);
    return r.fulfill({ json: {} });
  });
  await page.goto("/#/asset/mock_hist?version=006_rig_fit");
  await history(page);
  const rows = page.locator("#versions li");
  await expect(rows).toHaveCount(4); // model, "3 steps", bake, rig
  await page.screenshot({ path: "test-results/history-folded.png", animations: "disabled" });
  await page.getByRole("button", { name: /3 steps/ }).click();
  await expect(rows).toHaveCount(7);
  await page.getByRole("button", { name: /3 steps/ }).click();
  await expect(rows).toHaveCount(4);
  await rows
    .first()
    .getByRole("button", { name: /^Star / })
    .click();
  await expect.poll(() => patches).toEqual([["001_trellis2", { starred: true }]]);
  await page.getByLabel("Starred only").check();
  await expect(rows).toHaveCount(1);
});

test("Setup: a summary, cards with status and install buttons, a gated card, the credits and the advanced tables", async ({ page }) => {
  const m = (id: number, name: string, groups: string[], installed: boolean, bytes: number, partial = false) => ({
    id,
    name,
    groups,
    revision: "r",
    where: "hub",
    installed,
    partial,
    bytes: installed ? bytes : partial ? bytes / 2 : 0,
    missing_bytes: installed ? 0 : partial ? bytes / 2 : bytes,
  });
  await page.route("**/api/models", (r) =>
    r.fulfill({
      json: [
        m(1, "black-forest-labs/concepts", ["concepts"], true, 27e9),
        m(2, "facebook/dinov3-vitl16-pretrain-lvd1689m", ["faithful", "fast"], false, 1.2e9),
        m(3, "TencentARC/fast", ["fast"], false, 9e9),
        m(4, "ai/texture", ["texture"], false, 3e9),
        m(5, "nvidia/kimodo", ["kimodo"], true, 2e9),
        m(6, "nvidia/GEM-X", ["mocap"], false, 9e9, true),
      ],
    }),
  );
  // M35b: the environment each feature runs in; text to motion's is missing
  const env = (name: string, state: string, groups: string[]) => ({ name, state, groups });
  await page.route("**/api/environments", (r) =>
    r.fulfill({
      json: [
        env(".venv-ml", "ok", ["concepts", "faithful", "fast", "texture"]),
        env(".venv-kimodo", "missing", ["kimodo"]),
        env(".venv-parts", "ok", ["parts"]),
        env(".venv-gemx", "ok", ["mocap"]),
      ],
    }),
  );
  const installs: { group: string; repair: boolean }[] = [];
  await page.route("**/api/models/install", (r) => {
    installs.push(r.request().postDataJSON());
    return r.fulfill({ status: 202, json: { job_id: `inst${installs.length}` } });
  });
  const item = (id: string, name: string, bytes: number, action: string | null) => ({ id, kind: id.split(":")[0].replace("tool", "tools"), name, bytes, drive: "D:\\", action });
  const space = {
    scanned: Date.now() / 1000,
    items: [
      item("models:concepts", "Model files: concepts", 27e9, "group:concepts"),
      item("venv:.venv-kimodo", "Python for text to motion (.venv-kimodo)", 8e9, "venv:.venv-kimodo"),
      item("library:trash", "Deleted assets and versions (the trash)", 4e8, "trash"),
      item("tool:blender", "Blender", 9e8, null),
      item("library:archived", "Archived assets (1)", 2e8, null),
    ],
    assets: [{ id: "old_knight", name: "Old knight", archived: true, bytes: 2e8 }],
    drives: [{ drive: "D:\\", total: 1e12, free: 1.5e10, low: true }],
  };
  await page.route("**/api/storage", (r) => r.fulfill({ json: space }));
  await page.route("**/api/storage/drives", (r) => r.fulfill({ json: space.drives }));
  let cleaned = "";
  await page.route("**/api/storage/cleanup", (r) => {
    cleaned = r.request().postDataJSON().action;
    return r.fulfill({ json: { ...space, items: space.items.filter((i) => i.action !== "trash"), freed: 4e8 } });
  });
  await page.goto("/#/setup");
  // what can be made now, by the guided setup's sets; everything else optional (UX-12)
  await expect(page.locator(".summary")).toContainText("Nothing is ready yet: the guided setup installs a whole set at once.");
  await expect(page.locator(".summary")).toContainText("Not installed, each optional until you want it: 3D model, Faithful (recommended), 3D model, Fast");
  await expect(page.getByRole("link", { name: "Open the guided setup" })).toBeVisible(); // a button, not a line of text
  // M35a: where the space goes, a cleanup, the low-space warning in the top bar
  await expect(page.getByRole("link", { name: "Low disk space: D:\\ 15.0 GB free" })).toBeVisible();
  await expect(page.locator("#space")).toContainText("Space is low on D:\\");
  await expect(page.locator("#space tr.item")).toHaveCount(6); // five items and the archived asset under its row
  await expect(page.locator("#space tr.group")).toHaveCount(4); // model files, environments, library, tools
  await expect(page.locator("#space tr.sub", { hasText: "Old knight" }).getByRole("button", { name: "Delete for good" })).toBeVisible();
  page.once("dialog", (d) => d.accept());
  await page.locator("#space tr", { hasText: "the trash" }).getByRole("button", { name: "Empty the trash" }).click();
  await expect(page.locator("#space")).toContainText("Freed 400 MB.");
  expect(cleaned).toBe("trash");
  await expect(page.locator("#space tr", { hasText: "Blender" }).getByRole("button")).toHaveCount(0); // needed: no cleanup
  await expect(page.locator("#cap-concepts")).toContainText("Installed · 27 GB");
  await expect(page.locator("#cap-texture")).toContainText("Not installed · 3.0 GB to download");
  await expect(page.locator("#cap-texture").getByRole("button", { name: "Install", exact: true })).toBeVisible();
  // M35b: files there but not all, or the environment missing: Repair; installed: Check and repair
  await expect(page.locator("#cap-fast")).toContainText("Needs a Hugging Face login first"); // the login comes first
  await expect(page.locator("#cap-mocap")).toContainText("Needs repair · 4.5 GB missing or damaged");
  await expect(page.locator("#cap-kimodo")).toContainText("Needs repair · its Python environment is not set up");
  await expect(page.locator("#cap-concepts").getByRole("button", { name: "Check and repair" })).toBeVisible();
  await page.locator("#cap-mocap").getByRole("button", { name: "Repair" }).click();
  await page.locator("#cap-kimodo").getByRole("button", { name: "Repair" }).click();
  await expect.poll(() => installs).toEqual([
    { group: "mocap", repair: true },
    { group: "kimodo", repair: false }, // only the environment: a plain install sets it up
  ]);
  await expect(page.locator("#cap-kimodo")).toContainText("Queued");
  await expect(page.locator("#cap-faithful")).toContainText("Needs a Hugging Face login first");
  await page.locator("#cap-faithful summary", { hasText: "Before you install" }).click();
  await expect(page.locator("#cap-faithful").getByText("hf auth login").first()).toBeVisible();
  await expect(page.locator("#attributions .attribution", { hasText: "Built with DINOv3" })).toBeVisible();
  await page.getByText("Model files and licences").click();
  await expect(page.locator("#model-table tbody tr")).toHaveCount(6);
  await page.screenshot({ path: "test-results/setup-mock.png", fullPage: true });
});

test("Get started: a fresh install points to the guided setup; checks, folder, presets, the token login, one install queue", async ({ page }) => {
  const m = (id: number, name: string, groups: string[]) => ({ id, name, groups, revision: "r", where: "x", installed: false, partial: false, bytes: 0, missing_bytes: 1e9 });
  await page.route("**/api/models", (r) => r.fulfill({ json: [m(1, "a/concepts", ["concepts"]), m(2, "facebook/dinov3-vitl16-pretrain-lvd1689m", ["faithful", "fast"]), m(3, "a/kimodo", ["kimodo"])] }));
  await page.route("**/api/environments", (r) => r.fulfill({ json: [{ name: ".venv-ml", state: "ok", groups: ["concepts", "faithful"] }] }));
  await page.route("**/api/setup/machine", (r) =>
    r.fulfill({
      json: [
        { id: "gpu", level: "ok", label: "NVIDIA GeForce RTX 5080 Laptop GPU, 16 GB of video memory", detail: "" },
        { id: "long_paths", level: "warn", label: "Long file paths are not allowed", detail: "Some downloads may fail." },
      ],
    }),
  );
  const folder = {
    path: String.raw`C:\Effigy\models`,
    source: "default",
    bytes: 0,
    choices: [
      { path: String.raw`C:\Effigy\models`, drive: "C:\\", free: 78e9, total: 1e12 },
      { path: String.raw`D:\Effigy-models`, drive: "D:\\", free: 412e9, total: 1e12 },
    ],
  };
  await page.route("**/api/setup/folder", (r) => (r.request().method() === "POST" ? r.fulfill({ json: { path: r.request().postDataJSON().path, job_id: null } }) : r.fulfill({ json: folder })));
  const p = (id: string, name: string, gb: number, groups: string[]) => ({ id, name, what: `${name}.`, groups, bytes: gb * 1e9, missing: gb * 1e9 });
  await page.route("**/api/setup/presets", (r) =>
    r.fulfill({
      json: [
        p("props", "Props and objects", 59.9, ["concepts", "faithful"]),
        p("characters", "Game characters", 77.7, ["concepts", "faithful", "skin"]),
        p("motion", "Characters with motion", 102.4, ["concepts", "faithful", "kimodo"]),
      ],
    }),
  );
  const gated = { "facebook/dinov3-vitl16-pretrain-lvd1689m": ["fast", "faithful"], "meta-llama/Meta-Llama-3-8B-Instruct": ["kimodo"] };
  let user: string | null = null;
  await page.route("**/api/hf", (r) => r.fulfill({ json: { token: !!user, user, access: user ? { "facebook/dinov3-vitl16-pretrain-lvd1689m": true, "meta-llama/Meta-Llama-3-8B-Instruct": false } : {}, gated } }));
  const sent: string[] = [];
  await page.route("**/api/hf/login", (r) => {
    sent.push(r.request().postDataJSON().token);
    user = "tester";
    return r.fulfill({ json: { user } });
  });
  const installs: string[] = [];
  await page.route("**/api/models/install", (r) => {
    installs.push(r.request().postDataJSON().group);
    return r.fulfill({ status: 202, json: { job_id: `w${installs.length}` } });
  });

  // the very first start opens the guided setup by itself; a link to a page does not
  await page.route("**/api/setup/first-run", (r) => r.fulfill({ json: { first_run: true } }));
  let welcomed = 0;
  await page.route("**/api/setup/welcomed", (r) => (welcomed++, r.fulfill({ json: { ok: true } })));
  await page.goto("/");
  await expect(page).toHaveURL(/#\/welcome/);
  await page.goto("/#/library");
  await expect(page.locator(".get-started")).toBeVisible(); // nothing installed: the library points there
  await expect(page).toHaveURL(/#\/library/);
  await page.locator(".get-started").getByRole("link", { name: "Get started" }).click();
  await expect(page).toHaveURL(/#\/welcome/);
  await expect(page.locator(".checks").first()).toContainText("16 GB of video memory");
  await expect(page.locator(".checks").first()).toContainText("Long file paths are not allowed");
  // the folder: the other drive, which has room
  await page.getByRole("radio", { name: /Effigy-models/ }).check();
  await page.getByRole("button", { name: "Use this folder" }).click();
  await expect(page.getByText(String.raw`The models go into D:\Effigy-models now.`)).toBeVisible();
  // the default preset fits 78 GB? no: a warning; the motion preset adds the Llama licence
  await expect(page.getByText("77.7 GB do not fit on this drive")).toBeVisible();
  await expect(page.getByText("Meta Llama 3 Community License")).toHaveCount(0);
  await page.getByRole("radio", { name: /^Characters with motion/ }).check({ force: true });
  await expect(page.getByText("Meta Llama 3 Community License")).toBeVisible();
  await expect(page.locator(".presets")).toContainText("about 25 min at 500 Mbit/s, 4.6 h at 50 Mbit/s");
  // the token: sent once, then gone from the page
  const tokenBox = page.getByLabel("Hugging Face token");
  await tokenBox.fill("hf_abcdefghijklmnopqrstuvwxyz12345678");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByText("Logged in as tester")).toBeVisible();
  expect(sent).toEqual(["hf_abcdefghijklmnopqrstuvwxyz12345678"]);
  await expect(page.getByLabel("Hugging Face token")).toHaveCount(0); // logged in: no field any more
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }))).not.toContain("hf_abc");
  await expect(page.getByText("not yet (approval can take a few hours)")).toBeVisible();
  // one queue: every group of the preset not installed yet, in order
  await page.getByRole("button", { name: /^Install Characters with motion/ }).click();
  await expect.poll(() => installs).toEqual(["concepts", "faithful", "kimodo"]);
  expect(welcomed).toBe(1); // decided: the app stops opening the page by itself
  await expect(page.getByText("A licence in step 4 is not accepted yet")).toBeVisible();
});

// M42: the library on mocked routes (the backend's listing fields, turntables and copy route, IMPLEMENTATION §3.5)
async function mockLibrary(page: Page) {
  const seen = { turntable: 0, patched: [] as string[][], copy: null as any, steps: [] as string[] };
  const at = "2026-10-09T10:00:00Z";
  let assets: any[] = [
    { id: "m42_a", name: "alpha", created: at, updated: at, versions: 2, tags: ["prop"], licenses: [], restricted: false, imported: true },
    { id: "m42_b", name: "beta", created: at, updated: at, versions: 1, tags: [], licenses: [], restricted: true, imported: false },
    { id: "m42_c", name: "gamma", created: at, updated: at, versions: 1, tags: [], licenses: ["Credited"], restricted: false, imported: false },
  ];
  await page.route("**/api/assets", (r) => (r.request().method() === "GET" ? r.fulfill({ json: assets }) : r.continue()));
  await page.route("**/api/licenses", (r) =>
    r.fulfill({ json: [{ name: "Credited", kind: "model", license: "CC-BY-4.0", commercial: true, attribution: "Made by Ann" }] }),
  );
  await page.route("**/api/assets/m42_*/thumb", (r) => r.fulfill({ body: PNG, contentType: "image/png" }));
  await page.route("**/api/assets/m42_*/turntable*", (r) => (seen.turntable++, r.fulfill({ body: PNG, contentType: "image/webp" })));
  const meta = (version: string, step: string, parent: string | null, params: any, outputs: any, restricted = false) => ({
    version, step, parent, params, outputs, created: at, metrics: {}, provenance: "step", restricted,
  });
  let n = 0;
  const jobs: Record<string, string> = {};
  for (const a of assets)
    await page.route(`**/api/assets/${a.id}`, (r) => {
      if (r.request().method() === "PATCH") {
        const tags = r.request().postDataJSON().tags;
        seen.patched.push(tags);
        assets = assets.map((x) => (x.id === a.id ? { ...x, tags } : x));
        return r.fulfill({ json: assets.find((x) => x.id === a.id) });
      }
      return r.fulfill({ json: { asset: a, versions: [meta("010_upload", "upload", null, {}, { mesh: "mesh.glb" }, !!a.restricted)] } });
    });
  await page.route("**/api/assets/m42_*/steps/export", (r) => {
    seen.steps.push(r.request().url().split("/")[5]);
    const job = `job-${++n}`;
    jobs[job] = `020_export${n}`;
    return r.fulfill({ status: 202, json: { job_id: job } });
  });
  await page.route("**/api/jobs/job-*", (r) => r.fulfill({ json: { status: "done", version: jobs[r.request().url().split("/").pop()!] } }));
  await page.route("**/api/export/copy", (r) => {
    seen.copy = r.request().postDataJSON();
    if (!seen.copy.items.length) return r.fulfill({ json: { copied: [], skipped: [] } }); // the folder check
    return r.fulfill({
      json: {
        copied: [{ asset_id: "m42_a", version: seen.copy.items[0].version, dir: "C:/out/m42_a_godot", files: ["a.glb", "CREDITS.txt"] }],
        skipped: [{ asset_id: "m42_c", version: seen.copy.items[1].version, reason: "exists" }],
      },
    });
  });
  return seen;
}

test("Library: chips, tags and their filter, the hover turntable", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const seen = await mockLibrary(page);
  await page.goto("/#/library");
  const card = (name: string) => page.locator(".cards li").filter({ hasText: name });
  await expect(card("beta").locator(".chip")).toHaveText(["Restricted"]);
  await expect(card("alpha").locator(".chip")).toHaveText(["Your file"]);
  await expect(card("gamma").locator(".chip")).toHaveText(["Credit"]);
  await expect(card("gamma").locator(".chip")).toHaveAttribute("title", "Made by Ann");
  // the chip row keeps the cards even
  const heights = await page.locator(".card").evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().height)));
  expect(new Set(heights).size).toBe(1);
  await page.screenshot({ path: "test-results/m42-grid.png" });

  // tags: a prompt like Rename; the Tag filter and the search find them
  page.once("dialog", (d) => d.accept("hero, prop"));
  await card("gamma").getByText("More").click();
  await card("gamma").getByRole("button", { name: "Tags" }).click();
  await expect.poll(() => seen.patched).toEqual([["hero", "prop"]]);
  await expect(card("gamma")).toContainText("#hero #prop");
  await page.getByLabel("Tag").selectOption("hero");
  await expect(page.locator(".cards li")).toHaveCount(1);
  await page.getByLabel("Tag").selectOption("");
  await page.getByLabel("Search assets").fill("prop");
  await expect(page.locator(".cards li")).toHaveCount(2);
  await page.getByLabel("Search assets").fill("");

  // hover: the still stays until the WebP has loaded, then the turntable replaces it; leaving swaps back
  const img = card("alpha").locator("img.thumb");
  await expect(img).toHaveAttribute("src", /\/thumb$/);
  await card("alpha").locator("a.card").hover();
  await expect(img).toHaveAttribute("src", /\/api\/assets\/m42_a\/turntable\?v=/);
  await page.screenshot({ path: "test-results/m42-hover.png" });
  await page.mouse.move(0, 0);
  await expect(img).toHaveAttribute("src", /\/thumb$/);
  expect(seen.turntable).toBeGreaterThan(0);
  expect(errors).toEqual([]);
});

test("Library: reduced motion never loads a turntable", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const seen = await mockLibrary(page);
  await page.goto("/#/library");
  await page.locator(".cards li").filter({ hasText: "alpha" }).locator("a.card").hover();
  await page.waitForTimeout(900);
  expect(seen.turntable).toBe(0);
});

test("Library: batch export runs each export, skips the restricted, copies into the folder", async ({ page }) => {
  const seen = await mockLibrary(page);
  await page.goto("/#/library");
  await page.getByRole("button", { name: "Select assets" }).click();
  for (const n of ["alpha", "beta", "gamma"]) await page.getByLabel(`Select ${n}`).check();
  await page.getByRole("button", { name: "Export selected (3)" }).click();
  await page.getByLabel("Folder").fill("C:/out");
  await page.screenshot({ path: "test-results/m42-batch.png" });
  await page.getByRole("button", { name: "Export and copy" }).click();
  const result = page.getByRole("list", { name: "Result" });
  await expect(result).toContainText("alpha: copied 2 files to C:/out/m42_a_godot");
  await expect(result).toContainText("beta: skipped, restricted");
  await expect(result).toContainText("gamma: skipped, a folder for it is already there");
  expect(seen.steps).toEqual(["m42_a", "m42_c"]); // the restricted one never ran
  expect(seen.copy.folder).toBe("C:/out");
  expect(seen.copy.items.map((i: any) => i.asset_id)).toEqual(["m42_a", "m42_c"]);
  await page.screenshot({ path: "test-results/m42-batch-done.png" });
});

test("Compare: two linked viewers and the mirrored table; Licences panel", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await mockAsset(page, "m42_cmp", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_validate", step: "validate", parent: "010_upload", outputs: { checklist: "checklist.json" }, metrics: { fail: 2 } },
    { version: "030_clean", step: "clean", parent: "010_upload", outputs: { mesh: "mesh.glb", report: "report.json" } },
  ]);
  await page.route("**/api/licenses", (r) => r.fulfill({ json: [{ name: "Credited", kind: "model", license: "CC-BY-4.0", commercial: true, attribution: "Made by Ann" }] }));
  await page.route("**/files/m42_cmp/020_validate/checklist.json", (r) =>
    r.fulfill({ json: { items: { manifold: { status: "fail", value: 3 }, watertight: { status: "fail", value: 1 }, triangle_budget: { status: "warn", value: 120000 }, has_uvs: { status: "pass", value: true } } } }),
  );
  await page.goto("/#/asset/m42_cmp?version=030_clean");
  await page.locator("canvas").waitFor();
  await history(page);
  await expect(page.getByRole("button", { name: /^Compare .* 030 with the shown version$/ })).toHaveCount(0); // not on the shown one
  await page.getByRole("button", { name: /^Compare Imported model 010 with the shown version$/ }).click();
  const cmp = page.getByRole("region", { name: "Compare versions" });
  await expect(cmp.locator("canvas")).toHaveCount(2);
  await page.waitForFunction(() => (window as any).__compare?.every((v: any) => v.loaded));
  await expect(cmp.locator("tr", { hasText: "Triangles" })).toContainText("2");
  await expect(cmp.locator("tr.differs")).toHaveCount(1);
  await expect(cmp.locator("tr.differs")).toContainText("Checks");
  // the side's own checklist, counted as the Checklist panel does (UI-005): a warning is not "passed"
  await expect(cmp.locator("tr.differs")).toContainText("1 passed · 1 warning · 2 failed");
  await expect(cmp.locator("tr.differs")).toContainText("not checked");
  // the cameras are linked
  await page.evaluate(() => {
    const [a, b] = (window as any).__compare;
    a.camera.position.x = 7;
    a.controls.dispatchEvent({ type: "change" });
    return b;
  });
  expect(await page.evaluate(() => (window as any).__compare[1].camera.position.x)).toBe(7);
  await page.screenshot({ path: "test-results/m42-compare.png" });
  await page.keyboard.press("Escape");
  await expect(cmp).toHaveCount(0);

  await page.getByRole("button", { name: "Licences" }).click();
  await expect(page.getByRole("region", { name: "Licences" })).toContainText("Your file");
  expect(errors).toEqual([]);
});

test("Animation editor: deleting the only copy can be saved as a return to the base (UI-002)", async ({ page }) => {
  const posted = await mockAsset(page, "mock_anim5", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_rig_skin", step: "rig_skin", parent: "010_upload", outputs: { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" } },
    { version: "030_anim", step: "anim", parent: "020_rig_skin", outputs: { rigged_anim: "rigged_anim.glb", clips: "clips.json", skeleton: "skeleton.json" } },
  ]);
  await page.route("**/files/mock_anim5/*/rigged_anim.glb", (r) => r.fulfill({ body: ANIM, contentType: "model/gltf-binary" }));
  await page.route("**/files/mock_anim5/*/clips.json", (r) =>
    r.fulfill({ json: { clips: [{ name: "Idle", frames: 12, seconds: 0.5 }, { name: "Walk", frames: 24, seconds: 1 }], fps: 24, missing: [] } }),
  );
  await page.route("**/files/mock_anim5/*/skeleton.json", (r) => r.fulfill({ json: { template: "mixamo52", joints: [] } }));
  page.on("dialog", (d) => d.accept(d.defaultValue()));
  await page.goto("/#/asset/mock_anim5?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.getByRole("combobox", { name: "Animation" }).selectOption({ label: "Play: Walk" });
  await page.getByRole("button", { name: "Edit animation" }).click();
  await page.locator(".anim-editor").getByRole("button", { name: "Duplicate", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Clip to edit" })).toHaveValue("Walk copy");
  await page.locator(".anim-editor").getByRole("button", { name: "Delete", exact: true }).click();
  // nothing is edited any more, but something changed since the load: the save is a return to the base, sent as
  // one base clip's empty layer (the step wants a layer and starts from the base)
  await expect(page.getByRole("button", { name: "Save as a new version" })).toBeEnabled();
  await page.getByRole("button", { name: "Save as a new version" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["anim_edit", { clips: { Idle: {} } }]);
});

test("Reconnect: a version deleted while the stream was down falls back; a job the restarted server lost is over (UI-003, UI-004)", async ({ page }) => {
  // the event stream stands in for itself: a fake EventSource the test drops and brings back (an open
  // stream cannot be cut from the client side)
  await page.addInitScript(() => {
    class FakeES {
      static OPEN = 1;
      readyState = 1;
      private ls: Record<string, ((e: any) => void)[]> = {};
      constructor() {
        (window as any).__es = this;
        setTimeout(() => this.emit("open"), 0);
      }
      addEventListener(n: string, f: (e: any) => void) {
        (this.ls[n] ??= []).push(f);
      }
      emit(n: string, data?: any) {
        (this.ls[n] ?? []).forEach((f) => f({ data }));
      }
      close() {}
    }
    (window as any).EventSource = FakeES;
  });
  const posted = await mockAsset(page, "mock_rc", [
    { version: "010_trellis2", step: "trellis2", parent: null, outputs: { mesh: "hi.glb", ovoxel: "ovoxel.npz" } },
    { version: "020_clean", step: "clean", parent: "010_trellis2", outputs: { mesh: "mesh.glb" } },
    { version: "030_remesh", step: "remesh", parent: "020_clean", outputs: { mesh: "mesh.glb" } },
  ]);
  await page.route("**/api/jobs/job-*", (r) => r.fulfill({ json: { status: "running" } })); // registered last: wins
  await page.goto("/#/asset/mock_rc?version=030_remesh");
  await page.getByText("Run this step again").click();
  await page.getByLabel("Triangle budget").fill("5000");
  await page.locator(".panel").getByRole("button", { name: "Simplify", exact: true }).click();
  await expect.poll(() => posted.length).toBe(1);
  await expect(page.getByRole("status").getByRole("button", { name: "Cancel" })).toBeVisible();
  // the server restarts: the job is unknown, 030_remesh was deleted by another client meanwhile
  await page.route("**/api/jobs/job-*", (r) => r.fulfill({ status: 404, json: { error: { code: "not_found", message: "no job" } } }));
  const keep = (version: string, step: string, parent: string | null) => ({ version, step, parent, params: {}, outputs: { mesh: "mesh.glb" }, created: "", metrics: {}, provenance: "step", restricted: false });
  await page.route("**/api/assets/mock_rc", (r) =>
    r.fulfill({ json: { asset: { id: "mock_rc", name: "lantern", created: "" }, versions: [keep("010_trellis2", "trellis2", null), keep("020_clean", "clean", "010_trellis2")] } }),
  );
  await page.evaluate(() => (window as any).__es.emit("error")); // the stream drops ...
  await page.evaluate(() => (window as any).__es.emit("open")); // ... and comes back: the app reconciles
  await expect(page).toHaveURL(/version=020_clean/, { timeout: 20000 }); // the deleted version's parent
  await history(page);
  await expect(page.locator("#versions")).toContainText("020"); // the two survivors, and no 030
  await expect(page.locator("#versions")).not.toContainText("030");
  await expect(page.getByRole("status").getByRole("button", { name: "Cancel" })).toHaveCount(0);
  await expect(page.getByText("the app was restarted while it ran").first()).toBeVisible(); // the banner (the Jobs list has it too)
});

test("Clip picker: a keyboard selection is what gets added (UI-006)", async ({ page }) => {
  const posted = await mockAsset(page, "mock_clips", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_rig_skin", step: "rig_skin", parent: "010_upload", outputs: { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" } },
  ]);
  await page.route(/\/api\/clips\?/, (r) => r.fulfill({ json: [{ name: "Idle_A" }, { name: "Jog" }, { name: "Walk" }, { name: "Wave" }] }));
  await page.route("**/files/mock_clips/*/skeleton.json", (r) => r.fulfill({ json: { template: "mixamo52", joints: [] } }));
  await page.route("**/files/mock_clips/*/qa.json", (r) => r.fulfill({ json: { picked: "blender", engines: {}, items: {} } }));
  await page.goto("/#/asset/mock_clips?version=020_rig_skin");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true); // the rig panel needs the model
  const clips = page.getByLabel("Clips");
  await expect(clips).toBeVisible();
  await clips.selectOption(["Idle_A", "Wave"]); // the native change event, as the keyboard fires it
  await page.getByRole("button", { name: "Add 2 animations" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["anim", { clips: ["Idle_A", "Wave"] }]);
  // M67: the arms are kept out of the body unless the box is cleared
  await page.getByLabel("Keep the arms out of the body").uncheck();
  await page.getByRole("button", { name: "Add 2 animations" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]).toEqual(["anim", { clips: ["Idle_A", "Wave"], clearance: false }]);
});

test("Viewer: a clear stops playback and drops the rig; a disposal disposes the skeleton gizmo (UI-007, UI-008)", async ({ page }) => {
  await mockAsset(page, "mock_vw", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_rig_skin", step: "rig_skin", parent: "010_upload", outputs: { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" } },
    { version: "030_anim", step: "anim", parent: "020_rig_skin", outputs: { rigged_anim: "rigged_anim.glb", clips: "clips.json", skeleton: "skeleton.json" } },
    { version: "040_validate", step: "validate", parent: "030_anim", outputs: { checklist: "checklist.json" } },
  ]);
  await page.route("**/files/mock_vw/*/rigged_anim.glb", (r) => r.fulfill({ body: ANIM, contentType: "model/gltf-binary" }));
  await page.route("**/files/mock_vw/*/clips.json", (r) => r.fulfill({ json: { clips: [{ name: "Walk", frames: 24, seconds: 1 }], fps: 24, missing: [] } }));
  await page.route("**/files/mock_vw/*/skeleton.json", (r) => r.fulfill({ json: { template: "mixamo52", joints: [{ name: "Hips", parent: null, position: [0, 1, 0] }] } }));
  await page.goto("/#/asset/mock_vw?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.getByRole("combobox", { name: "Animation" }).selectOption({ label: "Play: Walk" });
  await expect.poll(() => page.evaluate(() => (window as any).__viewer.playing)).toBe(true);
  await page.evaluate(() => {
    const v = (window as any).__viewer;
    v.setRig([{ name: "Hips", parent: null, position: [0, 1, 0] }], {});
    const tc = v.gizmo(); // the skeleton gizmo, made on first use
    (window as any).__tcDisposed = 0;
    const real = tc.dispose.bind(tc);
    tc.dispose = () => ((window as any).__tcDisposed++, real());
  });
  // the pane's clear (a version with no model of its own and none up its chain)
  await page.evaluate(() => (window as any).__viewer.clear());
  await expect.poll(() => page.evaluate(() => (window as any).__viewer.playing)).toBe(false);
  expect(await page.evaluate(() => (window as any).__viewer.rig)).toBeNull();
  // leaving the asset: the viewer is disposed, its gizmo with it
  await page.goto("/#/");
  await expect.poll(() => page.evaluate(() => (window as any).__tcDisposed)).toBe(1);
});

test("Prepare: level and square, close slits and square the taper reach their steps", async ({ page }) => {
  const posted = await mockAsset(page, "mock_fence", [
    { version: "010_trellis2", step: "trellis2", parent: null, outputs: { mesh: "hi.glb", ovoxel: "ovoxel.npz" } },
    { version: "020_clean", step: "clean", parent: "010_trellis2", outputs: { mesh: "mesh.glb" } },
    { version: "030_remesh", step: "remesh", parent: "020_clean", outputs: { mesh: "mesh.glb" } },
    { version: "040_uv", step: "uv", parent: "030_remesh", outputs: { mesh: "mesh.glb" } },
    { version: "050_bake", step: "bake", parent: "040_uv", outputs: { mesh: "mesh.glb", basecolor: "basecolor.png" } },
  ]);
  const panel = page.locator(".panel");
  // Simplify: millimetres in, metres to the step; a bad value blocks the run
  await page.goto("/#/asset/mock_fence?version=030_remesh");
  await page.getByText("Run this step again").click();
  await page.getByLabel("Triangle budget").fill("6000");
  await page.getByLabel("Close slits up to (mm)").fill("-1");
  await expect(panel.getByText("Close slits: a positive number of millimetres")).toBeVisible();
  await expect(panel.getByRole("button", { name: "Simplify", exact: true })).toBeDisabled();
  await page.getByLabel("Close slits up to (mm)").fill("12");
  await panel.getByRole("button", { name: "Simplify", exact: true }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["remesh", { faces: 6000, close: 0.012 }]);
  // Bake: the taper checkbox
  await page.goto("/#/asset/mock_fence?version=050_bake");
  await page.getByText("Run this step again").click();
  await page.getByLabel("Square a flat prop's perspective taper").check();
  await panel.getByRole("button", { name: "Bake", exact: true }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]).toEqual(["bake", { texture_size: 2048, detaper: true }]);
  // Clean: level and square; left unticked, nothing extra is sent
  await page.goto("/#/asset/mock_fence?version=020_clean");
  await page.getByText("Run this step again").click();
  await page.getByLabel("Also turn it square to the axes").check();
  await panel.locator("details", { hasText: "Run this step again" }).screenshot({ path: "test-results/prepare-fence-options.png" });
  await panel.getByRole("button", { name: "Clean", exact: true }).click();
  await expect.poll(() => posted.length).toBe(3);
  expect(posted[2]).toEqual(["clean", { level: true, straighten: true }]); // a generated model stands upright by default
});

test("Setup: the app's port is chosen there and applies on the next start", async ({ page }) => {
  let info = { running: 8000, next: 8000, source: "default", default: 8000 };
  const saved: number[] = [];
  await page.route("**/api/setup/port", async (r) => {
    if (r.request().method() === "POST") {
      const port = r.request().postDataJSON().port;
      if (port === 8080) return r.fulfill({ status: 409, json: { error: { code: "in_use", message: "another program uses port 8080: choose another" } } });
      saved.push(port);
      info = { ...info, next: port, source: "settings" };
    }
    return r.fulfill({ json: info });
  });
  await page.goto("/#/setup");
  const card = page.getByRole("region", { name: "App address" });
  await expect(card).toContainText("This app runs at http://127.0.0.1:8000.");
  const port = card.getByLabel("Port");
  await expect(port).toHaveValue("8000");
  await expect(card.getByRole("button", { name: "Save port" })).toBeDisabled(); // nothing to change
  await port.fill("80");
  await expect(card).toContainText("A whole number from 1024 to 65535.");
  await expect(card.getByRole("button", { name: "Save port" })).toBeDisabled();
  await port.fill("8080");
  await card.getByRole("button", { name: "Save port" }).click();
  await expect(card).toContainText("another program uses port 8080");
  await port.fill("8001");
  await card.getByRole("button", { name: "Save port" }).click();
  await expect(card).toContainText("Starts at http://127.0.0.1:8001 next time");
  expect(saved).toEqual([8001]);
  await card.screenshot({ path: "test-results/setup-app-port.png" });
});

test("Setup: Blender starts when it is needed; Open in Blender then works with none running", async ({ page, request }) => {
  // the owner's agent, 2026-10-13 (finding 4): Open in Blender failed with no Blender running
  let cfg: { start: boolean; exe: string | null; found: string | null } = { start: false, exe: null, found: "D:\\APPS\\Blender\\blender.exe" };
  const saved: object[] = [];
  await page.route("**/api/setup/blender", async (r) => {
    if (r.request().method() === "POST") {
      const body = r.request().postDataJSON();
      saved.push(body);
      cfg = { start: body.start, exe: body.exe, found: null };
    }
    return r.fulfill({ json: cfg });
  });
  const a = await upload(request, "start-blender"); // before the page loads: the library lists it
  await page.goto("/#/setup");
  const section = page.getByRole("region", { name: "Start Blender" });
  await expect(section.getByLabel("Your Blender")).toHaveValue("D:\\APPS\\Blender\\blender.exe"); // the connected one
  await expect(section).toContainText("Filled in from the Blender connected now");
  await section.getByLabel("Start Blender when it is needed").check();
  await section.getByRole("button", { name: "Save" }).click();
  await expect(section.getByRole("button", { name: "Save" })).toBeDisabled();
  expect(saved).toEqual([{ start: true, exe: "D:\\APPS\\Blender\\blender.exe" }]);
  await section.screenshot({ path: "test-results/setup-start-blender.png" });

  await page.route("**/api/blender/status", (r) => r.fulfill({ json: { link: null, start: true } }));
  let opened = 0;
  await page.route(`**/api/assets/${a.id}/blender/open`, (r) => (opened++, r.fulfill({ json: { ok: true, version: "001_upload", objects: ["m"] } })));
  await toLibrary(page);
  await openCard(page, "start-blender");
  await page.locator("#blender > summary").click();
  await expect(page.getByText("Open starts your Blender and its add-on's server")).toBeVisible();
  await page.getByRole("button", { name: "Open 001 in Blender" }).click();
  await expect(page.getByText("Opened 001_upload in Blender")).toBeVisible();
  expect(opened).toBe(1);
});

// --- UX review 2026-10-12, Pass 1 ---------------------------------------------------------------

const animMock = async (page: Page, id: string, metrics: object = {}) => {
  const posted = await mockAsset(page, id, [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_rig_skin", step: "rig_skin", parent: "010_upload", outputs: { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" } },
    { version: "030_anim", step: "anim", parent: "020_rig_skin", outputs: { rigged_anim: "rigged_anim.glb", clips: "clips.json", skeleton: "skeleton.json" }, metrics },
  ]);
  await page.route(`**/files/${id}/*/rigged_anim.glb`, (r) => r.fulfill({ body: ANIM, contentType: "model/gltf-binary" }));
  await page.route(`**/files/${id}/*/clips.json`, (r) =>
    r.fulfill({ json: { clips: [{ name: "Idle", frames: 12, seconds: 0.5 }, { name: "Walk", frames: 24, seconds: 1 }], fps: 24, missing: [] } }),
  );
  await page.route(`**/files/${id}/*/skeleton.json`, (r) => r.fulfill({ json: { template: "mixamo52", joints: [] } }));
  await page.route(`**/files/${id}/*/qa.json`, (r) => r.fulfill({ json: { picked: "blender", engines: {}, items: {} } }));
  return posted;
};
const hitsItself = (el: Element) => {
  const r = el.getBoundingClientRect();
  return el.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2));
};

test("Unsaved edits are guarded: Close, a link and a version switch ask first; a clean editor closes at once (UX-01)", async ({ page }) => {
  await animMock(page, "mock_dirty");
  const dialogs: string[] = [];
  let answer = false;
  page.on("dialog", (d) => {
    dialogs.push(d.message());
    return answer ? d.accept() : d.dismiss();
  });
  await page.goto("/#/asset/mock_dirty?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.getByRole("combobox", { name: "Animation" }).selectOption({ label: "Play: Walk" });
  await page.getByRole("button", { name: "Edit animation" }).click();
  await page.getByText("Clip tools", { exact: true }).click();
  await page.getByRole("spinbutton", { name: "Speed" }).fill("0.5");
  await page.getByRole("spinbutton", { name: "Speed" }).blur();
  await expect(page.getByText("Unsaved changes")).toBeVisible();
  // Close asks; kept editing: the speed is still there
  const close = page.locator(".anim-editor").getByRole("button", { name: "Close", exact: true });
  await close.click();
  expect(dialogs).toEqual(["Discard the unsaved changes?"]);
  await expect(page.getByRole("spinbutton", { name: "Speed" })).toHaveValue("0.5");
  // a link elsewhere asks; kept editing: the address stays on the version
  await page.evaluate(() => void (location.hash = "#/library"));
  await expect.poll(() => dialogs.length).toBe(2);
  await expect(page).toHaveURL(/version=030_anim/);
  await expect(page.getByRole("spinbutton", { name: "Speed" })).toHaveValue("0.5");
  // a version switch from History asks too
  await history(page);
  await page.locator("#versions li").first().click();
  await expect.poll(() => dialogs.length).toBe(3);
  await expect(page).toHaveURL(/version=030_anim/);
  await expect(page.locator(".anim-editor")).toHaveCount(1);
  // discarded: the editor closes; a clean editor closes without a question
  answer = true;
  await close.click();
  await expect(page.locator(".anim-editor")).toHaveCount(0);
  await page.getByRole("button", { name: "Edit animation" }).click();
  await page.locator(".anim-editor").getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.locator(".anim-editor")).toHaveCount(0);
  expect(dialogs).toHaveLength(4);
});

test("Licences has its own column; History is docked beside the view, Escape closes it from anywhere, focus returns (UX-03, UX-08)", async ({ page }) => {
  await mockAsset(page, "mock_side", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_clean", step: "clean", parent: "010_upload", outputs: { mesh: "mesh.glb", report: "report.json" } },
  ]);
  await page.route("**/api/licenses", (r) => r.fulfill({ json: [{ name: "Credited", kind: "model", license: "CC-BY-4.0", commercial: true, attribution: "Made by Ann" }] }));
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/#/asset/mock_side?version=020_clean");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.getByRole("button", { name: "Licences" }).click();
  const lic = page.getByRole("region", { name: "Licences" });
  await expect(lic).toContainText("Your file");
  const heading = lic.getByRole("heading", { name: /^Licences of/ });
  await expect(heading).toBeInViewport();
  expect(await heading.evaluate(hitsItself)).toBe(true); // nothing lies over it
  await expect(lic.getByRole("button", { name: "Close" })).toBeFocused();
  await lic.getByRole("button", { name: "Close" }).click();
  await expect(page.getByRole("button", { name: "Licences" })).toBeFocused();
  // History takes a column of its own: the view's controls stay reachable, Escape from one of them closes it
  await history(page);
  const hist = page.getByRole("region", { name: "History" });
  await expect(hist.getByRole("button", { name: "Close" })).toBeFocused();
  const frame = page.getByRole("button", { name: "Frame model" });
  await frame.focus();
  expect(await frame.evaluate(hitsItself)).toBe(true);
  await page.keyboard.press("Escape");
  await expect(hist).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^History/ })).toBeFocused();
});

test("The motion panel never scrolls sideways; the editor fits a laptop and a compact screen (UX-02, UX-04)", async ({ page }) => {
  await animMock(page, "mock_fit");
  await page.route(/\/api\/clips\?/, (r) => r.fulfill({ json: [{ name: "A clip whose name goes on and on and on and on and on" }, { name: "Walk" }] }));
  for (const width of [1280, 1024, 900]) {
    await page.setViewportSize({ width, height: 720 });
    await page.goto("/#/asset/mock_fit?version=020_rig_skin");
    await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
    await expect(page.getByLabel("Clips")).toBeVisible();
    expect(await page.locator("aside.panel").evaluate((p) => p.scrollWidth - p.clientWidth), `${width} px wide`).toBeLessThanOrEqual(1);
  }
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/#/asset/mock_fit?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.getByRole("combobox", { name: "Animation" }).selectOption({ label: "Play: Walk" });
  await page.getByRole("button", { name: "Edit animation" }).click();
  const transport = page.locator(".timeline-slot .transport");
  await expect(transport).toBeVisible();
  const box = () =>
    page.evaluate(() => {
      const r = (s: string) => document.querySelector(s)!.getBoundingClientRect();
      return { view: r(".preview").right, transport: r(".timeline-slot .transport").right, canvas: r("#viewport").height, page: document.documentElement.scrollWidth };
    });
  let b = await box();
  expect(b.transport).toBeLessThanOrEqual(b.view + 1);
  expect(b.canvas).toBeGreaterThanOrEqual(180);
  await page.setViewportSize({ width: 640, height: 360 });
  await expect.poll(async () => (await box()).page).toBeLessThanOrEqual(640);
  b = await box();
  expect(b.canvas).toBeGreaterThanOrEqual(140);
});

// --- Pass 1 follow-ups (review 2026-10-12) -------------------------------------------------------

test("The timeline's canvases draw at the size they are shown; the 3D view keeps its 180 px (UX-04 follow-up)", async ({ page }) => {
  await animMock(page, "mock_ruler");
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/#/asset/mock_ruler?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.getByRole("combobox", { name: "Animation" }).selectOption({ label: "Play: Walk" });
  await page.getByRole("button", { name: "Edit animation" }).click();
  await expect(page.locator(".timeline-slot canvas").first()).toBeVisible();
  const sizes = () =>
    page.evaluate(() => ({
      canvases: [...document.querySelectorAll(".timeline-slot canvas")].map((c) => {
        const el = c as HTMLCanvasElement;
        return Math.abs(el.getBoundingClientRect().height - el.height / (window.devicePixelRatio || 1));
      }),
      view: document.querySelector("#viewport")!.getBoundingClientRect().height,
    }));
  await expect.poll(async () => Math.max(...(await sizes()).canvases)).toBeLessThanOrEqual(1); // the ruler was 24 drawn, 180 shown
  expect((await sizes()).view).toBeGreaterThanOrEqual(180);
  await page.screenshot({ path: "test-results/editor-timeline.png" });
});

test("A result that arrives during an unsaved edit is offered, not opened over the edit", async ({ page }) => {
  await animMock(page, "mock_late");
  let release = false;
  await page.route("**/api/jobs/job-*", (r) => r.fulfill({ json: release ? { status: "done", version: "040_validate" } : { status: "running" } }));
  const dialogs: string[] = [];
  page.on("dialog", (d) => (dialogs.push(d.message()), d.dismiss()));
  await page.goto("/#/asset/mock_late?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.locator("aside.panel").getByRole("button", { name: "Check model" }).click(); // a job on this version
  await page.getByRole("combobox", { name: "Animation" }).selectOption({ label: "Play: Walk" });
  await page.getByRole("button", { name: "Edit animation" }).click();
  await page.getByText("Clip tools", { exact: true }).click();
  await page.getByRole("spinbutton", { name: "Speed" }).fill("0.5");
  await page.getByRole("spinbutton", { name: "Speed" }).blur();
  await expect(page.getByText("Unsaved changes")).toBeVisible();
  release = true; // the check ends while the user edits
  await expect(page.getByRole("button", { name: "View result" })).toBeVisible();
  expect(dialogs).toEqual([]); // nothing asked out of nowhere
  await expect(page).toHaveURL(/version=030_anim/);
  await expect(page.getByRole("spinbutton", { name: "Speed" })).toHaveValue("0.5");
  // opening it is the user's choice, and that asks
  await page.getByRole("button", { name: "View result" }).click();
  expect(dialogs).toEqual(["Discard the unsaved changes?"]);
  await expect(page).toHaveURL(/version=030_anim/);
});

test("At 900 px History docks beside the view without a sideways scroll", async ({ page }) => {
  await mockAsset(page, "mock_mid", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_clean", step: "clean", parent: "010_upload", outputs: { mesh: "mesh.glb", report: "report.json" } },
  ]);
  await page.setViewportSize({ width: 900, height: 720 });
  await page.goto("/#/asset/mock_mid?version=020_clean");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await history(page);
  await expect(page.getByRole("region", { name: "History" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(900);
  await page.screenshot({ path: "test-results/history-900.png" });
});

test("The time bar's buttons each look different: Play is not Next frame", async ({ page }) => {
  await animMock(page, "mock_glyphs");
  await page.goto("/#/asset/mock_glyphs?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.getByRole("combobox", { name: "Animation" }).selectOption({ label: "Play: Walk" });
  const bar = page.getByRole("group", { name: "Time" });
  await bar.getByRole("button", { name: "Pause" }).click();
  const names = ["First frame", "Previous frame", "Play", "Next frame", "Last frame"];
  const glyphs = await Promise.all(names.map((n) => bar.getByRole("button", { name: n, exact: true }).innerText()));
  expect(glyphs).toEqual(["|◀◀", "◀|", "▶", "|▶", "▶▶|"]);
  expect(new Set(glyphs).size).toBe(5);
});

// --- Import an animation (owner 2026-10-12) --------------------------------------------------------

test("Animate: an animation file comes in as one of your motions; its page brings in the file's others and puts it on the model", async ({ page }) => {
  await page.route("**/api/clips?*", (r) => r.fulfill({ json: [{ name: "Walk" }] }));
  const posted = await mockAsset(page, "mock_file", [
    { version: "010_trellis2", step: "trellis2", parent: null, outputs: { mesh: "hi.glb", ovoxel: "ovoxel.npz" } },
    { version: "030_rig_skin", step: "rig_skin", parent: "010_trellis2", outputs: { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" } },
    {
      version: "050_import_motion",
      step: "import_motion",
      parent: "040_upload",
      params: { for: "030_rig_skin" },
      outputs: { clip: "clip.glb" },
      metrics: { name: "Samba", file: "Samba.fbx", skeleton: "mixamo", seconds: 3.2, clips: ["Samba", "Wave"], picked: "Samba" },
    },
  ]);
  await page.route("**/files/mock_file/030_rig_skin/qa.json", (r) => r.fulfill({ json: { items: {} } }));
  const parents: string[] = [];
  for (const step of ["import_motion", "anim"])
    await page.route(`**/api/assets/mock_file/steps/${step}`, (r) => (parents.push(r.request().postDataJSON().parent), r.fallback()));
  await page.route("**/api/assets/mock_file/upload/*", (r) =>
    r.fulfill({ json: { version: "060_upload", step: "upload", parent: null, outputs: { model: "source/run.fbx" } } }),
  );
  await page.goto("/#/asset/mock_file?version=030_rig_skin");
  await expect(page.locator('optgroup[label="Your motions"]')).toContainText("Samba (file) · 3.2 s");
  const panel = page.locator("aside.panel"); // the Import page's own form stays mounted, hidden
  await panel.getByRole("radio", { name: "From a file" }).check(); // the animation source (Pass 2: one form shown)
  const go = panel.getByRole("button", { name: "Import animation", exact: true });
  await expect(go).toBeDisabled(); // no file yet
  await panel.getByLabel("Animation file").setInputFiles({ name: "run.fbx", mimeType: "application/octet-stream", buffer: Buffer.from("fbx") });
  await go.click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["import_motion", { for: "030_rig_skin" }]);
  expect(parents[0]).toBe("060_upload"); // the step runs on the uploaded file
  // the imported motion's page: what came in, the file's other animation, then onto the model
  await page.goto("/#/asset/mock_file?version=050_import_motion");
  await expect(page.getByRole("heading", { name: "Animation from a file" })).toBeVisible();
  await expect(page.locator("aside.panel")).toContainText('"Samba", 3.2 s, from Samba.fbx on a Mixamo skeleton.');
  await page.locator("aside.panel").screenshot({ path: "test-results/import-motion-page.png" });
  await page.getByLabel("Another animation in the file").selectOption("Wave");
  await page.getByRole("button", { name: "Bring it in too" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]).toEqual(["import_motion", { clip: "Wave", for: "030_rig_skin" }]);
  expect(parents[1]).toBe("040_upload"); // the same upload, another of its animations
  await page.goto("/#/asset/mock_file?version=050_import_motion");
  await page.getByRole("button", { name: "Put this motion on the model" }).click();
  await expect.poll(() => posted.length).toBe(3);
  expect(posted[2]).toEqual(["anim", { clips: ["050_import_motion"] }]);
  expect(parents[2]).toBe("030_rig_skin");
});

test("Import: an animation file goes onto a character's newest skinned version in one go", async ({ page }) => {
  const posted = await mockAsset(page, "mock_char", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_rig_skin", step: "rig_skin", parent: "010_upload", outputs: { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" } },
    { version: "030_rig_skin", step: "rig_skin", parent: "010_upload", outputs: { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" } },
  ]);
  const parents: string[] = [];
  for (const step of ["import_motion", "anim"])
    await page.route(`**/api/assets/mock_char/steps/${step}`, (r) => (parents.push(r.request().postDataJSON().parent), r.fallback()));
  await page.route("**/api/assets/mock_char/upload/*", (r) =>
    r.fulfill({ json: { version: "045_upload", step: "upload", parent: null, outputs: { model: "source/dance.bvh" } } }),
  );
  await page.goto("/#/import");
  await page.getByRole("button", { name: "An animation for a character" }).click(); // the Import page's second mode
  const form = page.locator(".form", { has: page.getByRole("heading", { name: "Put an animation on one of your characters" }) });
  const go = form.getByRole("button", { name: "Import animation" });
  await form.getByLabel("Character").selectOption("mock_char");
  await form.getByLabel("Animation file").setInputFiles({ name: "chair.obj", mimeType: "text/plain", buffer: Buffer.from("v 0 0 0") });
  await expect(form).toContainText("this isn't an animation file the app reads");
  await expect(go).toBeDisabled();
  await form.getByLabel("Animation file").setInputFiles({ name: "dance.bvh", mimeType: "text/plain", buffer: Buffer.from("HIERARCHY") });
  await page.screenshot({ path: "test-results/import-animation-form.png", fullPage: true });
  await go.click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted).toEqual([
    ["import_motion", { for: "030_rig_skin" }], // the newest skinned version
    ["anim", { clips: ["040_import_motion"] }],
  ]);
  expect(parents).toEqual(["045_upload", "030_rig_skin"]);
  await expect(page).toHaveURL(/#\/asset\/mock_char\?version=050_anim/); // the animated character opens
});

// --- UX review Pass 3 (UX-10, UX-11, UX-12) -----------------------------------------------------------

test("Export: Save all files to the folder copies the package through export-copy, says where, and remembers the folder (UX-10)", async ({ page }) => {
  await mockAsset(page, "mock_pkg", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    {
      version: "020_export",
      step: "export",
      parent: "010_upload",
      params: { profile: "obj" },
      outputs: { "lantern.obj": "obj/lantern.obj", "lantern.mtl": "obj/lantern.mtl", "lantern.png": "obj/lantern.png", "CREDITS.txt": "obj/CREDITS.txt" },
    },
  ]);
  let body: any = null;
  await page.route("**/api/export/copy", (r) => {
    body = r.request().postDataJSON();
    return r.fulfill({ json: { copied: [{ asset_id: "mock_pkg", version: "020_export", dir: "D:\\Games\\mock_pkg_obj", files: ["CREDITS.txt", "lantern.mtl", "lantern.obj", "lantern.png"] }], skipped: [] } });
  });
  await page.goto("/#/asset/mock_pkg?version=020_export");
  const save = page.getByRole("button", { name: "Save all files to the folder" });
  await expect(save).toBeDisabled(); // no folder yet
  await expect(page.locator("aside.panel")).toContainText("Keep the .mtl and the texture next to the .obj");
  await page.getByLabel("Folder").fill("D:\\Games");
  await save.click();
  await expect(page.getByRole("status").filter({ hasText: "Saved 4 files into D:\\Games\\mock_pkg_obj" })).toBeVisible();
  expect(body).toEqual({ folder: "D:\\Games", items: [{ asset_id: "mock_pkg", version: "020_export" }] });
  await page.reload();
  await expect(page.getByLabel("Folder")).toHaveValue("D:\\Games"); // remembered on this computer
});

test("Batch export: each selected asset's version is shown before the export runs (UX-10)", async ({ page }) => {
  await mockAsset(page, "mock_plan", [
    { version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } },
    { version: "020_clean", step: "clean", parent: "010_upload", outputs: { mesh: "mesh.glb" } },
    { version: "030_clean", step: "clean", parent: "010_upload", outputs: { mesh: "mesh.glb" } },
  ]);
  await page.route("**/api/assets/mock_plan", (r) =>
    r.fulfill({
      json: {
        asset: { id: "mock_plan", name: "lantern", created: "" },
        versions: [
          { version: "010_upload", step: "upload", parent: null, params: {}, outputs: { mesh: "mesh.glb" }, created: "", metrics: {}, provenance: "upload", restricted: false },
          { version: "020_clean", step: "clean", parent: "010_upload", params: {}, outputs: { mesh: "mesh.glb" }, created: "", metrics: {}, provenance: "step", restricted: false, starred: true },
          { version: "030_clean", step: "clean", parent: "010_upload", params: {}, outputs: { mesh: "mesh.glb" }, created: "", metrics: {}, provenance: "step", restricted: false },
        ],
      },
    }),
  );
  await page.goto("/#/");
  await page.getByLabel("Search assets").fill("mock_plan");
  await page.getByRole("button", { name: "Select assets" }).click();
  await page.getByLabel("Select lantern").check();
  await page.getByRole("button", { name: "Export selected (1)" }).click();
  // the starred one, not the newest: said before anything runs
  await expect(page.getByRole("list", { name: "What will be exported" })).toContainText("lantern: Cleaned 020 (starred)");
});

test("Library: search, sort and filter survive Setup, the Library button and Back; Clear filters (UX-11)", async ({ page }) => {
  await mockAsset(page, "mock_view", [{ version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } }]);
  await page.goto("/#/");
  const search = page.getByLabel("Search assets");
  await search.fill("mock_view");
  await page.getByLabel("Sort").selectOption({ label: "Name A–Z" });
  await expect(page).toHaveURL(/#\/\?q=mock_view&sort=name$/); // in the address, no new history entry
  await expect(page.getByLabel("Sort").locator("option").first()).toHaveText("Recently changed"); // the update-based sort, named so
  await page.getByRole("navigation").getByRole("link", { name: "Setup" }).click();
  await expect(page.getByRole("heading", { name: "Setup", exact: true })).toBeVisible();
  await page.getByRole("navigation").getByRole("link", { name: "Library" }).click();
  await expect(search).toHaveValue("mock_view"); // the Library button brings the view back
  await expect(page.getByLabel("Sort")).toHaveValue("name");
  await page.getByRole("navigation").getByRole("link", { name: "Setup" }).click();
  await page.goBack();
  await expect(search).toHaveValue("mock_view"); // and Back
  await search.fill("no asset is called this");
  await expect(page.getByText("No assets match.")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).first().click();
  await expect(search).toHaveValue("");
  await expect(page.locator("ul.cards li").first()).toBeVisible();
});

test("Setup: a blocked action's link lands on its card; the character cards; one Hugging Face login (UX-12)", async ({ page }) => {
  const row = (id: number, name: string, groups: string[], installed: boolean) => ({ id, name, groups, revision: "r", where: "hub", installed, partial: false, bytes: 1e9, missing_bytes: installed ? 0 : 1e9 });
  await page.route("**/api/models", (r) =>
    r.fulfill({
      json: [
        row(1, "Tongyi-MAI/Z-Image-Turbo", ["concepts"], true),
        row(2, "black-forest-labs/faithful", ["faithful"], true),
        row(3, "Mesh2Motion/mesh2motion-app", ["clips"], false),
        row(4, "VAST-AI/SkinTokens", ["skin"], false),
        row(5, "meta-llama/Meta-Llama-3-8B-Instruct", ["kimodo"], false),
      ],
    }),
  );
  await page.route("**/api/environments", (r) => r.fulfill({ json: [{ name: ".venv-ml", state: "ok", groups: ["concepts", "faithful", "clips", "skin"] }] }));
  await page.route("**/api/hf", (r) => r.fulfill({ json: { token: false, user: null, access: {}, gated: { "meta-llama/Meta-Llama-3-8B-Instruct": ["kimodo"] } } }));
  await page.goto("/#/setup?cap=clips");
  const card = page.locator("#cap-clips");
  await expect(card).toContainText("Ready-made animations");
  await expect(card).toHaveClass(/target/);
  await expect(card).toBeInViewport();
  await expect(page.locator("#cap-skin")).toContainText("Learned skinning");
  await expect(page.locator(".summary")).toContainText("Ready for props and objects.");
  await expect(page.locator(".summary")).toContainText("Ready-made animations");
  // a gated card: the same login as the guided setup, no terminal
  const kimodo = page.locator("#cap-kimodo");
  await kimodo.getByText("Before you install").click();
  await expect(kimodo.getByLabel("Hugging Face token")).toBeVisible();
  await expect(kimodo).toContainText("Accept the Meta Llama 3 Community License");
  await expect(kimodo).not.toContainText("Run this in a terminal");
  await expect(kimodo).not.toContainText("never asks for");
});

// --- UX review Pass 4 (UX-13, UX-14) -----------------------------------------------------------------

test("Accessibility: a skip link to the content, the asset name as the page's h1, field outlines at 3:1, a file error announced on its field (UX-13)", async ({ page }) => {
  await mockAsset(page, "mock_a11y", [{ version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } }]);
  await page.goto("/#/asset/mock_a11y?version=010_upload");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Skip to the content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();
  await expect(page.locator("h1:visible")).toHaveText(["lantern"]); // one h1: the asset (hidden pages are out of the tree)
  await expect(page.locator("aside.panel h2")).toHaveText("Next step");
  // a task that is not reachable yet still takes the focus and says why (UX-15): Generate, for a description alone
  await mockAsset(page, "mock_needs", [{ version: "010_brief", step: "brief", parent: null, params: { prompt: "a lantern", category: "prop" }, outputs: { brief: "brief.yaml" } }]);
  await page.goto("/#/asset/mock_needs?version=010_brief");
  const generate = page.locator(".stages").getByRole("button", { name: "Generate" });
  await expect(generate).toBeDisabled();
  await generate.focus();
  await expect(generate).toBeFocused();
  await expect(generate).toHaveAccessibleDescription("Needs a cut-out reference");
  await expect(generate.locator("xpath=following-sibling::span[1]")).toBeInViewport(); // shown under it while focused
  // an empty field's outline against the page it sits on
  await page.goto("/#/new");
  const ratio = await page.getByLabel("Description").evaluate((el) => {
    const rgb = (c: string) => c.match(/\d+/g)!.slice(0, 3).map(Number);
    const lum = (c: string) => {
      const [r, g, b] = rgb(c).map((v) => v / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    let bg = el.parentElement!;
    while (getComputedStyle(bg).backgroundColor === "rgba(0, 0, 0, 0)") bg = bg.parentElement!;
    const [x, y] = [lum(getComputedStyle(el).borderTopColor), lum(getComputedStyle(bg).backgroundColor)].sort((m, n) => n - m);
    return (x + 0.05) / (y + 0.05);
  });
  expect(ratio).toBeGreaterThanOrEqual(3);
  // a file the import cannot read: said at once, and tied to the file field
  await page.goto("/#/import");
  const input = page.getByLabel("Model file or .zip");
  await input.setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("x") });
  await expect(page.getByRole("alert")).toContainText("this isn't a model file the app reads");
  await expect(input).toHaveAttribute("aria-invalid", "true");
  await expect(input).toHaveAttribute("aria-describedby", "model-file-error");
});

test("Animation editor: the keys as a list, selected and moved a frame without a mouse (UX-13)", async ({ page }) => {
  const posted = await animMock(page, "mock_keylist");
  await page.goto("/#/asset/mock_keylist?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.getByRole("combobox", { name: "Animation" }).selectOption({ label: "Play: Walk" });
  await page.getByRole("button", { name: "Pause" }).click();
  await page.getByRole("spinbutton", { name: "Frame number" }).fill("7");
  await page.getByRole("button", { name: "Edit animation" }).click();
  await expect(page.getByRole("combobox", { name: "Clip to edit" })).toHaveValue("Walk"); // the editor has the clip
  await page.evaluate(() => (window as any).__viewer.testPickBone("Spine"));
  await expect(page.getByRole("heading", { name: "Bone · Spine" })).toBeVisible();
  const q: number[] = await page.evaluate(() => (window as any).__viewer.boneQuat("Spine"));
  const [s, c] = [Math.sin(Math.PI / 12), Math.cos(Math.PI / 12)];
  await page.evaluate((t) => (window as any).__viewer.testPose("Spine", t), [q[3] * s + q[0] * c, q[1] * c + q[2] * s, q[2] * c - q[1] * s, q[3] * c - q[0] * s]);
  const list = page.locator(".key-list");
  await list.getByText("Keys as a list").click();
  // in Curves: the key's values as fields that fit the side panel; typing one changes the turn
  await page.getByRole("button", { name: "Curves" }).click();
  const x = list.getByRole("spinbutton", { name: "X at frame 7" });
  await expect(x).toHaveValue("30");
  expect(await list.evaluate((e) => e.scrollWidth <= e.clientWidth)).toBe(true);
  await x.fill("45");
  await x.press("Enter");
  await expect(page.getByRole("spinbutton", { name: "X degrees" })).toHaveValue("45.0");
  await page.getByRole("button", { name: "Keys", exact: true }).click();
  const key = list.getByRole("button", { name: "Spine, frame 7" });
  await key.focus();
  await page.keyboard.press("Enter"); // select it with the keyboard
  await expect(key).toHaveAttribute("aria-pressed", "true");
  await list.getByRole("button", { name: "A frame later" }).click();
  await expect(list.getByRole("button", { name: "Spine, frame 8" })).toBeVisible();
  await page.getByRole("button", { name: "Save as a new version" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0][1].clips.Walk.keys.Spine[0]).toMatchObject({ f: 8 });
});

test("Library: Select assets is explicit; Recently deleted lists what deleting moved aside and restores it (UX-14)", async ({ page }) => {
  await mockAsset(page, "mock_bin", [{ version: "010_upload", step: "upload", parent: null, outputs: { mesh: "mesh.glb" } }]);
  let rows = [
    { name: "20261012T130500_crate", deleted: "2026-10-12T13:05:00+00:00", asset_id: "crate", asset_name: "crate", kind: "asset", versions: 4 },
    {
      name: "20261012T120000_mock_bin_020_clean",
      deleted: "2026-10-12T12:00:00+00:00",
      asset_id: "mock_bin",
      asset_name: "lantern",
      kind: "versions",
      versions: ["020_clean", "030_remesh"],
      first: { version: "020_clean", step: "clean", parent: "010_upload", params: {}, outputs: { mesh: "mesh.glb" } },
    },
  ];
  const restored: string[] = [];
  await page.route("**/api/trash", (r) => r.fulfill({ json: rows }));
  await page.route("**/api/trash/*/restore", (r) => {
    const name = decodeURIComponent(r.request().url().split("/").at(-2)!);
    restored.push(name);
    rows = rows.filter((x) => x.name !== name);
    return r.fulfill({ json: { asset_id: "crate", versions: [] } });
  });
  await page.goto("/#/");
  const select = page.getByRole("button", { name: "Select assets" });
  await select.click();
  await expect(page.getByRole("button", { name: "Done selecting" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Done selecting" }).click();
  await page.getByText("Recently deleted", { exact: true }).click();
  const list = page.getByRole("list", { name: "Recently deleted" });
  await expect(list).toContainText("crate, 4 versions");
  await expect(list).toContainText("lantern: Cleaned 020 and 1 made from it");
  await list.locator("li", { hasText: "crate" }).getByRole("button", { name: "Restore" }).click();
  await expect(page.getByRole("status").filter({ hasText: "crate, 4 versions: restored." })).toBeVisible();
  expect(restored).toEqual(["20261012T130500_crate"]);
  await expect(list).not.toContainText("crate, 4 versions");
  // the real server: an asset deleted while the list is open shows there at once, and Restore brings it back
  await page.unroute("**/api/trash");
  await page.unroute("**/api/trash/*/restore");
  await page.request.post("/api/assets", { data: { name: "bin test" } });
  await page.reload();
  await page.getByText("Recently deleted", { exact: true }).click();
  const card = page.locator(".cards li", { hasText: "bin test" });
  await card.getByText("More").click();
  page.once("dialog", (d) => d.accept());
  await card.getByRole("button", { name: "Delete" }).click();
  await expect(card).toHaveCount(0);
  await expect(list).toContainText("bin test, 0 versions");
  await list.locator("li", { hasText: "bin test" }).getByRole("button", { name: "Restore" }).click();
  await expect(card).toHaveCount(1);
  await expect(page.locator(".trash li", { hasText: "bin test" })).toHaveCount(0);
  await expect(page.locator(".trash [role=status]")).toHaveText("bin test, 0 versions: restored.");
});

// --- the 2D track (research 15; M50 pictures, M51 pixel art) ------------------------------------------------------

const models2d = async (page: Page, styles: boolean) => {
  const row = (id: number, name: string, groups: string[], installed: boolean) => ({ id, name, groups, revision: "r", where: "hub", installed, partial: false, bytes: 1e9, missing_bytes: installed ? 0 : 1e9 });
  await page.route("**/api/models", (r) =>
    r.fulfill({ json: [row(1, "Tongyi-MAI/Z-Image-Turbo", ["concepts"], true), row(2, "tarn59/pixel_art_style_lora_z_image_turbo", ["styles"], styles)] }),
  );
  await page.route("**/api/environments", (r) => r.fulfill({ json: [{ name: ".venv-ml", state: "ok", groups: ["concepts", "styles"] }] }));
};

test("New asset: a 2D picture from a description is a 2D asset, a brief and the pictures in the kind's size (M50)", async ({ page }) => {
  await models2d(page, false);
  const created: any[] = [];
  const posted: [string, any][] = [];
  await page.route("**/api/assets", (r) =>
    r.request().method() === "POST" ? (created.push(r.request().postDataJSON()), r.fulfill({ json: { id: "mock_new2d", name: "neon", created: "", kind: "2d" } })) : r.fallback(),
  );
  await page.route("**/api/assets/mock_new2d/steps/*", (r) => {
    posted.push([r.request().url().split("/").pop()!, r.request().postDataJSON()]);
    return r.fulfill({ status: 202, json: { job_id: `job-n${posted.length}` } });
  });
  await page.route("**/api/jobs/job-n*", (r) => r.fulfill({ json: { status: "done", version: r.request().url().endsWith("n1") ? "010_brief" : "020_concepts" } }));
  await page.goto("/#/new");
  await page.getByRole("button", { name: "A 2D picture or sprite" }).click();
  await page.getByLabel("Description").fill("a rainy neon street");
  await expect(page.getByLabel("Picture", { exact: false }).first()).toBeChecked(); // the owner: a picture as described, first
  await expect(page.getByLabel("Size")).toHaveValue("1:1");
  await page.getByLabel("Wallpaper").check();
  await expect(page.getByLabel("Size")).toHaveValue("16:9");
  await expect(page.locator("option", { hasText: "Pixel art" })).toHaveJSProperty("disabled", true); // the styles are not installed
  await expect(page.getByRole("link", { name: "set up 2D styles" })).toHaveAttribute("href", "#/setup?cap=styles");
  await page.getByLabel("Item or sprite").check(); // its own size and the cut-out
  await expect(page.getByLabel("Size")).toHaveValue("1:1");
  await expect(page.getByLabel("Cut out the subject")).toBeChecked();
  await page.getByLabel("Wallpaper").check();
  await page.getByRole("button", { name: "Make 2 pictures" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(created).toEqual([{ name: "a rainy neon street", kind: "2d" }]);
  expect(posted[0]).toEqual(["brief", { parent: null, params: { prompt: "a rainy neon street", category: "wallpaper" } }]);
  expect(posted[1]).toEqual(["concepts", { parent: "010_brief", params: { op: "image", width: 1920, height: 1088, count: 2 } }]);
});

test("New asset: a 2D picture from a file is uploaded as it is, no cut-out (M50)", async ({ page }) => {
  const created: any[] = [];
  let steps = 0;
  await page.route("**/api/assets", (r) =>
    r.request().method() === "POST" ? (created.push(r.request().postDataJSON()), r.fulfill({ json: { id: "mock_up2d", name: "x", created: "", kind: "2d" } })) : r.fallback(),
  );
  await page.route("**/api/assets/mock_up2d/upload/*", (r) => r.fulfill({ json: { version: "010_upload", step: "upload", parent: null, params: {}, outputs: { image: "image.png" }, metrics: {}, created: "", provenance: "upload", restricted: false } }));
  await page.route("**/api/assets/mock_up2d/steps/*", (r) => (steps++, r.fulfill({ status: 500, json: {} })));
  await page.goto("/#/new");
  await page.getByRole("button", { name: "A 2D picture or sprite" }).click();
  await page.getByRole("button", { name: "From an image" }).click();
  await expect(page.getByLabel("Views in the picture")).toHaveCount(0); // turnaround sheets are a 3D matter
  await page.getByLabel("Picture (PNG, JPEG or WebP)").setInputFiles({ name: "hero.png", mimeType: "image/png", buffer: PNG });
  await page.getByRole("button", { name: "Add the picture" }).click();
  await expect.poll(() => created.length).toBe(1);
  expect(created[0]).toEqual({ name: "hero", kind: "2d" });
  await page.waitForTimeout(300);
  expect(steps).toBe(0);
});

test("2D workspace: pick a picture, pixelize it, play the sheet, recolour from its palette (M50, M51)", async ({ page }) => {
  await models2d(page, true);
  const posted = await mockAsset(
    page,
    "mock_2d",
    [
      { version: "010_brief", step: "brief", parent: null, params: { prompt: "a brawler", category: "item" }, outputs: { brief: "brief.yaml" } },
      {
        version: "020_concepts",
        step: "concepts",
        parent: "010_brief",
        params: { op: "image", width: 1024, height: 1024, count: 3 },
        outputs: { image_0: "image_0.png", image_1: "image_1.png", image_2: "image_2.png", image: "image.png", preview: "preview.png" },
      },
    ],
    { kind: "2d" },
  );
  await page.route("**/files/mock_2d/*/sheet.json", (r) =>
    r.fulfill({
      json: {
        frames: [0, 1, 2].map((k) => ({ filename: `walk_${k}`, frame: { x: k * 16, y: 0, w: 16, h: 24 }, duration: 100 })),
        meta: { frameTags: [{ name: "walk", from: 0, to: 2 }] },
      },
    }),
  );
  await page.route("**/files/mock_2d/*/palette.gpl", (r) => r.fulfill({ body: "GIMP Palette\nName: mock\n#\n255   0   0 red\n  0   0 255 blue\n", contentType: "text/plain" }));
  await page.goto("/#/asset/mock_2d?version=020_concepts");
  await expect(page.locator(".stages [aria-current=step]")).toHaveText("Picture"); // the 2D row
  const strip = page.getByRole("group", { name: "Pictures" });
  await expect(strip.getByRole("button")).toHaveCount(3);
  await strip.getByRole("button", { name: "picture 2" }).click();
  await expect(page.locator("aside.panel")).toContainText("From picture 2 of 3");
  await expect(page.getByLabel("A height for the subject")).toBeChecked(); // not a pixel-style picture: a height
  await expect(page.getByLabel("Subject's height (px)")).toHaveValue("64");
  await page.getByLabel("Outline").selectOption("darkest");
  await page.getByRole("button", { name: "Make pixel art" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["pixelize", { key: "image_1", height_px: 64, colors: 16, dither: "none", outline: "darkest", method: "dominant", alpha_cut: 0.5 }]);
  // the result: a sheet played at its own durations, whole-pixel zoom
  await expect(page).toHaveURL(/version=030_pixelize/);
  await expect(page.locator(".stages [aria-current=step]")).toHaveText("Pixelize");
  // the 3D viewer's own time bar and Animation menu (one interface)
  await expect(page.getByRole("combobox", { name: "Animation" })).toContainText("Play: walk");
  await page.getByRole("group", { name: "Time" }).getByRole("button", { name: "Pause" }).click();
  await page.getByRole("button", { name: "First frame" }).click();
  await expect(page.getByRole("spinbutton", { name: "Frame number" })).toHaveValue("0");
  await page.getByRole("button", { name: "Next frame" }).click();
  await expect(page.getByRole("spinbutton", { name: "Frame number" })).toHaveValue("1");
  await expect(page.locator(".sheet-canvas")).toHaveAttribute("data-frame", "1");
  await page.getByRole("group", { name: "View" }).getByRole("button", { name: "4×" }).click();
  await expect(page.locator(".sheet-canvas")).toHaveAttribute("width", "64");
  // a recolour from the version's own palette
  await page.getByText("Palette and recolour").click();
  await page.getByLabel("Colour #ff0000").fill("#00ff00");
  await page.getByRole("button", { name: "Make the recoloured version (1 colour)" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]).toEqual(["pixelize", { scale: 1, palette: "input", palette_map: { "#ff0000": "#00ff00" } }]);
  const file = page.getByRole("link", { name: "Sprite sheet (PNG)" });
  await expect(file).toHaveAttribute("download", "");
  await expect(file).toHaveClass(/button small/); // as the 3D export lists its files
});

test("Wireframe: a quad mesh shows its quads, a triangle mesh its triangles (mesh-gates)", async ({ page }) => {
  const QUADS = readFileSync(new URL("../../tests/fixtures/quad_box.glb", import.meta.url)); // six quads as Blender's triangle pairs
  await mockAsset(page, "mock_quads", [
    { version: "010_trellis2", step: "trellis2", parent: null, outputs: { mesh: "hi.glb", ovoxel: "ovoxel.npz" } },
    { version: "020_remesh", step: "remesh", parent: "010_trellis2", params: { faces: 6, topology: "quad" }, outputs: { mesh: "mesh.glb" } },
  ]);
  await page.route("**/files/mock_quads/020_remesh/mesh.glb", (r) => r.fulfill({ body: QUADS, contentType: "model/gltf-binary" }));
  await page.goto("/#/asset/mock_quads?version=020_remesh");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.getByLabel("Wireframe").check();
  await expect.poll(() => page.evaluate(() => (window as any).__viewer.quadEdges)).toBe(24); // four edges per quad
  await page.screenshot({ path: "test-results/wireframe-quads.png" });
  await page.goto("/#/asset/mock_quads?version=010_trellis2"); // the raw triangle mesh, Wireframe still ticked: triangles
  await expect.poll(() => page.evaluate(() => (window as any).__viewer.quadEdges)).toBe(0);
  await expect(page.getByLabel("Wireframe")).toBeChecked();
});

test("Sprites: an animated character's clips rendered for 2D, then pixel art from the sheet (M52)", async ({ page }) => {
  const posted = await animMock(page, "mock_sprites");
  await page.route("**/files/mock_sprites/*/sheet.json", (r) =>
    r.fulfill({
      json: {
        frames: [0, 1, 2, 3].map((k) => ({ filename: `Walk_090_000${k}`, frame: { x: k * 128, y: 0, w: 128, h: 128 }, duration: 83 })),
        meta: { frameTags: [{ name: "Walk", from: 0, to: 3 }] },
      },
    }),
  );
  await page.goto("/#/asset/mock_sprites?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await page.getByText("Tools ▾").click();
  await page.getByRole("button", { name: "Sprites for 2D" }).click();
  const form = page.locator("#sprites");
  await expect(form.getByLabel("Walk")).toBeChecked();
  await form.getByLabel("Idle").uncheck();
  await form.getByLabel("Facing left").check();
  await expect(form.getByLabel("Facing right")).toBeChecked();
  await form.getByLabel("Height in pixels").fill("104");
  await form.getByRole("button", { name: "Render sprites" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["sprites", { clips: ["Walk"], yaws: [90, 270], height_px: 104, fps: 12, shading: "textured", cell: 128, pitch_deg: 12 }]);
  // the sheet: played in whole pixels; pixel art starts at the sheet's own size, 15 colours and an outline
  await expect(page).toHaveURL(/version=040_sprites/);
  await expect(page.getByRole("combobox", { name: "Animation" })).toContainText("Play: Walk");
  await expect(page.locator("aside.panel")).toContainText("Sprites");
  await expect(page.getByLabel("A fixed pixel size")).toBeChecked();
  await expect(page.getByLabel("Picture pixels per pixel")).toHaveValue("1");
  await page.getByRole("button", { name: "Make pixel art" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1]).toEqual(["pixelize", { scale: 1, colors: 15, dither: "none", outline: "darkest", method: "dominant", alpha_cut: 0.5 }]);
});

test("2D: Animate this character makes a 3D asset from the full-size picture behind the pixel art, redrawn in 3D", async ({ page }) => {
  await models2d(page, true);
  await mockAsset(
    page,
    "mock_anim2d",
    [
      { version: "010_brief", step: "brief", parent: null, params: { prompt: "a viking", category: "item" }, outputs: { brief: "brief.yaml" } },
      { version: "020_concepts", step: "concepts", parent: "010_brief", params: { op: "image", count: 2 }, outputs: { image_0: "image_0.png", image_1: "image_1.png", image: "image.png" } },
      { version: "030_pixelize", step: "pixelize", parent: "020_concepts", params: { key: "image_1", scale: 8 }, outputs: { sheet: "sheet.png", data: "sheet.json", image: "image.png", palette: "palette.gpl" } },
    ],
    { kind: "2d" },
  );
  await page.route("**/files/mock_anim2d/*/sheet.json", (r) => r.fulfill({ json: { frames: [{ frame: { x: 0, y: 0, w: 16, h: 16 }, duration: 100 }], meta: {} } }));
  const fetched: string[] = [];
  page.on("request", (r) => r.url().includes("/files/mock_anim2d/020_concepts/") && fetched.push(r.url().split("/").pop()!));
  const created: any[] = [];
  const posted: [string, any][] = [];
  await page.route("**/api/assets", (r) =>
    r.request().method() === "POST" ? (created.push(r.request().postDataJSON()), r.fulfill({ json: { id: "mock_3dviking", name: "lantern (3D)", created: "" } })) : r.fallback(),
  );
  await page.route("**/api/assets/mock_3dviking/upload/*", (r) =>
    r.fulfill({ json: { version: "010_upload", step: "upload", parent: null, params: {}, outputs: { image: "image.png" }, metrics: {}, created: "", provenance: "upload", restricted: false } }),
  );
  await page.route("**/api/assets/mock_3dviking/steps/*", (r) => (posted.push([r.request().url().split("/").pop()!, r.request().postDataJSON()]), r.fulfill({ status: 202, json: { job_id: "job-3d1" } })));
  await page.route("**/api/jobs/job-3d1", (r) => r.fulfill({ json: { status: "done", version: "020_prep" } }));
  await page.goto("/#/asset/mock_anim2d?version=030_pixelize");
  // the next step of a still character: open, right under the pixel art, the main action (the export below it is not)
  const animate = page.locator("#animate");
  await expect(animate.getByRole("heading", { name: "Animate this character" })).toBeVisible();
  await expect(animate).toContainText("the full-size one the pixel art was made from");
  const sections = await page.locator("aside.panel > section, aside.panel > details").evaluateAll((els) => els.map((e) => e.id || e.querySelector("h3")?.textContent || ""));
  expect(sections.slice(0, 2)).toEqual(["Pixel art", "animate"]);
  await expect(animate.getByRole("button", { name: "Make a 3D character from this picture" })).toHaveClass(/primary/);
  await expect(page.locator("aside.panel").getByRole("button", { name: /^Export for/ })).not.toHaveClass(/primary/);
  await animate.getByRole("button", { name: "Make a 3D character from this picture" }).click();
  await expect.poll(() => posted.length).toBe(3);
  expect(created).toEqual([{ name: "lantern (3D)" }]); // a 3D asset: no kind
  // cut out, redrawn as a shaded 3D figure standing (a flat picture makes a cardboard model), cut out again
  expect(posted).toEqual([
    ["prep", { parent: "010_upload", params: {} }],
    ["canon", { parent: "020_prep", params: { look: "render" } }],
    ["prep", { parent: "020_prep", params: {} }],
  ]);
  expect(fetched).toContain("image_1.png"); // the picture the pixel art came from, not the 128 px pixel art
});

test("2D Finish: check the sprites, export them for Godot 2D or Paper2D, the check shown with its sheet (M53)", async ({ page }) => {
  await models2d(page, true);
  const posted = await mockAsset(
    page,
    "mock_fin2d",
    [
      { version: "010_upload", step: "upload", parent: null, outputs: { image: "image.png" } },
      { version: "020_pixelize", step: "pixelize", parent: "010_upload", params: { scale: 8 }, outputs: { sheet: "sheet.png", data: "sheet.json", image: "image.png", palette: "palette.gpl" }, metrics: { frames: 1, cell: [16, 16], colors: 4, scale: 8 } },
      { version: "030_validate", step: "validate", parent: "020_pixelize", outputs: { checklist: "checklist.json" }, metrics: { fail: 0 } },
    ],
    { kind: "2d" },
  );
  await page.route("**/files/mock_fin2d/*/sheet.json", (r) => r.fulfill({ json: { frames: [{ frame: { x: 0, y: 0, w: 16, h: 16 }, duration: 83 }], meta: {} } }));
  await page.route("**/files/mock_fin2d/*/checklist.json", (r) =>
    r.fulfill({ json: { items: { uniform_cells: { status: "pass", value: 1 }, stray_pixels: { status: "warn", value: 3 }, palette: { status: "pass", value: 4 } } } }),
  );
  await page.goto("/#/asset/mock_fin2d?version=020_pixelize");
  const panel = page.locator("aside.panel");
  await expect(panel.getByRole("button", { name: "Check sprites" })).toBeVisible();
  await expect(panel.getByRole("radio", { name: "Godot 2D" })).toBeChecked();
  await expect(panel.getByRole("radio", { name: "Unreal (Paper2D)" })).toBeVisible();
  await expect(panel.getByRole("radio", { name: "Unity" })).toHaveCount(0); // no 3D destinations for a sheet
  await panel.getByRole("radio", { name: "Unreal (Paper2D)" }).check();
  await panel.getByRole("button", { name: "Export for Unreal (Paper2D)" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["export", { profile: "unreal-2d" }]);
  // the check: the sheet in the view, the checklist and the 2D export beside it
  await page.goto("/#/asset/mock_fin2d?version=030_validate");
  await expect(page.locator(".pixel-view")).toBeVisible();
  await expect(panel).toContainText("single stray pixels");
  await expect(panel.getByRole("radio", { name: "Godot 2D" })).toBeChecked();
  await expect(page.locator(".stages [aria-current=step]")).toHaveText("Finish");
});

test("Move sets: a fighter's ready-made set picks the pack's names it has; the fighting sentences fill the prompt", async ({ page }) => {
  await animMock(page, "mock_moves");
  await page.route("**/api/clips*", (r) =>
    r.fulfill({ json: ["Idle_A", "Walk", "Jog", "Fighting Idle", "Punch_Cross", "Kick_Breach", "Death_A", "Sword_Regular_A", "Idle_Sword"].map((name) => ({ name, frames: 24, seconds: 1 })) }),
  );
  await page.goto("/#/asset/mock_moves?version=020_rig_skin");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  const set = page.getByLabel("Move set");
  await expect(set.locator("option", { hasText: "Brawler (Double Dragon)" })).toContainText("6 animations"); // of its 20, the 6 this pack has
  await set.selectOption("brawler");
  await expect(page.getByRole("button", { name: "Add 6 animations" })).toBeVisible();
  await page.getByLabel("From a sentence").check();
  await page.getByRole("group", { name: "Fighting moves" }).getByRole("button", { name: "Roundhouse kick" }).click();
  await expect(page.getByLabel("Motion prompt")).toHaveValue(/roundhouse kick/);
  await expect(page.getByRole("spinbutton", { name: "Seconds" })).toHaveValue("2.5");
});

test("A clip that never moves after the retarget is named on the animated model's panel", async ({ page }) => {
  await animMock(page, "mock_still");
  await page.route("**/files/mock_still/*/clips.json", (r) =>
    r.fulfill({ json: { clips: [{ name: "Idle", frames: 12, seconds: 0.5 }, { name: "Walk", frames: 24, seconds: 1 }], fps: 24, missing: [], still: ["Walk"] } }),
  );
  await page.goto("/#/asset/mock_still?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await expect(page.locator("aside.panel .note.warn")).toContainText("Walk plays but never moves");
  await expect(page.locator("aside.panel")).toContainText("Walk · 1.0 s · never moves");
});

test("The clips whose arms were opened to clear the body are named on the animated model's panel (M67)", async ({ page }) => {
  await animMock(page, "mock_opened", { arms_opened: { Walk: { Left: 14, Right: 9 }, Idle: { Right: 40, uncleared: 3 } } });
  await page.goto("/#/asset/mock_opened?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await expect(page.locator("aside.panel .note").first()).toContainText(
    "Arms opened so the hands clear the body: Walk (up to 14°), Idle (up to 40°, 3 frames still touching).",
  );
});

test("Animate this character is offered for a character picture, not for a wallpaper", async ({ page }) => {
  await models2d(page, true);
  await mockAsset(
    page,
    "mock_wall2d",
    [
      { version: "010_brief", step: "brief", parent: null, params: { prompt: "a neon street", category: "wallpaper" }, outputs: { brief: "brief.yaml" } },
      { version: "020_concepts", step: "concepts", parent: "010_brief", params: { op: "image", count: 1 }, outputs: { image_0: "image_0.png", image: "image.png" } },
    ],
    { kind: "2d" },
  );
  await page.goto("/#/asset/mock_wall2d?version=020_concepts");
  await expect(page.locator("aside.panel").getByRole("heading", { name: "Make it pixel art" })).toBeVisible();
  await expect(page.locator("#animate")).toHaveCount(0);
  // Tools in a 2D asset: the 3D tools say so, not that they need setting up
  await page.locator(".tools > summary").click();
  await expect(page.locator(".tools li", { hasText: "Texture detail" })).toHaveText(/3D models only$/);
  await expect(page.locator(".tools li", { hasText: "Sprites for 2D" })).toContainText("in a 3D character: Animate this character");
  // M61: the 2D tool opens its section of the panel
  await expect(page.locator(".tools li", { hasText: "Upscale or redraw" })).toContainText("available");  await page.locator(".tools").getByRole("button", { name: "Upscale or redraw" }).click();
  await expect(page.locator("#redraw")).toHaveAttribute("open", "");
});

test("A redrawn character's cut-out offers Redraw again: the same picture, the same look, another seed", async ({ page }) => {
  await page.route("**/api/models", (r) => r.fulfill({ json: [] })); // nothing known missing: Generate 3D, not its setup link
  const posted = await mockAsset(page, "mock_redraw", [
    { version: "010_upload", step: "upload", parent: null, outputs: { image: "image.png" } },
    { version: "020_prep", step: "prep", parent: "010_upload", outputs: { image: "image.png" } },
    { version: "030_canon", step: "canon", parent: "020_prep", params: { look: "render", seed: 7 }, outputs: { image: "image.png", gate: "gate.json" } },
    { version: "040_prep", step: "prep", parent: "030_canon", outputs: { image: "image.png" } },
  ]);
  await page.goto("/#/asset/mock_redraw?version=020_prep");
  await expect(page.locator("#generate").getByRole("button", { name: "Generate 3D" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Redraw again" })).toHaveCount(0); // the first cut-out was not redrawn
  await page.goto("/#/asset/mock_redraw?version=040_prep");
  await page.getByRole("button", { name: "Redraw again" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted).toEqual([["canon", { look: "render" }], ["prep", {}]]);
});

test("New asset: a custom picture size snaps to the model's grid; a seamless tile asks for a square tile (M54)", async ({ page }) => {
  await models2d(page, false);
  const posted: [string, any][] = [];
  await page.route("**/api/assets", (r) =>
    r.request().method() === "POST" ? r.fulfill({ json: { id: "mock_tile_new", name: "bricks", created: "", kind: "2d" } }) : r.fallback(),
  );
  await page.route("**/api/assets/mock_tile_new/steps/*", (r) => {
    posted.push([r.request().url().split("/").pop()!, r.request().postDataJSON()]);
    return r.fulfill({ status: 202, json: { job_id: `job-t${posted.length}` } });
  });
  await page.route("**/api/jobs/job-t*", (r) => r.fulfill({ json: { status: "done", version: r.request().url().endsWith("t1") ? "010_brief" : "020_concepts" } }));
  await page.goto("/#/new");
  await page.getByRole("button", { name: "A 2D picture or sprite" }).click();
  await page.getByLabel("Description").fill("red brick wall");
  // a size of one's own: typed off the grid, rounded on leaving the field; out of bounds, held to them
  await page.getByLabel("Size").selectOption("custom");
  await page.getByLabel("Width (px)").fill("1080");
  await page.getByLabel("Height (px)").click();
  await expect(page.getByLabel("Width (px)")).toHaveValue("1088");
  await page.getByLabel("Height (px)").fill("3000");
  await expect(page.getByRole("button", { name: "Make 2 pictures" })).toBeDisabled();
  await page.getByLabel("Width (px)").click();
  await expect(page.getByLabel("Height (px)")).toHaveValue("2048");
  await expect(page.getByRole("button", { name: "Make 2 pictures" })).toBeEnabled();
  // the tile kind: square sizes only, one picture, no cut-out
  await page.getByLabel("Seamless tile").check();
  await expect(page.getByLabel("Tile size")).toHaveValue("512");
  await expect(page.getByLabel("Pictures")).toHaveCount(0);
  await expect(page.getByLabel("Cut out the subject")).toHaveCount(0);
  await page.getByLabel("Tile size").selectOption("256");
  await page.getByRole("button", { name: "Make a seamless tile" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[0]).toEqual(["brief", { parent: null, params: { prompt: "red brick wall", category: "tile" } }]);
  expect(posted[1]).toEqual(["concepts", { parent: "010_brief", params: { op: "tile", size: 256 } }]);
});

test("2D workspace: a tile shows repeated with its seams, makes pixel art in whole tiles, and has its check (M54)", async ({ page }) => {
  await models2d(page, true);
  const posted = await mockAsset(
    page,
    "mock_tile",
    [
      { version: "010_brief", step: "brief", parent: null, params: { prompt: "red bricks", category: "tile" }, outputs: { brief: "brief.yaml" } },
      {
        version: "020_concepts",
        step: "concepts",
        parent: "010_brief",
        params: { op: "tile", size: 512 },
        outputs: { tile: "tile.png", image: "tile.png", preview: "preview.png", report: "report.json" },
        metrics: { size: 512, seam_ratio: 0.93, seam_ratio_before: 4.2 },
      },
    ],
    { kind: "2d" },
  );
  await page.goto("/#/asset/mock_tile?version=020_concepts");
  await expect(page.locator(".stages [aria-current=step]")).toHaveText("Tiles");
  await expect(page.getByRole("img", { name: "the tile, repeated 3 × 3" })).toBeVisible();
  await page.getByRole("button", { name: "One tile" }).click();
  await expect(page.getByRole("img", { name: "the tile", exact: true })).toBeVisible();
  const panel = page.locator("aside.panel");
  await expect(panel.getByRole("heading", { name: "Seamless tile" })).toBeVisible();
  await expect(panel).toContainText("Its edges meet");
  await expect(panel).toContainText("4.20 before the mending");
  // pixel art in whole tiles: a size that divides 512, no outline (a tile has no edge to draw round)
  await expect(page.getByLabel("Pixels a side", { exact: true }).first()).toBeChecked();
  await expect(page.getByRole("combobox", { name: "Pixels a side" })).toHaveValue("64"); // an eighth of the tile
  await expect(page.getByRole("combobox", { name: "Pixels a side" }).locator("option")).toHaveText(["8 × 8", "16 × 16", "32 × 32", "64 × 64", "128 × 128", "256 × 256"]);
  await expect(page.getByLabel("Outline")).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Check the tile" })).toBeVisible();
  await page.getByRole("combobox", { name: "Pixels a side" }).selectOption("16");
  await page.getByRole("button", { name: "Make pixel art" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["pixelize", { height_px: 16, colors: 16, dither: "none", method: "median", alpha_cut: 0.5 }]); // not pixel-style
});

test("2D workspace: a background becomes parallax layers (depth, then layers); the layers scroll, name their speeds and have their check (M55)", async ({ page }) => {
  await models2d(page, true);
  const row = (id: number, name: string, groups: string[]) => ({ id, name, groups, revision: "r", where: "hub", installed: true, partial: false, bytes: 1e9, missing_bytes: 0 });
  await page.route("**/api/models", (r) =>
    r.fulfill({ json: [row(1, "Tongyi-MAI/Z-Image-Turbo", ["concepts"]), row(2, "Ruicheng/moge-3-vitg", ["layers"])] }),
  );
  const posted = await mockAsset(
    page,
    "mock_stage",
    [
      { version: "010_brief", step: "brief", parent: null, params: { prompt: "a street at dusk", category: "background" }, outputs: { brief: "brief.yaml" } },
      {
        version: "020_concepts",
        step: "concepts",
        parent: "010_brief",
        params: { op: "image", width: 2048, height: 768, count: 1 },
        outputs: { image_0: "image_0.png", image: "image.png", preview: "preview.png" },
      },
    ],
    { kind: "2d" },
  );
  await page.route("**/files/mock_stage/*/layers.json", (r) =>
    r.fulfill({ json: [{ file: "layer_0.png", scroll_scale: 0.05, share: 0.4 }, { file: "layer_1.png", scroll_scale: 1, share: 0.6 }] }),
  );
  await page.goto("/#/asset/mock_stage?version=020_concepts");
  const panel = page.locator("aside.panel");
  await expect(panel.getByRole("heading", { name: "Parallax layers" })).toBeVisible(); // a stage: its own section
  await panel.getByLabel("Layers").selectOption("3");
  await panel.getByRole("button", { name: "Make parallax layers" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted).toEqual([
    ["depth", { key: "image_0" }],
    ["layers", { count: 3, subject: false }],
  ]);
  // the layers version: a scrolling stack, its speeds, the bands, its check and export
  await expect(page).toHaveURL(/version=040_layers/);
  await expect(page.locator(".stages [aria-current=step]")).toHaveText("Layers");
  await expect(page.getByRole("img", { name: "2 parallax layers, scrolling" })).toBeVisible();
  await page.getByRole("button", { name: "Pause" }).click();
  await page.getByLabel("Camera").fill("1");
  await expect(panel.getByRole("link", { name: "Farthest (× 0.05)" })).toBeVisible();
  await expect(panel.getByRole("link", { name: "Nearest (× 1)" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Check the layers" })).toBeVisible();
  await page.getByLabel("Show").selectOption({ label: "The farthest layer alone" }); // what the stack hides
  await expect(page.getByRole("img", { name: "the picture" })).toHaveAttribute("src", /layer_0\.png/);
  await page.getByLabel("Show").selectOption("bands");
  await expect(page.getByRole("img", { name: "the picture" })).toHaveAttribute("src", /bands\.png/);
  await expect(panel.getByText("Make the layers again")).toBeVisible();
});

test("Pixel editor: draw a line in a palette colour, undo and redo, fill, move a selection, save the changed frame (M56)", async ({ page }) => {
  await models2d(page, true);
  const posted = await mockAsset(
    page,
    "mock_edit",
    [
      { version: "010_upload", step: "upload", parent: null, outputs: { image: "image.png" } },
      { version: "020_pixelize", step: "pixelize", parent: "010_upload", params: { colors: 4 }, outputs: { frames: "frames", sheet: "sheet.png", data: "sheet.json", palette: "palette.gpl", preview: "preview.png" }, metrics: { frames: 2, cell: [16, 16], colors: 2 } },
    ],
    { kind: "2d" },
  );
  await page.route("**/files/mock_edit/*/sheet.json", (r) =>
    r.fulfill({ json: { frames: [0, 1].map((k) => ({ filename: `walk_${k}`, frame: { x: k * 16, y: 0, w: 16, h: 16 }, duration: 100 })), meta: { frameTags: [] } } }),
  );
  await page.route("**/files/mock_edit/*/palette.gpl", (r) => r.fulfill({ body: "GIMP Palette\nName: m\n#\n255   0   0 red\n  0   0 255 blue\n", contentType: "text/plain" }));
  await page.goto("/#/asset/mock_edit?version=020_pixelize");
  await page.locator("#edit > summary").click();
  await page.getByRole("button", { name: "Edit the pixels" }).click();
  await expect(page.locator("canvas.pixel-edit")).toHaveAttribute("aria-label", "frame 1 of 2, 16 × 16 px");
  const box = (await page.locator("canvas.pixel-edit").boundingBox())!;
  const z = box.width / 16;
  const at = (x: number, y: number) => [box.x + x * z + z / 2, box.y + y * z + z / 2] as const;
  await page.getByRole("button", { name: "Colour #ff0000" }).click();
  await page.keyboard.press("l"); // the Line tool
  await expect(page.getByLabel("Line")).toBeChecked();
  await page.mouse.move(...at(1, 1));
  await page.mouse.down();
  await page.mouse.move(...at(6, 1), { steps: 5 });
  await page.mouse.up();
  const panel = page.locator("aside.panel");
  await expect(panel.getByText("Unsaved changes")).toBeVisible();
  await panel.getByRole("button", { name: "Undo" }).click();
  await expect(panel.getByText("Unsaved changes")).toHaveCount(0);
  await panel.getByRole("button", { name: "Redo" }).click();
  await expect(panel.getByText("Unsaved changes")).toBeVisible();
  await page.getByLabel("Fill").check();
  await page.keyboard.press("l"); // a key right after clicking a tool's radio still picks a tool
  await expect(page.getByLabel("Line")).toBeChecked();
  await page.getByLabel("Fill").check();
  await page.mouse.click(...at(10, 10));
  await page.getByLabel("Select").check(); // a rectangle, then its pixels dragged one to the right
  await page.mouse.move(...at(1, 0));
  await page.mouse.down();
  await page.mouse.move(...at(6, 2), { steps: 3 });
  await page.mouse.up();
  await expect(panel.getByText("Selection (6 × 3)")).toBeVisible();
  await page.mouse.move(...at(3, 1));
  await page.mouse.down();
  await page.mouse.move(...at(4, 1), { steps: 2 });
  await page.mouse.up();
  await panel.getByRole("button", { name: "Save as a new version" }).click();
  await expect.poll(() => posted.length).toBe(1);
  const [step, params] = posted[0];
  expect(step).toBe("pixelize");
  expect(params.op).toBe("edit");
  expect(Object.keys(params.frames)).toEqual(["walk_0"]); // only the frame that changed travels
  expect(params.frames.walk_0.startsWith("iVBOR")).toBe(true); // a PNG
  await expect(page).toHaveURL(/version=030_pixelize/);
  await expect(page.locator("canvas.pixel-edit")).toHaveCount(0); // the new version closed the editor
});

test("Pixel editor: the saved frame differs from the original exactly where the line was drawn (M56)", async ({ page }) => {
  await models2d(page, true);
  const posted = await mockAsset(
    page,
    "mock_line",
    [
      { version: "010_upload", step: "upload", parent: null, outputs: { image: "image.png" } },
      { version: "020_pixelize", step: "pixelize", parent: "010_upload", outputs: { frames: "frames", sheet: "sheet.png", data: "sheet.json" }, metrics: { frames: 1, cell: [16, 16] } },
    ],
    { kind: "2d" },
  );
  await page.route("**/files/mock_line/*/sheet.json", (r) => r.fulfill({ json: { frames: [{ filename: "f", frame: { x: 0, y: 0, w: 16, h: 16 }, duration: 100 }], meta: { frameTags: [] } } }));
  await page.goto("/#/asset/mock_line?version=020_pixelize");
  await page.locator("#edit > summary").click();
  await page.getByRole("button", { name: "Edit the pixels" }).click();
  const c = page.locator("canvas.pixel-edit");
  await expect(c).toHaveAttribute("aria-label", "frame 1 of 1, 16 × 16 px");
  await page.getByLabel("Drawing colour").fill("#12ab34"); // no palette here (a sprites-like sheet): any colour
  await page.getByLabel("Line").check();
  const box = (await c.boundingBox())!;
  const z = box.width / 16;
  await page.mouse.move(box.x + 2 * z + z / 2, box.y + 3 * z + z / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 9 * z + z / 2, box.y + 3 * z + z / 2, { steps: 4 });
  await page.mouse.up();
  await page.getByRole("button", { name: "Save as a new version" }).click();
  await expect.poll(() => posted.length).toBe(1);
  const changed = await page.evaluate(async (b64) => {
    const read = async (src: string) => {
      const im = new Image();
      im.src = src;
      await im.decode();
      const cv = document.createElement("canvas");
      [cv.width, cv.height] = [16, 16];
      const ctx = cv.getContext("2d")!;
      ctx.drawImage(im, 0, 0, 16, 16, 0, 0, 16, 16);
      return ctx.getImageData(0, 0, 16, 16).data;
    };
    const was = await read("/files/mock_line/020_pixelize/sheet.png");
    const now = await read(`data:image/png;base64,${b64}`);
    const out: string[] = [];
    for (let i = 0; i < 256; i++) if ([0, 1, 2, 3].some((k) => was[i * 4 + k] !== now[i * 4 + k] && was[i * 4 + 3] + now[i * 4 + 3] > 0)) out.push(`${i % 16},${Math.floor(i / 16)}`);
    const green = [2, 3, 4, 5, 6, 7, 8, 9].every((x) => now[(3 * 16 + x) * 4] === 0x12 && now[(3 * 16 + x) * 4 + 1] === 0xab && now[(3 * 16 + x) * 4 + 2] === 0x34);
    return { out, green };
  }, posted[0][1].frames.f);
  expect(changed.green).toBe(true);
  expect(changed.out).toEqual(["2,3", "3,3", "4,3", "5,3", "6,3", "7,3", "8,3", "9,3"]);
});

test("Pixel editor: add, duplicate and move frames, time one, tag them as a play-once animation, play it, save (M57)", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await models2d(page, true);
  const posted = await mockAsset(
    page,
    "mock_frames",
    [
      { version: "010_upload", step: "upload", parent: null, outputs: { image: "image.png" } },
      { version: "020_pixelize", step: "pixelize", parent: "010_upload", params: { colors: 2 }, outputs: { frames: "frames", sheet: "sheet.png", data: "sheet.json", palette: "palette.gpl", preview: "preview.png" }, metrics: { frames: 2, cell: [16, 16], colors: 2 } },
    ],
    { kind: "2d" },
  );
  await page.route("**/files/mock_frames/*/sheet.json", (r) =>
    r.fulfill({ json: { frames: [0, 1].map((k) => ({ filename: `walk_${k}`, frame: { x: k * 16, y: 0, w: 16, h: 16 }, duration: 100 })), meta: { frameTags: [] } } }),
  );
  await page.route("**/files/mock_frames/*/palette.gpl", (r) => r.fulfill({ body: "GIMP Palette\nName: m\n#\n255   0   0 red\n  0   0 255 blue\n", contentType: "text/plain" }));
  await page.goto("/#/asset/mock_frames?version=020_pixelize");
  await page.locator("#edit > summary").click();
  await page.getByRole("button", { name: "Edit the pixels" }).click();
  const canvas = page.locator("canvas.pixel-edit");
  const panel = page.locator("aside.panel");
  const strip = page.getByRole("group", { name: "Frames" });
  await expect(canvas).toHaveAttribute("aria-label", "frame 1 of 2, 16 × 16 px");
  await panel.getByRole("button", { name: "Add a frame" }).click(); // a blank one after the shown frame
  await expect(canvas).toHaveAttribute("aria-label", "frame 2 of 3, 16 × 16 px");
  await panel.getByLabel("Frame 2's duration (ms)").fill("250");
  await page.keyboard.press("Enter");
  await expect(strip.getByRole("button", { name: "Frame 2: frame_1, 250 ms" })).toHaveAttribute("aria-pressed", "true");
  const box = (await canvas.boundingBox())!;
  await page.getByLabel("Pencil").check();
  await page.mouse.click(box.x + (box.width / 16) * 2.5, box.y + (box.width / 16) * 2.5); // one pixel on the new frame
  await strip.getByRole("button", { name: "Frame 1: walk_0, 100 ms" }).click();
  await panel.getByRole("button", { name: "Duplicate" }).click();
  await panel.getByRole("button", { name: "Move ▶" }).click();
  await expect(strip.getByRole("button")).toHaveText([/1 · 100 ms/, /2 · 250 ms/, /3 · 100 ms/, /4 · 100 ms/]);
  await expect(strip.getByRole("button", { name: "Frame 3: walk_0_copy_1, 100 ms" })).toHaveAttribute("aria-pressed", "true");
  await panel.getByRole("button", { name: "Add an animation" }).click(); // the first: every frame
  await panel.getByLabel("Animation 1's name").fill("spark");
  await page.keyboard.press("Enter");
  await panel.getByLabel("Loop").uncheck(); // a hit spark plays once
  await expect(strip.getByRole("button", { name: /Frame 4/ })).toContainText("⇥");
  await page.getByLabel("Onion skin").selectOption("1");
  await page.keyboard.press("2"); // the second zoom
  await expect(page.getByRole("button", { name: "4×", exact: true })).toHaveAttribute("aria-pressed", "true");
  await strip.getByRole("button", { name: /Frame 1/ }).click();
  await page.getByRole("button", { name: "Play" }).click();
  await expect(page.getByRole("button", { name: "Stop" })).toBeVisible();
  await expect(canvas).toHaveAttribute("aria-label", "frame 4 of 4, 16 × 16 px"); // played through, once
  await expect(page.getByRole("button", { name: "Play" })).toBeVisible();
  await panel.getByRole("button", { name: "Save as a new version" }).click();
  await expect.poll(() => posted.length).toBe(1);
  const [step, params] = posted[0];
  expect(step).toBe("pixelize");
  expect(params.order).toEqual(["walk_0", "frame_1", "walk_0_copy_1", "walk_1"]);
  expect(params.durations).toMatchObject({ frame_1: 250, walk_0_copy_1: 100 });
  expect(params.tags).toEqual([{ name: "spark", from: 0, to: 3, direction: "forward", repeat: "1" }]);
  expect(Object.keys(params.frames)).toContain("frame_1"); // the new frame's pixel
  expect(params.frames.walk_0).toBeUndefined(); // untouched: its pixels stay the version's
  await expect(page).toHaveURL(/version=030_pixelize/);
  expect(errors).toEqual([]);
});

test("New asset: Draw it yourself makes a blank sheet and opens it in the pixel editor (M57)", async ({ page }) => {
  await models2d(page, true);
  const posted = await mockAsset(page, "mock_draw", [], { kind: "2d" });
  const created: any[] = [];
  await page.route("**/api/assets", (r) =>
    r.request().method() === "POST" ? (created.push(r.request().postDataJSON()), r.fulfill({ json: { id: "mock_draw", name: "spark", created: "", kind: "2d" } })) : r.fallback(),
  );
  await page.route("**/files/mock_draw/*/sheet.json", (r) =>
    r.fulfill({ json: { frames: [0, 1].map((k) => ({ filename: `frame_${k + 1}`, frame: { x: k * 8, y: 0, w: 8, h: 8 }, duration: 100 })), meta: { frameTags: [] } } }),
  );
  await page.route("**/files/mock_draw/*/palette.gpl", (r) => r.fulfill({ body: "GIMP Palette\nName: m\n#\n255 236  39 yellow\n", contentType: "text/plain" }));
  await page.goto("/#/new");
  await page.getByRole("button", { name: "A 2D picture or sprite" }).click();
  await page.getByRole("button", { name: "Draw it yourself" }).click();
  await page.getByLabel("Width (px)").fill("8");
  await page.getByLabel("Height (px)").fill("8");
  await page.getByLabel("Frames").fill("2");
  await expect(page.getByLabel("Colours")).toHaveValue("pico-8");
  await page.getByRole("button", { name: "Start drawing" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(created).toEqual([{ name: "drawing", kind: "2d" }]);
  expect(posted[0]).toEqual(["pixelize", { op: "edit", size: [8, 8], order: ["frame_1", "frame_2"], palette: "pico-8" }]);
  await expect(page).toHaveURL(/version=010_pixelize/);
  await expect(page.locator("canvas.pixel-edit")).toHaveAttribute("aria-label", "frame 1 of 2, 8 × 8 px"); // straight into the editor
  await expect(page.getByRole("button", { name: "Colour #ffec27" })).toBeVisible();
});

test("Generate: the best preset for the card by default, the user's pick kept, a card the 3D models do not support (M46b)", async ({ page }) => {
  await page.route("**/api/models", (r) => r.fulfill({ json: [] }));
  await mockAsset(page, "mock_card", [
    { version: "010_upload", step: "upload", parent: null, outputs: { image: "image.png" } },
    { version: "020_prep", step: "prep", parent: "010_upload", outputs: { image: "image.png", image_rgb: "image_rgb.png" } },
  ]);
  await fakeCard(page, 22.6, []); // an RTX 4090: everything fits, the HD preset is the default
  await page.goto("/#/asset/mock_card?version=020_prep");
  const panel = page.locator(".panel");
  await expect(panel).toContainText("Faithful HD — Recommended for this GPU");
  await page.getByText("Advanced", { exact: true }).click();
  await page.getByRole("radio", { name: /^Faithful Pixal3D: follows/ }).check(); // the user's own pick
  await page.reload();
  await expect(panel.locator("p", { hasText: "Pixal3D: follows the picture closely." }).first()).toContainText("Faithful");
  await expect(panel).not.toContainText("Recommended for this GPU"); // kept over the recommendation
  // an RTX 3090: every preset greyed out with the reason, Generate disabled
  await page.unroute("**/api/setup/machine");
  const why = "not supported: the 3D models need an RTX 40 or 50 series card";
  await fakeCard(page, 22.6, ["faithful-hd", "faithful-hq", "faithful", "fast"].map((n) => [n, 0]) as [string, number][], why);
  await page.reload();
  await expect(panel.locator("p.note", { hasText: why })).toHaveText(why); // nothing else fits: no "Another generator"
  await expect(panel).not.toContainText("Recommended for this GPU");
  await expect(page.getByRole("button", { name: "Generate 3D" })).toBeDisabled();
});

test("Setup: a download for bigger cards says why and is not offered; a 24 GB card gets its Install (M46c)", async ({ page }) => {
  const row = { id: 1, name: "alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1", groups: ["refine-full"], revision: "r", where: "x", installed: false, partial: false, bytes: 0, missing_bytes: 6.7e9 };
  await page.route("**/api/models", (r) => r.fulfill({ json: [row] }));
  await page.route("**/api/environments", (r) => r.fulfill({ json: [{ name: ".venv-ml", state: "ok", groups: ["refine", "refine-full"] }] }));
  const card = (budget: number, big: object[]) =>
    page.route("**/api/setup/machine", (r) => r.fulfill({ json: [{ id: "gpu", level: "ok", label: "a card", detail: "", budget_gb: budget, too_big: big }] }));
  await card(14.7, [{ step: "refine", param: "tile", value: "full", need_gb: 15.6, group: "refine-full" }]); // the 16 GB laptop
  await page.goto("/#/setup");
  const full = page.locator("#cap-refine-full");
  await expect(full).toContainText("Needs a bigger card · about 15.6 GB of video memory, this one allows 14.7 GB");
  await expect(full.getByRole("button", { name: "Install" })).toHaveCount(0);
  await page.unroute("**/api/setup/machine");
  await card(22.6, []); // an RTX 4090
  await page.reload();
  await expect(full.getByRole("button", { name: "Install" })).toBeVisible();
});

test("Generate: a character's cut-out can stand in an A-pose first; an object's has no such button", async ({ page }) => {
  await page.route("**/api/models", (r) => r.fulfill({ json: [] }));
  await fakeCard(page, 14.7, [["faithful-hd", 19.4]]);
  const cut = (kind: string) => [
    { version: "010_brief", step: "brief", parent: null, params: { prompt: "x", category: kind }, outputs: { brief: "brief.yaml" } },
    { version: "020_upload", step: "upload", parent: null, outputs: { image: "image.png" } },
    { version: "030_prep", step: "prep", parent: "020_upload", outputs: { image: "image.png", image_rgb: "image_rgb.png" } },
  ];
  const posted = await mockAsset(page, "mock_hero", cut("character"));
  await page.goto("/#/asset/mock_hero?version=030_prep");
  await page.getByRole("button", { name: "Put it in a standing pose" }).click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted).toEqual([
    ["canon", {}],
    ["prep", {}],
  ]);
  await expect(page).toHaveURL(/version=050_prep/); // the redrawn cut-out
  await expect(page.getByRole("button", { name: "Redraw again" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Put it in a standing pose" })).toHaveCount(0);
  await mockAsset(page, "mock_crate", cut("prop"));
  await page.goto("/#/asset/mock_crate?version=030_prep");
  await expect(page.getByRole("button", { name: "Generate 3D" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Put it in a standing pose" })).toHaveCount(0);
});

test("M62: an import with materials offers the bake in its Prepare panel, like a generated model", async ({ page }) => {
  const shots = process.env.M62_SHOTS; // optional: a folder for the two screenshots compared by hand
  const imp = await mockAsset(page, "mock_m62", [
    { version: "010_upload", step: "upload", parent: null, params: { name: "hero.glb" }, outputs: { model: "source/hero.glb" } },
    { version: "020_import_model", step: "import_model", parent: "010_upload", params: {}, outputs: { mesh: "mesh.glb" }, metrics: { materials: 2, textures: 2, triangles: 1200, skeleton: null, clips: [] } },
  ]);
  await page.goto("/#/asset/mock_m62?version=020_import_model");
  await page.getByText("Simplify geometry (copy)").click();
  await expect(page.getByLabel("Texture size")).toBeVisible();
  await expect(page.locator(".panel")).toContainText("materials baked into one texture set");
  if (shots) await page.screenshot({ path: `${shots}/prepare-imported.png`, animations: "disabled" });
  await page.getByRole("button", { name: "Simplify geometry" }).click();
  await expect.poll(() => imp.map(([s]) => s)).toContain("bake");
  if (shots) {
    await mockAsset(page, "mock_m62g", [
      { version: "010_prep", step: "prep", parent: null, outputs: { image: "image.png" } },
      { version: "020_trellis2", step: "trellis2", parent: "010_prep", outputs: { mesh: "hi.glb", ovoxel: "ovoxel.npz" } },
      { version: "030_clean", step: "clean", parent: "020_trellis2", outputs: { mesh: "mesh.glb" } },
    ]);
    await page.goto("/#/asset/mock_m62g?version=030_clean");
    await page.getByLabel("Texture size").waitFor({ timeout: 3000 }).catch(() => {});
    await page.screenshot({ path: `${shots}/prepare-generated.png`, animations: "disabled" });
  }
});

const redrawAsset = async (page: Page, id: string) => {
  await models2d(page, true);
  const posted = await mockAsset(
    page,
    id,
    [
      { version: "010_upload", step: "upload", parent: null, outputs: { image: "image.png" } },
      { version: "020_upload", step: "upload", parent: null, outputs: { image: "mask.png" } },
      { version: "030_concepts", step: "concepts", parent: null, params: { op: "image", width: 2048, height: 768 }, outputs: { image_0: "image_0.png", image: "image.png" } },
    ],
    { kind: "2d" },
  );
  await page.goto(`/#/asset/${id}?version=030_concepts`);
  await page.locator("#redraw > summary").click();
  return { posted, form: page.locator("#redraw") };
};

test("A 2D picture can be upscaled: the size and the factor (M61)", async ({ page }) => {
  const { posted, form } = await redrawAsset(page, "mock_up2d_m61");
  await expect(form).toContainText("8192 × 3072 px"); // 4× a side
  await form.getByRole("button", { name: "Make it bigger" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["redraw", { op: "upscale", key: "image_0", factor: 4 }]);
});

test("A 2D picture can be redrawn with a mask kept as it is (M61)", async ({ page }) => {
  const { posted, form } = await redrawAsset(page, "mock_rd2d_m61");
  await form.getByLabel("What to do").selectOption("redraw");
  await expect(form.getByRole("button", { name: "Redraw the picture" })).toBeDisabled(); // a description is needed
  await form.getByLabel("Description").fill("a painted mural");
  await form.getByLabel("Keep as it is (a mask)").selectOption("020_upload");
  await expect(form).toContainText("3 windows"); // 2048 × 768 in windows of 1024, 256 overlapping
  await form.getByRole("button", { name: "Redraw the picture" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(["redraw", { op: "redraw", key: "image_0", factor: 1, prompt: "a painted mural", strength: 0.35, protect: "020_upload" }]);
});
