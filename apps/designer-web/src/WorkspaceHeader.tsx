import {
    Activity,
    ArrowLeft,
    ChevronDown,
    ExternalLink,
    LogOut,
    Moon,
    MoreHorizontal,
    PanelRight,
    Play,
    Settings2,
    Sun,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { prototypeUrl } from "./api";
import { isEmbedded, logout, projectId, usePlatform } from "./platform";
import type { Run, Session } from "./types";

export function WorkspaceHeader({
    session,
    run,
    theme,
    reviewOpen,
    onTheme,
    onReview,
    onSettings,
    onOpenCanvas,
    canvasOpen,
}: {
    session?: Session;
    run?: Run;
    theme: "light" | "dark";
    reviewOpen: boolean;
    onTheme: () => void;
    onReview: () => void;
    onSettings: () => void;
    onOpenCanvas?: () => void;
    canvasOpen?: boolean;
}) {
    const platform = isEmbedded ? undefined : usePlatform();
    const [openMenu, setOpenMenu] = useState<"more" | "user" | null>(null);
    const actionsRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!openMenu) return;
        const onClose = (event: MouseEvent | KeyboardEvent) => {
            if (event instanceof KeyboardEvent ? event.key === "Escape" : !actionsRef.current?.contains(event.target as Node)) {
                setOpenMenu(null);
            }
        };
        document.addEventListener("mousedown", onClose);
        document.addEventListener("keydown", onClose);
        return () => {
            document.removeEventListener("mousedown", onClose);
            document.removeEventListener("keydown", onClose);
        };
    }, [openMenu]);

    const userInitial = (platform?.user.name || platform?.user.username || "U").trim().charAt(0).toUpperCase();
    const logsHref = `${import.meta.env.BASE_URL}logs.html?${new URLSearchParams({
        ...(projectId ? { project: projectId } : {}),
        ...(session ? { session: session.id } : {}),
        ...(run ? { run: run.id } : {}),
    })}`;

    return (
        <header className="topbar">
            <div className="topbar-title">
                {platform && (
                    <nav className="platform-crumbs" aria-label="Dự án">
                        <a href="/" className="platform-back" title="Về danh sách dự án">
                            <ArrowLeft size={14} />
                            Dự án
                        </a>
                        <span className="platform-sep">/</span>
                        <span className="platform-project" title={platform.project.id}>
                            {platform.project.name}
                        </span>
                        <span className="platform-sep">/</span>
                    </nav>
                )}
                <span
                    className="connection-pulse"
                    title="System connected & streaming active"
                />
                <h2>{session?.title ?? "Agentic Designer Workspace"}</h2>
            </div>

            <div className="topbar-actions" ref={actionsRef}>
                {onOpenCanvas && (
                    <button
                        className="secondary-button compact"
                        onClick={onOpenCanvas}
                        title={canvasOpen ? "Quay lại hội thoại Designer" : "Mở phần thiết kế trong trang này"}
                        type="button"
                    >
                        {canvasOpen ? <ArrowLeft size={15} /> : <ExternalLink size={15} />}
                        {canvasOpen ? "Quay lại chat" : "Trang thiết kế"}
                    </button>
                )}
                {(projectId || session?.project_id) && (
                    <a
                        className="secondary-button compact"
                        style={{ textDecoration: "none" }}
                        href={prototypeUrl(projectId || session?.project_id || undefined)}
                        title="Xem & chạy thử Prototype toàn dự án"
                        target="_blank"
                        rel="noreferrer"
                    >
                        <Play size={14} /> Prototype
                    </a>
                )}

                <button
                    className={`icon-button ${reviewOpen ? "active" : ""}`}
                    onClick={onReview}
                    title={reviewOpen ? "Ẩn bảng Review" : "Hiện bảng Review"}
                    type="button"
                >
                    <PanelRight size={17} />
                </button>

                <div className="topbar-menu-wrap">
                    <button
                        className={`icon-button ${openMenu === "more" ? "active" : ""}`}
                        onClick={() => setOpenMenu((m) => (m === "more" ? null : "more"))}
                        title="Thêm tùy chọn (Logs, Giao diện, Cài đặt)"
                        type="button"
                        aria-haspopup="menu"
                        aria-expanded={openMenu === "more"}
                    >
                        <MoreHorizontal size={17} />
                    </button>
                    {openMenu === "more" && (
                        <div className="topbar-popover" role="menu">
                            <div className="topbar-popover-meta">
                                {run ? `Run ${run.id.slice(0, 7).toUpperCase()} · R${run.revision}` : "Trạng thái: Sẵn sàng"}
                            </div>
                            <a
                                className="topbar-popover-item"
                                href={logsHref}
                                target="_blank"
                                rel="noreferrer"
                                role="menuitem"
                                onClick={() => setOpenMenu(null)}
                            >
                                <Activity size={15} /> Logs chi tiết
                            </a>
                            {!isEmbedded && (
                                <button
                                    className="topbar-popover-item"
                                    type="button"
                                    role="menuitem"
                                    onClick={() => {
                                        onTheme();
                                        setOpenMenu(null);
                                    }}
                                >
                                    {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
                                    {theme === "dark" ? "Chuyển giao diện Sáng" : "Chuyển giao diện Tối"}
                                </button>
                            )}
                            <button
                                className="topbar-popover-item"
                                type="button"
                                role="menuitem"
                                onClick={() => {
                                    setOpenMenu(null);
                                    onSettings();
                                }}
                            >
                                <Settings2 size={15} /> Cài đặt Designer
                            </button>
                        </div>
                    )}
                </div>

                {platform && (
                    <div className="topbar-menu-wrap">
                        <button
                            className={`topbar-user-pill ${openMenu === "user" ? "active" : ""}`}
                            onClick={() => setOpenMenu((m) => (m === "user" ? null : "user"))}
                            title={`${platform.user.name} (${platform.readOnly ? "Chỉ đọc" : platform.project.my_roles.join(" · ")})`}
                            type="button"
                            aria-haspopup="menu"
                            aria-expanded={openMenu === "user"}
                        >
                            <span className="topbar-user-avatar">{userInitial}</span>
                            <span className="topbar-user-name">{platform.user.name}</span>
                            <ChevronDown size={13} />
                        </button>
                        {openMenu === "user" && (
                            <div className="topbar-popover" role="menu">
                                <div className="topbar-popover-meta">
                                    <strong>{platform.user.name}</strong>
                                    <span>
                                        {platform.readOnly ? "Chỉ đọc" : platform.project.my_roles.join(" · ")}
                                    </span>
                                </div>
                                <button
                                    className="topbar-popover-item is-danger"
                                    type="button"
                                    role="menuitem"
                                    onClick={() => {
                                        setOpenMenu(null);
                                        void logout();
                                    }}
                                >
                                    <LogOut size={15} /> Đăng xuất
                                </button>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </header>
    );
}
