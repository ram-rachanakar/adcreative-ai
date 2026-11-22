"use client";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { useSession } from "@/stores/session";
import type { Me } from "@/types";

const schema = z.object({
  email: z.string().email(),
  password: z.string().min(10, "At least 10 characters"),
  organization_name: z.string().optional(),
});
type V = z.infer<typeof schema>;

export default function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const setMe = useSession(s => s.set);
  const [error, setError] = useState("");
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<V>({ resolver: zodResolver(schema) });
  const onSubmit = async (v: V) => {
    setError("");
    if (mode === "register" && !v.organization_name) { setError("Organization name is required"); return; }
    try {
      const me = await api<Me>(`/auth/${mode}`, { method: "POST", json: v });
      setMe(me);
      router.push("/dashboard");
    } catch (e) { setError((e as Error).message); }
  };
  return (
    <main className="grid min-h-screen place-items-center px-4">
      <form onSubmit={handleSubmit(onSubmit)} className="card w-full max-w-sm space-y-4">
        <h1 className="text-xl font-semibold">{mode === "login" ? "Welcome back" : "Create your workspace"}</h1>
        {mode === "register" && (<div><label className="label">Organization</label><input className="input" {...register("organization_name")} /></div>)}
        <div><label className="label">Email</label><input className="input" type="email" {...register("email")} />{errors.email && <p className="err">{errors.email.message}</p>}</div>
        <div><label className="label">Password</label><input className="input" type="password" {...register("password")} />{errors.password && <p className="err">{errors.password.message}</p>}</div>
        {error && <p className="err">{error}</p>}
        <button className="btn w-full" disabled={isSubmitting}>{mode === "login" ? "Sign in" : "Create account"}</button>
        <p className="text-center text-xs text-zinc-400">
          {mode === "login" ? <>No account? <Link className="text-accent" href="/register">Register</Link></> : <>Have an account? <Link className="text-accent" href="/login">Sign in</Link></>}
        </p>
      </form>
    </main>
  );
}
