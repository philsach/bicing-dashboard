#!/usr/bin/env python3
"""Bicing availability pipeline: monthly status files -> dashboard data.

Two steps:

  summarize  one monthly file from Barcelona Open Data (dataset
             "estat-estacions-bicing", e.g. 2026_10_Octubre_BicingNou_ESTACIONS.7z
             or the .csv inside it) -> data/monthly/2026_10.csv.gz

  build      the latest N monthly summaries (default 3) ->
             data/availability_mon.json ... availability_sun.json + data/meta.json

    python3 scripts/bicing_data.py summarize ~/Downloads/2026_10_Octubre_BicingNou_ESTACIONS.7z
    python3 scripts/bicing_data.py build --months 3

Requirements: pandas, and py7zr for .7z input (pip install pandas py7zr).

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
import re
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MONTHLY = DATA / "monthly"

TZ = "Europe/Madrid"
SNAP_SECONDS = 300          # source snapshot interval
INTERVAL_MIN = 15           # dashboard window
THRESHOLDS = [1, 2, 3, 4, 5]
FIELDS = ["any", "ebike", "mechanical", "dock"]
COUNT_COLS = [f"{f}_{k}" for f in FIELDS for k in THRESHOLDS]
KEYS = ["weekday", "station_id", "interval"]
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

SOURCE_COLS = [
    "station_id", "num_bikes_available_types.mechanical", "num_bikes_available_types.ebike",
    "num_docks_available", "status", "is_renting", "is_returning", "last_updated",
]


# ---------------------------------------------------------------- summarize --
def read_source(path: Path) -> pd.DataFrame:
    if path.suffix == ".7z":
        import py7zr  # only needed for .7z input
        with tempfile.TemporaryDirectory() as tmp:
            with py7zr.SevenZipFile(path) as z:
                csvs = [n for n in z.getnames() if n.lower().endswith(".csv")]
                if len(csvs) != 1:
                    sys.exit(f"{path}: expected one CSV inside, found {csvs}")
                z.extract(tmp, targets=csvs)
            return pd.read_csv(Path(tmp) / csvs[0], usecols=SOURCE_COLS)
    return pd.read_csv(path, usecols=SOURCE_COLS)


def month_id(path: Path, df: pd.DataFrame) -> str:
    m = re.match(r"(\d{4})_(\d{2})_", path.name)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    t = pd.to_datetime(df["last_updated"], unit="s", utc=True).dt.tz_convert(TZ)
    return t.dt.strftime("%Y_%m").mode()[0]


def summarize(path: Path, out_dir: Path = MONTHLY) -> Path:
    df = read_source(path)
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
    first, last = df["date"].min(), df["date"].max()
    shown = out.relative_to(ROOT) if out.resolve().is_relative_to(ROOT) else out
    print(f"{path.name}: {rows_in:,} rows -> {len(df):,} snapshots, {first} .. {last}, "
          f"{df['station_id'].nunique()} stations -> {shown} ({len(summary):,} cells)")
    return out


# -------------------------------------------------------------------- build --
def write_day(path: Path, rows) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"format":2,"fields":' + json.dumps(FIELDS) + ',"thresholds":' + str(len(THRESHOLDS)) + ',"rows":[\n')
        fh.write(",\n".join(json.dumps(r, separators=(",", ":")) for r in rows))
        fh.write("\n]}\n")


def build(months: int, monthly_dir: Path = MONTHLY, out_dir: Path = DATA) -> None:
    files = sorted(monthly_dir.glob("*.csv.gz"))
    if not files:
        sys.exit(f"No monthly summaries in {monthly_dir}. Run 'summarize' first.")
    files = files[-months:]
    used = [f.name.split(".")[0] for f in files]
    if len(files) < months:
        print(f"Note: only {len(files)} of {months} months available: {', '.join(used)}")

    total = pd.concat([pd.read_csv(f) for f in files]).groupby(KEYS, as_index=False).sum()
    pct = ((total[COUNT_COLS].to_numpy() * 100) / total[["snapshots"]].to_numpy() + 0.5).astype(int)

    stations = {s["station_id"] for s in json.loads((out_dir / "stations.json").read_text())}
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
        write_day(out_dir / f"availability_{DAY_NAMES[d]}.json", rows)
        print(f"availability_{DAY_NAMES[d]}.json: {len(rows):,} rows")

    meta = {
        "months": [m.replace("_", "-") for m in used],
        "generated": dt.date.today().isoformat(),
        "source": "Barcelona Open Data, dataset estat-estacions-bicing (CC BY 4.0)",
        "n": "days with data in the window",
        "method": "share of 5-minute snapshots in each 15-minute window with at least K bikes/docks; "
                  "closed stations count as unavailable (see scripts/bicing_data.py)",
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"meta.json: months {', '.join(meta['months'])}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("summarize", help="monthly .7z/.csv -> data/monthly/YYYY_MM.csv.gz")
    s.add_argument("files", nargs="+", type=Path)
    b = sub.add_parser("build", help="latest monthly summaries -> data/availability_*.json")
    b.add_argument("--months", type=int, default=3, help="how many recent months to combine (default 3)")
    a = ap.parse_args(argv)
    if a.cmd == "summarize":
        for f in a.files:
            summarize(f)
    else:
        build(a.months)


if __name__ == "__main__":
    main()
