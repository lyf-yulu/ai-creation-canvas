export type ModelOperation = "image.generate" | "image.edit" | "video.generate" | "video.image_to_video";
export type PortalSession = { user_id: string; username: string; role: "admin" | "user" | "viewer" };
export type ModelSpec = { id: string; service_id: string; display_name: string; operations: ModelOperation[]; input_media: ("text" | "image")[]; parameter_schema: Record<string, unknown>; requires_asset_kind?: "portrait" };
export type AssetRef = { id: string; kind: "reference" | "portrait"; status: "processing" | "active" | "failed"; mime_type: string };
export type JobRequest = { operation: ModelOperation; model_id: string; prompt: string; params: Record<string, unknown>; asset_ids: string[]; idempotency_key: string };
export type ApiError = { code: string; message: string; retryable: boolean; request_id: string; phase: string };
export type JobState = { id: string; operation?: ModelOperation; status: "uploading" | "submitting" | "queued" | "running" | "succeeded" | "failed"; result_url?: string; error?: ApiError };
