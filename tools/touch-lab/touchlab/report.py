# SPDX-License-Identifier: Apache-2.0
"""A self-contained HTML report for one or more sessions (no network, no server)."""

from __future__ import annotations

import html
import json
import os
import statistics
from collections import Counter

from . import features
from .dataset import Joined


def _cls(tr) -> str:
    if tr.label_class in ("intended", "accidental"):
        return tr.label_class
    return "unlabelled"


def session_stats(j: Joined) -> dict:
    recs = j.session.records()
    start, end = j.session.span()
    hs = [r[0] for r in recs if r[1] == "h"]
    gaps = [(b - a) / 1e6 for a, b in zip(hs, hs[1:]) if (b - a) < 50_000_000]
    status = Counter(r[2].get("event") for r in j.status)
    tracks = [t for t in j.tracks if t.samples]
    return {
        "session": j.session.id,
        "synthetic": bool(j.session.meta.get("synthetic")),
        "host": j.session.meta.get("host", {}).get("product", ""),
        "minutes": round((end - start) / 6e10, 1),
        "reports": len(hs),
        "report_ms": round(statistics.median(gaps), 2) if gaps else None,
        "evdev_frames": sum(1 for r in recs if r[1] == "e"),
        "libinput_lines": sum(1 for r in recs if r[1] == "l"),
        "contacts": len(tracks),
        "intended": sum(1 for t in tracks if t.label_class == "intended"),
        "accidental": sum(1 for t in tracks if t.label_class == "accidental"),
        "marked_wrong": sum(1 for m in j.marks if m.get("mark") == "wrong"),
        "marked_almost": sum(1 for m in j.marks if m.get("mark") == "almost"),
        "scenarios": len(j.scenarios),
        "firmware_flagged": sum(1 for t in tracks if t.firmware_flagged()),
        "libinput_palm": sum(1 for t in tracks if t.libinput_verdict() == "palm"),
        "libinput_thumb": sum(1 for t in tracks if t.libinput_verdict() == "thumb"),
        "kernel_palm": sum(1 for t in tracks if t.kernel_palm_at),
        "key_presses": len(j.keys),
        "evdev_dropped": status.get("evdev-dropped", 0),
        "libinput_restarts": status.get("libinput-start", 0),
        "paused": status.get("pause", 0),
    }


def payload(joined: list[Joined]) -> dict:
    out = {"sessions": [], "pad": None}
    for j in joined:
        start, _end = j.session.span()
        out["pad"] = out["pad"] or [round(j.pad_mm[0], 2), round(j.pad_mm[1], 2)]
        rows50 = {r["contact"]: r for r in features.rows(j, 50)}
        contacts = []
        for tr in j.tracks:
            if not tr.samples:
                continue
            early = [x for x in tr.samples if x.t - tr.t_down <= 600_000_000]
            late = tr.samples[len(early):]
            ss = early + late[::max(1, len(late) // 80)]
            r = rows50.get(tr.key, {})
            contacts.append({
                "k": tr.key, "cid": tr.cid, "t": round((tr.t_down - start) / 1e9, 4),
                "d": round(tr.duration_ms, 1), "c": _cls(tr), "l": tr.label or "", "sc": tr.scenario or "",
                "m": sorted(set(tr.marks)), "fw": tr.first_unconfident_ms(), "li": tr.libinput_verdict(),
                "lim": tr.libinput_first_ms(), "kp": tr.kernel_palm_at is not None,
                "s": [[round((s.t - tr.t_down) / 1e6, 1), round(s.x, 2), round(s.y, 2), round(s.force, 4),
                       -1 if s.confident is None else int(s.confident)] for s in ss],
                "f": {k: r.get(k) for k in ("edge_min_mm", "f_max", "net_mm", "speed_mean_mm_s",
                                            "since_key_ms", "others_at_down", "still_frac")},
            })
        out["sessions"].append({
            "stats": session_stats(j), "contacts": contacts,
            "keys": [round((t - start) / 1e9, 3) for t in j.keys],
            "marks": [{"t": round((m["t"] - start) / 1e9, 3), "m": m.get("mark"),
                       "w": m.get("window_ns", 3e9) / 1e9} for m in j.marks],
            "scenarios": [{"s": s["scenario"], "t0": round((s["t0"] - start) / 1e9, 3),
                           "t1": round((s["t1"] - start) / 1e9, 3), "i": s.get("intent")} for s in j.scenarios],
            "lidecisions": [{"t": round((d.t - start) / 1e9, 4), "k": d.kind}
                            for d in j.decisions if d.kind in ("motion", "scroll", "button", "gesture")][:20000],
        })
    return out


def render(joined: list[Joined], path: str, title: str = "Touch Lab report") -> str:
    data = json.dumps(payload(joined), separators=(",", ":"))
    page = TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__DATA__", data.replace("</", "<\\/"))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return path


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{color-scheme:light;--bg:#f6f6f4;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--ink3:#8a8984;
--rule:#e4e3df;--intended:#2a78d6;--accidental:#eb6834;--unlabelled:#1baf7a;--mark:#e34948;
--h0:#cde2fb;--h1:#9ec5f4;--h2:#6da7ec;--h3:#3987e5;--h4:#256abf;--h5:#184f95;--h6:#0d366b;
font-family:Figtree,system-ui,sans-serif;font-feature-settings:"kern" 1}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#121211;--surface:#1a1a19;
--ink:#f0efec;--ink2:#c3c2b7;--ink3:#8d8c85;--rule:#2e2e2c;--intended:#3987e5;--accidental:#d95926;--unlabelled:#199e70;
--mark:#e66767;--h0:#0d366b;--h1:#104281;--h2:#184f95;--h3:#1c5cab;--h4:#2a78d6;--h5:#5598e7;--h6:#9ec5f4}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#121211;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--ink3:#8d8c85;
--rule:#2e2e2c;--intended:#3987e5;--accidental:#d95926;--unlabelled:#199e70;--mark:#e66767;--h0:#0d366b;--h1:#104281;
--h2:#184f95;--h3:#1c5cab;--h4:#2a78d6;--h5:#5598e7;--h6:#9ec5f4}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);line-height:20px;font-size:14px}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:24px;line-height:32px;font-weight:650;margin:0 0 4px}h2{font-size:17px;line-height:24px;font-weight:620;margin:0 0 4px}
p.sub{color:var(--ink2);margin:0 0 16px}.card{background:var(--surface);border:1px solid var(--rule);border-radius:16px;padding:16px;margin:16px 0}
.note{color:var(--ink2);font-size:13px;margin:0 0 12px}.row{display:flex;flex-wrap:wrap;gap:12px;align-items:center}
select,button{font:inherit;color:var(--ink);background:var(--bg);border:1px solid var(--rule);border-radius:10px;padding:6px 10px}
.legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--ink2);font-size:13px}.sw{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--rule);white-space:nowrap}th{color:var(--ink2);font-weight:560;cursor:pointer}
.scroll{overflow-x:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
canvas{width:100%;display:block;border-radius:10px}.tip{position:fixed;pointer-events:none;background:var(--surface);color:var(--ink);
border:1px solid var(--rule);border-radius:10px;padding:8px 10px;font-size:12px;line-height:16px;box-shadow:0 6px 20px rgba(0,0,0,.15);display:none;z-index:9;max-width:280px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px}.stat{padding:8px 10px;border-radius:10px;background:var(--bg)}
.stat b{display:block;font-size:20px;line-height:28px;font-variant-numeric:tabular-nums}.stat span{color:var(--ink2);font-size:12px}
.badge{font-size:11px;padding:2px 8px;border-radius:99px;background:var(--bg);color:var(--ink2);border:1px solid var(--rule)}
</style></head><body><main>
<h1>Touch Lab</h1><p class="sub" id="sub"></p>
<div class="row" id="pick"></div>
<section class="card"><h2>Session</h2><div class="stats" id="stats"></div></section>
<section class="card"><h2>Where contacts land</h2><p class="note">Touch-down points and dwell (time on the pad) by class, on the pad's own proportions. Darker is more.</p>
<div class="row"><select id="hmMode"><option value="down">Touch-down</option><option value="dwell">Dwell</option></select><select id="hmLabel"></select></div>
<div class="grid" id="heat"></div></section>
<section class="card"><h2>Force over time</h2><p class="note">Each contact's force from touch-down (first 600 ms). The thick line is the class median.</p>
<div class="legend" id="lgForce"></div><canvas id="force" height="300"></canvas></section>
<section class="card"><h2>Contacts against the keyboard</h2><p class="note">One bar per contact, in lanes; ticks above are key presses (time only). Hatched: the firmware flagged it not confident. ◆ libinput called it a palm, ▲ a suppressed thumb. Red brackets: "that was wrong" marks and their window.</p>
<div class="row"><button id="tlPrev">Earlier</button><button id="tlNext">Later</button><select id="tlSpan"><option>10</option><option selected>30</option><option>120</option><option>600</option></select><span class="note" id="tlRange"></span></div>
<div class="legend" id="lgTl"></div><canvas id="tl" height="220"></canvas></section>
<section class="card"><h2>Who agrees</h2><p class="note">Label against the firmware's confidence flag and libinput's verdict. Rows are labels; the off-diagonal cells are where the model has to beat them.</p>
<div class="scroll"><table id="agree"></table></div><div class="scroll" style="margin-top:12px"><table id="timing"></table></div></section>
<section class="card"><h2>Contacts</h2><p class="note">Features at 50 ms. Click a heading to sort.</p><div class="scroll"><table id="ctab"></table></div></section>
</main><div class="tip" id="tip"></div>
<script>
const D=__DATA__;
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const CL=["intended","accidental","unlabelled"],NAME={intended:"Intended",accidental:"Accidental",unlabelled:"Unlabelled"};
let S=0,tl0=0;const tip=document.getElementById("tip");
function showTip(e,h){tip.innerHTML=h;tip.style.display="block";const x=Math.min(innerWidth-300,e.clientX+14);tip.style.left=x+"px";tip.style.top=(e.clientY+14)+"px"}
function hideTip(){tip.style.display="none"}
function ctx(c){const H=+(c.dataset.h||(c.dataset.h=c.getAttribute("height"))),r=c.getBoundingClientRect(),d=devicePixelRatio||1;c.style.height=H+"px";c.width=r.width*d;c.height=H*d;const g=c.getContext("2d");g.scale(d,d);return [g,r.width,H]}
function legend(el,items){el.innerHTML=items.map(([n,c])=>`<span><span class="sw" style="background:${c}"></span>${n}</span>`).join("")}
function sess(){return D.sessions[S]}
function stats(){const s=sess().stats;const L=[["contacts","Contacts"],["intended","Intended (labelled)"],["accidental","Accidental (labelled)"],["marked_wrong","Marked wrong"],["marked_almost","Marked almost"],["scenarios","Guided trials"],["firmware_flagged","Firmware flagged"],["libinput_palm","libinput palm"],["libinput_thumb","libinput thumb"],["kernel_palm","Kernel palm tool"],["key_presses","Key presses"],["minutes","Minutes"],["reports","HID reports"],["report_ms","Report interval, ms"],["evdev_dropped","evdev drops"],["libinput_restarts","libinput starts"]];
document.getElementById("stats").innerHTML=L.map(([k,n])=>`<div class="stat"><b>${s[k]??"–"}</b><span>${n}</span></div>`).join("");
document.getElementById("sub").innerHTML=`${s.session} · ${s.host||""} ${s.synthetic?'<span class="badge">synthetic</span>':""}`}
function heat(){const mode=document.getElementById("hmMode").value,lab=document.getElementById("hmLabel").value,el=document.getElementById("heat");el.innerHTML="";
const [W,H]=D.pad,NX=48,NY=Math.round(48*H/W),ramp=["--h0","--h1","--h2","--h3","--h4","--h5","--h6"].map(css);
const groups=lab?[[lab,c=>c.l===lab]]:CL.map(k=>[NAME[k],c=>c.c===k]);
for(const [name,pred] of groups){const box=document.createElement("div");box.innerHTML=`<div class="note" style="margin:0 0 6px">${name}</div>`;const cv=document.createElement("canvas");cv.setAttribute("height",Math.round(260*H/W));box.appendChild(cv);el.appendChild(box);
const g0=new Float64Array(NX*NY);let n=0;for(const c of sess().contacts){if(!pred(c))continue;n++;if(mode==="down"){const s=c.s[0];g0[Math.min(NY-1,Math.floor(s[2]/H*NY))*NX+Math.min(NX-1,Math.floor(s[1]/W*NX))]+=1}else{for(let i=1;i<c.s.length;i++){const s=c.s[i],dt=s[0]-c.s[i-1][0];g0[Math.min(NY-1,Math.max(0,Math.floor(s[2]/H*NY)))*NX+Math.min(NX-1,Math.max(0,Math.floor(s[1]/W*NX)))]+=dt}}}
const [g,w,h]=ctx(cv);g.fillStyle=css("--bg");g.fillRect(0,0,w,h);const mx=Math.max(...g0)||1,cw=w/NX,ch=h/NY;
for(let j=0;j<NY;j++)for(let i=0;i<NX;i++){const v=g0[j*NX+i];if(!v)continue;g.fillStyle=ramp[Math.min(6,Math.floor(Math.sqrt(v/mx)*6.99))];g.fillRect(i*cw,j*ch,cw-0.5,ch-0.5)}
g.strokeStyle=css("--rule");g.strokeRect(0.5,0.5,w-1,h-1);box.querySelector(".note").textContent=`${name} · ${n} contacts`;
cv.onmousemove=e=>{const r=cv.getBoundingClientRect(),i=Math.floor((e.clientX-r.left)/r.width*NX),j=Math.floor((e.clientY-r.top)/r.height*NY),v=g0[j*NX+i]||0;showTip(e,`${(i/NX*W).toFixed(0)}–${((i+1)/NX*W).toFixed(0)} mm across, ${(j/NY*H).toFixed(0)} mm down<br><b>${mode==="down"?v+" touch-downs":(v/1000).toFixed(2)+" s dwell"}</b>`)};cv.onmouseleave=hideTip}}
function force(){const cv=document.getElementById("force"),[g,w,h]=ctx(cv),pad={l:44,r:12,t:10,b:28},T=600;const cs=sess().contacts;
const X=t=>pad.l+t/T*(w-pad.l-pad.r),Y=f=>h-pad.b-f*(h-pad.t-pad.b);const fmax=Math.max(0.05,...cs.flatMap(c=>c.s.filter(s=>s[0]<=T).map(s=>s[3])));const Yn=f=>Y(f/fmax);
g.strokeStyle=css("--rule");g.fillStyle=css("--ink3");g.font="12px Figtree,system-ui";g.lineWidth=1;
for(let i=0;i<=4;i++){const y=Yn(fmax*i/4);g.beginPath();g.moveTo(pad.l,y);g.lineTo(w-pad.r,y);g.stroke();g.fillText((fmax*i/4).toFixed(2),4,y+4)}
for(let t=0;t<=T;t+=100){g.fillText(t+" ms",X(t)-14,h-8)}
const col=Object.fromEntries(CL.map(k=>[k,css("--"+k)]));g.globalAlpha=0.22;g.lineWidth=1;
for(const c of cs){g.strokeStyle=col[c.c];g.beginPath();c.s.filter(s=>s[0]<=T).forEach((s,i)=>i?g.lineTo(X(s[0]),Yn(s[3])):g.moveTo(X(s[0]),Yn(s[3])));g.stroke()}
g.globalAlpha=1;g.lineWidth=2.5;for(const k of CL){const bins=[];for(let t=0;t<=T;t+=20){const v=cs.filter(c=>c.c===k).map(c=>{const s=c.s.find(s=>s[0]>=t);return s&&s[0]<t+20?s[3]:null}).filter(v=>v!==null).sort((a,b)=>a-b);if(v.length>2)bins.push([t,v[v.length>>1]])}
g.strokeStyle=col[k];g.beginPath();bins.forEach(([t,v],i)=>i?g.lineTo(X(t),Yn(v)):g.moveTo(X(t),Yn(v)));g.stroke()}
legend(document.getElementById("lgForce"),CL.map(k=>[NAME[k],col[k]]));
cv.onmousemove=e=>{const r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;let best=null,bd=1e9;for(const c of cs)for(const s of c.s){if(s[0]>T)break;const d=(X(s[0])-mx)**2+(Yn(s[3])-my)**2;if(d<bd){bd=d;best=[c,s]}}
if(best&&bd<400){const [c,s]=best;showTip(e,`<b>${NAME[c.c]}${c.l?" · "+c.l:""}</b><br>${s[0]} ms · force ${s[3].toFixed(3)}<br>contact ${c.cid} at ${c.t.toFixed(2)} s`)}else hideTip()};cv.onmouseleave=hideTip}
function timeline(){const span=+document.getElementById("tlSpan").value,cv=document.getElementById("tl"),[g,w,h]=ctx(cv),s=sess(),pad={l:8,r:8,t:26,b:24};
const t1=tl0+span,X=t=>pad.l+(t-tl0)/span*(w-pad.l-pad.r);g.font="12px Figtree,system-ui";
document.getElementById("tlRange").textContent=`${tl0.toFixed(1)}–${t1.toFixed(1)} s`;
g.fillStyle=css("--bg");for(const sc of s.scenarios){if(sc.t1<tl0||sc.t0>t1)continue;const xa=X(Math.max(sc.t0,tl0)),xb=X(Math.min(sc.t1,t1));g.fillRect(xa,pad.t,xb-xa,h-pad.t-pad.b);if(g.measureText(sc.s).width<xb-xa-4){g.fillStyle=css("--ink3");g.fillText(sc.s,xa+3,h-8)}g.fillStyle=css("--bg")}
g.strokeStyle=css("--ink2");g.lineWidth=1;for(const k of s.keys){if(k<tl0||k>t1)continue;g.beginPath();g.moveTo(X(k)+.5,6);g.lineTo(X(k)+.5,18);g.stroke()}
const lanes=[],cs=s.contacts.filter(c=>c.t+c.d/1000>=tl0&&c.t<=t1),lh=14,hit=[];
for(const c of cs){let L=lanes.findIndex(e=>e<c.t);if(L<0){L=lanes.length;lanes.push(0)}lanes[L]=c.t+c.d/1000+0.02;const y=pad.t+6+L*(lh+4);if(y>h-pad.b-lh)continue;
const x0=X(Math.max(tl0,c.t)),x1=Math.max(x0+3,X(Math.min(t1,c.t+c.d/1000)));g.fillStyle=css("--"+c.c);g.beginPath();g.roundRect(x0,y,x1-x0,lh,4);g.fill();
if(c.fw!==null){const xf=X(c.t+c.fw/1000);g.save();g.beginPath();g.rect(Math.max(x0,xf),y,x1-Math.max(x0,xf),lh);g.clip();g.strokeStyle=css("--surface");g.lineWidth=2;for(let xx=xf-lh;xx<x1;xx+=6){g.beginPath();g.moveTo(xx,y+lh);g.lineTo(xx+lh,y);g.stroke()}g.restore()}
g.fillStyle=css("--ink");if(c.li==="palm"||c.li==="thumb"){const xm=X(c.t+(c.lim??0)/1000);g.fillText(c.li==="palm"?"◆":"▲",xm-4,y+11)}hit.push([x0,x1,y,y+lh,c])}
g.strokeStyle=css("--mark");g.lineWidth=2;for(const m of s.marks){if(m.t<tl0||m.t-m.w>t1)continue;const a=X(m.t-m.w),b=X(m.t);g.beginPath();g.moveTo(a,pad.t-2);g.lineTo(a,pad.t-8);g.lineTo(b,pad.t-8);g.lineTo(b,pad.t-2);g.stroke();g.fillStyle=css("--mark");g.fillText(m.m,b+3,pad.t-4)}
legend(document.getElementById("lgTl"),CL.map(k=>[NAME[k],css("--"+k)]));
cv.onmousemove=e=>{const r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top,f=hit.find(([a,b,c,d])=>mx>=a-2&&mx<=b+2&&my>=c-2&&my<=d+2);
if(f){const c=f[4];showTip(e,`<b>${NAME[c.c]}${c.l?" · "+c.l:""}</b><br>${c.d} ms from ${c.t.toFixed(2)} s · contact ${c.cid}<br>firmware: ${c.fw===null?"confident":"flagged at "+c.fw.toFixed(0)+" ms"}<br>libinput: ${c.li}${c.lim!==null?" at "+c.lim.toFixed(0)+" ms":""}${c.m.length?"<br>marked: "+c.m.join(", "):""}${c.sc?"<br>trial: "+c.sc:""}`)}else hideTip()};cv.onmouseleave=hideTip}
function agree(){const cs=sess().contacts,rows=["intended","accidental","unlabelled"],cols=[["fw ok","li finger"],["fw ok","li thumb/palm"],["fw flagged","li finger"],["fw flagged","li thumb/palm"]];
const k=c=>`${c.fw===null?"fw ok":"fw flagged"}|${c.li==="finger"||c.li==="thumb-suspect"?"li finger":"li thumb/palm"}`;
let t=`<tr><th>Label</th>${cols.map(c=>`<th>${c[0]} · ${c[1]}</th>`).join("")}<th>Total</th></tr>`;
for(const r of rows){const sub=cs.filter(c=>c.c===r);t+=`<tr><td><span class="sw" style="background:${css("--"+r)}"></span>${NAME[r]}</td>${cols.map(cc=>`<td>${sub.filter(c=>k(c)===cc.join("|")).length}</td>`).join("")}<td>${sub.length}</td></tr>`}
document.getElementById("agree").innerHTML=t;
const lab=cs.filter(c=>c.c!=="unlabelled"),acc=lab.filter(c=>c.c==="accidental"),int=lab.filter(c=>c.c==="intended");
const rate=(a,f)=>a.length?(100*a.filter(f).length/a.length).toFixed(0)+"%":"–";const med=a=>{a=a.filter(v=>v!==null).sort((x,y)=>x-y);return a.length?a[a.length>>1].toFixed(0)+" ms":"–"};
document.getElementById("timing").innerHTML=`<tr><th></th><th>Caught (accidental)</th><th>False alarms (intended)</th><th>Median time to decide</th></tr>
<tr><td>Firmware confidence</td><td>${rate(acc,c=>c.fw!==null)}</td><td>${rate(int,c=>c.fw!==null)}</td><td>${med(acc.map(c=>c.fw))}</td></tr>
<tr><td>libinput</td><td>${rate(acc,c=>c.li==="palm"||c.li==="thumb")}</td><td>${rate(int,c=>c.li==="palm"||c.li==="thumb")}</td><td>${med(acc.map(c=>c.lim))}</td></tr>
<tr><td>Either</td><td>${rate(acc,c=>c.fw!==null||c.li==="palm"||c.li==="thumb")}</td><td>${rate(int,c=>c.fw!==null||c.li==="palm"||c.li==="thumb")}</td><td></td></tr>`}
let sortKey="t",sortDir=1;function table(){const cs=[...sess().contacts];const cols=[["t","At, s",c=>c.t.toFixed(2)],["c","Class",c=>NAME[c.c]],["l","Label",c=>c.l],["d","ms",c=>c.d],["fw","Firmware",c=>c.fw===null?"ok":c.fw.toFixed(0)+" ms"],["li","libinput",c=>c.li],
["edge","Edge mm",c=>(c.f.edge_min_mm??0).toFixed(1)],["fmax","Force max",c=>(c.f.f_max??0).toFixed(3)],["net","Moved mm",c=>(c.f.net_mm??0).toFixed(1)],["key","Since key ms",c=>Math.round(c.f.since_key_ms??0)],["oth","Others",c=>c.f.others_at_down??0]];
const val={edge:c=>c.f.edge_min_mm,fmax:c=>c.f.f_max,net:c=>c.f.net_mm,key:c=>c.f.since_key_ms,oth:c=>c.f.others_at_down};
cs.sort((a,b)=>{const f=val[sortKey]||(c=>c[sortKey]??"");const x=f(a),y=f(b);return (x>y?1:x<y?-1:0)*sortDir});
const el=document.getElementById("ctab");el.innerHTML=`<tr>${cols.map(c=>`<th data-k="${c[0]}">${c[1]}</th>`).join("")}</tr>`+cs.slice(0,400).map(c=>`<tr>${cols.map(k=>`<td>${k[2](c)}</td>`).join("")}</tr>`).join("");
el.querySelectorAll("th").forEach(th=>th.onclick=()=>{sortDir=sortKey===th.dataset.k?-sortDir:1;sortKey=th.dataset.k;table()})}
function all(){stats();heat();force();timeline();agree();table()}
const pick=document.getElementById("pick");if(D.sessions.length>1){const sel=document.createElement("select");D.sessions.forEach((s,i)=>sel.add(new Option(s.stats.session,i)));sel.onchange=()=>{S=+sel.value;tl0=0;all()};pick.appendChild(sel)}
const hl=document.getElementById("hmLabel");hl.add(new Option("By class",""));[...new Set(D.sessions.flatMap(s=>s.contacts.map(c=>c.l)).filter(Boolean))].sort().forEach(l=>hl.add(new Option(l,l)));
document.getElementById("hmMode").onchange=heat;hl.onchange=heat;document.getElementById("tlSpan").onchange=timeline;
document.getElementById("tlPrev").onclick=()=>{tl0=Math.max(0,tl0-+document.getElementById("tlSpan").value/2);timeline()};
document.getElementById("tlNext").onclick=()=>{tl0+=+document.getElementById("tlSpan").value/2;timeline()};
matchMedia("(prefers-color-scheme: dark)").addEventListener("change",all);addEventListener("resize",()=>{clearTimeout(window._r);window._r=setTimeout(all,150)});
if(!D.sessions.length){document.querySelector("main").innerHTML="<h1>Touch Lab</h1><p class='sub'>No sessions yet.</p>"}else all();
</script></body></html>
"""
