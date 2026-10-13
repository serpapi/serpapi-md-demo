# SerpApi JSON vs. Markdown demos

This project contains two related experiments: an independent reproduction of
SerpApi's Google Shopping JSON-vs-Markdown token benchmark, and a practical demo
showing how the larger Markdown sample changes an SEO/marketing analysis. Both
use DeepSeek's API-reported `usage.prompt_tokens`.

## Setup

Create the virtual environment and install the official SerpApi Python client:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create a local `.env` file in the project root:

```text
serpapi-md-demo/.env
```

with this content:

```dotenv
SERPAPI_API_KEY=...
DEEPSEEK_API_KEY=...
```

You can start from the committed example and restrict the local file permissions:

```bash
cp .env.example .env
chmod 600 .env
```

Explicit `SERPAPI_API_KEY` and `DEEPSEEK_API_KEY` environment variables take
precedence when present. Otherwise the scripts automatically load `.env` from
the project root. The real `.env` is ignored by Git and must never be committed.

## Run

This repository contains two scripts for two different parts of the experiment.

### 1. Reproduce the token-savings benchmark

```bash
.venv/bin/python benchmark.py
```

`benchmark.py` validates SerpApi's published JSON-vs-Markdown token savings over
12 predefined Google Shopping queries. For every query, it:

1. fetches matching JSON and Markdown responses from SerpApi;
2. sends both raw payloads to the same DeepSeek model configuration;
3. reads DeepSeek's API-reported `usage.prompt_tokens`; and
4. calculates the percentage saved by Markdown.

Raw responses, DeepSeek usage records, CSV/JSON results, and a Markdown summary
are written to `benchmark-results/latest/`. The run is resumable, so completed
queries are not fetched again. Use a named output directory when you want to
preserve a particular run:

```bash
.venv/bin/python benchmark.py \
  --run-dir benchmark-results/run-YYYY-MM-DD
```

The benchmark fixes the engine, location, locale, and device. It changes only
SerpApi's `output` format between each JSON/Markdown pair. DeepSeek is called in
non-thinking mode with identical request settings for both payloads.

SerpApi calls use the official `serpapi` client. The benchmark reads the
underlying HTTP response's `.text` for both formats so the JSON response is not
re-serialized before token measurement.

### 2. Run the Google Shopping marketing demo

```bash
.venv/bin/python use_cases/shopping/demo.py
```

`use_cases/shopping/demo.py` turns the benchmark result into a practical
fixed-budget experiment for the query `trail running shoes` in New York. It:

1. fetches the same Shopping fields as JSON and Markdown;
2. measures how many complete products each format fits into the same
   1,550-token DeepSeek input budget;
3. runs the same SEO/marketing analysis on both samples; and
4. reports which findings and marketing actions are only supported by the
   deeper rank evidence available in Markdown.

Each run is saved under `demo-runs/<UTC timestamp>/`, including the paired source
responses and an auditable `report.json`.

For all live, fixture, replay, and custom-output options, see
[`use_cases/shopping/README.md`](use_cases/shopping/README.md).
