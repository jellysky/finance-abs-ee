// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Script} from "forge-std/Script.sol";
import {console2} from "forge-std/console2.sol";
import {SerentionIndexOracle} from "../src/SerentionIndexOracle.sol";
import {NetLossFuture} from "../src/NetLossFuture.sol";

/// @notice Deploys the Net-Loss Future stack to Sepolia, reusing the existing MockUSDC.
/// Run: forge script script/DeployFuture.s.sol --rpc-url sepolia --broadcast
contract DeployFuture is Script {
    address constant MOCKUSDC = 0x2A79d10E87ac92a185117ED2C0922d056421a06b; // existing Sepolia MockUSDC

    function run() external {
        uint256 pk = vm.envUint("PRIVATE_KEY");
        // Placeholder net-loss rate (%/yr * 1e8); to be fed by the 90+ DPD pipeline (NLI).
        int256 seedLoss = 12_00000000;            // 12.0%/yr
        uint256 settleAt = block.timestamp + 35 days; // ~ next ABS-EE print

        vm.startBroadcast(pk);
        SerentionIndexOracle lossOracle =
            new SerentionIndexOracle(seedLoss, "Serention net-loss rate (pct/yr) x1e8 [placeholder]");
        NetLossFuture fut = new NetLossFuture(
            MOCKUSDC, address(lossOracle), 2000, 1000, settleAt,
            "Serention Net-Loss Future - testnet POC"
        );
        vm.stopBroadcast();

        console2.log("MockUSDC (reused)   :", MOCKUSDC);
        console2.log("NetLoss Oracle      :", address(lossOracle));
        console2.log("NetLossFuture       :", address(fut));
        console2.log("settlementTime      :", settleAt);
    }
}
