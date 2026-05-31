"""waypoint — forward-recovery checkpoint-restart for Claude Code.

A small, file-based mechanism that records the state of a tracked multi-step
task so a fresh session can continue forward from the last committed step
after a close, crash, or token-limit interruption.

See docs/design.md for the full design.
"""

__version__ = "0.1.0"
