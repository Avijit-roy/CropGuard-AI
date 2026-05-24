# ============================================================
# FILE: frontend/app.py
# SAVE TO: frontend/ folder
# PURPOSE: Unified Streamlit dashboard combining:
#          1. Plant disease detection (your existing app9.py)
#          2. Live IoT soil monitoring
#          3. Fusion engine recommendations
#
# INSTALL:
#   pip install streamlit tensorflow opencv-python joblib
#               paho-mqtt streamlit-cropper plotly
#
# HOW TO RUN:
#   streamlit run frontend/app.py
#
# MAKE SURE:
#   - backend/mqtt_subscriber.py is running in background
#   - soil_data.db exists (created by subscriber automatically)
#   - plant_disease_model_new.keras is in root or update path
#   - label_encoder_new.joblib is in root or update path
# ============================================================

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
import numpy as np
import joblib
import tensorflow as tf
import cv2
import json
import sqlite3
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from PIL import Image
from datetime import datetime
from streamlit_cropper import st_cropper
from tensorflow.keras.applications.mobilenet_v2 import preprocess_input

from backend.mqtt_subscriber  import get_latest_reading, get_readings_history, get_average_scores, get_duration_metrics
from backend.fusion_engine    import fuse, SoilState, WeatherState
import requests

# Get project root for model files
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

# ============================================================
# PAGE CONFIG (Must be first Streamlit command)
# ============================================================

# Peek at session state for dynamic title without calling a widget yet
current_nav = st.session_state.get("nav_selection", "🔬 Disease Detection")
st.set_page_config(
    page_title=f"CropGuard | {current_nav.split(' ', 1)[-1]}",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# CUSTOM CSS
# ============================================================

st.markdown("""
<style>
    .metric-card {
        background: #f0f7f0;
        border-radius: 12px;
        padding: 16px 20px;
        border-left: 4px solid #2d8a4e;
        margin-bottom: 12px;
    }
    .alert-critical { border-left-color: #e53e3e !important; background: #fff5f5; }
    .alert-high     { border-left-color: #dd6b20 !important; background: #fffaf0; }
    .alert-medium   { border-left-color: #d69e2e !important; background: #fffff0; }
    .alert-low      { border-left-color: #38a169 !important; background: #f0fff4; }
    .section-header {
        font-size: 1.1rem;
        font-weight: 600;
        color: #2d8a4e;
        border-bottom: 2px solid #e2e8f0;
        padding-bottom: 6px;
        margin: 16px 0 10px 0;
    }
    .risk-badge {
        display: inline-block;
        padding: 4px 14px;
        border-radius: 20px;
        font-weight: bold;
        font-size: 0.9rem;
    }
    
    /* === Modern Sidebar Navigation === */
    section[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] {
        gap: 8px;
    }
    section[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label {
        display: flex;
        align-items: center;
        width: 100%;
        min-height: 54px;
        box-sizing: border-box;
        background-color: rgba(255, 255, 255, 0.03);
        padding: 12px 16px;
        margin-bottom: 20px;
        border-radius: 12px;
        border: 1px solid rgba(255, 255, 255, 0.05);
        cursor: pointer;
        transition: all 0.2s ease-in-out;
    }
    section[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label:hover {
        background-color: rgba(45, 138, 78, 0.1);
        border-color: rgba(45, 138, 78, 0.3);
        transform: translateX(4px);
    }
    /* Hide the actual radio circle */
    section[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label > div:first-child {
        display: none;
    }
    /* Style the text */
    section[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label div[data-testid="stMarkdownContainer"] p {
        font-size: 1.05rem;
        margin-left: 0 !important;
        transition: color 0.2s;
    }
    /* Active/Selected State */
    section[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label:has(input:checked) {
        background: linear-gradient(135deg, #2d8a4e 0%, #1e5c34 100%);
        border: none;
        box-shadow: 0 4px 15px rgba(45, 138, 78, 0.3);
        transform: translateX(6px);
    }
    section[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label:has(input:checked) div[data-testid="stMarkdownContainer"] p {
        color: white !important;
        font-weight: 600;
        text-shadow: 0 1px 2px rgba(0,0,0,0.2);
    }
</style>
""", unsafe_allow_html=True)

# ============================================================
# MODEL LOADING
# ============================================================

# Model paths relative to project root
MODEL_PATH   = os.path.join(_PROJECT_ROOT, "plant_disease_model_new.keras")
ENCODER_PATH = os.path.join(_PROJECT_ROOT, "label_encoder_new.joblib")

import tf_keras  # noqa: F401 — side-effect import required for tf.keras compatibility

@st.cache_resource
def load_ai_model():
    try:
        model = tf.keras.models.load_model(MODEL_PATH, compile=False)
        label_encoder = joblib.load(ENCODER_PATH)
        return model, label_encoder
    except Exception as e:
        return None, str(e)

model, label_encoder = load_ai_model()

if model is None:
    st.error(f"❌ Model load failed: {label_encoder}")
    st.stop()

# ============================================================
# IMAGE PROCESSING HELPERS
# ============================================================

def suppress_background(image: Image.Image) -> Image.Image:
    img = np.array(image)
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    masks = [
        cv2.inRange(hsv, np.array([25, 40, 40]),  np.array([90, 255, 255])),   # green
        cv2.inRange(hsv, np.array([15, 40, 40]),  np.array([35, 255, 255])),   # yellow
        cv2.inRange(hsv, np.array([5,  40, 20]),  np.array([20, 255, 200])),   # brown
    ]
    mask    = masks[0]
    for m in masks[1:]:
        mask = cv2.bitwise_or(mask, m)
    blurred = cv2.GaussianBlur(img, (25, 25), 0)
    result  = np.where(mask[:, :, None] == 255, img, blurred)
    return Image.fromarray(result)

def enhance_contrast(image: Image.Image) -> Image.Image:
    img      = np.array(image)
    lab      = cv2.cvtColor(img, cv2.COLOR_RGB2LAB)
    l, a, b  = cv2.split(lab)
    clahe    = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    merged   = cv2.merge((clahe.apply(l), a, b))
    return Image.fromarray(cv2.cvtColor(merged, cv2.COLOR_LAB2RGB))

def preprocess_image(img: Image.Image) -> np.ndarray:
    img = img.resize((224, 224))
    arr = np.array(img)
    return preprocess_input(np.expand_dims(arr, axis=0))

def predict(image):
    if model is None:
        return None, None, None, None
    img_array = preprocess_image(image)
    preds     = model.predict(img_array, verbose=0)

    # label_encoder is already {int: class_name} — use directly
    # convert np.int64 to plain int to avoid KeyError
    top3_idx   = np.argsort(preds[0])[-3:][::-1]
    idx        = int(top3_idx[0])
    confidence = float(preds[0][idx]) * 100
    label      = label_encoder[idx]
    top3       = [(label_encoder[int(i)], float(preds[0][i]) * 100)
                  for i in top3_idx]

    return label, confidence, top3

def parse_label(label: str):
    if "___" in label:
        crop, disease = label.split("___", 1)
    else:
        crop, disease = "Unknown", label
    disease = disease.replace("_", " ").title()
    return crop, disease, disease.lower() == "healthy"

# ============================================================
# WEATHER HELPER
# ============================================================

import requests

def fetch_weather_api():
    try:
        res = requests.get("http://127.0.0.1:5000/api/weather", timeout=2)
        if res.status_code == 200:
            return res.json()
    except:
        pass
    return None



# ============================================================
# SOIL CHART HELPERS
# ============================================================

def gauge_chart(value, title, min_val, max_val, good_range, unit=""):
    fig = go.Figure(go.Indicator(
        mode  = "gauge+number",
        value = value,
        title = {"text": title, "font": {"size": 13}},
        number= {"suffix": unit, "font": {"size": 18}},
        gauge = {
            "axis":  {"range": [min_val, max_val], "tickwidth": 1},
            "bar":   {"color": "#2d8a4e", "thickness": 0.25},
            "steps": [
                {"range": [min_val, good_range[0]],  "color": "#fed7d7"},
                {"range": good_range,                  "color": "#c6f6d5"},
                {"range": [good_range[1], max_val],    "color": "#fed7d7"},
            ],
            "threshold": {
                "line":  {"color": "black", "width": 2},
                "thickness": 0.75,
                "value": value,
            },
        },
    ))
    fig.update_layout(
        height=180, margin=dict(l=10, r=10, t=30, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)"
    )
    return fig

def history_chart(df: pd.DataFrame, column: str, title: str, color: str = "#2d8a4e"):
    fig = px.line(
        df, x="created_at", y=column,
        title=title,
        color_discrete_sequence=[color],
    )
    fig.update_layout(
        height=200,
        margin=dict(l=0, r=0, t=30, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        xaxis_title="",
        yaxis_title="",
    )
    fig.update_traces(line=dict(width=2))
    return fig
# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.image(
    "https://cdn-icons-png.flaticon.com/512/2917/2917995.png",
    width=60
)
st.sidebar.title("🌿 CropGuard AI")
st.sidebar.caption("Plant Disease + Soil Health System")
st.sidebar.divider()

page = st.sidebar.radio(
    "Navigation",
    ["🔬 Disease Detection", "🌱 Soil Monitor", "📄 History", "⚙️ Preferences"],
    key="nav_selection"
)

st.sidebar.markdown("---")
device_id = "cropguard_01"

# ============================================================
# PAGE 1: DISEASE DETECTION
# ============================================================

def page_disease_detection():
    st.title("🔬 Plant Disease Detection")
    st.caption("Upload a leaf image — AI will diagnose the disease")

    uploaded_file = st.file_uploader(
        "Upload Leaf Image", type=["jpg", "jpeg", "png"]
    )

    if not uploaded_file:
        st.info("📸 Upload a leaf photo of Apple/ Peach/ Pepper bell/ Potato/ Tomato to begin diagnosis")
        return

    image = Image.open(uploaded_file)

    st.subheader("Step 1 — Crop the leaf region")
    cropped = st_cropper(image, realtime_update=True)
    st.image(cropped, width=380, caption="Cropped leaf")

    if not st.button("🚀 Run AI Diagnosis", type="primary"):
        return

    with st.spinner("Processing image..."):
        processed = suppress_background(cropped)
        enhanced  = enhance_contrast(processed)

    col1, col2 = st.columns(2)
    with col1:
        st.image(cropped,   width=280, caption="Original crop")
    with col2:
        st.image(enhanced,  width=280, caption="After CLAHE + background suppression")

    with st.spinner("Running AI model..."):
        label, confidence, top3 = predict(enhanced)

    if label is None:
        st.error("❌ Model not loaded. Check file paths.")
        return

    crop_name, disease, healthy = parse_label(label)

    # ---- Results ----
    st.divider()
    st.subheader("📋 Diagnosis Results")

    c1, c2, c3 = st.columns(3)
    c1.metric("Detected Crop",    crop_name)
    c2.metric("Disease",          "Healthy" if healthy else disease)
    c3.metric("Confidence",       f"{confidence:.1f}%")

    st.progress(confidence / 100)

    if confidence < 60:
        st.warning(
            f"⚠️ Low confidence ({confidence:.1f}%). "
            "Try a clearer image or crop more tightly around the leaf."
        )

    if healthy:
        st.success("✅ Plant appears healthy!")
    else:
        st.error(f"⚠️ Disease detected: **{disease}**")

    # ---- Save to Database ----
    try:
        conn = sqlite3.connect("soil_data.db")
        conn.execute(
            """
            INSERT INTO disease_predictions 
            (device_id, disease, crop, confidence, severity)
            VALUES (?, ?, ?, ?, ?)
            """,
            (device_id, "Healthy" if healthy else disease, crop_name, float(confidence), "None" if healthy else "High")
        )
        conn.commit()
        conn.close()
    except Exception as e:
        st.warning(f"Failed to save prediction history: {e}")

    # ---- Top 3 Predictions ----
    with st.expander("📊 Top 3 Model Predictions"):
        for rank, (lbl, conf) in enumerate(top3, 1):
            c, d, _ = parse_label(lbl)
            st.write(f"**{rank}.** {c} — {d.replace('_',' ').title()}")
            st.progress(conf / 100)
            st.caption(f"{conf:.2f}%")

    # ---- Fusion Engine: Disease + Soil Analysis ----
    fusion_result = None
    
    with st.spinner("🔄 Fusing AI Diagnosis with 7-Day Trend Data..."):
        latest_soil = get_latest_reading(device_id)
        avg_soil = get_average_scores(hours=168, device_id=device_id) # 7-day average
        weather_data = fetch_weather_api()

        if latest_soil and latest_soil.get("temperature") is not None:
            try:
                # Use 7-day averages if available, fallback to latest
                f_temp = avg_soil.get("avg_temperature") if avg_soil.get("avg_temperature") is not None else latest_soil.get("temperature")
                f_hum  = avg_soil.get("avg_humidity")    if avg_soil.get("avg_humidity") is not None else latest_soil.get("humidity")
                f_sm   = avg_soil.get("avg_soil_moisture") if avg_soil.get("avg_soil_moisture") is not None else latest_soil.get("soil_moisture")

                # Extract weather bits
                w_curr = weather_data.get("current", {}) if weather_data else {}
                w_hourly = weather_data.get("hourly", []) if weather_data else []
                max_precip = max([h.get("precip_prob", 0) for h in w_hourly[:6]]) if w_hourly else 0.0
                
                w_code = w_curr.get("weather_code") or 0

                soil_state = SoilState(
                    temperature   = float(f_temp),
                    humidity      = float(f_hum),
                    soil_moisture = float(f_sm),
                    soil_dry      = bool(latest_soil.get("soil_dry", False)),
                    soil_wet      = bool(latest_soil.get("soil_wet", False)),
                )
                
                weather_state = WeatherState(
                    temp          = w_curr.get("temp") or 0.0,
                    humidity      = w_curr.get("humidity") or 0.0,
                    rain_mm       = w_curr.get("rain") or 0.0,
                    precip_prob   = float(max_precip),
                    pressure_hpa  = w_curr.get("pressure") or 1013.0,
                    weather_code  = w_code
                )

                fusion_result = fuse(
                    disease_label = label,
                    crop          = crop_name,
                    confidence    = confidence,
                    soil          = soil_state,
                    weather       = weather_state
                )
                
                pass

            except Exception as e:
                st.warning(f"Fusion engine error: {e}")

    if fusion_result:
        st.divider()
        st.subheader("🧬 Fusion Analysis — Disease + Soil Intelligence")

        # ── Alert Banner ──
        alert_colors = {
            "critical": ("#dc2626", "#fef2f2", "🚨"),
            "high":     ("#ea580c", "#fff7ed", "⚠️"),
            "medium":   ("#ca8a04", "#fefce8", "🟡"),
            "low":      ("#16a34a", "#f0fdf4", "🟢"),
        }
        bg, fg, icon = alert_colors.get(fusion_result.alert_level, ("#6b7280", "#f9fafb", "ℹ️"))
        st.markdown(
            f'<div style="background:{fg}; border-left:5px solid {bg}; padding:14px 18px; '
            f'border-radius:8px; margin-bottom:12px;">'
            f'<span style="font-size:1.3rem; font-weight:700; color:{bg};">'
            f'{icon} Alert Level: {fusion_result.alert_level.upper()}</span>'
            f'<span style="float:right; font-size:1.1rem; font-weight:600; color:{bg};">'
            f'Risk Score: {fusion_result.risk_score}/100</span></div>',
            unsafe_allow_html=True,
        )

        # ── Combined Insight ──
        st.info(f"**🔍 AI Insight:** {fusion_result.combined_insight}")

        # ── Sensor Summary ──
        st.caption(f"📡 {fusion_result.soil_advice}")

        # ── Immediate Actions ──
        if fusion_result.immediate_actions:
            st.markdown('<p class="section-header">🚀 Immediate Actions Required</p>', unsafe_allow_html=True)
            for action in fusion_result.immediate_actions:
                st.markdown(f"- ⚡ {action}")

        # ── Treatment & Prevention side by side ──
        col_treat, col_prevent = st.columns(2)

        with col_treat:
            st.markdown('<p class="section-header">💊 Treatment Plan</p>', unsafe_allow_html=True)
            for item in fusion_result.treatment:
                st.markdown(f"- {item}")

        with col_prevent:
            st.markdown('<p class="section-header">🛡️ Prevention Tips</p>', unsafe_allow_html=True)
            for item in fusion_result.prevention:
                st.markdown(f"- {item}")

        # ── Soil Fixes ──
        col_irr, col_fert = st.columns(2)

        with col_irr:
            st.markdown('<p class="section-header">💧 Irrigation Advice</p>', unsafe_allow_html=True)
            st.write(fusion_result.irrigation_fix)

        with col_fert:
            st.markdown('<p class="section-header">🌿 Fertiliser Advice</p>', unsafe_allow_html=True)
            st.write(fusion_result.fertiliser_fix)

        pass

    elif not avg_7d or avg_7d.get("avg_temperature") is None:
        st.divider()
        st.info(
            "ℹ️ Not enough sensor data found for 7-day average analysis. "
            "Connect your IoT device and collect data to see Fusion Analysis."
        )

    # ---- Download Report ----
    st.divider()
    report_lines = [
        "CropGuard AI — Plant Disease Report",
        "=" * 40,
        f"Date       : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Crop       : {crop_name}",
        f"Disease    : {'Healthy' if healthy else disease}",
        f"Confidence : {confidence:.2f}%",
    ]
    if fusion_result:
        report_lines += [
            "",
            f"Fusion Alert  : {fusion_result.alert_level.upper()}",
            f"Risk Score    : {fusion_result.risk_score}/100",
            f"AI Insight    : {fusion_result.combined_insight}",
            "",
            "Sensor Readings (IoT Kit)",
            "-" * 40,
            f"  {fusion_result.soil_advice}",
            "",
            "Immediate Actions",
            "-" * 40,
        ]
        for a in fusion_result.immediate_actions:
            report_lines.append(f"  • {a}")
        report_lines += ["", "Treatment", "-" * 40]
        for t in fusion_result.treatment:
            report_lines.append(f"  • {t}")
        report_lines += ["", "Prevention", "-" * 40]
        for p in fusion_result.prevention:
            report_lines.append(f"  • {p}")
        report_lines += [
            "",
            "Irrigation",
            "-" * 40,
            f"  {fusion_result.irrigation_fix}",
            "",
            "Fertiliser",
            "-" * 40,
            f"  {fusion_result.fertiliser_fix}",
        ]
    elif avg_7d and avg_7d.get("avg_temperature") is not None:
        report_lines += [
            "",
            "Sensor Summary (7-Day Average)",
            "-" * 40,
            f"Avg Temperature (7d) : {avg_7d.get('avg_temperature'):.1f}°C",
            f"Avg Humidity (7d)    : {avg_7d.get('avg_humidity'):.1f}%",
            f"Avg Soil Moisture(7d): {avg_7d.get('avg_soil_moisture'):.1f}%",
            f"High Humid Duration  : {durations.get('humidity_high_hours'):.1f} hours",
            f"Soil Wet Duration    : {durations.get('soil_wet_hours'):.1f} hours",
        ]

    st.download_button(
        "📄 Download Full Report",
        data="\n".join(report_lines),
        file_name=f"diagnosis_{datetime.now().strftime('%Y%m%d_%H%M')}.txt",
        mime="text/plain",
    )

# ============================================================
# PAGE 2: SOIL MONITOR
# ============================================================

def page_soil_monitor():
    st.title("🌱 Live Soil Health Monitor")
    st.caption(f"Device: {device_id} | Auto-refreshes when new data arrives")

    # Embed AgriSense Dashboard from ardino_proj with explicit permissions for Web Serial & Geolocation
    import streamlit.components.v1 as components
    components.html(
        '''
        <iframe src="https://cropguard-ai-1-ys7p.onrender.com" style="width:100%; height:850px; border:none;" allow="serial; geolocation"></iframe>
        ''',
        height=860
    )



# ============================================================
# PAGE 4: HISTORY
# ============================================================

def page_history():
    st.title("📄 Detection & Soil History")
    st.caption("Showing data from the last 7 days only — older records are auto-purged")

    tab1, tab2 = st.tabs(["Disease Predictions", "Soil Readings"])

    MAX_DISEASE_ROWS = 100   # cap disease history to last 100 entries
    MAX_SOIL_HOURS   = 48    # only show last 48h of soil data
    MAX_SOIL_ROWS    = 500   # hard cap on soil rows

    with tab1:
        conn = sqlite3.connect("soil_data.db")
        try:
            df = pd.read_sql(
                f"SELECT * FROM disease_predictions ORDER BY timestamp DESC LIMIT {MAX_DISEASE_ROWS}",
                conn
            )
            if df.empty:
                st.info("No disease predictions recorded yet.")
            else:
                if 'timestamp' in df.columns:
                    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.strftime('%d %b %Y, %H:%M:%S')
                st.dataframe(df, use_container_width=True)
                csv = df.to_csv(index=False)
                st.download_button("⬇️ Download CSV", csv, "disease_history.csv", "text/csv")
        except Exception:
            st.info("No history yet — run a diagnosis first.")
        finally:
            conn.close()

    with tab2:
        history = get_readings_history(hours=MAX_SOIL_HOURS, device_id=device_id)
        if not history:
            st.info("No soil readings yet.")
        else:
            df = pd.DataFrame(history).tail(MAX_SOIL_ROWS)  # hard cap
            # Remove columns that do not have real sensor data from the Arduino
            cols_to_drop = ['air_quality_pct', 'high_ammonia', 'pressure_hpa', 'altitude_m', 'risk_score']
            df = df.drop(columns=[c for c in cols_to_drop if c in df.columns])
            
            if 'created_at' in df.columns:
                df['created_at'] = pd.to_datetime(df['created_at']).dt.strftime('%d %b %Y, %H:%M:%S')
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp']).dt.strftime('%d %b %Y, %H:%M:%S')

            st.dataframe(df, use_container_width=True)
            csv = df.to_csv(index=False)
            st.download_button("⬇️ Download CSV", csv, "soil_history.csv", "text/csv")

# ============================================================
# PAGE 5: PREFERENCES
# ============================================================

def page_preferences():
    st.title("⚙️ System Preferences")
    st.caption("Configure thresholds, sensor calibration, and API credentials")

    # --- Load current settings ---
    try:
        base_url = "http://127.0.0.1:5000/api"
        th = requests.get(f"{base_url}/thresholds", timeout=2).json()
        cal = requests.get(f"{base_url}/calibration", timeout=2).json()
        cfg = requests.get(f"{base_url}/config", timeout=2).json()
    except Exception as e:
        st.error(f"⚠️ Could not connect to Dashboard API: {e}")
        st.info("Make sure the backend is running (python run_all.py)")
        return

    # --- Section 1: Weather & Gemini ---
    st.subheader("🌐 Weather Configuration")
    with st.container(border=True):
        new_city = st.text_input("City Name", value=cfg.get("city", ""))
        
        if st.button("Save Weather Config", type="primary"):
            payload = {"city": new_city}
            res = requests.post(f"{base_url}/config", json=payload)
            if res.status_code == 200:
                st.success("✅ Weather configuration saved!")
                st.rerun()
            else:
                st.error(f"Error: {res.json().get('message')}")

    # --- Section 2: Alert Thresholds ---
    st.subheader("⚠️ Alert Thresholds")
    with st.container(border=True):
        st.caption("Alerts trigger when sensor values fall outside these ranges.")
        c1, c2, c3 = st.columns(3)
        
        sm_min = c1.number_input("Soil Moisture Min (%)", value=int(th['soil_moisture']['min']))
        sm_max = c1.number_input("Soil Moisture Max (%)", value=int(th['soil_moisture']['max']))
        
        temp_min = c2.number_input("Temperature Min (°C)", value=int(th['temperature']['min']))
        temp_max = c2.number_input("Temperature Max (°C)", value=int(th['temperature']['max']))
        
        hum_min = c3.number_input("Humidity Min (%)", value=int(th['humidity']['min']))
        hum_max = c3.number_input("Humidity Max (%)", value=int(th['humidity']['max']))
        
        if st.button("Save Thresholds"):
            payload = {
                "soil_moisture": {"min": sm_min, "max": sm_max},
                "temperature":   {"min": temp_min, "max": temp_max},
                "humidity":      {"min": hum_min, "max": hum_max}
            }
            requests.post(f"{base_url}/thresholds", json=payload)
            st.success("✅ Thresholds updated!")

    # --- Section 3: Calibration ---
    st.subheader("🔧 Sensor Calibration")
    with st.container(border=True):
        st.caption("Offsets added to raw sensor values to correct errors.")
        c1, c2, c3 = st.columns(3)
        cal_sm = c1.number_input("Soil Moisture Offset", value=float(cal.get('soil_moisture', 0)), step=0.1)
        cal_temp = c2.number_input("Temperature Offset", value=float(cal.get('temperature', 0)), step=0.1)
        cal_hum = c3.number_input("Humidity Offset", value=float(cal.get('humidity', 0)), step=0.1)
        
        if st.button("Apply Calibration"):
            payload = {"soil_moisture": cal_sm, "temperature": cal_temp, "humidity": cal_hum}
            requests.post(f"{base_url}/calibration", json=payload)
            st.success("✅ Calibration offsets applied!")

# ============================================================
# ROUTER
# ============================================================

if   page == "🔬 Disease Detection":
    page_disease_detection()
elif page == "🌱 Soil Monitor":
    page_soil_monitor()
elif page == "📄 History":
    page_history()
elif page == "⚙️ Preferences":
    page_preferences()
