"""Slick web departures-board interface for commute-trains.

Runs a local Flask server; opens a browser automatically.
The RTT Bearer-token exchange happens server-side so credentials
stay out of the browser entirely.
"""
from __future__ import annotations

import datetime as dt
import threading
import webbrowser
from typing import Any

from flask import Flask, jsonify, render_template_string, request

from .commute import CommuteFinder

_lock = threading.Lock()
_finder_cache: dict[tuple[str, tuple[str, ...]], CommuteFinder] = {}

_STATION_NAMES: dict[str, str] = {
    "CTM": "Chatham",
    "STP": "St Pancras Intl",
    "CST": "Cannon Street",
    "VIC": "London Victoria",
    "CHX": "Charing Cross",
    "LBG": "London Bridge",
    "WAT": "London Waterloo",
    "BFR": "Blackfriars",
    "MYB": "Marylebone",
    "PAD": "Paddington",
    "LST": "Liverpool Street",
    "FST": "Fenchurch Street",
}


def _station(crs: str) -> str:
    return _STATION_NAMES.get(crs.upper(), crs.upper())


def _get_finder(home: str, destinations: tuple[str, ...]) -> CommuteFinder:
    key = (home.upper(), tuple(d.upper() for d in destinations))
    with _lock:
        if key not in _finder_cache:
            _finder_cache[key] = CommuteFinder(home=key[0], destinations=key[1])
    return _finder_cache[key]


def _sort_by_arrival(
    pairs: list[tuple[str, Any]], target: dt.datetime
) -> list[tuple[str, Any]]:
    def _key(item: tuple[str, Any]):
        _, s = item
        if not s.arrival_at_query_dest:
            return (1, 0)
        try:
            hh, mm = s.arrival_at_query_dest.split(":")
            arr = target.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            return (0, abs((arr - target).total_seconds()))
        except Exception:
            return (1, 0)
    return sorted(pairs, key=_key)


def _svc_dict(crs: str, svc: Any) -> dict[str, Any]:
    arr_sched = svc.arrival_at_query_dest or ""
    arr_rt = svc.arrival_realtime_at_query_dest or ""
    arr_display = arr_rt if (arr_rt and arr_rt != arr_sched) else arr_sched
    return {
        "crs": crs.upper(),
        "std": svc.std,
        "etd": svc.etd,
        "status": svc.status,
        "platform": svc.platform,
        "operator": svc.operator or "",
        "destination": svc.destination or "",
        "origin": svc.origin or "",
        "arr": arr_display or None,
        "cancelled": svc.cancelled,
    }


def create_app(
    home: str = "CTM",
    destinations: tuple[str, ...] = ("STP", "CST"),
) -> Flask:
    app = Flask(__name__)

    home_up = home.upper()
    dests_up = tuple(d.upper() for d in destinations)
    dests_str = ",".join(dests_up)
    home_name = _station(home_up)
    dests_display = " / ".join(_station(d) for d in dests_up)

    @app.route("/")
    def index() -> str:
        return render_template_string(
            _HTML,
            home=home_up,
            home_name=home_name,
            dests=dests_str,
            dests_display=dests_display,
        )

    @app.route("/api/trains")
    def trains_api():
        direction  = request.args.get("direction", "outbound")
        window     = min(int(request.args.get("window", "120")), 480)
        limit      = int(request.args.get("limit", "10"))   # 0 = no limit
        when_str   = request.args.get("when")               # ISO datetime, optional
        is_arrival = request.args.get("is_arrival", "false").lower() == "true"

        finder = _get_finder(home_up, dests_up)
        now = dt.datetime.now()

        if when_str:
            try:
                when = dt.datetime.fromisoformat(when_str)
            except ValueError:
                return jsonify(error=f"Invalid 'when': {when_str!r}"), 400
        else:
            when = now

        if is_arrival:
            time_from = when - dt.timedelta(hours=2)
            time_to   = when + dt.timedelta(minutes=30)
        else:
            time_from = when
            time_to   = when + dt.timedelta(minutes=window)

        try:
            if direction == "return":
                pairs = finder.trains_home(when=time_from, time_to=time_to)
            else:
                pairs = finder.next_trains(when=time_from, time_to=time_to)
            if is_arrival:
                pairs = _sort_by_arrival(pairs, when)
            trains = [_svc_dict(crs, svc) for crs, svc in pairs]
            if limit > 0:
                trains = trains[:limit]
            rate_limits = getattr(finder.client, "last_rate_limits", {})
            return jsonify(
                trains=trains,
                updated=now.isoformat(),
                direction=direction,
                planned=when_str is not None,
                is_arrival=is_arrival,
                rate_limits=rate_limits,
            )
        except Exception as exc:
            return jsonify(error=str(exc)), 500

    return app


def serve(
    host: str = "127.0.0.1",
    port: int = 5000,
    open_browser: bool = True,
    home: str = "CTM",
    destinations: tuple[str, ...] = ("STP", "CST"),
) -> None:
    app = create_app(home=home, destinations=destinations)
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
    print(f"  ✦  Commute Trains  →  http://{host}:{port}/")
    app.run(host=host, port=port, debug=False, use_reloader=False)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Run the commute-trains web departures board.")
    p.add_argument("--home", default="CTM", metavar="CRS",
                   help="Home station CRS code (default: CTM)")
    p.add_argument("--to", action="append", default=None, metavar="CRS",
                   help="Work terminus CRS (repeat for multiple; default: STP CST)")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--no-browser", action="store_true",
                   help="Don't automatically open a browser tab")
    args = p.parse_args(argv)
    destinations = tuple(args.to) if args.to else ("STP", "CST")
    serve(
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
        home=args.home,
        destinations=destinations,
    )
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Embedded HTML / CSS / JS — single-file, no static assets needed
# ──────────────────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Commute Trains</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}

:root{
  --bg:#071540;
  --surface:#091a50;
  --surface2:#0d2472;
  --border:#1c3d8c;
  --text:#ffffff;
  --muted:#6898d4;
  --accent:#f5a520;
  --green:#00de47;
  --red:#ff3030;
  --amber:#f5a520;
  --blue:#4ab0ff;
  --highlight:#0f308f;
}

body{
  background:var(--bg);
  color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,Helvetica,Arial,sans-serif;
  min-height:100vh;
  position:relative;
}

body::before{
  content:"";
  position:fixed;
  top:0;left:0;right:0;
  height:160px;
  background:linear-gradient(180deg,rgba(15,48,143,.25) 0%,transparent 100%);
  pointer-events:none;
  z-index:0;
}

/* ── HEADER ─────────────────────────────────────────────────────────────── */
.hdr{
  position:sticky;top:0;z-index:20;
  background:#071540;
  border-bottom:2px solid #1c3d8c;
  padding:14px 24px;
  display:flex;align-items:center;justify-content:space-between;
}

.logo{
  display:flex;align-items:center;gap:14px;
}

.logo-mark{
  width:40px;height:40px;
  background:var(--surface2);
  border:1px solid var(--border);
  border-radius:6px;
  display:flex;align-items:center;justify-content:center;
  font-size:1.2rem;
  flex-shrink:0;
}

.logo-text{
  display:flex;flex-direction:column;gap:2px;
}

.logo-title{
  font-size:.85rem;
  font-weight:700;
  letter-spacing:.18em;
  text-transform:uppercase;
  color:var(--accent);
}

.logo-route{
  font-size:.7rem;
  color:var(--muted);
  letter-spacing:.06em;
  text-transform:uppercase;
}

.clock{
  font-family:ui-monospace,"SF Mono","Cascadia Code","Roboto Mono",monospace;
  font-size:1.85rem;
  font-weight:700;
  letter-spacing:.06em;
  color:var(--accent);
  text-shadow:0 0 32px rgba(245,165,32,.5);
}

/* ── CONTROLS ────────────────────────────────────────────────────────────── */
.controls{
  position:relative;z-index:1;
  padding:16px 24px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;
}

.tabs{
  display:flex;
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:12px;
  padding:4px;
  gap:3px;
}

.tab{
  padding:8px 20px;
  border:none;
  background:transparent;
  color:var(--muted);
  font-size:.78rem;
  font-weight:700;
  letter-spacing:.07em;
  text-transform:uppercase;
  border-radius:9px;
  cursor:pointer;
  transition:all .2s ease;
  display:flex;align-items:center;gap:7px;
}

.tab-icon{font-size:.9rem}
.tab:hover:not(.active){color:var(--text)}
.tab.active{
  background:linear-gradient(135deg,#f5a623 0%,#e08b10 100%);
  color:#0a0a0a;
  box-shadow:0 2px 12px rgba(245,166,35,.3);
}

.right-controls{display:flex;align-items:center;gap:10px}

.window-select{
  padding:7px 12px;
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:8px;
  color:var(--muted);
  font-size:.75rem;
  cursor:pointer;
  outline:none;
  transition:border-color .2s;
  appearance:none;
  -webkit-appearance:none;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%234d6585'/%3E%3C/svg%3E");
  background-repeat:no-repeat;
  background-position:right 10px center;
  padding-right:28px;
}
.window-select:hover{border-color:var(--accent)}
.window-select option{background:#0d1627}

.ring-wrap{
  display:flex;align-items:center;gap:6px;
  cursor:pointer;
}

.cring{
  width:38px;height:38px;
  transform:rotate(-90deg);
  overflow:visible;
}

.cring circle{fill:none;stroke-width:3}
.cring .bg{stroke:var(--border)}
.cring .fg{
  stroke:var(--accent);
  stroke-linecap:round;
  stroke-dasharray:100 100;
  stroke-dashoffset:0;
  transition:stroke-dashoffset 1s linear;
  filter:drop-shadow(0 0 4px rgba(245,166,35,.5));
}

.cd-num{
  font-family:ui-monospace,monospace;
  font-size:.78rem;
  font-weight:600;
  color:var(--muted);
  min-width:18px;
  text-align:center;
}

.refresh-btn{
  padding:7px 14px;
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:8px;
  color:var(--muted);
  font-size:.75rem;
  font-weight:600;
  cursor:pointer;
  transition:all .2s;
  letter-spacing:.04em;
}
.refresh-btn:hover{border-color:var(--accent);color:var(--accent)}
.refresh-btn:active{transform:scale(.97)}

/* ── PLAN BAR ────────────────────────────────────────────────────────────── */
.plan-btn{
  padding:7px 14px;
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:8px;
  color:var(--muted);
  font-size:.75rem;
  font-weight:600;
  cursor:pointer;
  transition:all .2s;
  letter-spacing:.04em;
}
.plan-btn:hover{border-color:var(--accent);color:var(--accent)}
.plan-btn.active{
  background:rgba(245,166,35,.1);
  border-color:rgba(245,166,35,.4);
  color:var(--accent);
}

.plan-bar{
  position:relative;z-index:1;
  padding:0 24px 14px;
  display:flex;align-items:center;gap:10px;flex-wrap:wrap;
}
.plan-bar.hidden{display:none}

.plan-bar-label{
  font-size:.7rem;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);
}

.plan-input{
  padding:7px 12px;
  background:var(--surface);
  border:1px solid rgba(245,166,35,.35);
  border-radius:8px;
  color:var(--text);
  font-size:.8rem;
  font-family:inherit;
  outline:none;
  color-scheme:dark;
  transition:border-color .2s,box-shadow .2s;
}
.plan-input:focus{
  border-color:var(--accent);
  box-shadow:0 0 0 2px rgba(245,166,35,.12);
}

.plan-toggle{
  position:relative;
  display:flex;
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:5px;
  padding:3px;
  cursor:pointer;
  user-select:none;
}
.pt-opt{
  position:relative;z-index:1;
  padding:5px 14px;
  font-size:.75rem;
  font-weight:700;
  letter-spacing:.07em;
  text-transform:uppercase;
  border-radius:3px;
  transition:color .2s;
  white-space:nowrap;
}
.plan-toggle:not(.arrive) .pt-depart,
.plan-toggle.arrive .pt-arrive{color:#070c17}
.plan-toggle.arrive .pt-depart,
.plan-toggle:not(.arrive) .pt-arrive{color:var(--muted)}
.pt-slider{
  position:absolute;
  top:3px;bottom:3px;
  width:calc(50% - 3px);
  left:3px;
  background:var(--accent);
  border-radius:3px;
  transition:left .2s ease;
  z-index:0;
}
.plan-toggle.arrive .pt-slider{left:calc(50%)}

.live-btn{
  padding:7px 14px;
  background:transparent;
  border:1px solid var(--border);
  border-radius:8px;
  color:var(--muted);
  font-size:.75rem;
  font-weight:600;
  cursor:pointer;
  transition:all .2s;
  letter-spacing:.04em;
  margin-left:auto;
}
.live-btn:hover{border-color:var(--red);color:var(--red)}

.go-btn{
  padding:7px 18px;
  background:var(--accent);
  border:none;
  border-radius:5px;
  color:#070c17;
  font-size:.78rem;
  font-weight:700;
  cursor:pointer;
  transition:all .15s;
  letter-spacing:.06em;
  text-transform:uppercase;
}
.go-btn:hover{filter:brightness(1.1)}
.go-btn:active{transform:scale(.97)}

/* ── BOARD ───────────────────────────────────────────────────────────────── */
.board-wrap{
  position:relative;z-index:1;
  padding:0 24px 32px;
}

.board-card{
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:16px;
  overflow:hidden;
  box-shadow:0 4px 40px rgba(0,0,0,.4);
  transition:opacity .25s ease;
}

.board-card.loading{opacity:.6}

table{width:100%;border-collapse:collapse;font-size:.875rem}

thead th{
  padding:12px 16px;
  text-align:left;
  font-size:.65rem;
  font-weight:700;
  letter-spacing:.12em;
  text-transform:uppercase;
  color:var(--muted);
  background:var(--surface2);
  border-bottom:1px solid var(--border);
  white-space:nowrap;
}

tbody tr{
  border-bottom:1px solid rgba(27,46,72,.6);
  transition:background .15s;
}
tbody tr:last-child{border-bottom:none}
tbody tr:hover{background:rgba(17,30,54,.8)}
tbody tr.first-train{background:rgba(245,166,35,.04)}
tbody tr.first-train td:first-child{
  border-left:3px solid var(--accent);
  padding-left:13px;
}
tbody tr.cancelled-row{opacity:.55}

tbody td{padding:13px 16px;vertical-align:middle}

/* Per-column alignment — use .tc class on individual th/td instead of fragile nth-child */
.tc{text-align:center}

/* ── CELL COMPONENTS ─────────────────────────────────────────────────────── */

/* Time */
.t-dep{
  font-family:ui-monospace,"SF Mono",monospace;
  font-size:1.05rem;
  font-weight:700;
  color:var(--text);
  letter-spacing:.03em;
}

/* Status badge */
.badge{
  display:inline-flex;align-items:center;gap:5px;
  padding:4px 10px 4px 7px;
  border-radius:20px;
  font-size:.72rem;
  font-weight:700;
  letter-spacing:.03em;
  white-space:nowrap;
}
.dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.b-ok{background:rgba(39,201,138,.1);color:var(--green);border:1px solid rgba(39,201,138,.2)}
.b-ok .dot{background:var(--green);box-shadow:0 0 6px rgba(39,201,138,.6)}
.b-late{background:rgba(245,166,35,.1);color:var(--amber);border:1px solid rgba(245,166,35,.2)}
.b-late .dot{background:var(--amber);animation:pulse 1.5s ease infinite}
.b-cancel{background:rgba(232,93,93,.1);color:var(--red);border:1px solid rgba(232,93,93,.2)}
.b-cancel .dot{background:var(--red)}
.b-sched{background:rgba(77,101,133,.1);color:var(--muted);border:1px solid rgba(77,101,133,.2)}
.b-sched .dot{background:var(--muted)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}

/* Due in */
.t-due{
  font-family:ui-monospace,"SF Mono",monospace;
  font-size:.9rem;
  font-weight:700;
  letter-spacing:.02em;
  display:inline-block;
  min-width:3ch;
  text-align:right;
}
.t-due-green{color:var(--green)}
.t-due-amber{color:var(--amber)}
.t-due-red{color:var(--red);animation:pulse 1.2s ease infinite}
.t-due-dep{color:var(--muted);font-weight:400;font-size:.75rem}

/* Platform */
.plat{
  display:inline-flex;align-items:center;justify-content:center;
  min-width:30px;height:28px;
  padding:0 6px;
  border-radius:7px;
  background:var(--surface2);
  border:1px solid var(--border);
  font-size:.75rem;
  font-weight:700;
  color:var(--text);
  font-family:ui-monospace,monospace;
}
.plat-none{
  color:var(--muted);
  background:transparent;
  border-color:transparent;
}

/* Via / From chip */
.chip{
  display:inline-flex;align-items:center;
  padding:3px 9px;
  border-radius:6px;
  font-size:.7rem;
  font-weight:700;
  letter-spacing:.07em;
  background:rgba(77,157,224,.1);
  border:1px solid rgba(77,157,224,.2);
  color:var(--blue);
  font-family:ui-monospace,monospace;
}

/* Arrival time */
.t-arr{
  font-family:ui-monospace,monospace;
  font-size:.875rem;
  color:var(--muted);
}
.t-arr.late{color:var(--amber)}

/* Destination / operator */
.dest-name{font-weight:500;color:var(--text)}
.dest-op{font-size:.72rem;color:var(--muted);margin-top:2px}

/* ── STATES ──────────────────────────────────────────────────────────────── */
.state-box{
  padding:64px 24px;
  text-align:center;
  color:var(--muted);
}
.state-box .ico{font-size:2.5rem;margin-bottom:14px;display:block}
.state-box p{font-size:.9rem;line-height:1.6}
.state-box .err{
  font-family:ui-monospace,monospace;
  font-size:.75rem;
  color:var(--red);
  margin-top:10px;
  opacity:.85;
}

/* Skeleton shimmer */
.sk{
  display:inline-block;
  height:.85em;
  border-radius:4px;
  background:linear-gradient(90deg,var(--surface2) 25%,var(--border) 50%,var(--surface2) 75%);
  background-size:300% 100%;
  animation:shimmer 1.4s ease infinite;
}
@keyframes shimmer{0%{background-position:200%}100%{background-position:-200%}}

/* Row fade-in */
@keyframes rowIn{from{opacity:0;transform:translateY(-5px)}to{opacity:1;transform:none}}
tbody tr{animation:rowIn .25s ease both}
tbody tr:nth-child(1){animation-delay:.02s}
tbody tr:nth-child(2){animation-delay:.05s}
tbody tr:nth-child(3){animation-delay:.08s}
tbody tr:nth-child(4){animation-delay:.1s}
tbody tr:nth-child(5){animation-delay:.12s}

/* ── FOOTER ──────────────────────────────────────────────────────────────── */
.footer{
  position:relative;z-index:1;
  padding:8px 28px 24px;
  display:flex;align-items:center;justify-content:space-between;
  font-size:.72rem;
  color:var(--muted);
}

/* ── RESPONSIVE ──────────────────────────────────────────────────────────── */
@media(max-width:680px){
  .hdr{padding:12px 14px}
  .clock{font-size:1.2rem}
  .controls,.board-wrap{padding-left:14px;padding-right:14px}
  .col-op{display:none}
  .logo-route{display:none}
}
</style>
</head>
<body>

<!-- ── HEADER ────────────────────────────────────────────────────────────── -->
<header class="hdr">
  <div class="logo">
    <div class="logo-mark">🚆</div>
    <div class="logo-text">
      <span class="logo-title">Commute</span>
      <span class="logo-route" id="route-label">{{ home_name }} → {{ dests_display }}</span>
    </div>
  </div>
  <div class="clock" id="clock">--:--:--</div>
</header>

<!-- ── CONTROLS ──────────────────────────────────────────────────────────── -->
<div class="controls">
  <div class="tabs">
    <button class="tab active" id="tab-out" onclick="setDir('outbound')">
      <span class="tab-icon">↑</span>Outbound
    </button>
    <button class="tab" id="tab-ret" onclick="setDir('return')">
      <span class="tab-icon">↓</span>Return
    </button>
  </div>

  <div class="right-controls">
    <button class="plan-btn" id="plan-btn" onclick="togglePlan()">📅 Plan</button>

    <select class="window-select" id="limit-sel" onchange="fetchNow()" title="Max results">
      <option value="5">5 trains</option>
      <option value="10" selected>10 trains</option>
      <option value="15">15 trains</option>
      <option value="20">20 trains</option>
      <option value="0">All</option>
    </select>

    <select class="window-select" id="window-sel" onchange="fetchNow()">
      <option value="60">60 min</option>
      <option value="120" selected>2 hours</option>
      <option value="240">4 hours</option>
    </select>

    <div class="ring-wrap" onclick="fetchNow()" title="Click to refresh">
      <svg class="cring" viewBox="0 0 36 36">
        <circle class="bg" cx="18" cy="18" r="15.9"/>
        <circle class="fg" id="ring-fg" cx="18" cy="18" r="15.9"/>
      </svg>
      <span class="cd-num" id="cd-num">–</span>
    </div>

    <button class="refresh-btn" onclick="fetchNow()">↻ Refresh</button>
  </div>
</div>

<!-- ── PLAN BAR ────────────────────────────────────────────────────────────── -->
<div class="plan-bar hidden" id="plan-bar">
  <span class="plan-bar-label">Plan</span>
  <input type="date" class="plan-input" id="plan-date">
  <input type="time" class="plan-input" id="plan-time" step="300">
  <div class="plan-toggle" id="plan-toggle" onclick="toggleArriveMode()">
    <span class="pt-opt pt-depart">Depart</span>
    <span class="pt-opt pt-arrive">Arrive</span>
    <div class="pt-slider"></div>
  </div>
  <button class="go-btn" onclick="fetchNow()">Go →</button>
  <button class="live-btn" onclick="goLive()">✕ Live</button>
</div>

<!-- ── BOARD ─────────────────────────────────────────────────────────────── -->
<div class="board-wrap">
  <div class="board-card" id="board">
    <div class="state-box">
      <span class="ico">🚆</span>
      <p>Loading departure board…</p>
    </div>
  </div>
</div>

<div class="footer">
  <span id="footer-updated">–</span>
  <span id="footer-ratelimit" class="footer-rl"></span>
  <span id="footer-count"></span>
</div>

<script>
const HOME  = "{{ home }}";
const DESTS = "{{ dests }}".split(",");
const CIRC  = 100;
const CD_TOTAL = 30;

let direction = "outbound";
let cdTimer   = null;
let cdVal     = CD_TOTAL;
let planMode  = false;

// ── Clock ──────────────────────────────────────────────────────────────────
function pad(n){ return String(n).padStart(2,"0"); }
function tick(){
  const d = new Date();
  document.getElementById("clock").textContent =
    pad(d.getHours())+":"+pad(d.getMinutes())+":"+pad(d.getSeconds());
}
setInterval(tick,1000); tick();

// ── Direction tabs ────────────────────────────────────────────────────────
function setDir(d){
  direction = d;
  document.getElementById("tab-out").classList.toggle("active", d==="outbound");
  document.getElementById("tab-ret").classList.toggle("active", d==="return");
  fetchNow();
}

// ── Plan mode ─────────────────────────────────────────────────────────────
function togglePlan(){
  planMode = !planMode;
  document.getElementById("plan-bar").classList.toggle("hidden", !planMode);
  document.getElementById("plan-btn").classList.toggle("active", planMode);
  if(planMode){
    const now = new Date();
    document.getElementById("plan-date").value = isoDate(now);
    document.getElementById("plan-time").value = isoTime(now);
    clearInterval(cdTimer);
    document.getElementById("cd-num").textContent = "–";
    document.getElementById("ring-fg").style.strokeDashoffset = CIRC;
    fetchNow();
  } else {
    goLive();
  }
}

function goLive(){
  planMode = false;
  document.getElementById("plan-bar").classList.add("hidden");
  document.getElementById("plan-btn").classList.remove("active");
  fetchNow();
}

// toggle slides between Depart / Arrive — no fetch until Go is pressed
function toggleArriveMode(){
  document.getElementById("plan-toggle").classList.toggle("arrive");
}

function planParams(){
  if(!planMode) return "";
  const date   = document.getElementById("plan-date").value;
  const time   = document.getElementById("plan-time").value;
  const arrive = document.getElementById("plan-toggle").classList.contains("arrive");
  if(!date || !time) return "";
  return `&when=${encodeURIComponent(date+"T"+time+":00")}&is_arrival=${arrive}`;
}

function isoDate(d){ return d.toISOString().slice(0,10); }
function isoTime(d){ return pad(d.getHours())+":"+pad(d.getMinutes()); }

function fmtPlanLabel(){
  const date   = document.getElementById("plan-date").value;
  const time   = document.getElementById("plan-time").value;
  const arrive = document.getElementById("plan-toggle").classList.contains("arrive");
  if(!date || !time) return "";
  const d = new Date(date+"T"+time);
  const label = d.toLocaleDateString(undefined,{weekday:"short",day:"numeric",month:"short"})
    +" · "+time;
  return arrive ? "Arriving around "+label : "Departing around "+label;
}

// ── Countdown ring ────────────────────────────────────────────────────────
function startCountdown(){
  clearInterval(cdTimer);
  if(planMode){
    document.getElementById("cd-num").textContent = "–";
    document.getElementById("ring-fg").style.strokeDashoffset = CIRC;
    return;
  }
  cdVal = CD_TOTAL;
  drawRing();
  cdTimer = setInterval(()=>{
    cdVal--;
    drawRing();
    if(cdVal<=0){ clearInterval(cdTimer); fetchNow(); }
  },1000);
}

function drawRing(){
  document.getElementById("cd-num").textContent = cdVal;
  const offset = CIRC*(1 - cdVal/CD_TOTAL);
  document.getElementById("ring-fg").style.strokeDashoffset = offset;
}

// ── Fetch ─────────────────────────────────────────────────────────────────
async function fetchNow(){
  clearInterval(cdTimer);
  const board = document.getElementById("board");
  board.classList.add("loading");
  showSkeleton();
  const win   = document.getElementById("window-sel").value;
  const limit = document.getElementById("limit-sel").value;
  try{
    const r = await fetch(`/api/trains?direction=${direction}&window=${win}&limit=${limit}${planParams()}`);
    const data = await r.json();
    if(data.error) throw new Error(data.error);
    renderBoard(data.trains, data.updated, data.planned, data.is_arrival);
  } catch(e){
    showError(e.message);
  }
  board.classList.remove("loading");
  startCountdown();
}

// ── Time-to-departure ─────────────────────────────────────────────────────
function minutesUntil(t){
  const timeStr = t.etd || t.std;
  if(!timeStr) return null;
  const now = new Date();
  const [h,m] = timeStr.split(':').map(Number);
  const dep = new Date(now);
  dep.setHours(h, m, 0, 0);
  if(dep.getTime() < now.getTime() - 6*3600000) dep.setDate(dep.getDate()+1);
  return Math.round((dep.getTime() - now.getTime()) / 60000);
}

function dueCell(t){
  if(t.cancelled) return `<span class="t-due t-due-dep">–</span>`;
  const mins = minutesUntil(t);
  if(mins===null) return `<span class="t-due t-due-dep">–</span>`;
  if(mins<0)  return `<span class="t-due t-due-dep">dep</span>`;
  if(mins===0) return `<span class="t-due t-due-red">now</span>`;
  const cls = mins<=5 ? "red" : mins<=20 ? "amber" : "green";
  return `<span class="t-due t-due-${cls}">${mins}m</span>`;
}

// ── Skeleton ──────────────────────────────────────────────────────────────
function showSkeleton(){
  const live = !planMode;
  const dueTh = live ? "<th>Due</th>" : "";
  const base = [38,70,24,36,38,85,120];
  const cols = live ? [32,...base] : base;
  const rows = [0,1,2].map(()=>`<tr>${cols.map(x=>`<td><span class="sk" style="width:${x}px"></span></td>`).join("")}</tr>`).join("");
  setBoard(`<table>
    <thead><tr><th>Dep</th>${dueTh}<th>Status</th><th class="tc">Plat</th><th>Via</th><th class="tc">Arr</th><th class="col-op">Operator</th><th>Destination</th></tr></thead>
    <tbody>${rows}</tbody></table>`);
}

// ── Render ────────────────────────────────────────────────────────────────
function statusBadge(t){
  if(t.cancelled)
    return `<span class="badge b-cancel"><span class="dot"></span>Cancelled</span>`;
  const s = t.status||"";
  if(s==="On time")
    return `<span class="badge b-ok"><span class="dot"></span>On time</span>`;
  if(s==="Scheduled")
    return `<span class="badge b-sched"><span class="dot"></span>Scheduled</span>`;
  return `<span class="badge b-late"><span class="dot"></span>${esc(s)}</span>`;
}

function renderBoard(trains, updated, planned, isArrival){
  if(!trains.length){
    setBoard(`<div class="state-box"><span class="ico">🕐</span><p>No trains in this window.<br>Try a wider time range.</p></div>`);
    setFooter(updated, 0, planned); return;
  }

  const isRet  = direction==="return";
  const col4   = isRet ? "From" : "Via";
  const col5   = isRet ? `Arr ${esc(HOME)}` : "Arr";
  const dueTh  = planned ? "" : `<th>Due</th>`;

  const rows = trains.map((t,i)=>{
    const first = i===0 && !planned;
    const platHtml = t.platform
      ? `<span class="plat">${esc(t.platform)}</span>`
      : `<span class="plat plat-none">–</span>`;
    const arrClass = (t.arr && t.status!=="On time" && !t.cancelled) ? " late" : "";
    const arrHtml  = t.arr ? `<span class="t-arr${arrClass}">${esc(t.arr)}</span>` : `<span class="t-arr">–</span>`;
    const rowClass = t.cancelled ? "cancelled-row" : (first ? "first-train" : "");
    const dueTd    = planned ? "" : `<td>${dueCell(t)}</td>`;
    return `<tr class="${rowClass}">
      <td><span class="t-dep">${esc(t.std||"?")}</span></td>
      ${dueTd}
      <td>${statusBadge(t)}</td>
      <td class="tc">${platHtml}</td>
      <td><span class="chip">${esc(t.crs)}</span></td>
      <td class="tc">${arrHtml}</td>
      <td class="col-op"><span style="font-size:.8rem">${esc(t.operator)}</span></td>
      <td><span class="dest-name">${esc(t.destination||t.origin)}</span></td>
    </tr>`;
  }).join("");

  setBoard(`<table>
    <thead><tr>
      <th>Dep</th>${dueTh}<th>Status</th>
      <th class="tc">Plat</th><th>${col4}</th><th class="tc">${col5}</th>
      <th class="col-op">Operator</th><th>Destination</th>
    </tr></thead>
    <tbody>${rows}</tbody></table>`);

  setFooter(updated, trains.length, planned);
}

function showError(msg){
  setBoard(`<div class="state-box"><span class="ico">⚠️</span>
    <p>Could not load departures</p>
    <p class="err">${esc(msg)}</p></div>`);
  document.getElementById("footer-updated").textContent="–";
  document.getElementById("footer-count").textContent="";
}

function setBoard(html){
  document.getElementById("board").innerHTML=html;
}

function setFooter(iso, count, planned){
  let t="–";
  try{ t=new Date(iso).toLocaleTimeString(); }catch{}
  const label = planned ? fmtPlanLabel() : ("Updated "+t);
  document.getElementById("footer-updated").textContent = label;
  document.getElementById("footer-count").textContent = count+" service"+(count!==1?"s":"");
}

function esc(s){
  if(!s) return "";
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

// Boot
fetchNow();
</script>
</body>
</html>"""
