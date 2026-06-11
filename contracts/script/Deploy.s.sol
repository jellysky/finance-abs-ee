// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Script} from "forge-std/Script.sol";
import {console2} from "forge-std/console2.sol";
import {MockUSDC} from "../src/MockUSDC.sol";
import {IndexOracle} from "../src/IndexOracle.sol";
import {MarginedIndex} from "../src/MarginedIndex.sol";

/// @notice Deploys the full testnet stack to Sepolia.
/// Run: forge script script/Deploy.s.sol --rpc-url sepolia --broadcast
///      (needs PRIVATE_KEY and SEPOLIA_RPC_URL in the environment / .env)
contract Deploy is Script {
    function run() external {
        uint256 pk = vm.envUint("PRIVATE_KEY");
        // Seed the oracle with the latest Auto Subprime 30+ DPD reading (19.52%) * 1e8.
        uint256 initialPrice = 1_952_000_000;

        vm.startBroadcast(pk);
        MockUSDC usdc = new MockUSDC();
        IndexOracle oracle = new IndexOracle(initialPrice, "Serention Auto Subprime - 30+ DPD (%) x1e8");
        MarginedIndex mi = new MarginedIndex(address(usdc), address(oracle), 2000, 1000); // 20% IM, 10% maint
        vm.stopBroadcast();

        console2.log("MockUSDC      :", address(usdc));
        console2.log("IndexOracle   :", address(oracle));
        console2.log("MarginedIndex :", address(mi));
        console2.log("-> paste these into web/assets/contracts.js");
    }
}
