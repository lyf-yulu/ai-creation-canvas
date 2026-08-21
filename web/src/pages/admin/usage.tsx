import { useEffect, useState } from "react";

import {
    fetchAdminUsage,
    fetchAdminUsageRates,
    updateAdminUsageRates,
    type AdminUsageCharged,
    type AdminUsageCounters,
    type AdminUsageRates,
    type AdminUserUsage,
} from "@/api/admin";

const empty: AdminUsageCounters = { jobs: 0, succeeded: 0, failed: 0, active: 0, image: 0, video: 0 };
const emptyCharged: AdminUsageCharged = { successful_jobs: 0, image_count: 0, video_seconds: 0, total_cost_fen: "0" };

const fenToYuan = (fen: string | number) => (Number(fen) / 100).toFixed(2);

export default function AdminUsagePage() {
    const [totals, setTotals] = useState<AdminUsageCounters>(empty);
    const [charged, setCharged] = useState<AdminUsageCharged>(emptyCharged);
    const [users, setUsers] = useState<AdminUserUsage[] | null>(null);
    const [failed, setFailed] = useState(false);
    const [imagePrice, setImagePrice] = useState("");
    const [videoPrice, setVideoPrice] = useState("");
    const [ratesStatus, setRatesStatus] = useState<"idle" | "saving" | "saved" | "failed">("idle");
    useEffect(() => {
        let active = true;
        void fetchAdminUsage().then((result) => {
            if (!active) return;
            setTotals(result.totals); setCharged(result.summary); setUsers(result.users); setFailed(false);
        }).catch(() => { if (active) { setUsers([]); setFailed(true); } });
        void fetchAdminUsageRates().then((rates) => {
            if (!active) return;
            setImagePrice(String(rates.image_price_fen));
            setVideoPrice(String(rates.video_price_fen));
        }).catch(() => undefined);
        return () => { active = false; };
    }, []);
    const saveRates = async () => {
        const imagePriceFen = Number(imagePrice);
        const videoPriceFen = Number(videoPrice);
        if (!Number.isInteger(imagePriceFen) || imagePriceFen < 0 || !Number.isInteger(videoPriceFen) || videoPriceFen < 0) {
            setRatesStatus("failed");
            return;
        }
        setRatesStatus("saving");
        try {
            const rates: AdminUsageRates = { image_price_fen: imagePriceFen, video_price_fen: videoPriceFen };
            await updateAdminUsageRates(rates);
            setRatesStatus("saved");
        } catch {
            setRatesStatus("failed");
        }
    };
    const cards = [
        ["全部任务", totals.jobs], ["成功", totals.succeeded], ["进行中", totals.active], ["失败", totals.failed],
        ["图像", totals.image], ["视频", totals.video],
        ["图像用量(张)", charged.image_count], ["视频用量(秒)", charged.video_seconds],
        ["总费用(元)", fenToYuan(charged.total_cost_fen)],
    ] as const;
    return <section className="mx-auto max-w-7xl px-4 py-7 sm:px-5">
        <p className="text-xs tracking-[0.2em] text-[var(--c-accent)]">ADMIN · USAGE</p>
        <h1 className="mt-2 text-2xl font-semibold sm:text-3xl">使用统计</h1>
        <p className="mt-2 text-sm text-[var(--c-text-3)]">按服务端确认的用户 ID 汇总任务，不采用浏览器上报的用户名或所有者。</p>
        {failed && <p role="alert" className="mt-5 rounded border border-[var(--c-amber-border)] bg-[var(--c-amber-bg)] p-3 text-sm text-[var(--c-warning)]">统计加载失败，请重试。</p>}
        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">{cards.map(([label, value]) => <div key={label} className="rounded-xl border border-[var(--c-border-strong)] bg-[var(--c-panel)] p-4"><span className="block text-xs text-[var(--c-text-3)]">{label}</span><strong className="mt-2 block text-2xl text-[var(--c-text-2)]">{value}</strong></div>)}</div>
        <section className="mt-6 rounded-xl border border-[var(--c-border)] bg-[var(--c-panel)] p-4" aria-label="费用单价">
            <h2 className="text-lg font-semibold">费用单价</h2>
            <p className="mt-1 text-xs text-[var(--c-text-3)]">任务成功时按此单价计费：图片按张、视频按秒，单位为分（100 分 = 1 元）。修改后仅对新完成的任务生效。</p>
            <div className="mt-4 flex flex-wrap items-end gap-4">
                <label className="text-sm text-[var(--c-text-2)]">
                    图片单价（分/张）
                    <input
                        aria-label="图片单价（分/张）"
                        type="number"
                        min={0}
                        step={1}
                        value={imagePrice}
                        onChange={(event) => { setImagePrice(event.target.value); setRatesStatus("idle"); }}
                        className="mt-1 block w-40 rounded border border-[var(--c-border-strong)] bg-[var(--c-panel)] px-3 py-2 text-[var(--c-text)]"
                    />
                </label>
                <label className="text-sm text-[var(--c-text-2)]">
                    视频单价（分/秒）
                    <input
                        aria-label="视频单价（分/秒）"
                        type="number"
                        min={0}
                        step={1}
                        value={videoPrice}
                        onChange={(event) => { setVideoPrice(event.target.value); setRatesStatus("idle"); }}
                        className="mt-1 block w-40 rounded border border-[var(--c-border-strong)] bg-[var(--c-panel)] px-3 py-2 text-[var(--c-text)]"
                    />
                </label>
                <button type="button" disabled={ratesStatus === "saving"} onClick={() => void saveRates()} className="rounded bg-[var(--c-accent)] px-4 py-2 text-sm font-semibold text-[var(--c-accent-fg)] disabled:opacity-40">
                    {ratesStatus === "saving" ? "正在保存…" : "保存单价"}
                </button>
                {ratesStatus === "saved" && <p role="status" className="text-sm text-[var(--c-accent)]">单价已保存。</p>}
                {ratesStatus === "failed" && <p role="alert" className="text-sm text-[var(--c-warning)]">单价无效或保存失败，请重试。</p>}
            </div>
        </section>
        <div className="mt-6 overflow-x-auto rounded-xl border border-[var(--c-border)] bg-[var(--c-panel)]">
            {users === null ? <p className="p-8 text-sm text-[var(--c-text-3)]">正在加载统计…</p> : <table className="w-full min-w-[52rem] text-left text-sm"><thead className="border-b border-[var(--c-border)] text-xs text-[var(--c-text-3)]"><tr><th className="p-4">账号</th><th>任务</th><th>图像</th><th>视频</th><th>成功</th><th>进行中</th><th>失败</th><th>费用(元)</th></tr></thead><tbody className="divide-y divide-[var(--c-panel-hover)]">{users.map((user) => <tr key={user.user_id}><th className="p-4 font-medium text-[var(--c-text)]">{user.display_name}<span className="ml-2 font-normal text-[var(--c-text-3)]">· {user.username}</span></th><td>{user.jobs}</td><td>{user.image}</td><td>{user.video}</td><td>{user.succeeded}</td><td>{user.active}</td><td>{user.failed}</td><td>{fenToYuan(user.summary?.total_cost_fen ?? 0)}</td></tr>)}</tbody></table>}
        </div>
    </section>;
}
