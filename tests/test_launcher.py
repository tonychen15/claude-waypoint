"""Tests for the worker process launcher (fake-worker stubs; no real claude)."""

import os
import signal
import time

from waypoint import launcher, model, store


def _stub(tmp_path, name, body):
    p = tmp_path / name
    p.write_text("#!/usr/bin/env python3\n" + body)
    p.chmod(0o755)
    return str(p)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_spawn_writes_worker_json_and_runs(tmp_path):
    root = str(tmp_path)
    store.save(root, model.new_task("t1", "g"))
    stub = _stub(tmp_path, "fakeclaude", "import time\ntime.sleep(30)\n")
    info = launcher.spawn(root, "t1", store.load(root, "t1"), claude_bin=stub)
    try:
        assert info["pid"] and info["session_id"]
        assert launcher.worker_info(root, "t1")["pid"] == info["pid"]
        time.sleep(0.3)
        assert _alive(info["pid"])
    finally:
        launcher.stop(root, "t1")


def test_stop_kills_the_worker(tmp_path):
    root = str(tmp_path)
    store.save(root, model.new_task("t1", "g"))
    stub = _stub(tmp_path, "fakeclaude", "import time\ntime.sleep(30)\n")
    info = launcher.spawn(root, "t1", store.load(root, "t1"), claude_bin=stub)
    assert launcher.stop(root, "t1") is True
    time.sleep(0.3)
    assert not _alive(info["pid"])


def test_spawn_fresh_passes_session_id_to_argv(tmp_path, monkeypatch):
    root = str(tmp_path)
    store.save(root, model.new_task("t1", "g"))
    out = tmp_path / "argv.txt"
    monkeypatch.setenv("ARGV_OUT", str(out))
    stub = _stub(tmp_path, "fakeclaude",
                 "import sys, os\n"
                 "open(os.environ['ARGV_OUT'], 'w').write('\\x00'.join(sys.argv))\n")
    info = launcher.spawn(root, "t1", store.load(root, "t1"), claude_bin=stub)
    time.sleep(0.3)
    argv = out.read_text().split("\x00")
    assert "--session-id" in argv
    assert argv[argv.index("--session-id") + 1] == info["session_id"]


def test_worker_info_none_when_absent(tmp_path):
    assert launcher.worker_info(str(tmp_path), "t1") is None


def test_spawn_sets_task_id_env(tmp_path, monkeypatch):
    root = str(tmp_path)
    store.save(root, model.new_task("t1", "g"))
    out = tmp_path / "env.txt"
    monkeypatch.setenv("ENV_OUT", str(out))
    stub = _stub(tmp_path, "fakeclaude",
                 "import os\nopen(os.environ['ENV_OUT'],'w').write("
                 "os.environ.get('WAYPOINT_TASK_ID',''))\n")
    launcher.spawn(root, "t1", store.load(root, "t1"), claude_bin=stub)
    import time
    time.sleep(0.3)
    assert out.read_text() == "t1"
