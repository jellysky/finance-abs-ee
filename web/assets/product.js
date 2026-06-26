// Render a Serention index page from data/<slug>.json
const C = {accent:"#14b8c4", blue:"#3b82f6", amber:"#f59e0b", green:"#22c55e", muted:"#c9b8f2"};
Chart.defaults.color = "#c9b8f2";
Chart.defaults.borderColor = "rgba(201,184,242,0.18)";
Chart.defaults.font.family = "-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif";

const slug = new URLSearchParams(location.search).get("slug") || "auto-subprime";

fetch(`data/${slug}.json`).then(r => r.json()).then(render).catch(err => {
  document.getElementById("title").textContent = "Could not load index data";
  document.getElementById("method").innerHTML =
    `Run <code>python web/build_site_data.py</code> to generate <code>data/${slug}.json</code>. (${err})`;
});

// Net-loss index — the Net-Loss Future's settlement reference (data/nli.json).
// Rendered independently so a missing file just hides the card.
fetch("data/nli.json").then(r => r.json()).then(renderNLI).catch(() => {
  const card = document.getElementById("nliCard");
  if (card) card.style.display = "none";
});

function renderNLI(d) {
  if (!d || !d.series || !d.series.length) { const c = document.getElementById("nliCard"); if (c) c.style.display = "none"; return; }
  const labels = d.series.map(s => s.date);
  const covid = boxAnno({start:"2020-04-01"});
  line("cNLI", labels, [
    ds("Net-loss index % (ann.)", d.series.map(s => s.nli), C.accent, {fill:true, fillc:"rgba(20,184,196,.10)"}),
  ], {yTitle:"% per year", anno:{covid}});
}

const pct = v => (v == null ? "—" : v.toFixed(1) + "%");
const fmtMonth = s => { const [y,m] = s.split("-"); return ["", "Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+m] + " " + y; };

function render(d) {
  document.title = `Serention — ${d.product}`;
  document.getElementById("title").textContent = d.product;
  const L = d.latest;
  document.getElementById("asof").textContent =
    `${d.ticker ? d.ticker + " · " : ""}as of ${fmtMonth(L.as_of)}` + (L.first ? ` · history from ${fmtMonth(L.first)}` : "");

  const kpis = [
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

  renderValidation(d);
  renderDuration(d);
  renderCompTable(d);
}

// One collapsed validation chart: our loss vs NY Fed series and rating-agency loss markers.
function renderValidation(d) {
  const fed = d.fed || {}, a = d.agency || {};
  const sub = fed.sub620_30plus_q || [], auto = fed.auto90_annl || [];
  const fl = union(d.series.map(s => s.date), sub.map(x => x.date), auto.map(x => x.date));
  const marker = (pts, color, label) => ({type:"line", label, data: mapTo(fl, pts || [], "value"),
    borderColor: color, backgroundColor: color, showLine: false, pointRadius: 6, pointStyle: "rectRot"});
  line("cValid", fl, [
    ds("Our net loss % (ann.)", mapTo(fl, d.series, "net_loss"), C.accent, {span:true}),
    ds("Fed subprime <620, 30+ % (q)", mapTo(fl, sub, "value"), C.blue, {span:true, dash:[5,4]}),
    ds("Fed all-auto 90+ % (ann.)", mapTo(fl, auto, "value"), C.green, {span:true, dash:[2,3]}),
    marker(a.kbra_nonprime && a.kbra_nonprime.net_loss_annl, C.amber, "KBRA Non-Prime ◇"),
    marker(a.fitch_subprime && a.fitch_subprime.net_loss_annl, C.muted, "Fitch Subprime ◇"),
  ], {yTitle:"%", anno:{covid: boxAnno(d.covid)}});
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
    y:{title:{display:!!cfg.yTitle, text:cfg.yTitle||""}, grid:{color:"rgba(201,184,242,0.14)"}},
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

// align an array of {date,<key>} (or our series) to a master label list
function mapTo(labels, points, key) {
  const m = new Map(points.map(p => [p.date, p[key]]));
  return labels.map(l => (m.has(l) ? m.get(l) : null));
}
function union(...lists) {
  return [...new Set([].concat(...lists))].sort();
}
