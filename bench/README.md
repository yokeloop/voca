# bench

Stand scripts that measure the E0 pieces against their gates. They run in the
`agent/` environment, so they share its Python, dependencies and checks:

```sh
uv run --project agent python bench/<script>.py
```

Scripts:

- `hermes_runs.py` (VOCA-3) — G7, the Hermes Runs API: `preflight`, `create`,
  `resume`, `conflict`, `session`, `all` (the default: those five in order),
  `delegate`, `restart --phase start|verify`, `backend-down`.
- `llm_template.py` (VOCA-4) — the first-hour checks of the LLM stand:
  `kwargs-body`, `kwargs-live`, `tool-stream`, `tool-cycle`, `all` (the default).
- `llm_ru_tools.py` (VOCA-4) — G3: `dialogs`, `delegate`, `all` (the default),
  `dialogs-thinking` (the twenty dialogs with thinking on, VOCA-7),
  `delegate-thinking` (the ten scenarios with thinking on).
- `llm_speed.py` (VOCA-4) — G4: `turns`, `points`, `all` (the default);
  `--thinking` (VOCA-7) sends the thinking kwargs with `max_tokens` 1024,
  measures TTFT to the first output (reasoning or content), also records the
  first content, and writes `speed_thinking` instead of `speed`.
- `llm_two_turn.py` (VOCA-4) — the two-turn mechanic (a fast phrase without
  thinking, then a thinking turn with tools): `timing [--label np1|np2]`,
  `dialogs`, `delegate`, `g3` (the default: G3 by the mechanic).
- `llm_common.py` — what the `llm_*` scripts share: `openai.LLM` setup,
  the `delegate` tool, the system prompt, template markers.
- `stt_ru.py` (VOCA-5) — G1 and «STT batch»: `samples` (the Russian samples in
  `$VOCA_MODELS_DIR/audio-cpp/samples/`, batch and `StreamAdapter`; a sample
  whose peak is below 2000 of 32767 is reported as `skip` and does not fail the
  run), `record
  [--from ID] [--target NODE]` (for a person: records `bench/corpus/stt_ru.tsv`
  with `pw-record`), `recordings` (G1 on those recordings, key words checked),
  `rtf5 [--file PATH] [--repeat 5]` (RTF on a 5 s segment).
- `tts_ru.py` (VOCA-5) — G2 and «TTS pcm/sample rate»: `check`, `render` (30
  phrases × the variants `default` and `ru`), `listen [--from NN]` (for a
  person: plays both variants and records the answers), `soak --variant
  default|ru [--count 200]`; the G2 experiments `ab-render [--numbers 1,2,4]`
  and `ab` (numbers as words, q8_0/f16, 8/16 steps, voices), `ab2-render` and
  `ab2` (blind F4 q8_0/f16 × 8/16 steps, then manual stress marks), `voiceprobe
  [--model tts-f16] [--steps 16]` (all 10 voices on phrases 1, 2, 4, scored by
  a Parakeet round-trip; its default model `tts-f16` exists only on the
  server from `.local/bench/server-ab.json`, README 0.2); `soak` needs a
  `render` with the same `--voice`.
- `tts_pipeline.py` (VOCA-5) — the text path before TTS: `accent [--omograph
  tiny2.1,turbo3.1]` (ruaccent latency and accuracy against
  `corpus/tts_ru_stress.tsv`), `run [--omograph turbo3.1]` (LLM reply with
  numbers as words → stress → F4 synthesis, time to the first sound over
  `corpus/tts_pipeline.json`, needs `scripts/up.sh llm`), `poezd` (stress mark
  spellings of «поезд»), `chunks` (first sound when the first fragment is cut
  at a comma or dash after at least 3 words), `think [--modes
  off,on,b128,b64]` (answer accuracy against the `answer`/`keys` of
  `corpus/tts_pipeline.json` and first-sound delay without thinking, with it
  and with `thinking_budget_tokens` 128 and 64; `--digits` drops the
  numbers-as-words line from the system prompt and writes
  `think-<mode>-digits.tsv` and `think-digits.tsv`), `caps` (stress on a capital
  vowel at the start of a phrase), `listen` and `caps-listen` (for a person);
  needs
  `scripts/ruaccent-setup.sh` once per `agent/.venv`.
- `audio_parallel.py` (VOCA-5) — «STT does not wait for TTS» and llama-server
  decode under audio load: `--skip-llm`, `--threads-label N`, `--llm-url`.
- `audio_stand.py` — what the audio scripts share: `openai.STT`/`openai.TTS`
  setup, `SpeechLanguageClient` (adds `language` to `/audio/speech`), WAV
  helpers, the server health check.

The `llm_*` scripts need `scripts/up.sh llm`; their corpora are
`bench/corpus/llm_*.json` and their logs go to `.local/bench/llm/`.

The scripts take the model name from `GET /v1/models` (`--alias`, the
`VOCA_LLM_PROFILE` of the running server). The outputs of a model other than
`bolt-e4b` go to `.local/bench/llm/<model>/` and `.local/bench/pipeline/<model>/`
(`tts_pipeline.py` `run`, `chunks`, `think`); the audio-only outputs stay shared.
Sampling comes from the server profile; the client sends none.

The audio scripts need `scripts/up.sh audio`; their corpora are
`bench/corpus/stt_ru.tsv`, `bench/corpus/tts_ru.txt`, `tts_ru_norm.txt`,
`tts_ru_stress.tsv` and `tts_pipeline.json`; their recordings, synthesis and
tables go to `.local/bench/{stt,tts,parallel,pipeline}/`.

Reports, one per measurement task:

- `report_g7.md` (VOCA-3)
- `report_g3_g4.md` (VOCA-4)
- `report_g1_g2.md` (VOCA-5)
- `report_lfm3_1_7b.md` (VOCA-7)

Corpora are plain text or JSON in `bench/corpus/`. Voice recordings stay out of the
repository, in `.local/bench/`; each report names the recordings it used with
their sha256.
