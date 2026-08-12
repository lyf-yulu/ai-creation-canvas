import type { AdminLogicalModel, AdminOperationContract } from "@/api/admin";

const prompt = { port_id: "prompt", media_type: "text" as const, min_items: 1, max_items: 1 };
const objectSchema = (properties: Record<string, unknown>, required: string[] = []) => ({ type: "object", properties, ...(required.length ? { required } : {}), additionalProperties: false });
const size = { type: "string", default: "2K", "x-ark-size": { presets: ["1K", "1.5K", "2K", "3K", "4K"], min_pixels: 921600, max_pixels: 16777216, min_ratio: 0.0625, max_ratio: 16 } };

const arkImageProperties = {
    size,
    watermark: { type: "boolean", default: false },
    output_format: { type: "string", enum: ["png", "jpeg"], default: "png" },
    prompt_optimization: { type: "string", enum: ["standard", "fast"], default: "standard" },
};
const arkImageMappings = { size: "size", watermark: "watermark", output_format: "output_format", prompt_optimization: "optimize_prompt_options.mode" };
const chiyunProperties = {
    size: { type: "string", enum: ["auto", "1024x1024", "1024x1536", "1536x1024"], default: "auto" },
    output_count: { type: "integer", minimum: 1, maximum: 4, default: 1 },
};
const videoProperties = {
    ratio: { type: "string", enum: ["adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], default: "16:9" },
    resolution: { type: "string", enum: ["480p", "720p", "1080p", "4k"], default: "720p" },
    duration: { type: "integer", minimum: 4, maximum: 30, default: 5 },
    generate_audio: { type: "boolean", default: true },
    camera_fixed: { type: "boolean", default: false },
    return_last_frame: { type: "boolean", default: false },
    output_format: { type: "string", enum: ["mp4", "mov"], default: "mp4" },
    watermark: { type: "boolean", default: false },
};
const videoMappings = Object.fromEntries(Object.keys(videoProperties).map((key) => [key, key]));

export type TemplateId = "ark_image_generate" | "ark_image_edit" | "chiyun_image_edit" | "ark_video_generate";
export type AdminTemplate = { id: TemplateId; label: string; modality: "image" | "video"; adapter_type: "ark" | "chiyun_openai_images"; familyHint: string; contract: AdminOperationContract };

export const ADMIN_MODEL_TEMPLATES: readonly AdminTemplate[] = [
    { id: "ark_image_generate", label: "图像生成 · Ark", modality: "image", adapter_type: "ark", familyHint: "seedream", contract: { operation: "image.generate", input_ports: [prompt], output_media_type: "image", parameter_schema: objectSchema(arkImageProperties), parameter_mappings: arkImageMappings } },
    { id: "ark_image_edit", label: "多参考图编辑 · Ark", modality: "image", adapter_type: "ark", familyHint: "seedream", contract: { operation: "image.edit", input_ports: [prompt, { port_id: "reference_images", media_type: "image", min_items: 1, max_items: 14 }], output_media_type: "image", parameter_schema: objectSchema(arkImageProperties), parameter_mappings: arkImageMappings } },
    { id: "chiyun_image_edit", label: "多参考图编辑 · Chiyun / OpenAI Images", modality: "image", adapter_type: "chiyun_openai_images", familyHint: "nano-banana", contract: { operation: "image.edit", input_ports: [prompt, { port_id: "reference_images", media_type: "image", min_items: 1, max_items: 10 }], output_media_type: "image", parameter_schema: objectSchema(chiyunProperties, ["size", "output_count"]), parameter_mappings: { size: "size", output_count: "n" } } },
    { id: "ark_video_generate", label: "视频生成 · Ark", modality: "video", adapter_type: "ark", familyHint: "seedance", contract: { operation: "video.generate", input_ports: [prompt, { port_id: "first_frame", media_type: "image", min_items: 0, max_items: 1 }, { port_id: "last_frame", media_type: "image", min_items: 0, max_items: 1 }, { port_id: "reference_images", media_type: "image", min_items: 0, max_items: 9 }, { port_id: "reference_audio", media_type: "audio", min_items: 0, max_items: 3 }], output_media_type: "video", parameter_schema: objectSchema(videoProperties), parameter_mappings: videoMappings } },
];

export const templateForModel = (model: AdminLogicalModel | null): AdminTemplate => {
    if (!model?.operation_contracts?.[0]) return ADMIN_MODEL_TEMPLATES[0];
    const contract = model.operation_contracts[0];
    if (contract.operation === "video.generate") return ADMIN_MODEL_TEMPLATES[3];
    if (contract.operation === "image.generate") return ADMIN_MODEL_TEMPLATES[0];
    return Object.prototype.hasOwnProperty.call((contract.parameter_schema.properties || {}) as object, "output_count") ? ADMIN_MODEL_TEMPLATES[2] : ADMIN_MODEL_TEMPLATES[1];
};

export const routeTemplatesForModel = (model: AdminLogicalModel) => {
    const operation = model.operation_contracts?.[0]?.operation;
    const publicProperties = (model.operation_contracts?.[0]?.parameter_schema.properties || {}) as Record<string, unknown>;
    return ADMIN_MODEL_TEMPLATES.filter((item) => {
        if (item.contract.operation !== operation) return false;
        if (item.adapter_type !== "chiyun_openai_images") return true;
        const trusted = (item.contract.parameter_schema.properties || {}) as Record<string, unknown>;
        return Object.entries(trusted).every(([name, rule]) => JSON.stringify(publicProperties[name]) === JSON.stringify(rule));
    });
};

export const templateForRoute = (route: { adapter_type?: string; operation_contracts?: AdminOperationContract[] } | null) =>
    ADMIN_MODEL_TEMPLATES.find((item) => item.adapter_type === route?.adapter_type && item.contract.operation === route?.operation_contracts?.[0]?.operation);

export const routeContractForModel = (template: AdminTemplate, model: AdminLogicalModel): AdminOperationContract => {
    const publicContract = model.operation_contracts?.find((item) => item.operation === template.contract.operation);
    if (!publicContract) return template.contract;
    const publicPorts = new Map(publicContract.input_ports.map((port) => [port.port_id, port]));
    const input_ports = template.contract.input_ports.flatMap((port) => {
        const publicPort = publicPorts.get(port.port_id);
        if (!publicPort || publicPort.media_type !== port.media_type) return [];
        return [{ ...port, min_items: Math.max(port.min_items, publicPort.min_items), max_items: Math.min(port.max_items, publicPort.max_items) }];
    }).filter((port) => port.min_items <= port.max_items);
    const publicProperties = (publicContract.parameter_schema.properties || {}) as Record<string, unknown>;
    const trustedProperties = (template.contract.parameter_schema.properties || {}) as Record<string, unknown>;
    const properties = Object.fromEntries(Object.entries(trustedProperties).filter(([name, rule]) => JSON.stringify(publicProperties[name]) === JSON.stringify(rule)));
    const required = (template.contract.parameter_schema.required as string[] | undefined)?.filter((name) => name in properties) || [];
    return {
        ...template.contract,
        input_ports,
        parameter_schema: objectSchema(properties, required),
        parameter_mappings: Object.fromEntries(Object.entries(template.contract.parameter_mappings).filter(([name]) => name in properties)),
    };
};
