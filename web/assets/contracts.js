// Serention testnet config. After deploying (contracts/README.md), paste the
// three printed addresses into `addresses` below and redeploy the site.
window.SERENTION = {
  chainIdHex: "0xaa36a7",            // Sepolia (11155111)
  chainName: "Sepolia",
  priceScale: 1e8,                   // oracle stores index level * 1e8
  usdcDecimals: 6,
  addresses: {
    usdc: "",                        // <- MockUSDC
    oracle: "",                      // <- IndexOracle
    margined: ""                     // <- MarginedIndex
  },
  abi: {
    usdc: [
      "function mint(uint256) external",
      "function approve(address,uint256) external returns (bool)",
      "function balanceOf(address) view returns (uint256)",
      "function allowance(address,address) view returns (uint256)"
    ],
    oracle: [
      "function price() view returns (uint256)",
      "function updatedAt() view returns (uint256)",
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
