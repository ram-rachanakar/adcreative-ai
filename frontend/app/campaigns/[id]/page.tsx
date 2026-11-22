"use client";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useState } from "react";
import { AlertTriangle, Loader2, Upload } from "lucide-react";
import Link from "next/link";
import Shell from "@/components/Shell";
import { api, ApiError, runJob } from "@/lib/api";
import { colorsValid, parseColors, productEditSchema, type ProductEditForm } from "@/lib/validation";
import { useSession } from "@/stores/session";
import type { Brand, Campaign, Product } from "@/types";

export default function CampaignReview() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const orgId = useSession(s => s.orgId);
  const campaign = useQuery({ queryKey: ["campaign", id, orgId], queryFn: () => api<Campaign>(`/campaigns/${id}`), enabled: !!orgId });
  const product = useQuery({
    queryKey: ["product", id, orgId], enabled: !!orgId, retry: false,
    queryFn: async () => { try { return await api<Product>(`/campaigns/${id}/product`); } catch (e) { if (e instanceof ApiError && e.status === 404) return null; throw e; } },
  });
  const brands = useQuery({ queryKey: ["brands", orgId], queryFn: () => api<Brand[]>("/brand-profiles"), enabled: !!orgId });
  const brand = brands.data?.find(b => b.id === (campaign.data?.brief as { brand_profile_id?: string } | undefined)?.brand_profile_id);

  const analyze = useMutation({
    mutationFn: () => runJob(`/campaigns/${id}/analyze`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["product", id] }); qc.invalidateQueries({ queryKey: ["campaign", id] }); qc.invalidateQueries({ queryKey: ["brands"] }); },
  });

  return (
    <Shell>
      <div className="mb-6 flex items-center justify-between">
        <div><h1 className="text-2xl font-semibold">{campaign.data?.name ?? "Campaign"}</h1>
          <p className="text-sm text-zinc-400">{campaign.data?.product_url ?? "No product URL"}</p></div>
        <div className="flex gap-2">
        {product.data && <Link href={`/campaigns/${id}/concepts`} className="btn">Continue to concepts →</Link>}
        <button className="btn" disabled={!campaign.data?.product_url || analyze.isPending} onClick={() => analyze.mutate()}>
          {analyze.isPending && <Loader2 className="animate-spin" size={16} />}{product.data ? "Re-analyze" : "Analyze product"}
        </button></div>
      </div>
      {analyze.error && <p className="err mb-4">{(analyze.error as Error).message}</p>}
      {product.isLoading && <p className="text-zinc-500">Loading…</p>}
      {product.data === null && !analyze.isPending && <p className="text-sm text-zinc-400">Not analyzed yet. Click “Analyze product” to extract details from the URL.</p>}
      {product.data && (<div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-6">
          {(product.data.extraction_meta.warnings?.length ?? 0) > 0 && (
            <div className="card border-yellow-700/50"><div className="mb-2 flex items-center gap-2 text-sm text-yellow-400"><AlertTriangle size={16} />Needs attention</div>
              <ul className="list-disc space-y-1 pl-5 text-xs text-zinc-300">{product.data.extraction_meta.warnings!.map((w, i) => <li key={i}>{w}</li>)}</ul></div>)}
          <ProductForm key={product.data.id + String(product.data.user_edited)} product={product.data} campaignId={id} />
        </div>
        <div className="space-y-6">
          <Assets product={product.data} campaignId={id} />
          {brand && <BrandPanel brand={brand} />}
        </div>
      </div>)}
    </Shell>
  );
}

function ProductForm({ product, campaignId }: { product: Product; campaignId: string }) {
  const qc = useQueryClient();
  const d = product.data;
  const src = product.extraction_meta.sources ?? {};
  const { register, handleSubmit, formState: { errors, isDirty } } = useForm<ProductEditForm>({
    resolver: zodResolver(productEditSchema),
    defaultValues: { title: d.title ?? "", description: d.description ?? "", category: d.category ?? "", brand_name: d.brand_name ?? "", price: d.price ?? "", features: d.features.join("\n") },
  });
  const save = useMutation({
    mutationFn: (v: ProductEditForm) => api<Product>(`/campaigns/${campaignId}/product`, { method: "PATCH", json: { ...v, features: (v.features ?? "").split("\n").map(s => s.trim()).filter(Boolean) } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["product", campaignId] }),
  });
  const Src = ({ k }: { k: string }) => src[k] ? <span className="ml-2 normal-case text-zinc-500">from {src[k]}</span> : null;
  return (
    <form onSubmit={handleSubmit(v => save.mutate(v))} className="card space-y-4">
      <h2 className="font-medium">Product details <span className="text-xs text-zinc-500">(review and correct — copy is only generated from what’s here)</span></h2>
      <div><label className="label">Title<Src k="title" /></label><input className="input" {...register("title")} />{errors.title && <p className="err">{errors.title.message}</p>}</div>
      <div><label className="label">Description<Src k="description" /></label><textarea rows={4} className="input" {...register("description")} /></div>
      <div className="grid grid-cols-3 gap-4">
        <div><label className="label">Brand<Src k="brand_name" /></label><input className="input" {...register("brand_name")} /></div>
        <div><label className="label">Category<Src k="category" /></label><input className="input" {...register("category")} /></div>
        <div><label className="label">Price<Src k="price" /></label><input className="input" {...register("price")} /></div>
      </div>
      <div><label className="label">Features (one per line)</label><textarea rows={4} className="input" {...register("features")} /></div>
      {save.error && <p className="err">{(save.error as Error).message}</p>}
      <button className="btn" disabled={!isDirty || save.isPending}>{save.isPending ? "Saving…" : "Save changes"}</button>
      {product.user_edited && !isDirty && <span className="ml-3 text-xs text-accent2">Saved</span>}
    </form>
  );
}

function Assets({ product, campaignId }: { product: Product; campaignId: string }) {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const upload = useMutation({
    mutationFn: async ({ file, kind }: { file: File; kind: "image" | "logo" }) => {
      const fd = new FormData(); fd.append("file", file);
      return api<Product>(`/campaigns/${campaignId}/product/assets?kind=${kind}`, { method: "POST", body: fd });
    },
    onSuccess: () => { setErr(""); qc.invalidateQueries({ queryKey: ["product", campaignId] }); },
    onError: (e) => setErr((e as Error).message),
  });
  return (
    <div className="card">
      <h2 className="mb-3 font-medium">Images</h2>
      <div className="grid grid-cols-3 gap-2">
        {product.assets.map(a => (
          <div key={a.id} className="relative overflow-hidden rounded-lg border border-line bg-black/30">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={a.url} alt={a.kind} className="aspect-square w-full object-contain" />
            <span className="absolute left-1 top-1 rounded bg-black/70 px-1 text-[10px] uppercase">{a.kind}</span>
          </div>))}
      </div>
      {(["image", "logo"] as const).map(kind => (
        <label key={kind} className="mt-3 flex cursor-pointer items-center gap-2 text-xs text-zinc-300 hover:text-white">
          <Upload size={14} />Upload {kind}
          <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
            onChange={e => { const f = e.target.files?.[0]; if (f) upload.mutate({ file: f, kind }); e.target.value = ""; }} />
        </label>))}
      {err && <p className="err">{err}</p>}
    </div>
  );
}

function BrandPanel({ brand }: { brand: Brand }) {
  const qc = useQueryClient();
  const [text, setText] = useState((brand.overrides.colors ?? []).join(", "));
  useEffect(() => setText((brand.overrides.colors ?? []).join(", ")), [brand.overrides.colors]);
  const colors = parseColors(text);
  const valid = colorsValid(colors);
  const save = useMutation({
    mutationFn: () => api<Brand>(`/brand-profiles/${brand.id}`, { method: "PATCH", json: { overrides: { ...brand.overrides, colors } } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["brands"] }),
  });
  return (
    <div className="card space-y-3">
      <h2 className="font-medium">Brand: {brand.name}</h2>
      <div><div className="label">Detected palette{brand.detected.color_source && ` (from ${brand.detected.color_source})`}</div>
        <div className="flex gap-1">{(brand.detected.colors ?? []).map(c => <span key={c} title={c} className="h-7 w-7 rounded border border-line" style={{ background: c }} />)}
          {!(brand.detected.colors?.length) && <span className="text-xs text-zinc-500">None detected</span>}</div></div>
      <div><label className="label">Override colors (#RRGGBB, comma separated)</label>
        <input className="input" value={text} onChange={e => setText(e.target.value)} placeholder="#112233, #ffcc00" />
        {!valid && <p className="err">Each color must look like #RRGGBB</p>}</div>
      <button className="btn" disabled={!valid || save.isPending} onClick={() => save.mutate()}>Save brand</button>
    </div>
  );
}
