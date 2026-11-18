import { BookOpen, Palette, RefreshCw, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, type ProjectWikiResponse } from "./api";

type Props = {
    projectId?: string;
    sessionId?: string;
    onShowFiles: () => void;
    onShowWorkbench: () => void;
    onCreateStylePreviews: () => Promise<void>;
};

export function ProjectDesignWikiPanel({ projectId, sessionId, onShowFiles, onShowWorkbench, onCreateStylePreviews }: Props) {
    const [wiki, setWiki] = useState<ProjectWikiResponse>();
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);

    const load = useCallback(() => {
        if (!projectId) return;
        setError("");
        api.getProjectWiki(projectId).then(setWiki).catch((reason: Error) => setError(reason.message));
    }, [projectId]);

    useEffect(load, [load]);

    const restore = async (version: number) => {
        if (!projectId) return;
        setBusy(true);
        setError("");
        try {
            await api.restoreProjectWiki(projectId, version);
            load();
        } catch (reason) {
            setError((reason as Error).message);
        } finally {
            setBusy(false);
        }
    };

    const explore = async () => {
        setBusy(true);
        setError("");
        try {
            await onCreateStylePreviews();
        } catch (reason) {
            setError((reason as Error).message);
        } finally {
            setBusy(false);
        }
    };

    const current = wiki?.current;
    const baseline = current?.baseline ?? {};
    const contract = (baseline.contract as Record<string, unknown> | undefined) ?? {};
    const palette = (contract.palette as Record<string, string> | undefined) ?? {};

    return (
        <aside className="review-panel project-wiki-panel" aria-label="Project Design Wiki">
            <header>
                <div>
                    <span className="eyebrow">Project Wiki</span>
                    <h2>Chuẩn thiết kế dùng chung</h2>
                </div>
                <div className="files-header-actions">
                    <button className="icon-button" type="button" title="Tải lại" onClick={load}><RefreshCw size={15} /></button>
                    <button className="panel-switch" type="button" onClick={onShowFiles}>Files</button>
                    <button className="panel-switch" type="button" onClick={onShowWorkbench}>Workbench</button>
                </div>
            </header>

            <div className="project-wiki-content">
                {error && <div className="files-empty files-error">{error}</div>}
                {!wiki && !error && <div className="files-empty">Đang tải Wiki…</div>}
                {wiki && !current && (
                    <div className="project-wiki-empty">
                        <BookOpen size={25} />
                        <strong>Project chưa có chuẩn thiết kế được duyệt</strong>
                        <span>Chuẩn đầu tiên sẽ được lưu sau khi user approve thiết kế cuối của một feature.</span>
                    </div>
                )}
                {current && (
                    <>
                        <section className="project-wiki-card">
                            <div className="project-wiki-card-title">
                                <span className="project-wiki-icon"><Palette size={17} /></span>
                                <div>
                                    <strong>{String(baseline.style_name ?? contract.name ?? "Design System")}</strong>
                                    <span>Đang dùng · v{current.version}</span>
                                </div>
                            </div>
                            <p>{String(baseline.style_direction ?? contract.direction ?? contract.tagline ?? "")}</p>
                            <dl>
                                <div><dt>Font tiêu đề</dt><dd>{String(contract.heading_font ?? "—")}</dd></div>
                                <div><dt>Font nội dung</dt><dd>{String(contract.body_font ?? "—")}</dd></div>
                                <div><dt>Bo góc</dt><dd>{String(contract.radius ?? "—")}</dd></div>
                                <div><dt>Mật độ</dt><dd>{String(contract.density ?? "—")}</dd></div>
                            </dl>
                            {Object.keys(palette).length > 0 && (
                                <div className="project-wiki-palette" aria-label="Bảng màu">
                                    {Object.entries(palette).map(([name, color]) => (
                                        <span key={name} title={`${name}: ${color}`}>
                                            <i style={{ background: color }} />{name}
                                        </span>
                                    ))}
                                </div>
                            )}
                            <p className="project-wiki-note">
                                Feature mới trong project này tự kế thừa chuẩn trên. BA package vẫn quyết định nội dung và trạng thái riêng của từng feature.
                            </p>
                        </section>

                        <section className="project-wiki-action">
                            <strong>Muốn hướng khác cho feature đang mở?</strong>
                            <p>Tạo ba hướng để chọn. Wiki mặc định chỉ đổi khi thiết kế mới được approve.</p>
                            <button type="button" className="project-wiki-primary" disabled={!sessionId || busy} onClick={explore}>
                                <Palette size={15} /> Tạo 3 hướng khác cho feature này
                            </button>
                        </section>

                        <section className="project-wiki-history">
                            <h3>Lịch sử chuẩn thiết kế</h3>
                            {(wiki.versions ?? []).map((item) => {
                                const itemBaseline = item.baseline ?? {};
                                const itemContract = (itemBaseline.contract as Record<string, unknown> | undefined) ?? {};
                                const isCurrent = item.version === current.version;
                                return (
                                    <article className="project-wiki-version" key={item.version}>
                                        <div>
                                            <strong>v{item.version} · {String(itemBaseline.style_name ?? itemContract.name ?? "Design System")}</strong>
                                            <span>{new Date(item.updated_at).toLocaleString()}</span>
                                        </div>
                                        {isCurrent ? <span className="file-tag">Đang dùng</span> : (
                                            <button type="button" className="icon-button" title={`Khôi phục v${item.version}`} disabled={busy} onClick={() => restore(item.version)}>
                                                <RotateCcw size={15} />
                                            </button>
                                        )}
                                    </article>
                                );
                            })}
                        </section>
                    </>
                )}
            </div>
        </aside>
    );
}
