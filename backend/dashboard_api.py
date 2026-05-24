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

# ── Supabase Managed OAuth ───────────────────────────────────
# OAuth is handled directly via Supabase Auth client-side and backend tokens.

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

# ============================================================
# PER-USER SETTINGS
# ============================================================
def get_user_settings(user_id):
    return db.get_user_settings(user_id)

def save_user_settings(user_id, **kwargs):
    db.save_user_settings(user_id, **kwargs)

# Initialize Database tables (crucial for Gunicorn/Render deployments)
try:
    from backend.server import init_database
    init_database()
except Exception as e:
    print(f"[CropGuard] Warning: Could not initialize database: {e}")

# ============================================================
# AI MODEL — loaded once at startup
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
        print(f'[CropGuard] AI model loaded — {MODEL_PATH}')
    except Exception as e:
        print(f'[CropGuard] WARNING: AI model not loaded: {e}')

_load_ai_model()

# ============================================================
# IMAGE PROCESSING HELPERS
# ============================================================

def _suppress_background(img_rgb):
    import cv2
    hsv = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2HSV)
    masks = [
        cv2.inRange(hsv, np.array([25,40,40]),  np.array([90,255,255])),
        cv2.inRange(hsv, np.array([15,40,40]),  np.array([35,255,255])),
        cv2.inRange(hsv, np.array([5, 40,20]),  np.array([20,255,200])),
    ]
    mask = masks[0]
    for m in masks[1:]:
        mask = cv2.bitwise_or(mask, m)
    blurred = cv2.GaussianBlur(img_rgb, (25,25), 0)
    return np.where(mask[:,:,None]==255, img_rgb, blurred)

def _enhance_contrast(img_rgb):
    import cv2
    lab      = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2LAB)
    l, a, b  = cv2.split(lab)
    clahe    = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
    merged   = cv2.merge((clahe.apply(l), a, b))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2RGB)

def _preprocess_for_model(img_rgb):
    import cv2
    resized = cv2.resize(img_rgb, (224,224))
    # MobileNetV2 preprocessing: scale to [-1, 1]
    return (np.expand_dims(resized.astype('float32'), axis=0) / 127.5) - 1.0

def _parse_label(label: str):
    if '___' in label:
        crop, disease = label.split('___', 1)
    else:
        crop, disease = 'Unknown', label
    disease = disease.replace('_',' ').title()
    return crop, disease, disease.lower() == 'healthy'

def get_latest_sensor(user_id=None):
    return db.get_latest_sensor(user_id)

def get_readings(hours=1, max_rows=1000, user_id=None):
    return db.get_readings(hours, max_rows, user_id)


def fetch_weather(lat=None, lon=None):
    global CACHED_GEO, WEATHER_CITY
    try:
        if lat is not None and lon is not None:
            lat = float(lat)
            lon = float(lon)
            city = f"GPS ({round(lat, 3)}, {round(lon, 3)})"
        else:
            if not CACHED_GEO:
                geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={WEATHER_CITY}&count=1"
                geo_res = requests.get(geo_url, timeout=5).json()
                if not geo_res.get("results"):
                    raise Exception("City not found")
                CACHED_GEO = {
                    "lat": geo_res["results"][0]["latitude"],
                    "lon": geo_res["results"][0]["longitude"],
                    "name": geo_res["results"][0]["name"]
                }
            
            lat, lon, city = CACHED_GEO["lat"], CACHED_GEO["lon"], CACHED_GEO["name"]
        
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,cloud_cover,surface_pressure,wind_speed_10m,wind_direction_10m&hourly=temperature_2m,precipitation_probability,weather_code,surface_pressure&daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max&timezone=auto"
        aqi_url = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=european_aqi,us_aqi,pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone,aerosol_optical_depth,dust,uv_index"


        w = requests.get(weather_url, timeout=5).json()
        a = requests.get(aqi_url, timeout=5).json()

        current = w.get("current", {})
        aqi_current = a.get("current", {})
        
        hourly_raw = w.get("hourly", {})
        hourly_list = []
        if "time" in hourly_raw:
            now_iso = datetime.now().isoformat()[:13]
            start_idx = 0
            for i, t in enumerate(hourly_raw["time"]):
                if t >= now_iso:
                    start_idx = i
                    break
            for i in range(start_idx, min(start_idx+24, len(hourly_raw["time"]))):
                hourly_list.append({
                    "time": hourly_raw["time"][i],
                    "temp": hourly_raw["temperature_2m"][i],
                    "precip_prob": hourly_raw["precipitation_probability"][i],
                    "weather_code": hourly_raw["weather_code"][i],
                    "pressure": hourly_raw["surface_pressure"][i]
                })
                
        daily_raw = w.get("daily", {})
        daily_list = []
        if "time" in daily_raw:
            for i in range(len(daily_raw["time"])):
                daily_list.append({
                    "date": daily_raw["time"][i],
                    "weather_code": daily_raw["weather_code"][i],
                    "temp_max": daily_raw["temperature_2m_max"][i],
                    "temp_min": daily_raw["temperature_2m_min"][i],
                    "sunrise": daily_raw["sunrise"][i],
                    "sunset": daily_raw["sunset"][i],
                    "uv_max": daily_raw["uv_index_max"][i]
                })

        alerts = []
        for h in hourly_list[:12]:
            code = h["weather_code"]
            if code in [95, 96, 99]:
                alerts.append({"type": "danger", "message": f"SEVERE: Thunderstorm/Hail expected around {h['time'][11:16]}!"})
                break
            elif code in [63, 65, 66, 67, 75]:
                alerts.append({"type": "warning", "message": f"ALERT: Heavy precipitation expected around {h['time'][11:16]}."})
                break

        # Calculate pressure trend (next 3 hours)
        p_trend = 0.0
        if len(hourly_list) >= 4:
            p_trend = (hourly_list[3]["pressure"] - hourly_list[0]["pressure"]) / 3.0

        result = {
            "city": city,
            "alerts": alerts,
            "current": {
                "temp": current.get("temperature_2m"),
                "feels_like": current.get("apparent_temperature"),
                "humidity": current.get("relative_humidity_2m"),
                "weather_code": current.get("weather_code"),
                "is_day": current.get("is_day"),
                "rain": current.get("precipitation"),
                "cloud_cover": current.get("cloud_cover"),
                "pressure": current.get("surface_pressure"),
                "pressure_trend": round(p_trend, 2),
                "wind_speed": current.get("wind_speed_10m"),
                "wind_direction": current.get("wind_direction_10m"),
                "aqi": aqi_current.get("us_aqi"),
                "pm2_5": aqi_current.get("pm2_5"),
                "pm10": aqi_current.get("pm10"),
                "uv_index": aqi_current.get("uv_index")
            },
            "hourly": hourly_list,
            "daily": daily_list,
            "fetched_at": datetime.now().isoformat(),
            "mock": False
        }
        return result
    except Exception as e:
        print("Weather fetch error:", e)
        return get_mock_weather()

def get_mock_weather():
    return {
        "city": WEATHER_CITY,
        "alerts": [{"type": "warning", "message": "TEST ALERT: Thunderstorm warning (Mock Data)"}],
        "current": {
            "temp": 28.5, "feels_like": 31.2, "humidity": 72,
            "weather_code": 2, "is_day": 1, "rain": 0, "cloud_cover": 20,
            "pressure": 1012, "wind_speed": 12, "wind_direction": 180,
            "aqi": 45, "pm2_5": 12.5, "pm10": 25.0, "uv_index": 6.5
        },
        "hourly": [
            {"time": (datetime.now() + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00"),
             "temp": 28 + i%3, "precip_prob": 10 if i>10 else 0, "weather_code": 1}
            for i in range(24)
        ],
        "daily": [
            {"date": (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d"),
             "weather_code": 1, "temp_max": 32, "temp_min": 24, 
             "sunrise": "06:00", "sunset": "18:00", "uv_max": 8.0}
            for i in range(7)
        ],
        "fetched_at": datetime.now().isoformat(),
        "mock": True
    }

def generate_insights(sensor, weather, thresholds=None):
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS
    insights, score = [], 100
    sm   = sensor.get("soil_moisture", 0)
    temp = sensor.get("temperature", 0)
    hum  = sensor.get("humidity", 0)
    w_hum = weather.get("current", {}).get("humidity", 0) if weather else 0
    if sm < thresholds["soil_moisture"]["min"]:
        score -= 25
        insights.append({"level":"warning","icon":"💧","title":"Irrigation Needed","message":f"Soil moisture ({sm}%) is below minimum ({thresholds['soil_moisture']['min']}%). Turn on irrigation for 30 minutes."})
    elif sm > thresholds["soil_moisture"]["max"]:
        score -= 20
        insights.append({"level":"danger","icon":"🌊","title":"Soil Oversaturated","message":f"Soil moisture ({sm}%) exceeds maximum ({thresholds['soil_moisture']['max']}%). Halt irrigation and check drainage."})
    else:
        insights.append({"level":"success","icon":"✅","title":"Moisture Optimal","message":"Soil moisture is optimal."})
    if hum > 80 or w_hum > 80:
        score -= 15
        insights.append({"level":"danger","icon":"🍄","title":"Fungal Disease Risk","message":f"High humidity ({hum}%). Increase airflow or apply preventative fungicides."})
    if temp > thresholds["temperature"]["max"]:
        score -= 25
        insights.append({"level":"danger","icon":"🌡️","title":"Heat Stress Warning","message":f"Temperature ({temp}°C) critically high. Consider shade netting immediately."})
    elif temp < thresholds["temperature"]["min"]:
        score -= 20
        insights.append({"level":"warning","icon":"❄️","title":"Cold Stress Risk","message":f"Temperature ({temp}°C) too cold. Deploy frost covers tonight."})
    rain = weather.get("current", {}).get("rain", 0) if weather else 0
    if rain > 5:
        score -= 10
        insights.append({"level":"info","icon":"🌧️","title":"Heavy Rainfall","message":f"{rain}mm/h rainfall. Delay manual irrigation."})
    score = max(0, score)
    if score < 50:   tone = "Critical attention required! ⚠️"
    elif score < 80: tone = "A few things need attention today. 🔍"
    else:            tone = "Conditions are looking optimal today! 🌱"
    return {"score": score, "tone": tone, "insights": insights}

@app.route('/api/sensor')
@login_required
def api_sensor():
    # LINKING STEP: Every time an authenticated user visits their dashboard,
    # we link the "local_usb" device to them automatically.
    db.set_device_owner("local_usb", current_user.id)
    return jsonify(get_latest_sensor(current_user.id))

@app.route('/api/history')
@login_required
def api_history():
    hours = min(int(request.args.get('hours', 1)), 168)
    return jsonify(get_readings(hours, max_rows=1000, user_id=current_user.id))

@app.route('/api/weather')
@login_required
def api_weather():
    return jsonify(fetch_weather(request.args.get('lat'), request.args.get('lon')))

@app.route('/api/insights')
@login_required
def api_insights():
    s  = get_latest_sensor(current_user.id)
    th = get_user_settings(current_user.id)['thresholds']
    return jsonify(generate_insights(s, fetch_weather(request.args.get('lat'), request.args.get('lon')), th))

@app.route('/api/sensor/upload', methods=['POST'])
def api_sensor_upload():
    data = request.json
    if not data: return jsonify({"status":"error","message":"No data received"}), 400
    sm  = data.get("soil_moisture")
    tmp = data.get("temperature")
    hum = data.get("humidity")
    device_id = data.get("device_id", "local_usb")

    if sm is None or tmp is None or hum is None:
        return jsonify({"status":"error","message":"Missing sensor fields"}), 400
    
    # Identify user_id automatically
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
        # Claim device ownership if authenticated
        db.set_device_owner(device_id, user_id)
    else:
        # Check database for registered device owner
        user_id = db.get_device_owner(device_id)
        
    if not user_id:
        # Emergency fallback to user_id 4 if no owner found yet
        user_id = 4
        
    th = get_user_settings(user_id)['thresholds']
    try:
        db.save_soil_reading({
            "device_id": device_id,
            "user_id": user_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "temperature": float(tmp),
            "humidity": float(hum),
            "soil_moisture": float(sm),
            "soil_dry": 1 if float(sm) < th["soil_moisture"]["min"] else 0,
            "soil_wet": 1 if float(sm) > th["soil_moisture"]["max"] else 0
        })
    except Exception as e:
        return jsonify({"status":"error","message":str(e)}), 500
    return jsonify({"status":"success"})


@app.route('/api/thresholds', methods=['GET', 'POST'])
@login_required
def api_thresholds():
    s = get_user_settings(current_user.id)
    if request.method == 'POST':
        j = request.json or {}
        sm_  = j.get('soil_moisture', {})
        tmp_ = j.get('temperature', {})
        hum_ = j.get('humidity', {})
        th   = s['thresholds']
        save_user_settings(current_user.id,
            th_sm_min=sm_.get('min',   th['soil_moisture']['min']),
            th_sm_max=sm_.get('max',   th['soil_moisture']['max']),
            th_temp_min=tmp_.get('min',th['temperature']['min']),
            th_temp_max=tmp_.get('max',th['temperature']['max']),
            th_hum_min=hum_.get('min', th['humidity']['min']),
            th_hum_max=hum_.get('max', th['humidity']['max']),
        )
        return jsonify({"status":"ok","thresholds":get_user_settings(current_user.id)['thresholds']})
    return jsonify(s['thresholds'])


@app.route('/api/config', methods=['GET', 'POST'])
@login_required
def api_config():
    if request.method == 'POST':
        j = request.json or {}
        if 'city' in j: save_user_settings(current_user.id, city=j['city'])
    return jsonify({"status":"ok","city":get_user_settings(current_user.id)['city']})


@app.route('/api/calibration', methods=['GET', 'POST'])
@login_required
def api_calibration():
    s = get_user_settings(current_user.id)
    if request.method == 'POST':
        j = request.json or {}
        cal = s['calibration']
        save_user_settings(current_user.id,
            cal_sm=j.get('soil_moisture', cal['soil_moisture']),
            cal_temp=j.get('temperature', cal['temperature']),
            cal_hum=j.get('humidity',     cal['humidity']),
        )
        return jsonify({"status":"ok","calibration":get_user_settings(current_user.id)['calibration']})
    return jsonify(s['calibration'])

@app.route('/api/export/csv')
@login_required
def export_csv():
    hours    = min(int(request.args.get('hours', 24)), 168)
    readings = get_readings(hours, max_rows=500, user_id=current_user.id)
    output   = io.StringIO()
    writer   = csv.DictWriter(output, fieldnames=['timestamp','soil_moisture','temperature','humidity'])
    writer.writeheader(); writer.writerows(readings); output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv',
                     as_attachment=True, download_name=f'agri_report_{datetime.now().strftime("%Y%m%d_%H%M")}.csv')

@app.route('/api/export/pdf')
@login_required
def export_pdf():
    hours = min(int(request.args.get('hours', 24)), 168)
    readings = get_readings(hours, max_rows=200, user_id=current_user.id)
    weather = fetch_weather()
    sensor = get_latest_sensor(current_user.id)
    s = get_user_settings(current_user.id)
    th = s['thresholds']
    insights = generate_insights(sensor, weather, th)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.75*inch)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle('Title', parent=styles['Title'], fontSize=20, textColor=colors.HexColor('#2d5a27'))
    story.append(Paragraph("Agricultural Dashboard Report", title_style))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Period: Last {hours}h", styles['Normal']))
    story.append(Spacer(1, 0.2*inch))

    story.append(Paragraph("Current Sensor Readings", styles['Heading2']))
    sensor_data = [
        ['Metric', 'Value', 'Status'],
        ['Soil Moisture', f"{sensor['soil_moisture']}%", 'OK' if th['soil_moisture']['min'] <= sensor['soil_moisture'] <= th['soil_moisture']['max'] else 'ALERT'],
        ['Temperature', f"{sensor['temperature']}°C", 'OK' if th['temperature']['min'] <= sensor['temperature'] <= th['temperature']['max'] else 'ALERT'],
        ['Humidity', f"{sensor['humidity']}%", 'OK' if th['humidity']['min'] <= sensor['humidity'] <= th['humidity']['max'] else 'ALERT'],
    ]
    t = Table(sensor_data, colWidths=[2*inch, 2*inch, 2*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2d5a27')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')]),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.2*inch))

    story.append(Paragraph("Insights & Recommendations", styles['Heading2']))
    for ins in insights["insights"]:
        story.append(Paragraph(f"• {ins['title']}: {ins['message']}", styles['Normal']))
    story.append(Spacer(1, 0.2*inch))

    if readings:
        story.append(Paragraph(f"Sensor History ({len(readings)} readings)", styles['Heading2']))
        table_data = [['Timestamp', 'Soil Moisture (%)', 'Temp (°C)', 'Humidity (%)']]
        for r in readings[-50:]:  # Last 50 rows for PDF
            table_data.append([r['timestamp'][:16], r['soil_moisture'], r['temperature'], r['humidity']])
        t2 = Table(table_data, colWidths=[2.2*inch, 1.5*inch, 1.5*inch, 1.5*inch])
        t2.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2d5a27')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')]),
        ]))
        story.append(t2)

    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, mimetype='application/pdf', as_attachment=True,
                     download_name=f'agri_report_{datetime.now().strftime("%Y%m%d_%H%M")}.pdf')

# ============================================================
# NEW API: /api/predict  (POST — multipart image)
# ============================================================

@app.route('/api/predict', methods=['POST'])
@login_required
def api_predict():
    if _ai_model is None:
        return jsonify({'error': 'AI model not loaded on server'}), 503
    if 'image' not in request.files:
        return jsonify({'error': 'No image file in request'}), 400
    try:
        from PIL import Image
        file = request.files['image']
        pil_img = Image.open(file.stream).convert('RGB')
        img_rgb = np.array(pil_img)

        processed = _suppress_background(img_rgb)
        enhanced  = _enhance_contrast(processed)
        inp       = _preprocess_for_model(enhanced)

        input_details = _ai_model.get_input_details()
        output_details = _ai_model.get_output_details()
        _ai_model.set_tensor(input_details[0]['index'], inp)
        _ai_model.invoke()
        preds = _ai_model.get_tensor(output_details[0]['index'])
        top3_idx = np.argsort(preds[0])[-3:][::-1]
        idx      = int(top3_idx[0])
        conf     = float(preds[0][idx]) * 100
        label    = _label_enc[idx]
        top3     = [[_label_enc[int(i)], float(preds[0][i])*100] for i in top3_idx]

        crop, disease, healthy = _parse_label(label)

        # Save to DB
        try:
            db.save_disease_prediction({
                "device_id": f"user_{current_user.id}",
                "user_id": current_user.id,
                "disease": 'Healthy' if healthy else disease,
                "crop": crop,
                "confidence": float(conf),
                "severity": 'None' if healthy else 'High'
            })
        except Exception as de:
            print("DB save error in predict:", de)

        # Fusion
        fusion_data = None
        try:
            try:
                from backend.fusion_engine import fuse, SoilState, WeatherState
            except ImportError:
                from fusion_engine import fuse, SoilState, WeatherState
            latest = get_latest_sensor(current_user.id)
            if latest and latest.get('temperature') is not None:
                readings_7d = get_readings(hours=168, max_rows=2000, user_id=current_user.id)
                if readings_7d:
                    f_temp = sum(r['temperature']   for r in readings_7d) / len(readings_7d)
                    f_hum  = sum(r['humidity']       for r in readings_7d) / len(readings_7d)
                    f_sm   = sum(r['soil_moisture']  for r in readings_7d) / len(readings_7d)
                else:
                    f_temp = latest['temperature']
                    f_hum  = latest['humidity']
                    f_sm   = latest['soil_moisture']

                weather_json = fetch_weather()
                w_curr   = weather_json.get('current', {})
                w_hourly = weather_json.get('hourly', [])
                max_prec = max([h.get('precip_prob', 0) for h in w_hourly[:6]], default=0.0)

                soil = SoilState(
                    temperature=float(f_temp), humidity=float(f_hum),
                    soil_moisture=float(f_sm),
                    soil_dry=bool(latest.get('soil_dry', False)),
                    soil_wet=bool(latest.get('soil_wet', False)),
                )
                weather = WeatherState(
                    temp=w_curr.get('temp') or 0.0,
                    humidity=w_curr.get('humidity') or 0.0,
                    rain_mm=w_curr.get('rain') or 0.0,
                    precip_prob=float(max_prec),
                    pressure_hpa=w_curr.get('pressure') or 1013.0,
                    weather_code=w_curr.get('weather_code') or 0,
                )
                fr = fuse(label, crop, conf, soil, weather)
                fusion_data = {
                    'alert_level':       fr.alert_level,
                    'risk_score':        fr.risk_score,
                    'combined_insight':  fr.combined_insight,
                    'soil_advice':       fr.soil_advice,
                    'immediate_actions': fr.immediate_actions,
                    'treatment':         fr.treatment,
                    'prevention':        fr.prevention,
                    'irrigation_fix':    fr.irrigation_fix,
                    'fertiliser_fix':    fr.fertiliser_fix,
                }
        except Exception as fe:
            print('Fusion error:', fe)

        return jsonify({
            'label':       label,
            'crop':        crop,
            'disease':     disease,
            'healthy':     healthy,
            'confidence':  round(conf, 2),
            'top3':        top3,
            'alert_level': 'healthy' if healthy else (fusion_data or {}).get('alert_level','medium'),
            'fusion':      fusion_data,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# NEW API: /api/disease-history
# ============================================================

@app.route('/api/disease-history')
@login_required
def api_disease_history():
    limit = min(int(request.args.get('limit', 100)), 500)
    try:
        rows = db.get_disease_history(limit, current_user.id)
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# PAGE ROUTES & AUTHENTICATION ENDPOINTS
# ============================================================

@app.route('/login')
def page_login():
    if current_user.is_authenticated:
        return redirect(url_for('page_dashboard'))
    return render_template('login.html')

@app.route('/auth/supabase-login', methods=['POST'])
def auth_supabase_login():
    j = request.json or {}
    access_token = j.get('access_token')
    if not access_token:
        log.error("Supabase Login: No access_token provided in request")
        return jsonify({"error": "No token provided"}), 400

    try:
        url = f"{db.SUPABASE_URL}/auth/v1/user"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "apikey": db.SUPABASE_KEY
        }
        log.info(f"Supabase Login: Verifying token with {url}")
        res = requests.get(url, headers=headers, timeout=5)
        if not res.ok:
            log.error(f"Supabase Login: Token verification failed ({res.status_code}): {res.text}")
            return jsonify({"error": f"Invalid token: {res.text}"}), 401
        
        user_info = res.json()
        google_id = user_info.get('id')
        email = user_info.get('email')
        
        log.info(f"Supabase Login: Token verified for user {email}")
        
        meta = user_info.get('user_metadata', {})
        name = meta.get('full_name') or meta.get('name') or email.split('@')[0]
        avatar_url = meta.get('avatar_url') or meta.get('picture')

        if not google_id:
            log.error("Supabase Login: Google ID missing from user_info")
            return jsonify({"error": "Invalid user identity"}), 400

        user_row = db.create_or_update_user(google_id, email, name, avatar_url)
        if not user_row:
            log.error(f"Supabase Login: Failed to sync user {email} to database")
            return jsonify({"error": "Failed to sync user to database. Check if RLS is disabled."}), 500

        user = User(user_row['id'], user_row['google_id'], user_row['email'], user_row['name'], user_row['avatar_url'])
        login_user(user, remember=True)
        log.info(f"Supabase Login: Successfully logged in user {email} (ID: {user.id})")
        
        # AUTOMATIC LINKING: When a user logs in, link the local device to them immediately
        db.set_device_owner("local_usb", user.id)
        
        return jsonify({"status": "success", "user": {
            "id": user.id, "email": user.email, "name": user.name, "avatar_url": user.avatar_url
        }})
    except Exception as e:
        log.error(f"Supabase Login: Internal exception: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/logout')
@login_required
def page_logout():
    logout_user()
    return redirect(url_for('page_login'))


@app.route('/')
@app.route('/soil')
@app.route('/disease')
@app.route('/history')
@app.route('/preferences')
@login_required
def page_dashboard():
    return render_template('dashboard.html')


def start_api_server():
    import os
    wlog = logging.getLogger('werkzeug')
    wlog.setLevel(logging.ERROR)
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port, use_reloader=False)

if __name__ == '__main__':
    start_api_server()
