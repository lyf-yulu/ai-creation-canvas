import { Play, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";

import type { ModelOperation, ModelSpec } from "@/api/contracts";
import { parameterControls } from "@/components/model-picker";
import type { GraphModelMetadata, GraphParameterValue } from "@/features/graph/contracts";
import { declaredModelPorts, graphPortsForModel } from "@/features/graph/model-capabilities";
import type { CanvasNodeData } from "@/types/canvas";

type Props = {
    node: CanvasNodeData;
    models: readonly ModelSpec[];
    disabled?: boolean;
    message?: string;
    onChange: (graph: GraphModelMetadata) => void;
    onRun: () => void;
    onRetry?: (token: string) => void;
    onCancel?: (jobId: string) => void;
};

function defaults(model: ModelSpec) {
    return Object.fromEntries(parameterControls(model.parameter_schema).flatMap((control) => (control.default === undefined ? [] : [[control.name, control.default]]))) as Record<string, GraphParameterValue>;
}

const OPERATION_LABELS: Record<string, string> = {
    "image.generate": "图片生成",
    "image.edit": "图片编辑",
    "video.generate": "视频生成",
    "video.image_to_video": "图生视频",
};

export function ModelCallNode({ node, models, disabled = false, message, onChange, onRun, onRetry, onCancel }: Props) {
    const graph = node.metadata?.graph;
    if (graph?.role !== "model") return null;
    const [customOpen, setCustomOpen] = useState<Record<string, boolean>>({});
    const operations = useMemo(() => [...new Set(models.flatMap((model) => model.operations))], [models]);
    const operation = operations.includes(graph.operation as ModelOperation) ? (graph.operation as ModelOperation) : operations[0];
    const operationModels = useMemo(() => models.filter((model) => operation !== undefined && model.operations.includes(operation)), [models, operation]);
    const selected = operationModels.find((model) => model.model_id === graph.modelId) ?? operationModels[0];
    const controls = useMemo(() => parameterControls(selected?.parameter_schema ?? {}), [selected]);
    const visibleControls = controls.filter((control) => !control.visibleWhen || Object.is(graph.parameters[control.visibleWhen.name], control.visibleWhen.equals));
    const busy = node.metadata?.status === "loading" || node.metadata?.jobStatus === "queued" || node.metadata?.jobStatus === "running";
    const editDisabled = disabled || busy;
    if (!selected) return <article className="rounded-xl border border-[#6b4b2c] bg-[#171008] p-3 text-xs text-[#ffbd73]">暂无可用模型。</article>;
    const updateParameter = (name: string, value: GraphParameterValue) => onChange({ ...graph, parameters: { ...graph.parameters, [name]: value } });
    const choose = (modelId: string) => {
        const next = operationModels.find((model) => model.model_id === modelId);
        if (!next) return;
        onChange({ ...graph, modelId, operation: next.operations[0], inputPorts: graphPortsForModel(next), parameters: defaults(next) });
    };
    const switchOperation = (nextOperation: string) => {
        if (nextOperation === operation) return;
        const next = models.find((model) => model.operations.includes(nextOperation as ModelOperation));
        if (!next) return;
        onChange({ ...graph, operation: nextOperation, modelId: next.model_id, inputPorts: graphPortsForModel(next), parameters: defaults(next) });
    };
    return (
        <article className="flex h-full max-w-full flex-col overflow-hidden rounded-xl border border-[#285038] bg-[#0a140e] text-xs text-[#dceee1] shadow-xl">
            <header className="flex shrink-0 items-center gap-2 border-b border-[#1c3826] px-3 py-2">
                <Sparkles className="size-4 text-[#58ed87]" />
                <strong>{node.title}</strong>
            </header>
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3" data-canvas-no-zoom>
                <p role="status" className="text-[11px] text-[#9fb5a5]">
                    任务状态：{node.metadata?.jobStatus === "queued" ? "排队中，可取消" : node.metadata?.jobStatus === "running" ? "运行中（平台不支持取消运行中任务）" : node.metadata?.status === "loading" ? "提交中" : node.metadata?.status === "success" ? "已完成" : node.metadata?.status === "error" ? "失败，可修改后重试" : "待运行"}
                </p>
                {operations.length > 1 ? (
                    <label className="block text-[11px] text-[#9fb5a5]">
                        模式
                        <select aria-label="模式" disabled={editDisabled} value={operation} onChange={(event) => switchOperation(event.target.value)} className="mt-1 block w-full rounded-md border border-[#285038] bg-[#050806] p-2 text-[#dceee1]">
                            {operations.map((item) => (
                                <option key={item} value={item}>
                                    {OPERATION_LABELS[item] ?? item}
                                </option>
                            ))}
                        </select>
                    </label>
                ) : null}
                <label className="block text-[11px] text-[#9fb5a5]">
                    模型
                    <select aria-label="模型" disabled={editDisabled} value={selected.model_id} onChange={(event) => choose(event.target.value)} className="mt-1 block w-full rounded-md border border-[#285038] bg-[#050806] p-2 text-[#dceee1]">
                        {operationModels.map((model) => (
                            <option key={model.model_id} value={model.model_id}>
                                {model.display_name}
                            </option>
                        ))}
                    </select>
                </label>
                <div className="flex flex-wrap gap-1 text-[10px] text-[#8fa596]">
                    {declaredModelPorts(selected).map((port) => (
                        <span key={port.port_id} className="rounded border border-[#264532] px-1.5 py-1">
                            {port.port_id === "prompt" ? "提示词" : port.port_id}：{port.max_items}
                        </span>
                    ))}
                </div>
                {visibleControls.map((control) => (
                    <label key={control.name} className="block text-[11px] text-[#9fb5a5]">
                        {control.title ?? control.name}
                        {control.type === "enum" ? (
                            (() => {
                                const effective = graph.parameters[control.name] ?? control.default;
                                const index = control.enum?.findIndex((value) => Object.is(value, effective)) ?? -1;
                                return (
                                    <select
                                        aria-label={control.title ?? control.name}
                                        disabled={editDisabled}
                                        value={String(index >= 0 ? index : 0)}
                                        onChange={(event) => updateParameter(control.name, control.enum?.[Number(event.target.value)] ?? null)}
                                        className="mt-1 block w-full rounded-md border border-[#285038] bg-[#050806] p-2"
                                    >
                                        {control.enum?.map((value, optionIndex) => (
                                            <option key={String(value)} value={optionIndex}>
                                                {String(value)}
                                            </option>
                                        ))}
                                    </select>
                                );
                            })()
                        ) : control.type === "preset" ? (
                            (() => {
                                const presets = control.presets ?? [];
                                const raw = graph.parameters[control.name];
                                const value = typeof raw === "string" ? raw : typeof control.default === "string" ? control.default : "";
                                const custom = customOpen[control.name] === true || (typeof raw === "string" && !presets.includes(raw));
                                const index = !custom && presets.includes(value) ? presets.indexOf(value) : presets.length;
                                return (
                                    <div>
                                        <select
                                            aria-label={control.title ?? control.name}
                                            disabled={editDisabled}
                                            value={String(index)}
                                            onChange={(event) => {
                                                const next = Number(event.target.value);
                                                if (next === presets.length) {
                                                    setCustomOpen((previous) => ({ ...previous, [control.name]: true }));
                                                    return;
                                                }
                                                setCustomOpen((previous) => ({ ...previous, [control.name]: false }));
                                                updateParameter(control.name, presets[next]);
                                            }}
                                            className="mt-1 block w-full rounded-md border border-[#285038] bg-[#050806] p-2"
                                        >
                                            {presets.map((preset) => (
                                                <option key={preset} value={presets.indexOf(preset)}>
                                                    {preset}
                                                </option>
                                            ))}
                                            <option value={presets.length}>自定义（宽x高）</option>
                                        </select>
                                        {custom ? (
                                            <input
                                                aria-label={`${control.title ?? control.name}（自定义宽x高）`}
                                                disabled={editDisabled}
                                                type="text"
                                                placeholder="如 2048x1024"
                                                value={typeof raw === "string" ? raw : ""}
                                                onChange={(event) => updateParameter(control.name, event.target.value)}
                                                className="mt-1 block w-full rounded-md border border-[#285038] bg-[#050806] p-2"
                                            />
                                        ) : null}
                                    </div>
                                );
                            })()
                        ) : control.type === "boolean" ? (
                            <input
                                aria-label={control.title ?? control.name}
                                disabled={editDisabled}
                                type="checkbox"
                                checked={graph.parameters[control.name] === true}
                                onChange={(event) => updateParameter(control.name, event.target.checked)}
                                className="ml-2 accent-[#58ed87]"
                            />
                        ) : (
                            <input
                                aria-label={control.title ?? control.name}
                                disabled={editDisabled}
                                type={control.type === "number" || control.type === "integer" ? "number" : "text"}
                                min={control.minimum}
                                max={control.maximum}
                                step={control.type === "integer" ? 1 : undefined}
                                value={String(graph.parameters[control.name] ?? "")}
                                onChange={(event) => updateParameter(control.name, control.type === "number" || control.type === "integer" ? event.target.value === "" ? null : Number(event.target.value) : event.target.value)}
                                className="mt-1 block w-full rounded-md border border-[#285038] bg-[#050806] p-2"
                            />
                        )}
                        {control.description ? <span className="mt-1 block text-[10px] leading-4 text-[#789080]">{control.description}</span> : null}
                    </label>
                ))}
            </div>
            <footer className="shrink-0 space-y-2 border-t border-[#1c3826] p-3">
                <button
                    type="button"
                    disabled={editDisabled}
                    onClick={node.metadata?.status === "error" && node.metadata.idempotencyKey && onRetry ? () => onRetry(node.metadata!.idempotencyKey!) : onRun}
                    className="flex w-full items-center justify-center gap-2 rounded-lg bg-[#47d978] px-3 py-2 font-semibold text-[#041008] disabled:opacity-40"
                >
                    <Play className="size-3.5" />
                    {node.metadata?.status === "error" && node.metadata.idempotencyKey ? "使用原任务键重试" : "运行模型"}
                </button>
                {node.metadata?.jobStatus === "queued" && node.metadata.jobId && onCancel ? (
                    <button type="button" disabled={disabled} onClick={() => onCancel(node.metadata!.jobId!)} className="w-full rounded-lg border border-[#6b4b2c] px-3 py-2 text-[#ffbd73] disabled:opacity-40">
                        取消排队任务
                    </button>
                ) : null}
                {message ? (
                    <p role="status" className="text-[#ffbd73]">
                        {message}
                    </p>
                ) : null}
            </footer>
        </article>
    );
}
