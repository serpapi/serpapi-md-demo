#!/usr/bin/env python3
"""Reproduce SerpApi Google Shopping JSON vs Markdown token savings.

The benchmark fetches paired raw responses from SerpApi, sends each raw payload
through the same DeepSeek request, and uses the API-reported prompt_tokens as the
measurement. Results and raw inputs are saved so the run can be audited later.
"""

import argparse
import csv
import json
import os
import re
import stat
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import serpapi


QUERIES = [
    "trail running shoes",
    "wireless headphones",
    "standing desk",
    "coffee machine",
    "air purifier",
    "gaming laptop",
    "hiking backpack",
    "electric toothbrush",
    "yoga mat",
    "winter jacket",
    "office chair",
    "protein powder",
]

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-flash"
SECRETS_FILE = Path(__file__).resolve().with_name(".env")


def load_secrets_file(path=SECRETS_FILE):
    """Load demo credentials without exposing them in the project directory.

    Explicit environment variables take precedence. The file must not be
    readable or writable by group/other users.
    """
    if not path.exists():
        return

    permissions = stat.S_IMODE(path.stat().st_mode)
    if permissions & 0o077:
        raise RuntimeError(
            "Secrets file permissions are too open: {}. Run: chmod 600 {}".format(
                oct(permissions), path
            )
        )

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise RuntimeError("Invalid secrets file line {}".format(line_number))
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        if key in {"SERPAPI_API_KEY", "DEEPSEEK_API_KEY"}:
            os.environ.setdefault(key, value)


def slugify(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def request(url, headers=None, body=None, attempts=4):
    headers = headers or {}
    last_error = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=180) as response:
                return response.read(), dict(response.headers)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if isinstance(exc, urllib.error.HTTPError):
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
                if exc.code < 500 and exc.code != 429:
                    raise RuntimeError("HTTP {}: {}".format(exc.code, detail)) from exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError("Request failed after {} attempts: {}".format(attempts, last_error))


def fetch_serpapi(client, query, output):
    params = {
        "engine": "google_shopping",
        "q": query,
        "location": "Austin, Texas, United States",
        "gl": "us",
        "hl": "en",
        "device": "desktop",
        "output": output,
    }
    # Use SerpApi's official Python client, but keep the underlying response
    # text. client.search() parses JSON into a SerpResults object while leaving
    # Markdown as text; measuring response.text preserves an equal raw-payload
    # benchmark for both formats.
    response = client.request("GET", "/search", params=params)
    return response.text, dict(response.headers)


def deepseek_prompt_tokens(api_key, payload):
    request_body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": payload}],
        "thinking": {"type": "disabled"},
        "temperature": 0,
        "max_tokens": 1,
        "stream": False,
    }
    raw, _ = request(
        DEEPSEEK_URL,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "User-Agent": "serpapi-md-benchmark/1.0",
        },
        body=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
    )
    response = json.loads(raw.decode("utf-8"))
    usage = response.get("usage") or {}
    if "prompt_tokens" not in usage:
        raise RuntimeError("DeepSeek response did not include usage.prompt_tokens")
    return int(usage["prompt_tokens"]), response


def inspect_pair(json_text, markdown_text):
    parsed = json.loads(json_text)
    products = parsed.get("shopping_results") or []
    titles = [str(item.get("title", "")).strip() for item in products if item.get("title")]
    # Markdown tables escape literal pipes inside values ("|" -> "\|").
    # Normalize that representation before comparing product titles.
    normalized_md = re.sub(r"\s+", " ", markdown_text.replace("\\|", "|")).lower()
    matched = sum(1 for title in titles if re.sub(r"\s+", " ", title).lower() in normalized_md)
    return {
        "json_search_id": (parsed.get("search_metadata") or {}).get("id"),
        "json_product_count": len(products),
        "titles_checked": len(titles),
        "titles_found_in_markdown": matched,
        "title_coverage_pct": round(100 * matched / len(titles), 2) if titles else None,
    }


def write_outputs(run_dir, records, metadata):
    result_path = run_dir / "results.json"
    result_path.write_text(
        json.dumps({"metadata": metadata, "results": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fieldnames = [
        "query",
        "json_prompt_tokens",
        "markdown_prompt_tokens",
        "token_savings_pct",
        "json_bytes",
        "markdown_bytes",
        "byte_savings_pct",
        "json_product_count",
        "titles_checked",
        "titles_found_in_markdown",
        "title_coverage_pct",
        "json_search_id",
    ]
    with (run_dir / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    savings = [row["token_savings_pct"] for row in records]
    lines = [
        "# Google Shopping JSON vs Markdown — DeepSeek reproduction",
        "",
        "- Run time (UTC): {}".format(metadata["run_time_utc"]),
        "- DeepSeek model: `{}` (non-thinking)".format(metadata["deepseek_model"]),
        "- Search controls: Austin, US; English; desktop",
        "- Samples completed: {}".format(len(records)),
    ]
    if savings:
        lines.extend(
            [
                "- Median token savings: **{:.2f}%**".format(statistics.median(savings)),
                "- Mean token savings: **{:.2f}%**".format(statistics.mean(savings)),
                "- Range: **{:.2f}%–{:.2f}%**".format(min(savings), max(savings)),
                "",
                "| Query | JSON tokens | Markdown tokens | Savings | Title coverage |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for row in records:
            coverage = row["title_coverage_pct"]
            coverage_text = "n/a" if coverage is None else "{:.1f}%".format(coverage)
            lines.append(
                "| {} | {:,} | {:,} | {:.2f}% | {} |".format(
                    row["query"],
                    row["json_prompt_tokens"],
                    row["markdown_prompt_tokens"],
                    row["token_savings_pct"],
                    coverage_text,
                )
            )
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_existing(run_dir):
    path = run_dir / "results.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("results", [])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default="benchmark-results/latest")
    parser.add_argument("--limit", type=int, default=len(QUERIES))
    args = parser.parse_args()

    load_secrets_file()
    serpapi_key = os.environ.get("SERPAPI_API_KEY")
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    if not serpapi_key or not deepseek_key:
        print(
            "SERPAPI_API_KEY and DEEPSEEK_API_KEY must be set in the environment "
            "or {}".format(SECRETS_FILE),
            file=sys.stderr,
        )
        return 2

    serpapi_client = serpapi.Client(api_key=serpapi_key, timeout=180)

    run_dir = Path(args.run_dir).resolve()
    raw_dir = run_dir / "raw"
    usage_dir = run_dir / "deepseek-usage"
    raw_dir.mkdir(parents=True, exist_ok=True)
    usage_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "run_time_utc": datetime.now(timezone.utc).isoformat(),
        "deepseek_model": MODEL,
        "measurement": "DeepSeek API usage.prompt_tokens",
        "query_count_planned": min(args.limit, len(QUERIES)),
        "parameters": {
            "engine": "google_shopping",
            "location": "Austin, Texas, United States",
            "gl": "us",
            "hl": "en",
            "device": "desktop",
        },
    }
    records = load_existing(run_dir)
    # Refresh audit checks from saved raw pairs, including when resuming a run.
    by_query = {row["query"]: row for row in records}
    for index, query in enumerate(QUERIES[: args.limit], start=1):
        stem = "{:02d}-{}".format(index, slugify(query))
        json_path = raw_dir / (stem + ".json")
        md_path = raw_dir / (stem + ".md")
        if query in by_query and json_path.exists() and md_path.exists():
            by_query[query].update(
                inspect_pair(
                    json_path.read_text(encoding="utf-8"),
                    md_path.read_text(encoding="utf-8"),
                )
            )
    completed = {row["query"] for row in records}

    for index, query in enumerate(QUERIES[: args.limit], start=1):
        if query in completed:
            print("[{}/{}] already complete: {}".format(index, args.limit, query), flush=True)
            continue
        print("[{}/{}] fetching: {}".format(index, args.limit, query), flush=True)
        stem = "{:02d}-{}".format(index, slugify(query))
        json_path = raw_dir / (stem + ".json")
        md_path = raw_dir / (stem + ".md")

        if json_path.exists():
            json_text = json_path.read_text(encoding="utf-8")
        else:
            json_text, _ = fetch_serpapi(serpapi_client, query, "json")
            json_path.write_text(json_text, encoding="utf-8")

        if md_path.exists():
            markdown_text = md_path.read_text(encoding="utf-8")
        else:
            markdown_text, _ = fetch_serpapi(serpapi_client, query, "md")
            md_path.write_text(markdown_text, encoding="utf-8")

        print("[{}/{}] measuring DeepSeek tokens".format(index, args.limit), flush=True)
        json_tokens, json_usage = deepseek_prompt_tokens(deepseek_key, json_text)
        md_tokens, md_usage = deepseek_prompt_tokens(deepseek_key, markdown_text)
        (usage_dir / (stem + "-json.json")).write_text(
            json.dumps(json_usage.get("usage"), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (usage_dir / (stem + "-markdown.json")).write_text(
            json.dumps(md_usage.get("usage"), ensure_ascii=False, indent=2), encoding="utf-8"
        )

        checks = inspect_pair(json_text, markdown_text)
        record = {
            "query": query,
            "json_prompt_tokens": json_tokens,
            "markdown_prompt_tokens": md_tokens,
            "token_savings_pct": round(100 * (1 - md_tokens / json_tokens), 2),
            "json_bytes": len(json_text.encode("utf-8")),
            "markdown_bytes": len(markdown_text.encode("utf-8")),
            "byte_savings_pct": round(
                100 * (1 - len(markdown_text.encode("utf-8")) / len(json_text.encode("utf-8"))), 2
            ),
            **checks,
        }
        records.append(record)
        write_outputs(run_dir, records, metadata)
        print(
            "[{}/{}] {:.2f}% savings (JSON {:,}; MD {:,})".format(
                index, args.limit, record["token_savings_pct"], json_tokens, md_tokens
            ),
            flush=True,
        )

    write_outputs(run_dir, records, metadata)
    print("\nWrote results to {}".format(run_dir), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
