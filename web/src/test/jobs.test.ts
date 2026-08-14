import { expect, it, vi } from "vitest";
import { ApiRequestError } from "@/api/client";
import { waitForJob } from "@/api/jobs";

it("polls queued jobs until a protected asset result succeeds", async () => {
    const fetchJob = vi.fn().mockResolvedValueOnce({ id: "j", status: "queued" }).mockResolvedValueOnce({ id: "j", status: "running" }).mockResolvedValueOnce({ id: "j", status: "succeeded", result: { asset_id: "asset-1", mime_type: "image/png" } });
    const sleep = vi.fn().mockResolvedValue(undefined);
    await expect(waitForJob("j", { fetchJob, sleep, pollIntervalMs: 1, maxWaitMs: 10 })).resolves.toMatchObject({ status: "succeeded" });
    expect(sleep).toHaveBeenCalledTimes(2);
});

it("preserves structured API errors from a failed job", async () => {
    const error = { code: "rate_limited", message: "Retry later", retryable: true, request_id: "req-1", phase: "submit" };
    await expect(waitForJob("j", { fetchJob: vi.fn().mockResolvedValue({ id: "j", status: "failed", error }), sleep: vi.fn() })).rejects.toBeInstanceOf(ApiRequestError);
    await expect(waitForJob("j", { fetchJob: vi.fn().mockResolvedValue({ id: "j", status: "failed", error }), sleep: vi.fn() })).rejects.toMatchObject(error);
});

it("fails only on terminal failure or timeout", async () => {
    const pending = vi.fn().mockResolvedValue({ id: "j", status: "queued" });
    let now = 0;
    await expect(waitForJob("j", { fetchJob: pending, sleep: async (milliseconds) => { now += milliseconds; }, now: () => now, pollIntervalMs: 1, maxWaitMs: 2 })).rejects.toThrow("timed out");
    await expect(waitForJob("j", { fetchJob: vi.fn().mockResolvedValue({ id: "j", status: "succeeded" }), maxWaitMs: 0 })).resolves.toMatchObject({ status: "succeeded" });
    await expect(waitForJob("j", { fetchJob: vi.fn().mockResolvedValue({ id: "j", status: "failed", error: { code: "failed", message: "failed", retryable: false, request_id: "req", phase: "run" } }), sleep: vi.fn() })).rejects.toThrow("failed");
});

it("counts request time toward an explicit deadline and forwards cancellation", async () => {
    let now = 0;
    const controller = new AbortController();
    const fetchJob = vi.fn(async (_id: string, signal?: AbortSignal) => {
        expect(signal).toBe(controller.signal);
        now = 3;
        return { id: "j", status: "running" as const };
    });
    const sleep = vi.fn().mockResolvedValue(undefined);

    await expect(waitForJob("j", { fetchJob, sleep, now: () => now, maxWaitMs: 2, signal: controller.signal })).rejects.toThrow("timed out");
    expect(sleep).not.toHaveBeenCalled();
});

it("has no default two-minute deadline and backs off between polls", async () => {
    let polls = 0;
    let elapsed = 0;
    const longFetch = vi.fn(async () => {
        polls += 1;
        return polls === 16 ? { id: "j", status: "succeeded" as const } : { id: "j", status: "running" as const };
    });
    await expect(waitForJob("j", {
        fetchJob: longFetch,
        sleep: async (milliseconds) => { elapsed += milliseconds; },
        now: () => elapsed,
        pollIntervalMs: 10_000,
    })).resolves.toMatchObject({ status: "succeeded" });
    expect(elapsed).toBeGreaterThan(120_000);

    const sleepDurations: number[] = [];
    const controller = new AbortController();
    const fetchJob = vi.fn()
        .mockResolvedValueOnce({ id: "j", status: "queued" })
        .mockResolvedValueOnce({ id: "j", status: "running" })
        .mockResolvedValueOnce({ id: "j", status: "succeeded" });
    await waitForJob("j", {
        fetchJob,
        sleep: async (milliseconds, signal) => {
            expect(signal).toBe(controller.signal);
            sleepDurations.push(milliseconds);
        },
        pollIntervalMs: 1_000,
        signal: controller.signal,
    });
    expect(sleepDurations).toEqual([1_000, 2_000]);
});

it("caps the default exponential backoff at ten seconds", async () => {
    const sleepDurations: number[] = [];
    let polls = 0;
    await waitForJob("j", {
        fetchJob: vi.fn(async () => {
            polls += 1;
            return polls === 8 ? { id: "j", status: "succeeded" as const } : { id: "j", status: "running" as const };
        }),
        sleep: async (milliseconds) => { sleepDurations.push(milliseconds); },
    });
    expect(sleepDurations).toEqual([1_000, 2_000, 4_000, 8_000, 10_000, 10_000, 10_000]);
});

it("rejects invalid polling options before fetching", async () => {
    const invalidOptions = [
        { pollIntervalMs: 0 },
        { pollIntervalMs: Number.NaN },
        { pollIntervalMs: Number.POSITIVE_INFINITY },
        { pollIntervalMs: 2_147_483_648, maxPollIntervalMs: 2_147_483_648 },
        { maxPollIntervalMs: 0 },
        { maxPollIntervalMs: Number.NaN },
        { maxPollIntervalMs: Number.POSITIVE_INFINITY },
        { maxPollIntervalMs: 2_147_483_648 },
        { pollIntervalMs: 2, maxPollIntervalMs: 1 },
        { maxWaitMs: -1 },
        { maxWaitMs: Number.NaN },
        { maxWaitMs: Number.POSITIVE_INFINITY },
    ];

    for (const option of invalidOptions) {
        const fetchJob = vi.fn();
        await expect(waitForJob("j", { ...option, fetchJob })).rejects.toThrow("Job polling options are invalid");
        expect(fetchJob).not.toHaveBeenCalled();
    }
});

it("rejects an invalid calculated deadline before fetching", async () => {
    for (const now of [() => Number.NaN, () => Number.MAX_VALUE]) {
        const fetchJob = vi.fn();
        await expect(waitForJob("j", { fetchJob, now, maxWaitMs: Number.MAX_VALUE })).rejects.toThrow("Job polling options are invalid");
        expect(fetchJob).not.toHaveBeenCalled();
    }
});

it("aborts the default sleep promptly and passes the signal to fetch", async () => {
    const controller = new AbortController();
    const fetchJob = vi.fn(async (_id: string, signal?: AbortSignal) => {
        expect(signal).toBe(controller.signal);
        return { id: "j", status: "running" as const };
    });
    const waiting = waitForJob("j", { fetchJob, pollIntervalMs: 10_000, signal: controller.signal });

    await Promise.resolve();
    expect(fetchJob).toHaveBeenCalledTimes(1);
    controller.abort();

    await expect(waiting).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchJob).toHaveBeenCalledTimes(1);
});
