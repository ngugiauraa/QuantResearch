"""Read-only replay debugging viewer. Standard library only; no external scripts or fonts.

This is a debugging instrument, not the dashboard. It controls one replay and cannot place orders.

    python -m quantresearch.timemachine.viewer --tf m15 --start 2024-01-02 --end 2024-02-01
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from quantresearch.timemachine.replay import ReplaySession
from quantresearch.warehouse.store import friendly_warehouse_errors

PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>Replay Viewer</title>
<style>
:root{--bg:#0f1318;--panel:#171d24;--text:#d8dee6;--muted:#8693a3;--up:#3fb68b;--down:#e0605e;--line:#2a333d;--accent:#5b9dd9}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:13px/1.4 system-ui,sans-serif}
header{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 12px;background:var(--panel);border-bottom:1px solid var(--line)}
button{background:#232c36;color:var(--text);border:1px solid var(--line);padding:6px 10px;border-radius:4px;cursor:pointer}
button:hover{border-color:var(--accent)}select,input{background:#232c36;color:var(--text);border:1px solid var(--line);padding:5px;border-radius:4px}
.stat{margin-left:10px}.stat b{font-weight:600}.muted{color:var(--muted)}
main{display:grid;grid-template-columns:1fr 340px;height:calc(100vh - 54px)}canvas{width:100%;height:100%;display:block}
aside{overflow:auto;border-left:1px solid var(--line);padding:10px}h3{margin:12px 0 6px;font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
table{width:100%;border-collapse:collapse;font-size:12px}td,th{padding:3px 4px;text-align:left;border-bottom:1px solid var(--line)}
.log{font:11px/1.45 ui-monospace,monospace;white-space:pre-wrap;word-break:break-word}
@media(max-width:800px){main{grid-template-columns:1fr;height:auto}canvas{height:60vh}aside{border-left:0}}
</style></head><body>
<header>
<button id="play">Play</button><button id="pause">Pause</button><button id="step">Step</button><button id="reset">Reset</button>
<label>Speed <select id="speed"><option>1</option><option>5</option><option selected>20</option><option>100</option><option>500</option></select></label>
<label>Go to <input id="when" placeholder="2024-01-15 10:00"></label><button id="go">Go</button>
<span class="stat" id="time"></span><span class="stat">Bid/Ask <b id="px"></b></span><span class="stat">Equity <b id="eq"></b></span>
</header>
<main><div><canvas id="c"></canvas></div><aside>
<h3>Open positions</h3><table id="pos"></table><h3>Pending orders</h3><table id="pend"></table>
<h3>Recent trades</h3><table id="tr"></table><h3>Event log</h3><div class="log" id="log"></div></aside></main>
<script>
const $=id=>document.getElementById(id);let state=null;
async function ctl(a,extra={}){const r=await fetch('/api/control',{method:'POST',body:JSON.stringify({action:a,...extra})});state=await r.json();draw();}
async function poll(){const r=await fetch('/api/tick',{method:'POST'});state=await r.json();draw();setTimeout(poll,state.playing?200:600);}
$('play').onclick=()=>ctl('play');$('pause').onclick=()=>ctl('pause');$('step').onclick=()=>ctl('step');$('reset').onclick=()=>ctl('reset');
$('speed').onchange=e=>ctl('speed',{value:+e.target.value});$('go').onclick=()=>ctl('seek',{when:$('when').value});
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function rows(id,head,items){$(id).innerHTML='<tr>'+head.map(h=>'<th>'+h+'</th>').join('')+'</tr>'+items.map(r=>'<tr>'+r.map(x=>'<td>'+esc(x??'-')+'</td>').join('')+'</tr>').join('');}
const f=v=>v==null?'-':(+v).toFixed(2);
function draw(){if(!state)return;const s=state;
$('time').textContent=s.time+'  ('+s.cursor+'/'+s.total+')'+(s.playing?' playing':'');$('px').textContent=f(s.bid)+' / '+f(s.ask);$('eq').textContent=f(s.equity);
rows('pos',['Side','Lots','Entry','Stop','Target'],s.positions.map(p=>[p.side>0?'Long':'Short',p.lots,f(p.entry),f(p.stop),f(p.target)]));
rows('pend',['Side','Lots','Type','Price'],s.pending.map(p=>[p.side>0?'Buy':'Sell',p.lots,p.kind,f(p.price)]));
rows('tr',['Side','Entry','Exit','Why','Net'],s.trades.slice(-12).reverse().map(t=>[t.side>0?'Long':'Short',f(t.entry),f(t.exit),t.reason,f(t.net)]));
$('log').textContent=s.events.slice(-40).reverse().map(e=>e.time.slice(0,19)+' '+e.event+' '+Object.entries(e).filter(([k])=>!['seq','time','event'].includes(k)).map(([k,v])=>k+'='+v).join(' ')).join('\n');
chart(s);}
function chart(s){const c=$('c'),dpr=devicePixelRatio||1,W=c.clientWidth,H=c.clientHeight;c.width=W*dpr;c.height=H*dpr;const g=c.getContext('2d');g.scale(dpr,dpr);g.fillStyle='#0f1318';g.fillRect(0,0,W,H);
const cs=s.candles;if(!cs.length){g.fillStyle='#8693a3';g.fillText('No closed bars yet',20,30);return;}
const R=64,B=22,w=W-R;const lo0=Math.min(...cs.map(x=>x.l)),hi0=Math.max(...cs.map(x=>x.h));const pad=(hi0-lo0)*.05||1;const lo=lo0-pad,hi=hi0+pad;
const y=v=>(H-B)-(v-lo)/(hi-lo)*(H-B-8);const bw=w/cs.length;const x=i=>i*bw+bw/2;
g.strokeStyle='#2a333d';g.fillStyle='#8693a3';g.font='11px system-ui';for(let k=0;k<6;k++){const v=lo+(hi-lo)*k/5;g.beginPath();g.moveTo(0,y(v));g.lineTo(w,y(v));g.stroke();g.fillText(v.toFixed(2),w+6,y(v)+4);}
const idx=t=>{let a=0,b=cs.length-1;if(t<cs[0].t)return -1;while(a<b){const m=(a+b+1)>>1;if(cs[m].t<=t)a=m;else b=m-1;}return a;};
cs.forEach((b,i)=>{const up=b.c>=b.o;g.strokeStyle=g.fillStyle=up?'#3fb68b':'#e0605e';g.beginPath();g.moveTo(x(i),y(b.h));g.lineTo(x(i),y(b.l));g.stroke();const t=Math.min(y(b.o),y(b.c));g.fillRect(x(i)-Math.max(1,bw*.35),t,Math.max(2,bw*.7),Math.max(1,Math.abs(y(b.o)-y(b.c))));});
const mark=(i,p,col,up)=>{if(i<0)return;g.fillStyle=col;g.beginPath();const d=up?7:-7;g.moveTo(x(i),y(p)+d);g.lineTo(x(i)-5,y(p)+d*2);g.lineTo(x(i)+5,y(p)+d*2);g.fill();};
s.trades.forEach(t=>{mark(idx(t.entry_time),t.entry,'#5b9dd9',t.side>0);mark(idx(t.exit_time),t.exit,'#f0b35a',t.side<0);});
const level=(p,col,label)=>{if(p==null||p<lo||p>hi)return;g.strokeStyle=col;g.setLineDash([5,4]);g.beginPath();g.moveTo(0,y(p));g.lineTo(w,y(p));g.stroke();g.setLineDash([]);g.fillStyle=col;g.fillText(label,4,y(p)-3);};
s.positions.forEach(p=>{mark(idx(p.entry_time),p.entry,'#5b9dd9',p.side>0);level(p.entry,'#5b9dd9','entry');level(p.stop,'#e0605e','stop');level(p.target,'#3fb68b','target');});
if(s.bid!=null)level(s.bid,'#d8dee6','bid');}
addEventListener('resize',draw);poll();
</script></body></html>"""


class ViewerState:
    def __init__(self, session: ReplaySession):
        self.session = session
        self.lock = threading.Lock()

    def control(self, payload: dict) -> dict:
        action = payload.get("action")
        with self.lock:
            s = self.session
            if action == "play":
                s.play()
            elif action == "pause":
                s.pause()
            elif action == "step":
                s.pause()
                s.step(int(payload.get("value", 1)))
            elif action == "reset":
                s.reset()
            elif action == "speed":
                s.set_speed(int(payload["value"]))
            elif action == "seek":
                s.seek(payload["when"])
            elif action == "tick":
                s.tick()
            elif action != "state":
                raise ValueError(f"Unknown action {action!r}")
            return s.snapshot()


def make_handler(state: ViewerState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status=200):
            self._send(json.dumps(payload, default=str).encode(), "application/json", status)

        def do_GET(self):
            if self.path == "/":
                self._send(PAGE.encode(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._json(state.control({"action": "state"}))
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                if self.path == "/api/tick":
                    self._json(state.control({"action": "tick"}))
                elif self.path == "/api/control":
                    self._json(state.control(json.loads(raw)))
                else:
                    self._json({"error": "not found"}, 404)
            except (ValueError, KeyError, TypeError) as exc:
                self._json({"error": str(exc)}, 400)

    return Handler


def serve(session: ReplaySession, port: int = 8765, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(ViewerState(session)))


@friendly_warehouse_errors
def main() -> None:
    from quantresearch.features.registry import build_features
    from quantresearch.sim.reference import SmaCross
    from quantresearch.timemachine.replay import ReplayConfig
    from quantresearch.warehouse.store import load_bars

    ap = argparse.ArgumentParser(description="Replay debugging viewer")
    ap.add_argument("--tf", default="m15")
    ap.add_argument("--start", default="2024-01-02")
    ap.add_argument("--end", default="2024-02-01")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    execution = load_bars("m1", args.start, args.end)
    signals = build_features(load_bars(args.tf, args.start, args.end))
    session = ReplaySession(execution, signals, SmaCross,
                            ReplayConfig(timeframe=args.tf, start=args.start, end=args.end, strategy="sma_cross"))
    server = serve(session, args.port)
    print(f"Viewer on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
