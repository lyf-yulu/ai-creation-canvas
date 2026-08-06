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

const defaultError = (status: number): Omit<ApiError, "request_id" | "phase"> => {
    if (status === 401) return { code: "unauthorized", message: "Authentication is required.", retryable: false };
    if (status === 403) return { code: "forbidden", message: "You are not allowed to perform this action.", retryable: false };
    if (status === 429) return { code: "rate_limited", message: "Too many requests. Please try again later.", retryable: true };
    if (status >= 500) return { code: "internal_error", message: "The service failed to process the request.", retryable: true };
    return { code: "request_failed", message: "The request could not be completed.", retryable: false };
};

const safeString = (value: unknown, fallback: string, pattern: RegExp) => typeof value === "string" && pattern.test(value) ? value : fallback;
const safeMessage = (value: unknown, fallback: string) => {
    if (typeof value !== "string" || value.length > 240 || /api[_ -]?key|authorization|bearer|secret|token|traceback|stack/i.test(value)) return fallback;
    return value;
};

export class ApiRequestError extends Error implements ApiError {
    readonly code: string;
    readonly retryable: boolean;
    readonly request_id: string;
    readonly phase: string;

    constructor(details: ApiError) {
        super(details.message);
        this.name = "ApiRequestError";
        this.code = details.code;
        this.retryable = details.retryable;
        this.request_id = details.request_id;
        this.phase = details.phase;
    }
}

async function responseError(response: Response): Promise<ApiRequestError> {
    const fallback = defaultError(response.status);
    const requestId = safeString(response.headers.get("x-request-id"), "", /^[A-Za-z0-9_-]{1,128}$/);
    const contentType = response.headers.get("content-type") || "";
    let payload: Record<string, unknown> | null = null;
    if (contentType.toLowerCase().includes("application/json")) {
        const value: unknown = await response.json().catch(() => null);
        if (value && typeof value === "object" && !Array.isArray(value)) payload = value as Record<string, unknown>;
    }
    const details: ApiError = {
        code: safeString(payload?.code, fallback.code, /^[a-z0-9_.-]{1,80}$/i),
        message: safeMessage(payload?.message, fallback.message),
        retryable: typeof payload?.retryable === "boolean" ? payload.retryable : fallback.retryable,
        request_id: safeString(payload?.request_id, requestId, /^[A-Za-z0-9_-]{1,128}$/),
        phase: safeString(payload?.phase, "response", /^[a-z0-9_.-]{1,80}$/i),
    };
    return new ApiRequestError(details);
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
    const safePath = safeApiPath(path);
    const response = await fetch(safePath, { ...init, credentials: "same-origin", headers: { Accept: "application/json", ...init.headers } });
    if (!response.ok) {
        throw await responseError(response);
    }
    return response.json() as Promise<T>;
}
