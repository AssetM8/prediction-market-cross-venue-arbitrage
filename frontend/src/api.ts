import type {
  AuditEvent,
  ExecuteResponse,
  ExecutionScenario,
  Health,
  MarketPair,
  Opportunity,
  OpportunityDetail,
  PaperExecution,
  PaperOrder,
  Portfolio,
  PublicConfig,
  RelationshipGraph,
  ScanSummary,
} from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      // body was not JSON; keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export function newIdempotencyKey(prefix = "ui"): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${random}`.replace(/[^A-Za-z0-9_.:-]/g, "").slice(0, 128);
}

export const api = {
  health: () => request<Health>("/health"),
  config: () => request<PublicConfig>("/api/config/public"),
  pairs: () => request<MarketPair[]>("/api/market-pairs"),
  opportunities: () => request<Opportunity[]>("/api/opportunities"),
  opportunity: (id: string) => request<OpportunityDetail>(`/api/opportunities/${encodeURIComponent(id)}`),
  relationships: () => request<RelationshipGraph>("/api/relationships"),
  portfolio: () => request<Portfolio>("/api/paper/portfolio"),
  executions: () => request<PaperExecution[]>("/api/paper/executions"),
  orders: () => request<PaperOrder[]>("/api/paper/orders"),
  audit: (category?: string) =>
    request<AuditEvent[]>(`/api/audit?limit=200${category ? `&category=${encodeURIComponent(category)}` : ""}`),
  refresh: () =>
    request<{ summary: ScanSummary }>("/api/market-pairs/refresh", {
      method: "POST",
      headers: { "Idempotency-Key": newIdempotencyKey("refresh") },
    }),
  execute: (id: string, body: { quantity?: string; scenario: ExecutionScenario }, key: string) =>
    request<ExecuteResponse>(`/api/paper/execute/${encodeURIComponent(id)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": key },
      body: JSON.stringify(body),
    }),
  resetKillSwitch: () => request<Portfolio>("/api/paper/kill-switch/reset", { method: "POST" }),
};

export type Api = typeof api;
