import { useCallback, useEffect, useRef, useState } from "react";
import { createJob, fetchJob } from "@/api/jobs";
import type { JobRequest, JobState } from "@/api/contracts";
import { captureScopedStore, isStorageLeaseActive, onStorageScopeCleared, type ScopedStoreLease } from "@/storage/scope";
import { generationErrorMessage, safeFailureMetadata } from "./error-message";
import { ApiRequestError } from "@/api/client";

export type GenerationStatus = "idle" | "submitting" | "queued" | "running" | "succeeded" | "failed";
export type GenerationState = { status: GenerationStatus; jobId?: string; message?: string; retryable?: boolean };
export type PendingRef = { request: JobRequest; jobId?: string; sourceNodeId?: string };
type GenerationApi = { create: (job: JobRequest, signal?: AbortSignal) => Promise<JobState>; fetch: (id: string, signal?: AbortSignal) => Promise<JobState> };
type SubmitInput = Omit<JobRequest, "idempotency_key"> & { sourceNodeId?: string };
type Options = { api?: GenerationApi; pollDelayMs?: number; idempotencyKey?: () => string; onSucceeded?: (job: JobState, ref?: PendingRef) => void; onFailed?: (details: { request: JobRequest; sourceNodeId?: string; message: string; requestId?: string; phase?: string }) => void };
const REFS_KEY = "generation-job-refs";
const delay = (ms: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => { const timer = setTimeout(resolve, ms); signal.addEventListener("abort", () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); }, { once: true }); });
const stateFor = (job: JobState): GenerationStatus => job.status === "uploading" || job.status === "submitting" ? "submitting" : job.status;

export function useGenerationJob(options: Options = {}) {
    const apiRef = useRef<GenerationApi>(options.api ?? { create: createJob, fetch: fetchJob });
    const optionsRef = useRef(options);
    apiRef.current = options.api ?? apiRef.current;
    optionsRef.current = options;
    const [state, setState] = useState<GenerationState>({ status: "idle" });
    const controllers = useRef(new Map<string, AbortController>());
    const lease = useRef<ScopedStoreLease | null>(null);
    const refs = useRef(new Map<string, PendingRef>());
    const restoredVersion = useRef<number | null>(null);
    const restoring = useRef<Promise<void> | null>(null);
    const completed = useRef(new Set<string>());
    const active = useRef(true);
    const persist = useCallback(async () => {
        const current = lease.current;
        if (current && isStorageLeaseActive(current)) await current.store.setItem(REFS_KEY, [...refs.current.values()]);
    }, []);
    const publish = useCallback((next: GenerationState, captured: ScopedStoreLease | null) => {
        if (active.current && (!captured || isStorageLeaseActive(captured))) setState(next);
    }, []);
    const stop = useCallback((jobId?: string) => {
        if (jobId) { controllers.current.get(jobId)?.abort(); controllers.current.delete(jobId); return; }
        controllers.current.forEach((controller) => controller.abort()); controllers.current.clear();
    }, []);
    const restore = useCallback(async (captured: ScopedStoreLease | null) => {
        if (!captured || !isStorageLeaseActive(captured) || restoredVersion.current === captured.version) return;
        if (restoring.current) return restoring.current;
        restoring.current = (async () => {
            const saved = await captured.store.getItem<PendingRef[]>(REFS_KEY);
            if (!isStorageLeaseActive(captured)) return;
            refs.current.clear();
            for (const ref of saved || []) refs.current.set(ref.jobId || ref.request.idempotency_key, ref);
            restoredVersion.current = captured.version;
        })();
        try { await restoring.current; } finally { restoring.current = null; }
    }, []);
    const poll = useCallback(async (jobId: string, captured: ScopedStoreLease | null) => {
        if (controllers.current.has(jobId)) return;
        const signal = new AbortController(); controllers.current.set(jobId, signal);
        let wait = optionsRef.current.pollDelayMs ?? 1_000;
        try {
            while (!signal.signal.aborted && (!captured || isStorageLeaseActive(captured))) {
                const job = await apiRef.current.fetch(jobId, signal.signal);
                const status = stateFor(job);
                publish({ status, jobId }, captured);
                if (status === "succeeded") {
                    const ref = refs.current.get(jobId);
                    const operation = ref?.request.operation;
                    refs.current.delete(jobId); await persist();
                    const completeJob = { ...job, operation: job.operation ?? operation };
                    if (!completed.current.has(jobId) && (!captured || isStorageLeaseActive(captured))) { completed.current.add(jobId); optionsRef.current.onSucceeded?.(completeJob, ref); }
                    return;
                }
                if (status === "failed") {
                    const ref = refs.current.get(jobId);
                    const request = ref?.request;
                    refs.current.delete(jobId); await persist();
                    const message = generationErrorMessage(job.error ? new ApiRequestError(job.error) : new Error("failed"));
                    publish({ status, jobId, message, retryable: job.error?.retryable }, captured);
                    const safe = job.error ? { request_id: job.error.request_id, phase: job.error.phase } : undefined;
                    if (request) optionsRef.current.onFailed?.({ request, sourceNodeId: ref?.sourceNodeId, message, requestId: safe?.request_id, phase: safe?.phase });
                    return;
                }
                await delay(wait, signal.signal); wait = Math.min(wait * 2, 10_000);
            }
        } catch (error) { if (!(error instanceof DOMException && error.name === "AbortError") && (!captured || isStorageLeaseActive(captured))) publish({ status: "failed", jobId, message: generationErrorMessage(error), retryable: true }, captured); } finally { controllers.current.delete(jobId); }
    }, [persist, publish, stop]);
    const submit = useCallback(async (input: SubmitInput) => {
        const captured = lease.current = captureScopedStore(REFS_KEY);
        await restore(captured);
        const { sourceNodeId, ...jobInput } = input;
        const matching = [...refs.current.values()].find((item) => {
            const { idempotency_key: _key, ...pendingInput } = item.request;
            return !item.jobId && JSON.stringify(pendingInput) === JSON.stringify(jobInput);
        });
        const request = matching?.request ?? { ...jobInput, idempotency_key: optionsRef.current.idempotencyKey?.() ?? crypto.randomUUID() };
        refs.current.set(request.idempotency_key, { request, sourceNodeId: matching?.sourceNodeId ?? sourceNodeId }); await persist(); publish({ status: "submitting" }, captured);
        try {
            const submitController = new AbortController(); controllers.current.set(request.idempotency_key, submitController);
            const job = await apiRef.current.create(request, submitController.signal);
            controllers.current.delete(request.idempotency_key);
            const ref = refs.current.get(request.idempotency_key); refs.current.delete(request.idempotency_key); refs.current.set(job.id, { request, jobId: job.id, sourceNodeId: ref?.sourceNodeId }); await persist();
            await poll(job.id, captured);
        } catch (error) {
            controllers.current.delete(request.idempotency_key);
            const safe = safeFailureMetadata(error);
            const message = generationErrorMessage(error);
            publish({ status: "failed", message, retryable: true }, captured);
            optionsRef.current.onFailed?.({ request, sourceNodeId, message, requestId: safe?.request_id, phase: safe?.phase });
            throw error;
        }
    }, [persist, poll, publish, restore]);
    const resume = useCallback(async (jobId: string) => { const captured = lease.current = captureScopedStore(REFS_KEY); await poll(jobId, captured); }, [poll]);
    useEffect(() => { active.current = true; lease.current = captureScopedStore(REFS_KEY); const captured = lease.current; void (async () => { await restore(captured); if (!captured || !isStorageLeaseActive(captured)) return; for (const ref of refs.current.values()) if (ref.jobId) void poll(ref.jobId, captured); })(); const unsubscribe = onStorageScopeCleared(stop); return () => { active.current = false; unsubscribe(); stop(); }; }, [poll, restore, stop]);
    return { state, submit, resume, cancel: stop, failureMetadata: safeFailureMetadata };
}
