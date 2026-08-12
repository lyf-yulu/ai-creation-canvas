import { useEffect, useMemo, useState } from "react";

import { createAdminModel, createAdminProvider, fetchAdminModelRegistry, fetchAdminModels, fetchAdminUsers, replaceAdminUserModels, type AdminModelRegistry, type AdminUser } from "@/api/admin";
import type { ModelSpec } from "@/api/contracts";


export default function AdminModelsPage() {
    const [users, setUsers] = useState<AdminUser[]>([]);
    const [models, setModels] = useState<ModelSpec[]>([]);
    const [userId, setUserId] = useState("");
    const [selected, setSelected] = useState<string[]>([]);
    const [status, setStatus] = useState<"loading" | "ready" | "saving" | "saved" | "failed">("loading");
    const [registry, setRegistry] = useState<AdminModelRegistry>({ providers: [], models: [], templates: [] });
    const [providerStatus, setProviderStatus] = useState<"idle" | "saving" | "saved" | "failed">("idle");
    const [modelStatus, setModelStatus] = useState<"idle" | "saving" | "saved" | "failed">("idle");
    const [providerForm, setProviderForm] = useState({ provider_id: "", display_name: "", adapter_type: "chiyun_openai_images", base_url: "", credential_ref: "", enabled: true });
    const [modelForm, setModelForm] = useState({ model_id: "", provider_id: "", provider_model_name: "", display_name: "", introduction: "", template_id: "chiyun_gpt_image_edit_v1", enabled: true });
    const user = useMemo(() => users.find((item) => item.user_id === userId), [userId, users]);

    useEffect(() => {
        void Promise.all([fetchAdminUsers(), fetchAdminModels(), fetchAdminModelRegistry()]).then(([nextUsers, nextModels, nextRegistry]) => {
            setUsers(nextUsers);
            setModels(nextModels);
            setRegistry(nextRegistry);
            if (nextRegistry.providers[0]) setModelForm((current) => ({ ...current, provider_id: nextRegistry.providers[0].provider_id }));
            setStatus("ready");
        }).catch(() => setStatus("failed"));
    }, []);

    useEffect(() => { setSelected(user?.model_ids || []); setStatus((current) => current === "loading" ? current : "ready"); }, [userId]);

    const toggle = (modelId: string) => setSelected((current) => current.includes(modelId) ? current.filter((id) => id !== modelId) : [...current, modelId]);
    const save = async () => {
        if (!userId) return;
        setStatus("saving");
        try {
            const response = await replaceAdminUserModels(userId, selected);
            setUsers((current) => current.map((item) => item.user_id === userId ? { ...item, model_ids: response.model_ids } : item));
            setStatus("saved");
        } catch {
            setStatus("failed");
        }
    };

    const saveProvider = async () => {
        setProviderStatus("saving");
        try {
            const saved = await createAdminProvider(providerForm);
            setRegistry((current) => ({ ...current, providers: [...current.providers, saved] }));
            setModelForm((current) => ({ ...current, provider_id: current.provider_id || saved.provider_id }));
            setProviderStatus("saved");
        } catch { setProviderStatus("failed"); }
    };
    const saveModel = async () => {
        setModelStatus("saving");
        try {
            const saved = await createAdminModel(modelForm);
            setRegistry((current) => ({ ...current, models: [...current.models, saved] }));
            setModels((current) => [...current, { model_id: saved.model_id, service_id: saved.provider_id, display_name: saved.display_name, operations: saved.operations as ModelSpec["operations"], input_media: [], parameter_schema: {} }]);
            setModelStatus("saved");
        } catch { setModelStatus("failed"); }
    };

    return <section className="mx-auto max-w-6xl px-5 py-8">
        <p className="text-xs tracking-[0.2em] text-[#58ed87]">ADMIN · MODELS</p>
        <h1 className="mt-2 text-3xl font-semibold">模型派发</h1>
        <p className="mt-2 text-sm text-[#829889]">用户只会看到这里分配的模型；服务密钥由管理员在服务端部署时配置。</p>
        <label className="mt-7 block max-w-md text-sm text-[#b9d0c0]">选择账号<select aria-label="选择账号" value={userId} onChange={(event) => setUserId(event.target.value)} className="mt-2 block w-full rounded-lg border border-[#285038] bg-[#08100b] px-3 py-2 text-[#e5f5e9]"><option value="">请选择账号</option>{users.map((item) => <option key={item.user_id} value={item.user_id}>{item.display_name} · {item.username}</option>)}</select></label>
        <div className="mt-6 grid gap-3 md:grid-cols-2">{models.map((model) => <label key={model.model_id} className="flex cursor-pointer items-start gap-3 rounded-xl border border-[#1f3f2a] bg-[#09120c] p-4"><input type="checkbox" aria-label={model.display_name} disabled={!userId} checked={selected.includes(model.model_id)} onChange={() => toggle(model.model_id)} className="mt-1 accent-[#58ed87]" /><span><span className="block text-sm text-[#e4f5e9]">{model.display_name}</span><span className="mt-1 block text-xs text-[#688371]">{model.service_id} · {model.operations.join(" / ")}</span></span></label>)}</div>
        <div className="mt-6 flex items-center gap-4"><button type="button" disabled={!userId || status === "saving"} onClick={() => void save()} className="rounded-lg bg-[#47d978] px-4 py-2 text-sm font-medium text-[#041008] disabled:opacity-40">保存派发</button>{status === "saved" && <span className="text-sm text-[#58d881]">派发已保存</span>}{status === "failed" && <span role="alert" className="text-sm text-[#ffbd73]">操作未完成，请重试。</span>}</div>
        <div className="mt-10 grid gap-6 lg:grid-cols-2">
            <section className="rounded-xl border border-[#1f3f2a] bg-[#09120c] p-5">
                <h2 className="text-lg font-medium text-[#e4f5e9]">创建 Provider</h2>
                <p className="mt-1 text-xs text-[#688371]">这里只绑定服务地址和部署凭据引用，不读取或显示真实 API Key。</p>
                <div className="mt-4 grid gap-3">
                    <label className="text-sm">Provider ID<input aria-label="Provider ID" value={providerForm.provider_id} onChange={(event) => setProviderForm({ ...providerForm, provider_id: event.target.value })} className="mt-1 block w-full rounded border border-[#285038] bg-[#08100b] px-3 py-2" /></label>
                    <label className="text-sm">Provider 名称<input aria-label="Provider 名称" value={providerForm.display_name} onChange={(event) => setProviderForm({ ...providerForm, display_name: event.target.value })} className="mt-1 block w-full rounded border border-[#285038] bg-[#08100b] px-3 py-2" /></label>
                    <label className="text-sm">适配器<select aria-label="适配器" value={providerForm.adapter_type} onChange={(event) => setProviderForm({ ...providerForm, adapter_type: event.target.value })} className="mt-1 block w-full rounded border border-[#285038] bg-[#08100b] px-3 py-2"><option value="chiyun_openai_images">Chiyun OpenAI Images</option></select></label>
                    <label className="text-sm">Base URL<input aria-label="Base URL" value={providerForm.base_url} onChange={(event) => setProviderForm({ ...providerForm, base_url: event.target.value })} placeholder="https://example.com" className="mt-1 block w-full rounded border border-[#285038] bg-[#08100b] px-3 py-2" /></label>
                    <label className="text-sm">凭据引用<input aria-label="凭据引用" value={providerForm.credential_ref} onChange={(event) => setProviderForm({ ...providerForm, credential_ref: event.target.value })} placeholder="chiyun-primary" className="mt-1 block w-full rounded border border-[#285038] bg-[#08100b] px-3 py-2" /></label>
                    <button type="button" disabled={providerStatus === "saving"} onClick={() => void saveProvider()} className="rounded bg-[#183f26] px-4 py-2 text-sm text-[#8ff0aa] disabled:opacity-40">创建 Provider</button>
                    {providerStatus === "saved" && <span className="text-sm text-[#58d881]">Provider 已创建</span>}{providerStatus === "failed" && <span role="alert" className="text-sm text-[#ffbd73]">Provider 创建失败</span>}
                </div>
            </section>
            <section className="rounded-xl border border-[#1f3f2a] bg-[#09120c] p-5">
                <h2 className="text-lg font-medium text-[#e4f5e9]">创建模型对象</h2>
                <p className="mt-1 text-xs text-[#688371]">用途由服务端模板固定，图片模型不会进入视频节点。</p>
                <div className="mt-4 grid gap-3">
                    <label className="text-sm">Provider<select aria-label="模型 Provider" value={modelForm.provider_id} onChange={(event) => setModelForm({ ...modelForm, provider_id: event.target.value })} className="mt-1 block w-full rounded border border-[#285038] bg-[#08100b] px-3 py-2"><option value="">请选择 Provider</option>{registry.providers.map((provider) => <option key={provider.provider_id} value={provider.provider_id}>{provider.display_name}</option>)}</select></label>
                    <label className="text-sm">模型模板<select aria-label="模型模板" value={modelForm.template_id} onChange={(event) => setModelForm({ ...modelForm, template_id: event.target.value })} className="mt-1 block w-full rounded border border-[#285038] bg-[#08100b] px-3 py-2">{registry.templates.map((template) => <option key={template.template_id} value={template.template_id}>{template.title}</option>)}</select></label>
                    <label className="text-sm">模型 ID<input aria-label="模型 ID" value={modelForm.model_id} onChange={(event) => setModelForm({ ...modelForm, model_id: event.target.value })} className="mt-1 block w-full rounded border border-[#285038] bg-[#08100b] px-3 py-2" /></label>
                    <label className="text-sm">供应商模型名<input aria-label="供应商模型名" value={modelForm.provider_model_name} onChange={(event) => setModelForm({ ...modelForm, provider_model_name: event.target.value })} className="mt-1 block w-full rounded border border-[#285038] bg-[#08100b] px-3 py-2" /></label>
                    <label className="text-sm">模型显示名<input aria-label="模型显示名" value={modelForm.display_name} onChange={(event) => setModelForm({ ...modelForm, display_name: event.target.value })} className="mt-1 block w-full rounded border border-[#285038] bg-[#08100b] px-3 py-2" /></label>
                    <label className="text-sm">模型介绍<textarea aria-label="模型介绍" value={modelForm.introduction} onChange={(event) => setModelForm({ ...modelForm, introduction: event.target.value })} className="mt-1 block w-full rounded border border-[#285038] bg-[#08100b] px-3 py-2" /></label>
                    <button type="button" disabled={!modelForm.provider_id || modelStatus === "saving"} onClick={() => void saveModel()} className="rounded bg-[#183f26] px-4 py-2 text-sm text-[#8ff0aa] disabled:opacity-40">创建模型</button>
                    {modelStatus === "saved" && <span className="text-sm text-[#58d881]">模型已创建</span>}{modelStatus === "failed" && <span role="alert" className="text-sm text-[#ffbd73]">模型创建失败</span>}
                </div>
            </section>
        </div>
    </section>;
}
