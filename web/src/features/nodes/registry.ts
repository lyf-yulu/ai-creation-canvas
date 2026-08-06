import { builtinNodeDefinitions } from "./builtins";
import type { NodeDefinition } from "./types";

export type NodeRegistry = {
    registerNode: (definition: NodeDefinition) => void;
    listNodes: () => readonly NodeDefinition[];
    getNode: (id: string) => NodeDefinition | undefined;
    subscribe: (listener: () => void) => () => void;
};

function validate(definition: NodeDefinition) {
    if (!definition.id || !Number.isInteger(definition.version) || definition.version < 1) throw new Error("node definition requires a stable id and positive integer version");
}

function freezeData<T>(value: T): T {
    if (Array.isArray(value)) return Object.freeze(value.map(freezeData)) as T;
    if (!value || typeof value !== "object" || "$$typeof" in value || Object.getPrototypeOf(value) !== Object.prototype) return value;
    return Object.freeze(Object.fromEntries(Object.entries(value).map(([key, item]) => [key, freezeData(item)]))) as T;
}

export function createNodeRegistry(): NodeRegistry {
    const nodes = new Map<string, NodeDefinition>();
    const listeners = new Set<() => void>();
    return {
        registerNode(definition) {
            validate(definition);
            if (nodes.has(definition.id)) throw new Error(`duplicate node: ${definition.id}`);
            nodes.set(definition.id, freezeData(definition));
            listeners.forEach((listener) => listener());
        },
        listNodes: () => Object.freeze([...nodes.values()]),
        getNode: (id) => nodes.get(id),
        subscribe(listener) {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
    };
}

export const nodeRegistry = createNodeRegistry();
builtinNodeDefinitions.forEach(nodeRegistry.registerNode);
export const registerNode = nodeRegistry.registerNode;
export const listNodes = nodeRegistry.listNodes;
export const getNode = nodeRegistry.getNode;
export const subscribeToNodeRegistry = nodeRegistry.subscribe;
