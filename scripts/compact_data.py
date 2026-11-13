#!/usr/bin/env python3
"""Convert availability files to the compact format the app loads.

Usage:
    python3 scripts/compact_data.py data/availability_*.json

Input: the pandas export, a flat JSON array of row objects:
    {"station_id": 1, "time_window": "08:15", "n": 39,
     "t1": {"any": 1.0, "ebike": 0.9, "mechanical": 1.0, "dock": 1.0}, ... "t5": {...}}

Output (written over the input file): the same data as plain arrays with
probabilities stored as whole percentages (0-100). The source values have at
most two decimals, so the conversion is lossless; the script checks this and
refuses to write a file if any value would be rounded.

    {"format": 2,
     "fields": ["any", "ebike", "mechanical", "dock"],
     "thresholds": 5,
     "rows": [[station_id, interval, n, t1.any, t1.ebike, t1.mechanical, t1.dock,
               t2.any, ..., t5.dock], ...]}

interval = 15-minute bin index (00:00 -> 0, 23:45 -> 95). A missing value is
written as null. Files already in the compact format are left untouched.
"""
import json
import sys

FIELDS = ["any", "ebike", "mechanical", "dock"]
THRESHOLDS = 5
INTERVAL_MIN = 15


def to_pct(v, where):
    if v is None:
        return None
    p = round(v * 100)
    if abs(p - v * 100) > 1e-6:
        raise ValueError(f"{where}: {v} has more than two decimals; conversion would lose precision")
    return p


def convert(rows, path):
    out = []
    for r in rows:
        hh, mm = r["time_window"].split(":")
        iv = (int(hh) * 60 + int(mm)) // INTERVAL_MIN
        row = [r["station_id"], iv, r["n"]]
        for k in range(1, THRESHOLDS + 1):
            t = r.get(f"t{k}") or {}
            for f in FIELDS:
                row.append(to_pct(t.get(f), f"{path} station {r['station_id']} {r['time_window']} t{k}.{f}"))
        out.append(row)
    return {"format": 2, "fields": FIELDS, "thresholds": THRESHOLDS, "rows": out}


def main(paths):
    if not paths:
        sys.exit(__doc__)
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("format") == 2:
            print(f"{path}: already compact, skipped")
            continue
        compact = convert(data, path)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"format":2,"fields":' + json.dumps(FIELDS) + ',"thresholds":' + str(THRESHOLDS) + ',"rows":[\n')
            fh.write(",\n".join(json.dumps(r, separators=(",", ":")) for r in compact["rows"]))
            fh.write("\n]}\n")
        print(f"{path}: {len(compact['rows'])} rows converted")


if __name__ == "__main__":
    main(sys.argv[1:])
