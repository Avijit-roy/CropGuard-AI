<p align="center">
  <img src="https://cdn-icons-png.flaticon.com/512/2917/2917995.png" width="80" alt="CropGuard Logo"/>
</p>

<h1 align="center">🌿 CropGuard AI</h1>
<p align="center">
  <b>Intelligent Plant Disease Detection &amp; IoT Soil Health Monitoring System</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/TensorFlow-2.12+-orange?logo=tensorflow&logoColor=white" alt="TensorFlow"/>
  <img src="https://img.shields.io/badge/Flask-3.0+-black?logo=flask&logoColor=white" alt="Flask"/>
  <img src="https://img.shields.io/badge/Arduino-Uno_R3-teal?logo=arduino&logoColor=white" alt="Arduino"/>
  <img src="https://img.shields.io/badge/MQTT-HiveMQ-purple?logo=eclipsemosquitto&logoColor=white" alt="MQTT"/>
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License"/>
</p>

---

## 🔗 Live Application
Access the production dashboard here:  
**🚀 [https://cropguard-ai-1-ys7p.onrender.com](https://cropguard-ai-1-ys7p.onrender.com)**

---

## 📖 Overview

**CropGuard AI** is a full-stack agricultural intelligence platform that combines **deep learning-based plant disease detection** with **real-time IoT soil monitoring** and **external weather data** to deliver actionable farming insights.

The system fuses three data sources — an **AI image classifier**, **live Arduino sensor readings**, and **Open-Meteo weather forecasts** — through a rule-based **Fusion Engine** that produces crop-specific risk scores, treatment plans, and irrigation recommendations.

Recently upgraded from a Streamlit prototype to a **fully unified, production-ready Flask Web Application**, CropGuard AI now features a responsive, dynamic UI with direct hardware connectivity right from your browser.

### ✨ Key Features

| Feature | Description |
|---|---|
| 🔬 **AI Disease Detection** | Client-side image cropping + server-side MobileNetV2 classification |
| 🔌 **Web Serial Connect** | Browser-level hardware connection filters & reads Arduino USB directly |
| 📡 **Live IoT Monitoring** | Robust Python background publisher auto-detects ports & heals from unplug/replug events |
| 🌦️ **Dynamic Weather** | Uses Browser Geolocation GPS to pull hyper-local real-time weather & AQI dynamically |
| 🧬 **Fusion Engine** | Cross-references disease diagnosis with 7-day sensor trends + weather to generate risk scores |
| 📊 **Interactive Dashboard** | Custom HTML/JS/CSS with real-time Chart.js graphs, gauge cards & threshold alerts |
| 📄 **Report Generation** | Export sensor data as CSV or professional PDF reports from the dashboard |
| 🗃️ **Auto Data Retention** | Background thread purges records older than 7 days and auto-vacuums the database |

---

## 🛠️ How to Use

### 1. Connecting Your Hardware
*   **Plug and Play:** Connect your Arduino Uno (with the flashed firmware) to your system via USB.
*   **Manual Connection:** If the dashboard does not automatically display sensor readings, use the **"🔌 Connect USB Sensor"** button located in the bottom-right corner of the interface. This uses the Web Serial API to bridge your hardware directly to the browser.
*   **Stabilization Period:** After connecting, reconnecting, or refreshing, please **wait for approximately 1 minute** for the system to stabilize, establish a consistent data stream, and update the "Online/Offline" status indicators.

### 2. Disease Diagnosis
*   Navigate to the **Microscope** icon (Disease Detection) tab.
*   Upload or drag-and-drop a photo of a plant leaf.
*   Use the interactive cropper to focus on the affected area and click **"Run AI Diagnosis"**.
*   View the combined report which includes the AI's confidence score and the **Fusion Engine**'s environmental risk analysis.

### 3. Monitoring & Reports
*   Check the **Live Soil Monitor** for real-time graphs of temperature, humidity, and soil moisture.
*   The system **automatically stores your data for up to 7 days**. Older data is purged automatically to maintain system performance.
*   Export your findings using the **"Export CSV"** or **"Download PDF Report"** buttons in the History tab.

---

## 🏗️ System Architecture & Pipeline

### 1. Data Acquisition (Hardware)
The **Arduino Uno R3** acts as the edge node, polling data from DHT11 (Air) and HL-69 (Soil) sensors every 2 seconds. This data is packaged as a JSON string and broadcasted over the Serial USB interface.

### 2. Data Ingestion (Dual Pipeline)
*   **Backend Reader:** A local Python script (`serial_reader.py`) can run in the background, auto-detecting COM/tty ports and pushing data to the cloud API.
*   **Web Serial Bridge:** For a seamless experience without local setup, the frontend (`app.js`) uses the **Web Serial API** to read the USB port directly from the browser and sync it to the database.

### 3. Processing & Storage
The **Flask Backend** serves as the central orchestrator:
*   **SQLite Database:** Stores all historical readings, disease predictions, and user preferences.
*   **Retention Worker:** A background thread monitors the database and purges records older than **168 hours (7 days)** to ensure high performance and low storage overhead.

### 4. Intelligence (Fusion Engine)
The core logic resides in `fusion_engine.py`. It evaluates the **Cumulative Environmental Stress**:
*   **Disease Model:** MobileNetV2 (TensorFlow) classifies the leaf pathogen.
*   **Soil Context:** Fetches 24-hour averages of soil moisture and humidity to check if conditions favor fungal growth.
*   **Weather Context:** Integrates live precipitation and temperature forecasts from Open-Meteo.
*   **Outcome:** Generates a unified **Risk Score (0-100)** and a prioritized action list.

---

## 📁 Project Structure

```text
CropGuard-AI/
│
├── run_all.py                      # 🚀 Master launcher — starts services
├── requirements.txt                # Python dependencies
├── backend/
│   ├── server.py                   # Central server (DB, Retention, API Launcher)
│   ├── dashboard_api.py            # Flask REST Endpoints & Routing
│   ├── fusion_engine.py            # AI + Environmental logic
│   ├── database.py                 # SQLite abstractions
│   └── serial_reader.py            # USB-to-Cloud bridge script
│
├── frontend/
│   ├── static/
│   │   ├── css/                    # Glassmorphism design system
│   │   └── js/app.js               # SPA logic, Charts, Web Serial API
│   └── templates/                  # Jinja2 HTML templates
│
├── iot/
│   └── arduino_sensor.ino          # Arduino C++ firmware
│
├── soil_data.db                    # 🗃️ SQLite database (auto-created)
└── plant_disease_model_new.keras   # 🧠 Trained AI Model
```

---

## ⚙️ Hardware Requirements

| Component | Model | Pin | Purpose |
|---|---|---|---|
| **Microcontroller** | Arduino Uno R3 | — | Central controller |
| **Temp/Humidity Sensor** | DHT11 | D2 | Air temperature & humidity |
| **Soil Moisture Sensor** | HL-69 (Analog) | A0 | Soil moisture level (0–100%) |
| **Alert LED** | Standard LED | D8 | Visual alert indicator |
| **Alert Buzzer** | Passive Buzzer | D9 | Audible alert |

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.10+** with `pip`
- **Arduino IDE** (to flash the firmware)
- **Arduino Uno R3** with sensors connected
- **USB Cable** connecting Arduino to your machine
- **Google Chrome or Edge** (required for Web Serial API features)

### 1. Clone & Setup Environment

```bash
git clone <your-repo-url>
cd plant_disease_iot_v3
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows
pip install -r requirements.txt
```

### 2. Flash Arduino Firmware

1. Open `iot/arduino_sensor.ino` in **Arduino IDE**
2. Install the **DHT sensor library** (by Adafruit) via Library Manager
3. Select **Arduino Uno** board and the correct serial port
4. Click **Upload**

### 3. Launch the System

```bash
python run_all.py
```

This single command starts:
1. `mqtt_subscriber.py` — The MQTT listener, SQLite DB manager, and the embedded **Flask API server** (Port 5000).
2. `mqtt_publisher.py` — The background hardware scanner that bridges Arduino Serial to the cloud.

### 4. Access the Dashboard

Open your browser (Chrome/Edge recommended) to:
**[https://cropguard-ai-1-ys7p.onrender.com](https://cropguard-ai-1-ys7p.onrender.com)**

---

## 📡 API Reference

All REST endpoints are served by the Flask API on **port 5000**.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/sensor` | Latest sensor reading + connection status |
| `POST` | `/api/sensor/upload` | Upload dynamic Web Serial client sensor readings to SQLite |
| `POST` | `/api/predict` | Run MobileNetV2 inference on an uploaded image crop |
| `GET` | `/api/history?hours=N` | Sensor history (max 168h / 7 days) |
| `GET` | `/api/weather?lat=X&lon=Y` | Local weather, hourly/daily forecasts |
| `GET` | `/api/insights?lat=X&lon=Y` | Rule-based crop insights with health score |
| `GET` | `/api/thresholds` | Current alert thresholds |
| `POST` | `/api/thresholds` | Update alert thresholds |
| `GET` | `/api/calibration` | Current sensor calibration offsets |
| `POST` | `/api/calibration` | Update calibration offsets |
| `GET` | `/api/export/csv?hours=N` | Download sensor data as CSV |
| `GET` | `/api/export/pdf?hours=N` | Download full PDF report |

---

## 🧪 Tech Stack

| Layer | Technology |
|---|---|
| **AI Model** | TensorFlow/Keras (MobileNetV2), OpenCV |
| **Backend Web Framework** | Flask, Jinja2, Flask-CORS |
| **Frontend Utilities** | Chart.js, Cropper.js, Web Serial API, Geolocation API |
| **IoT Hardware** | Arduino Uno R3, DHT11, HL-69 Soil Sensor |
| **Communication** | MQTT (paho-mqtt) via HiveMQ public broker |
| **Serial I/O** | PySerial (9600 baud) |
| **Database** | SQLite3 |
| **Weather** | Open-Meteo API (free, no key) |
| **Styling** | Custom Vanilla CSS (Glassmorphism, CSS Variables) |

---

## 📄 License

This project is for educational and research purposes.

---

<p align="center">
  Built with 💚 for smarter agriculture
</p>
