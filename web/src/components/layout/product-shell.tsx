import { FolderKanban, Images, LogOut, Orbit, Rows3, SlidersHorizontal, Users } from "lucide-react";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

import { TaskTray } from "@/components/layout/task-tray";
import { useSessionStore } from "@/stores/portal/use-session-store";


const releasedNavigation = [
    { label: "项目", to: "/canvas", icon: FolderKanban },
    { label: "资产", to: "/assets", icon: Images },
    { label: "任务", to: "/tasks", icon: Rows3 },
] as const;

const adminNavigation = [
    { label: "账号管理", to: "/admin/users", icon: Users },
    { label: "模型派发", to: "/admin/models", icon: SlidersHorizontal },
] as const;

export function ProductShell({ children }: { children: ReactNode }) {
    const session = useSessionStore((state) => state.session);
    const logout = useSessionStore((state) => state.logout);

    return (
        <div className="h-dvh overflow-hidden bg-[#050806] text-[#e5f5e9]">
            <header className="flex h-14 items-center justify-between border-b border-[#193523] bg-[#08100b] px-4 md:hidden">
                <div className="flex items-center gap-2 font-semibold"><Orbit className="size-5 text-[#57ed86]" />AI 创作画布</div>
                <nav className="flex gap-1">{[...releasedNavigation, ...(session?.role === "admin" ? adminNavigation : [])].map(({ label, to }) => <NavLink key={to} to={to} className="rounded px-2 py-1 text-xs text-[#a8bbae]">{label}</NavLink>)}</nav>
            </header>
            <aside className="fixed inset-y-0 left-0 z-30 hidden w-56 border-r border-[#193523] bg-[#08100b] p-4 md:flex md:flex-col">
                <div className="flex items-center gap-2 text-base font-semibold"><Orbit className="size-5 text-[#57ed86]" /><span><i className="not-italic text-[#57ed86]">AI</i> 创作画布</span></div>
                <p className="mt-2 text-xs text-[#688371]">本地创作工作室</p>
                <nav className="mt-8 space-y-1" aria-label="主导航">
                    {releasedNavigation.map(({ label, to, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => `flex items-center gap-3 rounded-lg border-l-2 px-3 py-2.5 text-sm ${isActive ? "border-[#58ed87] bg-[#102619] text-[#e9fff0]" : "border-transparent text-[#94aa9a] hover:bg-[#0d1b12] hover:text-[#dceee1]"}`}><Icon className="size-4" />{label}</NavLink>)}
                    {session?.role === "admin" && adminNavigation.map(({ label, to, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => `flex items-center gap-3 rounded-lg border-l-2 px-3 py-2.5 text-sm ${isActive ? "border-[#58ed87] bg-[#102619] text-[#e9fff0]" : "border-transparent text-[#94aa9a] hover:bg-[#0d1b12] hover:text-[#dceee1]"}`}><Icon className="size-4" />{label}</NavLink>)}
                </nav>
                <div className="mt-auto border-t border-[#193523] pt-4">
                    <div className="text-sm text-[#d8eadd]">{session?.username || "未登录"}</div>
                    <div className="mt-1 text-xs text-[#688371]">{session?.role === "admin" ? "管理员" : "普通用户"}</div>
                    <button className="mt-3 flex items-center gap-2 rounded px-2 py-1.5 text-xs text-[#8fa596] hover:bg-[#102219] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#58ed87]" onClick={() => void logout()}><LogOut className="size-3.5" />退出登录</button>
                </div>
            </aside>
            <main data-testid="product-main" className="h-[calc(100dvh-3.5rem)] overflow-auto bg-[#050806] pb-[var(--task-tray-height)] md:ml-56 md:h-dvh">{children}</main>
            <TaskTray />
        </div>
    );
}
