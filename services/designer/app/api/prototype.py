"""Project-wide interactive prototype graph (screens, data-goto links, cross-feature handoffs)."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1", tags=["Prototype"])

_GOTO_RE = re.compile(r'data-goto\s*=\s*["\']([^"\']+)["\']', re.I)
_STATE_RE = re.compile(r'data-state(?:-hide)?\s*=\s*["\']([^"\']+)["\']', re.I)
_CONTRACT_RE = re.compile(r"<!--\s*BA_SCREEN_CONTRACT_B64:([A-Za-z0-9_=-]+)\s*-->")


class PrototypeFeature(BaseModel):
    index: int
    session_id: str
    title: str
    screen_keys: list[str]
    entry_screen_key: str | None = None
    exit_screen_key: str | None = None


class PrototypeScreen(BaseModel):
    key: str
    id: str
    name: str
    session_id: str
    session_title: str
    feature_index: int
    platform: str
    width: int
    version: int
    gotos: list[str]
    states: list[str]


class PrototypeEdge(BaseModel):
    from_key: str
    to_key: str
    kind: Literal["internal", "cross_feature", "handoff"]
    direction: Literal["forward", "back"] = "forward"
    label: str | None = None


class ProjectPrototype(BaseModel):
    project_id: str
    start_screen_key: str | None
    features: list[PrototypeFeature]
    screens: list[PrototypeScreen]
    edges: list[PrototypeEdge]


def _extract_gotos(html: str) -> list[str]:
    seen: list[str] = []
    for target in _GOTO_RE.findall(html):
        t = target.strip()
        if t and t not in seen:
            seen.append(t)
    return seen


def _extract_states(html: str) -> list[str]:
    seen: list[str] = []
    for raw in _STATE_RE.findall(html):
        for name in raw.split():
            if name and name not in seen:
                seen.append(name)
    return seen


def _extract_contract_order(runs: list[object]) -> dict[str, int]:
    """Read BA_SCREEN_CONTRACT_B64 sequence order from a session's runs."""
    for run in reversed(runs):
        prompt = getattr(run, "prompt", None) or ""
        match = _CONTRACT_RE.search(prompt)
        if not match:
            continue
        try:
            padded = match.group(1) + "=" * (-len(match.group(1)) % 4)
            raw = base64.urlsafe_b64decode(padded).decode("utf-8", "ignore")
            items = json.loads(raw)
            if not isinstance(items, list):
                continue
            order: dict[str, int] = {}
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("screen_id") or item.get("id") or item.get("slug") or "").strip()
                if not sid:
                    continue
                seq = item.get("sequence")
                order[sid] = int(seq) if isinstance(seq, int) or (isinstance(seq, str) and seq.isdigit()) else idx + 1
            if order:
                return order
        except Exception:
            continue
    return {}


def build_project_prototype(
    project_id: str,
    session_entries: list[tuple[object, list[object], dict[str, int]]],
) -> ProjectPrototype:
    """Assemble ordered features, screens, and navigation edges for a project."""
    features: list[PrototypeFeature] = []
    screens: list[PrototypeScreen] = []
    by_session_screen: dict[tuple[str, str], str] = {}
    by_slug: dict[str, list[str]] = {}
    key_to_feature: dict[str, int] = {}
    key_to_pos: dict[str, int] = {}

    for session, raw_screens, contract_order in session_entries:
        if not raw_screens:
            continue
        sess_screens = [
            sc
            for _, sc in sorted(
                enumerate(raw_screens),
                key=lambda item: (contract_order.get(item[1].id, 10_000 + item[0]), item[0]),
            )
        ]
        f_idx = len(features) + 1
        sid = str(session.id)
        screen_keys: list[str] = []
        for pos, sc in enumerate(sess_screens):
            key = f"{sid}:{sc.id}"
            html = sc.html or ""
            gotos = _extract_gotos(html)
            states = _extract_states(html)
            screens.append(
                PrototypeScreen(
                    key=key,
                    id=sc.id,
                    name=sc.name,
                    session_id=sid,
                    session_title=session.title,
                    feature_index=f_idx,
                    platform=sc.platform,
                    width=sc.width,
                    version=sc.version,
                    gotos=gotos,
                    states=states,
                )
            )
            screen_keys.append(key)
            by_session_screen[(sid, sc.id)] = key
            by_slug.setdefault(sc.id, []).append(key)
            key_to_feature[key] = f_idx
            key_to_pos[key] = pos

        features.append(
            PrototypeFeature(
                index=f_idx,
                session_id=sid,
                title=session.title,
                screen_keys=screen_keys,
                entry_screen_key=screen_keys[0],
                exit_screen_key=screen_keys[-1],
            )
        )

    edges: list[PrototypeEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()
    cross_feature_pairs: set[tuple[int, int]] = set()

    for sc in screens:
        for target_slug in sc.gotos:
            target_key = by_session_screen.get((sc.session_id, target_slug))
            if not target_key:
                candidates = by_slug.get(target_slug) or []
                target_key = candidates[0] if candidates else None
            if not target_key or target_key == sc.key:
                continue
            target_f = key_to_feature[target_key]
            kind: Literal["internal", "cross_feature"] = (
                "internal" if target_f == sc.feature_index else "cross_feature"
            )
            direction: Literal["forward", "back"] = (
                "back"
                if (kind == "internal" and key_to_pos[target_key] < key_to_pos[sc.key])
                or target_f < sc.feature_index
                else "forward"
            )
            edge_sig = (sc.key, target_key, kind)
            if edge_sig in seen_edges:
                continue
            seen_edges.add(edge_sig)
            edges.append(
                PrototypeEdge(
                    from_key=sc.key,
                    to_key=target_key,
                    kind=kind,
                    direction=direction,
                    label=target_slug,
                )
            )
            if kind == "cross_feature":
                cross_feature_pairs.add((sc.feature_index, target_f))

    # Connect consecutive features from the ordered exit screen to the next feature's entry screen.
    for i in range(len(features) - 1):
        curr = features[i]
        nxt = features[i + 1]
        if (curr.index, nxt.index) in cross_feature_pairs:
            continue
        if not curr.exit_screen_key or not nxt.entry_screen_key:
            continue
        edges.append(
            PrototypeEdge(
                from_key=curr.exit_screen_key,
                to_key=nxt.entry_screen_key,
                kind="handoff",
                direction="forward",
                label=f"Sang Feature {nxt.index}",
            )
        )

    return ProjectPrototype(
        project_id=project_id,
        start_screen_key=screens[0].key if screens else None,
        features=features,
        screens=screens,
        edges=edges,
    )


@router.get("/projects/{project_id}/prototype", response_model=ProjectPrototype)
async def get_project_prototype(request: Request, project_id: str) -> ProjectPrototype:
    store = request.app.state.store
    sessions = await store.list_sessions(project_id)
    sessions.sort(key=lambda s: s.created_at)
    screen_lists, run_lists = await asyncio.gather(
        asyncio.gather(*(store.list_screens(s.id, with_html=True) for s in sessions)),
        asyncio.gather(*(store.list_runs(s.id) for s in sessions)),
    )
    entries = [
        (s, scs, _extract_contract_order(runs))
        for s, scs, runs in zip(sessions, screen_lists, run_lists)
    ]
    return build_project_prototype(project_id, entries)
