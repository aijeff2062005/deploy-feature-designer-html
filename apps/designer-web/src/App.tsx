import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { CanvasApp } from "./canvas/CanvasApp";
import "./canvas/canvas.css";
import { BuildRail } from "./BuildRail";
import { ChatWorkspace } from "./ChatWorkspace";
import { openRunStream } from "./events";
import { ProjectFilesPanel } from "./ProjectFilesPanel";
import { ProjectDesignWikiPanel } from "./ProjectDesignWikiPanel";
import { ReviewPanel } from "./ReviewPanel";
import { SessionRail } from "./SessionRail";
import { SettingsDrawer } from "./SettingsDrawer";
import { useTheme } from "./theme";
import type { Run, RunArtifacts, Session, WorkflowEvent } from "./types";
import { WorkspaceHeader } from "./WorkspaceHeader";
import { sessionWorkflowState } from "./workflowState";

export default function App() {
    const [sessions, setSessions] = useState<Session[]>([]);
    const [activeSession, setActiveSession] = useState<Session>();
    const [runs, setRuns] = useState<Run[]>([]);
    const [activeRun, setActiveRun] = useState<Run>();
    const [events, setEvents] = useState<WorkflowEvent[]>([]);
    const [historyEvents, setHistoryEvents] = useState<WorkflowEvent[]>([]);
    const [artifacts, setArtifacts] = useState<RunArtifacts>();
    const [collapsed, setCollapsed] = useState(false);
    const [settingsOpen, setSettingsOpen] = useState(false);
    const [reviewOpen, setReviewOpen] = useState(false);
    const [rightPanel, setRightPanel] = useState<"workbench" | "files" | "wiki">("workbench");
    const [screensVersion, setScreensVersion] = useState(0);
    const [canvasOpen, setCanvasOpen] = useState(false);
    const [canvasScreenId, setCanvasScreenId] = useState<string>();
    const [notice, setNotice] = useState("");
    const { resolvedTheme, toggleTheme } = useTheme();
    const historyPreviewRunKey = useMemo(
        () => runs.filter((item) => item.id !== activeRun?.id && item.intent === "gather").map((item) => item.id).join("|"),
        [runs, activeRun?.id],
    );

    const applyRun = useCallback((updated: Run) => {
        setActiveRun((current) => (current?.id === updated.id ? updated : current));
        setRuns((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    }, []);

    const applySession = useCallback((updated: Session) => {
        setActiveSession((current) => (current?.id === updated.id ? updated : current));
        setSessions((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    }, []);

    const createStylePreviews = useCallback(async () => {
        if (!activeSession) return;
        const run = await api.createStylePreviews(activeSession.id);
        setRuns((current) => [run, ...current]);
        setActiveRun(run);
        setEvents([]);
        setArtifacts(undefined);
        setReviewOpen(false);
    }, [activeSession]);

    useEffect(() => {
        api.listSessions()
            .then((items) => {
                setSessions(items);
                const requested = new URLSearchParams(window.location.search).get("session");
                const selected = (requested && items.find((item) => item.id === requested)) || items[0];
                if (selected) setActiveSession(selected);
            })
            .catch((error) => setNotice(error.message));
    }, []);

    useEffect(() => {
        if (!activeSession) return;
        api.listRuns(activeSession.id)
            .then((items) => {
                setRuns(items);
                setActiveRun(items[0]);
                setEvents([]);
                setArtifacts(undefined);
            })
            .catch((error) => setNotice(error.message));
    }, [activeSession?.id]);

    useEffect(() => {
        if (!activeSession || !runs.length) {
            setHistoryEvents([]);
            return;
        }
        let alive = true;
        const historyPreviewRunIds = historyPreviewRunKey ? historyPreviewRunKey.split("|") : [];
        Promise.all(historyPreviewRunIds.map((id) => api.eventHistory(id).catch(() => [])))
            .then((groups) => {
                if (!alive) return;
                setHistoryEvents(groups.flat().filter((event) => event.type === "style.previews" || event.type === "style.inherited.preview"));
            });
        return () => { alive = false; };
    }, [activeSession?.id, historyPreviewRunKey]);

    useEffect(() => {
        if (!activeRun) return;
        const source = openRunStream(activeRun.id, (event) => {
            setEvents((current) => (current.some((item) => item.id === event.id) ? current : [...current, event]));
            if (event.type === "screen.saved" || event.type === "screen.finished") {
                setScreensVersion((v) => v + 1);
            }
            api.getRun(activeRun.id).then(applyRun).catch(() => undefined);
            api.getArtifacts(activeRun.id).then(setArtifacts).catch(() => undefined);
        });
        return () => source.close();
    }, [activeRun?.id, applyRun]);

    // Rehydrate persisted conversation events on page reload. The SSE stream
    // is intended for live updates; the gallery itself must also survive
    // reopening a completed run when its original event has already passed.
    useEffect(() => {
        if (!activeRun) return;
        let alive = true;
        api.eventHistory(activeRun.id)
            .then((persisted) => {
                if (!alive) return;
                setEvents((current) => {
                    const byId = new Map(persisted.map((event) => [event.id, event]));
                    for (const event of current) byId.set(event.id, event);
                    return [...byId.values()].sort((left, right) => left.sequence - right.sequence);
                });
            })
            .catch(() => undefined);
        return () => { alive = false; };
    }, [activeRun?.id]);

    useEffect(() => {
        if (!activeRun) {
            setArtifacts(undefined);
            return;
        }
        api.getArtifacts(activeRun.id).then(setArtifacts).catch(() => undefined);
    }, [activeRun?.id, activeRun?.revision, activeRun?.stage]);

    useEffect(() => {
        if (!activeRun || !["queued", "running"].includes(activeRun.status)) return;
        const timer = window.setInterval(() => {
            api.getRun(activeRun.id).then(applyRun).catch(() => undefined);
        }, 3000);
        return () => window.clearInterval(timer);
    }, [activeRun?.id, activeRun?.status, applyRun]);

    const busy = useMemo(() => activeRun?.status === "running" || activeRun?.status === "queued", [activeRun]);
    const workflow = useMemo(() => sessionWorkflowState(activeRun, runs, events), [activeRun, runs, events]);

    const createSession = async () => {
        try {
            const session = await api.createSession(`Design session ${sessions.length + 1}`);
            setSessions((current) => [session, ...current]);
            setActiveSession(session);
            setActiveRun(undefined);
        } catch (error) {
            setNotice((error as Error).message);
        }
    };

    const deleteSession = async (session: Session) => {
        if (!window.confirm(`Delete "${session.title}" and all of its messages, screens and workflow data?`)) return;
        try {
            await api.deleteSession(session.id);
            const remaining = sessions.filter((item) => item.id !== session.id);
            setSessions(remaining);
            if (activeSession?.id === session.id) {
                setActiveSession(remaining[0]);
                setRuns([]);
                setActiveRun(undefined);
                setEvents([]);
                setArtifacts(undefined);
            }
            setNotice("");
        } catch (error) {
            setNotice((error as Error).message);
        }
    };

    const submit = async (value: {
        prompt: string;
        screenName: string;
        platform: string;
        styleName?: string;
        libraryIds: string[];
        assets?: import("./types").ExactAsset[];
    }) => {
        try {
            let session = activeSession;
            if (!session) {
                session = await api.createSession(value.screenName || "Design session");
                setSessions((current) => [session!, ...current]);
                setActiveSession(session);
            }
            const run = await api.sendMessage(session.id, {
                content: value.prompt,
                screen_name_hint: value.screenName,
                platform_hint: value.platform,
                style_hint: value.styleName,
                library_ids: value.libraryIds,
                assets: value.assets,
            });
            setRuns((current) => [run, ...current]);
            setActiveRun(run);
            setEvents([]);
            setArtifacts(undefined);
            setNotice("");
        } catch (error) {
            setNotice((error as Error).message);
        }
    };

    const review = async (decision: "approved" | "changes_requested", feedback = "") => {
        const pendingRun =
            (activeRun?.status === "waiting_review" &&
            (activeRun.stage === "review_spec" || activeRun.stage === "review_final"))
                ? activeRun
                : runs.find(
                      (item) =>
                          (item.stage === "review_spec" || item.stage === "review_final") &&
                          item.status === "waiting_review",
                  ) ?? activeRun;
        if (!pendingRun) return;
        try {
            const checkpoint = pendingRun.stage === "review_final" ? "final" : "specification";
            await api.review(pendingRun.id, pendingRun.revision, checkpoint, decision, feedback);
            const updated = await api.getRun(pendingRun.id);
            setRuns((items) => items.map((item) => (item.id === updated.id ? updated : item)));
            setActiveRun(updated);
        } catch (error) {
            setNotice((error as Error).message);
            throw error;
        }
    };

    const openCanvas = activeSession
        ? (screenId?: string) => {
              setCanvasScreenId(screenId);
              setReviewOpen(false);
              setCanvasOpen(true);
          }
        : undefined;

    return (
        <div className={`app-shell ${collapsed ? "rail-collapsed" : ""} ${reviewOpen ? "" : "review-closed"} ${canvasOpen ? "canvas-open" : ""}`}>
            <SessionRail
                sessions={sessions}
                activeId={activeSession?.id}
                activeSession={activeSession}
                collapsed={collapsed}
                onToggle={() => setCollapsed(!collapsed)}
                onSelect={setActiveSession}
                onDelete={deleteSession}
                onCreate={createSession}
                onSettings={() => setSettingsOpen(true)}
            />

            <WorkspaceHeader
                session={activeSession}
                run={activeRun}
                theme={resolvedTheme}
                reviewOpen={reviewOpen}
                onTheme={toggleTheme}
                onReview={() => setReviewOpen(!reviewOpen)}
                onSettings={() => setSettingsOpen(true)}
                onOpenCanvas={canvasOpen ? () => setCanvasOpen(false) : openCanvas}
                canvasOpen={canvasOpen}
            />

            <section className="timeline-slot">
                <BuildRail
                    stage={workflow?.stage}
                    status={workflow?.status}
                    mode={workflow?.run.intent === "design" ? "workspace" : "project"}
                />
            </section>

            {canvasOpen && <CanvasApp key={activeSession?.id} sessionId={activeSession?.id} onBack={() => setCanvasOpen(false)} initialScreenId={canvasScreenId} />}

            {!canvasOpen && <ChatWorkspace
                run={activeRun}
                runs={runs}
                events={events}
                historyEvents={historyEvents}
                busy={busy}
                onSubmit={submit}
                onExploreProjectStyles={createStylePreviews}
                onReview={review}
                sessionId={activeSession?.id}
                onOpenCanvas={openCanvas}
                onToggleSidePanel={() => setReviewOpen((prev) => !prev)}
                onRetry={async () => {
                    if (!activeRun) return;
                    try {
                        await api.retry(activeRun.id);
                        applyRun(await api.getRun(activeRun.id));
                    } catch (error) {
                        setNotice((error as Error).message);
                    }
                }}
                onCancel={async () => {
                    if (!activeRun) return;
                    try {
                        await api.cancel(activeRun.id);
                        applyRun(await api.getRun(activeRun.id));
                    } catch (error) {
                        setNotice((error as Error).message);
                    }
                }}
            />}

            {!canvasOpen && reviewOpen && rightPanel === "workbench" && (
                <ReviewPanel
                    run={activeRun}
                    sessionId={activeSession?.id}
                    artifacts={artifacts}
                    screensVersion={screensVersion}
                    onReview={review}
                    onShowFiles={() => setRightPanel("files")}
                    onOpenCanvas={openCanvas ?? (() => undefined)}
                />
            )}
            {!canvasOpen && reviewOpen && rightPanel === "files" && (
                <ProjectFilesPanel onShowWorkbench={() => setRightPanel("workbench")} onShowWiki={() => setRightPanel("wiki")} />
            )}
            {!canvasOpen && reviewOpen && rightPanel === "wiki" && (
                <ProjectDesignWikiPanel
                    projectId={activeSession?.project_id ?? undefined}
                    sessionId={activeSession?.id}
                    onShowFiles={() => setRightPanel("files")}
                    onShowWorkbench={() => setRightPanel("workbench")}
                    onCreateStylePreviews={createStylePreviews}
                />
            )}

            {notice && (
                <button className="notice" onClick={() => setNotice("")} type="button">
                    {notice} (Click to dismiss)
                </button>
            )}

            {settingsOpen && (
                <SettingsDrawer
                    session={activeSession}
                    onClose={() => setSettingsOpen(false)}
                    onSessionChange={applySession}
                />
            )}
        </div>
    );
}
