import type { NodeDefinition } from "./types";

export type NodeRegistry = {
    registerNode: (definition: NodeDefinition) => void;
    ensureNode: (definition: NodeDefinition, owner: string) => boolean;
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

function freezeData<T>(value: T): T {
    if (Array.isArray(value)) return Object.freeze(value.map(freezeData)) as T;
    if (!value || typeof value !== "object" || "$$typeof" in value || Object.getPrototypeOf(value) !== Object.prototype) return value;
    const copy = Object.fromEntries(Object.entries(value).map(([key, item]) => [key, freezeData(item)]));
    return Object.freeze(copy) as T;
}

function storeDefinition(definition: NodeDefinition) {
    return freezeData(definition);
}

function createRegistry(nodes: Map<string, NodeDefinition>, owners: Map<string, string>, onRegister?: () => void): NodeRegistry {
    const registerNode = (definition: NodeDefinition, owner?: string) => {
        validate(definition);
        if (nodes.has(definition.id)) throw new Error(`duplicate node: ${definition.id}`);
        nodes.set(definition.id, storeDefinition(definition));
        if (owner) owners.set(definition.id, owner);
        onRegister?.();
    };
    return {
        registerNode,
        ensureNode(definition, owner) {
            const existing = nodes.get(definition.id);
            if (existing && owners.get(definition.id) === owner && existing.version === definition.version) return false;
            registerNode(definition, owner);
            return true;
        },
        listNodes: () => Object.freeze([...nodes.values()]),
        getNode: (id) => nodes.get(id),
    };
}

export function createNodeRegistry(): NodeRegistry {
    const nodes = new Map<string, NodeDefinition>();
    return createRegistry(nodes, new Map());
}

const registryNodes = new Map<string, NodeDefinition>();
export const nodeRegistry = createRegistry(registryNodes, new Map(), notifyListeners);
export const registerNode = nodeRegistry.registerNode;
export const listNodes = nodeRegistry.listNodes;
export const getNode = nodeRegistry.getNode;
export const subscribeToNodeRegistry = (listener: NodeRegistryListener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
};
