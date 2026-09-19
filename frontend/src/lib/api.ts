export type DocumentRecord = {
  tenant_id: string; owner_id: string; allowed_roles: string[]; allowed_users: string[];
  classification: string;
  document_id: string; filename: string; status: string; created_at: string;
  size: number; chunk_count: number; error: string | null; warnings: string[];
  reused: boolean; timings_ms: Record<string, number>;
  processing: { parser?: string; ocr_enabled?: boolean; pages?: number };
  stage_started_at: string | null; completed_at: string | null;
};
export type Chunk = {
  chunk_id: string; document_id: string; filename: string; text: string;
  section: string; page: number | null; page_end: number | null;
  content_type: string; token_count: number;
};
export type Source = Chunk & { citation: number; score: number };
export type Health = {
  azure_configured: boolean; missing: string[]; vector_store: string;
  vector_mode: string; chat_deployment: string | null;
};
export type Answer = {
  security_trace: SecurityTrace | null;
  answer: string; sources: Source[]; grounded: boolean;
  retrieved_count: number; duration_ms: number;
  generative_ui: { root: { component: "Chart" | "Table"; props: Record<string, unknown> } } | null;
};

function getErrorMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== "object") return fallback;
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return fallback;
  const messages = detail.map((entry) => {
    if (!entry || typeof entry !== "object") return "";
    const item = entry as { loc?: unknown; msg?: unknown };
    if (typeof item.msg !== "string") return "";
    const location = Array.isArray(item.loc)
      ? item.loc.filter((part) => part !== "body").join(".")
      : "";
    return location ? `${location}: ${item.msg}` : item.msg;
  }).filter(Boolean);
  return messages.length ? messages.join("; ") : fallback;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { cache: "no-store", ...init });
  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/login" && path !== "/auth/me") window.dispatchEvent(new Event("folio:session-expired"));
    const data = await response.json().catch(() => null);
    throw new Error(getErrorMessage(data, "The API is unavailable. Check that the backend is running."));
  }
  return response.json();
}

export async function* readEvents(response: Response) {
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event("folio:session-expired"));
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : "The request failed. Try again.");
  }
  if (!response.body) throw new Error("The server returned an empty response.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) if (line.trim()) yield JSON.parse(line);
      if (done) {
        if (buffer.trim()) yield JSON.parse(buffer);
        break;
      }
    }
  } finally { reader.releaseLock(); }
}

export type User = { user_id: string; email: string; tenant_id: string; roles: string[] };
export type SecurityTrace = {
  audit_id: string; user_id: string; email: string; tenant_id: string; roles: string[];
  authorized_documents: number; retrieved_chunks: number; context_chunks: number;
  policy: string; query_hash: string;
};
