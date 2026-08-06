import type { NodeDefinition } from "./types";

export type NodeRegistry = {
    registerNode: (definition: NodeDefinition) => void;
    listNodes: () => readonly NodeDefinition[];
    getNode: (id: string) => NodeDefinition | undefined;
    subscribe: (listener: () => void) => () => void;
};

type BootstrapState = {
    owners: Map<string, string>;
    fingerprints: Map<string, string>;
    registerBuiltin: (definition: NodeDefinition, owner: string, fingerprint: string) => void;
};
const bootstrapStates = new WeakMap<NodeRegistry, BootstrapState>();

function validate(definition: NodeDefinition) {
    if (!definition.id || !Number.isInteger(definition.version) || definition.version < 1) throw new Error("node definition requires a stable id and positive integer version");
}

function freezeData<T>(value: T): T {
    if (Array.isArray(value)) return Object.freeze(value.map(freezeData)) as T;
    if (!value || typeof value !== "object" || "$$typeof" in value || Object.getPrototypeOf(value) !== Object.prototype) return value;
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

function createRegistry(): NodeRegistry {
    const nodes = new Map<string, NodeDefinition>();
    const owners = new Map<string, string>();
    const fingerprints = new Map<string, string>();
    const listeners = new Set<() => void>();
    const store = (definition: NodeDefinition, owner?: string, definitionFingerprint?: string) => {
        validate(definition);
        if (nodes.has(definition.id)) throw new Error(`duplicate node: ${definition.id}`);
        nodes.set(definition.id, freezeData(definition));
        if (owner && definitionFingerprint) {
            owners.set(definition.id, owner);
            fingerprints.set(definition.id, definitionFingerprint);
        }
        listeners.forEach((listener) => listener());
    };
    const registry: NodeRegistry = {
        registerNode: (definition) => store(definition),
        listNodes: () => Object.freeze([...nodes.values()]),
        getNode: (id) => nodes.get(id),
        subscribe(listener) {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
    };
    bootstrapStates.set(registry, { owners, fingerprints, registerBuiltin: (definition, owner, definitionFingerprint) => store(definition, owner, definitionFingerprint) });
    return registry;
}

/** Narrow built-in-only bootstrap path; ordinary extensions use registerNode. */
export function bootstrapBuiltinNode(registry: NodeRegistry, definition: NodeDefinition, owner: string): boolean {
    const state = bootstrapStates.get(registry);
    if (!state) throw new Error("unknown node registry");
    const definitionFingerprint = fingerprint(definition);
    if (registry.getNode(definition.id)) {
        if (state.owners.get(definition.id) === owner) {
            if (state.fingerprints.get(definition.id) === definitionFingerprint) return false;
            throw new Error(`builtin definition drift: ${definition.id}; bump version before bootstrapping`);
        }
        registry.registerNode(definition);
    }
    state.registerBuiltin(definition, owner, definitionFingerprint);
    return true;
}

export function createNodeRegistry(): NodeRegistry {
    return createRegistry();
}

export const nodeRegistry = createRegistry();
export const registerNode = nodeRegistry.registerNode;
export const listNodes = nodeRegistry.listNodes;
export const getNode = nodeRegistry.getNode;
export const subscribeToNodeRegistry = nodeRegistry.subscribe;
