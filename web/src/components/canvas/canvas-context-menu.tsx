import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { Plus, Trash2 } from "lucide-react";

import { canvasThemes } from "@/lib/canvas-theme";
import { useThemeStore } from "@/stores/use-theme-store";
import type { ContextMenuState } from "@/types/canvas";

export function CanvasNodeContextMenu({ menu, onClose, onDuplicate, onDelete }: { menu: ContextMenuState; onClose: (restoreFocus?: boolean) => void; onDuplicate?: () => void; onDelete: () => void }) {
    const theme = canvasThemes[useThemeStore((state) => state.theme)];
    const menuRef = useRef<HTMLDivElement>(null);
    const left = Math.max(8, Math.min(menu.x, window.innerWidth - 184));
    const top = Math.max(8, Math.min(menu.y, window.innerHeight - 96));

    useEffect(() => {
        const close = (event: PointerEvent) => {
            const target = event.target;
            if (target instanceof Node && menuRef.current?.contains(target)) return;
            if (target instanceof Element && target.closest(".ant-popover")) return;
            onClose(false);
        };
        window.addEventListener("pointerdown", close);
        return () => window.removeEventListener("pointerdown", close);
    }, [onClose]);

    useEffect(() => {
        menuRef.current?.querySelector<HTMLElement>("[role='menuitem']")?.focus();
    }, [menu]);

    return (
        <div
            ref={menuRef}
            role="menu"
            aria-label={menu.type === "node" ? "节点操作" : "连接操作"}
            className="fixed z-[80] min-w-44 overflow-hidden rounded-xl border py-1 shadow-2xl"
            style={{ left, top, background: theme.toolbar.panel, borderColor: theme.toolbar.border, color: theme.node.text }}
            onPointerDown={(event) => event.stopPropagation()}
            onKeyDown={(event) => {
                if (event.key === "Escape") {
                    event.preventDefault();
                    event.stopPropagation();
                    onClose(true);
                } else if (event.key === "Tab") {
                    onClose(false);
                }
            }}
        >
            {menu.type === "node" && onDuplicate ? <MenuButton icon={<Plus className="size-4" />} label="复制" onClick={onDuplicate} /> : null}
            <MenuButton icon={<Trash2 className="size-4" />} label="删除" onClick={onDelete} danger />
        </div>
    );
}

function MenuButton({ icon, label, onClick, danger = false }: { icon: ReactNode; label: string; onClick?: () => void; danger?: boolean }) {
    const theme = canvasThemes[useThemeStore((state) => state.theme)];

    return (
        <button role="menuitem" type="button" className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors hover:opacity-80" style={{ color: danger ? "#f87171" : theme.node.text }} onClick={onClick}>
            {icon}
            <span>{label}</span>
        </button>
    );
}
