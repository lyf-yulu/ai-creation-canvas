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

const jsonHeaders = { "Content-Type": "application/json" };

export const fetchAdminUsers = async () => (await apiFetch<{ users: AdminUser[] }>("/api/v1/admin/users")).users;
export const fetchAdminModels = async () => (await apiFetch<{ models: ModelSpec[] }>("/api/v1/admin/models")).models;

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
