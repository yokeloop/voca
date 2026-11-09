# bench

Stand scripts that measure the E0 pieces against their gates. They run in the
`agent/` environment, so they share its Python, dependencies and checks:

```sh
uv run --project agent python bench/<script>.py
```

Reports, one per measurement task:

- `report_g7.md` (VOCA-3)
- `report_g3_g4.md` (VOCA-4)
- `report_g1_g2.md` (VOCA-5)

Corpora are plain text in `bench/corpus/`. Voice recordings stay out of the
repository, in `.local/bench/`; each report names the recordings it used with
their sha256.
