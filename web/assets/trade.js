// Serention testnet trading dapp (ethers v6). Connects MetaMask on Sepolia and
// drives the MockUSDC / IndexOracle / MarginedIndex contracts.
const cfg = window.SERENTION;
const $ = id => document.getElementById(id);
const D = cfg.usdcDecimals;
let provider, signer, addr, usdc, oracle, mi;

const configured = cfg.addresses.usdc && cfg.addresses.oracle && cfg.addresses.margined;
if (!configured) { $("needsetup").style.display = ""; $("connect").disabled = true; }

const status = (m, err) => { const s = $("status"); s.textContent = m; s.style.color = err ? "#e23b4e" : "#8a97a8"; };
const usd = bn => "$" + Number(ethers.formatUnits(bn, D)).toLocaleString(undefined, {maximumFractionDigits: 0});
const usdSigned = bn => (bn < 0n ? "-" : "") + usd(bn < 0n ? -bn : bn);

$("connect").addEventListener("click", connect);

async function connect() {
  if (!window.ethereum) { status("No Ethereum wallet found — install MetaMask.", true); return; }
  try {
    provider = new ethers.BrowserProvider(window.ethereum);
    await provider.send("eth_requestAccounts", []);
    // ensure Sepolia
    try {
      await window.ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId: cfg.chainIdHex }] });
    } catch (e) {
      if (e.code === 4902) await window.ethereum.request({ method: "wallet_addEthereumChain", params: [{
        chainId: cfg.chainIdHex, chainName: "Sepolia", nativeCurrency: { name: "Sepolia ETH", symbol: "ETH", decimals: 18 },
        rpcUrls: ["https://rpc.sepolia.org"], blockExplorerUrls: ["https://sepolia.etherscan.io"] }] });
    }
    signer = await provider.getSigner();
    addr = await signer.getAddress();
    usdc = new ethers.Contract(cfg.addresses.usdc, cfg.abi.usdc, signer);
    oracle = new ethers.Contract(cfg.addresses.oracle, cfg.abi.oracle, signer);
    mi = new ethers.Contract(cfg.addresses.margined, cfg.abi.margined, signer);
    $("wallet").textContent = addr.slice(0, 6) + "…" + addr.slice(-4);
    $("app").style.display = ""; $("connect").style.display = "none";
    wire();
    await refresh();
    status("Connected to Sepolia.");
  } catch (e) { status(err(e), true); }
}

function wire() {
  $("faucet").onclick = () => tx(() => usdc.mint(ethers.parseUnits("100000", D)), "Minting test USDC…");
  $("deposit").onclick = deposit;
  $("withdraw").onclick = () => tx(() => mi.withdraw(amt()), "Withdrawing…");
  $("open").onclick = () => tx(() => mi.open($("side").value === "long", notional()), "Opening position…");
  $("close").onclick = () => tx(() => mi.close(), "Closing position…");
}

const amt = () => ethers.parseUnits($("amt").value || "0", D);
const notional = () => ethers.parseUnits($("notional").value || "0", D);

async function deposit() {
  try {
    const need = amt();
    const allowed = await usdc.allowance(addr, cfg.addresses.margined);
    if (allowed < need) { status("Approving USDC…"); await (await usdc.approve(cfg.addresses.margined, need)).wait(); }
    await tx(() => mi.deposit(need), "Depositing collateral…");
  } catch (e) { status(err(e), true); }
}

async function tx(fn, msg) {
  try { status(msg); const t = await fn(); await t.wait(); status("Done. " + (t.hash ? t.hash.slice(0, 10) + "…" : "")); await refresh(); }
  catch (e) { status(err(e), true); }
}

async function refresh() {
  try {
    const [price, wbal, coll, pnl, eq, pos] = await Promise.all([
      oracle.price(), usdc.balanceOf(addr), mi.collateral(addr),
      mi.pnlOf(addr), mi.equityOf(addr), mi.getPosition(addr)
    ]);
    $("kPrice").textContent = Number(ethers.formatUnits(price, 8)).toFixed(2);
    $("kWallet").textContent = usd(wbal);
    $("kColl").textContent = usd(coll);
    $("kPnl").innerHTML = `<span class="${pnl < 0n ? "neg" : "pos"}">${usdSigned(pnl)}</span>`;
    $("kEquity").textContent = usdSigned(eq);
    const [n, entry, isLong, open] = pos;
    $("posline").innerHTML = open
      ? `Open: <b class="${isLong ? "pos" : "neg"}">${isLong ? "LONG" : "SHORT"}</b> · notional ${usd(n)} · entry ${Number(ethers.formatUnits(entry, 8)).toFixed(2)}`
      : "No open position.";
  } catch (e) { status(err(e), true); }
}

function err(e) { return "Error: " + (e?.shortMessage || e?.reason || e?.message || String(e)).slice(0, 140); }
