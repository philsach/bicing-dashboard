/* ============================================================================
 * Bicing Availability — DATA LAYER  (REAL precalculated data)
 * ----------------------------------------------------------------------------
 * Single source of truth for data access. The app only ever calls the PUBLIC
 * API at the bottom; swapping/extending data means touching only this file.
 *
 * ── TWO STATIC DATASETS, NO BACKEND ─────────────────────────────────────────
 *
 *  FILE 1 — station metadata   data/stations.json   (546 stations)
 *    [{ "station_id": 1, "name": "...", "lat": 41.39, "lon": 2.18,
 *       "capacity": 46, "altitude": 16 }, ...]
 *
 *  FILE 2..8 — aggregated availability, ONE FILE PER WEEKDAY so the browser
 *  only loads the day in view:
 *      data/availability_mon.json … availability_sun.json
 *
 *    Compact format (what's in the repo; made by scripts/compact_data.py):
 *      { "format": 2, "fields": ["any","ebike","mechanical","dock"],
 *        "thresholds": 5,
 *        "rows": [[station_id, interval, n,
 *                  t1.any, t1.ebike, t1.mechanical, t1.dock, … t5.dock], …] }
 *      probabilities as whole percentages 0–100, interval = 15-min bin 0..95.
 *
 *    The original pandas export (flat array of row objects) still loads too:
 *      { "station_id": 1, "time_window": "08:15", "n": 39,
 *        "t1": { "any": .., "ebike": .., "mechanical": .., "dock": .. }, … }
 *
 *    • n  = sample size (number of observed days behind the estimate)
 *    • tK = threshold K (1..5):  P(at least K …)
 *        - any        P(≥K bikes of any kind)        ← pickup, "All"
 *        - ebike      P(≥K e-bikes)                  ← pickup, "E-bike"
 *        - mechanical P(≥K classic/mechanical bikes) ← pickup, "Classic"
 *        - dock       P(≥K free dock slots)          ← drop-off
 *
 *  Rows for missing (station, window) combinations may be absent — getProb()
 *  returns null for those and the UI greys the station out. A weekday whose
 *  file is missing stays empty (no fallback to another day, so missing data
 *  can never masquerade as real data).
 * ========================================================================== */
(function () {
  'use strict';

  // ---- config ---------------------------------------------------------------
  const INTERVAL_MIN = 15;                       // bin width
  const INTERVALS = (24 * 60) / INTERVAL_MIN;    // 96 bins
  const DOW = 7;                                 // Mon..Sun
  const N_MIN = 1, N_MAX = 5;                    // threshold slider range (t1..t5)
  const LOW_N = 10;                              // fewer observed days than this = "limited data"
  const FIELDS = ['any', 'ebike', 'mechanical', 'dock'];

  const DAY_FILES = [
    'data/availability_mon.json', 'data/availability_tue.json',
    'data/availability_wed.json', 'data/availability_thu.json',
    'data/availability_fri.json', 'data/availability_sat.json',
    'data/availability_sun.json',
  ];

  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  const lerp = (a, b, t) => a + (b - a) * t;

  // ---- color ramps ----------------------------------------------------------
  const RAMP = {
    pickup:  [[0.0, [214, 58, 70]], [0.5, [226, 170, 45]], [1.0, [40, 180, 99]]],
    dropoff: [[0.0, [214, 58, 70]], [0.5, [126, 110, 158]], [1.0, [52, 130, 220]]],
  };
  function rampColor(p, mode) {
    const stops = RAMP[mode === 'dropoff' ? 'dropoff' : 'pickup'];
    p = clamp01(p);
    for (let i = 0; i < stops.length - 1; i++) {
      const [p0, c0] = stops[i], [p1, c1] = stops[i + 1];
      if (p <= p1) {
        const t = (p - p0) / (p1 - p0);
        return `rgb(${Math.round(lerp(c0[0], c1[0], t))},${Math.round(lerp(c0[1], c1[1], t))},${Math.round(lerp(c0[2], c1[2], t))})`;
      }
    }
    const c = stops[stops.length - 1][1];
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  }

  // ---- station metadata -----------------------------------------------------
  let CENTER = { lat: 41.398, lng: 2.175 };  // refined to the data mean after load
  let STATIONS = [];

  // Official Bicing names are ALL CAPS; render them in tasteful title case.
  const NAME_LOWERS = new Set(['de', 'del', 'dels', 'la', 'les', 'el', 'els', 'i', 'd', 'a', 'en', 'na']);
  function cleanName(s) {
    return String(s).trim().replace(/\s+/g, ' ').toLowerCase().split(' ').map((w, i) => {
      if (i > 0 && NAME_LOWERS.has(w.replace(/[.'\u2019]/g, ''))) return w;
      return w.replace(/[a-z\u00e0-\u00ff]/i, (c) => c.toUpperCase());
    }).join(' ');
  }

  async function loadStations(url) {
    const res = await fetch(url || 'data/stations.json');
    if (!res.ok) throw new Error('stations.json HTTP ' + res.status);
    const raw = await res.json();

    let sLat = 0, sLng = 0, k = 0;
    raw.forEach((s) => { if (isFinite(s.lat) && isFinite(s.lon)) { sLat += s.lat; sLng += s.lon; k++; } });
    if (k) CENTER = { lat: sLat / k, lng: sLng / k };

    STATIONS = raw
      .filter((s) => isFinite(s.lat) && isFinite(s.lon))
      .map((s) => ({
        id: String(s.station_id),
        name: cleanName(s.name || s.address || ('Station ' + s.station_id)),
        lat: s.lat, lng: s.lon,
        docks: s.capacity || 0,
        altitude: s.altitude,
      }));

    window.BICING.STATIONS = STATIONS;
    window.BICING.CENTER = CENTER;
    return STATIONS;
  }

  /* ==========================================================================
   *  AVAILABILITY STORE  — one indexed map per loaded weekday.
   *  STORE[dow] = { [stationId]: { [intervalIdx]: row } }
   * ======================================================================== */
  const STORE = {};         // dow -> index
  const DAY_STATE = {};     // dow -> 'loading' | 'ready' | 'missing'
  const dayPromises = {};   // dow -> Promise (dedupe in-flight loads)

  function twToInterval(tw) {
    const m = /^(\d{1,2}):(\d{2})$/.exec(tw);
    if (!m) return -1;
    return (parseInt(m[1], 10) * 60 + parseInt(m[2], 10)) / INTERVAL_MIN;
  }

  // Every row is stored as a compact array:
  //   [station_id, interval, n, t1.any, t1.ebike, t1.mechanical, t1.dock, … t5.dock]
  // with probabilities as percentages (0–100).
  function fromObjectRow(r) {
    const out = [r.station_id, twToInterval(r.time_window), r.n];
    for (let k = 1; k <= N_MAX; k++) {
      const t = r['t' + k] || {};
      for (const f of FIELDS) out.push(typeof t[f] === 'number' ? Math.round(t[f] * 100) : null);
    }
    return out;
  }

  function indexRows(data) {
    const compact = !Array.isArray(data) && data && data.format === 2;
    if (compact && (data.thresholds !== N_MAX || String(data.fields) !== String(FIELDS))) {
      throw new Error('unexpected availability file layout');
    }
    const rows = compact ? data.rows : (data || []).map(fromObjectRow);
    const idx = {};
    for (const r of rows) {
      const iv = r[1];
      if (!(iv >= 0 && iv < INTERVALS)) continue;
      const sid = String(r[0]);
      (idx[sid] || (idx[sid] = {}))[iv] = r;
    }
    return idx;
  }

  function loadAvailability(dow) {
    if (STORE[dow]) return Promise.resolve(STORE[dow]);
    if (dayPromises[dow]) return dayPromises[dow];
    DAY_STATE[dow] = 'loading';
    dayPromises[dow] = (async () => {
      try {
        const res = await fetch(DAY_FILES[dow]);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        STORE[dow] = indexRows(await res.json());
        DAY_STATE[dow] = 'ready';
      } catch (e) {
        // No file for this weekday (yet) → leave it empty. getProb() then returns
        // null for every cell and the UI greys the stations out. We deliberately
        // do NOT fall back to another day, so a missing file can't masquerade as
        // real data.
        STORE[dow] = {};
        DAY_STATE[dow] = 'missing';
      }
      return STORE[dow];
    })();
    return dayPromises[dow];
  }

  const isDayReady = (dow) => !!STORE[dow];
  const dayState = (dow) => DAY_STATE[dow] || (STORE[dow] ? 'ready' : null);

  /* ==========================================================================
   *  PUBLIC API  — the app only ever calls these.
   * ======================================================================== */

  // mode: 'pickup' => P(≥n bikes of `bike` type) ; 'dropoff' => P(≥n free docks)
  // interval: bin 0..95.  dow: 0=Mon..6=Sun.  n: 1..5.
  // Returns a probability in [0,1], or null when there's no data for that cell.
  function getRow(st, dow, interval) {
    const idx = STORE[dow];
    const byIv = idx && idx[st.id];
    return (byIv && byIv[interval]) || null;
  }

  function getProb(st, mode, dow, interval, bike, n) {
    const row = getRow(st, dow, interval);
    if (!row || !(n >= N_MIN && n <= N_MAX)) return null;
    const field = mode === 'dropoff' ? 3 : bike === 'ebike' ? 1 : bike === 'classic' ? 2 : 0;
    const v = row[3 + (n - 1) * FIELDS.length + field];
    return typeof v === 'number' ? v / 100 : null;
  }

  // Number of observed days behind a (station, weekday, 15-min window) cell,
  // or null when there's no data for it.
  function getSample(st, dow, interval) {
    const row = getRow(st, dow, interval);
    return row && typeof row[2] === 'number' ? row[2] : null;
  }

  // 24 hourly values for the detail chart = mean of the four 15-min bins in each
  // hour (the hourly "random-moment" probability). null when the hour has no data.
  function hourlySeries(st, mode, dow, bike, n) {
    const out = [];
    for (let h = 0; h < 24; h++) {
      let s = 0, c = 0;
      for (let k = 0; k < 4; k++) {
        const p = getProb(st, mode, dow, h * 4 + k, bike, n);
        if (p != null) { s += p; c++; }
      }
      out.push(c ? s / c : null);
    }
    return out;
  }

  window.BICING = {
    STATIONS, CENTER,
    INTERVAL_MIN, INTERVALS, DOW, N_MIN, N_MAX, LOW_N,
    rampColor, loadStations,
    loadAvailability, isDayReady, dayState,
    getProb, getSample, hourlySeries,
  };
})();
