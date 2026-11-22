"use client";
/** Interactive canvas. Konva is only the interaction + drawing layer: every pixel decision (text wrap, image fit, gradient) comes from lib/editor/scene.ts,
 *  which mirrors the backend SVG renderer, and every change goes back through the command engine in the store. Nothing here owns document state. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Circle, Group, Image as KImage, Layer as KLayer, Line, Rect, Shape, Stage, Transformer } from "react-konva";
import type Konva from "konva";
import { apply, cmd, newLayerId } from "@/lib/editor/commands";
import { fitZoom, round, safeArea, snapBox } from "@/lib/editor/geometry";
import { backgroundImagePlan, gradientPoints, imagePlan, nodeToBox, textPlan } from "@/lib/editor/scene";
import { canvasMeasure, canvasMetrics } from "@/lib/editor/textLayout";
import { BG, type ButtonLayer, type ImageLayer, type Layer, type ShapeLayer, type TextLayer } from "@/lib/editor/types";
import { useEditor } from "@/stores/editor";

function useImg(url?: string): HTMLImageElement | null {
  const [img, setImg] = useState<HTMLImageElement | null>(null);
  useEffect(() => { if (!url) { setImg(null); return; } let ok = true; const i = new window.Image(); i.crossOrigin = "anonymous"; i.onload = () => ok && setImg(i); i.onerror = () => ok && setImg(null); i.src = url; return () => { ok = false; }; }, [url]);
  return img;
}

function TextNode({ l, fontsReady }: { l: TextLayer | ButtonLayer; fontsReady: boolean }) {
  const plan = useMemo(() => textPlan(l, canvasMeasure, canvasMetrics), [l, fontsReady]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Group listening={false}>
      {l.type === "button" && <Rect x={l.x} y={l.y} width={l.w} height={l.h} cornerRadius={Math.min(l.radius, l.h / 2)} fill={l.fill} />}
      <Shape sceneFunc={(ctx) => {
        const c = ctx._context as CanvasRenderingContext2D & { letterSpacing?: string; textRendering?: string };
        c.textRendering = "geometricPrecision";   // sub-pixel glyph positioning like the server renderer (default snaps glyphs to whole pixels on Linux)
        c.font = `${plan.weight} ${plan.fontSize}px "${plan.family}"`; c.fillStyle = plan.color; c.textAlign = plan.anchor; c.textBaseline = "alphabetic"; c.letterSpacing = `${plan.spacing}px`;
        for (const ln of plan.lines) c.fillText(ln.text, ln.x, ln.baseline);
        c.letterSpacing = "0px";
      }} />
    </Group>
  );
}

function ImageNode({ l, url, info }: { l: ImageLayer; url?: string; info?: { width?: number; height?: number; missing?: boolean } }) {
  const img = useImg(url);
  if (!img) return <Rect x={l.x} y={l.y} width={l.w} height={l.h} fill="#00000010" stroke={info?.missing ? "#ef4444" : "#9ca3af"} dash={[6, 4]} listening={false} />;
  const p = imagePlan(l, { width: img.naturalWidth, height: img.naturalHeight });
  const shadow = l.shadow === "drop" ? { shadowColor: "#000", shadowBlur: Math.max(2, p.h * 0.06), shadowOffsetY: Math.max(1, p.h * 0.02), shadowOpacity: 0.35 } : {};
  return (
    <Group listening={false} clipFunc={l.corner_radius || l.fit === "cover" ? (c) => { c.beginPath(); const r = Math.min(l.corner_radius, p.w / 2, p.h / 2); (c as unknown as CanvasRenderingContext2D).roundRect(p.x, p.y, p.w, p.h, r); } : undefined}>
      {l.shadow === "contact" && <Shape sceneFunc={(ctx) => { const c = ctx._context as CanvasRenderingContext2D; const g = c.createRadialGradient(0, 0, 0, 0, 0, 1); g.addColorStop(0, "rgba(0,0,0,.38)"); g.addColorStop(1, "rgba(0,0,0,0)"); c.save(); c.translate(p.x + p.w / 2, p.y + p.h - p.h * 0.01); c.scale(p.w * 0.46, Math.max(2, p.h * 0.045)); c.fillStyle = g; c.beginPath(); c.arc(0, 0, 1, 0, Math.PI * 2); c.fill(); c.restore(); }} />}
      <KImage image={img} x={p.x} y={p.y} width={p.w} height={p.h} crop={{ x: p.src.x, y: p.src.y, width: p.src.w, height: p.src.h }} {...shadow} />
    </Group>
  );
}

function Background() {
  const real = useEditor(s => s.doc)!; const preview = useEditor(s => s.preview); const assets = useEditor(s => s.assets);
  const doc = useMemo(() => { if (!preview) return real; try { return apply(real, preview).doc; } catch { return real; } }, [real, preview]);
  const bg = doc.background; const { width: W, height: H } = doc.canvas;
  const img = useImg(bg.type === "image" && bg.asset_id ? assets[bg.asset_id]?.url : undefined);
  const g = gradientPoints(bg.angle, W, H);
  return (
    <Group listening={false}>
      <Rect width={W} height={H} fill={bg.type === "gradient" ? undefined : bg.color}
        {...(bg.type === "gradient" ? { fillLinearGradientStartPoint: g.start, fillLinearGradientEndPoint: g.end, fillLinearGradientColorStops: [0, bg.color, 1, bg.gradient_to ?? bg.color] } : {})} />
      {img && (() => { const p = backgroundImagePlan(bg, { width: img.naturalWidth, height: img.naturalHeight }, W, H); return <KImage image={img} x={p.x} y={p.y} width={p.w} height={p.h} crop={{ x: p.src.x, y: p.src.y, width: p.src.w, height: p.src.h }} />; })()}
      {bg.scrims.map((s, i) => { const w = s.w ?? W, h = s.h ?? H; return s.fade === "none"
        ? <Rect key={i} x={s.x} y={s.y} width={w} height={h} fill={s.color} opacity={s.opacity} />
        : <Rect key={i} x={s.x} y={s.y} width={w} height={h} fillLinearGradientStartPoint={{ x: 0, y: 0 }} fillLinearGradientEndPoint={{ x: w, y: 0 }}
            fillLinearGradientColorStops={s.fade === "right" ? [0, s.color, s.solid_until, s.color, 1, "transparent"] : [0, "transparent", 1 - s.solid_until, s.color, 1, s.color]} opacity={s.opacity} />; })}
    </Group>
  );
}

export default function CanvasStage({ fontsReady, onContextMenu, onEditText }: { fontsReady: boolean; onContextMenu: (id: string | null, x: number, y: number) => void; onEditText: (id: string) => void }) {
  const st = useEditor(); const real = st.doc!; const preview = st.preview;
  const doc = useMemo(() => { if (!preview) return real; try { return apply(real, preview).doc; } catch { return real; } }, [real, preview]);   // candidate preview: never persisted
  const { width: W, height: H } = doc.canvas;
  const box = useRef<HTMLDivElement>(null); const stageRef = useRef<Konva.Stage>(null); const trRef = useRef<Konva.Transformer>(null);
  const nodes = useRef(new Map<string, Konva.Node>());
  useEffect(() => { (window as unknown as { __editorStage?: Konva.Stage | null }).__editorStage = stageRef.current; });   // debugging / e2e handle (read-only use)
  const [size, setSize] = useState({ width: 800, height: 600 }); const [space, setSpace] = useState(false);

  useEffect(() => { const el = box.current; if (!el) return; const ro = new ResizeObserver(() => setSize({ width: el.clientWidth, height: el.clientHeight })); ro.observe(el); setSize({ width: el.clientWidth, height: el.clientHeight }); return () => ro.disconnect(); }, []);
  const zoom = st.fit ? fitZoom(doc.canvas, size) : st.zoom;
  const origin = st.fit ? { x: (size.width - W * zoom) / 2, y: (size.height - H * zoom) / 2 } : st.pan;
  useEffect(() => { useEditor.setState({ stage: { x: origin.x, y: origin.y, zoom } }); }, [origin.x, origin.y, zoom]);
  useEffect(() => { if (st.fit && Math.abs(st.zoom - zoom) > 1e-6) useEditor.setState({ zoom }); }, [st.fit, zoom, st.zoom]);
  useEffect(() => { const d = (e: KeyboardEvent) => { if (e.code === "Space" && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) { setSpace(true); e.preventDefault(); } }; const u = (e: KeyboardEvent) => e.code === "Space" && setSpace(false); window.addEventListener("keydown", d); window.addEventListener("keyup", u); return () => { window.removeEventListener("keydown", d); window.removeEventListener("keyup", u); }; }, []);

  const editable = !st.readOnly && !st.conflict;
  const selectedLayers = doc.elements.filter(e => st.selection.includes(e.id));
  const canTransform = editable && st.mode === "advanced" && selectedLayers.length > 0 && selectedLayers.every(l => !l.locked && l.visible);
  useEffect(() => { const tr = trRef.current; if (!tr) return; tr.nodes(canTransform ? selectedLayers.map(l => nodes.current.get(l.id)).filter(Boolean) as Konva.Node[] : []); tr.getLayer()?.batchDraw(); }, [canTransform, st.selection, doc]); // eslint-disable-line react-hooks/exhaustive-deps

  const others = (ids: string[]) => doc.elements.filter(e => e.visible && !ids.includes(e.id)).map(e => ({ x: e.x, y: e.y, w: e.w, h: e.h }));
  const dragStart = useRef<Map<string, { x: number; y: number }>>(new Map());
  const onDragStart = (l: Layer) => {
    if (!st.selection.includes(l.id)) st.select([l.id]);
    const ids = st.selection.includes(l.id) ? st.selection : [l.id]; dragStart.current = new Map(ids.map(i => { const n = nodes.current.get(i)!; return [i, { x: n.x(), y: n.y() }]; }));
  };
  const onDragMove = (l: Layer, e: Konva.KonvaEventObject<DragEvent>) => {
    const n = e.target; const start = dragStart.current.get(l.id); if (!start) return;
    const ids = [...dragStart.current.keys()]; const b = { x: n.x() - l.w / 2, y: n.y() - l.h / 2, w: l.w, h: l.h };
    const snap = snapBox(b, doc.canvas, others(ids), { threshold: 6 / zoom, grid: st.snapGrid ? st.gridSize : undefined });
    n.position({ x: snap.x + l.w / 2, y: snap.y + l.h / 2 }); st.setGuides(snap.guides);
    const dx = n.x() - start.x, dy = n.y() - start.y;
    for (const [id, s] of dragStart.current) if (id !== l.id) nodes.current.get(id)?.position({ x: s.x + dx, y: s.y + dy });
  };
  const onDragEnd = (l: Layer) => {
    st.setGuides([]); const cmds = [...dragStart.current.keys()].map(id => { const el = doc.elements.find(x => x.id === id)!; const n = nodes.current.get(id)!; return cmd("MOVE_ELEMENT", { layer_id: id, x: round(n.x() - el.w / 2), y: round(n.y() - el.h / 2) }); });
    if (!st.dispatchBatch(cmds, cmds.length > 1 ? "Move layers" : "Move")) cmds.forEach((_, i) => { const id = [...dragStart.current.keys()][i]; const s = dragStart.current.get(id)!; nodes.current.get(id)?.position(s); });
  };
  const onTransformEnd = () => {
    const cmds = selectedLayers.flatMap(l => { const n = nodes.current.get(l.id)!; const b = nodeToBox({ cx: n.x(), cy: n.y(), scaleX: n.scaleX(), scaleY: n.scaleY(), rotation: n.rotation() }, l.w, l.h); n.scale({ x: 1, y: 1 });
      return [cmd("RESIZE_ELEMENT", { layer_id: l.id, x: round(b.x), y: round(b.y), w: round(b.w), h: round(b.h) }), cmd("ROTATE_ELEMENT", { layer_id: l.id, rotation: round(b.rotation) })]; });
    st.dispatchBatch(cmds.filter((c, i) => !(c.type === "ROTATE_ELEMENT" && c.payload.rotation === selectedLayers[Math.floor(i / 2)].rotation)), "Transform");
    // snap the node back to the (validated) document state next render
    useEditor.setState(s => ({ doc: s.doc ? { ...s.doc } : s.doc }));
  };

  const wheel = (e: Konva.KonvaEventObject<WheelEvent>) => {
    e.evt.preventDefault(); const stage = stageRef.current!; const p = stage.getPointerPosition()!;
    if (e.evt.ctrlKey || e.evt.metaKey) {
      const nz = Math.min(8, Math.max(0.05, zoom * (e.evt.deltaY > 0 ? 0.92 : 1.08))); const wx = (p.x - origin.x) / zoom, wy = (p.y - origin.y) / zoom;
      st.setPan({ x: p.x - wx * nz, y: p.y - wy * nz }); st.setZoom(nz, false);
    } else { st.setPan({ x: origin.x - e.evt.deltaX, y: origin.y - e.evt.deltaY }); if (st.fit) st.setZoom(zoom, false); }
  };
  const safe = st.showSafe ? safeArea(doc.canvas, st.view?.artboard.safe_zone ?? null) : null;
  const gridLines = useMemo(() => { if (!st.showGrid) return []; const out: number[][] = []; for (let x = st.gridSize; x < W; x += st.gridSize) out.push([x, 0, x, H]); for (let y = st.gridSize; y < H; y += st.gridSize) out.push([0, y, W, y]); return out.slice(0, 600); }, [st.showGrid, st.gridSize, W, H]);
  const R = (v: number) => v / zoom;

  const register = useCallback((id: string) => (n: Konva.Node | null) => { if (n) nodes.current.set(id, n); else nodes.current.delete(id); }, []);

  return (
    <div ref={box} className="relative h-full w-full overflow-hidden bg-[#1b1e26]" data-testid="canvas-container" style={{ cursor: space ? "grab" : "default" }}>
      <Stage ref={stageRef} width={size.width} height={size.height} x={origin.x} y={origin.y} scaleX={zoom} scaleY={zoom} draggable={space}
        onDragEnd={e => { if (e.target === stageRef.current) st.setPan({ x: e.target.x(), y: e.target.y() }); }} onDragStart={() => { if (st.fit) st.setZoom(zoom, false); }}
        onWheel={wheel} onMouseDown={e => { if (e.target === stageRef.current || e.target.name() === "artboard") st.clearSelection(); }}
        onContextMenu={e => { e.evt.preventDefault(); const id = e.target.name() === "layer-hit" ? e.target.getParent()?.id() ?? null : null; if (id) st.select([id]); onContextMenu(id, e.evt.clientX, e.evt.clientY); }}>
        <KLayer>
          <Rect name="artboard" width={W} height={H} fill="#fff" shadowColor="#000" shadowBlur={R(24)} shadowOpacity={0.5} />
          <Group clip={{ x: 0, y: 0, width: W, height: H }}>
            <Background />
            {doc.elements.map(l => l.visible && (
              <Group key={l.id} id={l.id} ref={register(l.id)} x={l.x + l.w / 2} y={l.y + l.h / 2} offsetX={l.w / 2} offsetY={l.h / 2} rotation={l.rotation} opacity={l.opacity}
                draggable={editable && !l.locked && !space} onDragStart={() => onDragStart(l)} onDragMove={e => onDragMove(l, e)} onDragEnd={() => onDragEnd(l)}
                onClick={e => st.select([l.id], e.evt.shiftKey)} onTap={() => st.select([l.id])} onDblClick={() => (l.type === "text" || l.type === "button") && editable && !l.locked && onEditText(l.id)}>
                <Rect name="layer-hit" width={l.w} height={l.h} fill="transparent" />
                <Group x={-l.x} y={-l.y}>{/* children are drawn in document coordinates; this group re-bases them onto the layer's own box */}
                  {l.type === "text" || l.type === "button" ? <TextNode l={l} fontsReady={fontsReady} />
                    : l.type === "image" ? <ImageNode l={l} url={st.assets[l.asset_id]?.url} info={st.assets[l.asset_id]} />
                    : l.shape === "ellipse" ? <Shape listening={false} sceneFunc={(ctx, n) => { const s = l as ShapeLayer; ctx.beginPath(); ctx.ellipse(s.x + s.w / 2, s.y + s.h / 2, s.w / 2, s.h / 2, 0, 0, Math.PI * 2); ctx.fillStrokeShape(n); }} fill={l.fill} stroke={l.stroke ?? undefined} strokeWidth={l.stroke ? l.stroke_width : 0} />
                    : <Rect listening={false} x={l.x} y={l.y} width={l.w} height={l.h} cornerRadius={l.radius} fill={l.fill} stroke={l.stroke ?? undefined} strokeWidth={l.stroke ? l.stroke_width : 0} />}
                </Group>
              </Group>
            ))}
          </Group>
          {safe && <><Rect name="chrome" x={0} y={0} width={W} height={safe.y} fill="#ef444422" listening={false} /><Rect name="chrome" x={0} y={safe.y + safe.h} width={W} height={H - safe.y - safe.h} fill="#ef444422" listening={false} /><Rect name="chrome" x={safe.x} y={safe.y} width={safe.w} height={safe.h} stroke="#22d3a6" strokeWidth={R(1)} dash={[R(6), R(4)]} listening={false} /></>}
          {gridLines.map((p, i) => <Line name="chrome" key={i} points={p} stroke="#00000022" strokeWidth={R(0.5)} listening={false} />)}
          <Rect name="chrome" width={W} height={H} stroke="#7c5cff" strokeWidth={R(1)} listening={false} />
          {st.guides.map((g, i) => <Line name="chrome" key={i} points={g.axis === "x" ? [g.pos, -R(2000), g.pos, R(2000) + H] : [-R(2000), g.pos, R(2000) + W, g.pos]} stroke="#ff2d95" strokeWidth={R(1)} dash={[R(4), R(3)]} listening={false} />)}
          {selectedLayers.filter(l => l.visible && !canTransform).map(l => <Group name="chrome" key={l.id} x={l.x + l.w / 2} y={l.y + l.h / 2} offsetX={l.w / 2} offsetY={l.h / 2} rotation={l.rotation} listening={false}><Rect width={l.w} height={l.h} stroke={l.locked ? "#f59e0b" : "#7c5cff"} strokeWidth={R(1.5)} /></Group>)}
          <Transformer name="chrome" ref={trRef} rotateEnabled keepRatio={selectedLayers.every(l => l.type === "image")} rotationSnaps={[0, 45, 90, 135, 180, -135, -90, -45]} rotationSnapTolerance={4}
            anchorSize={10} anchorCornerRadius={2} borderStroke="#7c5cff" anchorStroke="#7c5cff" boundBoxFunc={(o, n) => (Math.abs(n.width) < 8 || Math.abs(n.height) < 8 ? o : n)} onTransformEnd={onTransformEnd} />
          {st.selection.length === 0 && <Circle x={0} y={0} radius={0} />}
        </KLayer>
      </Stage>
    </div>
  );
}

export { newLayerId, BG };
