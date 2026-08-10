import { nanoid } from "nanoid";
import type { StoreApi } from "zustand";

import * as projectsApi from "@/api/projects";
import { ApiRequestError } from "@/api/client";
import { isStorageLeaseActive, onStorageScopeCleared, type ScopedStoreLease } from "@/storage/scope";
import { useCanvasStore, type CanvasProject, type CanvasStore } from "@/stores/canvas/use-canvas-store";


export type ProjectEnvelope = projectsApi.ProjectEnvelope;
export type ProjectApi = {
    list: (signal?: AbortSignal) => Promise<ProjectEnvelope[]>;
    create: (project: CanvasProject, signal?: AbortSignal) => Promise<ProjectEnvelope>;
    get: (id: string, signal?: AbortSignal) => Promise<ProjectEnvelope>;
    update: (project: CanvasProject, expectedVersion: number, signal?: AbortSignal) => Promise<ProjectEnvelope>;
    remove: (id: string, signal?: AbortSignal) => Promise<void>;
};

const defaultApi: ProjectApi = {
    list: projectsApi.listProjects,
    create: projectsApi.createProject,
    get: projectsApi.getProject,
    update: projectsApi.updateProject,
    remove: projectsApi.deleteProject,
};

function serialized(project: CanvasProject) { return JSON.stringify(project); }
function isConflict(error: unknown) { return error instanceof ApiRequestError ? error.code === "PROJECT_CONFLICT" : Boolean(error && typeof error === "object" && (error as { code?: unknown }).code === "PROJECT_CONFLICT"); }

export class ProjectSync {
    private generation = 0;
    private lease: ScopedStoreLease | null = null;
    private versions = new Map<string, number>();
    private snapshots = new Map<string, string>();
    private timer: ReturnType<typeof setTimeout> | null = null;
    private unsubscribe: (() => void) | null = null;
    private controllers = new Set<AbortController>();
    private flushingGenerations = new Set<number>();
    private queuedAgain = new Set<number>();

    constructor(private readonly api: ProjectApi, private readonly store: Pick<StoreApi<CanvasStore>, "getState" | "subscribe">) {}

    async activate(lease: ScopedStoreLease): Promise<void> {
        this.stop();
        const generation = this.generation;
        this.lease = lease;
        const localDrafts = this.store.getState().projects;
        const controller = this.controller();
        try {
            const serverEnvelopes = await this.api.list(controller.signal);
            if (!this.active(generation, lease)) return;
            this.versions.clear();
            this.snapshots.clear();
            const merged = serverEnvelopes.map((item) => {
                this.versions.set(item.project.id, item.version);
                this.snapshots.set(item.project.id, serialized(item.project));
                const local = localDrafts.find((draft) => draft.id === item.project.id);
                return local && local.updatedAt > item.project.updatedAt ? local : item.project;
            });
            for (const local of localDrafts) if (!this.versions.has(local.id)) merged.unshift(local);
            this.store.getState().replaceProjects(merged);
            this.store.getState().setSyncNotice(null);
            this.unsubscribe = this.store.subscribe((state, previous) => {
                if (state.projects !== previous.projects) this.queue();
            });
            if (merged.some((project) => this.snapshots.get(project.id) !== serialized(project))) this.queue();
        } catch (error) {
            if (this.active(generation, lease) && !(error instanceof DOMException && error.name === "AbortError")) {
                this.store.getState().setSyncNotice("项目暂时无法同步，当前修改仍保留在本机。");
            }
        } finally {
            this.controllers.delete(controller);
        }
    }

    async save(project: CanvasProject, expectedVersion: number, signal?: AbortSignal): Promise<ProjectEnvelope> {
        return expectedVersion > 0 ? this.api.update(project, expectedVersion, signal) : this.api.create(project, signal);
    }

    stop = () => {
        this.generation += 1;
        if (this.timer) clearTimeout(this.timer);
        this.timer = null;
        this.unsubscribe?.();
        this.unsubscribe = null;
        for (const controller of this.controllers) controller.abort();
        this.controllers.clear();
        this.lease = null;
        this.versions.clear();
        this.snapshots.clear();
    };

    private active(generation: number, lease: ScopedStoreLease) { return this.generation === generation && this.lease === lease && isStorageLeaseActive(lease); }
    private controller() { const controller = new AbortController(); this.controllers.add(controller); return controller; }
    private queue() {
        if (!this.lease || !isStorageLeaseActive(this.lease)) return;
        if (this.timer) clearTimeout(this.timer);
        this.timer = setTimeout(() => { this.timer = null; void this.flush(); }, 400);
    }

    private async flush() {
        const lease = this.lease;
        const generation = this.generation;
        if (!lease || !this.active(generation, lease)) return;
        if (this.flushingGenerations.has(generation)) {
            this.queuedAgain.add(generation);
            return;
        }
        this.flushingGenerations.add(generation);
        try {
            await this.flushOnce(lease, generation);
        } finally {
            this.flushingGenerations.delete(generation);
            if (this.queuedAgain.delete(generation) && this.active(generation, lease)) this.queue();
        }
    }

    private async flushOnce(lease: ScopedStoreLease, generation: number) {
        const projects = this.store.getState().projects;
        const currentIds = new Set(projects.map((project) => project.id));
        for (const id of [...this.snapshots.keys()]) {
            if (currentIds.has(id)) continue;
            const controller = this.controller();
            try {
                await this.api.remove(id, controller.signal);
                if (!this.active(generation, lease)) return;
                this.snapshots.delete(id);
                this.versions.delete(id);
            } catch (error) {
                if (this.active(generation, lease) && !(error instanceof DOMException && error.name === "AbortError")) this.store.getState().setSyncNotice("项目暂时无法同步，当前修改仍保留在本机。");
            } finally { this.controllers.delete(controller); }
        }
        for (const project of projects) {
            const localSnapshot = serialized(project);
            if (this.snapshots.get(project.id) === localSnapshot) continue;
            const controller = this.controller();
            try {
                const result = await this.save(project, this.versions.get(project.id) || 0, controller.signal);
                if (!this.active(generation, lease)) return;
                this.versions.set(project.id, result.version);
                this.snapshots.set(project.id, localSnapshot);
                if (serialized(this.store.getState().projects.find((item) => item.id === project.id) || project) !== localSnapshot) this.queue();
            } catch (error) {
                if (!this.active(generation, lease)) return;
                if (isConflict(error)) await this.preserveConflict(project, generation, lease);
                else if (!(error instanceof DOMException && error.name === "AbortError")) this.store.getState().setSyncNotice("项目暂时无法同步，当前修改仍保留在本机。");
            } finally { this.controllers.delete(controller); }
        }
    }

    private async preserveConflict(project: CanvasProject, generation: number, lease: ScopedStoreLease) {
        const controller = this.controller();
        try {
            const server = await this.api.get(project.id, controller.signal);
            if (!this.active(generation, lease)) return;
            const latestLocal = this.store.getState().projects.find((item) => item.id === project.id) || project;
            const now = new Date().toISOString();
            const copy = { ...latestLocal, id: nanoid(), title: `${latestLocal.title}（冲突副本）`, createdAt: now, updatedAt: now };
            const remaining = this.store.getState().projects.filter((item) => item.id !== project.id);
            this.versions.set(server.project.id, server.version);
            this.snapshots.set(server.project.id, serialized(server.project));
            this.store.getState().replaceProjects([copy, server.project, ...remaining]);
            this.store.getState().setSyncNotice("检测到其他位置的更新，已保留一个冲突副本。");
        } catch (error) {
            if (this.active(generation, lease) && !(error instanceof DOMException && error.name === "AbortError")) this.store.getState().setSyncNotice("项目发生版本冲突，本地修改仍保留在本机。");
        } finally { this.controllers.delete(controller); }
    }
}

export const projectSync = new ProjectSync(defaultApi, useCanvasStore);
onStorageScopeCleared(projectSync.stop);
