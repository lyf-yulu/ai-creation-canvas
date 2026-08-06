import type { ApiError } from "./contracts";

const API_PREFIX = "/api/v1/";
const unsafePathError = () => new Error("API requests must use a normalized relative /api/v1/ path");

function fullyDecode(value: string) {
    let decoded = value;
    for (let attempt = 0; attempt < 4; attempt += 1) {
        let next: string;
        try { next = decodeURIComponent(decoded); } catch { throw unsafePathError(); }
        if (next === decoded) return decoded;
        decoded = next;
    }
    return decoded;
}

/** Validates before URL normalization so encoded dot-segments cannot escape the API prefix. */
export function safeApiPath(path: string) {
    if (!path.startsWith("/") || path.startsWith("//") || /^[a-z][a-z0-9+.-]*:/i.test(path)) throw unsafePathError();
    const pathname = path.split(/[?#]/, 1)[0];
    if (!pathname.startsWith(API_PREFIX)) throw unsafePathError();
    for (const segment of pathname.split("/")) {
        const decoded = fullyDecode(segment);
        if (decoded === "." || decoded === ".." || decoded.includes("/") || decoded.includes("\\")) throw unsafePathError();
    }
    return path;
}

export function assetUrl(assetId: string) {
    return safeApiPath(`${API_PREFIX}assets/${encodeURIComponent(assetId)}`);
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
    const safePath = safeApiPath(path);
    const response = await fetch(safePath, { ...init, credentials: "same-origin", headers: { Accept: "application/json", ...init.headers } });
    if (!response.ok) {
        const error = (await response.json().catch(() => null)) as ApiError | null;
        throw new Error(error?.message || `Request failed (${response.status})`);
    }
    return response.json() as Promise<T>;
}
