"""End-to-end tests for the waypoint CLI lifecycle and invariants."""

import pytest

from waypoint import cli, fingerprint, model, store


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return str(tmp_path)


def run(root, *argv):
    return cli.main([*argv, "--root", root]) if "--root" not in argv \
        else cli.main(list(argv))


def test_full_lifecycle(root):
    assert cli.main(["start", "--goal", "build", "--id", "t1", "--root", root]) == 0
    # Declare a step, then commit it.
    assert cli.main(["set-step", "--step", "a", "--purpose", "first",
                     "--id", "t1", "--root", root]) == 0
    assert cli.main(["commit", "--summary", "did first", "--id", "t1",
                     "--root", root]) == 0
    t = store.load(root, "t1")
    assert len(t["steps"]) == 1
    assert t["steps"][0]["status"] == model.STEP_SUCCEEDED
    assert t["current_step"] is None


def test_one_uncommitted_step_invariant(root):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["set-step", "--step", "a", "--purpose", "p", "--id", "t1",
              "--root", root])
    # A second set-step before committing must be refused.
    assert cli.main(["set-step", "--step", "b", "--purpose", "p2", "--id",
                     "t1", "--root", root]) == 1


def test_commit_without_step_fails(root):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    assert cli.main(["commit", "--summary", "x", "--id", "t1",
                     "--root", root]) == 1


def test_commit_fingerprints_artifact_and_check_detects_change(root, tmp_path):
    art = tmp_path / "out.txt"
    art.write_text("result")
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["set-step", "--step", "a", "--purpose", "p", "--id", "t1",
              "--root", root])
    cli.main(["commit", "--summary", "made out", "--artifact", str(art),
              "--id", "t1", "--root", root])
    # Intact -> check passes (0).
    assert cli.main(["check", "--id", "t1", "--root", root]) == 0
    # Delete the artifact -> check fails (1), detecting the missing result.
    art.unlink()
    assert cli.main(["check", "--id", "t1", "--root", root]) == 1


def test_resume_reports_and_done_archives(root, capsys):
    cli.main(["start", "--goal", "ship it", "--id", "t1", "--root", root])
    cli.main(["set-step", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    cli.main(["commit", "--summary", "done first", "--id", "t1", "--root", root])
    assert cli.main(["resume", "--id", "t1", "--root", root]) == 0
    out = capsys.readouterr().out
    assert "Resuming task" in out and "first" in out
    assert cli.main(["done", "--id", "t1", "--root", root]) == 0
    assert store.active_tasks(root) == []


def test_resume_points_at_next_planned_step(root, capsys):
    # After committing a step, resume should name the next planned step
    # (sourced from the permanent plan, not the removed 'pending' key).
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    cli.main(["plan", "--step", "b", "--purpose", "second", "--id", "t1",
              "--root", root])
    cli.main(["set-step", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    cli.main(["commit", "--summary", "did a", "--id", "t1", "--root", root])
    capsys.readouterr()
    assert cli.main(["resume", "--id", "t1", "--root", root]) == 0
    out = capsys.readouterr().out
    assert "Next planned: b" in out


def test_status_with_no_task_errors_consistently(root, capsys):
    # No active task: status flows through _resolve for the canonical
    # message and exit 1 (consistent with other commands).
    assert cli.main(["status", "--root", root]) == 1
    assert "no active task in this folder" in capsys.readouterr().err


def test_version_flag(capsys):
    import pytest as _pytest
    with _pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert "waypoint" in capsys.readouterr().out


def test_infer_single_active_task(root):
    cli.main(["start", "--goal", "g", "--id", "only", "--root", root])
    # No --id: should infer the single active task.
    assert cli.main(["set-step", "--step", "a", "--purpose", "p",
                     "--root", root]) == 0


def test_plan_appends_and_rejects_duplicates(root):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    assert cli.main(["plan", "--step", "a", "--purpose", "first",
                     "--id", "t1", "--root", root]) == 0
    assert cli.main(["plan", "--step", "b", "--purpose", "second",
                     "--id", "t1", "--root", root]) == 0
    t = store.load(root, "t1")
    assert [p["id"] for p in t["plan"]] == ["a", "b"]
    # Duplicate id is refused.
    assert cli.main(["plan", "--step", "a", "--purpose", "dup",
                     "--id", "t1", "--root", root]) == 1


def test_set_step_does_not_shrink_plan(root):
    assert cli.main(["start", "--goal", "g", "--id", "t1", "--root", root]) == 0
    assert cli.main(["plan", "--step", "a", "--purpose", "first", "--id", "t1",
                     "--root", root]) == 0
    assert cli.main(["set-step", "--step", "a", "--purpose", "first", "--id", "t1",
                     "--root", root]) == 0
    t = store.load(root, "t1")
    assert [p["id"] for p in t["plan"]] == ["a"]   # roadmap intact


def test_steps_lists_markers_and_counter(root, capsys):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    cli.main(["plan", "--step", "b", "--purpose", "second", "--id", "t1",
              "--root", root])
    cli.main(["set-step", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    cli.main(["commit", "--summary", "did a", "--id", "t1", "--root", root])
    capsys.readouterr()  # clear
    assert cli.main(["steps", "--id", "t1", "--root", root]) == 0
    out = capsys.readouterr().out
    assert "1 of 2 done" in out
    assert "✓ a" in out
    assert "☐ b" in out


def test_where_prints_state_and_task_dirs(root, capsys):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    capsys.readouterr()
    assert cli.main(["where", "--id", "t1", "--root", root]) == 0
    out = capsys.readouterr().out
    assert store.waypoint_dir(root) in out
    assert store.task_dir(root, "t1") in out


def test_where_errors_for_missing_task(root):
    # An explicit --id that doesn't exist errors cleanly (no bogus path).
    assert cli.main(["where", "--id", "nope", "--root", root]) == 1


def test_current_command_is_removed(root):
    import pytest as _pytest
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    with _pytest.raises(SystemExit):   # argparse: invalid choice
        cli.main(["current", "--id", "t1", "--root", root])


def test_status_shows_progress_line(root, capsys):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    capsys.readouterr()
    assert cli.main(["status", "--id", "t1", "--root", root]) == 0
    assert "0 of 1 done" in capsys.readouterr().out


def test_list_shows_folder_header_and_progress(root, capsys):
    import os
    cli.main(["start", "--goal", "build it", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    capsys.readouterr()
    assert cli.main(["list", "--root", root]) == 0
    out = capsys.readouterr().out
    assert os.path.basename(root) in out      # folder name header
    assert root in out                          # abs path header
    assert "t1" in out and "0/1 done" in out    # task line + progress token


def test_commit_beat_shows_progress_by_default(root, capsys):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    cli.main(["plan", "--step", "b", "--purpose", "second", "--id", "t1",
              "--root", root])
    cli.main(["set-step", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    capsys.readouterr()
    cli.main(["commit", "--summary", "did a", "--id", "t1", "--root", root])
    out = capsys.readouterr().out
    assert "1 of 2 done" in out          # progress beat
    assert "next" in out.lower() and "b" in out


def test_quiet_collapses_commit_output(root, capsys):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["set-step", "--step", "a", "--purpose", "p", "--id", "t1",
              "--root", root])
    capsys.readouterr()
    cli.main(["commit", "--summary", "x", "--id", "t1", "-q", "--root", root])
    out = capsys.readouterr().out
    assert "of" not in out               # no progress beat
    assert "committed" in out


def test_ambiguous_id_error_lists_candidates(root, capsys):
    cli.main(["start", "--goal", "g1", "--id", "t1", "--root", root])
    cli.main(["start", "--goal", "g2", "--id", "t2", "--root", root])
    # No --id with two active tasks -> exit 1 and both ids surfaced.
    rc = cli.main(["status", "--root", root])
    err = capsys.readouterr().err
    assert rc == 1
    assert "t1" in err and "t2" in err
    assert "--id" in err


def test_check_output_labels_artifacts(root, tmp_path, capsys):
    art = tmp_path / "out.txt"
    art.write_text("r")
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["set-step", "--step", "a", "--purpose", "p", "--id", "t1",
              "--root", root])
    cli.main(["commit", "--summary", "s", "--artifact", str(art),
              "--id", "t1", "--root", root])
    capsys.readouterr()
    cli.main(["check", "--id", "t1", "--root", root])
    out = capsys.readouterr().out
    assert "INTACT" in out and str(art) in out


def test_watch_once_renders_progress_and_liveness(root, capsys):
    from waypoint import runtime
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    runtime.touch_heartbeat(root, "t1")
    capsys.readouterr()
    assert cli.main(["watch", "--once", "--id", "t1", "--root", root]) == 0
    out = capsys.readouterr().out
    assert "0 of 1 done" in out
    assert "worker:" in out


def test_run_spawns_worker_with_grants(root, capsys, tmp_path):
    import time
    from waypoint import launcher
    stub = tmp_path / "fakeclaude"
    stub.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n")
    stub.chmod(0o755)
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    rc = cli.main(["run", "--id", "t1", "--no-follow", "--allow", "push",
                   "--claude-bin", str(stub), "--root", root])
    try:
        assert rc == 0
        from waypoint import model, store
        assert model.has_grant(store.load(root, "t1"), "push") is True
        info = launcher.worker_info(root, "t1")
        assert info and info["pid"]
    finally:
        launcher.stop(root, "t1")


def test_run_requires_a_plan(root):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    assert cli.main(["run", "--id", "t1", "--no-follow", "--root", root]) == 1


