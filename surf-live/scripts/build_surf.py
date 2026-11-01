r"""
build_surf.py  --  ad-free surf report cards for kauaiconnect.com (replaces the Surfline frames on /surf-reports-all/).

What it does
  1. For each spot in ..\surf_spot.csv it asks Surfline's public (unofficial, undocumented) forecast service for the
     CURRENT rating, surf height, swells, wind and weather: 4 small requests per spot, ~1 second apart.
  2. Gets tide and water temperature from NOAA (Nawiliwili station), which is free and official.
  3. Writes ..\surf.json and one self-contained page ..\kauai_surf_cards.html (cards, rating colour, no ads).

Things to know
  * Surfline publishes no official data feed for websites. This uses the same unofficial feed your 2024 surf_report.py used.
    It can change or be blocked at any time, and Surfline's terms may not allow it - check with them. Every card
    credits Surfline and links to its full report. It is meant to be run once an hour at most; please keep it gentle.
  * A spot that cannot be read shows "report unavailable" and a link to Surfline instead of stale numbers.
  * The HTML is a snapshot: it shows when it was built, and warns visitors when it is more than ~2.5 hours old.

Options:  --no-photos  leave out the spot photos (they are read from kauaiconnect.com/wp-content/uploads/<file1>)

Usage (Spyder):
    %run build_surf.py                 # live: fetch, write surf.json + kauai_surf_cards.html
    %run build_surf.py --offline       # rebuild the page from the last surf.json (no network)
    %run build_surf.py --sample        # preview with made-up numbers (no network)
    %run build_surf.py --debug         # also save the raw responses to surf_raw.json to diagnose problems
"""
import argparse, csv, json, os, sys, time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
BASE = os.path.abspath(os.path.join(HERE, ".."))
TEMPLATE = os.path.join(HERE, "surf_template", "surf_cards.html")
SL = "https://services.surfline.com/kbyg/spots/forecasts/"
NOAA = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
STATION = "1611400"                                   # Nawiliwili
UA = "KauaiConnect-surfcards/1.0 (+https://kauaiconnect.com)"
HST = timezone(timedelta(hours=-10))
REGIONS = ["North", "East", "Lihue", "South", "West"]
UNITS = {"units[waveHeight]": "FT", "units[swellHeight]": "FT", "units[windSpeed]": "KTS",
         "units[temperature]": "F", "units[tideHeight]": "FT"}
csv.field_size_limit(10**9)


# ------------------------------------------------------------------ helpers
def get_json(url, params=None, tries=2, pause=2.0):
    import requests
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=30)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:                       # network trouble: wait and try once more
            last = e
        time.sleep(pause)
    raise last


def nearest(items, now_ts, key="timestamp"):
    """The forecast entry for the CURRENT hour: the latest one that has started (Surfline's page does the same at
    7:49 it shows the 7:00 entry). Falls back to the closest entry if none has started yet. Epoch seconds."""
    items = [x for x in (items or []) if isinstance(x, dict) and x.get(key) is not None]
    if not items:
        return None
    started = [x for x in items if x[key] <= now_ts]
    return max(started, key=lambda x: x[key]) if started else min(items, key=lambda x: abs(x[key] - now_ts))


def pick(d, *path, default=None):
    for p in path:
        if isinstance(d, dict):
            d = d.get(p)
        elif isinstance(d, list) and isinstance(p, int) and p < len(d):
            d = d[p]
        else:
            return default
    return default if d is None else d


def find_dict(obj, key, depth=6):
    """First dict stored under `key` anywhere inside obj (the feed nests things differently in different endpoints)."""
    if depth < 0:
        return None
    if isinstance(obj, dict):
        if isinstance(obj.get(key), dict):
            return obj[key]
        for v in obj.values():
            r = find_dict(v, key, depth - 1)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj[:5]:
            r = find_dict(v, key, depth - 1)
            if r:
                return r
    return None


def find_list(obj, key, depth=6):
    """First non-empty list of dicts stored under `key` anywhere inside obj."""
    if depth < 0:
        return None
    if isinstance(obj, dict):
        v = obj.get(key)
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
        for x in obj.values():
            r = find_list(x, key, depth - 1)
            if r:
                return r
    elif isinstance(obj, list):
        for x in obj[:5]:
            r = find_list(x, key, depth - 1)
            if r:
                return r
    return None


def parse_report_swells(rep):
    sw = find_list(rep, "swells") or []
    return [{"h": x.get("height"), "p": x.get("period"), "dir": x.get("direction")} for x in sw if x and x.get("height")][:3]


def parse_report(rep):
    """Fallback source for the CURRENT surf height: Surfline's spot report ('reports' endpoint)."""
    wh = find_dict(rep, "waveHeight") or {}
    lo, hi = wh.get("min"), wh.get("max")
    if lo is None or hi is None:
        return None
    return {"min": lo, "max": hi, "plus": bool(wh.get("plus")), "rel": wh.get("humanRelation") or ""}


def parse_spot(spot, raw, now_ts):
    """raw = {'rating': json, 'wave': json, 'wind': json, 'weather': json} from Surfline (any may be missing)."""
    out = {"rating": None, "surf": None, "swells": [], "wind": None, "air": None, "cond": None}
    r = nearest(pick(raw.get("rating"), "data", "rating"), now_ts)
    if r and pick(r, "rating", "key"):
        out["rating"] = {"key": r["rating"]["key"], "value": pick(r, "rating", "value")}
    w = nearest(pick(raw.get("wave"), "data", "wave"), now_ts)
    if w:
        hu = str(pick(raw.get("wave"), "associated", "units", "waveHeight") or "FT").upper()
        k = 3.28084 if hu in ("M", "METERS", "METRES") else 1.0
        s = w.get("surf") or {}
        lo, hi = s.get("min"), s.get("max")
        if lo is None or hi is None:                                  # fall back to the unrounded values if that is all we get
            lo, hi = pick(s, "raw", "min"), pick(s, "raw", "max")
            lo, hi = (round(lo), round(hi)) if lo is not None and hi is not None else (None, None)
        if lo is not None and hi is not None:
            out["surf"] = {"min": round(lo * k) if k != 1.0 else lo, "max": round(hi * k) if k != 1.0 else hi,
                           "plus": bool(s.get("plus")), "rel": s.get("humanRelation") or ""}
        out["swells"] = [{"h": round(x.get("height") * k, 1) if k != 1.0 else x.get("height"), "p": x.get("period"), "dir": x.get("direction")}
                         for x in (w.get("swells") or []) if x and x.get("height")][:3]
    n = nearest(pick(raw.get("wind"), "data", "wind"), now_ts)
    if n and n.get("speed") is not None:
        u = str(pick(raw.get("wind"), "associated", "units", "windSpeed") or "").upper()
        # The feed states its unit (we ask for knots and it answers KTS). Convert only if it says otherwise.
        f = {"KTS": 1.0, "KT": 1.0, "KPH": 1 / 1.852, "KMH": 1 / 1.852, "MPH": 0.868976, "MS": 1.94384}.get(u, 1.0)
        out["wind"] = {"speed": round(n["speed"] * f, 1), "dir": n.get("direction"), "type": n.get("directionType") or "",
                       "gust": round(n["gust"] * f, 1) if n.get("gust") is not None else None, "unit": "kts",
                       "src_unit": u or "not stated"}
    c = nearest(pick(raw.get("weather"), "data", "weather"), now_ts)
    if c:
        out["air"] = c.get("temperature")
        out["cond"] = (c.get("condition") or "").replace("_", " ").capitalize()
    return out


def parse_tide(hilo, six, now):
    """NOAA tide predictions -> {'height','trend','next':{'type','time','height'}} (times are Hawaii standard time)."""
    def t(s):
        return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=HST)
    res = {"height": None, "trend": None, "next": None}
    pts = [(t(p["t"]), float(p["v"])) for p in (six or {}).get("predictions", []) if p.get("v") not in (None, "")]
    if pts:
        cur = min(pts, key=lambda x: abs((x[0] - now).total_seconds()))
        later = [p for p in pts if p[0] > cur[0]]
        res["height"] = round(cur[1], 1)
        if later:
            res["trend"] = "rising" if later[min(4, len(later) - 1)][1] > cur[1] else "falling"
    nxt = [(t(p["t"]), p["type"], float(p["v"])) for p in (hilo or {}).get("predictions", []) if t(p["t"]) > now]
    if nxt:
        a = min(nxt, key=lambda x: x[0])
        res["next"] = {"type": a[1], "time": a[0].isoformat(), "height": round(a[2], 1)}
    return res


def parse_water(j):
    try:
        return round(float(j["data"][-1]["v"]))
    except Exception:
        return None


# ------------------------------------------------------------------ data
def fix_old_wind(spots):
    """Kept for old surf.json files: nothing to convert (the feed's wind numbers are knots)."""
    return spots


# ------------------------------------------------------------------ buoy swells (NOAA NDBC), used when Surfline gives none
BUOYS = [  # id, name, lat, lon
    ("51208", "Hanalei buoy", 22.285, -159.574),
    ("51201", "Waimea Bay buoy", 21.671, -158.118),
    ("51212", "Barbers Point buoy", 21.323, -158.149),
]
COMPASS16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
M_TO_FT = 3.28084


def compass_deg(c):
    c = (c or "").strip().upper()
    if c in COMPASS16:
        return COMPASS16.index(c) * 22.5
    try:
        return float(c)
    except ValueError:
        return None


def parse_spec(text, now_ts, max_age_h=6):
    """Newest usable row of an NDBC .spec file -> {'time': epoch, 'swells': [...]} or None. Heights m -> ft.
    Direction is where the swell comes FROM (same as Surfline); the card's arrow points the way it travels."""
    for line in (text or "").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        c = line.split()
        if len(c) < 11:
            continue
        try:
            t = datetime(int(c[0]), int(c[1]), int(c[2]), int(c[3]), int(c[4]), tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        if now_ts - t > max_age_h * 3600:
            return None                       # newest row is already too old: buoy is not reporting
        def num(x):
            try:
                return float(x)
            except ValueError:
                return None
        out = []
        # columns: YY MM DD hh mm WVHT SwH SwP WWH WWP SwD WWD ...
        swell = (num(c[6]), num(c[7]), compass_deg(c[10]))
        wind = (num(c[8]), num(c[9]), compass_deg(c[11]) if len(c) > 11 else None)
        for kind, (h, per, dr) in (("swell", swell), ("wind", wind)):
            if h is not None and per is not None and h >= 0.2 and (kind == "swell" or h * M_TO_FT >= 1.0):
                out.append({"h": round(h * M_TO_FT, 1), "p": round(per), "dir": dr, "kind": kind})
        out.sort(key=lambda x: -x["h"])
        return {"time": int(t), "swells": out[:2]}
    return None


def spot_latlon(maplink):
    import re
    m = re.search(r"!2d(-?[\d.]+)!3d(-?[\d.]+)", maplink or "")
    return (float(m.group(2)), float(m.group(1))) if m else None


def km(a, b):
    import math
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    x = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(x))


def fetch_buoys(now_ts, notes):
    import requests
    res = {}
    for bid, name, lat, lon in BUOYS:
        try:
            r = requests.get(f"https://www.ndbc.noaa.gov/data/realtime2/{bid}.spec", headers={"User-Agent": UA}, timeout=30)
            r.raise_for_status()
            d = parse_spec(r.text, now_ts)
        except Exception as e:
            notes.append(f"{name} {bid}: request failed ({e})")
            continue
        if d and d["swells"]:
            res[bid] = d
        else:
            notes.append(f"{name} {bid}: no recent data (offline or older than 6 h)")
        time.sleep(0.5)
    return res


def apply_buoys(spots, buoys, maplinks, now_ts):
    for sp in spots:
        if sp.get("swells") or not buoys:
            continue
        ll = spot_latlon(maplinks.get(sp["id"]))
        cands = [b for b in BUOYS if b[0] in buoys]
        if ll:
            cands.sort(key=lambda b: km(ll, (b[2], b[3])))
        b = cands[0]
        d = buoys[b[0]]
        sp["swells"] = d["swells"]
        sp["swell_src"] = {"name": b[1], "id": b[0], "time": d["time"]}


def load_spots(path):
    spots = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            sid = (r.get("Spot ID") or "").strip()
            if not sid:
                continue
            spots.append({"id": sid, "name": r["Spot Name"].strip(), "region": r["Region"].strip(),
                          "page": (r.get("applink") or "").strip(), "surfline": (r.get("link") or "").strip(),
                          "maplink": (r.get("maplink") or ""), "photo": ("https://kauaiconnect.com/wp-content/uploads/" + r["file1"].strip()) if r.get("file1") else ""})
    return spots


def fetch_all(spots, debug=False):
    now = datetime.now(HST)
    now_ts = int(time.time())
    raw_all, notes = {}, []
    WAVE_TRIES = [(1, 1, UNITS), (1, 3, UNITS), (5, 3, {})]     # (days, interval hours, unit params)
    wave_cfg, wave_dead, first_wave, first_report = None, False, None, None
    for i, sp in enumerate(spots, 1):
        raw = {}
        for kind in ("rating", "wind", "weather"):
            try:
                raw[kind] = get_json(SL + kind, dict({"spotId": sp["id"], "days": 1, "intervalHours": 1}, **UNITS))
            except Exception as e:
                notes.append(f"{sp['name']}: {kind} failed ({e})")
            time.sleep(1.0)
        # wave endpoint (swell lines, and surf height). The first spot tries three variants; if none works the endpoint is
        # skipped for the other spots (it returned 404 for every spot on 1 Nov 2026).
        if not wave_dead:
            for cfg in ((WAVE_TRIES if wave_cfg is None else [wave_cfg])):
                days, iv, un = cfg
                try:
                    w = get_json(SL + "wave", dict({"spotId": sp["id"], "days": days, "intervalHours": iv}, **un))
                except Exception as e:
                    notes.append(f"{sp['name']}: wave request (days={days}, every {iv}h{'' if un else ', no units'}) failed ({e})")
                    w = None
                time.sleep(1.0)
                if w is not None:
                    raw["wave"] = w
                    if i == 1:
                        first_wave = w
                    pw = parse_spot(sp, {"wave": w}, now_ts)
                    if pw["surf"] or pw["swells"]:
                        wave_cfg = cfg
                        break
            if i == 1 and wave_cfg is None:
                wave_dead = True
                notes.append("The wave endpoint is not available, so it is skipped for the other spots. Swell lines will come from the spot report if it has them.")
        parsed = parse_spot(sp, raw, now_ts)
        if not parsed["surf"] or not parsed["swells"]:   # fallback: the spot report (current surf height, description, swells)
            try:
                rep = get_json(SL.replace("/forecasts/", "/reports"), {"spotId": sp["id"], **UNITS})
                raw["report"] = rep
                if i == 1:
                    first_report = rep
                parsed["surf"] = parsed["surf"] or parse_report(rep)
                parsed["swells"] = parsed["swells"] or parse_report_swells(rep)
            except Exception as e:
                notes.append(f"{sp['name']}: report request failed ({e})")
            time.sleep(1.0)
        raw_all[sp["id"]] = raw
        sp.update(parsed)
        wsrc = (parsed["wind"] or {}).get("src_unit")
        print(f"  {i}/{len(spots)} {sp['name']}: {sp['rating']['key'] if sp['rating'] else 'no rating'} | surf "
              f"{str(sp['surf']['min']) + '-' + str(sp['surf']['max']) if sp['surf'] else '-'}" + (f" | wind unit from Surfline: {wsrc}" if i == 1 else ""))
    if not any(s["surf"] for s in spots) or not any(s["swells"] for s in spots):
        snap = {"note": "first spot's wave and report responses, trimmed", "spot": spots[0]["name"],
                "wave": first_wave, "report": first_report}
        try:
            snap["wave"] = {**first_wave, "data": {**first_wave["data"], "wave": first_wave["data"]["wave"][:3]}}
        except Exception:
            pass
        dbg = os.path.join(BASE, "surf_wave_debug.json")
        with open(dbg, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
        notes.append(("Surf height" if not any(s["surf"] for s in spots) else "Swell lines") +
                     f" not found for any spot. The first spot's responses are saved in {dbg} - send that file to Claude.")
    if not all(s["swells"] for s in spots):
        buoys = fetch_buoys(now_ts, notes)
        apply_buoys(spots, buoys, {s["id"]: s.get("maplink") for s in spots}, now_ts)
        n = sum(1 for s in spots if s.get("swell_src"))
        notes.append(f"Swells for {n} spot(s) come from NOAA buoys ({', '.join(sorted(buoys)) or 'none reporting'}).")
    tide = {"height": None, "trend": None, "next": None}
    water = None
    day = now.strftime("%Y%m%d")
    common = {"station": STATION, "application": "KauaiConnect", "time_zone": "lst_ldt", "units": "english", "format": "json"}
    try:
        hilo = get_json(NOAA, dict(common, product="predictions", datum="MLLW", interval="hilo",
                                   begin_date=day, end_date=(now + timedelta(days=1)).strftime("%Y%m%d")))
        six = get_json(NOAA, dict(common, product="predictions", datum="MLLW", begin_date=day, end_date=day))
        tide = parse_tide(hilo, six, now)
    except Exception as e:
        notes.append(f"NOAA tide failed ({e})")
    try:
        water = parse_water(get_json(NOAA, dict(common, product="water_temperature", date="latest")))
    except Exception as e:
        notes.append(f"NOAA water temperature failed ({e})")
    for sp in spots:
        sp["tide"], sp["water"] = tide, water
    if debug:
        with open(os.path.join(BASE, "surf_raw.json"), "w", encoding="utf-8") as f:
            json.dump(raw_all, f)
    return spots, notes


PREVIEW_SURF = {}
PREVIEW_SWELLS = {}


def sample_data(spots, json_path):
    """Preview built from the last real run (surf.json) plus the surf heights above. Falls back to made-up numbers."""
    if os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as f:
            real = {s["id"]: s for s in fix_old_wind(json.load(f))}
        out = []
        for sp in spots:
            r = real.get(sp["id"])
            if r and sp["name"] in PREVIEW_SURF:
                r = dict(r)
                r["surf"] = PREVIEW_SURF[sp["name"]]
                r["swells"] = PREVIEW_SWELLS.get(sp["name"], [])
                out.append(r)
        if out:
            return out, "Preview. Ratings, wind, tide and temperatures are from this morning's live run. Surf heights are copied from Surfline's own pages. Your live page will fill in heights itself once that works."
    import random
    random.seed(4)
    keys = ["POOR_TO_FAIR", "FAIR", "FAIR_TO_GOOD", "POOR", "GOOD", "VERY_POOR", "FAIR", "POOR_TO_FAIR", "FAIR_TO_GOOD", "EPIC", "POOR", "FAIR"]
    now = datetime.now(HST)
    for i, sp in enumerate(spots):
        sp["rating"] = {"key": keys[i % len(keys)], "value": i % 7}
        sp["surf"] = {"min": 2, "max": 3, "plus": False, "rel": ""}
        sp["swells"] = []
        sp["wind"] = {"speed": 8, "dir": 60, "type": "Offshore", "gust": 12, "unit": "kts"}
        sp["air"], sp["cond"] = 74, "Partly cloudy"
        sp["tide"] = {"height": 1.7, "trend": "rising", "next": {"type": "H", "time": now.replace(hour=9, minute=32).isoformat(), "height": 2.0}}
        sp["water"], sp["photo"] = 80, ""
    return spots, "Preview with MADE-UP numbers. Your live page will show real conditions."


def render(spots, out, sample=False, note=""):
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    present = [r for r in REGIONS if any(s["region"] == r for s in spots)]
    extra = sorted({s["region"] for s in spots} - set(REGIONS))
    data = {"generated": datetime.now(HST).isoformat(), "sample": sample, "sampleNote": note, "regions": present + extra, "spots": spots}
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html.replace("__DATA__", blob, 1))
    print(f"Wrote {out}  ({os.path.getsize(out) / 1e3:.0f} KB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spots", default=os.path.join(BASE, "surf_spot.csv"))
    ap.add_argument("--out", default=os.path.join(BASE, "kauai_surf_cards.html"))
    ap.add_argument("--json", default=os.path.join(BASE, "surf.json"))
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--no-photos", action="store_true", help="leave out the spot photos (thin header instead)")
    a = ap.parse_args()

    spots = load_spots(a.spots)
    if a.no_photos:
        for s in spots:
            s["photo"] = ""
    print(f"{len(spots)} spots from {os.path.basename(a.spots)}")
    if a.sample:
        sp, note = sample_data(spots, a.json)
        render(sp, a.out, sample=True, note=note)
        return
    if a.offline:
        with open(a.json, encoding="utf-8") as f:
            spots = fix_old_wind(json.load(f))
        render(spots, a.out)
        return
    spots, notes = fetch_all(spots, a.debug)
    with open(a.json, "w", encoding="utf-8") as f:
        json.dump(spots, f, ensure_ascii=False)
    ok = sum(1 for s in spots if s["rating"] or s["surf"])
    print(f"Got data for {ok} of {len(spots)} spots.")
    for n in notes:
        print("  note:", n)
    with open(os.path.join(BASE, "surf_notes.txt"), "w", encoding="utf-8") as f:
        f.write(datetime.now(HST).isoformat() + "\n" + "\n".join(notes) if notes else datetime.now(HST).isoformat() + "\nno problems")
    if not ok:
        print("Nothing could be read. Run again with --debug and send me surf_raw.json (or the messages above).")
    render(spots, a.out)


if __name__ == "__main__":
    main()
