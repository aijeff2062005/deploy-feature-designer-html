import {
    ArrowLeft,
    ArrowRight,
    Check,
    ChevronDown,
    Code2,
    Columns2,
    Download,
    FileArchive,
    Files,
    FlaskConical,
    History,
    Image,
    Layers,
    Maximize2,
    Minus,
    Monitor,
    Moon,
    MousePointerClick,
    Plus,
    Rows3,
    Save,
    Search,
    Smartphone,
    Sun,
    Tablet,
    X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useTheme } from "../theme";
import type { Run, Screen, ScreenVersion } from "../types";

const params = new URLSearchParams(window.location.search);
const SESSION = params.get("session") || "";
const DESIGN_SYSTEM = params.get("ds") || "";
const PLATFORMS: Record<string, { label: string; icon: typeof Monitor }> = {
    mobile: { label: "Mobile", icon: Smartphone },
    tablet: { label: "Tablet", icon: Tablet },
    desktop: { label: "Desktop", icon: Monitor },
};
const POLL_MS = 3000;
const SCREEN_EDIT_MARKER = /^\[\[DESIGNER_SCREEN_EDIT:([^\]]+)\]\]\s*/;

type FigLayout = "rows" | "pages";
const LAYOUT_KEY = "designer.figLayout";
const FIG_LAYOUTS: { id: FigLayout; label: string; hint: string; icon: typeof Rows3 }[] = [
    { id: "rows", label: "Một page", hint: "Mỗi màn một hàng", icon: Rows3 },
    { id: "pages", label: "Nhiều page", hint: "Mỗi màn một page", icon: Files },
];

function readLayout(): FigLayout {
    try {
        return localStorage.getItem(LAYOUT_KEY) === "pages" ? "pages" : "rows";
    } catch {
        return "rows";
    }
}

/** Custom dropdown that renders entirely in our design tokens — no OS chrome. */
function CanvasPicker<T extends string | number>({
    icon: Icon,
    value,
    options,
    onChange,
    title,
}: {
    icon: React.ComponentType<{ size: number }>;
    value: T;
    options: { value: T; label: string }[];
    onChange: (v: T) => void;
    title?: string;
}) {
    const [open, setOpen] = useState(false);
    const box = useRef<HTMLDivElement>(null);
    const current = options.find((o) => o.value === value);

    useEffect(() => {
        if (!open) return;
        const close = (e: MouseEvent | KeyboardEvent) => {
            if (e instanceof KeyboardEvent ? e.key === "Escape" : !box.current?.contains(e.target as Node)) setOpen(false);
        };
        document.addEventListener("mousedown", close);
        document.addEventListener("keydown", close);
        return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
    }, [open]);

    return (
        <div className="canvas-picker" ref={box} title={title}>
            <button className="canvas-picker-trigger" onClick={() => setOpen(!open)} type="button" aria-haspopup="listbox" aria-expanded={open}>
                <Icon size={14} />
                <span>{current?.label ?? String(value)}</span>
                <ChevronDown size={13} className={open ? "flip" : ""} />
            </button>
            {open && (
                <ul className="canvas-picker-pop" role="listbox">
                    {options.map((o) => (
                        <li key={String(o.value)} role="option" aria-selected={o.value === value}
                            onClick={() => { onChange(o.value); setOpen(false); }}>
                            {o.label}
                            {o.value === value && <Check size={13} />}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}

function Brand({ subtitle }: { subtitle: string }) {
    return (
        <div className="brand-lockup canvas-brand">
            <span className="brand-mark">
                <img src={`${import.meta.env.BASE_URL}logo.png`} alt="Designer Studio" className="brand-logo-img" />
            </span>
            <div>
                <strong>Designer Studio</strong>
                <small>{subtitle}</small>
            </div>
        </div>
    );
}

/** Messages posted by app/render/runtime.js inside the screen iframe. */
type ScreenMessage =
    | { source: "designer-screen"; type: "ready"; states: string[]; height: number }
    | { source: "designer-screen"; type: "goto"; screen: string }
    | { source: "designer-screen"; type: "select"; element: { id: string; tag: string; text: string; html: string } };

export function CanvasApp({ onBack, initialScreenId }: { onBack?: () => void; initialScreenId?: string }) {
    useTheme();
    return DESIGN_SYSTEM ? <Gallery id={DESIGN_SYSTEM} /> : SESSION ? <ScreensCanvas onBack={onBack} initialScreenId={initialScreenId} /> : <p className="canvas-empty">Thiếu session.</p>;
}

function Frame({
    doc,
    width,
    onMessage,
    frameRef,
}: {
    doc: string;
    width: number;
    onMessage: (message: ScreenMessage) => void;
    frameRef: React.RefObject<HTMLIFrameElement | null>;
}) {
    const stage = useRef<HTMLDivElement>(null);
    const [height, setHeight] = useState(900);
    const [scale, setScale] = useState(1);

    useEffect(() => {
        const listen = (event: MessageEvent) => {
            if (event.source !== frameRef.current?.contentWindow) return;
            const data = event.data as ScreenMessage;
            if (data?.source !== "designer-screen") return;
            if (data.type === "ready") setHeight(Math.max(600, data.height));
            onMessage(data);
        };
        window.addEventListener("message", listen);
        return () => window.removeEventListener("message", listen);
    }, [onMessage, frameRef]);

    useEffect(() => {
        const fit = () => {
            const available = (stage.current?.clientWidth ?? width) - 48;
            setScale(Math.min(1, available / width));
        };
        fit();
        window.addEventListener("resize", fit);
        return () => window.removeEventListener("resize", fit);
    }, [width]);

    return (
        <div className="canvas-stage" ref={stage}>
            <div className="canvas-frame-box" style={{ width: width * scale, height: height * scale }}>
                {/* Opaque origin (no allow-same-origin): the screen's scripts cannot reach the studio. */}
                <iframe
                    ref={frameRef}
                    title="screen"
                    sandbox="allow-scripts allow-forms allow-popups"
                    srcDoc={doc}
                    style={{ width, height, transform: `scale(${scale})` }}
                />
            </div>
        </div>
    );
}

/** Export popover: the .fig layout choice applies to the .fig and to the design.fig inside the zip. */
function ExportMenu({ screen, state }: { screen?: Screen; state: string }) {
    const [open, setOpen] = useState(false);
    const [layout, setLayout] = useState<FigLayout>(readLayout);
    const box = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!open) return;
        const close = (event: MouseEvent | KeyboardEvent) => {
            if (event instanceof KeyboardEvent ? event.key === "Escape" : !box.current?.contains(event.target as Node)) {
                setOpen(false);
            }
        };
        document.addEventListener("mousedown", close);
        document.addEventListener("keydown", close);
        return () => {
            document.removeEventListener("mousedown", close);
            document.removeEventListener("keydown", close);
        };
    }, [open]);

    const choose = (value: FigLayout) => {
        setLayout(value);
        try {
            localStorage.setItem(LAYOUT_KEY, value);
        } catch {
            /* preference only */
        }
    };

    return (
        <div className="export-menu" ref={box}>
            <button className="canvas-export" onClick={() => setOpen(!open)} type="button" aria-expanded={open} aria-haspopup="menu">
                <Download size={15} /> Xuất <ChevronDown size={14} className={open ? "flip" : ""} />
            </button>
            {open && (
                <div className="export-pop" role="menu">
                    <p className="export-label">Bố cục file Figma</p>
                    <div className="export-layouts" role="radiogroup" aria-label="Bố cục file Figma">
                        {FIG_LAYOUTS.map((l) => (
                            <button
                                key={l.id}
                                className={layout === l.id ? "active" : ""}
                                onClick={() => choose(l.id)}
                                role="radio"
                                aria-checked={layout === l.id}
                                type="button"
                            >
                                <l.icon size={18} />
                                <span>
                                    <strong>{l.label}</strong>
                                    <small>{l.hint}</small>
                                </span>
                                {layout === l.id && <Check size={14} className="export-check" />}
                            </button>
                        ))}
                    </div>
                    <div className="export-items">
                        <a className="export-item primary" href={api.exportUrl(SESSION, "fig", layout)} onClick={() => setOpen(false)}>
                            <Download size={16} />
                            <span>
                                <strong>File Figma (.fig)</strong>
                                <small>Kéo thả vào Figma để chỉnh sửa</small>
                            </span>
                        </a>
                        <a className="export-item" href={api.exportUrl(SESSION, "zip", layout)} onClick={() => setOpen(false)}>
                            <FileArchive size={16} />
                            <span>
                                <strong>HTML + PNG (.zip)</strong>
                                <small>Prototype offline, ảnh từng màn và trạng thái</small>
                            </span>
                        </a>
                        {screen && (
                            <a
                                className="export-item"
                                href={api.screenshotUrl(SESSION, screen.id, state)}
                                target="_blank"
                                rel="noreferrer"
                                onClick={() => setOpen(false)}
                            >
                                <Image size={16} />
                                <span>
                                    <strong>PNG màn này</strong>
                                    <small>
                                        {screen.name}
                                        {state ? ` · ${state}` : ""}
                                    </small>
                                </span>
                            </a>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}

/** Hide scrollbars without overflow:hidden so scrollHeight stays accurate. */
function noScrollDoc(doc: string): string {
    const tag = "<style>html::-webkit-scrollbar,body::-webkit-scrollbar{display:none!important}html,body{scrollbar-width:none!important}</style>";
    return doc.includes("</head>") ? doc.replace("</head>", `${tag}</head>`) : tag + doc;
}

function ScreenThumbnail({ screen, index }: { screen: Screen; index: number }) {
    const box = useRef<HTMLSpanElement>(null);
    const [doc, setDoc] = useState("");
    const [boxWidth, setBoxWidth] = useState(120);
    const platform = PLATFORMS[screen.platform] ?? PLATFORMS.desktop;
    const frameHeight = screen.platform === "mobile" ? 844 : screen.platform === "tablet" ? 1024 : 900;

    useEffect(() => {
        let active = true;
        setDoc("");
        api.screenDocument(SESSION, screen.id).then((html) => { if (active) setDoc(html); }).catch(() => undefined);
        return () => { active = false; };
    }, [screen.id, screen.version]);

    useEffect(() => {
        const element = box.current;
        if (!element) return;
        const observer = new ResizeObserver(() => setBoxWidth(element.clientWidth));
        observer.observe(element);
        setBoxWidth(element.clientWidth);
        return () => observer.disconnect();
    }, []);

    const scale = screen.platform === "mobile"
        ? boxWidth / screen.width
        : Math.min(boxWidth / screen.width, 132 / frameHeight);
    return (
        <span className="canvas-screen-thumb" ref={box}>
            {doc ? (
                <span className="canvas-screen-thumb-content" style={{ width: screen.width * scale, height: frameHeight * scale }}>
                    <iframe title={screen.name} sandbox="allow-scripts allow-forms" srcDoc={noScrollDoc(doc)}
                        style={{ width: screen.width, height: frameHeight, transform: `scale(${scale})` }} />
                </span>
            ) : <span className="canvas-screen-thumb-fallback"><platform.icon size={18} /></span>}
            <span className="canvas-screen-number">{index + 1}</span>
        </span>
    );
}

/** One screen preview in the sequence overview. */
function StateFrame({
    doc, w, h: defaultH, index, screenId, screenName, zoom, active, onClick,
}: { doc: string; w: number; h: number; index: number; screenId: string; screenName: string; zoom: number; active: boolean; onClick: () => void }) {
    const ref = useRef<HTMLIFrameElement>(null);
    const [h, setH] = useState(defaultH);
    const baseScale = Math.min(1, 300 / w);
    const scale = baseScale * zoom;

    // Use the default state and keep the preview height in sync with the rendered screen.
    const handleLoad = () => ref.current?.contentWindow?.postMessage({ type: "designer:state", state: "" }, "*");
    useEffect(() => {
        const onMsg = (e: MessageEvent) => {
            if (e.source !== ref.current?.contentWindow) return;
            const d = e.data as { source?: string; type?: string; height?: number };
            if ((d?.type === "ready" || d?.type === "resize") && d.height) {
                setH(d.height!);
            }
        };
        window.addEventListener("message", onMsg);
        return () => window.removeEventListener("message", onMsg);
    }, []);

    const frameH = defaultH;
    const contentH = Math.max(defaultH, h);

    return (
        <div className={`canvas-multi-frame ${active ? "active" : ""}`} data-screen-id={screenId} onClick={onClick} role="button" tabIndex={0}
            aria-label={`Mở màn hình ${screenName}`} onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onClick(); }
            }}>
            <div className="canvas-multi-label">
            <span className="canvas-multi-index">{index}</span>
                <strong>{screenName}</strong>
            </div>
            <div className="canvas-multi-viewport" style={{ width: w * scale, height: frameH * scale }}>
                <div className="canvas-multi-viewport-inner" style={{ width: w * scale, height: contentH * scale }}>
                    <iframe ref={ref} title={screenName} sandbox="allow-scripts allow-forms"
                        srcDoc={noScrollDoc(doc)} onLoad={handleLoad}
                        style={{ width: w, height: contentH, transform: `scale(${scale})`, transformOrigin: "top left", pointerEvents: "none", border: 0 }} />
                </div>
            </div>
        </div>
    );
}

const ZOOM_MIN = 0.25, ZOOM_MAX = 2.0, ZOOM_STEP = 0.15;
const OVERVIEW_CARD_WIDTH = 280;
const OVERVIEW_GAP = 24;

/** Horizontal overview — one numbered card per screen, in flow order. */
function MultiView({ screens, selectedId, onSelect, layout = "flow" }: { screens: Screen[]; selectedId: string; onSelect: (id: string) => void; layout?: "flow" | "selected" }) {
    const [docs, setDocs] = useState<Record<string, string>>({});
    const [zoom, setZoom] = useState(0.42);
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        let active = true;
        screens.forEach((s) => {
            const key = `${s.id}:${s.version}`;
            if (docs[key]) return;
            api.screenDocument(SESSION, s.id).then((d) => {
                if (active) setDocs((prev) => ({ ...prev, [key]: d }));
            }).catch(() => undefined);
        });
        return () => { active = false; };
    }, [screens]);

    // Ctrl+wheel zoom
    useEffect(() => {
        const el = containerRef.current;
        if (!el) return;
        const onWheel = (e: WheelEvent) => {
            if (!e.ctrlKey && !e.metaKey) return;
            e.preventDefault();
            setZoom((z) => Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, z - e.deltaY * 0.001)));
        };
        el.addEventListener("wheel", onWheel, { passive: false });
        return () => el.removeEventListener("wheel", onWheel);
    }, []);

    const changeZoom = (delta: number) =>
        setZoom((z) => Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.round((z + delta) * 100) / 100)));

    const fitAll = () => {
        const viewport = containerRef.current?.querySelector(".canvas-flow-scroll") as HTMLDivElement | null;
        if (!viewport || !screens.length) return;
        const available = Math.max(240, viewport.clientWidth - 48);
        const baseWidths = orderedScreens.map((screen) => Math.min(screen.width || 390, 300));
        const connectorWidth = 22;
        const gap = layout === "selected" ? 16 : OVERVIEW_GAP + connectorWidth;
        const fit = (available - gap * (screens.length - 1)) / baseWidths.reduce((sum, width) => sum + width, 0);
        setZoom(Math.max(ZOOM_MIN, Math.min(1, Math.round(fit * 100) / 100)));
    };

    const orderedScreens = [...screens].sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
    const screenKey = orderedScreens.map((item) => item.id).join(",");

    useEffect(() => {
        if (layout !== "selected" || orderedScreens.length < 2) return;
        const viewport = containerRef.current?.querySelector(".canvas-flow-scroll");
        if (!viewport) return;
        const observer = new ResizeObserver(() => fitAll());
        observer.observe(viewport);
        requestAnimationFrame(() => fitAll());
        return () => observer.disconnect();
    }, [layout, screenKey]);

    return (
        <div className={`canvas-flow-wrap ${layout === "selected" ? "canvas-selection-preview" : ""}`} ref={containerRef}>
            <div className="canvas-flow-toolbar">
                <div className="canvas-flow-device">{layout === "selected" ? <><Columns2 size={15} /><span>{orderedScreens.length} màn hình đang chọn · chỉnh sửa riêng từng màn</span></> : <><Smartphone size={15} /><span>{PLATFORMS[orderedScreens[0]?.platform ?? "mobile"]?.label ?? "Mobile"} · {orderedScreens[0]?.width ?? 390} × {orderedScreens[0]?.platform === "mobile" ? 844 : 900}</span></>}</div>
                <div className="canvas-zoom-bar">
                <button className="canvas-icon" onClick={() => changeZoom(-ZOOM_STEP)} disabled={zoom <= ZOOM_MIN} type="button" title="Thu nhỏ (Ctrl+scroll)"><Minus size={14} /></button>
                <span className="canvas-zoom-label">{Math.round(zoom * 100)}%</span>
                <button className="canvas-icon" onClick={() => changeZoom(ZOOM_STEP)} disabled={zoom >= ZOOM_MAX} type="button" title="Phóng to (Ctrl+scroll)"><Plus size={14} /></button>
                <span className="canvas-zoom-divider" aria-hidden="true" />
                <button className="canvas-fit-button" onClick={fitAll} type="button" title="Thu phóng để thấy tất cả màn hình">
                    <Maximize2 size={13} /> Vừa khung
                </button>
                </div>
            </div>
            <div className="canvas-flow-scroll">
            <div className="canvas-multi">
                {orderedScreens.map((s, index) => {
                    const doc = docs[`${s.id}:${s.version}`] ?? null;
                    const w = s.width ?? (s.platform === "mobile" ? 390 : 1440);
                    const h = s.platform === "mobile" ? 844 : s.platform === "tablet" ? 1024 : 900;
                    return <div className="canvas-flow-step" key={s.id}>
                        {doc
                            ? <StateFrame doc={doc} w={w} h={h} index={index + 1} screenId={s.id}
                                screenName={s.name} zoom={zoom} active={selectedId === s.id} onClick={() => onSelect(s.id)} />
                            : <div className="canvas-multi-frame canvas-multi-loading-card" data-screen-id={s.id}>
                                <div className="canvas-multi-label">
                                    <span className="canvas-multi-index">{index + 1}</span>
                                    <strong>{s.name}</strong>
                                </div>
                                <div className="canvas-multi-viewport" style={{ width: OVERVIEW_CARD_WIDTH * zoom, height: h * Math.min(1, 300 / w) * zoom }}>
                                    <div className="canvas-multi-loading" />
                                </div>
                              </div>}
                        {layout === "flow" && index < orderedScreens.length - 1 && <div className="canvas-flow-connector" aria-hidden="true"><ArrowRight size={16} /></div>}
                    </div>;
                })}
            </div>
            </div>
        </div>
    );
}

function ScreensCanvas({ onBack, initialScreenId }: { onBack?: () => void; initialScreenId?: string }) {
    const { resolvedTheme, toggleTheme } = useTheme();
    const [screens, setScreens] = useState<Screen[]>([]);
    const [current, setCurrent] = useState(initialScreenId || params.get("screen") || "");
    const [doc, setDoc] = useState("");
    const [states, setStates] = useState<string[]>([]);
    const [state, setState] = useState("");
    const [selecting, setSelecting] = useState(false);
    const [testPanelOpen, setTestPanelOpen] = useState(false);
    const [codeOpen, setCodeOpen] = useState(false);
    const [code, setCode] = useState("");
    const [codeScreenId, setCodeScreenId] = useState("");
    const [versions, setVersions] = useState<ScreenVersion[]>([]);
    const [version, setVersion] = useState<number>();
    const [toast, setToast] = useState("");
    const [viewMode, setViewMode] = useState<"single" | "multi">("single");
    const [selectedIds, setSelectedIds] = useState<string[]>([]);
    const [editRuns, setEditRuns] = useState<Run[]>([]);
    const [editPrompt, setEditPrompt] = useState("");
    const [editBusy, setEditBusy] = useState(false);
    const [editPanelOpen, setEditPanelOpen] = useState(true);
    const [editError, setEditError] = useState("");
    const [syncLabel, setSyncLabel] = useState("Chat được đồng bộ");
    const frameRef = useRef<HTMLIFrameElement>(null);
    const initialSelectionApplied = useRef(false);

    // Build the back-to-chat URL from the current page's query params.
    const backUrl = (() => {
        const project = params.get("project");
        const base = window.location.pathname.replace("canvas.html", "");
        return project ? `${base}?session=${SESSION}&project=${project}` : `${base}?session=${SESSION}`;
    })();
    const channel = useMemo(
        () => (typeof BroadcastChannel !== "undefined" ? new BroadcastChannel(`designer-canvas-${SESSION}`) : null),
        [],
    );

    const screen = screens.find((s) => s.id === current) ?? screens[0];
    const codeScreen = screens.find((s) => s.id === codeScreenId) ?? screen;
    const width = screen?.width ?? 1440;

    const loadScreens = useCallback(async () => {
        const items = await api.listScreens(SESSION);
        setScreens((old) =>
            old.length === items.length && old.every((s, i) => s.id === items[i].id && s.version === items[i].version)
                ? old
                : items,
        );
    }, []);

    useEffect(() => {
        void loadScreens().catch((e) => setToast((e as Error).message));
        const timer = window.setInterval(() => void loadScreens().catch(() => undefined), POLL_MS);
        return () => window.clearInterval(timer);
    }, [loadScreens]);

    useEffect(() => {
        if (!screens.length || initialSelectionApplied.current) return;
        initialSelectionApplied.current = true;
        const requestedId = initialScreenId || params.get("screen");
        const initial = screens.find((item) => item.id === requestedId)
            ?? screens.find((item) => item.id === current)
            ?? screens[0];
        if (initial) {
            setCurrent(initial.id);
            setCodeScreenId(initial.id);
            setSelectedIds([initial.id]);
        }
    }, [screens, current, initialScreenId]);

    const loadEditRuns = useCallback(async () => {
        const items = await api.listRuns(SESSION);
        setEditRuns(items.filter((run) => SCREEN_EDIT_MARKER.test(run.prompt)));
    }, []);

    useEffect(() => {
        void loadEditRuns().catch(() => undefined);
        const timer = window.setInterval(() => void loadEditRuns().catch(() => undefined), POLL_MS);
        return () => window.clearInterval(timer);
    }, [loadEditRuns]);

    useEffect(() => {
        if (!selectedIds.length) setEditPanelOpen(false);
    }, [selectedIds.length]);

    useEffect(() => {
        const pending = editRuns.some((run) => run.status === "running" || run.status === "queued");
        if (pending) setSyncLabel("Đang xử lý yêu cầu…");
        else if (editRuns.length) setSyncLabel("Đã đồng bộ vào hội thoại Designer");
    }, [editRuns]);

    // Reload the document when the screen, its latest version or the viewed version changes.
    useEffect(() => {
        if (!screen) return;
        let cancelled = false;
        api.screenDocument(SESSION, screen.id, version).then((d) => !cancelled && setDoc(d)).catch(() => undefined);
        const versionScreen = codeOpen ? codeScreen : screen;
        if (versionScreen) api.screenVersions(SESSION, versionScreen.id).then((v) => !cancelled && setVersions(v)).catch(() => undefined);
        if (codeOpen && codeScreen) api.getScreen(SESSION, codeScreen.id, version).then((s) => !cancelled && setCode(s.html ?? ""));
        return () => {
            cancelled = true;
        };
    }, [screen?.id, screen?.version, codeScreen?.id, codeScreen?.version, version, codeOpen]);

    useEffect(() => {
        setState("");
        setVersion(undefined);
        setTestPanelOpen(false);
    }, [screen?.id]);

    const post = (message: Record<string, unknown>) => frameRef.current?.contentWindow?.postMessage(message, "*");
    useEffect(() => post({ type: "designer:state", state }), [state, doc]);
    useEffect(() => post({ type: "designer:select", on: selecting }), [selecting, doc]);

    const onMessage = useCallback(
        (message: ScreenMessage) => {
            if (message.type === "ready") {
                setStates(message.states);
                post({ type: "designer:state", state });
                post({ type: "designer:select", on: selecting });
            } else if (message.type === "goto") {
                if (screens.some((s) => s.id === message.screen)) setCurrent(message.screen);
                else setToast(`Chưa có màn hình "${message.screen}"`);
            } else if (message.type === "select" && screen) {
                channel?.postMessage({ type: "select", screenId: screen.id, screenName: screen.name, element: message.element });
                setToast(`Đã gửi phần tử "${message.element.text.slice(0, 40) || message.element.tag}" sang khung chat`);
                setSelecting(false);
            }
        },
        [screens, screen, channel, state, selecting],
    );

    useEffect(() => {
        if (!toast) return;
        const t = window.setTimeout(() => setToast(""), 3500);
        return () => window.clearTimeout(t);
    }, [toast]);

    const saveCode = async () => {
        if (!codeScreen) return;
        try {
            await api.saveScreen(SESSION, codeScreen.id, { name: codeScreen.name, platform: codeScreen.platform, html: code, note: "manual edit", expected_version: codeScreen.version });
            setVersion(undefined);
            await loadScreens();
            setToast("Đã lưu phiên bản mới");
        } catch (error) {
            setToast((error as Error).message);
        }
    };

    const selectedScreens = screens.filter((item) => selectedIds.includes(item.id));
    const pendingEdit = editRuns.some((run) => run.status === "running" || run.status === "queued");
    const visibleEditRuns = editRuns.filter((run) => {
        const match = run.prompt.match(SCREEN_EDIT_MARKER);
        const ids = match?.[1].split(",").map((item) => item.split("@")[0]) ?? [];
        return ids.some((id) => selectedIds.includes(id));
    });

    const submitScreenEdit = async () => {
        if (!editPrompt.trim() || !selectedScreens.length || editBusy) return;
        const targetText = selectedScreens.map((item) => `${item.id}@${item.version}`).join(",");
        const prompt = `[[DESIGNER_SCREEN_EDIT:${targetText}]]\n${editPrompt.trim()}`;
        setEditBusy(true);
        setEditError("");
        setSyncLabel("Đang đồng bộ hội thoại…");
        try {
            const run = await api.sendMessage(SESSION, {
                content: prompt,
                screen_name_hint: selectedScreens.length === 1 ? selectedScreens[0].name : `${selectedScreens.length} màn hình đã chọn`,
                platform_hint: selectedScreens[0].platform,
                library_ids: [],
            });
            setEditRuns((items) => [run, ...items.filter((item) => item.id !== run.id)]);
            setEditPrompt("");
            setSyncLabel("Đã đồng bộ vào hội thoại Designer");
            void loadEditRuns();
        } catch (error) {
            setEditError((error as Error).message);
            setSyncLabel("Chưa đồng bộ");
        } finally {
            setEditBusy(false);
        }
    };

    const undoCurrentScreen = async () => {
        if (!screen) return;
        try {
            const history = await api.screenVersions(SESSION, screen.id);
            const previous = history.find((item) => item.version < screen.version);
            if (!previous) {
                setEditError("Chưa có phiên bản trước để hoàn tác.");
                return;
            }
            const prior = await api.getScreen(SESSION, screen.id, previous.version);
            await api.saveScreen(SESSION, screen.id, {
                name: screen.name, platform: screen.platform, html: prior.html ?? "",
                note: `Hoàn tác về v${previous.version}`, expected_version: screen.version,
            });
            setVersion(undefined);
            await loadScreens();
            await loadEditRuns();
            setEditError("");
            setToast(`Đã hoàn tác màn hình về nội dung v${previous.version}`);
        } catch (error) {
            setEditError((error as Error).message);
        }
    };

    if (!screens.length) {
        return (
                    <div className={`canvas-shell ${onBack ? "canvas-shell-embedded" : ""}`}>
                <div className="canvas-empty">
                    <Brand subtitle="Trang thiết kế" />
                    <p>Chưa có màn hình nào. Agent sẽ dựng màn hình sau khi bạn duyệt spec; trang này tự cập nhật.</p>
                </div>
            </div>
        );
    }

    const platform = PLATFORMS[screen?.platform ?? ""] ?? PLATFORMS.desktop;

    return (
        <div className={`canvas-shell ${onBack ? "canvas-shell-embedded" : ""}`}>
            <aside className="canvas-rail">
                {!onBack && <div className="canvas-rail-top">
                    <Brand subtitle="Trang thiết kế" />
                    <a className="canvas-icon canvas-rail-back" href={backUrl} title="Về khung chat" aria-label="Về khung chat">
                        <ArrowLeft size={15} />
                    </a>
                </div>}
                <div className={`canvas-rail-label ${viewMode === "multi" ? "canvas-rail-label-flow" : ""}`}>
                    <span>Danh sách màn hình</span>
                    <span className="canvas-count">{screens.length}</span>
                </div>
                <div className="canvas-view-tabs" role="tablist" aria-label="Chế độ thiết kế">
                    <button className={viewMode === "single" ? "active" : ""} type="button" role="tab" aria-selected={viewMode === "single"}
                        onClick={() => { setViewMode("single"); setCodeOpen(false); setSelecting(false); setTestPanelOpen(false); }}>Thiết kế màn hình</button>
                    <button className={viewMode === "multi" ? "active" : ""} type="button" role="tab" aria-selected={viewMode === "multi"}
                        onClick={() => { setViewMode("multi"); setCodeOpen(false); setSelecting(false); setTestPanelOpen(false); }}>Prototype</button>
                </div>
                {viewMode === "multi" && <nav className="canvas-flow-nav" aria-label="Điều hướng flow">
                    <button className="canvas-flow-nav-overview active" type="button" aria-current="page">
                        <Columns2 size={15} /><span>Flow overview</span>
                    </button>
                    <div className="canvas-flow-nav-heading"><span>All screens</span><span>{screens.length}</span></div>
                </nav>}
                <nav className={`canvas-screens ${viewMode === "multi" ? "canvas-screens-flow" : ""}`} aria-label="Màn hình">
                    {[...screens].sort((a, b) => (a.position ?? 0) - (b.position ?? 0)).map((s, index) => {
                        const p = PLATFORMS[s.platform] ?? PLATFORMS.desktop;
                        return (
                            <div key={s.id} className="canvas-screen-row">
                                <button
                                    className={`canvas-screen ${s.id === screen?.id ? "active" : ""} ${viewMode === "multi" ? "canvas-flow-screen-link" : ""}`}
                                    onClick={() => {
                                        setCurrent(s.id);
                                        setCodeScreenId(s.id);
                                        if (viewMode === "multi") {
                                            const target = Array.from(document.querySelectorAll<HTMLElement>("[data-screen-id]"))
                                                .find((item) => item.dataset.screenId === s.id);
                                            target?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
                                        }
                                    }}
                                    aria-current={s.id === screen?.id ? "page" : undefined}
                                    type="button"
                                >
                                    {viewMode === "multi" ? <span className="canvas-screen-icon"><span className="canvas-flow-screen-number">{index + 1}</span></span> : (
                                        <ScreenThumbnail screen={s} index={index} />
                                    )}
                                    <span className="canvas-screen-text">
                                        <strong>{s.name}</strong>
                                        <small>{viewMode === "multi" ? p.label : `${p.label} · v${s.version}`}</small>
                                    </span>
                                </button>
                                <button
                                    className={`canvas-screen-check ${selectedIds.includes(s.id) ? "selected" : ""}`}
                                    type="button"
                                    aria-label={`${selectedIds.includes(s.id) ? "Bỏ chọn" : "Chọn"} ${s.name} để chỉnh sửa bằng chat`}
                                    aria-pressed={selectedIds.includes(s.id)}
                                    onClick={() => {
                                        if (selectedIds.includes(s.id)) {
                                            const remaining = selectedIds.filter((id) => id !== s.id);
                                            setSelectedIds(remaining);
                                            if (current === s.id && remaining.length) setCurrent(remaining[0]);
                                            if (codeScreenId === s.id && remaining.length) setCodeScreenId(remaining[0]);
                                        } else {
                                            setCurrent(s.id);
                                            setCodeScreenId(s.id);
                                            setSelectedIds((items) => [...items, s.id]);
                                            setEditPanelOpen(true);
                                        }
                                    }}
                                >
                                    {selectedIds.includes(s.id) && <Check size={12} />}
                                </button>
                            </div>
                        );
                    })}
                </nav>
            </aside>

            <main className="canvas-main">
                <header className="canvas-toolbar">
                    <div className="canvas-title">
                        {viewMode === "multi"
                            ? <><h2>Flow overview</h2><span className="canvas-flow-crumb">/ {screens.length} màn hình · {screen?.name}</span></>
                            : codeOpen && codeScreen
                                ? <><h2>{codeScreen.name}</h2><span className="canvas-chip"><Code2 size={13} /> HTML · v{version ?? codeScreen.version}</span></>
                                : selectedScreens.length > 1
                                    ? <><h2>{selectedScreens.length} màn hình đang chọn</h2><span className="canvas-chip">Yêu cầu chat áp dụng riêng từng màn</span></>
                                    : <><h2>{screen?.name}</h2><span className="canvas-chip"><platform.icon size={13} /> {platform.label} · {width}px</span></>
                        }
                    </div>
                    <div className="canvas-tools">
                        <a className="canvas-back-chat" href={backUrl} onClick={onBack ? (event) => { event.preventDefault(); onBack(); } : undefined}>
                            <ArrowLeft size={14} /> Quay lại chat
                        </a>
                        <button className="canvas-tool" type="button" onClick={() => {
                            if (!selectedIds.length && screen) setSelectedIds([screen.id]);
                            setEditPanelOpen((open) => !open);
                        }} aria-expanded={editPanelOpen}>
                            Chỉnh sửa bằng chat
                        </button>
                        <span className="canvas-sync-status"><Check size={13} /> {syncLabel}</span>
                        {viewMode === "single" && <>
                            {states.length > 0 && (
                                <div className="canvas-test-menu">
                                    <button
                                        className={`canvas-tool ${testPanelOpen ? "active" : ""}`}
                                        onClick={() => setTestPanelOpen((open) => !open)}
                                        type="button"
                                        aria-expanded={testPanelOpen}
                                        aria-controls="canvas-test-panel"
                                        title="Mở công cụ thử trạng thái màn hình"
                                    >
                                        <FlaskConical size={15} /> Test
                                    </button>
                                    {testPanelOpen && (
                                        <section className="canvas-test-panel" id="canvas-test-panel" aria-label="Công cụ thử trạng thái">
                                            <strong>Thử trạng thái màn hình</strong>
                                            <p>Chỉ dùng để xem trước trạng thái; không thêm các điều khiển này vào giao diện ứng dụng.</p>
                                            <CanvasPicker
                                                icon={Layers}
                                                title="Chọn trạng thái để xem thử"
                                                value={state}
                                                options={[{ value: "", label: "Mặc định" }, ...states.map((s) => ({ value: s, label: s }))]}
                                                onChange={setState}
                                            />
                                        </section>
                                    )}
                                </div>
                            )}
                            <CanvasPicker
                                icon={History}
                                title="Lịch sử phiên bản"
                                value={version ?? ""}
                                options={[
                                    { value: "", label: `v${(codeOpen ? codeScreen : screen)?.version} · mới nhất` },
                                    ...versions.slice(1).map((v) => ({ value: v.version, label: `v${v.version} · ${v.note || v.updated_by || ""}` })),
                                ]}
                                onChange={(v) => setVersion(v ? Number(v) : undefined)}
                            />
                            <span className="canvas-divider" aria-hidden />
                            <button
                                className={`canvas-tool ${selecting ? "active" : ""}`}
                                onClick={() => setSelecting(!selecting)}
                                type="button"
                                title="Chọn một phần tử để góp ý ở khung chat"
                            >
                                <MousePointerClick size={15} /> Chọn phần tử
                            </button>
                            <button
                                className={`canvas-tool ${codeOpen ? "active" : ""}`}
                                onClick={() => setCodeOpen(!codeOpen)}
                                type="button"
                                title="Sửa HTML của màn hình"
                            >
                                <Code2 size={15} /> Code
                            </button>
                        </>}
                        <button className="canvas-icon" onClick={toggleTheme} title="Đổi giao diện sáng/tối" type="button">
                            {resolvedTheme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
                        </button>
                        <ExportMenu screen={screen} state={state} />
                    </div>
                </header>
                <div className={`canvas-body ${codeOpen && viewMode === "single" ? "with-code" : ""}`}>
                    {codeOpen && viewMode === "single" ? (
                        <section className="canvas-code" aria-label="HTML của màn hình">
                            <header>
                                <span>
                                    <Code2 size={15} /> {codeScreen?.name} · HTML · v{version ?? codeScreen?.version}
                                </span>
                                <button className="canvas-icon" onClick={() => setCodeOpen(false)} type="button" title="Đóng">
                                    <X size={16} />
                                </button>
                            </header>
                            {selectedScreens.length > 1 && <nav className="canvas-code-tabs" role="tablist" aria-label="Màn hình đang chọn để sửa code">
                                {selectedScreens.map((item) => <button key={item.id} role="tab" aria-selected={codeScreen?.id === item.id} className={codeScreen?.id === item.id ? "active" : ""} type="button" onClick={() => { setCodeScreenId(item.id); setCurrent(item.id); setVersion(undefined); }}>
                                    <span>#{item.position + 1}</span>{item.name}
                                </button>)}
                            </nav>}
                            <textarea value={code} onChange={(e) => setCode(e.target.value)} spellCheck={false} />
                            <button className="primary-button" onClick={() => void saveCode()} type="button">
                                <Save size={15} /> Lưu phiên bản mới · {codeScreen?.name}
                            </button>
                        </section>
                    ) : viewMode === "multi"
                        ? <MultiView screens={screens} selectedId={screen?.id ?? ""} onSelect={(id) => { setCurrent(id); setCodeScreenId(id); setState(""); setVersion(undefined); setViewMode("single"); }} />
                        : selectedScreens.length > 1
                            ? <MultiView screens={selectedScreens} selectedId={screen?.id ?? ""} layout="selected" onSelect={(id) => { setCurrent(id); setCodeScreenId(id); setState(""); setVersion(undefined); }} />
                            : doc && <Frame doc={doc} width={width} onMessage={onMessage} frameRef={frameRef} />
                    }
                </div>
            </main>
            {editPanelOpen && selectedScreens.length > 0 && <aside className="canvas-edit-panel" aria-label="Chỉnh sửa màn hình bằng chat">
                <header className="canvas-edit-header">
                    <div><strong>Chỉnh sửa bằng chat</strong><small>Yêu cầu được lưu trong hội thoại Designer</small></div>
                    <span className="canvas-edit-count">{selectedScreens.length} màn hình</span>
                </header>
                <section className="canvas-edit-targets">
                    <strong>Phạm vi chỉnh sửa</strong>
                    <p>{selectedScreens.length > 1 ? "Áp dụng riêng yêu cầu cho từng màn hình đã chọn." : selectedScreens.length === 1 ? "Chỉ áp dụng cho màn hình đang chọn." : "Chọn ít nhất một màn hình ở danh sách bên trái."}</p>
                    <div className="canvas-edit-chips">
                        {selectedScreens.map((item) => <span key={item.id}>#{item.position + 1} {item.name}</span>)}
                    </div>
                </section>
                <form className="canvas-edit-form" onSubmit={(event) => { event.preventDefault(); void submitScreenEdit(); }}>
                    <textarea value={editPrompt} onChange={(event) => setEditPrompt(event.target.value)}
                        placeholder="Mô tả thay đổi cần thực hiện…" rows={4} disabled={editBusy || pendingEdit || !selectedScreens.length} />
                    <div className="canvas-edit-form-footer">
                        <span>Giữ nguyên bố cục và nội dung ngoài phạm vi yêu cầu.</span>
                        <button className="primary-button" type="submit" disabled={!editPrompt.trim() || !selectedScreens.length || editBusy || pendingEdit}>
                            {editBusy || pendingEdit ? "Đang xử lý…" : "Gửi yêu cầu"} <ArrowRight size={14} />
                        </button>
                    </div>
                </form>
                {editError && <div className="canvas-edit-error" role="alert">{editError}</div>}
                <section className="canvas-edit-history">
                    <div className="canvas-edit-history-title"><strong><History size={15} /> Lịch sử chỉnh sửa</strong>
                        <button type="button" onClick={() => void undoCurrentScreen()} disabled={!screen || !versions.some((item) => item.version < (screen?.version ?? 0))}>
                            <ArrowLeft size={13} /> Hoàn tác màn đang xem
                        </button>
                    </div>
                    {!visibleEditRuns.length && <p className="canvas-edit-empty">Chưa có yêu cầu chỉnh sửa cho màn hình đang chọn.</p>}
                    {visibleEditRuns.slice(0, 5).map((run) => {
                        const cleanPrompt = run.prompt.replace(SCREEN_EDIT_MARKER, "").trim();
                        const pending = run.status === "running" || run.status === "queued";
                        const failed = run.status === "failed" || run.status === "blocked";
                        return <article className="canvas-edit-history-item" key={run.id}>
                            <div className={`canvas-edit-run-status ${failed ? "failed" : pending ? "pending" : "done"}`}>
                                {pending ? "Đang xử lý" : failed ? "Không áp dụng được" : "Đã áp dụng"} · {new Date(run.created_at).toLocaleString()}
                            </div>
                            <p>{cleanPrompt}</p>
                            {run.assistant_message && <small>{run.assistant_message}</small>}
                            {failed && run.error?.detail && <small className="canvas-edit-run-error">{run.error.detail}</small>}
                            {failed && run.error?.code === "SCREEN_VERSION_CONFLICT" && <button className="canvas-edit-reload" type="button" onClick={() => {
                                void loadScreens().then(() => api.listScreens(SESSION)).then((items) => {
                                    const targetId = run.prompt.match(SCREEN_EDIT_MARKER)?.[1].split(",")[0].split("@")[0];
                                    if (targetId && items.some((item) => item.id === targetId)) setCurrent(targetId);
                                    setVersion(undefined);
                                    setEditError("Màn hình đã được cập nhật phiên bản mới. Hãy kiểm tra nội dung mới trước khi gửi lại yêu cầu.");
                                }).catch((error) => setEditError((error as Error).message));
                            }}>Tải phiên bản mới</button>}
                        </article>;
                    })}
                </section>
            </aside>}
            {toast && <div className="canvas-toast">{toast}</div>}
        </div>
    );
}

function Gallery({ id }: { id: string }) {
    const [doc, setDoc] = useState("");
    const [page, setPage] = useState(0);
    const [q, setQ] = useState("");
    const [query, setQuery] = useState("");
    const frameRef = useRef<HTMLIFrameElement>(null);
    const noop = useCallback(() => undefined, []);

    useEffect(() => {
        api.galleryDocument(id, page, query).then(setDoc).catch((e) => setDoc(`<p>${(e as Error).message}</p>`));
    }, [id, page, query]);

    return (
        <div className="canvas-shell gallery">
            <main className="canvas-main">
                <header className="canvas-toolbar">
                    <form
                        className="canvas-group"
                        onSubmit={(e) => {
                            e.preventDefault();
                            setPage(0);
                            setQuery(q);
                        }}
                    >
                        <Search size={15} />
                        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm component…" />
                    </form>
                    <button disabled={page === 0} onClick={() => setPage(page - 1)} type="button">‹ Trước</button>
                    <span>Trang {page + 1}</span>
                    <button onClick={() => setPage(page + 1)} type="button">Sau ›</button>
                </header>
                <div className="canvas-body">{doc && <Frame doc={doc} width={1280} onMessage={noop} frameRef={frameRef} />}</div>
            </main>
        </div>
    );
}
