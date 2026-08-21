import { useRef, useState } from "react";

import { importAdminCredentialPools, type AdminCredentialPool } from "@/api/admin";
import { ApiRequestError } from "@/api/client";
import { ConfigExampleDownload } from "@/components/admin/config-example-download";


type Props = {
    onImport?: (file: File) => Promise<{ pools: AdminCredentialPool[] }>;
    onImported: (pools: AdminCredentialPool[]) => void;
};


export function CredentialPoolImport({ onImport = importAdminCredentialPools, onImported }: Props) {
    const inputRef = useRef<HTMLInputElement>(null);
    const [file, setFile] = useState<File | null>(null);
    const [confirmed, setConfirmed] = useState(false);
    const [status, setStatus] = useState<"idle" | "uploading" | "succeeded" | "failed">("idle");
    const [detail, setDetail] = useState<string | null>(null);
    const [poolCount, setPoolCount] = useState(0);
    const locked = status === "uploading";

    const clearSelection = () => {
        setFile(null);
        setConfirmed(false);
        if (inputRef.current) inputRef.current.value = "";
    };

    const submit = async () => {
        if (!file || !confirmed || locked) return;
        setStatus("uploading");
        setDetail(null);
        try {
            const result = await onImport(file);
            setPoolCount(result.pools.length);
            onImported(result.pools);
            setStatus("succeeded");
        } catch (error) {
            setStatus("failed");
            setDetail(error instanceof ApiRequestError ? error.message : null);
        } finally {
            clearSelection();
        }
    };

    return (
        <section className="mt-6 rounded-xl border border-[var(--c-border)] bg-[var(--c-panel)] p-4">
            <h2 className="text-lg font-semibold">导入凭据池 JSON</h2>
            <p className="mt-1 text-xs text-[var(--c-text-3)]">文件只会原样上传到服务端验证；页面不会读取、展示或保存供应商凭据。</p>
            <div className="mt-4 flex flex-wrap items-end gap-3">
                <ConfigExampleDownload kind="credential-pools" />
                <label className="text-sm text-[var(--c-text-2)]">
                    选择凭据 JSON
                    <input
                        ref={inputRef}
                        aria-label="选择凭据 JSON"
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
                    aria-label="确认增量合并凭据池"
                    checked={confirmed}
                    disabled={!file || locked}
                    onChange={(event) => setConfirmed(event.target.checked)}
                    className="mt-0.5 accent-[var(--c-accent)]"
                />
                确认增量合并：文件中的凭据池按名称更新或新增，未提到的现有凭据池保持不变；占位符不会覆盖已配置的真实密钥。
            </label>
            <button
                type="button"
                disabled={!file || !confirmed || locked}
                onClick={() => void submit()}
                className="mt-3 rounded bg-[var(--c-accent)] px-4 py-2 text-sm font-semibold text-[var(--c-accent-fg)] disabled:opacity-40"
            >
                {locked ? "正在导入…" : "导入并合并凭据池"}
            </button>
            {status === "succeeded" && <p role="status" className="mt-3 text-sm text-[var(--c-accent)]">已导入 {poolCount} 个凭据池。</p>}
            {status === "failed" && <p role="alert" className="mt-3 text-sm text-[var(--c-warning)]">{detail || "导入失败，请检查 JSON 格式和服务端配置。"}</p>}
        </section>
    );
}
