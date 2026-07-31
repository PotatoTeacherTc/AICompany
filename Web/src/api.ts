export const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T>(
  path: string, token?: string, init: RequestInit = {}, timeoutMs = 10000,
): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init.headers,
      },
    });
    const value = await response.json().catch(() => ({}));
    if (!response.ok) throw new ApiError(response.status, value?.error?.message || "Request failed");
    return value as T;
  } finally {
    window.clearTimeout(timeout);
  }
}
