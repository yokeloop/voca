#!/usr/bin/env python3
"""Voice wake-word listener for VOCA.

Stub mode (--stub): reads lines from stdin, emits JSON events on stdout.
  Type "wake" → {"event":"wake"}
  Type "stop" → {"event":"stop"}

Real mode: uses openWakeWord + PyAudio for continuous wake-word detection.
Also handles recording to WAV when signalled (SIGUSR1 = start, SIGUSR2 = stop).
"""

import argparse
import json
import os
import signal
import struct
import sys
import tempfile
import time
import wave
import math


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

    if not os.path.isfile(wake_model_path):
        print(
            f"Error: wake model not found at {wake_model_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build model paths list (stop model is optional)
    model_paths = [wake_model_path]
    has_stop_model = os.path.isfile(stop_model_path)
    if has_stop_model:
        model_paths.append(stop_model_path)

    # Load openWakeWord model(s)
    model = openwakeword.Model(wakeword_model_paths=model_paths)

    # Derive prediction keys from model filenames (openwakeword uses basename without .onnx)
    wake_key = os.path.splitext(os.path.basename(wake_model_path))[0]
    stop_key = os.path.splitext(os.path.basename(stop_model_path))[0] if has_stop_model else None

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

    # State
    running = True
    paused = False
    recording = False
    speaking = False

    # Recording state
    rec_wav_file = None
    rec_wav_path = None
    rec_start_time = 0.0
    rec_speech_detected = False
    rec_silence_start = 0.0

    # Constants
    THRESHOLD = 0.5          # Wake/stop word detection threshold
    SPEAKING_THRESHOLD = 0.85  # Raised wake threshold while TTS is playing
    RMS_THRESHOLD = 200      # RMS threshold for speech detection (int16 scale)
    SILENCE_AFTER_SPEECH = 2.0   # Seconds of silence after speech to stop recording
    MAX_SILENCE_NO_SPEECH = 30.0 # Seconds of silence with no speech before cancel
    MAX_RECORDING_DURATION = 120.0  # Max recording duration in seconds

    def compute_rms(audio_bytes):
        """Compute RMS of int16 audio data."""
        n_samples = len(audio_bytes) // 2
        if n_samples == 0:
            return 0
        fmt = "<%dh" % n_samples
        samples = struct.unpack(fmt, audio_bytes)
        sum_sq = sum(s * s for s in samples)
        return math.sqrt(sum_sq / n_samples)

    def start_recording():
        nonlocal recording, rec_wav_file, rec_wav_path, rec_start_time
        nonlocal rec_speech_detected, rec_silence_start
        recording = True
        rec_speech_detected = False
        rec_start_time = time.time()
        rec_silence_start = time.time()
        # Create temp WAV file
        fd, rec_wav_path = tempfile.mkstemp(prefix="voca-rec-", suffix=".wav", dir="/tmp")
        os.close(fd)
        rec_wav_file = wave.open(rec_wav_path, "wb")
        rec_wav_file.setnchannels(1)
        rec_wav_file.setsampwidth(2)  # 16-bit
        rec_wav_file.setframerate(16000)

    def stop_recording_save():
        """Stop recording and emit recorded event."""
        nonlocal recording, rec_wav_file, rec_wav_path
        recording = False
        path = rec_wav_path
        if rec_wav_file is not None:
            rec_wav_file.close()
            rec_wav_file = None
        rec_wav_path = None
        if path:
            print(json.dumps({"event": "recorded", "path": path}), flush=True)

    def cancel_recording():
        """Cancel recording, delete file, emit cancelled event."""
        nonlocal recording, rec_wav_file, rec_wav_path
        recording = False
        if rec_wav_file is not None:
            rec_wav_file.close()
            rec_wav_file = None
        if rec_wav_path:
            try:
                os.unlink(rec_wav_path)
            except OSError:
                pass
            rec_wav_path = None
        print(json.dumps({"event": "cancelled"}), flush=True)

    # Signal handlers
    def handle_sigterm(signum, frame):
        nonlocal running
        running = False

    def handle_sigusr1(signum, frame):
        """SIGUSR1 = start recording."""
        nonlocal paused
        if not recording:
            paused = False
            start_recording()

    def handle_sigusr2(signum, frame):
        """SIGUSR2 = stop recording (explicit stop)."""
        if recording:
            stop_recording_save()

    def handle_speaking_start(signum, frame):
        """SIGRTMIN = daemon started TTS playback."""
        nonlocal speaking
        speaking = True

    def handle_speaking_end(signum, frame):
        """SIGRTMIN+1 = daemon finished TTS playback (or interrupted)."""
        nonlocal speaking
        speaking = False

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGUSR1, handle_sigusr1)
    signal.signal(signal.SIGUSR2, handle_sigusr2)
    signal.signal(signal.SIGRTMIN, handle_speaking_start)
    signal.signal(signal.SIGRTMIN + 1, handle_speaking_end)

    try:
        while running:
            if paused:
                time.sleep(0.1)
                continue

            # Read audio chunk from mic
            try:
                raw = stream.read(1280, exception_on_overflow=False)
            except Exception:
                if paused or not running:
                    continue
                raise
            audio = np.frombuffer(raw, dtype=np.int16)

            if recording:
                # Write audio to WAV file
                if rec_wav_file is not None:
                    rec_wav_file.writeframes(raw)

                now = time.time()
                elapsed = now - rec_start_time

                # Check max duration
                if elapsed >= MAX_RECORDING_DURATION:
                    cancel_recording()
                    continue

                # Compute RMS for silence detection
                rms = compute_rms(raw)

                if rms > RMS_THRESHOLD:
                    rec_speech_detected = True
                    rec_silence_start = now
                else:
                    # Silence
                    silence_duration = now - rec_silence_start

                    if rec_speech_detected and silence_duration >= SILENCE_AFTER_SPEECH:
                        # Speech happened, then silence — done recording
                        stop_recording_save()
                        continue

                    if not rec_speech_detected and silence_duration >= MAX_SILENCE_NO_SPEECH:
                        # No speech detected for too long — cancel
                        cancel_recording()
                        continue

                # Run inference for stop word detection during recording
                if stop_key is not None:
                    scores = model.predict(audio)
                    if scores.get(stop_key, 0) > THRESHOLD:
                        print(json.dumps({"event": "stop"}), flush=True)
                        stop_recording_save()
                        model.reset()
                        continue
            else:
                # Normal wake word detection mode
                scores = model.predict(audio)

                # Raise wake threshold while TTS is playing to reduce self-trigger;
                # skip stop detection entirely during SPEAKING.
                wake_thr = SPEAKING_THRESHOLD if speaking else THRESHOLD

                # Check wake model score
                if scores.get(wake_key, 0) > wake_thr:
                    print(json.dumps({"event": "wake"}), flush=True)
                    model.reset()

                # Check stop model score (disabled during SPEAKING)
                if not speaking and stop_key is not None and scores.get(stop_key, 0) > THRESHOLD:
                    print(json.dumps({"event": "stop"}), flush=True)
                    model.reset()

    except KeyboardInterrupt:
        pass
    finally:
        # Clean up recording if still active
        if recording and rec_wav_file is not None:
            rec_wav_file.close()
            rec_wav_file = None
        if not paused:
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
        default="hey_jarvis_v0.1",
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
