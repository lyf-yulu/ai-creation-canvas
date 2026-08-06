import { apiFetch } from "./client";
import type { ModelSpec } from "./contracts";
export const fetchModels = () => apiFetch<ModelSpec[]>("/api/v1/models");
