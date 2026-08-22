import type { SkinColors } from "@/api/session";

/** Editable skin tokens and the CSS variables they drive. */
export const SKIN_TOKENS: ReadonlyArray<{ key: string; label: string; cssVar: string }> = [
    { key: "bg", label: "背景", cssVar: "--c-bg" },
    { key: "canvas", label: "画布", cssVar: "--c-canvas" },
    { key: "panel", label: "面板", cssVar: "--c-panel" },
    { key: "panel_hover", label: "面板悬停", cssVar: "--c-panel-hover" },
    { key: "border", label: "边框", cssVar: "--c-border" },
    { key: "border_strong", label: "强边框", cssVar: "--c-border-strong" },
    { key: "text", label: "主文字", cssVar: "--c-text" },
    { key: "text_2", label: "次文字", cssVar: "--c-text-2" },
    { key: "text_3", label: "弱文字", cssVar: "--c-text-3" },
    { key: "accent", label: "强调色", cssVar: "--c-accent" },
    { key: "accent_soft", label: "强调色(浅)", cssVar: "--c-accent-soft" },
    { key: "accent_fg", label: "强调色上的文字", cssVar: "--c-accent-fg" },
];

export const DEFAULT_SKIN: SkinColors = {
    bg: "#1a1a1e",
    canvas: "#1a1a1e",
    panel: "#232327",
    panel_hover: "#2e2e33",
    border: "#333338",
    border_strong: "#46464d",
    text: "#e4e4e7",
    text_2: "#c4c4cc",
    text_3: "#8b8b94",
    accent: "#3b82f6",
    accent_soft: "#60a5fa",
    accent_fg: "#ffffff",
};

function hexToRgb(hex: string): string | null {
    const match = /^#([0-9a-fA-F]{6})$/.exec(hex);
    if (!match) return null;
    const value = Number.parseInt(match[1], 16);
    return `${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}`;
}

/** Apply (or clear) the skin as inline overrides; light mode keeps its fixed palette. */
export function applySkinToDocument(colors: SkinColors, dark: boolean) {
    for (const token of SKIN_TOKENS) {
        const value = colors[token.key];
        if (dark && typeof value === "string") {
            document.documentElement.style.setProperty(token.cssVar, value);
        } else {
            document.documentElement.style.removeProperty(token.cssVar);
        }
    }
    if (dark && typeof colors.accent === "string") {
        const rgb = hexToRgb(colors.accent);
        if (rgb) document.documentElement.style.setProperty("--c-accent-rgb", rgb);
    } else {
        document.documentElement.style.removeProperty("--c-accent-rgb");
    }
}
