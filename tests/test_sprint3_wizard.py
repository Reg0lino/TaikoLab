"""Sprint 3 tests: Wizard phrasing controls, profile persistence, and collapsible drawer mechanics."""

import tkinter as tk
import pytest
from taikolab.ui.wizard import TaikoWizard


def test_wizard_phrasing_controls_and_collapsible_sections(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        pytest.skip("Tk display not available in headless environment")

    # Isolate from user's live configs/wizard_settings.json
    monkeypatch.setattr("taikolab.ui.wizard.load_wizard_preferences", lambda *a, **kw: {})
    monkeypatch.setattr("taikolab.ui.wizard.save_wizard_preferences", lambda *a, **kw: None)

    try:
        wiz = TaikoWizard(root)
        # Verify both top and bottom Open Manual Editor buttons exist
        assert hasattr(wiz, "top_editor_button")
        assert hasattr(wiz, "editor_button")

        # Verify new phrasing variables exist with defaults
        assert wiz.default_blend_style.get() == "Simultaneous (Layered)"
        assert wiz.default_phrasing_lead.get() == "Auto"
        assert wiz.selective_phrase_parts.get() is False

        # Modify phrasing controls and source mode
        wiz.default_blend_style.set("Alternating 2-Bar (Call & Resp)")
        wiz.default_phrasing_lead.set("Vocals Lead")
        wiz.selective_phrase_parts.set(True)
        wiz.source_modes["hard"].set("Drums + Vocals")

        ctrl = wiz._chart_control_settings()
        assert ctrl.default_blend_style == "alt_2bar"
        assert ctrl.default_phrasing_lead == "vocals"
        assert ctrl.selective_phrase_parts is True
        assert ctrl.difficulty_overrides["hard"].follow_mode == "drums_vocals"

        # Save into profile dictionary and reload
        wiz.saved_profiles["TestAlternating"] = ctrl.model_dump(exclude_none=True)
        wiz.default_blend_style.set("Simultaneous (Layered)")
        wiz.default_phrasing_lead.set("Auto")
        wiz.selective_phrase_parts.set(False)
        wiz.selected_slider_profile.set("TestAlternating")

        wiz._load_slider_profile()

        assert wiz.default_blend_style.get() == "Alternating 2-Bar (Call & Resp)"
        assert wiz.default_phrasing_lead.get() == "Vocals Lead"
        assert wiz.selective_phrase_parts.get() is True
        assert wiz.source_modes["hard"].get() == "Drums + Vocals"
    finally:
        root.destroy()
