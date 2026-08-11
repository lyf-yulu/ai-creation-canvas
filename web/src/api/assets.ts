import { apiFetch, assetUrl, csrfTokenForRequest, safeApiPath } from "./client";
import type { AssetRef, OwnedMediaAsset } from "./contracts";
import type { GraphMediaType } from "@/features/graph/contracts";

type UploadResponse = {
    asset_id?: unknown;
    kind?: unknown;
    status?: unknown;
    media_type?: unknown;
    mime_type?: unknown;
    size_bytes?: unknown;
};

function assetFromResponse(value: unknown): AssetRef {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("媒体上传响应无效，请重试。");
    const response = value as UploadResponse;
    const id = response.asset_id;
    if (typeof id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(id)
        || (response.kind !== "reference" && response.kind !== "portrait")
        || (response.status !== "processing" && response.status !== "active" && response.status !== "failed")
        || (response.media_type !== "image" && response.media_type !== "video" && response.media_type !== "audio")
        || typeof response.mime_type !== "string" || !response.mime_type.startsWith(`${response.media_type}/`)
        || typeof response.size_bytes !== "number" || !Number.isSafeInteger(response.size_bytes) || response.size_bytes < 1) {
        throw new Error("媒体上传响应无效，请重试。");
    }
    return {
        id,
        kind: response.kind,
        status: response.status,
        media_type: response.media_type,
        mime_type: response.mime_type,
        size_bytes: response.size_bytes,
        content_url: safeApiPath(`${assetUrl(id)}/content`),
    };
}

function ownedAssetFromResponse(value: unknown, expectedMediaType: GraphMediaType): OwnedMediaAsset {
    const asset = assetFromResponse(value);
    if (asset.kind !== "reference" || asset.status !== "active" || asset.media_type !== expectedMediaType
        || typeof asset.size_bytes !== "number" || typeof asset.content_url !== "string") {
        throw new Error("媒体上传响应无效，请重试。");
    }
    return asset as OwnedMediaAsset;
}

export async function fetchAsset(id: string) {
    const response = await apiFetch<unknown>(`/api/v1/assets/${encodeURIComponent(id)}`);
    return assetFromResponse(response);
}

export function uploadMediaAsset(file: File, mediaType: GraphMediaType, onProgress: (percent: number) => void = () => undefined): Promise<OwnedMediaAsset> {
    return new Promise((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open("POST", safeApiPath("/api/v1/assets"));
        request.withCredentials = true;
        request.setRequestHeader("Accept", "application/json");
        const csrf = csrfTokenForRequest();
        if (csrf) request.setRequestHeader("X-CSRF-Token", csrf);
        request.upload.addEventListener("progress", (event) => {
            if (!event.lengthComputable || event.total <= 0) return;
            onProgress(Math.max(0, Math.min(99, Math.round(event.loaded / event.total * 100))));
        });
        request.addEventListener("load", () => {
            if (request.status < 200 || request.status >= 300) {
                reject(new Error("媒体上传失败，请重试。"));
                return;
            }
            try {
                const asset = ownedAssetFromResponse(JSON.parse(request.responseText) as unknown, mediaType);
                onProgress(100);
                resolve(asset);
            } catch {
                reject(new Error("媒体上传响应无效，请重试。"));
            }
        });
        request.addEventListener("error", () => reject(new Error("媒体上传失败，请检查网络后重试。")));
        request.addEventListener("abort", () => reject(new Error("媒体上传已取消。")));
        const body = new FormData();
        body.append("kind", "reference");
        body.append("media_type", mediaType);
        body.append("file", file, file.name);
        request.send(body);
    });
}
