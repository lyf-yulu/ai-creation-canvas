import { useCallback, useEffect, useRef, useState } from "react";
import { createJob, fetchJob } from "@/api/jobs";
import type { JobRequest, JobState } from "@/api/contracts";
import { captureScopedStore, isStorageLeaseActive, onStorageScopeCleared, type ScopedStoreLease } from "@/storage/scope";
import { generationErrorMessage, safeFailureMetadata } from "./error-message";
import { ApiRequestError } from "@/api/client";

export type GenerationStatus = "idle" | "submitting" | "queued" | "running" | "succeeded" | "failed";
export type GenerationState = { status: GenerationStatus; jobId?: string; message?: string; retryable?: boolean };
type PendingRef = { request: JobRequest; jobId?: string };
type GenerationApi = { create: (job: JobRequest, signal?: AbortSignal) => Promise<JobState>; fetch: (id: string, signal?: AbortSignal) => Promise<JobState> };
type Options = { api?: GenerationApi; pollDelayMs?: number; idempotencyKey?: () => string; onSucceeded?: (job: JobState) => void; onFailed?: (details: { request: JobRequest; message: string; requestId?: string; phase?: string }) => void };
const REFS_KEY = "generation-job-refs";
const delay = (ms: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => { const timer = setTimeout(resolve, ms); signal.addEventListener("abort", () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); }, { once: true }); });
const stateFor = (job: JobState): GenerationStatus => job.status === "uploading" || job.status === "submitting" ? "submitting" : job.status;

export function useGenerationJob(options: Options = {}) {
    const apiRef = useRef<GenerationApi>(options.api ?? { create: createJob, fetch: fetchJob });
    const optionsRef = useRef(options);
    apiRef.current = options.api ?? apiRef.current;
    optionsRef.current = options;
    const [state, setState] = useState<GenerationState>({ status: "idle" });
    const controller = useRef<AbortController | null>(null);
    const lease = useRef<ScopedStoreLease | null>(null);
    const refs = useRef(new Map<string, PendingRef>());
    const completed = useRef(new Set<string>());
    const active = useRef(true);
    const persist = useCallback(async () => {
        const current = lease.current;
        if (current && isStorageLeaseActive(current)) await current.store.setItem(REFS_KEY, [...refs.current.values()]);
    }, []);
    const publish = useCallback((next: GenerationState, captured: ScopedStoreLease | null) => {
        if (active.current && (!captured || isStorageLeaseActive(captured))) setState(next);
    }, []);
    const stop = useCallback(() => controller.current?.abort(), []);
    const poll = useCallback(async (jobId: string, captured: ScopedStoreLease | null) => {
        stop();
        const signal = new AbortController(); controller.current = signal;
        let wait = optionsRef.current.pollDelayMs ?? 1_000;
        try {
            while (!signal.signal.aborted && (!captured || isStorageLeaseActive(captured))) {
                const job = await apiRef.current.fetch(jobId, signal.signal);
                const status = stateFor(job);
                publish({ status, jobId }, captured);
                if (status === "succeeded") {
                    const operation = refs.current.get(jobId)?.request.operation;
                    refs.current.delete(jobId); await persist();
                    const completeJob = { ...job, operation: job.operation ?? operation };
                    if (!completed.current.has(jobId) && (!captured || isStorageLeaseActive(captured))) { completed.current.add(jobId); optionsRef.current.onSucceeded?.(completeJob); }
                    return;
                }
                if (status === "failed") {
                    const request = refs.current.get(jobId)?.request;
                    refs.current.delete(jobId); await persist();
                    const message = generationErrorMessage(job.error ? new ApiRequestError(job.error) : new Error("failed"));
                    publish({ status, jobId, message, retryable: job.error?.retryable }, captured);
                    const safe = job.error ? { request_id: job.error.request_id, phase: job.error.phase } : undefined;
                    if (request) optionsRef.current.onFailed?.({ request, message, requestId: safe?.request_id, phase: safe?.phase });
                    return;
                }
                await delay(wait, signal.signal); wait = Math.min(wait * 2, 10_000);
            }
        } catch (error) { if (!(error instanceof DOMException && error.name === "AbortError") && (!captured || isStorageLeaseActive(captured))) publish({ status: "failed", jobId, message: generationErrorMessage(error), retryable: true }, captured); }
    }, [persist, publish, stop]);
    const submit = useCallback(async (input: Omit<JobRequest, "idempotency_key">) => {
        const captured = lease.current = captureScopedStore(REFS_KEY);
        const matching = [...refs.current.values()].find((item) => !item.jobId && JSON.stringify({ ...item.request, idempotency_key: undefined }) === JSON.stringify({ ...input, idempotency_key: undefined }));
        const request = matching?.request ?? { ...input, idempotency_key: optionsRef.current.idempotencyKey?.() ?? crypto.randomUUID() };
        refs.current.set(request.idempotency_key, { request }); await persist(); publish({ status: "submitting" }, captured);
        try {
            const submitController = new AbortController(); controller.current = submitController;
            const job = await apiRef.current.create(request, submitController.signal);
            refs.current.delete(request.idempotency_key); refs.current.set(job.id, { request, jobId: job.id }); await persist();
            await poll(job.id, captured);
        } catch (error) { publish({ status: "failed", message: generationErrorMessage(error), retryable: true }, captured); throw error; }
    }, [persist, poll, publish]);
    const resume = useCallback(async (jobId: string) => { const captured = lease.current = captureScopedStore(REFS_KEY); await poll(jobId, captured); }, [poll]);
    useEffect(() => { active.current = true; lease.current = captureScopedStore(REFS_KEY); const captured = lease.current; void (async () => { if (!captured || !isStorageLeaseActive(captured)) return; const saved = await captured.store.getItem<PendingRef[]>(REFS_KEY); if (!isStorageLeaseActive(captured)) return; for (const ref of saved || []) { if (ref.jobId) { refs.current.set(ref.jobId, ref); void poll(ref.jobId, captured); } } })(); const unsubscribe = onStorageScopeCleared(stop); return () => { active.current = false; unsubscribe(); stop(); }; }, [poll, stop]);
    return { state, submit, resume, cancel: stop, failureMetadata: safeFailureMetadata };
}
