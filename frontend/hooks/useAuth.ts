"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useSession } from "@/stores/session";
import type { Me } from "@/types";

/** Loads the session; redirects to /login when unauthenticated. */
export function useRequireAuth() {
  const { me, set } = useSession();
  const [loading, setLoading] = useState(!me);
  const router = useRouter();
  useEffect(() => {
    if (me) return;
    api<Me>("/auth/me").then(set).catch(() => router.replace("/login")).finally(() => setLoading(false));
  }, [me, set, router]);
  return { me, loading };
}
