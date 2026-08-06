import { nanoid } from "nanoid";
import { createJob } from "@/api/jobs";
import type { AiConfig } from "@/stores/use-config-store";
import type { ReferenceImage } from "@/types/image";

export type AiTextMessage = { role: "system" | "user" | "assistant"; content: string | Array<{ type: "text"; text: string } | { type: "image_url"; image_url: { url: string } }> };
type RequestOptions = { signal?: AbortSignal };
type GeneratedImage = { id: string; dataUrl: string };

async function submit(config: AiConfig, prompt: string, operation: "image.generate" | "image.edit", signal?: AbortSignal): Promise<GeneratedImage[]> {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    const job = await createJob({ operation, model_id: config.model || config.imageModel, prompt, params: { size: config.size, quality: config.quality, count: config.count, background: config.background }, asset_ids: [], idempotency_key: nanoid() });
    if (!job.result?.url) throw new Error(`图像任务已提交（${job.id}），等待任务状态接口完成`);
    return [{ id: nanoid(), dataUrl: job.result.url }];
}
export const requestGeneration = (config: AiConfig, prompt: string, options?: RequestOptions) => submit(config, prompt, "image.generate", options?.signal);
export const requestEdit = (config: AiConfig, prompt: string, _references: ReferenceImage[], _mask?: ReferenceImage, options?: RequestOptions) => submit(config, prompt, "image.edit", options?.signal);
export async function requestImageQuestion(_config: AiConfig, _messages: AiTextMessage[], _onDelta: (text: string) => void, _options?: RequestOptions) { throw new Error("文本问答将在受控任务接口可用后提供"); }
