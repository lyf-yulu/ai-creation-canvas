import { create } from "zustand";

import { fetchSession } from "@/api/session";
import type { PortalSession } from "@/api/contracts";
import { clearStorageScope, setStorageScope } from "@/storage/scope";
import { useAssetStore } from "@/stores/use-asset-store";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";

type PortalSessionStore = {
    session: PortalSession | null;
    environment: string | null;
    loading: boolean;
    loadSession: (environment: string) => Promise<PortalSession>;
    setSession: (session: PortalSession, environment: string) => Promise<void>;
    clearSession: () => void;
};

let sessionVersion = 0;

function clearInMemoryUserState() {
    useCanvasStore.getState().replaceProjects([]);
    useCanvasStore.setState({ hydrated: false });
    useAssetStore.getState().replaceAssets([]);
    useAssetStore.setState({ hydrated: false });
}

export const useSessionStore = create<PortalSessionStore>()((set, get) => ({
    session: null,
    environment: null,
    loading: false,
    loadSession: async (environment) => {
        const version = ++sessionVersion;
        set({ loading: true });
        try {
            const session = await fetchSession();
            await activateSession(version, session, environment, set);
            return session;
        } finally {
            if (version === sessionVersion) set({ loading: false });
        }
    },
    setSession: (session, environment) => activateSession(++sessionVersion, session, environment, set),
    clearSession: () => {
        sessionVersion += 1;
        clearStorageScope();
        clearInMemoryUserState();
        set({ session: null, environment: null, loading: false });
    },
}));

async function activateSession(version: number, session: PortalSession, environment: string, set: (state: Partial<PortalSessionStore>) => void) {
    if (version !== sessionVersion) return;
    clearStorageScope();
    clearInMemoryUserState();
    set({ session: null, environment: null });
    await setStorageScope({ environment, userId: session.user_id });
    if (version !== sessionVersion) return;
    await Promise.all([useCanvasStore.persist.rehydrate(), useAssetStore.persist.rehydrate()]);
    if (version === sessionVersion) set({ session, environment });
}
