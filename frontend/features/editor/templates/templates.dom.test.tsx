// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));
const pushMock = vi.fn();
vi.mock("@/lib/api", () => {
  class ApiError extends Error { constructor(public status: number, m: string, public body?: unknown, public code?: string) { super(m); } }
  return { ApiError, api: vi.fn() };
});
import { api, ApiError } from "@/lib/api";
import { makeView } from "@/lib/editor/fixtures";
import { boot } from "@/lib/editor/testkit";
import type { Preview, TemplateSummary } from "@/lib/templates/types";
import { useEditor } from "@/stores/editor";
import SaveAsTemplateDialog from "./SaveAsTemplateDialog";
import TemplatesPanel from "./TemplatesPanel";

const mockApi = api as unknown as ReturnType<typeof vi.fn>;
const wrap = (ui: React.ReactNode) => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>{ui}</QueryClientProvider>);
const fmt = (w: number, h: number) => ({ format_id: `${w}x${h}`, width: w, height: h, aspect_ratio: w / h, aspect_class: "landscape", inherits: null, placements: [] });
const tpl = (o: Partial<TemplateSummary> = {}): TemplateSummary => ({ id: "t1", name: "Modern Split", description: "Dark split layout", owner_type: "system", owner: "System", visibility: "system", category: "electronics", style_tags: ["modern"], layout_tags: ["split"],
  status: "published", current_version: 1, source: "built_in", application_modes: ["layout_only", "layout_style", "full"], supported_formats: [fmt(300, 250), fmt(1080, 1080)], favorite: false, can_edit: false, compatible: true,
  thumbnail: { asset_id: "x", url: "http://img/t1.png", width: 10, height: 10, format_id: "300x250" }, ...o });
const preview = (o: Partial<Preview> = {}): Preview => ({ application_id: "app1", status: "previewed", mode: "layout_only", format_id: "300x250", template: { id: "t1", name: "Modern Split", version: 1 }, document_id: "doc1", campaign_id: "camp", source_revision: 0,
  before_url: "http://img/before.png", after_url: "http://img/after.png", warnings: [{ code: "overlap", severity: "warning", message: "Headline overlaps CTA", layer_id: "head" }], requires_confirmation: false, needs_confirmation: [], can_apply: true,
  quality: { before: { score: 80, status: "pass" }, after: { score: 84, status: "pass" } },
  mapping: { items: [{ slot: "headline_primary", layer_id: "head", layer_name: "Headline", action: "moved", changes: ["x", "y"], note: "" }, { slot: "product_primary", layer_id: "prod", layer_name: "Product", action: "preserved", changes: [], note: "" }],
    missing_required: [], ambiguities: [], unmapped_layers: [], counts: { moved: 1, preserved: 1 } }, ...o });

let templates: TemplateSummary[]; let calls: { path: string; init?: RequestInit & { json?: unknown } }[];
const route = (handlers: Record<string, (init?: any) => unknown> = {}) => mockApi.mockImplementation(async (path: string, init?: RequestInit & { json?: unknown }) => {
  calls.push({ path, init });
  for (const [k, h] of Object.entries(handlers)) if (path.startsWith(k)) { const r = await h(init); if (r instanceof Error) throw r; return r; }
  if (path.startsWith("/templates?") || path === "/templates") return { total: templates.length, items: templates, filters: { industries: [], styles: [], layouts: [] } };
  if (/^\/templates\/[^/]+\/layouts\//.test(path)) return { asset_id: "a", url: "http://img/big.png", width: 300, height: 250, format_id: "300x250" };
  if (/^\/templates\/[^/]+\/versions/.test(path)) return [{ version: 1, change_note: "Built-in template", created_at: "2026-01-01T00:00:00Z", current: true, formats: ["300x250"] }];
  if (/^\/templates\/[^/]+$/.test(path)) return { ...(templates.find(t => path.endsWith("/" + t.id)) ?? templates[0]), definition: {}, brand_constraints: {}, slots_by_format: {} };
  if (/\/favorite$/.test(path)) return { favorite: init?.method === "POST" };
  throw new Error("unmocked " + path);
});
const target = { kind: "document" as const, documentId: "doc1", width: 300, height: 250 };
const open = async (name = "Modern Split") => { fireEvent.click(await screen.findByRole("button", { name: `Open ${name}` })); await screen.findByTestId("template-details"); };

beforeEach(() => { useEditor.getState().reset(); boot(); mockApi.mockReset(); calls = []; templates = [tpl(), tpl({ id: "t2", name: "Hero Full-bleed", category: "fitness", style_tags: ["bold"], layout_tags: ["full-bleed"], compatible: false, supported_formats: [fmt(1080, 1080)] })]; route(); });
afterEach(cleanup);

describe("template library", () => {
  it("lists cards with name, description, industry, style, sizes and owner; unsupported sizes are flagged, never shown as compatible", async () => {
    wrap(<TemplatesPanel target={target} />);
    const cards = await screen.findAllByTestId("template-card"); expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByText("Modern Split")).toBeTruthy(); expect(within(cards[0]).getByText("Dark split layout")).toBeTruthy(); expect(within(cards[0]).getByText("Electronics")).toBeTruthy(); expect(within(cards[0]).getByText("Modern")).toBeTruthy();
    expect(within(cards[0]).getByText(/2 sizes/)).toBeTruthy(); expect(within(cards[0]).getByText(/System/)).toBeTruthy(); expect(within(cards[0]).queryByText(/Not available/)).toBeNull();
    expect(within(cards[1]).getByText(/Not available for this size/)).toBeTruthy();
    expect(calls[0].path).toContain("document_id=doc1");
  });
  it("switches categories and passes them to the API", async () => {
    wrap(<TemplatesPanel target={target} />); await screen.findAllByTestId("template-card");
    for (const [label, tab] of [["Layouts", "layouts"], ["Creative styles", "styles"], ["Industry templates", "industry"], ["My templates", "mine"]] as const) {
      fireEvent.click(screen.getByRole("tab", { name: label })); await waitFor(() => expect(calls.some(c => c.path.includes(`tab=${tab}`))).toBe(true));
    }
    expect(screen.getByRole("tab", { name: "My templates" }).getAttribute("aria-selected")).toBe("true");
  });
  it("debounces search and sends it", async () => {
    wrap(<TemplatesPanel target={target} />); await screen.findAllByTestId("template-card");
    fireEvent.change(screen.getByLabelText("Search templates"), { target: { value: "luxury" } });
    await waitFor(() => expect(calls.some(c => c.path.includes("q=luxury"))).toBe(true)); expect(calls.filter(c => c.path.includes("q=luxu")).length).toBe(1);
  });
  it("filters by industry, style, layout, aspect, owner, source, exact dimensions, favourites and compatibility", async () => {
    wrap(<TemplatesPanel target={target} />); await screen.findAllByTestId("template-card"); fireEvent.click(screen.getByRole("button", { name: /Filters/ }));
    const pick = async (label: string, value: string, expect_: string) => { fireEvent.change(screen.getByLabelText(label), { target: { value } }); await waitFor(() => expect(calls.some(c => c.path.includes(expect_))).toBe(true)); };
    await pick("Industry", "food", "industry=food"); await pick("Style", "luxury", "style=luxury"); await pick("Layout", "split", "layout=split"); await pick("Aspect ratio", "square", "aspect=square");
    await pick("Owner", "organization", "owner=organization"); await pick("Source", "saved_from_design", "source=saved_from_design");
    fireEvent.change(screen.getByLabelText("Exact dimensions"), { target: { value: "300x250" } }); await waitFor(() => expect(calls.some(c => c.path.includes("width=300") && c.path.includes("height=250"))).toBe(true));
    fireEvent.click(screen.getByLabelText("Favorites only")); await waitFor(() => expect(calls.some(c => c.path.includes("favorites=true"))).toBe(true));
    fireEvent.click(screen.getByLabelText(/Only templates for 300×250/)); await waitFor(() => expect(screen.queryByText("Hero Full-bleed")).toBeNull());     // incompatible one hidden
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" })); await waitFor(() => expect((screen.getByLabelText("Industry") as HTMLSelectElement).value).toBe(""));
  });
  it("shows loading, empty and error states", async () => {
    templates = []; wrap(<TemplatesPanel target={target} />); expect(screen.getByText(/Loading templates/)).toBeTruthy(); expect(await screen.findByText(/No templates match/)).toBeTruthy();
    cleanup(); route({ "/templates": () => new ApiError(500, "Server exploded") }); wrap(<TemplatesPanel target={target} />);
    expect((await screen.findByRole("alert")).textContent).toContain("Server exploded"); expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });
  it("shows a friendly message on My templates when nothing is saved", async () => {
    templates = []; wrap(<TemplatesPanel target={target} />); fireEvent.click(screen.getByRole("tab", { name: "My templates" })); expect(await screen.findByText(/haven’t saved any templates/)).toBeTruthy();
  });
  it("toggles favourites on a card", async () => {
    wrap(<TemplatesPanel target={target} />); fireEvent.click((await screen.findAllByRole("button", { name: "Add Modern Split to favorites" }))[0]);
    await waitFor(() => expect(calls.some(c => c.path === "/templates/t1/favorite" && c.init?.method === "POST")).toBe(true));
  });
});

describe("template details", () => {
  it("opens details with supported formats, version history, and refuses unsupported banners", async () => {
    wrap(<TemplatesPanel target={target} />); await open();
    expect(screen.getByRole("radiogroup", { name: "Supported formats" })).toBeTruthy(); expect(screen.getByRole("radio", { name: "300×250" })).toBeTruthy(); expect(screen.getByRole("radio", { name: "1080×1080" })).toBeTruthy();
    expect(screen.getByText(/dedicated 300×250 layout/)).toBeTruthy(); expect(await screen.findByAltText(/Template preview at/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Show version history/ })); expect(await screen.findByRole("list", { name: "Template versions" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /All templates/ })); await open("Hero Full-bleed");
    expect(screen.getByText(/unavailable for this banner/)).toBeTruthy(); expect((screen.getByRole("button", { name: "Use this template" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("owners can edit, publish and archive (with confirmation); system templates offer neither", async () => {
    templates = [tpl({ owner_type: "organization", owner: "Organization", can_edit: true, status: "draft", source: "saved_from_design" })];
    route({ "/templates/t1/publish": () => ({ ...templates[0], status: "published" }), "/templates/t1/archive": () => ({ ...templates[0], status: "archived" }), "/templates/t1": (i) => i?.method === "PATCH" ? { ...templates[0], name: i.json.name } : { ...templates[0], definition: {}, brand_constraints: {}, slots_by_format: {} } });
    wrap(<TemplatesPanel target={target} />); await open();
    fireEvent.click(screen.getByRole("button", { name: "Edit details" })); fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Renamed" } }); fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls.some(c => c.init?.method === "PATCH" && (c.init.json as { name: string }).name === "Renamed")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Publish" })); await waitFor(() => expect(calls.some(c => c.path === "/templates/t1/publish")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Archive" })); const dlg = await screen.findByRole("dialog", { name: "Archive this template?" }); expect(calls.some(c => c.path.endsWith("/archive"))).toBe(false);
    fireEvent.click(within(dlg).getByRole("button", { name: "Cancel" })); expect(calls.some(c => c.path.endsWith("/archive"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Archive" })); fireEvent.click(within(await screen.findByRole("dialog", { name: "Archive this template?" })).getByRole("button", { name: "Archive" })); await waitFor(() => expect(calls.some(c => c.path === "/templates/t1/archive")).toBe(true));
  });
  it("system templates can be duplicated but not edited", async () => {
    route({ "/templates/t1/duplicate": () => tpl({ id: "t9", owner_type: "personal" }) }); wrap(<TemplatesPanel target={target} />); await open();
    expect(screen.queryByRole("button", { name: "Edit details" })).toBeNull(); expect(screen.queryByRole("button", { name: "Archive" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Duplicate/ })); expect(await screen.findByText("Copied to My templates.")).toBeTruthy();
  });
});

describe("applying a template", () => {
  const start = async () => { wrap(<TemplatesPanel target={target} />); await open(); fireEvent.click(screen.getByRole("button", { name: "Use this template" })); return screen.findByRole("dialog"); };

  it("previews without touching the document, shows before/after, mapping and warnings, then applies as ONE undoable step", async () => {
    const applied = makeView("doc1", 1); applied.layers[1] = { ...applied.layers[1], x: 99 } as never;
    route({ "/templates/t1/preview": () => preview(), "/templates/t1/apply": () => ({ document: applied, application_id: "app1", created: false }) });
    const before = JSON.stringify(useEditor.getState().doc); const dlg = await start();
    expect((within(dlg).getByRole("button", { name: "Apply template" }) as HTMLButtonElement).disabled).toBe(true);        // nothing to apply before a preview
    fireEvent.click(within(dlg).getByRole("button", { name: "Preview" }));
    expect(await within(dlg).findByAltText("Current")).toBeTruthy(); expect(within(dlg).getByAltText("Proposed")).toBeTruthy();
    expect(JSON.stringify(useEditor.getState().doc)).toBe(before);
    const g = within(dlg).getByTestId("mapping-summary"); expect(within(g).getByRole("region", { name: "Moved" })).toBeTruthy(); expect(within(g).getByRole("region", { name: "Preserved" })).toBeTruthy();
    expect(within(dlg).getByText("Headline overlaps CTA")).toBeTruthy(); expect(within(dlg).getByText(/not advertising performance/)).toBeTruthy();
    const previewCall = calls.find(c => c.path === "/templates/t1/preview")!; expect(previewCall.init?.json).toMatchObject({ document_id: "doc1", mode: "layout_only", confirm_replace: [] });
    fireEvent.click(within(dlg).getByRole("button", { name: "Apply template" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(calls.find(c => c.path === "/templates/t1/apply")!.init?.json).toMatchObject({ application_id: "app1", expected_revision: 0, confirm: false });
    expect(useEditor.getState().past.at(-1)?.label).toBe("Template: Modern Split"); expect(useEditor.getState().notice).toContain("Applied");
    act(() => useEditor.getState().undo()); expect(useEditor.getState().doc!.elements[1].x).not.toBe(99);                    // client undo restores the previous layout
  });

  it("full-template mode lists replacements, requires an explicit confirmation, and cancel changes nothing", async () => {
    const full = preview({ mode: "full", requires_confirmation: true, needs_confirmation: ["brand_logo"], mapping: { ...preview().mapping, items: [{ slot: "background", layer_id: "background", layer_name: "Background", action: "replaced", changes: ["background"], note: "Template background" },
      { slot: "decorative_shape", layer_id: "tpl_bar", layer_name: "Template: bar", action: "added", changes: [], note: "" }, { slot: "headline_primary", layer_id: "head", layer_name: "Headline", action: "restyled", changes: ["color"], note: "" }] } });
    route({ "/templates/t1/preview": () => full, "/template-applications/app1/reject": () => ({ status: "rejected" }), "/templates/t1/apply": () => ({ document: makeView("doc1", 1), application_id: "app1", created: false }) });
    const dlg = await start(); fireEvent.click(within(dlg).getByRole("radio", { name: /Full template/ })); fireEvent.click(within(dlg).getByRole("button", { name: "Preview" }));
    await within(dlg).findByTestId("mapping-summary"); expect(within(dlg).getByRole("region", { name: "Replaced" })).toBeTruthy(); expect(within(dlg).getByRole("region", { name: "Added" })).toBeTruthy();
    expect(within(dlg).getByText(/brings its own content for/)).toBeTruthy();
    const applyBtn = within(dlg).getByRole("button", { name: "Apply template" }) as HTMLButtonElement; expect(applyBtn.disabled).toBe(true);                       // not until the user confirms
    fireEvent.click(within(dlg).getByRole("button", { name: "Cancel" })); await waitFor(() => expect(calls.some(c => c.path === "/template-applications/app1/reject")).toBe(true));
    expect(calls.some(c => c.path === "/templates/t1/apply")).toBe(false); expect(useEditor.getState().past).toHaveLength(0);
    // second attempt: confirm and apply
    fireEvent.click(screen.getByRole("button", { name: "Use this template" })); const d2 = await screen.findByRole("dialog"); fireEvent.click(within(d2).getByRole("radio", { name: /Full template/ })); fireEvent.click(within(d2).getByRole("button", { name: "Preview" }));
    await within(d2).findByTestId("mapping-summary"); fireEvent.click(within(d2).getByLabelText("I have reviewed the changes")); fireEvent.click(within(d2).getByRole("button", { name: "Apply template" }));
    await waitFor(() => expect(calls.some(c => c.path === "/templates/t1/apply")).toBe(true)); expect(calls.find(c => c.path === "/templates/t1/apply")!.init?.json).toMatchObject({ confirm: true });
  });

  it("re-previews with confirm_replace when the user allows a template-supplied replacement", async () => {
    const needs = preview({ mode: "full", requires_confirmation: true, needs_confirmation: ["brand_logo"] }); let n = 0;
    route({ "/templates/t1/preview": (i) => { n++; return n === 1 ? needs : preview({ mode: "full", requires_confirmation: true, needs_confirmation: [] }); } });
    const dlg = await start(); fireEvent.click(within(dlg).getByRole("radio", { name: /Full template/ })); fireEvent.click(within(dlg).getByRole("button", { name: "Preview" }));
    fireEvent.click(await within(dlg).findByLabelText(/Allow the template to replace “brand logo”/));
    await waitFor(() => expect(calls.filter(c => c.path === "/templates/t1/preview").length).toBe(2));
    expect((calls.filter(c => c.path === "/templates/t1/preview")[1].init!.json as { confirm_replace: string[] }).confirm_replace).toEqual(["brand_logo"]);
  });

  it("a stale preview (revision conflict) is not applied and asks to preview again", async () => {
    route({ "/templates/t1/preview": () => preview(), "/templates/t1/apply": () => new ApiError(409, "changed", {}, "revision_conflict") });
    const dlg = await start(); fireEvent.click(within(dlg).getByRole("button", { name: "Preview" })); await within(dlg).findByTestId("mapping-summary"); fireEvent.click(within(dlg).getByRole("button", { name: "Apply template" }));
    expect((await within(dlg).findByRole("alert")).textContent).toMatch(/Nothing was applied; preview again/); expect((within(dlg).getByRole("button", { name: "Apply template" }) as HTMLButtonElement).disabled).toBe(true);
    expect(useEditor.getState().past).toHaveLength(0);
  });

  it("shows preview errors and blocks applying when there are blocking errors", async () => {
    route({ "/templates/t1/preview": () => new ApiError(422, "No 300x250 layout", {}, "format_unsupported") });
    let dlg = await start(); fireEvent.click(within(dlg).getByRole("button", { name: "Preview" })); expect((await within(dlg).findByRole("alert")).textContent).toContain("No 300x250 layout");
    cleanup(); useEditor.getState().reset(); boot(); route({ "/templates/t1/preview": () => preview({ can_apply: false, warnings: [{ code: "missing_asset", severity: "error", message: "Asset missing", layer_id: null }] }) });
    dlg = await start(); fireEvent.click(within(dlg).getByRole("button", { name: "Preview" })); await within(dlg).findByText("Asset missing"); expect((within(dlg).getByRole("button", { name: "Apply template" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("lets the user resolve an ambiguous slot", async () => {
    const amb = preview({ mapping: { ...preview().mapping, ambiguities: [{ slot: "headline_primary", candidates: ["head", "prod"], chosen: "head" }] } }); let n = 0;
    route({ "/templates/t1/preview": () => (++n, amb) }); const dlg = await start(); fireEvent.click(within(dlg).getByRole("button", { name: "Preview" }));
    fireEvent.change(await within(dlg).findByRole("combobox", { name: /Several layers match/ }), { target: { value: "prod" } }); await waitFor(() => expect(n).toBe(2));
    expect((calls.filter(c => c.path === "/templates/t1/preview")[1].init!.json as { slot_overrides: Record<string, string> }).slot_overrides).toEqual({ headline_primary: "prod" });
  });

  it("starting a new banner picks a supported format and navigates to the editor", async () => {
    const created = { document: makeView("doc9", 0), creative_id: "cr9", application_id: "app2", created: true };
    route({ "/templates/t1/preview": () => preview({ document_id: null, mode: "full", requires_confirmation: true, source_revision: null }), "/templates/t1/apply": () => created });
    wrap(<TemplatesPanel target={{ kind: "campaign", campaignId: "camp1" }} />); await open(); fireEvent.click(screen.getByRole("button", { name: "Use this template" }));
    const dlg = await screen.findByRole("dialog"); const sel = within(dlg).getByLabelText("Banner format") as HTMLSelectElement; expect([...sel.options].map(o => o.textContent)).toEqual(["300×250", "1080×1080"]);
    fireEvent.change(sel, { target: { value: "1080x1080" } }); fireEvent.click(within(dlg).getByRole("button", { name: "Preview" })); await within(dlg).findByTestId("mapping-summary");
    expect(calls.find(c => c.path === "/templates/t1/preview")!.init?.json).toMatchObject({ campaign_id: "camp1", format_id: "1080x1080", mode: "full" });
    fireEvent.click(within(dlg).getByLabelText("I have reviewed the changes")); fireEvent.click(within(dlg).getByRole("button", { name: "Create banner" }));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/campaigns/camp1/editor/cr9"));
  });
});

describe("save as template", () => {
  const open_ = () => { wrap(<SaveAsTemplateDialog documentId="doc1" onClose={() => {}} />); return screen.getByRole("dialog"); };
  it("validates, selects visibility, submits placeholders-only options and reports what was left out", async () => {
    const saved = { template: tpl({ id: "t5", name: "Saved", owner_type: "organization", owner: "Organization", source: "saved_from_design" }), notes: ["Background image not included: it becomes a solid colour in the template"], included_assets: [] };
    route({ "/creative-documents/doc1/save-as-template": () => saved });
    const dlg = open_(); fireEvent.change(within(dlg).getByLabelText("Template name"), { target: { value: "  " } }); fireEvent.click(within(dlg).getByRole("button", { name: "Save template" }));
    expect((await within(dlg).findByRole("alert")).textContent).toContain("Give the template a name"); expect(calls.some(c => c.path.includes("save-as-template"))).toBe(false);
    fireEvent.change(within(dlg).getByLabelText("Template name"), { target: { value: "Saved" } }); fireEvent.change(within(dlg).getByLabelText("Also support these sizes"), { target: { value: "wide" } });
    fireEvent.click(within(dlg).getByRole("button", { name: "Save template" })); await waitFor(() => expect(within(dlg).getAllByRole("alert").some(a => /sizes like/i.test(a.textContent ?? ""))).toBe(true));
    fireEvent.change(within(dlg).getByLabelText("Also support these sizes"), { target: { value: "600x500" } });
    expect(within(dlg).getByRole("radio", { name: /Personal/ }).getAttribute("aria-checked")).toBe("true"); fireEvent.click(within(dlg).getByRole("radio", { name: /Organization/ }));
    fireEvent.click(within(dlg).getByLabelText("Luxury")); fireEvent.click(within(dlg).getByLabelText("Centered")); fireEvent.change(within(dlg).getByLabelText("Industry"), { target: { value: "beauty" } });
    fireEvent.click(within(dlg).getByRole("button", { name: "Save template" }));
    expect(await screen.findByText(/Template saved/)).toBeTruthy(); expect(screen.getByText(/placeholders/)).toBeTruthy(); expect(screen.getByText(/Background image not included/)).toBeTruthy();
    expect(calls.find(c => c.path.includes("save-as-template"))!.init?.json).toMatchObject({ name: "Saved", visibility: "organization", category: "beauty", style_tags: ["luxury"], layout_tags: ["centered"], formats: ["600x500"],
      include_background_assets: false, include_decorative_assets: false, preserve_brand_styling: true });
  });
  it("shows the server's error and keeps the dialog open", async () => {
    route({ "/creative-documents/doc1/save-as-template": () => new ApiError(422, "This design has no layers that can become template slots", {}, "nothing_to_save") });
    const dlg = open_(); fireEvent.click(within(dlg).getByRole("button", { name: "Save template" })); expect((await within(dlg).findByRole("alert")).textContent).toContain("no layers");
    expect(screen.getByRole("dialog", { name: "Save as template" })).toBeTruthy();
  });
  it("offers saving as a new version only when the user owns an editable template", async () => {
    route({ "/templates": () => ({ total: 1, items: [tpl({ id: "mine1", name: "Mine", owner_type: "personal", can_edit: true })], filters: {} }) }); open_();
    fireEvent.click(await screen.findByLabelText("Save as a new version of an existing template")); expect(await screen.findByLabelText("Template to version")).toBeTruthy();
  });
});
