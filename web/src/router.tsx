import { createBrowserRouter, Outlet } from "react-router-dom";

import { AuthGate, RoleGate } from "@/components/auth/auth-gate";
import { ProductShell } from "@/components/layout/product-shell";
import ActivityAssetsPage from "@/pages/assets/activity";
import CanvasPage from "@/pages/canvas";
import CanvasProjectPage from "@/pages/canvas/project";
import LoginPage from "@/pages/auth/login";
import RegisterPage from "@/pages/auth/register";
import NotFound from "@/pages/not-found";
import TasksPage from "@/pages/tasks";
import AdminModelsPage from "@/pages/admin/models";
import AdminUsersPage from "@/pages/admin/users";
import AdminUsagePage from "@/pages/admin/usage";
import AdminComfyWorkflowsPage from "@/pages/admin/comfy-workflows";
import AdminAssetLibraryPage from "@/pages/admin/asset-library";
import AdminLogsPage from "@/pages/admin/logs";
import UsagePage from "@/pages/usage";

export const router = createBrowserRouter([
    { path: "/login", element: <LoginPage /> },
    { path: "/register", element: <RegisterPage /> },
    {
        element: <AuthGate><ProductShell><Outlet /></ProductShell></AuthGate>,
        children: [
            { path: "/", element: <CanvasPage /> },
            { path: "/canvas", element: <CanvasPage /> },
            { path: "/canvas/:id", element: <CanvasProjectPage /> },
            { path: "/assets", element: <ActivityAssetsPage /> },
            { path: "/tasks", element: <TasksPage /> },
            { path: "/usage", element: <UsagePage /> },
            { path: "/admin/users", element: <RoleGate allowed={["admin"]}><AdminUsersPage /></RoleGate> },
            { path: "/admin/models", element: <RoleGate allowed={["admin"]}><AdminModelsPage /></RoleGate> },
            { path: "/admin/comfy-workflows", element: <RoleGate allowed={["admin"]}><AdminComfyWorkflowsPage /></RoleGate> },
            { path: "/admin/usage", element: <RoleGate allowed={["admin"]}><AdminUsagePage /></RoleGate> },
            { path: "/admin/asset-library", element: <RoleGate allowed={["admin"]}><AdminAssetLibraryPage /></RoleGate> },
            { path: "/admin/logs", element: <RoleGate allowed={["admin"]}><AdminLogsPage /></RoleGate> },
        ],
    },
    { path: "*", element: <NotFound /> },
]);
