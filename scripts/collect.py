"""Coleta eventos de páginas que publicam dados estruturados (schema.org/Event em JSON-LD).
Respeita robots.txt. Só usa Python padrão. Edite sources.json para adicionar fontes."""
import json, os, re, hashlib, sys, urllib.request, urllib.robotparser, urllib.parse
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta

UA = "AgendaSPBot/1.0 (+contato: seu-email@exemplo.com)"  # troque pelo seu contato
KEYS = {"Samba": ["samba", "pagode"], "Forró": ["forró", "forro"], "Stand-up": ["stand-up", "stand up"], "Infantil": ["infantil", "crianças"], "Teatro": ["teatro", "peça", "musical"],
        "Dança": ["dança", "balé", "ballet"], "Show": ["show", "turnê"], "Música": ["concerto", "orquestra", "jazz", "dj"]}

def allowed(url):
    p = urlparse(url); rp = urllib.robotparser.RobotFileParser(f"{p.scheme}://{p.netloc}/robots.txt")
    try: rp.read(); return rp.can_fetch(UA, url)
    except Exception: return False

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")

def walk(o):
    if isinstance(o, list):
        for i in o: yield from walk(i)
    elif isinstance(o, dict):
        t = o.get("@type"); t = t if isinstance(t, list) else [t]
        if any(isinstance(x, str) and x.endswith("Event") for x in t): yield o
        for v in o.values(): yield from walk(v)

def guess(title, default):
    s = title.lower()
    for cat, words in KEYS.items():
        if any(w in s for w in words): return cat
    return default

def parse(src, html):
    for blob in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try: data = json.loads(blob)
        except Exception: continue
        for ev in walk(data):
            title, start = ev.get("name"), ev.get("startDate")
            if not title or not start: continue
            offers = ev.get("offers") or {}; offers = offers[0] if isinstance(offers, list) and offers else offers
            price = offers.get("lowPrice") or offers.get("price") if isinstance(offers, dict) else None
            loc = ev.get("location") or {}; venue = (loc.get("name") if isinstance(loc, dict) else None) or src.get("venue")
            url = ev.get("url") or src["url"]
            img = ev.get("image"); img = img[0] if isinstance(img, list) and img else img
            img = img.get("url") if isinstance(img, dict) else img
            yield {"id": hashlib.md5(f"{title}{start}{venue}".encode()).hexdigest()[:12], "title": title.strip(),
                   "category": guess(title, src.get("category", "Outros")), "venue": venue, "start": start,
                   "price": f"R$ {price}" if price not in (None, "", 0, "0") else None, "url": url, "end": ev.get("endDate"), "image": img, "source": src["name"]}

def tm_cat(ev):
    title = ev.get("name", "")
    c = (ev.get("classifications") or [{}])[0]
    seg = (c.get("segment") or {}).get("name", ""); gen = (c.get("genre") or {}).get("name", "")
    base = {"Arts & Theatre": "Teatro", "Music": "Show", "Family": "Infantil"}.get(seg, "Outros")
    if "Comedy" in gen: base = "Stand-up"
    elif "Dance" in gen or "Ballet" in gen: base = "Dança"
    elif "Classical" in gen: base = "Música"
    return guess(title, base)

def tm(src):
    key = os.environ.get("TM_API_KEY")
    if not key: print("TM_API_KEY ausente; pulando Ticketmaster."); return
    for page in range(4):
        q = urllib.parse.urlencode({"apikey": key, "countryCode": "BR", "city": src.get("city", "São Paulo"),
                                    "size": 200, "page": page, "sort": "date,asc", "locale": "*"})
        data = json.loads(fetch("https://app.ticketmaster.com/discovery/v2/events.json?" + q))
        evs = (data.get("_embedded") or {}).get("events") or []
        for ev in evs:
            st = ev.get("dates", {}).get("start", {})
            start = st.get("dateTime") or (st.get("localDate") and st["localDate"] + "T" + (st.get("localTime") or "20:00:00") + "-03:00")
            if not start: continue
            venue = ((ev.get("_embedded") or {}).get("venues") or [{}])[0].get("name")
            pr = (ev.get("priceRanges") or [{}])[0]
            price = (f"R$ {pr['min']:.0f}" + (f" a R$ {pr['max']:.0f}" if pr.get("max") and pr["max"] != pr["min"] else "")) if pr.get("min") else None
            imgs = sorted(ev.get("images") or [], key=lambda i: -(i.get("width") or 0))
            img = next((i["url"] for i in imgs if i.get("ratio") == "4_3"), imgs[0]["url"] if imgs else None)
            yield {"id": ev["id"], "title": ev["name"], "category": tm_cat(ev), "venue": venue, "start": start,
                   "price": price, "url": ev.get("url"), "image": img, "source": src["name"]}
        if page + 1 >= (data.get("page") or {}).get("totalPages", 1): break

def inspect(url):
    print("==", url)
    try:
        print("  robots.txt permite:", allowed(url))
        html = fetch(url)
    except Exception as ex:
        print("  Falha ao abrir:", ex); return
    blobs = re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I)
    evs = list(parse({"name": "teste", "url": url}, html))
    print("  tamanho:", len(html), "| JSON-LD:", len(blobs), "| eventos reconhecidos:", len(evs), "| __NEXT_DATA__:", "__NEXT_DATA__" in html)
    for e in evs[:5]: print("   -", e["start"], "|", e["title"], "|", e["venue"])

def inspect_all():
    for s in json.load(open("sources.json"))["sources"]:
        if str(s.get("url", "")).startswith("http"): inspect(s["url"])

def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--inspect": return inspect_all() if sys.argv[2] == "todas" else inspect(sys.argv[2])
    srcs = [s for s in json.load(open("sources.json"))["sources"] if s.get("enabled")]
    found = {}
    for s in srcs:
        try:
            if s.get("type") == "ticketmaster": items = tm(s)
            elif not allowed(s["url"]): print("robots.txt bloqueia:", s["url"]); continue
            else: items = parse(s, fetch(s["url"]))
            for e in items or []: found[e["id"]] = e
        except Exception as ex: print("Falha em", s["name"], ex, file=sys.stderr)
    if not srcs: sys.exit("Nenhuma fonte ativa em sources.json.")
    if not found: sys.exit("Nenhum evento coletado; mantendo events.json anterior.")
    cut = datetime.now(timezone.utc) - timedelta(hours=3)
    def ok(e):
        try: d = datetime.fromisoformat(e["start"].replace("Z", "+00:00")); return (d if d.tzinfo else d.replace(tzinfo=timezone(timedelta(hours=-3)))) >= cut
        except Exception: return False
    out = sorted((e for e in found.values() if ok(e)), key=lambda e: e["start"])
    json.dump({"updated": datetime.now(timezone.utc).isoformat(), "events": out}, open("events.json", "w"), ensure_ascii=False, indent=1)
    print(len(out), "eventos salvos")

if __name__ == "__main__": main()
