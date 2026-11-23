#!/usr/bin/env python3
"""
Resilient TimescaleDB Scenario Harvester for InfraPlay.

Handles corrupted PostgreSQL disk blocks (e.g. 'invalid page in block 160 of
relation base/17864/57226') on 10.102.0.70:5432/tsdb by querying tasks and
telemetry tables in isolated transactions per task, falling back gracefully
whenever a specific table/chunk hits a damaged page.
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

import psycopg2
import psycopg2.extras

from infraplay.harvester import ScenarioHarvester
from infraplay.models import SCENARIOS_DIR
from infraplay.seed_scenarios import ensure_seed_scenarios

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("resilient_harvester")


def fetch_candidate_tasks(conn, limit: int) -> list[dict[str, Any]]:
    """Fetch candidate tasks without touching any potentially corrupted telemetry chunk."""
    queries = [
        # Primary query with workdirectives + resource_allocations
        """
        WITH candidate_tasks AS (
            SELECT t.id::text AS task_id,
                   t.status,
                   t.start_time,
                   COALESCE(t.end_time, MAX(j.end_time)) AS end_time,
                   EXTRACT(EPOCH FROM (COALESCE(t.end_time, MAX(j.end_time)) - t.start_time)) AS dur_s,
                   COUNT(DISTINCT j.id) AS job_count,
                   string_agg(DISTINCT r.label, ',') FILTER (WHERE r.label ~ '^(InfraflexCell|KR120)') AS cell_sig
            FROM execution.tasks t
            JOIN execution.jobs j ON j.parent_id = t.id
            LEFT JOIN execution.resource_allocations ra ON ra.job_id = j.id
            LEFT JOIN execution.resources r ON r.id = ra.resource_id
            WHERE t.start_time IS NOT NULL
            GROUP BY t.id, t.status, t.start_time, t.end_time
            HAVING COUNT(DISTINCT j.id) >= 3
               AND EXTRACT(EPOCH FROM (COALESCE(t.end_time, MAX(j.end_time)) - t.start_time)) >= 45.0
        ),
        ranked_tasks AS (
            SELECT ct.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY COALESCE(ct.cell_sig, 'other'), ct.status, ct.job_count
                       ORDER BY ct.start_time DESC
                   ) AS rn
            FROM candidate_tasks ct
        )
        SELECT task_id, status, start_time, end_time, ROUND(dur_s::numeric, 2) AS dur_s, job_count
        FROM ranked_tasks
        WHERE rn <= 4
        ORDER BY start_time DESC
        LIMIT %s
        """,
        # Fallback query using only execution.tasks and execution.jobs
        """
        SELECT t.id::text AS task_id,
               t.status,
               t.start_time,
               COALESCE(t.end_time, MAX(j.end_time)) AS end_time,
               ROUND(EXTRACT(EPOCH FROM (COALESCE(t.end_time, MAX(j.end_time)) - t.start_time))::numeric, 2) AS dur_s,
               COUNT(DISTINCT j.id) AS job_count
        FROM execution.tasks t
        JOIN execution.jobs j ON j.parent_id = t.id
        WHERE t.start_time IS NOT NULL
        GROUP BY t.id, t.status, t.start_time, t.end_time
        HAVING COUNT(DISTINCT j.id) >= 3
        ORDER BY t.start_time DESC
        LIMIT %s
        """,
    ]

    for idx, q in enumerate(queries):
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SET enable_seqscan = off;")
                cur.execute(q, (limit,))
                rows = [dict(r) for r in cur.fetchall()]
                if rows:
                    return rows
        except Exception as exc:
            log.warning("Candidate task query #%d hit DB error (%s), trying fallback...", idx + 1, str(exc).strip())
            conn.rollback()

    return []


def fetch_jobs_for_task(conn, task_id: str) -> list[dict[str, Any]]:
    """Fetch jobs for a single task, isolating workdirectives and robot_telemetry if corrupted."""
    queries = [
        # Try with workdirectives + resources
        """
        SELECT j.id::text AS job_id,
               COALESCE(wd.label, regexp_replace(j.label, '^.*/', ''), j.skill, 'Step') AS step_label,
               COALESCE(j.skill, 'PickAndPlace') AS skill,
               COALESCE(j.status, 'Done') AS status,
               j.start_time,
               COALESCE(j.end_time, j.start_time + INTERVAL '2 seconds') AS end_time,
               COALESCE(array_agg(DISTINCT r.label) FILTER (WHERE r.label IS NOT NULL), ARRAY[]::text[]) AS resource_labels,
               COALESCE(array_agg(DISTINCT r.id::text) FILTER (WHERE r.id IS NOT NULL), ARRAY[]::text[]) AS resource_ids
        FROM execution.jobs j
        LEFT JOIN workdirective.workdirectives wd ON wd.job_id = j.id
        LEFT JOIN execution.resource_allocations ra ON ra.job_id = j.id
        LEFT JOIN execution.resources r ON r.id = ra.resource_id
        WHERE j.parent_id = %s::uuid
        GROUP BY j.id, wd.label, j.label, j.skill, j.status, j.start_time, j.end_time
        ORDER BY j.start_time ASC
        """,
        # Fallback without workdirectives (if workdirectives has the corrupted page)
        """
        SELECT j.id::text AS job_id,
               COALESCE(regexp_replace(j.label, '^.*/', ''), j.skill, 'Step') AS step_label,
               COALESCE(j.skill, 'PickAndPlace') AS skill,
               COALESCE(j.status, 'Done') AS status,
               j.start_time,
               COALESCE(j.end_time, j.start_time + INTERVAL '2 seconds') AS end_time,
               COALESCE(array_agg(DISTINCT r.label) FILTER (WHERE r.label IS NOT NULL), ARRAY[]::text[]) AS resource_labels,
               COALESCE(array_agg(DISTINCT r.id::text) FILTER (WHERE r.id IS NOT NULL), ARRAY[]::text[]) AS resource_ids
        FROM execution.jobs j
        LEFT JOIN execution.resource_allocations ra ON ra.job_id = j.id
        LEFT JOIN execution.resources r ON r.id = ra.resource_id
        WHERE j.parent_id = %s::uuid
        GROUP BY j.id, j.label, j.skill, j.status, j.start_time, j.end_time
        ORDER BY j.start_time ASC
        """,
    ]

    jobs: list[dict[str, Any]] = []
    for q in queries:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(q, (task_id,))
                jobs = [dict(r) for r in cur.fetchall()]
                break
        except Exception:
            conn.rollback()

    # Optionally enrich each job with a robot_telemetry snapshot (isolated so a bad block never drops the job)
    for j in jobs:
        j["robot_snap"] = {}
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT error_code,
                           ROUND(pose_x::numeric, 3)::float AS pose_x,
                           ROUND(pose_y::numeric, 3)::float AS pose_y,
                           ROUND(pose_z::numeric, 3)::float AS pose_z,
                           ROUND(joint_1::numeric, 3)::float AS joint_1,
                           ROUND(joint_2::numeric, 3)::float AS joint_2,
                           ROUND(joint_3::numeric, 3)::float AS joint_3,
                           ROUND(force_z::numeric, 3)::float AS force_z
                    FROM telemetry.robot_telemetry rt
                    WHERE rt.timestamp >= %s
                      AND rt.timestamp <= %s
                    ORDER BY rt.timestamp DESC
                    LIMIT 1
                    """,
                    (j["start_time"], j["end_time"]),
                )
                snap = cur.fetchone()
                if snap:
                    j["robot_snap"] = dict(snap)
        except Exception:
            conn.rollback()

    return jobs


def fetch_optional_telemetry(conn, sql: str, params: tuple) -> list[dict[str, Any]]:
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        conn.rollback()
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="Resilient InfraPlay Scenario Harvester")
    ap.add_argument("--host", default="10.102.0.70")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--dbname", default="tsdb")
    ap.add_argument("--user", default="postgres")
    ap.add_argument("--password", default="postgres")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    ensure_seed_scenarios(SCENARIOS_DIR)
    harvester = ScenarioHarvester(
        host=args.host,
        port=args.port,
        dbname=args.dbname,
        user=args.user,
        password=args.password,
    )

    try:
        conn = psycopg2.connect(**harvester.db_cfg)
        conn.autocommit = True
    except Exception as exc:
        log.warning("Could not connect to TimescaleDB at %s:%s: %s", args.host, args.port, exc)
        return 1

    # Log which relation has relfilenode 57226 if present
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT relnamespace::regnamespace::text, relname FROM pg_class WHERE relfilenode = 57226;")
            bad_rel = cur.fetchone()
            if bad_rel:
                log.info("Detected damaged page in relation %s.%s (isolating automatically)", bad_rel[0], bad_rel[1])
    except Exception:
        pass

    tasks = fetch_candidate_tasks(conn, args.limit)
    log.info("Found %d candidate tasks in TimescaleDB.", len(tasks))

    written: list[Path] = []
    for idx, t in enumerate(tasks, 1):
        task_id = t["task_id"]
        jobs = fetch_jobs_for_task(conn, task_id)
        if len(jobs) < 3:
            continue

        pa_events = fetch_optional_telemetry(
            conn,
            """
            SELECT resource_id::text AS resource_id,
                   timestamp AS ts,
                   error_code,
                   air_gripper,
                   air_toolchange,
                   power_tool
            FROM telemetry.powerair_telemetry
            WHERE timestamp >= %s AND timestamp <= %s
            ORDER BY timestamp ASC
            LIMIT 40
            """,
            (t["start_time"], t["end_time"]),
        )

        cv_events = fetch_optional_telemetry(
            conn,
            """
            SELECT resource_id::text AS resource_id,
                   timestamp AS ts,
                   error_code,
                   state
            FROM telemetry.conveyor_telemetry
            WHERE timestamp >= %s AND timestamp <= %s
            ORDER BY timestamp ASC
            LIMIT 30
            """,
            (t["start_time"], t["end_time"]),
        )

        bundle = {
            "task_id": task_id,
            "status": t["status"],
            "start_time": t["start_time"],
            "end_time": t["end_time"],
            "dur_s": float(t["dur_s"] or 60.0),
            "jobs": jobs,
            "powerair_events": pa_events,
            "conveyor_events": cv_events,
        }

        try:
            scen = harvester._build_scenario_from_bundle(bundle, idx)
            if scen:
                p = scen.save(SCENARIOS_DIR)
                written.append(p)
                log.info("Harvested [%d/%d] %s (%d steps, %d events)", idx, len(tasks), scen.id, len(scen.steps), len(scen.events))
        except Exception as exc:
            log.warning("Skipping task %s due to bundle build error: %s", task_id, exc)

    conn.close()
    total_files = len(list(SCENARIOS_DIR.glob("*.json")))
    log.info("Harvest complete: %d DB scenarios harvested (%d total scenarios now in %s).", len(written), total_files, SCENARIOS_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
