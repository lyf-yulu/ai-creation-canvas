import type { ApiError } from "./contracts";

const API_PREFIX = "/api/v1/";

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
    if (!path.startsWith(API_PREFIX) || new URL(path, window.location.origin).origin !== window.location.origin) {
        throw new Error("API requests must use a relative /api/v1/ path");
    }
    const response = await fetch(path, { ...init, credentials: "same-origin", headers: { Accept: "application/json", ...init.headers } });
    if (!response.ok) {
        const error = (await response.json().catch(() => null)) as ApiError | null;
        throw new Error(error?.message || `Request failed (${response.status})`);
    }
    return response.json() as Promise<T>;
}
