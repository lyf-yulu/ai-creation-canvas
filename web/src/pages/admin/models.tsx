import { useEffect, useMemo, useState } from "react";

import { fetchAdminModels, fetchAdminUsers, replaceAdminUserModels, type AdminUser } from "@/api/admin";
import type { ModelSpec } from "@/api/contracts";


export default function AdminModelsPage() {
    const [users, setUsers] = useState<AdminUser[]>([]);
    const [models, setModels] = useState<ModelSpec[]>([]);
    const [userId, setUserId] = useState("");
    const [selected, setSelected] = useState<string[]>([]);
    const [status, setStatus] = useState<"loading" | "ready" | "saving" | "saved" | "failed">("loading");
    const user = useMemo(() => users.find((item) => item.user_id === userId), [userId, users]);

    useEffect(() => {
        void Promise.all([fetchAdminUsers(), fetchAdminModels()]).then(([nextUsers, nextModels]) => {
            setUsers(nextUsers);
            setModels(nextModels);
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

    return <section className="mx-auto max-w-6xl px-5 py-8">
        <p className="text-xs tracking-[0.2em] text-[#58ed87]">ADMIN · MODELS</p>
        <h1 className="mt-2 text-3xl font-semibold">模型派发</h1>
        <p className="mt-2 text-sm text-[#829889]">用户只会看到这里分配的模型；服务密钥由管理员在服务端部署时配置。</p>
        <label className="mt-7 block max-w-md text-sm text-[#b9d0c0]">选择账号<select aria-label="选择账号" value={userId} onChange={(event) => setUserId(event.target.value)} className="mt-2 block w-full rounded-lg border border-[#285038] bg-[#08100b] px-3 py-2 text-[#e5f5e9]"><option value="">请选择账号</option>{users.map((item) => <option key={item.user_id} value={item.user_id}>{item.display_name} · {item.username}</option>)}</select></label>
        <div className="mt-6 grid gap-3 md:grid-cols-2">{models.map((model) => <label key={model.model_id} className="flex cursor-pointer items-start gap-3 rounded-xl border border-[#1f3f2a] bg-[#09120c] p-4"><input type="checkbox" aria-label={model.display_name} disabled={!userId} checked={selected.includes(model.model_id)} onChange={() => toggle(model.model_id)} className="mt-1 accent-[#58ed87]" /><span><span className="block text-sm text-[#e4f5e9]">{model.display_name}</span><span className="mt-1 block text-xs text-[#688371]">{model.service_id} · {model.operations.join(" / ")}</span></span></label>)}</div>
        <div className="mt-6 flex items-center gap-4"><button type="button" disabled={!userId || status === "saving"} onClick={() => void save()} className="rounded-lg bg-[#47d978] px-4 py-2 text-sm font-medium text-[#041008] disabled:opacity-40">保存派发</button>{status === "saved" && <span className="text-sm text-[#58d881]">派发已保存</span>}{status === "failed" && <span role="alert" className="text-sm text-[#ffbd73]">操作未完成，请重试。</span>}</div>
    </section>;
}
