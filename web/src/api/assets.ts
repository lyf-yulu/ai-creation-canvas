import { apiFetch } from "./client";
import type { AssetRef } from "./contracts";
export const fetchAsset = (id: string) => apiFetch<AssetRef>(`/api/v1/assets/${encodeURIComponent(id)}`);
