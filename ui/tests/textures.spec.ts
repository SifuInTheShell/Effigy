import { expect, test, type APIRequestContext } from "@playwright/test";
import { readFileSync } from "node:fs";

async function step(
  request: APIRequestContext,
  asset: string,
  parent: string | null,
  params: object,
) {
  const response = await request.post(`/api/assets/${asset}/steps/textures`, {
    data: { parent, params },
  });
  expect(response.ok()).toBe(true);
  const { job_id } = await response.json();
  let result: any;
  await expect
    .poll(
      async () => {
        result = await (await request.get(`/api/jobs/${job_id}`)).json();
        return result.status;
      },
      { timeout: 40_000, message: "texture job finishes" },
    )
    .toMatch(/^(done|failed|cancelled)$/);
  expect(result.status, JSON.stringify(result.error)).toBe("done");
  return result.version as string;
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/blender/status", (r) =>
    r.fulfill({ json: { link: null } }),
  );
});

test("Painter: brushes, eraser, picker, zoom/pan, history and lossless save", async ({
  page,
  request,
}) => {
  const asset = await (
    await request.post("/api/assets", {
      data: { name: "Painter tools validation", kind: "texture" },
    })
  ).json();
  const version = await step(request, asset.id, null, {
    op: "create",
    size: 256,
    color: "#ffffff",
  });
  await page.goto(`/#/asset/${asset.id}?version=${version}`);
  await page.getByRole("button", { name: "Paint map", exact: true }).click();
  const c = page.getByLabel("Texture painting canvas");
  await expect
    .poll(() => c.evaluate((el: HTMLCanvasElement) => el.width))
    .toBe(256);
  const pixel = (x: number, y: number) =>
    c.evaluate(
      (el: HTMLCanvasElement, p) => [
        ...el.getContext("2d")!.getImageData(p[0], p[1], 1, 1).data,
      ],
      [x, y],
    );
  const clickPixel = async (x: number, y: number) => {
    const b = (await c.boundingBox())!;
    await page.mouse.click(
      b.x + (x / 256) * b.width,
      b.y + (y / 256) * b.height,
    );
  };
  const range = async (name: string, value: number) => {
    await page
      .getByRole("slider", { name: new RegExp(`^${name}`) })
      .evaluate((el: HTMLInputElement, value) => {
        Object.getOwnPropertyDescriptor(
          HTMLInputElement.prototype,
          "value",
        )!.set!.call(el, String(value));
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }, value);
  };
  const save = page.getByRole("button", { name: "Save painted map" });
  await page
    .getByRole("button", { name: "Expand painter", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Restore painter" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "100%", exact: true }).click();
  await page.getByLabel("Brush colour", { exact: true }).fill("#ff0000");
  await range("Brush size", 32);
  await page.getByLabel("Brush shape").selectOption("square");
  await clickPixel(128, 128);
  expect(await pixel(128, 128)).toEqual([255, 0, 0, 255]);
  expect(await pixel(115, 115)).toEqual([255, 0, 0, 255]);
  await page.getByRole("button", { name: "Undo stroke" }).click();
  expect(await pixel(128, 128)).toEqual([255, 255, 255, 255]);
  await expect(save).toBeDisabled();
  await page.getByRole("button", { name: "Redo stroke" }).click();
  expect(await pixel(128, 128)).toEqual([255, 0, 0, 255]);
  await page.getByRole("button", { name: "Pick colour", exact: true }).click();
  await clickPixel(128, 128);
  await expect(page.getByLabel("Brush colour", { exact: true })).toHaveValue(
    "#ff0000",
  );
  await page.getByRole("button", { name: "Eraser", exact: true }).click();
  await clickPixel(128, 128);
  expect((await pixel(128, 128))[3]).toBe(0);
  await page
    .getByLabel("Painting viewport", { exact: true })
    .press("Control+z");
  expect(await pixel(128, 128)).toEqual([255, 0, 0, 255]);
  await page.getByRole("button", { name: "Brush", exact: true }).click();
  await page.getByLabel("Brush shape").selectOption("round");
  await range("Hardness", 0);
  await page.getByLabel("Brush colour", { exact: true }).fill("#000000");
  await clickPixel(70, 128);
  const centre = (await pixel(70, 128))[0],
    edge = (await pixel(83, 128))[0];
  expect(centre).toBeLessThan(30);
  expect(edge).toBeGreaterThan(150);
  expect(edge).toBeLessThan(255);
  await range("Hardness", 1);
  await range("Opacity", 0.5);
  await clickPixel(190, 128);
  const translucent = (await pixel(190, 128))[0];
  expect(translucent).toBeGreaterThan(120);
  expect(translucent).toBeLessThan(135);
  await range("Opacity", 1);
  // Wheel zoom preserves the texel beneath the pointer.
  let b = (await c.boundingBox())!;
  const cursor = { x: b.x + b.width * 0.5, y: b.y + b.height * 0.5 };
  await page.mouse.move(cursor.x, cursor.y);
  await page.mouse.wheel(0, -1000);
  await expect(page.getByLabel("Zoom level")).not.toHaveText("100%");
  b = (await c.boundingBox())!;
  expect(Math.abs(((cursor.x - b.x) * 256) / b.width - 128)).toBeLessThan(2);
  expect(Math.abs(((cursor.y - b.y) * 256) / b.height - 128)).toBeLessThan(2);
  await range("Hardness", 1);
  await page.getByLabel("Brush colour", { exact: true }).fill("#0000ff");
  await clickPixel(128, 128);
  expect(await pixel(128, 128)).toEqual([0, 0, 255, 255]);
  const before = await c.evaluate((el: HTMLCanvasElement) => el.toDataURL());
  await page.getByRole("button", { name: "Hand", exact: true }).click();
  await page.mouse.move(cursor.x, cursor.y);
  await page.mouse.down();
  await page.mouse.move(cursor.x - 40, cursor.y - 30);
  await page.mouse.up();
  expect(await c.evaluate((el: HTMLCanvasElement) => el.toDataURL())).toBe(
    before,
  );
  const panned = (await c.boundingBox())!;
  expect(panned.x).toBeLessThan(b.x - 20);
  await page.getByRole("button", { name: "Fit map" }).click();
  // Failed upload retains the edited canvas and keeps Save available.
  await page.route("**/upload/*", (route) =>
    route.fulfill({ status: 500, json: { detail: "Test upload failure" } }),
  );
  await save.click();
  await expect(page.getByRole("alert")).toContainText("500");
  await expect(save).toBeEnabled();
  expect(await c.evaluate((el: HTMLCanvasElement) => el.toDataURL())).toBe(
    before,
  );
  await page.unroute("**/upload/*");
  await save.click();
  await expect
    .poll(async () =>
      (
        await page.getByLabel("Texture version", { exact: true }).inputValue()
      ).slice(0, 3),
    )
    .toBe("003");
  await expect
    .poll(() => c.evaluate((el: HTMLCanvasElement) => [el.width, el.height]))
    .toEqual([256, 256]);
  expect(await pixel(128, 128)).toEqual([0, 0, 255, 255]);
  await page
    .getByRole("button", { name: "Expand painter", exact: true })
    .click();
  await expect(
    page.getByLabel("Painting viewport", { exact: true }),
  ).toBeVisible();
  await expect
    .poll(
      async () =>
        (await page
          .getByLabel("Painting viewport", { exact: true })
          .boundingBox())!.height,
    )
    .toBeGreaterThan(250);
  expect(
    await page
      .getByLabel("Painting viewport", { exact: true })
      .evaluate((el) => {
        const b = el.getBoundingClientRect();
        return el.contains(
          document.elementFromPoint(
            b.left + b.width * 0.8,
            b.top + b.height * 0.4,
          ),
        );
      }),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/painter-tools.png",
    fullPage: true,
  });
});

test("Library: batch export of a material uses a texture package", async ({
  page,
  request,
}) => {
  const asset = await (
    await request.post("/api/assets", {
      data: { name: "Batch material validation", kind: "texture" },
    })
  ).json();
  await step(request, asset.id, null, { op: "create", size: 64 });
  await page.route("**/api/export/copy", async (route) => {
    const body = route.request().postDataJSON();
    await route.fulfill({
      json: {
        copied: body.items.map((item: any) => ({
          ...item,
          dir: "D:/test-export/material",
          files: ["textures.zip"],
        })),
        skipped: [],
      },
    });
  });
  await page.goto("/#/library");
  await page.getByRole("button", { name: /^Textures \/ Materials/ }).click();
  await page
    .getByRole("button", { name: "Select assets", exact: true })
    .click();
  await page
    .getByLabel("Select Batch material validation", { exact: true })
    .check();
  await page
    .getByRole("button", { name: "Export selected (1)", exact: true })
    .click();
  await expect(
    page.getByRole("list", { name: "What will be exported" }),
  ).not.toContainText("skipped");
  await page.getByLabel("Folder", { exact: true }).fill("D:/test-export");
  await page
    .getByRole("button", { name: "Export and copy", exact: true })
    .click();
  await expect(
    page.getByRole("list", { name: "Result", exact: true }),
  ).toContainText("copied 1 file", { timeout: 40_000 });
  const saved = await (await request.get(`/api/assets/${asset.id}`)).json();
  expect(saved.versions.at(-1).params.profile).toBe("textures");
});

test("Textures: workflow tabs and surface controls fit a laptop above activity", async ({
  page,
  request,
}) => {
  const asset = await (
    await request.post("/api/assets", {
      data: { name: "Layout material", kind: "texture" },
    })
  ).json();
  const version = await step(request, asset.id, null, {
    op: "create",
    size: 64,
  });
  await page.goto(`/#/asset/${asset.id}?version=${version}`);
  await expect(page.getByLabel("Moving light")).toBeVisible();
  for (const width of [1280, 900]) {
    await page.setViewportSize({ width, height: 720 });
    const rows = await page
      .getByRole("group", { name: "Texture view", exact: true })
      .getByRole("button")
      .evaluateAll((buttons) =>
        buttons.map((button) => button.getBoundingClientRect().top),
      );
    expect(Math.max(...rows) - Math.min(...rows)).toBeLessThan(1);
    const tabRows = await page
      .getByRole("tablist", { name: "Texture workflow" })
      .getByRole("tab")
      .evaluateAll((buttons) =>
        buttons.map((button) => button.getBoundingClientRect().top),
      );
    expect(Math.max(...tabRows) - Math.min(...tabRows)).toBeLessThan(1);
    const controls = await page
      .locator(".texture-surface .toolbar")
      .boundingBox();
    const activity = await page
      .getByRole("contentinfo", { name: "Activity" })
      .boundingBox();
    await page.screenshot({ path: `test-results/texture-layout-${width}.png` });
    expect(
      controls!.y + controls!.height,
      JSON.stringify({
        width,
        controls,
        activity,
        canvas: await page.locator(".texture-surface > canvas").boundingBox(),
      }),
    ).toBeLessThanOrEqual(activity!.y);
    expect(controls!.y).toBeGreaterThan(0);
  }
  await page
    .getByRole("contentinfo", { name: "Activity" })
    .evaluate((footer) => (footer.style.minHeight = "100px"));
  await page.locator(".texture-active").evaluate((slot) => {
    slot.style.height = "36px";
  });
  await expect
    .poll(async () => {
      const controls = (await page
        .locator(".texture-surface .toolbar")
        .boundingBox())!;
      const activity = (await page
        .getByRole("contentinfo", { name: "Activity" })
        .boundingBox())!;
      return controls.y + controls.height <= activity.y;
    })
    .toBe(true);
  await page.getByRole("tab", { name: "Upscale", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(
    page.getByRole("tab", { name: "Enhance", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tabpanel")).toHaveAccessibleName("Enhance");
  await expect(
    page.getByRole("heading", { name: "Enhance material details" }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/texture-layout-laptop.png",
    fullPage: true,
  });
});

test("Library: material types, direct import and Unity packed channels", async ({
  page,
  request,
}) => {
  const source = await (
    await request.post("/api/assets", {
      data: { name: "Packed source", kind: "texture" },
    })
  ).json();
  const version = await step(request, source.id, null, {
    op: "create",
    size: 64,
    color: "#506080c8",
  });
  const png = await (
    await request.get(`/files/${source.id}/${version}/basecolor.png`)
  ).body();
  await page.goto("/#/library");
  await page.getByRole("button", { name: /^Textures \/ Materials/ }).click();
  await page
    .getByRole("link", { name: "Import material", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Textures / Materials", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("Material name").fill("Imported Unity material");
  await page.getByLabel("Packed map format").selectOption("unity_mask");
  await page
    .getByLabel("Packed map", { exact: true })
    .setInputFiles({ name: "mask.png", mimeType: "image/png", buffer: png });
  await page
    .getByRole("button", { name: "Import material set", exact: true })
    .click();
  await expect(page.locator(".texture-foot")).toContainText(
    "Standalone material",
    { timeout: 40_000 },
  );
  await expect(
    page.getByRole("button", { name: /^Roughness 64/ }),
  ).toBeVisible();
  const id = page.url().split("/asset/")[1].split("?")[0];
  const selected = await page
    .getByLabel("Texture version", { exact: true })
    .inputValue();
  const info = await (
    await request.get(`/api/assets/${id}/versions/${selected}/textures`)
  ).json();
  expect(Object.keys(info.materials[0].maps).sort()).toEqual([
    "ao",
    "metallic",
    "roughness",
  ]);
  await page.getByRole("link", { name: "Library", exact: true }).last().click();
  await page.getByRole("button", { name: /^Textures \/ Materials/ }).click();
  await expect(page.locator(`a.card[href="#/asset/${id}"]`)).toBeVisible();
  await page.getByRole("button", { name: /^Models/ }).click();
  await expect(page.locator(`a.card[href="#/asset/${id}"]`)).toHaveCount(0);
  await page.getByRole("link", { name: "New material", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Texture / Material", exact: true }),
  ).toBeVisible();
});

test("Textures: generation uses the image worker then packages a standalone material", async ({
  page,
  request,
}) => {
  await page.route("**/api/models", (route) => route.fulfill({ json: [] }));
  const generated: any[] = [];
  await page.route("**/api/assets/*/steps/concepts", async (route) => {
    const body = route.request().postDataJSON();
    generated.push(body.params);
    const asset = route.request().url().split("/assets/")[1].split("/")[0];
    // Replace neural inference with a real CPU-created image; keep the real job and packaging flow.
    const response = await request.post(`/api/assets/${asset}/steps/textures`, {
      data: {
        parent: body.parent,
        params: { op: "create", size: 256, color: "#aa9988", tileable: true },
      },
    });
    await route.fulfill({ status: 202, json: await response.json() });
  });
  await page.goto("/#/new");
  await page
    .getByRole("button", { name: "Texture / Material", exact: true })
    .click();
  await page.getByLabel("Material name").fill("Generated material validation");
  await page.getByLabel("Description", { exact: true }).fill("woven linen");
  await page.getByLabel("Starting width").selectOption("256");
  await page
    .getByRole("button", { name: "Generate texture", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: /^Base colour 256/ }),
  ).toBeVisible({ timeout: 40_000 });
  await expect
    .poll(async () =>
      page.getByLabel("Texture version", { exact: true }).inputValue(),
    )
    .toBe("002_textures");
  expect(generated).toEqual([
    { op: "tile", prompt: "woven linen", count: 1, size: 256 },
  ]);
  await expect(page.locator(".texture-foot")).toContainText(
    "Standalone material",
  );
});

test("Textures: create a standalone material, paint, resize, estimate surface maps and export", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/#/new");
  await page
    .getByRole("button", { name: "Texture / Material", exact: true })
    .click();
  await page.getByRole("button", { name: "Start with a colour" }).click();
  await page.getByLabel("Material name").fill("Texture browser validation");
  await page.getByLabel("Starting width").selectOption("256");
  await page
    .getByRole("button", { name: "Create texture", exact: true })
    .click();
  await expect(page.locator(".texture-workspace")).toBeVisible({
    timeout: 40_000,
  });
  await expect(page.locator(".texture-foot")).toContainText(
    "Standalone material",
  );
  await page.getByRole("button", { name: "Map", exact: true }).click();
  await expect(page.getByAltText("Base colour map")).toBeVisible();
  await page.getByRole("button", { name: "Paint map", exact: true }).click();
  const canvas = page.getByLabel("Texture painting canvas");
  await expect
    .poll(() => canvas.evaluate((c: HTMLCanvasElement) => c.width))
    .toBe(256);
  const bounds = (await canvas.boundingBox())!;
  await page.mouse.move(
    bounds.x + bounds.width * 0.3,
    bounds.y + bounds.height * 0.4,
  );
  await page.mouse.down();
  await page.mouse.move(
    bounds.x + bounds.width * 0.7,
    bounds.y + bounds.height * 0.6,
    { steps: 8 },
  );
  await page.mouse.up();
  const saveColour = await page
    .getByRole("button", { name: "Save painted map" })
    .evaluate((button) => {
      const reference = document.createElement("div");
      reference.style.backgroundColor = "var(--accent)";
      document.body.append(reference);
      const expected = getComputedStyle(reference).backgroundColor;
      reference.remove();
      return { actual: getComputedStyle(button).backgroundColor, expected };
    });
  expect(saveColour.actual).toBe(saveColour.expected);
  await page.getByRole("button", { name: "Save painted map" }).click();
  await expect(page.getByLabel("Texture version", { exact: true })).toHaveValue(
    /_textures$/,
    { timeout: 40_000 },
  );
  await expect
    .poll(async () =>
      (
        await page.getByLabel("Texture version", { exact: true }).inputValue()
      ).slice(0, 3),
    )
    .toBe("003");
  await page.getByRole("tab", { name: "Upscale", exact: true }).click();
  await page.getByLabel("Output width").selectOption("512");
  await page.getByRole("button", { name: "Resize without AI" }).click();
  await expect(page.locator(".texture-foot")).toContainText("512 × 512", {
    timeout: 40_000,
  });
  await page.getByRole("tab", { name: "Create new", exact: true }).click();
  await page.getByText("Estimate surface maps", { exact: true }).click();
  await page
    .getByRole("button", { name: "Create missing surface maps" })
    .click();
  await expect(page.getByRole("button", { name: /^Normal 512/ })).toBeVisible({
    timeout: 40_000,
  });
  await page.getByRole("button", { name: "Package maps for export" }).click();
  await expect(page.getByRole("link", { name: "textures.zip" })).toBeVisible({
    timeout: 40_000,
  });
  const archive = await request.get(
    (await page
      .getByRole("link", { name: "textures.zip" })
      .getAttribute("href"))!,
  );
  expect(archive.ok()).toBe(true);
  expect((await archive.body()).subarray(0, 2).toString()).toBe("PK");
  expect(errors).toEqual([]);
  await page.screenshot({
    path: "test-results/texture-standalone.png",
    fullPage: true,
  });
});

test("Textures: import map set and apply a pinned Library version to an animated GLB", async ({
  page,
  request,
}) => {
  const source = await (
    await request.post("/api/assets", {
      data: { name: "Reusable test material", kind: "texture" },
    })
  ).json();
  const materialVersion = await step(request, source.id, null, {
    op: "create",
    size: 64,
    color: "#a53c22",
    tileable: true,
  });
  // Existing animated fixture, with a material slot. Its original animation bytes stay intact.
  const glb = readFileSync(
    new URL("../../tests/fixtures/anim.glb", import.meta.url),
  );
  const length = glb.readUInt32LE(12),
    doc = JSON.parse(glb.subarray(20, 20 + length).toString());
  const binary = glb.subarray(20 + length + 8);
  doc.materials = [
    {
      name: "Character cloth",
      pbrMetallicRoughness: { baseColorFactor: [0.5, 0.5, 0.5, 1] },
    },
  ];
  doc.meshes[0].primitives[0].material = 0;
  let json = Buffer.from(JSON.stringify(doc));
  json = Buffer.concat([json, Buffer.alloc((4 - (json.length % 4)) % 4, 32)]);
  const packed = Buffer.alloc(12 + 8 + json.length + 8 + binary.length);
  packed.write("glTF");
  packed.writeUInt32LE(2, 4);
  packed.writeUInt32LE(packed.length, 8);
  packed.writeUInt32LE(json.length, 12);
  packed.write("JSON", 16);
  json.copy(packed, 20);
  packed.writeUInt32LE(binary.length, 20 + json.length);
  packed.write("BIN\0", 24 + json.length);
  binary.copy(packed, 28 + json.length);
  const asset = await (
    await request.post("/api/assets", {
      data: { name: "Texture animated model" },
    })
  ).json();
  const up = await (
    await request.put(`/api/assets/${asset.id}/upload/character.glb`, {
      data: packed,
    })
  ).json();
  await page.goto(`/#/asset/${asset.id}?version=${up.version}`);
  await page.getByRole("button", { name: /^Textures/ }).click();
  await expect(
    page.getByRole("button", { name: "Back to model", exact: true }),
  ).toHaveClass(/primary/);
  await expect(page.locator(".texture-workspace")).toBeVisible();
  await expect(page.locator(".texture-foot")).toContainText(
    "Animation preserved",
  );
  await page.getByRole("tab", { name: "Library", exact: true }).click();
  await page.getByLabel("Material from Library").selectOption(source.id);
  await expect(page.getByLabel("Material version")).toHaveValue(
    materialVersion,
  );
  await page
    .getByRole("button", { name: "Apply material as new version" })
    .click();
  await expect(
    page.getByRole("button", { name: /^Base colour 64/ }),
  ).toBeVisible({ timeout: 40_000 });
  await expect(page.locator(".texture-foot")).toContainText(
    "Animation preserved",
  );
  const selectedVersion = await page
    .getByLabel("Texture version", { exact: true })
    .inputValue();
  const result = await (await request.get(`/api/assets/${asset.id}`)).json();
  const edited = result.versions.find(
    (v: any) => v.version === selectedVersion,
  );
  const model = await request.get(
    `/files/${asset.id}/${selectedVersion}/${edited.outputs.mesh}`,
  );
  const bytes = await model.body(),
    jsonLength = bytes.readUInt32LE(12);
  const finalDoc = JSON.parse(bytes.subarray(20, 20 + jsonLength).toString());
  expect(finalDoc.animations).toEqual(doc.animations);
  expect(finalDoc.skins).toEqual(doc.skins);
  expect(finalDoc.meshes).toEqual(doc.meshes);
  // The texture task remains reachable from M71's new workspace, including after a saved edit.
  await page.getByRole("button", { name: "Back to model", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "Tools", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Check", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.locator(".stages").getByRole("button", { name: /Textures/ }).click();
  await expect(page.locator(".texture-workspace")).toBeVisible();
  await expect(page.getByLabel("Texture version", { exact: true })).toHaveValue(selectedVersion);
  await page.getByRole("tab", { name: "Library", exact: true }).click();
  await page.getByRole("button", { name: "Save this set to Library" }).click();
  await expect(page.locator(".texture-foot")).toContainText(
    "Standalone material",
    { timeout: 40_000 },
  );
  await page.screenshot({
    path: "test-results/texture-model-application.png",
    fullPage: true,
  });
  await page.goto("/#/import?kind=texture");
  await page
    .getByRole("button", { name: "From model GLB", exact: true })
    .click();
  await page.getByLabel("Material name").fill("Material imported from GLB");
  await page.getByLabel("Textured GLB file").setInputFiles({
    name: "character-material.glb",
    mimeType: "model/gltf-binary",
    buffer: bytes,
  });
  await expect(page.getByLabel("Material to import")).toContainText(
    "Character cloth",
  );
  await page
    .getByRole("button", { name: "Import material set", exact: true })
    .click();
  await expect(page.locator(".texture-foot")).toContainText(
    "Standalone material",
    { timeout: 40_000 },
  );
  await expect(
    page.getByRole("button", { name: /^Base colour 64/ }),
  ).toBeVisible();
});
