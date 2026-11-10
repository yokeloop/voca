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
  `delegate-thinking` (the ten scenarios with thinking on).
- `llm_speed.py` (VOCA-4) — G4: `turns`, `points`, `all` (the default).
- `llm_two_turn.py` (VOCA-4) — the two-turn mechanic (a fast phrase without
  thinking, then a thinking turn with tools): `timing [--label np1|np2]`,
  `dialogs`, `delegate`, `g3` (the default: G3 by the mechanic).
- `llm_common.py` — what the `llm_*` scripts share: `openai.LLM` setup,
  the `delegate` tool, the system prompt, template markers.
- `stt_ru.py` (VOCA-5) — G1 and «STT batch»: `samples` (the Russian samples in
  `$VOCA_MODELS_DIR/audio-cpp/samples/`, batch and `StreamAdapter`), `record
  [--from ID] [--target NODE]` (for a person: records `bench/corpus/stt_ru.tsv`
  with `pw-record`), `recordings` (G1 on those recordings, key words checked),
  `rtf5 [--file PATH] [--repeat 5]` (RTF on a 5 s segment).
- `tts_ru.py` (VOCA-5) — G2 and «TTS pcm/sample rate»: `check`, `render` (30
  phrases × the variants `default` and `ru`), `listen [--from NN]` (for a
  person: plays both variants and records the answers), `soak --variant
  default|ru [--count 200]`.
- `audio_parallel.py` (VOCA-5) — «STT does not wait for TTS» and llama-server
  decode under audio load: `--skip-llm`, `--threads-label N`, `--llm-url`.
- `audio_stand.py` — what the audio scripts share: `openai.STT`/`openai.TTS`
  setup, `SpeechLanguageClient` (adds `language` to `/audio/speech`), WAV
  helpers, the server health check.

The `llm_*` scripts need `scripts/up.sh llm`; their corpora are
`bench/corpus/llm_*.json` and their logs go to `.local/bench/llm/`.

The audio scripts need `scripts/up.sh audio`; their corpora are
`bench/corpus/stt_ru.tsv` and `bench/corpus/tts_ru.txt`, their recordings,
synthesis and tables go to `.local/bench/{stt,tts,parallel}/`.

Reports, one per measurement task:

- `report_g7.md` (VOCA-3)
- `report_g3_g4.md` (VOCA-4)
- `report_g1_g2.md` (VOCA-5)

Corpora are plain text or JSON in `bench/corpus/`. Voice recordings stay out of the
repository, in `.local/bench/`; each report names the recordings it used with
their sha256.
