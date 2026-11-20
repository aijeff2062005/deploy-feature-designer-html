"""core-api routes, mounted under /api/core (the gateway forwards the full path)."""

from __future__ import annotations

import re
import uuid
import os
from typing import Any

import mimetypes
import unicodedata
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Response
from platform_core import artifacts
from platform_core.auth import CurrentUser, get_auth_settings, issue_token
from platform_core.fastapi import authenticate, get_current_user
from platform_core.logs import bind
from platform_core.projects import PROJECT_ROLES, clear_cache
from pydantic import BaseModel, Field

from .db import connection
from .passwords import verify_password
from .seed import DEMO_PASSWORD, seed_users

public = APIRouter(prefix="/api/core")


def _disposition(filename: str) -> str:
    """attachment header that survives non-ASCII names (RFC 5987)."""
    ascii_name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode() or "download"
    ascii_name = ascii_name.replace('"', "")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
private = APIRouter(prefix="/api/core", dependencies=[Depends(authenticate)])


class LoginIn(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    huly_url: str = Field(default="", max_length=1000)


class HulyResolveIn(BaseModel):
    huly_url: str = Field(min_length=1, max_length=1000)


class MemberIn(BaseModel):
    user_id: str = Field(min_length=1)
    project_role: str


def _user_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "name": row["display_name"],
        "roles": list(row["platform_roles"]),
    }


def _current_view(user: CurrentUser) -> dict[str, Any]:
    return {"id": user.id, "username": user.username, "name": user.name, "roles": sorted(user.roles)}


def _require_mock() -> None:
    if get_auth_settings().auth_mode != "mock":
        raise HTTPException(404, detail={"code": "NOT_AVAILABLE", "message": "Mock login is disabled"})


def _my_roles(conn, project_id: str, user_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT project_role FROM project_members WHERE project_id=%s AND user_id=%s ORDER BY project_role",
        (project_id, user_id),
    ).fetchall()
    return [r["project_role"] for r in rows]


def _require_member(conn, project_id: str, user: CurrentUser, admin: bool = False) -> list[str]:
    bind(project_id=project_id)
    project = conn.execute(
        "SELECT id FROM projects WHERE id=%s AND archived_at IS NULL", (project_id,)
    ).fetchone()
    roles = _my_roles(conn, project_id, user.id) if project else []
    if not project or not roles:
        # Same answer for "missing" and "not a member": do not leak project ids.
        raise HTTPException(404, detail={"code": "PROJECT_NOT_FOUND", "message": "Project not found"})
    if admin and "ADMIN" not in roles:
        raise HTTPException(403, detail={"code": "ADMIN_REQUIRED", "message": "Project ADMIN role required"})
    return roles


# ── Public ───────────────────────────────────────────────


@public.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "auth_mode": get_auth_settings().auth_mode}


@public.get("/auth/providers")
def auth_providers() -> dict[str, Any]:
    """Sign-in options for the login page. Keycloak is shown once KEYCLOAK_* is configured."""
    import os
    from urllib.parse import urlencode

    base = os.environ.get("KEYCLOAK_URL", "").rstrip("/")
    realm = os.environ.get("KEYCLOAK_REALM", "")
    client = os.environ.get("KEYCLOAK_CLIENT_ID", "")
    keycloak: dict[str, Any] = {"enabled": bool(base and realm and client), "login_url": None}
    if keycloak["enabled"]:
        public_url = os.environ.get("PLATFORM_PUBLIC_URL", "http://localhost:8080").rstrip("/")
        query = urlencode({
            "client_id": client,
            "response_type": "code",
            "scope": "openid profile email",
            "redirect_uri": f"{public_url}/api/core/auth/keycloak/callback",
        })
        keycloak["login_url"] = f"{base}/realms/{realm}/protocol/openid-connect/auth?{query}"
    return {"mock": get_auth_settings().auth_mode == "mock", "keycloak": keycloak}


@public.get("/auth/mock-users")
def mock_users() -> list[dict[str, Any]]:
    _require_mock()
    return [
        {"username": username, "name": name, "roles": roles, "password": DEMO_PASSWORD}
        for _uid, username, name, roles in seed_users()
    ]


@public.post("/auth/login")
def login(body: LoginIn, response: Response) -> dict[str, Any]:
    _require_mock()
    with connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=%s", (body.username.strip(),)).fetchone()
    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, detail={"code": "LOGIN_FAILED", "message": "Wrong username or password"})
    user = CurrentUser(row["id"], row["username"], row["display_name"], frozenset(row["platform_roles"]))
    settings = get_auth_settings()
    token = issue_token(user, settings)
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        max_age=settings.auth_jwt_ttl_minutes * 60,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"user": _user_view(row), "token": token}


@public.post("/auth/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(get_auth_settings().auth_cookie_name, path="/")
    return {"ok": True}


# ── Authenticated ────────────────────────────────────────


@private.get("/me")
def me() -> dict[str, Any]:
    return _current_view(get_current_user())


@private.get("/users")
def list_users() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY display_name").fetchall()
    return [_user_view(r) for r in rows]


@private.get("/projects")
def list_projects() -> list[dict[str, Any]]:
    user = get_current_user()
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.description, p.huly_url, p.huly_project_id, p.huly_project_name, p.created_at,
                   COALESCE(w.status, 'not_started') AS wiki_bootstrap_status,
                   array_agg(DISTINCT m.project_role ORDER BY m.project_role) AS my_roles,
                   (SELECT count(DISTINCT user_id) FROM project_members x WHERE x.project_id = p.id) AS member_count
            FROM projects p JOIN project_members m ON m.project_id = p.id AND m.user_id = %s
            LEFT JOIN project_wiki_bootstrap w ON w.project_id = p.id
            WHERE p.archived_at IS NULL
            GROUP BY p.id, w.status ORDER BY p.name
            """,
            (user.id,),
        ).fetchall()
    return [{**r, "my_roles": list(r["my_roles"]), "created_at": r["created_at"].isoformat()} for r in rows]


@private.post("/projects", status_code=201)
def create_project(body: ProjectIn) -> dict[str, Any]:
    user = get_current_user()
    ascii_name = unicodedata.normalize("NFKD", body.name.replace("Đ", "D").replace("đ", "d"))
    ascii_name = "".join(c for c in ascii_name if not unicodedata.combining(c))
    slug = re.sub(r"[^A-Z0-9]+", "-", ascii_name.upper()).strip("-")[:16].strip("-") or "PROJECT"
    project_id = f"PRJ-{slug}-{uuid.uuid4().hex[:4].upper()}"
    # Default: the creator runs the whole journey (PM = BA + Designer) and administers it.
    roles = ["ADMIN", "PM"]
    huly_url = body.huly_url.strip()
    huly_project = None
    if huly_url:
        parsed = urlsplit(huly_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(422, detail={"code": "HULY_URL_INVALID", "message": "Huly URL must be an http(s) URL"})
        huly_project = _resolve_huly_project(huly_url)
    with connection() as conn:
        conn.execute(
            "INSERT INTO projects (id, name, description, huly_url, huly_project_id, huly_project_name, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (project_id, body.name.strip(), body.description.strip(), huly_url,
             str(huly_project.get("huly_id", "")) if huly_project else "",
             str(huly_project.get("name", "")) if huly_project else "", user.id),
        )
        conn.execute("INSERT INTO project_wiki_bootstrap (project_id, status) VALUES (%s, 'pending')", (project_id,))
        for role in roles:
            conn.execute(
                "INSERT INTO project_members (project_id, user_id, project_role) VALUES (%s, %s, %s)",
                (project_id, user.id, role),
            )
    clear_cache()
    status, error, version = _run_project_wiki_bootstrap(project_id, body.name.strip(), body.description.strip(), huly_url, huly_project)
    return {"id": project_id, "name": body.name.strip(), "description": body.description.strip(),
            "huly_url": huly_url, "huly_project_id": (huly_project or {}).get("huly_id", ""),
            "huly_project_name": (huly_project or {}).get("name", ""), "my_roles": roles, "wiki_bootstrap_status": status,
            "wiki_bootstrap_error": error, "wiki_template_version": version}


def _run_project_wiki_bootstrap(project_id: str, name: str, description: str, huly_url: str, huly_project: dict[str, Any] | None = None) -> tuple[str, str | None, str | None]:
    try:
        from wiki.bootstrap import bootstrap_project_wiki
        result = bootstrap_project_wiki(project_id, name, description, huly_url, huly_project)
        status, error, version = "ready", None, str(result["template_version"])
    except Exception as exc:
        status, error, version = "failed", str(exc)[:2000], None
    with connection() as conn:
        conn.execute("UPDATE project_wiki_bootstrap SET status=%s, template_version=%s, error=%s, updated_at=now() WHERE project_id=%s",
                     (status, version, error, project_id))
    return status, error, version


@private.post("/projects/resolve-huly")
def resolve_huly_project(body: HulyResolveIn) -> dict[str, str]:
    return _resolve_huly_project(body.huly_url)


def _resolve_huly_project(raw_url: str) -> dict[str, str]:
    """Resolve only against authenticated Huly project records; never fetch user URLs."""
    import anyio
    from urllib.parse import unquote
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    import httpx2

    parsed = urlsplit(raw_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(422, detail={"code": "HULY_URL_INVALID", "message": "Huly URL must be an http(s) URL"})
    token = os.getenv("MCP_AUTH_TOKEN", "").strip()
    if not token:
        raise HTTPException(503, detail={"code": "HULY_NOT_CONFIGURED", "message": "Huly project lookup is not configured"})
    mcp_url = os.getenv("HULY_MCP_URL", "http://huly-mcp:3001/mcp")
    workspace = os.getenv("HULY_WORKSPACE", "testing")

    async def fetch_projects():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=httpx2.Timeout(30.0)) as client:
            async with streamable_http_client(mcp_url, http_client=client) as streams:
                read, write = streams[:2]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.call_tool("list_projects", arguments={"workspace": workspace})
    try:
        result = anyio.run(fetch_projects)
    except Exception as exc:
        raise HTTPException(502, detail={"code": "HULY_LOOKUP_FAILED", "message": "Could not read projects from Huly. Check the Huly MCP connection."}) from exc
    if getattr(result, "is_error", getattr(result, "isError", False)):
        raise HTTPException(502, detail={"code": "HULY_LOOKUP_FAILED", "message": "Huly could not resolve this project."})
    data = getattr(result, "structured_content", getattr(result, "structuredContent", None))
    if data is None:
        for item in getattr(result, "content", []):
            text_value = getattr(item, "text", "")
            if text_value:
                import json
                try: data = json.loads(text_value)
                except json.JSONDecodeError: pass
                break
    def entries(value):
        if isinstance(value, list): return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            for key in ("projects", "items", "results", "data", "result"):
                if key in value: return entries(value[key])
            return [value] if any(k in value for k in ("id", "_id", "identifier", "name")) else []
        return []
    candidates = [unquote(x).strip() for x in re.split(r"[/?#&=]", parsed.path + " " + parsed.query) if unquote(x).strip()]
    normalize = lambda s: re.sub(r"[^a-z0-9]", "", s.casefold())
    matches = []
    for item in entries(data):
        pid = str(item.get("id") or item.get("_id") or item.get("projectId") or "").strip()
        identifier = str(item.get("identifier") or item.get("shortId") or item.get("code") or "").strip()
        name = str(item.get("name") or item.get("title") or "").strip()
        values = {normalize(v) for v in (pid, identifier, name) if v}
        if any(normalize(part) in values for part in candidates):
            matches.append({"id": identifier or pid, "huly_id": pid or identifier, "identifier": identifier or pid, "name": name or identifier or pid})
    # Deduplicate aliases pointing to the same Huly record.
    unique = {item["huly_id"]: item for item in matches}
    if len(unique) != 1:
        code, message = ("HULY_PROJECT_NOT_FOUND", "Không tìm thấy project nào trong Huly khớp với URL này.") if not unique else ("HULY_PROJECT_AMBIGUOUS", "URL khớp nhiều project Huly; hãy dùng link có mã project cụ thể.")
        raise HTTPException(422, detail={"code": code, "message": message})
    return next(iter(unique.values()))


@private.post("/projects/{project_id}/wiki/bootstrap")
def retry_project_wiki_bootstrap(project_id: str) -> dict[str, Any]:
    with connection() as conn:
        _require_member(conn, project_id, get_current_user(), admin=True)
        row = conn.execute("SELECT id,name,description,huly_url,huly_project_id,huly_project_name FROM projects WHERE id=%s", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, detail={"code": "PROJECT_NOT_FOUND", "message": "Project not found"})
        conn.execute("INSERT INTO project_wiki_bootstrap (project_id,status) VALUES (%s,'pending') "
                      "ON CONFLICT (project_id) DO UPDATE SET status='pending',error=NULL,updated_at=now()", (project_id,))
    linked = {"huly_id": row["huly_project_id"], "name": row["huly_project_name"]} if row["huly_project_id"] else None
    status, error, version = _run_project_wiki_bootstrap(project_id, row["name"], row["description"], row["huly_url"], linked)
    return {"project_id": project_id, "wiki_bootstrap_status": status, "wiki_bootstrap_error": error,
            "wiki_template_version": version}


@private.get("/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    user = get_current_user()
    with connection() as conn:
        roles = _require_member(conn, project_id, user)
        project = conn.execute("SELECT p.*, COALESCE(w.status, 'not_started') AS wiki_bootstrap_status, w.error AS wiki_bootstrap_error FROM projects p LEFT JOIN project_wiki_bootstrap w ON w.project_id=p.id WHERE p.id=%s", (project_id,)).fetchone()
        members = conn.execute(
            """
            SELECT u.id, u.username, u.display_name,
                   array_agg(m.project_role ORDER BY m.project_role) AS project_roles
            FROM project_members m JOIN users u ON u.id = m.user_id
            WHERE m.project_id = %s GROUP BY u.id ORDER BY u.display_name
            """,
            (project_id,),
        ).fetchall()
    return {
        "id": project["id"],
        "name": project["name"],
        "description": project["description"],
        "huly_url": project["huly_url"],
        "huly_project_id": project["huly_project_id"],
        "huly_project_name": project["huly_project_name"],
        "wiki_bootstrap_status": project["wiki_bootstrap_status"],
        "wiki_bootstrap_error": project["wiki_bootstrap_error"],
        "created_at": project["created_at"].isoformat(),
        "my_roles": roles,
        "members": [
            {"id": m["id"], "username": m["username"], "name": m["display_name"], "roles": list(m["project_roles"])}
            for m in members
        ],
    }


@private.post("/projects/{project_id}/members", status_code=201)
def add_member(project_id: str, body: MemberIn) -> dict[str, Any]:
    if body.project_role not in PROJECT_ROLES:
        raise HTTPException(422, detail={"code": "ROLE_INVALID", "message": f"project_role must be one of {PROJECT_ROLES}"})
    with connection() as conn:
        _require_member(conn, project_id, get_current_user(), admin=True)
        if not conn.execute("SELECT 1 FROM users WHERE id=%s", (body.user_id,)).fetchone():
            raise HTTPException(404, detail={"code": "USER_NOT_FOUND", "message": "User not found"})
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, project_role) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (project_id, body.user_id, body.project_role),
        )
    clear_cache()
    return {"project_id": project_id, "user_id": body.user_id, "project_role": body.project_role}


@private.delete("/projects/{project_id}/members/{user_id}")
def remove_member(project_id: str, user_id: str, role: str | None = None) -> dict[str, Any]:
    user = get_current_user()
    with connection() as conn:
        _require_member(conn, project_id, user, admin=True)
        if user_id == user.id and (role in (None, "ADMIN")):
            raise HTTPException(409, detail={"code": "SELF_ADMIN", "message": "Admins cannot remove their own ADMIN role"})
        if role:
            cur = conn.execute(
                "DELETE FROM project_members WHERE project_id=%s AND user_id=%s AND project_role=%s",
                (project_id, user_id, role),
            )
        else:
            cur = conn.execute(
                "DELETE FROM project_members WHERE project_id=%s AND user_id=%s", (project_id, user_id)
            )
    clear_cache()
    return {"removed": cur.rowcount}


# ── Assets (Sprint 4) ────────────────────────────────────


@private.get("/projects/{project_id}/artifacts")
def list_artifacts(project_id: str) -> list[dict[str, Any]]:
    """Published artifacts of the project, newest first; any member may read."""
    with connection() as conn:
        _require_member(conn, project_id, get_current_user())
    rows = artifacts.list_for_project(project_id)
    return [{k: v for k, v in r.items() if k not in {"content_hash"}} for r in rows]


@private.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str) -> Response:
    from platform_core.storage import get_storage

    row = artifacts.get(artifact_id)
    if not row:
        raise HTTPException(404, detail={"code": "ARTIFACT_NOT_FOUND", "message": "Artifact not found"})
    with connection() as conn:
        _require_member(conn, row["project_id"], get_current_user())
    if not row["storage_key"]:
        raise HTTPException(404, detail={"code": "ARTIFACT_NO_FILE", "message": "Artifact has no stored file"})
    try:
        data = get_storage().get(row["storage_key"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, detail={"code": "STORAGE_ERROR", "message": str(exc)}) from exc
    filename = row["storage_key"].rsplit("/", 1)[-1]
    media = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(data, media_type=media, headers={"Content-Disposition": _disposition(filename)})


# ── Project files (shared files for agents and chats) ────


def _file_name(storage_key: str | None, fallback: str) -> str:
    return (storage_key or "").rsplit("/", 1)[-1] or fallback


@private.get("/projects/{project_id}/files")
def list_project_files(project_id: str) -> list[dict[str, Any]]:
    """Every shared file a member can cite: published artifacts + wiki sources.

    ``citable`` says whether the Designer attachment parser can read it
    (wiki sources are served as their Markdown extract; .fig designs are not).
    """
    with connection() as conn:
        _require_member(conn, project_id, get_current_user())
        users = {
            r["username"]: r["display_name"]
            for r in conn.execute("SELECT username, display_name FROM users").fetchall()
        }
        has_wiki = conn.execute("SELECT to_regclass('wiki.wiki_source') IS NOT NULL AS ok").fetchone()["ok"]
        sources = (
            conn.execute(
                "SELECT source_id, filename, namespace, project_id, ingested_by, ingested_at, "
                "length(markdown_extract) AS chars FROM wiki.wiki_source "
                "WHERE (namespace = 'project' AND project_id = %s) OR namespace = 'company' "
                "ORDER BY ingested_at DESC",
                (project_id,),
            ).fetchall()
            if has_wiki
            else []
        )
    files: list[dict[str, Any]] = []
    for a in artifacts.list_for_project(project_id):
        name = _file_name(a["storage_key"], a["title"])
        files.append({
            "id": a["id"],
            "kind": a["kind"],
            "name": name,
            "title": a["title"],
            "version": a["version"],
            "stale": a["stale"],
            "created_by": a["created_by"],
            "created_by_name": users.get(a["created_by"] or "", a["created_by"]),
            "created_at": a["created_at"],
            "scope": "project",
            "citable": a["kind"] in ("ba_package", "bpmn", "ui_spec", "project_design_wiki"),
            "download_url": f"/api/core/artifacts/{a['id']}/download",
        })
    for s in sources:
        stem = s["filename"].rsplit(".", 1)[0] or s["source_id"]
        files.append({
            "id": s["source_id"],
            "kind": "wiki_source",
            "name": f"{stem}.md",
            "title": s["filename"],
            "version": None,
            "stale": False,
            "created_by": s["ingested_by"],
            "created_by_name": users.get(s["ingested_by"], s["ingested_by"]),
            "created_at": s["ingested_at"],
            "scope": s["namespace"],
            "citable": True,
            "download_url": f"/api/core/projects/{project_id}/wiki-sources/{s['source_id']}/markdown",
        })
    files.sort(key=lambda f: str(f["created_at"]), reverse=True)
    return files


@private.get("/projects/{project_id}/wiki-sources/{source_id}/markdown")
def wiki_source_markdown(project_id: str, source_id: str) -> Response:
    """The lossless Markdown extract of a wiki source (company or this project)."""
    with connection() as conn:
        _require_member(conn, project_id, get_current_user())
        row = conn.execute(
            "SELECT filename, namespace, project_id, markdown_extract FROM wiki.wiki_source WHERE source_id = %s "
            "AND ((namespace = 'project' AND project_id = %s) OR namespace = 'company')",
            (source_id, project_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, detail={"code": "FILE_NOT_FOUND", "message": "File not found"})
    markdown = row["markdown_extract"]
    if markdown is None:
        try:
            from platform_core.storage import get_storage
            from wiki.basic_memory import BasicMemoryClient
            from wiki.project_files import ProjectFileWiki

            files = ProjectFileWiki()
            try:
                note = BasicMemoryClient().read_note(project_id, files.markdown_root(project_id), f"sources/{source_id}.md")
                content = note.get("content", note.get("markdown", ""))
                metadata, markdown = files.parse_markdown(str(content))
            except Exception:
                snapshot = get_storage().get(files.key(project_id, f"history/sources/{source_id}.md")).decode("utf-8")
                metadata, markdown = files.parse_markdown(snapshot)
            if metadata.get("project_id") != project_id or metadata.get("source_id") != source_id:
                raise ValueError("source note metadata mismatch")
        except Exception as exc:
            raise HTTPException(502, detail={"code": "WIKI_SOURCE_UNAVAILABLE", "message": str(exc)}) from exc
    stem = row["filename"].rsplit(".", 1)[0] or source_id
    return Response(
        markdown.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _disposition(f"{stem}.md")},
    )


# ── Journey (guided BA -> Design path of one feature) ────


@private.get("/projects/{project_id}/features/{feature_id}/journey")
def feature_journey(project_id: str, feature_id: str) -> dict[str, Any]:
    from .journey import build_journey

    with connection() as conn:
        roles = _require_member(conn, project_id, get_current_user())
        journey = build_journey(conn, project_id, feature_id)
    if journey is None:
        raise HTTPException(404, detail={"code": "FEATURE_NOT_FOUND", "message": "Feature not found in this project"})
    journey["my_roles"] = roles
    return journey
