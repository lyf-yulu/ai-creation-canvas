import { useRef, useState } from "react";

import { importAdminComfyWorkflow, type AdminComfyWorkflow, type WorkflowImportMetadata } from "@/api/comfy-workflows";
import { ConfigExampleDownload } from "@/components/admin/config-example-download";

type Props = {
    onImport?: (file: File, metadata: WorkflowImportMetadata) => Promise<AdminComfyWorkflow>;
    onImported: (workflow: AdminComfyWorkflow) => void;
};

export function WorkflowImport({ onImport = importAdminComfyWorkflow, onImported }: Props) {
    const inputRef = useRef<HTMLInputElement>(null);
    const [file, setFile] = useState<File | null>(null);
    const [displayName, setDisplayName] = useState("");
    const [serviceId, setServiceId] = useState("");
    const [status, setStatus] = useState<"idle" | "uploading" | "succeeded" | "failed">("idle");
    const [missing, setMissing] = useState<string | null>(null);
    const locked = status === "uploading";

    const clearMissing = () => setMissing(null);

    const submit = async () => {
        if (locked) return;
        const problems: string[] = [];
        if (!file) problems.push("工作流 JSON 文件");
        if (!displayName.trim()) problems.push("工作流显示名");
        if (!serviceId.trim()) problems.push("ComfyUI 服务 ID");
        if (problems.length || !file) {
            setMissing(`请先填写：${problems.join("、")}。`);
            return;
        }
        setMissing(null);
        setStatus("uploading");
        try {
            const imported = await onImport(file, { displayName: displayName.trim(), serviceId: serviceId.trim() });
            setFile(null);
            if (inputRef.current) inputRef.current.value = "";
            setStatus("succeeded");
            onImported(imported);
        } catch {
            setStatus("failed");
        }
    };

    return (
        <section className="rounded-xl border border-[var(--c-border)] bg-[var(--c-panel)] p-4">
            <h2 className="text-lg font-semibold">导入 ComfyUI 工作流</h2>
            <p className="mt-1 text-xs text-[var(--c-text-3)]">文件仅会原样上传到同源服务端验证；页面不会读取、展示、保存或记录 JSON 内容。</p>
            <div className="mt-3">
                <ConfigExampleDownload kind="comfy-workflow" />
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-3">
                <label className="text-sm text-[var(--c-text-2)]">
                    选择工作流 JSON
                    <input
                        ref={inputRef}
                        aria-label="选择工作流 JSON"
                        type="file"
                        accept="application/json,.json"
                        disabled={locked}
                        onChange={(event) => {
                            setFile(event.target.files?.[0] || null);
                            setStatus("idle");
                            clearMissing();
                        }}
                        className="mt-1 block max-w-full text-xs file:mr-3 file:rounded file:border file:border-[var(--c-border)] file:bg-[var(--c-panel-hover)] file:px-3 file:py-2 file:text-[var(--c-accent-soft)]"
                    />
                </label>
                <label className="text-sm text-[var(--c-text-2)]">
                    工作流显示名
                    <input aria-label="工作流显示名" value={displayName} disabled={locked} onChange={(event) => { setDisplayName(event.target.value); clearMissing(); }} placeholder="例如：贝尔尼尼写真工作流" className="mt-1 block w-full rounded border border-[var(--c-border-strong)] bg-[var(--c-panel)] px-3 py-2 text-[var(--c-text)]" />
                </label>
                <label className="text-sm text-[var(--c-text-2)]">
                    ComfyUI 服务 ID
                    <input aria-label="ComfyUI 服务 ID" value={serviceId} disabled={locked} onChange={(event) => { setServiceId(event.target.value); clearMissing(); }} placeholder="服务声明中的 service_id，例如 comfy-local" className="mt-1 block w-full rounded border border-[var(--c-border-strong)] bg-[var(--c-panel)] px-3 py-2 text-[var(--c-text)]" />
                </label>
            </div>
            <p className="mt-2 max-w-xs truncate text-xs text-[var(--c-text-3)]">{file ? `${file.name} · ${file.size} bytes` : "尚未选择文件"}</p>
            <button type="button" disabled={locked} onClick={() => void submit()} className="mt-3 rounded bg-[var(--c-accent)] px-4 py-2 text-sm font-semibold text-[var(--c-accent-fg)] disabled:opacity-40">
                {locked ? "正在导入…" : "导入工作流"}
            </button>
            {missing && (
                <p role="alert" className="mt-3 text-sm text-[var(--c-warning)]">
                    {missing}
                </p>
            )}
            {status === "succeeded" && (
                <p role="status" className="mt-3 text-sm text-[var(--c-accent)]">
                    工作流已导入，当前处于停用状态。
                </p>
            )}
            {status === "failed" && (
                <p role="alert" className="mt-3 text-sm text-[var(--c-warning)]">
                    导入失败，请检查工作流 JSON 和服务配置。
                </p>
            )}
        </section>
    );
}
