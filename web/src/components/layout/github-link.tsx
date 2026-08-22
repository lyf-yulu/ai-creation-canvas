import { GithubOutlined } from "@ant-design/icons";

import { cn } from "@/lib/utils";

type GitHubLinkProps = {
    className?: string;
    style?: React.CSSProperties;
};

export function GitHubLink({ className, style }: GitHubLinkProps) {
    return (
        <a
            className={cn("inline-flex size-9 shrink-0 items-center justify-center rounded-full text-muted-foreground transition hover:bg-muted hover:text-foreground dark:text-muted-foreground dark:hover:bg-muted dark:hover:text-white", className)}
            style={style}
            href="https://github.com/basketikun/infinite-canvas"
            target="_blank"
            rel="noreferrer"
            aria-label="GitHub"
            title="GitHub"
        >
            <GithubOutlined className="text-base" />
        </a>
    );
}
