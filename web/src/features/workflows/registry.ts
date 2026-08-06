import type { WorkflowDefinition } from "./types";

export type WorkflowRegistry = {
    registerWorkflow: (definition: WorkflowDefinition<never, unknown>) => void;
    ensureWorkflow: (definition: WorkflowDefinition<never, unknown>, owner: string) => boolean;
    getWorkflow: (id: string) => WorkflowDefinition<never, unknown> | undefined;
};

function freezeData<T>(value: T): T {
    if (Array.isArray(value)) return Object.freeze(value.map(freezeData)) as T;
    if (!value || typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype) return value;
    return Object.freeze(Object.fromEntries(Object.entries(value).map(([key, item]) => [key, freezeData(item)]))) as T;
}

function createRegistry(workflows: Map<string, WorkflowDefinition<never, unknown>>, owners: Map<string, string>): WorkflowRegistry {
    const registerWorkflow = (definition: WorkflowDefinition<never, unknown>, owner?: string) => {
        if (!definition.id || !Number.isInteger(definition.version) || definition.version < 1) throw new Error("workflow definition requires a stable id and positive integer version");
        if (workflows.has(definition.id)) throw new Error(`duplicate workflow: ${definition.id}`);
        workflows.set(definition.id, freezeData(definition));
        if (owner) owners.set(definition.id, owner);
    };
    return {
        registerWorkflow,
        ensureWorkflow(definition, owner) {
            const existing = workflows.get(definition.id);
            if (existing && owners.get(definition.id) === owner && existing.version === definition.version) return false;
            registerWorkflow(definition, owner);
            return true;
        },
        getWorkflow: (id) => workflows.get(id),
    };
}

export function createWorkflowRegistry(): WorkflowRegistry {
    const workflows = new Map<string, WorkflowDefinition<never, unknown>>();
    return createRegistry(workflows, new Map());
}

export const workflowRegistry = createRegistry(new Map(), new Map());
export const registerWorkflow = workflowRegistry.registerWorkflow;
export const getWorkflow = workflowRegistry.getWorkflow;
