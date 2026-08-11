import { afterEach, describe, expect, it } from "vitest";

import { GRAPH_SCHEMA_VERSION } from "@/features/graph/contracts";
import type { CanvasProjectInput } from "@/features/graph/normalize-project";
import { clearCanvasInMemory, migrateCanvasPersistedState, useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { CanvasNodeType } from "@/types/canvas";

const timestamp = "2026-08-11T01:02:03.000Z";

function legacyProject(id = "legacy"): CanvasProjectInput {
    return {
        id,
        title: "Legacy",
        createdAt: timestamp,
        updatedAt: timestamp,
        nodes: [{ id: "text", type: CanvasNodeType.Text, title: "Text", position: { x: 0, y: 0 }, width: 200, height: 100, metadata: { content: "hello" } }],
        connections: [],
        chatSessions: [],
        activeChatId: null,
        backgroundMode: "lines",
        showImageInfo: false,
        viewport: { x: 0, y: 0, k: 1 },
    };
}

afterEach(() => clearCanvasInMemory());

describe("canvas graph persistence", () => {
    it("creates graph-versioned projects", () => {
        const id = useCanvasStore.getState().createProject("New");
        expect(useCanvasStore.getState().openProject(id)?.graphSchemaVersion).toBe(GRAPH_SCHEMA_VERSION);
    });

    it("normalizes imported projects without retaining the caller's mutable node arrays", () => {
        const source = legacyProject();
        const id = useCanvasStore.getState().importProject(source);
        source.nodes[0].metadata!.content = "changed outside";

        const imported = useCanvasStore.getState().openProject(id)!;
        expect(imported.graphSchemaVersion).toBe(GRAPH_SCHEMA_VERSION);
        expect(imported.nodes[0].metadata?.graph).toMatchObject({ role: "prompt", text: "hello" });
        expect(imported.nodes[0].metadata?.content).toBe("hello");
    });

    it("normalizes authoritative server replacements while preserving server IDs and timestamps", () => {
        const source = legacyProject("server-id");
        useCanvasStore.getState().replaceProjects([source]);

        const stored = useCanvasStore.getState().openProject("server-id")!;
        expect(stored).toMatchObject({ id: "server-id", createdAt: timestamp, updatedAt: timestamp, graphSchemaVersion: GRAPH_SCHEMA_VERSION });
        expect(stored.nodes[0].metadata?.graph?.role).toBe("prompt");
    });

    it("normalizes old persisted projects and marks their sync metadata as legacy", () => {
        const source = legacyProject();
        const migrated = migrateCanvasPersistedState({ projects: [source] }, 0);

        expect(migrated.projects[0]).toMatchObject({ id: "legacy", updatedAt: timestamp, graphSchemaVersion: GRAPH_SCHEMA_VERSION });
        expect(migrated.projects[0].nodes[0].metadata?.graph?.role).toBe("prompt");
        expect(migrated.projectSyncMetadata).toEqual({ legacy: { source: "legacy" } });
    });

    it("normalizes version-one caches even when sync metadata already exists", () => {
        const source = legacyProject();
        const migrated = migrateCanvasPersistedState({ projects: [source], projectSyncMetadata: { legacy: { source: "draft" } } }, 1);

        expect(migrated.projects[0].graphSchemaVersion).toBe(GRAPH_SCHEMA_VERSION);
        expect(migrated.projectSyncMetadata).toEqual({ legacy: { source: "draft" } });
    });
});
