import { BarChart3, Boxes, FileText, FolderKanban, Images, KeyRound, LogOut, Orbit, Rows3, SlidersHorizontal, Users } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { ChangePasswordDialog } from "@/components/auth/change-password-dialog";
import { SkinPicker } from "@/components/layout/skin-picker";
import { TaskTray } from "@/components/layout/task-tray";
import { useSessionStore } from "@/stores/portal/use-session-store";


const releasedNavigation = [
    { label: "项目", to: "/canvas", icon: FolderKanban },
    { label: "资产", to: "/assets", icon: Images },
    { label: "任务", to: "/tasks", icon: Rows3 },
    { label: "统计", to: "/usage", icon: BarChart3 },
] as const;

const adminNavigation = [
    { label: "账号管理", to: "/admin/users", icon: Users },
    { label: "模型派发", to: "/admin/models", icon: SlidersHorizontal },
    { label: "工作流库", to: "/admin/comfy-workflows", icon: Boxes },
    { label: "人像资产库", to: "/admin/asset-library", icon: Images },
    { label: "使用统计", to: "/admin/usage", icon: BarChart3 },
    { label: "后台日志", to: "/admin/logs", icon: FileText },
] as const;

export function ProductShell({ children }: { children: ReactNode }) {
    const session = useSessionStore((state) => state.session);
    const logout = useSessionStore((state) => state.logout);
    const location = useLocation();
    const currentCanvasPath = /^\/canvas\/[^/]+$/.test(location.pathname) ? location.pathname : null;
    const [passwordOpen, setPasswordOpen] = useState(false);
    const [rememberedCanvas, setRememberedCanvas] = useState(() => ({ userId: session?.user_id ?? null, path: currentCanvasPath ?? "/canvas", ignoredPath: null as string | null }));
    const sameUser = rememberedCanvas.userId === (session?.user_id ?? null);
    const projectTarget = sameUser && currentCanvasPath !== rememberedCanvas.ignoredPath ? currentCanvasPath ?? rememberedCanvas.path : sameUser ? rememberedCanvas.path : "/canvas";
    const navigation = releasedNavigation.map((item) => item.to === "/canvas" ? { ...item, to: projectTarget } : item);

    useEffect(() => {
        const userId = session?.user_id ?? null;
        setRememberedCanvas((current) => {
            if (current.userId !== userId) return { userId, path: "/canvas", ignoredPath: currentCanvasPath };
            if (!currentCanvasPath) return current.ignoredPath ? { ...current, ignoredPath: null } : current;
            if (currentCanvasPath === current.ignoredPath || current.path === currentCanvasPath) return current;
            return { userId, path: currentCanvasPath, ignoredPath: null };
        });
    }, [currentCanvasPath, session?.user_id]);

    return (
        <div className="h-dvh overflow-hidden bg-[var(--c-bg)] text-[var(--c-text)]">
            <header className="flex h-14 items-center justify-between gap-2 overflow-x-auto border-b border-[var(--c-panel-hover)] bg-[var(--c-panel)] px-3 md:hidden">
                <div className="flex shrink-0 items-center gap-2 font-semibold"><Orbit className="size-5 text-[var(--c-accent)]" /><span className="hidden sm:inline">AI 创作画布</span></div>
                <nav className="flex shrink-0 gap-1">{[...navigation, ...(session?.role === "admin" ? adminNavigation : [])].map(({ label, to }) => <NavLink key={label} to={to} className="rounded px-2 py-1 text-xs text-[var(--c-text-3)]">{label}</NavLink>)}</nav>
                <button aria-label="修改密码" title="修改密码" className="shrink-0 rounded p-1.5 text-[var(--c-text-3)] hover:bg-[var(--c-panel-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--c-accent)]" onClick={() => setPasswordOpen(true)}><KeyRound className="size-4" /></button>
                <SkinPicker />
                <button aria-label="退出登录" title="退出登录" className="shrink-0 rounded p-1.5 text-[var(--c-text-3)] hover:bg-[var(--c-panel-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--c-accent)]" onClick={() => void logout()}><LogOut className="size-4" /></button>
            </header>
            <aside aria-label="侧边栏" className="fixed inset-y-0 left-0 z-30 hidden w-56 border-r border-[var(--c-panel-hover)] bg-[var(--c-panel)] p-4 md:flex md:flex-col">
                <div className="flex items-center gap-2 text-base font-semibold"><Orbit className="size-5 text-[var(--c-accent)]" /><span><i className="not-italic text-[var(--c-accent)]">AI</i> 创作画布</span></div>
                <p className="mt-2 text-xs text-[var(--c-text-3)]">本地创作工作室</p>
                <nav className="mt-8 space-y-1" aria-label="主导航">
                    {navigation.map(({ label, to, icon: Icon }) => <NavLink key={label} to={to} className={({ isActive }) => `flex items-center gap-3 rounded-lg border-l-2 px-3 py-2.5 text-sm ${isActive ? "border-[var(--c-accent)] bg-[var(--c-panel-hover)] text-[var(--c-text)]" : "border-transparent text-[var(--c-text-3)] hover:bg-[var(--c-panel)] hover:text-[var(--c-text)]"}`}><Icon className="size-4" />{label}</NavLink>)}
                    {session?.role === "admin" && adminNavigation.map(({ label, to, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => `flex items-center gap-3 rounded-lg border-l-2 px-3 py-2.5 text-sm ${isActive ? "border-[var(--c-accent)] bg-[var(--c-panel-hover)] text-[var(--c-text)]" : "border-transparent text-[var(--c-text-3)] hover:bg-[var(--c-panel)] hover:text-[var(--c-text)]"}`}><Icon className="size-4" />{label}</NavLink>)}
                </nav>
                <div className="mt-auto border-t border-[var(--c-panel-hover)] pt-4">
                    <div className="text-sm text-[var(--c-text-2)]">{session?.username || "未登录"}</div>
                    <div className="mt-1 text-xs text-[var(--c-text-3)]">{session?.role === "admin" ? "管理员" : "普通用户"}</div>
                    <div className="mt-3 flex items-center gap-2 rounded px-2 py-1.5 text-xs text-[var(--c-text-3)] hover:bg-[var(--c-panel-hover)]"><SkinPicker /></div>
                    <button className="flex items-center gap-2 rounded px-2 py-1.5 text-xs text-[var(--c-text-3)] hover:bg-[var(--c-panel-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--c-accent)]" onClick={() => setPasswordOpen(true)}><KeyRound className="size-3.5" />修改密码</button>
                    <button className="mt-1 flex items-center gap-2 rounded px-2 py-1.5 text-xs text-[var(--c-text-3)] hover:bg-[var(--c-panel-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--c-accent)]" onClick={() => void logout()}><LogOut className="size-3.5" />退出登录</button>
                </div>
            </aside>
            <main data-testid="product-main" className="h-[calc(100dvh-3.5rem)] overflow-auto bg-[var(--c-bg)] pb-[var(--task-tray-height)] md:ml-56 md:h-dvh">{children}</main>
            <TaskTray />
            <ChangePasswordDialog open={passwordOpen} onClose={() => setPasswordOpen(false)} />
        </div>
    );
}
