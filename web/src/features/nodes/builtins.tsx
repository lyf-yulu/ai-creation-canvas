import { FileText, Group, Image as ImageIcon, Music2, Settings2, Video } from "lucide-react";

import { NODE_SPECS } from "@/constant/canvas";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";
import { bootstrapBuiltinNode, nodeRegistry, type NodeRegistry } from "./registry";
import type { NodeDefinition } from "./types";

const iconClass = "size-5";
const BuiltinNodeRenderer = () => null;

function builtinResource(node: CanvasNodeData): ReturnType<NonNullable<NodeDefinition["resource"]>> {
    if (node.type === CanvasNodeType.Image && node.metadata?.content) return { kind: "image", url: node.metadata.content };
    if (node.type === CanvasNodeType.Video && node.metadata?.content) return { kind: "video", url: node.metadata.content };
    if (node.type === CanvasNodeType.Audio && node.metadata?.content) return { kind: "audio", url: node.metadata.content };
    if (node.type === CanvasNodeType.Text && (node.metadata?.content || node.metadata?.prompt)) return { kind: "text", text: node.metadata.content || node.metadata.prompt };
    return null;
}

const details = [
    [CanvasNodeType.Text, "文本生成", "脚本、广告词、品牌文案", <FileText className={iconClass} />],
    [CanvasNodeType.Image, "图片生成", undefined, <ImageIcon className={iconClass} />],
    [CanvasNodeType.Video, "视频生成", undefined, <Video className={iconClass} />],
    [CanvasNodeType.Audio, "音频参考", undefined, <Music2 className={iconClass} />],
    [CanvasNodeType.Config, "配置节点", "模型、尺寸、数量和输入顺序", <Settings2 className={iconClass} />],
    [CanvasNodeType.Group, "组", undefined, <Group className={iconClass} />],
] as const;

export const builtinNodes: readonly NodeDefinition[] = details.map(([id, connectionTitle, description, icon]) => {
    const spec = NODE_SPECS[id];
    return {
        id,
        version: 1,
        title: spec.title,
        inputs: [],
        outputs: [],
        createMetadata: () => ({ ...spec.metadata }),
        render: BuiltinNodeRenderer,
        icon,
        description,
        connectionTitle,
        defaultSize: { width: spec.width, height: spec.height },
        minimapColor: id === CanvasNodeType.Image ? "#10b981" : id === CanvasNodeType.Video ? "#f97316" : id === CanvasNodeType.Audio ? "#a855f7" : id === CanvasNodeType.Config ? "#60a5fa" : id === CanvasNodeType.Group ? "#94a3b8" : undefined,
        hasSourceHandle: id === CanvasNodeType.Config ? false : undefined,
        keepAspectRatio: id === CanvasNodeType.Image ? (node) => !node.metadata?.freeResize : id === CanvasNodeType.Video ? () => true : undefined,
        resource: builtinResource,
    };
});

const BUILTIN_OWNER = "ai-creation-canvas.nodes.builtins";
export function registerBuiltinNodes(registry: NodeRegistry = nodeRegistry) {
    builtinNodes.forEach((definition) => bootstrapBuiltinNode(registry, definition, BUILTIN_OWNER));
}
