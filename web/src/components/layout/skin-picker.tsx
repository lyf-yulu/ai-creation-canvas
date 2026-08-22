import { useMemo, useState } from "react";
import { Popover } from "antd";
import { Palette } from "lucide-react";

import type { SkinColors } from "@/api/session";
import { DEFAULT_SKIN, SKIN_TOKENS } from "@/lib/skin";
import { useSkinStore } from "@/stores/use-skin-store";

const PRESET_LABELS: Record<string, string> = {
    default: "蓝灰",
    monochrome: "黑白灰",
    "classic-green": "绿黑经典",
};

const iconClass = "inline-flex size-7 shrink-0 items-center justify-center text-muted-foreground transition hover:text-foreground dark:text-muted-foreground dark:hover:text-white [&_svg]:size-4";

export function SkinPicker() {
    const skin = useSkinStore((state) => state.skin);
    const presets = useSkinStore((state) => state.presets);
    const save = useSkinStore((state) => state.save);
    const reset = useSkinStore((state) => state.reset);
    const [draft, setDraft] = useState<SkinColors | null>(null);
    const [busy, setBusy] = useState(false);
    const [open, setOpen] = useState(false);

    const current = draft ?? skin ?? DEFAULT_SKIN;
    const mergedPresets = useMemo(() => ({ default: DEFAULT_SKIN, ...presets }), [presets]);

    const applyPreset = async (colors: SkinColors) => {
        setBusy(true);
        try {
            await save({ ...colors });
            setDraft(null);
        } finally {
            setBusy(false);
        }
    };

    const saveCustom = async () => {
        setBusy(true);
        try {
            await save({ ...current });
            setDraft(null);
        } finally {
            setBusy(false);
        }
    };

    const content = (
        <div className="w-72">
            <div className="mb-3 text-xs text-muted-foreground">预设皮肤</div>
            <div className="flex gap-2">
                {Object.entries(mergedPresets).map(([name, colors]) => (
                    <button
                        key={name}
                        type="button"
                        disabled={busy}
                        className="min-w-0 flex-1 rounded-lg border border-border p-1.5 text-center transition hover:border-foreground/60 disabled:opacity-50"
                        onClick={() => void applyPreset(colors)}
                    >
                        <span className="mx-auto block h-6 w-full rounded" style={{ background: colors.bg, border: `2px solid ${colors.accent}` }} />
                        <span className="mt-1 block truncate text-[11px]">{PRESET_LABELS[name] ?? name}</span>
                    </button>
                ))}
            </div>
            <div className="mb-2 mt-4 text-xs text-muted-foreground">自定义配色</div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-2">
                {SKIN_TOKENS.map((token) => (
                    <label key={token.key} className="flex min-w-0 items-center justify-between gap-2 text-[11px]">
                        <span className="truncate">{token.label}</span>
                        <input
                            type="color"
                            value={current[token.key] ?? DEFAULT_SKIN[token.key]}
                            disabled={busy}
                            aria-label={`皮肤颜色 ${token.label}`}
                            onChange={(event) => setDraft({ ...current, [token.key]: event.target.value })}
                            className="size-6 shrink-0 cursor-pointer rounded border border-border bg-transparent p-0.5"
                        />
                    </label>
                ))}
            </div>
            <div className="mt-3 flex items-center justify-between">
                <button type="button" disabled={busy} className="text-[11px] text-muted-foreground underline-offset-2 hover:underline" onClick={() => void reset()}>恢复默认</button>
                <button
                    type="button"
                    disabled={busy || !draft}
                    onClick={() => void saveCustom()}
                    className="rounded bg-foreground px-3 py-1.5 text-xs font-medium text-background transition disabled:opacity-40 dark:bg-muted dark:text-foreground"
                >
                    保存配色
                </button>
            </div>
        </div>
    );

    return (
        <Popover content={content} trigger="click" placement="bottomRight" open={open} onOpenChange={setOpen}>
            <button type="button" className={iconClass} aria-label="皮肤设置" title="皮肤设置"><Palette /></button>
        </Popover>
    );
}
