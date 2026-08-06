import { afterEach, expect, it, vi } from "vitest";

import { ApiRequestError, apiFetch } from "@/api/client";

afterEach(() => vi.unstubAllGlobals());

it.each([
    [401, "unauthorized", false],
    [403, "forbidden", false],
    [429, "rate_limited", true],
    [500, "internal_error", true],
] as const)("normalizes HTTP %i to a stable ApiError", async (status, code, retryable) => {
    vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
            new Response(JSON.stringify({ code, message: "safe failure", request_id: "req-123", phase: "submit", retryable }), {
                status,
                headers: { "Content-Type": "application/json" },
            }),
        ),
    );

    await expect(apiFetch("/api/v1/jobs")).rejects.toMatchObject({ code, message: status >= 500 ? "The service failed to process the request." : "safe failure", request_id: "req-123", phase: "submit", retryable } satisfies Partial<ApiRequestError>);
});

it("normalizes non-JSON failures without exposing response content", async () => {
    const sensitiveResponse = `${["api", "key"].join("_")}=private\nTraceback: internal stack`;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(sensitiveResponse, { status: 500, headers: { "Content-Type": "text/plain", "X-Request-Id": "req-header" } })));

    await expect(apiFetch("/api/v1/jobs")).rejects.toMatchObject({
        code: "internal_error",
        message: "The service failed to process the request.",
        request_id: "req-header",
        phase: "response",
        retryable: true,
    } satisfies Partial<ApiRequestError>);
});

it("uses the fixed local message for 5xx JSON responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: "internal_error", message: "Traceback /srv/private.py: secret" }), { status: 500, headers: { "Content-Type": "application/json" } })));
    await expect(apiFetch("/api/v1/jobs")).rejects.toMatchObject({ message: "The service failed to process the request." });
});

it("does not expose filesystem and exception details in 4xx messages", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ message: "ENOENT /srv/private.py" }), { status: 403, headers: { "Content-Type": "application/json" } })));
    await expect(apiFetch("/api/v1/jobs")).rejects.toMatchObject({ message: "You are not allowed to perform this action." });
});

it.each(["OSError ('/srv/private.py')", "failed (C:/private.txt)", "/private.py", "failed:/srv/private.py", "path=/private.py", "file:///srv/private", "at foo (/srv/x.ts:12:3)", "bad\u0007message", "Visit https://example.com/help"])("rejects unsafe 4xx detail %s", async (message) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ message }), { status: 403, headers: { "Content-Type": "application/json" } })));
    await expect(apiFetch("/api/v1/jobs")).rejects.toMatchObject({ message: "You are not allowed to perform this action." });
});

it("keeps a short single-line user-facing 4xx message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ message: "This item is not available." }), { status: 403, headers: { "Content-Type": "application/json" } })));
    await expect(apiFetch("/api/v1/jobs")).rejects.toMatchObject({ message: "This item is not available." });
});
