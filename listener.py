#!/usr/bin/env python3
"""Voice wake-word listener for VOCA.

Stub mode (--stub): reads lines from stdin, emits JSON events on stdout.
  Type "wake" → {"event":"wake"}
  Type "stop" → {"event":"stop"}

Real mode: uses openWakeWord + PyAudio for continuous wake-word detection.
"""

import argparse
import json
import os
import signal
import sys


def run_stub():
    """Stub mode: read lines from stdin and emit matching JSON events."""
    for line in sys.stdin:
        word = line.strip().lower()
        if word == "wake":
            print(json.dumps({"event": "wake"}), flush=True)
        elif word == "stop":
            print(json.dumps({"event": "stop"}), flush=True)


def run_real(model_dir: str, wake_model: str, stop_model: str, device_index: int):
    """Real openWakeWord mode: continuous mic inference, JSON line output."""
    try:
        import numpy as np
        import openwakeword
        import pyaudio
    except ImportError as e:
        print(
            f"Error: missing dependency ({e}). Run 'voca bootstrap' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Resolve model file paths
    wake_model_path = os.path.join(model_dir, wake_model + ".onnx")
    stop_model_path = os.path.join(model_dir, stop_model + ".onnx")

    for path, label in [(wake_model_path, "wake"), (stop_model_path, "stop")]:
        if not os.path.isfile(path):
            print(
                f"Error: {label} model not found at {path}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Load openWakeWord model(s)
    model = openwakeword.Model(
        wakeword_models=[wake_model_path, stop_model_path],
        inference_framework="onnx",
    )

    # Init PyAudio and open mic stream
    pa = pyaudio.PyAudio()
    stream = pa.open(
        rate=16000,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=1280,
        input_device_index=device_index,
    )

    # Clean shutdown on SIGTERM
    running = True

    def handle_sigterm(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, handle_sigterm)

    # Detection threshold
    THRESHOLD = 0.5

    try:
        while running:
            # Read audio chunk from mic
            raw = stream.read(1280, exception_on_overflow=False)
            audio = np.frombuffer(raw, dtype=np.int16)

            # Run inference
            scores = model.predict(audio)

            # Check wake model score
            if scores.get(wake_model, 0) > THRESHOLD:
                print(json.dumps({"event": "wake"}), flush=True)
                model.reset()

            # Check stop model score
            if scores.get(stop_model, 0) > THRESHOLD:
                print(json.dumps({"event": "stop"}), flush=True)
                model.reset()

    except KeyboardInterrupt:
        pass
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


def main():
    parser = argparse.ArgumentParser(description="VOCA wake-word listener")
    parser.add_argument(
        "--stub",
        action="store_true",
        help="Run in stub mode (stdin lines instead of mic)",
    )
    parser.add_argument(
        "--model-dir",
        default=".",
        help="Directory containing .onnx model files",
    )
    parser.add_argument(
        "--wake-model",
        default="hey_jarvis",
        help="Wake model name (without .onnx extension)",
    )
    parser.add_argument(
        "--stop-model",
        default="stop",
        help="Stop model name (without .onnx extension)",
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=None,
        help="PyAudio input device index",
    )

    args = parser.parse_args()

    if args.stub:
        run_stub()
    else:
        run_real(args.model_dir, args.wake_model, args.stop_model, args.device_index)


if __name__ == "__main__":
    main()
