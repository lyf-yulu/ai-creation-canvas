import { ApiRequestError, apiFetch, safeApiPath } from "./client";
import type { JobRequest, JobState } from "./contracts";

export const createJob = (job: JobRequest, signal?: AbortSignal) => apiFetch<JobState>("/api/v1/jobs", { method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(job) });
export const fetchJob = (id: string, signal?: AbortSignal) => apiFetch<JobState>(`/api/v1/jobs/${encodeURIComponent(id)}`, { signal });
export const cancelJob = (id: string) => apiFetch<JobState>(`/api/v1/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
type AssetLike = { asset_id?: string; [key: string]: unknown };
export function assetIdsForReferences(references: AssetLike[]) {
    return references.map((reference) => {
        if (!reference.asset_id) throw new Error("参考资源需先上传资产后再提交任务");
        return reference.asset_id;
    });
}
type WaitOptions = { fetchJob?: typeof fetchJob; sleep?: (milliseconds: number, signal?: AbortSignal) => Promise<void>; now?: () => number; pollIntervalMs?: number; maxPollIntervalMs?: number; maxWaitMs?: number; signal?: AbortSignal };
const MAX_TIMER_DELAY_MS = 2_147_483_647;
const defaultSleep = (milliseconds: number, signal?: AbortSignal) => new Promise<void>((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException("Aborted", "AbortError")); return; }
    const onAbort = () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); };
    const timer = setTimeout(() => { signal?.removeEventListener("abort", onAbort); resolve(); }, milliseconds);
    signal?.addEventListener("abort", onAbort, { once: true });
});
export async function waitForJob(id: string, options: WaitOptions = {}): Promise<JobState> {
    const getJob = options.fetchJob || fetchJob;
    const sleep = options.sleep || defaultSleep;
    const now = options.now || (() => performance.now());
    const pollIntervalMs = options.pollIntervalMs ?? 1_000;
    const maxPollIntervalMs = options.maxPollIntervalMs ?? 10_000;
    const maxWaitMs = options.maxWaitMs ?? Number.POSITIVE_INFINITY;
    if (!Number.isFinite(pollIntervalMs) || pollIntervalMs <= 0 || pollIntervalMs > MAX_TIMER_DELAY_MS
        || !Number.isFinite(maxPollIntervalMs) || maxPollIntervalMs <= 0 || maxPollIntervalMs > MAX_TIMER_DELAY_MS
        || pollIntervalMs > maxPollIntervalMs
        || (options.maxWaitMs !== undefined && (!Number.isFinite(maxWaitMs) || maxWaitMs < 0))) {
        throw new Error("Job polling options are invalid");
    }
    let deadline = Number.POSITIVE_INFINITY;
    if (Number.isFinite(maxWaitMs)) {
        const startedAt = now();
        deadline = startedAt + maxWaitMs;
        if (!Number.isFinite(startedAt) || !Number.isFinite(deadline)) throw new Error("Job polling options are invalid");
    }
    let wait = pollIntervalMs;
    while (true) {
        if (options.signal?.aborted) throw new DOMException("Aborted", "AbortError");
        const job = await getJob(id, options.signal);
        if (job.status === "succeeded") return job;
        if (job.status === "failed") {
            const error = job.error;
            if (error) throw new ApiRequestError(error);
            throw new Error(`Job ${job.status}`);
        }
        const remaining = deadline - now();
        if (remaining <= 0) break;
        await sleep(Math.min(wait, remaining), options.signal);
        wait = Math.min(wait * 2, maxPollIntervalMs);
    }
    throw new Error(`Job ${id} timed out`);
}

export function protectedResultUrl(job: JobState) {
    if (!job.result_url) throw new Error("Job did not return a protected result URL");
    return safeApiPath(job.result_url);
}
