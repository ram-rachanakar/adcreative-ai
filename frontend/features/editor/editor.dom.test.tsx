// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => {
  class ApiError extends Error { constructor(public status: number, m: string, public body?: unknown, public code?: string) { super(m); } }
  return { ApiError, api: vi.fn() };
});
import { api, ApiError } from "@/lib/api";
import { makeDoc, makeView } from "@/lib/editor/fixtures";
import { boot } from "@/lib/editor/testkit";
import { useEditor } from "@/stores/editor";
import AiEditDialog from "./AiEditDialog";
import LeftPanel from "./LeftPanel";
import PropertiesPanel from "./PropertiesPanel";
import QuickEdit from "./QuickEdit";

const mockApi = api as unknown as ReturnType<typeof vi.fn>;
const wrap = (ui: React.ReactNode) => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);
const doc = () => useEditor.getState().doc!; const layer = (id: string) => doc().elements.find(e => e.id === id)! as unknown as Record<string, unknown>;
const assets = { products: [{ id: "a2", kind: "product", url: "u2", width: 100, height: 100, mime: "image/png" }], logos: [], backgrounds: [], cutouts: [], brand: { colors: ["#112233"], fonts: ["Liberation Sans"] } };

beforeEach(() => { vi.useRealTimers(); useEditor.getState().reset(); boot(); mockApi.mockReset(); mockApi.mockImplementation(async (path: string) => { if (String(path).includes("editor-assets")) return assets; throw new Error("unexpected " + path); }); });
afterEach(cleanup);

describe("layers panel", () => {
  it("lists layers top-first, selects, toggles visibility and lock, and reorders by drag and drop", async () => {
    wrap(<LeftPanel campaignId="c" />);
    const list = screen.getByRole("list", { name: "Layers" }); const names = () => within(list).getAllByRole("listitem").map(li => li.textContent);
    expect(names()[0]).toContain("CTA"); expect(names().at(-1)).toContain("Background");
    fireEvent.click(screen.getByRole("button", { name: "Select layer Headline" })); expect(useEditor.getState().selection).toEqual(["head"]);
    fireEvent.click(within(list).getAllByRole("button", { name: "Hide layer" })[1]); expect(layer("head").visible).toBe(false);
    fireEvent.click(within(list).getAllByRole("button", { name: "Lock layer" })[1]); expect(layer("head").locked).toBe(true);
    const rows = within(list).getAllByRole("listitem"); expect(rows[1].getAttribute("draggable")).toBe("false");                    // locked layers cannot be dragged
    const before = doc().elements.map(e => e.id); fireEvent.dragStart(rows[0]); fireEvent.dragOver(rows[2]); fireEvent.drop(rows[2]);   // move CTA (top) to the bottom of the stack
    expect(doc().elements.map(e => e.id)).toEqual([before[2], before[0], before[1]]);
    act(() => useEditor.getState().undo()); expect(doc().elements.map(e => e.id)).toEqual(before);
  });
  it("adds elements from the Elements tab and selects the new layer", () => {
    wrap(<LeftPanel campaignId="c" />); fireEvent.click(screen.getByRole("tab", { name: /Elements/ })); fireEvent.click(screen.getByRole("button", { name: "Rectangle" }));
    expect(doc().elements).toHaveLength(4); expect(useEditor.getState().selection).toEqual([doc().elements[3].id]); expect(doc().elements[3]).toMatchObject({ type: "shape", shape: "rect" });
  });
  it("viewers get disabled controls", () => { useEditor.getState().load(makeView(), { commands: vi.fn(), undo: vi.fn(), redo: vi.fn() }, { readOnly: true }); wrap(<LeftPanel campaignId="c" />); fireEvent.click(screen.getByRole("tab", { name: /Elements/ })); expect((screen.getByRole("button", { name: "Text" }) as HTMLButtonElement).disabled).toBe(true); });
});

describe("properties panel", () => {
  it("text: edits content, font, size, align, colour and validates numbers", () => {
    act(() => useEditor.getState().select(["head"])); wrap(<PropertiesPanel campaignId="c" onAi={() => {}} />);
    fireEvent.change(screen.getByLabelText("Text content"), { target: { value: "Edited!" } }); expect(layer("head").text).toBe("Edited!");
    fireEvent.change(screen.getByLabelText("Font family"), { target: { value: "DejaVu Sans" } }); expect(layer("head").font_family).toBe("DejaVu Sans");
    const size = screen.getByLabelText("Font size"); fireEvent.change(size, { target: { value: "40" } }); fireEvent.blur(size); expect(layer("head").font_size).toBe(40);
    fireEvent.change(size, { target: { value: "abc" } }); fireEvent.blur(size); expect(layer("head").font_size).toBe(40);                              // garbage rejected, value restored
    fireEvent.change(size, { target: { value: "9999" } }); fireEvent.blur(size); expect(layer("head").font_size).toBe(600);                         // clamped
    fireEvent.click(within(screen.getByRole("group", { name: "Text align" })).getByTitle("Center")); expect(layer("head").align).toBe("center");
    fireEvent.change(screen.getByLabelText("Text color"), { target: { value: "#ff0000" } }); expect(layer("head").color).toBe("#ff0000");
    fireEvent.change(screen.getByLabelText("Text color"), { target: { value: "red" } }); expect(layer("head").color).toBe("#ff0000");                  // invalid hex never dispatched
  });
  it("geometry: position, size, rotation, opacity; locked layers are read-only", () => {
    act(() => useEditor.getState().select(["prod"])); wrap(<PropertiesPanel campaignId="c" onAi={() => {}} />);
    const num = (l: string, v: string) => { const f = screen.getByRole("textbox", { name: l }); fireEvent.change(f, { target: { value: v } }); fireEvent.blur(f); };
    num("X", "20"); num("Y", "30"); num("Width", "50"); num("Height", "60"); num("Rotation", "45"); num("Opacity", "50");
    expect(layer("prod")).toMatchObject({ x: 20, y: 30, w: 50, h: 60, rotation: 45, opacity: 0.5 });
    fireEvent.click(screen.getByRole("button", { name: "Lock layer" })); expect((screen.getByRole("textbox", { name: "Width" }) as HTMLInputElement).disabled).toBe(true); expect((screen.getByRole("button", { name: "AI edit this layer" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("image: fit, crop and shadow; button: label and colours; multi-select shows alignment tools", () => {
    act(() => useEditor.getState().select(["prod"])); const { rerender } = wrap(<PropertiesPanel campaignId="c" onAi={() => {}} />);
    fireEvent.click(within(screen.getByRole("group", { name: "Image fit" })).getByText("Cover")); expect(layer("prod").fit).toBe("cover");
    const cw = screen.getByLabelText("Crop w"); fireEvent.change(cw, { target: { value: "50" } }); fireEvent.blur(cw); expect(layer("prod").crop).toMatchObject({ w: 0.5 });
    fireEvent.click(screen.getByText("Reset crop")); expect(layer("prod").crop).toBeNull();
    act(() => useEditor.getState().select(["cta"])); rerender(<QueryClientProvider client={new QueryClient()}><PropertiesPanel campaignId="c" onAi={() => {}} /></QueryClientProvider>);
    fireEvent.change(screen.getByLabelText("CTA text"), { target: { value: "Buy" } }); expect(layer("cta").label).toBe("Buy");
    fireEvent.change(screen.getByLabelText("CTA background"), { target: { value: "#010203" } }); expect(layer("cta").fill).toBe("#010203");
    act(() => useEditor.getState().select(["head", "cta"])); rerender(<QueryClientProvider client={new QueryClient()}><PropertiesPanel campaignId="c" onAi={() => {}} /></QueryClientProvider>);
    expect(screen.getByText("2 layers selected")).toBeTruthy(); fireEvent.click(screen.getByRole("button", { name: "Align right" })); expect((layer("head").x as number) + (layer("head").w as number)).toBe(layer("cta").x as number + (layer("cta").w as number));
  });
  it("background: type, colour and overlay", () => {
    act(() => useEditor.getState().select(["background"])); wrap(<PropertiesPanel campaignId="c" onAi={() => {}} />);
    fireEvent.click(within(screen.getByRole("group", { name: "Background type" })).getByText("Gradient")); expect(doc().background.type).toBe("gradient");
    fireEvent.change(screen.getByLabelText("Background color"), { target: { value: "#222222" } }); expect(doc().background.color).toBe("#222222");
    fireEvent.click(screen.getByText("Add colour overlay")); expect(doc().background.scrims).toHaveLength(1);
  });
});

describe("quick edit", () => {
  it("lets a non-designer change copy, CTA, colours and shows no layer controls", () => {
    wrap(<QuickEdit campaignId="c" onAi={() => {}} />);
    fireEvent.change(screen.getByLabelText("Headline text"), { target: { value: "New headline" } }); expect(layer("head").text).toBe("New headline");
    fireEvent.change(screen.getByLabelText("Button text"), { target: { value: "Go" } }); expect(layer("cta").label).toBe("Go");
    fireEvent.click(within(screen.getByRole("group", { name: "Headline alignment" })).getAllByRole("button")[1]); expect(layer("head").align).toBe("center");
    fireEvent.change(screen.getByRole("textbox", { name: "Button color" }), { target: { value: "#0a0b0c" } }); expect(layer("cta").fill).toBe("#0a0b0c");
    expect(screen.queryByLabelText("Rotation")).toBeNull(); expect(screen.queryByRole("list", { name: "Layers" })).toBeNull();
  });
  it("opens the picker to replace the product image", async () => {
    wrap(<QuickEdit campaignId="c" onAi={() => {}} />); fireEvent.click(screen.getByText("Replace product image…")); fireEvent.click(await screen.findByRole("button", { name: /Use products a2/ }));
    expect(layer("prod").asset_id).toBe("a2");
  });
});

describe("AI edit dialog (selected element only)", () => {
  const ready = (over: Record<string, unknown> = {}) => ({ id: "job1", status: "ready", target_layer_id: "background", target_type: "background", source_revision: 0, current_revision: 0, stale: false, target_exists: true, target_changed: false, candidates: [{ index: 0, asset_id: "n1", url: "u1" }, { index: 1, asset_id: "n2", url: "u2" }], error_message: null, provider: "mock", usage: {}, ...over });
  const api2 = (job: Record<string, unknown>, extra: (p: string, o?: { method?: string; json?: unknown }) => unknown = () => undefined) => mockApi.mockImplementation(async (p: string, o?: { method?: string; json?: unknown }) => {
    if (p.includes("editor-assets")) return assets; const x = extra(p, o); if (x !== undefined) return x;
    if (p.endsWith("/regenerate")) return { ...job, status: "pending" }; if (p === "/regeneration-jobs/job1") return job; throw new Error("unexpected " + p);
  });
  it("previews a candidate on the canvas without changing the document, then accept applies only that layer", async () => {
    const accepted = makeDoc(); accepted.background = { ...accepted.background, type: "image", asset_id: "n2" };
    api2(ready(), (p, o) => (p.endsWith("/accept") ? { document: makeView("doc1", 1, accepted) } : undefined));
    const onClose = vi.fn(); wrap(<AiEditDialog layerId="background" documentId="doc1" onClose={onClose} />);
    fireEvent.change(screen.getByLabelText("Instruction"), { target: { value: "calm sunrise" } }); fireEvent.click(screen.getByRole("button", { name: "Generate options" }));
    await screen.findByRole("radiogroup", { name: "Candidates" }, { timeout: 4000 });
    expect(useEditor.getState().preview).toMatchObject({ type: "REGENERATE_ELEMENT_ACCEPTED", payload: { layer_id: "background", asset_id: "n1" } }); expect(doc().background.type).toBe("solid");     // preview only
    fireEvent.click(screen.getByRole("radio", { name: /Option 2/ })); await waitFor(() => expect((useEditor.getState().preview!.payload as { asset_id: string }).asset_id).toBe("n2"));
    fireEvent.click(screen.getByRole("button", { name: "Accept option 2" })); await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(doc().background).toMatchObject({ type: "image", asset_id: "n2" }); expect(useEditor.getState().preview).toBeNull(); expect(doc().elements.map(e => e.id)).toEqual(["prod", "head", "cta"]);
    expect(mockApi.mock.calls.find(c => String(c[0]).endsWith("/accept"))![1].json).toMatchObject({ candidate_index: 1 });
    act(() => useEditor.getState().undo()); expect(doc().background.type).toBe("solid");                                                          // accepted AI edit is undoable
  });
  it("reject closes the job, clears the preview and leaves the document untouched", async () => {
    const rejected = vi.fn(); api2(ready(), (p) => (p.endsWith("/reject") ? (rejected(), { ...ready(), status: "rejected" }) : undefined));
    const onClose = vi.fn(); wrap(<AiEditDialog layerId="background" documentId="doc1" onClose={onClose} />);
    fireEvent.change(screen.getByLabelText("Instruction"), { target: { value: "calm sunrise" } }); fireEvent.click(screen.getByRole("button", { name: "Generate options" })); await screen.findByRole("radiogroup", { name: "Candidates" }, { timeout: 4000 });
    const before = JSON.stringify(doc()); fireEvent.click(screen.getByRole("button", { name: "Reject all" })); await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(rejected).toHaveBeenCalled(); expect(JSON.stringify(doc())).toBe(before); expect(useEditor.getState().preview).toBeNull();
  });
  it("a layer edited while generating needs explicit confirmation before it is replaced", async () => {
    const accepted = makeDoc(); accepted.background = { ...accepted.background, type: "image", asset_id: "n1" }; let first = true;
    api2(ready({ stale: true, target_changed: true }), (p) => { if (p.endsWith("/accept")) { if (first) { first = false; throw new ApiError(409, "The layer was edited", {}, "target_changed"); } return { document: makeView("doc1", 2, accepted) }; } });
    const onClose = vi.fn(); wrap(<AiEditDialog layerId="background" documentId="doc1" onClose={onClose} />);
    fireEvent.change(screen.getByLabelText("Instruction"), { target: { value: "calm sunrise" } }); fireEvent.click(screen.getByRole("button", { name: "Generate options" })); await screen.findByRole("radiogroup", { name: "Candidates" }, { timeout: 4000 });
    expect(screen.getByText(/The design changed while this was generating/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Accept option 1" })); const apply = await screen.findByRole("button", { name: /reviewed it/ }); expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(apply); await waitFor(() => expect(onClose).toHaveBeenCalled()); expect(mockApi.mock.calls.filter(c => String(c[0]).endsWith("/accept")).at(-1)![1].json).toMatchObject({ acknowledge_changes: true });
  });
  it("failures are shown and nothing changes; product layers explain the no-AI rule and require opt-in", async () => {
    api2(ready({ status: "failed", error_message: "ImageProviderError: upstream 503" })); const before = JSON.stringify(doc());
    const { unmount } = wrap(<AiEditDialog layerId="prod" documentId="doc1" onClose={() => {}} />);
    expect(screen.getByText(/never redrawn by AI/)).toBeTruthy(); expect((screen.getByLabelText(/Let AI create a/) as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Generate options" })); expect(await screen.findByText(/upstream 503/, {}, { timeout: 4000 })).toBeTruthy(); expect(JSON.stringify(doc())).toBe(before);
    expect(mockApi.mock.calls.find(c => String(c[0]).endsWith("/regenerate"))![1].json).toMatchObject({ layer_id: "prod", allow_ai_product: false, expected_revision: 0 }); unmount();
  });
});
