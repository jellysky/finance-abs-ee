// Chainlink Functions source — runs on the DON.
// Fetches the published Serention Auto Subprime index and returns the affine-rebased,
// strictly-positive index LEVEL scaled by 1e8 as a uint256, for SerentionIndexOracle.
//
//   level  = 100 + 25 * stress_index   (z-score average; floored > 0)
//   answer = round(level * 1e8)
//
// To switch the published series later (e.g. net yield), change which field is read
// and/or the transform here — no contract change needed.

const resp = await Functions.makeHttpRequest({
  url: "https://www.serention.com/data/auto-subprime.json",
  timeout: 9000,
});

if (resp.error || !resp.data || !resp.data.latest) {
  throw Error("serention index fetch failed");
}

const stress = Number(resp.data.latest.stress);
if (!isFinite(stress)) {
  throw Error("bad stress value");
}

let level = 100 + 25 * stress; // affine rebasing to a positive index level
if (level < 0.01) level = 0.01; // keep strictly positive for the oracle

const answer = Math.round(level * 1e8);
return Functions.encodeUint256(answer);
