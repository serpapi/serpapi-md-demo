# SerpApi Markdown token benchmark

This project independently reproduces the Google Shopping JSON-vs-Markdown
token comparison using twelve preselected queries and DeepSeek's API-reported
`usage.prompt_tokens`.

## Run

Create the virtual environment and install the official SerpApi Python client:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Store the demo credentials outside the project in:

```text
/Users/magentaqin/serpapi/serpapi-keys/.env
```

with this content:

```dotenv
SERPAPI_API_KEY=...
DEEPSEEK_API_KEY=...
```

Restrict the file permissions, then run:

```bash
chmod 600 /Users/magentaqin/serpapi/serpapi-keys/.env
.venv/bin/python benchmark.py
```

Explicit `SERPAPI_API_KEY` and `DEEPSEEK_API_KEY` environment variables take
precedence when present. Otherwise the script automatically loads the fixed
file above.

Raw paired responses, DeepSeek usage records, CSV data, JSON data, and a Markdown
summary are written under `benchmark-results/latest/`. The run is resumable.

The benchmark fixes the engine, location, locale, and device. It changes only
SerpApi's `output` format between each JSON/Markdown pair. DeepSeek is called in
non-thinking mode with identical request settings for both payloads.

SerpApi calls use the official `serpapi` client. The benchmark reads the
underlying HTTP response's `.text` for both formats so the JSON response is not
re-serialized before token measurement.

## Practical use case

The follow-on Google Shopping demo is documented in
`use_cases/shopping/README.md`. It compares how many products fit in one fixed
DeepSeek input budget, computes deterministic metrics in Python, and uses
DeepSeek for paired semantic analyses and grounded recommendations. The program
then reports which findings and marketing tests are supported only by the deeper
rank evidence available in Markdown.

## Run
python3 benchmark.py  --run-dir benchmark-results/run-2026-09-01
python3 use_cases/shopping/demo.py
