"""
Explainability dashboard generator (Phase 3).

Produces a single self-contained HTML file (inline SVG + JS, no external
dependencies) that visualises every decision: the 90-day balance forecast with
and without the recommended plan, the minimum-balance floor, payment markers,
the persona fingerprint, and the decision explanation. This makes the agent
auditable -- you can see exactly which day the balance would breach the minimum.

Usage:  python code/report.py [--sample] [--out dashboard.html]
"""

from __future__ import annotations

import argparse
import json
import os

import config
from agent import FinancialAgent

_STATUS_COLOR = {
    "affordable_now": "#16a34a",
    "affordable_with_plan": "#2563eb",
    "affordable_later": "#d97706",
    "not_affordable": "#dc2626",
}

_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Buy or Wait — Decisions</title>
<style>
:root{--bg:#f7f8fa;--card:#fff;--ink:#0f172a;--mut:#64748b;--line:#e2e8f0;--accent:#2563eb;}
@media(prefers-color-scheme:dark){:root{--bg:#0b1220;--card:#131c2e;--ink:#e5edf7;--mut:#93a4bd;--line:#25324a;--accent:#60a5fa;}}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
.wrap{display:flex;height:100vh;overflow:hidden}
.side{width:300px;min-width:260px;border-right:1px solid var(--line);overflow:auto;background:var(--card)}
.side h1{font-size:15px;margin:14px 16px 4px}.side .sub{color:var(--mut);font-size:12px;margin:0 16px 10px}
.item{padding:9px 16px;border-top:1px solid var(--line);cursor:pointer}
.item:hover{background:rgba(37,99,235,.06)}.item.active{background:rgba(37,99,235,.12)}
.item .rid{font-weight:600}.badge{display:inline-block;padding:1px 8px;border-radius:999px;color:#fff;font-size:11px}
.main{flex:1;overflow:auto;padding:22px 26px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:16px}
.k{color:var(--mut);font-size:12px}.v{font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px}
.chip{background:rgba(37,99,235,.08);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.expl{font-size:14px;margin-top:6px}.mut{color:var(--mut)}
svg{width:100%;height:280px;display:block}
.legend{display:flex;gap:16px;font-size:12px;color:var(--mut);margin-top:6px}
.sw{display:inline-block;width:11px;height:3px;vertical-align:middle;margin-right:5px}
</style></head><body><div class="wrap">
<div class="side"><h1>Buy or Wait?</h1><div class="sub" id="count"></div><div id="list"></div></div>
<div class="main" id="main"></div></div>
<script>const DATA=%DATA%;const SC=%SC%;
function money(n){return (Math.round(n*100)/100).toLocaleString()}
function chart(b){const W=760,H=280,pad=44;const base=b.series_base,plan=b.series_plan;
 const xs=base.map((_,i)=>i);const all=base.concat(plan).map(p=>p.b).concat([b.min_balance]);
 let mn=Math.min(...all),mx=Math.max(...all);if(mn===mx){mn-=1;mx+=1}const rng=mx-mn;mn-=rng*0.08;mx+=rng*0.08;
 const X=i=>pad+(W-pad-10)*i/(base.length-1);const Y=v=>H-pad-(H-pad-14)*(v-mn)/(mx-mn);
 const poly=s=>s.map((p,i)=>X(i)+","+Y(p.b)).join(" ");
 const minY=Y(b.min_balance);
 let pays="";const start=new Date(base[0].d);
 (b.plan_dates||[]).forEach(pd=>{const di=Math.round((new Date(pd)-start)/864e5);if(di>=0&&di<base.length){const pv=plan[di];pays+=`<circle cx="${X(di)}" cy="${Y(pv.b)}" r="4" fill="var(--accent)"/>`}});
 const ticks=[0,30,60,90].map(i=>`<text x="${X(Math.min(i,base.length-1))}" y="${H-pad+16}" fill="var(--mut)" font-size="11" text-anchor="middle">${base[Math.min(i,base.length-1)].d.slice(5)}</text>`).join("");
 return `<svg viewBox="0 0 ${W} ${H}">
  <line x1="${pad}" y1="${minY}" x2="${W-10}" y2="${minY}" stroke="#dc2626" stroke-dasharray="5 4" stroke-width="1.5"/>
  <text x="${W-12}" y="${minY-5}" fill="#dc2626" font-size="11" text-anchor="end">min ${money(b.min_balance)}</text>
  <polyline fill="none" stroke="var(--mut)" stroke-width="1.6" opacity=".55" points="${poly(base)}"/>
  <polyline fill="none" stroke="var(--accent)" stroke-width="2.2" points="${poly(plan)}"/>
  ${pays}${ticks}
 </svg>
 <div class="legend"><span><i class="sw" style="background:var(--mut)"></i>balance, no purchase</span>
 <span><i class="sw" style="background:var(--accent)"></i>with recommended plan</span>
 <span><i class="sw" style="background:#dc2626"></i>minimum balance</span></div>`;}
function render(i){const b=DATA[i];const r=b.row;const cur=b.home_currency;
 document.querySelectorAll('.item').forEach((e,j)=>e.classList.toggle('active',j===i));
 const P=b.persona;
 document.getElementById('main').innerHTML=`
 <div class="card"><div class="k">${r.request_id} · ${b.user_id} · ${cur}</div>
  <div style="font-size:16px;margin:4px 0 10px">${b.request_text||''}</div>
  <span class="badge" style="background:${SC[r.affordability_status]||'#555'}">${r.affordability_status}</span>
  <span class="badge" style="background:#334155;margin-left:6px">${r.recommended_payment_method}</span>
  <div class="expl">${r.decision_explanation}</div></div>
 <div class="card">${chart(b)}</div>
 <div class="card"><div class="grid">
  <div class="chip"><div class="k">Amount safe today</div><div class="v">${cur} ${r.amount_safe_to_pay}</div></div>
  <div class="chip"><div class="k">Requested</div><div class="v">${cur} ${money(b.requested)}</div></div>
  <div class="chip"><div class="k">Earliest full</div><div class="v">${r.earliest_date_for_full_payment||'—'}</div></div>
  <div class="chip"><div class="k">Deadline</div><div class="v">${b.deadline||'—'}</div></div>
  <div class="chip"><div class="k">Payment plan</div><div class="v" style="font-size:12px">${r.payment_plan}</div></div>
  <div class="chip"><div class="k">Spending changes</div><div class="v" style="font-size:12px">${r.spending_changes_needed}</div></div>
 </div></div>
 <div class="card"><div class="k" style="margin-bottom:6px">Persona fingerprint</div>
  <div class="v" style="font-size:13px">${P.fingerprint}</div>
  <div class="grid" style="margin-top:10px">
   <div class="chip"><div class="k">Monthly income</div><div class="v">${cur} ${money(P.monthly_income)}</div></div>
   <div class="chip"><div class="k">Essential/mo</div><div class="v">${cur} ${money(P.monthly_essential)}</div></div>
   <div class="chip"><div class="k">Flexible/mo</div><div class="v">${cur} ${money(P.monthly_flexible)}</div></div>
   <div class="chip"><div class="k">Runway</div><div class="v">${P.runway_months} mo</div></div>
   <div class="chip"><div class="k">DTI</div><div class="v">${Math.round(P.dti*100)}%</div></div>
   <div class="chip"><div class="k">Need vs want</div><div class="v">${P.need_vs_want}</div></div>
  </div></div>`;}
const list=document.getElementById('list');
DATA.forEach((b,i)=>{const d=document.createElement('div');d.className='item';
 d.innerHTML=`<div class="rid">${b.row.request_id} <span class="badge" style="background:${SC[b.row.affordability_status]||'#555'};float:right">${b.row.affordability_status.replace('affordable_','').replace('_',' ')}</span></div>
 <div class="mut" style="font-size:12px">${b.user_id} · ${b.home_currency} ${money(b.requested)}</div>`;
 d.onclick=()=>render(i);list.appendChild(d);});
document.getElementById('count').textContent=DATA.length+" requests";
render(0);
</script></body></html>"""


def build(bundles, out_path: str) -> str:
    # Attach plan payment dates for the chart markers.
    for b in bundles:
        plan = b["row"]["payment_plan"]
        b["plan_dates"] = ([p.split(":")[0] for p in plan.split("|")]
                           if plan and plan != "none" else [])
    html = (_HTML
            .replace("%DATA%", json.dumps(bundles, ensure_ascii=False))
            .replace("%SC%", json.dumps(_STATUS_COLOR)))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--out", default=os.path.join(config.REPO_ROOT, "dashboard.html"))
    args = ap.parse_args(argv)
    agent = FinancialAgent(use_sample_requests=args.sample)
    bundles = agent.explain_all()
    build(bundles, args.out)
    print(f"Wrote dashboard with {len(bundles)} requests to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
