// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import {Test} from "forge-std/Test.sol";
import {SerentionIndexOracle} from "../src/SerentionIndexOracle.sol";
import {SerentionFunctionsFeeder} from "../src/SerentionFunctionsFeeder.sol";

/// Tests the feeder's fulfillment path (DON callback -> oracle). The DON only runs on a
/// live network, so here we drive handleOracleFulfillment directly as the router — which
/// is exactly what the router does on Sepolia after the DON returns.
contract FeederTest is Test {
    SerentionIndexOracle oracle;
    SerentionFunctionsFeeder feeder;
    address router = address(0x1111);
    address stranger = address(0xBEEF);

    function setUp() public {
        oracle = new SerentionIndexOracle(9_680_000_000, "test"); // seed level 96.80
        feeder = new SerentionFunctionsFeeder(router, address(oracle), bytes32("don"), 1, "return 0");
        oracle.setUpdater(address(feeder)); // feeder is the authorized writer
    }

    function testFulfillPushesLevelToOracle() public {
        bytes memory resp = abi.encode(uint256(12_500_000_000)); // DON returns level 125.00 * 1e8
        vm.prank(router);
        feeder.handleOracleFulfillment(bytes32(uint256(1)), resp, "");
        (, int256 answer,,,) = oracle.latestRoundData();
        assertEq(answer, 12_500_000_000);
        assertEq(feeder.lastAnswer(), 12_500_000_000);
    }

    function testOnlyRouterCanFulfill() public {
        vm.prank(stranger);
        vm.expectRevert(); // OnlyRouterCanFulfill
        feeder.handleOracleFulfillment(bytes32(uint256(1)), abi.encode(uint256(1e10)), "");
    }

    function testErrorResponseLeavesOracleUnchanged() public {
        (, int256 before,,,) = oracle.latestRoundData();
        vm.prank(router);
        feeder.handleOracleFulfillment(bytes32(uint256(1)), "", "execution error");
        (, int256 afterVal,,,) = oracle.latestRoundData();
        assertEq(afterVal, before); // unchanged
        assertEq(string(feeder.lastError()), "execution error");
    }

    function testNonPositiveAnswerIsSkipped() public {
        (, int256 before,,,) = oracle.latestRoundData();
        vm.prank(router);
        feeder.handleOracleFulfillment(bytes32(uint256(1)), abi.encode(uint256(0)), "");
        (, int256 afterVal,,,) = oracle.latestRoundData();
        assertEq(afterVal, before); // not pushed, no revert
    }

    function testRequestAccessControl() public {
        vm.prank(stranger);
        vm.expectRevert(SerentionFunctionsFeeder.NotAuthorized.selector);
        feeder.requestIndexUpdate(); // reverts at the auth check before touching the router
    }
}
