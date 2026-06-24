// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Script} from "forge-std/Script.sol";
import {console2} from "forge-std/console2.sol";
import {NetLossFuture} from "../src/NetLossFuture.sol";

/// @notice Calendar-aligned Net-Loss Future strip: 3 dated series whose settlementTime is the
/// real ABS-EE print date (~22nd of the month after the reference month), sharing the net-loss
/// oracle + MockUSDC. Run: NETLOSS_ORACLE=0x... forge script script/DeployStripDated.s.sol --rpc-url sepolia --broadcast
contract DeployStripDated is Script {
    address constant MOCKUSDC = 0x2A79d10E87ac92a185117ED2C0922d056421a06b;

    function run() external {
        uint256 pk = vm.envUint("PRIVATE_KEY");
        address oracle = vm.envAddress("NETLOSS_ORACLE");

        vm.startBroadcast(pk);
        // refMonth -> settlement (print) date
        NetLossFuture jun = new NetLossFuture(MOCKUSDC, oracle, 2000, 1000, 1784692800, "Net-Loss Future Jun-2026"); // 2026-07-22
        NetLossFuture jul = new NetLossFuture(MOCKUSDC, oracle, 2000, 1000, 1787371200, "Net-Loss Future Jul-2026"); // 2026-08-22
        NetLossFuture aug = new NetLossFuture(MOCKUSDC, oracle, 2000, 1000, 1790049600, "Net-Loss Future Aug-2026"); // 2026-09-22
        vm.stopBroadcast();

        console2.log("Jun-2026:", address(jun));
        console2.log("Jul-2026:", address(jul));
        console2.log("Aug-2026:", address(aug));
    }
}
