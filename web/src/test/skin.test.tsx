import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchSession, updateSessionSkin, type SkinColors } from "@/api/session";
import { SkinPicker } from "@/components/layout/skin-picker";
import { applySkinToDocument, DEFAULT_SKIN, SKIN_TOKENS } from "@/lib/skin";
import { useSkinStore } from "@/stores/use-skin-store";

vi.mock("@/api/session", () => ({
    fetchSession: vi.fn(),
    updateSessionSkin: vi.fn(),
}));

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    for (const token of SKIN_TOKENS) document.documentElement.style.removeProperty(token.cssVar);
    document.documentElement.style.removeProperty("--c-accent-rgb");
    useSkinStore.setState({ skin: null, presets: {}, loaded: false });
});

describe("skin system", () => {
    it("applies skin colors as CSS overrides in dark mode and clears them in light mode", () => {
        const custom: SkinColors = { ...DEFAULT_SKIN, accent: "#ff0000", bg: "#101010" };
        applySkinToDocument(custom, true);
        expect(document.documentElement.style.getPropertyValue("--c-accent")).toBe("#ff0000");
        expect(document.documentElement.style.getPropertyValue("--c-bg")).toBe("#101010");
        expect(document.documentElement.style.getPropertyValue("--c-accent-rgb")).toBe("255, 0, 0");
        applySkinToDocument(custom, false);
        expect(document.documentElement.style.getPropertyValue("--c-accent")).toBe("");
    });

    it("loads the session skin and saves updates through the api", async () => {
        vi.mocked(fetchSession).mockResolvedValue({
            user_id: "u-a", username: "Alice", role: "user",
            skin: { ...DEFAULT_SKIN, accent: "#00ff00" },
            skin_presets: { default: DEFAULT_SKIN, monochrome: { ...DEFAULT_SKIN, accent: "#f4f4f5" } },
        } as never);
        await useSkinStore.getState().load();
        expect(useSkinStore.getState().skin?.accent).toBe("#00ff00");
        expect(useSkinStore.getState().presets.monochrome.accent).toBe("#f4f4f5");

        vi.mocked(updateSessionSkin).mockResolvedValue({ skin: { ...DEFAULT_SKIN, accent: "#123456" } });
        await useSkinStore.getState().save({ ...DEFAULT_SKIN, accent: "#123456" });
        expect(useSkinStore.getState().skin?.accent).toBe("#123456");
    });

    it("renders presets and custom color inputs", async () => {
        vi.stubGlobal(
            "ResizeObserver",
            class {
                observe = vi.fn();
                unobserve = vi.fn();
                disconnect = vi.fn();
            },
        );
        vi.mocked(fetchSession).mockResolvedValue({
            user_id: "u-a", username: "Alice", role: "user",
            skin: DEFAULT_SKIN,
            skin_presets: { default: DEFAULT_SKIN, monochrome: { ...DEFAULT_SKIN, bg: "#0c0c0d" }, "classic-green": { ...DEFAULT_SKIN, accent: "#58ed87" } },
        } as never);
        await useSkinStore.getState().load();
        render(<SkinPicker />);
        fireEvent.click(screen.getByLabelText("皮肤设置"));
        await waitFor(() => expect(screen.getByText("预设皮肤")).toBeInTheDocument());
        expect(screen.getByText("蓝灰")).toBeInTheDocument();
        expect(screen.getByText("黑白灰")).toBeInTheDocument();
        expect(screen.getByText("绿黑经典")).toBeInTheDocument();
        expect(screen.getByLabelText("皮肤颜色 强调色")).toBeInTheDocument();
    });
});
