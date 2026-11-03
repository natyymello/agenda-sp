"""Coleta eventos de páginas que publicam dados estruturados (schema.org/Event em JSON-LD).
Respeita robots.txt. Só usa Python padrão. Edite sources.json para adicionar fontes."""
import json, os, re, hashlib, sys, socket, urllib.error, urllib.request, urllib.robotparser, urllib.parse
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta

socket.setdefaulttimeout(20)  # evita travar em sites lentos
UA = "AgendaSPBot/1.0 (+contato: seu-email@exemplo.com)"  # troque pelo seu contato
KEYS = {"Samba": ["samba", "pagode"], "Forró": ["forró", "forro"], "Stand-up": ["stand-up", "stand up"], "Infantil": ["infantil", "crianças"], "Teatro": ["teatro", "peça", "musical"],
        "Dança": ["dança", "balé", "ballet"], "Show": ["show", "turnê"], "Música": ["concerto", "orquestra", "jazz", "dj"]}

def allowed(url):
    p = urlparse(url); rp = urllib.robotparser.RobotFileParser()
    try:
        req = urllib.request.Request(f"{p.scheme}://{p.netloc}/robots.txt", headers={"User-Agent": UA})
        rp.parse(urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore").splitlines())
    except urllib.error.HTTPError as e:
        return e.code == 404  # sem robots.txt = sem restrição; outros erros = não arrisca
    except Exception:
        return False
    return rp.can_fetch(UA, url)

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

SP = timezone(timedelta(hours=-3))
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

def soup(html):
    try: from bs4 import BeautifulSoup
    except ImportError:
        import subprocess; subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "beautifulsoup4"])
        from bs4 import BeautifulSoup
    s = BeautifulSoup(html, "html.parser")
    for x in s(["script", "style", "noscript", "svg"]): x.decompose()
    return s

def when(day, mon, hh=None, mm=0):
    """Os sites não mostram o ano: assume a próxima ocorrência da data."""
    now = datetime.now(SP)
    d = datetime(now.year, mon, day, 12 if hh is None else hh, mm, tzinfo=SP)
    if d < now - timedelta(days=2): d = d.replace(year=now.year + 1)
    return d.isoformat()

def ev(src, title, start, url, image=None, sub="", notime=False, end=None, price=None):
    e = {"id": hashlib.md5(f"{title}{start}{src['name']}".encode()).hexdigest()[:12], "title": title,
         "category": guess(f"{title} {sub}", src.get("category", "Outros")), "venue": src.get("venue"),
         "start": start, "price": None, "url": url, "image": image, "source": src["name"]}
    if notime: e["notime"] = True
    if end: e["end"] = end
    if price: e["price"] = price
    return e

def p_unimed(src, html):
    for n in range(1, 5):
        if n > 1:
            try: html = fetch(src["url"].rstrip("/") + f"/page/{n}/")
            except Exception: break
        lis = soup(html).select("ul.shows > li")
        if not lis: break
        for li in lis:
            try:
                h, d, m = li.select_one("h3"), li.select_one(".day-of-month"), li.select_one(".month")
                mon = MESES.index(m.get_text(strip=True)[:3].lower()) + 1
                t = re.search(r"(\d{1,2}):(\d{2})", " ".join(p.get_text() for p in li.select("article p")))
                a = li.select_one("a.tickets") or h.find("a"); im = li.find("img"); sub = li.select_one(".subtitle")
                yield ev(src, h.get_text(strip=True), when(int(d.get_text()), mon, int(t[1]) if t else None, int(t[2]) if t else 0),
                         a["href"] if a else src["url"], im.get("src") if im else None, sub.get_text(strip=True) if sub else "", notime=not t)
            except Exception: continue

def p_tokio(src, html):
    for li in soup(html).select("li.filter-shows"):
        try:
            parts = [x for x in li.get_text("|", strip=True).split("|") if x and x != "Comprar"]
            rx = re.compile(r"(\d{1,2}) de (\w+)(?:,? às (\d{1,2})h(\d{2})?)?")
            dt = next(m for m in (rx.search(x) for x in parts) if m)
            title = parts[0].title() if parts[0].isupper() else parts[0]
            sub = parts[1] if len(parts) > 1 and not rx.search(parts[1]) else ""
            links = [a["href"] for a in li.find_all("a", href=True)]
            url = next((h for h in links if "tokiomarinehall" not in h), links[0]); im = li.find("img")
            yield ev(src, title, when(int(dt[1]), MESES.index(dt[2][:3].lower()) + 1, int(dt[3]) if dt[3] else None, int(dt[4] or 0)),
                     url, im.get("data-lazysrc") if im else None, sub, notime=not dt[3])
        except Exception: continue

def p_natura(src, html):
    for c in soup(html).select("div.e-loop-item.eventos"):
        try:
            h = c.select_one("h2 a"); txt = c.get_text("|", strip=True).split("|")
            m = next(x for x in (re.fullmatch(r"(\d{1,2})\.(\d{1,2})", t) for t in txt) if x)
            btn = c.select_one("a.elementor-button"); im = c.find("img")
            sub = next((t for t in txt if t not in (h.get_text(strip=True), m[0]) and "ingresso" not in t.lower()), "")
            yield ev(src, h.get_text(strip=True), when(int(m[1]), int(m[2])), btn["href"] if btn else h["href"],
                     im.get("src") if im else None, sub, notime=True)
        except Exception: continue

def p_theatro(src, html):
    cats = {"ópera": "Música", "concertos": "Música", "música": "Música", "dança": "Dança", "teatro": "Teatro"}
    for c in soup(html).select("article.card-evento"):
        try:
            t = [x for x in c.get_text("|", strip=True).split("|") if x]
            ds = [x for x in t if re.fullmatch(r"\d{2}/\d{2}/\d{4}", x)]
            if not ds or any("cancelado" in x.lower() for x in t): continue
            iso = lambda x: datetime(int(x[6:]), int(x[3:5]), int(x[:2]), 12, tzinfo=SP).isoformat()
            e = ev(src, t[2], iso(ds[0]), c.find("a", href=True)["href"], notime=True,
                   end=iso(ds[-1]) if ds[-1] != ds[0] else None, price="Grátis" if "gratuito" in t[0].lower() else None)
            e["venue"] = t[-1]; e["category"] = cats.get(t[1].lower()) or guess(t[2], "Teatro")
            yield e
        except Exception: continue

def sheet(src):
    """Lê uma planilha do Google publicada como CSV (cadastro manual de eventos)."""
    import csv, io
    cats = {"show": "Show", "teatro": "Teatro", "samba": "Samba", "forró": "Forró", "forro": "Forró", "música": "Música", "musica": "Música",
            "dança": "Dança", "danca": "Dança", "stand-up": "Stand-up", "standup": "Stand-up", "infantil": "Infantil", "outros": "Outros"}
    def dt(txt, hh=None, mm=0):
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", txt) or None
        if m:
            d, mo, y = int(m[1]), int(m[2]), m[3]
        else:
            m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", txt)
            if not m: return None
            y, mo, d = m[1], int(m[2]), int(m[3])
        if y:
            y = int(y) + (2000 if int(y) < 100 else 0)
            return datetime(y, mo, d, 12 if hh is None else hh, mm, tzinfo=SP).isoformat()
        return when(d, mo, hh, mm)
    for r in csv.DictReader(io.StringIO(fetch(src["url"]))):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        try:
            title = r.get("titulo")
            h = re.fullmatch(r"(\d{1,2})[:h](\d{2})?", r.get("hora", ""))
            start = dt(r.get("data", ""), int(h[1]) if h else None, int(h[2] or 0) if h else 0)
            if not (title and start): continue
            e = ev(src, title, start, r.get("link") or "#", r.get("imagem") or None, notime=not h,
                   end=dt(r["data_fim"]) if r.get("data_fim") else None, price=r.get("preco") or None)
            e["venue"] = r.get("local") or None
            e["category"] = cats.get(r.get("categoria", "").lower()) or guess(title, "Outros")
            yield e
        except Exception: continue

PARSERS = {"theatro": p_theatro, "unimed": p_unimed, "tokio": p_tokio, "natura": p_natura}

def inspect(url):
    print("==", url)
    try:
        print("  robots.txt permite:", allowed(url))
        html = fetch(url)
    except Exception as ex:
        print("  Falha ao abrir:", ex); return
    blobs = re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I)
    src = next((x for x in json.load(open("sources.json"))["sources"] if x.get("url") == url), {"name": "teste", "url": url})
    evs = list(PARSERS[src["parser"]](src, html)) if src.get("parser") else list(parse(src, html))
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
            elif s.get("type") == "sheet": items = sheet(s)
            elif not allowed(s["url"]): print("robots.txt bloqueia:", s["url"]); continue
            else: items = PARSERS[s["parser"]](s, fetch(s["url"])) if s.get("parser") else parse(s, fetch(s["url"]))
            for e in items or []: found[e["id"]] = e
        except Exception as ex: print("Falha em", s["name"], ex, file=sys.stderr)
    if not srcs: sys.exit("Nenhuma fonte ativa em sources.json.")
    if not found: sys.exit("Nenhum evento coletado; mantendo events.json anterior.")
    cut = datetime.now(timezone.utc) - timedelta(hours=3)
    def ok(e):
        try: d = datetime.fromisoformat((e.get("end") or e["start"]).replace("Z", "+00:00")); return (d if d.tzinfo else d.replace(tzinfo=timezone(timedelta(hours=-3)))) >= cut
        except Exception: return False
    out = sorted((e for e in found.values() if ok(e)), key=lambda e: e["start"])
    json.dump({"updated": datetime.now(timezone.utc).isoformat(), "events": out}, open("events.json", "w"), ensure_ascii=False, indent=1)
    print(len(out), "eventos salvos")

if __name__ == "__main__": main()
