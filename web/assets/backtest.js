// Backtest a margined total-return trade on a Serention index series (client-side).
Chart.defaults.color = "#8a97a8"; Chart.defaults.borderColor = "#1f2733";
const C = {accent:"#e23b4e", green:"#22c55e", blue:"#3b82f6", muted:"#8a97a8"};

let SERIES = [];
let mainChart, marginChart;

fetch("data/auto-subprime.json").then(r => r.json()).then(d => {
  SERIES = d.series;
  const ds = SERIES.map(s => s.date.slice(0, 7));
  const lo = ds[0], hi = ds[ds.length - 1];
  const e = document.getElementById("entry"), x = document.getElementById("exit");
  e.min = x.min = lo; e.max = x.max = hi; e.value = "2022-01"; x.value = hi;
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
  for (let k = 0; k < 200; k++) {
    const mid = (lo + hi) / 2, fm = npv(mid);
    if (Math.abs(fm) < 1e-7) return mid;
    if (flo * fm < 0) hi = mid; else { lo = mid; flo = fm; }
  }
  return (lo + hi) / 2;
}

function run() {
  const field = document.getElementById("series").value;
  const entry = document.getElementById("entry").value, exit = document.getElementById("exit").value;
  const notional = +document.getElementById("notional").value;
  const imPct = +document.getElementById("im").value / 100;
  const mmPct = +document.getElementById("mm").value / 100;
  const gas = +document.getElementById("gas").value || 0;
  const status = document.getElementById("status");

  if (entry >= exit) { status.textContent = "Exit month must be after entry month."; return; }
  if (mmPct >= imPct) { status.textContent = "Maintenance margin must be below initial margin."; return; }
  const pts = SERIES.filter(s => s.date.slice(0, 7) >= entry && s.date.slice(0, 7) <= exit && s[field] != null)
                    .map(s => ({ym: s.date.slice(0, 7), p: s[field]}));
  if (pts.length < 2) { status.textContent = "Not enough data in that window for this series."; return; }
  const P0 = pts[0].p, IM = notional * imPct, MM = notional * mmPct;
  if (!(P0 > 0)) { status.textContent = "Entry index value must be positive."; return; }

  // Monthly variation margin (long), fixed denominator so it telescopes to total.
  let cumL = 0, rows = [], lCall = -1, sCall = -1;
  pts.forEach((pt, i) => {
    const mtm = i === 0 ? 0 : notional * (pt.p - pts[i - 1].p) / P0;
    cumL += mtm;
    const eqL = IM + cumL, eqS = IM - cumL;     // account equity each side
    if (lCall < 0 && i > 0 && eqL < MM) lCall = i;
    if (sCall < 0 && i > 0 && eqS < MM) sCall = i;
    rows.push({ym: pt.ym, p: pt.p, mtm, cumL, eqL, eqS});
  });
  const totalL = notional * (pts[pts.length - 1].p - P0) / P0;

  // IRR cash flows (long): -IM-gas at entry, +VM each month, +IM at exit.
  const cf = rows.map((r, i) => i === 0 ? -(IM + gas) : r.mtm); cf[cf.length - 1] += IM;
  const irrL = irrMonthly(cf), irrLann = irrL == null ? null : Math.pow(1 + irrL, 12) - 1;
  const cfS = rows.map((r, i) => i === 0 ? -(IM + gas) : -r.mtm); cfS[cfS.length - 1] += IM;
  const irrS = irrMonthly(cfS), irrSann = irrS == null ? null : Math.pow(1 + irrS, 12) - 1;

  const m = rows.slice(1).map(r => r.mtm);
  const mean = m.reduce((a, b) => a + b, 0) / m.length;
  const vol = Math.sqrt(m.reduce((a, b) => a + (b - mean) ** 2, 0) / m.length);
  const maxDD = arr => { let pk = arr[0], dd = 0; arr.forEach(v => { pk = Math.max(pk, v); dd = Math.min(dd, v - pk); }); return dd; };
  const cumArr = rows.map(r => r.cumL);

  renderMetrics({
    notional, IM, MM, imPct, mmPct, gas, months: rows.length - 1, field,
    P0, Pn: pts[pts.length - 1].p, vol,
    totalL: totalL - gas, totalS: -totalL - gas,
    irrLann, irrSann, ddL: maxDD(cumArr), ddS: maxDD(cumArr.map(v => -v)),
    lCall: lCall < 0 ? null : rows[lCall].ym, sCall: sCall < 0 ? null : rows[sCall].ym
  });
  renderCharts(rows, IM, MM);
  renderTable(rows, MM);
  document.getElementById("results").style.display = "";
  status.textContent = `${rows.length - 1} months · ${monthLabel(entry)} → ${monthLabel(exit)} · index ${P0.toFixed(1)} → ${pts[pts.length-1].p.toFixed(1)} (${fmtPct((pts[pts.length-1].p-P0)/P0)})`;
}

function renderMetrics(o) {
  const ror = x => o.IM ? x / o.IM : null;
  const col = (side, total, irr, dd, call) => `
    <div class="col ${side}">
      <div class="h">${side === "long" ? "LONG (profits if credit worsens)" : "SHORT (profits if credit improves)"}</div>
      <div class="r"><span class="k">Total PnL (net of gas)</span><span class="v ${cls(total)}">${fmt$(total)}</span></div>
      <div class="r"><span class="k">Return on margin</span><span class="v ${cls(total)}">${fmtPct(ror(total))}</span></div>
      <div class="r"><span class="k">IRR (annualized)</span><span class="v ${cls(irr)}">${fmtPct(irr)}</span></div>
      <div class="r"><span class="k">Max drawdown (cum.)</span><span class="v neg">${fmt$(dd)}</span></div>
      <div class="r"><span class="k">Margin call?</span><span class="v ${call ? "neg" : "pos"}">${call ? "Yes — " + monthLabel(call) : "No"}</span></div>
    </div>`;
  document.getElementById("metrics").innerHTML = col("long", o.totalL, o.irrLann, o.ddL, o.lCall) + col("short", o.totalS, o.irrSann, o.ddS, o.sCall);

  const shared = `Notional <b>${fmt$(o.notional)}</b> ·
    initial margin <b>${fmt$(o.IM)}</b> (${(o.imPct*100)|0}% → ${(1/o.imPct).toFixed(1)}× leverage) ·
    maintenance margin <b>${fmt$(o.MM)}</b> (${(o.mmPct*100)|0}%) ·
    gas/round-trip <b>${fmt$(o.gas)}</b> · ${o.months} months ·
    index ${o.field} <b>${o.P0.toFixed(1)} → ${o.Pn.toFixed(1)}</b> · monthly PnL vol <b>${fmt$(o.vol)}</b>.
    <br><span style="color:#8a97a8">A margin call is triggered the month account equity (margin ± PnL) falls below the maintenance margin — top up or be liquidated.</span>`;
  let n = document.getElementById("sharednote");
  if (!n) { n = document.createElement("div"); n.id = "sharednote"; n.className = "note"; document.getElementById("metrics").after(n); }
  n.innerHTML = shared;
}

function renderCharts(rows, IM, MM) {
  const labels = rows.map(r => monthLabel(r.ym));
  if (mainChart) mainChart.destroy(); if (marginChart) marginChart.destroy();
  mainChart = new Chart(document.getElementById("cMain"), {
    data: {labels, datasets: [
      {type:"line", label:"Index level", data: rows.map(r => r.p), borderColor: C.muted, borderWidth: 2, pointRadius: 0, yAxisID: "y1", tension: .2},
      {type:"line", label:"Long cumulative PnL", data: rows.map(r => r.cumL), borderColor: C.green, borderWidth: 2, pointRadius: 0, tension: .2},
      {type:"line", label:"Short cumulative PnL", data: rows.map(r => -r.cumL), borderColor: C.accent, borderWidth: 2, pointRadius: 0, tension: .2},
    ]},
    options: chartOpts("PnL ($)", {y1: {position:"right", title:{display:true,text:"Index level"}, grid:{drawOnChartArea:false}}})
  });
  // Account equity vs initial & maintenance margin (both sides).
  marginChart = new Chart(document.getElementById("cMargin"), {
    data: {labels, datasets: [
      {type:"line", label:"Long equity", data: rows.map(r => r.eqL), borderColor: C.green, borderWidth: 2, pointRadius: 0, tension: .2},
      {type:"line", label:"Short equity", data: rows.map(r => r.eqS), borderColor: C.accent, borderWidth: 2, pointRadius: 0, tension: .2},
      {type:"line", label:"Initial margin", data: rows.map(() => IM), borderColor: C.blue, borderWidth: 1, borderDash:[6,4], pointRadius: 0},
      {type:"line", label:"Maintenance margin (call below)", data: rows.map(() => MM), borderColor: "#f59e0b", borderWidth: 1.5, borderDash:[3,3], pointRadius: 0},
    ]},
    options: chartOpts("Account equity ($)", {})
  });
}

function chartOpts(yTitle, extra) {
  const scales = {x:{grid:{display:false}, ticks:{maxTicksLimit:12, autoSkip:true}}, y:{title:{display:true, text:yTitle}, grid:{color:"#161d28"}}, ...extra};
  return {responsive:true, maintainAspectRatio:false, interaction:{mode:"index",intersect:false}, plugins:{legend:{labels:{boxWidth:12,usePointStyle:true,padding:12}}}, scales};
}

function renderTable(rows, MM) {
  const flag = (eq) => eq < MM ? ' style="background:rgba(245,158,11,.12)"' : "";
  const head = `<thead><tr><th>Month</th><th>Index</th><th>Long PnL</th><th>Long cum.</th><th>Long equity</th>
    <th>Short PnL</th><th>Short cum.</th><th>Short equity</th></tr></thead>`;
  const body = rows.map(r => `<tr>
    <td>${monthLabel(r.ym)}</td><td>${r.p.toFixed(2)}</td>
    <td class="${cls(r.mtm)}">${fmt$(r.mtm)}</td><td class="${cls(r.cumL)}">${fmt$(r.cumL)}</td><td${flag(r.eqL)}>${fmt$(r.eqL)}</td>
    <td class="${cls(-r.mtm)}">${fmt$(-r.mtm)}</td><td class="${cls(-r.cumL)}">${fmt$(-r.cumL)}</td><td${flag(r.eqS)}>${fmt$(r.eqS)}</td></tr>`).join("");
  document.getElementById("tbl").innerHTML = head + "<tbody>" + body + "</tbody>";
}
