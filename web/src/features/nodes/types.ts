import type { ReactNode } from "react";
import type { CanvasNodeData, CanvasNodeMetadata } from "@/types/canvas";

export type CanvasNodeDefinition = {
    type: string;
    title: string;
    icon: ReactNode;
    description?: string;
    defaultSize: { width: number; height: number };
    defaultMetadata?: CanvasNodeMetadata;
    minimapColor?: string;
    showInCreateMenu?: boolean;
    hasSourceHandle?: boolean;
    hidePanel?: boolean;
    transparentBackground?: boolean;
    keepAspectRatio?: (node: CanvasNodeData) => boolean;
    resource?: (node: CanvasNodeData) => { kind: "text" | "image" | "video" | "audio"; text?: string; url?: string } | null;
};
