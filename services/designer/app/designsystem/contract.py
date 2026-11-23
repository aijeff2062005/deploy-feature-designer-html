"""Portable project design-system contract shared by Figma and Q&A sources."""

from __future__ import annotations

from typing import Any


def qna_contract(contract: dict[str, Any], sample: dict[str, Any], version: int) -> dict[str, Any]:
    palette = contract.get("palette") if isinstance(contract.get("palette"), dict) else {}
    tokens = [
        {"name": name.replace("_", "-"), "category": "color", "value": value,
         "semantic_roles": [name.replace("_", "-")],
         "provenance": [{"kind": "qna_answer", "version": version}]}
        for name, value in sorted(palette.items()) if isinstance(value, str) and value.strip()
    ]
    for role, value in (("font-heading", contract.get("heading_font")),
                        ("font-body", contract.get("body_font"))):
        if value:
            tokens.append({"name": role, "category": "font", "value": value,
                           "semantic_roles": [role],
                           "provenance": [{"kind": "qna_answer", "version": version}]})
    for role, category in (("radius", "radius"), ("density", "density")):
        value = contract.get(role)
        if value:
            tokens.append({"name": role, "category": category, "value": value,
                           "semantic_roles": [role],
                           "provenance": [{"kind": "qna_answer", "version": version}]})
    return {
        "schema_version": 1,
        "source": {"type": "qna", "version": version},
        "tokens": tokens,
        # A selected screen template is not mislabeled as a reusable component library.
        "components": [],
        "screens": [{"id": sample.get("screen_id"), "name": sample.get("screen_name"),
                     "platform": sample.get("platform"), "template": sample}],
        "unresolved": [
            {"kind": "token_category", "category": "spacing", "question": "Chưa thu thập quy tắc spacing cụ thể."},
            {"kind": "token_category", "category": "shadow", "question": "Chưa thu thập quy tắc shadow cụ thể."},
            {"kind": "token_category", "category": "breakpoint", "question": "Chưa chốt breakpoint responsive cụ thể."},
            {"kind": "component_catalog", "question": "Cần chuẩn hóa HTML component và các trạng thái default/hover/focus/disabled/loading/empty/error."},
        ],
    }
