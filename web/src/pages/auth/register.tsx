import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { registerLocal } from "@/api/auth";
import { ApiRequestError } from "@/api/client";


export default function RegisterPage() {
    const [username, setUsername] = useState("");
    const [displayName, setDisplayName] = useState("");
    const [password, setPassword] = useState("");
    const [confirm, setConfirm] = useState("");
    const [message, setMessage] = useState("");
    const [busy, setBusy] = useState(false);
    const [submitted, setSubmitted] = useState(false);

    const submit = async (event: FormEvent) => {
        event.preventDefault();
        setMessage("");
        if (password !== confirm) {
            setMessage("两次输入的密码不一致。");
            return;
        }
        setBusy(true);
        try {
            await registerLocal(username, displayName, password);
            setSubmitted(true);
        } catch (error) {
            setMessage(error instanceof ApiRequestError && error.code === "USERNAME_TAKEN" ? "用户名已被占用。" : "注册失败，请稍后再试。");
        } finally {
            setBusy(false);
        }
    };

    const formReady = username.trim() !== "" && displayName.trim() !== "" && password.length >= 12 && confirm.length >= 12;

    return (
        <main className="flex min-h-dvh items-center justify-center bg-[var(--c-bg)] px-5 text-[var(--c-text)]">
            <section className="w-full max-w-sm rounded-2xl border border-[var(--c-border)] bg-[var(--c-panel)] p-7 shadow-2xl">
                {submitted ? <div>
                    <p className="text-xs tracking-[0.25em] text-[var(--c-accent)]">AI CREATION CANVAS</p>
                    <h1 className="mt-3 text-2xl font-semibold">注册已提交</h1>
                    <p className="mt-2 text-sm text-[var(--c-text-3)]">注册已提交，请等待管理员审核后登录。</p>
                    <p className="mt-6 text-center text-sm text-[var(--c-text-3)]"><Link to="/login" className="text-[var(--c-accent)] hover:underline">返回登录</Link></p>
                </div> : <div>
                    <p className="text-xs tracking-[0.25em] text-[var(--c-accent)]">AI CREATION CANVAS</p>
                    <h1 className="mt-3 text-2xl font-semibold">注册新账号</h1>
                    <p className="mt-2 text-sm text-[var(--c-text-3)]">注册后需等待管理员审核通过，才能登录使用。</p>
                    <form className="mt-6 space-y-4" onSubmit={(event) => void submit(event)}>
                        <label className="block text-sm">用户名<input aria-label="用户名" autoComplete="username" className="mt-1 w-full rounded-lg border border-[var(--c-border)] bg-[var(--c-panel)] px-3 py-2 text-[var(--c-text)] outline-none focus:border-[var(--c-accent)]" value={username} onChange={(event) => setUsername(event.target.value)} /></label>
                        <label className="block text-sm">显示名称<input aria-label="显示名称" className="mt-1 w-full rounded-lg border border-[var(--c-border)] bg-[var(--c-panel)] px-3 py-2 text-[var(--c-text)] outline-none focus:border-[var(--c-accent)]" value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>
                        <label className="block text-sm">密码<input aria-label="密码" type="password" autoComplete="new-password" minLength={12} className="mt-1 w-full rounded-lg border border-[var(--c-border)] bg-[var(--c-panel)] px-3 py-2 text-[var(--c-text)] outline-none focus:border-[var(--c-accent)]" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
                        <label className="block text-sm">确认密码<input aria-label="确认密码" type="password" autoComplete="new-password" minLength={12} className="mt-1 w-full rounded-lg border border-[var(--c-border)] bg-[var(--c-panel)] px-3 py-2 text-[var(--c-text)] outline-none focus:border-[var(--c-accent)]" value={confirm} onChange={(event) => setConfirm(event.target.value)} /></label>
                        <p className="text-xs text-[var(--c-text-3)]">密码至少 12 个字符。</p>
                        {message ? <p role="alert" className="text-sm text-[var(--c-warning)]">{message}</p> : null}
                        <button type="submit" disabled={busy || !formReady} className="w-full rounded-lg bg-[var(--c-accent)] px-4 py-2.5 font-semibold text-[var(--c-accent-fg)] disabled:opacity-45">{busy ? "提交中…" : "提交注册"}</button>
                    </form>
                    <p className="mt-5 text-center text-sm text-[var(--c-text-3)]">已有账号？<Link to="/login" className="text-[var(--c-accent)] hover:underline">登录</Link></p>
                </div>}
            </section>
        </main>
    );
}
