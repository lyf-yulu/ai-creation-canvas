import type { WorkflowDefinition } from "./types";

export type WorkflowRegistry = {
    registerWorkflow: (definition: WorkflowDefinition<never, unknown>) => void;
    getWorkflow: (id: string) => WorkflowDefinition<never, unknown> | undefined;
};

type BootstrapState = {
    owners: Map<string, string>;
    fingerprints: Map<string, string>;
    registerBuiltin: (definition: WorkflowDefinition<never, unknown>, owner: string, fingerprint: string) => void;
};
const bootstrapStates = new WeakMap<WorkflowRegistry, BootstrapState>();

function freezeData<T>(value: T): T {
    if (Array.isArray(value)) return Object.freeze(value.map(freezeData)) as T;
    if (!value || typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype) return value;
    return Object.freeze(Object.fromEntries(Object.entries(value).map(([key, item]) => [key, freezeData(item)]))) as T;
}

function fingerprint(value: unknown, seen = new Set<object>()): string {
    if (typeof value === "function") return `function:${Function.prototype.toString.call(value)}`;
    if (value === null || typeof value !== "object") return `${typeof value}:${String(value)}`;
    if (seen.has(value)) throw new Error("builtin definition fingerprint cannot contain cycles");
    seen.add(value);
    const result = Array.isArray(value)
        ? `array:[${value.map((item) => fingerprint(item, seen)).join(",")}]`
        : `object:{${Object.keys(value).sort().map((key) => `${key}=${fingerprint((value as Record<string, unknown>)[key], seen)}`).join(",")}}`;
    seen.delete(value);
    return result;
}

function validate(definition: WorkflowDefinition<never, unknown>) {
    if (!definition.id || !Number.isInteger(definition.version) || definition.version < 1) throw new Error("workflow definition requires a stable id and positive integer version");
}

function createRegistry(): WorkflowRegistry {
    const workflows = new Map<string, WorkflowDefinition<never, unknown>>();
    const owners = new Map<string, string>();
    const fingerprints = new Map<string, string>();
    const store = (definition: WorkflowDefinition<never, unknown>, owner?: string, definitionFingerprint?: string) => {
        validate(definition);
        if (workflows.has(definition.id)) throw new Error(`duplicate workflow: ${definition.id}`);
        workflows.set(definition.id, freezeData(definition));
        if (owner && definitionFingerprint) {
            owners.set(definition.id, owner);
            fingerprints.set(definition.id, definitionFingerprint);
        }
    };
    const registry: WorkflowRegistry = { registerWorkflow: (definition) => store(definition), getWorkflow: (id) => workflows.get(id) };
    bootstrapStates.set(registry, { owners, fingerprints, registerBuiltin: (definition, owner, definitionFingerprint) => store(definition, owner, definitionFingerprint) });
    return registry;
}

/** Narrow built-in-only bootstrap path; ordinary extensions use registerWorkflow. */
export function bootstrapBuiltinWorkflow(registry: WorkflowRegistry, definition: WorkflowDefinition<never, unknown>, owner: string): boolean {
    const state = bootstrapStates.get(registry);
    if (!state) throw new Error("unknown workflow registry");
    const definitionFingerprint = fingerprint(definition);
    if (registry.getWorkflow(definition.id)) {
        if (state.owners.get(definition.id) === owner) {
            if (state.fingerprints.get(definition.id) === definitionFingerprint) return false;
            throw new Error(`builtin definition drift: ${definition.id}; bump version before bootstrapping`);
        }
        registry.registerWorkflow(definition);
    }
    state.registerBuiltin(definition, owner, definitionFingerprint);
    return true;
}

export function createWorkflowRegistry(): WorkflowRegistry {
    return createRegistry();
}

export const workflowRegistry = createRegistry();
export const registerWorkflow = workflowRegistry.registerWorkflow;
export const getWorkflow = workflowRegistry.getWorkflow;
