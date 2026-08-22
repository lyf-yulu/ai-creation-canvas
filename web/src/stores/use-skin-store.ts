import { create } from "zustand";

import { fetchSession, updateSessionSkin, type SkinColors } from "@/api/session";
import { DEFAULT_SKIN } from "@/lib/skin";

type SkinStore = {
    skin: SkinColors | null;
    presets: Record<string, SkinColors>;
    loaded: boolean;
    load: () => Promise<void>;
    save: (colors: SkinColors) => Promise<void>;
    reset: () => Promise<void>;
};

export const useSkinStore = create<SkinStore>()((set, get) => ({
    skin: null,
    presets: {},
    loaded: false,
    load: async () => {
        try {
            const session = await fetchSession();
            set({ skin: session.skin ?? { ...DEFAULT_SKIN }, presets: session.skin_presets ?? {}, loaded: true });
        } catch {
            // Not signed in (or the endpoint is unavailable): keep the default skin.
            set({ skin: { ...DEFAULT_SKIN }, loaded: true });
        }
    },
    save: async (colors) => {
        const saved = await updateSessionSkin(colors);
        set({ skin: saved.skin });
    },
    reset: async () => {
        await get().save({ ...DEFAULT_SKIN });
    },
}));
