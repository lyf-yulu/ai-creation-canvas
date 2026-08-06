import localforage from "localforage";

export type StorageScope = { environment: string; userId: string };

let activeScope: StorageScope | null = null;
let scopeVersion = 0;
const instances = new Map<string, LocalForage>();
const clearListeners = new Set<() => void>();

function encodeScopeSegment(value: string) {
    return encodeURIComponent(value);
}

export function storageDatabaseName(scope: StorageScope) {
    return `ai-creation-canvas:${encodeScopeSegment(scope.environment)}:${encodeScopeSegment(scope.userId)}`;
}

export function currentStorageScope() {
    return activeScope;
}

export function currentStorageScopeVersion() {
    return scopeVersion;
}

export function isCurrentStorageScopeVersion(version: number) {
    return activeScope !== null && scopeVersion === version;
}

export async function setStorageScope(scope: StorageScope) {
    if (!scope.environment || !scope.userId) throw new Error("A Portal session and environment are required before opening browser storage");
    clearStorageScope();
    activeScope = { environment: scope.environment, userId: scope.userId };
}

export function clearStorageScope() {
    scopeVersion += 1;
    activeScope = null;
    instances.clear();
    clearListeners.forEach((listener) => listener());
}

export function scopedStore(storeName: string): LocalForage | null {
    if (!activeScope) return null;
    const key = `${storageDatabaseName(activeScope)}:${storeName}`;
    let instance = instances.get(key);
    if (!instance) {
        instance = localforage.createInstance({ name: storageDatabaseName(activeScope), storeName });
        instances.set(key, instance);
    }
    return instance;
}

export function onStorageScopeCleared(listener: () => void) {
    clearListeners.add(listener);
    return () => clearListeners.delete(listener);
}
