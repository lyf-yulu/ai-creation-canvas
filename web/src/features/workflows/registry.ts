import type { WorkflowDefinition } from "./types";

export type WorkflowRegistry = {
    registerWorkflow: (definition: WorkflowDefinition<never, unknown>) => void;
    getWorkflow: (id: string) => WorkflowDefinition<never, unknown> | undefined;
};

export function createWorkflowRegistry(): WorkflowRegistry {
    const workflows = new Map<string, WorkflowDefinition<never, unknown>>();
    return {
        registerWorkflow(definition) {
            if (!definition.id || !Number.isInteger(definition.version) || definition.version < 1) throw new Error("workflow definition requires a stable id and positive integer version");
            if (workflows.has(definition.id)) throw new Error(`duplicate workflow: ${definition.id}`);
            workflows.set(definition.id, Object.freeze({ ...definition }));
        },
        getWorkflow: (id) => workflows.get(id),
    };
}

const registry = createWorkflowRegistry();
export const registerWorkflow = registry.registerWorkflow;
export const getWorkflow = registry.getWorkflow;
