from __future__ import annotations

from jev_sim.candidates import build_candidates, choice_criteria, outline_text


def test_build_candidates_from_settings_fixture(ui_settings):
    cands = build_candidates(ui_settings)
    ids = [c.id for c in cands]
    assert "@9" in ids
    assert "@7" in ids
    assert "@5" in ids  # SearchField is tappable
    assert "done" in ids
    assert "scroll-up" in ids
    assert "scroll-down" in ids
    assert "wait" in ids
    # StaticText "Settings" should be skipped
    assert "@1" not in ids
    general = next(c for c in cands if c.id == "@9")
    assert general.kind == "tap"
    assert "General" in general.label
    assert general.selector == "@9"


def test_choice_criteria_keys_match_ids(ui_settings):
    cands = build_candidates(ui_settings)
    criteria = choice_criteria(cands)
    assert set(criteria) == {c.id for c in cands}
    assert "General" in criteria["@9"]


def test_outline_text_uses_envelope(ui_settings):
    text = outline_text(ui_settings)
    assert "Settings" in text
    assert "@9" in text


def test_accepts_unwrapped_data(ui_settings):
    data = ui_settings["data"]
    cands = build_candidates(data)
    assert any(c.id == "@10" for c in cands)


def test_disabled_entry_skipped():
    ui = {
        "entries": [
            {
                "aliases": {"at": 2},
                "role": "Button",
                "label": "Disabled",
                "frame": {"x": 0, "y": 0, "width": 10, "height": 10},
                "region": {"kind": "Content"},
                "states": ["disabled"],
            },
            {
                "aliases": {"at": 3},
                "role": "Button",
                "label": "Ok",
                "frame": {"x": 0, "y": 20, "width": 10, "height": 10},
                "region": {"kind": "Content"},
                "states": [],
            },
        ]
    }
    ids = [c.id for c in build_candidates(ui)]
    assert "@2" not in ids
    assert "@3" in ids
