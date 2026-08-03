export const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

function asciiHeaders(token?: string, input?: HeadersInit): Record<string, string> {
  const result: Record<string, string> = { "Content-Type": "application/json" };
  if (token) result.Authorization = `Bearer ${token}`;
  let entries: [string, string][];
  if (input instanceof Headers) {
    entries = [];
    input.forEach((value, key) => entries.push([key, value]));
  } else if (Array.isArray(input)) {
    entries = input.map(([key, value]) => [String(key), String(value)]);
  } else {
    entries = Object.entries(input || {}).map(([key, value]) => [key, String(value)]);
  }
  for (const [key, value] of entries) {
    if (!/^[\x21-\x7e]+$/.test(key) || !/^[\x20-\x7e]*$/.test(value)) {
      throw new ApiError(0, "Request headers must contain ASCII-safe values only");
    }
    result[key] = value;
  }
  return result;
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
      headers: asciiHeaders(token, init.headers),
    });
    const value = await response.json().catch(() => ({}));
    if (!response.ok) throw new ApiError(response.status, value?.error?.message || "Request failed");
    return value as T;
  } finally {
    window.clearTimeout(timeout);
  }
}
