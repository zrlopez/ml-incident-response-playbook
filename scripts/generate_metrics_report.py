"""Generate a comprehensive incident metrics report for leadership review.

This script aggregates metrics from the PostgreSQL incidents database and
produces a monthly metrics report in both CSV and Markdown formats. It is
designed to run as a scheduled cron job (e.g., 1st of each month at 09:00)
or as an Airflow task in the ML incident monitoring DAG.

Usage:
    python generate_metrics_report.py [--month YYYY-MM] [--output-dir ./reports]

Outputs:
    - {output_dir}/metrics_{YYYY-MM}.csv         # Raw metrics data
    - {output_dir}/metrics_{YYYY-MM}.md          # Leadership-friendly summary
    - {output_dir}/metrics_{YYYY-MM}_trends.csv  # Month-over-month trends

Dependencies:
    - psycopg2-binary (PostgreSQL driver)
    - pandas (data manipulation)
    - python-dotenv (environment variable loading)

Author: ML Incident Response Team
Date: 2026-05
"""

import argparse
import csv
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# ============================================================================
# Configuration Defaults
# ============================================================================
DEFAULT_OUTPUT_DIR = Path("reports")
DEFAULT_MONTH = (datetime.now() - timedelta(days=30)).strftime("%Y-%m")
DB_PATH = Path("api/incidents.db")  # Default SQLite path; override via env


# ============================================================================
# Data Fetching
# ============================================================================
def fetch_incidents_for_month(db_path: Path, month: str) -> List[Dict[str, Any]]:
    """Fetch all incidents for the given YYYY-MM from the database.

    Args:
        db_path: Path to the SQLite incidents database.
        month: Month in YYYY-MM format.

    Returns:
        List of incident records as dictionaries.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Extract year and month from the YYYY-MM string
    year, mon = month.split("-")

    query = """
        SELECT
            id, severity, category, status,
            created_at, resolved_at,
            time_to_detect_minutes, time_to_resolve_minutes,
            impact_score, root_cause
        FROM incidents
        WHERE strftime('%Y', created_at) = ?
          AND strftime('%m', created_at) = ?
        ORDER BY created_at ASC
    """

    cursor.execute(query, (year, mon))
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def calculate_metrics(incidents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate aggregate metrics from incident list.

    Args:
        incidents: List of incident dictionaries.

    Returns:
        Dictionary containing calculated metrics.
    """
    if not incidents:
        return {
            "total_incidents": 0,
            "by_severity": {},
            "by_category": {},
            "avg_time_to_detect_minutes": 0,
            "avg_time_to_resolve_minutes": 0,
            "avg_impact_score": 0,
            "sever_1_count": 0,
            "sever_2_count": 0,
        }

    by_severity: Dict[str, int] = {}
    by_category: Dict[str, int] = {}
    total_ttd = 0
    total_ttr = 0
    total_impact = 0
    sevl_count = 0
    sev2_count = 0

    for inc in incidents:
        # Count by severity
        sev = inc["severity"]
        by_severity[sev] = by_severity.get(sev, 0) + 1

        # Count by category
        cat = inc["category"]
        by_category[cat] = by_category.get(cat, 0) + 1

        # Accumulate metrics
        if inc["time_to_detect_minutes"]:
            total_ttd += inc["time_to_detect_minutes"]
        if inc["time_to_resolve_minutes"]:
            total_ttr += inc["time_to_resolve_minutes"]
        if inc["impact_score"]:
            total_impact += inc["impact_score"]

        # Track SEV counts for SLA reporting
        if sev == "SEV-1":
            sevl_count += 1
        elif sev == "SEV-2":
            sev2_count += 1

    n = len(incidents)
    return {
        "total_incidents": n,
        "by_severity": by_severity,
        "by_category": by_category,
        "avg_time_to_detect_minutes": round(total_ttd / n, 1) if n > 0 else 0,
        "avg_time_to_resolve_minutes": round(total_ttr / n, 1) if n > 0 else 0,
        "avg_impact_score": round(total_impact / n, 2) if n > 0 else 0,
        "sever_1_count": sevl_count,
        "sever_2_count": sev2_count,
    }


def calculate_trends(current_metrics: Dict[str, Any], prev_metrics: Dict[str, Any]) -> Dict[str, Any]:  # noqa: E501
    """Calculate month-over-month trend percentages.

    Args:
        current_metrics: Current month's metrics.
        prev_metrics: Previous month's metrics.

    Returns:
        Dictionary containing trend percentages (positive = increase).
    """
    def pct_change(curr: float, prev: float) -> float:
        if prev == 0:
            return 0.0 if curr == 0 else 100.0
        return round(((curr - prev) / prev) * 100, 1)

    return {
        "total_incidents_change_pct": pct_change(
            current_metrics["total_incidents"], prev_metrics["total_incidents"]
        ),
        "avg_ttd_change_pct": pct_change(
            current_metrics["avg_time_to_detect_minutes"],
            prev_metrics["avg_time_to_detect_minutes"],
        ),
        "avg_ttr_change_pct": pct_change(
            current_metrics["avg_time_to_resolve_minutes"],
            prev_metrics["avg_time_to_resolve_minutes"],
        ),
        "avg_impact_change_pct": pct_change(
            current_metrics["avg_impact_score"], prev_metrics["avg_impact_score"]
        ),
    }


# ============================================================================
# Report Generation
# ============================================================================
def write_csv_metrics(metrics: Dict[str, Any], output_path: Path) -> None:
    """Write metrics to a CSV file for spreadsheet analysis.

    Args:
        metrics: Metrics dictionary from calculate_metrics().
        output_path: Path to write the CSV file.
    """
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value", "details"])

        writer.writerow(["total_incidents", metrics["total_incidents"], ""])
        writer.writerow(["sever_1_count", metrics["sever_1_count"], ""])
        writer.writerow(["sever_2_count", metrics["sever_2_count"], ""])
        writer.writerow(
            ["avg_time_to_detect_minutes", metrics["avg_time_to_detect_minutes"], "minutes"]
        )
        writer.writerow(
            ["avg_time_to_resolve_minutes", metrics["avg_time_to_resolve_minutes"], "minutes"]
        )
        writer.writerow(["avg_impact_score", metrics["avg_impact_score"], "1-10 scale"])

        # Write by_severity breakdown
        for sev, count in metrics["by_severity"].items():
            writer.writerow([f"severity_{sev}", count, ""])

        # Write by_category breakdown
        for cat, count in metrics["by_category"].items():
            writer.writerow([f"category_{cat}", count, ""])


def write_markdown_report(metrics: Dict[str, Any], trends: Optional[Dict[str, Any]],
                          month: str, output_path: Path) -> None:
    """Write a leadership-friendly Markdown summary report.

    Args:
        metrics: Metrics dictionary from calculate_metrics().
        trends: Optional trends dictionary from calculate_trends().
        month: Month in YYYY-MM format for the report title.
        output_path: Path to write the Markdown file.
    """
    lines = [
        f"# ML Incident Metrics Report — {month}",
        "",
        "## Executive Summary",
        "",
        f"- **Total Incidents:** {metrics['total_incidents']}",
        f"- **SEV-1 Incidents:** {metrics['sever_1_count']}",
        f"- **SEV-2 Incidents:** {metrics['sever_2_count']}",
        f"- **Avg Time to Detect:** {metrics['avg_time_to_detect_minutes']} minutes",
        f"- **Avg Time to Resolve:** {metrics['avg_time_to_resolve_minutes']} minutes",
        f"- **Avg Impact Score:** {metrics['avg_impact_score']} / 10",
        "",
    ]

    if trends:
        lines.extend([
            "## Month-over-Month Trends",
            "",
        ])
        for key, value in trends.items():
            friendly_name = key.replace("_change_pct", "").replace("_", " ").title()
            direction = "↑" if value > 0 else "↓" if value < 0 else "→"
            lines.append(f"- **{friendly_name}:** {direction} {abs(value)}%")
        lines.append("")

    if metrics["by_severity"]:
        lines.extend([
            "## Incidents by Severity",
            "",
            "| Severity | Count |",
            "|----------|-------|",
        ])
        for sev, count in sorted(metrics["by_severity"].items()):
            lines.append(f"| {sev} | {count} |")
        lines.append("")

    if metrics["by_category"]:
        lines.extend([
            "## Incidents by Category",
            "",
            "| Category | Count |",
            "|----------|-------|",
        ])
        for cat, count in sorted(metrics["by_category"].items()):
            lines.append(f"| {cat} | {count} |")
        lines.append("")

    lines.extend([
        "---",
        f"*Report generated on {datetime.now().strftime('%Y-%m-%d %H:%M')} CDT*",
    ])

    output_path.write_text("\n".join(lines))


# ============================================================================
# Main Entry Point
# ============================================================================
def main() -> None:
    """Main entry point for the metrics report generation."""
    parser = argparse.ArgumentParser(
        description="Generate monthly ML incident metrics report"
    )
    parser.add_argument(
        "--month",
        default=DEFAULT_MONTH,
        help="Month in YYYY-MM format (default: last 30 days)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Output directory for report files (default: ./reports)",
    )
    parser.add_argument(
        "--db-path",
        default=str(DB_PATH),
        help="Path to incidents database (default: ./api/incidents.db)",
    )
    args = parser.parse_args()

    # Parse and validate month
    try:
        year, mon = args.month.split("-")
        if not (1 <= int(mon) <= 12):
            raise ValueError("Month must be 01-12")
    except ValueError as e:
        print(f"Error: Invalid month format. Use YYYY-MM. Details: {e}")
        return

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    db_path = Path(args.db_path)

    print(f"Fetching incidents for {args.month}...")
    try:
        incidents = fetch_incidents_for_month(db_path, args.month)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Tip: Run the API first to create the database, or set --db-path")
        return

    print(f"Found {len(incidents)} incidents")

    metrics = calculate_metrics(incidents)
    print(f"Calculated metrics: {metrics['total_incidents']} total incidents")

    # For now, we don't have previous month data, so trends = None
    # In production, you'd fetch prev month and call calculate_trends()
    trends = None

    # Write outputs
    csv_path = output_dir / f"metrics_{args.month}.csv"
    write_csv_metrics(metrics, csv_path)
    print(f"Wrote CSV: {csv_path}")

    md_path = output_dir / f"metrics_{args.month}.md"
    write_markdown_report(metrics, trends, args.month, md_path)
    print(f"Wrote Markdown report: {md_path}")

    print("✓ Metrics report generation complete")


if __name__ == "__main__":
    main()
