// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Script} from "forge-std/Script.sol";
import {console2} from "forge-std/console2.sol";
import {NetLossFuture} from "../src/NetLossFuture.sol";

/// @notice Deploys 2 more dated Net-Loss Future series (M+2, M+3) sharing the existing
/// net-loss oracle + MockUSDC, forming a 3-month strip with the front-month already live.
/// Run: NETLOSS_ORACLE=0x... forge script script/DeployStrip.s.sol --rpc-url sepolia --broadcast
contract DeployStrip is Script {
    address constant MOCKUSDC = 0x2A79d10E87ac92a185117ED2C0922d056421a06b;

    function run() external {
        uint256 pk = vm.envUint("PRIVATE_KEY");
        address oracle = vm.envAddress("NETLOSS_ORACLE");

        vm.startBroadcast(pk);
        NetLossFuture m2 = new NetLossFuture(
            MOCKUSDC, oracle, 2000, 1000, block.timestamp + 66 days, "Serention Net-Loss Future - M+2");
        NetLossFuture m3 = new NetLossFuture(
            MOCKUSDC, oracle, 2000, 1000, block.timestamp + 96 days, "Serention Net-Loss Future - M+3");
        vm.stopBroadcast();

        console2.log("NLF M+2:", address(m2));
        console2.log("NLF M+3:", address(m3));
    }
}
