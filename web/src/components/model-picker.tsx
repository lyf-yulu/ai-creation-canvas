import type { ModelOperation, ModelSpec } from "@/api/contracts";

export type ParameterControl = { name: string; type: "number" | "integer" | "string" | "boolean" | "enum"; required?: boolean; minimum?: number; maximum?: number; default?: string | number | boolean; enum?: readonly (string | number)[] };

/** The catalog is data, never executable UI. Unknown JSON-schema fields are ignored. */
export function parameterControls(schema: Record<string, unknown>): ParameterControl[] {
    const objectSchema = schema.type === "object" && schema.properties && typeof schema.properties === "object" && !Array.isArray(schema.properties);
    const entries = Object.entries((objectSchema ? schema.properties : schema) as Record<string, unknown>);
    const required = objectSchema && Array.isArray(schema.required) ? new Set(schema.required.filter((name): name is string => typeof name === "string")) : new Set<string>();
    return entries.flatMap(([name, raw]) => {
        if (!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(name) || !raw || typeof raw !== "object" || Array.isArray(raw)) return [];
        const value = raw as Record<string, unknown>;
        const type = value.type;
        if (Array.isArray(value.enum)) {
            if (!value.enum.length || !value.enum.every((item) => typeof item === "string" || typeof item === "number") || (type !== undefined && type !== "string" && type !== "number" && type !== "integer")) return [];
            const values = value.enum as (string | number)[];
            if ((type === "string" && values.some((item) => typeof item !== "string")) || ((type === "number" || type === "integer") && values.some((item) => typeof item !== "number" || (type === "integer" && !Number.isInteger(item)))) || (value.default !== undefined && !values.some((item) => Object.is(item, value.default)))) return [];
            return [{ name, type: "enum", required: required.has(name) || value.required === true, enum: values, default: value.default as string | number | undefined }];
        }
        if (type === "number" || type === "integer" || type === "string" || type === "boolean") {
            const result: ParameterControl = { name, type, required: required.has(name) || value.required === true };
            if ((type === "number" || type === "integer") && typeof value.minimum === "number") result.minimum = value.minimum;
            if ((type === "number" || type === "integer") && typeof value.maximum === "number") result.maximum = value.maximum;
            if (["string", "number", "boolean"].includes(typeof value.default)) result.default = value.default as string | number | boolean;
            return [result];
        }
        return [];
    });
}

export function modelSupportsOperation(model: ModelSpec, operation: ModelOperation) {
    return model.operations.includes(operation);
}

export function modelsForOperation(models: readonly ModelSpec[], operation: ModelOperation, inputMedia?: "text" | "image") {
    return models.filter((model) => modelSupportsOperation(model, operation) && (!inputMedia || model.input_media.includes(inputMedia)));
}
