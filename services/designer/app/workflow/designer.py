"""The HTML designer: route a message, write the UI spec + art direction, build/edit screens.

Stages (read by core/app/journey.py): requirement → specification → review_spec (human)
→ assembly → review_final (human) → finished. Chat-driven edits go requirement →
assembly → finished without reviews.

A screen is one draft call plus at most ``designer_review_rounds`` review calls (screenshot +
DOM lint + required texts → OK / edit blocks). Screens after the first are built in parallel.
Design knowledge comes from the vendored skills (``app.knowledge``), retrieved per product
and per screen, so no domain or platform is hard-coded.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import functools
import hashlib
import inspect
import json
import logging
import re
import time
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, Field

from .. import knowledge
from ..core.errors import AppError
from ..designsystem.direction import direction_theme
from ..designsystem.service import DesignContext, design_context
from ..models import DEFAULT_DESIGN_SYSTEM, SCREEN_WIDTHS, RunStage, RunStatus, RunView, ScreenWrite, SessionView, StyleCandidate
from ..render import fragment_of
from ..render.assets import inline_assets
from .preview_assets import asset_by_id, inline_svg, relevant_assets
from .patches import apply_reply, extract_html
from .schemas import DesignDecision, Palette, ScreenSpec, ScreenStateSpec, StylePreviewPlan, UiSpec

if TYPE_CHECKING:
    from .service import RunService

logger = logging.getLogger("designer_agent.designer")
_TRACE_CONTEXT: ContextVar[tuple[Any, str] | None] = ContextVar("designer_trace_context", default=None)
_PROJECT_WIKI_STYLE_LOCK = re.compile(r"<style id=\"project-wiki-style-lock\">.*?</style>", re.S | re.I)


def _apply_project_wiki_backdrop(
    markup: str, reference: dict[str, Any] | None, *, target_selectors: list[str] | None = None,
) -> str:
    """Apply only the conflict-free semantic role rules captured in Project Wiki."""
    cleaned = _PROJECT_WIKI_STYLE_LOCK.sub("", fragment_of(markup))
    css = str((reference or {}).get("style_lock_css") or "").strip()
    if not css:
        return cleaned
    # Role selectors are emitted from validated role/variant identifiers and only
    # target elements carrying the explicit semantic contract attributes.
    return cleaned + f'\n<style id="project-wiki-style-lock">\n{css}\n</style>'


_PROJECT_WIKI_STYLE_PROFILE_JS = r"""() => {
  const safeName = (value) => /^[a-z][a-z0-9_-]*$/i.test(value || '');
  const properties = ['color', 'backgroundColor', 'borderColor', 'borderRadius',
    'fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'boxShadow'];
  const tokens = {};
  const rootStyle = getComputedStyle(document.documentElement);
  for (const name of rootStyle) {
    if (/^--(?:color|font|radius|spacing|shadow|text)-[a-z0-9_-]+$/i.test(name)) {
      const value = rootStyle.getPropertyValue(name).trim();
      if (value) tokens[name] = value;
    }
  }
  const roles = {};
  for (const element of document.querySelectorAll('[data-design-role][data-design-variant]')) {
    const role = element.getAttribute('data-design-role');
    const variant = element.getAttribute('data-design-variant');
    if (!safeName(role) || !safeName(variant)) continue;
    const style = getComputedStyle(element);
    const values = Object.fromEntries(properties.map((name) => [name, style[name]]));
    (roles[role] ||= {})[variant] ||= [];
    roles[role][variant].push(values);
  }
  return { tokens, roles };
}"""


def _project_wiki_style_css(profile: dict[str, Any]) -> str:
    """Render only explicit semantic role/variant data; never infer global selectors."""
    roles = profile.get("roles") if isinstance(profile, dict) else None
    if not isinstance(roles, dict):
        return ""
    declarations = {
        "color": "color", "backgroundColor": "background-color", "borderColor": "border-color",
        "borderRadius": "border-radius", "fontFamily": "font-family", "fontSize": "font-size",
        "fontWeight": "font-weight", "lineHeight": "line-height", "boxShadow": "box-shadow",
    }
    css: list[str] = []
    for role, variants in sorted(roles.items()):
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", str(role), re.I) or not isinstance(variants, dict):
            continue
        for variant, properties in sorted(variants.items()):
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", str(variant), re.I) or not isinstance(properties, dict):
                continue
            safe = {}
            for source, target in declarations.items():
                record = properties.get(source)
                value = record.get("value") if isinstance(record, dict) and not record.get("conflict") else None
                if (isinstance(value, str) and value.strip()
                        and not any(ch in value for ch in "{};\x00")
                        and "</style" not in value.casefold()):
                    safe[target] = value.strip()
            if safe:
                selector = f'[data-design-role="{role}"][data-design-variant="{variant}"]'
                css.append(selector + " { " + "; ".join(f"{key}: {value}" for key, value in safe.items()) + "; }")
    return "\n".join(css)


def _merge_project_wiki_profiles(screen_profiles: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate explicit token and component observations without selecting a winner."""
    def merge_values(observations: list[tuple[str, str]]) -> dict[str, Any]:
        grouped: dict[str, list[str]] = {}
        for source, value in observations:
            if value:
                grouped.setdefault(value, []).append(source)
        values = [{"value": value, "sources": sources} for value, sources in sorted(grouped.items())]
        return {"value": values[0]["value"] if len(values) == 1 else None,
                "values": values, "conflict": len(values) > 1}

    token_observations: dict[str, list[tuple[str, str]]] = {}
    role_observations: dict[str, dict[str, dict[str, list[tuple[str, str]]]]] = {}
    for screen in screen_profiles:
        source = str(screen.get("screen_id") or "unknown")
        profile = screen.get("profile") if isinstance(screen.get("profile"), dict) else {}
        for token, value in (profile.get("tokens") or {}).items():
            if isinstance(value, str) and value.strip():
                token_observations.setdefault(str(token), []).append((source, value.strip()))
        for role, variants in (profile.get("roles") or {}).items():
            if not isinstance(variants, dict):
                continue
            for variant, samples in variants.items():
                if not isinstance(samples, list):
                    samples = [samples]
                for sample in samples:
                    if not isinstance(sample, dict):
                        continue
                    for prop, value in sample.items():
                        if isinstance(value, str) and value.strip():
                            role_observations.setdefault(str(role), {}).setdefault(str(variant), {}).setdefault(str(prop), []).append((source, value.strip()))
    tokens = {name: merge_values(rows) for name, rows in sorted(token_observations.items())}
    roles = {role: {variant: {prop: merge_values(rows) for prop, rows in sorted(properties.items())}
                    for variant, properties in sorted(variants.items())}
             for role, variants in sorted(role_observations.items())}
    return {"tokens": tokens, "roles": roles, "screens": screen_profiles}


def _trace_value(value: Any, limit: int = 40_000) -> Any:
    """Keep debug events useful while bounding database and SSE event sizes."""
    def prepare(item: Any) -> Any:
        if isinstance(item, BaseModel):
            return prepare(item.model_dump(mode="json", by_alias=True))
        if dataclasses.is_dataclass(item) and not isinstance(item, type):
            return prepare(dataclasses.asdict(item))
        if isinstance(item, bytes):
            return {"binary_bytes": len(item), "content_omitted": True}
        if isinstance(item, dict):
            return {str(key): prepare(value) for key, value in item.items()}
        if isinstance(item, (list, tuple, set)):
            return [prepare(value) for value in item]
        return item

    value = prepare(value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        encoded = str(value)
    if len(encoded) > limit:
        return {"truncated": True, "original_characters": len(encoded), "preview": encoded[:limit]}
    try:
        return json.loads(encoded)
    except json.JSONDecodeError:
        return encoded


def _trace_async_method(function):
    """Trace async Designer helpers with arguments, return values, duration, and failures."""
    signature = inspect.signature(function)

    @functools.wraps(function)
    async def traced(self, *args, **kwargs):
        bound = signature.bind_partial(self, *args, **kwargs)
        arguments = {key: value for key, value in bound.arguments.items() if key != "self"}
        context = _TRACE_CONTEXT.get()
        run = context[0] if context else None
        if run is None:
            run = next((value for value in arguments.values() if isinstance(value, RunView)), None)
        if run is None and arguments.get("run_id") is not None:
            run = await self.store.get_run(arguments["run_id"])
        if run is None:
            return await function(self, *args, **kwargs)

        phase = function.__name__ if context is None or function.__name__ in {"analyze", "write_spec", "build"} else context[1]
        token = _TRACE_CONTEXT.set((run, phase))
        payload = {
            "phase": phase,
            "function": f"designer.{function.__name__}",
            "input": _trace_value(arguments),
        }
        started = time.perf_counter()

        async def emit(event_type: str, event_payload: dict[str, Any]) -> None:
            if hasattr(self, "_emit"):
                await self._emit(run, event_type, event_payload)
            else:
                await self.events.emit(run.id, run.revision, event_type, event_payload)

        await emit("debug.function.started", payload)
        try:
            result = await function(self, *args, **kwargs)
        except Exception as error:
            await emit("debug.function.failed", {
                **payload,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "error": {"type": type(error).__name__, "message": str(error)[:4000]},
            })
            raise
        else:
            await emit("debug.function.completed", {
                **payload,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "output": _trace_value(result),
            })
            return result
        finally:
            _TRACE_CONTEXT.reset(token)

    return traced

INTENTS = {"chat": "chat", "clarify_design": "clarify_design", "propose_scope": "gather", "gather": "gather",
           "start_design": "start_design", "edit_screens": "design"}
HISTORY_RUNS = 8
SOURCE_CHARS = 40_000
MAX_FINDINGS = 8


class _StylePreviewReviewItem(BaseModel):
    key: str
    ready: bool
    issues: list[str] = Field(default_factory=list, max_length=3)


class _StylePreviewReviewBatch(BaseModel):
    reviews: list[_StylePreviewReviewItem] = Field(min_length=1, max_length=3)


class _ScreenAudit(BaseModel):
    ready: bool
    issues: list[str] = Field(default_factory=list, max_length=5)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… ({len(text) - limit} more characters)"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _copy_key(text: str) -> str:
    return "".join(char for char in str(text).casefold() if char.isalnum())


def _unique_in_order(values: list[Any]) -> list[Any]:
    """Deduplicate JSON values without requiring them to be hashable."""
    result: list[Any] = []
    for value in values:
        if not any(value == existing for existing in result):
            result.append(value)
    return result


def _matches_reference(text: str, references: list[str]) -> bool:
    key = _copy_key(text)
    if len(key) < 10:
        return False
    return any(
        (ref_key := _copy_key(reference))
        and len(ref_key) >= 10
        and (key in ref_key or ref_key in key)
        for reference in references
    )


def _is_conditional_copy(text: str, approved_screen: dict[str, Any]) -> bool:
    conditional = [
        *(approved_screen.get("conditional_content") or []),
        *(approved_screen.get("states") or []),
        *(approved_screen.get("conditional_actions") or []),
        *(state.get("trigger", "") for state in (approved_screen.get("state_spec") or []) if isinstance(state, dict)),
        *(item for state in (approved_screen.get("state_spec") or []) if isinstance(state, dict)
          for item in (state.get("content") or [])),
    ]
    return _matches_reference(text, conditional)


def _default_source_items(approved_screen: dict[str, Any], field: str) -> list[str]:
    """Read the approved first-paint source only; never infer it from generated UI spec fields."""
    if field == "visible_copy":
        if approved_screen.get("visible_copy"):
            source = approved_screen.get("visible_copy")
        else:
            # Legacy briefs have no exact-copy field. Preserve only text they
            # explicitly quoted; structural descriptions are not verbatim UI.
            quoted = re.compile(r'[“"]([^”"]{1,140})[”"]|[‘\']([^’\']{1,140})[’\']')
            source = []
            for line in [*(approved_screen.get("content_outline") or []),
                         *(approved_screen.get("default_actions") or approved_screen.get("actions") or [])]:
                source.extend(next((part for part in match.groups() if part), "").strip()
                              for match in quoted.finditer(str(line)))
    elif field == "actions":
        source = approved_screen.get("default_actions")
        if source is None:
            # Legacy briefs have a combined action inventory. Only migrate an
            # action when the confirmed default outline independently names it.
            default_content = _default_source_items(approved_screen, "content_outline")
            source = [
                item for item in (approved_screen.get("actions") or [])
                if _anchored_to_default(str(item), default_content)
            ]
    else:
        source = approved_screen.get("content_outline") or []
    return [
        re.sub(r"^\s*\[(?:BA|user|suggestion)\]\s*", "", str(item), flags=re.I).strip()
        for item in source
        if str(item).strip() and not re.match(r"^\s*\[Open\]", str(item), re.I)
        and not _is_conditional_copy(str(item), approved_screen)
    ]


def _anchored_to_default(text: str, default_items: list[str]) -> bool:
    key = _copy_key(text)
    return len(key) >= 3 and any(
        (item_key := _copy_key(item)) and (key in item_key or item_key in key)
        for item in default_items
    )


def _default_screen_fields(screen: ScreenSpec, approved_screen: dict[str, Any]) -> dict[str, Any]:
    """Return only first-paint content for the normal screen build/review pipeline."""
    payload = screen.model_dump(exclude_defaults=True)
    for field in ("states", "conditional_regions", "state_scenarios", "content_contract", "rules", "ux_query"):
        payload.pop(field, None)
    default_content = _default_source_items(approved_screen, "content_outline")
    default_actions = _default_source_items(approved_screen, "actions")
    visible_copy = _default_source_items(approved_screen, "visible_copy")
    payload["sections"] = default_content
    payload["actions"] = default_actions
    # Generated data and required-copy fields are accepted only when grounded in
    # the separately approved first-paint source. Unknown content is not promoted.
    payload["data"] = [
        item for item in payload.get("data", [])
        if _anchored_to_default(str(item), [*default_content, *default_actions])
    ]
    # ScreenSpec.must_show historically mirrored descriptive outline entries,
    # making builders print component descriptions as field labels. Only exact,
    # user-approved copy is a verbatim text contract.
    payload["must_show"] = visible_copy
    purpose = str(approved_screen.get("purpose") or payload.get("purpose", ""))
    payload["purpose"] = "" if _is_conditional_copy(purpose, approved_screen) else purpose
    payload["layout"] = ""
    return payload


def _default_approved_brief(approved_screen: dict[str, Any]) -> dict[str, Any]:
    """Keep only confirmed default regions and actions for screen review."""
    result = {
        key: value for key, value in approved_screen.items()
        if key not in {"conditional_content", "states", "state_spec", "open_questions", "rules",
                       "content_contract", "conditional_regions", "state_scenarios", "conditional_actions"}
    }
    result["content_outline"] = _default_source_items(approved_screen, "content_outline")
    result["visible_copy"] = _default_source_items(approved_screen, "visible_copy")
    result["actions"] = _default_source_items(approved_screen, "actions")
    result.pop("default_actions", None)
    if _is_conditional_copy(str(result.get("purpose", "")), approved_screen):
        result["purpose"] = ""
    return result


def _split_approved_brief(brief: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Persist the reviewed default contract and state analysis in separate records."""
    explicit_default_actions = brief.get("default_actions")
    if explicit_default_actions is None:
        # Older briefs only have a combined action inventory. Preserve those
        # actions as default unless their text is explicitly identified as
        # conditional; requiring a substring match against content_outline
        # silently dropped ordinary controls such as submit buttons.
        default_actions = [
            str(item).strip() for item in (brief.get("actions") or [])
            if str(item).strip() and not _is_conditional_copy(str(item), brief)
        ]
    else:
        default_actions = _default_source_items(brief, "actions")
    default = {
        "id": brief.get("id", ""),
        "name": brief.get("name", ""),
        "purpose": "" if _is_conditional_copy(str(brief.get("purpose", "")), brief) else brief.get("purpose", ""),
        "platforms": brief.get("platforms", []),
        "source_ui_ids": brief.get("source_ui_ids", []),
        "content_outline": _default_source_items(brief, "content_outline"),
        "visible_copy": _default_source_items(brief, "visible_copy"),
        "default_actions": default_actions,
    }
    all_actions = [str(item) for item in (brief.get("actions") or [])]
    state = {
        "states": brief.get("states", []),
        "state_spec": brief.get("state_spec", []),
        "conditional_content": brief.get("conditional_content", []),
        "rules": brief.get("rules", []),
        "conditional_actions": [item for item in all_actions if item not in default_actions],
    }
    return default, state


def _approved_source_for_screen(spec: UiSpec, screen_id: str) -> dict[str, Any]:
    source = dict(spec.approved_default_screens.get(screen_id, {}))
    reference = spec.state_reference_screens.get(screen_id, {})
    state_spec = spec.state_specs.get(screen_id)
    if reference:
        source.update(reference)
    if state_spec:
        for field, value in state_spec.model_dump(mode="json", exclude_defaults=True).items():
            if isinstance(value, list):
                source[field] = _unique_in_order([*(source.get(field) or []), *value])
            else:
                source[field] = value
    return source


def _default_visual_contract(contract: dict[str, Any], approved_screen: dict[str, Any]) -> dict[str, Any]:
    """Remove state-bearing prose before a visual contract reaches UI generation."""
    result = dict(contract or {})
    for field in (
        "direction", "tagline", "composition", "surface_language", "shape_language",
        "hero_placement", "background_treatment",
    ):
        if _is_conditional_copy(str(result.get(field, "")), approved_screen):
            result.pop(field, None)
    return result


def _conditional_copy_forbidden(approved_screen: dict[str, Any]) -> list[str]:
    """Return state-only copy, excluding phrases also approved in the default screen."""
    quoted = re.compile(r"[“\"]([^”\"]{2,100})[”\"]|[‘']([^’']{2,100})[’']")
    phrases: list[str] = []
    state_content = [
        item for state in (approved_screen.get("state_spec") or []) if isinstance(state, dict)
        for item in (state.get("content") or [])
    ]
    for line in [*(approved_screen.get("conditional_content") or []), *state_content]:
        matches = [next((part for part in match.groups() if part), "").strip() for match in quoted.finditer(str(line))]
        phrases.extend(value for value in matches if value)
        if not matches and len(str(line).split()) <= 12:
            phrases.append(str(line).strip())
    default_source = _copy_key(" ".join([
        *(str(item) for item in _default_source_items(approved_screen, "content_outline")),
        *(str(item) for item in _default_source_items(approved_screen, "actions")),
        *(str(item) for item in _default_source_items(approved_screen, "visible_copy")),
    ]))
    # State notes may repeat the name of a persistent field/control. That
    # shared label is valid in the initial screen; only genuinely state-only
    # phrases should be forbidden there.
    return list(dict.fromkeys(
        phrase for phrase in phrases
        if _copy_key(phrase) and _copy_key(phrase) not in default_source
    ))


def _validate_preview_content_contract(screen, contract) -> tuple[list[str], list[str]]:
    """Validate the model's content analysis against the approved structured brief.

    Visibility is not inferred from wording. Persistent regions come from
    ``content_outline`` and triggered regions from ``conditional_content``;
    action visibility is analyzed by the art director and must form an exact,
    non-overlapping partition of the approved action inventory.
    """
    source_actions = [str(item).strip() for item in (screen.actions or []) if str(item).strip()]
    default_actions = [str(item).strip() for item in contract.default_visible_actions]
    conditional_actions = [str(item).strip() for item in contract.conditional_actions]
    expected = [_norm(item) for item in source_actions]
    classified = [_norm(item) for item in [*default_actions, *conditional_actions]]
    if len(classified) != len(set(classified)) or sorted(classified) != sorted(expected):
        raise ValueError("Preview action analysis must classify every approved action exactly once")

    default_source = _norm(" ".join([
        str(getattr(screen, "name", "")), str(getattr(screen, "purpose", "")),
        *(str(item) for item in (screen.content_outline or [])),
        *(str(item) for item in (getattr(screen, "visible_copy", None) or [])),
        *default_actions,
    ]))
    # States describe behavior, not evidence that an exact phrase belongs in a
    # hidden UI region. Only explicitly conditional content/actions may create
    # first-paint exclusions; otherwise harmless counters/status values from a
    # state description can incorrectly block an otherwise valid preview.
    conditional_source = _norm(" ".join([
        *(str(item) for item in (getattr(screen, "conditional_content", None) or [])),
        *conditional_actions,
    ]))
    default_text = [str(item).strip() for item in contract.default_visible_text_exact if str(item).strip()]
    conditional_text = [str(item).strip() for item in contract.conditional_text_exact if str(item).strip()]
    normalized_default = [_norm(item) for item in default_text]
    normalized_conditional = [_norm(item) for item in conditional_text]
    if any(item not in default_source for item in normalized_default):
        raise ValueError("Default preview copy is not grounded in approved default content/actions")
    # A phrase explicitly present in persistent/default content is allowed on
    # first paint even if a state description also mentions it. Keep it in the
    # required copy set and never classify the same phrase as forbidden.
    promoted = [
        text for text, normalized in zip(conditional_text, normalized_conditional)
        if normalized and normalized in default_source and normalized not in normalized_default
    ]
    default_text.extend(promoted)
    normalized_default = [_norm(item) for item in default_text]
    first_paint_forbidden = [
        text for text, normalized in zip(conditional_text, normalized_conditional)
        if normalized and normalized not in normalized_default and normalized in conditional_source
    ]
    return default_text, first_paint_forbidden


def _representative_preview_screen(screens):
    """Pick the most informative confirmed screen without relying on domain keywords."""
    if not screens:
        return None

    def information_score(screen):
        content = screen.content_outline or []
        actions = screen.actions or []
        return (
            len(content) + len(actions),
            len(content),
            len(actions),
            sum(len(str(item)) for item in content + actions),
            len((screen.purpose or "").strip()),
            "mobile" in (screen.platforms or []),
        )

    return max(enumerate(screens), key=lambda pair: (information_score(pair[1]), -pair[0]))[1]


def _style_preview_reviews(response: str, candidates: list[StyleCandidate]) -> dict[str, list[str]] | None:
    """Parse critic JSON; return None when the reviewer response cannot be trusted."""
    start, end = response.find("{"), response.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(response[start:end + 1])
    except json.JSONDecodeError:
        return None
    rows = payload.get("reviews") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    by_key = {row.get("key"): row for row in rows if isinstance(row, dict)}
    issues: dict[str, list[str]] = {}

    def format_issue(note: Any) -> str:
        if isinstance(note, dict):
            element = str(note.get("element") or "Visible element").strip()
            defect = str(note.get("defect") or note.get("issue") or "Needs visual correction").strip()
            correction = str(note.get("correction") or "").strip()
            return f"{element}: {defect}" + (f" Correction: {correction}" if correction else "")
        return str(note).strip()

    for candidate in candidates:
        row = by_key.get(candidate.key)
        if not row or row.get("ready") is not True:
            notes = row.get("issues", []) if row else []
            issues[candidate.key] = []
            for note in notes:
                text = format_issue(note)
                if text:
                    issues[candidate.key].append(text)
                if len(issues[candidate.key]) == 3:
                    break
            if not issues[candidate.key]:
                issues[candidate.key] = [
                    "Inspect the screenshot and improve its product fit, legibility, source fidelity, and visual distinction."
                ]
    return issues


def _candidate_theme(candidate: StyleCandidate, *, preserve_system_type: bool = False) -> dict[str, Any] | None:
    """Build renderer tokens from the exact palette/font contract shown in this option."""
    if not candidate.palette:
        return None
    palette = Palette.model_validate(candidate.palette)
    theme = direction_theme({
        "palette": palette.model_dump(),
        "heading_font": candidate.heading_font,
        "body_font": candidate.body_font,
        "radius": candidate.radius,
    })
    if theme and preserve_system_type:
        # Imported systems keep their typography and geometry, while each preview
        # receives the semantic color roles from its selected palette.
        theme["css"] = re.sub(r"^\s*--(?:font-[^:]+|radius-[^:]+):[^\n]*\n?", "", theme["css"], flags=re.MULTILINE)
        theme["fonts"] = []
    return theme


def _bind_primary_button_foreground(fragment: str, candidate: StyleCandidate) -> str:
    """Use the validated on-primary token on primary buttons instead of hard-coded foreground classes."""
    palette = Palette.model_validate(candidate.palette)
    primary = palette.primary.lower()
    button = re.compile(r"<button\b[^>]*>.*?</button\s*>", re.IGNORECASE | re.DOTALL)
    token = re.compile(r"(?<![\w-])text-white(?![\w-])")

    def replace_foreground(match: re.Match[str]) -> str:
        block = match.group(0)
        lower = block.lower()
        is_primary = "bg-primary" in lower or primary in lower
        if is_primary and token.search(block):
            return token.sub("text-primary-foreground", block)
        return block

    return button.sub(replace_foreground, fragment)


def _normalize_malformed_text_color_classes(fragment: str) -> str:
    """Repair a common LLM typo without changing the selected color."""
    return re.sub(
        r"(?<![\w-])text-text-(\[#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\])",
        r"text-\1",
        fragment,
    )


class HtmlDesigner:
    def __init__(self, service: "RunService") -> None:
        self.service = service
        self.store = service.store
        self.events = service.events
        self.llm = service.llm
        self.prompts = service.prompts

    # ── helpers ──
    async def _emit(self, run: RunView, kind: str, payload: dict[str, Any]) -> None:
        await self.events.emit(run.id, run.revision, kind, payload)

    async def _trace_decision(
        self, run: RunView, name: str, condition: str, chosen: str, details: Any = None
    ) -> None:
        context = _TRACE_CONTEXT.get()
        await self._emit(run, "debug.decision", {
            "phase": context[1] if context else "designer",
            "function": f"decision.{name}",
            "condition": condition,
            "chosen": chosen,
            "details": _trace_value(details or {}),
        })

    async def _llm_call(self, method: str, **kwargs: Any) -> Any:
        """Record the exact prompt/context and result for every Designer model call."""
        trace_name = kwargs.pop("trace_name", f"llm.{method}")
        context = _TRACE_CONTEXT.get()
        run, phase = context if context else (None, "designer")
        schema = kwargs.get("schema")
        inputs = {key: value for key, value in kwargs.items() if key not in {"usage", "image_bytes", "images"}}
        if schema is not None:
            inputs["schema"] = getattr(schema, "__name__", str(schema))
        if kwargs.get("image_bytes") is not None:
            inputs["image_bytes"] = {"bytes": len(kwargs["image_bytes"]), "content_omitted": True}
        if kwargs.get("images"):
            inputs["images"] = [{"bytes": len(item), "content_omitted": True} for item in kwargs["images"]]
        payload = {"phase": phase, "function": trace_name, "input": _trace_value(inputs)}
        if run is not None:
            await self._emit(run, "debug.step.started", payload)
        try:
            result = await getattr(self.llm, method)(**kwargs)
        except Exception as error:
            if run is not None:
                await self._emit(run, "debug.step.failed", {
                    **payload, "error": {"type": type(error).__name__, "message": str(error)[:4000]},
                })
            raise
        if run is not None:
            await self._emit(run, "debug.step.completed", {
                **payload, "output": _trace_value(result),
                "usage": _trace_value(kwargs.get("usage") or {}),
            })
        return result

    async def _stage(self, run: RunView, stage: RunStage, status: RunStatus = RunStatus.RUNNING, **fields: Any) -> RunView:
        run = await self.store.update_run(run.id, stage=stage, status=status, **fields)
        await self._emit(run, "stage.changed", {"stage": stage.value, "status": status.value})
        return run

    async def _usage(self, run: RunView, usage: dict[str, int], step: str) -> None:
        total = await self.store.get_run_data(run.id, run.revision, "token_usage") or {"steps": []}
        total["steps"].append({"step": step, **usage})
        for k in ("calls", "prompt_tokens", "output_tokens"):
            total[k] = total.get(k, 0) + usage.get(k, 0)
        await self.store.set_run_data(run.id, run.revision, "token_usage", total)
        await self._emit(run, "llm.usage", {"step": step, **usage,
                                            "run_total": {k: total[k] for k in ("calls", "prompt_tokens", "output_tokens")}})

    async def _session_runs(self, session: SessionView, current: RunView) -> list[RunView]:
        runs = [r for r in await self.store.list_runs(session.id) if r.id != current.id]
        return list(reversed(runs))

    def _history(self, runs: list[RunView], *, first_limit: int, limit: int = 1500) -> str:
        lines = []
        for i, r in enumerate(runs[-HISTORY_RUNS:] if len(runs) > HISTORY_RUNS else runs):
            lines.append(f"User: {_clip(r.prompt, first_limit if i == 0 else limit)}")
            if r.assistant_message:
                lines.append(f"Designer: {_clip(r.assistant_message, 800)}")
        return "\n".join(lines) or "(none)"

    async def _design_text(self, design: DesignContext, query: str = "") -> str:
        """Imported design system tokens and real Figma component snippets relevant to ``query``."""
        if design.id == DEFAULT_DESIGN_SYSTEM or not design.components:
            return "None imported: use the art direction and the theme tokens."
        tokens = [line.strip().split(":")[0][2:] for line in design.theme_css.splitlines()
                  if line.strip().startswith("--") and "Imported" not in line]
        colors = [t.removeprefix("color-") for t in tokens if t.startswith("color-")][-40:]
        texts = sorted({t.removeprefix("text-").split("--")[0] for t in tokens if t.startswith("text-")})[:24]
        words = {word for word in re.findall(r"[^\W\d_]+", query.lower(), flags=re.UNICODE) if len(word) > 2}
        ranked = []
        for component in design.components:
            searchable = " ".join([
                str(component.get("name", "")), str(component.get("description", "")),
                " ".join(str(key) + " " + " ".join(map(str, values if isinstance(values, list) else [values]))
                         for key, values in (component.get("props") or {}).items()),
                " ".join(" ".join(map(str, variant.get("props", {}).values()))
                         for variant in (component.get("variants") or [])),
            ]).lower()
            component_words = set(re.findall(r"[^\W\d_]+", searchable, flags=re.UNICODE))
            score = len(words & component_words)
            if score:
                ranked.append((score, component))
        ranked.sort(key=lambda item: (-item[0], str(item[1].get("name", "")).lower()))
        relevant = [component for _, component in ranked]
        names = ", ".join(
            str(c.get("name") or c.get("id") or "Component")
            + (f" ({'/'.join(c['props'])})" if c.get("props") else "")
            for c in relevant
        ) or "No component matched this screen's name, purpose, content, actions, or variant labels."
        variants: list[tuple[str, str, str]] = []
        for component in relevant:
            for variant in component.get("variants") or []:
                variant_id = str(variant.get("id") or "")
                if variant_id:
                    variants.append((str(component.get("name") or component.get("id") or "Component"),
                                     variant_id, str(variant.get("props") or {})))
        snippet_map = await self.store.get_snippets(design.id, [variant_id for _, variant_id, _ in variants])
        examples = []
        remaining = 24_000
        omitted = 0
        for name, variant_id, props in variants:
            snippet = snippet_map.get(variant_id, "").strip()
            if not snippet or remaining <= 0:
                omitted += bool(snippet)
                continue
            if len(snippet) > remaining:
                omitted += 1
                snippet = snippet[:remaining]
            examples.append(f"### {name} · {props}\n```html\n{snippet}\n```")
            remaining -= len(snippet)
        example_text = (
            "\nScreen-relevant Figma component HTML (reuse matching structures/classes and choose the needed "
            "variants; replace only feature-specific labels/data, and do not force an unrelated component):\n"
            + "\n".join(examples)
            if examples else ""
        )
        if omitted:
            example_text += f"\n{omitted} additional matching variant snippet(s) were omitted only to fit the prompt context."
        return (f"{design.name} (imported from Figma) — match its look. Color utilities: {', '.join(colors)}. "
                f"Text styles: {', '.join('text-' + t for t in texts)}. Screen-relevant components: {names}."
                f"{example_text}")

    # ── stage 1: route ──
    async def analyze(self, run_id: UUID, feedback: str = "None") -> None:
        run = await self.store.get_run(run_id)
        _TRACE_CONTEXT.set((run, "analyze / requirement routing"))
        session = await self.store.get_session(run.session_id)
        run = await self._stage(run, RunStage.REQUIREMENT)
        runs = await self._session_runs(session, run)
        screens = await self.store.list_screens(session.id)
        brief = await self.store.get_design_brief(session.id)
        style_mode = await self.store.get_run_data(run.id, run.revision, "project_style_mode") or {}
        explore_project_styles = style_mode.get("mode") == "explore"
        project_wiki = (
            await self.store.get_project_design_wiki(session.project_id)
            if session.project_id and not explore_project_styles else None
        )
        inherited_style = None
        if project_wiki and isinstance(project_wiki.get("baseline"), dict):
            baseline = project_wiki["baseline"]
            contract = baseline.get("contract")
            if (not brief.style_preview_shown and isinstance(contract, dict)
                    and contract.get("key") and contract.get("name")
                    and (not brief.selected_style_contract or brief.selected_style_key == contract.get("key"))):
                inherited_style = StyleCandidate.model_validate(contract)
                # A project baseline is an approved visual contract, not prior chat.
                # Seed only the reusable style fields into this feature's brief.
                if not brief.selected_style_contract:
                    brief.style_candidates = [inherited_style]
                    brief.selected_style_key = inherited_style.key
                    brief.style_name = inherited_style.name
                    brief.style_direction = inherited_style.direction or inherited_style.tagline
                    brief.selected_style_contract = inherited_style.model_dump(mode="json")
                    brief.style_preview_shown = False
                    await self.store.save_design_brief(session.id, brief)
            if not session.design_system_id and baseline.get("design_system_id"):
                await self.store.set_session_design_system(session.id, baseline["design_system_id"])
                session.design_system_id = baseline["design_system_id"]
        usage: dict[str, int] = {}
        if session.source == "project-design-system":
            project_context = (
                "\n\nPROJECT DESIGN SYSTEM AGENT MODE (applies to this entire project):\n"
                "This conversation establishes the reusable visual baseline and one representative UI template for the project; "
                "it is not a feature handoff. Use the project name/description and all user answers as source context. "
                "Never infer a business domain from a project name alone. If the product, its users, or their main goal is unclear, "
                "ask one concise, relevant question before proposing a direction. Do not follow a fixed questionnaire or fixed order. "
                "Inspect the project description and prior answers first, then ask only the highest-impact unresolved question that "
                "could materially change this project's visual system or representative screen. Cover brand constraints, visual "
                "preference, information density, target devices, and component states only when that specific detail is still unknown "
                "and matters to the described product; do not ask again when the brief already answers it. If the product domain, "
                "main workflow, audience, or platform is already clear, acknowledge it briefly and move to a real ambiguity instead "
                "of asking a generic audience/product question. Begin with a short, concrete interpretation of the product; do not "
                "repeat the full project description, use a canned greeting, or sound like a survey. When offering answer ideas, derive "
                "2-4 concise, mutually distinct options from actors, workflows, constraints, and goals explicitly present in this project's "
                "context and prior answers. Phrase them as optional examples the user can edit. Never fall back to generic industry/persona "
                "lists, generic style adjectives, or a default color palette that the project context does not support. Vary the wording "
                "naturally and do not reuse the same introduction across turns. "
                "For each direct clarification question, end the reply with one line exactly formatted "
                "'Gợi ý trả lời: lựa chọn A | lựa chọn B | lựa chọn C', with 2-4 context-specific choices. "
                "Do not include that line when no question is being asked. The user can always answer in their own words. "
                "Once context is sufficient, summarize it and ask the user to confirm or correct it. Then propose a representative "
                "screen grounded in the confirmed project purpose and ask for approval. Only after approval, mark one representative "
                "screen and its content confirmed in the brief, set scope_confirmed=true, and proceed to the rendered three-style "
                "preview gallery. The gallery options must be genuinely distinct directions generated for this product. "
                "Do not start a feature UI spec after the user selects a gallery option; that selection saves the project-wide baseline."
            )
            run_prompt = project_context + "\n\nProject title and description are already in Current brief.\nUser message: " + run.prompt
        else:
            run_prompt = run.prompt
        decision = await self._llm_call("structured", trace_name="router.decide_intent",
            prompt=self.prompts.render(
                "router",
                brief=brief.model_dump_json(exclude_defaults=True) or "{}",
                screens=json.dumps([{"id": s.id, "name": s.name, "platform": s.platform} for s in screens], ensure_ascii=False) or "[]",
                design_rules=knowledge.ui_ux_design_rules(),
                history=self._history(runs, first_limit=6000),
                message=_clip(run_prompt, 20_000),
            ),
            schema=DesignDecision,
            usage=usage,
        )
        await self._usage(run, usage, "router")
        normalized_prompt = _norm(run.prompt)
        if re.search(r"\b(chọn|choose|select)\b", normalized_prompt):
            selected = next((item for item in brief.style_candidates
                             if item.key.casefold() in normalized_prompt or item.name.casefold() in normalized_prompt), None)
            if selected:
                brief.selected_style_key = selected.key
                brief.style_name = selected.name
                brief.style_direction = selected.direction or selected.tagline
                brief.selected_style_contract = selected.model_dump(mode="json")
                decision.brief = brief
                decision.intent = "start_design"
                decision.reply = f"Đã chọn phong cách {selected.name}. Mình sẽ dùng hướng này để lập UI spec cho các screen đã chốt."
        # Style candidates are generated and stored by the backend; do not let a
        # later router turn accidentally replace or hallucinate the gallery.
        decision.brief.style_candidates = brief.style_candidates
        decision.brief.style_preview_shown = brief.style_preview_shown
        decision.brief.selected_style_key = brief.selected_style_key
        decision.brief.style_name = brief.style_name
        decision.brief.style_direction = brief.style_direction
        decision.brief.selected_style_contract = brief.selected_style_contract
        if decision.brief.screens or decision.brief.product_description:
            await self.store.save_design_brief(session.id, decision.brief)
            brief = decision.brief
        intent = INTENTS[decision.intent]
        await self._trace_decision(run, "router.intent", "normalized model intent", decision.intent, {
            "mapped_intent": intent, "targets": decision.target_screen_ids,
            "missing_fields": brief.missing_fields(), "selected_style": brief.selected_style_key,
        })
        if decision.intent == "start_design" and brief.missing_fields():
            # Keep the user's partial decisions and continue gathering instead of
            # entering spec generation with unresolved screen content.
            intent = "gather"
            decision.intent = "gather"
            unresolved = [screen.name for screen in brief.screens if not screen.confirmed or screen.open_questions]
            detail = ", ".join(unresolved) or "phạm vi hoặc nền tảng"
            decision.reply = (
                f"Mình đã lưu các lựa chọn của bạn. Trước khi lập UI spec, mình cần chốt thêm "
                f"nội dung/chức năng cho: {detail}. Bạn có thể trả lời các mục còn mở ở trên."
            )
            await self._trace_decision(run, "start_design.missing_brief", "intent == start_design and brief.missing_fields()", "continue_gathering", {
                "missing_fields": brief.missing_fields(), "unresolved_screens": unresolved,
            })
        if inherited_style:
            source_title = (project_wiki or {}).get("baseline", {}).get("source_feature_title")
            source_text = f" từ feature **{source_title}**" if source_title else ""
            decision.reply = (
                f"**Project Wiki:** Feature này sẽ dùng chuẩn thiết kế đã duyệt **{inherited_style.name}**{source_text}. "
                "Mình chỉ kế thừa design system chung; yêu cầu và trạng thái vẫn lấy từ BA package của feature hiện tại.\n\n"
                f"{decision.reply}"
            )
        if decision.intent == "start_design" and inherited_style and not brief.missing_fields():
            await self._offer_style_previews(
                run, session, brief, inherited_candidate=inherited_style,
                inherited_source=(project_wiki or {}).get("baseline", {}).get("source_feature_title"),
            )
            return
        valid_style_choice = (
            brief.style_preview_shown
            and bool(brief.style_direction)
            and any(candidate.key == brief.selected_style_key for candidate in brief.style_candidates)
        )
        if decision.intent == "start_design" and not valid_style_choice:
            await self._trace_decision(run, "style_choice.validation", "intent == start_design and not valid_style_choice", "generate_style_previews", {
                "style_preview_shown": brief.style_preview_shown,
                "style_direction_present": bool(brief.style_direction),
                "selected_style_key": brief.selected_style_key,
                "available_style_keys": [candidate.key for candidate in brief.style_candidates],
            })
            brief.selected_style_key = ""
            brief.style_name = None
            brief.style_direction = ""
            brief.selected_style_contract = {}
            await self.store.save_design_brief(session.id, brief)
            await self._offer_style_previews(run, session, brief)
            return
        await self._trace_decision(run, "style_choice.validation", "intent == start_design and valid_style_choice", "continue_to_spec", {
            "valid_style_choice": valid_style_choice, "selected_style_key": brief.selected_style_key,
        })
        await self._emit(run, "assistant.message", {"intent": decision.intent, "message": decision.reply})
        if decision.intent in {"chat", "clarify_design", "propose_scope", "gather"}:
            await self._trace_decision(run, "router.exit", "intent is chat / clarify / gather", "finish_without_build", {"intent": decision.intent})
            await self._stage(run, RunStage.FINISHED, RunStatus.COMPLETED, intent=intent, assistant_message=decision.reply)
            return
        if decision.intent == "start_design":
            await self._trace_decision(run, "router.next_stage", "intent == start_design", "write_spec")
            run = await self.store.update_run(run.id, intent=intent, assistant_message=decision.reply)
            await self.write_spec(run.id, feedback="None")
            return
        # edit_screens: change existing screens (or add new ones) directly, no review gates.
        spec = await self._current_spec(session, runs, screens)
        for screen_id in decision.target_screen_ids:
            if spec.screen(screen_id) is None:
                named = next((s for s in brief.screens if s.id == screen_id), None)
                spec.screens.append(ScreenSpec(
                    id=screen_id, name=named.name if named else screen_id.replace("-", " ").title(),
                    platform=(named.platforms[0] if named and named.platforms else spec.screens[0].platform if spec.screens else "desktop"),
                    purpose=(named.purpose if named else decision.instruction),
                ))
        targets = [s for s in decision.target_screen_ids if spec.screen(s)] or [s.id for s in spec.screens]
        await self._trace_decision(run, "router.targets", "use valid targets, otherwise all spec screens", "build_screens", {
            "requested_targets": decision.target_screen_ids, "selected_targets": targets,
        })
        run = await self.store.update_run(run.id, intent=intent, assistant_message=decision.reply, specification=spec)
        await self.build(run.id, targets, instruction=decision.instruction or run.prompt, review=False)

    async def capture_project_wiki_visual_system(self, session: SessionView) -> dict[str, Any]:
        """Capture explicit design tokens and annotated component roles from every approved screen."""
        screens = [item for item in await self.store.list_screens(session.id, with_html=True) if item.html]
        platforms = list(dict.fromkeys(item.platform for item in screens))
        design = await design_context(self.store, session)
        profiles: dict[str, Any] = {}
        for platform in platforms:
            platform_screens = [item for item in screens if item.platform == platform]
            observations = []
            for source in platform_screens:
                html = await inline_assets(source.html or "", session.project_id)
                async with self.service.renderer.page(
                    html,
                    width=source.width or SCREEN_WIDTHS.get(platform, 390),
                    height=900,
                    theme_css=design.theme_css,
                    fonts=design.fonts,
                ) as page:
                    profile = await page.evaluate(_PROJECT_WIKI_STYLE_PROFILE_JS) or {}
                observations.append({
                    "screen_id": source.id,
                    "screen_name": source.name,
                    "screen_version": source.version,
                    "width": source.width,
                    "html_sha256": hashlib.sha256((source.html or "").encode("utf-8")).hexdigest(),
                    "profile": profile,
                })
            aggregate = _merge_project_wiki_profiles(observations)
            source = platform_screens[0]
            profiles[platform] = {
                "source_screen_id": source.id,
                "source_screen_name": source.name,
                "source_screen_version": source.version,
                "source_screen_width": source.width,
                "source_session_id": str(session.id),
                "profile": aggregate,
                "css": _project_wiki_style_css(aggregate),
            }
        if not profiles:
            raise AppError(
                "PROJECT_WIKI_STYLE_SOURCE_EMPTY",
                "No approved screen is available for the Project Wiki baseline",
                "The approved feature has no saved screen HTML. Save at least one approved screen before promoting its style to the project Wiki.",
                409,
                True,
                action="Restore or approve a feature with saved screens, then retry.",
            )
        return {"schema_version": 3, "platforms": profiles}

    async def _project_wiki_style_reference(
        self, session: SessionView, *, style_key: str, platform: str,
    ) -> dict[str, Any] | None:
        """Render the approved source feature screen for visual-only Wiki inheritance."""
        if not session.project_id or not style_key:
            return None
        wiki = await self.store.get_project_design_wiki(session.project_id)
        baseline = (wiki or {}).get("baseline") or {}
        contract = baseline.get("contract") or {}
        if not isinstance(contract, dict) or contract.get("key") != style_key:
            return None
        selected_sample = baseline.get("selected_ui_sample") or {}
        if selected_sample.get("platform") == platform:
            from wiki.project_files import ProjectFileWiki

            project_files = ProjectFileWiki()
            try:
                template_html = project_files.read(session.project_id, str(selected_sample["html_path"])).decode("utf-8")
                template_css = project_files.read(session.project_id, str(selected_sample["css_path"])).decode("utf-8")
                sample_image = project_files.read(session.project_id, str(selected_sample["preview_path"]))
                design_system = baseline.get("design_system") or {}
                if not design_system and baseline.get("design_system_path"):
                    try:
                        design_system = json.loads(project_files.read(
                            session.project_id, str(baseline["design_system_path"])
                        ).decode("utf-8"))
                    except Exception:
                        logger.info("Project Design System contract unavailable for %s", session.project_id)
                saved_visual_system = baseline.get("visual_system") or {}
                saved_platform = (saved_visual_system.get("platforms") or {}).get(platform) or {}
                style_profile = saved_platform.get("profile") if isinstance(saved_platform.get("profile"), dict) else {}
                return {
                    "image": sample_image,
                    "feature_title": baseline.get("source_feature_title") or session.title,
                    "screen_name": selected_sample.get("screen_name") or selected_sample.get("name") or "Selected UI sample",
                    "platform": platform,
                    "style_name": contract.get("name", ""),
                    "wiki_version": (wiki or {}).get("version"),
                    "visual_system_source": "selected_ui_template",
                    "style_lock_css": template_css,
                    "style_profile": style_profile,
                    "template_html": template_html,
                    "template_css": template_css,
                    "design_system": design_system,
                }
            except Exception as error:
                logger.exception("Could not load the selected Project Wiki UI template for project %s", session.project_id)
                raise AppError(
                    "PROJECT_WIKI_TEMPLATE_UNAVAILABLE",
                    "The approved Project Wiki UI template could not be loaded",
                    "The saved Design System is missing its selected HTML/CSS/image template. Restore or re-approve the Design System before building this feature.",
                    502,
                    True,
                    action="Retry after restoring the selected Project Wiki UI template.",
                ) from error
        project_template = baseline.get("project_template") or {}
        if project_template.get("storage_key"):
            try:
                from platform_core.storage import get_storage

                saved_template = json.loads(get_storage().get(str(project_template["storage_key"])).decode("utf-8"))
                option = saved_template["option"]
                image_value = str(option.get("image") or "")
                image_match = re.match(r"data:[^;]+;base64,(.+)", image_value, re.DOTALL)
                if not image_match:
                    raise ValueError("The selected project preview does not contain its rendered image")
                saved_visual_system = baseline.get("visual_system") or {}
                saved_platform = (saved_visual_system.get("platforms") or {}).get(platform) or {}
                return {
                    "image": base64.b64decode(image_match.group(1)),
                    "feature_title": baseline.get("source_feature_title") or "Project Design System",
                    "screen_name": project_template.get("screen_name") or "Project UI template",
                    "platform": platform,
                    "style_name": contract.get("name", ""),
                    "wiki_version": (wiki or {}).get("version"),
                    "visual_system_source": "selected_project_template",
                    "style_lock_css": str(option.get("compiled_css") or ""),
                    "style_profile": saved_platform.get("profile") if isinstance(saved_platform.get("profile"), dict) else {},
                    "template_html": str(option.get("html") or ""),
                    "template_css": str(option.get("compiled_css") or ""),
                }
            except Exception as error:
                logger.exception("Could not load the selected Project Design System template for %s", session.project_id)
                raise AppError(
                    "PROJECT_WIKI_TEMPLATE_UNAVAILABLE",
                    "The approved Project Design System template could not be loaded",
                    "The saved project-wide template is missing its HTML, CSS, or preview image.",
                    502,
                    True,
                    action="Restore or re-approve the Project Design System template.",
                ) from error
        source_id = (wiki or {}).get("source_session_id")
        if not source_id:
            raise AppError(
                "PROJECT_WIKI_STYLE_REFERENCE_UNAVAILABLE",
                "The approved Project Wiki has no source design session",
                "The project style baseline exists, but its approved source screen cannot be loaded. Restore or re-approve the source feature before building this feature.",
                409,
                True,
                action="Restore or re-approve the Project Wiki source feature, then retry.",
            )
        try:
            source_session = await self.store.get_session(UUID(str(source_id)))
            if not source_session or source_session.project_id != session.project_id:
                raise ValueError("The Project Wiki source session is missing or belongs to another project")
            saved_visual_system = baseline.get("visual_system") or {}
            saved_platform = (saved_visual_system.get("platforms") or {}).get(platform) or {}
            source_screen_id = saved_platform.get("source_screen_id")
            source_screen_version = saved_platform.get("source_screen_version")
            source_screen = None
            if source_screen_id and source_screen_version:
                source_screen = await self.store.get_screen(
                    source_session.id, str(source_screen_id), int(source_screen_version),
                )
                if source_screen and source_screen.platform != platform:
                    raise ValueError("The pinned Project Wiki source screen platform does not match the requested platform")
            if source_screen is None:
                # Compatibility path for pre-snapshot Wiki entries.
                source_screens = await self.store.list_screens(source_session.id, with_html=True)
                candidates = [item for item in source_screens if item.html]
                if not candidates:
                    raise ValueError("The Project Wiki source session has no saved screen HTML")
                source_screen = next((item for item in candidates if item.platform == platform), candidates[0])
            source_design = await design_context(self.store, source_session)
            html = await inline_assets(source_screen.html or "", session.project_id)
            style_profile = saved_platform.get("profile")
            if not isinstance(style_profile, dict) or not isinstance(style_profile.get("roles"), dict):
                # Legacy snapshots used guesses (first field/button/card and viewport geometry).
                # Do not promote that inferred data to a new feature's style authority.
                style_profile = {"tokens": {}, "roles": {}, "screens": []}
            style_lock_css = _project_wiki_style_css(style_profile)
            rendered = await self.service.renderer.render(
                html,
                width=source_screen.width or SCREEN_WIDTHS.get(source_screen.platform, 390),
                height=900,
                theme_css=source_design.theme_css,
                fonts=source_design.fonts,
                screenshot="jpeg",
                scale=0.8,
                lint=False,
                axe=False,
                max_height=2400,
            )
            if not rendered.image:
                raise ValueError("The Project Wiki source screen could not be rendered")
            return {
                "image": rendered.image,
                "feature_title": baseline.get("source_feature_title") or source_session.title,
                "screen_name": source_screen.name,
                "platform": source_screen.platform,
                "style_name": contract.get("name", ""),
                "wiki_version": (wiki or {}).get("version"),
                "visual_system_source": "saved_semantic_contract" if style_profile.get("roles") or style_profile.get("tokens") else "contract_only",
                "style_lock_css": style_lock_css,
                "style_profile": style_profile,
            }
        except AppError:
            raise
        except Exception as error:
            logger.exception("Could not render the Project Design Wiki source screen for style inheritance")
            raise AppError(
                "PROJECT_WIKI_STYLE_REFERENCE_UNAVAILABLE",
                "The approved Project Wiki source screen could not be loaded",
                "The new screen was not generated without its approved visual reference. Retry after the source feature is available.",
                502,
                True,
                action="Retry the build; if it persists, restore or re-approve the source feature's design in Project Wiki.",
            ) from error

    @staticmethod
    def _style_reference_instruction(reference: dict[str, Any] | None) -> str:
        if not reference:
            return ""
        profile = reference.get("style_profile") if isinstance(reference.get("style_profile"), dict) else {}
        contract_lines = []
        for name, record in sorted((profile.get("tokens") or {}).items()):
            if isinstance(record, dict) and not record.get("conflict") and record.get("value"):
                contract_lines.append(f"- Token {name}: {record['value']}")
        for role, variants in sorted((profile.get("roles") or {}).items()):
            if not isinstance(variants, dict):
                continue
            for variant, properties in sorted(variants.items()):
                if not isinstance(properties, dict):
                    continue
                values = [f"{prop}={record['value']}" for prop, record in sorted(properties.items())
                          if isinstance(record, dict) and not record.get("conflict") and record.get("value")]
                if values:
                    contract_lines.append(f"- Component {role}/{variant}: " + ", ".join(values))
        design_system = reference.get("design_system") if isinstance(reference.get("design_system"), dict) else {}
        for token in design_system.get("tokens", []):
            if isinstance(token, dict) and token.get("name") and token.get("value") is not None:
                contract_lines.append(f"- Shared token {token['name']} ({token.get('category', 'token')}): {token['value']}")
        if design_system.get("components"):
            contract_lines.append(f"- Shared component catalog: {len(design_system['components'])} components")
        unresolved = design_system.get("unresolved") or []
        if unresolved:
            contract_lines.append("- Unresolved Design System items are not approved tokens/components; do not guess their values.")
        contract_text = "\n".join(contract_lines) or "- No reusable semantic values were recorded; follow the selected project visual contract."
        has_selected_template = bool(reference.get("template_html"))
        reference_rule = (
            "The approved Project Wiki sample is the canonical visual template. Preserve its visual shell, "
            "spacing, typography, component shapes, surfaces, colors and responsive behavior. Adapt its "
            "content and controls to this feature's BA requirements."
            if has_selected_template else
            "The attached approved screen is visual reference only; do not copy its page structure, labels or "
            "feature-specific content."
        )
        instruction = (
            "\nProject Wiki inheritance: use the approved project visual contract and semantic token/component "
            "roles as the source of truth. Reuse only values recorded without conflicts; a conflict means the "
            "contract does not prescribe that value. Preserve the current feature's BA-defined screen content, "
            "states, navigation and interactions. " + reference_rule + " Apply shared values to the same semantic component role/variant only; do not style elements "
            "by size, DOM position, class-name guesses, or by being the first/largest control. When the Wiki has "
            "no matching role, follow the selected visual contract consistently and do not invent a Wiki override.\n"
            "Approved Project Wiki values (conflicted values are intentionally omitted):\n" + contract_text
        )
        template_html = str(reference.get("template_html") or "")
        template_css = str(reference.get("template_css") or "")
        if template_html:
            instruction += (
                "\nThe attached Project Wiki image and the following saved HTML/CSS are the exact UI sample "
                "approved for this project. Start from this template and adapt its content and controls to "
                "the current BA package while preserving its visual design.\nSelected UI sample HTML:\n```html\n" + template_html[:40_000] +
                "\n```\nSelected UI sample compiled CSS:\n```css\n" + template_css[:40_000] + "\n```"
            )
        return instruction

    async def _offer_style_previews(
        self, run: RunView, session: SessionView, brief, *,
        inherited_candidate: StyleCandidate | None = None, inherited_source: str | None = None,
    ) -> None:
        """Render three style directions, or one inherited project-Wiki preview."""
        _TRACE_CONTEXT.set((run, "style previews"))
        screen = _representative_preview_screen(brief.screens)
        if screen is None:
            raise AppError("STYLE_PREVIEW_UNAVAILABLE", "No confirmed screen is available for a style preview",
                           "Confirm at least one screen before choosing a visual style.", 409, True,
                           action="Confirm the screen scope.")
        platforms = screen.platforms or brief.platforms or ["mobile"]
        platform = "mobile" if "mobile" in platforms else platforms[0]
        width = SCREEN_WIDTHS.get(platform, 390)
        height = 900 if platform == "mobile" else 920
        design = await design_context(self.store, session)
        approved_screen_data = screen.model_dump(mode="json")
        source_default_regions = _default_source_items(approved_screen_data, "content_outline")
        source_default_actions = _default_source_items(approved_screen_data, "actions")
        source_visible_copy = _default_source_items(approved_screen_data, "visible_copy")
        default_purpose = "" if _is_conditional_copy(screen.purpose, approved_screen_data) else screen.purpose
        default_analysis_screen = screen.model_copy(update={
            "content_outline": source_default_regions,
            "actions": source_default_actions,
            "default_actions": source_default_actions,
            "purpose": default_purpose,
            "conditional_content": [],
            "states": [],
            "state_spec": [],
        })
        query = " ".join([screen.name, default_purpose, *source_default_regions, *source_default_actions])
        screen_context = json.dumps({
            "screen_name": screen.name,
            "purpose": default_purpose,
            "platform": platform,
            "approved_default_regions": source_default_regions,
            "approved_default_actions": source_default_actions,
            "visible_copy": source_visible_copy,
            "preview_initial_state_only": True,
        }, ensure_ascii=False)
        settings = self.service.settings
        style_reference = (
            await self._project_wiki_style_reference(session, style_key=inherited_candidate.key, platform=platform)
            if inherited_candidate else None
        )
        reference_instruction = self._style_reference_instruction(style_reference)
        preview_theme = design.theme_css if design.id != DEFAULT_DESIGN_SYSTEM else None
        preview_fonts = design.fonts if design.id != DEFAULT_DESIGN_SYSTEM else None
        # Product descriptions and prior conversation can contain state
        # analysis. Derive generation context only from the approved default
        # screen contract so state prose cannot enter a preview prompt/query.
        product_query = " ".join([brief.product_name, screen.name, default_purpose,
                                  *source_default_regions, *source_default_actions, platform])
        preview_assets = relevant_assets(f"{product_query} {screen.name} {default_purpose}")
        asset_catalog = [{key: value for key, value in asset.items() if key != "path"}
                         for asset in preview_assets]

        total_usage: dict[str, int] = {}

        def add_usage(usage: dict[str, int]) -> None:
            for key, value in usage.items():
                total_usage[key] = total_usage.get(key, 0) + value

        # Plan the visual directions from the live product/screen brief on every
        # gallery run. The knowledge catalogue informs the product context, while
        # the art directions themselves stay specific to this project.
        plan_usage: dict[str, int] = {}
        design_knowledge = knowledge.profile_text(knowledge.product_profile(product_query))
        try:
            plan = await self._llm_call("structured", trace_name="style_preview.plan_directions",
                prompt=self.prompts.render(
                    "style-preview-plan",
                    product=brief.product_name or "Product",
                    product_context="",
                    design_query=product_query,
                    design_system=(design.name + "\n" + design.theme_css[:5000]
                                   if design.id != DEFAULT_DESIGN_SYSTEM else design_knowledge),
                    domain_palette_guidance=knowledge.domain_palette_guidance(),
                    screen=screen_context,
                    available_assets=json.dumps(asset_catalog, ensure_ascii=False),
                ),
                schema=StylePreviewPlan,
                model=settings.effective_designer_model,
                usage=plan_usage,
            )
            candidates = [inherited_candidate] if inherited_candidate else plan.options
            # The art director sees only first-paint content; state metadata stays
            # in the BA spec and never enters preview generation.
            content_contract = plan.content_contract
            content_contract.default_visible_actions = list(source_default_actions)
            content_contract.conditional_actions = []
            content_contract.conditional_text_exact = []
            required_visible_text, conditional_text = _validate_preview_content_contract(default_analysis_screen, content_contract)
            # The plan's action classification is useful for QA analysis only.
            # UI rendering is grounded in the separately approved default list.
            approved_default_actions = source_default_actions
            required_visible_text = [
                item for item in required_visible_text
                if any(_norm(item) == _norm(exact) for exact in source_visible_copy)
            ]
            screen_context = json.dumps({
                "screen_name": screen.name,
                "purpose": "" if _is_conditional_copy(screen.purpose, screen.model_dump(mode="json")) else screen.purpose,
                "platform": platform,
                "approved_content_outline": _default_source_items(screen.model_dump(mode="json"), "content_outline"),
                "preview_default_actions": approved_default_actions,
                "visible_copy": source_visible_copy,
                "preview_initial_state_only": True,
                "required_visible_text_exact": required_visible_text,
            }, ensure_ascii=False)
        except Exception as error:
            logger.exception("Project-aware style planning/content classification failed")
            raise AppError(
                "STYLE_PREVIEW_UNAVAILABLE",
                "Could not classify approved content for the style preview",
                "Retry the style preview; the gallery was not generated because its default-versus-conditional content contract could not be verified.",
                502,
                True,
                action="Retry the style preview.",
            ) from error
        add_usage(plan_usage)
        brief.style_candidates = list(candidates)
        await self.store.save_design_brief(session.id, brief)

        async def render_candidate(
            candidate: StyleCandidate,
            *,
            review_feedback: str = "",
            previous_html: str = "",
        ) -> dict[str, Any]:
            candidate_started = time.perf_counter()
            await self._emit(run, "debug.function.started", {
                "phase": "style previews", "function": "designer.render_candidate",
                "input": _trace_value({"candidate": candidate, "review_feedback": review_feedback,
                                       "previous_html": previous_html}),
            })
            usage: dict[str, int] = {}
            assigned_asset = asset_by_id(candidate.preview_asset_id, preview_assets) if candidate.preview_asset_id else None
            option_theme = _candidate_theme(candidate, preserve_system_type=(design.id != DEFAULT_DESIGN_SYSTEM))
            option_theme_css = "\n".join(part for part in (
                design.theme_css if design.id != DEFAULT_DESIGN_SYSTEM else "",
                option_theme.get("css") if option_theme else "",
            ) if part) or None
            option_fonts = (option_theme.get("fonts") if option_theme else None) or (
                design.fonts if design.id != DEFAULT_DESIGN_SYSTEM else None
            )
            asset_description = "(none — use an original motif only if appropriate)"
            asset_data = ""
            if assigned_asset:
                asset_description = (
                    f"{assigned_asset['title']} illustration, Storyset {assigned_asset['style']} style. "
                    f"{assigned_asset['description']} Use the complete illustration as a large decorative background/scene layer "
                    "in a dedicated hero or screen-background region; it is not a logo, app icon, badge, or header ornament. "
                    "Keep the actual wordmark separate and keep text/controls readable above the image."
                )
                asset_data = inline_svg(assigned_asset)
            system = self.prompts.render(
                "builder", width=width,
                direction=(f"Gallery palette exploration policy: {plan.palette_exploration}. "
                           f"Style option '{candidate.name}' is the visual authority for this preview. "
                           f"Direction: {candidate.direction or candidate.tagline}. "
                           f"Required background treatment: {candidate.background_treatment or 'create a palette-derived decorative gradient/texture and original SVG motif, behind content, covering at least 18% of the canvas'}. "
                           f"Visual contract: {candidate.model_dump_json(exclude={'key', 'name', 'tagline', 'direction', 'recommended'})}. "
                           "Make its composition, typography, surfaces, color balance and product-specific visual motif unmistakable; "
                           "do not fall back to a generic form or default style." + reference_instruction),
                design_system=await self._design_text(design, query),
                skill=knowledge.frontend_design_skill() + "\n\n" + knowledge.style_preview_skill(),
                design_rules=knowledge.ui_ux_design_rules(),
            )
            fragment = await self._llm_call("generate", trace_name="style_preview.generate_candidate",
                system=system,
                text=self.prompts.render(
                    "style-preview", width=width, product=brief.product_name or "Product",
                    product_context="",
                    platform=platform,
                    screen=screen_context, style_name=candidate.name,
                    style_direction=candidate.direction or candidate.tagline,
                    assigned_visual_asset=asset_description,
                    visual_contract=candidate.model_dump_json(exclude={"key", "name", "tagline", "direction", "recommended"}),
                    review_feedback=review_feedback or "(none)",
                    previous_html=previous_html or "(none)",
                ),
                images=[style_reference["image"]] if style_reference else None,
                usage=usage, model=settings.effective_designer_model,
                timeout_seconds=settings.designer_page_timeout_seconds,
            )
            html = _normalize_malformed_text_color_classes(
                _bind_primary_button_foreground(fragment_of(extract_html(fragment)), candidate)
            )
            render_html = html
            asset_included = False
            if assigned_asset and asset_data:
                if "__DESIGNER_PREVIEW_ASSET__" in render_html:
                    render_html = render_html.replace("__DESIGNER_PREVIEW_ASSET__", asset_data)
                    asset_included = True
                else:
                    logger.warning("Style preview %s did not include its assigned illustration token", candidate.key)
            result = await self.service.renderer.render(
                render_html, width=width, height=height,
                theme_css=option_theme_css or preview_theme, fonts=option_fonts or preview_fonts, screenshot="jpeg",
                scale=0.7, lint=True, axe=True, strict_touch_targets=(platform == "mobile"),
                strict_visual_quality=True, required_texts=required_visible_text,
                forbidden_texts=conditional_text, max_height=1200,
            )
            add_usage(usage)
            candidate_result = {
                "key": candidate.key, "name": candidate.name, "tagline": candidate.tagline,
                "recommended": candidate.recommended,
                "asset_credit": assigned_asset.get("credit", "") if asset_included and assigned_asset else "",
                "asset_source_url": assigned_asset.get("source_url", "") if asset_included and assigned_asset else "",
                "image": f"data:{result.mime or 'image/jpeg'};base64,{base64.b64encode(result.image or b'').decode('ascii')}",
                "html": render_html,
                "compiled_css": result.css,
                "preview_width": width,
                "preview_height": result.height,
                "rendered_image": result.image,
                "qa_errors": [finding for finding in result.blocking],
            }
            await self._emit(run, "debug.function.completed", {
                "phase": "style previews", "function": "designer.render_candidate",
                "duration_ms": round((time.perf_counter() - candidate_started) * 1000, 2),
                "output": _trace_value({key: value for key, value in candidate_result.items()
                                         if key not in {"image", "rendered_image"}}),
            })
            return candidate_result

        previews = await asyncio.gather(*(render_candidate(candidate) for candidate in candidates))

        def static_issues(items: list[dict[str, Any]]) -> dict[str, list[str]]:
            failed: dict[str, list[str]] = {}
            for item in items:
                if item.get("qa_errors"):
                    failed[item["key"]] = [
                        (
                            f"{finding.get('rule')}: {finding.get('message')} "
                            "Affected visible nodes: " + "; ".join(
                            " / ".join(part for part in (
                                    str(node.get("text") or "").strip(),
                                    str(node.get("html") or "").strip(),
                                    (
                                        "foreground=" + str(node.get("foreground")) + "; surfaces=" + " over ".join(
                                            f"{surface.get('tag')}.{surface.get('className')} [{surface.get('background')}]"
                                            for surface in node.get("surfaces", [])
                                        )
                                    ) if finding.get("rule") == "color-contrast" and node.get("surfaces") else "",
                                ) if part)
                                for node in finding.get("elements", [])[:3]
                            ) + ". " + (
                                "Add a decorative data-preview-background layer with palette-derived CSS gradients/textures or inline SVG; "
                                "it must cover at least 18% of the canvas, stay behind content, and add no copy or controls. "
                                if finding.get("rule") == "background-treatment" else
                                "Set the actual text/password input hit box to at least 48px high (for example min-h-12), "
                                "and make its field row at least 48px high; do not enlarge only a non-interactive outer card. "
                                "Keep any eye/clear icon button at least 48×48px."
                                if finding.get("rule") == "tap-target" else
                                "Separate the reported interactive elements so their rendered hit areas do not intersect. "
                                "If a fixed or sticky action bar covers a selectable card, input, or other control, keep the action "
                                "in normal flow or provide sufficient scrollable bottom space for the covered content. Preserve every approved control."
                                if finding.get("rule") in {"interactive-overlap", "sticky-footer-overlap"} else
                                "Put the related input and its trailing icon button inside one full-width field row. "
                                "Put the border, background, and radius on the full-width outer row; make the input "
                                "borderless with flex-1 min-w-0, and keep the icon button inside that row at 48×48px. "
                                "Align this outer row's left and right edges with its sibling field shells; do not "
                                "let the input itself become a narrower bordered shell."
                                if finding.get("rule") == "form-field-alignment" else
                                "Use the reported foreground and rendered surface stack to fix only each failing text element. Remove its existing text-color utility before adding exactly one valid color utility; never produce malformed classes such as `text-text-[#HEX]`. For a small label over a translucent dark overlay, use a high-contrast light foreground only if it reaches 4.5:1 on the composited surface; otherwise move the label onto an opaque light chip and use the palette's dark foreground. On a solid bright primary surface, use the palette's dark foreground when that is the passing pair. For text inside a primary button, use `text-primary-foreground`. Do not darken the whole card or alter other text. Preserve the palette and verify the actual pair reaches 4.5:1 (3:1 for large text)."
                                if finding.get("rule") == "color-contrast" else
                                "Keep this conditional-state text and its controls out of the initial preview; show them only after the approved trigger. Preserve the interaction in the downstream screen spec."
                                if finding.get("rule") == "conditional-content-on-first-paint" else
                                "Remove the dark pill/camera cutout at the top-center of the app canvas; it is simulated device chrome, not safe-area spacing. Keep the approved page title/header visible and reserve the top inset with empty layout space only."
                                if finding.get("rule") == "fake-device-notch" else
                                "Keep all text and interactive controls inside their visible content container; "
                                "only decorative imagery may intentionally crop inside a dedicated background region."
                            ) + " Rerender."
                        )
                        for finding in item["qa_errors"]
                    ]
            return failed

        async def critique_previews() -> dict[str, list[str]] | None:
            usage: dict[str, int] = {}
            try:
                response = await self._llm_call("generate", trace_name="style_preview.review_candidate",
                    system=self.prompts.render("style-preview-review"),
                    text=("Approved screen data and exact-content contract:\n" + screen_context +
                          ("\n\nReview the inherited style preview for alignment with its saved visual contract:\n"
                           if inherited_candidate else
                           "\n\nCompare the three screenshots as one gallery. Style options in screenshot order:\n") +
                          ("\nThe first attached image is the approved Project Wiki source screen for shared visual styling only; "
                           "remaining images are the new feature's generated previews in candidate order.\n"
                           if inherited_candidate and style_reference else "") +
                          json.dumps([{"key": item.key, "name": item.name, "direction": item.direction or item.tagline,
                                       "palette_exploration": plan.palette_exploration,
                                       "palette": item.palette, "composition": item.composition,
                                       "surface_language": item.surface_language, "shape_language": item.shape_language,
                                       "hero_placement": item.hero_placement,
                                       "preview_asset_id": item.preview_asset_id}
                                      for item in candidates], ensure_ascii=False)),
                    images=([style_reference["image"]] if inherited_candidate and style_reference else []) +
                           [item["rendered_image"] for item in previews if item.get("rendered_image")],
                    usage=usage, model=settings.effective_designer_model,
                    timeout_seconds=settings.designer_page_timeout_seconds,
                )
            except Exception as error:
                logger.warning("Style preview visual review skipped: %s", error)
                return None
            add_usage(usage)
            parsed = _style_preview_reviews(response, candidates)
            if parsed is not None:
                return parsed

            # A visual review can be useful even when the model adds prose or emits a
            # slightly different JSON shape. Normalize that response to the strict
            # schema without asking the model to inspect the screenshots again. Never
            # infer a pass when the original review does not make a clear decision.
            logger.warning(
                "Style preview visual review returned an unsupported format (chars=%d); attempting schema normalization",
                len(response),
            )
            normalize_usage: dict[str, int] = {}
            candidate_keys = [item.key for item in candidates]
            try:
                normalized = await self._llm_call("structured", trace_name="style_preview.normalize_candidate",
                    prompt=(
                        "Normalize the supplied visual-review response into the required JSON schema. "
                        "Preserve its judgments; do not perform a new visual review or invent a pass. "
                        "Return one review for each exact candidate key: " + json.dumps(candidate_keys) + ". "
                        "If the source does not clearly establish that a candidate passed, set ready=false "
                        "and add a short issue stating that the review result was unclear.\n\n"
                        "Original visual-review response:\n" + response
                    ),
                    schema=_StylePreviewReviewBatch,
                    max_attempts=2,
                    model=settings.effective_designer_model,
                    usage=normalize_usage,
                )
                add_usage(normalize_usage)
                return _style_preview_reviews(
                    normalized.model_dump_json(), candidates
                )
            except Exception as error:
                logger.warning("Style preview review schema normalization failed: %s", error)
                return None

        # Deterministic browser checks are the hard gate. They detect missing
        # approved labels, overflow/clipping, broken assets, insufficient contrast,
        # overlapping controls, and strict mobile tap-target/alignment errors.
        # Vision-model critiques are useful guidance but are probabilistic; they
        # must never suppress an otherwise renderable gallery after repeating the
        # same subjective observation.
        max_static_repairs = 3
        static_findings = static_issues(previews)
        await self._trace_decision(run, "style_preview.static_qa", "browser blocking findings exist", "repair" if static_findings else "pass", {
            "candidate_errors": static_findings,
        })
        for repair_round in range(max_static_repairs):
            if not static_findings:
                await self._trace_decision(run, "style_preview.static_repair_loop", "no static findings remain", "stop_repairs", {
                    "repair_round": repair_round, "max_rounds": max_static_repairs,
                })
                break
            await self._trace_decision(run, "style_preview.static_repair_loop", "static findings remain", "repair_failed_candidates", {
                "repair_round": repair_round + 1, "max_rounds": max_static_repairs,
                "candidate_keys": list(static_findings), "findings": static_findings,
            })
            repair_keys = set(static_findings)
            revised = await asyncio.gather(*(
                render_candidate(
                    candidate,
                    review_feedback=(
                        f"MEASURABLE LAYOUT REPAIR {repair_round + 1}/{max_static_repairs}. Fix each browser QA error below. "
                        "Keep all approved content and controls visible; do not add unsupported copy. "
                        "For mobile, maintain consistent form-field alignment and touch targets of at least 48 by 48 CSS px.\n"
                        + "\n".join(static_findings[candidate.key])
                    ),
                    previous_html=next(item["html"] for item in previews if item["key"] == candidate.key),
                )
                for candidate in candidates if candidate.key in repair_keys
            ), return_exceptions=True)
            for candidate, result in zip(
                (candidate for candidate in candidates if candidate.key in repair_keys), revised
            ):
                if isinstance(result, Exception):
                    raise AppError("STYLE_PREVIEW_REPAIR_FAILED", "Could not repair a style preview",
                                   f"Preview option '{candidate.name}' could not pass browser layout checks. The gallery was withheld to avoid showing a broken screen.",
                                   502, True, action="Retry the style preview.") from result
                old = next(i for i, item in enumerate(previews) if item["key"] == candidate.key)
                previews[old] = result
            static_findings = static_issues(previews)

        if static_findings:
            await self._trace_decision(run, "style_preview.static_qa_final", "static findings remain after repair limit", "fail_gallery", {
                "max_repairs": max_static_repairs, "candidate_errors": static_findings,
            })
            details = "; ".join(f"{key}: {', '.join(notes)}" for key, notes in static_findings.items())
            raise AppError("STYLE_PREVIEW_QA_FAILED", "Style preview did not pass layout checks",
                           f"The gallery was withheld because measurable browser checks still fail after {max_static_repairs} targeted repairs: {details}",
                           422, True, action="Retry the style preview after updating the screen or design direction.")

        # Attempt one focused visual polish pass. Keep the last browser-QA-clean
        # render if the repair fails or introduces a measurable regression.
        review_issues = await critique_previews()
        visual_notes: dict[str, list[str]] = {
            key: list(notes) for key, notes in (review_issues or {}).items() if notes
        }
        if review_issues:
            await self._trace_decision(run, "style_preview.visual_critique", "review returned candidate issues", "attempt_one_visual_polish_pass", {
                "review_issues": review_issues,
            })
            originals = {item["key"]: item for item in previews}
            repair_candidates = [candidate for candidate in candidates if candidate.key in review_issues]
            revised = await asyncio.gather(*(
                render_candidate(
                    candidate,
                    review_feedback=(
                        "ONE FOCUSED VISUAL POLISH PASS. Apply these concrete observations where visibly valid. "
                        "Preserve approved content, use a coherent type system, and do not add new copy.\n"
                        + "\n".join(review_issues[candidate.key])
                    ),
                    previous_html=originals[candidate.key]["html"],
                )
                for candidate in repair_candidates
            ), return_exceptions=True)
            for candidate, result in zip(repair_candidates, revised):
                if isinstance(result, Exception):
                    logger.warning("Optional visual polish failed for style preview %s: %s", candidate.key, result)
                    continue
                if result.get("qa_errors"):
                    logger.warning("Optional visual polish introduced browser QA errors for %s; keeping prior render", candidate.key)
                    continue
                old = next(i for i, item in enumerate(previews) if item["key"] == candidate.key)
                previews[old] = result
            final_review = await critique_previews()
            if final_review is not None:
                visual_notes = {key: list(notes) for key, notes in final_review.items() if notes}
        else:
            await self._trace_decision(run, "style_preview.visual_critique", "no actionable review issues or review unavailable", "keep_browser_qa_clean_previews", {
                "review_issues": review_issues,
            })

        for item in previews:
            item["review_notes"] = visual_notes.get(item["key"], [])

        await self._usage(run, total_usage, "style_previews")
        if inherited_candidate:
            source = f" từ feature **{inherited_source}**" if inherited_source else " từ Project Wiki"
            message = (
                f"Màn hình **{screen.name}** được xem trước theo chuẩn **{inherited_candidate.name}**{source}. "
                "Nội dung và trạng thái vẫn theo BA package của feature hiện tại. "
                "Chọn **Dùng chuẩn này** để tiếp tục, hoặc xem ba hướng khác nếu muốn đổi phong cách."
            )
        else:
            message = (f"Mình chọn **{screen.name}** làm màn hình đại diện vì nó thể hiện rõ nội dung và trải nghiệm của sản phẩm. "
                       "Ba preview giữ nguyên nội dung màn hình này nhưng trình bày theo ba hướng thị giác khác nhau. "
                       "Chọn một hướng để áp dụng cho toàn bộ các màn hình đã chốt.")
        await self._emit(run, "assistant.message", {"intent": "gather", "message": message})
        # Keep the exact selected preview recoverable for Project Wiki approval.
        # The JPEG is the same rendered image shown in the style picker; the raw
        # HTML/CSS and dimensions are already part of each option payload.
        public_previews = [{key: value for key, value in item.items()
                            if key not in {"qa_errors", "rendered_image"}}
                           for item in previews]
        await self._emit(run, "style.inherited.preview" if inherited_candidate else "style.previews", {
            "screen_id": screen.id, "screen_name": screen.name,
            "screen_purpose": screen.purpose, "platform": platform, "width": width,
            "source_feature_title": inherited_source, "options": public_previews,
        })
        brief.style_preview_shown = True
        await self.store.save_design_brief(session.id, brief)
        await self._stage(run, RunStage.FINISHED, RunStatus.COMPLETED, intent="gather", assistant_message=message)

    async def _current_spec(self, session: SessionView, runs: list[RunView], screens) -> UiSpec:
        for r in reversed(runs):
            if r.specification and r.specification.get("schema_version") == "html-1":
                return UiSpec.model_validate(r.specification)
        if screens:
            return UiSpec(screens=[ScreenSpec(id=s.id, name=s.name, platform=s.platform) for s in screens])
        return UiSpec.model_construct(schema_version="html-1", style_direction="", screens=[])

    # ── stage 2: specification + art direction ──
    async def write_spec(self, run_id: UUID, feedback: str = "None") -> None:
        run = await self.store.get_run(run_id)
        _TRACE_CONTEXT.set((run, "specification / art direction"))
        session = await self.store.get_session(run.session_id)
        run = await self._stage(run, RunStage.SPECIFICATION)
        runs = await self._session_runs(session, run)
        brief = await self.store.get_design_brief(session.id)
        design = await design_context(self.store, session)
        imported = design.id != DEFAULT_DESIGN_SYSTEM and bool(design.components)
        query_parts = [brief.product_name]
        for item in brief.screens:
            source, _ = _split_approved_brief(item.model_dump(mode="json"))
            query_parts.extend([source.get("name", ""), source.get("purpose", ""),
                                *(source.get("content_outline") or []), *(source.get("default_actions") or [])])
        query = " ".join(part for part in query_parts if part)
        spec_brief = brief.model_dump(mode="json", exclude_defaults=True)
        state_material: dict[str, list[str]] = {"states": [], "conditional_content": [], "conditional_actions": []}
        default_screens = []
        for item in brief.screens:
            default_source, state_reference = _split_approved_brief(item.model_dump(mode="json"))
            default_screens.append(default_source)
            for field in state_material:
                state_material[field].extend(state_reference.get(field) or [])
            state_material["state_spec"] = [
                *state_material.get("state_spec", []), *(state_reference.get("state_spec") or [])
            ]
        spec_brief["screens"] = default_screens
        spec_brief["product_description"] = ""
        spec_brief["design_query"] = query
        safe_style_direction = "" if _is_conditional_copy(brief.style_direction, state_material) else brief.style_direction
        safe_style_contract = _default_visual_contract(brief.selected_style_contract, state_material)
        spec_brief["style_direction"] = safe_style_direction
        spec_brief["selected_style_contract"] = safe_style_contract
        spec_brief["style_candidates"] = []
        usage: dict[str, int] = {}
        spec = await self._llm_call("structured", trace_name="specification.generate",
            prompt=self.prompts.render(
                "spec",
                profile=knowledge.profile_text(knowledge.product_profile(query)),
                skill=knowledge.frontend_design_skill(),
                design_rules=knowledge.ui_ux_design_rules(),
                palette_rule=(
                    "An imported design system supplies components and typography. The selected project visual contract "
                    "is authoritative for palette and shared art direction; use its exact semantic color tokens on every "
                    "screen and do not substitute imported colors."
                    if imported and safe_style_contract.get("palette") else
                    "A design system is imported: set `palette` to null and preserve its colors."
                    if imported else
                    "Use the selected project visual contract palette exactly when present; otherwise choose colors for this product."
                ),
                design_system=await self._design_text(design, query),
                brief=json.dumps(spec_brief, ensure_ascii=False),
                style_selection=(f"{brief.style_name}: {safe_style_direction}".strip(": ")
                                 if brief.style_name or safe_style_direction else "No style selected yet."),
                visual_contract=json.dumps(safe_style_contract, ensure_ascii=False),
                feedback=feedback or "None",
                context="No prior chat history. Use only the state-free confirmed brief and selected visual contract.",
            ),
            schema=UiSpec,
            usage=usage,
        )
        await self._usage(run, usage, "spec")
        contract = safe_style_contract
        spec.selected_style_key = brief.selected_style_key
        spec.style_name = brief.style_name or ""
        spec.style_direction = safe_style_direction or spec.style_direction
        spec.visual_contract = contract
        spec.approved_default_screens = {}
        spec.state_reference_screens = {}
        spec.state_specs = {}
        for item in brief.screens:
            default_source, state_reference = _split_approved_brief(item.model_dump(mode="json"))
            spec.approved_default_screens[item.id] = default_source
            spec.state_reference_screens[item.id] = state_reference
            state_data = spec.state_specs.get(item.id)
            merged = state_data.model_dump(mode="json", exclude_defaults=True) if state_data else {}
            for field, values in state_reference.items():
                if isinstance(values, list):
                    merged[field] = _unique_in_order([*(merged.get(field) or []), *values])
                elif field not in merged:
                    merged[field] = values
            spec.state_specs[item.id] = ScreenStateSpec.model_validate(merged)
        # State data lives in separate reference maps; generated screen fields
        # are sanitized against those references before the spec is persisted.
        for screen in spec.screens:
            approved = _approved_source_for_screen(spec, screen.id)
            screen.must_show = [item for item in screen.must_show if not _is_conditional_copy(item, approved)]
            screen.sections = [item for item in screen.sections if not _is_conditional_copy(item, approved)]
            screen.data = [item for item in screen.data if not _is_conditional_copy(item, approved)]
        # The approved Project Wiki contract is the project-wide source of truth
        # even when this feature also uses an imported component library.
        if contract.get("palette"):
            spec.design.palette = Palette.model_validate(contract["palette"])
        if not imported or contract:
            spec.design.heading_font = contract.get("heading_font") or spec.design.heading_font
            spec.design.body_font = contract.get("body_font") or spec.design.body_font
            spec.design.radius = contract.get("radius") or spec.design.radius
            spec.design.density = contract.get("density") or spec.design.density
            if safe_style_direction:
                spec.design.mood = safe_style_direction
        spec.style_direction = safe_style_direction or spec.style_direction
        theme = direction_theme(spec.design.model_dump()) if (not imported or contract.get("palette")) else None
        await self.store.set_session_visual_theme(session.id, theme)
        run = await self._stage(run, RunStage.REVIEW_SPEC, RunStatus.WAITING_REVIEW, specification=spec)
        await self._emit(run, "review.required", {"checkpoint": "specification", "screens": [s.id for s in spec.screens]})

    # ── stage 3: build ──
    async def build(self, run_id: UUID, screen_ids: list[str] | None = None, *, instruction: str = "", review: bool = True) -> None:
        run = await self.store.get_run(run_id)
        _TRACE_CONTEXT.set((run, "screen assembly"))
        session = await self.store.get_session(run.session_id)
        if not run.specification:
            raise AppError("SPEC_MISSING", "No specification", "Approve a specification before building.", 409)
        spec = UiSpec.model_validate(run.specification)
        # Repair legacy specs on the way into build: state notes stay present,
        # while first-paint content requirements contain only default copy.
        for screen in spec.screens:
            approved = _approved_source_for_screen(spec, screen.id)
            screen.must_show = [item for item in screen.must_show if not _is_conditional_copy(item, approved)]
            screen.sections = [item for item in screen.sections if not _is_conditional_copy(item, approved)]
            screen.data = [item for item in screen.data if not _is_conditional_copy(item, approved)]
        # Retry resumes from a persisted UiSpec and skips write_spec. Re-apply
        # the approved project contract here too, so legacy/stale specs cannot
        # bypass the Wiki palette when a user retries a failed Build stage.
        contract = _default_visual_contract(spec.visual_contract, {})
        if contract.get("palette"):
            spec.design.palette = Palette.model_validate(contract["palette"])
            spec.design.heading_font = contract.get("heading_font") or spec.design.heading_font
            spec.design.body_font = contract.get("body_font") or spec.design.body_font
            spec.design.radius = contract.get("radius") or spec.design.radius
            spec.design.density = contract.get("density") or spec.design.density
            spec.design.mood = contract.get("direction") or contract.get("tagline") or spec.design.mood
            theme = direction_theme(spec.design.model_dump())
            await self.store.set_session_visual_theme(session.id, theme)
            session.visual_theme = theme
        run = await self._stage(run, RunStage.ASSEMBLY, specification=spec)
        design = await design_context(self.store, session)
        targets = [s for s in (screen_ids or [s.id for s in spec.screens]) if spec.screen(s)]
        inherited_style_key = str(spec.selected_style_key or contract.get("key") or "")
        target_platforms = {spec.screen(sid).platform for sid in targets if spec.screen(sid)}
        style_references = {
            platform: await self._project_wiki_style_reference(
                session, style_key=inherited_style_key, platform=platform,
            )
            for platform in target_platforms
        }
        for platform, reference in style_references.items():
            await self._trace_decision(run, "project_wiki.visual_system", "approved style contract matches current project Wiki",
                "apply_saved_visual_system" if reference and reference.get("visual_system_source") == "saved"
                else "apply_approved_source_fallback" if reference else "no_project_wiki_reference", {
                    "platform": platform,
                    "wiki_version": reference.get("wiki_version") if reference else None,
                    "source_feature_title": reference.get("feature_title") if reference else None,
                    "source_screen_name": reference.get("screen_name") if reference else None,
                    "visual_system_source": reference.get("visual_system_source") if reference else None,
                })
        await self._trace_decision(run, "build.targets", "filter requested ids against current specification", "build_selected_screens", {
            "requested_screen_ids": screen_ids, "selected_targets": targets,
            "all_spec_screens": [screen.id for screen in spec.screens],
            "instruction_present": bool(instruction),
        })
        existing = {s.id: s for s in await self.store.list_screens(session.id, with_html=True)}
        summaries: dict[str, str] = {}
        built_screen_ids: set[str] = set()

        async def one(index: int, screen_id: str) -> None:
            screen = spec.screen(screen_id)
            await self._emit(run, "screen.started", {"screen_id": screen_id, "name": screen.name,
                                                     "index": index, "total": len(targets)})
            current = existing.get(screen_id)
            summaries[screen_id] = await self._build_screen(
                run, session, spec, screen, design, current.html if current and instruction else "", instruction,
                style_reference=style_references.get(screen.platform))
            built_screen_ids.add(screen_id)

        limit = asyncio.Semaphore(max(1, self.service.settings.designer_parallel_screens))
        await self._trace_decision(run, "build.concurrency", "designer_parallel_screens setting", "build_screens_with_semaphore", {
            "target_count": len(targets), "parallel_limit": max(1, self.service.settings.designer_parallel_screens),
        })

        async def limited(index: int, screen_id: str) -> None:
            async with limit:
                await one(index, screen_id)

        await asyncio.gather(*(limited(i, sid) for i, sid in enumerate(targets)))
        if built_screen_ids != set(targets):
            missing = sorted(set(targets) - built_screen_ids)
            raise AppError("SCREEN_BUILD_INCOMPLETE", "Not all selected screens were rebuilt",
                           "The build did not finish every target screen: " + ", ".join(missing), 502, True,
                           action="Retry the screen build.")
        latest_screens = {item.id: item for item in await self.store.list_screens(session.id)}
        built_versions = {sid: latest_screens[sid].version for sid in targets if sid in latest_screens}
        await self._emit(run, "design.build.completed", {
            "screen_ids": targets,
            "versions": built_versions,
            "style_key": spec.selected_style_key,
            "style_name": spec.style_name,
        })
        message = "\n".join(f"**{spec.screen(sid).name}**: {summaries.get(sid, '')}" for sid in targets)
        if review:
            await self._trace_decision(run, "build.review_gate", "review == true", "wait_for_final_human_review", {"screen_ids": targets})
            run = await self._stage(run, RunStage.REVIEW_FINAL, RunStatus.WAITING_REVIEW, assistant_message=message)
            await self._emit(run, "review.required", {"checkpoint": "final", "screens": targets})
        else:
            await self._trace_decision(run, "build.review_gate", "review == false", "finish_run", {"screen_ids": targets})
            await self._stage(run, RunStage.FINISHED, RunStatus.COMPLETED, assistant_message=message)

    def _direction_text(self, spec: UiSpec, approved_screen: dict[str, Any] | None = None,
                        visual_contract: dict[str, Any] | None = None) -> str:
        d = spec.design
        approved_screen = approved_screen or {}
        mood = d.mood or spec.style_direction or "Derive it from the product."
        lines = ["" if _is_conditional_copy(mood, approved_screen) else mood]
        if d.palette:
            lines.append("Palette: " + ", ".join(f"{k}={v}" for k, v in d.palette.model_dump().items()))
            lines.append("These semantic palette tokens are the approved project-wide source of truth. Use them consistently on this screen; do not replace them with another palette.")
        lines.append(f"Fonts: headings {d.heading_font}, body {d.body_font}; radius {d.radius}; density {d.density}.")
        if d.do:
            lines.append("Do: " + "; ".join(d.do))
        if d.dont:
            lines.append("Don't: " + "; ".join(d.dont))
        contract = visual_contract if visual_contract is not None else _default_visual_contract(spec.visual_contract, approved_screen)
        for key, label in (("composition", "Composition"), ("surface_language", "Surfaces"),
                           ("shape_language", "Shape language"), ("hero_placement", "Illustration placement"),
                           ("background_treatment", "Screen background treatment")):
            if contract.get(key):
                lines.append(f"{label}: {contract[key]}")
        return "\n".join(lines)

    async def _check(self, html: str, screen: ScreenSpec, width: int, design: DesignContext,
                     project_id: str | None, approved_screen: dict[str, Any] | None = None):
        approved_screen = approved_screen or {}
        default_fields = _default_screen_fields(screen, approved_screen)
        rendered_html = await inline_assets(html, project_id)
        context = _TRACE_CONTEXT.get()
        run, phase = context if context else (None, "screen inspection")
        trace_payload = {"phase": phase, "function": "renderer.inspect", "input": _trace_value({
            "screen": screen.model_dump(mode="json"), "width": width, "html": html,
            "default_content_contract": default_fields, "design_system": design.name,
        })}
        if run is not None:
            await self._emit(run, "debug.step.started", trace_payload)
        try:
            result = await self.service.renderer.render(
                rendered_html, width=width, theme_css=design.theme_css, fonts=design.fonts,
                screenshot="jpeg", forbidden_texts=_conditional_copy_forbidden(approved_screen), max_height=2400)
        except Exception as error:
            if run is not None:
                await self._emit(run, "debug.step.failed", {
                    **trace_payload, "error": {"type": type(error).__name__, "message": str(error)[:4000]},
                })
            raise
        text = _norm(result.text)
        missing = [m for m in default_fields.get("must_show", []) if _norm(m) not in text]
        lines = []
        for finding in sorted(result.findings, key=lambda f: {"error": 0, "warning": 1, "info": 2}[f["severity"]])[:MAX_FINDINGS]:
            if finding.get("rule") == "conditional-content-on-first-paint":
                lines.append(
                    "- ERROR conditional-state-leak: Conditional-only content appears in the default view. "
                    "Remove the conditional element; keep only approved default-screen content."
                )
                continue
            examples = finding.get("elements", [])
            if finding.get("rule") in {"interactive-overlap", "sticky-footer-overlap"} and examples:
                details = "; ".join(
                    f"{node.get('html', '')[:180]} at {node.get('bounds', {})}"
                    for node in examples[:3]
                )
                lines.append(f"- {finding['severity'].upper()} {finding['rule']}: {finding['message']} Overlapping rendered elements: {details}")
            else:
                lines.append(f"- {finding['severity'].upper()} {finding['rule']}: {finding['message']}"
                             + (f" e.g. {examples[0]['html'][:140]}" if examples else ""))
        if missing:
            lines.insert(0, "- ERROR missing-text: required texts not on the screen: " + "; ".join(missing))
        blocking = bool(missing) or bool(result.blocking)
        score = (len(missing), len(result.blocking))  # edits must not worsen default-screen QA
        if run is not None:
            await self._trace_decision(run, "renderer.quality_gate", "required text present and no blocking browser findings", "pass" if not blocking else "block_or_repair", {
                "missing_required_text": missing, "blocking_findings": result.blocking,
                "finding_count": len(result.findings), "score": score,
            })
            await self._emit(run, "debug.step.completed", {
                **trace_payload,
                "output": _trace_value({"visible_text": result.text, "findings": result.findings,
                                        "missing_required_text": missing, "blocking": blocking, "score": score}),
            })
        return result, "\n".join(lines) or "- none", blocking, score

    async def _build_screen(self, run, session, spec: UiSpec, screen: ScreenSpec, design: DesignContext,
                            current_html: str, instruction: str,
                            *, style_reference: dict[str, Any] | None = None) -> str:
        _TRACE_CONTEXT.set((run, f"screen: {screen.name} ({screen.id})"))
        approved_screen = _approved_source_for_screen(spec, screen.id)
        default_approved_screen = _default_approved_brief(approved_screen)
        default_screen = _default_screen_fields(screen, approved_screen)
        visual_contract = _default_visual_contract(spec.visual_contract, approved_screen)
        settings = self.service.settings
        width = SCREEN_WIDTHS.get(screen.platform, 1440)
        query = " ".join([
            screen.name, default_screen.get("purpose", ""),
            *default_screen.get("sections", []), *default_screen.get("actions", []),
        ])[:600]
        selected_asset_id = str(visual_contract.get("preview_asset_id") or "")
        selected_asset = asset_by_id(selected_asset_id) if selected_asset_id and screen.visual_asset_role != "none" else None
        selected_asset_data = inline_svg(selected_asset) if selected_asset else ""
        style_asset = (
            f"Selected local illustration: {selected_asset['title']} ({selected_asset['style']}). "
            f"Role for this screen: {screen.visual_asset_role}. Use it only as a separate decorative "
            "hero/background scene, at scene scale, behind/under the UI with a legibility surface. "
            "Keep the product wordmark separate. Embed it using src=\"__DESIGNER_SELECTED_STYLE_ASSET__\"."
            if selected_asset and selected_asset_data else
            f"Selected local illustration role for this screen: {screen.visual_asset_role}. "
            "Do not introduce an unrelated stock photo or substitute an illustration for the logo."
        )
        reference_instruction = self._style_reference_instruction(style_reference)
        review_reference_instruction = (
            reference_instruction +
            "\nFor this review, the first attached image is the current feature screen and the second is the approved Project Wiki source screen."
            if style_reference else ""
        )
        system = self.prompts.render(
            "builder", width=width,
            direction=self._direction_text(spec, approved_screen, visual_contract) + reference_instruction,
            design_system=await self._design_text(design, query), skill=knowledge.frontend_design_skill(),
            design_rules=knowledge.ui_ux_design_rules(), style_asset=style_asset,
        )
        usage: dict[str, int] = {}
        model = settings.effective_designer_model
        timeout = settings.designer_page_timeout_seconds

        async def save(html: str, note: str) -> str:
            html = _apply_project_wiki_backdrop(html, style_reference)
            if selected_asset_data:
                html = html.replace("__DESIGNER_SELECTED_STYLE_ASSET__", selected_asset_data)
            # Keep drafts local until deterministic and scope/style review pass.
            # A blocked build must never leave an unapproved screen on the canvas.
            return html

        if current_html:
            await self._trace_decision(run, "screen.source", "current_html exists", "edit_existing_screen", {
                "screen_id": screen.id, "instruction": instruction,
            })
            html, task = current_html, f"Apply this change requested by the user to the screen: {instruction}"
        else:
            await self._trace_decision(run, "screen.source", "current_html is empty", "generate_screen_draft", {
                "screen_id": screen.id, "platform": screen.platform, "width": width,
                "approved_default_fields": default_screen,
            })
            ux = knowledge.ux_rules(query, screen.platform)
            ux += knowledge.tailwind_rules(query, 3)
            if re.search(r"chart|graph|trend|biểu đồ|thống kê|report", f"{query} {' '.join(default_screen.get('sections', []))}", re.I):
                ux += knowledge.chart_rules(query)
                await self._trace_decision(run, "screen.chart_guidance", "query or approved sections mention charts or reports", "include_chart_rules", {"query": query})
            others = ", ".join(f"{s.id} ({s.name})" for s in spec.screens if s.id != screen.id) or "(none)"
            draft = await self._llm_call("generate", trace_name="screen.draft_html",
                system=system,
                text=self.prompts.render(
                    "draft", screen_id=screen.id,
                    screen=json.dumps(default_screen, ensure_ascii=False),
                    must_show="; ".join(default_screen.get("must_show", [])) or "(none)",
                    style_asset=style_asset, others=others,
                    ux="\n".join(f"- {u}" for u in ux),
                    reference=(
                        "Use the attached Project Wiki screen as a visual style reference only; "
                        "preserve this screen's BA-approved content and structure."
                        if style_reference else ""
                    ),
                ),
                images=[style_reference["image"]] if style_reference else None,
                usage=usage, model=model, timeout_seconds=timeout,
            )
            html = await save(extract_html(draft), "draft")
            task = "Review this screen you built and finish it."
        result, checks, blocking, score = await self._check(
            html, screen, width, design, session.project_id, approved_screen)
        for round_no in range(max(1, settings.designer_review_rounds)):
            if round_no and not blocking:
                await self._trace_decision(run, "screen.review_loop", "round_no > 0 and blocking == false", "stop_review_rounds", {
                    "round": round_no, "score": score,
                })
                break  # one visual review always happens; later rounds only to clear errors
            await self._trace_decision(run, "screen.review_loop", "first round or blocking findings remain", "request_model_review", {
                "round": round_no + 1, "max_rounds": max(1, settings.designer_review_rounds),
                "blocking": blocking, "score": score,
            })
            reply = await self._llm_call("generate", trace_name="screen.review_and_edit",
                system=system,
                text=self.prompts.render("review", task=task, width=width, checks=checks,
                                         checklist=knowledge.ui_ux_design_rules() + "\n\n" + knowledge.review_checklist(),
                                         html=html,
                                         approved_screen=json.dumps(default_approved_screen, ensure_ascii=False),
                                         screen_spec=json.dumps(default_screen, ensure_ascii=False),
                                         visual_contract=json.dumps(visual_contract, ensure_ascii=False) +
                                         review_reference_instruction),
                images=([result.image] if result.image else []) +
                       ([style_reference["image"]] if style_reference else []),
                usage=usage, model=model, timeout_seconds=timeout,
            )
            edit = apply_reply(html, reply)
            notes = [f"These edit blocks did not apply: {'; '.join(edit.failed)}."] if edit.failed else []
            if edit.applied:
                await self._trace_decision(run, "screen.edit_parse", "apply_reply returned edit.applied", "render_and_compare_candidate", {
                    "round": round_no + 1, "rewrite": edit.rewrite, "failed_operations": edit.failed,
                })
                # Keep an edit only if it does not make the screen worse (lost texts, new blocking errors).
                candidate_html = await save(edit.html, f"review candidate {round_no + 1}")
                candidate = await self._check(candidate_html, screen, width, design, session.project_id, approved_screen)
                if candidate[3] <= score:
                    await self._trace_decision(run, "screen.edit_quality_gate", "candidate score <= accepted score", "accept_candidate", {
                        "previous_score": score, "candidate_score": candidate[3],
                    })
                    html = await save(edit.html, "rework" if edit.rewrite else f"review {round_no + 1}")
                    result, checks, blocking, score = candidate
                else:
                    await self._trace_decision(run, "screen.edit_quality_gate", "candidate score > accepted score", "reject_candidate_keep_previous", {
                        "previous_score": score, "candidate_score": candidate[3],
                    })
                    logger.info("Screen %s: rejected an edit that worsened the checks %s -> %s",
                                screen.id, score, candidate[3])
                    notes.append("Your last change was rejected because it made the checks worse:\n" + candidate[1])
            elif edit.ok and not blocking:
                await self._trace_decision(run, "screen.edit_parse", "edit.ok and no blocking findings", "stop_review_loop", {
                    "round": round_no + 1, "failed_operations": edit.failed,
                })
                break
            else:
                await self._trace_decision(run, "screen.edit_parse", "no applicable edit or checks remain blocking", "continue_review_loop", {
                    "round": round_no + 1, "ok": edit.ok, "applied": edit.applied,
                    "failed_operations": edit.failed, "blocking": blocking,
                })
            task = " ".join(notes + ["Continue: fix what the checks report."])

        async def audit_scope() -> _ScreenAudit:
            audit_usage: dict[str, int] = {}
            report = await self._llm_call("structured", trace_name="screen.scope_audit",
                prompt=self.prompts.render(
                    "screen-scope-review",
                    approved_screen=json.dumps(default_approved_screen, ensure_ascii=False),
                    screen_spec=json.dumps(default_screen, ensure_ascii=False),
                    visual_contract=json.dumps(visual_contract, ensure_ascii=False),
                    visible_text=result.text,
                    html=_clip(html, 18_000),
                    style_reference_instruction=review_reference_instruction,
                ),
                schema=_ScreenAudit,
                image_bytes=result.image,
                images=[style_reference["image"]] if style_reference else None,
                model=model,
                usage=audit_usage,
                max_attempts=2,
            )
            await self._usage(run, audit_usage, f"scope-review:{screen.id}")
            return report

        audit = await audit_scope()
        await self._trace_decision(run, "screen.scope_audit", "audit.ready", "pass" if audit.ready else "repair_scope_issues", {
            "ready": audit.ready, "issues": audit.issues,
        })
        # Scope review issues (for example, BA-dependent visibility or a footer
        # covering the form) need a targeted repair loop. Re-audit only the
        # candidate that was actually accepted and rendered; never report an
        # old screenshot as if it were the repaired screen.
        for repair_round in range(1, 4):
            if audit.ready:
                await self._trace_decision(run, "screen.scope_repair_loop", "audit.ready", "stop_repairs", {
                    "repair_round": repair_round, "issues": audit.issues,
                })
                break
            await self._trace_decision(run, "screen.scope_repair_loop", "audit.ready == false", "request_targeted_repair", {
                "repair_round": repair_round, "max_rounds": 3, "issues": audit.issues,
            })
            audit_notes = "\n".join(f"- {issue}" for issue in audit.issues)
            repair = await self._llm_call("generate", trace_name="screen.scope_repair",
                system=system,
                text=self.prompts.render(
                    "review",
                    task=(
                        f"Targeted scope/style repair, attempt {repair_round} of 3. "
                        "Fix every listed issue in the visible rendered result, including its actual default-state "
                        "behavior and geometry. Delete unsupported factual claims. Remove any control absent from "
                        "the approved default action list; error-recovery actions must not be visible or active "
                        "in the initial loading view. For a duplicated input placeholder, preserve the approved "
                        "native placeholder and remove only the overlay text element that repeats it; do not remove "
                        "the placeholder to satisfy the visual repair. Preserve approved default content and do not "
                        "add anything outside the approved screen specification.\n" + audit_notes
                    ),
                    width=width,
                    checks="- Scope/style audit failed:\n" + audit_notes,
                    checklist=knowledge.ui_ux_design_rules() + "\n\n" + knowledge.review_checklist(),
                    html=html,
                    approved_screen=json.dumps(default_approved_screen, ensure_ascii=False),
                    screen_spec=json.dumps(default_screen, ensure_ascii=False),
                    visual_contract=json.dumps(visual_contract, ensure_ascii=False) +
                    review_reference_instruction,
                    style_reference_instruction=review_reference_instruction,
                ),
                images=([result.image] if result.image else []) +
                       ([style_reference["image"]] if style_reference else []),
                usage=usage,
                model=model,
                timeout_seconds=timeout,
            )
            edit = apply_reply(html, repair)
            if not edit.applied:
                await self._trace_decision(run, "screen.scope_repair_parse", "edit.applied == false", "discard_repair_continue", {
                    "repair_round": repair_round, "failed_operations": edit.failed,
                })
                logger.warning(
                    "Scope/style repair %s/%s produced no applicable edit for screen %s",
                    repair_round, 3, screen.id,
                )
                continue

            candidate_html = await save(edit.html, f"scope/style candidate {repair_round}/3")
            candidate = await self._check(candidate_html, screen, width, design, session.project_id, approved_screen)
            if candidate[3] > score:
                await self._trace_decision(run, "screen.scope_repair_quality_gate", "candidate score > accepted score", "reject_repair_keep_previous", {
                    "repair_round": repair_round, "previous_score": score, "candidate_score": candidate[3],
                })
                logger.warning(
                    "Scope/style repair %s/%s increased blocking lint score for screen %s; keeping last accepted render",
                    repair_round, 3, screen.id,
                )
                continue

            html = await save(edit.html, f"scope/style repair {repair_round}/3")
            result, checks, blocking, score = candidate
            audit = await audit_scope()
            await self._trace_decision(run, "screen.scope_audit", "audit.ready after repair", "pass" if audit.ready else "continue_repairing", {
                "repair_round": repair_round, "ready": audit.ready, "issues": audit.issues,
            })

        if blocking:
            await self._trace_decision(run, "screen.final_quality_gate", "blocking findings remain", "fail_screen_build", {
                "checks": checks, "score": score,
            })
            raise AppError(
                "SCREEN_RENDER_QA_FAILED",
                "Screen did not pass deterministic browser checks",
                f"{screen.name}: {checks}",
                422,
                True,
                action="Retry the screen build after resolving the listed browser-check failures.",
            )
        if not audit.ready:
            await self._trace_decision(run, "screen.final_scope_gate", "scope audit did not pass", "fail_screen_build", {
                "issues": audit.issues,
            })
            details = "; ".join(audit.issues) or "The screen did not pass source-scope/style review."
            raise AppError(
                "SCREEN_SCOPE_REVIEW_FAILED",
                "Screen did not pass the approved scope and style review",
                f"{screen.name}: {details}",
                422,
                True,
                action="Retry the screen build after resolving the reported issues.",
            )
        saved = await self.store.save_screen(
            session.id, screen.id,
            ScreenWrite(name=screen.name, platform=screen.platform, html=html, note="Approved build"),
            run_id=run.id, updated_by="designer-agent",
        )
        await self._emit(run, "screen.saved", {"screen_id": screen.id, "version": saved.version, "note": "approved build"})
        await self._usage(run, usage, f"build:{screen.id}")
        await self._emit(run, "screen.finished", {"screen_id": screen.id})
        return screen.purpose or "Đã dựng xong."

    # ── review feedback on the final design ──
    async def targets_for(self, run: RunView, feedback: str) -> list[str]:
        spec = UiSpec.model_validate(run.specification)
        named = [s.id for s in spec.screens if s.id in feedback or s.name.lower() in feedback.lower()]
        return named or [s.id for s in spec.screens]


# Wrap each async Designer method once so entry arguments, return values,
# durations, and exceptions are visible alongside model/render traces.
for _method_name, _method in tuple(vars(HtmlDesigner).items()):
    if inspect.iscoroutinefunction(_method) and _method_name not in {"_emit", "_llm_call", "_trace_decision"}:
        setattr(HtmlDesigner, _method_name, _trace_async_method(_method))
