import { apiFetch } from "./client";
import type { ModelSpec, PortalSession } from "./contracts";


export type AdminUser = PortalSession & {
    display_name: string;
    enabled: boolean;
    must_change_password: boolean;
    model_ids: string[];
    created_at: number;
    updated_at: number;
};

export type AdminProvider = {
    provider_id: string; display_name: string; adapter_type: string; base_url: string;
    credential_ref: string; credential_available: boolean; enabled: boolean; revision: number;
};
export type AdminModelDefinition = {
    model_id: string; provider_id: string; display_name: string; introduction: string;
    modality: "image" | "video" | "audio" | "text"; operations: string[]; enabled: boolean; revision: number;
};
export type AdminModelTemplate = { template_id: string; title: string; modality: string; operation: string };
export type AdminModelRegistry = { providers: AdminProvider[]; models: AdminModelDefinition[]; templates: AdminModelTemplate[] };

const jsonHeaders = { "Content-Type": "application/json" };

export const fetchAdminUsers = async () => (await apiFetch<{ users: AdminUser[] }>("/api/v1/admin/users")).users;
export const fetchAdminModels = async () => (await apiFetch<{ models: ModelSpec[] }>("/api/v1/admin/models")).models;
export const fetchAdminModelRegistry = () => apiFetch<AdminModelRegistry>("/api/v1/admin/model-registry");

export const createAdminProvider = (body: Omit<AdminProvider, "revision" | "credential_available">) => apiFetch<AdminProvider>("/api/v1/admin/model-registry/providers", {
    method: "POST", headers: jsonHeaders, body: JSON.stringify(body),
});

export const createAdminModel = (body: { model_id: string; provider_id: string; provider_model_name: string; display_name: string; introduction: string; template_id: string; enabled: boolean }) => apiFetch<AdminModelDefinition>("/api/v1/admin/model-registry/models", {
    method: "POST", headers: jsonHeaders, body: JSON.stringify(body),
});

export const setAdminUserEnabled = (userId: string, enabled: boolean) => apiFetch<AdminUser>(`/api/v1/admin/users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    headers: jsonHeaders,
    body: JSON.stringify({ enabled }),
});

export const replaceAdminUserModels = (userId: string, modelIds: string[]) => apiFetch<{ user_id: string; model_ids: string[] }>(`/api/v1/admin/users/${encodeURIComponent(userId)}/models`, {
    method: "PUT",
    headers: jsonHeaders,
    body: JSON.stringify({ model_ids: modelIds }),
});
