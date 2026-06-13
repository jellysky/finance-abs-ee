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
    margined: "0x59Ef5b42A2E080Bfd317c0AE32b9e902e100F914",  // MarginedIndex (Sepolia)
    feeder: "0x9f37Eb792b60E89465B7b545fe770c591646755b"     // SerentionFunctionsFeeder, Chainlink Functions (Sepolia)
  },
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
    ]
  }
};
