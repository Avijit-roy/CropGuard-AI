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
from flask import Flask, jsonify, request, send_file, render_template, redirect, url_for
from flask_cors import CORS
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
from reportlab.lib import colors

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

app = Flask(
    __name__,
    static_folder='../frontend/static',
    static_url_path='/static',
    template_folder='../frontend/templates',
)
CORS(app)

log = logging.getLogger(__name__)

# Use the existing DB from the subscriber
import os
_HERE   = os.path.dirname(os.path.abspath(__file__))
ROOT    = os.path.dirname(_HERE)
DB_PATH = os.path.join(ROOT, 'soil_data.db')
WEATHER_API_KEY = os.environ.get('OPENWEATHER_API_KEY', 'YOUR_API_KEY_HERE')
WEATHER_CITY = os.environ.get('WEATHER_CITY', 'Nabadwip')

THRESHOLDS = { "soil_moisture": {"min": 30, "max": 80, "unit": "%"}, "temperature": {"min": 10, "max": 35, "unit": "°C"}, "humidity": {"min": 40, "max": 80, "unit": "%"} }
calibration = {"soil_moisture": 0, "temperature": 0, "humidity": 0}
CONFIG_PATH = "config.json"

def load_persistent_config():
    global WEATHER_CITY
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                c = json.load(f)
                WEATHER_CITY = c.get("city", WEATHER_CITY)
                print(f"Loaded config: City={WEATHER_CITY}")
        except: pass

def save_persistent_config():
    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump({"city": WEATHER_CITY}, f)
    except: pass

load_persistent_config()

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

def get_latest_sensor():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM soil_readings ORDER BY created_at DESC LIMIT 1").fetchone()
    conn.close()
    if row:
        d = dict(row)
        timestamp_str = d.get("timestamp")
        
        connected = False
        if timestamp_str:
            try:
                # Remove Z if present so it parses as naive, or handle tz
                clean_ts = timestamp_str.replace("Z", "+00:00")
                ts = datetime.fromisoformat(clean_ts)
                # Ensure we compare timezone-aware or naive correctly
                if ts.tzinfo:
                    now = datetime.now(ts.tzinfo)
                else:
                    now = datetime.now(timezone.utc)
                    
                if (now - ts).total_seconds() < 15: # 15 seconds (publisher sends every 10s)
                    connected = True
            except Exception as e:
                print("Timestamp parse error:", e)

        return {
            "soil_moisture": round((d.get("soil_moisture") or 0.0) + calibration["soil_moisture"], 1),
            "temperature": round((d.get("temperature") or 0.0) + calibration["temperature"], 1),
            "humidity": round((d.get("humidity") or 0.0) + calibration["humidity"], 1),
            "timestamp": timestamp_str,
            "connected": connected
        }
    return {"soil_moisture": 0.0, "temperature": 0.0, "humidity": 0.0, "timestamp": None, "connected": False}

def get_readings(hours=1, max_rows=1000):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT timestamp, soil_moisture, temperature, humidity FROM soil_readings WHERE created_at >= datetime('now', ?) ORDER BY created_at DESC LIMIT ?", (f"-{hours} hours", max_rows)).fetchall()
    conn.close()
    return [{"timestamp": r["timestamp"], "soil_moisture": (r["soil_moisture"] or 0) + calibration["soil_moisture"], "temperature": (r["temperature"] or 0) + calibration["temperature"], "humidity": (r["humidity"] or 0) + calibration["humidity"]} for r in rows]

CACHED_GEO = None

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

def generate_insights(sensor, weather):
    insights = []
    sm = sensor.get("soil_moisture", 0)
    temp = sensor.get("temperature", 0)
    hum = sensor.get("humidity", 0)
    w_hum = weather.get("current", {}).get("humidity", 0) if weather else 0

    score = 100
    
    if sm < THRESHOLDS["soil_moisture"]["min"]:
        score -= 25
        insights.append({"level": "warning", "icon": "💧", "title": "Irrigation Needed",
                         "message": f"Soil moisture ({sm}%) is below minimum ({THRESHOLDS['soil_moisture']['min']}%). Turn on irrigation for 30 minutes."})
    elif sm > THRESHOLDS["soil_moisture"]["max"]:
        score -= 20
        insights.append({"level": "danger", "icon": "🌊", "title": "Soil Oversaturated",
                         "message": f"Soil moisture ({sm}%) exceeds maximum ({THRESHOLDS['soil_moisture']['max']}%). Halt irrigation and check drainage."})
    else:
        insights.append({"level": "success", "icon": "✅", "title": "Moisture Optimal",
                         "message": "Soil moisture is optimal."})

    if hum > 80 or w_hum > 80:
        score -= 15
        insights.append({"level": "danger", "icon": "🍄", "title": "Fungal Disease Risk",
                         "message": f"High humidity detected ({hum}%). Increase airflow or apply preventative fungicides."})

    if temp > THRESHOLDS["temperature"]["max"]:
        score -= 25
        insights.append({"level": "danger", "icon": "🌡️", "title": "Heat Stress Warning",
                         "message": f"Temperature ({temp}°C) is critically high. Consider shade netting or misting immediately."})
    elif temp < THRESHOLDS["temperature"]["min"]:
        score -= 20
        insights.append({"level": "warning", "icon": "❄️", "title": "Cold Stress Risk",
                         "message": f"Temperature ({temp}°C) is too cold. Deploy frost covers tonight."})

    rain = weather.get("current", {}).get("rain", 0) if weather else 0
    if rain > 5:
        score -= 10
        insights.append({"level": "info", "icon": "🌧️", "title": "Heavy Rainfall Expected",
                         "message": f"{rain}mm/h rainfall. Delay manual irrigation."})

    score = max(0, score)
    
    tone = "Good morning, conditions are looking optimal today! 🌱"
    if score < 50:
        tone = "Critical attention required for your field. Action needed immediately! ⚠️"
    elif score < 80:
        tone = f"Conditions are decent, but a few things need your attention today. 🔍"
        
    return {
        "score": score,
        "tone": tone,
        "insights": insights
    }

@app.route('/api/sensor')
def api_sensor():
    return jsonify(get_latest_sensor())

@app.route('/api/history')
def api_history():
    hours = min(int(request.args.get('hours', 1)), 168)  # max 7 days
    return jsonify(get_readings(hours, max_rows=1000))

@app.route('/api/weather')
def api_weather():
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    return jsonify(fetch_weather(lat, lon))

@app.route('/api/insights')
def api_insights():
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    return jsonify(generate_insights(get_latest_sensor(), fetch_weather(lat, lon)))

@app.route('/api/sensor/upload', methods=['POST'])
def api_sensor_upload():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No data received"}), 400
        
    device_id = data.get("device_id", "client_usb_device")
    soil_moisture = data.get("soil_moisture")
    temperature = data.get("temperature")
    humidity = data.get("humidity")
    
    if soil_moisture is None or temperature is None or humidity is None:
        return jsonify({"status": "error", "message": "Missing sensor fields"}), 400
        
    conn = sqlite3.connect(DB_PATH)
    timestamp = datetime.now(timezone.utc).isoformat()
    soil_dry = 1 if float(soil_moisture) < THRESHOLDS["soil_moisture"]["min"] else 0
    soil_wet = 1 if float(soil_moisture) > THRESHOLDS["soil_moisture"]["max"] else 0
    
    try:
        conn.execute("""
            INSERT INTO soil_readings (device_id, timestamp, temperature, humidity, soil_moisture, soil_dry, soil_wet, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            device_id,
            timestamp,
            float(temperature),
            float(humidity),
            float(soil_moisture),
            soil_dry,
            soil_wet
        ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"status": "error", "message": str(e)}), 500
        
    conn.close()
    return jsonify({"status": "success", "message": "Sensor data uploaded successfully"})


@app.route('/api/thresholds', methods=['GET', 'POST'])
def api_thresholds():
    global THRESHOLDS
    if request.method == 'POST':
        for key in THRESHOLDS:
            if key in request.json: THRESHOLDS[key].update(request.json[key])
        return jsonify({"status": "ok", "thresholds": THRESHOLDS})
    return jsonify(THRESHOLDS)


@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    global WEATHER_CITY, CACHED_GEO
    if request.method == 'POST':
        req = request.json
        if req:
            if "city" in req:
                WEATHER_CITY = req["city"]
                CACHED_GEO = None 
            save_persistent_config()
    return jsonify({"status": "ok", "city": WEATHER_CITY})

@app.route('/api/calibration', methods=['GET', 'POST'])
def api_calibration():
    global calibration
    if request.method == 'POST':
        calibration.update(request.json)
        return jsonify({"status": "ok", "calibration": calibration})
    return jsonify(calibration)

@app.route('/api/export/csv')
def export_csv():
    hours = min(int(request.args.get('hours', 24)), 168)  # max 7 days
    readings = get_readings(hours, max_rows=500)           # cap at 500 rows
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=['timestamp', 'soil_moisture', 'temperature', 'humidity'])
    writer.writeheader()
    writer.writerows(readings)
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode()),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'agri_report_{datetime.now().strftime("%Y%m%d_%H%M")}.csv'
    )

@app.route('/api/export/pdf')
def export_pdf():
    hours = min(int(request.args.get('hours', 24)), 168)  # max 7 days
    readings = get_readings(hours, max_rows=200)           # cap at 200 rows for PDF
    weather = fetch_weather()
    sensor = get_latest_sensor()
    insights = generate_insights(sensor, weather)

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
        ['Soil Moisture', f"{sensor['soil_moisture']}%", 'OK' if THRESHOLDS['soil_moisture']['min'] <= sensor['soil_moisture'] <= THRESHOLDS['soil_moisture']['max'] else 'ALERT'],
        ['Temperature', f"{sensor['temperature']}°C", 'OK' if THRESHOLDS['temperature']['min'] <= sensor['temperature'] <= THRESHOLDS['temperature']['max'] else 'ALERT'],
        ['Humidity', f"{sensor['humidity']}%", 'OK' if THRESHOLDS['humidity']['min'] <= sensor['humidity'] <= THRESHOLDS['humidity']['max'] else 'ALERT'],
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
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                'INSERT INTO disease_predictions (device_id,disease,crop,confidence,severity) VALUES (?,?,?,?,?)',
                ('cropguard_01', 'Healthy' if healthy else disease, crop, float(conf), 'None' if healthy else 'High')
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

        # Fusion
        fusion_data = None
        try:
            from backend.server import get_latest_reading, get_average_scores
            from backend.fusion_engine import fuse, SoilState, WeatherState
            latest = get_latest_reading('cropguard_01')
            avg    = get_average_scores(hours=168, device_id='cropguard_01')
            if latest and latest.get('temperature') is not None:
                f_temp = avg.get('avg_temperature') or latest['temperature']
                f_hum  = avg.get('avg_humidity')    or latest['humidity']
                f_sm   = avg.get('avg_soil_moisture') or latest['soil_moisture']
                weather_json = fetch_weather()
                w_curr   = weather_json.get('current', {})
                w_hourly = weather_json.get('hourly', [])
                max_prec = max([h.get('precip_prob',0) for h in w_hourly[:6]], default=0.0)
                soil = SoilState(
                    temperature=float(f_temp), humidity=float(f_hum),
                    soil_moisture=float(f_sm),
                    soil_dry=bool(latest.get('soil_dry',False)),
                    soil_wet=bool(latest.get('soil_wet',False)),
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
                    'alert_level':      fr.alert_level,
                    'risk_score':       fr.risk_score,
                    'combined_insight': fr.combined_insight,
                    'soil_advice':      fr.soil_advice,
                    'immediate_actions':fr.immediate_actions,
                    'treatment':        fr.treatment,
                    'prevention':       fr.prevention,
                    'irrigation_fix':   fr.irrigation_fix,
                    'fertiliser_fix':   fr.fertiliser_fix,
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
def api_disease_history():
    limit = min(int(request.args.get('limit', 100)), 500)
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f'SELECT * FROM disease_predictions ORDER BY timestamp DESC LIMIT {limit}'
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# PAGE ROUTES (Jinja2 templates)
# ============================================================

@app.route('/')
@app.route('/soil')
@app.route('/disease')
@app.route('/history')
@app.route('/preferences')
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
