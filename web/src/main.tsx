import React from "react";
import { createRoot } from "react-dom/client";
import "antd/dist/reset.css";
import "streamdown/styles.css";
import "./styles/globals.css";
import { RouterProvider } from "react-router-dom";

import { AppProviders } from "@/components/layout/app-providers";
import { registerBuiltinNodes } from "@/features/nodes/builtins";
import { registerBuiltinWorkflows } from "@/features/workflows/portrait-video";
import { initAnalytics } from "@/lib/analytics";
import { router } from "@/router";

initAnalytics();
registerBuiltinNodes();
registerBuiltinWorkflows();

document.body.style.fontFamily = '"SF Pro Display","SF Pro Text","PingFang SC","Microsoft YaHei","Helvetica Neue",sans-serif';

createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
        <AppProviders>
            <RouterProvider router={router} />
        </AppProviders>
    </React.StrictMode>,
);
