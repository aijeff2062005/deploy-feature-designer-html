// core-api client. Same origin as the gateway; the session lives in an
// HttpOnly cookie set by /api/core/auth/login.

export type User = { id: string; username: string; name: string; roles: string[] };
export type MockUser = { username: string; name: string; roles: string[]; password: string };
export type AuthProviders = { mock: boolean; keycloak: { enabled: boolean; login_url: string | null } };
export type ProjectSummary = {
  id: string;
  name: string;
  description: string;
  huly_url?: string;
  huly_project_id?: string;
  huly_project_name?: string;
  wiki_bootstrap_status?: string;
  wiki_bootstrap_error?: string | null;
  my_roles: string[];
  member_count: number;
};
export type Member = { id: string; username: string; name: string; roles: string[] };
export type ProjectDetail = ProjectSummary & { members: Member[] };
export type HulyProjectMatch = { id: string; huly_id: string; identifier: string; name: string };
export type DesignerChatSession = { id: string; project_id: string; title: string };
export type DesignerChatRun = { id: string; status: string; prompt?: string; assistant_message?: string | null; created_at?: string; error?: { message?: string } | null };
export type ProjectStyleOption = {
  key: string; name: string; tagline?: string; direction?: string; html: string; compiled_css: string;
  preview_width?: number; preview_height?: number; recommended?: boolean;
};
export type FigmaAnalysis = {
  filename: string;
  summary: { colors?: number; texts?: number; shadows?: number; radii?: number; spacing?: number; fonts?: string[] };
  component_count: number;
  screen_count: number;
  components: { id: string; name: string; variants?: unknown[] }[];
  screens: { id: string; name: string; type?: string }[];
  template_candidates: { id: string; name: string; page: string; width: number; height: number; score: number; looks_like_template: boolean; html?: string }[];
  has_template: boolean;
  recommended_template: { id: string; name: string; page: string; width: number; height: number; score: number; looks_like_template: boolean; html: string } | null;
  unresolved: { kind: string; question: string; node_name?: string; page?: string }[];
};

export type Artifact = {
  id: string;
  kind: "ba_package" | "bpmn" | "ui_spec" | "designer_output" | "project_design_wiki";
  source_module: string;
  source_ref: string;
  version: number;
  latest_version: number;
  title: string;
  storage_key: string | null;
  derived_from_id: string | null;
  derived_version: number | null;
  derived_title: string | null;
  derived_latest_version: number | null;
  stale: boolean;
  meta: Record<string, string>;
  created_by: string | null;
  created_at: string;
};
export type BaFeature = { session_id: string; title: string; feature_status: string; updated_at: string };
export type DesignerSession = { id: string; title: string; source?: string | null; updated_at: string };
export type WikiList = { project_approved: unknown[]; project_drafts: unknown[]; company_approved: unknown[] };

export type JourneyStep = {
  key: string;
  label: string;
  role: "BA" | "DESIGNER";
  hint: string;
  status: "done" | "current" | "attention" | "locked" | "todo";
  reason: string | null;
};
export type Journey = {
  project_id: string;
  feature: { id: string; title: string; status: string };
  current: string;
  role: "BA" | "DESIGNER";
  steps: JourneyStep[];
  ba_package: { latest_version: number | null; used_by_design: number | null; stale: boolean };
  designer: { session_id: string | null; latest_run: { stage: string; status: string } | null; design_artifact_id: string | null };
  my_roles: string[];
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/core${path}`, {
    credentials: "same-origin",
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : detail?.message ?? res.statusText;
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

async function moduleGet<T>(url: string): Promise<T> {
  const res = await fetch(url, { credentials: "same-origin" });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return res.json() as Promise<T>;
}

async function modulePost<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = data?.detail;
    throw new ApiError(res.status, typeof detail === "string" ? detail : detail?.message ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  analyzeFigmaFile: async (file: File) => {
    const query = new URLSearchParams({ filename: file.name });
    const res = await fetch(`/designer/api/v1/design-systems/analyze?${query}`, {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/octet-stream" }, body: file,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const detail = body?.detail;
      throw new ApiError(res.status, typeof detail === "string" ? detail : detail?.message ?? res.statusText);
    }
    return res.json() as Promise<FigmaAnalysis>;
  },
  importFigmaFile: async (projectId: string, file: File) => {
    const query = new URLSearchParams({ project_id: projectId, name: file.name.replace(/\.fig$/i, ""), filename: file.name });
    const res = await fetch(`/designer/api/v1/design-systems?${query}`, {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/octet-stream" }, body: file,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const detail = body?.detail;
      throw new ApiError(res.status, typeof detail === "string" ? detail : detail?.message ?? res.statusText);
    }
    return res.json() as Promise<{ id: string; status: "importing" | "ready" | "failed"; name: string; error?: string | null }>;
  },
  getDesignSystem: (id: string) => moduleGet<{ id: string; name: string; status: "importing" | "ready" | "failed"; error?: string | null }>(
    `/designer/api/v1/design-systems/${encodeURIComponent(id)}`,
  ),
  approveFigmaTemplate: (projectId: string, designSystemId: string, frameId: string) =>
    modulePost<{ project_id: string; version: number; name: string; design_system_id: string; selected_ui_sample: unknown }>(
      `/designer/api/v1/project-wiki/approve-figma-template?project_id=${encodeURIComponent(projectId)}`,
      { design_system_id: designSystemId, frame_id: frameId },
    ),
  journey: (projectId: string, featureId: string) =>
    call<Journey>(`/projects/${encodeURIComponent(projectId)}/features/${encodeURIComponent(featureId)}/journey`),
  createFeature: (projectId: string, title: string, description: string) =>
    modulePost<BaFeature>("/ba/api/features", { title, description, project_id: projectId }),
  handoff: (featureId: string) =>
    modulePost<{ designer_session_id: string; designer_run_id: string }>(
      `/ba/api/features/${encodeURIComponent(featureId)}/designer-handoff`,
      {},
    ),
  artifacts: (projectId: string) => call<Artifact[]>(`/projects/${encodeURIComponent(projectId)}/artifacts`),
  baFeatures: (projectId: string) =>
    moduleGet<BaFeature[]>(`/ba/api/features?project_id=${encodeURIComponent(projectId)}`),
  designerSessions: (projectId: string) =>
    moduleGet<DesignerSession[]>(`/designer/api/v1/sessions?project_id=${encodeURIComponent(projectId)}`),
  designerRuns: (sessionId: string) =>
    moduleGet<DesignerChatRun[]>(`/designer/api/v1/sessions/${encodeURIComponent(sessionId)}/runs`),
  projectDesignWiki: (projectId: string) =>
    moduleGet<{ current: { version: number; baseline: { contract?: { name?: string } }; source_session_id?: string } | null }>(
      `/designer/api/v1/project-wiki?project_id=${encodeURIComponent(projectId)}`,
    ),
  createProjectDesignSession: (projectId: string, projectName: string, projectDescription: string, designSystemId?: string) =>
    modulePost<DesignerChatSession>("/designer/api/v1/project-design-system/sessions", {
      project_id: projectId, project_name: projectName, project_description: projectDescription,
      ...(designSystemId ? { design_system_id: designSystemId } : {}),
    }),
  sendDesignerMessage: (sessionId: string, content: string) =>
    modulePost<DesignerChatRun>(`/designer/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
      content, screen_name_hint: "Project design system", platform_hint: "responsive", library_ids: [],
    }),
  getDesignerRun: (runId: string) =>
    moduleGet<DesignerChatRun>(`/designer/api/v1/runs/${encodeURIComponent(runId)}`),
  getDesignerRunEvents: (runId: string) =>
    moduleGet<{ id: string; type: string; payload: Record<string, unknown> }[]>(
      `/designer/api/v1/runs/${encodeURIComponent(runId)}/event-history`,
    ),
  approveProjectDesignTemplate: (projectId: string, sessionId: string, runId: string, optionKey: string) =>
    modulePost<{ version: number; name: string; template_key: string }>(
      `/designer/api/v1/project-wiki/approve-template?project_id=${encodeURIComponent(projectId)}`,
      { session_id: sessionId, run_id: runId, option_key: optionKey },
    ),
  wiki: (projectId: string) => moduleGet<WikiList>(`/ba/api/wiki/pages?project_id=${encodeURIComponent(projectId)}`),
  me: () => call<User>("/me"),
  mockUsers: () => call<MockUser[]>("/auth/mock-users"),
  authProviders: () => call<AuthProviders>("/auth/providers"),
  login: (username: string, password: string) =>
    call<{ user: User }>("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => call<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  projects: () => call<ProjectSummary[]>("/projects"),
  project: (id: string) => call<ProjectDetail>(`/projects/${encodeURIComponent(id)}`),
  createProject: (name: string, description: string, hulyUrl = "") =>
    call<ProjectSummary>("/projects", { method: "POST", body: JSON.stringify({ name, description, huly_url: hulyUrl }) }),
  updateProject: (id: string, name: string, description: string, hulyUrl = "") =>
    call<{ id: string; name: string; description: string; huly_url: string; huly_project_id: string; huly_project_name: string }>(
      `/projects/${encodeURIComponent(id)}`,
      { method: "PUT", body: JSON.stringify({ name, description, huly_url: hulyUrl }) },
    ),
  resolveHulyProject: (hulyUrl: string) =>
    call<HulyProjectMatch>("/projects/resolve-huly", { method: "POST", body: JSON.stringify({ huly_url: hulyUrl }) }),
  retryProjectWikiBootstrap: (id: string) =>
    call<{ wiki_bootstrap_status: string; wiki_bootstrap_error: string | null }>(`/projects/${encodeURIComponent(id)}/wiki/bootstrap`, { method: "POST" }),
  users: () => call<User[]>("/users"),
  addMember: (projectId: string, userId: string, role: string) =>
    call(`/projects/${encodeURIComponent(projectId)}/members`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, project_role: role }),
    }),
  removeMember: (projectId: string, userId: string) =>
    call(`/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(userId)}`, { method: "DELETE" }),
};

// Which module UI opens a project. BA → /ba/, Designer → /designer/.
export function moduleUrl(module: "ba" | "designer", projectId: string): string {
  return `/${module}/?project=${encodeURIComponent(projectId)}`;
}

export function canWrite(roles: string[], role: "BA" | "DESIGNER"): boolean {
  return roles.includes(role) || roles.includes("ADMIN") || roles.includes("PM");
}
