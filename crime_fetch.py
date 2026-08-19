#!/opt/homebrew/bin/python3.11
"""
Flintshire Crime Dashboard: monthly data fetcher.

Pulls street-level crime for Flintshire neighbourhoods from data.police.uk
and writes static JSON into the site repo. Netlify redeploys on push.

Replaces 59 live API calls per visitor with zero.

Run monthly. Police.uk updates once a month, usually mid-month,
covering data about two months in arrears.

Usage:
    python3.11 crime_fetch.py              # fetch any months not yet stored
    python3.11 crime_fetch.py --months 12  # backfill last 12 months
    python3.11 crime_fetch.py --refresh    # re-fetch even if already stored
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

API = "https://data.police.uk/api"
FORCE = "north-wales"

# Where the site repo lives. Change if you clone it elsewhere.
REPO = Path.home() / "deeside-crime-dashboard"
DATA_DIR = REPO / "data"

LOG_DIR = Path.home() / "logs"
LOG_FILE = LOG_DIR / "crime_fetch.log"

# Flintshire neighbourhood IDs, carried over from the dashboard.
# Verified against the map: these are Flintshire, including the Talacre
# and Point of Ayr coast in the north and the Caergwrle and Hope areas
# in the south, both of which sit close to neighbouring county lines.
FLINTSHIRE_IDS = [
    "FNW01", "FNW02", "FNW03", "FNW04", "FNW05", "FNW06", "FNW07", "FNW08",
    "FNW09", "FNW10", "FNW11", "FNW12", "FNW13", "FNW14", "FNW15", "FNW16",
    "FNW17", "FNW18", "FNW19", "FNW20", "FNW21", "FNW22", "FNW23", "FNW24",
    "FNW25", "FNW26",
    "FSW01", "FSW02", "FSW03", "FSW04", "FSW05", "FSW06", "FSW07", "FSW08",
    "FSW09", "FSW10", "FSW11", "FSW12", "FSW13", "FSW14", "FSW15", "FSW16",
    "FSW17", "FSW18", "FSW19", "FSW20", "FSW21", "FSW22", "FSW23", "FSW24",
    "FSW25", "FSW26", "FSW27", "FSW28", "FSW29", "FSW30", "FSW31",
]

DEESIDE_IDS = [
    "FNW16", "FNW17", "FNW18", "FNW19", "FNW20", "FNW21", "FNW22", "FNW25",
    "FSW09", "FSW10", "FSW13", "FSW16", "FSW17", "FSW18", "FSW19",
]

PAUSE = 0.4          # seconds between API calls, to stay a good citizen
RETRIES = 3
TIMEOUT = 60

# ----------------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------------

LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("crime")

session = requests.Session()
session.headers.update({"User-Agent": "Deeside.com crime dashboard (editorial use)"})


def api_get(path):
    """GET a JSON endpoint with retries."""
    url = f"{API}{path}"
    for attempt in range(1, RETRIES + 1):
        try:
            r = session.get(url, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            log.warning("GET %s returned %s (attempt %s)", path, r.status_code, attempt)
        except requests.RequestException as e:
            log.warning("GET %s failed: %s (attempt %s)", path, e, attempt)
        time.sleep(2 * attempt)
    return None


def api_post_poly(boundary, date):
    """
    POST a polygon crime query.

    POST is used rather than GET so the full boundary can be sent.
    Squeezing the polygon into a URL forces you to simplify it, which
    distorts the edges and moves crimes in and out of the area.
    """
    poly = ":".join(f"{p['latitude']},{p['longitude']}" for p in boundary)
    url = f"{API}/crimes-street/all-crime"
    for attempt in range(1, RETRIES + 1):
        try:
            r = session.post(url, data={"poly": poly, "date": date}, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 503:
                # Too many crimes in the requested area for one call.
                log.warning("503 for %s: area too large for a single query", date)
                return None
            if r.status_code == 404:
                return []
            log.warning("POST poly %s returned %s (attempt %s)", date, r.status_code, attempt)
        except requests.RequestException as e:
            log.warning("POST poly %s failed: %s (attempt %s)", date, e, attempt)
        time.sleep(2 * attempt)
    return None


def slim(crime):
    """
    Cut each crime down to what the dashboard actually renders.

    The raw API record is roughly four times this size. Trimming keeps
    a year of data small enough to load instantly.
    """
    loc = crime.get("location") or {}
    street = (loc.get("street") or {}).get("name")
    outcome = (crime.get("outcome_status") or {}).get("category")
    lat = loc.get("latitude")
    lng = loc.get("longitude")
    return {
        "c": crime.get("category"),
        "lat": round(float(lat), 6) if lat else None,
        "lng": round(float(lng), 6) if lng else None,
        "s": street,
        "o": outcome,
    }


# ----------------------------------------------------------------------------
# Fetch steps
# ----------------------------------------------------------------------------

def get_areas():
    """Fetch Flintshire neighbourhoods and their full boundaries."""
    log.info("Fetching neighbourhood list for %s", FORCE)
    all_n = api_get(f"/{FORCE}/neighbourhoods")
    if not all_n:
        log.error("Could not fetch neighbourhood list. Stopping.")
        return None

    wanted = {n["id"]: n["name"] for n in all_n if n["id"] in FLINTSHIRE_IDS}

    missing = [i for i in FLINTSHIRE_IDS if i not in wanted]
    if missing:
        # Forces restructure neighbourhood teams. If this fires, the ID list
        # needs reviewing before the totals can be trusted.
        log.warning("%s configured IDs no longer exist upstream: %s",
                    len(missing), ", ".join(missing))

    areas = []
    for i, (nid, name) in enumerate(sorted(wanted.items()), 1):
        b = api_get(f"/{FORCE}/{nid}/boundary")
        time.sleep(PAUSE)
        if not isinstance(b, list) or len(b) < 3:
            log.warning("No usable boundary for %s (%s)", nid, name)
            continue
        areas.append({
            "id": nid,
            "name": name,
            "deeside": nid in DEESIDE_IDS,
            "boundary": [{"latitude": float(p["latitude"]),
                          "longitude": float(p["longitude"])} for p in b],
        })
        log.info("  [%2d/%2d] %s  %s  (%s boundary points)",
                 i, len(wanted), nid, name, len(b))

    log.info("Loaded %s Flintshire areas", len(areas))
    return areas


def fetch_month(areas, date):
    """Fetch every Flintshire neighbourhood for one month and combine."""
    log.info("Fetching %s across %s areas", date, len(areas))
    crimes = []
    seen = set()
    no_id = 0
    failed = []

    for i, area in enumerate(areas, 1):
        data = api_post_poly(area["boundary"], date)
        time.sleep(PAUSE)

        if data is None:
            failed.append(area["name"])
            log.warning("  [%2d/%2d] %-32s FAILED", i, len(areas), area["name"])
            continue

        added = 0
        for c in data:
            pid = c.get("persistent_id")
            if pid:
                # Neighbourhood boundaries can touch, so the same crime can
                # come back from two areas. persistent_id is the only safe
                # key: crimes are snapped to shared anonymised map points,
                # so matching on location and category would delete real ones.
                if pid in seen:
                    continue
                seen.add(pid)
            else:
                no_id += 1
            rec = slim(c)
            rec["a"] = area["id"]
            crimes.append(rec)
            added += 1

        log.info("  [%2d/%2d] %-32s %4d crimes", i, len(areas), area["name"], added)

    if failed:
        log.warning("%s areas failed for %s: %s", len(failed), date, ", ".join(failed))
    if no_id:
        log.info("%s crimes had no persistent_id and were kept unfiltered", no_id)

    return crimes, failed


def summarise(crimes):
    cats, outcomes, by_area = {}, {}, {}
    for c in crimes:
        cats[c["c"]] = cats.get(c["c"], 0) + 1
        if c["o"]:
            outcomes[c["o"]] = outcomes.get(c["o"], 0) + 1
        by_area[c["a"]] = by_area.get(c["a"], 0) + 1
    return {
        "total": len(crimes),
        "with_outcome": sum(1 for c in crimes if c["o"]),
        "categories": dict(sorted(cats.items(), key=lambda x: -x[1])),
        "outcomes": dict(sorted(outcomes.items(), key=lambda x: -x[1])),
        "by_area": by_area,
    }


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=0,
                    help="fetch this many most recent months (default: only missing ones)")
    ap.add_argument("--refresh", action="store_true",
                    help="re-fetch months already stored")
    args = ap.parse_args()

    if not REPO.exists():
        log.error("Repo folder not found: %s", REPO)
        log.error("Clone the site repo there first, then re-run.")
        sys.exit(1)

    DATA_DIR.mkdir(exist_ok=True)

    available = api_get("/crimes-street-dates")
    if not available:
        log.error("Could not fetch the available dates list. Stopping.")
        sys.exit(1)
    all_dates = [d["date"] for d in available]
    log.info("API has data for %s months, latest %s", len(all_dates), all_dates[0])

    if args.months:
        targets = all_dates[:args.months]
    else:
        targets = all_dates[:1]

    if not args.refresh:
        targets = [d for d in targets if not (DATA_DIR / f"{d}.json").exists()]

    if not targets:
        log.info("Nothing to fetch. All requested months already stored.")
        return

    log.info("Will fetch: %s", ", ".join(targets))

    areas = get_areas()
    if not areas:
        sys.exit(1)

    (DATA_DIR / "areas.json").write_text(json.dumps(areas, separators=(",", ":")))
    log.info("Wrote areas.json")

    for date in targets:
        crimes, failed = fetch_month(areas, date)
        if not crimes:
            log.warning("No crimes returned for %s. Skipping write.", date)
            continue

        payload = {
            "month": date,
            "fetched": datetime.now().isoformat(timespec="seconds"),
            "source": "data.police.uk",
            "areas_queried": len(areas),
            "areas_failed": failed,
            "summary": summarise(crimes),
            "crimes": crimes,
        }
        out = DATA_DIR / f"{date}.json"
        out.write_text(json.dumps(payload, separators=(",", ":")))
        size = out.stat().st_size / 1024
        log.info("Wrote %s  (%s crimes, %.0f KB)", out.name, len(crimes), size)

    # Build the index every run so the front end knows what exists.
    months = []
    for f in sorted(DATA_DIR.glob("20*.json"), reverse=True):
        d = json.loads(f.read_text())
        months.append({
            "month": d["month"],
            "fetched": d["fetched"],
            "total": d["summary"]["total"],
            "with_outcome": d["summary"]["with_outcome"],
            "categories": d["summary"]["categories"],
            "by_area": d["summary"]["by_area"],
        })

    index = {
        "updated": datetime.now().isoformat(timespec="seconds"),
        "latest_available": all_dates[0],
        "months": months,
    }
    (DATA_DIR / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    log.info("Wrote index.json covering %s months", len(months))
    log.info("Done. Commit and push to deploy.")


if __name__ == "__main__":
    main()
