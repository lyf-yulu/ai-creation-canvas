export type CanvasColorTheme = "light" | "dark";
export type CanvasBackgroundMode = "dots" | "lines" | "blank";

export const canvasThemes = {
    light: {
        canvas: {
            background: "#eef1f6",
            dot: "rgba(100,116,139,.30)",
            line: "rgba(100,116,139,.14)",
            selectionStroke: "#2563eb",
            selectionFill: "rgba(37,99,235,.08)",
        },
        node: {
            label: "#475569",
            fill: "#ffffff",
            panel: "#ffffff",
            stroke: "#dbe2ec",
            activeStroke: "#2563eb",
            placeholder: "#94a3b8",
            text: "#1e293b",
            muted: "#64748b",
            faint: "#94a3b8",
        },
        toolbar: {
            panel: "rgba(255,255,255,.96)",
            border: "#dbe2ec",
            item: "#475569",
            itemHover: "#eef2f8",
            activeBg: "#e3ecfb",
            activeText: "#1d4ed8",
        },
    },
    dark: {
        canvas: {
            background: "var(--c-bg)",
            dot: "rgba(134,169,145,.28)",
            line: "rgba(134,169,145,.12)",
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
            panel: "rgba(8,16,11,.96)",
            border: "var(--c-border)",
            item: "var(--c-text-2)",
            itemHover: "var(--c-panel-hover)",
            activeBg: "var(--c-panel-hover)",
            activeText: "var(--c-accent-soft)",
        },
    },
} as const;

export type CanvasTheme = (typeof canvasThemes)[CanvasColorTheme];
