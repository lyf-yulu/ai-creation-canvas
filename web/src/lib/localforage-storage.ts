import type { StateStorage } from "zustand/middleware";
import { currentStorageScopeVersion, isCurrentStorageScopeVersion, scopedStore } from "@/storage/scope";

export const localForageStorage: StateStorage = {
    getItem: async (name) => {
        if (typeof window === "undefined") return null;
        const version = currentStorageScopeVersion();
        const store = scopedStore("app_state");
        if (!store) return null;
        try {
            const value = (await store.getItem<string>(name)) || null;
            return isCurrentStorageScopeVersion(version) ? value : null;
        } catch {
            return null;
        }
    },
    setItem: async (name, value) => {
        if (typeof window === "undefined") return;
        const store = scopedStore("app_state");
        if (!store) return;
        try {
            await store.setItem(name, value);
        } catch { /* Do not fall back to an unscoped browser store. */ }
    },
    removeItem: async (name) => {
        if (typeof window === "undefined") return;
        const store = scopedStore("app_state");
        if (!store) return;
        try {
            await store.removeItem(name);
        } catch { /* Do not fall back to an unscoped browser store. */ }
    },
};
