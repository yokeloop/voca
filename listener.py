#!/usr/bin/env python3
"""Voice wake-word listener for VOCA.

Stub mode (--stub): reads lines from stdin, emits JSON events on stdout.
  Type "wake" → {"event":"wake"}
  Type "stop" → {"event":"stop"}

Real mode (Phase 6): uses openWakeWord for continuous wake-word detection.
"""

import sys
import json


def run_stub():
    """Stub mode: read lines from stdin and emit matching JSON events."""
    for line in sys.stdin:
        word = line.strip().lower()
        if word == "wake":
            print(json.dumps({"event": "wake"}), flush=True)
        elif word == "stop":
            print(json.dumps({"event": "stop"}), flush=True)


def run_real(model_dir: str):
    """Real openWakeWord mode — skeleton for Phase 6."""
    try:
        import openwakeword  # noqa: F401
    except ImportError:
        print(
            "Error: openwakeword is not installed. Run 'voca bootstrap' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    # TODO (Phase 6): initialize openwakeword, open mic stream,
    # detect wake/stop words, emit JSON lines on stdout.
    raise NotImplementedError("Real openWakeWord listener not yet implemented")


def main():
    if "--stub" in sys.argv:
        run_stub()
    else:
        model_dir = None
        if "--model-dir" in sys.argv:
            idx = sys.argv.index("--model-dir")
            if idx + 1 < len(sys.argv):
                model_dir = sys.argv[idx + 1]
        run_real(model_dir or ".")


if __name__ == "__main__":
    main()
