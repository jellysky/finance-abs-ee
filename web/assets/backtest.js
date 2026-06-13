// Backtest a margined total-return trade on a Serention index series (client-side).
// Maintenance model: each side starts at initial margin; each month it's marked to
// market, and if equity falls below maintenance the holder tops up to initial margin.
// The top-up columns show the capital that must be injected to stay in (else liquidated).
Chart.defaults.color = "#c9b8f2"; Chart.defaults.borderColor = "rgba(201,184,242,0.18)";
const C = {accent:"#14b8c4", green:"#22c55e", blue:"#3b82f6", muted:"#c9b8f2", amber:"#f59e0b"};

let SERIES = [];
let mainChart, marginChart;

Promise.all([
  fetch("data/auto-subprime.json").then(r => r.json()),
  fetch("data/netyield.json").then(r => r.json()).catch(() => null),
]).then(([d, ny]) => {
  SERIES = d.series;
  if (ny && ny.series) {   // merge the net-yield index in by month
    const m = new Map(ny.series.map(s => [s.date, s]));
    SERIES.forEach(s => { const n = m.get(s.date); if (n) { s.net_yield = n.net_yield; s.net_yield_accrued = n.net_yield_accrued; } });
  }
  const ds = SERIES.map(s => s.date.slice(0, 7));
  const e = document.getElementById("entry"), x = document.getElementById("exit");
  e.min = x.min = ds[0]; e.max = x.max = ds[ds.length - 1]; e.value = "2022-01"; x.value = ds[ds.length - 1];
  document.getElementById("run").addEventListener("click", run);
});

const fmt$ = v => (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString(undefined, {maximumFractionDigits: 0});
const fmtPct = (v, d = 1) => (v == null || !isFinite(v) ? "—" : (v * 100).toFixed(d) + "%");
const cls = v => v > 0 ? "pos" : v < 0 ? "neg" : "";
const monthLabel = ym => { const [y, m] = ym.split("-"); return ["", "Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+m] + " " + y; };

function irrMonthly(cfs) {
  const npv = r => cfs.reduce((s, c, i) => s + c / Math.pow(1 + r, i), 0);
  let lo = -0.9999, hi = 5, flo = npv(lo);
  if (flo * npv(hi) > 0) return null;
  for (let k = 0; k < 200; k++) { const mid = (lo + hi) / 2, fm = npv(mid);
    if (Math.abs(fm) < 1e-7) return mid; if (flo * fm < 0) hi = mid; else { lo = mid; flo = fm; } }
  return (lo + hi) / 2;
}
const ann = r => r == null ? null : Math.pow(1 + r, 12) - 1;

function run() {
  const field = document.getElementById("series").value;
  const entry = document.getElementById("entry").value, exit = document.getElementById("exit").value;
  const notional = +document.getElementById("notional").value;
  const imPct = +document.getElementById("im").value / 100, mmPct = +document.getElementById("mm").value / 100;
  const gas = +document.getElementById("gas").value || 0;
  const status = document.getElementById("status");

  if (entry >= exit) { status.textContent = "Exit month must be after entry month."; return; }
  if (mmPct >= imPct) { status.textContent = "Maintenance margin must be below initial margin."; return; }
  const pts = SERIES.filter(s => s.date.slice(0,7) >= entry && s.date.slice(0,7) <= exit && s[field] != null)
                    .map(s => ({ym: s.date.slice(0,7), p: s[field]}));
  if (pts.length < 2) { status.textContent = "Not enough data in that window for this series."; return; }
  const P0 = pts[0].p, IM = notional * imPct, MM = notional * mmPct;
  if (!(P0 > 0)) { status.textContent = "Entry index value must be positive."; return; }

  let eqL = IM, eqS = IM, cumTopL = 0, cumTopS = 0, cumPnlL = 0, callsL = 0, callsS = 0, firstCallL = null, firstCallS = null;
  const rows = [], cfL = [], cfS = [];
  pts.forEach((pt, i) => {
    const mtmL = i === 0 ? 0 : notional * (pt.p - pts[i - 1].p) / P0, mtmS = -mtmL;
    cumPnlL += mtmL;
    eqL += mtmL; eqS += mtmS;
    const eqLpre = eqL, eqSpre = eqS;
    let topL = 0, topS = 0;
    if (i > 0 && eqL < MM) { topL = IM - eqL; eqL = IM; cumTopL += topL; callsL++; if (!firstCallL) firstCallL = pt.ym; }
    if (i > 0 && eqS < MM) { topS = IM - eqS; eqS = IM; cumTopS += topS; callsS++; if (!firstCallS) firstCallS = pt.ym; }
    cfL.push(i === 0 ? -(IM + gas) : -topL);
    cfS.push(i === 0 ? -(IM + gas) : -topS);
    rows.push({ym: pt.ym, p: pt.p, mtmL, mtmS, cumPnlL, eqLpre, eqSpre, topL, topS, capL: IM + cumTopL, capS: IM + cumTopS});
  });
  const totalL = notional * (pts[pts.length - 1].p - P0) / P0;
  const eqLclose = IM + totalL + cumTopL, eqSclose = IM - totalL + cumTopS;
  cfL[cfL.length - 1] += eqLclose; cfS[cfS.length - 1] += eqSclose;

  const m = rows.slice(1).map(r => r.mtmL), mean = m.reduce((a,b)=>a+b,0)/m.length;
  const vol = Math.sqrt(m.reduce((a,b)=>a+(b-mean)**2,0)/m.length);
  const maxDD = arr => { let pk = arr[0], dd = 0; arr.forEach(v => { pk = Math.max(pk,v); dd = Math.min(dd, v-pk); }); return dd; };
  const cum = rows.map(r => r.cumPnlL);

  renderMetrics({
    notional, IM, MM, imPct, mmPct, gas, months: rows.length - 1, field, P0, Pn: pts[pts.length-1].p, vol,
    totalL: totalL - gas, totalS: -totalL - gas, capL: IM + cumTopL, capS: IM + cumTopS,
    topL: cumTopL, topS: cumTopS, callsL, callsS, firstCallL, firstCallS,
    irrL: ann(irrMonthly(cfL)), irrS: ann(irrMonthly(cfS)), ddL: maxDD(cum), ddS: maxDD(cum.map(v => -v))
  });
  renderCharts(rows, IM);
  renderTable(rows, MM);
  document.getElementById("results").style.display = "";
  status.textContent = `${rows.length-1} months · ${monthLabel(entry)} → ${monthLabel(exit)} · index ${P0.toFixed(1)} → ${pts[pts.length-1].p.toFixed(1)} (${fmtPct((pts[pts.length-1].p-P0)/P0)})`;
}

function renderMetrics(o) {
  const col = (side, total, irr, cap, top, calls, first, dd) => `
    <div class="col ${side}">
      <div class="h">${side === "long" ? "LONG (profits if credit worsens)" : "SHORT (profits if credit improves)"}</div>
      <div class="r"><span class="k">Total PnL (net of gas)</span><span class="v ${cls(total)}">${fmt$(total)}</span></div>
      <div class="r"><span class="k">IRR (annualized)</span><span class="v ${cls(irr)}">${fmtPct(irr)}</span></div>
      <div class="r"><span class="k">Margin top-ups (total)</span><span class="v ${top>0?'neg':'pos'}">${fmt$(top)}</span></div>
      <div class="r"><span class="k">Margin calls</span><span class="v ${calls>0?'neg':'pos'}">${calls>0 ? calls+" (first "+monthLabel(first)+")" : "None"}</span></div>
      <div class="r"><span class="k">Total capital committed</span><span class="v">${fmt$(cap)}</span></div>
      <div class="r"><span class="k">Return on capital</span><span class="v ${cls(total)}">${fmtPct(total/cap)}</span></div>
      <div class="r"><span class="k">Max drawdown (cum.)</span><span class="v neg">${fmt$(dd)}</span></div>
    </div>`;
  document.getElementById("metrics").innerHTML =
    col("long", o.totalL, o.irrL, o.capL, o.topL, o.callsL, o.firstCallL, o.ddL) +
    col("short", o.totalS, o.irrS, o.capS, o.topS, o.callsS, o.firstCallS, o.ddS);
  const shared = `Notional <b>${fmt$(o.notional)}</b> · initial margin <b>${fmt$(o.IM)}</b>
    (${(o.imPct*100)|0}% → ${(1/o.imPct).toFixed(1)}× leverage) · maintenance margin <b>${fmt$(o.MM)}</b> (${(o.mmPct*100)|0}%) ·
    gas <b>${fmt$(o.gas)}</b> · ${o.months} months · index ${o.field} <b>${o.P0.toFixed(1)} → ${o.Pn.toFixed(1)}</b>.
    <br><span style="color:#c9b8f2">When account equity (margin + PnL) falls below the maintenance margin, the holder must top up to the initial margin to stay in — that month's injection is the <b>Margin top-up</b>. If they don't, the position is liquidated at that point instead.</span>`;
  let n = document.getElementById("sharednote");
  if (!n) { n = document.createElement("div"); n.id = "sharednote"; n.className = "note"; document.getElementById("metrics").after(n); }
  n.innerHTML = shared;
}

function renderCharts(rows, IM) {
  const labels = rows.map(r => monthLabel(r.ym));
  if (mainChart) mainChart.destroy(); if (marginChart) marginChart.destroy();
  mainChart = new Chart(document.getElementById("cMain"), {
    data: {labels, datasets: [
      {type:"line", label:"Index level", data: rows.map(r => r.p), borderColor: C.muted, borderWidth: 2, pointRadius: 0, yAxisID: "y1", tension: .2},
      {type:"line", label:"Long cumulative PnL", data: rows.map(r => r.cumPnlL), borderColor: C.green, borderWidth: 2, pointRadius: 0, tension: .2},
      {type:"line", label:"Short cumulative PnL", data: rows.map(r => -r.cumPnlL), borderColor: C.accent, borderWidth: 2, pointRadius: 0, tension: .2},
    ]},
    options: chartOpts("PnL ($)", {y1: {position:"right", title:{display:true,text:"Index level"}, grid:{drawOnChartArea:false}}})
  });
  marginChart = new Chart(document.getElementById("cMargin"), {
    data: {labels, datasets: [
      {type:"line", label:"Long capital posted (margin + top-ups)", data: rows.map(r => r.capL), borderColor: C.green, borderWidth: 2, pointRadius: 0, stepped: true},
      {type:"line", label:"Short capital posted (margin + top-ups)", data: rows.map(r => r.capS), borderColor: C.accent, borderWidth: 2, pointRadius: 0, stepped: true},
      {type:"line", label:"Initial margin", data: rows.map(() => IM), borderColor: C.blue, borderWidth: 1, borderDash:[6,4], pointRadius: 0},
    ]},
    options: chartOpts("Capital posted ($)", {})
  });
}

function chartOpts(yTitle, extra) {
  return {responsive:true, maintainAspectRatio:false, interaction:{mode:"index",intersect:false},
    plugins:{legend:{labels:{boxWidth:12,usePointStyle:true,padding:12}}},
    scales:{x:{grid:{display:false}, ticks:{maxTicksLimit:14, autoSkip:true}}, y:{title:{display:true,text:yTitle}, grid:{color:"rgba(201,184,242,0.14)"}}, ...extra}};
}

function renderTable(rows, MM) {
  const tu = t => t > 0 ? ` style="background:rgba(245,158,11,.16)"` : "";
  const head = `<thead><tr><th>Month</th><th>Index</th>
    <th>Long PnL</th><th>Long cum.</th><th>Long equity</th><th>Long top-up</th>
    <th>Short PnL</th><th>Short cum.</th><th>Short equity</th><th>Short top-up</th></tr></thead>`;
  const body = rows.map(r => `<tr>
    <td>${monthLabel(r.ym)}</td><td>${r.p.toFixed(2)}</td>
    <td class="${cls(r.mtmL)}">${fmt$(r.mtmL)}</td><td class="${cls(r.cumPnlL)}">${fmt$(r.cumPnlL)}</td>
    <td>${fmt$(r.eqLpre)}</td><td${tu(r.topL)}>${r.topL>0?fmt$(r.topL):"—"}</td>
    <td class="${cls(r.mtmS)}">${fmt$(r.mtmS)}</td><td class="${cls(-r.cumPnlL)}">${fmt$(-r.cumPnlL)}</td>
    <td>${fmt$(r.eqSpre)}</td><td${tu(r.topS)}>${r.topS>0?fmt$(r.topS):"—"}</td></tr>`).join("");
  document.getElementById("tbl").innerHTML = head + "<tbody>" + body + "</tbody>";
}
