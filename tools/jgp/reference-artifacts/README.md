# JGP 2026 compatibility baseline

`fixtures/2026` freezes the upstream inputs for the partial 2026 season. This directory freezes what
the pipeline produces from them.

The **generated Typst source** is the automated contract. It carries every content decision the
pipeline makes, so byte-for-byte equality on it catches any change in generated output and needs no
rendering, no font embedding, and no container bytes in the comparison. `2026-standings-reviewed.pdf`
and `2026-stats-golden.xlsx` come out of the same run, so a run that reproduces the golden source
reproduces them; both are accepted evidence and neither is compared automatically.

The static Typst assets the generated source pulls in sit outside the comparison, in two groups:

- `typst/details.typ` and the `typst/rules.typ` it includes are static non-tabular prose: the rules
  explanation and the data-source attribution. They change by hand with no re-acceptance, as
  Flickornas treats its `rules.typ`. The acceptance record's `comparison_scope_note` says so; it
  names `typst/how.typ`, the name `rules.typ` had until `b666108`.
- `typst/lib.typ`, `typst/stsf.png` and `typst/fonts/` shape the layout and pagination of the
  standings pages. They change only by hand, and a change to any of them is reviewed by re-rendering
  and reading the PDF under the procedure below.

| File | Role |
| --- | --- |
| `2026-standings-golden.typ` | Accepted Typst source — the automated contract |
| `2026-stats-golden.xlsx` | Accepted participant statistics workbook; evidence, not a contract |
| `2026-standings-reviewed.pdf` | The document the reviewer read; evidence, not a contract |
| `2026-golden-acceptance.json` | Who accepted, when, on what basis, and the SHA-256 of all three |
| `2026-edge-case-coverage.md` | Which named edge cases the capture exercises, and which it does not |

## Regenerate and compare

```console
JGP_FIXTURES_DIR=fixtures/2026 uv run jgp generate
diff reference-artifacts/2026-standings-golden.typ output/runs/<run id>/standings.typ
```

Use a scratch working directory, or move `state/run_input.json` aside first: the run replays the
rating-list date recorded there, and the fixtures hold only the one they were captured with.

`tests/test_golden_typst.py` runs that comparison in the default offline test suite, through
`compare_typst.py`. The same check is available on the command line:

```console
uv run python compare_typst.py reference-artifacts/2026-standings-golden.typ output/runs/<run id>/standings.typ
```

The run is reproducible only because the standings sort keys are a total order. `create_merged_df`'s
full outer join returns rows in an order polars does not specify and that differs between calls, so
`rank_band` breaks ties on `contenderId` descending — the higher, more recently
registered id first — after the four ranking keys (`Σ`, tournaments played, birth year, latest
scoring round). Remove that tie-break and no two runs agree. See `tests/test_standings_order.py`.
`assign_standings_points` ends its sort on the same key, so two players level on match and quality
points within one class and tournament take their standings points in a fixed order too. No such pair
occurs in this capture, which is why adding that key left the golden source unchanged. See
`tests/test_points_allocation.py`.

## Re-accept after an approved behaviour change

1. Keep the existing baseline in place while reviewing.
2. Regenerate a candidate from the same `fixtures/2026` — the same fixtures, not a fresh capture.
3. Read the whole PDF the run rendered, `output/runs/<run id>/standings-<time>.pdf`.
4. Only after a named reviewer approves it, replace the golden Typst source, the golden workbook and
   the reviewed PDF, and update every hash and the review details in `2026-golden-acceptance.json`.

Never regenerate the golden files as a side effect of an ordinary run, and never re-capture
`fixtures/2026` as part of re-accepting output. A new input capture is a separate baseline decision
and needs its own manifest and its own review.
