import { apiFetch } from "./client";
import type { JobRequest, JobState } from "./contracts";
export const createJob = (job: JobRequest) => apiFetch<JobState>("/api/v1/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(job) });
export const fetchJob = (id: string) => apiFetch<JobState>(`/api/v1/jobs/${encodeURIComponent(id)}`);
