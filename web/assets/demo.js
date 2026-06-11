// Animated two-party trade + hedging demo on a Serention index (client-side).
Chart.defaults.color = "#8a97a8"; Chart.defaults.borderColor = "#1f2733";
const C = {accent:"#e23b4e", green:"#22c55e", blue:"#3b82f6", muted:"#8a97a8", amber:"#f59e0b"};
const $ = id => document.getElementById(id);

let SERIES = [], anim = null, tradeChart, hedgeChart, MODEL = null;

const fmt$ = v => (v < 0 ? "-$" : "$") + Math.abs(Math.round(v)).toLocaleString();
const fmtM = v => (v < 0 ? "-$" : "$") + (Math.abs(v) / 1e6).toFixed(2) + "M";
const mlabel = ym => { const [y, m] = ym.split("-"); return ["", "Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+m] + " " + y; };
const cl = v => v > 0 ? "pos" : v < 0 ? "neg" : "";

fetch("data/auto-subprime.json").then(r => r.json()).then(d => {
  SERIES = d.series;
  const lo = SERIES[0].date.slice(0, 7), hi = SERIES[SERIES.length - 1].date.slice(0, 7);
  ["entry", "exit"].forEach(id => { $(id).min = lo; $(id).max = hi; });
  $("entry").value = "2024-01"; $("exit").value = hi;
  $("run").onclick = run; $("reset").onclick = reset;
});

function compute() {
  const field = $("series").value, entry = $("entry").value, exit = $("exit").value;
  const notional = +$("notional").value, lev = +$("lev").value;
  const V0 = +$("pool").value, beta = +$("beta").value, hr = +$("hr").value / 100;
  const pts = SERIES.filter(s => s.date.slice(0,7) >= entry && s.date.slice(0,7) <= exit && s[field] != null)
                    .map(s => ({ym: s.date.slice(0,7), p: s[field]}));
  if (pts.length < 2) return null;
  const P0 = pts[0].p, im = notional / lev, maint = notional * 0.10;
  const hedgeNotional = hr * beta * V0;
  // Maintenance model: each side tops up to initial margin when equity < maintenance.
  let eqL = im, eqS = im, cumTopL = 0, cumTopS = 0, callsL = 0, callsS = 0, firstCallL = null, firstCallS = null, prev = P0;
  const rows = pts.map((pt, i) => {
    const ret = (pt.p - P0) / P0;
    const mtm = i === 0 ? 0 : notional * (pt.p - prev) / P0; prev = pt.p;
    eqL += mtm; eqS += -mtm;
    let topL = 0, topS = 0;
    if (i > 0 && eqL < maint) { topL = im - eqL; eqL = im; cumTopL += topL; callsL++; if (!firstCallL) firstCallL = pt.ym; }
    if (i > 0 && eqS < maint) { topS = im - eqS; eqS = im; cumTopS += topS; callsS++; if (!firstCallS) firstCallS = pt.ym; }
    const unhedged = V0 * (1 - beta * ret), hedge = hedgeNotional * ret;
    return {ym: pt.ym, p: pt.p, ret, lPnl: notional * ret, sPnl: -notional * ret,
            lEq: eqL, sEq: eqS, topL, topS, cumTopL, cumTopS, unhedged, hedge, net: unhedged + hedge};
  });
  return {rows, im, maint, notional, V0, callsL, callsS, firstCallL, firstCallS, field, lev};
}

function run() {
  reset();
  MODEL = compute();
  if (!MODEL) { $("status").textContent = "Not enough data in that window."; return; }
  const {rows} = MODEL;
  $("lIM").textContent = $("sIM").textContent = fmt$(MODEL.im);
  $("lMM").textContent = $("sMM").textContent = fmt$(MODEL.maint);
  buildCharts(rows);
  let i = 0, speed = +$("speed").value;
  $("run").disabled = true;
  step(0);
  anim = setInterval(() => {
    i++;
    if (i >= rows.length) { clearInterval(anim); anim = null; $("run").disabled = false; finish(); return; }
    step(i);
  }, speed);
}

function step(i) {
  const r = MODEL.rows[i];
  $("monthflag").textContent = mlabel(r.ym) + "  ·  index " + r.p.toFixed(1);
  // traders
  setTrader("l", r.lPnl, r.lEq, r.topL, r.cumTopL);
  setTrader("s", r.sPnl, r.sEq, r.topS, r.cumTopS);
  // hedge
  $("pUn").textContent = fmtM(r.unhedged);
  $("pHedge").innerHTML = `<span class="${cl(r.hedge)}">${fmtM(r.hedge)}</span>`;
  $("pNet").textContent = fmtM(r.net);
  $("pSaved").innerHTML = `<span class="pos">${fmtM(r.net - r.unhedged)}</span>`;
  // advance charts
  const sl = (a) => MODEL.rows.slice(0, i + 1).map(a);
  tradeChart.data.labels = sl(x => mlabel(x.ym));
  tradeChart.data.datasets[0].data = sl(x => x.p);
  tradeChart.data.datasets[1].data = sl(x => x.lEq);
  tradeChart.data.datasets[2].data = sl(x => x.sEq);
  tradeChart.data.datasets[3].data = sl(() => MODEL.im);
  tradeChart.data.datasets[4].data = sl(() => MODEL.maint);
  tradeChart.update("none");
  hedgeChart.data.labels = sl(x => mlabel(x.ym));
  hedgeChart.data.datasets[0].data = sl(x => x.unhedged);
  hedgeChart.data.datasets[1].data = sl(x => x.net);
  hedgeChart.data.datasets[2].data = sl(x => x.hedge);
  hedgeChart.update("none");
}

function setTrader(k, pnl, eq, topThis, cumTop) {
  $(k + "Pnl").innerHTML = `<span class="${cl(pnl)}">${fmt$(pnl)}</span>`;
  $(k + "Eq").textContent = fmt$(eq);
  $(k + "Stat").innerHTML = topThis > 0
    ? `<span class="liq">⚠ Margin call — top up ${fmt$(topThis)}</span>`
    : (cumTop > 0 ? `<span style="color:#f59e0b">Open · topped up ${fmt$(cumTop)}</span>`
                  : `<span class="pos">Open</span>`);
}

function buildCharts(rows) {
  const opts = (yT, extra) => ({responsive:true, maintainAspectRatio:false, animation:false,
    interaction:{mode:"index",intersect:false}, plugins:{legend:{labels:{boxWidth:12,usePointStyle:true,padding:12}}},
    scales:{x:{grid:{display:false},ticks:{maxTicksLimit:10,autoSkip:true}}, y:{title:{display:true,text:yT},grid:{color:"#161d28"}}, ...extra}});
  const ds = (label, color, axis, dash) => ({label, data:[], borderColor:color, backgroundColor:color, borderWidth:2, pointRadius:0, tension:.2, yAxisID:axis||"y", borderDash:dash||[]});
  if (tradeChart) tradeChart.destroy(); if (hedgeChart) hedgeChart.destroy();
  tradeChart = new Chart($("cTrade"), {type:"line", data:{labels:[], datasets:[
    ds("Index level", C.muted, "y1"), ds("Long Larry equity", C.green), ds("Short Sarah equity", C.accent),
    ds("Initial margin", C.blue, "y", [6,4]), ds("Maintenance margin (call below)", C.amber, "y", [3,3])]},
    options: opts("Account equity ($)", {y1:{position:"right",title:{display:true,text:"Index"},grid:{drawOnChartArea:false}}})});
  hedgeChart = new Chart($("cHedge"), {type:"line", data:{labels:[], datasets:[
    ds("Portfolio — unhedged", C.accent), ds("Portfolio — hedged", C.green), ds("Hedge PnL", C.blue, "y", [5,4])]},
    options: opts("Value ($)", {})});
}

function finish() {
  const rows = MODEL.rows, r = rows[rows.length - 1];
  const line = (name, color, pnl, calls, cumTop, firstCall) => {
    const cap = MODEL.im + cumTop, roc = (pnl / cap * 100).toFixed(0) + "% on capital";
    const calltxt = calls > 0
      ? ` — <span class="liq">${calls} margin call${calls > 1 ? "s" : ""}, topped up ${fmt$(cumTop)}</span> to stay in (else liquidated ${mlabel(firstCall)})`
      : " — no margin calls";
    return `<br>· <b style="color:${color}">${name}</b>: PnL ${fmt$(pnl)}, ${roc}${calltxt}.`;
  };
  const minUn = Math.min(...rows.map(x => x.unhedged)), minNet = Math.min(...rows.map(x => x.net));
  const avoided = (MODEL.V0 - minUn) - (MODEL.V0 - minNet);
  $("results").style.display = "";
  $("results").innerHTML = `<b>Result over ${rows.length - 1} months.</b> Index moved <b>${(r.ret * 100).toFixed(0)}%</b> (${MODEL.field}).
    ${line("Long Larry", "#22c55e", r.lPnl, MODEL.callsL, r.cumTopL, MODEL.firstCallL)}
    ${line("Short Sarah", "#e23b4e", r.sPnl, MODEL.callsS, r.cumTopS, MODEL.firstCallS)}
    <br>· <b>Hedger</b>: unhedged portfolio ${fmtM(rows[0].unhedged)} → <b>${fmtM(r.unhedged)}</b>; hedged ended at <b>${fmtM(r.net)}</b>. The long-index hedge offset <b style="color:#22c55e">${fmtM(avoided)}</b> of drawdown.`;
  $("status").textContent = "Done. Adjust inputs and run again.";
}

function reset() {
  if (anim) { clearInterval(anim); anim = null; }
  $("run").disabled = false; $("results").style.display = "none"; $("monthflag").textContent = "";
  ["lPnl","lIM","lMM","lEq","lStat","sPnl","sIM","sMM","sEq","sStat","pUn","pHedge","pNet","pSaved"].forEach(id => $(id).textContent = "—");
  $("status").textContent = "";
}
