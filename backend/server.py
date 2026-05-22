# ============================================================
# FILE: backend/server.py
# PURPOSE: Initializes SQLite database, runs data retention,
#          and starts the Flask REST API server.
# ============================================================

import sqlite3
import logging
import logging.handlers
import os
import sys
import time
import threading
from datetime import datetime, timezone

# Resolve paths relative to project root regardless of invocation directory
_HERE   = os.path.dirname(os.path.abspath(__file__))
ROOT    = os.path.dirname(_HERE)   # one level up from backend/
DB_PATH = os.path.join(ROOT, 'soil_data.db')

# Ensure backend/ is on sys.path so "from dashboard_api import ..." works
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ── Data Retention Config ──────────────────────────────────────
DATA_RETENTION_DAYS   = 7        # Keep only the last 7 days
CLEANUP_INTERVAL_SECS = 6 * 3600 # Run cleanup every 6 hours

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            os.path.join(ROOT, 'server.log'), maxBytes=5*1024*1024, backupCount=3
        ),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

# ============================================================
# DATABASE SETUP
# ============================================================

def init_database():
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS soil_readings (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id        TEXT    NOT NULL,
            timestamp        TEXT    NOT NULL,
            temperature      REAL,
            humidity         REAL,
            soil_moisture    REAL,
            soil_dry         INTEGER,
            soil_wet         INTEGER,
            air_quality_pct  REAL,
            high_ammonia     INTEGER,
            pressure_hpa     REAL,
            altitude_m       REAL,
            risk_score       INTEGER,
            created_at       TEXT    DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS disease_predictions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT,
            disease     TEXT    NOT NULL,
            crop        TEXT,
            confidence  REAL,
            severity    TEXT,
            timestamp   TEXT    DEFAULT (datetime('now')),
            image_path  TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS recommendations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id       TEXT,
            disease         TEXT,
            soil_score      INTEGER,
            alert_level     TEXT,
            recommendation  TEXT,
            soil_fix        TEXT,
            timestamp       TEXT    DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()
    log.info(f"Database initialized: {DB_PATH}")

# ============================================================
# DATA RETENTION — auto-purge records older than 7 days
# ============================================================

db_lock = threading.Lock()

def purge_old_data():
    """Delete rows older than DATA_RETENTION_DAYS from every table."""
    cutoff = f"-{DATA_RETENTION_DAYS} days"
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            tables_and_cols = [
                ("soil_readings",       "created_at"),
                ("disease_predictions", "timestamp"),
                ("recommendations",     "timestamp"),
            ]
            total_deleted = 0
            for table, col in tables_and_cols:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE {col} < datetime('now', ?)",
                    (cutoff,),
                )
                total_deleted += cur.rowcount
                log.info(f"Purged {cur.rowcount} old rows from {table}")

            conn.commit()
            conn.close()
            
            # VACUUM cannot be run inside a transaction
            conn = sqlite3.connect(DB_PATH)
            conn.execute("VACUUM")
            conn.close()
            
            log.info(
                f"Data retention cleanup done — "
                f"{total_deleted} total rows removed (keeping last {DATA_RETENTION_DAYS} days)"
            )
        except sqlite3.Error as e:
            log.error(f"Retention cleanup error: {e}")
            if conn: conn.close()


def _retention_loop():
    """Background loop that fires purge_old_data on a fixed interval."""
    while True:
        try:
            purge_old_data()
        except Exception as e:
            log.error(f"Retention thread error: {e}")
        time.sleep(CLEANUP_INTERVAL_SECS)

# ============================================================
# DATABASE WRITER
# ============================================================

def save_soil_reading(data: dict):
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("""
                INSERT INTO soil_readings
                    (device_id, timestamp,
                     temperature, humidity,
                     soil_moisture, soil_dry, soil_wet)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get("device_id", "cropguard_01"),
                data.get("timestamp", datetime.now(timezone.utc).isoformat()),
                data.get("temperature"),
                data.get("humidity"),
                data.get("soil_moisture"),
                1 if data.get("soil_dry",  False) else 0,
                1 if data.get("soil_wet",  False) else 0,
            ))
            conn.commit()
            log.info(
                f"Saved | device={data.get('device_id')} | "
                f"temp={data.get('temperature')}°C | "
                f"soil={data.get('soil_moisture')}%"
            )
        except sqlite3.Error as e:
            log.error(f"DB write error: {e}")
        finally:
            conn.close()

# ============================================================
# PUBLIC QUERY FUNCTIONS
# ============================================================

def get_latest_reading(device_id=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    where = f"WHERE device_id = '{device_id}'" if device_id else ""
    row = conn.execute(
        f"SELECT * FROM soil_readings {where} ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def get_readings_history(hours=24, device_id=None):
    conn  = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    where = f"WHERE created_at >= datetime('now', '-{hours} hours')"
    if device_id:
        where += f" AND device_id = '{device_id}'"
    rows = conn.execute(
        f"SELECT * FROM soil_readings {where} ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_average_scores(hours=24, device_id=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    where = f"WHERE created_at >= datetime('now', ?)"
    if device_id:
        where += f" AND device_id = '{device_id}'"
        
    row  = conn.execute(f"""
        SELECT
            AVG(temperature)     as avg_temperature,
            AVG(humidity)        as avg_humidity,
            AVG(soil_moisture)   as avg_soil_moisture
        FROM soil_readings
        {where}
    """, (f"-{hours} hours",)).fetchone()
    conn.close()
    return dict(row) if row else {}

def get_duration_metrics(hours=24, device_id=None):
    readings = get_readings_history(hours=hours, device_id=device_id)
    if not readings:
        return {"humidity_high_hours": 0.0, "soil_wet_hours": 0.0}

    readings.sort(key=lambda x: x['created_at'], reverse=True)

    hum_hours = 0.0
    wet_hours = 0.0
    last_ts = None
    
    for r in readings:
        ts = datetime.fromisoformat(r['created_at'].replace(' ', 'T'))
        if r['humidity'] > 80:
            if last_ts:
                delta = (last_ts - ts).total_seconds() / 3600.0
                hum_hours += delta
            last_ts = ts
        else:
            break 

    last_ts = None
    for r in readings:
        ts = datetime.fromisoformat(r['created_at'].replace(' ', 'T'))
        if r['soil_wet'] == 1:
            if last_ts:
                delta = (last_ts - ts).total_seconds() / 3600.0
                wet_hours += delta
            last_ts = ts
        else:
            break 

    return {
        "humidity_high_hours": round(hum_hours, 1),
        "soil_wet_hours": round(wet_hours, 1)
    }

# ============================================================
# MAIN
# ============================================================

def main():
    log.info("=== CropGuard AI Server Starting ===")
    init_database()

    # Start background data-retention thread
    threading.Thread(target=_retention_loop, daemon=True).start()
    log.info(f"Data retention active — keeping last {DATA_RETENTION_DAYS} days, cleanup every {CLEANUP_INTERVAL_SECS//3600}h")

    # Start the embedded dashboard API
    from dashboard_api import start_api_server
    threading.Thread(target=start_api_server, daemon=True).start()
    log.info("Embedded Dashboard API started on port 5000")

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopped by user")

if __name__ == "__main__":
    main()
