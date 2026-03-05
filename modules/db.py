"""db.py

Supabase (Postgres) connection and read functions for the MBTA app.
All functions return empty/None gracefully if Supabase is not configured.
"""

import json
import os

import polyline
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


def _get_supabase_conn():
    """Open a Postgres connection from SUPABASE_* env vars. Returns None if not configured."""
    host = os.getenv("SUPABASE_HOST")
    if not host:
        return None
    return psycopg2.connect(
        host=host,
        port=int(os.getenv("SUPABASE_PORT", "5432")),
        dbname=os.getenv("SUPABASE_DB", "postgres"),
        user=os.getenv("SUPABASE_USER", "postgres"),
        password=os.getenv("SUPABASE_PASSWORD", ""),
        connect_timeout=10,
        sslmode="require",
    )


def _log_facility_statuses(facilities):
    """Write a row for any facility whose status has changed since the last snapshot."""
    conn = _get_supabase_conn()
    if conn is None:
        return  # Supabase not configured — skip silently
    try:
        with conn:
            with conn.cursor() as cur:
                # Last recorded status per facility
                cur.execute("""
                    SELECT facility_id, status FROM outage_log
                    WHERE id IN (
                        SELECT MAX(id) FROM outage_log GROUP BY facility_id
                    )
                """)
                last_status = {row[0]: row[1] for row in cur.fetchall()}

                now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
                to_insert = []
                for fid, f in facilities.items():
                    current = f.get("status", "operational")
                    if last_status.get(fid) != current:
                        alert = f.get("alert") or {}
                        to_insert.append((
                            now, fid,
                            f.get("type"),
                            f.get("name") or f.get("short_name"),
                            f.get("stop_id"),
                            f.get("station_name"),
                            current,
                            alert.get("id"),
                            alert.get("outage_start"),
                            alert.get("cause"),
                            alert.get("outage_end"),
                            alert.get("header"),
                            json.dumps(alert) if alert else None,
                        ))

                if to_insert:
                    cur.executemany("""
                        INSERT INTO outage_log
                            (logged_at, facility_id, facility_type, facility_name,
                             stop_id, station_name, status, alert_id, outage_start,
                             cause, outage_end_scheduled, alert_header, alert_raw)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """, to_insert)
    finally:
        conn.close()


def _read_station_routes_from_db():
    """Read station→routes mapping from Supabase.

    Returns dict: {stop_id: [{id, name, color, text_color, route_type}]}
    Returns empty dict if Supabase not configured or table is empty.
    """
    conn = _get_supabase_conn()
    if conn is None:
        return {}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT stop_id, route_id, route_name, route_color,
                           route_text_color, route_type
                    FROM station_routes
                """)
                rows = cur.fetchall()
        result = {}
        for stop_id, route_id, name, color, text_color, route_type in rows:
            if stop_id not in result:
                result[stop_id] = []
            result[stop_id].append({
                "id": route_id,
                "name": name,
                "color": color,
                "text_color": text_color,
                "route_type": route_type,
            })
        return result
    except Exception:
        return {}
    finally:
        conn.close()


def _read_route_shapes_from_db():
    """Read canonical route shapes from Supabase and decode polylines.

    Returns dict: {route_id: [[(lat, lon), ...], ...]}
    Returns empty dict if Supabase not configured or table is empty.
    """
    conn = _get_supabase_conn()
    if conn is None:
        return {}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT route_id, shapes FROM route_shapes")
                rows = cur.fetchall()
        result = {}
        for route_id, shapes_val in rows:
            try:
                encoded_list = shapes_val if isinstance(shapes_val, list) else json.loads(shapes_val)
                result[route_id] = [polyline.decode(enc) for enc in encoded_list]
            except Exception:
                pass
        return result
    except Exception:
        return {}
    finally:
        conn.close()


def _read_facility_line_mapping_from_db():
    """Read facility_line_mapping from Supabase.

    Returns dict: {facility_id: {lines, direction, source}}
    Returns empty dict if Supabase not configured or table is empty.
    """
    conn = _get_supabase_conn()
    if conn is None:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT facility_id, lines, direction, source FROM facility_line_mapping")
            rows = cur.fetchall()
        return {
            row[0]: {
                "lines": list(row[1]) if row[1] else None,
                "direction": row[2],
                "source": row[3],
            }
            for row in rows
        }
    except Exception as e:
        print(f"_read_facility_line_mapping_from_db error: {e}")
        return {}
    finally:
        conn.close()
