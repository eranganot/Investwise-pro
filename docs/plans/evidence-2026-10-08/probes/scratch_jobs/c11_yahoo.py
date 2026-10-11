"""C11: compare chartPreviousClose vs previous daily bar vs previousClose (live, read-only)."""
import datetime as dt
import json
import urllib.request

for sym in ["CL=F", "ES=F", "NQ=F", "GC=F", "^VIX"]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "InvestWise/1.0"})
    d = json.loads(urllib.request.urlopen(req, timeout=15).read())
    r = d["chart"]["result"][0]
    m = r["meta"]
    ts = [dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%m-%d") for t in r["timestamp"]]
    closes = r["indicators"]["quote"][0]["close"]
    p = m.get("regularMarketPrice")
    cpc = m.get("chartPreviousClose")
    pc = m.get("previousClose")
    prev_bar = [c for c in closes[:-1] if c is not None][-1]
    rmt = dt.datetime.fromtimestamp(m["regularMarketTime"], dt.timezone.utc).isoformat()
    print(sym, "price", p, "| chartPreviousClose", cpc, "| previousClose", pc,
          "| regularMarketPreviousClose", m.get("regularMarketPreviousClose"), "| time", rmt)
    print("   bars", list(zip(ts, [round(c, 2) if c else c for c in closes])))
    print("   app formula (chartPreviousClose) %+.2f%%   vs previous daily bar %+.2f%%"
          % ((p - cpc) / cpc * 100, (p - prev_bar) / prev_bar * 100))
