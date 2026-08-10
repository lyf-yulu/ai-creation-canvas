export function TaskTray() {
    return (
        <aside data-testid="task-tray" aria-label="任务托盘" className="fixed inset-x-0 bottom-0 z-40 flex h-[var(--task-tray-height)] items-center border-t border-[#21472f] bg-[#09110c]/98 px-4 text-sm text-[#89a792] backdrop-blur md:left-56">
            <span className="mr-3 font-medium text-[#dceee1]">运行任务</span>
            <span>暂无运行任务</span>
        </aside>
    );
}
