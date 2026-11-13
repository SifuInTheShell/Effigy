// M71: the workspace layout: three thin rows, the tool strip, the Blender group, the developer switch.
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const GLB = readFileSync(new URL("../../tests/fixtures/two_tris.glb", import.meta.url));
const ANIM = readFileSync(new URL("../../tests/fixtures/anim.glb", import.meta.url));
const SHOTS = process.env.M71_SHOTS; // optional: a folder for the screenshots

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.route("**/api/blender/status", (r) => r.fulfill({ json: { link: null } }));
  await page.route("**/api/setup/blender", (r) => r.fulfill({ json: { start: false, exe: null, found: null } }));
});

const upload = async (request: any, name: string) => {
  const asset = await (await request.post("/api/assets", { data: { name } })).json();
  expect((await request.put(`/api/assets/${asset.id}/upload/two_tris.glb`, { data: GLB })).ok()).toBe(true);
  return asset as { id: string };
};
const meta = (version: string, step: string, parent: string | null, outputs: Record<string, string>) => ({
  version, step, parent, outputs, params: {}, created: "", metrics: {}, provenance: "step", restricted: false,
});
// a skinned column (version 020) and the animated one (030) made from it
async function rigged(page: Page) {
  const versions = [
    meta("010_upload", "upload", null, { mesh: "mesh.glb" }),
    meta("020_rig_skin", "rig_skin", "010_upload", { rigged: "rigged.glb", skeleton: "skeleton.json", qa: "qa.json" }),
    meta("030_anim", "anim", "020_rig_skin", { rigged_anim: "rigged_anim.glb", clips: "clips.json", skeleton: "skeleton.json" }),
  ];
  await page.route("**/api/assets/m71_rig", (r) => r.fulfill({ json: { asset: { id: "m71_rig", name: "column", created: "" }, versions } }));
  await page.route("**/files/m71_rig/*/*.glb", (r) => r.fulfill({ body: ANIM, contentType: "model/gltf-binary" }));
  await page.route("**/files/m71_rig/*/clips.json", (r) => r.fulfill({ json: { clips: [{ name: "Idle", frames: 12, seconds: 0.5 }], fps: 24, missing: [] } }));
  await page.route("**/files/m71_rig/*/skeleton.json", (r) => r.fulfill({ json: { template: "mixamo52", joints: [] } }));
  await page.route("**/files/m71_rig/*/qa.json", (r) => r.fulfill({ json: { items: {} } }));
}

test("The chrome above the view is at most 112 px at 1280×720, and a tool that does not fit says why", async ({ page, request }) => {
  const a = await upload(request, "m71-chrome");
  await page.goto(`/#/asset/${a.id}`);
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  const top = await page.locator(".preview").evaluate((e) => e.getBoundingClientRect().top);
  expect(top).toBeLessThanOrEqual(112);
  console.log("chrome height", top);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/model.png` });
  await page.getByRole("button", { name: "Character" }).click();
  const edit = page.getByRole("tab", { name: "Edit animation" });
  await expect(edit).toBeEnabled(); // opening the requirement does not run the tool
  await expect(edit).toHaveAttribute("data-unavailable", "true");
  await expect(edit).toHaveAttribute("title", "needs an animated model");
  await expect(page.getByRole("tab", { name: "Fit skeleton" })).toBeEnabled();
});

test("The strip follows the opened version: a skinned model lands on Ready-made clips, an animated one on Edit animation", async ({ page }) => {
  await rigged(page);
  await page.goto("/#/asset/m71_rig?version=020_rig_skin");
  await expect(page.locator(".stages [aria-current=step]")).toContainText("Character");
  await expect(page.getByRole("tab", { name: "Ready-made clips" })).toHaveAttribute("aria-selected", "true");
  await page.goto("/#/asset/m71_rig?version=030_anim");
  await page.waitForFunction(() => (window as any).__viewer?.loaded === true);
  await expect(page.locator(".stages [aria-current=step]")).toContainText("Character");
  await expect(page.getByRole("tab", { name: "Edit animation" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Ready-made clips" })).toHaveAttribute("data-unavailable", "true");
  // the asset has a skinned model already: the hint points there, never at fitting a skeleton
  await page.getByRole("tab", { name: "Ready-made clips" }).click();
  const requirement = page.getByRole("region", { name: "Tool requirements" });
  await expect(requirement).toContainText("is one, so no skeleton needs fitting");
  await expect(requirement).not.toContainText("use Fit skeleton");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/animated.png` });
});

test("Unavailable tools explain their requirements and open an existing suitable version without running a job", async ({ page }) => {
  await rigged(page);
  let runs = 0;
  await page.route("**/api/assets/m71_rig/steps/*", (r) => { runs++; return r.abort(); });
  await page.goto("/#/asset/m71_rig?version=020_rig_skin");
  const edit = page.getByRole("tab", { name: "Edit animation", exact: true });
  await edit.focus();
  await page.keyboard.press("Enter");
  await expect(edit).toHaveAttribute("aria-selected", "true");
  const requirement = page.getByRole("region", { name: "Tool requirements" });
  await expect(requirement).toContainText("Needs an animated model");
  await expect(requirement).toContainText("Add an animation");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/tool-requirements.png` });
  await requirement.getByRole("button", { name: /Open Animated model 030/ }).click();
  await expect(page).toHaveURL(/version=030_anim$/);
  await expect(page.getByRole("tab", { name: "Edit animation", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(requirement).toHaveCount(0);
  expect(runs).toBe(0);
});

test("History keeps version names readable and opens earlier and later versions with mouse and keyboard", async ({ page }) => {
  await rigged(page);
  await page.goto("/#/asset/m71_rig?version=030_anim");
  await page.getByRole("button", { name: /^History/ }).click();
  for (const width of [1280, 900]) {
    await page.setViewportSize({ width, height: 720 });
    const rows = page.locator("#versions li");
    await expect(rows).toHaveCount(3);
    for (const row of await rows.all()) {
      expect(await row.locator(".vlabel").evaluate((e) => e.getBoundingClientRect().width)).toBeGreaterThan(90);
    }
    await rows.nth(1).locator("button").first().click();
    await expect(page).toHaveURL(/version=020_rig_skin$/);
    await expect(rows.nth(1).locator("button").first()).toHaveAttribute("aria-current", "true");
    await expect(page.getByRole("tab", { name: "Ready-made clips", exact: true })).toHaveAttribute("aria-selected", "true");
    await rows.first().locator("button").first().click();
    await expect(page).toHaveURL(/version=010_upload$/);
    await rows.last().locator("button").first().focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/version=030_anim$/);
    await expect(rows.last().locator("button").first()).toHaveAttribute("aria-current", "true");
    await expect(page.getByRole("tab", { name: "Edit animation", exact: true })).toHaveAttribute("aria-selected", "true");
  }
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/history-readable.png` });
});

test("Run any step is a developer option: hidden until Setup turns it on", async ({ page, request }) => {
  const a = await upload(request, "m71-dev");
  // the switch is a server-wide setting: this page gets its own, so tests running beside it are not affected
  let on = false;
  await page.route("**/api/setup/developer", (r) => {
    if (r.request().method() === "POST") on = r.request().postDataJSON().on;
    return r.fulfill({ json: { on } });
  });
  await page.goto(`/#/asset/${a.id}`);
  await page.locator(".ws-name h1").waitFor(); // a fresh upload can take a moment to open
  await expect(page.getByRole("tab", { name: "Check" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "⋯ Run any step" })).toHaveCount(0);
  await page.goto("/#/setup");
  await page.getByLabel("Show Run any step in the workspace").click();
  await expect.poll(() => on).toBe(true);
  await page.goto(`/#/asset/${a.id}`);
  await page.reload();
  await page.getByRole("tab", { name: "⋯ Run any step" }).click();
  await expect(page.getByLabel("step", { exact: true })).toBeVisible();
});

test("The Blender group is on every asset page, with its reasons; each History row can open its version", async ({ page, request }) => {
  const a = await upload(request, "m71-blender");
  await page.goto(`/#/asset/${a.id}`);
  await page.locator(".ws-name h1").waitFor(); // a fresh upload can take a moment to open
  const group = page.getByRole("group", { name: "Blender" });
  await expect(group.getByRole("button", { name: "○ Blender" })).toBeVisible();
  await expect(group.getByRole("button", { name: /^Open 001/ })).toBeDisabled();
  await expect(group.getByRole("button", { name: /^Open 001/ })).toHaveAttribute("title", "Blender is not connected: see Setup");
  await expect(group.getByRole("button", { name: "Save back" })).toBeDisabled();
  await page.getByRole("button", { name: /^History/ }).click();
  await expect(page.locator("#versions li").first().getByRole("button", { name: /in Blender$/ })).toBeDisabled();
  // connected: the group and the row open the version
  await page.route("**/api/blender/status", (r) => r.fulfill({ json: { link: "pro" } }));
  const opened: string[] = [];
  await page.route("**/blender/open", (r) => (opened.push(r.request().postDataJSON().version), r.fulfill({ json: { ok: true, version: "001_upload", objects: [] } })));
  await group.getByRole("button", { name: "○ Blender" }).click(); // the dot checks again
  await expect(group.getByRole("button", { name: "● Blender" })).toBeVisible();
  await group.getByRole("button", { name: /^Open 001/ }).click();
  await page.locator("#versions li").first().getByRole("button", { name: /in Blender$/ }).click();
  await expect.poll(() => opened).toEqual(["001_upload", "001_upload"]);
});

test("A picture asset has the Blender group too, saying it has no 3D model", async ({ page, request }) => {
  const PNG = readFileSync(new URL("../../tests/fixtures/golden/lantern.png", import.meta.url));
  const asset = await (await request.post("/api/assets", { data: { name: "m71-picture" } })).json();
  await request.put(`/api/assets/${asset.id}/upload/lantern.png`, { data: PNG });
  await page.goto(`/#/asset/${asset.id}`);
  await page.locator(".ws-name h1").waitFor(); // a fresh upload can take a moment to open
  const open = page.getByRole("group", { name: "Blender" }).getByRole("button", { name: /^Open 001/ });
  await expect(open).toBeDisabled();
  await expect(open).toHaveAttribute("title", "this version has no 3D model");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/picture.png` });
});
