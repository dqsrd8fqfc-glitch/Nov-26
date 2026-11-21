"""Fetch public news feeds and write them to a JSON file for the dashboard.

Runs in GitHub Actions on a schedule. Standard library only.
Usage: python scripts/fetch_feeds.py data/feeds.json
"""
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape

FEEDS = [
    {"id": "top", "title": "Top", "sources": [
        ("BBC", "https://feeds.bbci.co.uk/news/rss.xml"),
    ]},
    {"id": "msft", "title": "Microsoft", "sources": [
        ("Google News", "https://news.google.com/rss/search?q=Microsoft+when:2d&hl=en-GB&gl=GB&ceid=GB:en"),
    ]},
    {"id": "markets", "title": "Business", "sources": [
        ("BBC", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ]},
    {"id": "tech", "title": "Tech", "sources": [
        ("BBC", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ]},
    {"id": "apple", "title": "iPhone", "sources": [
        ("9to5Mac", "https://9to5mac.com/feed/"),
    ]},
]

MAX_ITEMS = 20
ATOM = "{http://www.w3.org/2005/Atom}"
UA = "Mozilla/5.0 (dashboard feed fetcher; +https://github.com)"


def clean(text):
    text = unescape(re.sub(r"<[^>]+>", "", text or "")).strip()
    return re.sub(r"\s+", " ", text)


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse(xml_bytes, source):
    root = ET.fromstring(xml_bytes)
    items = []
    # RSS 2.0
    for it in root.iter("item"):
        title = clean(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        src = source
        src_el = it.find("source")  # Google News gives the real publisher here
        if src_el is not None and src_el.text:
            src = clean(src_el.text)
            title = re.sub(r"\s+-\s+" + re.escape(src) + r"$", "", title)
        items.append({"title": title, "link": link, "source": src,
                      "date": parse_date(it.findtext("pubDate"))})
    # Atom
    for en in root.iter(ATOM + "entry"):
        link = ""
        for l in en.findall(ATOM + "link"):
            if l.get("rel", "alternate") == "alternate":
                link = l.get("href", "")
                break
        items.append({"title": clean(en.findtext(ATOM + "title")), "link": link, "source": source,
                      "date": parse_date(en.findtext(ATOM + "published") or en.findtext(ATOM + "updated"))})
    return [i for i in items if i["title"] and i["link"].startswith(("https://", "http://"))]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


QUOTES = [
    ("MSFT", "Microsoft", "MSFT"),
    ("SPX", "S&P 500", "^GSPC"),
    ("NDX", "Nasdaq 100", "^NDX"),
    ("FTSE", "FTSE 100", "^FTSE"),
    ("GBPUSD", "GBP/USD", "GBPUSD=X"),
]


def fetch_quote(symbol):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.request.quote(symbol) + "?range=1d&interval=1d")
    meta = json.loads(fetch(url))["chart"]["result"][0]["meta"]
    price = meta["regularMarketPrice"]
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    change = price - prev if prev else None
    t = meta.get("regularMarketTime")
    return {
        "price": round(price, 4),
        "change": round(change, 4) if change is not None else None,
        "pct": round(change / prev * 100, 3) if change is not None and prev else None,
        "currency": meta.get("currency"),
        "time": datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z") if t else None,
    }


def main(out_path, previous=None):
    out = {"updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "feeds": [], "quotes": {}}
    prev_quotes = (previous or {}).get("quotes", {})
    for key, name, symbol in QUOTES:
        try:
            out["quotes"][key] = {"name": name, **fetch_quote(symbol)}
        except Exception as e:
            print(f"warn: quote {symbol}: {e}", file=sys.stderr)
            if key in prev_quotes:
                out["quotes"][key] = prev_quotes[key]
    prev = {f["id"]: f for f in (previous or {}).get("feeds", [])}
    for feed in FEEDS:
        items, ok = [], False
        for name, url in feed["sources"]:
            try:
                items += parse(fetch(url), name)
                ok = True
            except Exception as e:  # keep going if one feed is down
                print(f"warn: {feed['id']} {name}: {e}", file=sys.stderr)
        if not ok and feed["id"] in prev:
            out["feeds"].append(prev[feed["id"]])  # keep last good copy
            continue
        seen, uniq = set(), []
        for i in sorted(items, key=lambda x: x["date"] or "", reverse=True):
            if i["link"] in seen:
                continue
            seen.add(i["link"])
            uniq.append(i)
        out["feeds"].append({"id": feed["id"], "title": feed["title"], "items": uniq[:MAX_ITEMS]})
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    path = sys.argv[1]
    try:
        with open(path, encoding="utf-8") as f:
            previous = json.load(f)
    except (OSError, ValueError):
        previous = None
    main(path, previous)
