import { createBrowserRouter, Outlet } from "react-router-dom";

import { AuthGate } from "@/components/auth/auth-gate";
import CanvasPage from "@/pages/canvas";
import CanvasProjectPage from "@/pages/canvas/project";
import LoginPage from "@/pages/auth/login";
import NotFound from "@/pages/not-found";

export const router = createBrowserRouter([
    { path: "/login", element: <LoginPage /> },
    {
        element: <AuthGate><Outlet /></AuthGate>,
        children: [
            { path: "/", element: <CanvasPage /> },
            { path: "/canvas", element: <CanvasPage /> },
            { path: "/canvas/:id", element: <CanvasProjectPage /> },
        ],
    },
    { path: "*", element: <NotFound /> },
]);
