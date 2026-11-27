import {
    ArrowDown,
    ArrowLeft,
    ArrowRight,
    Columns3,
    ExternalLink,
    GitBranch,
    Maximize2,
    Minus,
    Moon,
    PanelLeftClose,
    PanelLeftOpen,
    Play,
    Plus,
    RotateCcw,
    Rows3,
    Sparkles,
    Sun,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, canvasUrl, type ProjectPrototype, type PrototypeEdge, type PrototypeScreen } from "../api";
import { projectId as platformProjectId } from "../platform";
import { useTheme } from "../theme";

type Mode = "player" | "map";
type MapView = "swimlanes" | "board";

type EdgeCoord = {
    id: string;
    d: string;
    kind: PrototypeEdge["kind"];
    fromKey: string;
    toKey: string;
    isBack: boolean;
};

function resolvedProjectId(): string {
    const q = new URLSearchParams(window.location.search).get("project") || "";
    return (q || platformProjectId || "").trim();
}

export function PrototypeApp() {
    const pid = useMemo(() => resolvedProjectId(), []);
    const { resolvedTheme, toggleTheme } = useTheme();
    const [data, setData] = useState<ProjectPrototype | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [mode, setMode] = useState<Mode>("player");
    const [mapView, setMapView] = useState<MapView>("swimlanes");
    const [sidebarOpen, setSidebarOpen] = useState(true);

    // Interactive Player state
    const [activeKey, setActiveKey] = useState<string>("");
    const [history, setHistory] = useState<string[]>([]);
    const [hotspotsOn, setHotspotsOn] = useState(true);
    const [activeState, setActiveState] = useState("");
    const [docCache, setDocCache] = useState<Record<string, string>>({});
    const [iframeDoc, setIframeDoc] = useState<string>("");
    const iframeRef = useRef<HTMLIFrameElement>(null);

    // Flow Map shared + board pan/zoom state
    const [hoveredKey, setHoveredKey] = useState<string | null>(null);
    const [swimZoom, setSwimZoom] = useState(1);
    const [boardZoom, setBoardZoom] = useState(0.85);
    const [pan, setPan] = useState({ x: 28, y: 56 });
    const [panning, setPanning] = useState(false);
    const panOrigin = useRef({ x: 0, y: 0, px: 0, py: 0 });
    const boardStageRef = useRef<HTMLDivElement>(null);
    const boardCardRefs = useRef<Record<string, HTMLDivElement | null>>({});
    const [edgeCoords, setEdgeCoords] = useState<EdgeCoord[]>([]);

    useEffect(() => {
        if (!pid) {
            setLoading(false);
            setError("Thiếu project_id. Hãy mở trang này từ Workspace của dự án.");
            return;
        }
        setLoading(true);
        api.projectPrototype(pid)
            .then((res) => {
                setData(res);
                setActiveKey(res.start_screen_key || res.screens[0]?.key || "");
            })
            .catch((err) => setError((err as Error).message || "Không tải được prototype dự án"))
            .finally(() => setLoading(false));
    }, [pid]);

    const screenByKey = useMemo(() => {
        const map = new Map<string, PrototypeScreen>();
        for (const sc of data?.screens || []) map.set(sc.key, sc);
        return map;
    }, [data]);

    const featureByIndex = useMemo(() => {
        const map = new Map<number, ProjectPrototype["features"][number]>();
        for (const f of data?.features || []) map.set(f.index, f);
        return map;
    }, [data]);

    const activeScreen = activeKey ? screenByKey.get(activeKey) : undefined;

    const navigateTo = useCallback(
        (targetKey: string, pushHistory = true) => {
            if (!targetKey || !screenByKey.has(targetKey)) return;
            setActiveState("");
            setActiveKey((prev) => {
                if (pushHistory && prev && prev !== targetKey) {
                    setHistory((h) => [...h.slice(-29), prev]);
                }
                return targetKey;
            });
        },
        [screenByKey],
    );

    const goBack = useCallback(() => {
        setHistory((h) => {
            if (!h.length) return h;
            const prev = h[h.length - 1];
            setActiveState("");
            setActiveKey(prev);
            return h.slice(0, -1);
        });
    }, []);

    const resetFlow = useCallback(() => {
        if (!data?.start_screen_key) return;
        setHistory([]);
        setActiveState("");
        setActiveKey(data.start_screen_key);
    }, [data?.start_screen_key]);

    // Load screen HTML document into cache when activeScreen changes.
    useEffect(() => {
        if (!activeScreen) {
            setIframeDoc("");
            return;
        }
        const cached = docCache[activeScreen.key];
        if (cached) {
            setIframeDoc(cached);
            return;
        }
        let alive = true;
        api.screenDocument(activeScreen.session_id, activeScreen.id)
            .then((html) => {
                if (!alive) return;
                setDocCache((prev) => ({ ...prev, [activeScreen.key]: html }));
                setIframeDoc(html);
            })
            .catch((err) => {
                if (alive) setError((err as Error).message || "Không tải được HTML màn hình");
            });
        return () => {
            alive = false;
        };
    }, [activeScreen, docCache]);

    const syncIframeFlags = useCallback(() => {
        const win = iframeRef.current?.contentWindow;
        if (!win) return;
        win.postMessage({ type: "designer:hotspots", on: hotspotsOn }, "*");
        win.postMessage({ type: "designer:state", state: activeState }, "*");
    }, [hotspotsOn, activeState]);

    useEffect(() => {
        syncIframeFlags();
    }, [syncIframeFlags, iframeDoc]);

    // Listen to postMessage events from the sandboxed screen iframe.
    useEffect(() => {
        const onMessage = (event: MessageEvent) => {
            if (event.source !== iframeRef.current?.contentWindow) return;
            const msg = event.data;
            if (!msg || msg.source !== "designer-screen" || !data || !activeScreen) return;

            if (msg.type === "ready") {
                syncIframeFlags();
                return;
            }

            const feat = featureByIndex.get(activeScreen.feature_index);
            const handoffEdge = data.edges.find(
                (e) => e.from_key === activeScreen.key && e.kind === "handoff",
            );

            if (msg.type === "goto" && typeof msg.screen === "string") {
                const targetSlug = msg.screen.trim();
                const sameSession = data.screens.find(
                    (s) => s.session_id === activeScreen.session_id && s.id === targetSlug,
                );
                const crossSession = data.screens.find((s) => s.id === targetSlug);
                const resolved = sameSession || crossSession;

                if (msg.isPrimary && !msg.isBack && handoffEdge && resolved && feat) {
                    const currIdx = feat.screen_keys.indexOf(activeScreen.key);
                    const targetIdx = feat.screen_keys.indexOf(resolved.key);
                    if (resolved.session_id === activeScreen.session_id && targetIdx >= 0 && targetIdx <= currIdx) {
                        navigateTo(handoffEdge.to_key, true);
                        return;
                    }
                }
                if (resolved) navigateTo(resolved.key, true);
                return;
            }

            if (msg.type === "primary_cta") {
                if (handoffEdge) {
                    navigateTo(handoffEdge.to_key, true);
                    return;
                }
                if (feat) {
                    const currIdx = feat.screen_keys.indexOf(activeScreen.key);
                    const nextKey = currIdx >= 0 ? feat.screen_keys[currIdx + 1] : undefined;
                    if (nextKey) navigateTo(nextKey, true);
                }
            }
        };
        window.addEventListener("message", onMessage);
        return () => window.removeEventListener("message", onMessage);
    }, [data, activeScreen, featureByIndex, navigateTo, syncIframeFlags]);

    const outgoingEdges = useMemo(() => {
        if (!data || !activeScreen) return [];
        return data.edges.filter((e) => e.from_key === activeScreen.key);
    }, [data, activeScreen]);

    const { hoveredTargets, hoveredSources } = useMemo(() => {
        const targets = new Set<string>();
        const sources = new Set<string>();
        if (!data || !hoveredKey) return { hoveredTargets: targets, hoveredSources: sources };
        for (const e of data.edges) {
            if (e.from_key === hoveredKey && e.direction !== "back") targets.add(e.to_key);
            if (e.to_key === hoveredKey && e.direction !== "back") sources.add(e.from_key);
        }
        return { hoveredTargets: targets, hoveredSources: sources };
    }, [data, hoveredKey]);

    const outgoingCountByKey = useMemo(() => {
        const counts = new Map<string, number>();
        for (const e of data?.edges || []) {
            if (e.direction !== "back") counts.set(e.from_key, (counts.get(e.from_key) || 0) + 1);
        }
        return counts;
    }, [data]);

    // Measure card DOM coordinates for the Board (node-wire) view.
    const measureBoardEdges = useCallback(() => {
        if (mode !== "map" || mapView !== "board" || !data || !boardStageRef.current) return;
        const stage = boardStageRef.current;
        const stageRect = stage.getBoundingClientRect();
        const next: EdgeCoord[] = [];
        data.edges.forEach((edge, idx) => {
            const fromEl = boardCardRefs.current[edge.from_key];
            const toEl = boardCardRefs.current[edge.to_key];
            if (!fromEl || !toEl) return;
            const r1 = fromEl.getBoundingClientRect();
            const r2 = toEl.getBoundingClientRect();
            const x1 = (r1.right - stageRect.left) / boardZoom;
            const y1 = (r1.top + r1.height / 2 - stageRect.top) / boardZoom;
            const x2 = (r2.left - stageRect.left) / boardZoom;
            const y2 = (r2.top + r2.height / 2 - stageRect.top) / boardZoom;
            const sameColumn = Math.abs(r1.left - r2.left) < 40;
            const d = sameColumn
                ? `M ${x1} ${y1} C ${x1 + 52} ${y1}, ${x1 + 52} ${y2}, ${x1} ${y2}`
                : `M ${x1} ${y1} C ${x1 + Math.max(48, Math.abs(x2 - x1) * 0.45)} ${y1}, ${x2 - Math.max(48, Math.abs(x2 - x1) * 0.45)} ${y2}, ${x2} ${y2}`;
            next.push({
                id: `${idx}:${edge.from_key}->${edge.to_key}`,
                d,
                kind: edge.kind,
                fromKey: edge.from_key,
                toKey: edge.to_key,
                isBack: edge.direction === "back",
            });
        });
        setEdgeCoords(next);
    }, [mode, mapView, data, boardZoom]);

    useEffect(() => {
        if (mode !== "map" || mapView !== "board") return;
        const t = setTimeout(measureBoardEdges, 80);
        window.addEventListener("resize", measureBoardEdges);
        return () => {
            clearTimeout(t);
            window.removeEventListener("resize", measureBoardEdges);
        };
    }, [mode, mapView, measureBoardEdges, data]);

    const onBoardWheel = (e: React.WheelEvent) => {
        e.preventDefault();
        const factor = e.deltaY < 0 ? 1.08 : 0.92;
        setBoardZoom((z) => Math.min(1.5, Math.max(0.35, Number((z * factor).toFixed(2)))));
    };

    const onBoardPointerDown = (e: React.PointerEvent) => {
        if ((e.target as HTMLElement).closest(".proto-map-card, .proto-map-switch, button, a")) return;
        setPanning(true);
        panOrigin.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
    };

    const onBoardPointerMove = (e: React.PointerEvent) => {
        if (!panning) return;
        setPan({
            x: panOrigin.current.px + (e.clientX - panOrigin.current.x),
            y: panOrigin.current.py + (e.clientY - panOrigin.current.y),
        });
    };

    const onBoardPointerUp = () => setPanning(false);

    return (
        <div className="proto-shell">
            <header className="proto-topbar">
                <div className="proto-topbar-left">
                    <a
                        className="proto-btn"
                        href={`${import.meta.env.BASE_URL}${pid ? `?project=${encodeURIComponent(pid)}` : ""}`}
                    >
                        <ArrowLeft size={14} /> Designer Studio
                    </a>
                    <div className="proto-title-wrap">
                        <h1>Prototype toàn dự án</h1>
                        {data && (
                            <span className="proto-badge">
                                {data.features.length} tính năng · {data.screens.length} màn hình
                            </span>
                        )}
                    </div>
                </div>
                <div className="proto-topbar-right">
                    <div className="proto-mode-tabs" role="tablist">
                        <button
                            type="button"
                            className={`proto-mode-btn ${mode === "player" ? "is-active" : ""}`}
                            onClick={() => setMode("player")}
                        >
                            <Play size={13} /> Chạy thử (Player)
                        </button>
                        <button
                            type="button"
                            className={`proto-mode-btn ${mode === "map" ? "is-active" : ""}`}
                            onClick={() => setMode("map")}
                        >
                            <GitBranch size={13} /> Bản đồ luồng (Flow Map)
                        </button>
                    </div>
                    <button
                        type="button"
                        className="proto-btn"
                        onClick={toggleTheme}
                        title={`Đổi giao diện ${resolvedTheme === "dark" ? "Sáng" : "Tối"}`}
                    >
                        {resolvedTheme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
                    </button>
                </div>
            </header>

            {loading ? (
                <div className="proto-empty">Đang tổng hợp toàn bộ màn hình của dự án…</div>
            ) : error ? (
                <div className="proto-empty">{error}</div>
            ) : !data || data.screens.length === 0 ? (
                <div className="proto-empty">Dự án chưa có màn hình nào được thiết kế.</div>
            ) : (
                <main className="proto-main">
                    {mode === "player" && (
                        <>
                            {sidebarOpen && (
                                <aside className="proto-sidebar">
                                    {data.features.map((feat) => (
                                        <div className="proto-feature-group" key={feat.session_id}>
                                            <div className="proto-feature-title" title={feat.title}>
                                                <span className="proto-feature-num">{feat.index}</span>
                                                <span className="proto-feature-name">{feat.title}</span>
                                            </div>
                                            {feat.screen_keys.map((key, sIdx) => {
                                                const sc = screenByKey.get(key);
                                                if (!sc) return null;
                                                return (
                                                    <button
                                                        type="button"
                                                        key={key}
                                                        className={`proto-screen-btn ${key === activeKey ? "is-active" : ""}`}
                                                        onClick={() => navigateTo(key, true)}
                                                    >
                                                        <strong>
                                                            {sIdx + 1}. {sc.name}
                                                        </strong>
                                                        <span>{sc.id}</span>
                                                    </button>
                                                );
                                            })}
                                        </div>
                                    ))}
                                </aside>
                            )}

                            <section className="proto-player-stage">
                                <div className="proto-player-bar">
                                    <div className="proto-player-controls">
                                        <button
                                            type="button"
                                            className="proto-btn"
                                            onClick={() => setSidebarOpen((v) => !v)}
                                            title="Ẩn/hiện danh sách màn hình"
                                        >
                                            {sidebarOpen ? <PanelLeftClose size={14} /> : <PanelLeftOpen size={14} />}
                                        </button>
                                        <button
                                            type="button"
                                            className="proto-btn"
                                            onClick={goBack}
                                            disabled={history.length === 0}
                                        >
                                            <ArrowLeft size={14} /> Quay lại
                                        </button>
                                        <button type="button" className="proto-btn" onClick={resetFlow}>
                                            <RotateCcw size={13} /> Về màn đầu
                                        </button>
                                        {activeScreen && (
                                            <div className="proto-breadcrumb">
                                                <span>Feature {activeScreen.feature_index}</span>
                                                <span>›</span>
                                                <strong>{activeScreen.name}</strong>
                                            </div>
                                        )}
                                    </div>

                                    <div className="proto-player-controls">
                                        <button
                                            type="button"
                                            className={`proto-btn ${hotspotsOn ? "is-active" : ""}`}
                                            onClick={() => setHotspotsOn((v) => !v)}
                                            title="Làm nổi bật các nút bấm chuyển màn hình"
                                        >
                                            <Sparkles size={13} /> Gợi ý vùng bấm
                                        </button>
                                        {activeScreen && activeScreen.states.length > 0 && (
                                            <select
                                                className="proto-select"
                                                value={activeState}
                                                onChange={(e) => setActiveState(e.target.value)}
                                                aria-label="Trạng thái màn hình"
                                            >
                                                <option value="">Trạng thái: Mặc định</option>
                                                {activeScreen.states.map((st) => (
                                                    <option key={st} value={st}>
                                                        {st}
                                                    </option>
                                                ))}
                                            </select>
                                        )}
                                        {activeScreen && (
                                            <a
                                                className="proto-btn"
                                                href={canvasUrl(activeScreen.session_id)}
                                                target="_blank"
                                                rel="noreferrer"
                                            >
                                                <ExternalLink size={13} /> Mở Canvas
                                            </a>
                                        )}
                                    </div>
                                </div>

                                <div className="proto-device-viewport">
                                    {activeScreen && (
                                        <div
                                            className={`proto-device-frame ${activeScreen.platform === "desktop" ? "is-desktop" : ""}`}
                                            style={{ width: Math.min(activeScreen.width || 390, 1180) + 24 }}
                                        >
                                            <iframe
                                                ref={iframeRef}
                                                className="proto-device-iframe"
                                                title={activeScreen.name}
                                                sandbox="allow-scripts"
                                                srcDoc={iframeDoc}
                                            />
                                        </div>
                                    )}
                                </div>

                                {outgoingEdges.length > 0 && (
                                    <div className="proto-next-strip">
                                        <span className="proto-next-label">Đi tiếp:</span>
                                        {outgoingEdges.map((edge, idx) => {
                                            const target = screenByKey.get(edge.to_key);
                                            if (!target) return null;
                                            const cls =
                                                edge.kind === "handoff"
                                                    ? "is-handoff"
                                                    : edge.kind === "cross_feature"
                                                      ? "is-cross"
                                                      : "";
                                            return (
                                                <button
                                                    type="button"
                                                    key={`${edge.to_key}-${idx}`}
                                                    className={`proto-next-pill ${cls}`}
                                                    onClick={() => navigateTo(target.key, true)}
                                                >
                                                    <ArrowRight size={12} />
                                                    {edge.kind === "handoff"
                                                        ? `Feature ${target.feature_index}: ${target.name}`
                                                        : target.name}
                                                </button>
                                            );
                                        })}
                                    </div>
                                )}
                            </section>
                        </>
                    )}

                    {mode === "map" && (
                        <>
                            <div className="proto-map-switch" role="tablist" aria-label="Kiểu hiển thị bản đồ luồng">
                                <button
                                    type="button"
                                    className={`proto-mode-btn ${mapView === "swimlanes" ? "is-active" : ""}`}
                                    onClick={() => setMapView("swimlanes")}
                                >
                                    <Rows3 size={13} /> Hàng ngang
                                </button>
                                <button
                                    type="button"
                                    className={`proto-mode-btn ${mapView === "board" ? "is-active" : ""}`}
                                    onClick={() => setMapView("board")}
                                >
                                    <Columns3 size={13} /> Sơ đồ nối dây
                                </button>
                            </div>

                            {mapView === "swimlanes" ? (
                                <section className="proto-map-viewport">
                                    <div className="proto-map-stage" style={{ zoom: swimZoom }}>
                                        <div className="proto-map-swimlanes">
                                            {data.features.map((feat, fIdx) => {
                                                const nextFeat = data.features[fIdx + 1];
                                                const exitSc = feat.exit_screen_key ? screenByKey.get(feat.exit_screen_key) : undefined;
                                                const nextEntrySc = nextFeat?.entry_screen_key
                                                    ? screenByKey.get(nextFeat.entry_screen_key)
                                                    : undefined;

                                                return (
                                                    <div className="proto-swimlane-wrap" key={feat.session_id}>
                                                        <div className="proto-swimlane">
                                                            <div className="proto-swimlane-head">
                                                                <span className="proto-feature-num">{feat.index}</span>
                                                                <h3>{feat.title}</h3>
                                                                <span className="proto-swimlane-count">
                                                                    {feat.screen_keys.length} màn hình
                                                                </span>
                                                            </div>

                                                            <div className="proto-swimlane-row">
                                                                {feat.screen_keys.map((key, sIdx) => {
                                                                    const sc = screenByKey.get(key);
                                                                    if (!sc) return null;
                                                                    const isHovered = hoveredKey === key;
                                                                    const isTarget = hoveredTargets.has(key);
                                                                    const isSource = hoveredSources.has(key);
                                                                    const isDimmed = Boolean(hoveredKey && !isHovered && !isTarget && !isSource);
                                                                    const outCount = outgoingCountByKey.get(key) || 0;
                                                                    return (
                                                                        <div className="proto-step-slot" key={key}>
                                                                            <div
                                                                                className={`proto-map-card ${key === activeKey ? "is-active" : ""} ${isHovered ? "is-hovered" : ""} ${isTarget ? "is-target" : ""} ${isDimmed ? "is-dimmed" : ""}`}
                                                                                onMouseEnter={() => setHoveredKey(key)}
                                                                                onMouseLeave={() => setHoveredKey(null)}
                                                                                onClick={() => {
                                                                                    navigateTo(key, true);
                                                                                    setMode("player");
                                                                                }}
                                                                            >
                                                                                <div className="proto-card-badges">
                                                                                    <span className="proto-card-step">Bước {sIdx + 1}</span>
                                                                                    {isTarget && <span className="proto-target-badge">Đích đến</span>}
                                                                                    {!isTarget && outCount > 0 && (
                                                                                        <span className="proto-link-badge">{outCount} lối đi →</span>
                                                                                    )}
                                                                                </div>
                                                                                <img
                                                                                    className="proto-map-thumb"
                                                                                    src={api.screenshotUrl(sc.session_id, sc.id)}
                                                                                    alt={sc.name}
                                                                                    loading="lazy"
                                                                                />
                                                                                <div className="proto-map-card-meta">
                                                                                    <strong>{sc.name}</strong>
                                                                                    <span>{sc.id}</span>
                                                                                </div>
                                                                            </div>
                                                                            {sIdx < feat.screen_keys.length - 1 && (
                                                                                <div className="proto-step-arrow" aria-hidden="true">
                                                                                    <ArrowRight size={18} />
                                                                                </div>
                                                                            )}
                                                                        </div>
                                                                    );
                                                                })}
                                                            </div>
                                                        </div>

                                                        {nextFeat && exitSc && nextEntrySc && (
                                                            <button
                                                                type="button"
                                                                className="proto-handoff-bridge"
                                                                onClick={() => {
                                                                    navigateTo(nextEntrySc.key, true);
                                                                    setMode("player");
                                                                }}
                                                            >
                                                                <ArrowDown size={14} />
                                                                <span>
                                                                    Chuyển tiếp Feature {feat.index} → {nextFeat.index}:{" "}
                                                                    <strong>{exitSc.name}</strong> → <strong>{nextEntrySc.name}</strong>
                                                                </span>
                                                            </button>
                                                        )}
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    </div>

                                    <div className="proto-legend">
                                        <span className="proto-legend-item">
                                            <ArrowRight size={13} color="#818cf8" /> Thứ tự luồng
                                        </span>
                                        <span className="proto-legend-item">
                                            <ArrowDown size={13} color="#fbbf24" /> Chuyển sang Feature kế
                                        </span>
                                        <span className="proto-legend-item" style={{ opacity: 0.78 }}>
                                            (Rê chuột lên màn hình để làm nổi bật đích đến)
                                        </span>
                                    </div>

                                    <div className="proto-map-hud">
                                        <button
                                            type="button"
                                            className="proto-btn"
                                            onClick={() => setSwimZoom((z) => Math.max(0.6, Number((z - 0.1).toFixed(2))))}
                                        >
                                            <Minus size={13} />
                                        </button>
                                        <span style={{ fontSize: 12, minWidth: 44, textAlign: "center" }}>
                                            {Math.round(swimZoom * 100)}%
                                        </span>
                                        <button
                                            type="button"
                                            className="proto-btn"
                                            onClick={() => setSwimZoom((z) => Math.min(1.3, Number((z + 0.1).toFixed(2))))}
                                        >
                                            <Plus size={13} />
                                        </button>
                                        <button type="button" className="proto-btn" onClick={() => setSwimZoom(1)} title="Đặt lại 100%">
                                            <Maximize2 size={13} />
                                        </button>
                                    </div>
                                </section>
                            ) : (
                                <section
                                    className={`proto-board-viewport ${panning ? "is-panning" : ""}`}
                                    onWheel={onBoardWheel}
                                    onPointerDown={onBoardPointerDown}
                                    onPointerMove={onBoardPointerMove}
                                    onPointerUp={onBoardPointerUp}
                                    onPointerLeave={onBoardPointerUp}
                                >
                                    <div
                                        ref={boardStageRef}
                                        className="proto-board-stage"
                                        style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${boardZoom})` }}
                                    >
                                        <svg className="proto-board-edges" width="1" height="1">
                                            <defs>
                                                <marker id="arrow-internal" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                                                    <path d="M 0 1 L 10 5 L 0 9 z" fill="#818cf8" />
                                                </marker>
                                                <marker id="arrow-cross" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                                                    <path d="M 0 1 L 10 5 L 0 9 z" fill="#34d399" />
                                                </marker>
                                                <marker id="arrow-handoff" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                                                    <path d="M 0 1 L 10 5 L 0 9 z" fill="#fbbf24" />
                                                </marker>
                                            </defs>
                                            {edgeCoords.map((e) => {
                                                if (e.isBack && hoveredKey !== e.fromKey) return null;
                                                const related = !hoveredKey || e.fromKey === hoveredKey || e.toKey === hoveredKey;
                                                const stroke =
                                                    e.kind === "handoff"
                                                        ? "#fbbf24"
                                                        : e.kind === "cross_feature"
                                                          ? "#34d399"
                                                          : "#818cf8";
                                                const marker =
                                                    e.kind === "handoff"
                                                        ? "url(#arrow-handoff)"
                                                        : e.kind === "cross_feature"
                                                          ? "url(#arrow-cross)"
                                                          : "url(#arrow-internal)";
                                                return (
                                                    <path
                                                        key={e.id}
                                                        d={e.d}
                                                        fill="none"
                                                        stroke={stroke}
                                                        strokeWidth={related && hoveredKey ? 2.5 : 1.75}
                                                        strokeDasharray={e.kind === "handoff" ? "6 4" : undefined}
                                                        opacity={related ? 0.92 : 0.14}
                                                        markerEnd={marker}
                                                    />
                                                );
                                            })}
                                        </svg>

                                        <div className="proto-board-lanes">
                                            {data.features.map((feat) => (
                                                <div className="proto-board-lane" key={feat.session_id}>
                                                    <div className="proto-board-lane-head">
                                                        <span className="proto-feature-num">{feat.index}</span>
                                                        <h3>{feat.title}</h3>
                                                    </div>
                                                    {feat.screen_keys.map((key, sIdx) => {
                                                        const sc = screenByKey.get(key);
                                                        if (!sc) return null;
                                                        return (
                                                            <div
                                                                key={key}
                                                                ref={(el) => {
                                                                    boardCardRefs.current[key] = el;
                                                                }}
                                                                className={`proto-map-card proto-board-card ${key === activeKey ? "is-active" : ""}`}
                                                                onMouseEnter={() => setHoveredKey(key)}
                                                                onMouseLeave={() => setHoveredKey(null)}
                                                                onClick={() => {
                                                                    navigateTo(key, true);
                                                                    setMode("player");
                                                                }}
                                                            >
                                                                <div className="proto-card-badges">
                                                                    <span className="proto-card-step">Bước {sIdx + 1}</span>
                                                                </div>
                                                                <img
                                                                    className="proto-map-thumb"
                                                                    src={api.screenshotUrl(sc.session_id, sc.id)}
                                                                    alt={sc.name}
                                                                    loading="lazy"
                                                                    onLoad={measureBoardEdges}
                                                                />
                                                                <div className="proto-map-card-meta">
                                                                    <strong>{sc.name}</strong>
                                                                    <span>{sc.id}</span>
                                                                </div>
                                                            </div>
                                                        );
                                                    })}
                                                </div>
                                            ))}
                                        </div>
                                    </div>

                                    <div className="proto-legend">
                                        <span className="proto-legend-item">
                                            <span className="proto-legend-line" style={{ background: "#818cf8" }} /> Trong Feature
                                        </span>
                                        <span className="proto-legend-item">
                                            <span className="proto-legend-line" style={{ background: "#34d399" }} /> Liên kết chéo
                                        </span>
                                        <span className="proto-legend-item">
                                            <span className="proto-legend-line" style={{ borderTop: "2px dashed #fbbf24" }} /> Nối tiếp Feature
                                        </span>
                                    </div>

                                    <div className="proto-map-hud">
                                        <button
                                            type="button"
                                            className="proto-btn"
                                            onClick={() => setBoardZoom((z) => Math.max(0.35, Number((z - 0.1).toFixed(2))))}
                                        >
                                            <Minus size={13} />
                                        </button>
                                        <span style={{ fontSize: 12, minWidth: 44, textAlign: "center" }}>
                                            {Math.round(boardZoom * 100)}%
                                        </span>
                                        <button
                                            type="button"
                                            className="proto-btn"
                                            onClick={() => setBoardZoom((z) => Math.min(1.5, Number((z + 0.1).toFixed(2))))}
                                        >
                                            <Plus size={13} />
                                        </button>
                                        <button
                                            type="button"
                                            className="proto-btn"
                                            onClick={() => {
                                                setBoardZoom(0.85);
                                                setPan({ x: 28, y: 56 });
                                            }}
                                            title="Đặt lại góc nhìn"
                                        >
                                            <Maximize2 size={13} />
                                        </button>
                                    </div>
                                </section>
                            )}
                        </>
                    )}
                </main>
            )}
        </div>
    );
}
