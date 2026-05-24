#!/usr/bin/env python3
"""Generate synthetic sample incidents for demos, tests, and documentation.

This utility creates realistic-looking incident records that mirror the schema
used by the incident tracking API. The generated data is intended for local
experimentation, onboarding exercises, and sample dashboards — not for use in
production analytics.

The script writes a Markdown preview by default so teams can inspect the sample
content in GitHub or their editor. It can also be extended to emit JSON or CSV
for direct import into fixtures, notebooks, or dbt seeds.

Usage:
    python generate_sample_incidents.py --count 10 --output sample_incidents.md

Design goals:
    - Produce varied but plausible incident narratives.
    - Cover multiple severities, categories, and root causes.
    - Keep the output deterministic when a seed is provided.
    - Make it easy to reuse the data in docs, tests, and dashboards.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List

SEVERITIES = ["SEV-1", "SEV-2", "SEV-3", "SEV-4"]
CATEGORIES = [
    "api",
    "data-quality",
    "model-drift",
    "cost-spike",
    "pipeline-failure",
    "security",
]
TEAMS = ["ml-platform", "data-eng", "sre", "security", "product-analytics"]
ROOT_CAUSES = [
    "schema drift in upstream feed",
    "unexpected traffic surge",
    "credential rotation missing one service",
    "resource exhaustion in job worker",
    "model confidence degradation after rollout",
    "feature store lag caused stale predictions",
]
REMEDIATIONS = [
    "rolled back to the previous stable deployment",
    "added a guardrail and alert threshold",
    "patched the pipeline and replayed affected data",
    "restored service capacity and validated recovery",
    "disabled the new feature flag and monitored error rates",
]


@dataclass(frozen=True)
class SampleIncident:
    """Representation of a synthetic incident record."""

    incident_id: str
    title: str
    severity: str
    category: str
    team: str
    created_at: str
    summary: str
    root_cause: str
    remediation: str


def build_incident(index: int, rng: random.Random) -> SampleIncident:
    """Build a single plausible incident record.

    Args:
        index: 1-based incident number used to create stable IDs.
        rng: Random number generator instance for deterministic output.

    Returns:
        A populated SampleIncident.
    """
    severity = rng.choices(SEVERITIES, weights=[15, 25, 35, 25], k=1)[0]
    category = rng.choice(CATEGORIES)
    team = rng.choice(TEAMS)
    root_cause = rng.choice(ROOT_CAUSES)
    remediation = rng.choice(REMEDIATIONS)

    created_at = datetime.now() - timedelta(days=rng.randint(1, 45), hours=rng.randint(0, 23))
    title = f"{severity} {category.replace('-', ' ').title()} incident"
    summary = (
        f"Detected an issue in the {category} domain affecting the {team} team. "
        f"Investigation identified {root_cause}. The team {remediation}."
    )

    return SampleIncident(
        incident_id=f"INC-{index:04d}",
        title=title,
        severity=severity,
        category=category,
        team=team,
        created_at=created_at.isoformat(timespec="seconds"),
        summary=summary,
        root_cause=root_cause,
        remediation=remediation,
    )


def render_markdown(incidents: Iterable[SampleIncident]) -> str:
    """Render incident records as a Markdown document.

    The output is intentionally human-readable so it can be pasted directly into
    onboarding materials or reviewed in GitHub without any extra tooling.
    """
    lines: List[str] = [
        "# Sample Incidents",
        "",
        "This document contains synthetic incidents for demo and testing use only.",
        "",
    ]
    for incident in incidents:
        lines.extend([
            f"## {incident.incident_id} — {incident.title}",
            "",
            f"- Severity: {incident.severity}",
            f"- Category: {incident.category}",
            f"- Team: {incident.team}",
            f"- Created At: {incident.created_at}",
            f"- Summary: {incident.summary}",
            f"- Root Cause: {incident.root_cause}",
            f"- Remediation: {incident.remediation}",
            "",
        ])
    return "\n".join(lines)


def main() -> None:
    """Entry point for generating sample incident content."""
    parser = argparse.ArgumentParser(description="Generate synthetic sample incidents")
    parser.add_argument("--count", type=int, default=5, help="Number of incidents to generate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("sample_incidents.md"),
        help="Output file path (Markdown by default)",
    )
    args = parser.parse_args()

    if args.count < 1:
        raise SystemExit("--count must be at least 1")

    rng = random.Random(args.seed)
    incidents = [build_incident(i + 1, rng) for i in range(args.count)]
    args.output.write_text(render_markdown(incidents), encoding="utf-8")
    print(f"Wrote {len(incidents)} sample incidents to {args.output}")


if __name__ == "__main__":
    main()
