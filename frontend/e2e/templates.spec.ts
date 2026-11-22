import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

/** Real-browser tests for the M7 Templates system (mock AI providers; templates never call an AI provider). */
const seed = JSON.parse(readFileSync(path.join(__dirname, ".seed.json"), "utf8")) as { email: string; password: string; campaign_id: string; org_id: string; creatives: Record<string, string> };
const API = "http://localhost:8001";
type L = Record<string, any>;

async function login(page: Page) {
  await page.goto("/login"); await page.fill("input[type=email]", seed.email); await page.fill("input[type=password]", seed.password); await page.click("form button"); await page.waitForURL("**/dashboard");
}
const H = { "x-org-id": seed.org_id };
const docJson = async (page: Page, creativeId: string) => { const r = await page.request.get(`${API}/creatives/${creativeId}/document`, { headers: H }); expect(r.ok(), await r.text()).toBeTruthy(); return r.json(); };
/** a document minus time-limited signed URLs (they differ per request) */
const stable = (d: L) => { const { assets: _a, updated_at: _u, ...rest } = d; return { ...rest, assetIds: Object.keys(_a ?? {}).sort() }; };
const strip = (d: { layers: L[] }) => d.layers.map(({ z_index: _z, ...l }) => l);
const byId = (d: { layers: L[] }) => Object.fromEntries(d.layers.map(l => [l.id, l]));
const saved = (page: Page) => expect(page.getByTestId("save-status")).toHaveAttribute("data-state", "saved", { timeout: 15_000 });
const openEditor = async (page: Page, size: string) => {
  await page.goto(`/campaigns/${seed.campaign_id}/editor/${seed.creatives[size]}`); await page.waitForSelector("[data-testid=editor-root]");
  await page.waitForFunction(() => !!(window as unknown as { __editorStage?: unknown }).__editorStage); await saved(page);
};
const templatesTab = async (page: Page) => { await page.getByRole("tab", { name: /Templates/ }).click(); await expect(page.getByTestId("templates-panel")).toBeVisible(); };
const useTemplate = async (page: Page, name: string) => {
  if (await page.getByTestId("template-details").isVisible() && !(await page.getByTestId("template-details").getByRole("heading", { name }).isVisible())) await page.getByRole("button", { name: /All templates/ }).click();
  if (!(await page.getByTestId("template-details").isVisible())) await page.getByRole("button", { name: `Open ${name}` }).click();
  await expect(page.getByTestId("template-details")).toBeVisible(); await page.getByRole("button", { name: "Use this template" }).click(); return page.getByRole("dialog"); };
const preview = async (dlg: ReturnType<Page["getByRole"]>, mode?: RegExp) => {
  if (mode) await dlg.getByRole("radio", { name: mode }).click();
  await dlg.getByRole("button", { name: /^(Preview|Refresh preview)$/ }).click(); await expect(dlg.getByTestId("mapping-summary")).toBeVisible({ timeout: 60_000 }); await expect(dlg.getByAltText("Proposed")).toBeVisible();
};

test.describe.configure({ mode: "serial" });

test("A: start a banner from a template, edit it, replace the product image and export", async ({ page }) => {
  await login(page);
  await page.goto(`/campaigns/${seed.campaign_id}/creatives`);
  await page.getByRole("button", { name: "Start from a template" }).click();
  const dlg0 = page.getByRole("dialog", { name: "Start a banner from a template" }); await expect(dlg0.getByTestId("template-card").first()).toBeVisible({ timeout: 30_000 });
  await dlg0.getByRole("button", { name: "Open Minimal Centered" }).click(); await dlg0.getByRole("button", { name: "Use this template" }).click();
  const dlg = page.getByRole("dialog", { name: /Start a banner from/ }).last();
  await dlg.getByLabel("Banner format").selectOption("1080x1080");
  await preview(dlg); await expect(dlg.getByRole("button", { name: "Create banner" })).toBeDisabled();                    // full template: needs confirmation
  await dlg.getByLabel("I have reviewed the changes").check(); await dlg.getByRole("button", { name: "Create banner" }).click();
  await page.waitForURL(/\/editor\/[0-9a-f-]{36}$/, { timeout: 60_000 }); const creativeId = page.url().split("/").pop()!;
  await page.waitForSelector("[data-testid=editor-root]"); await expect(page.getByTestId("doc-size")).toHaveText("1080 × 1080px"); await saved(page);
  const d = await docJson(page, creativeId); const roles = d.layers.map((l: L) => l.role);
  expect(roles).toEqual(expect.arrayContaining(["headline", "product", "cta"])); expect(d.metadata.source_template_id).toBeTruthy(); expect(d.layers.every((l: L) => !l.locked)).toBe(true);       // editable layers
  const prodBefore = d.layers.find((l: L) => l.role === "product").asset_id;
  // quick edits
  await page.getByLabel("Headline text").fill("Template headline"); await page.getByLabel("Button text").fill("Get it"); await saved(page);
  // replace the product image with the other uploaded picture
  await page.getByRole("button", { name: "Replace product image…" }).click();
  const assets = page.getByRole("dialog").getByRole("button", { name: /^Use products / }); const n = await assets.count(); expect(n).toBeGreaterThan(1);
  for (let i = 0; i < n; i++) { const label = await assets.nth(i).getAttribute("aria-label"); if (!prodBefore.startsWith(label!.split(" ").pop()!)) { await assets.nth(i).click(); break; } }
  await saved(page);
  const edited = await docJson(page, creativeId);
  expect(edited.layers.find((l: L) => l.role === "headline").text).toBe("Template headline"); expect(edited.layers.find((l: L) => l.role === "cta").label).toBe("Get it");
  expect(edited.layers.find((l: L) => l.role === "product").asset_id).not.toBe(prodBefore);
  // export
  await page.getByRole("button", { name: "Export" }).click(); await expect(page.getByRole("dialog", { name: "Export banner" })).toBeVisible();
  const ack = page.getByLabel(/seen the warnings/); if (await ack.isVisible().catch(() => false)) await ack.check();
  await page.getByRole("button", { name: /^Export PNG/ }).click(); await expect(page.getByRole("link", { name: "Download" })).toBeVisible({ timeout: 60_000 });
  const img = page.getByAltText("Rendered banner"); await expect(img).toBeVisible(); expect(await img.evaluate((i: HTMLImageElement) => [i.naturalWidth, i.naturalHeight])).toEqual([1080, 1080]);
});

test("B: layout only moves elements and keeps copy/fonts/colours/assets/background; undo restores the original", async ({ page }) => {
  await login(page); await openEditor(page, "300x250");
  const before = await docJson(page, seed.creatives["300x250"]);
  await templatesTab(page); const dlg = await useTemplate(page, "Modern Split"); await preview(dlg, /Layout only/);
  await expect(dlg.getByRole("region", { name: "Moved" })).toBeVisible();
  expect(strip(await docJson(page, seed.creatives["300x250"]))).toEqual(strip(before));                                       // preview changed nothing
  await dlg.getByRole("button", { name: "Apply template" }).click(); await expect(page.getByRole("dialog")).toBeHidden({ timeout: 30_000 }); await saved(page);
  const after = await docJson(page, seed.creatives["300x250"]); const b = byId(before), a = byId(after);
  expect(Object.keys(a).sort()).toEqual(Object.keys(b).sort());
  expect(Object.keys(a).some(id => ["x", "y", "w", "h"].some(k => a[id][k] !== b[id][k]))).toBe(true);                       // positions changed
  for (const id of Object.keys(a)) for (const k of ["text", "label", "font_family", "font_weight", "color", "fill", "text_color", "asset_id"]) expect(a[id][k], `${id}.${k}`).toEqual(b[id][k]);
  expect(after.background).toEqual(before.background);
  await page.getByRole("button", { name: "Undo" }).click(); await saved(page);
  const undone = await docJson(page, seed.creatives["300x250"]); expect(strip(undone)).toEqual(strip(before)); expect(undone.background).toEqual(before.background);
});

test("C: layout + style restyles but keeps product, logo and campaign copy and respects brand rules", async ({ page }) => {
  await login(page); await openEditor(page, "1080x1080");
  const before = await docJson(page, seed.creatives["1080x1080"]);
  await templatesTab(page); const dlg = await useTemplate(page, "Luxury Noir"); await preview(dlg, /Layout \+ style/);
  await expect(dlg.getByRole("region", { name: "Restyled" })).toBeVisible();
  await dlg.getByRole("button", { name: "Apply template" }).click(); await expect(page.getByRole("dialog")).toBeHidden({ timeout: 30_000 }); await saved(page);
  const after = await docJson(page, seed.creatives["1080x1080"]); const b = byId(before), a = byId(after);
  const texts = Object.keys(b).filter(id => b[id].type === "text");
  expect(texts.some(id => a[id].font_family !== b[id].font_family || a[id].color !== b[id].color || a[id].x !== b[id].x)).toBe(true);
  for (const id of Object.keys(b)) { if (b[id].type === "image") expect(a[id].asset_id).toBe(b[id].asset_id); if (b[id].type === "text") expect(a[id].text).toBe(b[id].text); if (b[id].type === "button") expect(a[id].label).toBe(b[id].label); }
  expect(after.background.asset_id).toBe(before.background.asset_id);
  const colours: string[] = (await (await page.request.get(`${API}/campaigns/${seed.campaign_id}/editor-assets`, { headers: H })).json()).brand.colors ?? [];
  if (colours.length) for (const id of texts) expect([...colours.map(c => c.toLowerCase()), "#ffffff", "#111111", "#000000"]).toContain(a[id].color.toLowerCase());
});

test("D: full template shows replacements, needs confirmation, cancel changes nothing, undo restores", async ({ page }) => {
  await login(page); await openEditor(page, "300x250");
  const before = await docJson(page, seed.creatives["300x250"]);
  await templatesTab(page); let dlg = await useTemplate(page, "Bold Promo"); await preview(dlg, /Full template/);
  await expect(dlg.getByRole("region", { name: "Replaced" })).toBeVisible(); await expect(dlg.getByRole("region", { name: "Added" })).toBeVisible();
  await expect(dlg.getByRole("button", { name: "Apply template" })).toBeDisabled();                                            // explicit confirmation required
  await dlg.getByRole("button", { name: "Cancel" }).click(); await expect(page.getByRole("dialog")).toBeHidden();
  expect(stable(await docJson(page, seed.creatives["300x250"]))).toEqual(stable(before));                                                     // cancel -> nothing changed (incl. revision)
  dlg = await useTemplate(page, "Bold Promo"); await preview(dlg, /Full template/);
  await dlg.getByLabel("I have reviewed the changes").check(); await dlg.getByRole("button", { name: "Apply template" }).click(); await expect(page.getByRole("dialog")).toBeHidden({ timeout: 30_000 }); await saved(page);
  const after = await docJson(page, seed.creatives["300x250"]);
  expect(after.background.type).toBe("solid"); expect(after.background.color).toBe("#ffd400"); expect(after.layers.some((l: L) => String(l.id).startsWith("tpl_"))).toBe(true);
  await page.getByRole("button", { name: "Undo" }).click(); await saved(page);
  const undone = await docJson(page, seed.creatives["300x250"]); expect(strip(undone)).toEqual(strip(before)); expect(undone.background).toEqual(before.background);
});

test("E: save as organization template; placeholders only; it appears under My templates; another organization cannot see or read it", async ({ page, browser }) => {
  await login(page); await openEditor(page, "300x250");
  const doc = await docJson(page, seed.creatives["300x250"]); const headline: string = doc.layers.find((l: L) => l.role === "headline").text;
  await page.getByRole("button", { name: "Save as template" }).click(); const dlg = page.getByRole("dialog", { name: "Save as template" });
  await dlg.getByLabel("Template name").fill("E2E Org Template"); await dlg.getByRole("radio", { name: /Organization/ }).click(); await dlg.getByLabel("Luxury").check(); await dlg.getByLabel("Centered").check();
  await dlg.getByRole("button", { name: "Save template" }).click(); await expect(page.getByText("Template saved")).toBeVisible({ timeout: 30_000 }); await page.getByRole("button", { name: "Done" }).click();
  const list = await (await page.request.get(`${API}/templates?thumbnails=false&owner=organization`, { headers: H })).json(); const t = list.items.find((x: L) => x.name === "E2E Org Template");
  expect(t).toBeTruthy(); expect(t.source).toBe("saved_from_design"); expect(t.visibility).toBe("organization");
  const detail = await (await page.request.get(`${API}/templates/${t.id}`, { headers: H })).json();
  expect(JSON.stringify(detail)).not.toContain(headline);                                                                       // no customer copy in the blueprint
  expect(Object.values(detail.slots_by_format).flat().map((s: any) => s.role)).toEqual(expect.arrayContaining(["headline_primary", "primary_cta"]));
  // appears under My templates and is usable on the same size
  await templatesTab(page); await page.getByRole("tab", { name: "My templates" }).click(); await expect(page.getByRole("button", { name: "Open E2E Org Template" })).toBeVisible();
  // a different organization: cannot list or read it
  const ctx = await browser.newContext(); const p2 = await ctx.newPage();
  const email = `rival-${Date.now()}@example.com`; const reg = await p2.request.post(`${API}/auth/register`, { data: { email, password: "rivalpassword123", organization_name: "Rival Org" } });
  expect(reg.ok()).toBeTruthy(); const rj = await reg.json(); const rh = { "x-org-id": rj.memberships[0].organization.id, "x-csrf-token": rj.csrf_token };
  const rl = await (await p2.request.get(`${API}/templates?thumbnails=false`, { headers: rh })).json(); expect(rl.items.map((x: L) => x.name)).not.toContain("E2E Org Template");
  expect((await p2.request.get(`${API}/templates/${t.id}`, { headers: rh })).status()).toBe(404);
  await ctx.close();
});

test("F: applying a template to one format never changes the other format", async ({ page }) => {
  await login(page); await openEditor(page, "300x250");
  const bigBefore = await docJson(page, seed.creatives["1080x1080"]);
  await templatesTab(page); let dlg = await useTemplate(page, "Corporate Clean"); await preview(dlg, /Layout \+ style/); await dlg.getByRole("button", { name: "Apply template" }).click();
  await expect(page.getByRole("dialog")).toBeHidden({ timeout: 30_000 }); await saved(page);
  expect(stable(await docJson(page, seed.creatives["1080x1080"]))).toEqual(stable(bigBefore));                                                // other format: byte-for-byte the same document (same revision)
  await openEditor(page, "1080x1080"); await templatesTab(page); const small = await docJson(page, seed.creatives["300x250"]);
  dlg = await useTemplate(page, "Editorial"); await preview(dlg, /Layout only/); await dlg.getByRole("button", { name: "Apply template" }).click();
  await expect(page.getByRole("dialog")).toBeHidden({ timeout: 30_000 }); await saved(page);
  expect(stable(await docJson(page, seed.creatives["300x250"]))).toEqual(stable(small));
  const big = await docJson(page, seed.creatives["1080x1080"]); const sm = await docJson(page, seed.creatives["300x250"]);
  expect(big.metadata.source_template_id).not.toBe(sm.metadata.source_template_id);                                           // two independent documents, two different templates
});
