// Demo: bet every monthly mark in the index history with a one-month Net-Loss Future.
// At month t you open at NLI_t and settle to next month's realized print NLI_{t+1}.
// PnL_t = side * notional * (NLI_{t+1} - NLI_t) / 100. Step chronologically through all marks.
Chart.defaults.color = "#c9b8f2"; Chart.defaults.borderColor = "rgba(201,184,242,0.18)";
const C = {accent:"#14b8c4", green:"#22c55e", neg:"#ff5c5c", amber:"#f59e0b", muted:"#c9b8f2", blue:"#3b82f6"};

let SERIES = [], chart = null, timer = null, i = 0, state = null;

const $ = id => document.getElementById(id);
const fmt$ = v => (v < 0 ? "-$" : "$") + Math.abs(Math.round(v)).toLocaleString();
const MON = ["", "Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const monthLabel = ym => { const [y, m] = ym.split("-"); return MON[+m] + " " + y; };
const ym = idx => SERIES[idx].date.slice(0, 7);
const ptsTxt = n => (n >= 0 ? "+" : "") + n.toFixed(2) + " pts";

fetch("data/nli.json").then(r => r.json()).then(d => {
  SERIES = d.series.filter(s => s.nli != null);
  $("run").onclick = play;
  $("pause").onclick = pause;
  $("step").onclick = () => { pause(); stepOnce(); };
  $("reset").onclick = reset;
  $("strategy").onchange = reset;
  $("bet").onchange = reset;
  reset();
}).catch(() => { $("status").textContent = "Could not load index data (data/nli.json)."; });

// +1 long / -1 short, per the selected strategy
function sideFor(t) {
  const s = $("strategy").value;
  if (s === "long") return 1;
  if (s === "short") return -1;
  const mv = t === 0 ? 1 : (Math.sign(SERIES[t].nli - SERIES[t - 1].nli) || 1);
  return s === "momentum" ? mv : -mv;   // contrarian = fade
}
const sideName = s => s > 0 ? "Long" : "Short";

function newChart() {
  if (chart) chart.destroy();
  chart = new Chart($("cBet"), {
    type: "line",
    data: { labels: [], datasets: [
      { label: "Net-loss index %", data: [], yAxisID: "y1", borderColor: C.muted, borderWidth: 2,
        tension: .2, pointRadius: ctx => ctx.dataIndex === ctx.dataset.data.length - 1 ? 5 : 0,
        pointBackgroundColor: C.amber, pointBorderColor: C.amber },
      { label: "Cumulative PnL", data: [], yAxisID: "y", borderColor: C.green, borderWidth: 2,
        tension: .2, pointRadius: 0, fill: true, backgroundColor: "rgba(34,197,94,.08)" },
    ]},
    options: { responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "bottom", labels: { boxWidth: 12, usePointStyle: true, padding: 12 } } },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 12, autoSkip: true } },
        y: { position: "left", title: { display: true, text: "Cumulative PnL ($)" }, grid: { color: "rgba(201,184,242,0.14)" } },
        y1: { position: "right", title: { display: true, text: "Index (%)" }, grid: { drawOnChartArea: false } },
      } },
  });
}

function reset() {
  pause();
  i = 0;
  state = { cum: 0, bets: 0, wins: 0, streak: 0, best: {p: -Infinity}, worst: {p: Infinity} };
  newChart();
  renderScoreboard();
  renderThisBet(null);
  $("ledger").innerHTML = `<table class="dt"><thead><tr><th>Month</th><th>Side</th><th>Entry</th>
    <th>Settle</th><th>Move</th><th>PnL</th><th>Cumulative</th></tr></thead><tbody id="ledgerBody"></tbody></table>`;
  $("monthflag").textContent = "";
  $("status").textContent = `Ready — ${SERIES.length - 1} monthly bets from ${monthLabel(ym(0))} to ${monthLabel(ym(SERIES.length - 1))}. Press play.`;
  $("run").disabled = false;
}

function play() {
  if (timer) return;
  if (i >= SERIES.length - 1) reset();
  timer = setInterval(stepOnce, +$("speed").value);
  $("run").disabled = true;
}
function pause() { clearInterval(timer); timer = null; $("run").disabled = false; }

function stepOnce() {
  if (i >= SERIES.length - 1) { pause(); finish(); return; }
  const notional = +$("bet").value || 100000;
  const entry = SERIES[i].nli, settle = SERIES[i + 1].nli, side = sideFor(i);
  const move = settle - entry;
  const pnl = side * notional * move / 100;

  state.cum += pnl; state.bets++;
  const win = pnl > 0, flat = pnl === 0;
  if (win) state.wins++;
  state.streak = win ? (state.streak >= 0 ? state.streak + 1 : 1)
                     : flat ? 0 : (state.streak <= 0 ? state.streak - 1 : -1);
  if (pnl > state.best.p) state.best = { p: pnl, ym: ym(i) };
  if (pnl < state.worst.p) state.worst = { p: pnl, ym: ym(i) };

  // grow the chart
  chart.data.labels.push(monthLabel(ym(i)));
  chart.data.datasets[0].data.push(entry);
  chart.data.datasets[1].data.push(state.cum);
  chart.update("none");

  renderScoreboard();
  renderThisBet({ ym: ym(i), entry, settle, side, move, pnl, win, flat });
  addLedger({ ym: ym(i), side, entry, settle, move, pnl, cum: state.cum, win, flat });
  $("monthflag").textContent = `${monthLabel(ym(i))} → settles ${monthLabel(ym(i + 1))}`;

  i++;
  if (i >= SERIES.length - 1) { pause(); finish(); }
}

function renderThisBet(b) {
  if (!b) {
    ["tbMonth","tbSide","tbES","tbMove","tbPnl"].forEach(id => $(id).textContent = "—");
    const bd = $("tbBadge"); bd.textContent = "—"; bd.className = "badge flat";
    return;
  }
  $("tbMonth").textContent = monthLabel(b.ym);
  $("tbSide").textContent = sideName(b.side);
  $("tbES").textContent = `${b.entry.toFixed(2)} → ${b.settle.toFixed(2)}`;
  $("tbMove").textContent = ptsTxt(b.move);
  const pnlEl = $("tbPnl");
  pnlEl.textContent = fmt$(b.pnl);
  pnlEl.className = "v " + (b.win ? "pos" : b.flat ? "" : "neg");
  const bd = $("tbBadge");
  bd.textContent = b.flat ? "FLAT" : b.win ? "WIN" : "LOSS";
  bd.className = "badge " + (b.flat ? "flat" : b.win ? "win" : "loss");
}

function renderScoreboard() {
  const s = state;
  const cumEl = $("scCum"); cumEl.textContent = fmt$(s.cum);
  cumEl.className = "v " + (s.cum > 0 ? "pos" : s.cum < 0 ? "neg" : "");
  $("scBets").textContent = s.bets;
  $("scWr").textContent = s.bets ? Math.round(100 * s.wins / s.bets) + "%" : "—";
  $("scStreak").textContent = s.streak === 0 ? "—" : (s.streak > 0 ? "W" + s.streak : "L" + (-s.streak));
  $("scBest").textContent = s.best.ym ? `${fmt$(s.best.p)} · ${monthLabel(s.best.ym)}` : "—";
  $("scWorst").textContent = s.worst.ym ? `${fmt$(s.worst.p)} · ${monthLabel(s.worst.ym)}` : "—";
}

function addLedger(b) {
  const cls = b.win ? "pos" : b.flat ? "" : "neg";
  const row = `<tr><td>${monthLabel(b.ym)}</td><td>${sideName(b.side)}</td><td>${b.entry.toFixed(2)}</td>
    <td>${b.settle.toFixed(2)}</td><td class="${cls}">${ptsTxt(b.move * b.side)}</td>
    <td class="${cls}">${fmt$(b.pnl)}</td><td>${fmt$(b.cum)}</td></tr>`;
  $("ledgerBody").insertAdjacentHTML("afterbegin", row);   // newest on top
}

function finish() {
  const s = state;
  const wr = s.bets ? Math.round(100 * s.wins / s.bets) : 0;
  $("status").textContent = `Done — bet all ${s.bets} marks (${monthLabel(ym(0))} → ${monthLabel(ym(SERIES.length - 1))}). ` +
    `Win rate ${wr}%. Cumulative PnL ${fmt$(s.cum)} on ${fmt$(+$("bet").value || 100000)} per bet. ` +
    `Best ${fmt$(s.best.p)} (${monthLabel(s.best.ym)}), worst ${fmt$(s.worst.p)} (${monthLabel(s.worst.ym)}).`;
}
