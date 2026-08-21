import { useRef, useState } from "react";

import { importAdminArkKey, type AdminArkKey } from "@/api/admin";
import { ConfigExampleDownload } from "@/components/admin/config-example-download";


type Props = {
    onImport?: (file: File) => Promise<AdminArkKey>;
    onImported?: (summary: AdminArkKey) => void;
};


export function ArkKeyImport({ onImport = importAdminArkKey, onImported }: Props) {
    const inputRef = useRef<HTMLInputElement>(null);
    const [file, setFile] = useState<File | null>(null);
    const [confirmed, setConfirmed] = useState(false);
    const [status, setStatus] = useState<"idle" | "uploading" | "succeeded" | "failed">("idle");
    const locked = status === "uploading";

    const clearSelection = () => {
        setFile(null);
        setConfirmed(false);
        if (inputRef.current) inputRef.current.value = "";
    };

    const submit = async () => {
        if (!file || !confirmed || locked) return;
        setStatus("uploading");
        try {
            const result = await onImport(file);
            onImported?.(result);
            setStatus("succeeded");
        } catch {
            setStatus("failed");
        } finally {
            clearSelection();
        }
    };

    return (
        <section className="mt-6 rounded-xl border border-[var(--c-border)] bg-[var(--c-panel)] p-4">
            <h2 className="text-lg font-semibold">导入方舟生成 Key</h2>
            <p className="mt-1 text-xs text-[var(--c-text-3)]">文件只会原样上传到服务端验证；页面不会读取、展示或保存 Key，导入后新任务立即生效，无需重启。</p>
            <div className="mt-4 flex flex-wrap items-end gap-3">
                <ConfigExampleDownload kind="ark-key" />
                <label className="text-sm text-[var(--c-text-2)]">
                    选择 Key JSON
                    <input
                        ref={inputRef}
                        aria-label="选择 Key JSON"
                        type="file"
                        accept="application/json,.json"
                        disabled={locked}
                        onChange={(event) => {
                            const selected = event.target.files?.[0] || null;
                            setFile(selected);
                            setConfirmed(false);
                            setStatus("idle");
                        }}
                        className="mt-1 block max-w-full text-xs file:mr-3 file:rounded file:border file:border-[var(--c-border)] file:bg-[var(--c-panel-hover)] file:px-3 file:py-2 file:text-[var(--c-accent-soft)]"
                    />
                </label>
                <span className="max-w-xs truncate text-xs text-[var(--c-text-3)]">{file ? `${file.name} · ${file.size} bytes` : "尚未选择文件"}</span>
            </div>
            <label className="mt-3 flex items-start gap-2 text-xs text-[var(--c-text-2)]">
                <input
                    type="checkbox"
                    aria-label="确认替换现有方舟 Key"
                    checked={confirmed}
                    disabled={!file || locked}
                    onChange={(event) => setConfirmed(event.target.checked)}
                    className="mt-0.5 accent-[var(--c-accent)]"
                />
                确认替换现有方舟 Key；新任务使用新 Key，已提交任务不会重放。
            </label>
            <button
                type="button"
                disabled={!file || !confirmed || locked}
                onClick={() => void submit()}
                className="mt-3 rounded bg-[var(--c-accent)] px-4 py-2 text-sm font-semibold text-[var(--c-accent-fg)] disabled:opacity-40"
            >
                {locked ? "正在导入…" : "导入并替换方舟 Key"}
            </button>
            {status === "succeeded" && (
                <p role="status" className="mt-3 text-sm text-[var(--c-accent)]">
                    方舟 Key 已导入，新任务立即生效。
                </p>
            )}
            {status === "failed" && (
                <p role="alert" className="mt-3 text-sm text-[var(--c-warning)]">
                    导入失败：请检查文件格式（{`{"version": 1, "api_key": "…"}`}）后重试。
                </p>
            )}
        </section>
    );
}
