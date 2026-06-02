"""Tests for the data model: construction, validation, helpers."""

from waypoint import model


def test_now_iso_has_offset():
    ts = model.now_iso()
    assert "T" in ts
    # tz-aware ISO ends with an offset (+/-HH:MM) or 'Z'.
    assert ts[-6] in "+-" or ts.endswith("Z")


def test_new_task_shape():
    t = model.new_task("2026-05-30-x", "do a thing", scope=["src/"], auto=True)
    assert t["task_id"] == "2026-05-30-x"
    assert t["status"] == model.IN_PROGRESS
    assert t["steps"] == []
    assert t["current_step"] is None
    assert t["auto"] is True
    assert model.validate(t) == []


def test_validate_catches_problems():
    assert any("missing" in e for e in model.validate({}))
    bad = model.new_task("i", "g")
    bad["status"] = "weird"
    assert any("invalid status" in e for e in model.validate(bad))


def test_validate_committed_step_must_be_succeeded():
    t = model.new_task("i", "g")
    t["steps"].append({"id": "a", "status": "in_progress"})
    assert any("not 'succeeded'" in e for e in model.validate(t))


def test_validate_current_step_must_be_in_progress():
    t = model.new_task("i", "g")
    t["current_step"] = {"id": "a", "status": "succeeded"}
    assert any("current_step.status" in e for e in model.validate(t))


def test_validate_committed_human_gate_needs_human_response():
    # A committed human-gate step must carry the human's recorded response —
    # otherwise it was deemed done without the human ever answering.
    t = model.new_task("i", "g")
    t["steps"].append({"id": "a", "status": model.STEP_SUCCEEDED,
                       "awaits_human": True,
                       "actual_result": {"summary": "presented"}})
    assert any("human response" in e for e in model.validate(t))
    # With the response recorded, it validates.
    t["steps"][0]["actual_result"]["human_response"] = "go ahead"
    assert model.validate(t) == []


def test_last_succeeded():
    t = model.new_task("i", "g")
    assert model.last_succeeded(t) is None
    t["steps"] = [{"id": "a", "status": "succeeded"},
                  {"id": "b", "status": "succeeded"}]
    assert model.last_succeeded(t)["id"] == "b"


def test_new_task_has_empty_plan_and_no_pending():
    t = model.new_task("t1", "g")
    assert t["plan"] == []
    assert "pending" not in t


def test_migrate_legacy_pending_reconstructs_full_roadmap():
    # A legacy task: committed a, b; between steps; one planned 'c'.
    legacy = {
        "task_id": "t1", "goal": "g", "status": "in_progress",
        "created_at": "2026-01-01T00:00:00+00:00", "steps": [
            {"id": "a", "purpose": "first", "status": "succeeded"},
            {"id": "b", "purpose": "second", "status": "succeeded"},
        ],
        "current_step": None,
        "pending": [{"id": "c", "purpose": "third"}],
    }
    model.migrate(legacy)
    assert [p["id"] for p in legacy["plan"]] == ["a", "b", "c"]
    assert legacy["plan"][2]["purpose"] == "third"
    assert "pending" not in legacy


def test_migrate_legacy_without_pending_means_no_plan():
    legacy = {
        "task_id": "t1", "goal": "g", "status": "in_progress",
        "created_at": "2026-01-01T00:00:00+00:00",
        "steps": [{"id": "a", "purpose": "first", "status": "succeeded"}],
        "current_step": None, "pending": [],
    }
    model.migrate(legacy)
    assert legacy["plan"] == []


def test_migrate_empty_pending_is_no_plan_but_keeps_current_step():
    # Empty pending => no roadmap was declared => plan stays []. An active
    # current_step must NOT be promoted into a plan (that would read as
    # "step N of M" with no real finish line), but must be preserved intact.
    legacy = {
        "task_id": "t1", "goal": "g", "status": "in_progress",
        "created_at": "2026-01-01T00:00:00+00:00",
        "steps": [{"id": "a", "purpose": "first", "status": "succeeded"}],
        "current_step": {"id": "b", "purpose": "second", "status": "in_progress"},
        "pending": [],
    }
    model.migrate(legacy)
    assert legacy["plan"] == []                       # no plan declared
    assert legacy["current_step"]["id"] == "b"        # current step preserved
    assert "pending" not in legacy


def test_migrate_is_idempotent():
    t = model.new_task("t1", "g")
    t["plan"] = [{"id": "a", "purpose": "p"}]
    model.migrate(t)
    assert t["plan"] == [{"id": "a", "purpose": "p"}]


def test_new_task_has_empty_grants():
    t = model.new_task("t1", "g")
    assert t["grants"] == {}


def test_set_and_has_grant():
    t = model.new_task("t1", "g")
    assert model.has_grant(t, model.GRANT_PUSH) is False
    model.set_grant(t, model.GRANT_PUSH)
    assert model.has_grant(t, model.GRANT_PUSH) is True
    model.set_grant(t, model.GRANT_PUSH, False)
    assert model.has_grant(t, model.GRANT_PUSH) is False


def test_migrate_adds_grants_to_legacy():
    legacy = {"task_id": "t", "goal": "g", "status": "in_progress",
              "created_at": "2026-01-01T00:00:00+00:00", "steps": [],
              "current_step": None, "plan": []}
    model.migrate(legacy)
    assert legacy["grants"] == {}


def test_validate_rejects_non_dict_grants():
    t = model.new_task("t1", "g")
    t["grants"] = ["push"]
    assert any("grants" in e for e in model.validate(t))


def test_new_task_review_defaults():
    t = model.new_task("t1", "g")
    assert t["review"] == "auto"
    assert t["reviewer"] == ""
    assert t["max_retries"] == 2


def test_new_task_review_overrides():
    t = model.new_task("t1", "g", review="manual", reviewer="gemini",
                       max_retries=3)
    assert t["review"] == "manual" and t["reviewer"] == "gemini"
    assert t["max_retries"] == 3


def test_migrate_adds_review_defaults():
    legacy = {"task_id": "t", "goal": "g", "status": "in_progress",
              "created_at": "2026-01-01T00:00:00+00:00", "steps": [],
              "current_step": None, "plan": []}
    model.migrate(legacy)
    assert legacy["review"] == "auto"
    assert legacy["reviewer"] == "" and legacy["max_retries"] == 2


def test_validate_rejects_bad_review_mode():
    t = model.new_task("t1", "g")
    t["review"] = "sometimes"
    assert any("review" in e for e in model.validate(t))
