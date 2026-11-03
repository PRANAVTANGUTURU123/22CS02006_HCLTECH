export type Health = {
  status: string;
  app: string;
  version: string;
  environment: string;
  llm_provider: string;
  database: string;
  time: string;
};

export type Completion = {
  text: string;
  provider: string;
  model: string;
  latency_ms: number;
  fallback_used: boolean;
};

type ApiErrorBody = { error?: { code?: string; message?: string; request_id?: string } };

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId?: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    signal: init?.signal ?? AbortSignal.timeout(30_000),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as ApiErrorBody;
    throw new ApiError(
      res.status,
      body.error?.code ?? "http_error",
      body.error?.message ?? res.statusText,
      body.error?.request_id,
    );
  }
  return (await res.json()) as T;
}

export const getHealth = () => request<Health>("/health");

export const complete = (prompt: string) =>
  request<Completion>("/api/llm/complete", { method: "POST", body: JSON.stringify({ prompt }) });
