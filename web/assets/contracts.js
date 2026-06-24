// Serention testnet config. After deploying (contracts/README.md), paste the
// three printed addresses into `addresses` below and redeploy the site.
window.SERENTION = {
  chainIdHex: "0xaa36a7",            // Sepolia (11155111)
  chainName: "Sepolia",
  readRpc: "https://ethereum-sepolia-rpc.publicnode.com",  // reliable RPC for reads (not wallet-dependent)
  priceScale: 1e8,                   // oracle stores index level * 1e8
  usdcDecimals: 6,
  addresses: {
    usdc: "0x2A79d10E87ac92a185117ED2C0922d056421a06b",      // MockUSDC (Sepolia)
    oracle: "0x99e3Eee494164F28781cDF8612bce410CaBA0826",    // SerentionIndexOracle, AggregatorV3 (Sepolia)
    margined: "0x59Ef5b42A2E080Bfd317c0AE32b9e902e100F914",  // MarginedIndex (Sepolia, prior POC)
    feeder: "0x9f37Eb792b60E89465B7b545fe770c591646755b",    // SerentionFunctionsFeeder, Chainlink Functions (Sepolia)
    netLossFuture: "0x5bb21f95f6a2b44c57fc4ab178491add597b3092", // NetLossFuture (Sepolia)
    netLossOracle: "0xb1405f63aadf7d87d81dd6f18590bd7fd7d6e542"  // net-loss rate oracle (Sepolia)
  },
  // Net-Loss Future strip (dated monthly series, shared net-loss oracle) — a forward loss curve.
  strip: [
    { label: "Front month", address: "0x5bb21f95f6a2b44c57fc4ab178491add597b3092" },
    { label: "M+2",         address: "0x91424bf7747bcd30587d2f93ce8033391ad68efd" },
    { label: "M+3",         address: "0x0cb6e40a1f2b7c808c3bcf0db90f11db8fcf32ba" }
  ],
  abi: {
    usdc: [
      "function mint(uint256) external",
      "function approve(address,uint256) external returns (bool)",
      "function balanceOf(address) view returns (uint256)",
      "function allowance(address,address) view returns (uint256)"
    ],
    oracle: [
      "function latestRoundData() view returns (uint80,int256,uint256,uint256,uint80)",
      "function decimals() view returns (uint8)",
      "function description() view returns (string)"
    ],
    margined: [
      "function deposit(uint256) external",
      "function withdraw(uint256) external",
      "function open(bool,uint256) external",
      "function close() external",
      "function liquidate(address) external",
      "function collateral(address) view returns (uint256)",
      "function pnlOf(address) view returns (int256)",
      "function equityOf(address) view returns (int256)",
      "function getPosition(address) view returns (uint256,uint256,bool,bool)",
      "function initialMarginBps() view returns (uint256)"
    ],
    netLossFuture: [
      "function deposit(uint256) external",
      "function withdraw(uint256) external",
      "function open(bool,uint256) external",
      "function close() external",
      "function collateral(address) view returns (uint256)",
      "function pnlOf(address) view returns (int256)",
      "function equityOf(address) view returns (int256)",
      "function getPosition(address) view returns (uint256,int256,bool,bool)",
      "function markPrice() view returns (int256)",
      "function settled() view returns (bool)",
      "function settlementTime() view returns (uint256)",
      "function referenceLabel() view returns (string)"
    ]
  }
};
