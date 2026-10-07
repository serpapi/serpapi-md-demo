# Google Shopping fixed-budget demo

This use case follows the benchmark with a practical question: after Markdown
reduces representation overhead, how much more SEO evidence fits in the same
DeepSeek input budget?

The demo uses New York as the default market because the United States is a
common export destination for the Shenzhen SEO audience, while New York is an
internationally recognizable, broad retail market. The location remains a CLI
option for later UK, Germany, or Southeast Asia variants.

Both SerpApi requests use the same task-specific fields:

- position
- title
- price
- rating
- reviews
- source

Python computes medians and rank correlations. DeepSeek then receives the same
analysis instructions twice: once with the JSON sample that fits the budget and
once with the Markdown sample. Each report includes title patterns, search-intent
segments, evidence-backed marketing experiments, KPIs, and limitations. The model
is explicitly told not to infer causation from a single search snapshot.

Finally, Python compares the two reports. It identifies products and rank ranges
that JSON could not expose, findings and actions grounded in that additional
evidence, evidence breadth, and whether the larger sample changes the direction
of the observed query-term signal. This comparison is deterministic; the model
does not grade its own work.

The verified New York reference run uses a 1,550-token total input budget. With
the six fields above, the measured capacity was roughly half the catalog in
JSON versus all 30 products in Markdown. The exact JSON count is measured live
because search results can change. A 3,000-token budget is not used because both
formats fit all 30 products at that size.

## Live run

```bash
.venv/bin/python use_cases/shopping/demo.py --pause
```

## One live run without the final analysis

```bash
.venv/bin/python use_cases/shopping/demo.py --no-analysis
```

## Save or refresh the verified reference responses

```bash
.venv/bin/python use_cases/shopping/demo.py --save-reference
```

## Use the saved SerpApi fixture

```bash
.venv/bin/python use_cases/shopping/demo.py --source fixture
```

The full demo makes two final DeepSeek analysis calls, one per format, after the
token-capacity measurements. Use `--no-analysis` to skip those two calls.

Every completed run saves both analyses and their marketing-strategy delta in a
`report.json`. It can be replayed without either
API for an on-stage network fallback:

```bash
.venv/bin/python use_cases/shopping/demo.py \
  --replay-report demo-runs/<run>/report.json
```

The repository also includes a verified reference report:

```bash
.venv/bin/python use_cases/shopping/demo.py \
  --replay-report use_cases/shopping/reference-data/reference-report.json
```

For the default New York scenario, a failed live SerpApi request automatically
uses the verified fixture. If DeepSeek is unavailable, the demo automatically
renders the verified reference report. Custom queries and locations fail
explicitly instead of silently showing mismatched fallback data.
