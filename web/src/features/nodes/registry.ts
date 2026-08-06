import type { NodeDefinition } from "./types";

export type NodeRegistry = {
    registerNode: (definition: NodeDefinition) => void;
    listNodes: () => readonly NodeDefinition[];
    getNode: (id: string) => NodeDefinition | undefined;
};

type NodeRegistryListener = () => void;
const listeners = new Set<NodeRegistryListener>();
function notifyListeners() {
    listeners.forEach((listener) => listener());
}

function validate(definition: NodeDefinition) {
    if (!definition.id || !Number.isInteger(definition.version) || definition.version < 1) throw new Error("node definition requires a stable id and positive integer version");
}

export function createNodeRegistry(): NodeRegistry {
    const nodes = new Map<string, NodeDefinition>();
    return {
        registerNode(definition) {
            validate(definition);
            if (nodes.has(definition.id)) throw new Error(`duplicate node: ${definition.id}`);
            nodes.set(definition.id, Object.freeze({ ...definition, inputs: Object.freeze([...definition.inputs]), outputs: Object.freeze([...definition.outputs]) }));
        },
        listNodes: () => Object.freeze([...nodes.values()]),
        getNode: (id) => nodes.get(id),
    };
}

const registryNodes = new Map<string, NodeDefinition>();
function createProductionNodeRegistry(): NodeRegistry {
    return {
        registerNode(definition) {
            validate(definition);
            if (registryNodes.has(definition.id)) throw new Error(`duplicate node: ${definition.id}`);
            registryNodes.set(definition.id, Object.freeze({ ...definition, inputs: Object.freeze([...definition.inputs]), outputs: Object.freeze([...definition.outputs]) }));
            notifyListeners();
        },
        listNodes: () => Object.freeze([...registryNodes.values()]),
        getNode: (id) => registryNodes.get(id),
    };
}

const registry = createProductionNodeRegistry();
export const registerNode = registry.registerNode;
export const listNodes = registry.listNodes;
export const getNode = registry.getNode;
export const subscribeToNodeRegistry = (listener: NodeRegistryListener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
};
