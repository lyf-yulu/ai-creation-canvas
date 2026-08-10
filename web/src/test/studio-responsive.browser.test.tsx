import { page } from "vitest/browser";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ProductShell } from "@/components/layout/product-shell";
import CanvasProjectPage from "@/pages/canvas/project";
import { clearCanvasInMemory, useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { useSessionStore } from "@/stores/portal/use-session-store";
import "@/styles/globals.css";


type Bounds = Pick<DOMRect, "bottom" | "left" | "right" | "top">;

let root: Root;

function bounds(selector: string): Bounds {
    const element = document.querySelector(selector);
    expect(element, `missing layout element: ${selector}`).not.toBeNull();
    return element!.getBoundingClientRect();
}

function overlapArea(first: Bounds, second: Bounds) {
    return Math.max(0, Math.min(first.right, second.right) - Math.max(first.left, second.left))
        * Math.max(0, Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top));
}

beforeEach(() => {
    clearCanvasInMemory();
    useCanvasStore.setState({ hydrated: true, projectsLoaded: true });
    useSessionStore.setState({
        session: { user_id: "responsive-user", username: "响应式验收", role: "user", must_change_password: false },
        environment: "test",
        loading: false,
        errorCode: null,
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ models: [] }), { headers: { "content-type": "application/json" } })));
    document.body.innerHTML = '<div id="responsive-test-root"></div>';
    root = createRoot(document.getElementById("responsive-test-root")!);
});

afterEach(() => {
    flushSync(() => root.unmount());
    vi.unstubAllGlobals();
    clearCanvasInMemory();
    document.body.replaceChildren();
});

it.each([415, 240])("keeps canvas controls contained and non-overlapping at %i px", async (viewportWidth) => {
    await page.viewport(viewportWidth, 900);
    const projectId = useCanvasStore.getState().createProject("Responsive canvas");
    flushSync(() => root.render(
        <MemoryRouter initialEntries={[`/canvas/${projectId}`]}>
            <ProductShell>
                <Routes><Route path="/canvas/:id" element={<CanvasProjectPage />} /></Routes>
            </ProductShell>
        </MemoryRouter>,
    ));
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

    const canvas = bounds('[data-testid="studio-canvas"]');
    const controls = bounds('[data-testid="studio-canvas"] [data-canvas-no-zoom]');
    const inspector = bounds('[data-testid="generation-inspector"]');
    const tray = bounds('[data-testid="task-tray"]');

    expect(controls.left).toBeGreaterThanOrEqual(canvas.left);
    expect(controls.right).toBeLessThanOrEqual(canvas.right);
    expect(controls.top).toBeGreaterThanOrEqual(canvas.top);
    expect(controls.bottom).toBeLessThanOrEqual(canvas.bottom);
    expect(overlapArea(controls, inspector)).toBe(0);
    expect(overlapArea(controls, tray)).toBe(0);
    expect(overlapArea(inspector, tray)).toBe(0);
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);

    const controlClasses = document.querySelector('[data-testid="studio-canvas"] [data-canvas-no-zoom]')!.classList;
    expect(controlClasses).toContain("left-4");
    expect(controlClasses).toContain("max-w-[calc(100%-2rem)]");
});
