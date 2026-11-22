import { create } from "zustand";
import { setSession } from "@/lib/api";
import type { Me } from "@/types";

interface S { me: Me | null; orgId: string | null; set: (me: Me | null) => void; selectOrg: (id: string) => void }
export const useSession = create<S>((set, get) => ({
  me: null, orgId: null,
  set: (me) => {
    const orgId = me ? (get().orgId && me.memberships.some(m => m.organization.id === get().orgId) ? get().orgId : me.memberships[0]?.organization.id ?? null) : null;
    setSession(me?.csrf_token ?? "", orgId ?? "");
    set({ me, orgId });
  },
  selectOrg: (id) => { const me = get().me; if (me) { setSession(me.csrf_token, id); set({ orgId: id }); } },
}));
