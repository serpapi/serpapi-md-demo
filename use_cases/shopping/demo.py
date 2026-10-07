#!/usr/bin/env python3
"""Live Google Shopping demo: turn format savings into a larger SEO sample."""

import argparse
import json
import math
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import serpapi


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.runtime import DEEPSEEK_MODEL, DeepSeekClient, require_api_keys  # noqa: E402


DEFAULT_QUERY = "trail running shoes"
DEFAULT_LOCATION = "New York, New York, United States"
DEFAULT_BUDGET = 1550
DEFAULT_MAX_RESULTS = 30
PROMPT_PATH = Path(__file__).with_name("prompt.txt")
REFERENCE_DIR = Path(__file__).with_name("reference-data")
RESTRICTOR_FIELDS = [
    "shopping_results[].position",
    "shopping_results[].title",
    "shopping_results[].price",
    "shopping_results[].rating",
    "shopping_results[].reviews",
    "shopping_results[].source",
]
SYSTEM_PROMPT = (
    "Analyze only the supplied evidence. Return valid JSON and never infer causation "
    "from a single search snapshot."
)


def pause(enabled):
    if enabled:
        input("\nPress Enter to continue...")


def heading(text):
    print("\n" + "=" * 76)
    print(text)
    print("=" * 76)


def fetch_pair(client, query, location):
    params = {
        "engine": "google_shopping",
        "q": query,
        "location": location,
        "google_domain": "google.com",
        "gl": "us",
        "hl": "en",
        "device": "desktop",
        "json_restrictor": ",".join(RESTRICTOR_FIELDS),
    }
    json_response = client.request("GET", "/search", params={**params, "output": "json"})
    markdown_response = client.request("GET", "/search", params={**params, "output": "md"})
    return json_response.text, markdown_response.text


def save_pair(directory, json_text, markdown_text, metadata):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "serpapi.json").write_text(json_text, encoding="utf-8")
    (directory / "serpapi.md").write_text(markdown_text, encoding="utf-8")
    (directory / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_pair(directory):
    return (
        (directory / "serpapi.json").read_text(encoding="utf-8"),
        (directory / "serpapi.md").read_text(encoding="utf-8"),
    )


def markdown_table_parts(markdown_text):
    lines = markdown_text.splitlines()
    heading_index = next(
        (index for index, line in enumerate(lines) if line.strip() == "## Shopping Results"), None
    )
    if heading_index is None:
        raise RuntimeError("Markdown response did not contain a Shopping Results section")
    table_start = next(
        (index for index in range(heading_index + 1, len(lines)) if lines[index].startswith("|")),
        None,
    )
    if table_start is None or table_start + 1 >= len(lines):
        raise RuntimeError("Markdown response did not contain a Shopping Results table")
    header = lines[table_start]
    separator = lines[table_start + 1]
    rows = []
    for line in lines[table_start + 2 :]:
        if not line.startswith("|"):
            break
        rows.append(line)
    return header, separator, rows


def markdown_payload(markdown_text, count):
    header, separator, rows = markdown_table_parts(markdown_text)
    return "## Shopping Results\n\n" + "\n".join([header, separator] + rows[:count])


def json_payload(products, count):
    return json.dumps(
        {"shopping_results": products[:count]}, ensure_ascii=False, indent=2
    )


def normalized_title(value):
    return re.sub(r"\s+", " ", value.replace("\\|", "|")).strip().lower()


def validate_pair(products, markdown_text, limit):
    normalized_markdown = normalized_title(markdown_text)
    missing = [
        product.get("position")
        for product in products[:limit]
        if normalized_title(str(product.get("title", ""))) not in normalized_markdown
    ]
    if missing:
        raise RuntimeError("JSON/Markdown title mismatch at positions: {}".format(missing))


def parse_price(value):
    if value is None:
        return None
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value))
    return float(match.group(0).replace(",", "")) if match else None


def rank_values(values):
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        average_rank = (cursor + 1 + end) / 2
        for index in range(cursor, end):
            ranks[indexed[index][0]] = average_rank
        cursor = end
    return ranks


def pearson(xs, ys):
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
    )
    return numerator / denominator if denominator else None


def spearman(pairs):
    clean = [(float(x), float(y)) for x, y in pairs if x is not None and y is not None]
    if len(clean) < 3:
        return None
    xs, ys = zip(*clean)
    return pearson(rank_values(list(xs)), rank_values(list(ys)))


def median_or_none(values):
    clean = [value for value in values if value is not None]
    return statistics.median(clean) if clean else None


def round_or_none(value, digits=3):
    return round(value, digits) if value is not None else None


def mean_or_none(values):
    clean = [value for value in values if value is not None]
    return statistics.mean(clean) if clean else None


def compute_metrics(products, query):
    if not products:
        return {}
    positions = [int(product.get("position", index + 1)) for index, product in enumerate(products)]
    prices = [parse_price(product.get("price")) for product in products]
    reviews = [
        float(product["reviews"]) if product.get("reviews") is not None else None
        for product in products
    ]
    query_terms = [term for term in re.findall(r"[a-z0-9]+", query.lower()) if len(term) > 2]
    title_match = []
    for product in products:
        words = set(re.findall(r"[a-z0-9]+", str(product.get("title", "")).lower()))
        title_match.append(
            sum(term in words for term in query_terms) / len(query_terms) if query_terms else None
        )
    group_size = min(10, max(1, len(products) // 3))
    top_slice = slice(0, group_size)
    lower_slice = slice(max(0, len(products) - group_size), len(products))
    return {
        "sample_size": len(products),
        "rank_range": [min(positions), max(positions)],
        "group_size": group_size,
        "top_group": {
            "positions": [positions[top_slice.start], positions[top_slice.stop - 1]],
            "median_price": round_or_none(median_or_none(prices[top_slice]), 2),
            "median_reviews": round_or_none(median_or_none(reviews[top_slice]), 1),
            "mean_query_term_coverage": round_or_none(mean_or_none(title_match[top_slice])),
        },
        "lower_group": {
            "positions": [positions[lower_slice.start], positions[lower_slice.stop - 1]],
            "median_price": round_or_none(median_or_none(prices[lower_slice]), 2),
            "median_reviews": round_or_none(median_or_none(reviews[lower_slice]), 1),
            "mean_query_term_coverage": round_or_none(mean_or_none(title_match[lower_slice])),
        },
        "spearman_position_vs_price": round_or_none(spearman(zip(positions, prices))),
        "spearman_position_vs_reviews": round_or_none(spearman(zip(positions, reviews))),
        "missing_price_count": sum(value is None for value in prices),
        "missing_reviews_count": sum(value is None for value in reviews),
    }


def build_messages(query, location, products, payload):
    metrics = compute_metrics(products, query)
    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    user_prompt = prompt_template.format(
        query=query,
        location=location,
        sample_size=len(products),
        metrics=json.dumps(metrics, ensure_ascii=False, indent=2),
        payload=payload,
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def fit_to_budget(deepseek, query, location, products, payload_builder, budget):
    cache = {}

    def measure(count):
        if count not in cache:
            payload = payload_builder(count)
            messages = build_messages(query, location, products[:count], payload)
            tokens, usage = deepseek.measure_prompt_tokens(messages)
            cache[count] = {"tokens": tokens, "usage": usage}
        return cache[count]["tokens"]

    maximum = len(products)
    if maximum == 0:
        return 0, 0, cache
    if measure(maximum) <= budget:
        return maximum, measure(maximum), cache
    low, high = 0, maximum
    while low < high:
        middle = (low + high + 1) // 2
        if measure(middle) <= budget:
            low = middle
        else:
            high = middle - 1
    return low, measure(low) if low else 0, cache


def render_metrics(label, count, tokens, budget, metrics):
    print("{:<10} {:>2} products  {:>5,} / {:,} input tokens".format(label, count, tokens, budget))
    rank_range = metrics.get("rank_range")
    if rank_range:
        print("           rank coverage {}–{}".format(*rank_range))
    top = metrics.get("top_group") or {}
    lower = metrics.get("lower_group") or {}
    if top and lower:
        print(
            "           median reviews: top {} | lower {}".format(
                top.get("median_reviews"), lower.get("median_reviews")
            )
        )


def render_analysis(analysis):
    if not analysis:
        return
    print("\nHeadline: {}".format(analysis.get("headline", "(no headline)")))
    for finding in analysis.get("findings", []):
        print("- {} [positions: {}]".format(
            finding.get("claim", ""), finding.get("evidence_positions", [])
        ))
        if finding.get("caveat"):
            print("  Caveat: {}".format(finding["caveat"]))
        if finding.get("marketing_implication"):
            print("  Marketing implication: {}".format(finding["marketing_implication"]))
    patterns = analysis.get("title_patterns") or {}
    if any(patterns.get(group) for group in ("top_group", "lower_group")):
        print("\nTitle and intent patterns")
        for group in ("top_group", "lower_group"):
            for pattern in patterns.get(group, []):
                if isinstance(pattern, str):
                    print("- {}: {}".format(group, pattern))
                else:
                    print("- {}: {} [positions: {}]".format(
                        group,
                        pattern.get("pattern", ""),
                        pattern.get("evidence_positions", []),
                    ))
                    if pattern.get("marketing_implication"):
                        print("  {}".format(pattern["marketing_implication"]))
    if analysis.get("intent_segments"):
        print("\nSearch-intent segments")
        for segment in analysis["intent_segments"]:
            print("- {} [positions: {}]".format(
                segment.get("segment", ""), segment.get("evidence_positions", [])
            ))
            if segment.get("marketing_implication"):
                print("  {}".format(segment["marketing_implication"]))
    if analysis.get("actions"):
        print("\nSuggested actions")
        for action in analysis["actions"]:
            print("- {} [positions: {}]".format(
                action.get("action", ""), action.get("evidence_positions", [])
            ))
            if action.get("rationale"):
                print("  {}".format(action["rationale"]))
            if action.get("experiment"):
                print("  Experiment: {}".format(action["experiment"]))
            if action.get("primary_kpi"):
                print("  KPI: {} | Guardrail: {}".format(
                    action["primary_kpi"], action.get("guardrail_kpi", "n/a")
                ))
    if analysis.get("limitations"):
        print("\nLimitations")
        for limitation in analysis["limitations"]:
            print("- {}".format(limitation))
    audit = analysis.get("evidence_audit") or {}
    rejected = sum(
        value
        for key, value in audit.items()
        if key.startswith("rejected_") and isinstance(value, int)
    )
    if rejected:
        print("\nEvidence audit: rejected {} ungrounded model item(s).".format(rejected))
        for reason in audit.get("rejected_action_reasons", []):
            print("- {}".format(reason))


def sanitize_analysis(analysis, products, metrics):
    """Enforce stage-safe evidence and action constraints on model output."""
    if not isinstance(analysis, dict):
        return {"raw_response": str(analysis)}
    valid_positions = {
        int(product.get("position")) for product in products if product.get("position") is not None
    }
    top_range = (metrics.get("top_group") or {}).get("positions") or []
    lower_range = (metrics.get("lower_group") or {}).get("positions") or []
    audit = {
        "rejected_findings": 0,
        "rejected_title_patterns": 0,
        "rejected_intent_segments": 0,
        "rejected_actions": 0,
        "rejected_action_reasons": [],
    }
    for section in ("findings", "intent_segments"):
        valid_items = []
        for item in analysis.get(section, []):
            evidence = item.get("evidence_positions") or []
            item["evidence_positions"] = [
                int(position)
                for position in evidence
                if str(position).isdigit() and int(position) in valid_positions
            ][:5]
            evidence_is_valid = bool(item["evidence_positions"])
            if section == "findings" and len(top_range) == 2 and len(lower_range) == 2:
                has_top = any(top_range[0] <= p <= top_range[1] for p in item["evidence_positions"])
                has_lower = any(
                    lower_range[0] <= p <= lower_range[1] for p in item["evidence_positions"]
                )
                evidence_is_valid = evidence_is_valid and has_top and has_lower
            if evidence_is_valid:
                valid_items.append(item)
            else:
                audit["rejected_" + section] += 1
        analysis[section] = valid_items[:4]

    clean_patterns = {"top_group": [], "lower_group": []}
    patterns = analysis.get("title_patterns") or {}
    for group in ("top_group", "lower_group"):
        group_range = top_range if group == "top_group" else lower_range
        for item in patterns.get(group, []):
            if not isinstance(item, dict):
                audit["rejected_title_patterns"] += 1
                continue
            evidence = [
                int(position)
                for position in item.get("evidence_positions") or []
                if str(position).isdigit() and int(position) in valid_positions
            ][:5]
            if len(group_range) == 2:
                evidence = [p for p in evidence if group_range[0] <= p <= group_range[1]]
            if evidence:
                item["evidence_positions"] = evidence
                clean_patterns[group].append(item)
            else:
                audit["rejected_title_patterns"] += 1
    analysis["title_patterns"] = clean_patterns

    if len(lower_range) == 2:
        lower_start, lower_end = lower_range
        valid_actions = []
        for action in analysis.get("actions", []):
            evidence = action.get("evidence_positions") or []
            valid_evidence = [
                int(position)
                for position in evidence
                if str(position).isdigit() and int(position) in valid_positions
            ][:5]
            lower_evidence = [
                position for position in valid_evidence if lower_start <= position <= lower_end
            ]
            unsupported_reason = unsupported_action_reason(action, metrics)
            if lower_evidence and not unsupported_reason:
                action["evidence_positions"] = valid_evidence
                valid_actions.append(action)
            else:
                audit["rejected_actions"] += 1
                if unsupported_reason:
                    audit["rejected_action_reasons"].append(unsupported_reason)
        analysis["actions"] = valid_actions[:4]
    limitations = analysis.get("limitations") or []
    if isinstance(limitations, str):
        limitations = [limitations]
    analysis["limitations"] = [str(item) for item in limitations][:6]
    analysis["evidence_audit"] = audit
    return analysis


def unsupported_action_reason(action, metrics):
    """Reject recommendations that contradict deterministic snapshot metrics."""
    text = " ".join(
        str(action.get(field, ""))
        for field in ("action", "hypothesis", "experiment", "rationale")
    ).lower()
    top = metrics.get("top_group") or {}
    lower = metrics.get("lower_group") or {}
    top_coverage = top.get("mean_query_term_coverage")
    lower_coverage = lower.get("mean_query_term_coverage")
    keyword_push = any(
        phrase in text
        for phrase in (
            "add exact",
            "adding exact",
            "include exact",
            "exact query term",
            "increase query-term coverage",
            "increase keyword coverage",
        )
    )
    if (
        keyword_push
        and None not in (top_coverage, lower_coverage)
        and lower_coverage >= top_coverage
    ):
        return "exact-query expansion contradicted query-coverage metrics"

    review_correlation = metrics.get("spearman_position_vs_reviews")
    review_push = "review" in text and any(
        phrase in text
        for phrase in (
            "acquir",
            "accumulat",
            "gather",
            "generat",
            "increase",
            "boost",
            "request",
        )
    )
    if review_push and review_correlation is not None and abs(review_correlation) < 0.4:
        return "review acquisition was not supported by rank correlation"

    if "missing review" in text and any(
        phrase in text for phrase in ("feed", "complete", "fill", "update")
    ):
        return "missing SerpApi review evidence was treated as a broken product feed"

    price_correlation = metrics.get("spearman_position_vs_price")
    price_push = any(
        phrase in text
        for phrase in (
            "change price",
            "lower price",
            "reduce price",
            "price reduction",
            "pricing strategy",
            "discount",
            "promotion",
        )
    )
    if price_push and price_correlation is not None and abs(price_correlation) < 0.4:
        return "price change was not supported by rank correlation"
    return None


def cited_positions(analysis):
    positions = set()
    if not isinstance(analysis, dict):
        return positions
    for section in ("findings", "intent_segments", "actions"):
        for item in analysis.get(section, []):
            for position in item.get("evidence_positions") or []:
                if str(position).isdigit():
                    positions.add(int(position))
    for group in ("top_group", "lower_group"):
        for item in (analysis.get("title_patterns") or {}).get(group, []):
            if isinstance(item, dict):
                for position in item.get("evidence_positions") or []:
                    if str(position).isdigit():
                        positions.add(int(position))
    return positions


def compare_analyses(
    json_analysis,
    markdown_analysis,
    json_products,
    markdown_products,
    json_metrics,
    markdown_metrics,
):
    """Measure evidence and strategy unlocked by the larger Markdown sample."""
    json_positions = {
        int(item["position"]) for item in json_products if item.get("position") is not None
    }
    markdown_positions = {
        int(item["position"]) for item in markdown_products if item.get("position") is not None
    }
    newly_visible = sorted(markdown_positions - json_positions)

    def uses_new_evidence(item):
        return bool(set(item.get("evidence_positions") or []) & set(newly_visible))

    md_analysis = markdown_analysis if isinstance(markdown_analysis, dict) else {}
    json_top = (json_metrics.get("top_group") or {}).get("mean_query_term_coverage")
    json_lower = (json_metrics.get("lower_group") or {}).get("mean_query_term_coverage")
    md_top = (markdown_metrics.get("top_group") or {}).get("mean_query_term_coverage")
    md_lower = (markdown_metrics.get("lower_group") or {}).get("mean_query_term_coverage")
    json_gap = round(json_top - json_lower, 3) if None not in (json_top, json_lower) else None
    md_gap = round(md_top - md_lower, 3) if None not in (md_top, md_lower) else None
    direction_changed = (
        json_gap is not None
        and md_gap is not None
        and json_gap != 0
        and md_gap != 0
        and (json_gap > 0) != (md_gap > 0)
    )
    markdown_only_findings = [
        item for item in md_analysis.get("findings", []) if uses_new_evidence(item)
    ]
    markdown_only_segments = [
        item for item in md_analysis.get("intent_segments", []) if uses_new_evidence(item)
    ]
    markdown_only_actions = [
        item for item in md_analysis.get("actions", []) if uses_new_evidence(item)
    ]
    json_review_rho = json_metrics.get("spearman_position_vs_reviews")
    md_review_rho = markdown_metrics.get("spearman_position_vs_reviews")
    md_price_rho = markdown_metrics.get("spearman_position_vs_price")
    md_lower_range = (markdown_metrics.get("lower_group") or {}).get("positions") or []
    md_lower_median_reviews = (markdown_metrics.get("lower_group") or {}).get("median_reviews")
    review_counterexamples = []
    if len(md_lower_range) == 2 and md_lower_median_reviews is not None:
        for product in markdown_products:
            position = product.get("position")
            reviews = product.get("reviews")
            if (
                position is not None
                and md_lower_range[0] <= int(position) <= md_lower_range[1]
                and reviews is not None
                and float(reviews) >= 2 * md_lower_median_reviews
            ):
                review_counterexamples.append(
                    {"position": int(position), "reviews": int(reviews), "title": product.get("title")}
                )
    priorities = []
    if (
        json_review_rho is not None
        and md_review_rho is not None
        and abs(json_review_rho) >= 0.4
        and abs(md_review_rho) < 0.4
    ):
        priorities.append(
            "The review signal drops from moderate in JSON's truncated sample to weak "
            "in the full Markdown sample; do not launch a blanket review-acquisition strategy."
        )
    if md_top is not None and md_lower is not None and md_lower >= md_top:
        priorities.append(
            "Lower-ranked titles already match at least as many query terms; test semantic "
            "intent and click-through clarity instead of keyword stuffing."
        )
    if md_price_rho is not None and abs(md_price_rho) < 0.2:
        priorities.append(
            "Use price as a conversion, margin, or ROAS lever—not as a ranking explanation."
        )
    if review_counterexamples:
        priorities.append(
            "Audit offer, feed, merchant, bid, and intent factors for high-review products "
            "that still appear in the lower group."
        )
    return {
        "additional_products": len(markdown_products) - len(json_products),
        "sample_multiplier": round(len(markdown_products) / len(json_products), 2)
        if json_products
        else None,
        "newly_visible_positions": newly_visible,
        "comparison_scope": {
            "json": {
                "top_group": (json_metrics.get("top_group") or {}).get("positions"),
                "lower_group": (json_metrics.get("lower_group") or {}).get("positions"),
            },
            "markdown": {
                "top_group": (markdown_metrics.get("top_group") or {}).get("positions"),
                "lower_group": (markdown_metrics.get("lower_group") or {}).get("positions"),
            },
        },
        "query_term_coverage_gap": {
            "json_top_minus_lower": json_gap,
            "markdown_top_minus_lower": md_gap,
            "direction_changed": direction_changed,
        },
        "evidence_breadth": {
            "json_unique_positions_cited": len(cited_positions(json_analysis)),
            "markdown_unique_positions_cited": len(cited_positions(markdown_analysis)),
        },
        "decision_shift": {
            "json_review_spearman": json_review_rho,
            "markdown_review_spearman": md_review_rho,
            "markdown_price_spearman": md_price_rho,
            "review_counterexamples_in_markdown_lower_group": review_counterexamples,
            "recommended_priorities": priorities,
        },
        "markdown_only_findings": markdown_only_findings,
        "markdown_only_intent_segments": markdown_only_segments,
        "markdown_only_actions": markdown_only_actions,
    }


def render_marketing_delta(delta):
    heading("WHAT MARKDOWN ADDED TO THE MARKETING DECISION")
    print(
        "Markdown exposed {} additional products at positions {}.".format(
            delta.get("additional_products", 0), delta.get("newly_visible_positions", [])
        )
    )
    scope = delta.get("comparison_scope") or {}
    print("JSON comparison groups: {}".format(scope.get("json")))
    print("Markdown comparison groups: {}".format(scope.get("markdown")))
    gap = delta.get("query_term_coverage_gap") or {}
    print(
        "Query-term coverage gap (top minus lower): JSON {} | Markdown {}".format(
            gap.get("json_top_minus_lower"), gap.get("markdown_top_minus_lower")
        )
    )
    if gap.get("direction_changed"):
        print("The larger sample changed the direction of this observed signal.")
    breadth = delta.get("evidence_breadth") or {}
    print(
        "Unique positions cited by the reports: JSON {} | Markdown {}".format(
            breadth.get("json_unique_positions_cited", 0),
            breadth.get("markdown_unique_positions_cited", 0),
        )
    )
    decision_shift = delta.get("decision_shift") or {}
    if decision_shift.get("recommended_priorities"):
        print("\nStrategy shift from the complete sample")
        for priority in decision_shift["recommended_priorities"]:
            print("- {}".format(priority))
    counterexamples = decision_shift.get("review_counterexamples_in_markdown_lower_group") or []
    if counterexamples:
        print("\nHigh-review counterexamples only visible in the deeper sample")
        for product in counterexamples:
            print("- Position {}: {} reviews — {}".format(
                product["position"], product["reviews"], product.get("title", "")
            ))
    if delta.get("markdown_only_findings"):
        print("\nFindings supported by products JSON could not see")
        for item in delta["markdown_only_findings"]:
            print("- {} [positions: {}]".format(
                item.get("claim", ""), item.get("evidence_positions", [])
            ))
    if delta.get("markdown_only_actions"):
        print("\nMarketing tests supported by products JSON could not see")
        for item in delta["markdown_only_actions"]:
            print("- {} [positions: {}]".format(
                item.get("action", ""), item.get("evidence_positions", [])
            ))
            if item.get("primary_kpi"):
                print("  KPI: {} | Guardrail: {}".format(
                    item["primary_kpi"], item.get("guardrail_kpi", "n/a")
                ))


def replay_products(report_path):
    candidates = [
        report_path.parent / "source" / "serpapi.json",
        report_path.parent / "serpapi.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            parsed = json.loads(candidate.read_text(encoding="utf-8"))
            return parsed.get("shopping_results") or []
    return []


def render_replay(report, products=None):
    products = products or []
    analyses = report.get("analyses")
    if analyses and products:
        capacity = report.get("capacity") or {}
        json_item = capacity.get("json") or {}
        md_item = capacity.get("markdown") or {}
        json_count = int(json_item.get("count") or 0)
        md_count = int(md_item.get("count") or 0)
        analyses["json"] = sanitize_analysis(
            analyses.get("json"), products[:json_count], json_item.get("metrics") or {}
        )
        analyses["markdown"] = sanitize_analysis(
            analyses.get("markdown"), products[:md_count], md_item.get("metrics") or {}
        )
        report["marketing_delta"] = compare_analyses(
            analyses.get("json"),
            analyses.get("markdown"),
            products[:json_count],
            products[:md_count],
            json_item.get("metrics") or {},
            md_item.get("metrics") or {},
        )
    heading("FIXED TOKEN BUDGET")
    print("Query: {}".format(report["query"]))
    print("Location: {}".format(report["location"]))
    print("Budget: {:,} DeepSeek input tokens\n".format(report["budget_tokens"]))
    for label in ("json", "markdown"):
        item = report["capacity"][label]
        render_metrics(label.upper(), item["count"], item["tokens"], report["budget_tokens"], item["metrics"])
    if analyses:
        heading("JSON-BUDGET SEO ANALYSIS")
        render_analysis(analyses.get("json"))
        heading("MARKDOWN-BUDGET SEO ANALYSIS")
        render_analysis(analyses.get("markdown"))
        render_marketing_delta(report.get("marketing_delta") or {})
    else:
        heading("PYTHON + DEEPSEEK SEO ANALYSIS (LEGACY REPORT)")
        render_analysis(report.get("analysis"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS)
    parser.add_argument("--source", choices=["live", "fixture"], default="live")
    parser.add_argument("--save-reference", action="store_true")
    parser.add_argument("--no-analysis", action="store_true")
    parser.add_argument("--pause", action="store_true")
    parser.add_argument("--replay-report", type=Path)
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()

    if args.replay_report:
        report_path = args.replay_report.resolve()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        render_replay(report, replay_products(report_path))
        return 0
    if args.budget <= 0 or args.max_results <= 0:
        parser.error("--budget and --max-results must be positive")

    serpapi_key, deepseek_key = require_api_keys()
    serpapi_client = serpapi.Client(api_key=serpapi_key, timeout=180)
    deepseek = DeepSeekClient(deepseek_key)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = (args.run_dir or PROJECT_ROOT / "demo-runs" / timestamp).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    heading("GOOGLE SHOPPING SNAPSHOT")
    print("Query: {}".format(args.query))
    print("Market: {}".format(args.location))
    print("SEO fields: position, title, price, rating, reviews, source")
    source_used = args.source
    if args.source == "live":
        print("Fetching paired JSON and Markdown responses...")
        try:
            json_text, markdown_text = fetch_pair(serpapi_client, args.query, args.location)
        except Exception as exc:
            if (
                args.query == DEFAULT_QUERY
                and args.location == DEFAULT_LOCATION
                and (REFERENCE_DIR / "serpapi.json").exists()
            ):
                print("Live search unavailable ({}). Using verified reference data.".format(exc))
                json_text, markdown_text = load_pair(REFERENCE_DIR)
                source_used = "fixture-fallback"
            else:
                raise
    else:
        print("Loading verified reference responses...")
        json_text, markdown_text = load_pair(REFERENCE_DIR)
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "query": args.query,
        "location": args.location,
        "source": source_used,
        "restrictor_fields": RESTRICTOR_FIELDS,
    }
    save_pair(run_dir / "source", json_text, markdown_text, metadata)
    if args.save_reference:
        save_pair(REFERENCE_DIR, json_text, markdown_text, metadata)

    parsed = json.loads(json_text)
    products = (parsed.get("shopping_results") or [])[: args.max_results]
    if not products:
        raise RuntimeError("SerpApi response did not contain shopping_results")
    validate_pair(products, markdown_text, len(products))
    print("Validated {} matching product titles across both formats.".format(len(products)))
    pause(args.pause)

    heading("SAME MODEL · SAME PROMPT · SAME TOKEN BUDGET")
    print("DeepSeek model: {} (non-thinking)".format(DEEPSEEK_MODEL))
    print("Total input budget: {:,} tokens".format(args.budget))
    print("Measuring complete prompts with DeepSeek usage.prompt_tokens...")

    json_builder = lambda count: json_payload(products, count)
    markdown_builder = lambda count: markdown_payload(markdown_text, count)
    try:
        json_count, json_tokens, _ = fit_to_budget(
            deepseek, args.query, args.location, products, json_builder, args.budget
        )
        md_count, md_tokens, _ = fit_to_budget(
            deepseek, args.query, args.location, products, markdown_builder, args.budget
        )
    except Exception as exc:
        reference_report = REFERENCE_DIR / "reference-report.json"
        if (
            args.query == DEFAULT_QUERY
            and args.location == DEFAULT_LOCATION
            and args.budget == DEFAULT_BUDGET
            and reference_report.exists()
        ):
            print("DeepSeek unavailable ({}). Replaying the verified report.".format(exc))
            render_replay(
                json.loads(reference_report.read_text(encoding="utf-8")), products
            )
            return 0
        raise
    json_metrics = compute_metrics(products[:json_count], args.query)
    md_metrics = compute_metrics(products[:md_count], args.query)
    print()
    render_metrics("JSON", json_count, json_tokens, args.budget, json_metrics)
    render_metrics("MARKDOWN", md_count, md_tokens, args.budget, md_metrics)
    if json_count:
        print("\nMarkdown fits {:.2f}× as many products in this run.".format(md_count / json_count))
    pause(args.pause)

    analyses = {"json": None, "markdown": None}
    analysis_usage = {"json": None, "markdown": None}
    if not args.no_analysis and json_count and md_count:
        heading("JSON-BUDGET SEO ANALYSIS")
        print("DeepSeek can inspect ranks 1–{} in JSON.".format(json_count))
        json_messages = build_messages(
            args.query,
            args.location,
            products[:json_count],
            json_builder(json_count),
        )
        analyses["json"], analysis_usage["json"] = deepseek.analyze_json(json_messages)
        analyses["json"] = sanitize_analysis(
            analyses["json"], products[:json_count], json_metrics
        )
        render_analysis(analyses["json"])
        pause(args.pause)

        heading("MARKDOWN-BUDGET SEO ANALYSIS")
        print("DeepSeek can inspect ranks 1–{} in Markdown.".format(md_count))
        md_messages = build_messages(
            args.query,
            args.location,
            products[:md_count],
            markdown_builder(md_count),
        )
        analyses["markdown"], analysis_usage["markdown"] = deepseek.analyze_json(md_messages)
        analyses["markdown"] = sanitize_analysis(
            analyses["markdown"], products[:md_count], md_metrics
        )
        render_analysis(analyses["markdown"])

    marketing_delta = compare_analyses(
        analyses["json"],
        analyses["markdown"],
        products[:json_count],
        products[:md_count],
        json_metrics,
        md_metrics,
    )
    render_marketing_delta(marketing_delta)

    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "query": args.query,
        "location": args.location,
        "source": source_used,
        "budget_tokens": args.budget,
        "deepseek_model": DEEPSEEK_MODEL,
        "capacity": {
            "json": {
                "count": json_count,
                "tokens": json_tokens,
                "metrics": json_metrics,
            },
            "markdown": {
                "count": md_count,
                "tokens": md_tokens,
                "metrics": md_metrics,
            },
        },
        "analyses": analyses,
        "analysis_usage": analysis_usage,
        "marketing_delta": marketing_delta,
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.save_reference:
        (REFERENCE_DIR / "reference-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print("\nSaved auditable run: {}".format(report_path))
    print("Replay without network: {} {} --replay-report {}".format(
        sys.executable, Path(__file__).resolve(), report_path
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
