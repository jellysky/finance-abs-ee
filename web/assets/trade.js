// Serention testnet — Net-Loss Future dapp (ethers v6).
// Trades a dated, cash-settled future on the net-loss rate. LONG gains when the loss rate
// rises (hedge an ABS book); SHORT fades it. Mark = the net-loss rate (8 decimals); the
// contract settles in arrears to the realized print. Collateral = test USDC (MockUSDC).
const cfg = window.SERENTION;
const $ = id => document.getElementById(id);
const D = cfg.usdcDecimals;
let provider, signer, addr, readProvider;
let usdc, usdcR;            // MockUSDC (signer + read)
let fut, futR;             // selected Net-Loss Future (signer + read)
let futAddr = cfg.strip && cfg.strip[0] ? cfg.strip[0].address : cfg.addresses.netLossFuture;

const configured = cfg.addresses.usdc && futAddr;
if (!configured) { $("needsetup").style.display = ""; $("connect").disabled = true; }

const status = (m, e) => { const s = $("status"); s.textContent = m; s.style.color = e ? "#ff5c5c" : "#b9a8ec"; };
const usd = bn => "$" + Number(ethers.formatUnits(bn, D)).toLocaleString(undefined, {maximumFractionDigits: 0});
const usdSigned = bn => (bn < 0n ? "-" : "") + usd(bn < 0n ? -bn : bn);
const ratePct = bn => Number(ethers.formatUnits(bn, 8)).toFixed(2) + "%"; // loss rate, 8 decimals

$("connect").addEventListener("click", connect);

async function connect() {
  if (!window.ethereum) { status("No Ethereum wallet found — install MetaMask, Rabby, or another browser wallet.", true); return; }
  try {
    provider = new ethers.BrowserProvider(window.ethereum);
    await provider.send("eth_requestAccounts", []);
    try {
      await window.ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId: cfg.chainIdHex }] });
    } catch (e) {
      if (e.code === 4902) await window.ethereum.request({ method: "wallet_addEthereumChain", params: [{
        chainId: cfg.chainIdHex, chainName: "Sepolia", nativeCurrency: { name: "Sepolia ETH", symbol: "ETH", decimals: 18 },
        rpcUrls: [cfg.readRpc || "https://ethereum-sepolia-rpc.publicnode.com"], blockExplorerUrls: ["https://sepolia.etherscan.io"] }] });
    }
    signer = await provider.getSigner();
    addr = await signer.getAddress();
    readProvider = cfg.readRpc ? new ethers.JsonRpcProvider(cfg.readRpc) : provider;
    usdc = new ethers.Contract(cfg.addresses.usdc, cfg.abi.usdc, signer);
    usdcR = new ethers.Contract(cfg.addresses.usdc, cfg.abi.usdc, readProvider);
    buildStripSelector();
    bindFuture(futAddr);
    $("wallet").textContent = addr.slice(0, 6) + "…" + addr.slice(-4);
    $("app").style.display = ""; $("connect").style.display = "none";
    wire();
    await refresh();
    status("Connected to Sepolia.");
  } catch (e) { status(err(e), true); }
}

function bindFuture(a) {
  futAddr = a;
  fut = new ethers.Contract(a, cfg.abi.netLossFuture, signer);
  futR = new ethers.Contract(a, cfg.abi.netLossFuture, readProvider);
}

function buildStripSelector() {
  const sel = $("contract");
  if (!sel || !cfg.strip) return;
  sel.innerHTML = cfg.strip.map(s => `<option value="${s.address}">${s.label}</option>`).join("");
  sel.value = futAddr;
  sel.onchange = async () => { bindFuture(sel.value); status("Switched contract."); await refresh(); };
}

function wire() {
  $("faucet").onclick = () => tx(() => usdc.mint(ethers.parseUnits("100000", D)), "Minting test USDC…");
  $("deposit").onclick = deposit;
  $("withdraw").onclick = () => tx(() => fut.withdraw(amt()), "Withdrawing…");
  $("open").onclick = () => tx(() => fut.open($("side").value === "long", notional()), "Opening position…");
  $("close").onclick = () => tx(() => fut.close(), "Closing position…");
}

const amt = () => ethers.parseUnits($("amt").value || "0", D);
const notional = () => ethers.parseUnits($("notional").value || "0", D);

async function deposit() {
  try {
    const need = amt();
    const allowed = await usdc.allowance(addr, futAddr);
    if (allowed < need) { status("Approving USDC…"); await (await usdc.approve(futAddr, need)).wait(); }
    await tx(() => fut.deposit(need), "Depositing collateral…");
  } catch (e) { status(err(e), true); }
}

async function tx(fn, msg) {
  try { status(msg); const t = await fn(); await t.wait(); status("Done. " + (t.hash ? t.hash.slice(0, 10) + "…" : "")); await refresh(); }
  catch (e) { status(err(e), true); }
}

async function refresh() {
  try {
    const [mark, wbal, coll, pnl, eq, pos, settled, settAt, label] = await Promise.all([
      futR.markPrice(), usdcR.balanceOf(addr), futR.collateral(addr),
      futR.pnlOf(addr), futR.equityOf(addr), futR.getPosition(addr),
      futR.settled(), futR.settlementTime(), futR.referenceLabel()
    ]);
    $("kPrice").textContent = ratePct(mark);
    $("kWallet").textContent = usd(wbal);
    $("kColl").textContent = usd(coll);
    $("kPnl").innerHTML = `<span class="${pnl < 0n ? "neg" : "pos"}">${usdSigned(pnl)}</span>`;
    $("kEquity").textContent = usdSigned(eq);
    const when = new Date(Number(settAt) * 1000).toISOString().slice(0, 10);
    $("settleline").textContent = settled
      ? `${label} — SETTLED at ${ratePct(mark)}`
      : `${label} — marks to the live net-loss rate; settles in arrears ~${when}`;
    const [n, entry, isLong, open] = pos;
    $("posline").innerHTML = open
      ? `Open: <b class="${isLong ? "pos" : "neg"}">${isLong ? "LONG (losses rise)" : "SHORT (losses fall)"}</b> · notional ${usd(n)} · entry ${ratePct(entry)}`
      : "No open position on this contract.";
  } catch (e) { status(err(e), true); }
}

function err(e) { return "Error: " + (e?.shortMessage || e?.reason || e?.message || String(e)).slice(0, 140); }
