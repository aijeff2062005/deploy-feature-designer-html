"""Import a design system from a Figma .fig: tokens → theme CSS, components → HTML snippets.

Components are published component sets (variants) and standalone components on regular
pages; names starting with ``.`` or ``_`` are private by Figma convention and skipped,
as is the internal canvas (library copies used only to resolve instances).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..figio import container
from ..render.document import DEFAULT_THEME
from .convert import Converter
from .scene import Node, Scene, slug
from .tokens import ICON_FONT, Tokens, extract_tokens, theme_css

GOOGLE_FONTS = "https://fonts.googleapis.com/css2?family={}:wght@300;400;500;600;700&display=swap"


@dataclass
class Variant:
    id: str
    component_id: str
    props: dict[str, str]
    width: float
    height: float
    html: str


@dataclass
class ImportedDesignSystem:
    name: str
    theme_css: str
    fonts: list[str]
    tokens: Tokens
    components: list[dict[str, Any]] = field(default_factory=list)
    variants: list[Variant] = field(default_factory=list)
    screens: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    template_candidates: list[dict[str, Any]] = field(default_factory=list)

    def index(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source": {"type": "figma_local_file", "name": self.name},
            "tokens": self.tokens.summary(),
            "design_system": {"schema_version": 1, "source": "figma", "tokens": self.tokens.records(),
                              "components": self.components, "screens": self.screens,
                              "unresolved": self.unresolved},
            "components": self.components,
        }


def _figma_id(node: Node) -> str:
    guid = node.get("guid") or {}
    return f"{guid.get('sessionID', '')}:{guid.get('localID', '')}"


def _screen_tree(scene: Scene, node: Node, depth: int = 0) -> dict[str, Any]:
    item = {"id": _figma_id(node), "name": node.get("name", "Untitled"), "type": node.get("type", "")}
    if depth < 4:
        children = [child for child in scene.children(node) if not child.get("isSoftDeleted")]
        if children:
            item["children"] = [_screen_tree(scene, child, depth + 1) for child in children[:80]]
    return item


def _unresolved_items(scene: Scene) -> list[dict[str, Any]]:
    """Flag only objectively unnamed styles and unstyled editable text; never invent values."""
    items = []
    for node in scene.nodes.values():
        if node.get("isSoftDeleted"):
            continue
        style_type = node.get("styleType")
        if style_type and not str(node.get("name") or "").strip():
            items.append({"kind": "unnamed_style", "node_id": _figma_id(node),
                          "question": f"Đặt tên/ý nghĩa cho style {style_type} này để map vào token ngữ nghĩa."})
        if node.get("type") == "TEXT" and str(node.get("characters") or "").strip() and not node.get("textStyleId"):
            page = scene.page(node)
            items.append({"kind": "unstyled_text", "node_id": _figma_id(node),
                          "node_name": node.get("name", "Text layer"), "page": (page or {}).get("name", ""),
                          "text_sample": str(node.get("characters", ""))[:120],
                          "question": "Xác nhận typography/ý nghĩa nội dung cho text layer chưa gắn text style."})
        if len(items) >= 200:
            break
    return items


def _props(name: str) -> dict[str, str]:
    props = {}
    for part in name.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            props[k.strip()] = v.strip()
    return props


def _private(name: str) -> bool:
    return name.strip().startswith((".", "_"))


def _description(node: Node) -> str:
    text = (node.get("symbolDescription") or node.get("description") or "").strip()
    return re.sub(r"\s+", " ", text)[:300]


def font_urls(families: list[str]) -> list[str]:
    """One Google Fonts stylesheet per text family; families Google lacks just fall back."""
    return [GOOGLE_FONTS.format(f.replace(" ", "+")) for f in families if not ICON_FONT.search(f)][:4]


def import_fig(data: bytes, name: str) -> ImportedDesignSystem:
    fig = container.read_fig(data)
    scene = Scene(fig.message)
    tokens = extract_tokens(scene)
    converter = Converter(scene, tokens)
    result = ImportedDesignSystem(
        name=name, theme_css=theme_css(tokens, DEFAULT_THEME), fonts=font_urls(tokens.fonts), tokens=tokens
    )

    def visible_page(node: Node) -> Node | None:
        page = scene.page(node)
        return None if page is None or page.get("internalOnly") else page

    result.unresolved = _unresolved_items(scene)
    template_words = re.compile(
        r"\b(template|screen|home|dashboard|checkout|login|sign[ -]?up|landing|onboarding|search results|"
        r"trang chủ|màn hình|man[- ]?hinh|thanh toán)\b", re.I,
    )
    for node in scene.of_type("FRAME"):
        parent = scene.parent(node)
        page = visible_page(node)
        if node.get("isSoftDeleted") or node.get("isStateGroup") or not parent or parent.get("type") != "CANVAS" or not page:
            continue
        children = [child for child in scene.children(node) if not child.get("isSoftDeleted")]
        size = node.get("size") or {"x": 0, "y": 0}
        width, height = size.get("x", 0), size.get("y", 0)
        ratio = width / height if height else 0
        nested = [grandchild for child in children for grandchild in scene.children(child)
                  if not grandchild.get("isSoftDeleted")]
        content_nodes = children + nested
        text_count = sum(child.get("type") == "TEXT" for child in content_nodes)
        structure_count = sum(child.get("type") in {"FRAME", "SYMBOL", "INSTANCE"} for child in content_nodes)
        plausible_screen_size = 320 <= width <= 2560 and 480 <= height <= 2200 and 0.25 <= ratio <= 2.5
        named_screen = bool(template_words.search(str(node.get("name", ""))))
        substantial_ui = len(children) >= 8 and text_count >= 3 and structure_count >= 3
        score = min(100, len(children) * 8 + (24 if template_words.search(str(node.get("name", ""))) else 0)
                    + (16 if size.get("x", 0) >= 300 and size.get("y", 0) >= 400 else 0))
        result.template_candidates.append({
            "id": _figma_id(node), "name": node.get("name", "Màn hình chưa đặt tên"),
            "page": page.get("name", ""), "width": round(size.get("x", 0)),
            "height": round(height), "child_count": len(children),
            "score": score, "looks_like_template": plausible_screen_size and (named_screen or substantial_ui),
            "html": converter.component_html(node, node.get("name", "Figma screen"))[:60_000],
        })
    result.template_candidates.sort(key=lambda item: item["score"], reverse=True)
    screen_ids = {item["id"] for item in result.template_candidates if item["looks_like_template"]}
    result.screens = [
        _screen_tree(scene, node) for node in scene.of_type("FRAME")
        if _figma_id(node) in screen_ids
    ][:100]
    taken: set[str] = set()

    def add(entry_node: Node, variants: list[Node], set_props: bool) -> None:
        base = slug(entry_node.get("name", ""), "component")
        component_id, i = base, 2
        while component_id in taken:
            component_id, i = f"{base}-{i}", i + 1
        taken.add(component_id)
        props: dict[str, list[str]] = {}
        entries = []
        for v in variants:
            v_props = _props(v.get("name", "")) if set_props else {}
            for k, value in v_props.items():
                props.setdefault(k, [])
                if value not in props[k]:
                    props[k].append(value)
            variant_id = component_id if not v_props else f"{component_id}--{slug('-'.join(v_props.values()), 'v')}"
            while any(e.id == variant_id for e in entries):
                variant_id += "-x"
            size = v.get("size") or {"x": 0, "y": 0}
            entries.append(Variant(variant_id, component_id, v_props, size["x"], size["y"],
                                   converter.component_html(v, entry_node.get("name"))))
        if not entries:
            return
        page = visible_page(entry_node)
        result.components.append({
            "id": component_id,
            "name": entry_node.get("name", component_id),
            "page": (page or {}).get("name", ""),
            "description": _description(entry_node) or _description(variants[0]),
            "props": props,
            "variants": [{"id": e.id, "props": e.props, "width": round(e.width), "height": round(e.height)} for e in entries],
        })
        result.variants.extend(entries)

    for node in scene.of_type("FRAME", "COMPONENT_SET"):
        if node.get("isStateGroup") and not node.get("isSoftDeleted") and not _private(node.get("name", "")) and visible_page(node):
            add(node, [c for c in scene.children(node) if c.get("type") == "SYMBOL" and not c.get("isSoftDeleted")], True)
    for node in scene.of_type("SYMBOL"):
        parent = scene.parent(node)
        if (parent or {}).get("isStateGroup") or node.get("isSoftDeleted") or _private(node.get("name", "")):
            continue
        if visible_page(node):
            add(node, [node], False)
    result.components.sort(key=lambda c: (c["page"], c["name"].lower()))
    return result
