import type {
    DesignSystemInfo,
    ExactAsset,
    ParsedAttachment,
    Run,
    RunArtifacts,
    Screen,
    ScreenVersion,
    Session,
} from "./types";

import { projectId } from "./platform";

const API_BASE = import.meta.env.VITE_API_BASE_URL;

if (!API_BASE) throw new Error("VITE_API_BASE_URL is required");

async function failure(response: Response): Promise<Error> {
    const problem = (await response
        .json()
        .catch(() => ({ detail: response.statusText }))) as {
        detail?: string | { loc?: (string | number)[]; msg?: string }[] | { code?: string; message?: string };
        title?: string;
    };
    const detail = problem.detail;
    if (!Array.isArray(detail) && typeof detail === "object" && detail?.code === "HULY_RESPONSE_UNVERIFIED") {
        return new Error("Huly chưa trả mã task nên Agent Designer chưa xác minh được kết quả. Lệnh tạo có thể đã được gửi; hãy kiểm tra project Huly trước khi thử lại để tránh tạo trùng.");
    }
    const message = Array.isArray(detail)
        ? detail
              .map(
                  (item) =>
                      `${item.loc?.slice(1).join(".") || "request"}: ${item.msg || "invalid value"}`,
              )
              .join("; ")
        : typeof detail === "object" && detail
          ? detail.message || problem.title || "Request failed"
          : detail || problem.title || "Request failed";
    return new Error(message);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, {
        ...init,
        headers: { "Content-Type": "application/json", ...init?.headers },
    });
    if (!response.ok) throw await failure(response);
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
}

async function text(path: string): Promise<string> {
    const response = await fetch(`${API_BASE}${path}`);
    if (!response.ok) throw await failure(response);
    return response.text();
}

function fileAsBase64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error(`Could not read ${file.name}`));
        reader.onload = () => {
            const value = String(reader.result ?? "");
            resolve(value.slice(value.indexOf(",") + 1));
        };
        reader.readAsDataURL(file);
    });
}

const projectQuery = () => (projectId ? `?project_id=${encodeURIComponent(projectId)}` : "");

export const api = {
    getProjectWiki: (projectId: string) =>
        request<ProjectWikiResponse>(`/project-wiki?project_id=${encodeURIComponent(projectId)}`),
    projectWikiPreview: (runId: string) =>
        request<{ run_id: string; revision: number; project_id: string; version: number; approved_at: string;
            files: { path: string; content: string; screen_name?: string; platform?: string; mime_type?: string }[] }>(
            `/runs/${runId}/project-wiki-preview`,
        ),
    restoreProjectWiki: (projectId: string, version: number) =>
        request<{ project_id: string; restored_from: number; version: number }>(
            `/project-wiki/restore?project_id=${encodeURIComponent(projectId)}`,
            { method: "POST", body: JSON.stringify({ version }) },
        ),
    createStylePreviews: (sessionId: string) =>
        request<Run>(`/sessions/${sessionId}/style-previews`, { method: "POST" }),
    parseAttachment: async (file: File) =>
        request<ParsedAttachment>(`/attachments/parse${projectQuery()}`, {
            method: "POST",
            body: JSON.stringify({
                name: file.name,
                mime_type: file.type,
                data_base64: await fileAsBase64(file),
            }),
        }),
    listSessions: () => request<Session[]>(`/sessions${projectQuery()}`),
    getSessions: () => request<Session[]>(`/sessions${projectQuery()}`),
    createSession: (title: string) =>
        request<Session>("/sessions", {
            method: "POST",
            body: JSON.stringify({ title, project_id: projectId || undefined }),
        }),
    deleteSession: (sessionId: string) =>
        request<{ status: string }>(`/sessions/${sessionId}`, {
            method: "DELETE",
        }),
    listRuns: (sessionId: string) =>
        request<Run[]>(`/sessions/${sessionId}/runs`),
    getRun: (runId: string) => request<Run>(`/runs/${runId}`),
    getArtifacts: (runId: string) =>
        request<RunArtifacts>(`/runs/${runId}/artifacts`),
    sendMessage: (
        sessionId: string,
        body: {
            content: string;
            screen_name_hint?: string;
            platform_hint?: string;
            style_hint?: string;
            library_ids: string[];
            assets?: ExactAsset[];
        },
    ) =>
        request<Run>(`/sessions/${sessionId}/messages`, {
            method: "POST",
            body: JSON.stringify(body),
        }),
    review: (
        runId: string,
        revision: number,
        checkpoint: "specification" | "final",
        decision: string,
        feedback = "",
    ) =>
        request<{ status: string }>(`/runs/${runId}/reviews`, {
            method: "POST",
            body: JSON.stringify({ checkpoint, decision, revision, feedback }),
        }),
    retry: (runId: string) =>
        request<{ status: string }>(`/runs/${runId}/retry`, { method: "POST" }),
    cancel: (runId: string) =>
        request<{ status: string }>(`/runs/${runId}/cancel`, {
            method: "POST",
        }),

    // Screens (HTML designer)
    listScreens: (sessionId: string) =>
        request<Screen[]>(`/sessions/${sessionId}/screens`),
    getScreen: (sessionId: string, screenId: string, version?: number) =>
        request<Screen>(
            `/sessions/${sessionId}/screens/${screenId}${version ? `?version=${version}` : ""}`,
        ),
    saveScreen: (
        sessionId: string,
        screenId: string,
        body: { name: string; platform: string; html: string; note?: string; expected_version?: number },
    ) =>
        request<Screen>(`/sessions/${sessionId}/screens/${screenId}`, {
            method: "PUT",
            body: JSON.stringify(body),
        }),
    screenVersions: (sessionId: string, screenId: string) =>
        request<ScreenVersion[]>(`/sessions/${sessionId}/screens/${screenId}/versions`),
    screenDocument: (sessionId: string, screenId: string, version?: number) =>
        text(
            `/sessions/${sessionId}/screens/${screenId}/document${version ? `?version=${version}` : ""}`,
        ),
    screenshotUrl: (sessionId: string, screenId: string, state?: string, width?: number) =>
        `${API_BASE}/sessions/${sessionId}/screens/${screenId}/screenshot?format=png${state ? `&state=${encodeURIComponent(state)}` : ""}${width ? `&width=${width}` : ""}`,
    /** ``layout`` arranges the .fig: one page with a row per screen, or a page per screen. */
    exportUrl: (sessionId: string, format: "fig" | "zip", layout: "rows" | "pages" = "rows") =>
        `${API_BASE}/sessions/${sessionId}/export?format=${format}&layout=${layout}`,

    // Design systems
    listDesignSystems: () =>
        request<DesignSystemInfo[]>(`/design-systems${projectQuery()}`),
    importDesignSystem: async (file: File, name: string) => {
        const params = new URLSearchParams({ name, filename: file.name });
        if (projectId) params.set("project_id", projectId);
        const response = await fetch(`${API_BASE}/design-systems?${params}`, {
            method: "POST",
            headers: { "Content-Type": "application/octet-stream" },
            body: file,
        });
        if (!response.ok) throw await failure(response);
        return (await response.json()) as DesignSystemInfo;
    },
    deleteDesignSystem: (id: string) =>
        request<void>(`/design-systems/${id}`, { method: "DELETE" }),
    galleryDocument: (id: string, page = 0, q = "") =>
        text(`/design-systems/${id}/gallery?page=${page}&q=${encodeURIComponent(q)}`),
    setSessionDesignSystem: (sessionId: string, designSystemId: string) =>
        request<Session>(`/sessions/${sessionId}/design-system`, {
            method: "PUT",
            body: JSON.stringify({ design_system_id: designSystemId }),
        }),

    eventUrl: (runId: string) => `${API_BASE}/runs/${runId}/events`,
    eventHistory: (runId: string) => request<import("./types").WorkflowEvent[]>(`/runs/${runId}/event-history`),
    hulyProjects: () => request<import("./types").HulyProjectsResponse>("/huly/projects"),
    hulyMembers: () => request<import("./types").HulyMembersResponse>("/huly/members"),
    hulyEpics: (projectId: string) => request<import("./types").HulyEpicsResponse>(`/huly/projects/${encodeURIComponent(projectId)}/epics`),
    hulyProjectMetadata: (projectId: string) => request<import("./types").HulyProjectMetadata>(`/huly/projects/${encodeURIComponent(projectId)}/metadata`),
    searchHulyIssues: (projectId: string, query: string) => request<import("./types").HulyIssueSearchResponse>(`/huly/projects/${encodeURIComponent(projectId)}/issues/search?q=${encodeURIComponent(query)}`),
    proposeHulyTasks: (sessionId: string, runId: string) =>
        request<import("./types").HulyTaskProposalResponse>(`/sessions/${sessionId}/huly/proposals`, {
            method: "POST",
            body: JSON.stringify({ run_id: runId }),
        }),
    createHulyIssue: (sessionId: string, draft: import("./types").HulyIssueDraft) =>
        request<import("./types").HulyIssueResult>(`/sessions/${sessionId}/huly/issues`, {
            method: "POST",
            body: JSON.stringify(draft),
        }),
    createHulyIssues: (sessionId: string, draft: import("./types").HulyBulkIssueDraft) =>
        request<import("./types").HulyBulkIssueResult>(`/sessions/${sessionId}/huly/issues/batch`, {
            method: "POST",
            body: JSON.stringify(draft),
        }),
    hulyAttachmentPreview: (sessionId: string, runId: string) =>
        request<import("./types").HulyAttachmentPreview>(`/sessions/${sessionId}/huly/preview?run_id=${encodeURIComponent(runId)}`),
    projectPrototype: (pid: string) =>
        request<ProjectPrototype>(`/projects/${encodeURIComponent(pid)}/prototype`),
};

export type PrototypeFeature = {
    index: number;
    session_id: string;
    title: string;
    screen_keys: string[];
    entry_screen_key?: string | null;
    exit_screen_key?: string | null;
};

export type PrototypeScreen = {
    key: string;
    id: string;
    name: string;
    session_id: string;
    session_title: string;
    feature_index: number;
    platform: string;
    width: number;
    version: number;
    gotos: string[];
    states: string[];
};

export type PrototypeEdge = {
    from_key: string;
    to_key: string;
    kind: "internal" | "cross_feature" | "handoff";
    direction?: "forward" | "back";
    label?: string | null;
};

export type ProjectPrototype = {
    project_id: string;
    start_screen_key: string | null;
    features: PrototypeFeature[];
    screens: PrototypeScreen[];
    edges: PrototypeEdge[];
};

export type ProjectWikiVersion = {
    version: number;
    baseline: Record<string, unknown>;
    source_session_id: string;
    source_feature_id: string | null;
    updated_at: string;
};

export type ProjectWikiResponse = {
    project_id: string;
    current: ProjectWikiVersion | null;
    versions: ProjectWikiVersion[];
};

/** URL of the design page (canvas) for a session; one browser tab per session. */
export function canvasUrl(sessionId: string): string {
    const params = new URLSearchParams({ session: sessionId });
    if (projectId) params.set("project", projectId);
    return `${import.meta.env.BASE_URL}canvas.html?${params}`;
}

export function prototypeUrl(pid?: string): string {
    const target = (pid || projectId || "").trim();
    const params = new URLSearchParams();
    if (target) params.set("project", target);
    return `${import.meta.env.BASE_URL}prototype.html${params.toString() ? `?${params}` : ""}`;
}
