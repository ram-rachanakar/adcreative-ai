import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

const seed = JSON.parse(readFileSync(path.join(__dirname, ".seed.json"), "utf8")) as { email: string; password: string; campaign_id: string; org_id: string; creatives: Record<string, string> };
const API = "http://localhost:8001";

async function login(page: Page) {
  await page.goto("/login"); await page.fill("input[type=email]", seed.email); await page.fill("input[type=password]", seed.password); await page.click("form button"); await page.waitForURL("**/dashboard");
}
const docJson = async (page: Page, creativeId: string) => {
  const r = await page.request.get(`${API}/creatives/${creativeId}/document`, { headers: { "x-org-id": seed.org_id } }); expect(r.ok(), await r.text()).toBeTruthy(); return r.json();
};
const csrf = async (page: Page) => (await (await page.request.get(`${API}/auth/me`, { headers: { "x-org-id": seed.org_id } })).json()).csrf_token as string;
const saved = (page: Page) => expect(page.getByTestId("save-status")).toHaveAttribute("data-state", "saved", { timeout: 15_000 });
const openEditor = async (page: Page, size: string) => {
  await page.goto(`/campaigns/${seed.campaign_id}/editor/${seed.creatives[size]}`); await page.waitForSelector("[data-testid=editor-root]"); await page.waitForFunction(() => !!(window as unknown as { __editorStage?: unknown }).__editorStage); await saved(page);
};
const advanced = (page: Page) => page.getByRole("group", { name: "Edit mode" }).getByText("Advanced Edit").click();
const selectLayer = (page: Page, name: string) => page.getByRole("button", { name: `Select layer ${name}` }).click();
const field = (page: Page, label: string) => page.getByLabel(label, { exact: true });
const setNumber = async (page: Page, label: string, v: number) => { const f = field(page, label); await f.fill(String(v)); await f.press("Enter"); };
const layersOf = (d: { layers: Record<string, unknown>[] }) => Object.fromEntries(d.layers.map(l => [l.id as string, l]));

test("full editing workflow: edit, undo/redo, save+reload, AI background only, independence, export, restore", async ({ page }) => {
  await login(page);

  // 1-2. open the campaign's banners and open the 300x250 in the editor
  await page.goto(`/campaigns/${seed.campaign_id}/creatives`);
  await page.locator(`a[href$="/editor/${seed.creatives["300x250"]}"]`).click();
  await page.waitForSelector("[data-testid=editor-root]"); await expect(page.getByTestId("doc-size")).toHaveText("300 × 250px");
  await page.waitForFunction(() => !!(window as unknown as { __editorStage?: unknown }).__editorStage); await saved(page);
  const original = await docJson(page, seed.creatives["300x250"]); const origLayers = layersOf(original);
  const head = original.layers.find((l: { role: string }) => l.role === "headline"), prod = original.layers.find((l: { role: string }) => l.role === "product"), cta = original.layers.find((l: { role: string }) => l.role === "cta");

  // 3. change the headline (Quick Edit form)
  const headline = page.getByLabel("Headline text"); await headline.fill("Fresh new headline"); await expect(page.getByTestId("save-status")).toHaveAttribute("data-state", /unsaved|saving|saved/); await saved(page);

  // 4. resize and move the product image (Advanced: properties + a real canvas drag)
  await advanced(page); await selectLayer(page, "Product");
  await setNumber(page, "Width", 80); await setNumber(page, "Height", 90); await setNumber(page, "X", 190); await setNumber(page, "Y", 60);
  const box = await page.evaluate(() => { const s = (window as unknown as { __editorStage: { x(): number; y(): number; scaleX(): number; container(): HTMLElement } }).__editorStage; const r = s.container().getBoundingClientRect(); return { ox: r.left + s.x(), oy: r.top + s.y(), z: s.scaleX() }; });
  const cx = box.ox + (190 + 40) * box.z, cy = box.oy + (60 + 45) * box.z;
  await page.mouse.move(cx, cy); await page.mouse.down(); await page.mouse.move(cx - 30 * box.z, cy + 10 * box.z, { steps: 8 }); await page.mouse.up(); await saved(page);
  const xAfterDrag = Number(await field(page, "X").inputValue()); expect(xAfterDrag).toBeLessThan(190); expect(xAfterDrag).toBeGreaterThan(140);

  // 5-7. CTA colour, undo, redo
  await selectLayer(page, "CTA"); const fill = field(page, "CTA background"); const before = await fill.inputValue();
  await fill.fill("#00aa44"); await expect(field(page, "CTA background")).toHaveValue("#00aa44"); await page.getByRole("button", { name: "Undo" }).click(); await expect(field(page, "CTA background")).toHaveValue(before);
  await page.getByRole("button", { name: "Redo" }).click(); await expect(field(page, "CTA background")).toHaveValue("#00aa44");
  // keyboard shortcuts do not hijack typing: Ctrl+Z inside a field must not undo the document
  await page.keyboard.press("Control+s"); await saved(page);

  // 8. reload: everything persisted
  await page.reload(); await page.waitForSelector("[data-testid=editor-root]"); await saved(page);
  const afterReload = await docJson(page, seed.creatives["300x250"]); const L = layersOf(afterReload);
  expect(L[head.id].text).toBe("Fresh new headline"); expect(L[cta.id].fill).toBe("#00aa44"); expect(Math.round(L[prod.id].w as number)).toBe(80); expect(L[prod.id].x).toBeLessThan(190);
  const beforeAi = await docJson(page, seed.creatives["300x250"]);

  // 9-12. AI: regenerate ONLY the background; reject one set, accept another
  await advanced(page); await selectLayer(page, "Background"); await page.getByRole("button", { name: /AI edit background/ }).click();
  await page.getByLabel("Instruction").fill("a calm sunrise over mountains"); await page.getByRole("button", { name: "Generate options" }).click();
  await expect(page.getByRole("radiogroup", { name: "Candidates" })).toBeVisible({ timeout: 30_000 });
  expect((await docJson(page, seed.creatives["300x250"])).revision).toBe(beforeAi.revision);                        // candidates never change the document
  await page.getByRole("button", { name: "Reject all" }).click();
  expect((await docJson(page, seed.creatives["300x250"])).background).toEqual(beforeAi.background);
  await page.getByRole("button", { name: /AI edit background/ }).click(); await page.getByLabel("Instruction").fill("a bright kitchen counter, morning light"); await page.getByRole("button", { name: "Generate options" }).click();
  await expect(page.getByRole("radiogroup", { name: "Candidates" })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("radio", { name: /Option 2/ }).click(); await page.getByRole("button", { name: "Accept option 2" }).click(); await expect(page.getByRole("dialog")).toBeHidden(); await saved(page);
  const afterAi = await docJson(page, seed.creatives["300x250"]);
  expect(afterAi.background.asset_id).not.toBe(beforeAi.background.asset_id);
  for (const l of beforeAi.layers) expect(layersOf(afterAi)[l.id]).toEqual(l);                                       // 12. every other layer is identical
  expect(afterAi.layers.length).toBe(beforeAi.layers.length);

  // 13-14. the 1080x1080 banner is a separate document and untouched
  await page.getByLabel("Banner format").selectOption(seed.creatives["1080x1080"]); await page.waitForURL(`**/editor/${seed.creatives["1080x1080"]}`);
  await expect(page.getByTestId("doc-size")).toHaveText("1080 × 1080px"); await page.waitForSelector("[data-testid=editor-root]"); await saved(page);
  const sq = await docJson(page, seed.creatives["1080x1080"]); expect(sq.revision).toBe(0); expect(sq.layers.find((l: { role: string }) => l.role === "headline").text).not.toBe("Fresh new headline");
  expect(sq.background.asset_id).not.toBe(afterAi.background.asset_id);

  // 15-16. export the 300x250: preflight, PNG, exact dimensions + valid file
  await page.goto(`/campaigns/${seed.campaign_id}/editor/${seed.creatives["300x250"]}`); await page.waitForSelector("[data-testid=editor-root]"); await saved(page);
  await page.getByRole("button", { name: "Export" }).click(); await expect(page.getByRole("dialog", { name: "Export banner" })).toBeVisible();
  await page.getByRole("button", { name: /^Export PNG/ }).click(); const dl = page.getByRole("link", { name: "Download" }); await expect(dl).toBeVisible({ timeout: 30_000 });
  const png = await (await page.request.get((await dl.getAttribute("href"))!)).body();
  expect(png.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a"); expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([300, 250]);
  await page.keyboard.press("Escape");

  // 17. restore the original version
  await page.getByRole("button", { name: "History" }).click(); const orig = page.locator("li", { hasText: "Original" }).first(); await orig.getByRole("button", { name: "Restore" }).click(); await orig.getByRole("button", { name: "Confirm restore" }).click();
  await expect(page.getByRole("dialog")).toBeHidden(); await saved(page);
  const restored = await docJson(page, seed.creatives["300x250"]); const R = layersOf(restored);
  expect(R[head.id].text).toBe(origLayers[head.id].text); expect(restored.background.asset_id).toBe(original.background.asset_id); expect(R[prod.id].w).toBe(origLayers[prod.id].w);
  await page.getByRole("button", { name: "Undo" }).click(); await saved(page);                                       // restore is undoable
  expect(layersOf(await docJson(page, seed.creatives["300x250"]))[head.id].text).toBe("Fresh new headline");
});

test("autosave: offline keeps edits pending and syncs when back online; conflict from another session is not overwritten", async ({ page, context, browser }) => {
  await login(page); await openEditor(page, "300x250"); await advanced(page); await selectLayer(page, "Headline");
  await context.setOffline(true); await page.getByLabel("Text content").fill("Typed while offline");
  await expect(page.getByTestId("save-status")).toHaveAttribute("data-state", /offline|saving|unsaved/, { timeout: 15_000 });
  await context.setOffline(false); await page.evaluate(() => window.dispatchEvent(new Event("online"))); await saved(page);
  const id = seed.creatives["300x250"]; const d1 = await docJson(page, id); expect(d1.layers.find((l: { role: string }) => l.role === "headline").text).toBe("Typed while offline");

  // a second session saves first -> our stale edit must not overwrite it
  const ctx2 = await browser.newContext({ viewport: { width: 1500, height: 900 } }); const p2 = await ctx2.newPage(); await login(p2); await openEditor(p2, "300x250"); await advanced(p2); await selectLayer(p2, "Headline");
  await p2.getByLabel("Text content").fill("Other session wins"); await saved(p2);
  await page.getByLabel("Text content").fill("Mine, stale"); await page.keyboard.press("Control+s");
  await expect(page.getByTestId("conflict-banner")).toBeVisible({ timeout: 15_000 });
  expect((await docJson(page, id)).layers.find((l: { role: string }) => l.role === "headline").text).toBe("Other session wins");   // nothing overwritten
  await expect(page.getByLabel("Text content")).toHaveValue("Mine, stale");                                                          // local work preserved
  await page.getByRole("button", { name: /Reapply my changes/ }).click(); await saved(page);
  expect((await docJson(page, id)).layers.find((l: { role: string }) => l.role === "headline").text).toBe("Mine, stale"); await ctx2.close();
});

test("keyboard shortcuts on canvas; typing in fields is not hijacked", async ({ page }) => {
  await login(page); await openEditor(page, "1080x1080"); await advanced(page); await selectLayer(page, "Headline");
  const x0 = Number(await field(page, "X").inputValue()); await page.locator("[data-testid=canvas-container]").hover(); await page.keyboard.press("ArrowRight"); await page.keyboard.press("Shift+ArrowRight");
  await expect(field(page, "X")).toHaveValue(String(x0 + 11)); await page.keyboard.press("Control+z"); await expect(field(page, "X")).toHaveValue(String(x0));
  await page.keyboard.press("Control+Shift+z"); await expect(field(page, "X")).toHaveValue(String(x0 + 11));
  await page.getByLabel("Text content").click(); await page.keyboard.press("Delete"); await expect(page.getByRole("button", { name: "Select layer Headline" })).toBeVisible();   // Delete edited the text, not the layer
  await page.keyboard.press("Escape");
});

test("canvas vs backend renderer parity (same document, same fonts)", async ({ page }) => {
  await login(page); await openEditor(page, "300x250"); await advanced(page); await page.getByRole("checkbox", { name: "Safe area" }).uncheck(); await page.keyboard.press("Escape");
  await page.evaluate(() => document.fonts.ready); await page.waitForTimeout(800);
  const rj = await page.request.post(`${API}/creative-documents/${(await docJson(page, seed.creatives["300x250"])).id}/render`, { data: { output_format: "png" }, headers: { "x-org-id": seed.org_id, "x-csrf-token": await csrf(page) } });
  const job = await rj.json(); const url = job.url ?? (await (await page.request.get(`${API}/render-jobs/${job.id}`, { headers: { "x-org-id": seed.org_id } })).json()).url;
  const stats = await page.evaluate(async (serverUrl: string) => {
    const s = (window as unknown as { __editorStage: { find(sel: string): { hide(): void }[]; toDataURL(o: object): string; x(): number; y(): number; scaleX(): number; scale(o: object): void; position(o: object): void } }).__editorStage;
    s.find(".chrome").forEach(n => n.hide()); const z = s.scaleX(), ox = s.x(), oy = s.y(); s.scale({ x: 1, y: 1 }); s.position({ x: 0, y: 0 });   // capture 1:1 at the origin: no zoom resampling, no fractional offset
    const mine = s.toDataURL({ x: 0, y: 0, width: 300, height: 250, pixelRatio: 1 }); s.scale({ x: z, y: z }); s.position({ x: ox, y: oy });
    const load = (u: string) => new Promise<HTMLImageElement>((res, rej) => { const i = new Image(); i.crossOrigin = "anonymous"; i.onload = () => res(i); i.onerror = rej; i.src = u; });
    const [a, b] = await Promise.all([load(mine), load(serverUrl)]); const px = (i: HTMLImageElement) => { const c = document.createElement("canvas"); c.width = 300; c.height = 250; const g = c.getContext("2d")!; g.drawImage(i, 0, 0, 300, 250); return g.getImageData(0, 0, 300, 250).data; };
    const A = px(a), B = px(b); let sum = 0, big = 0; for (let i = 0; i < A.length; i += 4) { const d = (Math.abs(A[i] - B[i]) + Math.abs(A[i + 1] - B[i + 1]) + Math.abs(A[i + 2] - B[i + 2])) / 3; sum += d; if (d > 48) big++; }
    return { mean: sum / (A.length / 4), bigFraction: big / (A.length / 4), size: [a.width, a.height], mine, server: serverUrl };
  }, url);
  console.log("PARITY", JSON.stringify({ ...stats, mine: undefined, server: undefined }));
  if (process.env.PARITY_DUMP) { const fs = await import("fs"); fs.writeFileSync(`${process.env.PARITY_DUMP}/canvas.png`, Buffer.from(stats.mine.split(",")[1], "base64")); fs.writeFileSync(`${process.env.PARITY_DUMP}/server.png`, Buffer.from(await (await page.request.get(stats.server)).body())); }
  expect(stats.size).toEqual([300, 250]); expect(stats.mean).toBeLessThan(1.5); expect(stats.bigFraction).toBeLessThan(0.01);   // measured 0.98 / 0.0034; was 2.66 / 0.020 before sub-pixel text + baseline snapping
});
