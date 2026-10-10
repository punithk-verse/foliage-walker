#!/usr/bin/env python3
"""foliage-walker: find the greenest / most colourful trail near you, pick the day, go outside.
Open pieces: OpenStreetMap (Nominatim + Overpass), Open-Meteo, a local open-weight LLM via Ollama.
Usage: python foliage_walker.py "Agara Lake, Bengaluru" --radius 4 --min-km 1.5 --model qwen2.5:1.5b
"""
import argparse, datetime, json, math, re, sys, time, urllib.parse, urllib.request

UA = {"User-Agent": "foliage-walker/0.3 (open-source hobby project)"}
MIRRORS = ["https://overpass-api.de/api/interpreter",
           "https://overpass.kumi.systems/api/interpreter",
           "https://overpass.private.coffee/api/interpreter"]


def http(url, data=None, timeout=90):
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def geocode(q):
    res = http("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": 1}))
    if not res:
        sys.exit(f"Couldn't find '{q}'")
    return float(res[0]["lat"]), float(res[0]["lon"]), res[0]["display_name"]


def hav(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 12742000 * math.asin(math.sqrt(h))


def inside(pt, poly):
    """Ray-casting point-in-polygon; pt and poly are (lat, lon)."""
    y, x, c = pt[0], pt[1], False
    for (y1, x1), (y2, x2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def fetch_osm(lat, lon, r):
    q = f"""[out:json][timeout:60];(
      way["highway"~"^(path|footway|track)$"]["name"](around:{r},{lat},{lon});
      way["natural"="wood"](around:{r},{lat},{lon});
      way["landuse"="forest"](around:{r},{lat},{lon});
      way["leisure"="park"](around:{r},{lat},{lon});
    );out geom tags;"""
    data = urllib.parse.urlencode({"data": q}).encode()
    for _ in range(2):
        for url in MIRRORS:
            try:
                return http(url, data, timeout=120)["elements"]
            except Exception as e:
                print(f"{url} failed ({e}); trying next...")
                time.sleep(3)
    sys.exit("All Overpass servers are busy. Wait a few minutes or use a smaller --radius.")


def rank_trails(elements, min_km):
    greens, trails = [], {}
    for e in elements:
        geom = [(p["lat"], p["lon"]) for p in e.get("geometry", [])]
        t = e["tags"]
        if "highway" in t:
            trails.setdefault(t["name"], []).append(geom)
        elif len(geom) >= 3:
            la, lo = [p[0] for p in geom], [p[1] for p in geom]
            greens.append((min(la), max(la), min(lo), max(lo), geom,
                           t.get("leaf_type") in ("broadleaved", "mixed")))
    out = []
    for name, segs in trails.items():
        length = sum(hav(s[i], s[i + 1]) for s in segs for i in range(len(s) - 1))
        pts = [p for s in segs for p in s]
        if length < min_km * 1000 or not pts:
            continue
        g = b = 0
        for p in pts:
            hit = [w[5] for w in greens
                   if w[0] <= p[0] <= w[1] and w[2] <= p[1] <= w[3] and inside(p, w[4])]
            g += bool(hit)
            b += any(hit)
        out.append({"name": name, "km": round(length / 1000, 1),
                    "green_pct": round(100 * g / len(pts)),
                    "broadleaf_pct": round(100 * b / len(pts)),
                    "start": pts[0], "segments": segs})
    return out


def elevation(lat, lon):
    return http(f"https://api.open-meteo.com/v1/elevation?latitude={lat}&longitude={lon}")["elevation"][0]


def colour_phase(lat, elev, today):
    """Rule of thumb for temperate northern-hemisphere forest. NOT a forecast."""
    if lat < 0:
        return "Not estimated for the southern hemisphere"
    if lat < 25:
        return "No temperate autumn colour expected this close to the tropics"
    peak = datetime.date(today.year, 1, 1) + datetime.timedelta(
        days=int(275 + (44 - lat) * 3 - elev / 100) - 1)
    d = (peak - today).days
    if abs(d) <= 5:
        return f"Near peak (rough estimate: peak around {peak:%b %d})"
    return (f"Colour building (rough estimate: peak around {peak:%b %d}, {d} days away)" if d > 0
            else f"Probably past peak (rough estimate: peak was around {peak:%b %d})")


def best_day(lat, lon):
    d = http("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 7,
        "daily": "temperature_2m_max,precipitation_probability_max,sunset"}))["daily"]
    days = [{"date": d["time"][i], "high_c": d["temperature_2m_max"][i],
             "rain_pct": d["precipitation_probability_max"][i],
             "sunset": d["sunset"][i][-5:]} for i in range(len(d["time"]))]
    return min(days, key=lambda x: (x["rain_pct"] or 0, abs((x["high_c"] or 20) - 20)))


def write_gpx(trail, path):
    segs = "".join("<trkseg>" + "".join(f'<trkpt lat="{la}" lon="{lo}"/>' for la, lo in s)
                   + "</trkseg>" for s in trail["segments"])
    open(path, "w").write('<?xml version="1.0"?><gpx version="1.1" creator="foliage-walker" '
                          f'xmlns="http://www.topografix.com/GPX/1/1"><trk><name>{trail["name"]}'
                          f"</name>{segs}</trk></gpx>")


def ask_llm(facts, model, host):
    prompt = ("Write exactly 3 friendly sentences inviting someone on this walk. Use only the facts "
              "below: mention the trail name, the day, and the leave-by time. Do not state any other "
              "numbers, and say nothing about terrain, flatness, wildlife or gear. Last sentence: "
              "tell them to put the phone away and enjoy it.\n" + json.dumps(facts))
    body = json.dumps({"model": model, "stream": False,
                       "options": {"num_ctx": 1024, "num_predict": 150, "temperature": 0.2},
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    text = http(f"{host}/api/chat", body, timeout=300)["message"]["content"].strip()
    return " ".join(re.split(r"(?<=[.!?])\s+", text)[:3])  # small models ignore length limits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("place")
    ap.add_argument("--radius", type=float, default=8, help="search radius, km")
    ap.add_argument("--min-km", type=float, default=2)
    ap.add_argument("--model", default="llama3.1:8b")
    ap.add_argument("--ollama", default="http://localhost:11434")
    a = ap.parse_args()

    lat, lon, label = geocode(a.place)
    trails = rank_trails(fetch_osm(lat, lon, int(a.radius * 1000)), a.min_km)
    if not trails:
        sys.exit("No named trails found. Try a bigger --radius.")
    foliage = lat >= 25 and any(t["broadleaf_pct"] > 0 for t in trails)
    key = "broadleaf_pct" if foliage else "green_pct"
    trails.sort(key=lambda t: (t[key], t["km"]), reverse=True)
    top = trails[0]
    day = best_day(*top["start"])
    leave = (datetime.datetime.strptime(day["sunset"], "%H:%M")
             - datetime.timedelta(hours=top["km"] / 4 + 0.5)).strftime("%H:%M")
    note = ("Ranked by broadleaf (deciduous) forest along the path." if foliage else
            "Ranked by overall green cover (woods and parks), because autumn colour isn't "
            "expected here or OSM lacks broadleaf tags for this area.")
    phase = colour_phase(lat, elevation(*top["start"]), datetime.date.today())
    facts = {"trail": top["name"], "day": day["date"], "leave_by": leave}

    write_gpx(top, "route.gpx")
    try:
        blurb = ask_llm(facts, a.model, a.ollama)
    except Exception as e:
        blurb = f"(Local model unavailable: {e})"
    plan = "\n".join([
        f"# {top['name']}", f"- Area: {label}", f"- Length: {top['km']} km",
        f"- Green cover: {top['green_pct']}% of the path is inside mapped woods/parks "
        f"({top['broadleaf_pct']}% tagged broadleaf)",
        f"- Best day: {day['date']} (high {day['high_c']} C, {day['rain_pct']}% rain chance)",
        f"- Leave by {leave} (sunset {day['sunset']}; assumes ~4 km/h plus 30 min buffer)",
        f"- Colour: {phase}", f"- Note: {note}", "", blurb])
    open("plan.md", "w").write(plan + "\n")
    print(plan, "\n\nSaved plan.md and route.gpx (open in Organic Maps/OsmAnd, works offline).")


if __name__ == "__main__":
    main()
