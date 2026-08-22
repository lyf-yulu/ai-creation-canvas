export type CanvasColorTheme = "light" | "dark";
export type CanvasBackgroundMode = "dots" | "lines" | "blank";

export const canvasThemes = {
    light: {
        canvas: {
            background: "#ececee",
            dot: "rgba(113,113,122,.30)",
            line: "rgba(113,113,122,.14)",
            selectionStroke: "#18181b",
            selectionFill: "rgba(24,24,27,.08)",
        },
        node: {
            label: "#3f3f46",
            fill: "#ffffff",
            panel: "#ffffff",
            stroke: "#e4e4e7",
            activeStroke: "#18181b",
            placeholder: "#a1a1aa",
            text: "#18181b",
            muted: "#71717a",
            faint: "#a1a1aa",
        },
        toolbar: {
            panel: "rgba(255,255,255,.96)",
            border: "#e4e4e7",
            item: "#3f3f46",
            itemHover: "#f4f4f5",
            activeBg: "#ececee",
            activeText: "#18181b",
        },
    },
    dark: {
        canvas: {
            background: "var(--c-bg)",
            dot: "rgba(139,139,148,.26)",
            line: "rgba(139,139,148,.12)",
            selectionStroke: "var(--c-accent)",
            selectionFill: "rgba(var(--c-accent-rgb),.10)",
        },
        node: {
            label: "var(--c-text-3)",
            fill: "var(--c-panel)",
            panel: "var(--c-panel)",
            stroke: "var(--c-border)",
            activeStroke: "var(--c-accent)",
            placeholder: "var(--c-text-3)",
            text: "var(--c-text)",
            muted: "var(--c-text-3)",
            faint: "var(--c-text-3)",
        },
        toolbar: {
            panel: "rgba(35,35,39,.96)",
            border: "var(--c-border)",
            item: "var(--c-text-2)",
            itemHover: "var(--c-panel-hover)",
            activeBg: "var(--c-panel-hover)",
            activeText: "var(--c-accent-soft)",
        },
    },
} as const;

export type CanvasTheme = (typeof canvasThemes)[CanvasColorTheme];
