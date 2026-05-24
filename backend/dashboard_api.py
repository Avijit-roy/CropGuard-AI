import os
import sys
import json
import csv
import io
import threading
import sqlite3
import logging
import requests
import numpy as np
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify, request, send_file, render_template, redirect, url_for, session, abort
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.middleware.proxy_fix import ProxyFix
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
from reportlab.lib import colors
import database as db

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

app = Flask(
    __name__,
    static_folder='../frontend/static',
    static_url_path='/static',
    template_folder='../frontend/templates',
)
app.secret_key = os.environ.get('SECRET_KEY', 'cropguard-dev-secret-change-in-prod')
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # HTTPS on Render
CORS(app)

log = logging.getLogger(__name__)

# ── Flask-Login ──────────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view = 'page_login'
login_manager.login_message = None

# ── Paths & Defaults ─────────────────────────────────────────
_HERE   = os.path.dirname(os.path.abspath(__file__))
ROOT    = os.path.dirname(_HERE)
DB_PATH = os.path.join(ROOT, 'soil_data.db')
WEATHER_CITY = os.environ.get('WEATHER_CITY', 'Nabadwip')

DEFAULT_THRESHOLDS = {
    "soil_moisture": {"min": 30, "max": 80, "unit": "%"},
    "temperature":   {"min": 10, "max": 35, "unit": "°C"},
    "humidity":      {"min": 40, "max": 80, "unit": "%"},
}
DEFAULT_CALIBRATION = {"soil_moisture": 0, "temperature": 0, "humidity": 0}
CACHED_GEO = {}
DEVICE_ID = "cropguard_basic"

# ============================================================
# USER MODEL
# ============================================================
class User(UserMixin):
    def __init__(self, id, google_id, email, name, avatar_url):
        self.id         = id
        self.google_id  = google_id
        self.email      = email
        self.name       = name
        self.avatar_url = avatar_url or ''
    def get_id(self): return str(self.id)

@login_manager.user_loader
def load_user(user_id):
    row = db.get_user_by_id(user_id)
    if row:
        return User(row['id'], row['google_id'], row['email'], row['name'], row['avatar_url'])
    return None

def get_user_settings(user_id):
    return db.get_user_settings(user_id)

def save_user_settings(user_id, **kwargs):
    db.save_user_settings(user_id, **kwargs)

# ============================================================
# AI MODEL
# ============================================================
MODEL_PATH   = os.path.join(ROOT, 'plant_disease_model.tflite')
ENCODER_PATH = os.path.join(ROOT, 'label_encoder_new.joblib')
_ai_model   = None
_label_enc  = None

def _load_ai_model():
    global _ai_model, _label_enc
    try:
        from ai_edge_litert.interpreter import Interpreter
        import joblib
        _ai_model  = Interpreter(model_path=MODEL_PATH)
        _ai_model.allocate_tensors()
        _label_enc = joblib.load(ENCODER_PATH)
        log.info(f'[CropGuard] AI model loaded — {MODEL_PATH}')
    except Exception as e:
        log.error(f'[CropGuard] WARNING: AI model not loaded: {e}')

_load_ai_model()

# ============================================================
# IMAGE HELPERS
# ============================================================
def _suppress_background(img_rgb):
    import cv2
    hsv = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2HSV)
    masks = [cv2.inRange(hsv, np.array([25,40,40]),  np.array([90,255,255])), cv2.inRange(hsv, np.array([15,40,40]),  np.array([35,255,255])), cv2.inRange(hsv, np.array([5, 40,20]),  np.array([20,255,200]))]
    mask = masks[0]
    for m in masks[1:]: mask = cv2.bitwise_or(mask, m)
    blurred = cv2.GaussianBlur(img_rgb, (25,25), 0)
    return np.where(mask[:,:,None]==255, img_rgb, blurred)

def _enhance_contrast(img_rgb):
    import cv2
    lab = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2LAB); l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8)); merged = cv2.merge((clahe.apply(l), a, b))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2RGB)

def _preprocess_for_model(img_rgb):
    import cv2
    resized = cv2.resize(img_rgb, (224,224))
    return (np.expand_dims(resized.astype('float32'), axis=0) / 127.5) - 1.0

def _parse_label(label: str):
    if '___' in label: crop, disease = label.split('___', 1)
    else: crop, disease = 'Unknown', label
    disease = disease.replace('_',' ').title()
    return crop, disease, disease.lower() == 'healthy'

# ============================================================
# WEATHER
# ============================================================
def fetch_weather(lat=None, lon=None):
    global CACHED_GEO, WEATHER_CITY
    try:
        if lat and lon:
            city = f"GPS ({round(float(lat), 3)}, {round(float(lon), 3)})"
        else:
            if not CACHED_GEO:
                geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={WEATHER_CITY}&count=1"
                geo_res = requests.get(geo_url, timeout=5).json()
                if not geo_res.get("results"): raise Exception("City not found")
                CACHED_GEO = {"lat": geo_res["results"][0]["latitude"], "lon": geo_res["results"][0]["longitude"], "name": geo_res["results"][0]["name"]}
            lat, lon, city = CACHED_GEO["lat"], CACHED_GEO["lon"], CACHED_GEO["name"]
        
        w = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,surface_pressure&hourly=temperature_2m,precipitation_probability&timezone=auto", timeout=5).json()
        current = w.get("current", {})
        hourly_list = []
        if "time" in w.get("hourly", {}):
            for i in range(len(w["hourly"]["time"])[:24]):
                hourly_list.append({"temp": w["hourly"]["temperature_2m"][i], "precip_prob": w["hourly"]["precipitation_probability"][i]})

        return {
            "city": city, "current": {"temp": current.get("temperature_2m"), "humidity": current.get("relative_humidity_2m"), "rain": current.get("precipitation"), "weather_code": current.get("weather_code"), "pressure": current.get("surface_pressure")},
            "hourly": hourly_list, "fetched_at": datetime.now().isoformat()
        }
    except Exception as e:
        log.error(f"Weather error: {e}")
        return {"city": WEATHER_CITY, "current": {"temp": 25, "humidity": 60, "rain": 0}, "hourly": [], "mock": True}

# ============================================================
# API ROUTES
# ============================================================
@app.route('/api/sensor')
@login_required
def api_sensor():
    db.set_device_owner("cropguard_basic", current_user.id)
    db.set_device_owner("local_usb", current_user.id)
    return jsonify(db.get_latest_sensor(current_user.id))

@app.route('/api/sensor/upload', methods=['POST'])
def api_sensor_upload():
    data = request.json
    if not data: return jsonify({"error":"No data"}), 400
    sm, tmp, hum = data.get("soil_moisture"), data.get("temperature"), data.get("humidity")
    device_id = data.get("device_id", DEVICE_ID)
    
    user_id = current_user.id if current_user.is_authenticated else db.get_device_owner(device_id) or 4
    th = db.get_user_settings(user_id)['thresholds']
    try:
        db.save_soil_reading({"device_id": device_id, "user_id": user_id, "timestamp": datetime.now(timezone.utc).isoformat(), "temperature": float(tmp), "humidity": float(hum), "soil_moisture": float(sm), "soil_dry": 1 if float(sm) < th["soil_moisture"]["min"] else 0, "soil_wet": 1 if float(sm) > th["soil_moisture"]["max"] else 0})
    except Exception as e: return jsonify({"error":str(e)}), 500
    return jsonify({"status":"success"})

@app.route('/api/predict', methods=['POST'])
@login_required
def api_predict():
    if _ai_model is None: return jsonify({'error': 'Model not loaded'}), 503
    if 'image' not in request.files: return jsonify({'error': 'No image'}), 400
    try:
        from PIL import Image
        from backend.fusion_engine import fuse, SoilState, WeatherState
        
        file = request.files['image']
        pil_img = Image.open(file.stream).convert('RGB')
        img_rgb = np.array(pil_img)
        processed = _suppress_background(img_rgb); enhanced = _enhance_contrast(processed); inp = _preprocess_for_model(enhanced)

        input_details = _ai_model.get_input_details(); output_details = _ai_model.get_output_details()
        _ai_model.set_tensor(input_details[0]['index'], inp); _ai_model.invoke()
        preds = _ai_model.get_tensor(output_details[0]['index'])
        top3_idx = np.argsort(preds[0])[-3:][::-1]
        idx = int(top3_idx[0]); conf = float(preds[0][idx]) * 100; label = _label_enc[idx]
        top3 = [[_label_enc[int(i)], float(preds[0][i])*100] for i in top3_idx]
        crop, disease, healthy = _parse_label(label)

        # DB SAVE
        db.save_disease_prediction({"device_id": f"user_{current_user.id}", "user_id": current_user.id, "disease": 'Healthy' if healthy else disease, "crop": crop, "confidence": float(conf), "severity": 'None' if healthy else 'High'})

        # FUSION
        latest = db.get_latest_sensor(current_user.id)
        readings_7d = db.get_readings(hours=168, user_id=current_user.id)
        if readings_7d:
            f_temp = sum(r['temperature'] for r in readings_7d)/len(readings_7d)
            f_hum = sum(r['humidity'] for r in readings_7d)/len(readings_7d)
            f_sm = sum(r['soil_moisture'] for r in readings_7d)/len(readings_7d)
        else:
            f_temp, f_hum, f_sm = latest['temperature'], latest['humidity'], latest['soil_moisture']

        w_json = fetch_weather()
        soil = SoilState(temperature=float(f_temp), humidity=float(f_hum), soil_moisture=float(f_sm), soil_dry=bool(latest.get('soil_dry')), soil_wet=bool(latest.get('soil_wet')))
        weather = WeatherState(temp=w_json['current']['temp'] or 0, humidity=w_json['current']['humidity'] or 0, rain_mm=w_json['current']['rain'] or 0, precip_prob=float(max([h['precip_prob'] for h in w_json['hourly'][:6]], default=0)), pressure_hpa=w_json['current']['pressure'] or 1013)
        
        log.info(f"Triggering Fusion for {label}...")
        fr = fuse(label, crop, conf, soil, weather)
        log.info(f"Fusion complete. LLM Insight present: {fr.llm_insight is not None}")

        return jsonify({
            'label': label, 'crop': crop, 'disease': disease, 'healthy': healthy, 'confidence': round(conf, 2), 'top3': top3,
            'alert_level': 'healthy' if healthy else fr.alert_level,
            'fusion': {
                'alert_level': fr.alert_level, 'risk_score': fr.risk_score, 'combined_insight': fr.combined_insight,
                'soil_advice': fr.soil_advice, 'immediate_actions': fr.immediate_actions, 'treatment': fr.treatment,
                'prevention': fr.prevention, 'irrigation_fix': fr.irrigation_fix, 'fertiliser_fix': fr.fertiliser_fix,
                'llm_insight': fr.llm_insight  # CRITICAL: PASS TO FRONTEND
            }
        })
    except Exception as e:
        log.error(f"Predict error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/api/disease-history')
@login_required
def api_disease_history():
    return jsonify(db.get_disease_history(100, current_user.id))

@app.route('/api/thresholds', methods=['GET', 'POST'])
@login_required
def api_thresholds():
    if request.method == 'POST':
        j = request.json or {}; th = db.get_user_settings(current_user.id)['thresholds']
        db.save_user_settings(current_user.id, th_sm_min=j.get('soil_moisture',{}).get('min', th['soil_moisture']['min']), th_sm_max=j.get('soil_moisture',{}).get('max', th['soil_moisture']['max']), th_temp_min=j.get('temperature',{}).get('min',th['temperature']['min']), th_temp_max=j.get('temperature',{}).get('max',th['temperature']['max']), th_hum_min=j.get('humidity',{}).get('min', th['humidity']['min']), th_hum_max=j.get('humidity',{}).get('max', th['humidity']['max']))
    return jsonify(db.get_user_settings(current_user.id)['thresholds'])

@app.route('/login')
def page_login():
    if current_user.is_authenticated: return redirect(url_for('page_dashboard'))
    return render_template('login.html')

@app.route('/auth/supabase-login', methods=['POST'])
def auth_supabase_login():
    access_token = (request.json or {}).get('access_token')
    if not access_token: return jsonify({"error": "No token"}), 400
    try:
        res = requests.get(f"{os.environ.get('SUPABASE_URL')}/auth/v1/user", headers={"Authorization": f"Bearer {access_token}", "apikey": os.environ.get('SUPABASE_KEY')}, timeout=5)
        if not res.ok: return jsonify({"error": "Invalid token"}), 401
        u = res.json(); meta = u.get('user_metadata', {})
        user_row = db.create_or_update_user(u['id'], u['email'], meta.get('full_name') or u['email'], meta.get('avatar_url'))
        user = User(user_row['id'], user_row['google_id'], user_row['email'], user_row['name'], user_row['avatar_url'])
        login_user(user, remember=True); db.set_device_owner("cropguard_basic", user.id)
        return jsonify({"status": "success", "user": {"id": user.id, "email": user.email, "name": user.name}})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/logout')
@login_required
def page_logout(): logout_user(); return redirect(url_for('page_login'))

@app.route('/')
@app.route('/soil')
@app.route('/disease')
@app.route('/history')
@app.route('/preferences')
@login_required
def page_dashboard(): return render_template('dashboard.html')

def start_api_server():
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port, use_reloader=False)

if __name__ == '__main__':
    start_api_server()
