# Bicing Availability

Interactive map of historical Bicing (Barcelona bike-share) availability — the
probability of finding **≥N bikes** (pick up) or **≥N free docks** (drop off) at
each station, by **weekday** and **time of day** (15-minute resolution).

Pure static site: HTML, one compiled React file and a small vanilla-JS data
layer. No backend. The only build step is compiling `app.jsx` to `app.js`
(see below).

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

## Editing the app

`app.jsx` is the source; the page loads the compiled `app.js`. After changing
`app.jsx`, rebuild and commit both files:

```bash
npm install      # once, installs esbuild
npm run build    # app.jsx → app.js (+ app.js.map)
```

`npm run watch` rebuilds on every save while you work. `bicing.js` and
`index.html` are plain files and need no build.

## Files

```
index.html      page shell + styling
app.jsx         React UI (source)
app.js          compiled from app.jsx by `npm run build` (what the page loads)
bicing.js       DATA LAYER (code) — the only place that knows the data format
package.json    build script (esbuild)
scripts/
  compact_data.py            converts the pandas export to the compact format
data/
  stations.json              546 stations: id, name, lat/lon, capacity
  availability_mon.json … availability_sun.json
                             one file per weekday, loaded when that day is shown
```

## Data

Each weekday file holds, per station and 15-minute window, the probability of
finding at least 1–5 bikes (any / e-bike / classic) or free docks, plus `n`,
the number of observed days behind the estimate (typically 39–41). Cells based
on fewer than 10 days are shown faded on the map, and the station panel says
how many days each number is based on. The threshold is `LOW_N` in
`bicing.js`.

If a weekday file is missing, that day's stations render grey and a note says
which file to add (no fallback to another day, so a missing file can never
masquerade as real data). The weekday → file mapping lives in `DAY_FILES` at
the top of `bicing.js` (index 0 = Monday … 6 = Sunday).

### Updating the data

Export each weekday from pandas in the original row format, drop the files into
`data/` with the names above, then compact them:

```bash
python3 scripts/compact_data.py data/availability_*.json
```

This shrinks each file from about 20 MB to about 4 MB without changing any
value (the script refuses to write if a value would be rounded). The app also
still reads the original format directly, it is just slower to load.

Original (pandas) format, a flat array of rows, one per station × window:

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

Compact format (written by the script): the same values as plain arrays, with
probabilities as whole percentages:

```json
{ "format": 2, "fields": ["any", "ebike", "mechanical", "dock"], "thresholds": 5,
  "rows": [[1, 33, 39, 100, 90, 100, 100, 100, 70, 100, 100, "…t5"]] }
```

Each row is `[station_id, interval (0–95), n, t1.any, t1.ebike,
t1.mechanical, t1.dock, t2.any, …, t5.dock]`.

Rows for missing (station, window) combinations may be omitted; those cells show
as "no data".

## Optional: gzip (.json.gz)

Not needed to start — GitHub Pages already gzips responses on the fly. If you
later want pre-compressed `.json.gz` files, the only change is the `fetch` in
`loadAvailability()` in `bicing.js` (decompress with `DecompressionStream('gzip')`).
