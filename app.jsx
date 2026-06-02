/* Bicing Availability — app */
const { useState, useEffect, useRef, useMemo, useCallback, useReducer } = React;
const B = window.BICING;

/* ----------------------------------------------------------------------------
 * STADIA MAPS API KEY
 * Paste your free key between the quotes below to load the real Stadia dark
 * basemap. Get it at https://client.stadiamaps.com → Properties →
 * Authentication Configuration → "Add API Key". Leave blank to use the
 * free CARTO dark fallback (no account needed).
 * -------------------------------------------------------------------------- */
const STADIA_API_KEY = '';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const DAYS_LONG = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const MAX_IV = B.INTERVALS - 1; // 95 (15-min bins)
const fmtTime = (iv) => {
  const m = iv * B.INTERVAL_MIN;
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
};
const pct = (p) => Math.round(p * 100);
const noun = (mode) => (mode === 'dropoff' ? 'free docks' : 'bikes');

// gradient css for legend / mode swatches
function rampCss(mode) {
  if (mode === 'dropoff') return 'linear-gradient(90deg, rgb(214,58,70), rgb(126,110,158), rgb(52,130,220))';
  return 'linear-gradient(90deg, rgb(214,58,70), rgb(226,170,45), rgb(40,180,99))';
}

/* ---------------- hourly chart (24 bars = mean of four 15-min bins) -------- */
function HourChart({ station, mode, day, bike, n, interval }) {
  const data = useMemo(() => B.hourlySeries(station, mode, day, bike, n), [station, mode, day, bike, n]);
  const W = 320, H = 92, pad = 4;
  const bw = (W - pad * 2) / 24;
  const curHour = Math.floor(interval / 4);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ display: 'block' }}>
      <line x1={pad} x2={W - pad} y1={H - 16} y2={H - 16} stroke="#272c36" strokeWidth="1" />
      {data.map((p, h) => {
        if (p == null) return null;
        const bh = Math.max(2, p * (H - 24));
        const x = pad + h * bw;
        const isCur = h === curHour;
        return (
          <rect key={h} x={x + 1.2} y={(H - 16) - bh} width={bw - 2.4} height={bh}
            rx="2" fill={B.rampColor(p, mode)} opacity={isCur ? 1 : 0.62}
            stroke={isCur ? '#fff' : 'none'} strokeWidth={isCur ? 1.4 : 0} />
        );
      })}
      {[0, 6, 12, 18].map((h) => (
        <text key={h} x={pad + h * bw + bw / 2} y={H - 4} fill="#6c7585" fontSize="9.5" textAnchor="middle" fontFamily="inherit">
          {String(h).padStart(2, '0')}
        </text>
      ))}
      <text x={W - pad} y={H - 4} fill="#6c7585" fontSize="9.5" textAnchor="end" fontFamily="inherit">24</text>
    </svg>
  );
}

/* ---------------- station detail ---------------- */
function Detail({ station, mode, day, bike, n, interval, onClose }) {
  if (!station) {
    return (
      <div className="detail empty">
        <div className="dkicker" style={{ marginBottom: 8 }}>Station detail</div>
        Tap any station dot on the map to see its name, the current probability, and how availability shifts across the day.
      </div>
    );
  }
  const p = B.getProb(station, mode, day, interval, bike, n);
  const lbl = `chance of \u2265${n} ${noun(mode)}`;
  const bikeSuffix = mode === 'pickup' && bike !== 'all' ? ` (${bike === 'ebike' ? 'e-bikes' : 'classic'})` : '';
  return (
    <div className="detail">
      <button className="dclose" onClick={onClose} aria-label="Close">{'\u00d7'}</button>
      <div className="dkicker">Station detail</div>
      <div className="dname">{station.name}</div>
      <div className="dmeta tnum">{station.docks} docks &middot; {DAYS_LONG[day]} {fmtTime(interval)}</div>
      <div className="big">
        {p == null ? (
          <div className="bignum tnum" style={{ color: '#6c7585' }}>{'\u2014'}</div>
        ) : (
          <div className="bignum tnum" style={{ color: B.rampColor(p, mode) }}>{pct(p)}%</div>
        )}
        <div className="biglbl">{p == null ? 'no data for this time' : <>{lbl}{bikeSuffix}</>}</div>
      </div>
      <div className="chart-wrap">
        <div className="chart-cap">
          <span>Across {DAYS[day]} &middot; by hour</span>
          <span>{noun(mode)} likelihood</span>
        </div>
        <HourChart station={station} mode={mode} day={day} bike={bike} n={n} interval={interval} />
      </div>
    </div>
  );
}

/* ---------------- legend ---------------- */
function Legend({ mode, n }) {
  return (
    <div className="legend">
      <div className="lt">Chance of <b>{'\u2265'}{n} {noun(mode)}</b></div>
      <div className="bar" style={{ background: rampCss(mode) }} />
      <div className="lr"><span>0% &mdash; none likely</span><span>100%</span></div>
    </div>
  );
}

/* ---------------- map ---------------- */
function useBicingMap(onPick) {
  const mapRef = useRef(null);
  const markersRef = useRef({});
  const pickRef = useRef(onPick);
  pickRef.current = onPick;

  useEffect(() => {
    const map = L.map('map', {
      center: [B.CENTER.lat, B.CENTER.lng], zoom: 14, zoomControl: true,
      attributionControl: true, preferCanvas: true, zoomSnap: 0.5,
    });
    map.zoomControl.setPosition('topright');
    L.control.attribution({ prefix: false }).addTo(map);

    let fellBack = false;
    const keySuffix = STADIA_API_KEY ? ('?api_key=' + STADIA_API_KEY) : '';
    const stadia = L.tileLayer(
      'https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png' + keySuffix,
      { maxZoom: 20, attribution: '&copy; Stadia Maps &copy; OpenMapTiles &copy; OpenStreetMap contributors' }
    );
    function useCartoFallback() {
      if (fellBack) return; fellBack = true;
      map.removeLayer(stadia);
      L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        { maxZoom: 20, subdomains: 'abcd', attribution: '&copy; CARTO &copy; OpenStreetMap contributors' }).addTo(map);
    }
    // If no key is configured, go straight to CARTO so the console stays clean.
    if (!STADIA_API_KEY) {
      useCartoFallback();
    } else {
      stadia.on('tileerror', useCartoFallback);
      stadia.addTo(map);
    }

    B.STATIONS.forEach((st) => {
      const m = L.circleMarker([st.lat, st.lng], {
        radius: 6, weight: 1.2, color: 'rgba(255,255,255,.32)',
        fillColor: '#777', fillOpacity: 0.9,
      });
      m.on('click', () => pickRef.current(st.id));
      m.addTo(map);
      markersRef.current[st.id] = m;
    });

    mapRef.current = map;
    setTimeout(() => map.invalidateSize(), 200);
    return () => map.remove();
  }, []);

  return { mapRef, markersRef };
}

/* ---------------- app ---------------- */
function App() {
  const today = ((new Date().getDay()) + 6) % 7; // JS Sun=0..Sat=6 → Mon=0..Sun=6
  const nowIv = (() => { const d = new Date(); return Math.min(MAX_IV, Math.round((d.getHours() * 60 + d.getMinutes()) / B.INTERVAL_MIN)); })();

  const [mode, setMode] = useState('pickup');
  const [day, setDay] = useState(today);
  const [interval, setInterval] = useState(nowIv);
  const [bike, setBike] = useState('all');
  const [n, setN] = useState(2);
  const [selected, setSelected] = useState(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [loadTick, bumpLoad] = useReducer((x) => x + 1, 0);
  const [dayLoading, setDayLoading] = useState(!B.isDayReady(today));

  // Lazy-load the selected weekday's availability; re-render once it arrives.
  useEffect(() => {
    let alive = true;
    if (B.isDayReady(day)) { setDayLoading(false); return; }
    setDayLoading(true);
    B.loadAvailability(day)
      .then(() => { if (alive) { setDayLoading(false); bumpLoad(); } })
      .catch(() => { if (alive) setDayLoading(false); });
    return () => { alive = false; };
  }, [day]);

  const onPick = useCallback((id) => { setSelected(id); setSheetOpen(true); }, []);
  const { mapRef, markersRef } = useBicingMap(onPick);

  // recolor markers whenever query changes (or a day's data finishes loading)
  useEffect(() => {
    const markers = markersRef.current;
    B.STATIONS.forEach((st) => {
      const m = markers[st.id]; if (!m) return;
      const p = B.getProb(st, mode, day, interval, bike, n);
      m.setStyle({
        fillColor: p == null ? '#39414f' : B.rampColor(p, mode),
        fillOpacity: p == null ? 0.45 : 0.9,
      });
    });
  }, [mode, day, interval, bike, n, loadTick]);

  // highlight selected
  useEffect(() => {
    const markers = markersRef.current;
    B.STATIONS.forEach((st) => {
      const m = markers[st.id]; if (!m) return;
      const on = st.id === selected;
      m.setStyle({ radius: on ? 10 : 6, weight: on ? 2.4 : 1.2, color: on ? '#fff' : 'rgba(255,255,255,.32)' });
      if (on) m.bringToFront();
    });
    const st = B.STATIONS.find((s) => s.id === selected);
    if (st && mapRef.current) mapRef.current.panTo([st.lat, st.lng], { animate: true });
  }, [selected]);

  const station = selected ? B.STATIONS.find((s) => s.id === selected) : null;
  const bikeDisabled = mode === 'dropoff';

  const dstate = B.dayState(day);
  const dayNote = dayLoading
    ? `Loading ${DAYS_LONG[day]}\u2026`
    : dstate === 'missing'
      ? `No availability file for ${DAYS_LONG[day]} \u2014 add data/availability_${DAYS[day].toLowerCase()}.json`
      : null;

  return (
    <div className="app">
      {/* mode toggle */}
      <div className="mode-toggle" role="tablist" aria-label="Mode">
        <button className={'pickup ' + (mode === 'pickup' ? 'on' : '')} onClick={() => setMode('pickup')} role="tab" aria-selected={mode === 'pickup'}>
          <span className="dotmark" style={{ background: '#28b463' }} /> Pick up
        </button>
        <button className={'dropoff ' + (mode === 'dropoff' ? 'on' : '')} onClick={() => setMode('dropoff')} role="tab" aria-selected={mode === 'dropoff'}>
          <span className="dotmark" style={{ background: '#3482dc' }} /> Drop off
        </button>
      </div>

      {/* panel: sidebar on desktop, bottom sheet on mobile */}
      <div className={'panel' + (sheetOpen ? ' open' : '')}>
        <div className="panel-head">
          <div className="sheet-grip" />
          <button className="sheet-handle" onClick={() => setSheetOpen((v) => !v)}>
            <div>
              <h1>Bicing Availability</h1>
              <p>Barcelona &middot; historical patterns</p>
            </div>
            <span className="sh-sub">{sheetOpen ? 'Hide \u25be' : `${DAYS[day]} ${fmtTime(interval)} \u25b4`}</span>
          </button>
        </div>
        <div className="panel-body">
          <div className="field">
            <label>Day of week</label>
            <div className="days">
              {DAYS.map((d, i) => (
                <button key={d} className={day === i ? 'on' : ''} onClick={() => setDay(i)}>{d}</button>
              ))}
            </div>
            {dayNote && <p style={{ margin: '9px 0 0', fontSize: 11.5, color: 'var(--ink-3)', lineHeight: 1.4 }}>{dayNote}</p>}
          </div>

          <div className="field">
            <label>Time of day</label>
            <div className="time-row">
              <span className="time-val tnum">{fmtTime(interval)}</span>
              <span className="time-cap">15-min steps</span>
            </div>
            <input type="range" min="0" max={MAX_IV} step="1" value={interval} onChange={(e) => setInterval(+e.target.value)} />
            <div className="ticks tnum"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>23:45</span></div>
          </div>

          <div className="field">
            <label>Minimum available</label>
            <div className="time-row">
              <span className="time-val tnum">{'\u2265'}{n}</span>
              <span className="time-cap">{noun(mode)} needed</span>
            </div>
            <input type="range" min={B.N_MIN} max={B.N_MAX} step="1" value={n} onChange={(e) => setN(+e.target.value)} />
            <div className="ticks tnum"><span>1</span><span>2</span><span>3</span><span>4</span><span>5</span></div>
          </div>

          <div className="field">
            <label>Bike type</label>
            {bikeDisabled ? (
              <div className="seg-note"><span>Any bike fits any free dock</span></div>
            ) : (
              <div className="seg">
                {[['all', 'All'], ['ebike', 'E-bike'], ['classic', 'Classic']].map(([v, t]) => (
                  <button key={v} className={bike === v ? 'on' : ''} onClick={() => setBike(v)}>{t}</button>
                ))}
              </div>
            )}
          </div>

          <Detail station={station} mode={mode} day={day} bike={bike} n={n} interval={interval} onClose={() => setSelected(null)} />
        </div>
      </div>

      <div id="map" />
      <Legend mode={mode} n={n} />
    </div>
  );
}

// Bootstrap: load REAL station metadata + today's availability, then render.
const root = ReactDOM.createRoot(document.getElementById('root'));
const BOOT_DOW = ((new Date().getDay()) + 6) % 7; // Mon=0..Sun=6
Promise.all([
  B.loadStations(),
  B.loadAvailability(BOOT_DOW).catch((err) => console.error('Failed to load availability:', err)),
])
  .catch((err) => console.error('Failed to load stations:', err))
  .finally(() => root.render(<App />));
