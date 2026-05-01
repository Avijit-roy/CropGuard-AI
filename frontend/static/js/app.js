// ─── State ─────────────────────────────────────────
const state = {
  sensor: { soil_moisture: 0, temperature: 0, humidity: 0, timestamp: null, connected: false },
  weather: null,
  history: [],
  insights: [],
  thresholds: { soil_moisture: {min:30,max:80}, temperature: {min:10,max:35}, humidity: {min:40,max:80} },
  range: 1,
  currentView: 'overview'
};

const BASE = window.location.origin;

let lastFetchTime = null;
setInterval(() => {
  if (!lastFetchTime) return;
  const seconds = Math.floor((new Date() - lastFetchTime) / 1000);
  let timeText = `Last updated: ${seconds} seconds ago`;
  if (seconds > 60) {
    timeText = `Last updated: ${Math.floor(seconds/60)} minutes ago`;
  }
  document.getElementById('last-update').textContent = timeText;
  
  if (seconds > 30) {
     document.getElementById('live-dot').style.animation = 'none';
     document.getElementById('live-dot').style.background = 'var(--red)';
  } else {
     document.getElementById('live-dot').style.animation = 'pulse 2s infinite';
     document.getElementById('live-dot').style.background = 'var(--green)';
  }
}, 1000);

// ─── Chart Instances ───────────────────────────────
let miniSm, miniTemp, miniHum, fullSm, fullTemp, fullHum;

const chartDefaults = (color, label) => ({
  type: 'line',
  data: { labels: [], datasets: [{ label, data: [], borderColor: color,
    backgroundColor: color + '33', fill: true, tension: 0.4, pointRadius: 3, pointHoverRadius: 6,
    borderWidth: 3 }] },
  options: {
    responsive: true, maintainAspectRatio: false, animation: { duration: 300 },
    scales: {
      x: { display: false },
      y: { grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { color: '#718096', font: { size: 11, family: 'DM Mono' } } }
    },
    plugins: { legend: { display: false }, tooltip: {
      backgroundColor: '#ffffff', borderColor: '#e2e8f0', borderWidth: 1,
      titleColor: '#1a202c', bodyColor: '#4a5568', bodyFont: {size: 14, weight: 'bold'},
      padding: 12, cornerRadius: 8,
      callbacks: { 
        title: (items) => {
          const chart = items[0].chart;
          const index = items[0].dataIndex;
          const fullTs = chart.data.fullTimestamps ? chart.data.fullTimestamps[index] : null;
          if (fullTs) {
            const dt = new Date(fullTs);
            return dt.toLocaleString('en-GB', { day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit' });
          }
          return items[0].label;
        }
      }
    }}
  }
});

function initCharts() {
  miniSm   = new Chart(document.getElementById('mini-sm'),   chartDefaults('#4ade80', 'Soil Moisture'));
  miniTemp = new Chart(document.getElementById('mini-temp'), chartDefaults('#f87171', 'Temperature'));
  miniHum  = new Chart(document.getElementById('mini-hum'),  chartDefaults('#60a5fa', 'Humidity'));
  fullSm   = new Chart(document.getElementById('chart-sm'),   {...chartDefaults('#4ade80', 'Soil Moisture %'), options: {...chartDefaults('#4ade80','').options}});
  fullTemp = new Chart(document.getElementById('chart-temp'), {...chartDefaults('#f87171', 'Temperature °C'), options: {...chartDefaults('#f87171','').options}});
  fullHum  = new Chart(document.getElementById('chart-hum'),  {...chartDefaults('#60a5fa', 'Humidity %'), options: {...chartDefaults('#60a5fa','').options}});

  fullSm.options.scales.x = { display: true, ticks: { color: '#6b8f65', maxRotation: 45, font: { size: 9, family: 'DM Mono' }, maxTicksLimit: 12 } };
  fullTemp.options.scales.x = fullSm.options.scales.x;
  fullHum.options.scales.x = fullSm.options.scales.x;
}

function pushChart(chart, label, value, maxPoints=40, fullTs=null) {
  chart.data.labels.push(label);
  chart.data.datasets[0].data.push(value);
  if (!chart.data.fullTimestamps) chart.data.fullTimestamps = [];
  chart.data.fullTimestamps.push(fullTs);

  if (chart.data.labels.length > maxPoints) {
    chart.data.labels.shift();
    chart.data.datasets[0].data.shift();
    chart.data.fullTimestamps.shift();
  }
  chart.update('none');
}

function loadHistoryToCharts(history) {
  [fullSm, fullTemp, fullHum].forEach(c => {
    c.data.labels = []; 
    c.data.datasets[0].data = []; 
    c.data.fullTimestamps = [];
  });
  
  history.forEach(r => {
    let lbl = r.timestamp.substring(11,16); 
    // If range is > 24h (Last 7d), include date in label
    if (state.range > 24) {
      const dt = new Date(r.timestamp);
      lbl = dt.toLocaleDateString('en-GB', { day:'2-digit', month:'short' }) + ' ' + lbl;
    }
    
    fullSm.data.labels.push(lbl);
    fullSm.data.datasets[0].data.push(r.soil_moisture);
    fullSm.data.fullTimestamps.push(r.timestamp);

    fullTemp.data.labels.push(lbl);
    fullTemp.data.datasets[0].data.push(r.temperature);
    fullTemp.data.fullTimestamps.push(r.timestamp);

    fullHum.data.labels.push(lbl);
    fullHum.data.datasets[0].data.push(r.humidity);
    fullHum.data.fullTimestamps.push(r.timestamp);
  });
  fullSm.update(); fullTemp.update(); fullHum.update();
}

// ─── Fetch Data ────────────────────────────────────
async function fetchSensor() {
  try {
    const r = await fetch(BASE + '/api/sensor');
    if (!r.ok) throw new Error("API error");
    const d = await r.json();
    state.sensor = d;
    
    document.getElementById('loading-overlay').style.opacity = '0';
    setTimeout(() => document.getElementById('loading-overlay').style.display = 'none', 300);
    document.getElementById('error-banner').style.display = 'none';

    updateSensorUI(d);
    const lbl = new Date().toLocaleTimeString('en', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
    lastFetchTime = new Date();
    const smVal = d.connected ? d.soil_moisture : 0;
    const tempVal = d.connected ? d.temperature : 0;
    const humVal = d.connected ? d.humidity : 0;
    
    const fullTs = d.timestamp || new Date().toISOString();
    pushChart(miniSm,   lbl, smVal, 40, fullTs);
    pushChart(miniTemp, lbl, tempVal, 40, fullTs);
    pushChart(miniHum,  lbl, humVal, 40, fullTs);
    document.getElementById('mini-sm-val').textContent   = smVal.toFixed(1) + '%';
    document.getElementById('mini-temp-val').textContent = tempVal.toFixed(1) + '°C';
    document.getElementById('mini-hum-val').textContent  = humVal.toFixed(1) + '%';
    document.getElementById('last-update').textContent   = 'Last update: ' + lbl;
    const dot = document.getElementById('serial-dot');
    document.getElementById('serial-label').textContent = d.connected ? 'Sensor Active' : 'Sensor Inactive';
    dot.classList.toggle('ok', d.connected);
  } catch(e) {
    console.error('sensor fetch:', e);
    document.getElementById('error-banner').style.display = 'flex';
    document.getElementById('loading-overlay').style.display = 'none';
  }
}

function updateSensorUI(d) {
  const minSM = state.thresholds.soil_moisture.min;
  const maxSM = state.thresholds.soil_moisture.max;
  const minTemp = state.thresholds.temperature.min;
  const maxTemp = state.thresholds.temperature.max;
  const minHum = state.thresholds.humidity.min;
  const maxHum = state.thresholds.humidity.max;

  document.getElementById('sm-opt-label').textContent = `Opt: ${minSM}-${maxSM}%`;
  document.getElementById('temp-opt-label').textContent = `Opt: ${minTemp}-${maxTemp}°`;
  document.getElementById('hum-opt-label').textContent = `Opt: ${minHum}-${maxHum}%`;

  const smVal = d.connected ? d.soil_moisture : 0;
  const tempVal = d.connected ? d.temperature : 0;
  const humVal = d.connected ? d.humidity : 0;

  setMetric('sm',   smVal, '%',  'val-sm',   minSM, maxSM,  100, 'bar-sm',   'badge-sm', 'card-sm');
  setMetric('temp', tempVal,   '°C', 'val-temp', minTemp, maxTemp,  50,  'bar-temp', 'badge-temp', 'card-temp');
  setMetric('hum',  humVal,      '%',  'val-hum',  minHum, maxHum,  100, 'bar-hum',  'badge-hum', 'card-hum');

  if (d.connected) {
    if (d.soil_moisture < minSM) showAlert('Low Soil Moisture', `Value is ${d.soil_moisture}%, below minimum ${minSM}%.`, 'warning');
    if (d.temperature < minTemp) showAlert('Low Temperature', `Value is ${d.temperature}°C, below minimum ${minTemp}°C.`, 'warning');
    if (d.humidity < minHum) showAlert('Low Humidity', `Value is ${d.humidity}%, below minimum ${minHum}%.`, 'warning');
  }
}

function setMetric(key, val, unit, valId, min, max, scale, barId, badgeId, cardId) {
  const el = document.getElementById(valId);
  el.innerHTML = val.toFixed(1) + `<span class="metric-unit">${unit}</span>`;
  const pct = Math.min(100, (val / scale) * 100);
  const bar = document.getElementById(barId);
  bar.style.width = pct + '%';
  const badge = document.getElementById(badgeId);
  const card = document.getElementById(cardId);
  
  if (val < min) {
    badge.className = 'metric-badge badge-warn'; badge.textContent = '● LOW';
    bar.style.backgroundColor = 'var(--amber)'; card.style.setProperty('--card-accent', 'var(--amber)');
  } else if (val > max) {
    badge.className = 'metric-badge badge-danger'; badge.textContent = '● HIGH';
    bar.style.backgroundColor = 'var(--red)'; card.style.setProperty('--card-accent', 'var(--red)');
  } else {
    badge.className = 'metric-badge badge-ok'; badge.textContent = '● OPTIMAL';
    bar.style.backgroundColor = 'var(--green)'; card.style.setProperty('--card-accent', 'var(--green)');
  }
}

async function fetchWeather() {
  try {
    const r = await fetch(BASE + '/api/weather');
    const d = await r.json();
    state.weather = d;
    updateWeatherUI(d);
  } catch(e) { console.error('weather fetch:', e); }
}

const WMO_CODES = {
  0: { icon: '☀️', desc: 'Clear sky' },
  1: { icon: '🌤️', desc: 'Mainly clear' },
  2: { icon: '⛅', desc: 'Partly cloudy' },
  3: { icon: '☁️', desc: 'Overcast' },
  45: { icon: '🌫️', desc: 'Fog' },
  48: { icon: '🌫️', desc: 'Depositing rime fog' },
  51: { icon: '🌧️', desc: 'Drizzle: Light' },
  53: { icon: '🌧️', desc: 'Drizzle: Moderate' },
  55: { icon: '🌧️', desc: 'Drizzle: Dense' },
  61: { icon: '🌧️', desc: 'Rain: Slight' },
  63: { icon: '🌧️', desc: 'Rain: Moderate' },
  65: { icon: '🌧️', desc: 'Rain: Heavy' },
  71: { icon: '❄️', desc: 'Snow: Slight' },
  73: { icon: '❄️', desc: 'Snow: Moderate' },
  75: { icon: '❄️', desc: 'Snow: Heavy' },
  77: { icon: '❄️', desc: 'Snow grains' },
  80: { icon: '🌦️', desc: 'Rain showers: Slight' },
  81: { icon: '🌦️', desc: 'Rain showers: Moderate' },
  82: { icon: '🌦️', desc: 'Rain showers: Violent' },
  85: { icon: '🌨️', desc: 'Snow showers: Slight' },
  86: { icon: '🌨️', desc: 'Snow showers: Heavy' },
  95: { icon: '⛈️', desc: 'Thunderstorm' },
  96: { icon: '⛈️', desc: 'Thunderstorm with slight hail' },
  99: { icon: '⛈️', desc: 'Thunderstorm with heavy hail' },
};

function getWmo(code) {
  return WMO_CODES[code] || { icon: '☁️', desc: 'Unknown' };
}

function updateWeatherUI(d) {
  const c = d.current;
  if (!c) return;
  
  const wmo = getWmo(c.weather_code);
  
  document.getElementById('w-city').textContent    = d.city || '—';
  document.getElementById('w-temp').textContent    = c.temp?.toFixed(1) || '—';
  document.getElementById('w-icon').textContent    = wmo.icon;
  document.getElementById('w-desc').textContent    = wmo.desc;
  document.getElementById('w-feels').textContent   = c.feels_like?.toFixed(1) || '—';
  
  document.getElementById('w-wind').textContent    = (c.wind_speed || 0) + ' km/h';
  document.getElementById('w-hum').textContent     = (c.humidity || 0) + '%';
  document.getElementById('w-press').textContent   = (c.pressure || 0) + ' hPa';
  document.getElementById('w-cloud').textContent   = (c.cloud_cover || 0) + '%';
  document.getElementById('w-uv').textContent      = (c.uv_index || 0).toFixed(1);
  
  const aqiBadge = document.getElementById('w-aqi-badge');
  const aqi = c.aqi || 0;
  aqiBadge.textContent = aqi + ' US AQI';
  aqiBadge.className = 'aqi-badge';
  if (aqi <= 50) aqiBadge.classList.add('aqi-good');
  else if (aqi <= 100) aqiBadge.classList.add('aqi-mod');
  else aqiBadge.classList.add('aqi-poor');

  document.getElementById('weather-fetched').textContent = 'Fetched at ' + (d.fetched_at || '').substring(11, 16) + (d.mock ? ' (cached)' : '');

  // Severe Weather Alerts
  const alertContainer = document.getElementById('severe-alert-container');
  if (alertContainer) {
    alertContainer.innerHTML = '';
    if (d.alerts && d.alerts.length > 0) {
      d.alerts.forEach(a => {
        const bg = a.type === 'danger' ? 'var(--red)' : 'var(--amber)';
        const color = a.type === 'danger' ? '#fff' : '#000';
        alertContainer.innerHTML += `
          <div style="background:${bg}; color:${color}; padding:12px 16px; border-radius:8px; margin-bottom:16px; font-weight:600; display:flex; align-items:center; gap:12px; box-shadow:0 4px 12px rgba(0,0,0,0.15);">
            <span style="font-size:20px;">⚠️</span>
            <span>${a.message}</span>
          </div>
        `;
      });
    }
  }

  // Hourly
  const hScroll = document.getElementById('hourly-scroll');
  hScroll.innerHTML = '';
  (d.hourly || []).forEach(h => {
    const time = h.time ? h.time.substring(11,16) : '—';
    const hwmo = getWmo(h.weather_code);
    hScroll.innerHTML += `<div class="w-hourly-item">
      <div class="w-hourly-time">${time}</div>
      <div class="w-hourly-icon">${hwmo.icon}</div>
      <div class="w-hourly-temp">${h.temp?.toFixed(0)}°</div>
      <div class="w-hourly-pop">💧 ${h.precip_prob}%</div>
    </div>`;
  });

  // Daily
  const dList = document.getElementById('daily-list');
  dList.innerHTML = '';
  (d.daily || []).forEach(day => {
    const date = new Date(day.date);
    const dayName = date.toLocaleDateString('en-US', { weekday: 'short' });
    const dwmo = getWmo(day.weather_code);
    dList.innerHTML += `<div class="w-daily-item">
      <div class="w-daily-day">${dayName}</div>
      <div class="w-daily-icon">${dwmo.icon}</div>
      <div class="w-daily-temps">
        <span class="w-daily-max">${day.temp_max?.toFixed(0)}°</span>
        <span class="w-daily-min">${day.temp_min?.toFixed(0)}°</span>
      </div>
    </div>`;
  });
  
  // Sun & Moon
  if (d.daily && d.daily.length > 0) {
    const today = d.daily[0];
    document.getElementById('w-sunrise').textContent = today.sunrise ? today.sunrise.substring(11,16) : '—';
    document.getElementById('w-sunset').textContent = today.sunset ? today.sunset.substring(11,16) : '—';
  }
}

async function fetchInsights() {
  const loader = document.getElementById('fusion-loader');
  const container = document.getElementById('full-insights');
  
  if (state.currentView === 'insights') {
    if (loader) loader.style.display = 'block';
    if (container) container.style.opacity = '0.3';
  }

  try {
    const r = await fetch(BASE + '/api/insights');
    if (!r.ok) throw new Error("Insights API unreachable");
    const d = await r.json();
    state.insights = d.insights;
    
    renderInsights(d.insights, 'quick-insights', 3);
    renderInsights(d.insights, 'full-insights', 999);
  } catch(e) {
    console.error('insights fetch:', e);
  } finally {
    if (loader) loader.style.display = 'none';
    if (container) container.style.opacity = '1';
  }
}

function renderInsights(insights, containerId, limit) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = insights.slice(0, limit).map(ins => `
    <div class="insight-card ${ins.level}">
      <div class="insight-icon">${ins.icon}</div>
      <div>
        <div class="insight-title">${ins.title}</div>
        <div class="insight-msg">${ins.message}</div>
      </div>
    </div>`).join('');
}

async function fetchHistory() {
  try {
    const r = await fetch(BASE + `/api/history?hours=${state.range}`);
    const d = await r.json();
    state.history = d;
    loadHistoryToCharts(d);
  } catch(e) {}
}

// ─── Alerts ────────────────────────────────────────
const shownAlerts = new Set();
function showAlert(title, msg, type='warning') {
  const key = title + type;
  if (shownAlerts.has(key)) return;
  shownAlerts.add(key);
  setTimeout(() => shownAlerts.delete(key), 30000);
  const bar = document.getElementById('alert-bar');
  const id = 'al-' + Date.now();
  const colors = { warning: '⚠️', danger: '🚨', success: '✅', info: 'ℹ️' };
  bar.innerHTML += `<div class="alert-item ${type}" id="${id}">
    <span>${colors[type]||'⚠️'}</span>
    <div><strong>${title}</strong><br><span style="font-size:12px;color:var(--text2)">${msg}</span></div>
    <span class="alert-close" onclick="document.getElementById('${id}').remove()">×</span>
  </div>`;
  setTimeout(() => document.getElementById(id)?.remove(), 8000);
}

// ─── Navigation ────────────────────────────────────
const VIEW_TITLES = {
  overview: '🌿 Your Field — CropGuard AI',
  charts:   '📈 Sensor History — CropGuard AI',
  weather:  '🌦️ Weather — CropGuard AI',
  insights: '🧠 AI Insights — CropGuard AI',
  reports:  '📁 Reports — CropGuard AI',
  settings: '⚙️ Settings — CropGuard AI',
};

function showView(name) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('view-' + name).classList.add('active');
  document.querySelectorAll('.nav-item').forEach(n => {
    if (n.getAttribute('onclick')?.includes(`'${name}'`)) n.classList.add('active');
  });
  state.currentView = name;
  document.title = VIEW_TITLES[name] || 'CropGuard AI — AgriSense';
  if (name === 'charts') fetchHistory();
  if (name === 'weather') fetchWeather();
  if (name === 'insights') fetchInsights();
  if (name === 'settings') loadSettings();
}

function setRange(h, el) {
  state.range = h;
  document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  fetchHistory();
}

// ─── Reports ───────────────────────────────────────
function downloadCSV() {
  const h = document.getElementById('report-hours').value;
  window.open(BASE + `/api/export/csv?hours=${h}`);
}
function downloadPDF() {
  const h = document.getElementById('report-hours').value;
  window.open(BASE + `/api/export/pdf?hours=${h}`);
}
function quickExportCSV() {
  const rows = [['Timestamp', 'Soil Moisture %', 'Temperature C', 'Humidity %']];
  rows.push([new Date().toISOString(), state.sensor.soil_moisture, state.sensor.temperature, state.sensor.humidity]);
  const csv = rows.map(r => r.join(',')).join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = 'agri_quick_export.csv'; a.click();
}

// ─── Settings ──────────────────────────────────────
async function loadSettings() {
  try {
    const [th, cal] = await Promise.all([
      fetch(BASE + '/api/thresholds').then(r => r.json()),
      fetch(BASE + '/api/calibration').then(r => r.json())
    ]);
    document.getElementById('th-sm-min').value = th.soil_moisture?.min || 30;
    document.getElementById('th-sm-max').value = th.soil_moisture?.max || 80;
    document.getElementById('th-temp-min').value = th.temperature?.min || 10;
    document.getElementById('th-temp-max').value = th.temperature?.max || 35;
    document.getElementById('th-hum-min').value = th.humidity?.min || 40;
    document.getElementById('th-hum-max').value = th.humidity?.max || 80;
    document.getElementById('cal-sm').value = cal.soil_moisture || 0;
    document.getElementById('cal-temp').value = cal.temperature || 0;
    document.getElementById('cal-hum').value = cal.humidity || 0;
    
    // We don't get the keys back for security, but we can set placeholders
    document.getElementById('cfg-city').value = state.weather?.city || '';
  } catch(e) {}
}

async function saveThresholds() {
  const data = {
    soil_moisture: { min: +document.getElementById('th-sm-min').value, max: +document.getElementById('th-sm-max').value },
    temperature:   { min: +document.getElementById('th-temp-min').value, max: +document.getElementById('th-temp-max').value },
    humidity:      { min: +document.getElementById('th-hum-min').value, max: +document.getElementById('th-hum-max').value }
  };
  await fetch(BASE + '/api/thresholds', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) });
  showAlert('Thresholds Saved', 'Alert thresholds have been updated.', 'success');
}

async function saveCalibration() {
  const data = {
    soil_moisture: +document.getElementById('cal-sm').value,
    temperature:   +document.getElementById('cal-temp').value,
    humidity:      +document.getElementById('cal-hum').value
  };
  await fetch(BASE + '/api/calibration', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) });
  showAlert('Calibration Applied', 'Sensor offsets have been updated.', 'success');
}

function resetCalibration() {
  ['cal-sm','cal-temp','cal-hum'].forEach(id => document.getElementById(id).value = 0);
  saveCalibration();
}

async function saveWeatherConfig() {
  const city = document.getElementById('cfg-city').value;
  
  const payload = {};
  if (city) payload.city = city;

  if (Object.keys(payload).length === 0) {
    showAlert('Error', 'Please enter at least one config value.', 'danger');
    return;
  }
  
  await fetch(BASE + '/api/config', { 
    method: 'POST', 
    headers: {'Content-Type':'application/json'}, 
    body: JSON.stringify(payload) 
  });
  
  showAlert('Settings Saved', 'Configuration updated successfully.', 'success');
  if (city) fetchWeather();
}

function toggleTheme() {
  const html = document.documentElement;
  html.setAttribute('data-theme', html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
}

// ─── Clock ─────────────────────────────────────────
function updateClock() {
  document.getElementById('time-display').textContent = new Date().toLocaleTimeString();
}

// ─── Init ──────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initCharts();
  fetchSensor();
  fetchWeather();
  fetchInsights();
  setInterval(fetchSensor, 2000);
  setInterval(fetchWeather, 300000);   // every 5 min
  setInterval(fetchInsights, 10000);
  setInterval(updateClock, 1000);
  updateClock();
});
