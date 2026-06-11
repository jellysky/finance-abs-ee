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
  let lLiq = -1, sLiq = -1;
  const rows = pts.map((pt, i) => {
    const ret = (pt.p - P0) / P0;
    let lPnl = notional * ret, sPnl = -notional * ret;
    let lEq = im + lPnl, sEq = im + sPnl;
    if (lLiq < 0 && lEq < maint) lLiq = i;
    if (sLiq < 0 && sEq < maint) sLiq = i;
    if (lLiq >= 0 && i >= lLiq) { lEq = 0; lPnl = -im; }
    if (sLiq >= 0 && i >= sLiq) { sEq = 0; sPnl = -im; }
    const unhedged = V0 * (1 - beta * ret);
    const hedge = hedgeNotional * ret;
    return {ym: pt.ym, p: pt.p, ret, lPnl, sPnl, lEq, sEq, unhedged, hedge, net: unhedged + hedge};
  });
  return {rows, im, maint, notional, V0, lLiq, sLiq, field, lev};
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
  setTrader("l", r.lPnl, r.lEq, MODEL.lLiq, i);
  setTrader("s", r.sPnl, r.sEq, MODEL.sLiq, i);
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

function setTrader(k, pnl, eq, liq, i) {
  $(k + "Pnl").innerHTML = `<span class="${cl(pnl)}">${fmt$(pnl)}</span>`;
  $(k + "Eq").textContent = fmt$(eq);
  const liquidated = liq >= 0 && i >= liq;
  $(k + "Stat").innerHTML = liquidated
    ? `<span class="liq">⚠ MARGIN CALL → liquidated ${mlabel(MODEL.rows[liq].ym)}</span>`
    : `<span class="pos">Open</span>`;
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
  const r = MODEL.rows[MODEL.rows.length - 1], rows = MODEL.rows;
  const ror = p => MODEL.im ? (p / MODEL.im * 100).toFixed(0) + "%" : "—";
  const minUn = Math.min(...rows.map(x => x.unhedged)), minNet = Math.min(...rows.map(x => x.net));
  const avoided = (MODEL.V0 - minUn) - (MODEL.V0 - minNet);
  $("results").style.display = "";
  $("results").innerHTML = `<b>Result over ${rows.length - 1} months.</b>
    Index moved <b>${(r.ret * 100).toFixed(0)}%</b> (${MODEL.field}).
    <br>· <b style="color:#22c55e">Long Larry</b>: ${fmt$(r.lPnl)} (${ror(r.lPnl)} on margin)${MODEL.lLiq>=0?` — <span class="liq">liquidated ${mlabel(rows[MODEL.lLiq].ym)}</span>`:""}.
    <br>· <b style="color:#e23b4e">Short Sarah</b>: ${fmt$(r.sPnl)} (${ror(r.sPnl)} on margin)${MODEL.sLiq>=0?` — <span class="liq">liquidated ${mlabel(rows[MODEL.sLiq].ym)}</span>`:""}.
    <br>· <b>Hedger</b>: unhedged portfolio ${fmtM(rows[0].unhedged)} → <b>${fmtM(r.unhedged)}</b>;
      hedged ended at <b>${fmtM(r.net)}</b>. The long-index hedge offset <b style="color:#22c55e">${fmtM(avoided)}</b> of drawdown.`;
  $("status").textContent = "Done. Adjust inputs and run again.";
}

function reset() {
  if (anim) { clearInterval(anim); anim = null; }
  $("run").disabled = false; $("results").style.display = "none"; $("monthflag").textContent = "";
  ["lPnl","lIM","lMM","lEq","lStat","sPnl","sIM","sMM","sEq","sStat","pUn","pHedge","pNet","pSaved"].forEach(id => $(id).textContent = "—");
  $("status").textContent = "";
}
