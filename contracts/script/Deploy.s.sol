// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Script} from "forge-std/Script.sol";
import {console2} from "forge-std/console2.sol";
import {MockUSDC} from "../src/MockUSDC.sol";
import {SerentionIndexOracle} from "../src/SerentionIndexOracle.sol";
import {MarginedIndex} from "../src/MarginedIndex.sol";
import {SerentionFunctionsFeeder} from "../src/SerentionFunctionsFeeder.sol";

/// @notice Deploys the full testnet stack to Sepolia (incl. the Chainlink Functions feeder).
/// Run: forge script script/Deploy.s.sol --rpc-url sepolia --broadcast
///      (needs PRIVATE_KEY and SEPOLIA_RPC_URL in the environment / .env)
///      Optionally set FUNCTIONS_SUB_ID once the Functions subscription exists; otherwise
///      it deploys with 0 and you call feeder.setSubscriptionId(<id>) afterwards.
contract Deploy is Script {
    // Chainlink Functions on Ethereum Sepolia. VERIFY against docs.chain.link — these
    // can change; the DON id below encodes "fun-ethereum-sepolia-1".
    address constant FUNCTIONS_ROUTER = 0xb83E47C2bC239B3bf370bc41e1459A34b41238D0;
    bytes32 constant DON_ID = 0x66756e2d657468657265756d2d7365706f6c69612d3100000000000000000000;

    function run() external {
        uint256 pk = vm.envUint("PRIVATE_KEY");
        // Seed with the z-score stress index, affine-rebased to a positive level:
        //   level = 100 + 25 * stress;  answer = level * 1e8
        // latest stress -0.128 -> 96.80 -> 9_680_000_000.
        int256 initialAnswer = 9_680_000_000;
        string memory source = vm.readFile("functions/index-source.js");
        uint64 subId = uint64(vm.envOr("FUNCTIONS_SUB_ID", uint256(0)));

        vm.startBroadcast(pk);
        MockUSDC usdc = new MockUSDC();
        SerentionIndexOracle oracle =
            new SerentionIndexOracle(initialAnswer, "Serention Auto Subprime stress (100+25*sigma) x1e8");
        MarginedIndex mi = new MarginedIndex(address(usdc), address(oracle), 2000, 1000); // 20% IM, 10% maint
        SerentionFunctionsFeeder feeder =
            new SerentionFunctionsFeeder(FUNCTIONS_ROUTER, address(oracle), DON_ID, subId, source);
        oracle.setUpdater(address(feeder)); // let the Functions feeder push index levels
        vm.stopBroadcast();

        console2.log("MockUSDC                :", address(usdc));
        console2.log("SerentionIndexOracle    :", address(oracle));
        console2.log("MarginedIndex           :", address(mi));
        console2.log("SerentionFunctionsFeeder:", address(feeder));
        console2.log("-> paste oracle/usdc/margin into web/assets/contracts.js");
        console2.log("-> add the feeder as a consumer on your Functions subscription, then");
        console2.log("   feeder.setSubscriptionId(<subId>) if FUNCTIONS_SUB_ID was not set");
    }
}
