/**
 * Typed fetch wrapper for the Shipwright orchestrator API.
 *
 * Contracts honored (all AUTHORITATIVE):
 *  - Base URL from `NEXT_PUBLIC_API_BASE` (lib/env.ts, docs/VERSIONS.md — orchestrator :8000).
 *  - JSON is camelCase on the wire (00-CANON.md §8).
 *  - Every non-2xx body is the canonical error envelope
 *    `{ error: { code, message, details, requestId, retryable } }`
 *    (00-CANON.md §13.5 / 03-api-and-events.md §3.2). It is parsed into a typed `ApiError`.
 *
 * When `@foundry/api-types` publishes its generated response types, callers pass them as
 * the `T` type argument (e.g. `apiFetch<Mission>("/missions/FND-1")`); this module stays
 * transport-only and holds no domain types of its own.
 */
import { getApiBase } from "./env";

/** Shape of the canonical error envelope (00-CANON §13.5). */
export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
    requestId: string;
    retryable: boolean;
  };
}

/** Typed error thrown for any non-2xx response (or a transport/parse failure). */
export class ApiError extends Error {
  /** HTTP status (0 for a network/transport failure). */
  readonly status: number;
  /** Stable machine code from the envelope, e.g. `not_found`, `rate_limited`. */
  readonly code: string;
  /** Structured context ({} when none). */
  readonly details: Record<string, unknown>;
  /** Correlates with the `X-Request-Id` response header — surface it for support. */
  readonly requestId: string;
  /** True if the client may safely retry the same request (with backoff). */
  readonly retryable: boolean;

  constructor(init: {
    status: number;
    code: string;
    message: string;
    details?: Record<string, unknown>;
    requestId?: string;
    retryable?: boolean;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.details = init.details ?? {};
    this.requestId = init.requestId ?? "";
    this.retryable = init.retryable ?? false;
  }
}

/** Narrowing guard for callers that catch unknown errors. */
export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError;
}

export interface ApiRequestOptions extends Omit<RequestInit, "body"> {
  /** JSON body — serialized and sent with `Content-Type: application/json`. */
  json?: unknown;
  /** Query params appended to the path. */
  query?: Record<string, string | number | boolean | undefined>;
}

function buildUrl(path: string, query?: ApiRequestOptions["query"]): string {
  const base = getApiBase();
  const url = new URL(`${base}${path.startsWith("/") ? path : `/${path}`}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

function looksLikeEnvelope(body: unknown): body is ErrorEnvelope {
  return (
    typeof body === "object" &&
    body !== null &&
    "error" in body &&
    typeof (body as { error: unknown }).error === "object" &&
    (body as { error: unknown }).error !== null
  );
}

/**
 * Perform a request and return the parsed JSON body typed as `T`.
 * Throws `ApiError` on any non-2xx response or transport failure.
 */
export async function apiFetch<T = unknown>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const { json, query, headers, ...rest } = options;
  const url = buildUrl(path, query);

  const finalHeaders = new Headers(headers);
  finalHeaders.set("Accept", "application/json");
  let body: BodyInit | undefined;
  if (json !== undefined) {
    finalHeaders.set("Content-Type", "application/json");
    body = JSON.stringify(json);
  }

  let res: Response;
  try {
    res = await fetch(url, { ...rest, headers: finalHeaders, body });
  } catch (cause) {
    // DNS/connection refused/CORS/etc. — orchestrator unreachable.
    throw new ApiError({
      status: 0,
      code: "network_error",
      message: cause instanceof Error ? cause.message : "Network request failed",
      retryable: true,
    });
  }

  const requestId = res.headers.get("X-Request-Id") ?? "";

  if (res.status === 204) {
    return undefined as T;
  }

  const raw = await res.text();
  let parsed: unknown;
  try {
    parsed = raw ? JSON.parse(raw) : undefined;
  } catch {
    parsed = undefined;
  }

  if (!res.ok) {
    if (looksLikeEnvelope(parsed)) {
      const e = parsed.error;
      throw new ApiError({
        status: res.status,
        code: typeof e.code === "string" ? e.code : "internal_error",
        message: typeof e.message === "string" ? e.message : res.statusText,
        details: (e.details as Record<string, unknown>) ?? {},
        requestId: (e.requestId as string) || requestId,
        retryable: Boolean(e.retryable),
      });
    }
    // Non-conforming error body — synthesize an envelope-shaped ApiError.
    throw new ApiError({
      status: res.status,
      code: "internal_error",
      message: res.statusText || `Request failed with status ${res.status}`,
      requestId,
      retryable: res.status >= 500,
    });
  }

  return parsed as T;
}

/** Convenience verb helpers. */
export const api = {
  get: <T = unknown>(path: string, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "GET" }),
  post: <T = unknown>(path: string, json?: unknown, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "POST", json }),
  patch: <T = unknown>(path: string, json?: unknown, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "PATCH", json }),
  put: <T = unknown>(path: string, json?: unknown, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "PUT", json }),
  del: <T = unknown>(path: string, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "DELETE" }),
} as const;

/** Orchestrator liveness probe (07-integrations.md §health — `GET /healthz`). */
export interface HealthResponse {
  status: string;
  [key: string]: unknown;
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/healthz", { signal, cache: "no-store" });
}
