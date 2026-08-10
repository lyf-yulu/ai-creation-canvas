import { create } from "zustand";
import { persist, type PersistStorage, type StorageValue } from "zustand/middleware";

import { nanoid } from "nanoid";
import { normalizeViewport } from "@/features/canvas/viewport";
import { captureAppStorageLease, localForageStorage, setItemForLease } from "@/lib/localforage-storage";
import type { ScopedStoreLease } from "@/storage/scope";
import type { CanvasBackgroundMode } from "@/lib/canvas-theme";
import type { CanvasAssistantSession, CanvasConnection, CanvasNodeData, ViewportTransform } from "@/types/canvas";

export type CanvasProject = {
    id: string;
    title: string;
    createdAt: string;
    updatedAt: string;
    nodes: CanvasNodeData[];
    connections: CanvasConnection[];
    chatSessions: CanvasAssistantSession[];
    activeChatId: string | null;
    backgroundMode: CanvasBackgroundMode;
    showImageInfo: boolean;
    viewport: ViewportTransform;
};

export type ProjectSyncMetadata =
    | { source: "draft" }
    | { source: "legacy" }
    | { source: "server"; version: number; snapshot: string };

export type ProjectSyncMetadataMap = Record<string, ProjectSyncMetadata>;

export type CanvasStore = {
    hydrated: boolean;
    projectsLoaded: boolean;
    projects: CanvasProject[];
    projectSyncMetadata: ProjectSyncMetadataMap;
    syncNotice: string | null;
    createProject: (title?: string) => string;
    importProject: (project: Partial<CanvasProject>) => string;
    openProject: (id: string) => CanvasProject | null;
    renameProject: (id: string, title: string) => void;
    deleteProjects: (ids: string[]) => void;
    replaceProjects: (projects: CanvasProject[], projectSyncMetadata?: ProjectSyncMetadataMap) => void;
    setProjectSyncMetadata: (id: string, metadata: ProjectSyncMetadata | null) => void;
    setProjectsLoaded: (loaded: boolean) => void;
    setSyncNotice: (notice: string | null) => void;
    updateProject: (id: string, patch: Partial<Pick<CanvasProject, "nodes" | "connections" | "chatSessions" | "activeChatId" | "backgroundMode" | "showImageInfo" | "viewport">>) => void;
};

const initialViewport: ViewportTransform = { x: 0, y: 0, k: 1 };
const CANVAS_STORE_KEY = "infinite-canvas:canvas_store";
type PersistedCanvasState = Pick<CanvasStore, "projects" | "projectSyncMetadata">;
let saveTimer: ReturnType<typeof setTimeout> | null = null;
let queuedPersistState: PersistedCanvasState | null = null;
let queuedLease: ScopedStoreLease | null = null;

export function clearCanvasInMemory() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = null;
    queuedPersistState = null;
    queuedLease = null;
    useCanvasStore.setState({ projects: [], projectSyncMetadata: {}, hydrated: false, projectsLoaded: false, syncNotice: null });
}

export function migrateCanvasPersistedState(persistedState: unknown, persistedVersion: number) {
    const state = persistedState && typeof persistedState === "object" ? persistedState as Partial<PersistedCanvasState> : {};
    const projects = Array.isArray(state.projects) ? state.projects : [];
    if (persistedVersion >= 1 && state.projectSyncMetadata && typeof state.projectSyncMetadata === "object") {
        return { ...state, projects, projectSyncMetadata: state.projectSyncMetadata };
    }
    const projectSyncMetadata: ProjectSyncMetadataMap = {};
    for (const project of projects) projectSyncMetadata[project.id] = { source: "legacy" };
    return { ...state, projects, projectSyncMetadata };
}

const canvasStorage: PersistStorage<PersistedCanvasState> = {
    getItem: async (name) => {
        const value = await localForageStorage.getItem(name);
        if (!value) return null;
        const parsed = JSON.parse(value) as StorageValue<PersistedCanvasState>;
        queuedPersistState = parsed.state;
        return parsed;
    },
    setItem: (name, value) => {
        const nextState = value.state as PersistedCanvasState;
        if (queuedPersistState && queuedPersistState.projects === nextState.projects && queuedPersistState.projectSyncMetadata === nextState.projectSyncMetadata) return;
        const lease = captureAppStorageLease();
        if (!lease) return;
        queuedPersistState = nextState;
        queuedLease = lease;
        if (saveTimer) clearTimeout(saveTimer);
        saveTimer = setTimeout(() => {
            saveTimer = null;
            const scheduledLease = queuedLease;
            queuedLease = null;
            if (scheduledLease) void setItemForLease(scheduledLease, name, JSON.stringify(value));
        }, 400);
    },
    removeItem: (name) => localForageStorage.removeItem(name),
};

export const useCanvasStore = create<CanvasStore>()(
    persist(
        (set, get) => ({
            hydrated: false,
            projectsLoaded: false,
            projects: [],
            projectSyncMetadata: {},
            syncNotice: null,
            createProject: (title = "未命名画布") => {
                const now = new Date().toISOString();
                const id = nanoid();
                const project: CanvasProject = {
                    id,
                    title,
                    createdAt: now,
                    updatedAt: now,
                    nodes: [],
                    connections: [],
                    chatSessions: [],
                    activeChatId: null,
                    backgroundMode: "lines",
                    showImageInfo: false,
                    viewport: initialViewport,
                };
                set((state) => ({
                    projects: [project, ...state.projects],
                    projectSyncMetadata: { ...state.projectSyncMetadata, [id]: { source: "draft" } },
                }));
                return id;
            },
            importProject: (source) => {
                const now = new Date().toISOString();
                const project: CanvasProject = {
                    id: nanoid(),
                    title: source.title || "导入画布",
                    createdAt: source.createdAt || now,
                    updatedAt: now,
                    nodes: source.nodes || [],
                    connections: source.connections || [],
                    chatSessions: source.chatSessions || [],
                    activeChatId: source.activeChatId || null,
                    backgroundMode: source.backgroundMode || "lines",
                    showImageInfo: source.showImageInfo || false,
                    viewport: normalizeViewport(source.viewport),
                };
                set((state) => ({
                    projects: [project, ...state.projects],
                    projectSyncMetadata: { ...state.projectSyncMetadata, [project.id]: { source: "draft" } },
                }));
                return project.id;
            },
            openProject: (id) => {
                return get().projects.find((item) => item.id === id) || null;
            },
            renameProject: (id, title) =>
                set((state) => ({
                    projects: state.projects.map((project) => (project.id === id ? { ...project, title: title.trim() || project.title, updatedAt: new Date().toISOString() } : project)),
                })),
            deleteProjects: (ids) =>
                set((state) => {
                    const projects = state.projects.filter((project) => !ids.includes(project.id));
                    const projectSyncMetadata = { ...state.projectSyncMetadata };
                    for (const id of ids) {
                        if (projectSyncMetadata[id]?.source !== "server") delete projectSyncMetadata[id];
                    }
                    return { projects, projectSyncMetadata };
                }),
            replaceProjects: (projects, projectSyncMetadata) => set(projectSyncMetadata ? { projects, projectSyncMetadata } : { projects }),
            setProjectSyncMetadata: (id, metadata) => set((state) => {
                const projectSyncMetadata = { ...state.projectSyncMetadata };
                if (metadata) projectSyncMetadata[id] = metadata;
                else delete projectSyncMetadata[id];
                return { projectSyncMetadata };
            }),
            setProjectsLoaded: (projectsLoaded) => set({ projectsLoaded }),
            setSyncNotice: (syncNotice) => set({ syncNotice }),
            updateProject: (id, patch) =>
                set((state) => ({
                    projects: state.projects.map((project) => (project.id === id ? { ...project, ...patch, updatedAt: new Date().toISOString() } : project)),
                })),
        }),
        {
            name: CANVAS_STORE_KEY,
            storage: canvasStorage,
            version: 1,
            migrate: migrateCanvasPersistedState,
            partialize: (state) =>
                ({
                    projects: state.projects,
                    projectSyncMetadata: state.projectSyncMetadata,
                }),
            onRehydrateStorage: () => () => {
                useCanvasStore.setState({ hydrated: true });
            },
        },
    ),
);
