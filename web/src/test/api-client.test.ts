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

    await expect(apiFetch("/api/v1/jobs")).rejects.toMatchObject({ code, message: "safe failure", request_id: "req-123", phase: "submit", retryable } satisfies Partial<ApiRequestError>);
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
