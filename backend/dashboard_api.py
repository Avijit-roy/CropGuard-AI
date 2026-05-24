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
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
CORS(app)

log = logging.getLogger(__name__)

# ── Flask-Login ──────────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view = 'page_login'
login_manager.login_message = None

# ── Paths & Defaults ─────────────────────────────────────────
_HERE   = os.path.dirname(os.path.abspath(__file__))
ROOT    = os.path.dirname(_HERE)
WEATHER_CITY = os.environ.get('WEATHER_CITY', 'Nabadwip')

DEFAULT_THRESHOLDS = {
    "soil_moisture": {"min": 30, "max": 80, "unit": "%"},
    "temperature":   {"min": 10, "max": 35, "unit": "°C"},
    "humidity":      {"min": 40, "max": 80, "unit": "%"},
}
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
        log.info(f'[CropGuard] AI model loaded')
    except Exception as e:
        log.error(f'[CropGuard] WARNING: AI model not loaded: {e}')

_load_ai_model()

# ============================================================
# IMAGE HELPERS
# ============================================================
def _suppress_background(img_rgb):
    import cv2
    hsv = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2HSV)
    masks = [cv2.inRange(hsv, np.array([25,40,40]), np.array([90,255,255])), cv2.inRange(hsv, np.array([15,40,40]), np.array([35,255,255])), cv2.inRange(hsv, np.array([5,40,20]), np.array([20,255,200]))]
    mask = masks[0]; [mask := cv2.bitwise_or(mask, m) for m in masks[1:]]
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
# WEATHER (OPENWEATHERMAP INTEGRATION)
# ============================================================
OWM_API_KEY = "40fc98243f68f3401caef00e163136aa"

def owm_to_wmo(owm_code):
    """Maps OpenWeatherMap condition codes to WMO codes used by the frontend."""
    if 200 <= owm_code <= 232: return 95 # Thunderstorm
    if 300 <= owm_code <= 321: return 51 # Drizzle
    if 500 <= owm_code <= 504: return 61 # Light/Mod Rain
    if 511 == owm_code: return 65        # Freezing rain -> Heavy
    if 520 <= owm_code <= 531: return 80 # Rain showers
    if 600 <= owm_code <= 622: return 71 # Snow
    if owm_code == 741: return 45        # Fog
    if owm_code == 800: return 0         # Clear
    if owm_code == 801: return 1         # Mainly clear
    if owm_code == 802: return 2         # Partly cloudy
    if 803 <= owm_code <= 804: return 3  # Overcast
    return 2 # Default to partly cloudy

def fetch_weather(lat=None, lon=None):
    """Weather fetching using OpenWeatherMap API."""
    try:
        # 1. Fetch Current Weather
        if lat and lon:
            curr_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OWM_API_KEY}&units=metric"
        else:
            curr_url = f"https://api.openweathermap.org/data/2.5/weather?q={WEATHER_CITY}&appid={OWM_API_KEY}&units=metric"
            
        c_res = requests.get(curr_url, timeout=5).json()
        if c_res.get("cod") != 200:
            raise Exception(f"OWM Current Error: {c_res.get('message')}")

        # 2. Fetch 5-Day / 3-Hour Forecast
        if lat and lon:
            fc_url = f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={OWM_API_KEY}&units=metric"
        else:
            fc_url = f"https://api.openweathermap.org/data/2.5/forecast?q={WEATHER_CITY}&appid={OWM_API_KEY}&units=metric"
            
        f_res = requests.get(fc_url, timeout=5).json()
        if f_res.get("cod") != "200":
            raise Exception(f"OWM Forecast Error: {f_res.get('message')}")

        # Process Hourly (3-hour steps from OWM)
        hourly = []
        for item in f_res.get("list", [])[:8]: # Next 24 hours (8 * 3h)
            hourly.append({
                "time": item.get("dt_txt"),
                "temp": item.get("main", {}).get("temp"),
                "precip_prob": int(item.get("pop", 0) * 100),
                "weather_code": owm_to_wmo(item.get("weather", [{}])[0].get("id", 800))
            })

        # Process Daily (Aggregated from 3-hour steps)
        daily_map = {}
        for item in f_res.get("list", []):
            date = item.get("dt_txt", "").split(" ")[0]
            if date not in daily_map:
                daily_map[date] = {
                    "temps": [],
                    "codes": [],
                    "dt": item.get("dt")
                }
            daily_map[date]["temps"].append(item.get("main", {}).get("temp"))
            daily_map[date]["codes"].append(item.get("weather", [{}])[0].get("id", 800))

        daily = []
        for date, val in list(daily_map.items())[:7]:
            daily.append({
                "date": date,
                "weather_code": owm_to_wmo(max(set(val["codes"]), key=val["codes"].count)),
                "temp_max": max(val["temps"]),
                "temp_min": min(val["temps"]),
                "sunrise": datetime.fromtimestamp(c_res.get("sys", {}).get("sunrise", 0)).isoformat() if date == list(daily_map.keys())[0] else None,
                "sunset": datetime.fromtimestamp(c_res.get("sys", {}).get("sunset", 0)).isoformat() if date == list(daily_map.keys())[0] else None
            })

        wmo_code = owm_to_wmo(c_res.get("weather", [{}])[0].get("id", 800))
        
        return {
            "city": c_res.get("name", WEATHER_CITY),
            "current": {
                "temp": c_res.get("main", {}).get("temp", 25),
                "humidity": c_res.get("main", {}).get("humidity", 60),
                "feels_like": c_res.get("main", {}).get("feels_like", 25),
                "wind_speed": c_res.get("wind", {}).get("speed", 0) * 3.6, # m/s to km/h
                "cloud_cover": c_res.get("clouds", {}).get("all", 0),
                "uv_index": 0, # Not available in free 2.5 API
                "rain": c_res.get("rain", {}).get("1h", 0),
                "weather_code": wmo_code,
                "pressure": c_res.get("main", {}).get("pressure", 1013),
                "aqi": 35 
            },
            "hourly": hourly,
            "daily": daily,
            "weather_code": wmo_code,
            "fetched_at": datetime.now().isoformat()
        }
    except Exception as e:
        log.warning(f"Weather: OpenWeatherMap failed: {e}")
        return {"city": WEATHER_CITY, "current": {"temp": 28, "humidity": 65, "rain": 0, "pressure": 1012, "weather_code": 1, "feels_like": 30, "wind_speed": 5, "cloud_cover": 20, "uv_index": 5, "aqi": 42}, "hourly": [], "daily": [], "mock": True, "fetched_at": datetime.now().isoformat()}

def generate_insights(sensor, weather, thresholds=None):
    if thresholds is None: thresholds = DEFAULT_THRESHOLDS
    insights, score = [], 100
    sm, temp, hum = sensor.get("soil_moisture", 0), sensor.get("temperature", 0), sensor.get("humidity", 0)
    w_hum = weather.get("current", {}).get("humidity", 0)
    if sm < thresholds["soil_moisture"]["min"]:
        score -= 25; insights.append({"level":"warning","icon":"💧","title":"Irrigation Needed","message":f"Soil moisture ({sm}%) is below minimum."})
    elif sm > thresholds["soil_moisture"]["max"]:
        score -= 20; insights.append({"level":"danger","icon":"🌊","title":"Soil Oversaturated","message":f"Soil moisture ({sm}%) exceeds maximum."})
    if hum > 80 or w_hum > 80:
        score -= 15; insights.append({"level":"danger","icon":"🍄","title":"Fungal Risk","message":f"High humidity ({hum}%). Increase airflow."})
    return {"score": max(0, score), "insights": insights}

# ============================================================
# API ROUTES
# ============================================================
@app.route('/api/sensor')
@login_required
def api_sensor():
    db.set_device_owner("cropguard_basic", current_user.id)
    return jsonify(db.get_latest_sensor(current_user.id))

@app.route('/api/history')
@login_required
def api_history():
    hours = min(int(request.args.get('hours', 1)), 168)
    return jsonify(db.get_readings(hours, max_rows=1000, user_id=current_user.id))

@app.route('/api/weather')
@login_required
def api_weather():
    return jsonify(fetch_weather(request.args.get('lat'), request.args.get('lon')))

@app.route('/api/insights')
@login_required
def api_insights():
    s = db.get_latest_sensor(current_user.id)
    th = db.get_user_settings(current_user.id)['thresholds']
    return jsonify(generate_insights(s, fetch_weather(request.args.get('lat'), request.args.get('lon')), th))

@app.route('/api/sensor/upload', methods=['POST'])
def api_sensor_upload():
    data = request.json or {}
    sm, tmp, hum = data.get("soil_moisture"), data.get("temperature"), data.get("humidity")
    if sm is None or tmp is None or hum is None: return jsonify({"error":"Missing data"}), 400
    device_id = data.get("device_id", DEVICE_ID)
    user_id = db.get_device_owner(device_id) or 4
    try:
        db.save_soil_reading({"device_id": device_id, "user_id": user_id, "timestamp": datetime.now(timezone.utc).isoformat(), "temperature": float(tmp), "humidity": float(hum), "soil_moisture": float(sm), "soil_dry": 1 if float(sm) < 30 else 0, "soil_wet": 1 if float(sm) > 80 else 0})
        return jsonify({"status":"success"})
    except Exception as e: return jsonify({"error":str(e)}), 500

@app.route('/api/predict', methods=['POST'])
@login_required
def api_predict():
    if _ai_model is None: return jsonify({'error': 'Model not loaded'}), 503
    if 'image' not in request.files: return jsonify({'error': 'No image'}), 400
    try:
        from PIL import Image
        file = request.files['image']
        pil_img = Image.open(file.stream).convert('RGB'); img_rgb = np.array(pil_img)
        processed = _suppress_background(img_rgb); enhanced = _enhance_contrast(processed); inp = _preprocess_for_model(enhanced)
        
        # Inference
        in_idx = _ai_model.get_input_details()[0]['index']; out_idx = _ai_model.get_output_details()[0]['index']
        _ai_model.set_tensor(in_idx, inp); _ai_model.invoke(); preds = _ai_model.get_tensor(out_idx)
        top3_idx = np.argsort(preds[0])[-3:][::-1]; idx = int(top3_idx[0]); conf = float(preds[0][idx]) * 100
        label = _label_enc[idx]; top3 = [[_label_enc[int(i)], float(preds[0][i])*100] for i in top3_idx]
        crop, disease, healthy = _parse_label(label)

        # Base response
        response_data = {
            'label': label, 'crop': crop, 'disease': disease, 'healthy': healthy, 'confidence': round(conf, 2), 'top3': top3,
            'alert_level': 'healthy' if healthy else 'medium', 'fusion': None
        }

        # Safe Fusion & LLM (Wrapped in Try-Except so base prediction always works)
        try:
            from backend.fusion_engine import fuse, SoilState, WeatherState
            latest = db.get_latest_sensor(current_user.id)
            readings_7d = db.get_readings(hours=168, user_id=current_user.id)
            if readings_7d:
                f_temp = sum(r['temperature'] for r in readings_7d)/len(readings_7d)
                f_hum = sum(r['humidity'] for r in readings_7d)/len(readings_7d)
                f_sm = sum(r['soil_moisture'] for r in readings_7d)/len(readings_7d)
            else:
                f_temp, f_hum, f_sm = latest.get('temperature', 25), latest.get('humidity', 60), latest.get('soil_moisture', 50)

            w_json = fetch_weather()
            soil = SoilState(temperature=float(f_temp), humidity=float(f_hum), soil_moisture=float(f_sm), soil_dry=bool(latest.get('soil_dry')), soil_wet=bool(latest.get('soil_wet')))
            weather = WeatherState(temp=w_json['current'].get('temp', 25), humidity=w_json['current'].get('humidity', 60), rain_mm=w_json['current'].get('rain', 0), precip_prob=float(max([h.get('precip_prob', 0) for h in w_json.get('hourly', [])[:6]], default=0)), pressure_hpa=w_json['current'].get('pressure', 1013))
            
            fr = fuse(label, crop, conf, soil, weather)
            response_data['alert_level'] = fr.alert_level
            response_data['fusion'] = {
                'alert_level': fr.alert_level, 'risk_score': fr.risk_score, 'combined_insight': fr.combined_insight,
                'soil_advice': fr.soil_advice, 'immediate_actions': fr.immediate_actions, 'treatment': fr.treatment,
                'prevention': fr.prevention, 'irrigation_fix': fr.irrigation_fix, 'fertiliser_fix': fr.fertiliser_fix,
                'llm_insight': fr.llm_insight
            }
        except Exception as fe:
            log.error(f"Fusion/LLM failed, returning base results: {fe}")

        db.save_disease_prediction({"device_id": f"user_{current_user.id}", "user_id": current_user.id, "disease": 'Healthy' if healthy else disease, "crop": crop, "confidence": float(conf), "severity": 'None' if healthy else 'High'})
        return jsonify(response_data)

    except Exception as e:
        log.error(f"Predict root error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/api/disease-history')
@login_required
def api_disease_history(): return jsonify(db.get_disease_history(100, current_user.id))

@app.route('/api/thresholds', methods=['GET', 'POST'])
@login_required
def api_thresholds():
    if request.method == 'POST':
        j = request.json or {}; th = db.get_user_settings(current_user.id)['thresholds']
        db.save_user_settings(current_user.id, th_sm_min=j.get('soil_moisture',{}).get('min', th['soil_moisture']['min']), th_sm_max=j.get('soil_moisture',{}).get('max', th['soil_moisture']['max']), th_temp_min=j.get('temperature',{}).get('min',th['temperature']['min']), th_temp_max=j.get('temperature',{}).get('max',th['temperature']['max']), th_hum_min=j.get('humidity',{}).get('min', th['humidity']['min']), th_hum_max=j.get('humidity',{}).get('max', th['humidity']['max']))
    return jsonify(db.get_user_settings(current_user.id)['thresholds'])

@app.route('/api/calibration', methods=['GET', 'POST'])
@login_required
def api_calibration():
    if request.method == 'POST':
        j = request.json or {}
        db.save_user_settings(current_user.id, cal_sm=j.get('soil_moisture', 0), cal_temp=j.get('temperature', 0), cal_hum=j.get('humidity', 0))
    return jsonify(db.get_user_settings(current_user.id)['calibration'])

@app.route('/api/config', methods=['GET', 'POST'])
@login_required
def api_config():
    if request.method == 'POST':
        j = request.json or {}
        if 'city' in j: db.save_user_settings(current_user.id, city=j['city'])
    return jsonify({"city": db.get_user_settings(current_user.id).get('city', 'Nabadwip')})

@app.route('/api/export/csv')
@login_required
def export_csv():
    hours = min(int(request.args.get('hours', 24)), 168); readings = db.get_readings(hours, user_id=current_user.id)
    output = io.StringIO(); writer = csv.DictWriter(output, fieldnames=['timestamp','soil_moisture','temperature','humidity'])
    writer.writeheader(); writer.writerows(readings); output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name='report.csv')

@app.route('/api/export/pdf')
@login_required
def export_pdf():
    try:
        hours = min(int(request.args.get('hours', 24)), 168)
        readings = db.get_readings(hours, user_id=current_user.id)
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
        styles = getSampleStyleSheet()
        
        # Custom Styles
        title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontSize=24, textColor=colors.HexColor("#2d8a4e"), spaceAfter=12, fontName='Helvetica-Bold')
        subtitle_style = ParagraphStyle('SubtitleStyle', parent=styles['Normal'], fontSize=10, textColor=colors.grey, spaceAfter=20)
        header_style = ParagraphStyle('HeaderStyle', parent=styles['Heading2'], fontSize=14, spaceBefore=15, spaceAfter=10, textColor=colors.HexColor("#1e293b"))
        
        elements = []
        
        # 1. Header
        elements.append(Paragraph("CropGuard AI — Soil Report", title_style))
        elements.append(Paragraph(f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | User: {current_user.name or 'User'}", subtitle_style))
        elements.append(Spacer(1, 0.2 * inch))
        
        # 2. Summary Stats
        if readings:
            avg_sm = sum(r['soil_moisture'] for r in readings) / len(readings)
            avg_temp = sum(r['temperature'] for r in readings) / len(readings)
            avg_hum = sum(r['humidity'] for r in readings) / len(readings)
            
            summary_data = [
                ["Metric", "Average Value", "Status"],
                ["Soil Moisture", f"{avg_sm:.1f}%", "Optimal" if 30 <= avg_sm <= 80 else "Action Required"],
                ["Temperature", f"{avg_temp:.1f}°C", "Normal" if 10 <= avg_temp <= 35 else "Attention"],
                ["Humidity", f"{avg_hum:.1f}%", "Normal" if 40 <= avg_hum <= 80 else "Attention"]
            ]
            
            summary_table = Table(summary_data, colWidths=[2 * inch, 2 * inch, 2 * inch])
            summary_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor("#475569")),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('TOPPADDING', (0, 1), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
            ]))
            
            elements.append(Paragraph("📊 Analytics Summary (Past " + str(hours) + " Hours)", header_style))
            elements.append(summary_table)
            elements.append(Spacer(1, 0.3 * inch))

        # 3. Data Table
        elements.append(Paragraph("📑 Detailed Sensor Readings", header_style))
        
        table_data = [["Timestamp", "Moisture (%)", "Temp (°C)", "Humidity (%)"]]
        # readings are DESC (newest first), so first 100 are the latest
        for r in readings[:100]: 
            ts = r['timestamp']
            if isinstance(ts, str) and 'T' in ts: ts = ts.replace('T', ' ').split('.')[0]
            table_data.append([ts, f"{r['soil_moisture']:.1f}", f"{r['temperature']:.1f}", f"{r['humidity']:.1f}"])
        
        if len(table_data) > 1:
            data_table = Table(table_data, colWidths=[2.2 * inch, 1.2 * inch, 1.2 * inch, 1.2 * inch])
            data_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2d8a4e")),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ]))
            elements.append(data_table)
        else:
            elements.append(Paragraph("No data records found for this period.", styles['Normal']))
            
        # 4. Footer
        elements.append(Spacer(1, 0.5 * inch))
        footer_text = "This report was autonomously generated by CropGuard AI Systems. For more details, visit the dashboard."
        elements.append(Paragraph(footer_text, styles['Normal']))

        doc.build(elements)
        buffer.seek(0)
        return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name=f'soil_report_{datetime.now().strftime("%Y%m%d")}.pdf')
    except Exception as e:
        log.error(f"PDF Export Error: {e}", exc_info=True)
        return jsonify({"error": "Failed to generate PDF report"}), 500

@app.route('/login')
def page_login():
    if current_user.is_authenticated: return redirect(url_for('page_dashboard'))
    return render_template('login.html')

@app.route('/auth/supabase-login', methods=['POST'])
def auth_supabase_login():
    token = (request.json or {}).get('access_token')
    if not token: return jsonify({"error": "No token"}), 400
    try:
        res = requests.get(f"{os.environ.get('SUPABASE_URL')}/auth/v1/user", headers={"Authorization": f"Bearer {token}", "apikey": os.environ.get('SUPABASE_KEY')}, timeout=5)
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
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False, use_reloader=False)

if __name__ == '__main__':
    start_api_server()
