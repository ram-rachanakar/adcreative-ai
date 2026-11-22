"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { LayoutDashboard, PlusCircle, LogOut, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { useSession } from "@/stores/session";
import { useRequireAuth } from "@/hooks/useAuth";

export default function Shell({ children }: { children: React.ReactNode }) {
  const { me, loading } = useRequireAuth();
  const { orgId, selectOrg, set } = useSession();
  const router = useRouter();
  if (loading || !me) return <div className="grid min-h-screen place-items-center text-zinc-500">Loading…</div>;
  const logout = async () => { await api("/auth/logout", { method: "POST" }); set(null); router.replace("/login"); };
  return (
    <div className="flex min-h-screen">
      <aside className="w-60 shrink-0 border-r border-line p-4">
        <div className="mb-6 flex items-center gap-2 font-semibold"><Sparkles className="text-accent" size={18} />AdCreative AI</div>
        <select className="input mb-4" value={orgId ?? ""} onChange={e => selectOrg(e.target.value)} aria-label="Organization">
          {me.memberships.map(m => <option key={m.organization.id} value={m.organization.id}>{m.organization.name} ({m.role})</option>)}
        </select>
        <nav className="space-y-1 text-sm">
          <Link className="flex items-center gap-2 rounded-lg px-3 py-2 hover:bg-panel" href="/dashboard"><LayoutDashboard size={16} />Dashboard</Link>
          <Link className="flex items-center gap-2 rounded-lg px-3 py-2 hover:bg-panel" href="/campaigns/new"><PlusCircle size={16} />New campaign</Link>
        </nav>
        <button onClick={logout} className="mt-8 flex items-center gap-2 px-3 text-xs text-zinc-400 hover:text-zinc-200"><LogOut size={14} />Sign out ({me.email})</button>
      </aside>
      <main className="flex-1 p-8">{children}</main>
    </div>
  );
}
