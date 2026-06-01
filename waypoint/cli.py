"""The ``waypoint`` command-line interface.

Subcommands implement the lifecycle and the per-step checkpoint protocol:

    waypoint start    --goal G [--id ID] [--scope P ...] [--auto]
    waypoint plan     --step b --purpose P [--id TASK]
    waypoint set-step --step b --purpose P [--target T] [--expected E]
                      [--context C] [--input PATH ...] [--id TASK]
    waypoint commit   --summary S [--artifact PATH ...] [--git] [--id TASK]
    waypoint status   [--id TASK] [--json]
    waypoint steps    [--id TASK]
    waypoint resume   [--id TASK]
    waypoint check    [--id TASK]
    waypoint where    [--id TASK]
    waypoint done     [--id TASK]
    waypoint abandon  [--id TASK]
    waypoint list

Global: ``--root PATH`` and ``-q/--quiet`` (collapse mutating-command output
to one line). ``list`` covers the current folder only.

The state machine (§2): a step is committed only after it succeeds; at most
one uncommitted ``current_step`` exists at a time; ``set-step`` opens it and
``commit`` closes it. ``resume`` re-checks the last step's artifacts (§9).
``plan`` declares the roadmap so ``status``/``steps`` show "step N of M".
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Optional

from . import __version__, fingerprint, model, monitor, progress, runtime, statusmd, store


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:40] or "task").rstrip("-")


def _resolve(root: str, task_id: Optional[str]) -> tuple:
    """Return ``(task_id, task)``; infer the single active task if id omitted.

    Raises:
        ValueError: If the id is missing and the active task is ambiguous
            (zero or multiple). ``main`` catches this and exits 1.
    """
    if task_id:
        return task_id, store.load(root, task_id)
    active = store.active_tasks(root)
    if len(active) == 1:
        return active[0]
    if not active:
        raise ValueError("no active task in this folder.")
    ids = "\n  ".join(tid for tid, _ in active)
    raise ValueError(
        f"{len(active)} active tasks here — rerun with --id <one of>:\n  {ids}"
    )


def cmd_start(root: str, args) -> int:
    task_id = args.id or f"{model.now_iso()[:10]}-{_slug(args.goal)}"
    try:
        store.load(root, task_id)
        print(f"waypoint: task {task_id!r} already exists", file=sys.stderr)
        return 1
    except FileNotFoundError:
        pass
    others = store.active_tasks(root)
    if others:
        names = ", ".join(tid for tid, _ in others)
        print(f"waypoint: note — other active task(s) present: {names}",
              file=sys.stderr)
    task = model.new_task(task_id, args.goal, scope=args.scope,
                          owner_session=args.session or "", auto=args.auto)
    store.save(root, task)
    if args.quiet:
        print(task_id)
    else:
        print(f"started task {task_id}")
        print(f"  goal: {task.get('goal')}")
        print(f"  state: {store.task_dir(root, task_id)}")
        print("  next: declare steps with `waypoint plan`, then `waypoint set-step`")
    return 0


def cmd_set_step(root: str, args) -> int:
    task_id, task = _resolve(root, args.id)
    if task.get("current_step") is not None:
        print("waypoint: a step is already in progress; commit it first "
              f"(current: {task['current_step'].get('id')})", file=sys.stderr)
        return 1
    task["current_step"] = {
        "id": args.step,
        "purpose": args.purpose,
        "target": args.target or "",
        "context": args.context or "",
        "inputs": [{"path": p} for p in (args.input or [])],
        "expected_result": args.expected or "",
        "status": model.STEP_IN_PROGRESS,
    }
    store.save(root, task)
    if args.quiet:
        print(f"started step {args.step}")
    else:
        pos = progress.position_of(task, args.step)
        print(f"▶ started step {args.step!r} (step {pos}) — {args.purpose}")
    return 0


def _git_commit(artifacts: list, message: str) -> Optional[str]:
    """Stage the given artifacts and commit; return the short SHA or None."""
    if not artifacts:
        return None
    try:
        subprocess.run(["git", "add", "--", *artifacts], check=True,
                       capture_output=True, text=True, timeout=30)
        subprocess.run(["git", "commit", "-m", message], check=True,
                       capture_output=True, text=True, timeout=30)
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def cmd_commit(root: str, args) -> int:
    task_id, task = _resolve(root, args.id)
    cur = task.get("current_step")
    if cur is None:
        print("waypoint: no step in progress to commit", file=sys.stderr)
        return 1
    artifacts = [fingerprint.fingerprint(p) for p in (args.artifact or [])]
    step_commit = None
    if args.git:
        step_commit = _git_commit(
            list(args.artifact or []),
            f"waypoint: {task_id} step {cur.get('id')} — {args.summary or cur.get('purpose')}",
        )
        if step_commit:
            for a in artifacts:
                a["step_commit"] = step_commit
    cur["actual_result"] = {"summary": args.summary or "", "artifacts": artifacts}
    if step_commit:
        cur["step_commit"] = step_commit
    cur["status"] = model.STEP_SUCCEEDED
    cur["completed_at"] = model.now_iso()
    task.setdefault("steps", []).append(cur)
    task["current_step"] = None
    store.save(root, task)
    if args.quiet:
        print(f"committed step {cur.get('id')!r}"
              + (f" @ {step_commit}" if step_commit else ""))
        return 0
    done, total = progress.done_count(task), progress.total_count(task)
    rem = progress.remaining(task)
    if progress.has_plan(task):
        beat = f"✓ committed step {cur.get('id')!r} — {done} of {total} done"
        beat += (f"; next: step {rem[0]['id']} ({rem[0]['purpose']})"
                 if rem else "; plan complete ✓")
    else:
        beat = (f"✓ committed step {cur.get('id')!r} — "
                f"{done} step{'s' if done != 1 else ''} committed (no plan)")
    if step_commit:
        beat += f"  @ {step_commit}"
    print(beat)
    return 0


def cmd_plan(root: str, args) -> int:
    task_id, task = _resolve(root, args.id)
    plan = task.setdefault("plan", [])
    if any(p.get("id") == args.step for p in plan):
        print(f"waypoint: step {args.step!r} is already in the plan",
              file=sys.stderr)
        return 1
    plan.append({"id": args.step, "purpose": args.purpose})
    store.save(root, task)
    if not getattr(args, "quiet", False):
        print(f"planned step {args.step!r} — {progress.summary(task)}")
    else:
        print(f"planned {args.step}")
    return 0


def cmd_status(root: str, args) -> int:
    # No active task and no --id flows through _resolve for one canonical
    # message ("no active task in this folder") and exit code (1), consistent
    # with every other command.
    _, task = _resolve(root, args.id)
    if args.json:
        print(json.dumps(task, indent=2, ensure_ascii=False))
    else:
        print(statusmd.render(task))
    return 0


def _check(task: dict) -> list:
    """Return ``(path, verdict)`` for each artifact of the last step."""
    step = model.last_succeeded(task)
    if not step:
        return []
    results = []
    for art in step.get("actual_result", {}).get("artifacts", []):
        results.append((art.get("path"), fingerprint.verify(art)))
    return results


def cmd_check(root: str, args) -> int:
    """Re-verify the last committed step's artifacts (drift detection)."""
    _, task = _resolve(root, args.id)
    results = _check(task)
    bad = [(p, v) for p, v in results if v != fingerprint.INTACT]
    if not results:
        print("no artifacts recorded on the last committed step")
        return 0
    print("last committed step's artifacts:")
    for path, verdict in results:
        print(f"  {verdict.upper():8} {path}")
    return 1 if bad else 0


def cmd_resume(root: str, args) -> int:
    task_id, task = _resolve(root, args.id)
    step = model.last_succeeded(task)
    cur = task.get("current_step")
    print(f"# Resuming task {task_id!r}: {task.get('goal')}")
    if step:
        print(f"\nLast committed step: {step.get('id')} — {step.get('purpose')}")
        print(f"  result: {step.get('actual_result', {}).get('summary', '')}")
        results = _check(task)
        for path, verdict in results:
            mark = {fingerprint.INTACT: "ok",
                    fingerprint.MISSING: "GONE",
                    fingerprint.CHANGED: "CHANGED"}[verdict]
            print(f"  [{mark}] {path}")
        if any(v != fingerprint.INTACT for _, v in results):
            print("\n⚠ Some artifacts changed or are missing — surface to the "
                  "human before continuing (§9 go-deep).")
    if cur:
        print(f"\n▶ In-progress step to re-run: {cur.get('id')} — "
              f"{cur.get('purpose')}")
        print(f"  target: {cur.get('target', '')}")
        print("  Re-run via observe-then-act: inspect current state, do only "
              "what remains.")
    else:
        rem = progress.remaining(task)
        if rem:
            nxt = rem[0]
            print(f"\nNo step in progress. Next planned: {nxt.get('id')} — "
                  f"{nxt.get('purpose')}. Declare it with `waypoint set-step`.")
        else:
            print("\nNo step in progress and nothing planned.")
    return 0


def _close(root: str, args, status: str) -> int:
    task_id, task = _resolve(root, args.id)
    task["status"] = status
    store.save(root, task)
    dst = store.archive(root, task_id)
    print(f"task {task_id!r} {status}; archived to {dst}")
    return 0


def cmd_done(root: str, args) -> int:
    return _close(root, args, model.COMPLETED)


def cmd_abandon(root: str, args) -> int:
    return _close(root, args, model.ABANDONED)


def _purpose_for(task: dict, step_id: str) -> str:
    """Best-effort purpose text for a step id (committed step or plan entry)."""
    for s in task.get("steps", []):
        if s.get("id") == step_id:
            return s.get("purpose", "")
    for p in task.get("plan", []):
        if p.get("id") == step_id:
            return p.get("purpose", "")
    return ""


def cmd_where(root: str, args) -> int:
    print(f"state dir:  {store.waypoint_dir(root)}")
    # Show the resolved task dir(s): the named/inferred one, else all active.
    if args.id:
        targets = [args.id]
    else:
        targets = [tid for tid, _ in store.active_tasks(root)]
    if not targets:
        print("(no active task)")
        return 0
    rc = 0
    for tid in targets:
        td = store.task_dir(root, tid)
        if not os.path.isdir(td):
            print(f"waypoint: no such task {tid!r}", file=sys.stderr)
            rc = 1
            continue
        print(f"task dir:   {td}")
        print(f"  {store.STATE_FILE}, {store.STATUS_FILE}")
    return rc


def cmd_steps(root: str, args) -> int:
    task_id, task = _resolve(root, args.id)
    if progress.has_plan(task):
        head = (f"Steps for {task_id}   "
                f"({progress.done_count(task)} of {progress.total_count(task)} done)")
    else:
        head = (f"Steps for {task_id}   "
                f"({progress.done_count(task)} committed, no plan declared)")
    print(head)
    cur = task.get("current_step")
    cur_id = cur.get("id") if cur else None
    done_ids = {s.get("id") for s in task.get("steps", [])}
    for sid in progress.ordered_ids(task):
        if sid in done_ids:
            mark, purpose = "✓", _purpose_for(task, sid)
        elif sid == cur_id:
            mark, purpose = "▶", cur.get("purpose", "")
        else:
            mark, purpose = "☐", _purpose_for(task, sid)
        print(f"  {mark} {sid}  {purpose}")
    return 0


def cmd_list(root: str, args) -> int:
    print(f"# {os.path.basename(root.rstrip('/')) or root}  {root}")
    active = store.active_tasks(root)
    if not active:
        print("(no active tasks)")
        return 0
    for tid, t in active:
        print(f"{tid}  [{progress.token(t)}]  {t.get('goal')}")
    return 0


def cmd_watch(root: str, args) -> int:
    import time
    task_id, _ = _resolve(root, args.id)
    while True:
        _, task = _resolve(root, task_id)   # reload each tick
        snap = runtime.snapshot(root, task_id)
        print(monitor.render(task, snap))
        if args.once or task.get("status") != model.IN_PROGRESS:
            return 0
        print("-" * 40)
        time.sleep(args.interval)


def build_parser() -> argparse.ArgumentParser:
    # Shared options live on a parent parser so they are accepted *after* the
    # subcommand name (e.g. `waypoint start --root X`), not only before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="project root (default: auto-detect)")
    common.add_argument("-q", "--quiet", action="store_true",
                        help="collapse mutating-command output to one line")

    p = argparse.ArgumentParser(prog="waypoint", description=__doc__,
                                parents=[common])
    # --version fires during parsing and exits before the required-subcommand
    # check, so `waypoint --version` works without a subcommand.
    p.add_argument("--version", "-V", action="version",
                   version=f"waypoint {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", parents=[common]); s.set_defaults(fn=cmd_start)
    s.add_argument("--goal", required=True)
    s.add_argument("--id")
    s.add_argument("--scope", nargs="*")
    s.add_argument("--session", default="")
    s.add_argument("--auto", action="store_true")

    s = sub.add_parser("set-step", parents=[common]); s.set_defaults(fn=cmd_set_step)
    s.add_argument("--step", required=True)
    s.add_argument("--purpose", required=True)
    s.add_argument("--target")
    s.add_argument("--expected")
    s.add_argument("--context")
    s.add_argument("--input", nargs="*")
    s.add_argument("--id")

    s = sub.add_parser("commit", parents=[common]); s.set_defaults(fn=cmd_commit)
    s.add_argument("--summary")
    s.add_argument("--artifact", nargs="*")
    s.add_argument("--git", action="store_true")
    s.add_argument("--id")

    s = sub.add_parser("plan", parents=[common]); s.set_defaults(fn=cmd_plan)
    s.add_argument("--step", required=True)
    s.add_argument("--purpose", required=True)
    s.add_argument("--id")

    for name, fn in (("resume", cmd_resume), ("done", cmd_done),
                     ("abandon", cmd_abandon), ("steps", cmd_steps),
                     ("where", cmd_where)):
        s = sub.add_parser(name, parents=[common]); s.set_defaults(fn=fn)
        s.add_argument("--id")

    s = sub.add_parser(
        "check", parents=[common],
        help="re-verify the last committed step's artifacts (INTACT/MISSING/CHANGED)",
    )
    s.set_defaults(fn=cmd_check)
    s.add_argument("--id")

    s = sub.add_parser("status", parents=[common]); s.set_defaults(fn=cmd_status)
    s.add_argument("--id")
    s.add_argument("--json", action="store_true")

    sub.add_parser("list", parents=[common]).set_defaults(fn=cmd_list)

    s = sub.add_parser("watch", parents=[common]); s.set_defaults(fn=cmd_watch)
    s.add_argument("--id")
    s.add_argument("--once", action="store_true",
                   help="render once and exit (no refresh loop)")
    s.add_argument("--interval", type=float, default=3.0,
                   help="refresh seconds when looping (default: 3)")
    return p


def main(argv: Optional[list] = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    args = build_parser().parse_args(argv)
    root = store.project_root(args.root)
    try:
        return args.fn(root, args)
    except FileNotFoundError as e:
        print(f"waypoint: not found: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"waypoint: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
