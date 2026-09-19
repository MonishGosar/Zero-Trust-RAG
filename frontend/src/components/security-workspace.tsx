"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowDown, ArrowRight, Check, ChevronRight, Database, Eye, FileCheck2, FileText, Filter, Fingerprint, KeyRound, LockKeyhole, Network, ShieldCheck } from "lucide-react";
import { api, type DocumentRecord, type SecurityTrace, type User } from "@/lib/api";
import { Button } from "@/components/ui/button";

const steps = [
  { title: "Verify identity", service: "FastAPI · JWT", icon: Fingerprint, detail: "The API verifies the signature, issuer, audience, expiration and active session. It resolves tenant and roles from the server-side user record on every request.", rule: "Verified session → trusted UserContext" },
  { title: "Resolve access", service: "SQLite · permissions", icon: KeyRound, detail: "Tenant membership is mandatory. Inside that tenant, a document must grant access through ownership, a role or an explicit user. Missing permissions deny access.", rule: "tenant AND (owner OR role OR explicit user)" },
  { title: "Filter retrieval", service: "Qdrant · vector search", icon: Database, detail: "The similarity query includes the tenant and ACL predicates alongside ready document IDs. Qdrant returns only matching chunks. A document picker cannot grant additional access.", rule: "ACL filter + vector similarity → authorized chunks" },
  { title: "Build context", service: "Authorized passages only", icon: ShieldCheck, detail: "Only authorized chunks enter the bounded generation context. The model has no authority to grant access. An empty result skips generation, including charts and tables.", rule: "No authorized context → no model call" },
  { title: "Generate & cite", service: "Azure OpenAI", icon: FileCheck2, detail: "Azure generates an answer using the authorized context. Citation references are validated. Charts and tables use the same context and a bounded rendering vocabulary.", rule: "Authorized context → cited text, charts and tables" },
];

export function Architecture({ user, trace, onAsk }: { user: User; trace: SecurityTrace | null; onAsk: () => void }) {
  const [selected, setSelected] = useState(2);
  const step = steps[selected];
  return <div className="architecture-page">
    <div className="architecture-heading"><div><p className="eyebrow">THE SYSTEM BEHIND THE ANSWER</p><h1>Trust is a boundary.<br /><span>Not a prompt.</span></h1><p>Identity is verified. Access is resolved. Only then does retrieval begin.</p></div><span className="architecture-badge"><ShieldCheck size={14} /> Enforced in the API</span></div>
    <section className="architecture-canvas" aria-label="Zero Trust RAG architecture">
      <div className="canvas-caption"><span><Network size={15} /> REQUEST PATH</span><small>Select a step to inspect its boundary</small></div>
      <div className="request-origin"><span>01 / CLIENT</span><strong>Question from your workspace</strong><small>Next.js · assistant-ui · session cookie</small></div>
      <div className="connector"><ArrowDown size={18} /></div>
      <div className="pipeline">{steps.map((item, index) => <div className="pipeline-cell" key={item.title}>
        <button className={`pipeline-node ${selected === index ? "selected" : ""}`} aria-pressed={selected === index} onClick={() => setSelected(index)}>
          <span className="node-index">0{index + 2}</span><item.icon size={22} strokeWidth={1.5} /><strong>{item.title}</strong><small>{item.service}</small>
        </button>{index < steps.length - 1 && <ChevronRight className="node-arrow" size={17} />}
      </div>)}</div>
      <div className="trust-boundary"><LockKeyhole size={13} /><span>CONTEXT BOUNDARY</span> Unauthorized chunks never enter the model context.</div>
      <div className="architecture-detail" aria-live="polite"><div><p className="eyebrow">0{selected + 2} / {step.service}</p><h2>{step.title}</h2><p>{step.detail}</p></div><code>{step.rule}</code></div>
      <div className="audit-rail"><span><Check size={15} /> Durable retrieval audit before generation</span><small>Identity · keyed query hash · retrieved IDs · timestamp</small></div>
    </section>
    <div className="architecture-bottom"><section className="ingestion-card"><p className="eyebrow">THE OTHER HALF / SECURE INGESTION</p><h2>Permissions travel with the data.</h2><p>An administrator assigns access. Every chunk inherits the document policy.</p><div className="ingestion-flow"><span>Admin upload</span><ArrowRight /><span>Docling / native parser</span><ArrowRight /><span>Chunks + ACL</span><ArrowRight /><span>Embed & index</span></div><p className="architecture-note">Current storage: SQLite + Qdrant. PostgreSQL is a future migration. ACLs reduce unauthorized data exposure; prompt injection inside accessible documents still needs separate evaluation.</p></section>
      <section className="live-boundary"><p className="eyebrow">YOUR CURRENT BOUNDARY</p><dl><dt>Identity</dt><dd>{user.email}</dd><dt>Tenant</dt><dd>{user.tenant_id}</dd><dt>Roles</dt><dd>{user.roles.join(", ")}</dd><dt>Latest retrieval</dt><dd>{trace ? `${trace.context_chunks} authorized chunks` : "No query in this session"}</dd></dl><Button variant="outline" onClick={onAsk}>Ask a question <ArrowRight size={14} /></Button></section></div>
  </div>;
}

export function TracePanel({ trace }: { trace: SecurityTrace }) {
  return <details className="security-trace"><summary><ShieldCheck size={15} /><span>Security trace</span><small>{trace.context_chunks} authorized context chunks</small><ChevronRight size={14} /></summary>
    <dl><dt>Verified identity</dt><dd>{trace.email}</dd><dt>Tenant</dt><dd>{trace.tenant_id}</dd><dt>Roles</dt><dd>{trace.roles.join(", ")}</dd><dt>Retrieval policy</dt><dd>{trace.policy}</dd><dt>Authorized documents in scope</dt><dd>{trace.authorized_documents}</dd><dt>Retrieved / context chunks</dt><dd>{trace.retrieved_chunks} / {trace.context_chunks}</dd><dt>Audit event</dt><dd><code>{trace.audit_id}</code></dd></dl>
    <p>Unauthorized document names, counts and contents are never included in this trace.</p>
  </details>;
}

type PreviewRole = "me" | "finance" | "hr" | "guest";
const previewRoles: Array<{ key: PreviewRole; label: string }> = [
  { key: "me", label: "Me" }, { key: "finance", label: "Finance" },
  { key: "hr", label: "HR" }, { key: "guest", label: "Guest" },
];

export function AccessMap({ user, documents, trace }: { user: User; documents: DocumentRecord[]; trace: SecurityTrace | null }) {
  const [preview, setPreview] = useState<PreviewRole>("me");
  const [compare, setCompare] = useState(false);
  const rows = documents.map((document) => {
    const allowed = preview === "me" ? true : document.allowed_roles.includes(preview);
    const reason = allowed
      ? preview === "me" ? "Returned by the live authorization check." : `Matches the ${preview} role grant.`
      : document.allowed_roles.length ? `Role policy excludes ${preview}.` : "Private to the document owner.";
    return { document, allowed, reason };
  });
  const allowedCount = rows.filter((row) => row.allowed).length;
  const filteredCount = rows.length - allowedCount;
  const admitted = trace?.context_chunks ?? null;
  const retrieved = trace?.retrieved_chunks ?? null;

  return <aside className="access-insights" aria-label="Permission preview">
    <div className="access-map-header"><div><p className="eyebrow">ANSWER WITH PERMISSIONS</p><h2>Trust summary</h2></div><span className="live-label"><ShieldCheck size={13} /> Live</span></div>
    <div className="trust-summary-grid">
      <div><Check size={15} /><strong>Identity verified</strong><small>{user.email}</small></div>
      <div><Check size={15} /><strong>Tenant matched</strong><small>{user.tenant_id}</small></div>
      <div><FileText size={15} /><strong>{admitted === null ? "—" : admitted} admitted</strong><small>{trace ? "to model context" : "ask to inspect context"}</small></div>
      <div><Filter size={15} /><strong>{retrieved === null || admitted === null ? "—" : Math.max(0, retrieved - admitted)} filtered</strong><small>before model context</small></div>
    </div>
    <div className="access-map-title"><div><p className="eyebrow">ACCESS MAP</p><h2>See how access changes</h2></div><Eye size={16} /></div>
    <div className="preview-controls" role="group" aria-label="Preview access as role">
      <span>View as</span>{previewRoles.map((role) => <button key={role.key} className={preview === role.key ? "selected" : ""} onClick={() => { setPreview(role.key); setCompare(false); }}>{role.label}</button>)}
    </div>
    <div className="preview-note">Preview only · does not change permissions</div>
    <div className="access-map-list">
      {rows.length === 0 ? <div className="access-map-empty"><LockKeyhole size={17} /><p>No documents are visible yet.</p><small>Upload or request access to see a policy result here.</small></div> : rows.map(({ document, allowed, reason }) => <button className={`access-map-row ${allowed ? "allowed" : "filtered"}`} key={document.document_id} onClick={() => setCompare(!compare)}>
        <span className="access-row-icon">{allowed ? <Check size={14} /> : <LockKeyhole size={14} />}</span><span className="access-row-copy"><strong>{document.filename}</strong><small>{document.classification} · {reason}</small></span><span className="access-state">{allowed ? "Allowed" : "Filtered"}</span><ChevronRight size={14} />
      </button>)}
    </div>
    <div className="access-map-footer"><span>{allowedCount} allowed · {filteredCount} filtered</span><button onClick={() => setCompare(!compare)}>{compare ? "Close comparison" : "Compare access"}</button></div>
    {compare && <div className="comparison-panel"><p className="eyebrow">POLICY PREVIEW</p><strong>{preview === "me" ? "Your live access" : `Previewing ${preview} access`}</strong><p>{preview === "me" ? "This view reflects the documents returned to your verified session." : "This is a policy simulation. It never signs in as this role or grants access."}</p><div><span><Check size={13} />Allowed {allowedCount}</span><span><Filter size={13} />Filtered {filteredCount}</span></div></div>}
  </aside>;
}

export type Policy = { allowed_roles: string[]; allowed_users: string[]; classification: string };
export const PRIVATE_POLICY: Policy = { allowed_roles: [], allowed_users: [], classification: "internal" };
export function PolicyFields({ value, onChange, users }: { value: Policy; onChange: (policy: Policy) => void; users: User[] }) {
  return <div className="security-form policy-fields"><label>Classification<select value={value.classification} onChange={e => onChange({ ...value, classification: e.target.value })}><option value="internal">Internal</option><option value="confidential">Confidential</option><option value="restricted">Restricted</option></select></label>
    <fieldset><legend>Allowed roles</legend><div className="role-options">{["employee", "finance", "hr", "engineering", "admin"].map(role => <label key={role}><input type="checkbox" checked={value.allowed_roles.includes(role)} onChange={e => onChange({ ...value, allowed_roles: e.target.checked ? [...value.allowed_roles, role] : value.allowed_roles.filter(r => r !== role) })} />{role}</label>)}</div></fieldset>
    <fieldset><legend>Specific people</legend><div className="user-options">{users.map(person => <label key={person.user_id}><input type="checkbox" checked={value.allowed_users.includes(person.user_id)} onChange={e => onChange({ ...value, allowed_users: e.target.checked ? [...value.allowed_users, person.user_id] : value.allowed_users.filter(id => id !== person.user_id) })} />{person.email}</label>)}</div></fieldset><small>The owner always retains access. With no grants, the document is private to its owner. Classification is a label; roles and people control access.</small></div>;
}

export function DocumentPermissions({ document, onSaved }: { document: DocumentRecord; onSaved: () => void }) {
  const [policy, setPolicy] = useState<Policy>({ allowed_roles: document.allowed_roles || [], allowed_users: document.allowed_users || [], classification: document.classification || "internal" });
  const [users, setUsers] = useState<User[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<User[]>("/auth/users").then(setUsers).catch(e => setMessage(e.message)); }, []);
  return <section className="document-permissions"><h3>Document access</h3><PolicyFields value={policy} onChange={setPolicy} users={users} /><Button variant="outline" disabled={busy || !["ready", "failed"].includes(document.status)} onClick={async () => {
    setBusy(true); setMessage("");
    try { await api(`/documents/${document.document_id}/acl`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(policy) }); setMessage("Permissions saved on the document and every chunk."); onSaved(); }
    catch (e) { setMessage((e as Error).message); }
    finally { setBusy(false); }
  }}>Save permissions</Button>{message && <p role="status">{message}</p>}</section>;
}

export function AccessManagement() {
  const [users, setUsers] = useState<User[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(() => api<User[]>("/auth/users").then(setUsers).catch(e => setMessage(e.message)), []);
  useEffect(() => { void refresh(); }, [refresh]);
  return <div className="access-page"><p className="eyebrow">WORKSPACE ADMINISTRATION</p><h1>People & access</h1><p>Create accounts in your tenant. Document permissions determine what each person can retrieve.</p>
    <div className="access-columns"><section className="people-list"><h2>Workspace members <span>{users.length}</span></h2>{users.map(person => <article key={person.user_id}><span className="person-icon"><Fingerprint size={18} /></span><div><strong>{person.email}</strong><small>{person.roles.join(" · ")}</small></div></article>)}</section>
    <form className="security-form new-user-form" onSubmit={async e => { e.preventDefault(); const form = e.currentTarget; const data = new FormData(form); setBusy(true); setMessage("");
      try { await api("/auth/users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: data.get("email"), password: data.get("password"), roles: [data.get("role")] }) }); form.reset(); setMessage("Account created. Share the credentials securely with this person."); await refresh(); }
      catch (error) { setMessage((error as Error).message); }
      finally { setBusy(false); }
    }}><h2>Add a person</h2><label>Email address<input type="email" name="email" required maxLength={200} autoComplete="off" /></label><label>Initial password<input name="password" type="password" required minLength={12} maxLength={128} autoComplete="new-password" /></label><label>Role<select name="role">{["employee", "finance", "hr", "engineering", "admin"].map(role => <option key={role}>{role}</option>)}</select></label><Button type="submit" disabled={busy}>{busy ? "Creating…" : "Create account"}</Button>{message && <p role="status">{message}</p>}</form></div>
  </div>;
}
