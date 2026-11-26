/**
 * The one fetch wrapper. Always sends the session cookie, speaks JSON, and turns every failure
 * (HTTP error or unreachable server) into a typed `ApiError` so callers never inspect raw responses.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  /** The raw `detail` from the response body; e.g. `detail.current` on a version conflict. */
  readonly detail: unknown;

  constructor(status: number, code: string, message: string, detail: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** Read a field off `ApiError.detail` without trusting its shape. */
export function detailField<T>(error: ApiError, field: string): T | undefined {
  const detail = error.detail;
  if (detail && typeof detail === "object" && field in detail) {
    return (detail as Record<string, unknown>)[field] as T;
  }
  return undefined;
}

const GENERIC_MESSAGES: Record<number, string> = {
  401: "Your session has ended. Sign in again to continue.",
  403: "You don't have permission to do that.",
  404: "That item no longer exists.",
};

function parseErrorBody(status: number, body: unknown): ApiError {
  const fallback = GENERIC_MESSAGES[status] ?? "Something went wrong. Try again.";
  const detail = body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : body;

  // PLAN section 6: {"detail": {"code", "message", ...}}
  if (detail && typeof detail === "object" && !Array.isArray(detail) && "code" in detail) {
    const d = detail as { code?: unknown; message?: unknown };
    return new ApiError(
      status,
      typeof d.code === "string" ? d.code : "error",
      typeof d.message === "string" ? d.message : fallback,
      detail,
    );
  }

  // FastAPI validation errors (422): detail is an array of {loc, msg, type}.
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: unknown; loc?: unknown };
    const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1]) : null;
    const msg = typeof first.msg === "string" ? first.msg : "Invalid input.";
    return new ApiError(status, "validation_error", field ? `${field}: ${msg}` : msg, detail);
  }

  // A 5xx with no structured body comes from a proxy in front of a dead or still-starting API.
  if (status >= 500) {
    return new ApiError(
      status,
      "server_unavailable",
      "The server isn't responding. It may still be starting up; try again in a moment.",
      detail,
    );
  }

  if (typeof detail === "string") return new ApiError(status, "error", detail, detail);
  return new ApiError(status, "error", fallback, detail);
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal } = options;

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      credentials: "include",
      signal,
      headers: {
        Accept: "application/json",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "network_error", "Can't reach the server. Check your connection and try again.");
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }

  if (!response.ok) throw parseErrorBody(response.status, parsed);
  return parsed as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => apiFetch<T>(path, { signal }),
  post: <T>(path: string, body?: unknown) => apiFetch<T>(path, { method: "POST", body: body ?? {} }),
  put: <T>(path: string, body: unknown) => apiFetch<T>(path, { method: "PUT", body }),
  patch: <T>(path: string, body: unknown) => apiFetch<T>(path, { method: "PATCH", body }),
};

/** Human-readable message for any thrown value, for toasts and inline errors. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong. Try again.";
}
