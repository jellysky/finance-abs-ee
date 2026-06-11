// Render a Serention index page from data/<slug>.json
const C = {accent:"#e23b4e", blue:"#3b82f6", amber:"#f59e0b", green:"#22c55e", muted:"#8a97a8"};
Chart.defaults.color = "#8a97a8";
Chart.defaults.borderColor = "#1f2733";
Chart.defaults.font.family = "-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif";

const slug = new URLSearchParams(location.search).get("slug") || "auto-subprime";

fetch(`data/${slug}.json`).then(r => r.json()).then(render).catch(err => {
  document.getElementById("title").textContent = "Could not load index data";
  document.getElementById("method").innerHTML =
    `Run <code>python web/build_site_data.py</code> to generate <code>data/${slug}.json</code>. (${err})`;
});

const pct = v => (v == null ? "—" : v.toFixed(1) + "%");
const fmtMonth = s => { const [y,m] = s.split("-"); return ["", "Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+m] + " " + y; };

function render(d) {
  document.title = `Serention — ${d.product}`;
  document.getElementById("title").textContent = d.product;
  const L = d.latest;
  document.getElementById("asof").textContent =
    `${d.ticker ? d.ticker + " · " : ""}as of ${fmtMonth(L.as_of)}` + (L.first ? ` · history from ${fmtMonth(L.first)}` : "");

  const kpis = [
    ["Stress index", L.stress != null ? L.stress.toFixed(2) + "σ" : "—"],
    ["30+ DPD", pct(L.delq30)], ["60+ DPD", pct(L.delq60)],
    ["Net loss (ann.)", pct(L.net_loss)], ["Recovery", pct(L.recovery)],
    ["Constituent deals", L.n_deals ?? "—"], ["Avg FICO", L.fico ?? "—"],
    ["Borrowers", L.borrowers != null ? L.borrowers.toLocaleString() : "—"],
    ["Avg loan term", L.orig_term != null ? L.orig_term.toFixed(0) + " mo" : "—"],
    ["Realized WAL", L.realized_wal != null ? L.realized_wal.toFixed(0) + " mo" : "—"],
  ];
  document.getElementById("kpis").innerHTML = kpis.map(([l,v]) =>
    `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`).join("");
  document.getElementById("method").innerHTML = `<b>Methodology.</b> ${d.methodology}`;

  const labels = d.series.map(s => s.date);
  const get = k => d.series.map(s => s[k]);
  const covid = boxAnno(d.covid);

  line("cStress", labels,
    [ds("Stress index", get("stress"), C.accent, {fill:true, fillc:"rgba(226,59,78,.10)"})],
    {yTitle:"σ", anno:{covid, zero:zeroLine()}});

  line("cPerf", labels, [
    ds("30+ DPD %", get("delq30"), C.accent),
    ds("60+ DPD %", get("delq60"), C.amber),
    ds("Net loss % (ann.)", get("net_loss"), C.blue),
  ], {yTitle:"%", anno:{covid}});

  const fl = union(labels, d.fed.sub620_30plus_q.map(x=>x.date), d.fed.auto90_annl.map(x=>x.date));
  line("cFed", fl, [
    ds("Our 30+ DPD %", mapTo(fl, d.series, "delq30"), C.accent, {span:true}),
    ds("Fed subprime <620, 30+ % (q)", mapTo(fl, d.fed.sub620_30plus_q, "value"), C.blue, {span:true, dash:[5,4]}),
    ds("Fed all-auto 90+ % (ann.)", mapTo(fl, d.fed.auto90_annl, "value"), C.green, {span:true, dash:[2,3]}),
  ], {yTitle:"%", anno:{covid}});

  new Chart(document.getElementById("cComp"), {
    data:{labels, datasets:[
      {type:"bar", label:"Constituent deals", data:get("n_deals"),
       backgroundColor:"rgba(59,130,246,.32)", yAxisID:"y"},
      {type:"line", label:"Avg FICO", data:get("fico"), borderColor:C.accent,
       borderWidth:2, pointRadius:0, tension:.25, spanGaps:true, yAxisID:"y1"},
    ]},
    options: baseOpts({yTitle:"# deals", anno:{covid},
      y1:{position:"right", title:{display:true,text:"Avg FICO"}, min:540, max:660,
          grid:{drawOnChartArea:false}}}),
  });
  renderBenchmark(d);
  renderDuration(d);
  renderCompTable(d);
}

function renderDuration(d) {
  const labels = d.series.map(s => s.date);
  const g = k => d.series.map(s => s[k]);
  line("cDur", labels, [
    ds("WA original term", g("orig_term"), C.muted, {span: true}),
    ds("WA remaining term", g("rem_term"), C.blue, {span: true}),
    ds("Scheduled WAL", g("sched_wal"), C.amber, {span: true}),
    ds("Realized WAL", g("realized_wal"), C.accent, {span: true}),
  ], {yTitle: "Months"});
}

function renderCompTable(d) {
  const n = v => v == null ? "—" : v.toLocaleString();
  const rows = d.series.filter(s => s.date.slice(5, 7) === "12" || s.date === d.latest.as_of);
  const body = rows.map(s => `<tr><td>${fmtMonth(s.date)}</td><td>${s.n_deals ?? "—"}</td>
    <td>${n(s.borrowers)}</td><td>${s.fico ?? "—"}</td><td>${s.orig_term ?? "—"}</td>
    <td>${s.rem_term ?? "—"}</td><td>${s.sched_wal ?? "—"}</td><td>${s.realized_wal ?? "—"}</td></tr>`).join("");
  document.getElementById("compTable").innerHTML =
    `<table class="dt"><thead><tr><th>Month</th><th>Deals</th><th>Borrowers</th><th>WA FICO</th>
     <th>WA orig (mo)</th><th>WA rem (mo)</th><th>Sched WAL</th><th>Realized WAL</th></tr></thead>
     <tbody>${body}</tbody></table>`;
}

function renderBenchmark(d) {
  const a = d.agency || {};
  const card = document.getElementById("cBench").closest(".chart-card");
  if (!Object.keys(a).length) { if (card) card.style.display = "none"; return; }
  const labels = d.series.map(s => s.date);
  const marker = (pts, color, label) => ({type:"line", label, data: mapTo(labels, pts || [], "value"),
    borderColor: color, backgroundColor: color, showLine: false, pointRadius: 6, pointStyle: "rectRot"});
  new Chart(document.getElementById("cBench"), {data:{labels, datasets:[
    {type:"line", label:"OUR net loss % (ann.)", data: d.series.map(s => s.net_loss),
     borderColor: C.accent, borderWidth: 2, pointRadius: 0, tension: .25, spanGaps: true},
    marker(a.kbra_nonprime && a.kbra_nonprime.net_loss_annl, C.blue, "KBRA Non-Prime"),
    marker(a.fitch_subprime && a.fitch_subprime.net_loss_annl, C.green, "Fitch Subprime"),
  ]}, options: baseOpts({yTitle:"Annualized net loss (%)"})});

  const last = arr => (arr && arr.length) ? arr[arr.length - 1].value : null;
  const m = (s, k) => a[s] ? a[s][k] : null;
  const v = x => x == null ? "—" : x.toFixed(1) + "%";
  const L = d.latest;
  const rows = [
    ["Annualized net loss", L.net_loss, last(m("kbra_nonprime","net_loss_annl")), last(m("fitch_subprime","net_loss_annl"))],
    ["60+ day delinquency", L.delq60, last(m("kbra_nonprime","delq60")), last(m("fitch_subprime","delq60"))],
    ["Recovery rate", L.recovery, last(m("kbra_nonprime","recovery")), last(m("fitch_subprime","recovery"))],
  ];
  document.getElementById("benchTable").innerHTML =
    `<table class="dt"><thead><tr><th>Latest reading</th><th>Ours (&lt;640)</th><th>KBRA Non-Prime</th><th>Fitch Subprime</th></tr></thead><tbody>` +
    rows.map(r => `<tr><td>${r[0]}</td><td>${v(r[1])}</td><td>${v(r[2])}</td><td>${v(r[3])}</td></tr>`).join("") +
    `</tbody></table>`;
}

function ds(label, data, color, o = {}) {
  return {label, data, borderColor:color, backgroundColor:o.fillc || color,
    borderWidth:2, pointRadius:0, tension:.25, fill:!!o.fill, spanGaps:!!o.span,
    borderDash:o.dash || []};
}

function line(id, labels, datasets, cfg) {
  new Chart(document.getElementById(id), {type:"line", data:{labels, datasets}, options:baseOpts(cfg)});
}

function baseOpts(cfg) {
  const anno = {};
  if (cfg.anno?.covid) anno.covid = cfg.anno.covid;
  if (cfg.anno?.zero) anno.zero = cfg.anno.zero;
  const scales = {
    x:{grid:{display:false}, ticks:{maxTicksLimit:11, autoSkip:true,
        callback:function(v){ const s=this.getLabelForValue(v); return s ? s.slice(0,4) : s; }}},
    y:{title:{display:!!cfg.yTitle, text:cfg.yTitle||""}, grid:{color:"#161d28"}},
  };
  if (cfg.y1) scales.y1 = cfg.y1;
  return {responsive:true, maintainAspectRatio:false, interaction:{mode:"index", intersect:false},
    plugins:{legend:{labels:{boxWidth:12, usePointStyle:true, padding:14}},
             annotation:{annotations:anno},
             tooltip:{callbacks:{title:items=>fmtMonth(items[0].label)}}},
    scales};
}

function boxAnno(c) {
  if (!c) return null;
  return {type:"box", xMin:c.start, xMax:"2020-12-01",
    backgroundColor:"rgba(245,158,11,.09)", borderWidth:0,
    label:{display:false}};
}
function zeroLine() {
  return {type:"line", yMin:0, yMax:0, borderColor:"#3a4554", borderWidth:1, borderDash:[4,4]};
}

// align an array of {date,<key>} (or our series) to a master label list
function mapTo(labels, points, key) {
  const m = new Map(points.map(p => [p.date, p[key]]));
  return labels.map(l => (m.has(l) ? m.get(l) : null));
}
function union(...lists) {
  return [...new Set([].concat(...lists))].sort();
}
