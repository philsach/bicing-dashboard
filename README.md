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
  bicing_data.py             data pipeline: monthly source files → dashboard data
  requirements.txt           Python packages for the pipeline
data/
  stations.json              546 stations: id, name, lat/lon, capacity
  availability_mon.json … availability_sun.json
                             one file per weekday, loaded when that day is shown
  meta.json                  which months the numbers cover (shown in the header)
  monthly/YYYY_MM.csv.gz     per-month count summaries the weekday files are built from
```

## Data

Source: [Barcelona Open Data](https://opendata-ajuntament.barcelona.cat/),
dataset **estat-estacions-bicing** ("New Bicing stations status", CC BY 4.0).
It is published as one 7z archive per month (about 20 MB), each holding a CSV
with a snapshot of every station every 5 minutes.

Each weekday file holds, per station and 15-minute window, the probability of
finding at least 1–5 bikes (any / e-bike / classic) or free docks, plus `n`,
the number of days behind the estimate. Cells based on fewer than 4 days are
shown faded on the map (`LOW_N` in `bicing.js`), and the station panel says
how many days each number is based on.

### How the numbers are calculated

`scripts/bicing_data.py` turns 5-minute snapshots into 15-minute probabilities:

1. Each snapshot's time is rounded to the nearest 5 minutes (the feed has a
   few seconds of jitter), keeping one snapshot per station and time. Every
   day then contributes exactly 3 snapshots to a window.
2. The window comes from the local clock time in Barcelona, so
   daylight-saving changes are handled.
3. A snapshot counts as "≥K bikes" only if the station is renting, and as
   "≥K docks" only if it accepts returns: a closed station counts as
   unavailable, because someone arriving there couldn't use it. "Any bike" =
   classic + e-bike. Stations that are only planned are left out.
4. Probability = share of snapshots in the window that meet the threshold
   (the chance at a random moment in that window). `n` = days with data.

Each month is first reduced to a small summary of **counts** in
`data/monthly/` (about 3 MB instead of 20 MB). Counts can be added, so the
dashboard is built from the latest months by summing them. The raw monthly
archives are not stored in the repository.

### Updating the data

```bash
pip install -r scripts/requirements.txt
python3 scripts/bicing_data.py summarize ~/Downloads/2026_10_Octubre_BicingNou_ESTACIONS.7z
python3 scripts/bicing_data.py build --months 3
```

`summarize` accepts the `.7z` or the `.csv` inside it, and several files at
once. `build` combines the latest 3 monthly summaries (change with
`--months`) into the weekday files and `data/meta.json`.

The monthly archives must be downloaded in a browser: the portal refuses
downloads from cloud servers (including GitHub Actions), with or without an
access token. Its catalogue API still works from anywhere.

The weekday files currently in the repository predate this pipeline (about
3 months processed with an earlier script that still counted bikes at closed
stations). They will be replaced on the first `build` with 3 months of data.

### Data file format

Each `availability_*.json`:

```json
{ "format": 2, "fields": ["any", "ebike", "mechanical", "dock"], "thresholds": 5,
  "rows": [[1, 33, 13, 100, 90, 100, 100, 100, 70, 100, 100, "…"]] }
```

Each row is `[station_id, interval (0–95 = 00:00–23:45), n (days), t1.any,
t1.ebike, t1.mechanical, t1.dock, t2.any, …, t5.dock]`, probabilities as whole
percentages. Missing (station, window) combinations are omitted and show as
"no data". If a weekday file is missing, that day's stations render grey and a
note says which file to add; there is no fallback to another day. The loader
also still accepts the older pandas row-object format.

## Optional: gzip (.json.gz)

Not needed to start — GitHub Pages already gzips responses on the fly. If you
later want pre-compressed `.json.gz` files, the only change is the `fetch` in
`loadAvailability()` in `bicing.js` (decompress with `DecompressionStream('gzip')`).
