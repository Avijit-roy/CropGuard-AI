import os
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Setup Logging
log = logging.getLogger(__name__)

# Initialize Supabase Client
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    log.error("Missing SUPABASE_URL or SUPABASE_KEY env vars!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============================================================
# USER OPERATIONS
# ============================================================

def get_user_by_id(user_id):
    try:
        res = supabase.table('users').select('*').eq('id', int(user_id)).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        log.error(f"Error fetching user by id {user_id}: {e}")
        return None

def get_user_by_google_id(google_id):
    try:
        res = supabase.table('users').select('*').eq('google_id', google_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        log.error(f"Error fetching user by google_id {google_id}: {e}")
        return None

def create_or_update_user(google_id, email, name, avatar_url):
    try:
        log.info(f"Syncing user: {email} (google_id: {google_id})")
        existing = get_user_by_google_id(google_id)
        data = {'google_id': google_id, 'email': email, 'name': name, 'avatar_url': avatar_url}
        
        if existing:
            res = supabase.table('users').update(data).eq('id', existing['id']).execute()
            return res.data[0] if res.data else existing
        else:
            res = supabase.table('users').insert(data).execute()
            if not res.data: return get_user_by_google_id(google_id)
            user = res.data[0]
            try:
                supabase.table('user_settings').insert({'user_id': user['id']}).execute()
            except Exception: pass
            return user
    except Exception as e:
        log.error(f"Error in create_or_update_user: {e}")
        return None

# ============================================================
# DEVICE OPERATIONS
# ============================================================

def get_device_owner(device_id):
    try:
        res = supabase.table('devices').select('user_id').eq('device_id', device_id).execute()
        return res.data[0]['user_id'] if res.data else None
    except Exception as e:
        log.error(f"Error fetching device owner for {device_id}: {e}")
        return None

def set_device_owner(device_id, user_id):
    try:
        data = {'device_id': device_id, 'user_id': int(user_id), 'updated_at': datetime.now(timezone.utc).isoformat()}
        res = supabase.table('devices').upsert(data).execute()
        return True
    except Exception as e:
        log.error(f"Error setting device owner: {e}")
        return False

# ============================================================
# USER SETTINGS OPERATIONS
# ============================================================

def get_user_settings(user_id):
    try:
        res = supabase.table('user_settings').select('*').eq('user_id', int(user_id)).execute()
        if res.data:
            row = res.data[0]
            return {
                "thresholds": {
                    "soil_moisture": {"min": row.get('th_sm_min', 30.0), "max": row.get('th_sm_max', 80.0), "unit": "%"},
                    "temperature":   {"min": row.get('th_temp_min', 10.0), "max": row.get('th_temp_max', 35.0), "unit": "°C"},
                    "humidity":      {"min": row.get('th_hum_min', 40.0), "max": row.get('th_hum_max', 80.0), "unit": "%"},
                },
                "calibration": {
                    "soil_moisture": row.get('cal_sm', 0.0),
                    "temperature":   row.get('cal_temp', 0.0),
                    "humidity":      row.get('cal_hum', 0.0),
                },
                "city": row.get('city', 'Nabadwip')
            }
    except Exception as e:
        log.error(f"Error getting settings for user {user_id}: {e}")
    return {"thresholds": {"soil_moisture": {"min": 30.0, "max": 80.0, "unit": "%"}, "temperature": {"min": 10.0, "max": 35.0, "unit": "°C"}, "humidity": {"min": 40.0, "max": 80.0, "unit": "%"}}, "calibration": {"soil_moisture": 0.0, "temperature": 0.0, "humidity": 0.0}, "city": "Nabadwip"}

def save_user_settings(user_id, **kwargs):
    try:
        check = supabase.table('user_settings').select('user_id').eq('user_id', int(user_id)).execute()
        data = {}
        mapping = {'th_sm_min': 'th_sm_min', 'th_sm_max': 'th_sm_max', 'th_temp_min': 'th_temp_min', 'th_temp_max': 'th_temp_max', 'th_hum_min': 'th_hum_min', 'th_hum_max': 'th_hum_max', 'cal_sm': 'cal_sm', 'cal_temp': 'cal_temp', 'cal_hum': 'cal_hum', 'city': 'city'}
        for k, col in mapping.items():
            if k in kwargs: data[col] = kwargs[k]
        if check.data:
            supabase.table('user_settings').update(data).eq('user_id', int(user_id)).execute()
        else:
            data['user_id'] = int(user_id)
            supabase.table('user_settings').insert(data).execute()
    except Exception as e:
        log.error(f"Error saving settings for user {user_id}: {e}")

# ============================================================
# SOIL READINGS OPERATIONS
# ============================================================

def get_latest_sensor(user_id=None):
    try:
        query = supabase.table('soil_readings').select('*')
        if user_id: query = query.eq('user_id', int(user_id))
        res = query.order('created_at', desc=True).limit(1).execute()
        cal = get_user_settings(user_id)['calibration'] if user_id else {"soil_moisture": 0.0, "temperature": 0.0, "humidity": 0.0}
        
        if res.data:
            row = res.data[0]
            # Use created_at (server time) for connection check
            ca = row.get("created_at")
            connected = False
            if ca:
                try:
                    t = datetime.fromisoformat(ca.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    # Allow 60 seconds (better for production/cloud)
                    connected = (now - t).total_seconds() < 60
                except Exception: pass
            return {
                "soil_moisture": round((row.get("soil_moisture") or 0.0) + cal["soil_moisture"], 1),
                "temperature":   round((row.get("temperature")   or 0.0) + cal["temperature"],   1),
                "humidity":      round((row.get("humidity")       or 0.0) + cal["humidity"],      1),
                "timestamp": ca, "connected": connected,
            }
    except Exception as e:
        log.error(f"Error getting latest sensor reading: {e}")
    return {"soil_moisture": 0.0, "temperature": 0.0, "humidity": 0.0, "timestamp": None, "connected": False}

def get_readings(hours=1, max_rows=1000, user_id=None):
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        query = supabase.table('soil_readings').select('timestamp, created_at, soil_moisture, temperature, humidity')
        if user_id: query = query.eq('user_id', int(user_id))
        res = query.gte('created_at', cutoff).order('created_at', desc=True).limit(max_rows).execute()
        cal = get_user_settings(user_id)['calibration'] if user_id else {"soil_moisture": 0.0, "temperature": 0.0, "humidity": 0.0}
        if res.data:
            return [{
                "timestamp":     r.get("created_at") or r.get("timestamp"),
                "soil_moisture": (r["soil_moisture"] or 0) + cal["soil_moisture"],
                "temperature":   (r["temperature"]   or 0) + cal["temperature"],
                "humidity":      (r["humidity"]       or 0) + cal["humidity"],
            } for r in res.data]
    except Exception as e:
        log.error(f"Error fetching readings history: {e}")
    return []

def save_soil_reading(data: dict):
    try:
        supabase.table('soil_readings').insert(data).execute()
    except Exception as e:
        log.error(f"Supabase reading write error: {e}")

# ============================================================
# DISEASE PREDICTIONS OPERATIONS
# ============================================================

def get_disease_history(limit=100, user_id=None):
    try:
        query = supabase.table('disease_predictions').select('*')
        if user_id: query = query.eq('user_id', int(user_id))
        res = query.order('timestamp', desc=True).limit(limit).execute()
        return res.data or []
    except Exception as e:
        log.error(f"Error fetching disease history: {e}")
        return []

def save_disease_prediction(data: dict):
    try:
        supabase.table('disease_predictions').insert(data).execute()
    except Exception as e:
        log.error(f"Supabase disease prediction write error: {e}")

# ============================================================
# PURGE OLD DATA OPERATIONS
# ============================================================

def purge_old_data(days=7):
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        supabase.table('soil_readings').delete().lt('created_at', cutoff).execute()
        supabase.table('disease_predictions').delete().lt('timestamp', cutoff).execute()
        supabase.table('recommendations').delete().lt('timestamp', cutoff).execute()
    except Exception as e:
        log.error(f"Error purging Supabase data: {e}")

def save_recommendation(data: dict):
    try:
        supabase.table('recommendations').insert(data).execute()
    except Exception as e:
        log.error(f"Supabase save_recommendation error: {e}")
