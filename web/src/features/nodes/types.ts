import type { ReactNode } from "react";
import type { CanvasNodeData, CanvasNodeMetadata } from "@/types/canvas";

export type NodeDefinition = {
    /** Stable persisted identifier; display names are deliberately not identifiers. */
    id: string;
    version: number;
    title: string;
    inputs: readonly string[];
    outputs: readonly string[];
    createMetadata: () => CanvasNodeMetadata;
    /** A renderer is supplied by a local, statically imported module. */
    render: (node: CanvasNodeData) => ReactNode;
    icon?: ReactNode;
    description?: string;
    connectionTitle?: string;
    defaultSize?: { width: number; height: number };
    minimapColor?: string;
    showInCreateMenu?: boolean;
    hasSourceHandle?: boolean;
    hidePanel?: boolean;
    transparentBackground?: boolean;
    keepAspectRatio?: (node: CanvasNodeData) => boolean;
    resource?: (node: CanvasNodeData) => { kind: "text" | "image" | "video" | "audio"; text?: string; url?: string } | null;
};

/** @deprecated Use NodeDefinition. Kept as an alias for existing canvas consumers. */
export type CanvasNodeDefinition = NodeDefinition;
