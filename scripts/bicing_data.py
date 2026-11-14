#!/usr/bin/env python3
"""Bicing availability pipeline: monthly status files -> dashboard data.

Commands:

  update     the usual one: ask the portal's catalogue which months exist,
             fetch whatever is missing or was republished among the latest N
             complete months (default 3), summarize it, and rebuild the
             dashboard files from exactly those months. Also refreshes
             stations.json from the newest station-information file.
             Safe to run any time; it does nothing when everything is current.

  summarize  one monthly file from Barcelona Open Data (dataset
             "estat-estacions-bicing", e.g. 2026_10_Octubre_BicingNou_ESTACIONS.7z
             or the .csv inside it) -> data/monthly/2026_10.csv.gz

  build      the latest N monthly summaries (default 3) ->
             data/availability_mon.json ... availability_sun.json + data/meta.json

    python3 scripts/bicing_data.py update
    python3 scripts/bicing_data.py summarize ~/Downloads/2026_10_Octubre_BicingNou_ESTACIONS.7z
    python3 scripts/bicing_data.py build --months 3

Requirements: pandas and py7zr (pip install -r scripts/requirements.txt).

The portal refuses downloads from cloud servers, so `update` must run on a
normal connection (your own computer). It first looks for the needed files in
~/Downloads (change with --downloads), so a file you downloaded in a browser is
used as-is.

"Complete months": the portal publishes a month only after it has ended, so
the newest N published months are always complete. If a month was never
published, the window simply reaches one month further back.

How 5-minute snapshots become 15-minute probabilities
-----------------------------------------------------
The source has one row per station every ~5 minutes (a few seconds of jitter).

1. Each snapshot's time (last_updated) is rounded to the nearest 5 minutes, and
   only one snapshot per station and time is kept. Without this, ~2% of
   snapshots land a few seconds before the mark and fall into the previous
   window, so some windows got 2 snapshots and others 4.
2. The window is taken from the local wall-clock time in Europe/Madrid:
   interval = (hour * 60 + minute) // 15  (0 = 00:00 ... 95 = 23:45).
   This works across daylight-saving changes; the repeated autumn hour simply
   contributes extra snapshots to its windows.
3. Each snapshot is checked against the thresholds K = 1..5:
     any / ebike / mechanical : at least K bikes of that kind can be rented
     dock                     : at least K free docks a bike can be returned to
   A station that is not renting (closed, maintenance) counts as 0 bikes, and
   one not accepting returns counts as 0 docks: someone arriving there could
   not take or return a bike. "any" = classic + e-bike. Stations with status
   PLANNED (not opened yet) are left out entirely.
4. Probability = share of snapshots in that window meeting the threshold
   (i.e. the chance at a random moment in the window).
   n = number of days with at least one snapshot in that window.

The monthly summaries hold counts, not probabilities, so months can simply be
added together: probability = sum(count) / sum(snapshots).
"""
import argparse
import datetime as dt
import gzip
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

TZ = "Europe/Madrid"
SNAP_SECONDS = 300          # source snapshot interval
INTERVAL_MIN = 15           # dashboard window
THRESHOLDS = [1, 2, 3, 4, 5]
FIELDS = ["any", "ebike", "mechanical", "dock"]
COUNT_COLS = [f"{f}_{k}" for f in FIELDS for k in THRESHOLDS]
KEYS = ["weekday", "station_id", "interval"]
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MIN_DAYS_WARNING = 20       # warn when a month covers fewer days than this

SOURCE_COLS = [
    "station_id", "num_bikes_available_types.mechanical", "num_bikes_available_types.ebike",
    "num_docks_available", "status", "is_renting", "is_returning", "last_updated",
]

PORTAL = "https://opendata-ajuntament.barcelona.cat/data/api/3/action/package_show?id="
STATUS_DATASET = "estat-estacions-bicing"
INFO_DATASET = "informacio-estacions-bicing"
USER_AGENT = "bicing-dashboard data update (+https://github.com/philsach/bicing-dashboard)"
SEVENZIP_MAGIC = b"7z\xbc\xaf\x27\x1c"
STATION_FIELDS = ["station_id", "name", "lat", "lon", "altitude", "capacity", "physical_configuration", "address"]


# ------------------------------------------------------------------ helpers --
def monthly_dir(data_dir: Path) -> Path:
    return data_dir / "monthly"


def shown(path: Path) -> str:
    p = path.resolve()
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(path)


def extract_csv(archive: Path, tmp: str) -> Path:
    import py7zr  # only needed for .7z input
    with py7zr.SevenZipFile(archive) as z:
        csvs = [n for n in z.getnames() if n.lower().endswith(".csv")]
        if len(csvs) != 1:
            raise ValueError(f"{archive.name}: expected one CSV inside, found {csvs}")
        z.extract(tmp, targets=csvs)
    return Path(tmp) / csvs[0]


def read_csv_any(path: Path, usecols) -> pd.DataFrame:
    if path.suffix == ".7z":
        with tempfile.TemporaryDirectory() as tmp:
            return pd.read_csv(extract_csv(path, tmp), usecols=usecols)
    return pd.read_csv(path, usecols=usecols)


# ---------------------------------------------------------------- summarize --
def month_id(path: Path, df: pd.DataFrame) -> str:
    m = re.search(r"(\d{4})_(\d{2})_[A-Za-z]+_BicingNou", path.name) or re.match(r"(\d{4})_(\d{2})_", path.name)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    t = pd.to_datetime(df["last_updated"], unit="s", utc=True).dt.tz_convert(TZ)
    return t.dt.strftime("%Y_%m").mode()[0]


def summarize(path: Path, out_dir: Path | None = None):
    """Monthly status file -> count summary. Returns (summary path, info dict)."""
    out_dir = out_dir or monthly_dir(DATA)
    df = read_csv_any(path, SOURCE_COLS)
    month = month_id(path, df)
    rows_in = len(df)

    df = df.dropna(subset=["station_id", "last_updated"])
    df = df[df["status"] != "PLANNED"].copy()
    df["station_id"] = df["station_id"].astype(int)

    # 1. snap to the 5-minute grid, one snapshot per station and time
    df["t"] = ((df["last_updated"].astype("int64") + SNAP_SECONDS // 2) // SNAP_SECONDS) * SNAP_SECONDS
    df = df.drop_duplicates(["station_id", "t"], keep="last")

    # 2. local wall-clock window
    local = pd.to_datetime(df["t"], unit="s", utc=True).dt.tz_convert(TZ)
    df["date"] = local.dt.strftime("%Y-%m-%d")
    df["weekday"] = local.dt.weekday
    df["interval"] = (local.dt.hour * 60 + local.dt.minute) // INTERVAL_MIN

    # 3. what someone arriving could actually use
    renting = df["is_renting"].fillna(0).astype(int) == 1
    returning = df["is_returning"].fillna(0).astype(int) == 1
    mech = df["num_bikes_available_types.mechanical"].fillna(0).where(renting, 0)
    ebike = df["num_bikes_available_types.ebike"].fillna(0).where(renting, 0)
    docks = df["num_docks_available"].fillna(0).where(returning, 0)
    counts = {"any": mech + ebike, "ebike": ebike, "mechanical": mech, "dock": docks}
    for f in FIELDS:
        for k in THRESHOLDS:
            df[f"{f}_{k}"] = (counts[f] >= k).astype("int32")

    # 4. counts per (weekday, station, window)
    g = df.groupby(KEYS)
    summary = g[COUNT_COLS].sum()
    summary.insert(0, "snapshots", g.size())
    summary.insert(0, "days", g["date"].nunique())
    summary = summary.reset_index().sort_values(KEYS)

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{month}.csv.gz"
    with gzip.open(out, "wt", encoding="utf-8", newline="") as fh:
        summary.to_csv(fh, index=False)
    info = {
        "month": month,
        "first_day": df["date"].min(),
        "last_day": df["date"].max(),
        "days_covered": int(df["date"].nunique()),
        "stations": int(df["station_id"].nunique()),
        "snapshots": int(len(df)),
    }
    print(f"{path.name}: {rows_in:,} rows -> {len(df):,} snapshots, {info['first_day']} .. {info['last_day']}, "
          f"{info['stations']} stations -> {shown(out)} ({len(summary):,} cells)")
    if info["days_covered"] < MIN_DAYS_WARNING:
        print(f"  WARNING: {month} only covers {info['days_covered']} days")
    return out, info


# -------------------------------------------------------------------- build --
def write_day(path: Path, rows) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"format":2,"fields":' + json.dumps(FIELDS) + ',"thresholds":' + str(len(THRESHOLDS)) + ',"rows":[\n')
        fh.write(",\n".join(json.dumps(r, separators=(",", ":")) for r in rows))
        fh.write("\n]}\n")


def build(months: int = 3, data_dir: Path = DATA, month_ids=None) -> None:
    """Sum monthly summaries into the weekday files.

    month_ids: exact months to use (e.g. ["2026_08", "2026_09", "2026_10"]);
    default: the newest `months` summaries present."""
    mdir = monthly_dir(data_dir)
    if month_ids:
        files = [mdir / f"{m}.csv.gz" for m in sorted(month_ids)]
        missing = [f.name for f in files if not f.exists()]
        if missing:
            sys.exit(f"Missing monthly summaries: {missing}")
    else:
        files = sorted(mdir.glob("*.csv.gz"))[-months:]
        if not files:
            sys.exit(f"No monthly summaries in {mdir}. Run 'summarize' first.")
        if len(files) < months:
            print(f"Note: only {len(files)} of {months} months available")
    used = [f.name.split(".")[0] for f in files]

    total = pd.concat([pd.read_csv(f) for f in files]).groupby(KEYS, as_index=False).sum()
    pct = ((total[COUNT_COLS].to_numpy() * 100) / total[["snapshots"]].to_numpy() + 0.5).astype(int)

    stations = {s["station_id"] for s in json.loads((data_dir / "stations.json").read_text())}
    unknown = sorted(set(total["station_id"]) - stations)
    if unknown:
        print(f"Note: {len(unknown)} station(s) not in stations.json won't be shown: {unknown[:10]}")

    # row layout: [station_id, interval, n_days, t1.any, t1.ebike, t1.mechanical, t1.dock, t2.any, ...]
    order = [COUNT_COLS.index(f"{f}_{k}") for k in THRESHOLDS for f in FIELDS]
    keys = total[KEYS + ["days"]].to_numpy()
    by_day = {d: [] for d in range(7)}
    for (wd, sid, iv, days), p in zip(keys, pct):
        by_day[int(wd)].append([int(sid), int(iv), int(days)] + [int(p[i]) for i in order])
    for d, rows in by_day.items():
        write_day(data_dir / f"availability_{DAY_NAMES[d]}.json", rows)
    print(f"Built weekday files from {', '.join(used)} ({sum(len(r) for r in by_day.values()):,} rows)")

    meta = {
        "months": [m.replace("_", "-") for m in used],
        "generated": dt.date.today().isoformat(),
        "source": "Barcelona Open Data, dataset estat-estacions-bicing (CC BY 4.0)",
        "n": "days with data in the window",
        "method": "share of 5-minute snapshots in each 15-minute window with at least K bikes/docks; "
                  "closed stations count as unavailable (see scripts/bicing_data.py)",
    }
    (data_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- stations --
def update_stations(info_file: Path, data_dir: Path = DATA) -> int:
    """Merge the newest record per station from a station-information file into
    stations.json. Stations not in the file are kept. Returns #stations added."""
    df = read_csv_any(info_file, usecols=lambda c: c in set(STATION_FIELDS) | {"last_updated"})
    need = {"station_id", "name", "lat", "lon", "capacity"}
    if not need <= set(df.columns):
        raise ValueError(f"{info_file.name}: missing columns {sorted(need - set(df.columns))}")
    df = df.dropna(subset=["station_id", "lat", "lon"])
    df["station_id"] = df["station_id"].astype(int)
    if "last_updated" in df.columns:
        df = df.sort_values("last_updated")
    latest = df.drop_duplicates("station_id", keep="last")

    path = data_dir / "stations.json"
    current = {s["station_id"]: s for s in json.loads(path.read_text())}
    before = set(current)
    for rec in latest.to_dict("records"):
        s = current.setdefault(int(rec["station_id"]), {})
        for f in STATION_FIELDS:
            if f in rec and pd.notna(rec[f]):
                v = rec[f]
                s[f] = int(v) if f in ("station_id", "capacity") else (float(v) if f in ("lat", "lon", "altitude") else v)
    out = [current[k] for k in sorted(current)]
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    added = len(set(current) - before)
    print(f"stations.json: {len(out)} stations ({added} new) from {info_file.name}")
    return added


# ------------------------------------------------------------------- update --
def catalogue(dataset: str) -> dict:
    """month id -> {name, url, version} for the dataset's monthly 7z files."""
    req = urllib.request.Request(PORTAL + dataset, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.load(r)["result"]["resources"]
    out = {}
    for x in res:
        m = re.match(r"(\d{4})_(\d{2})_.*\.7z$", x.get("name") or "")
        if m:
            out[f"{m.group(1)}_{m.group(2)}"] = {
                "name": x["name"], "url": x["url"],
                "version": f"{x.get('id')}@{x.get('last_modified') or x.get('created')}",
            }
    return out


def find_local(name: str, dirs) -> Path | None:
    for d in dirs:
        try:
            hits = sorted(Path(d).expanduser().glob(f"*{name}"), key=lambda p: p.stat().st_mtime)
        except OSError:  # e.g. macOS privacy protection on ~/Downloads
            continue
        if hits:
            return hits[-1]
    return None


def download(entry: dict, dest_dir: Path) -> Path:
    dest = dest_dir / entry["name"]
    headers = {"User-Agent": USER_AGENT}
    if os.environ.get("BCN_OPENDATA_TOKEN"):
        headers["Authorization"] = os.environ["BCN_OPENDATA_TOKEN"]
    req = urllib.request.Request(entry["url"], headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as fh:
            shutil.copyfileobj(r, fh)
    except urllib.error.HTTPError as e:
        hint = (" The portal refuses downloads from cloud servers; run this on your own computer,"
                " or download the file in a browser into ~/Downloads.") if e.code == 403 else ""
        raise RuntimeError(f"download of {entry['name']} failed: HTTP {e.code}.{hint}") from None
    with open(dest, "rb") as fh:
        if fh.read(6) != SEVENZIP_MAGIC:
            raise RuntimeError(f"download of {entry['name']} did not return a 7z archive")
    return dest


def obtain(entry: dict, dirs, tmp: Path) -> Path:
    local = find_local(entry["name"], dirs)
    if local:
        print(f"  using {local}")
        return local
    print(f"  downloading {entry['name']} ...")
    return download(entry, tmp)


def update(months: int = 3, data_dir: Path = DATA, downloads=("~/Downloads",), dry_run: bool = False,
           stations: bool = True, status_cat=None, info_cat=None) -> int:
    """Returns 0 when the data is current (changed or not), 2 when a needed month could not be obtained."""
    mdir = monthly_dir(data_dir)
    index_path = mdir / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}

    status_cat = status_cat if status_cat is not None else catalogue(STATUS_DATASET)
    wanted = sorted(status_cat)[-months:]
    todo = [m for m in wanted
            if not (mdir / f"{m}.csv.gz").exists() or index.get(m, {}).get("version") != status_cat[m]["version"]]
    print(f"Portal: {len(status_cat)} monthly files, newest {max(status_cat)}. Using the latest {months}: {', '.join(wanted)}")
    print("Up to date." if not todo else f"Needs processing: {', '.join(todo)}")
    for m in wanted:
        if index.get(m, {}).get("days_covered", MIN_DAYS_WARNING) < MIN_DAYS_WARNING:
            print(f"  WARNING: {m} only covers {index[m]['days_covered']} days")

    current_meta = {}
    meta_path = data_dir / "meta.json"
    if meta_path.exists():
        current_meta = json.loads(meta_path.read_text())
    needs_build = bool(todo) or current_meta.get("months") != [m.replace("_", "-") for m in wanted]

    info_entry = None
    if stations:
        info_cat = info_cat if info_cat is not None else catalogue(INFO_DATASET)
        if info_cat:
            info_entry = info_cat[max(info_cat)]
            if index.get("_stations", {}).get("version") == info_entry["version"]:
                info_entry = None
            else:
                print(f"Station list: refreshing from {info_entry['name']}")
    if dry_run:
        print("Dry run: nothing changed." + (" Would rebuild the weekday files." if needs_build else ""))
        return 0

    failed = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for m in todo:
            print(f"{m}:")
            try:
                src = obtain(status_cat[m], downloads, tmp)
                _, info = summarize(src, mdir)
                if info["month"] != m:
                    raise RuntimeError(f"{src.name} contains {info['month']}, expected {m}")
                index[m] = {**info, "source": status_cat[m]["name"], "version": status_cat[m]["version"],
                            "processed": dt.date.today().isoformat()}
                index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
            except Exception as e:  # keep going so the summary is complete
                print(f"  ERROR: {e}")
                failed.append(m)
        if info_entry:
            try:
                src = obtain(info_entry, downloads, tmp)
                update_stations(src, data_dir)
                index["_stations"] = {"source": info_entry["name"], "version": info_entry["version"],
                                      "processed": dt.date.today().isoformat()}
                index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
                needs_build = True  # new stations may now be shown
            except Exception as e:
                print(f"  WARNING: station list not refreshed: {e}")

    if failed:
        print(f"Not rebuilt: missing {', '.join(failed)}. The dashboard keeps its current data.")
        return 2
    if needs_build:
        build(data_dir=data_dir, month_ids=wanted)
    return 0


# --------------------------------------------------------------------- main --
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", type=Path, default=DATA, help="dashboard data folder (default: data/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update", help="fetch and process whatever is missing, then rebuild")
    u.add_argument("--months", type=int, default=3, help="how many recent complete months to use (default 3)")
    u.add_argument("--downloads", action="append", help="folder to look for already-downloaded files "
                   "(default ~/Downloads; repeatable)")
    u.add_argument("--dry-run", action="store_true", help="only show what would happen")
    u.add_argument("--no-stations", action="store_true", help="don't refresh stations.json")
    s = sub.add_parser("summarize", help="monthly .7z/.csv -> data/monthly/YYYY_MM.csv.gz")
    s.add_argument("files", nargs="+", type=Path)
    b = sub.add_parser("build", help="latest monthly summaries -> data/availability_*.json")
    b.add_argument("--months", type=int, default=3, help="how many recent months to combine (default 3)")
    a = ap.parse_args(argv)
    if a.cmd == "update":
        sys.exit(update(a.months, a.data_dir, a.downloads or ["~/Downloads"], a.dry_run, not a.no_stations))
    if a.cmd == "summarize":
        for f in a.files:
            summarize(f, monthly_dir(a.data_dir))
    else:
        build(a.months, a.data_dir)


if __name__ == "__main__":
    main()
