"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import Shell from "@/components/Shell";
import { api } from "@/lib/api";
import { useSession } from "@/stores/session";
import type { Dashboard } from "@/types";

export default function DashboardPage() {
  const orgId = useSession(s => s.orgId);
  const { data, isLoading, error } = useQuery({ queryKey: ["dashboard", orgId], queryFn: () => api<Dashboard>("/dashboard"), enabled: !!orgId });
  return (
    <Shell>
      <h1 className="mb-6 text-2xl font-semibold">Dashboard</h1>
      {isLoading && <p className="text-zinc-500">Loading…</p>}
      {error && <p className="err">{(error as Error).message}</p>}
      {data && (<>
        <div className="mb-8 grid grid-cols-3 gap-4">
          {[["Campaigns", data.campaigns], ["Projects", data.projects], ["Generated creatives", data.generated_creatives]].map(([k, v]) => (
            <div key={k as string} className="card"><div className="label">{k}</div><div className="text-3xl font-semibold">{v}</div></div>))}
        </div>
        <h2 className="mb-3 text-sm font-medium text-zinc-400">Recent campaigns</h2>
        {data.recent_campaigns.length === 0 ? <p className="text-sm text-zinc-500">No campaigns yet.</p> : (
          <ul className="space-y-2">{data.recent_campaigns.map(c => (
            <li key={c.id}><Link href={`/campaigns/${c.id}`} className="card flex items-center justify-between py-3 hover:border-accent"><span>{c.name}</span><span className="text-xs text-zinc-400">{c.status}</span></Link></li>))}</ul>)}
      </>)}
    </Shell>
  );
}
