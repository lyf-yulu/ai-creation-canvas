import { apiFetch } from "./client";
import type { PortalSession } from "./contracts";
export const fetchSession = () => apiFetch<PortalSession>("/api/v1/session");
