"use client";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useRouter } from "next/navigation";
import { useState } from "react";
import Shell from "@/components/Shell";
import { api } from "@/lib/api";
import type { Campaign, Project, Workspace } from "@/types";

const TONES = ["premium", "luxury", "professional", "friendly", "bold", "playful", "minimalist", "emotional", "corporate", "conversion-focused"];
const schema = z.object({
  name: z.string().min(1, "Required"),
  product_url: z.string().url("Enter a valid URL").optional().or(z.literal("")),
  description: z.string().min(1, "Describe the product"),
  audience: z.string().optional(), objective: z.string().optional(), tone: z.string(),
});
type V = z.infer<typeof schema>;

export default function NewCampaign() {
  const router = useRouter();
  const [error, setError] = useState("");
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<V>({ resolver: zodResolver(schema), defaultValues: { tone: "professional" } });
  const onSubmit = async (v: V) => {
    setError("");
    try {
      const ws = (await api<Workspace[]>("/workspaces"))[0];
      const projects = await api<Project[]>("/projects");
      const project = projects[0] ?? await api<Project>("/projects", { method: "POST", json: { workspace_id: ws.id, name: "Default project" } });
      const created = await api<Campaign>("/campaigns", { method: "POST", json: {
        project_id: project.id, name: v.name, product_url: v.product_url || null, description: v.description,
        brief: { audience: v.audience, objective: v.objective, tone: v.tone } } });
      router.push(`/campaigns/${created.id}`);
    } catch (e) { setError((e as Error).message); }
  };
  return (
    <Shell>
      <h1 className="mb-6 text-2xl font-semibold">New campaign</h1>
      <form onSubmit={handleSubmit(onSubmit)} className="card max-w-2xl space-y-4">
        <div><label className="label">Campaign name</label><input className="input" {...register("name")} />{errors.name && <p className="err">{errors.name.message}</p>}</div>
        <div><label className="label">Product URL</label><input className="input" placeholder="https://…" {...register("product_url")} />{errors.product_url && <p className="err">{errors.product_url.message}</p>}</div>
        <div><label className="label">Description</label><textarea rows={4} className="input" {...register("description")} />{errors.description && <p className="err">{errors.description.message}</p>}</div>
        <div className="grid grid-cols-2 gap-4">
          <div><label className="label">Target audience</label><input className="input" {...register("audience")} /></div>
          <div><label className="label">Objective</label><input className="input" {...register("objective")} /></div>
        </div>
        <div><label className="label">Tone</label><select className="input" {...register("tone")}>{TONES.map(t => <option key={t}>{t}</option>)}</select></div>
        {error && <p className="err">{error}</p>}
        <button className="btn" disabled={isSubmitting}>Create campaign</button>
      </form>
    </Shell>
  );
}
