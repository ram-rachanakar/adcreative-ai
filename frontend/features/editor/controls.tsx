"use client";
import { useEffect, useState } from "react";
import clsx from "clsx";

export function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="border-b border-line px-3 py-3"><h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-400">{title}</h4><div className="space-y-2">{children}</div></section>;
}
export function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="flex items-center justify-between gap-2 text-xs text-zinc-300"><span className="w-24 shrink-0">{label}</span><span className="flex flex-1 justify-end">{children}</span></label>;
}

/** Number input that commits on blur/Enter (not per keystroke), supports arrows, clamps to [min,max] and rejects non-numbers. */
export function NumberField({ value, onCommit, min, max, step = 1, label, disabled, suffix }: { value: number; onCommit: (v: number) => void; min?: number; max?: number; step?: number; label: string; disabled?: boolean; suffix?: string }) {
  const [txt, setTxt] = useState(String(Math.round(value * 100) / 100));
  useEffect(() => setTxt(String(Math.round(value * 100) / 100)), [value]);
  const commit = (raw: string) => {
    const n = Number(raw); if (raw.trim() === "" || !Number.isFinite(n)) { setTxt(String(value)); return; }
    const c = Math.min(max ?? Infinity, Math.max(min ?? -Infinity, n)); setTxt(String(c)); if (c !== value) onCommit(c);
  };
  return (
    <span className="relative inline-flex w-24"><input aria-label={label} className="input !py-1 text-right text-xs" inputMode="decimal" disabled={disabled} value={txt} onChange={e => setTxt(e.target.value)} onBlur={e => commit(e.target.value)}
      onKeyDown={e => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); else if (e.key === "ArrowUp" || e.key === "ArrowDown") { e.preventDefault(); const n = Number(txt) + (e.key === "ArrowUp" ? step : -step) * (e.shiftKey ? 10 : 1); commit(String(n)); } else if (e.key === "Escape") setTxt(String(value)); }} />
      {suffix && <span className="pointer-events-none absolute left-2 top-1.5 text-[10px] text-zinc-500">{suffix}</span>}</span>
  );
}

const HEX = /^#[0-9a-fA-F]{6}$/;
export function ColorField({ value, onCommit, label, disabled }: { value: string; onCommit: (v: string) => void; label: string; disabled?: boolean }) {
  const [txt, setTxt] = useState(value); useEffect(() => setTxt(value), [value]);
  return (
    <span className="inline-flex items-center gap-1.5">
      <input type="color" aria-label={`${label} picker`} disabled={disabled} className="h-7 w-7 cursor-pointer rounded border border-line bg-transparent p-0" value={HEX.test(value) ? value : "#000000"} onChange={e => onCommit(e.target.value)} />
      <input aria-label={label} className={clsx("input !w-20 !py-1 text-xs", !HEX.test(txt) && "!border-red-500")} disabled={disabled} value={txt} onChange={e => { setTxt(e.target.value); if (HEX.test(e.target.value)) onCommit(e.target.value.toLowerCase()); }} onBlur={() => setTxt(value)} />
    </span>
  );
}

export function Segmented<T extends string | number>({ value, options, onChange, label, disabled }: { value: T; options: { v: T; label: React.ReactNode; title?: string }[]; onChange: (v: T) => void; label: string; disabled?: boolean }) {
  return (
    <span role="group" aria-label={label} className="inline-flex overflow-hidden rounded-lg border border-line">
      {options.map(o => <button key={String(o.v)} type="button" title={o.title} disabled={disabled} aria-pressed={o.v === value} onClick={() => onChange(o.v)} className={clsx("px-2 py-1 text-xs", o.v === value ? "bg-accent text-white" : "bg-panel text-zinc-300 hover:text-white")}>{o.label}</button>)}
    </span>
  );
}

export function IconBtn({ label, onClick, disabled, active, children }: { label: string; onClick?: () => void; disabled?: boolean; active?: boolean; children: React.ReactNode }) {
  return <button type="button" aria-label={label} title={label} disabled={disabled} onClick={onClick} className={clsx("inline-flex h-8 w-8 items-center justify-center rounded-lg border border-line text-zinc-300 hover:text-white disabled:opacity-40", active && "bg-accent/20 text-white")}>{children}</button>;
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  useEffect(() => { const k = (e: KeyboardEvent) => e.key === "Escape" && onClose(); window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" role="dialog" aria-modal="true" aria-label={title} onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div className={clsx("max-h-[88vh] w-full overflow-auto rounded-xl border border-line bg-panel p-5 shadow-2xl", wide ? "max-w-4xl" : "max-w-lg")}>
        <div className="mb-4 flex items-center justify-between"><h3 className="text-base font-semibold">{title}</h3><button aria-label="Close" onClick={onClose} className="text-zinc-400 hover:text-white">✕</button></div>{children}
      </div>
    </div>
  );
}
