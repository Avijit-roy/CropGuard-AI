function wmo(c) {
  const m = {
    0:{d:'Clear sky',i:'☀️'}, 1:{d:'Mainly clear',i:'🌤️'}, 2:{d:'Partly cloudy',i:'⛅'}, 3:{d:'Overcast',i:'☁️'},
    45:{d:'Fog',i:'🌫️'}, 48:{d:'Depositing rime fog',i:'🌫️'}, 51:{d:'Drizzle: Light',i:'🌦️'}, 53:{d:'Drizzle: Moderate',i:'🌦️'},
    55:{d:'Drizzle: Dense',i:'🌦️'}, 61:{d:'Rain: Slight',i:'🌧️'}, 63:{d:'Rain: Moderate',i:'🌧️'}, 65:{d:'Rain: Heavy',i:'🌧️'},
    71:{d:'Snow fall: Slight',i:'🌨️'}, 73:{d:'Snow fall: Moderate',i:'🌨️'}, 75:{d:'Snow fall: Heavy',i:'🌨️'},
    80:{d:'Rain showers: Slight',i:'🌦️'}, 81:{d:'Rain showers: Moderate',i:'🌦️'}, 82:{d:'Rain showers: Violent',i:'⛈️'},
    95:{d:'Thunderstorm: Slight or moderate',i:'⛈️'}, 96:{d:'Thunderstorm with slight hail',i:'⛈️'}, 99:{d:'Thunderstorm with heavy hail',i:'⛈️'}
  };
  return m[c] || {d:'Unknown',i:'🌡️'};
}

// ─── State ─────────────────────────────────────────
const state = {
  sensor:     { soil_moisture:0, temperature:0, humidity:0, timestamp:null, connected:false },
  weather:    null,
  history:    [],
  insights:   [],
  thresholds: { soil_moisture:{min:30,max:80}, temperature:{min:10,max:35}, humidity:{min:40,max:80} },
  range:      1,
  currentView:'overview'
};

// Always use the same origin — Flask serves both the UI and API
const BASE = window.location.origin;

// Override fetch to automatically redirect to login page on 401 Unauthorized
const originalFetch = window.fetch;
window.fetch = async function(...args) {
  try {
    const response = await originalFetch(...args);
    if (response.status === 401) {
      window.location.href = '/login';
      return new Promise(() => {}); // keep pending to prevent error handling from triggering
    }
    return response;
  } catch (err) {
    throw err;
  }
};

let lastFetchTime = null;
let clientCoords  = null;

// ── Helpers ─────────────────────────────────────────
const $  = id => document.getElementById(id);
const set = (id, text) => { const e=$( id); if(e) e.textContent=text; };
const setHtml = (id, html) => { const e=$(id); if(e) e.innerHTML=html; };

// ── Last-update ticker ───────────────────────────────
setInterval(() => {
  if (!lastFetchTime) return;
  const s = Math.floor((Date.now() - lastFetchTime) / 1000);
  set('last-update', s > 60 ? `Last updated: ${Math.floor(s/60)}m ago` : `Last updated: ${s}s ago`);
  const dot = $('live-dot');
  if (dot) {
    if (s > 30) { dot.style.animation='none'; dot.style.background='var(--red)'; }
    else        { dot.style.animation='pulse 2s infinite'; dot.style.background='var(--green)'; }
  }
}, 1000);

// ─── Charts ─────────────────────────────────────────
let miniSm, miniTemp, miniHum, fullSm, fullTemp, fullHum;

const mkChartCfg = (color, label) => ({
  type:'line',
  data:{ labels:[], datasets:[{ label, data:[], borderColor:color,
    backgroundColor:color+'33', fill:true, tension:0.4, pointRadius:2, borderWidth:2 }] },
  options:{
    responsive:true, maintainAspectRatio:false, animation:{ duration:300 },
    scales:{
      x:{ display:false },
      y:{ grid:{ color:'rgba(255,255,255,0.05)' },
          ticks:{ color:'#718096', font:{ size:11 } } }
    },
    plugins:{ legend:{ display:false }, tooltip:{
      backgroundColor:'#1a202c', borderColor:'#2d3748', borderWidth:1,
      titleColor:'#e2e8f0', bodyColor:'#a0aec0', padding:10,
    }}
  }
});

function initCharts() {
  const bind = (id, cfg) => { const c=$(id); return c ? new Chart(c, cfg) : null; };
  miniSm   = bind('mini-sm',   mkChartCfg('#4ade80','Soil Moisture'));
  miniTemp = bind('mini-temp', mkChartCfg('#f87171','Temperature'));
  miniHum  = bind('mini-hum',  mkChartCfg('#60a5fa','Humidity'));
  fullSm   = bind('chart-sm',  mkChartCfg('#4ade80','Soil Moisture %'));
  fullTemp = bind('chart-temp',mkChartCfg('#f87171','Temperature °C'));
  fullHum  = bind('chart-hum', mkChartCfg('#60a5fa','Humidity %'));

  // Show x-axis on full charts
  [fullSm, fullTemp, fullHum].forEach(c => {
    if (!c) return;
    c.options.scales.x = { display:true, ticks:{ color:'#718096', maxRotation:45, font:{size:9}, maxTicksLimit:12 } };
    c.update();
  });
}

function pushChart(chart, label, value, maxPts=40, ts=null) {
  if (!chart) return;
  chart.data.labels.push(label);
  chart.data.datasets[0].data.push(value);
  if (!chart.data.fullTimestamps) chart.data.fullTimestamps=[];
  chart.data.fullTimestamps.push(ts);
  if (chart.data.labels.length > maxPts) {
    chart.data.labels.shift();
    chart.data.datasets[0].data.shift();
    chart.data.fullTimestamps.shift();
  }
  chart.update('none');
}

function loadHistoryToCharts(history) {
  [fullSm, fullTemp, fullHum].forEach(c => {
    if (!c) return;
    c.data.labels=[]; c.data.datasets[0].data=[]; c.data.fullTimestamps=[];
  });
  history.forEach(r => {
    let lbl = r.timestamp.substring(11,16);
    if (state.range > 24) {
      const dt = new Date(r.timestamp);
      lbl = dt.toLocaleDateString('en-GB',{day:'2-digit',month:'short'}) + ' ' + lbl;
    }
    if (fullSm)   { fullSm.data.labels.push(lbl); fullSm.data.datasets[0].data.push(r.soil_moisture); fullSm.data.fullTimestamps.push(r.timestamp); }
    if (fullTemp) { fullTemp.data.labels.push(lbl); fullTemp.data.datasets[0].data.push(r.temperature); fullTemp.data.fullTimestamps.push(r.timestamp); }
    if (fullHum)  { fullHum.data.labels.push(lbl); fullHum.data.datasets[0].data.push(r.humidity); fullHum.data.fullTimestamps.push(r.timestamp); }
  });
  if (fullSm) fullSm.update();
  if (fullTemp) fullTemp.update();
  if (fullHum) fullHum.update();
}

// ─── Sensor fetch ────────────────────────────────────
async function fetchSensor() {
  try {
    const r = await fetch(BASE + '/api/sensor');
    if (!r.ok) throw new Error('API error');
    const d = await r.json();
    state.sensor = d;

    const overlay = $('loading-overlay');
    if (overlay) { overlay.style.opacity='0'; setTimeout(()=>overlay.style.display='none',300); }
    const err = $('error-banner');
    if (err) err.style.display='none';

    updateSensorUI(d);
    lastFetchTime = Date.now();
    const lbl = new Date().toLocaleTimeString('en',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    const ts  = d.timestamp || new Date().toISOString();
    const sm   = d.connected ? d.soil_moisture : 0;
    const temp = d.connected ? d.temperature   : 0;
    const hum  = d.connected ? d.humidity      : 0;

    pushChart(miniSm,   lbl, sm,   40, ts);
    pushChart(miniTemp, lbl, temp, 40, ts);
    pushChart(miniHum,  lbl, hum,  40, ts);

    set('mini-sm-val',   sm.toFixed(1)   + '%');
    set('mini-temp-val', temp.toFixed(1) + '°C');
    set('mini-hum-val',  hum.toFixed(1)  + '%');
    set('last-update', 'Last update: ' + lbl);

    const dot   = $('serial-dot');
    const label = $('serial-label');
    if (label) label.textContent = d.connected ? 'Sensor Active' : 'Sensor Inactive';
    if (dot)   dot.classList.toggle('ok', d.connected);
  } catch(e) {
    console.error('sensor fetch:', e);
    const err = $('error-banner');
    if (err) err.style.display='flex';
    const overlay = $('loading-overlay');
    if (overlay) overlay.style.display='none';
  }
}

function updateSensorUI(d) {
  const th = state.thresholds;
  set('sm-opt-label',   `Opt: ${th.soil_moisture.min}-${th.soil_moisture.max}%`);
  set('temp-opt-label', `Opt: ${th.temperature.min}-${th.temperature.max}°`);
  set('hum-opt-label',  `Opt: ${th.humidity.min}-${th.humidity.max}%`);

  const sm   = d.connected ? d.soil_moisture : 0;
  const temp = d.connected ? d.temperature   : 0;
  const hum  = d.connected ? d.humidity      : 0;

  setMetric('sm',   sm,   '%',  'val-sm',   th.soil_moisture.min, th.soil_moisture.max, 100, 'bar-sm',   'badge-sm',   'card-sm');
  setMetric('temp', temp, '°C', 'val-temp', th.temperature.min,   th.temperature.max,    50, 'bar-temp', 'badge-temp', 'card-temp');
  setMetric('hum',  hum,  '%',  'val-hum',  th.humidity.min,      th.humidity.max,      100, 'bar-hum',  'badge-hum',  'card-hum');
}

function setMetric(_key, val, unit, valId, min, max, scale, barId, badgeId, cardId) {
  const el = $(valId);
  if (el) el.innerHTML = val.toFixed(1) + `<span class="metric-unit">${unit}</span>`;
  const pct  = Math.min(100, (val/scale)*100);
  const bar  = $(barId);
  const badge = $(badgeId);
  const card  = $(cardId);
  if (bar) bar.style.width = pct + '%';
  if (val < min) {
    if (badge) { badge.className='metric-badge badge-warn'; badge.textContent='● LOW'; }
    if (bar)   bar.style.backgroundColor='var(--amber)';
    if (card)  card.style.setProperty('--card-accent','var(--amber)');
  } else if (val > max) {
    if (badge) { badge.className='metric-badge badge-danger'; badge.textContent='● HIGH'; }
    if (bar)   bar.style.backgroundColor='var(--red)';
    if (card)  card.style.setProperty('--card-accent','var(--red)');
  } else {
    if (badge) { badge.className='metric-badge badge-ok'; badge.textContent='● OPTIMAL'; }
    if (bar)   bar.style.backgroundColor='var(--green)';
    if (card)  card.style.setProperty('--card-accent','var(--green)');
  }
}

// ─── Weather ─────────────────────────────────────────
async function fetchWeather() {
  try {
    let url = BASE + '/api/weather';
    if (clientCoords) url += `?lat=${clientCoords.lat}&lon=${clientCoords.lon}`;
    const d = await fetch(url).then(r=>r.json());
    state.weather = d;
    updateWeatherUI(d);
  } catch(e) { console.error('weather:', e); }
}

function updateWeatherUI(d) {
  const c = d.current; if (!c) return;
  const w = wmo(c.weather_code);
  set('w-city', d.city||'—'); set('w-temp', c.temp?.toFixed(1)||'—');
  set('w-icon', w.i); set('w-desc', w.d); set('w-feels', c.feels_like?.toFixed(1)||'—');
  set('w-wind', (c.wind_speed||0)+' km/h'); set('w-hum', (c.humidity||0)+'%');
  set('w-press', (c.pressure||0)+' hPa'); set('w-cloud', (c.cloud_cover||0)+'%');
  set('w-uv', (c.uv_index||0).toFixed(1));
  set('weather-fetched', 'Fetched at '+(d.fetched_at||'').substring(11,16)+(d.mock?' (cached)':''));
  const aqiEl = $('w-aqi-badge');
  if (aqiEl) {
    const aqi=c.aqi||0; aqiEl.textContent=aqi+' US AQI';
    aqiEl.className='aqi-badge '+(aqi<=50?'aqi-good':aqi<=100?'aqi-mod':'aqi-poor');
  }
  const ac = $('severe-alert-container');
  if (ac) { ac.innerHTML=''; (d.alerts||[]).forEach(a=>{const bg=a.type==='danger'?'var(--red)':'var(--amber)';ac.innerHTML+=`<div style="background:${bg};color:#fff;padding:12px 16px;border-radius:8px;margin-bottom:12px;font-weight:600;">⚠️ ${a.message}</div>`;});}
  const hs = $('hourly-scroll');
  if (hs) { hs.innerHTML=''; (d.hourly||[]).forEach(h=>{const t=h.time?.substring(11,16)||'—';const hw=wmo(h.weather_code);hs.innerHTML+=`<div class="w-hourly-item"><div class="w-hourly-time">${t}</div><div class="w-hourly-icon">${hw.i}</div><div class="w-hourly-temp">${h.temp?.toFixed(0)||'—'}°</div><div class="w-hourly-pop">💧${h.precip_prob||0}%</div></div>`;});}
  const dl = $('daily-list');
  if (dl) { dl.innerHTML=''; (d.daily||[]).forEach(day=>{const nm=new Date(day.date).toLocaleDateString('en-US',{weekday:'short'});const dw=wmo(day.weather_code);dl.innerHTML+=`<div class="w-daily-item"><div class="w-daily-day">${nm}</div><div class="w-daily-icon">${dw.i}</div><div class="w-daily-temps"><span class="w-daily-max">${day.temp_max?.toFixed(0)||'—'}°</span><span class="w-daily-min">${day.temp_min?.toFixed(0)||'—'}°</span></div></div>`;});}
  if (d.daily?.length) { set('w-sunrise',d.daily[0].sunrise?.substring(11,16)||'—'); set('w-sunset',d.daily[0].sunset?.substring(11,16)||'—'); }
}

// ─── Insights ────────────────────────────────────────
async function fetchInsights() {
  const loader = $('fusion-loader');
  const cont   = $('full-insights');
  if (loader) loader.style.display='block';
  if (cont)   cont.style.opacity='0.3';
  try {
    let url = BASE + '/api/insights';
    if (clientCoords) url += `?lat=${clientCoords.lat}&lon=${clientCoords.lon}`;
    const r = await fetch(url); if (!r.ok) throw new Error('insights');
    const d = await r.json();
    state.insights = d.insights;
    renderInsights(d.insights, 'quick-insights', 3);
    renderInsights(d.insights, 'full-insights', 999);
  } catch(e) { console.error('insights:', e); }
  finally {
    if (loader) loader.style.display='none';
    if (cont)   cont.style.opacity='1';
  }
}

function renderInsights(insights, id, limit) {
  const el = $(id); if (!el) return;
  if (!insights || !insights.length) { el.innerHTML='<div style="font-size:13px;color:var(--text2);padding:12px 0;">No insights available yet — waiting for sensor data.</div>'; return; }
  el.innerHTML = insights.slice(0, limit).map(ins =>
    `<div class="insight-card ${ins.level}"><div class="insight-icon">${ins.icon}</div><div><div class="insight-title">${ins.title}</div><div class="insight-msg">${ins.message}</div></div></div>`
  ).join('');
}

// ─── History ─────────────────────────────────────────
async function fetchHistory() {
  try {
    const d = await fetch(BASE+`/api/history?hours=${state.range}`).then(r=>r.json());
    state.history = d;
    loadHistoryToCharts(d);
  } catch(e) {}
}

// ─── Range pill ──────────────────────────────────────
function setRange(h, el) {
  state.range = h;
  document.querySelectorAll('.pill').forEach(p=>p.classList.remove('active'));
  if (el) el.classList.add('active');
  fetchHistory();
}

// ─── Reports ─────────────────────────────────────────
function downloadCSV() { window.open(BASE+`/api/export/csv?hours=${$('report-hours')?.value||24}`); }
function downloadPDF()  { window.open(BASE+`/api/export/pdf?hours=${$('report-hours')?.value||24}`); }
function quickExportCSV() {
  const s = state.sensor;
  const csv = `Timestamp,Soil Moisture %,Temperature C,Humidity %\n${new Date().toISOString()},${s.soil_moisture},${s.temperature},${s.humidity}`;
  const a = document.createElement('a'); a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'})); a.download='agri_quick_export.csv'; a.click();
}

// ─── Settings / Alerts (shared) ──────────────────────
async function loadSettings() {
  try {
    const [th, cal] = await Promise.all([
      fetch(BASE+'/api/thresholds').then(r=>r.json()),
      fetch(BASE+'/api/calibration').then(r=>r.json())
    ]);
    const sv = (id,v)=>{ const e=$(id); if(e) e.value=v; };
    sv('th-sm-min', th.soil_moisture?.min||30); sv('th-sm-max', th.soil_moisture?.max||80);
    sv('th-temp-min', th.temperature?.min||10); sv('th-temp-max', th.temperature?.max||35);
    sv('th-hum-min', th.humidity?.min||40);     sv('th-hum-max', th.humidity?.max||80);
    sv('cal-sm', cal.soil_moisture||0); sv('cal-temp', cal.temperature||0); sv('cal-hum', cal.humidity||0);
    sv('cfg-city', state.weather?.city||'');
  } catch(e) {}
}
async function saveThresholds() {
  const gv = id => +$(id)?.value;
  const data = { soil_moisture:{min:gv('th-sm-min'),max:gv('th-sm-max')}, temperature:{min:gv('th-temp-min'),max:gv('th-temp-max')}, humidity:{min:gv('th-hum-min'),max:gv('th-hum-max')} };
  await fetch(BASE+'/api/thresholds',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  showAlert('Thresholds Saved','Alert thresholds updated.','success');
}
async function saveCalibration() {
  const gv = id => +$(id)?.value;
  const data = { soil_moisture:gv('cal-sm'), temperature:gv('cal-temp'), humidity:gv('cal-hum') };
  await fetch(BASE+'/api/calibration',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  showAlert('Calibration Applied','Sensor offsets updated.','success');
}
function resetCalibration() { ['cal-sm','cal-temp','cal-hum'].forEach(id=>{const e=$(id);if(e)e.value=0;}); saveCalibration(); }
async function saveWeatherConfig() {
  const city = $('cfg-city')?.value?.trim();
  if (!city) { showAlert('Error','Enter a city name.','danger'); return; }
  await fetch(BASE+'/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({city})});
  showAlert('Saved','Configuration updated.','success');
  fetchWeather();
}
// Preferences page save wrappers
async function saveWeather() { await saveWeatherConfig(); }

// ─── Alerts ──────────────────────────────────────────
const shownAlerts = new Set();
function showAlert(title, msg, type='warning') {
  const key = title+type;
  if (shownAlerts.has(key)) return;
  shownAlerts.add(key); setTimeout(()=>shownAlerts.delete(key), 30000);
  const bar = $('alert-bar'); if (!bar) { if (type==='success'||type==='info') { if(typeof showToast==='function') showToast(title+': '+msg, type); } return; }
  const id = 'al-'+Date.now();
  const icons = {warning:'⚠️',danger:'🚨',success:'✅',info:'ℹ️'};
  bar.innerHTML += `<div class="alert-item ${type}" id="${id}"><span>${icons[type]||'⚠️'}</span><div><strong>${title}</strong><br><span style="font-size:12px;color:var(--text2)">${msg}</span></div><span class="alert-close" onclick="document.getElementById('${id}').remove()">×</span></div>`;
  setTimeout(()=>$(id)?.remove(), 8000);
}

// ─── Geolocation ──────────────────────────────────────
function getClientGeolocation() {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(pos=>{
    clientCoords={lat:pos.coords.latitude,lon:pos.coords.longitude};
    fetchWeather(); fetchInsights();
  }, ()=>{});
}

async function detectUserLocation(btn) {
  if (!navigator.geolocation) {
    showAlert('Not Supported', 'Geolocation is not supported by your browser.', 'danger');
    return;
  }

  const originalText = btn.innerHTML;
  btn.innerHTML = '⌛';
  btn.disabled = true;

  navigator.geolocation.getCurrentPosition(async (position) => {
    try {
      const lat = position.coords.latitude;
      const lon = position.coords.longitude;
      
      clientCoords = { lat, lon };

      const response = await fetch(
        `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json`
      );

      if (!response.ok) throw new Error('Geocoding failed');
      const data = await response.json();

      const addr = data.address || {};
      const city = addr.city || addr.town || addr.village || addr.suburb || addr.city_district || addr.county;

      if (city) {
        const input = $('cfg-city');
        if (input) {
          input.value = city;
          showAlert('Location Detected', `Detected: ${city}`, 'success');
        }
      } else {
        showAlert('Location Error', 'Could not determine city name.', 'warning');
      }
    } catch (err) {
      console.error('Reverse geocoding error:', err);
      showAlert('Location Error', 'Failed to fetch city name.', 'danger');
    } finally {
      btn.innerHTML = originalText;
      btn.disabled = false;
    }
  }, (err) => {
    console.error('Geolocation error:', err);
    let msg = 'Could not access your location.';
    if (err.code === 1) msg = 'Location permission denied.';
    showAlert('Location Error', msg, 'danger');
    btn.innerHTML = originalText;
    btn.disabled = false;
  });
}

// ─── USB Serial ───────────────────────────────────────
let serialPort=null, serialReader=null, serialBuf='';

function initUSBSerialButton() {
  const btn=$('btn-connect-usb'); if(!btn) return;
  if (!('serial' in navigator)) {
    btn.style.opacity='0.5'; btn.style.cursor='not-allowed';
    btn.title='Web Serial not supported — use Chrome/Edge';
    btn.addEventListener('click',()=>showAlert('Web Serial Unsupported','Use Google Chrome or Edge.','danger'));
    return;
  }
  btn.addEventListener('click',()=>serialPort ? disconnectUSBSerial() : connectUSBSerial());
}

async function connectUSBSerial() {
  const btn=$('btn-connect-usb');
  try {
    serialPort=await navigator.serial.requestPort({filters:[{usbVendorId:0x2341},{usbVendorId:0x1A86},{usbVendorId:0x10C4},{usbVendorId:0x0403}]});
    await serialPort.open({baudRate:9600});
    if(btn){btn.style.background='var(--green)';btn.style.color='#fff';btn.textContent='🔌 Connected';}
    showAlert('USB Connected','Serial link established!','success');
    const dec=new TextDecoderStream();
    serialPort.readable.pipeTo(dec.writable);
    serialReader=dec.readable.getReader();
    readSerial();
  } catch(e) { showAlert('USB Failed','Could not open port.','danger'); disconnectUSBSerial(); }
}

async function disconnectUSBSerial() {
  if(serialReader){try{await serialReader.cancel();}catch(e){}serialReader=null;}
  if(serialPort){try{await serialPort.close();}catch(e){}serialPort=null;}
  const btn=$('btn-connect-usb');
  if(btn){btn.style.background='';btn.style.color='var(--green)';btn.textContent='🔌 Connect USB Sensor';}
  showAlert('USB Disconnected','Sensor disconnected.','info');
}

async function readSerial() {
  try {
    while(serialReader&&serialPort){
      const{value,done}=await serialReader.read();
      if(done)break;
      if(value){
        serialBuf+=value;
        let idx;
        while((idx=serialBuf.indexOf('\n'))!==-1){
          const line=serialBuf.substring(0,idx).trim();
          serialBuf=serialBuf.substring(idx+1);
          if(line) handleLine(line);
        }
      }
    }
  } catch(e){disconnectUSBSerial();}
}

function handleLine(line) {
  try {
    const d=JSON.parse(line);
    if(d.temperature===undefined||d.humidity===undefined||d.soil_moisture===undefined)return;
    state.sensor={soil_moisture:+d.soil_moisture,temperature:+d.temperature,humidity:+d.humidity,timestamp:new Date().toISOString(),connected:true};
    updateSensorUI(state.sensor);
    const lbl=new Date().toLocaleTimeString('en',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    pushChart(miniSm,lbl,state.sensor.soil_moisture,40,state.sensor.timestamp);
    pushChart(miniTemp,lbl,state.sensor.temperature,40,state.sensor.timestamp);
    pushChart(miniHum,lbl,state.sensor.humidity,40,state.sensor.timestamp);
    set('mini-sm-val',state.sensor.soil_moisture.toFixed(1)+'%');
    set('mini-temp-val',state.sensor.temperature.toFixed(1)+'°C');
    set('mini-hum-val',state.sensor.humidity.toFixed(1)+'%');
    set('last-update',`USB: ${d.device_id||'device'} (${lbl})`);
    // sync to cloud
    fetch(BASE+'/api/sensor/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_id:d.device_id||'usb_device',soil_moisture:state.sensor.soil_moisture,temperature:state.sensor.temperature,humidity:state.sensor.humidity})}).catch(()=>{});
  } catch(e){}
}

// ─── Clock ───────────────────────────────────────────
function updateClock() { set('time-display', new Date().toLocaleTimeString()); }

// ─── SPA Tab Switching ──────────────────────────────
function switchAppTab(name, pushState = true) {
  // Update sidebar active link
  document.querySelectorAll('.app-sidebar .nav-link').forEach(link => {
    link.classList.remove('active');
  });
  
  let targetLink = document.getElementById('nav-' + name);
  if (name === 'preferences') {
    targetLink = document.getElementById('nav-prefs') || document.getElementById('nav-preferences');
  }
  if (targetLink) targetLink.classList.add('active');

  // Update visible panel
  document.querySelectorAll('.app-panel').forEach(panel => {
    panel.classList.remove('active-panel');
  });
  const targetPanel = document.getElementById('panel-' + name);
  if (targetPanel) targetPanel.classList.add('active-panel');

  // Update dynamic topbar header title
  const titleMap = {
    'soil': '🌱 Live Soil Monitor',
    'disease': '🔬 Plant Disease Detection',
    'history': '📄 Detection & Soil History',
    'preferences': '⚙️ System Preferences'
  };
  const titleEl = document.getElementById('app-title-dynamic') || document.querySelector('.topbar-title');
  if (titleEl) titleEl.innerHTML = titleMap[name] || 'CropGuard AI';

  // Push state to browser history if requested
  if (pushState) {
    window.history.pushState(null, "", "/" + (name === 'soil' ? '' : name));
  }

  // Trigger special page actions on switch
  if (name === 'history') {
    loadDiseaseHistory();
    loadSoilHistory();
  }
}

// Handle browser navigation (back/forward)
window.addEventListener('popstate', () => {
  const path = window.location.pathname.replace('/', '') || 'soil';
  switchAppTab(path, false);
});

// ─── Disease Detection Init ─────────────────────────
function initDiseaseDetection() {
  const dropZone = $('drop-zone');
  const fileInput = $('file-input');
  if (!dropZone || !fileInput) return;

  dropZone.addEventListener('click', () => fileInput.click());
  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file) loadImage(file);
  });
  fileInput.addEventListener('change', e => { if (e.target.files[0]) loadImage(e.target.files[0]); });
}

let cropper = null;
let lastResult = null;

function loadImage(file) {
  if (file.size > 10 * 1024 * 1024) { showToast('File too large (max 10MB)', 'error'); return; }
  const url = URL.createObjectURL(file);
  const img = $('crop-img');
  if (!img) return;
  img.src = url;
  $('crop-section').style.display = 'block';
  $('empty-state').style.display = 'none';
  $('result-panel').style.display = 'none';
  $('loading-state').style.display = 'none';

  if (cropper) { cropper.destroy(); cropper = null; }
  img.onload = () => {
    cropper = new Cropper(img, {
      viewMode: 2,
      dragMode: 'crop',
      autoCropArea: 0.8,
      responsive: true,
      background: false,
      guides: true,
    });
  };
}

async function runDiagnosis() {
  if (!cropper) { showToast('Please upload an image first', 'error'); return; }

  const btn = $('btn-diagnose');
  const btnText = $('btn-text');
  const spinner = $('btn-spinner');
  btn.disabled = true;
  btnText.textContent = 'Processing…';
  spinner.style.display = 'block';

  $('loading-state').style.display = 'block';
  $('empty-state').style.display = 'none';
  $('result-panel').style.display = 'none';

  try {
    $('loading-msg').textContent = 'Cropping image…';
    const canvas = cropper.getCroppedCanvas({ width: 640, height: 640 });
    const blob = await new Promise(res => canvas.toBlob(res, 'image/jpeg', 0.92));

    $('loading-msg').textContent = 'Running AI model…';
    const formData = new FormData();
    formData.append('image', blob, 'leaf.jpg');

    const resp = await fetch('/api/predict', { method: 'POST', body: formData });
    if (!resp.ok) throw new Error((await resp.json()).error || 'Prediction failed');
    const data = await resp.json();
    lastResult = data;

    displayResult(data);
  } catch(err) {
    $('loading-state').style.display = 'none';
    $('empty-state').style.display = 'block';
    showToast('Error: ' + err.message, 'error');
  } finally {
    btn.disabled = false;
    btnText.textContent = '🚀 Run AI Diagnosis';
    spinner.style.display = 'none';
  }
}

function displayResult(d) {
  $('loading-state').style.display = 'none';
  $('result-panel').style.display = 'block';

  const healthy = d.healthy;
  const icon = healthy ? '✅' : (d.alert_level === 'critical' ? '🚨' : '⚠️');
  set('res-icon', icon);
  set('res-crop', 'Crop: ' + d.crop);
  set('res-disease', healthy ? '✅ Healthy Plant' : '⚠️ ' + d.disease);
  const bar = $('res-conf-bar');
  if (bar) bar.style.width = d.confidence + '%';
  set('res-conf', d.confidence.toFixed(1) + '% confidence');

  const badge = $('res-alert-badge');
  if (badge) {
    badge.textContent = (d.alert_level || 'unknown').toUpperCase();
    badge.className = 'badge badge-' + (d.alert_level || 'low');
  }

  const lowConf = $('low-conf-warning');
  if (lowConf) lowConf.style.display = d.confidence < 60 ? 'flex' : 'none';

  // Top-3
  const top3Div = $('top3-bars');
  if (top3Div) {
    top3Div.innerHTML = d.top3.map(([lbl, pct]) => {
      const parts = lbl.split('___');
      const name = (parts[1] || lbl).replace(/_/g,' ');
      const crop = parts[0] || '';
      return `<div class="top3-bar">
        <div class="top3-label">${crop} — ${name.charAt(0).toUpperCase()+name.slice(1)}</div>
        <div class="top3-track"><div class="top3-fill" style="width:${pct}%"></div></div>
        <div class="top3-pct">${pct.toFixed(1)}%</div>
      </div>`;
    }).join('');
  }

  // Fusion
  if (d.fusion) {
    const f = d.fusion;
    $('fusion-panel').style.display = 'block';
    $('no-sensor-info').style.display = 'none';
    set('fusion-risk', f.risk_score + '/100');

    const insightEl = $('fusion-insight');
    if (insightEl) {
      const alertClass = {critical:'alert-critical', high:'alert-high', medium:'alert-medium', low:'alert-low', healthy:'alert-low'}[f.alert_level] || 'alert-info';
      insightEl.className = 'alert ' + alertClass;
      insightEl.innerHTML = '<strong>🔍 AI Insight:</strong> ' + f.combined_insight;
    }

    set('fusion-soil', f.soil_advice);

    const actionsEl = $('fusion-actions');
    if (actionsEl) {
      actionsEl.innerHTML = f.immediate_actions.map(a =>
        `<div class="action-item"><span>⚡</span><span>${a}</span></div>`).join('');
    }
    const treatmentEl = $('fusion-treatment');
    if (treatmentEl) {
      treatmentEl.innerHTML = f.treatment.map(t =>
        `<div class="action-item"><span>💊</span><span>${t}</span></div>`).join('');
    }
    const prevEl = $('fusion-prevention');
    if (prevEl) {
      prevEl.innerHTML = f.prevention.map(p =>
        `<div class="action-item"><span>🛡️</span><span>${p}</span></div>`).join('');
    }
    set('fusion-irrigation', f.irrigation_fix);
    set('fusion-fertiliser', f.fertiliser_fix);

    // LLM Insight
    if (f.llm_insight) {
      $('llm-insight-panel').style.display = 'block';
      set('llm-causes', f.llm_insight.causes || 'Data analysis in progress...');
      set('llm-suggestions', f.llm_insight.suggestions || 'Monitoring recommended.');
      set('llm-information', f.llm_insight.information || 'No additional context available.');
    } else {
      $('llm-insight-panel').style.display = 'none';
    }
  } else {
    $('llm-insight-panel').style.display = 'none';
    $('fusion-panel').style.display = 'none';
    $('no-sensor-info').style.display = 'flex';
  }

  buildReportUrl(d);
}

function buildReportUrl(d) {
  const lines = [
    'CropGuard AI — Plant Disease Report',
    '='.repeat(40),
    'Date       : ' + new Date().toLocaleString(),
    'Crop       : ' + d.crop,
    'Disease    : ' + (d.healthy ? 'Healthy' : d.disease),
    'Confidence : ' + d.confidence.toFixed(2) + '%',
  ];
  if (d.fusion) {
    const f = d.fusion;
    lines.push('', 'Fusion Alert  : ' + f.alert_level.toUpperCase());
    lines.push('Risk Score    : ' + f.risk_score + '/100');
    lines.push('AI Insight    : ' + f.combined_insight);
    lines.push('', 'Immediate Actions', '-'.repeat(40));
    f.immediate_actions.forEach(a => lines.push('  • ' + a));
    lines.push('', 'Treatment', '-'.repeat(40));
    f.treatment.forEach(t => lines.push('  • ' + t));
    lines.push('', 'Prevention', '-'.repeat(40));
    f.prevention.forEach(p => lines.push('  • ' + p));
    lines.push('', 'Irrigation : ' + f.irrigation_fix);
    lines.push('Fertiliser  : ' + f.fertiliser_fix);
  }
  const blob = new Blob([lines.join('\n')], {type:'text/plain'});
  const url = URL.createObjectURL(blob);
  const a = $('btn-download-report');
  if (a) {
    a.href = url;
    a.download = 'diagnosis_' + new Date().toISOString().slice(0,16).replace('T','_') + '.txt';
  }
}

// ─── History Page Logic ──────────────────────────────
let diseaseHistoryData = [];
let soilHistoryData = [];

function switchHistoryTab(name, btn) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  const target = $('tab-' + name);
  if (target) target.classList.add('active');
}

async function loadDiseaseHistory() {
  const loading = $('disease-loading');
  const table = $('disease-table');
  const empty = $('disease-empty');
  if (!loading) return;

  try {
    const resp = await fetch('/api/disease-history');
    const data = await resp.json();
    diseaseHistoryData = data;
    loading.style.display = 'none';

    if (!data || data.length === 0) {
      empty.style.display = 'flex';
      table.style.display = 'none';
      return;
    }
    table.style.display = 'table';
    empty.style.display = 'none';
    const tbody = $('disease-tbody');
    if (tbody) {
      tbody.innerHTML = data.map((r, i) => {
        const isHealthy = (r.disease || '').toLowerCase() === 'healthy';
        const sev = r.severity || '—';
        return `<tr>
          <td style="color:var(--text3);">${i+1}</td>
          <td>${fmtHistoryDate(r.timestamp)}</td>
          <td>${r.crop || '—'}</td>
          <td><span style="color:${isHealthy ? 'var(--green)' : '#f6ad55'};">${r.disease || '—'}</span></td>
          <td>
            <div style="display:flex;align-items:center;gap:8px;">
              <div class="progress-bar" style="width:80px;"><div class="progress-fill" style="width:${r.confidence||0}%"></div></div>
              <span style="color:var(--text2);">${(r.confidence||0).toFixed(1)}%</span>
            </div>
          </td>
          <td><span class="badge badge-${severityToBadge(sev)}">${sev}</span></td>
        </tr>`;
      }).join('');
    }
  } catch(e) {
    loading.innerHTML = '<span style="color:var(--red);">Failed to load history</span>';
  }
}

function severityToBadge(s) {
  s = (s||'').toLowerCase();
  if (s === 'high' || s === 'critical') return 'critical';
  if (s === 'medium') return 'medium';
  if (s === 'low' || s === 'none') return 'healthy';
  return 'low';
}

async function loadSoilHistory() {
  const hoursSelect = $('soil-hours');
  const loading = $('soil-loading');
  const table = $('soil-table');
  const empty = $('soil-empty');
  const count = $('soil-count');
  if (!loading) return;

  const hours = hoursSelect ? hoursSelect.value : 24;
  loading.style.display = 'block';
  table.style.display = 'none';
  empty.style.display = 'none';

  try {
    const resp = await fetch('/api/history?hours=' + hours);
    const data = await resp.json();
    soilHistoryData = data;
    loading.style.display = 'none';

    if (!data || data.length === 0) {
      empty.style.display = 'flex';
      if (count) count.textContent = '';
      return;
    }

    if (count) count.textContent = data.length + ' readings';
    table.style.display = 'table';

    const rows = data.slice(-500).reverse(); // most recent first, cap 500
    const tbody = $('soil-tbody');
    if (tbody) {
      tbody.innerHTML = rows.map(r => {
        const sm = r.soil_moisture || 0;
        const smOk = sm >= 30 && sm <= 80;
        const temp = r.temperature || 0;
        const tempOk = temp >= 10 && temp <= 35;
        const hum = r.humidity || 0;
        const humOk = hum >= 40 && hum <= 80;
        const allOk = smOk && tempOk && humOk;
        return `<tr>
          <td>${fmtHistoryDate(r.timestamp)}</td>
          <td style="color:${smOk?'var(--green)':'#f6ad55'};">${sm.toFixed(1)}</td>
          <td style="color:${tempOk?'var(--text1)':'#f6ad55'};">${temp.toFixed(1)}</td>
          <td style="color:${humOk?'var(--text1)':'#f6ad55'};">${hum.toFixed(1)}</td>
          <td><span class="badge badge-${allOk?'healthy':'medium'}">${allOk?'OPTIMAL':'CHECK'}</span></td>
        </tr>`;
      }).join('');
    }
  } catch(e) {
    loading.innerHTML = '<span style="color:var(--red);">Failed to load soil data</span>';
  }
}

function exportHistoryCSV(type) {
  if (type === 'soil') {
    const hours = $('soil-hours')?.value || 24;
    window.open('/api/export/csv?hours=' + hours, '_blank');
  } else {
    // Client-side disease CSV
    const headers = ['id','timestamp','crop','disease','confidence','severity'];
    const rows = diseaseHistoryData.map(r => headers.map(h => JSON.stringify(r[h]||'')).join(','));
    const csv = [headers.join(','), ...rows].join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], {type:'text/csv'}));
    a.download = 'disease_history.csv';
    a.click();
  }
}

function fmtHistoryDate(ts) {
  if (!ts) return '—';
  try { return new Date(ts).toLocaleString('en-IN'); } catch { return ts; }
}

// ─── Init ────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initCharts();
  getClientGeolocation();
  fetchSensor();
  fetchWeather();
  fetchInsights();
  setInterval(fetchSensor,  2000);
  setInterval(fetchWeather, 300000);
  setInterval(fetchInsights, 30000);
  setInterval(updateClock,  1000);
  updateClock();
  initUSBSerialButton();
  loadSettings();
  initDiseaseDetection();

  // Route to the initial SPA panel based on the browser path
  const initialPath = window.location.pathname.replace('/', '') || 'soil';
  switchAppTab(initialPath, false);
});
