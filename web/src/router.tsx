import { createBrowserRouter, Outlet } from "react-router-dom";

import CanvasPage from "@/pages/canvas";
import CanvasProjectPage from "@/pages/canvas/project";
import NotFound from "@/pages/not-found";

export const router = createBrowserRouter([
    {
        element: <Outlet />,
        children: [
            { path: "/", element: <CanvasPage /> },
            { path: "/canvas", element: <CanvasPage /> },
            { path: "/canvas/:id", element: <CanvasProjectPage /> },
        ],
    },
    { path: "*", element: <NotFound /> },
]);
