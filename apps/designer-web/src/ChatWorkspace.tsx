import {
    ArrowUp,
    Bot,
    Braces,
    ChevronLeft,
    ChevronRight,
    Check,
    Download,
    ExternalLink,
    Eye,
    MousePointerClick,
    List,
    FileCode,
    FileText,
    Image,
    LayoutGrid,
    LoaderCircle,
    Maximize2,
    Minimize2,
    Paperclip,
    ClipboardList,
    Sparkles,
    Workflow,
    ZoomIn,
    ZoomOut,
    X,
} from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { MentionMenu } from "./MentionMenu";
import {
    ATTACH_EVENT,
    type ProjectFile,
    filterFiles,
    listProjectFiles,
    readDraggedFile,
    toBrowserFile,
} from "./projectFiles";
import { api } from "./api";
import { STARTER_PROMPTS, StyleSelector } from "./ChatControls";
import { MarkdownRenderer } from "./MarkdownRenderer";
import type {
    ParsedAttachment,
    Run,
    HulyIssueDraft,
    HulyIssueResult,
    HulyAttachmentPreview,
    HulyProject,
    HulyProposedTask,
    HulyTaskProposalResponse,
    HulyTaskConfig,
    HulyProjectMetadata,
    HulyIssueMatch,
    HulyBulkIssueResult,
    WorkflowEvent,
    ExactAsset,
} from "./types";

/** Element picked on the design page (canvas tab), sent over BroadcastChannel. */
interface CanvasSelection {
    screenId: string;
    screenName: string;
    element: { id: string; tag: string; text: string; html: string };
}

interface StylePreviewOption {
    key: string;
    name: string;
    tagline: string;
    recommended: boolean;
    image?: string;
    asset_credit?: string;
    asset_source_url?: string;
    review_notes?: string[];
    html?: string;
    compiled_css?: string;
    preview_width?: number;
    preview_height?: number;
}

function previewDocument(option: StylePreviewOption): string | undefined {
    if (!option.html || !option.compiled_css) return undefined;
    const policy = "default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'";
    const css = option.compiled_css.replace(/<\/style/gi, "<\\/style");
    return `<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="${policy}"><style>${css}</style></head><body>${option.html}</body></html>`;
}

function LiveStylePreview({ option, className = "", zoom = 1 }: { option: StylePreviewOption; className?: string; zoom?: number }) {
    const srcDoc = previewDocument(option);
    const stageRef = useRef<HTMLDivElement>(null);
    const [stageSize, setStageSize] = useState({ width: 0, height: 0 });
    useEffect(() => {
        const node = stageRef.current;
        if (!node) return;
        const observer = new ResizeObserver(([entry]) => setStageSize({ width: entry.contentRect.width, height: entry.contentRect.height }));
        observer.observe(node);
        return () => observer.disconnect();
    }, []);
    const width = Math.max(1, Number(option.preview_width) || 390);
    const height = Math.max(1, Number(option.preview_height) || 844);
    const fittedScale = stageSize.width && stageSize.height ? Math.min(stageSize.width / width, stageSize.height / height) : 1;
    const scale = fittedScale * zoom;
    if (srcDoc) {
        return <div ref={stageRef} className={`live-style-preview ${className}`} style={{ aspectRatio: `${width} / ${height}` }}>
            <div className="live-style-preview-sized" style={{ width: width * scale, height: height * scale }}>
                <iframe title={`Bản xem trước ${option.name}`} srcDoc={srcDoc} sandbox="allow-scripts" style={{ width, height, transform: `scale(${scale})` }} />
            </div>
        </div>;
    }
    return <div ref={stageRef} className={`live-style-preview ${className}`} style={{ aspectRatio: `${width} / ${height}` }}>{option.image && <img src={option.image} alt={`Bản xem trước ${option.name}`} />}</div>;
}

function StylePreviewDialog({
    options, activeIndex, onActiveIndex, onClose, onSelect, selectedStyle, mode = "single",
}: {
    options: StylePreviewOption[];
    activeIndex: number;
    onActiveIndex: (index: number) => void;
    onClose: () => void;
    onSelect: (option: StylePreviewOption) => void;
    selectedStyle?: string;
    mode?: "single" | "gallery";
}) {
    const option = options[activeIndex];
    const [zoom, setZoom] = useState(1);
    useEffect(() => setZoom(1), [activeIndex]);
    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") onClose();
            if (event.key === "ArrowLeft") onActiveIndex((activeIndex + options.length - 1) % options.length);
            if (event.key === "ArrowRight") onActiveIndex((activeIndex + 1) % options.length);
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [activeIndex, onActiveIndex, onClose, options.length]);
    if (!option) return null;
    return (
        <div className="style-preview-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
            <section className={`style-preview-dialog${mode === "gallery" ? " is-gallery" : ""}`} role="dialog" aria-modal="true" aria-label={mode === "gallery" ? "Xem trước ba hướng thiết kế" : `Xem trước ${option.name}`}>
                <header>
                    <div><strong>{option.name}</strong><span>{activeIndex + 1} / {options.length}</span></div>
                    <button type="button" aria-label="Đóng xem trước" onClick={onClose}><X size={19} /></button>
                </header>
                {mode === "gallery" ? <div className="style-preview-dialog-gallery">
                    {options.map((item) => <article className={`style-preview-dialog-option${selectedStyle === item.key ? " is-selected" : ""}`} key={item.key}>
                        <strong>{item.name}</strong>
                        <p>{item.tagline}</p>
                        <LiveStylePreview option={item} className="style-preview-dialog-option-frame" />
                        <button type="button" className="style-preview-dialog-select" onClick={() => onSelect(item)}>
                            {selectedStyle === item.key ? <><Check size={15} /> Đã chọn</> : <><Check size={15} /> Chọn hướng này</>}
                        </button>
                    </article>)}
                </div> : <div className="style-preview-dialog-stage">
                    {options.length > 1 && <button type="button" className="style-preview-dialog-arrow" aria-label="Hướng trước" onClick={() => onActiveIndex((activeIndex + options.length - 1) % options.length)}><ChevronLeft /></button>}
                    <div className="style-preview-dialog-canvas">
                        <div className="style-preview-zoom-controls">
                            <button type="button" aria-label="Thu nhỏ" onClick={() => setZoom((value) => Math.max(0.5, value - 0.15))}><ZoomOut size={16} /></button>
                            <span>{Math.round(zoom * 100)}%</span>
                            <button type="button" aria-label="Phóng to" onClick={() => setZoom((value) => Math.min(2.5, value + 0.15))}><ZoomIn size={16} /></button>
                        </div>
                        <LiveStylePreview option={option} className="style-preview-dialog-frame" zoom={zoom} />
                    </div>
                    {options.length > 1 && <button type="button" className="style-preview-dialog-arrow" aria-label="Hướng tiếp theo" onClick={() => onActiveIndex((activeIndex + 1) % options.length)}><ChevronRight /></button>}
                </div>}
                {mode === "single" && <nav className="style-preview-dialog-tabs" aria-label="Các hướng thiết kế">
                    {options.map((item, index) => <button key={item.key} type="button" className={`${index === activeIndex ? "active" : ""}${selectedStyle === item.key ? " is-selected" : ""}`} onClick={() => onActiveIndex(index)}>
                        <span>{item.name}</span>{selectedStyle === item.key && <Check size={14} />}
                    </button>)}
                </nav>}
                <footer>
                    <span>{mode === "gallery" ? "Chọn một hướng để áp dụng cho các màn hình của feature" : "Preview HTML trực tiếp · Phóng to/thu nhỏ để xem chi tiết"}</span>
                    {mode === "single" && <button type="button" className="style-preview-dialog-select" onClick={() => onSelect(option)}>
                        {selectedStyle === option.key ? <><Check size={15} /> Đã chọn hướng này</> : <><Check size={15} /> Dùng hướng này</>}
                    </button>}
                </footer>
            </section>
        </div>
    );
}

function StylePreviewGallery({
    event,
    busy,
    onSelect,
    selectedStyle,
}: {
    event: WorkflowEvent;
    busy: boolean;
    onSelect: (option: StylePreviewOption) => void;
    selectedStyle?: string;
}) {
    const [dialogIndex, setDialogIndex] = useState<number | null>(null);
    const [dialogMode, setDialogMode] = useState<"single" | "gallery">("single");
    const options = Array.isArray(event.payload.options)
        ? (event.payload.options as StylePreviewOption[])
        : [];
    const screenName = String(event.payload.screen_name || "Màn hình đại diện");
    const screenPurpose = String(event.payload.screen_purpose || "");
    const platform = String(event.payload.platform || "mobile");
    if (!options.length) return null;
    return (
        <section className="style-preview-gallery" aria-label="Chọn hướng thiết kế giao diện">
            <div className="style-preview-heading">
                <div className="style-preview-heading-icon"><Sparkles size={17} /></div>
                <div>
                    <strong>Chọn hướng thiết kế</strong>
                    <p>Ba bản xem trước được tạo theo nội dung và mục đích của dự án.</p>
                </div>
                <button type="button" className="style-preview-open-gallery" onClick={() => { setDialogMode("gallery"); setDialogIndex(0); }}><Eye size={15} /> Xem popup 3 hướng</button>
            </div>
            <div className="style-preview-context">
                <span className="style-preview-context-dot" />
                <span>{screenName}</span>
                {screenPurpose && <span className="style-preview-purpose">{screenPurpose}</span>}
                <small>{platform === "mobile" ? "MOBILE APP" : platform.toUpperCase()}</small>
            </div>
            <div className="style-preview-grid">
                {options.map((option, index) => (
                    <article className={`style-preview-card${option.recommended ? " is-recommended" : ""}`} key={option.key}>
                        <div className="style-preview-card-head">
                            <span className="style-preview-index">{String.fromCharCode(65 + index)}</span>
                            <div className="style-preview-title">
                                <strong>{option.name}</strong>
                                {option.recommended && <span>Đề xuất</span>}
                            </div>
                        </div>
                        <p className="style-preview-tagline">{option.tagline}</p>
                        {option.asset_credit && option.asset_source_url && (
                            <a className="style-preview-credit" href={option.asset_source_url} target="_blank" rel="noreferrer">
                                Minh hoạ: {option.asset_credit}
                            </a>
                        )}
                        <button
                            className={`style-preview-image-button${platform === "mobile" ? " is-mobile" : " is-desktop"}`}
                            onClick={() => { setDialogMode("single"); setDialogIndex(index); }}
                            type="button"
                            aria-label={`Phóng to bản xem trước ${option.name}`}
                        >
                            <LiveStylePreview option={option} className="style-preview-device" />
                        </button>
                        <button
                            className={`style-preview-select${selectedStyle === option.key ? " is-selected" : ""}`}
                            onClick={() => onSelect(option)}
                            type="button"
                            disabled={busy}
                        >
                            {selectedStyle === option.key ? <><Check size={14} /> Đã chọn</> : "Chọn hướng này"}
                        </button>
                        {option.review_notes?.length ? (
                            <div className="style-preview-review-notes" role="note">
                                <strong>Gợi ý hoàn thiện</strong>
                                <ul>{option.review_notes.map((note, noteIndex) => <li key={noteIndex}>{note}</li>)}</ul>
                            </div>
                        ) : null}
                    </article>
                ))}
            </div>
            <p className="style-preview-note">Cùng một màn hình và chức năng đã chốt; mỗi lựa chọn thể hiện một ngôn ngữ thị giác riêng.</p>
            {dialogIndex !== null && <StylePreviewDialog options={options} activeIndex={dialogIndex} onActiveIndex={setDialogIndex} onClose={() => setDialogIndex(null)} onSelect={(option) => { onSelect(option); }} selectedStyle={selectedStyle} mode={dialogMode} />}
        </section>
    );
}

function InheritedStylePreview({
    event,
    busy,
    onUse,
    onExplore,
    selectedStyle,
}: {
    event: WorkflowEvent;
    busy: boolean;
    onUse: (option: StylePreviewOption) => Promise<void> | void;
    onExplore: () => Promise<void>;
    selectedStyle?: string;
}) {
    const [actionBusy, setActionBusy] = useState(false);
    const [dialogOpen, setDialogOpen] = useState(false);
    const option = Array.isArray(event.payload.options)
        ? (event.payload.options[0] as StylePreviewOption | undefined)
        : undefined;
    const screenName = typeof event.payload.screen_name === "string" ? event.payload.screen_name : "";
    const source = typeof event.payload.source_feature_title === "string" ? event.payload.source_feature_title : "";
    const platform = typeof event.payload.platform === "string" ? event.payload.platform : "";
    if (!option) return null;

    return (
        <section className="inherited-style-preview" aria-label="Xem trước chuẩn thiết kế kế thừa">
            <div className="inherited-style-preview-image">
                <span className="inherited-style-preview-badge"><Sparkles size={13} /> Kế thừa Wiki</span>
                <button type="button" className="inherited-style-open-preview" aria-label="Phóng to màn hình xem trước" onClick={() => setDialogOpen(true)}>
                    <LiveStylePreview option={option} />
                    <span><Maximize2 size={14} /> Xem lớn</span>
                </button>
            </div>
            <div className="inherited-style-preview-info">
                <div>
                    <strong>{option.name}</strong>
                    {[screenName, source && `từ ${source}`, platform].filter(Boolean).join(" · ")}
                    {option.tagline && <p>{option.tagline}</p>}
                </div>
                <div className="inherited-style-preview-actions">
                    <button type="button" className={`inherited-style-use${selectedStyle === option.key ? " is-selected" : ""}`} disabled={busy || actionBusy} onClick={async () => {
                        setActionBusy(true);
                        try { await onUse(option); } finally { setActionBusy(false); }
                    }}>
                        <Check size={15} /> {selectedStyle === option.key ? "Đã chọn" : "Dùng chuẩn này"}
                    </button>
                    <button type="button" className="inherited-style-explore" disabled={busy || actionBusy} onClick={async () => {
                        setActionBusy(true);
                        try { await onExplore(); } finally { setActionBusy(false); }
                    }}>
                        <LayoutGrid size={14} /> Xem 3 hướng khác
                    </button>
                </div>
            </div>
            {dialogOpen && <StylePreviewDialog options={[option]} activeIndex={0} onActiveIndex={() => undefined} onClose={() => setDialogOpen(false)} onSelect={(selected) => { void onUse(selected); }} selectedStyle={selectedStyle} />}
        </section>
    );
}

interface Props {
    run?: Run;
    runs: Run[];
    events: WorkflowEvent[];
    historyEvents: WorkflowEvent[];
    busy: boolean;
    sessionId?: string;
    onOpenCanvas?: () => void;
    onSubmit: (value: {
        prompt: string;
        screenName: string;
        platform: string;
        styleName?: string;
        libraryIds: string[];
        assets?: ExactAsset[];
    }) => void | Promise<void>;
    onExploreProjectStyles?: () => Promise<void>;
    onReview?: (
        decision: "approved" | "changes_requested",
        feedback?: string,
    ) => Promise<void> | void;
    onToggleSidePanel?: () => void;
    onRetry: () => void;
    onCancel: () => void;
}

function eventText(event: WorkflowEvent) {
    const value = event.payload.message ?? event.payload.detail ?? event.type;
    return String(value).replaceAll("_", " ");
}

function formatMessageTime(dateStr?: string) {
    if (!dateStr) {
        return new Date().toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
        });
    }
    try {
        const d = new Date(dateStr);
        if (isNaN(d.getTime())) return "";
        return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
        return "";
    }
}

function getStageStatusText(stage?: string) {
    switch (stage) {
        case "requirement":
            return "Analyzing requirements & design intent...";
        case "specification":
            return "Writing the UI specification...";
        case "review_spec":
            return "Preparing specification for review...";
        case "assembly":
            return "Building screens in HTML + Tailwind (write, check, refine)...";
        case "review_final":
            return "Finalizing design for review...";
        default:
            return "Designer is thinking & preparing response...";
    }
}

function getStageBadge(stage?: string) {
    switch (stage) {
        case "requirement":
            return "Requirements";
        case "specification":
            return "Specification";
        case "assembly":
            return "Build";
        case "review_spec":
        case "review_final":
            return "Review";
        default:
            return "Working";
    }
}

const HIDDEN_CONVERSATION_EVENTS = new Set(["run.created", "stage.changed", "llm.usage", "project.wiki.updated"]);
const ATTACHMENT_CONTEXT_MARKER = "<!-- DESIGNER_ATTACHMENT_CONTEXT -->";
const DEFAULT_ATTACHMENT_REQUEST =
    "Analyze the attached requirements and determine the complete, consistent set of screens and states to design.";

interface ParsedUserPrompt {
    text: string;
    attachmentNames: string[];
}

function parseUserPrompt(raw: string): ParsedUserPrompt {
    const visible = raw.split(ATTACHMENT_CONTEXT_MARKER, 1)[0]
        .replace(/^\[\[DESIGNER_SCREEN_EDIT:[^\]]+\]\]\s*/, "")
        .trim();
    const match = visible.match(
        /^(.*?)(?:\r?\n\r?\n|\r?\n)?Attachments:\s*(.+)$/s,
    );
    if (!match) {
        return { text: visible, attachmentNames: [] };
    }

    const userText = match[1].trim();
    const filesStr = match[2].trim();
    const attachmentNames = filesStr
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);

    const isDefault = userText === DEFAULT_ATTACHMENT_REQUEST;

    return {
        text: isDefault ? "" : userText,
        attachmentNames,
    };
}

function getFileMeta(fileName: string) {
    const ext = fileName.split(".").pop()?.toLowerCase() || "";
    switch (ext) {
        case "json":
            return {
                label: "JSON",
                type: "Data Specification",
                color: "#F59E0B",
                icon: "code",
            };
        case "bpmn":
        case "bpmn2":
            return {
                label: "BPMN",
                type: "Business Process",
                color: "#8B5CF6",
                icon: "workflow",
            };
        case "png":
        case "jpg":
        case "jpeg":
        case "webp":
        case "svg":
            return {
                label: ext.toUpperCase(),
                type: "Image Reference",
                color: "#38BDF8",
                icon: "image",
            };
        case "pdf":
            return {
                label: "PDF",
                type: "Document",
                color: "#EF4444",
                icon: "doc",
            };
        case "md":
            return {
                label: "MD",
                type: "Markdown Docs",
                color: "#A78BFA",
                icon: "doc",
            };
        case "txt":
        case "csv":
        case "yaml":
        case "yml":
            return {
                label: ext.toUpperCase(),
                type: "Text Data",
                color: "#10B981",
                icon: "doc",
            };
        default:
            return {
                label: ext ? ext.toUpperCase() : "FILE",
                type: "Attachment",
                color: "#94A3B8",
                icon: "file",
            };
    }
}

function UserMessageContent({ prompt }: { prompt: string }) {
    const { text, attachmentNames } = parseUserPrompt(prompt);

    return (
        <div className="user-message-content">
            {text && <p className="user-prompt-text">{text}</p>}
            {attachmentNames.length > 0 && (
                <div
                    className={`user-attachments-group ${text ? "has-text" : ""}`}
                >
                    {attachmentNames.map((fileName) => {
                        const meta = getFileMeta(fileName);
                        return (
                            <div
                                className="user-attachment-card"
                                key={fileName}
                            >
                                <span
                                    className="user-attachment-icon-box"
                                    style={{ color: meta.color }}
                                >
                                    {meta.icon === "image" ? (
                                        <Image size={16} />
                                    ) : meta.icon === "code" ? (
                                        <FileCode size={16} />
                                    ) : meta.icon === "workflow" ? (
                                        <Workflow size={16} />
                                    ) : (
                                        <FileText size={16} />
                                    )}
                                </span>
                                <div className="user-attachment-info">
                                    <span
                                        className="user-attachment-name"
                                        title={fileName}
                                    >
                                        {fileName}
                                    </span>
                                    <span className="user-attachment-type">
                                        {meta.type}
                                    </span>
                                </div>
                                <span className="user-attachment-badge">
                                    {meta.label}
                                </span>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

function AssistantAvatar({ className = "" }: { className?: string }) {
    return (
        <span className={`assistant-avatar ${className}`}>
            <img
                src={`${import.meta.env.BASE_URL}logo.png`}
                alt="Designer"
                className="assistant-avatar-img"
            />
        </span>
    );
}

function UserAvatar({ className = "" }: { className?: string }) {
    return (
        <span className={`user-avatar ${className}`} title="You">
            <img src={`${import.meta.env.BASE_URL}user_icon.png`} alt="You" className="user-avatar-img" />
        </span>
    );
}

export function ChatWorkspace({
    run,
    runs,
    events,
    historyEvents,
    busy,
    sessionId,
    onOpenCanvas,
    onSubmit,
    onExploreProjectStyles,
    onReview,
    onToggleSidePanel,
    onRetry,
    onCancel,
}: Props) {
    const [prompt, setPrompt] = useState("");
    const [screenName, setScreenName] = useState("");
    const [platform, setPlatform] = useState("responsive");
    const [styleName, setStyleName] = useState("minimal");
    const [attachments, setAttachments] = useState<ParsedAttachment[]>([]);
    const [attachmentError, setAttachmentError] = useState("");
    const [uploading, setUploading] = useState(false);
    const [dragging, setDragging] = useState(false);
    const [reviewFeedback, setReviewFeedback] = useState("");
    const [selection, setSelection] = useState<CanvasSelection>();
    const [submittingReview, setSubmittingReview] = useState(false);
    const [wikiApprovalOpen, setWikiApprovalOpen] = useState(false);
    const [loadingWikiPreview, setLoadingWikiPreview] = useState(false);
    const [wikiPreviewIndex, setWikiPreviewIndex] = useState(0);
    const [wikiVersion, setWikiVersion] = useState(1);
    const [wikiPreviewFiles, setWikiPreviewFiles] = useState<{ path: string; content: string; screen_name?: string; platform?: string; mime_type?: string }[]>([]);
    const [wikiPreviewError, setWikiPreviewError] = useState("");
    const [activityLogOpen, setActivityLogOpen] = useState(false);
    const [hulyDialogOpen, setHulyDialogOpen] = useState(false);
    const [hulyProjects, setHulyProjects] = useState<HulyProject[]>([]);
    const [hulyMembers, setHulyMembers] = useState<{ name: string; email: string }[]>([]);
    const [hulyEpics, setHulyEpics] = useState<{ identifier: string; title: string }[]>([]);
    const [hulyEpicId, setHulyEpicId] = useState("");
    const [hulyEpicsLoading, setHulyEpicsLoading] = useState(false);
    const [hulyMetadata, setHulyMetadata] = useState<HulyProjectMetadata | null>(null);
    const [hulyMetadataLoading, setHulyMetadataLoading] = useState(false);
    const [relatedQueries, setRelatedQueries] = useState<Record<string, string>>({});
    const [relatedOptions, setRelatedOptions] = useState<Record<string, HulyIssueMatch[]>>({});
    const [hulyEpicError, setHulyEpicError] = useState("");
    const [hulyProjectId, setHulyProjectId] = useState("");
    const [hulyDraft, setHulyDraft] = useState<Omit<HulyIssueDraft, "run_id" | "project_id"> | null>(null);
    const [hulyProposal, setHulyProposal] = useState<HulyTaskProposalResponse | null>(null);
    const [hulyProposalCacheOwner, setHulyProposalCacheOwner] = useState("");
    const [selectedProposalKey, setSelectedProposalKey] = useState("");
    const [selectedProposalKeys, setSelectedProposalKeys] = useState<string[]>([]);
    const [hulyTaskConfigs, setHulyTaskConfigs] = useState<Record<string, HulyTaskConfig>>({});
    const [clarificationDecisions, setClarificationDecisions] = useState<Record<number, "confirm" | "defer" | "exclude">>({});
    const [clarificationAnswers, setClarificationAnswers] = useState<Record<number, string>>({});
    const [hulyDialogStage, setHulyDialogStage] = useState<"proposal" | "detail" | "clarify" | "configure" | "review">("proposal");
    const [hulyLoading, setHulyLoading] = useState(false);
    const [hulySubmitting, setHulySubmitting] = useState(false);
    const [hulyError, setHulyError] = useState("");
    const [hulyProposalError, setHulyProposalError] = useState("");
    const [hulyCreated, setHulyCreated] = useState<HulyIssueResult | null>(null);
    const [hulyBulkCreated, setHulyBulkCreated] = useState<HulyBulkIssueResult | null>(null);
    const [hulyArtifacts, setHulyArtifacts] = useState<HulyAttachmentPreview | null>(null);
    const [hulyPreviewTab, setHulyPreviewTab] = useState<"png" | "spec" | "tokens" | null>(null);
    const [hulyPreviewIndex, setHulyPreviewIndex] = useState(0);
    const [hulyPreviewZoom, setHulyPreviewZoom] = useState(1);
    const [hulyPngMode, setHulyPngMode] = useState<"overview" | "screen">("overview");
    const [hulyPngImmersive, setHulyPngImmersive] = useState(false);
    const [hulyPreviewError, setHulyPreviewError] = useState("");
    const hulyDialogBodyRef = useRef<HTMLDivElement>(null);
    const conversationEndRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    // Platform: shared project files can be cited with "@" or dragged in.
    const [projectFiles, setProjectFiles] = useState<ProjectFile[]>([]);
    const [mention, setMention] = useState<{ start: number; query: string } | null>(null);
    const [mentionIndex, setMentionIndex] = useState(0);
    const mentionMatches = mention
        ? filterFiles(projectFiles, mention.query).filter((f) => f.citable).slice(0, 8)
        : [];
    const fileInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (!hulyDialogOpen || !hulyProjectId) return;
        let active = true;
        setHulyEpicsLoading(true);
        setHulyEpicError("");
        setHulyEpicId("");
        setHulyDraft((current) => current ? { ...current, epic_id: "" } : current);
        api.hulyEpics(hulyProjectId).then((response) => {
            if (active) setHulyEpics(response.epics);
        }).catch((error) => {
            if (!active) return;
            setHulyEpics([]);
            setHulyEpicError(error instanceof Error ? error.message : "Không tải được danh sách Epic.");
        }).finally(() => { if (active) setHulyEpicsLoading(false); });
        return () => { active = false; };
    }, [hulyDialogOpen, hulyProjectId]);

    useEffect(() => {
        if (!hulyDialogOpen || !hulyProjectId) { setHulyMetadata(null); return; }
        let active = true;
        setHulyMetadataLoading(true);
        api.hulyProjectMetadata(hulyProjectId).then((metadata) => {
            if (!active) return;
            setHulyMetadata(metadata);
            setHulyTaskConfigs((current) => Object.fromEntries(Object.entries(current).map(([key, config]) => [key, { ...config, status: config.status || metadata.default_status, task_type: config.task_type || metadata.task_types[0]?.name || metadata.task_types[0]?.title || "" }])));
        }).catch((error) => {
            if (active) { setHulyMetadata(null); setHulyError(error instanceof Error ? error.message : "Không tải được cấu hình Huly."); }
        }).finally(() => { if (active) setHulyMetadataLoading(false); });
        return () => { active = false; };
    }, [hulyDialogOpen, hulyProjectId]);

    const hulyProposalCacheKey = sessionId && run ? `huly-proposal:v5:${sessionId}:${run.id}` : "";
    useLayoutEffect(() => {
        if (hulyDialogOpen && hulyDialogBodyRef.current) hulyDialogBodyRef.current.scrollTop = 0;
    }, [hulyDialogOpen, hulyDialogStage]);
    useEffect(() => {
        if (!hulyProposalCacheKey || !hulyProposal || hulyProposalCacheOwner !== hulyProposalCacheKey) return;
        try {
            sessionStorage.setItem(hulyProposalCacheKey, JSON.stringify({ proposal: hulyProposal, selectedProposalKey, selectedProposalKeys, hulyTaskConfigs, hulyDialogStage, clarificationDecisions, clarificationAnswers, hulyProjectId, hulyEpicId, hulyBulkCreated }));
        } catch { /* Keep the current in-memory proposal if session storage is unavailable. */ }
    }, [hulyProposalCacheKey, hulyProposalCacheOwner, hulyProposal, selectedProposalKey, selectedProposalKeys, hulyTaskConfigs, hulyDialogStage, clarificationDecisions, clarificationAnswers, hulyProjectId, hulyEpicId, hulyBulkCreated]);

    const canReview =
        (run?.stage === "review_spec" || run?.stage === "review_final") &&
        run.status === "waiting_review";
    const specScreens = run?.specification?.schema_version === "html-1" ? run.specification.screens : [];
    const finalApproved = Boolean(
        run?.stage === "finished" &&
        run.status === "completed" &&
        run.specification?.schema_version === "html-1" &&
        run.specification.screens.length > 0,
    );
    const selectedProposedTask = hulyProposal?.proposal.tasks.find((task) => task.key === selectedProposalKey) ?? null;
    const selectedProposedTasks = hulyProposal?.proposal.tasks.filter((task) => selectedProposalKeys.includes(task.key)) ?? [];
    const proposalPriorityLabel: Record<string, string> = { urgent: "Urgent", high: "High", medium: "Medium", low: "Low", none: "Chưa đề xuất" };
    const finalBlocked = false;
    const designWikiFiles = wikiPreviewFiles.map((file) => ({
        ...file,
        summary: file.path.endsWith("/design-system.json")
            ? "Style contract được chọn, lưu trong Project Wiki"
            : file.path.endsWith("manifest.json")
              ? "Manifest phiên bản và tham chiếu tài nguyên"
              : file.path.endsWith(".md")
                ? "Tài liệu Design System trong Project Wiki"
                : file.path.endsWith(".css")
                  ? "CSS của mẫu UI đã chọn"
                  : file.path.endsWith(".jpg")
                    ? "Ảnh preview của mẫu UI đã chọn"
                    : file.path.endsWith(".html")
                      ? "HTML dùng chung từ mẫu UI đã chọn"
                      : "Tệp Design System trong Project Wiki",
    }));

    const approveCurrentReview = async () => {
        if (submittingReview || !onReview || finalBlocked) return;
        if (run?.stage === "review_final") {
            setLoadingWikiPreview(true);
            setWikiPreviewError("");
            try {
                const preview = await api.projectWikiPreview(run.id);
                setWikiVersion(preview.version);
                setWikiPreviewFiles(preview.files);
                const sampleImage = preview.files.findIndex((file) => file.path.endsWith(".jpg"));
                setWikiPreviewIndex(sampleImage >= 0 ? sampleImage : 0);
            } catch (error) {
                setWikiPreviewFiles([]);
                setWikiPreviewError((error as Error).message || "Không tạo được bản xem trước Project Wiki.");
            }
            setWikiApprovalOpen(true);
            return;
        }
        setSubmittingReview(true);
        try {
            await onReview("approved", reviewFeedback);
            setReviewFeedback("");
        } finally {
            setSubmittingReview(false);
        }
    };

    const confirmDesignWikiApproval = async () => {
        if (!onReview || submittingReview) return;
        setSubmittingReview(true);
        try {
            await onReview("approved", reviewFeedback);
            setReviewFeedback("");
            setWikiApprovalOpen(false);
        } catch (error) {
            setWikiPreviewError((error as Error).message || "Không lưu được Project Wiki. Hãy mở lại preview trước khi xác nhận.");
            setWikiPreviewFiles([]);
        } finally {
            setSubmittingReview(false);
        }
    };

    const applyHulyProposal = (response: HulyTaskProposalResponse, snapshot?: { selectedProposalKey?: string; selectedProposalKeys?: string[]; hulyTaskConfigs?: Record<string, HulyTaskConfig> }) => {
        const tasks = response.proposal.tasks;
        setHulyProposal(response);
        setHulyProposalCacheOwner(hulyProposalCacheKey);
        setSelectedProposalKey(snapshot?.selectedProposalKey && tasks.some((task) => task.key === snapshot.selectedProposalKey) ? snapshot.selectedProposalKey : tasks[0]?.key ?? "");
        setSelectedProposalKeys(Array.isArray(snapshot?.selectedProposalKeys) ? snapshot.selectedProposalKeys.filter((key) => tasks.some((task) => task.key === key)) : tasks.map((task) => task.key));
        setHulyTaskConfigs(Object.fromEntries(tasks.map((task) => [task.key, {
            assignee: snapshot?.hulyTaskConfigs?.[task.key]?.assignee ?? "",
            due_date: snapshot?.hulyTaskConfigs?.[task.key]?.due_date ?? "",
            priority: snapshot?.hulyTaskConfigs?.[task.key]?.priority ?? task.priority,
            status: snapshot?.hulyTaskConfigs?.[task.key]?.status || hulyMetadata?.default_status || "",
            task_type: snapshot?.hulyTaskConfigs?.[task.key]?.task_type || hulyMetadata?.task_types[0]?.name || hulyMetadata?.task_types[0]?.title || "",
            labels: snapshot?.hulyTaskConfigs?.[task.key]?.labels ?? [],
            component: snapshot?.hulyTaskConfigs?.[task.key]?.component ?? "",
            milestone: snapshot?.hulyTaskConfigs?.[task.key]?.milestone ?? "",
            estimation_hours: snapshot?.hulyTaskConfigs?.[task.key]?.estimation_hours ?? null,
            dependency_keys: snapshot?.hulyTaskConfigs?.[task.key]?.dependency_keys ?? task.dependency_keys,
            related_issue_ids: snapshot?.hulyTaskConfigs?.[task.key]?.related_issue_ids ?? [],
        }])));
    };

    const openHulyDialog = async () => {
        if (!run || !sessionId) return;
        setHulyDialogOpen(true);
        setHulyError("");
        setHulyProposalError("");
        setHulyDialogStage("proposal");
        setHulyCreated(null);
        setHulyBulkCreated(null);
        setClarificationDecisions({});
        setClarificationAnswers({});
        setHulyArtifacts(null);
        setHulyPreviewTab(null);
        setHulyPreviewIndex(0);
        setHulyPreviewZoom(1);
        setHulyPreviewError("");
        setHulyLoading(true);
        let cachedProposal: HulyTaskProposalResponse | null = null;
        let cachedSnapshot: { selectedProposalKey?: string; selectedProposalKeys?: string[]; hulyTaskConfigs?: Record<string, HulyTaskConfig>; hulyDialogStage?: "proposal" | "detail" | "clarify" | "configure" | "review"; clarificationDecisions?: Record<number, "confirm" | "defer" | "exclude">; clarificationAnswers?: Record<number, string>; hulyProjectId?: string; hulyEpicId?: string; hulyBulkCreated?: HulyBulkIssueResult | null } | undefined;
        try {
            const cached = hulyProposalCacheKey ? sessionStorage.getItem(hulyProposalCacheKey) : null;
            if (cached) {
                const parsed = JSON.parse(cached);
                if (parsed?.proposal?.proposal?.tasks?.length) {
                    cachedProposal = parsed.proposal as HulyTaskProposalResponse;
                    cachedSnapshot = parsed;
                    applyHulyProposal(cachedProposal, cachedSnapshot);
                    if (cachedSnapshot?.hulyDialogStage) setHulyDialogStage(cachedSnapshot.hulyDialogStage);
                    if (cachedSnapshot?.clarificationDecisions) setClarificationDecisions(cachedSnapshot.clarificationDecisions);
                    if (cachedSnapshot?.clarificationAnswers) setClarificationAnswers(cachedSnapshot.clarificationAnswers);
                    if (cachedSnapshot?.hulyProjectId) setHulyProjectId(cachedSnapshot.hulyProjectId);
                    if (cachedSnapshot?.hulyEpicId) setHulyEpicId(cachedSnapshot.hulyEpicId);
                    if (cachedSnapshot?.hulyBulkCreated) setHulyBulkCreated(cachedSnapshot.hulyBulkCreated);
                }
            }
        } catch { /* Ignore invalid browser cache and request a fresh proposal. */ }
        if (!cachedProposal) {
            setHulyProposal(null);
            setSelectedProposalKeys([]);
            setHulyTaskConfigs({});
        }
        const screens = run.specification?.schema_version === "html-1" ? run.specification.screens : [];
        const screenNotes = screens.map((screen, index) => {
            const details = [
                screen.purpose ? `Mục đích: ${screen.purpose.slice(0, 320)}` : "",
                screen.sections?.length ? `Thành phần: ${screen.sections.slice(0, 6).join(", ")}` : "",
                screen.states?.length ? `Trạng thái: ${screen.states.slice(0, 6).join(", ")}` : "",
            ].filter(Boolean);
            return `${index + 1}. ${screen.name}${details.length ? `\n   ${details.join("\n   ")}` : ""}`;
        }).join("\n\n");
        setHulyDraft({
            title: screens.length === 1
                ? `Triển khai màn hình ${screens[0].name}`
                : `Triển khai ${screens.length} màn hình UI đã duyệt`,
            objective: "",
            current_state: "",
            solution: screenNotes,
            acceptance_criteria: "",
            priority: "none",
            assignee: "",
            due_date: "",
            epic_id: "",
        });
        const [projectsResult, membersResult, previewResult, proposalResult] = await Promise.allSettled([
            api.hulyProjects(),
            api.hulyMembers(),
            api.hulyAttachmentPreview(sessionId, run.id),
            cachedProposal ? Promise.resolve(cachedProposal) : api.proposeHulyTasks(sessionId, run.id),
        ]);
        if (projectsResult.status === "fulfilled") {
            const response = projectsResult.value;
            setHulyProjects(response.projects);
            const preferred = response.projects.find((project) => project.huly_id === response.default_project_id || project.id === response.default_project_id || project.identifier === response.default_project_id);
            setHulyProjectId((current) => current || preferred?.id || response.projects[0]?.id || "");
        } else {
            setHulyError(projectsResult.reason instanceof Error ? projectsResult.reason.message : "Không tải được danh sách project Huly.");
        }
        if (membersResult.status === "fulfilled") {
            setHulyMembers(membersResult.value.members);
        } else {
            setHulyMembers([]);
            setHulyError((current) => current || (membersResult.reason instanceof Error ? membersResult.reason.message : "Không tải được thành viên Huly."));
        }
        if (previewResult.status === "fulfilled") {
            const preview = previewResult.value;
            setHulyArtifacts(preview);
            if (preview.task_draft) {
                setHulyDraft({
                    title: preview.task_draft.title,
                    objective: preview.task_draft.objective,
                    current_state: preview.task_draft.current_state,
                    solution: preview.task_draft.solution,
                    acceptance_criteria: preview.task_draft.acceptance_criteria,
                    priority: "none",
                    assignee: "",
                    due_date: "",
                    epic_id: "",
                });
            }
        } else {
            setHulyPreviewError(previewResult.reason instanceof Error ? previewResult.reason.message : "Không tải được tài liệu xem trước.");
        }
        if (proposalResult.status === "fulfilled") {
            applyHulyProposal(proposalResult.value, cachedSnapshot);
        } else {
            setHulyProposalError(proposalResult.reason instanceof Error ? proposalResult.reason.message : "Không tạo được đề xuất task.");
        }
        setHulyLoading(false);
    };

    const updateProposedTask = (key: string, update: Partial<HulyProposedTask>) => {
        setHulyProposal((current) => current ? {
            ...current,
            proposal: {
                ...current.proposal,
                tasks: current.proposal.tasks.map((task) => task.key === key ? { ...task, ...update } : task),
            },
        } : current);
    };

    const toggleProposalTask = (key: string, checked: boolean) => {
        const tasks = hulyProposal?.proposal.tasks ?? [];
        const byKey = new Map(tasks.map((task) => [task.key, task]));
        setSelectedProposalKeys((current) => {
            const next = new Set(current);
            if (checked) {
                const addWithDependencies = (taskKey: string) => {
                    if (next.has(taskKey)) return;
                    next.add(taskKey);
                    byKey.get(taskKey)?.dependency_keys.forEach(addWithDependencies);
                };
                addWithDependencies(key);
            } else {
                next.delete(key);
                let changed = true;
                while (changed) {
                    changed = false;
                    for (const taskKey of [...next]) {
                        if (byKey.get(taskKey)?.dependency_keys.some((dependency) => !next.has(dependency))) {
                            next.delete(taskKey);
                            changed = true;
                        }
                    }
                }
            }
            return tasks.map((task) => task.key).filter((taskKey) => next.has(taskKey));
        });
    };

    const updateTaskConfig = (key: string, update: Partial<HulyTaskConfig>) => {
        setHulyTaskConfigs((current) => ({ ...current, [key]: { ...current[key], ...update } }));
    };

    const searchRelatedIssues = async (taskKey: string, query: string) => {
        setRelatedQueries((current) => ({ ...current, [taskKey]: query }));
        if (query.trim().length < 2 || !hulyProjectId) { setRelatedOptions((current) => ({ ...current, [taskKey]: [] })); return; }
        try {
            const result = await api.searchHulyIssues(hulyProjectId, query.trim());
            setRelatedOptions((current) => ({ ...current, [taskKey]: result.issues }));
        } catch { setRelatedOptions((current) => ({ ...current, [taskKey]: [] })); }
    };

    const continueWithSelectedProposal = () => {
        const task = hulyProposal?.proposal.tasks.find((item) => item.key === selectedProposalKey);
        if (!task) return;
        setHulyDialogStage("detail");
    };

    const continueFromClarifications = () => {
        const questions = hulyProposal?.proposal.open_questions ?? [];
        for (let index = 0; index < questions.length; index += 1) {
            const decision = clarificationDecisions[index];
            if (!decision || (decision === "confirm" && !clarificationAnswers[index]?.trim())) {
                setHulyError("Hãy ghi nhận quyết định cho từng câu hỏi BA/PO trước khi tiếp tục.");
                return;
            }
        }
        const excludedKeys = new Set(questions.flatMap((question, index) => clarificationDecisions[index] === "exclude" ? question.affected_task_keys : []));
        if (excludedKeys.size) {
            const byKey = new Map((hulyProposal?.proposal.tasks ?? []).map((task) => [task.key, task]));
            setSelectedProposalKeys((current) => {
                const next = new Set(current.filter((key) => !excludedKeys.has(key)));
                let changed = true;
                while (changed) {
                    changed = false;
                    for (const key of [...next]) {
                        if (byKey.get(key)?.dependency_keys.some((dependency) => !next.has(dependency))) { next.delete(key); changed = true; }
                    }
                }
                return (hulyProposal?.proposal.tasks ?? []).map((task) => task.key).filter((key) => next.has(key));
            });
            setHulyTaskConfigs((current) => Object.fromEntries(Object.entries(current).map(([key, config]) => [key, { ...config, dependency_keys: config.dependency_keys.filter((dependency) => !excludedKeys.has(dependency)) }])));
        }
        setHulyError("");
        setHulyDialogStage("configure");
    };

    const submitHulyIssues = async () => {
        if (!run || !sessionId || !hulyProposal || !hulyProjectId || hulySubmitting) return;
        if (!selectedProposedTasks.length) { setHulyError("Chọn ít nhất một task cần tạo."); return; }
        const incompleteContent = selectedProposedTasks.filter((task) => !task.title.trim() || !task.outcome.trim() || !task.solution?.trim() || !task.scope_in.length || task.scope_in.some((item) => !item.trim()) || task.scope_out.some((item) => !item.trim()) || !task.acceptance_criteria.length || task.acceptance_criteria.some((item) => !item.trim()));
        if (incompleteContent.length) { setHulyError(`Hoàn thiện kết quả, giải pháp, phạm vi và tiêu chí nghiệm thu cho: ${incompleteContent.map((task) => task.title || task.key).join(", ")}`); return; }
        const missing = selectedProposedTasks.filter((task) => {
            const config = hulyTaskConfigs[task.key];
            return !config?.assignee || !config?.due_date || config.priority === "none" || !task.priority_rationale?.trim();
        });
        if (missing.length) { setHulyError(`Hoàn tất người nhận, deadline, priority và lý do ưu tiên có căn cứ cho: ${missing.map((task) => task.title).join(", ")}`); return; }
        setHulySubmitting(true);
        setHulyError("");
        try {
            const tasks = selectedProposedTasks.map((task) => {
                const config = hulyTaskConfigs[task.key];
                const questionNotes = (hulyProposal.proposal.open_questions ?? []).flatMap((question, index) => {
                    if (!question.affected_task_keys.includes(task.key)) return [];
                    const decision = clarificationDecisions[index];
                    return decision === "confirm" ? [`BA/PO đã xác nhận: ${clarificationAnswers[index]}`] : decision === "defer" ? [`Cần BA/PO xác nhận sau: ${question.question}`] : [];
                });
                return {
                    ...task,
                    ...config,
                    source_titles: task.source_refs.map((ref) => hulyProposal.sources?.[ref] || ref),
                    clarification_notes: questionNotes,
                };
            });
            const result = await api.createHulyIssues(sessionId, { run_id: run.id, project_id: hulyProjectId, epic_id: hulyEpicId, tasks });
            setHulyBulkCreated(result);
        } catch (error) {
            setHulyError(error instanceof Error ? error.message : "Không tạo được task trên Huly.");
        } finally {
            setHulySubmitting(false);
        }
    };

    // The design page posts the element the user clicked; it is attached to the next message.
    useEffect(() => {
        if (!sessionId || typeof BroadcastChannel === "undefined") return;
        const channel = new BroadcastChannel(`designer-canvas-${sessionId}`);
        channel.onmessage = (event: MessageEvent) => {
            if (event.data?.type === "select") {
                setSelection(event.data as CanvasSelection);
                textareaRef.current?.focus();
            }
        };
        return () => channel.close();
    }, [sessionId]);

    const isAgentWorking =
        busy ||
        (run ? ["running", "queued", "starting"].includes(run.status) : false);

    const history = runs
        .filter((item) => item.id !== run?.id)
        .slice()
        .reverse();
    const selectedStyleForRun = (sourceRunId: string) => {
        const sourceIndex = runs.findIndex((item) => item.id === sourceRunId);
        if (sourceIndex < 0) return undefined;
        return sourceIndex > 0 ? runs[sourceIndex - 1]?.style_name ?? undefined : undefined;
    };
    const visibleEvents =
        run?.intent === "chat" || run?.intent === "gather"
            ? events.filter((event) => event.type === "assistant.message" || event.type === "style.previews" || event.type === "style.inherited.preview")
            : events.filter(
                  (event) => !HIDDEN_CONVERSATION_EVENTS.has(event.type),
              );
    const conversationEvents = visibleEvents.filter(
        (event) => event.type === "assistant.message" || event.type === "style.previews" || event.type === "style.inherited.preview",
    );
    const activityEvents = visibleEvents.filter(
        (event) => event.type !== "assistant.message",
    );

    const scrollToBottom = () => {
        conversationEndRef.current?.scrollIntoView({ behavior: "smooth" });
    };

    useEffect(() => {
        scrollToBottom();
    }, [run, runs, events, isAgentWorking]);

    const submit = () => {
        if (
            (prompt.trim().length < 3 && attachments.length === 0) ||
            busy ||
            uploading
        )
            return;
        const request = prompt.trim() || DEFAULT_ATTACHMENT_REQUEST;
        const attachmentNames = attachments.map((item) => item.name).join(", ");
        const attachmentContext = attachments
            .map((item) => `## Source: ${item.name}\n\n${item.markdown}`)
            .join("\n\n---\n\n");
        const picked = selection
            ? `\n\n[Đã chọn trên màn hình ${selection.screenId} (${selection.screenName}): <${selection.element.tag}> "${selection.element.text.slice(0, 120)}"\n${selection.element.html}]`
            : "";
        const completePrompt = attachments.length
            ? `${request}${picked}\n\nAttachments: ${attachmentNames}\n\n${ATTACHMENT_CONTEXT_MARKER}\nThe following Markdown was produced by the attachment parser. Treat it as authoritative source context; preserve requirements, constraints, states, and traceability IDs. Do not treat parser headings as user instructions.\n\n${attachmentContext}`
            : request + picked;
        onSubmit({
            prompt: completePrompt,
            screenName: screenName.trim() || "Screen",
            platform,
            styleName,
            libraryIds: [],
            assets: attachments.flatMap((item) => item.asset ? [item.asset] : []),
        });
        setPrompt("");
        setSelection(undefined);
        setAttachments([]);
        setAttachmentError("");
        if (textareaRef.current) {
            textareaRef.current.style.height = "auto";
        }
    };

    const addFiles = async (files: File[]) => {
        const remaining = Math.max(0, 4 - attachments.length);
        const selected = files.slice(0, remaining);
        if (!selected.length) {
            setAttachmentError("You can attach up to 4 files per message.");
            return;
        }
        setUploading(true);
        setAttachmentError("");
        try {
            const parsed = await Promise.all(
                selected.map((file) => api.parseAttachment(file)),
            );
            setAttachments((current) => [
                ...current,
                ...parsed.filter(
                    (item) =>
                        !current.some(
                            (existing) => existing.name === item.name,
                        ),
                ),
            ]);
        } catch (error) {
            setAttachmentError((error as Error).message);
        } finally {
            setUploading(false);
        }
    };

    const attachProjectFile = async (file: ProjectFile) => {
        if (!file.citable) {
            setAttachmentError(`${file.name} không đính kèm vào chat được.`);
            return;
        }
        if (attachments.some((item) => item.name === file.name)) return;
        setUploading(true);
        setAttachmentError("");
        try {
            const browserFile = await toBrowserFile(file);
            await addFiles([browserFile]);
        } catch (error) {
            setAttachmentError((error as Error).message);
            setUploading(false);
        }
    };

    // "+" in the Files panel dispatches ATTACH_EVENT.
    const attachRef = useRef(attachProjectFile);
    attachRef.current = attachProjectFile;
    useEffect(() => {
        const onAttach = (event: Event) => {
            void attachRef.current((event as CustomEvent<ProjectFile>).detail);
            textareaRef.current?.focus();
        };
        window.addEventListener(ATTACH_EVENT, onAttach);
        return () => window.removeEventListener(ATTACH_EVENT, onAttach);
    }, []);

    const updateMention = (value: string, caret: number) => {
        const match = /(^|\s)@([^\s@]*)$/.exec(value.slice(0, caret));
        if (!match) {
            setMention(null);
            return;
        }
        if (!mention) listProjectFiles().then(setProjectFiles).catch(() => setProjectFiles([]));
        setMention({ start: caret - match[2].length - 1, query: match[2] });
        setMentionIndex(0);
    };

    const pickMention = (file: ProjectFile) => {
        if (!mention) return;
        const caret = mention.start + 1 + mention.query.length;
        const token = `@${file.name} `;
        const next = prompt.slice(0, mention.start) + token + prompt.slice(caret);
        setPrompt(next);
        setMention(null);
        void attachProjectFile(file);
        requestAnimationFrame(() => {
            const el = textareaRef.current;
            if (el) {
                const pos = mention.start + token.length;
                el.focus();
                el.setSelectionRange(pos, pos);
            }
        });
    };

    const selectStarter = (starter: (typeof STARTER_PROMPTS)[number]) => {
        setPrompt(starter.prompt);
        setScreenName(starter.screenName);
        setPlatform(starter.platform);
        if (textareaRef.current) {
            textareaRef.current.focus();
        }
    };

    return (
        <main className="chat-workspace" role="main">
            <section className="conversation" aria-live="polite">
                {!run && history.length === 0 && (
                    <div className="empty-conversation">
                        <span className="empty-orbit">
                            <img
                                src={`${import.meta.env.BASE_URL}logo.png`}
                                alt="Designer Studio"
                                className="empty-orbit-logo"
                            />
                        </span>
                        <h2>What would you like to build?</h2>
                        <p>
                            Describe your target screen, audience, and key
                            actions. The AI designer will turn your requirements
                            into structured, reviewable UI components.
                        </p>
                        <div className="starter-grid">
                            {STARTER_PROMPTS.map((starter) => (
                                <button
                                    key={starter.title}
                                    type="button"
                                    className="starter-card"
                                    onClick={() => selectStarter(starter)}
                                >
                                    <strong>{starter.title}</strong>
                                    <span>{starter.prompt}</span>
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {history.map((item) => (
                    <div className="turn-history" key={item.id}>
                        <div className="user-message-row">
                            <div className="user-bubble-group">
                                <article className="message user-message">
                                    <UserMessageContent prompt={item.prompt} />
                                </article>
                                <span className="user-message-time">
                                    {formatMessageTime(item.created_at)}
                                </span>
                            </div>
                            <UserAvatar />
                        </div>
                        {item.assistant_message && (
                            <article className="message assistant-message">
                                <AssistantAvatar />
                                <div className="assistant-body">
                                    <div className="assistant-header">
                                        <small>Designer</small>
                                    </div>
                                    <MarkdownRenderer
                                        content={item.assistant_message}
                                    />
                                </div>
                            </article>
                        )}
                        {historyEvents.filter((event) => event.run_id === item.id).map((event) => (
                            <article className="message assistant-message style-preview-message" key={event.id}>
                                <AssistantAvatar />
                                <div className="assistant-body">
                                    <div className="assistant-header"><small>Designer</small></div>
                                    {event.type === "style.inherited.preview" ? (
                                        <InheritedStylePreview
                                            event={event}
                                            busy={isAgentWorking}
                                            selectedStyle={selectedStyleForRun(item.id)}
                                            onUse={(option) => onSubmit({
                                                prompt: `Mình chọn phong cách "${option.name}" đã kế thừa từ Project Wiki. Hãy tiếp tục lập UI spec cho feature hiện tại theo BA package.`,
                                                screenName: item.screen_name || "",
                                                platform: item.platform,
                                                styleName: option.key,
                                                libraryIds: [],
                                            })}
                                            onExplore={async () => { await onExploreProjectStyles?.(); }}
                                        />
                                    ) : (
                                        <StylePreviewGallery
                                            event={event}
                                            busy={isAgentWorking}
                                            selectedStyle={selectedStyleForRun(item.id)}
                                            onSelect={(option) => onSubmit({
                                                prompt: `Mình chọn phong cách "${option.name}" trong preview. Áp dụng hướng này cho các screen đã chốt và tiếp tục tạo UI spec.`,
                                                screenName: item.screen_name || "",
                                                platform: item.platform,
                                                styleName: option.key,
                                                libraryIds: [],
                                            })}
                                        />
                                    )}
                                </div>
                            </article>
                        ))}
                    </div>
                ))}

                {run && (
                    <div className="user-message-row">
                        <div className="user-bubble-group">
                            <article className="message user-message">
                                <UserMessageContent prompt={run.prompt} />
                            </article>
                            <span className="user-message-time">
                                {formatMessageTime(run.created_at)}
                            </span>
                        </div>
                        <UserAvatar />
                    </div>
                )}

                {conversationEvents.map((event) =>
                    event.type === "style.inherited.preview" ? (
                        <article className="message assistant-message style-preview-message" key={event.id}>
                            <AssistantAvatar />
                            <div className="assistant-body">
                                <div className="assistant-header"><small>Designer</small></div>
                                <InheritedStylePreview
                                    event={event}
                                    busy={isAgentWorking}
                                    selectedStyle={selectedStyleForRun(event.run_id)}
                                    onUse={(option) => onSubmit({
                                        prompt: `Mình chọn phong cách "${option.name}" đã kế thừa từ Project Wiki. Hãy tiếp tục lập UI spec cho feature hiện tại theo BA package.`,
                                        screenName: run?.screen_name || screenName || "Screen",
                                        platform: run?.platform || platform,
                                        styleName: option.key,
                                        libraryIds: [],
                                    })}
                                    onExplore={async () => {
                                        try {
                                            await onExploreProjectStyles?.();
                                        } catch (error) {
                                            setAttachmentError((error as Error).message);
                                        }
                                    }}
                                />
                            </div>
                        </article>
                    ) :
                    event.type === "style.previews" ? (
                        <article className="message assistant-message style-preview-message" key={event.id}>
                            <AssistantAvatar />
                            <div className="assistant-body">
                                <div className="assistant-header"><small>Designer</small></div>
                                <StylePreviewGallery
                                    event={event}
                                    busy={isAgentWorking}
                                    selectedStyle={selectedStyleForRun(event.run_id)}
                                    onSelect={(option) => onSubmit({
                                        prompt: `Mình chọn phong cách "${option.name}" trong preview. Áp dụng hướng này cho các screen đã chốt và tiếp tục tạo UI spec.`,
                                        screenName: run?.screen_name || screenName || "Screen",
                                        platform: run?.platform || platform,
                                        styleName: option.key,
                                        libraryIds: [],
                                    })}
                                />
                            </div>
                        </article>
                    ) : (
                        <article className="message assistant-message" key={event.id}>
                            <AssistantAvatar />
                            <div className="assistant-body">
                                <div className="assistant-header"><small>Designer</small></div>
                                <MarkdownRenderer content={eventText(event)} />
                            </div>
                        </article>
                    ),
                )}

                {activityEvents.length > 0 && (
                    <details
                        className="activity-log"
                        open={activityLogOpen}
                        onToggle={(event) =>
                            setActivityLogOpen(event.currentTarget.open)
                        }
                    >
                        <summary className="activity-log-summary">
                            <List size={14} />
                            <span>Activity log</span>
                            <span className="activity-log-count">
                                {activityEvents.length}
                            </span>
                        </summary>
                        <div className="activity-log-list">
                            {activityEvents.map((event) => (
                                <div
                                    className={`activity-log-row event-${event.type.replace(".", "-")}`}
                                    key={event.id}
                                >
                                    <span className="event-marker" />
                                    <div>
                                        <small>
                                            {event.type.replaceAll(".", " · ")}
                                        </small>
                                        <p>{eventText(event)}</p>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </details>
                )}

                {(run?.intent === "chat" || run?.intent === "gather") &&
                    run.assistant_message &&
                    !conversationEvents.some(
                        (event) => event.type === "assistant.message",
                    ) && (
                        <article className="message assistant-message">
                            <AssistantAvatar />
                            <div className="assistant-body">
                                <div className="assistant-header">
                                    <small>Designer</small>
                                </div>
                                <MarkdownRenderer
                                    content={run.assistant_message}
                                />
                            </div>
                        </article>
                    )}

                {run?.error && (
                    <article className="message error-message">
                        <small>{run.error.code}</small>
                        <p>{run.error.detail}</p>
                        {run.error.action && <em>{run.error.action}</em>}
                        {(run.status === "failed" ||
                            run.status === "blocked") && (
                            <button
                                className="secondary-button compact"
                                onClick={onRetry}
                                type="button"
                            >
                                Retry this stage
                            </button>
                        )}
                    </article>
                )}

                {run?.status === "cancelled" && !run.error && (
                    <article className="message error-message">
                        <small>RUN CANCELLED</small>
                        <p>The saved design checkpoint is ready to continue.</p>
                        <button
                            className="secondary-button compact"
                            onClick={onRetry}
                            type="button"
                        >
                            Resume saved stage
                        </button>
                    </article>
                )}

                {run?.stage === "review_final" && run.assistant_message && (
                    <article className="message assistant-message">
                        <AssistantAvatar />
                        <div className="assistant-body">
                            <div className="assistant-header">
                                <small>Designer</small>
                            </div>
                            <MarkdownRenderer
                                content={`${run.assistant_message}\n\nĐã hoàn tất tạo các màn hình. Mở trang thiết kế để kiểm tra, sau đó chọn Approve Design để hoàn tất.`}
                            />
                        </div>
                    </article>
                )}

                {canReview && run && (
                    <article
                        className="message assistant-message chat-review-message"
                        aria-label="Review and approve design"
                    >
                        <AssistantAvatar />
                        <div className="assistant-body">
                            <div className="assistant-header">
                                <small>Designer</small>
                                <span className="chat-review-stage-badge">
                                    {run.stage === "review_final"
                                        ? "Final Design Review"
                                        : "Specification Review"}
                                </span>
                            </div>
                            <div className="chat-review-card">
                                <div className="chat-review-card-header">
                                    <div>
                                        <h4 className="chat-review-title">
                                            {run.stage === "review_final"
                                                ? "Review & export design"
                                                : "Approve UI Specification"}
                                        </h4>
                                        <p className="chat-review-subtitle">
                                            {run.stage === "review_final"
                                                ? "Open the design page to try the prototype, pick elements to comment on, then approve or request changes. Export .fig / PNG / HTML from there."
                                                : specScreens.length
                                                  ? `${specScreens.length} screens planned (${specScreens.map((s) => s.name).join(", ")}). Review the specification and approve to build them.`
                                                  : "The UI specification is ready. Approve to build the screens."}
                                        </p>
                                    </div>
                                    {onToggleSidePanel && (
                                        <button
                                            className="chat-review-side-btn"
                                            onClick={onToggleSidePanel}
                                            type="button"
                                            title="Inspect full details in side panel"
                                        >
                                            <Eye size={13} />
                                            <span>View Skeleton Screens</span>
                                        </button>
                                    )}
                                </div>

                                {specScreens.length > 1 && (
                                        <div
                                            style={{
                                                display: "flex",
                                                alignItems: "center",
                                                gap: "6px",
                                                flexWrap: "wrap",
                                                padding: "6px 10px",
                                                borderRadius: "8px",
                                                background: "var(--bg-app)",
                                                fontSize: "12px",
                                                fontWeight: 600,
                                                color: "var(--text-secondary)",
                                            }}
                                        >
                                            <span
                                                style={{
                                                    color: "var(--text-muted)",
                                                    fontSize: "11px",
                                                    fontWeight: 700,
                                                }}
                                            >
                                                Screens:
                                            </span>
                                            {specScreens.map(
                                                (s, idx) => (
                                                    <span
                                                        key={s.id}
                                                        style={{
                                                            padding: "2px 7px",
                                                            borderRadius: "6px",
                                                            background:
                                                                "var(--violet-soft)",
                                                            color: "var(--violet)",
                                                            fontSize: "11.5px",
                                                            fontFamily:
                                                                "var(--font-main)",
                                                        }}
                                                    >
                                                        #{idx + 1} {s.name}
                                                    </span>
                                                ),
                                            )}
                                        </div>
                                    )}

                                <div className="chat-review-feedback-container">
                                    <textarea
                                        className="chat-review-feedback-input"
                                        value={reviewFeedback}
                                        onChange={(e) =>
                                            setReviewFeedback(e.target.value)
                                        }
                                        placeholder="Describe adjustments needed... (optional for Approve, required for Request Changes)"
                                        rows={2}
                                        disabled={submittingReview}
                                    />
                                </div>

                                <div className="chat-review-actions">
                                    {run.stage === "review_final" && onOpenCanvas && (
                                        <button
                                            className="chat-review-btn"
                                            onClick={onOpenCanvas}
                                            disabled={submittingReview}
                                            type="button"
                                        >
                                            <ExternalLink size={15} />
                                            Mở trang thiết kế
                                        </button>
                                    )}
                                    {run.stage === "review_final" && sessionId && (
                                        <a className="chat-review-btn" href={api.exportUrl(sessionId, "fig")}>
                                            <Download size={15} /> .fig
                                        </a>
                                    )}
                                    <button
                                        className="chat-review-btn request-change"
                                        onClick={async () => {
                                            if (submittingReview || !onReview)
                                                return;
                                            setSubmittingReview(true);
                                            try {
                                                await onReview(
                                                    "changes_requested",
                                                    reviewFeedback,
                                                );
                                                setReviewFeedback("");
                                            } finally {
                                                setSubmittingReview(false);
                                            }
                                        }}
                                        disabled={submittingReview}
                                        type="button"
                                    >
                                        <X size={15} /> Request Changes
                                    </button>

                                    <button
                                        className="chat-review-btn approve"
                                        onClick={() => { void approveCurrentReview(); }}
                                        disabled={
                                            finalBlocked || submittingReview || loadingWikiPreview
                                        }
                                        aria-busy={loadingWikiPreview}
                                        title={
                                            finalBlocked
                                                ? "Resolve blocking layout findings first"
                                                : undefined
                                        }
                                        type="button"
                                    >
                                        {loadingWikiPreview
                                            ? <LoaderCircle className="designer-wiki-approval-spinner" size={15} />
                                            : <Check size={15} />}
                                        <span aria-live="polite">{loadingWikiPreview
                                            ? "Loading preview..."
                                            : submittingReview
                                              ? "Processing..."
                                              : run.stage === "review_final"
                                                ? "Approve Design"
                                                : "Approve Structure"}</span>
                                    </button>
                                </div>
                                {wikiApprovalOpen && run.stage === "review_final" && (
                                    <div className="designer-wiki-approval-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !submittingReview) setWikiApprovalOpen(false); }}>
                                        <section className="designer-wiki-approval" role="dialog" aria-modal="true" aria-label="Xác nhận lưu Design System vào Wiki">
                                            <header className="designer-wiki-approval-head"><div><h2>Chốt Designer · Xác nhận Design System vào Wiki</h2><p>Preview lấy từ backend; chính snapshot này sẽ được lưu nếu bạn xác nhận. Bấm từng file để xem.</p></div><button type="button" aria-label="Đóng" onClick={() => setWikiApprovalOpen(false)} disabled={submittingReview}><X size={18} /></button></header>
                                            <div className="designer-wiki-approval-source"><strong>{sessionId ? `Feature · ${sessionId}` : "Design feature"}</strong><span>Design System · v{wikiVersion} · nguồn Designer hiện tại</span><b>Chờ xác nhận</b></div>
                                            {wikiPreviewError && <p className="designer-wiki-approval-note" role="alert">Không lấy được preview chính xác: {wikiPreviewError}</p>}
                                            {designWikiFiles.length > 0 && <div className="designer-wiki-approval-files">
                                                <nav aria-label="File Design System sẽ lưu">{designWikiFiles.map((file, index) => <button key={file.path} type="button" className={wikiPreviewIndex === index ? "is-active" : ""} onClick={() => setWikiPreviewIndex(index)}><strong>{file.path}</strong><span>{file.path.endsWith(".css") ? "Compiled CSS for the selected UI sample" : file.path.endsWith(".jpg") ? "Preview image of the selected UI sample" : file.path.endsWith(".html") ? "Shared HTML template from the selected UI sample" : file.summary}</span></button>)}</nav>
                                                <section className="designer-wiki-approval-preview"><strong>{designWikiFiles[wikiPreviewIndex]?.path}</strong>
                                                    {designWikiFiles[wikiPreviewIndex]?.path.endsWith(".jpg") ? (
                                                        <img src={`data:${designWikiFiles[wikiPreviewIndex]?.mime_type || "image/jpeg"};base64,${designWikiFiles[wikiPreviewIndex]?.content || ""}`} alt="UI sample" />
                                                    ) : <pre>{designWikiFiles[wikiPreviewIndex]?.content}</pre>}
                                                </section>
                                            </div>}
                                            <div className="designer-wiki-approval-meta">project_id · version · status · approved_by · source_feature · applies_to</div>
                                            <p className="designer-wiki-approval-note">Khi xác nhận, contract JSON, Markdown, template HTML và asset manifest được lưu theo version; Basic Memory lập chỉ mục Markdown sau đó.</p>
                                            <footer><span>Wiki chưa được cập nhật. Xác nhận mới duyệt và lưu snapshot đang xem.</span><div><button type="button" onClick={() => setWikiApprovalOpen(false)} disabled={submittingReview}>Để sau</button><button type="button" className="confirm" onClick={() => { void confirmDesignWikiApproval(); }} disabled={submittingReview || designWikiFiles.length === 0}>{submittingReview ? "Đang lưu…" : `Duyệt & lưu Design System v${wikiVersion}`}</button></div></footer>
                                        </section>
                                    </div>
                                )}
                            </div>
                        </div>
                    </article>
                )}

                {finalApproved && run && (
                    <article className="message assistant-message chat-review-message" aria-label="Create Huly issue">
                        <AssistantAvatar />
                        <div className="assistant-body">
                            <div className="assistant-header">
                                <small>Designer</small>
                                <span className="chat-review-stage-badge">Design approved</span>
                            </div>
                            <div className="chat-review-card huly-approved-card">
                                <div className="huly-approved-icon" aria-hidden="true">
                                    <Sparkles size={25} />
                                    <span><Check size={13} strokeWidth={3} /></span>
                                </div>
                                <div className="huly-approved-copy">
                                        <h4 className="chat-review-title">Thiết kế đã được duyệt</h4>
                                        <p className="chat-review-subtitle">Xem lại nội dung task, chọn project Huly rồi xác nhận tạo issue.</p>
                                </div>
                                <button className="chat-review-btn approve huly-create-cta" type="button" onClick={openHulyDialog} disabled={Boolean(hulyCreated)}>
                                    <ClipboardList size={16} />
                                    {hulyCreated ? `Đã tạo ${hulyCreated.identifier}` : "Agent đề xuất task"}
                                </button>
                                {hulyCreated?.url && <a className="huly-created-link" href={hulyCreated.url} target="_blank" rel="noreferrer">Mở task {hulyCreated.identifier} trên Huly</a>}
                            </div>
                        </div>
                    </article>
                )}

                {isAgentWorking && (
                    <article
                        className="message assistant-message typing-indicator-message"
                        aria-label="Designer is thinking"
                    >
                        <span className="assistant-avatar pulse-glow">
                            <Bot size={17} />
                        </span>
                        <div className="assistant-body">
                            <div className="assistant-header">
                                <small>Designer</small>
                                <span className="typing-header-stage">
                                    {getStageBadge(run?.stage)}
                                </span>
                            </div>
                            <div className="typing-bubble">
                                <div className="typing-dots" aria-hidden="true">
                                    <span className="typing-dot" />
                                    <span className="typing-dot" />
                                    <span className="typing-dot" />
                                </div>
                                <span className="typing-status-text">
                                    {getStageStatusText(run?.stage)}
                                </span>
                            </div>
                        </div>
                    </article>
                )}

                <div ref={conversationEndRef} />
            </section>

            <div className="composer-wrapper">
                <section
                    className={`composer ${dragging ? "is-dragging" : ""}`}
                    aria-label="Create design request"
                    onDragEnter={(event) => {
                        event.preventDefault();
                        if (!busy) setDragging(true);
                    }}
                    onDragOver={(event) => event.preventDefault()}
                    onDragLeave={(event) => {
                        if (
                            !event.currentTarget.contains(
                                event.relatedTarget as Node,
                            )
                        ) {
                            setDragging(false);
                        }
                    }}
                    onDrop={(event) => {
                        event.preventDefault();
                        setDragging(false);
                        if (busy) return;
                        const projectFile = readDraggedFile(event.dataTransfer);
                        if (projectFile) void attachProjectFile(projectFile);
                        else void addFiles(Array.from(event.dataTransfer.files));
                    }}
                >
                    {dragging && (
                        <div className="composer-drop-overlay">
                            <Paperclip size={18} /> Drop files to add context
                        </div>
                    )}
                    {(attachments.length > 0 ||
                        uploading ||
                        attachmentError) && (
                        <div className="attachment-strip">
                            {attachments.map((item) => (
                                <span
                                    className="attachment-chip"
                                    key={item.name}
                                >
                                    {item.kind === "image" ? (
                                        <Image size={14} />
                                    ) : (
                                        <FileText size={14} />
                                    )}
                                    <span
                                        title={
                                            item.asset
                                                ? `Asset token: asset::${item.asset.id}`
                                                : item.name
                                        }
                                    >
                                        {item.name}
                                    </span>
                                    {item.truncated && <small>compact</small>}
                                    <button
                                        type="button"
                                        aria-label={`Remove ${item.name}`}
                                        onClick={() =>
                                            setAttachments((current) =>
                                                current.filter(
                                                    (entry) =>
                                                        entry.name !==
                                                        item.name,
                                                ),
                                            )
                                        }
                                    >
                                        <X size={13} />
                                    </button>
                                </span>
                            ))}
                            {uploading && (
                                <span className="attachment-loading">
                                    <LoaderCircle size={14} /> Parsing file…
                                </span>
                            )}
                            {attachmentError && (
                                <span className="attachment-error">
                                    {attachmentError}
                                </span>
                            )}
                        </div>
                    )}
                    {selection && (
                        <div className="attachment-strip">
                            <span className="attachment-chip" title={selection.element.html}>
                                <MousePointerClick size={14} />
                                <span>
                                    {selection.screenName}: {selection.element.text.slice(0, 40) || `<${selection.element.tag}>`}
                                </span>
                                <button type="button" aria-label="Bỏ chọn" onClick={() => setSelection(undefined)}>
                                    <X size={13} />
                                </button>
                            </span>
                        </div>
                    )}
                    <div className="composer-input-area">
                        {mention && (
                            <MentionMenu
                                files={mentionMatches}
                                activeIndex={mentionIndex}
                                query={mention.query}
                                onPick={pickMention}
                                onHover={setMentionIndex}
                            />
                        )}
                        <textarea
                            ref={textareaRef}
                            value={prompt}
                            onBlur={() => setTimeout(() => setMention(null), 150)}
                            onChange={(event) => {
                                setPrompt(event.target.value);
                                updateMention(event.target.value, event.target.selectionStart ?? event.target.value.length);
                                if (textareaRef.current) {
                                    textareaRef.current.style.height = "auto";
                                    textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 140)}px`;
                                }
                            }}
                            onKeyDown={(event) => {
                                if (mention && mentionMatches.length > 0) {
                                    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                                        event.preventDefault();
                                        const step = event.key === "ArrowDown" ? 1 : -1;
                                        setMentionIndex(
                                            (i) => (i + step + mentionMatches.length) % mentionMatches.length,
                                        );
                                        return;
                                    }
                                    if (event.key === "Enter" || event.key === "Tab") {
                                        event.preventDefault();
                                        pickMention(mentionMatches[Math.min(mentionIndex, mentionMatches.length - 1)]);
                                        return;
                                    }
                                }
                                if (mention && event.key === "Escape") {
                                    event.preventDefault();
                                    setMention(null);
                                    return;
                                }
                                if (event.key === "Enter" && !event.shiftKey) {
                                    event.preventDefault();
                                    submit();
                                }
                            }}
                            placeholder="Ask anything, or describe a screen to generate… (@ để trích dẫn file dự án)"
                            maxLength={8000}
                            rows={1}
                        />
                    </div>

                    <div className="composer-bottom-bar">
                        <div className="composer-pills-left">
                            {onOpenCanvas && (
                                <button className="composer-mini-pill" type="button" onClick={onOpenCanvas}
                                    title="Mở trang thiết kế ở tab mới">
                                    <ExternalLink size={14} /> Trang thiết kế
                                </button>
                            )}

                            <StyleSelector
                                value={styleName}
                                onChange={setStyleName}
                            />
                        </div>

                        <div className="composer-actions-right">
                            <input
                                ref={fileInputRef}
                                className="attachment-input"
                                type="file"
                                multiple
                                accept=".png,.jpg,.jpeg,.svg,.webp,.txt,.md,.json,.csv,.yaml,.yml,.bpmn,.bpmn2"
                                onChange={(event) => {
                                    void addFiles(
                                        Array.from(event.target.files ?? []),
                                    );
                                    event.target.value = "";
                                }}
                            />
                            <button
                                className={`composer-attach-btn ${uploading ? "is-uploading" : ""}`}
                                type="button"
                                onClick={() => fileInputRef.current?.click()}
                                disabled={
                                    busy || uploading || attachments.length >= 4
                                }
                                title="Attach requirements, BPMN, JSON, text, Markdown, or image"
                                aria-label="Attach file"
                            >
                                {uploading ? (
                                    <LoaderCircle size={16} />
                                ) : (
                                    <Paperclip size={16} />
                                )}
                            </button>
                            {busy && (
                                <button
                                    className="composer-cancel"
                                    onClick={onCancel}
                                    type="button"
                                >
                                    Cancel
                                </button>
                            )}
                            <span className="composer-shortcut">↵ Enter</span>
                            <button
                                className="composer-send-btn"
                                onClick={submit}
                                disabled={
                                    busy ||
                                    uploading ||
                                    (prompt.trim().length < 3 &&
                                        attachments.length === 0)
                                }
                                title={
                                    busy
                                        ? "Agent is working..."
                                        : "Send prompt (Enter)"
                                }
                                type="button"
                            >
                                <ArrowUp size={16} strokeWidth={2.5} />
                            </button>
                        </div>
                    </div>
                </section>
            </div>
            {hulyDialogOpen && hulyDraft && (
                <div className="huly-dialog-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !hulySubmitting) setHulyDialogOpen(false); }}>
                    <section className={`huly-dialog is-task-flow-dialog${hulyDialogStage === "proposal" ? " is-proposal-dialog" : ""}`} role="dialog" aria-modal="true" aria-labelledby="huly-dialog-title">
                        <header className="huly-dialog-header">
                            <div><h2 id="huly-dialog-title">{{ proposal: "Danh sách task đề xuất", detail: "Chi tiết task", clarify: "Làm rõ BA/PO", configure: "Phân công & Huly", review: "Rà soát trước khi tạo" }[hulyDialogStage]}</h2><p>{hulyDialogStage === "proposal" ? "Rà đề xuất từ BA package và thiết kế đã duyệt; chưa có task nào được tạo." : "Rà soát nội dung và liên kết trước khi tạo task trên Huly."}</p></div>
                            <button className="huly-dialog-close" type="button" onClick={() => setHulyDialogOpen(false)} disabled={hulySubmitting} aria-label="Đóng"><X size={19} /></button>
                        </header>
                        <div className="huly-dialog-body" ref={hulyDialogBodyRef}>
                            <nav className="huly-flow-steps" aria-label="Các bước tạo task Huly">
                                {([ ["proposal", "Đề xuất"], ["detail", "Chi tiết task"], ["clarify", "Làm rõ BA/PO"], ["configure", "Phân công & Huly"], ["review", "Rà soát"] ] as const).map(([step, label], index) => {
                                    const stages = ["proposal", "detail", "clarify", "configure", "review"];
                                    const currentIndex = stages.indexOf(hulyDialogStage);
                                    const stepIndex = stages.indexOf(step);
                                    const skippedClarify = step === "clarify" && !hulyProposal?.proposal.open_questions.length;
                                    return <button key={step} type="button" className={`${step === hulyDialogStage ? "is-current" : ""}${stepIndex < currentIndex ? " is-done" : ""}${skippedClarify ? " is-skipped" : ""}`} onClick={() => { if (stepIndex < currentIndex) setHulyDialogStage(step); }} disabled={stepIndex >= currentIndex || skippedClarify}><span>{index + 1}</span>{label}</button>;
                                })}
                            </nav>
                            {hulyDialogStage === "proposal" && <div className="huly-proposal-content">
                                {hulyLoading && <div className="huly-proposal-loading"><LoaderCircle className="huly-spinner" size={20} /> Đang phân tích BA package và thiết kế đã duyệt…</div>}
                                {!hulyLoading && hulyProposalError && <div className="huly-source-status is-missing"><strong>Chưa tạo được đề xuất task</strong><span>{hulyProposalError}</span><span>Anh có thể tiếp tục với bản nháp một task hiện tại.</span></div>}
                                {!hulyLoading && hulyProposal && <>
                                    {(hulyProposal.proposal_mode === "fallback" || hulyProposal.proposal_mode === "unreviewed") && <div className="huly-proposal-fallback"><span className="huly-fallback-dot" /><span><strong>{hulyProposal.proposal_mode === "fallback" ? "Bản nháp dự phòng" : "Chưa rà soát cách chia task"}</strong> · {hulyProposal.proposal_notice || "Hãy rà lại nội dung và phạm vi trước khi tạo."}</span></div>}
                                    <div className="huly-proposal-heading"><div><Sparkles size={17} /><strong>{hulyProposal.proposal_mode === "fallback" ? "Task từ thiết kế đã duyệt" : "Đề xuất task"}</strong></div><span>{hulyProposal.proposal_reviewed && <b className="huly-proposal-review-badge">Đã rà luồng nghiệp vụ</b>}{hulyProposal.proposal.tasks.length} task <i /> {hulyProposal.source_meta.approved_screens.length} màn hình đã duyệt</span></div>
                                    {hulyProposal.proposal.open_questions.length > 0 && <section className="huly-proposal-questions"><strong>Điểm BA/PO cần xác nhận</strong>{hulyProposal.proposal.open_questions.map((question, index) => <div key={`${question.question}-${index}`}><b>{question.question}</b><span>{question.reason}</span><small>Ảnh hưởng: {question.affected_task_keys.join(", ") || "Cần xác nhận trước khi triển khai"}</small></div>)}</section>}
                                    <div className="huly-proposal-workbench huly-proposal-pick-stage">
                                        <div className="huly-proposal-list-wrap">
                                            <div className="huly-section-label">DANH SÁCH TASK <small>{hulyProposal.proposal.tasks.length}</small></div>
                                            <div className="huly-proposal-list">
                                                {hulyProposal.proposal.tasks.map((task) => <article key={task.key} className={`huly-proposal-card${selectedProposalKeys.includes(task.key) ? " is-selected" : ""}`}>
                                                    <label className="huly-proposal-check"><input type="checkbox" checked={selectedProposalKeys.includes(task.key)} onChange={(event) => toggleProposalTask(task.key, event.target.checked)} aria-label={`Chọn ${task.title}`} /></label>
                                                    <button type="button" className="huly-proposal-card-main" onClick={() => { setSelectedProposalKey(task.key); setHulyDialogStage("detail"); }}>
                                                        <span className="huly-proposal-card-top"><strong>{task.title || "Chưa có tên task"}</strong><small className={`huly-priority is-${task.priority}`}>{proposalPriorityLabel[task.priority]}</small></span>
                                                        <span className="huly-outcome-label">Kết quả công việc</span><span className="huly-proposal-outcome">{task.outcome}</span>
                                                        <span className="huly-proposal-meta">{task.dependency_keys.length ? `${task.dependency_keys.length} phụ thuộc` : "Chưa có phụ thuộc"} <i /> {task.source_refs.length} nguồn <em>Xem chi tiết →</em></span>
                                                    </button>
                                                </article>)}
                                            </div>
                                        </div>
                                        <aside className="huly-selection-summary">
                                            <div className="huly-section-label">TÓM TẮT LỰA CHỌN</div>
                                            <strong>{selectedProposedTasks.length} / {hulyProposal.proposal.tasks.length} task</strong>
                                            <span>{hulyProposal.source_meta.approved_screens.length} màn hình đã duyệt</span>
                                            <div className="huly-selection-metrics"><span>High <b>{selectedProposedTasks.filter((task) => task.priority === "high").length}</b></span><span>Medium <b>{selectedProposedTasks.filter((task) => task.priority === "medium").length}</b></span><span>Chưa đề xuất <b>{selectedProposedTasks.filter((task) => task.priority === "none").length}</b></span><span>Câu hỏi BA/PO <b>{hulyProposal.proposal.open_questions.length}</b></span></div>
                                            {selectedProposedTasks.length === 0 && <p className="huly-selection-warning">Chọn ít nhất một task để tiếp tục.</p>}
                                        </aside>
                                    </div>
                                    <p className="huly-proposal-note">Chọn task cần tạo. Khi chọn task có dependency, các task tiên quyết sẽ được chọn kèm theo.</p>
                                </>}
                            </div>}
                            {hulyDialogStage === "detail" && selectedProposedTask && <div className="huly-stage-content">
                                <button className="huly-back-proposal" type="button" onClick={() => setHulyDialogStage("proposal")}>← Quay lại danh sách task</button>
                                <div className="huly-stage-heading"><div><h3>{selectedProposedTask.title || "Chưa có tên task"}</h3></div><span>Task {Math.max(1, hulyProposal?.proposal.tasks.findIndex((task) => task.key === selectedProposedTask.key) ?? 0) + 1} / {hulyProposal?.proposal.tasks.length ?? 0}</span></div>
                                <div className="huly-detail-grid">
                                    <section className="huly-proposal-editor">
                                        <div className="huly-section-label">NỘI DUNG TASK</div>
                                        <label className="huly-field"><span>Tên task</span><input value={selectedProposedTask.title} maxLength={240} placeholder="Tên đầu ra nghiệp vụ cần bàn giao" onChange={(event) => updateProposedTask(selectedProposedTask.key, { title: event.target.value })} /></label>
                                        <label className="huly-field"><span>Kết quả cần đạt</span><textarea rows={3} value={selectedProposedTask.outcome} onChange={(event) => updateProposedTask(selectedProposedTask.key, { outcome: event.target.value })} /></label>
                                        <label className="huly-field"><span>Hiện trạng <small>Tùy chọn · chỉ ghi khi có căn cứ</small></span><textarea rows={3} value={selectedProposedTask.current_state ?? ""} onChange={(event) => updateProposedTask(selectedProposedTask.key, { current_state: event.target.value })} placeholder="Chưa có thông tin hiện trạng từ nguồn. Bổ sung nếu task thay đổi chức năng đang tồn tại." /></label>
                                        <label className="huly-field"><span>Giải pháp / hành vi cần thực hiện</span><textarea rows={4} value={selectedProposedTask.solution ?? ""} onChange={(event) => updateProposedTask(selectedProposedTask.key, { solution: event.target.value })} /></label>
                                        <div className="huly-proposal-edit-grid">
                                            <label className="huly-field"><span>Phạm vi triển khai</span><textarea rows={5} value={selectedProposedTask.scope_in.join("\n")} onChange={(event) => updateProposedTask(selectedProposedTask.key, { scope_in: event.target.value.split("\n").map((value) => value.trim()).filter(Boolean) })} /></label>
                                            <label className="huly-field"><span>Ngoài phạm vi</span><textarea rows={5} value={selectedProposedTask.scope_out.join("\n")} onChange={(event) => updateProposedTask(selectedProposedTask.key, { scope_out: event.target.value.split("\n").map((value) => value.trim()).filter(Boolean) })} /></label>
                                            <label className="huly-field huly-criteria-field"><span>Tiêu chí nghiệm thu</span><textarea rows={6} value={selectedProposedTask.acceptance_criteria.join("\n")} onChange={(event) => updateProposedTask(selectedProposedTask.key, { acceptance_criteria: event.target.value.split("\n").map((value) => value.trim()).filter(Boolean) })} /></label>
                                        </div>
                                    </section>
                                    <aside className="huly-detail-aside">
                                        <section className="huly-proposal-rationale-panel"><span className="huly-section-label">LÝ DO ƯU TIÊN</span><strong className={`huly-priority is-${selectedProposedTask.priority}`}>{proposalPriorityLabel[selectedProposedTask.priority]}</strong><textarea rows={3} value={selectedProposedTask.priority_rationale ?? ""} onChange={(event) => updateProposedTask(selectedProposedTask.key, { priority_rationale: event.target.value })} placeholder="Nêu tác động hoặc phụ thuộc từ nguồn; để trống nếu chưa đủ căn cứ." /></section>
                                        <section className="huly-source-panel"><div className="huly-section-label">CĂN CỨ CÔNG VIỆC <small>{selectedProposedTask.source_refs.length} mục</small></div><p className="huly-source-summary">{hulyProposal?.source_meta.ba_available ? "Có BA package và thiết kế được duyệt làm căn cứ." : "Chưa có BA package; xem các nguồn thiết kế được liệt kê bên dưới."}</p><details className="huly-source-details"><summary>Xem nội dung tham chiếu</summary><ul>{selectedProposedTask.source_refs.map((ref) => <li key={ref} title={hulyProposal?.sources[ref] || ref}>{hulyProposal?.sources[ref] || ref}</li>)}</ul></details></section>
                                        <section className="huly-source-panel"><div className="huly-section-label">TÀI LIỆU ĐÍNH KÈM <small>{hulyArtifacts?.screens.length ?? 0} màn hình</small></div><div className="huly-attachment-chips huly-attachment-chips-compact">
                                            <button type="button" className="huly-attachment-chip" onClick={() => { setHulyPreviewIndex(0); setHulyPreviewZoom(1); setHulyPreviewTab("png"); }} disabled={!hulyArtifacts?.screens.length}><Image size={15} /><span>PNG thiết kế</span></button>
                                            <button type="button" className="huly-attachment-chip" onClick={() => setHulyPreviewTab("spec")} disabled={!hulyArtifacts}><FileText size={15} /><span>UI spec.md</span></button>
                                            <button type="button" className="huly-attachment-chip" onClick={() => setHulyPreviewTab("tokens")} disabled={!hulyArtifacts?.design_tokens}><Braces size={15} /><span>Style contract</span></button>
                                        </div></section>
                                    </aside>
                                </div>
                            </div>}
                            {hulyDialogStage === "clarify" && <div className="huly-stage-content">
                                <button className="huly-back-proposal" type="button" onClick={() => setHulyDialogStage("detail")}>← Quay lại task</button>
                                <div className="huly-stage-heading"><div><span className="huly-role-badge">BA / PO</span><h3>Làm rõ trước khi tạo task</h3></div><span>{hulyProposal?.proposal.open_questions.length ?? 0} câu hỏi</span></div>
                                <p className="huly-stage-intro">Chọn cách xử lý cho từng điểm chưa rõ. Câu trả lời sẽ được ghi vào task liên quan để người thực hiện có đủ ngữ cảnh.</p>
                                {!hulyProposal?.proposal.open_questions.length ? <div className="huly-empty-state"><Check size={18} /> Không có câu hỏi BA/PO đang mở cho đề xuất này.</div> : hulyProposal.proposal.open_questions.map((question, index) => <section className="huly-clarification-card" key={`${question.question}-${index}`}>
                                    <div><span className="huly-section-label">CÂU HỎI {index + 1}</span><h4>{question.question}</h4><p>{question.reason}</p><small>Ảnh hưởng: {question.affected_task_keys.map((key) => hulyProposal.proposal.tasks.find((task) => task.key === key)?.title || key).join(" · ") || "Toàn bộ task được chọn"}</small></div>
                                    <div className="huly-clarification-options">
                                        <label className={clarificationDecisions[index] === "confirm" ? "is-selected" : ""}><input type="radio" name={`clarification-${index}`} checked={clarificationDecisions[index] === "confirm"} onChange={() => setClarificationDecisions((current) => ({ ...current, [index]: "confirm" }))} /><span><b>Đã có quyết định</b><small>Ghi câu trả lời BA/PO bên dưới</small></span></label>
                                        <label className={clarificationDecisions[index] === "defer" ? "is-selected" : ""}><input type="radio" name={`clarification-${index}`} checked={clarificationDecisions[index] === "defer"} onChange={() => setClarificationDecisions((current) => ({ ...current, [index]: "defer" }))} /><span><b>Để mở và ghi chú vào task</b><small>Người nhận task cần xác nhận sau</small></span></label>
                                        <label className={clarificationDecisions[index] === "exclude" ? "is-selected" : ""}><input type="radio" name={`clarification-${index}`} checked={clarificationDecisions[index] === "exclude"} onChange={() => setClarificationDecisions((current) => ({ ...current, [index]: "exclude" }))} /><span><b>Bỏ task bị ảnh hưởng</b><small>Không tạo các task liên quan câu hỏi này</small></span></label>
                                    </div>
                                    {clarificationDecisions[index] === "confirm" && <label className="huly-field"><span>Quyết định BA/PO</span><textarea rows={3} value={clarificationAnswers[index] ?? ""} onChange={(event) => setClarificationAnswers((current) => ({ ...current, [index]: event.target.value }))} placeholder="Nhập câu trả lời đã được xác nhận..." /></label>}
                                </section>)}
                            </div>}
                            {hulyDialogStage === "configure" && <div className="huly-stage-content">
                                <button className="huly-back-proposal" type="button" onClick={() => setHulyDialogStage(hulyProposal?.proposal.open_questions.length ? "clarify" : "detail")}>← Quay lại bước trước</button>
                                <div className="huly-stage-heading"><div><span className="huly-role-badge">HULY</span><h3>Phân công & Huly</h3></div><span>{selectedProposedTasks.length} task được chọn</span></div>
                                <div className="huly-config-shared-grid">
                                    <label className="huly-field"><span>Project Huly *</span><select value={hulyProjectId} onChange={(event) => setHulyProjectId(event.target.value)} disabled={hulySubmitting}>{hulyProjects.map((project) => <option key={project.id} value={project.id}>{project.name} ({project.identifier})</option>)}</select></label>
                                    <label className="huly-field"><span>Epic cha <small>Tùy chọn</small></span><select value={hulyEpicId} onChange={(event) => setHulyEpicId(event.target.value)} disabled={hulyEpicsLoading || hulySubmitting || !hulyProjectId}><option value="">Không gắn Epic</option>{hulyEpics.map((epic) => <option key={epic.identifier} value={epic.identifier}>{epic.identifier} · {epic.title}</option>)}</select>{hulyEpicError && <small className="huly-field-error">{hulyEpicError}</small>}</label>
                                </div>
                                {hulyMetadataLoading && <p className="huly-metadata-loading">Đang đọc workflow và metadata từ Huly…</p>}
                                {!hulyMetadataLoading && hulyMetadata && <div className="huly-task-config-list">{selectedProposedTasks.map((task) => {
                                    const config = hulyTaskConfigs[task.key] ?? { assignee: "", due_date: "", priority: task.priority, status: hulyMetadata.default_status, task_type: hulyMetadata.task_types[0]?.name || "", labels: [], component: "", milestone: "", estimation_hours: null, dependency_keys: task.dependency_keys, related_issue_ids: [] };
                                    const candidates = selectedProposedTasks.filter((candidate) => candidate.key !== task.key);
                                    const selectedRelated = config.related_issue_ids.map((id) => relatedOptions[task.key]?.find((issue) => issue.identifier === id) || { identifier: id, title: id });
                                    return <article className="huly-task-config-card" key={task.key}>
                                        <header><span className="huly-role-badge">{config.task_type || "Chưa chọn loại"}</span><strong>{task.title}</strong><span className={`huly-priority is-${config.priority}`}>{proposalPriorityLabel[config.priority]}</span></header>
                                        <div className="huly-task-config-fields">
                                            <label className="huly-field"><span>Người nhận *</span><select value={config.assignee} onChange={(event) => updateTaskConfig(task.key, { assignee: event.target.value })}><option value="">Chọn người nhận</option>{hulyMembers.map((member) => <option key={member.email || member.name} value={member.name}>{member.name}</option>)}</select></label>
                                            <label className="huly-field"><span>Deadline *</span><input type="date" value={config.due_date} onChange={(event) => updateTaskConfig(task.key, { due_date: event.target.value })} /></label>
                                            <label className="huly-field"><span>Ưu tiên *</span><select value={config.priority} onChange={(event) => updateTaskConfig(task.key, { priority: event.target.value as HulyTaskConfig["priority"] })}><option value="none">Chọn mức</option><option value="urgent">Urgent</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label>
                                            <label className="huly-field"><span>Trạng thái · Huly</span><select value={config.status} onChange={(event) => updateTaskConfig(task.key, { status: event.target.value })}>{hulyMetadata.statuses.map((option) => { const value = option.name || option.title || option.identifier || option.id || ""; return <option key={value} value={value}>{option.name || option.title || option.identifier || value}</option>; })}</select></label>
                                            <details className="huly-native-extra"><summary>Metadata Huly và liên kết khác</summary><div className="huly-task-config-fields">
                                                <label className="huly-field"><span>Loại task</span><select value={config.task_type} onChange={(event) => updateTaskConfig(task.key, { task_type: event.target.value })}>{hulyMetadata.task_types.map((option) => { const value = option.name || option.title || option.identifier || option.id || ""; return <option key={value} value={value}>{option.name || option.title || option.identifier || value}</option>; })}</select></label>
                                                {hulyMetadata.components.length > 0 && <label className="huly-field"><span>Component</span><select value={config.component} onChange={(event) => updateTaskConfig(task.key, { component: event.target.value })}><option value="">Không chọn</option>{hulyMetadata.components.map((option) => { const value = option.name || option.title || option.identifier || option.id || ""; return <option key={value} value={value}>{option.name || option.title || option.identifier || value}</option>; })}</select></label>}
                                                {hulyMetadata.milestones.length > 0 && <label className="huly-field"><span>Milestone</span><select value={config.milestone} onChange={(event) => updateTaskConfig(task.key, { milestone: event.target.value })}><option value="">Không chọn</option>{hulyMetadata.milestones.map((option) => { const value = option.name || option.title || option.identifier || option.id || ""; return <option key={value} value={value}>{option.name || option.title || option.identifier || value}</option>; })}</select></label>}
                                                <label className="huly-field"><span>Estimation (giờ)</span><input type="number" min="0" step="0.5" value={config.estimation_hours ?? ""} onChange={(event) => updateTaskConfig(task.key, { estimation_hours: event.target.value === "" ? null : Number(event.target.value) })} placeholder="Chưa ước lượng" /></label>
                                            </div>
                                            {hulyMetadata.labels.length > 0 && <fieldset className="huly-label-options"><legend>Labels</legend>{hulyMetadata.labels.map((option) => { const value = option.name || option.title || option.identifier || option.id || ""; return <label key={value}><input type="checkbox" checked={config.labels.includes(value)} onChange={(event) => updateTaskConfig(task.key, { labels: event.target.checked ? [...config.labels, value] : config.labels.filter((label) => label !== value) })} />{option.name || option.title || value}</label>; })}</fieldset>}
                                            {hulyMetadata.labels.length === 0 && <small className="huly-field-hint">Project chưa có label nào được cấu hình trong Huly.</small>}
                                            <div className="huly-related-picker"><label className="huly-field"><span>Issue liên quan · Related</span><input value={relatedQueries[task.key] || ""} onChange={(event) => void searchRelatedIssues(task.key, event.target.value)} placeholder="Tìm theo mã hoặc tiêu đề Huly…" /></label>{(relatedOptions[task.key] || []).filter((issue) => !config.related_issue_ids.includes(issue.identifier)).map((issue) => <button type="button" key={issue.identifier} onClick={() => { updateTaskConfig(task.key, { related_issue_ids: [...config.related_issue_ids, issue.identifier] }); setRelatedQueries((current) => ({ ...current, [task.key]: "" })); setRelatedOptions((current) => ({ ...current, [task.key]: [] })); }}>{issue.identifier} · {issue.title}</button>)}<div className="huly-related-chips">{selectedRelated.map((issue) => <button type="button" key={issue.identifier} onClick={() => updateTaskConfig(task.key, { related_issue_ids: config.related_issue_ids.filter((id) => id !== issue.identifier) })}>{issue.identifier} ×</button>)}</div></div>
                                            </details>
                                            <details className="huly-dependency-picker"><summary>Task tiên quyết · {config.dependency_keys.length || "không có"}</summary>{candidates.length ? candidates.map((candidate) => <label key={candidate.key}><input type="checkbox" checked={config.dependency_keys.includes(candidate.key)} onChange={(event) => updateTaskConfig(task.key, { dependency_keys: event.target.checked ? [...config.dependency_keys, candidate.key] : config.dependency_keys.filter((key) => key !== candidate.key) })} />{candidate.title}</label>) : <small>Không có task khác để liên kết.</small>}</details>
                                        </div>
                                    </article>;
                                })}</div>}                                {hulyError && <p className="huly-dialog-error" role="alert">{hulyError}</p>}
                            </div>}
                            {hulyDialogStage === "review" && <div className="huly-stage-content">
                                <button className="huly-back-proposal" type="button" onClick={() => setHulyDialogStage("configure")}>← Quay lại phân công</button>
                                <div className="huly-stage-heading"><div><span className="huly-role-badge">RÀ SOÁT</span><h3>Phân công {selectedProposedTasks.length} task trong Huly</h3></div><span>Chưa tạo task</span></div>
                                <div className="huly-review-summary"><strong>{selectedProposedTasks.length} task được chọn</strong><span>{hulyProjects.find((project) => project.id === hulyProjectId)?.name || "Chưa chọn project"}{hulyEpicId ? ` · Epic ${hulyEpicId}` : " · Không gắn Epic"}</span></div>
                                <div className="huly-bulk-table-wrap"><table className="huly-bulk-table huly-review-table"><thead><tr><th>#</th><th>Nội dung task</th><th>Loại</th><th>Người nhận</th><th>Deadline</th><th>Ưu tiên</th><th>Trạng thái</th><th>Task tiên quyết</th></tr></thead><tbody>{selectedProposedTasks.map((task, index) => { const config = hulyTaskConfigs[task.key]; return <tr key={task.key}><td>{index + 1}</td><td><strong>{task.title}</strong></td><td>{config?.task_type || "Chưa chọn"}</td><td>{hulyMembers.find((member) => member.name === config?.assignee)?.name || "Chưa giao"}</td><td>{config?.due_date || "Chưa đặt"}</td><td><span className={`huly-priority is-${config?.priority ?? "none"}`}>{proposalPriorityLabel[config?.priority ?? "none"]}</span></td><td>{config?.status || "Chưa chọn"}</td><td>{config?.dependency_keys.map((key) => hulyProposal?.proposal.tasks.find((candidate) => candidate.key === key)?.title || key).join(", ") || "Không có"}</td></tr>; })}</tbody></table></div>
                                {hulyBulkCreated && <div className="huly-source-status is-linked"><strong>Đã tạo {hulyBulkCreated.created.length}/{selectedProposedTasks.length} task</strong>{hulyBulkCreated.created.map((item) => <div key={item.key}>{item.url ? <a href={item.url} target="_blank" rel="noreferrer">{item.identifier}</a> : <strong>{item.identifier}</strong>}{item.relation_errors.map((error, index) => <span className="huly-dialog-error" key={`${item.key}-relation-${index}`}>{error}</span>)}</div>)}{hulyBulkCreated.errors.map((item) => <span className="huly-dialog-error" key={item.key}>{item.title}: {item.error}</span>)}</div>}
                                {hulyError && <p className="huly-dialog-error" role="alert">{hulyError}</p>}
                            </div>}
                            <div className="huly-create-fields" style={{ display: "none" }}>
                            <button className="huly-back-proposal" type="button" onClick={() => setHulyDialogStage("proposal")}>← Quay lại đề xuất task</button>
                            <label className="huly-field"><span>Project Huly</span>
                                <select value={hulyProjectId} onChange={(event) => setHulyProjectId(event.target.value)} disabled={hulyLoading || hulySubmitting || hulyProjects.length === 0}>
                                    {hulyProjects.length === 0 && <option value="">{hulyLoading ? "Đang tải project..." : "Không có project khả dụng"}</option>}
                                    {hulyProjects.map((project) => <option key={project.id} value={project.id}>{project.name}{project.identifier ? ` (${project.identifier})` : ""}</option>)}
                                </select>
                            </label>
                            <label className="huly-field"><span>Epic cha <small>Tùy chọn</small></span>
                                <select value={hulyEpicId} onChange={(event) => { setHulyEpicId(event.target.value); setHulyDraft({ ...hulyDraft, epic_id: event.target.value }); }} disabled={hulyEpicsLoading || hulySubmitting || !hulyProjectId}>
                                    <option value="">Không gắn Epic</option>
                                    {hulyEpics.map((epic) => <option key={epic.identifier} value={epic.identifier}>{epic.identifier} · {epic.title}</option>)}
                                </select>
                                {hulyEpicsLoading && <small className="huly-field-hint">Đang tải Epic của project...</small>}
                                {hulyEpicError && <small className="huly-field-error">{hulyEpicError}</small>}
                            </label>
                            <div className="huly-task-meta-grid">
                                <label className="huly-field"><span>Người được giao</span>
                                    <select value={hulyDraft.assignee} onChange={(event) => setHulyDraft({ ...hulyDraft, assignee: event.target.value })} disabled={hulyLoading || hulySubmitting}>
                                        <option value="">Chưa giao</option>
                                        {hulyMembers.map((member) => <option key={member.email || member.name} value={member.name}>{member.name}{member.email ? ` · ${member.email}` : ""}</option>)}
                                    </select>
                                </label>
                                <label className="huly-field"><span>Priority</span>
                                    <select value={hulyDraft.priority} onChange={(event) => setHulyDraft({ ...hulyDraft, priority: event.target.value as HulyIssueDraft["priority"] })} disabled={hulySubmitting}>
                                        <option value="urgent">Urgent</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option><option value="none">Chưa đặt</option>
                                    </select>
                                </label>
                                <label className="huly-field"><span>Deadline</span>
                                    <input type="date" value={hulyDraft.due_date} onChange={(event) => setHulyDraft({ ...hulyDraft, due_date: event.target.value })} disabled={hulySubmitting} />
                                </label>
                            </div>
                            <label className="huly-field"><span>Tên task <small>{hulyDraft.title.length}/80 ký tự khuyến nghị</small></span><input value={hulyDraft.title} maxLength={240} onChange={(event) => setHulyDraft({ ...hulyDraft, title: event.target.value })} /></label>
                            <label className="huly-field"><span>Mục tiêu</span><textarea rows={6} value={hulyDraft.objective} onChange={(event) => setHulyDraft({ ...hulyDraft, objective: event.target.value })} /></label>
                            <label className="huly-field"><span>Hiện trạng</span><textarea rows={6} value={hulyDraft.current_state} onChange={(event) => setHulyDraft({ ...hulyDraft, current_state: event.target.value })} /></label>
                            <label className="huly-field"><span>Giải pháp</span><textarea rows={8} value={hulyDraft.solution} onChange={(event) => setHulyDraft({ ...hulyDraft, solution: event.target.value })} /></label>
                            <label className="huly-field"><span>Tiêu chí chấp nhận (AC)</span><textarea rows={8} value={hulyDraft.acceptance_criteria} onChange={(event) => setHulyDraft({ ...hulyDraft, acceptance_criteria: event.target.value })} /></label>
                            <section className="huly-attachments-preview" aria-label="Tài liệu thiết kế xem trước">
                                <div className="huly-attachments-heading">
                                    <strong><Paperclip size={15} /> Tài liệu đính kèm</strong>
                                    <span>Bấm để xem trước</span>
                                </div>
                                {hulyArtifacts ? (
                                    <div className="huly-attachment-chips">
                                        <button type="button" className="huly-attachment-chip" onClick={() => { setHulyPreviewIndex(0); setHulyPreviewZoom(1); setHulyPreviewTab("png"); }} disabled={!hulyArtifacts.screens.length}>
                                            <Image size={16} /><span>PNG màn hình</span><small>{hulyArtifacts.screens.length}</small>
                                        </button>
                                        <button type="button" className="huly-attachment-chip" onClick={() => setHulyPreviewTab("spec")}>
                                            <FileText size={16} /><span>UI spec .md</span><small>1</small>
                                        </button>
                                        <button type="button" className={`huly-attachment-chip${hulyArtifacts.design_tokens ? "" : " is-unavailable"}`} onClick={() => setHulyPreviewTab("tokens")}>
                                            <Braces size={16} /><span>Style contract.json</span><small>{hulyArtifacts.design_tokens ? "1" : "Chưa có"}</small>
                                        </button>
                                    </div>
                                ) : (
                                    <p className="huly-preview-loading">{hulyPreviewError || "Đang chuẩn bị tài liệu xem trước..."}</p>
                                )}
                                <p className="huly-preview-note">Xem trước artifact đã tạo từ thiết kế được duyệt.</p>
                            </section>
                            {hulyError && <p className="huly-dialog-error" role="alert">{hulyError}</p>}
                            {hulyCreated && <p className="huly-dialog-success" role="status">Đã tạo task {hulyCreated.identifier}{hulyCreated.parent_epic ? ` và gắn vào Epic ${hulyCreated.parent_epic}` : ""}; đính kèm {hulyCreated.attachments?.length ?? 0} tài liệu.</p>}
                            {hulyCreated?.parent_error && <p className="huly-dialog-error" role="alert">Task {hulyCreated.identifier} đã được tạo nhưng chưa gắn được vào Epic: {hulyCreated.parent_error} Mở task trên Huly để gắn Epic thủ công; không gửi lại form để tránh tạo trùng.</p>}
                            {Boolean(hulyCreated?.attachment_errors?.length) && <p className="huly-dialog-error" role="alert">Task đã được tạo nhưng một số file chưa đính kèm: {hulyCreated?.attachment_errors?.map((item) => item.filename).join(", ")}. Mở task Huly để kiểm tra trước khi thử lại.</p>}
                            </div>
                        </div>
                        <footer className="huly-dialog-footer huly-flow-footer">
                            <button className="chat-review-btn" type="button" onClick={() => setHulyDialogOpen(false)} disabled={hulySubmitting}>Đóng</button>
                            {hulyDialogStage === "proposal" && <button className="chat-review-btn approve" type="button" onClick={continueWithSelectedProposal} disabled={!selectedProposedTasks.length}>Xem chi tiết task →</button>}
                            {hulyDialogStage === "detail" && <><button className="chat-review-btn" type="button" onClick={() => setHulyDialogStage("proposal")}>Quay lại danh sách</button><button className="chat-review-btn approve" type="button" onClick={() => { setHulyError(""); setHulyDialogStage(hulyProposal?.proposal.open_questions.length ? "clarify" : "configure"); }} disabled={!selectedProposedTasks.length}>Tiếp tục: {hulyProposal?.proposal.open_questions.length ? "làm rõ BA/PO" : "phân công"} →</button></>}
                            {hulyDialogStage === "clarify" && <><button className="chat-review-btn" type="button" onClick={() => setHulyDialogStage("detail")}>Quay lại task</button><button className="chat-review-btn approve" type="button" onClick={continueFromClarifications}>Tiếp tục cấu hình →</button></>}
                            {hulyDialogStage === "configure" && <><button className="chat-review-btn" type="button" onClick={() => setHulyDialogStage(hulyProposal?.proposal.open_questions.length ? "clarify" : "detail")}>Quay lại bước trước</button><button className="chat-review-btn approve" type="button" onClick={() => { setHulyError(""); setHulyDialogStage("review"); }} disabled={!selectedProposedTasks.length}>Rà soát lựa chọn →</button></>}
                            {hulyDialogStage === "review" && (hulyBulkCreated ? <button className="chat-review-btn approve" type="button" onClick={() => setHulyDialogOpen(false)}>Hoàn tất</button> : <><button className="chat-review-btn" type="button" onClick={() => setHulyDialogStage("configure")}>Quay lại phân công</button><button className="chat-review-btn approve" type="button" onClick={submitHulyIssues} disabled={hulySubmitting || !selectedProposedTasks.length || !hulyProjectId || selectedProposedTasks.some((task) => { const config = hulyTaskConfigs[task.key]; return !config?.assignee || !config?.due_date || config.priority === "none"; })}>{hulySubmitting ? <><LoaderCircle className="huly-spinner" size={15} /> Đang tạo task...</> : <>✓ Tạo {selectedProposedTasks.length} task trên Huly</>}</button></>)}
                        </footer>
                    </section>
                </div>
            )}
            {hulyPreviewTab && hulyArtifacts && (
                <div className="huly-preview-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setHulyPreviewTab(null); }}>
                    <section className={`huly-preview-dialog${hulyPreviewTab === "png" ? " is-png-preview" : ""}${hulyPreviewTab === "png" && hulyPngMode === "overview" ? " is-flow-overview" : ""}${hulyPngImmersive ? " is-png-immersive" : ""}`} role="dialog" aria-modal="true" aria-label="Xem trước tài liệu đính kèm">
                        <header className="huly-preview-header">
                            <div className="huly-preview-title-icon">{hulyPreviewTab === "png" ? <Image size={18} /> : hulyPreviewTab === "spec" ? <FileText size={18} /> : <Braces size={18} />}</div>
                            <div className="huly-preview-title-copy">
                                <h2>{hulyPreviewTab === "png" ? `PNG màn hình (${hulyArtifacts.screens.length})` : hulyPreviewTab === "spec" ? "UI spec.md" : "style-contract.json"}</h2>
                                <p>{hulyPreviewTab === "png" ? "Xem từng màn hình trước khi tạo task Huly" : hulyPreviewTab === "spec" ? "Đặc tả các màn hình, nội dung mặc định và trạng thái đã duyệt" : "Bảng màu và định hướng thị giác; chưa phải token CSS đầy đủ"}</p>
                            </div>
                            <button type="button" className="huly-dialog-close" onClick={() => setHulyPreviewTab(null)} aria-label="Đóng xem trước"><X size={19} /></button>
                        </header>
                        <nav className="huly-preview-tabs" aria-label="Loại tài liệu">
                            <button type="button" className={hulyPreviewTab === "png" ? "active" : ""} onClick={() => setHulyPreviewTab("png")}><Image size={15} /> PNG màn hình</button>
                            <button type="button" className={hulyPreviewTab === "spec" ? "active" : ""} onClick={() => setHulyPreviewTab("spec")}><FileText size={15} /> UI spec .md</button>
                            <button type="button" className={hulyPreviewTab === "tokens" ? "active" : ""} onClick={() => setHulyPreviewTab("tokens")}><Braces size={15} /> Style contract</button>
                        </nav>
                        {hulyPreviewTab === "png" && (
                            <div className="huly-png-preview-body">
                                {hulyArtifacts.screens.length ? (
                                    <>
                                        <div className="huly-png-mode-switch" role="tablist" aria-label="Cách xem màn hình">
                                            <button type="button" className={hulyPngMode === "overview" ? "active" : ""} onClick={() => setHulyPngMode("overview")}><Workflow size={15} /> Tổng quan luồng</button>
                                            <button type="button" className={hulyPngMode === "screen" ? "active" : ""} onClick={() => setHulyPngMode("screen")}><Image size={15} /> Từng màn hình</button>
                                        </div>
                                        {hulyPngMode === "overview" ? (
                                            <div className="huly-flow-overview">
                                                <div className="huly-flow-overview-meta"><span>{hulyArtifacts.screens[0]?.platform === "mobile" ? "Mobile" : hulyArtifacts.screens[0]?.platform} · {hulyArtifacts.screens[0]?.width}px</span><span>{hulyArtifacts.screens.length} màn hình · bấm vào màn hình để xem chi tiết</span></div>
                                                <div className="huly-flow-overview-track">
                                                    {hulyArtifacts.screens.map((screen, index) => (
                                                        <div className="huly-flow-step" key={screen.id}>
                                                            <button type="button" className="huly-flow-screen" onClick={() => { setHulyPreviewIndex(index); setHulyPreviewZoom(1); setHulyPngMode("screen"); }}>
                                                                <span className="huly-flow-screen-title"><b>{index + 1}</b><strong>{screen.name}</strong></span>
                                                                <span className="huly-flow-screen-image"><img src={api.screenshotUrl(sessionId!, screen.id, undefined, 480)} alt={`Tổng quan: ${screen.name}`} loading={index < 2 ? "eager" : "lazy"} /></span>
                                                            </button>
                                                            {index < hulyArtifacts.screens.length - 1 && <span className="huly-flow-connector" aria-hidden="true">→</span>}
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        ) : (
                                        <>
                                        <div className="huly-png-toolbar">
                                            <div><strong>{hulyArtifacts.screens[hulyPreviewIndex]?.name}</strong><span>Màn hình {hulyPreviewIndex + 1}/{hulyArtifacts.screens.length}</span></div>
                                            <div className="huly-zoom-controls">
                                                <button type="button" onClick={() => setHulyPreviewZoom((zoom) => Math.max(0.5, Number((zoom - 0.1).toFixed(1))))} aria-label="Thu nhỏ"><ZoomOut size={16} /></button>
                                                <span>{Math.round(hulyPreviewZoom * 100)}%</span>
                                                <button type="button" onClick={() => setHulyPreviewZoom((zoom) => Math.min(2, Number((zoom + 0.1).toFixed(1))))} aria-label="Phóng to"><ZoomIn size={16} /></button>
                                                <button type="button" onClick={() => setHulyPngImmersive((value) => !value)} aria-label={hulyPngImmersive ? "Thoát toàn màn hình" : "Xem toàn màn hình"}>{hulyPngImmersive ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button>
                                            </div>
                                        </div>
                                        <div className="huly-png-canvas">
                                            <button type="button" className="huly-png-nav previous" onClick={() => setHulyPreviewIndex((index) => (index + hulyArtifacts.screens.length - 1) % hulyArtifacts.screens.length)} aria-label="Màn hình trước"><ChevronLeft size={20} /></button>
                                            <img key={hulyArtifacts.screens[hulyPreviewIndex].id} src={api.screenshotUrl(sessionId!, hulyArtifacts.screens[hulyPreviewIndex].id)} alt={`PNG xem trước: ${hulyArtifacts.screens[hulyPreviewIndex].name}`} style={{ zoom: hulyPreviewZoom }} />
                                            <button type="button" className="huly-png-nav next" onClick={() => setHulyPreviewIndex((index) => (index + 1) % hulyArtifacts.screens.length)} aria-label="Màn hình tiếp theo"><ChevronRight size={20} /></button>
                                        </div>
                                        <div className="huly-png-thumbnails">
                                            {hulyArtifacts.screens.map((screen, index) => (
                                                <button type="button" key={screen.id} className={index === hulyPreviewIndex ? "active" : ""} onClick={() => { setHulyPreviewIndex(index); setHulyPreviewZoom(1); }}>
                                                    <img src={api.screenshotUrl(sessionId!, screen.id, undefined, 320)} alt="" loading="lazy" />
                                                    <span>{screen.name}</span>
                                                </button>
                                            ))}
                                        </div>
                                        </>
                                        )}
                                    </>
                                ) : <p className="huly-preview-empty">Session chưa có PNG màn hình.</p>}
                            </div>
                        )}
                        {hulyPreviewTab === "spec" && <div className="huly-document-preview"><MarkdownRenderer content={hulyArtifacts.spec_markdown} className="huly-preview-markdown" /></div>}
                        {hulyPreviewTab === "tokens" && (
                            <div className="huly-document-preview">
                                {hulyArtifacts.design_tokens ? <pre className="huly-json-preview"><code>{JSON.stringify(hulyArtifacts.design_tokens, null, 2)}</code></pre> : <div className="huly-preview-empty"><Braces size={24} /><strong>Chưa có style contract</strong><span>Session này chưa có style contract chứa palette hoặc font.</span></div>}
                            </div>
                        )}
                        <footer className="huly-preview-footer">
                            <span>{hulyPreviewTab === "png" ? `${hulyArtifacts.screens.length} PNG · ${hulyPngMode === "overview" ? "tổng quan luồng" : `màn hình ${hulyPreviewIndex + 1} đang xem`}` : hulyPreviewTab === "spec" ? "UI spec · Markdown preview" : "Style contract · JSON preview"}</span>
                            <button type="button" className="chat-review-btn" onClick={() => setHulyPreviewTab(null)}>Đóng xem trước</button>
                        </footer>
                    </section>
                </div>
            )}
        </main>
    );
}
