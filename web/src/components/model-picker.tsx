import type { ModelOperation, ModelSpec } from "@/api/contracts";

export type ParameterControl = { name: string; type: "number" | "integer" | "string" | "boolean" | "enum"; minimum?: number; maximum?: number; default?: string | number | boolean; enum?: readonly (string | number)[] };

/** The catalog is data, never executable UI. Unknown JSON-schema fields are ignored. */
export function parameterControls(schema: Record<string, unknown>): ParameterControl[] {
    return Object.entries(schema).flatMap(([name, raw]) => {
        if (!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(name) || !raw || typeof raw !== "object" || Array.isArray(raw)) return [];
        const value = raw as Record<string, unknown>;
        const type = value.type;
        if (type === "number" || type === "integer" || type === "string" || type === "boolean") {
            const result: ParameterControl = { name, type };
            if ((type === "number" || type === "integer") && typeof value.minimum === "number") result.minimum = value.minimum;
            if ((type === "number" || type === "integer") && typeof value.maximum === "number") result.maximum = value.maximum;
            if (["string", "number", "boolean"].includes(typeof value.default)) result.default = value.default as string | number | boolean;
            return [result];
        }
        if (Array.isArray(value.enum) && value.enum.length && value.enum.every((item) => typeof item === "string" || typeof item === "number")) return [{ name, type: "enum", enum: value.enum as (string | number)[], default: (typeof value.default === "string" || typeof value.default === "number") ? value.default : undefined }];
        return [];
    });
}

export function modelSupportsOperation(model: ModelSpec, operation: ModelOperation) {
    return model.operations.includes(operation);
}

export function modelsForOperation(models: readonly ModelSpec[], operation: ModelOperation, inputMedia?: "text" | "image") {
    return models.filter((model) => modelSupportsOperation(model, operation) && (!inputMedia || model.input_media.includes(inputMedia)));
}
