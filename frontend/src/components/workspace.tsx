"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  AssistantRuntimeProvider, useLocalRuntime, type ChatModelAdapter, useAuiState,
  ThreadPrimitive, MessagePrimitive, ComposerPrimitive, ActionBarPrimitive,
  ThreadListPrimitive, ThreadListItemPrimitive,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ArrowUp, ArrowUpRight, BookOpen, Check, ChevronRight, CircleHelp, Copy,
  FileText, FolderOpen, Layers3, LoaderCircle, MessageSquare, PanelLeft, Paperclip,
  Plus, RefreshCw, Search, Settings2, Square, X, ShieldCheck, Network, Users, LogOut } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { api, readEvents, type Answer, type Chunk, type DocumentRecord, type Health, type Source, type User, type SecurityTrace } from "@/lib/api";
import { AccessMap, Architecture, AccessManagement, TracePanel, DocumentPermissions, PolicyFields, PRIVATE_POLICY, type Policy } from "@/components/security-workspace";
import { cn } from "@/lib/utils";

const SourceContext = createContext<(source: Source) => void>(() => {});

function Markdown() {
  return <MarkdownTextPrimitive className="markdown" />;
}

function Chart({ title, variant, data, citations }: { title?: string; variant?: "bar" | "line"; data?: Array<Record<string, unknown>>; citations?: unknown[] }) {
  const rows = Array.isArray(data) ? data : [];
  const keys = rows.length ? Object.keys(rows[0]).filter((key) => key !== "label" && rows.some((row) => typeof row[key] === "number")) : [];
  if (!rows.length || !keys.length) return null;
  const chartData = rows.map((row) => Object.fromEntries(Object.entries(row).filter(([key, value]) => key === "label" || keys.includes(key) && typeof value === "number")));
  return <div className="generative-card" aria-label={title || "Generated chart"}>
    {title && <h3>{title}</h3>}
    <div className="generative-chart"><ResponsiveContainer width="100%" height={260}>
      {variant === "line" ? <LineChart data={chartData}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="label" /><YAxis /><Tooltip />{keys.map((key, index) => <Line key={key} type="monotone" dataKey={key} stroke={`var(--chart-${(index % 5) + 1})`} strokeWidth={2} />)}</LineChart>
        : <BarChart data={chartData}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="label" /><YAxis /><Tooltip />{keys.map((key, index) => <Bar key={key} dataKey={key} fill={`var(--chart-${(index % 5) + 1})`} radius={[4, 4, 0, 0]} />)}</BarChart>}
    </ResponsiveContainer></div>
    {Array.isArray(citations) && citations.length > 0 && <small className="generative-citations">Sources: {citations.map(String).map((citation) => `[${citation}]`).join(" ")}</small>}
  </div>;
}

function GeneratedTable({ title, columns, rows, citations }: { title?: string; columns?: unknown[]; rows?: unknown[][]; citations?: unknown[] }) {
  if (!Array.isArray(columns) || !Array.isArray(rows)) return null;
  return <div className="generative-card"><div className="generative-table-title">{title || "Source table"}</div><div className="table-scroll"><table><thead><tr>{columns.map((column, index) => <th key={index}>{String(column)}</th>)}</tr></thead><tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell == null ? "—" : String(cell)}</td>)}</tr>)}</tbody></table></div>{Array.isArray(citations) && citations.length > 0 && <small className="generative-citations">Sources: {citations.map(String).map((citation) => `[${citation}]`).join(" ")}</small>}</div>;
}

function UserMessage() {
  return <MessagePrimitive.Root className="user-message"><MessagePrimitive.Parts /></MessagePrimitive.Root>;
}

function AssistantMessage() {
  const custom = useAuiState((s) => s.message.metadata.custom);
  const running = useAuiState((s) => s.message.status?.type === "running");
  const openSource = useContext(SourceContext);
  const sources = (custom.sources || []) as Source[];
  return <MessagePrimitive.Root className="assistant-message">
    <div className="answer-label"><BookOpen size={16} /><span>Folio</span>
      {running && <span className="progress-label" role="status"><LoaderCircle size={13} className="animate-spin" />
        {custom.stage === "answering" ? "Reading the evidence…" : "Finding relevant passages…"}</span>}
    </div>
    <MessagePrimitive.Parts components={{ Text: Markdown, generativeUI: { components: { Chart, Table: GeneratedTable } } }} />
    {typeof custom.error === "string" && <Alert variant="destructive"><AlertTitle>Couldn’t complete this answer</AlertTitle><AlertDescription>{custom.error}</AlertDescription></Alert>}
    {sources.length > 0 && <div className="answer-sources">
      <p className="eyebrow">Sources · {sources.length}</p>
      <div className="source-cards">{sources.map(source => <button className="source-card" key={source.chunk_id} onClick={() => openSource(source)}>
        <span className="source-number">{source.citation}</span><span className="source-card-text"><strong>{source.filename}</strong>
        <small>{source.page ? `Page ${source.page}` : "Document text"}{source.section && ` · ${source.section}`}</small></span><ArrowUpRight size={15} />
      </button>)}</div>
    </div>}
    {!running && !!custom.security_trace && <TracePanel trace={custom.security_trace as SecurityTrace} />}
    {!running && <ActionBarPrimitive.Root className="answer-actions">
      <ActionBarPrimitive.Copy asChild><Button variant="ghost" size="icon-sm" aria-label="Copy answer"><Copy /></Button></ActionBarPrimitive.Copy>
      <ActionBarPrimitive.Reload asChild><Button variant="ghost" size="icon-sm" aria-label="Retry answer"><RefreshCw /></Button></ActionBarPrimitive.Reload>
      {typeof custom.duration_ms === "number" && <span>{(custom.duration_ms / 1000).toFixed(1)}s · {custom.retrieved_count as number} passages retrieved</span>}
    </ActionBarPrimitive.Root>}
  </MessagePrimitive.Root>;
}

function ConversationItem() {
  return <ThreadListItemPrimitive.Root className="conversation-item">
    <ThreadListItemPrimitive.Trigger className="conversation-trigger"><MessageSquare size={15} /><ThreadListItemPrimitive.Title fallback="Document conversation" /></ThreadListItemPrimitive.Trigger>
  </ThreadListItemPrimitive.Root>;
}

function ThreadViewport({ children }: { children: React.ReactNode }) {
  const empty = useAuiState(s => s.thread.isEmpty);
  return <ThreadPrimitive.Viewport className="thread-viewport" autoScroll={!empty}
    scrollToBottomOnInitialize={false} scrollToBottomOnThreadSwitch={!empty}>{children}</ThreadPrimitive.Viewport>;
}

const statusLabels: Record<string, string> = {
  queued: "Queued", parsing: "Reading document", chunking: "Organizing passages",
  waiting_embedding: "Waiting for embeddings",
  embedding: "Creating embeddings", indexing: "Saving to index", ready: "Ready", failed: "Failed",
};

export function Workspace({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [view, setView] = useState<"ask" | "architecture" | "access">("ask");
  const [latestTrace, setLatestTrace] = useState<SecurityTrace | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadPolicy, setUploadPolicy] = useState<Policy>(PRIVATE_POLICY);
  const [users, setUsers] = useState<User[]>([]);
  const isAdmin = user.roles.includes("admin");
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [loadError, setLoadError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [uploadNotice, setUploadNotice] = useState("");
  const [uploading, setUploading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const [source, setSource] = useState<Source | null>(null);
  const [detail, setDetail] = useState<{ document: DocumentRecord; chunks: Chunk[] } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const readyCount = documents.filter(d => d.status === "ready").length;

  const refresh = useCallback(async () => {
    try {
      const [docs, status] = await Promise.all([api<DocumentRecord[]>("/documents"), api<Health>("/health")]);
      setDocuments(docs); setHealth(status); setLoadError("");
    } catch (error) { setLoadError((error as Error).message); }
  }, []);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => { void refresh(); }, 2500);
    return () => clearInterval(id);
  }, [refresh]);

  const detailId = detail?.document.document_id;
  const detailActive = !!detail && !["ready", "failed"].includes(detail.document.status);
  useEffect(() => {
    if (!detailId || !detailActive) return;
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const latest = await api<{ document: DocumentRecord; chunks: Chunk[] }>(`/documents/${detailId}`);
        if (!cancelled) setDetail(latest);
      } catch (error) {
        if (!cancelled) setUploadError((error as Error).message);
      } finally {
        if (!cancelled) timeout = setTimeout(poll, 2500);
      }
    }
    timeout = setTimeout(poll, 2500);
    return () => { cancelled = true; clearTimeout(timeout); };
  }, [detailId, detailActive]);

  const adapter = useMemo<ChatModelAdapter>(() => ({
    async *run({ messages, abortSignal }) {
      const message = messages.findLast(m => m.role === "user");
      const question = message?.content.filter(p => p.type === "text").map(p => p.text).join("\n") || "";
      try {
        const response = await fetch("/api/chat/stream", {
          method: "POST", headers: { "Content-Type": "application/json" }, signal: abortSignal,
          body: JSON.stringify({ question }),
        });
        let answered = false;
        for await (const event of readEvents(response)) {
          if (event.type === "error") throw new Error(event.message);
          if (event.type === "status") yield { content: [], metadata: { custom: { stage: event.stage } } };
          if (event.type === "answer") {
            answered = true;
            const result = event as Answer;
            setLatestTrace(result.security_trace);
            const content = result.generative_ui
              ? [{ type: "text" as const, text: result.answer }, { type: "generative-ui" as const, spec: result.generative_ui }]
              : [{ type: "text" as const, text: result.answer }];
            yield { content, metadata: { custom: {
              sources: result.sources, duration_ms: result.duration_ms,
              retrieved_count: result.retrieved_count, grounded: result.grounded,
              security_trace: result.security_trace,
            } } };
          }
        }
        if (!answered) throw new Error("The connection ended before the answer arrived. Please retry.");
      } catch (error) {
        if (abortSignal.aborted) return;
        yield { content: [], metadata: { custom: { error: (error as Error).message } } };
      }
    },
  }), []);
  const runtime = useLocalRuntime(adapter);

  async function uploadFiles(files: FileList | File[]) {
    setUploadError(""); setUploadNotice(""); setUploading(true);
    try {
      for (const file of Array.from(files)) {
        const body = new FormData(); body.append("file", file);
        body.append("acl", JSON.stringify(uploadPolicy));
        const accepted = await api<DocumentRecord>("/documents", { method: "POST", body });
        if (accepted.reused) setUploadNotice(`Already in your library: ${accepted.filename}`);
        await refresh();
      }
    } catch (error) { setUploadError((error as Error).message); }
    finally { setUploading(false); setUploadOpen(false); if (fileInput.current) fileInput.current.value = ""; }
  }

  async function inspect(document: DocumentRecord) {
    setDetail({ document, chunks: [] }); setDetailLoading(true); setSource(null);
    try { setDetail(await api(`/documents/${document.document_id}`)); }
    catch (error) { setUploadError((error as Error).message); setDetail(null); }
    finally { setDetailLoading(false); }
  }

  function pickFiles() {
    if (!isAdmin) return;
    setUploadOpen(true);
    api<User[]>("/auth/users").then(setUsers).catch(e => setUploadError(e.message));
  }
  const busy = documents.filter(d => !["ready", "failed"].includes(d.status));

  return <AssistantRuntimeProvider runtime={runtime}><SourceContext.Provider value={setSource}>
    <div className="app-shell">
      <input ref={fileInput} type="file" className="sr-only" tabIndex={-1} aria-label="Upload documents" accept=".pdf,.docx,.md,.txt" multiple onChange={e => { if (e.target.files) void uploadFiles(e.target.files); }} />
      {sidebarOpen && <button className="mobile-backdrop" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}
      <aside className={cn("sidebar", sidebarOpen && "sidebar-open")}>
        <div className="brand"><span className="brand-mark"><BookOpen size={22} /></span><span>folio<span className="brand-dot">.</span></span>
          <Button className="mobile-close" variant="ghost" size="icon" aria-label="Close sidebar" onClick={() => setSidebarOpen(false)}><X /></Button>
        </div>
        <Button variant="outline" className="new-chat" onClick={() => { runtime.threads.switchToNewThread(); setView("ask"); setSidebarOpen(false); }}><Plus data-icon="inline-start" />New conversation</Button>
        <div className="sidebar-section">
          <div className="section-label"><span>Workspace</span></div>
          <button className={cn("workspace-nav", view === "ask" && "active-nav")} onClick={() => { setView("ask"); setSidebarOpen(false); }}><MessageSquare size={16} /><span>Ask your documents</span><ChevronRight size={14} /></button>
          <button className={cn("workspace-nav", view === "architecture" && "active-nav")} onClick={() => { setView("architecture"); setSidebarOpen(false); }}><Network size={16} /><span>Architecture</span><span className="nav-tag">ZT</span></button>
          {isAdmin && <button className={cn("workspace-nav", view === "access" && "active-nav")} onClick={() => { setView("access"); setSidebarOpen(false); }}><Users size={16} /><span>People & access</span></button>}
          <ThreadListPrimitive.Root onClick={() => { setView("ask"); setSidebarOpen(false); }}><ThreadListPrimitive.Items components={{ ThreadListItem: ConversationItem }} /></ThreadListPrimitive.Root>
        </div>
        <Separator />
        <div className="document-section">
          <div className="section-label"><span>Documents</span><Badge variant="secondary">{documents.length}</Badge></div>
          <div className="document-list">
            {documents.length === 0 ? <div className="sidebar-empty"><FolderOpen size={26} /><p>A little knowledge<br />goes a long way.</p><small>Your uploaded files live here.</small></div> : documents.map(doc => <button className="document-row" key={doc.document_id} onClick={() => void inspect(doc)}>
              <FileText size={17} /><span><strong>{doc.filename}</strong><small className={cn(doc.status === "failed" && "text-destructive")}>{statusLabels[doc.status] || doc.status}{doc.status === "ready" && ` · ${doc.chunk_count} passages`}</small></span>
              {doc.status === "ready" ? <Check size={13} /> : doc.status === "failed" ? <CircleHelp size={14} /> : <LoaderCircle size={14} className="animate-spin" />}
            </button>)}
          </div>
          {isAdmin && <><Button variant="outline" className="upload-button" disabled={uploading} onClick={pickFiles}>{uploading ? <LoaderCircle data-icon="inline-start" className="animate-spin" /> : <Plus data-icon="inline-start" />}Upload documents</Button>
          <p className="file-types">PDF, DOCX, Markdown, TXT · up to 20 MB</p></>}
        </div>
        <div className="sidebar-footer"><span className="workspace-avatar">{user.email[0].toUpperCase()}</span><div><strong title={user.email}>{user.email}</strong><small>{user.tenant_id} · {user.roles.join(", ")}</small></div><Button variant="ghost" size="icon-sm" aria-label="Connection settings" onClick={() => setSetupOpen(true)}><Settings2 /></Button><Button variant="ghost" size="icon-sm" aria-label="Sign out" onClick={onLogout}><LogOut /></Button></div>
      </aside>

      <main className="main-panel">
        <header className="topbar"><div className="breadcrumb"><Button className="mobile-menu" variant="ghost" size="icon" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}><PanelLeft /></Button><span>{user.tenant_id}</span><ChevronRight size={13} /><strong>{view === "ask" ? "Ask" : view === "architecture" ? "Architecture" : "People & access"}</strong></div>
          <Button variant="ghost" size="sm" onClick={() => setSetupOpen(true)}><span className={cn("connection-dot", health?.azure_configured && "connected")} />{health?.azure_configured ? "Azure OpenAI" : "Connect Azure"}<ChevronRight data-icon="inline-end" /></Button>
        </header>
        {(loadError || uploadError) && <div className="notice"><Alert variant="destructive"><AlertTitle>{loadError ? "Backend connection" : "Upload needs attention"}</AlertTitle><AlertDescription>{loadError || uploadError}</AlertDescription></Alert><Button size="sm" variant="ghost" onClick={() => { setUploadError(""); void refresh(); }}>Dismiss / retry</Button></div>}
        {uploadNotice && <div className="setup-notice" role="status"><span>{uploadNotice}</span><Button variant="ghost" size="sm" onClick={() => setUploadNotice("")}>Dismiss</Button></div>}
        {!loadError && health && !health.azure_configured && <div className="setup-notice"><span>Connect your Azure deployments to start asking questions.</span><Button variant="link" size="sm" onClick={() => setSetupOpen(true)}>View setup <ArrowUpRight data-icon="inline-end" /></Button></div>}
        {view === "architecture" && <Architecture user={user} trace={latestTrace} onAsk={() => setView("ask")} />}
        {view === "access" && isAdmin && <AccessManagement />}
        <div className={cn("ask-layout", view !== "ask" && "ask-layout-hidden")}>
        <ThreadPrimitive.Root className="thread">
          <ThreadViewport>
            <ThreadPrimitive.Empty><div className="welcome">
              <div className="welcome-symbol"><ShieldCheck size={29} strokeWidth={1.4} /></div>
              <p className="eyebrow">YOUR KNOWLEDGE. YOUR BOUNDARIES.</p>
              <h1>Your documents.<br /><em>Only your answers.</em></h1>
              <p className="welcome-copy">Ask with confidence. Every answer starts with your permissions<br />and ends with sources you can inspect.</p>
              <button className="boundary-pill" onClick={() => setView("architecture")}><ShieldCheck size={13} /> Identity verified <span>·</span> Access enforced <ArrowUpRight size={12} /></button>
              <div className="suggestions">
                {[{ icon: FileText, title: "Find the key takeaways", prompt: "What are the key takeaways from these documents?" }, { icon: Search, title: "Look into the details", prompt: "What specific numbers and metrics are reported in these documents?" }, { icon: Layers3, title: "Connect the dots", prompt: "What common themes appear across these documents?" }].map(item => <ThreadPrimitive.Suggestion key={item.title} prompt={item.prompt} method="replace" autoSend={false} className="suggestion-card"><item.icon size={18} /><span>{item.title}</span><ArrowUpRight size={14} /></ThreadPrimitive.Suggestion>)}
              </div>
              {readyCount === 0 && (isAdmin ? <button className="first-upload" onClick={pickFiles}><Plus size={14} /> Start by adding your first document</button> : <p className="first-upload">No documents shared with you yet. Ask your administrator for access.</p>)}
            </div></ThreadPrimitive.Empty>
            <div className="messages"><ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} /></div>
          </ThreadViewport>
          <div className="composer-area">
            {busy.length > 0 && <div className="ingestion-status" role="status"><LoaderCircle size={14} className="animate-spin" /><span>{statusLabels[busy[0].status]}: {busy[0].filename}{busy.length > 1 && ` (+${busy.length - 1} more)`}</span></div>}
            <ComposerPrimitive.Root className="composer">
              <ComposerPrimitive.Input className="composer-input" placeholder="What would you like to know?" aria-label="Ask your documents" disabled={!health?.azure_configured} addAttachmentOnPaste={false} />
              <div className="composer-toolbar"><div>{isAdmin && <Button variant="ghost" size="icon" aria-label="Attach document" disabled={uploading} onClick={pickFiles}><Paperclip /></Button>}<span className="scope-label"><ShieldCheck size={13} />{readyCount ? `Authorized documents · ${readyCount}` : "No authorized documents"}</span></div>
                <ThreadPrimitive.If running={false}><ComposerPrimitive.Send asChild><Button size="icon" aria-label="Send question" disabled={!health?.azure_configured}><ArrowUp /></Button></ComposerPrimitive.Send></ThreadPrimitive.If>
                <ThreadPrimitive.If running><ComposerPrimitive.Cancel asChild><Button size="icon" aria-label="Stop response"><Square /></Button></ComposerPrimitive.Cancel></ThreadPrimitive.If>
              </div>
            </ComposerPrimitive.Root>
            <p className="composer-footnote">Answers grounded in your documents. Always check the sources.</p>
          </div>
        </ThreadPrimitive.Root>
        <AccessMap user={user} documents={documents} trace={latestTrace} />
        </div>
      </main>
    </div>

    <Sheet open={setupOpen} onOpenChange={setSetupOpen}><SheetContent className="detail-sheet"><SheetHeader><SheetTitle>Connect your knowledge</SheetTitle><SheetDescription>Use your Azure OpenAI chat and embedding deployments.</SheetDescription></SheetHeader>
      <div className="sheet-body"><Badge variant="secondary">{health?.azure_configured ? "Configuration present" : "Setup required"}</Badge><p>Copy <code>.env.example</code> to <code>.env</code> in the project root. Add your values locally, then restart the API.</p>
        <div className="config-list">{["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_CHAT_DEPLOYMENT", "AZURE_OPENAI_EMBEDDING_DEPLOYMENT"].map(key => <div key={key}><code>{key}</code>{health && !health.missing.includes(key) ? <Check size={14} /> : <span>Required</span>}</div>)}</div>
        <p className="text-muted-foreground text-sm">Use deployment names from Azure. Keep the API key in the backend environment; it is never sent to this browser.</p><Separator />
        <div className="connection-summary"><span>Vector storage</span><strong>Qdrant · {health?.vector_mode || "unknown"}</strong><span>Connection</span><strong>{health?.vector_store || "Not connected"}</strong></div>
        <Button variant="outline" onClick={() => void refresh()}><RefreshCw data-icon="inline-start" />Check connection</Button>
        <Alert><AlertTitle>Permission-aware workspace</AlertTitle><AlertDescription>JWT sessions, document ACLs, Qdrant filters and retrieval audit are active. Storage is SQLite and Qdrant. Chart pixels, reranking and PostgreSQL are future work.</AlertDescription></Alert>
      </div></SheetContent></Sheet>

    <Sheet open={!!source || !!detail} onOpenChange={open => { if (!open) { setSource(null); setDetail(null); } }}><SheetContent className="detail-sheet"><SheetHeader><SheetTitle>{source ? "Source passage" : "Document details"}</SheetTitle><SheetDescription>{source?.filename || detail?.document.filename}</SheetDescription></SheetHeader>
      <div className="sheet-body">
        {source ? <><div className="source-meta"><Badge variant="secondary">Source {source.citation}</Badge><span>{source.page ? `Page ${source.page}` : "No page number"}</span><Badge variant="outline">{source.content_type}</Badge></div><h3>{source.section || "Document excerpt"}</h3><pre className="source-excerpt">{source.text}</pre><a className={buttonVariants({ variant: "outline" })} href={`/api/documents/${source.document_id}/file`} download><FileText />Download original</a></> : detail && <>
          <div className="source-meta"><Badge variant={detail.document.status === "failed" ? "destructive" : "secondary"}>{statusLabels[detail.document.status]}</Badge><span>{detail.document.chunk_count} passages · {(detail.document.size / 1024).toFixed(1)} KB</span></div>
          {detail.document.error && <Alert variant="destructive"><AlertTitle>Ingestion failed</AlertTitle><AlertDescription>{detail.document.error} Upload the file again to retry.</AlertDescription></Alert>}
          {Object.keys(detail.document.timings_ms || {}).length > 0 && <section aria-label="Processing times">
            <h3>Processing times</h3><dl className="connection-summary">
              {Object.entries(detail.document.timings_ms).map(([stage, ms]) => <div className="contents" key={stage}><dt>{stage === "total" ? "Total" : statusLabels[stage] || stage}</dt><dd>{(ms / 1000).toFixed(2)} s</dd></div>)}
            </dl>
          </section>}
          {detail.document.warnings.map(w => <Alert key={w}><AlertDescription>{w}</AlertDescription></Alert>)}
          <a className={buttonVariants({ variant: "outline" })} href={`/api/documents/${detail.document.document_id}/file`} download><FileText />Download original</a>
          {isAdmin && <DocumentPermissions key={detail.document.document_id} document={detail.document} onSaved={() => { void refresh(); }} />}
          <Separator /><h3>Indexed passages</h3>{detailLoading ? <LoaderCircle className="animate-spin" /> : detail.chunks.length ? detail.chunks.map((chunk, i) => <article className="chunk-preview" key={chunk.chunk_id}><div><span>Passage {i + 1}{chunk.page ? ` · Page ${chunk.page}` : ""}</span><Badge variant="outline">{chunk.content_type}</Badge></div><pre>{chunk.text}</pre></article>) : <Empty><EmptyHeader><EmptyTitle>No indexed passages yet</EmptyTitle><EmptyDescription>Passages appear once ingestion completes.</EmptyDescription></EmptyHeader></Empty>}
        </>}
      </div></SheetContent></Sheet>
    <Sheet open={uploadOpen} onOpenChange={setUploadOpen}><SheetContent className="detail-sheet"><SheetHeader><SheetTitle>Upload with permissions</SheetTitle><SheetDescription>Set access before the document enters your library.</SheetDescription></SheetHeader><div className="sheet-body"><PolicyFields value={uploadPolicy} onChange={setUploadPolicy} users={users} /><Button disabled={uploading} onClick={() => fileInput.current?.click()}>{uploading ? "Uploading…" : "Choose files & upload"}<Plus size={15} /></Button><p>Every passage inherits these permissions. Access stays inside your tenant: <strong>{user.tenant_id}</strong>.</p></div></SheetContent></Sheet>
  </SourceContext.Provider></AssistantRuntimeProvider>;
}
