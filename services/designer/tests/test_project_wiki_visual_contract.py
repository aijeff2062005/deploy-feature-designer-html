from app.workflow.designer import (
    _apply_project_wiki_backdrop,
    _merge_project_wiki_profiles,
    _project_wiki_style_css,
)


def test_visual_profile_aggregates_values_and_marks_conflicts_without_winner():
    profile = _merge_project_wiki_profiles([
        {
            "screen_id": "login",
            "profile": {
                "tokens": {"--color-primary": "#123456"},
                "roles": {"action": {"primary": [{"color": "white"}]}},
            },
        },
        {
            "screen_id": "register",
            "profile": {
                "tokens": {"--color-primary": "#123456"},
                "roles": {"action": {"primary": [{"color": "black"}]}},
            },
        },
    ])

    assert profile["tokens"]["--color-primary"]["value"] == "#123456"
    assert profile["tokens"]["--color-primary"]["sources"] == ["login", "register"]
    color = profile["roles"]["action"]["primary"]["color"]
    assert color["conflict"] is True
    assert color["value"] is None
    assert {item["value"] for item in color["values"]} == {"black", "white"}


def test_wiki_css_is_limited_to_explicit_role_variant_and_conflict_free_properties():
    css = _project_wiki_style_css({
        "roles": {
            "action": {
                "primary": {
                    "backgroundColor": {"value": "rgb(1, 2, 3)", "conflict": False},
                    "color": {"value": None, "conflict": True},
                },
            },
        },
    })

    assert css == '[data-design-role="action"][data-design-variant="primary"] { background-color: rgb(1, 2, 3); }'
    assert "body" not in css
    assert "!important" not in css


def test_wiki_css_rejects_unsafe_role_names_and_values():
    css = _project_wiki_style_css({
        "roles": {
            'action"] body': {"primary": {"color": {"value": "red", "conflict": False}}},
            "field": {"default": {"color": {"value": "red; body { display:none", "conflict": False}}},
        },
    })

    assert css == ""


def test_legacy_global_wiki_css_is_removed_and_only_semantic_contract_is_applied():
    markup = '<main><button data-design-role="action" data-design-variant="primary">Continue</button></main>'
    reference = {
        "style_lock_css": '[data-design-role="action"][data-design-variant="primary"] { color: #123456; }',
    }

    result = _apply_project_wiki_backdrop(
        markup + '<style id="project-wiki-style-lock">body { color: red !important; }</style>',
        reference,
    )

    assert "body { color: red" not in result
    assert "!important" not in result
    assert '[data-design-role="action"][data-design-variant="primary"] { color: #123456; }' in result
