const base = import.meta.env.VITE_API_BASE || "/api";

export class ApiClient {
  constructor(private readonly token: string) {}

  async request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    const response = await fetch(`${base}${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      ...(signal ? { signal } : {}),
    });
    if (!response.ok) {
      const error: unknown = await response.json();
      const detail = typeof error === "object" && error !== null && "detail" in error
        && typeof error.detail === "string" ? error.detail : "The request could not be completed.";
      throw new Error(detail);
    }
    if (response.status === 204) return undefined as T;
    return response.json();
  }

  async download(path: string, filename: string): Promise<void> {
    const response = await fetch(`${base}${path}`, { headers: { Authorization: `Bearer ${this.token}` } });
    if (!response.ok) throw new Error("The requested export is not available.");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "The action could not be completed.";
}
