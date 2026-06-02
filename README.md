# Bicing Availability

Interactive map of historical Bicing (Barcelona bike-share) availability — the
probability of finding **≥N bikes** (pick up) or **≥N free docks** (drop off) at
each station, by **weekday** and **time of day** (15-minute resolution).

Pure static site: HTML + one React/JSX file + a small vanilla-JS data layer. No
build step, no backend.

## Run it

GitHub Pages (recommended): push this folder to a repo, then enable
**Settings → Pages → Deploy from branch** (root). `index.html` is served
automatically.

Local: any static server, e.g.
```bash
python3 -m http.server
# open http://localhost:8000
```
(Opening `index.html` directly via `file://` will fail — the data files are
loaded with `fetch`, which browsers block on the file protocol.)

## Files

```
index.html      page shell + styling
app.jsx         React UI (loaded in-browser via Babel standalone)
bicing.js       DATA LAYER (code) — the only place that knows the data format
data/
  stations.json              546 stations: id, name, lat/lon, capacity
  availability_mon.json       Monday availability (provided)
  availability_tue.json       ← add these to light up the other weekdays
  availability_wed.json
  availability_thu.json
  availability_fri.json
  availability_sat.json
  availability_sun.json
```

## Adding the other weekdays

Drop each day's file into `data/` with the **exact** names above. No code
changes needed — the app loads the selected day's file on demand. If a file is
missing, that day's stations render grey and a note tells you which file to add
(no fallback to another day, so a missing file can never masquerade as real
data).

The weekday → file mapping lives in `DAY_FILES` at the top of `bicing.js`
(index 0 = Monday … 6 = Sunday).

### Availability file format

Flat array of rows (one per station × 15-min window):

```json
{
  "station_id": 1,
  "time_window": "08:15",
  "n": 39,
  "t1": { "any": 1.0, "ebike": 0.9, "mechanical": 1.0, "dock": 1.0 },
  "t2": { "any": 1.0, "ebike": 0.7, "mechanical": 1.0, "dock": 1.0 },
  "t3": { "...": "..." }, "t4": { "...": "..." }, "t5": { "...": "..." }
}
```

- `time_window` — `"HH:MM"`, 15-min bins (`00:00` … `23:45`, 96 per day)
- `n` — sample size (observed days behind the estimate)
- `t1`…`t5` — threshold K = "at least K". Maps to the UI as:
  - `any` → Pick up · **All**
  - `ebike` → Pick up · **E-bike**
  - `mechanical` → Pick up · **Classic**
  - `dock` → **Drop off** (free docks)

Rows for missing (station, window) combinations may be omitted; those cells show
as "no data".

## Optional: gzip (.json.gz)

Not needed to start — GitHub Pages already gzips responses on the fly. If you
later want pre-compressed `.json.gz` files, the only change is the `fetch` in
`loadAvailability()` in `bicing.js` (decompress with `DecompressionStream('gzip')`).
