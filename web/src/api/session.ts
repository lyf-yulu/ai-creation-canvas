import { apiFetch } from "./client";
import type { SessionResponse } from "./contracts";

export type SkinColors = Record<string, string>;

export const fetchSession = () => apiFetch<SessionResponse>("/api/v1/session");

export const updateSessionSkin = (colors: SkinColors) =>
    apiFetch<{ skin: SkinColors }>("/api/v1/session/skin", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: 1, colors }),
    });
