"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowDown, ArrowRight, FileCheck2, Fingerprint, Layers3, LockKeyhole, ShieldCheck } from "lucide-react";
import { api, type User } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Workspace } from "@/components/workspace";

export function SecureWorkspace() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [setup, setSetup] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const check = useCallback(async () => {
    try {
      const status = await api<{ setup_required: boolean }>("/auth/status");
      setSetup(status.setup_required);
      if (!status.setup_required) {
        const response = await fetch("/api/auth/me", { cache: "no-store" });
        setUser(response.ok ? await response.json() : null);
      }
      setError("");
    } catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => {
    void check();
    const expired = () => { setUser(null); setError("Your session has expired. Sign in again."); };
    window.addEventListener("folio:session-expired", expired);
    return () => window.removeEventListener("folio:session-expired", expired);
  }, [check]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const values = new FormData(event.currentTarget);
    const body = { email: values.get("email"), password: values.get("password"),
      ...(setup ? { tenant_id: values.get("tenant") } : {}) };
    try {
      const result = await api<{ user: User }>(setup ? "/auth/setup" : "/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      setUser(result.user); setSetup(false);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  if (loading) return <div className="auth-loading" role="status"><ShieldCheck />Opening your workspace…</div>;
  if (user) return <Workspace key={user.user_id} user={user} onLogout={async () => {
    try { await api("/auth/logout", { method: "POST" }); setUser(null); }
    catch (e) { setError((e as Error).message); }
  }} />;
  return <main className="auth-page">
    <section className="auth-story">
      <div className="auth-brand"><Layers3 size={26} /> folio<span> / ZERO TRUST RAG</span></div>
      <div><p className="eyebrow">KNOWLEDGE WITH BOUNDARIES</p><h1>Every answer.<br />The right access.</h1>
        <p>Your documents, connected. Your permissions, enforced before the model sees a word.</p>
        <div className="auth-flow"><span><Fingerprint />Verify identity</span><ArrowDown /><span><ShieldCheck />Authorize retrieval</span><ArrowDown /><span><FileCheck2 />Answer with evidence</span></div>
      </div>
      <small>Folio / A permission-aware document workspace</small>
    </section>
    <section className="auth-form-wrap"><form className="security-form auth-form" onSubmit={submit}>
      <div className="auth-icon"><LockKeyhole size={24} /></div>
      <p className="eyebrow">{setup ? "YOUR WORKSPACE STARTS HERE" : "VERIFIED ACCESS"}</p>
      <h2>{setup ? "Create your workspace" : "Welcome back"}</h2>
      <p>{setup ? "Set up the first administrator. Existing local documents will become private to this account." : "Sign in to ask questions across the documents you can access."}</p>
      {setup && <label>Workspace ID<input name="tenant" placeholder="acme" pattern="[a-z0-9][a-z0-9_-]*" maxLength={64} required autoComplete="organization" /></label>}
      <label>Email address<input name="email" type="email" placeholder="you@company.com" required maxLength={200} autoComplete="username" /></label>
      <label>Password<input name="password" type="password" required minLength={12} maxLength={128} placeholder="At least 12 characters" autoComplete={setup ? "new-password" : "current-password"} /></label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <Button type="submit" disabled={busy}>{busy ? "Please wait…" : setup ? "Create workspace" : "Sign in"}<ArrowRight size={16} /></Button>
      <small><LockKeyhole size={12} /> Access is checked on every request.</small>
      {error && <Button type="button" variant="ghost" onClick={() => void check()}>Check connection</Button>}
    </form></section>
  </main>;
}
