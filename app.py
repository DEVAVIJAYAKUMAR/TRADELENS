"""TradeLens Live - single file app. Run: uvicorn app:app --host 0.0.0.0 --port $PORT"""

# ===== config =====

import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _i(k, d): return int(os.getenv(k, d))
def _f(k, d): return float(os.getenv(k, d))


class Cfg:
    api_key = os.getenv("KITE_API_KEY", "")
    api_secret = os.getenv("KITE_API_SECRET", "")
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    demo = os.getenv("DEMO", "0") == "1"
    # Candle timeframe in minutes: 1, 3, 5 or 15 (aligned with Kite candles)
    timeframe_min = _i("TIMEFRAME_MIN", 5)
    # Trend Pullback rules (edit these to match YOUR rules)
    ema_fast = _i("EMA_FAST", 20)
    ema_slow = _i("EMA_SLOW", 50)
    lookback = _i("PULLBACK_LOOKBACK", 8)      # candles to look back for the pullback
    touch_tol = _f("TOUCH_TOL", 0.0008)         # 0.08% zone around fast EMA
    rr = _f("RISK_REWARD", 2.0)
    cooldown = _i("COOLDOWN_CANDLES", 6)
    pending_expiry = _i("PENDING_EXPIRY_CANDLES", 3)
    start_hm = (9, 30)                          # no new setups before this (IST)
    end_hm = (15, 10)                           # no new setups after this (IST)


cfg = Cfg()


# ===== strategy =====

"""Trend Pullback setup detector. Pure python, no dependencies.

SELL: fast EMA < slow EMA (and slow EMA falling), price bounced up to the fast EMA
zone within the last N candles, then a bearish candle closes back below the fast EMA.
BUY is the mirror image.
Entry = break of the trigger candle low (SELL) / high (BUY).
SL    = pullback swing high (SELL) / low (BUY) +/- 0.1 ATR.
Target = entry -/+ RR x risk.
"""
from dataclasses import dataclass, asdict


@dataclass
class Candle:
    t: int
    o: float
    h: float
    l: float
    c: float


@dataclass
class Signal:
    side: str
    setup: str
    entry: float
    sl: float
    target: float
    rr: float
    option: str
    explain: str
    invalid: str
    t: int

    def to_dict(self):
        return asdict(self)


def r05(x):
    return round(round(x / 0.05) * 0.05, 2)


def ema(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    out[period - 1] = e
    for i in range(period, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(len(candles) - period, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c.h - c.l, abs(c.h - p.c), abs(c.l - p.c)))
    return sum(trs) / period


def detect(candles, cfg):
    if len(candles) < cfg.ema_slow + cfg.lookback + 6:
        return None
    closes = [c.c for c in candles]
    f, s = ema(closes, cfg.ema_fast), ema(closes, cfg.ema_slow)
    a = atr(candles)
    if not a or f[-1] is None or s[-1] is None or s[-6] is None:
        return None
    last, ef, es = candles[-1], f[-1], s[-1]
    win, wf = candles[-(cfg.lookback + 1):-1], f[-(cfg.lookback + 1):-1]
    tol = ef * cfg.touch_tol

    if ef < es and s[-1] < s[-6] and last.c < ef and last.c < last.o:
        idx = [i for i, (c, e) in enumerate(zip(win, wf)) if e is not None and c.h >= e - tol]
        if idx:
            swing = max(c.h for c in win[idx[0]:] + [last])
            entry, sl = last.l, swing + 0.1 * a
            risk = sl - entry
            if 0.5 * a <= risk <= 3 * a:
                tgt = entry - cfg.rr * risk
                return Signal(
                    "SELL", "Trend Pullback", r05(entry), r05(sl), r05(tgt), cfg.rr, "Buy PE",
                    "Downtrend (EMA%d < EMA%d). Price pullback EMA%d zone-ku vandhu reject aachu, "
                    "bearish candle EMA keela close aachu." % (cfg.ema_fast, cfg.ema_slow, cfg.ema_fast),
                    "Price %.2f mela vandha setup fail." % r05(sl), last.t)

    if ef > es and s[-1] > s[-6] and last.c > ef and last.c > last.o:
        idx = [i for i, (c, e) in enumerate(zip(win, wf)) if e is not None and c.l <= e + tol]
        if idx:
            swing = min(c.l for c in win[idx[0]:] + [last])
            entry, sl = last.h, swing - 0.1 * a
            risk = entry - sl
            if 0.5 * a <= risk <= 3 * a:
                tgt = entry + cfg.rr * risk
                return Signal(
                    "BUY", "Trend Pullback", r05(entry), r05(sl), r05(tgt), cfg.rr, "Buy CE",
                    "Uptrend (EMA%d > EMA%d). Price pullback EMA%d zone-ku vandhu support eduthuchu, "
                    "bullish candle EMA mela close aachu." % (cfg.ema_fast, cfg.ema_slow, cfg.ema_fast),
                    "Price %.2f keela vandha setup fail." % r05(sl), last.t)
    return None


# ===== telegram =====

import json
import threading
import urllib.request



def _send(text):
    try:
        req = urllib.request.Request(
            "https://api.telegram.org/bot%s/sendMessage" % cfg.tg_token,
            data=json.dumps({"chat_id": cfg.tg_chat, "text": text}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print("telegram error:", e)


def send_telegram(text):
    if cfg.tg_token and cfg.tg_chat:
        threading.Thread(target=_send, args=(text,), daemon=True).start()


# ===== engine =====

import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")


class CandleBuilder:
    def __init__(self, minutes):
        self.sec = minutes * 60
        self.cur = None

    def on_tick(self, price, ts):
        b = ts - ts % self.sec
        if self.cur is None or self.cur.t != b:
            closed = self.cur
            self.cur = Candle(b, price, price, price, price)
            return closed
        c = self.cur
        c.h, c.l, c.c = max(c.h, price), min(c.l, price), price
        return None


class Engine:
    def __init__(self, cfg, emit):
        self.cfg, self.emit = cfg, emit
        self.b = CandleBuilder(cfg.timeframe_min)
        self.candles, self.alerts = [], []
        self.price, self.active, self.mode = None, None, "waiting"
        self.since_signal, self.pending_age = 999, 0
        self.lock, self._push_t = threading.Lock(), 0

    def seed(self, candles):
        with self.lock:
            self.candles = list(candles)[-400:]

    # ---- ticks -----------------------------------------------------------
    def on_tick(self, price, ts=None):
        ts = int(ts or time.time())
        events = []
        with self.lock:
            self.price = price
            closed = self.b.on_tick(price, ts)
            if closed:
                self.candles = (self.candles + [closed])[-400:]
                self._on_close(closed, events)
            self._check_active(price, events)
        for kind, text, data in events:
            self._fire(kind, text, data, ts)
        if events or time.time() - self._push_t > 1:
            self._push_t = time.time()
            self.emit({"type": "snapshot", **self.snapshot()})

    def _in_window(self, t):
        d = datetime.fromtimestamp(t, IST)
        hm = (d.hour, d.minute)
        return self.cfg.start_hm <= hm <= self.cfg.end_hm

    def _on_close(self, candle, events):
        self.since_signal += 1
        if self.active and self.active["status"] == "PENDING":
            self.pending_age += 1
            if self.pending_age > self.cfg.pending_expiry:
                events.append(("EXPIRED", "Setup expire aachu (entry trigger aagala). Skip.", dict(self.active)))
                self.active = None
        if self.active or self.since_signal < self.cfg.cooldown or not self._in_window(candle.t):
            return
        sig = detect(self.candles, self.cfg)
        if sig:
            self.since_signal, self.pending_age = 0, 0
            self.active = {**sig.to_dict(), "status": "PENDING"}
            verb = "below" if sig.side == "SELL" else "above"
            text = (
                "SETUP: %s %s (%dm NIFTY)\n%s\n\nEntry: %s %.2f\nSL: %.2f\nTarget: %.2f (RR 1:%.1f)\n"
                "Option idea: %s\nInvalid: %s\n\nIdhu oru idea mattum, final decision unnodhu."
                % (sig.setup, sig.side, self.cfg.timeframe_min, sig.explain, verb, sig.entry,
                   sig.sl, sig.target, sig.rr, sig.option, sig.invalid))
            events.append(("SIGNAL", text, dict(self.active)))

    def _check_active(self, p, events):
        a = self.active
        if not a:
            return
        sell = a["side"] == "SELL"
        if a["status"] == "PENDING":
            if (p <= a["entry"]) if sell else (p >= a["entry"]):
                a["status"] = "OPEN"
                events.append(("ENTRY", "ENTRY triggered: %s @ %.2f\nSL %.2f | Target %.2f"
                               % (a["side"], p, a["sl"], a["target"]), dict(a)))
            elif (p >= a["sl"]) if sell else (p <= a["sl"]):
                events.append(("EXPIRED", "SL level touch aachu, entry edukkala. Setup cancel.", dict(a)))
                self.active = None
        elif a["status"] == "OPEN":
            hit_t = (p <= a["target"]) if sell else (p >= a["target"])
            hit_s = (p >= a["sl"]) if sell else (p <= a["sl"])
            if hit_t or hit_s:
                pts = (a["entry"] - p) if sell else (p - a["entry"])
                kind = "TARGET" if hit_t else "STOPLOSS"
                label = "TARGET HIT - exit pannu" if hit_t else "STOP LOSS HIT - exit pannu"
                events.append((kind, "%s @ %.2f (%+.1f pts)" % (label, p, pts), dict(a)))
                self.active = None

    def _fire(self, kind, text, data, ts):
        item = {"kind": kind, "text": text, "ts": ts, "data": data}
        self.alerts = (self.alerts + [item])[-60:]
        send_telegram(text)
        self.emit({"type": "alert", "alert": item})

    # ---- view ------------------------------------------------------------
    def snapshot(self):
        with self.lock:
            cs = self.candles[-70:] + ([self.b.cur] if self.b.cur else [])
            allc = self.candles + ([self.b.cur] if self.b.cur else [])
            closes = [c.c for c in allc]
            f, s = ema(closes, self.cfg.ema_fast), ema(closes, self.cfg.ema_slow)
            n = len(cs)
            trend = "Sideways"
            if f and s and f[-1] is not None and s[-1] is not None:
                trend = "Uptrend" if f[-1] > s[-1] else "Downtrend"
            return {
                "mode": self.mode, "price": self.price, "trend": trend,
                "tf": self.cfg.timeframe_min,
                "candles": [[c.t, c.o, c.h, c.l, c.c] for c in cs],
                "ema_fast": f[-n:], "ema_slow": s[-n:],
                "active": self.active, "alerts": self.alerts[-30:],
            }


# ===== demo feed =====

"""Simulated NIFTY feed so you can test the whole app without Kite or market hours."""
import random
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo



def make_ticks(n=100000, start=25400.0, seed=7):
    rnd = random.Random(seed)
    p, i = start, 0
    while i < n:
        for drift, length in ((-0.4, 180), (0.95, 55)):  # trend leg, then pullback leg
            for _ in range(length):
                p += drift + rnd.uniform(-1.2, 1.2)
                yield p
                i += 1


def run_demo(engine, cfg, fast=True):
    engine.mode = "demo"
    base = datetime.now(ZoneInfo("Asia/Kolkata")).replace(hour=10, minute=0, second=0, microsecond=0)
    ts = int(base.timestamp())
    step = cfg.timeframe_min * 60 // 20  # 20 ticks per candle

    def loop():
        nonlocal ts
        for price in make_ticks():
            ts += step
            engine.on_tick(price, ts)
            time.sleep(0.02 if fast else 1)

    threading.Thread(target=loop, daemon=True).start()


# ===== kite feed =====

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
NIFTY_TOKEN = 256265  # NSE: NIFTY 50 index
INTERVALS = {1: "minute", 3: "3minute", 5: "5minute", 15: "15minute"}


def start_kite(cfg, engine, access_token):
    from kiteconnect import KiteConnect, KiteTicker

    kite = KiteConnect(api_key=cfg.api_key)
    kite.set_access_token(access_token)

    # Warm-up: recent candles so EMAs are ready immediately
    try:
        now = datetime.now(IST)
        rows = kite.historical_data(NIFTY_TOKEN, now - timedelta(days=6), now,
                                    INTERVALS[cfg.timeframe_min])
        sec = cfg.timeframe_min * 60
        cur = int(time.time()) // sec * sec
        cs = [Candle(int(r["date"].timestamp()), r["open"], r["high"], r["low"], r["close"])
              for r in rows]
        engine.seed([c for c in cs if c.t < cur])
        print("seeded %d candles" % len(engine.candles))
    except Exception as e:
        print("historical data error (paid Kite Connect plan needed):", e)

    ticker = KiteTicker(cfg.api_key, access_token)

    def on_connect(ws, resp):
        ws.subscribe([NIFTY_TOKEN])
        ws.set_mode(ws.MODE_LTP, [NIFTY_TOKEN])
        engine.mode = "live"

    def on_ticks(ws, ticks):
        for t in ticks:
            if t.get("last_price"):
                engine.on_tick(t["last_price"], time.time())

    def on_close(ws, code, reason):
        engine.mode = "disconnected"

    ticker.on_connect, ticker.on_ticks, ticker.on_close = on_connect, on_ticks, on_close
    ticker.on_error = lambda ws, code, reason: print("ticker error:", code, reason)
    ticker.connect(threaded=True)


# ===== embedded web assets =====


import struct
import zlib


def make_png(n):
    bg, amb, buy, sell = (11, 16, 22), (255, 180, 84), (45, 212, 167), (255, 90, 82)
    px = [[bg] * n for _ in range(n)]

    def rect(x0, y0, x1, y1, c):
        for y in range(int(y0 * n), int(y1 * n)):
            for x in range(int(x0 * n), int(x1 * n)):
                px[y][x] = c

    for cx, top, bot, wt, wb, c in ((.30, .42, .62, .34, .70, sell), (.50, .32, .58, .26, .66, buy),
                                    (.70, .24, .46, .18, .54, buy)):
        rect(cx - .012, wt, cx + .012, wb, c)
        rect(cx - .05, top, cx + .05, bot, c)
    rect(.2, .78, .8, .795, amb)
    raw = b"".join(b"\x00" + b"".join(bytes(p) for p in row) for row in px)

    def ch(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)

    return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0))
            + ch(b"IDAT", zlib.compress(raw, 9)) + ch(b"IEND", b""))


ICON192, ICON512 = make_png(192), make_png(512)


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>TradeLens Live</title>
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#0b1016">
<link rel="icon" href="/icon-192.png">
<link rel="apple-touch-icon" href="/icon-192.png">
<meta name="mobile-web-app-capable" content="yes">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root{--bg:#0b1016;--panel:#121a23;--line:#1f2b38;--tx:#e6edf3;--mut:#7f8d9b;--sell:#ff5a52;--buy:#2dd4a7;--amb:#ffb454;--ac:#6aa9ff;color-scheme:dark}
@media (prefers-color-scheme:light){:root:not([data-theme="dark"]){--bg:#eef2f6;--panel:#fff;--line:#d9e1e9;--tx:#0f1a24;--mut:#5d6b79;--sell:#d63a32;--buy:#0d9f77;--amb:#b86e00;--ac:#2563eb;color-scheme:light}}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--tx);font:15px/1.5 "IBM Plex Sans",system-ui,sans-serif;padding:14px;padding-top:calc(14px + env(safe-area-inset-top,0px));padding-bottom:calc(14px + env(safe-area-inset-bottom,0px));font-variant-numeric:tabular-nums}
.wrap{max-width:980px;margin:0 auto;display:grid;gap:12px}
.top{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
h1{font-size:17px;font-weight:600;margin:0}
.pill{padding:3px 10px;border-radius:99px;border:1px solid var(--line);font-size:12.5px;color:var(--mut)}
.pill.live{color:var(--buy);border-color:var(--buy)}.pill.demo{color:var(--amb);border-color:var(--amb)}
.hero{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.price{font-size:clamp(38px,9vw,60px);font-weight:600;letter-spacing:-1px;line-height:1}
.trend{font-size:14px;font-weight:600}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px}
canvas{width:100%;height:300px;display:block}
.cols{display:grid;grid-template-columns:1fr;gap:12px}
@media(min-width:760px){.cols{grid-template-columns:1fr 1.3fr}}
h2{font-size:14px;font-weight:600;margin:0 0 8px}
.lv{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;margin:0}
.lv dt{color:var(--mut)}.lv dd{margin:0;text-align:right;font-weight:600}
.empty{color:var(--mut);margin:0}
.feed{display:grid;gap:8px;max-height:420px;overflow:auto}
.al{border-left:3px solid var(--line);padding:2px 0 2px 10px;white-space:pre-line;font-size:13.5px}
.al small{display:block;color:var(--mut)}
.al.SIGNAL{border-color:var(--amb)}.al.ENTRY{border-color:var(--ac)}.al.TARGET{border-color:var(--buy)}.al.STOPLOSS{border-color:var(--sell)}
button,a.b{font:inherit;font-size:13.5px;color:var(--tx);background:transparent;border:1px solid var(--line);border-radius:8px;padding:7px 12px;cursor:pointer;text-decoration:none}
button:focus-visible,a.b:focus-visible{outline:2px solid var(--ac);outline-offset:2px}
.sell{color:var(--sell)}.buy{color:var(--buy)}
.note{color:var(--mut);font-size:12.5px;margin:0}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <h1>TradeLens Live &middot; NIFTY 50</h1>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <span class="pill" id="mode">connecting</span>
      <a class="b" href="/login" id="login" hidden>Kite login</a>
      <button id="notif">Enable alerts</button>
    </div>
  </div>

  <div class="hero"><div class="price" id="price">--</div><div class="trend" id="trend"></div><div class="pill" id="tf"></div></div>

  <div class="panel"><canvas id="c" aria-label="NIFTY candles with EMA lines and trade levels"></canvas></div>

  <div class="cols">
    <div class="panel"><h2>Current setup</h2><div id="act"><p class="empty">Setup form aagala. Naan watch pannitu irukken.</p></div></div>
    <div class="panel"><h2>Alerts</h2><div class="feed" id="feed"><p class="empty">Innum alert illa.</p></div></div>
  </div>
  <p class="note">Idhu oru idea mattum, guarantee illa. Final decision unnodhu.</p>
</div>

<script>
const $=id=>document.getElementById(id);
const cv=$('c'),g=cv.getContext('2d');
let snap=null,seen=new Set(),audio=null;
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();

function connect(){
  const ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws');
  ws.onmessage=e=>{
    const m=JSON.parse(e.data);
    if(m.type==='snapshot'){snap=m;render()}
    else if(m.type==='alert'){notify(m.alert)}
  };
  ws.onclose=()=>{$('mode').textContent='reconnecting';$('mode').className='pill';setTimeout(connect,2000)};
}
connect();
if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});

function beep(){
  try{audio=audio||new (window.AudioContext||window.webkitAudioContext)();
    const o=audio.createOscillator(),v=audio.createGain();o.connect(v);v.connect(audio.destination);
    o.frequency.value=880;v.gain.setValueAtTime(.2,audio.currentTime);v.gain.exponentialRampToValueAtTime(.001,audio.currentTime+.5);
    o.start();o.stop(audio.currentTime+.5)}catch(e){}
}
function notify(a){
  const id=a.ts+a.kind;if(seen.has(id))return;seen.add(id);beep();
  if('Notification' in window&&Notification.permission==='granted'&&navigator.serviceWorker)
    navigator.serviceWorker.ready.then(r=>r.showNotification('TradeLens: '+a.kind,{body:a.text.split('\n')[0],icon:'/icon-192.png',vibrate:[200,100,200]}));
}
$('notif').onclick=async()=>{
  beep();
  if('Notification' in window)await Notification.requestPermission();
  $('notif').textContent='Alerts on';
};

function render(){
  const s=snap;
  const mode=$('mode');mode.textContent=s.mode;mode.className='pill '+(s.mode==='live'?'live':s.mode==='demo'?'demo':'');
  $('login').hidden=s.mode!=='waiting';
  $('price').textContent=s.price==null?'--':s.price.toFixed(2);
  const t=$('trend');t.textContent=s.trend;t.className='trend '+(s.trend==='Uptrend'?'buy':s.trend==='Downtrend'?'sell':'');
  $('tf').textContent=s.tf+'m';
  renderAct(s.active,s.price);renderFeed(s.alerts);draw();
}
function row(dl,k,v,cls){const a=document.createElement('dt'),b=document.createElement('dd');a.textContent=k;b.textContent=v;if(cls)b.className=cls;dl.append(a,b)}
function renderAct(a,p){
  const box=$('act');box.textContent='';
  if(!a){const e=document.createElement('p');e.className='empty';e.textContent='Setup form aagala. Naan watch pannitu irukken.';box.appendChild(e);return}
  const dl=document.createElement('dl');dl.className='lv';
  row(dl,'Setup',a.setup+' ('+a.side+')',a.side==='SELL'?'sell':'buy');
  row(dl,'Status',a.status==='PENDING'?'Entry-ku wait':'Trade open');
  row(dl,'Entry',(a.side==='SELL'?'below ':'above ')+a.entry.toFixed(2));
  row(dl,'Stop loss',a.sl.toFixed(2),'sell');
  row(dl,'Target',a.target.toFixed(2),'buy');
  row(dl,'Option idea',a.option);
  if(a.status==='OPEN'&&p!=null){const pts=a.side==='SELL'?a.entry-p:p-a.entry;row(dl,'Open P&L',(pts>=0?'+':'')+pts.toFixed(1)+' pts',pts>=0?'buy':'sell')}
  box.appendChild(dl);
  const n=document.createElement('p');n.className='note';n.style.marginTop='8px';n.textContent=a.explain;box.appendChild(n);
}
function renderFeed(list){
  const f=$('feed');f.textContent='';
  if(!list.length){const e=document.createElement('p');e.className='empty';e.textContent='Innum alert illa.';f.appendChild(e);return}
  list.slice().reverse().forEach(a=>{
    const d=document.createElement('div');d.className='al '+a.kind;
    const sm=document.createElement('small');
    sm.textContent=a.kind+' - '+new Date(a.ts*1000).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Kolkata'});
    d.append(sm,document.createTextNode(a.text));f.appendChild(d);
  });
}

function draw(){
  if(!snap)return;
  const dpr=window.devicePixelRatio||1,W=cv.clientWidth,H=cv.clientHeight;
  cv.width=W*dpr;cv.height=H*dpr;g.setTransform(dpr,0,0,dpr,0,0);g.clearRect(0,0,W,H);
  const cs=snap.candles;if(cs.length<2)return;
  const a=snap.active,lv=a?[a.entry,a.sl,a.target]:[];
  let lo=Math.min(...cs.map(c=>c[3]),...lv),hi=Math.max(...cs.map(c=>c[2]),...lv);
  const pad=(hi-lo)*.06||1;lo-=pad;hi+=pad;
  const R=54,x0=4,cw=(W-R-x0)/cs.length,y=v=>H-(v-lo)/(hi-lo)*H;
  g.font='11px "IBM Plex Sans",sans-serif';g.textBaseline='middle';
  g.strokeStyle=css('--line');g.fillStyle=css('--mut');g.lineWidth=1;
  for(let i=0;i<5;i++){const v=lo+(hi-lo)*i/4,yy=y(v);g.beginPath();g.moveTo(0,yy);g.lineTo(W-R,yy);g.stroke();g.fillText(v.toFixed(0),W-R+6,yy)}
  const sellC=css('--sell'),buyC=css('--buy');
  cs.forEach((c,i)=>{
    const x=x0+i*cw+cw/2,up=c[4]>=c[1];g.strokeStyle=g.fillStyle=up?buyC:sellC;
    g.beginPath();g.moveTo(x,y(c[2]));g.lineTo(x,y(c[3]));g.stroke();
    const top=y(Math.max(c[1],c[4])),h=Math.max(1,Math.abs(y(c[1])-y(c[4])));g.fillRect(x-cw*.35,top,cw*.7,h);
  });
  [[snap.ema_fast,css('--amb')],[snap.ema_slow,css('--ac')]].forEach(([arr,col])=>{
    g.strokeStyle=col;g.lineWidth=1.5;g.beginPath();let st=false;
    arr.forEach((v,i)=>{if(v==null)return;const x=x0+i*cw+cw/2;st?g.lineTo(x,y(v)):(g.moveTo(x,y(v)),st=true)});g.stroke();
  });
  if(a){
    [['ENTRY',a.entry,css('--ac')],['SL',a.sl,sellC],['TARGET',a.target,buyC]].forEach(([n,v,col])=>{
      g.strokeStyle=col;g.lineWidth=1.5;g.setLineDash([7,5]);g.beginPath();g.moveTo(0,y(v));g.lineTo(W-R,y(v));g.stroke();g.setLineDash([]);
      g.fillStyle=col;g.fillRect(W-R,y(v)-9,R,18);g.fillStyle='#fff';g.fillText(n,W-R+5,y(v));
    });
  }
}
window.addEventListener('resize',draw);
</script>
</body>
</html>
"""


MANIFEST = r"""{
  "name": "TradeLens Live",
  "short_name": "TradeLens",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "background_color": "#0b1016",
  "theme_color": "#0b1016",
  "icons": [
    {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
    {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
  ]
}
"""


SW_JS = r"""self.addEventListener("install",e=>self.skipWaiting());
self.addEventListener("activate",e=>e.waitUntil(self.clients.claim()));
self.addEventListener("fetch",e=>{});
self.addEventListener("notificationclick",e=>{e.notification.close();e.waitUntil(self.clients.openWindow("/"))});
"""


# ===== server =====

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response


clients, state = set(), {"loop": None, "started": False}


async def broadcast(msg):
    for ws in list(clients):
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            clients.discard(ws)


def emit(msg):
    if state["loop"] and clients:
        asyncio.run_coroutine_threadsafe(broadcast(msg), state["loop"])


engine = Engine(cfg, emit)


@asynccontextmanager
async def lifespan(app):
    state["loop"] = asyncio.get_running_loop()
    if cfg.demo:
        run_demo(engine, cfg)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
def index():
    return HTMLResponse(INDEX_HTML)


@app.get("/manifest.webmanifest")
def manifest():
    return Response(MANIFEST, media_type="application/manifest+json")


@app.get("/sw.js")
def sw():
    return Response(SW_JS, media_type="application/javascript")


@app.get("/icon-192.png")
def icon192():
    return Response(ICON192, media_type="image/png")


@app.get("/icon-512.png")
def icon512():
    return Response(ICON512, media_type="image/png")


@app.get("/login")
def login():
    from kiteconnect import KiteConnect
    return RedirectResponse(KiteConnect(api_key=cfg.api_key).login_url())


@app.get("/callback")
def callback(request_token: str = "", status: str = ""):
    if status != "success" or not request_token:
        return JSONResponse({"error": "Kite login failed"}, status_code=400)
    from kiteconnect import KiteConnect
    from kite_feed import start_kite
    data = KiteConnect(api_key=cfg.api_key).generate_session(request_token, api_secret=cfg.api_secret)
    if not state["started"]:
        state["started"] = True
        start_kite(cfg, engine, data["access_token"])
    return RedirectResponse("/")


@app.get("/api/state")
def api_state():
    return engine.snapshot()


@app.post("/api/test-alert")
def test_alert():
    send_telegram("TradeLens test alert: Telegram connection OK")
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "snapshot", **engine.snapshot()}))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)
