// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {MockUSDC} from "../src/MockUSDC.sol";
import {SerentionIndexOracle} from "../src/SerentionIndexOracle.sol";
import {NetLossFuture} from "../src/NetLossFuture.sol";

contract NetLossFutureTest is Test {
    MockUSDC usdc;
    SerentionIndexOracle oracle;
    NetLossFuture fut;
    address alice = address(0xA11CE);
    address bob = address(0xB0B);
    uint256 settleAt;

    // net-loss rate starts at 9.0%/yr; Alice posts the 20% initial margin on 100k notional.
    function setUp() public {
        usdc = new MockUSDC();
        oracle = new SerentionIndexOracle(9e8, "net loss rate 9.0%/yr"); // 9.0 * 1e8
        settleAt = block.timestamp + 30 days;
        fut = new NetLossFuture(address(usdc), address(oracle), 2000, 1000, settleAt, "NLF Apr-2026");
        vm.startPrank(alice);
        usdc.mint(20_000e6);
        usdc.approve(address(fut), type(uint256).max);
        fut.deposit(20_000e6);
        vm.stopPrank();
    }

    // LONG gains when the loss rate rises (the ABS-book hedge).
    function testLongGainsWhenLossRises() public {
        vm.prank(alice);
        fut.open(true, 100_000e6);
        oracle.setAnswer(11e8); // +2 points (9 -> 11)
        assertEq(fut.pnlOf(alice), int256(2_000e6)); // 2% of 100k = $2,000
        vm.prank(alice);
        fut.close();
        assertEq(fut.collateral(alice), 22_000e6);
    }

    // SHORT gains when the loss rate falls.
    function testShortGainsWhenLossFalls() public {
        vm.prank(alice);
        fut.open(false, 100_000e6);
        oracle.setAnswer(7e8); // -2 points
        assertEq(fut.pnlOf(alice), int256(2_000e6));
    }

    // Settlement in arrears: realized print becomes the mark.
    function testSettlesToRealizedPrint() public {
        vm.prank(alice);
        fut.open(true, 100_000e6); // entry 9.0
        vm.warp(settleAt);
        fut.settle(12e8); // realized net loss 12.0%/yr for the reference month
        vm.prank(alice);
        fut.close();
        assertEq(fut.collateral(alice), 23_000e6); // 20k + 3% of 100k
    }

    function testCannotOpenAfterExpiry() public {
        vm.warp(settleAt);
        vm.prank(alice);
        vm.expectRevert("expired");
        fut.open(true, 100_000e6);
    }

    function testCannotSettleEarly() public {
        vm.expectRevert("too early");
        fut.settle(10e8);
    }

    function testOnlyOwnerSettles() public {
        vm.warp(settleAt);
        vm.prank(bob);
        vm.expectRevert("owner");
        fut.settle(10e8);
    }

    // A short that's deep underwater on a loss spike gets liquidated.
    function testLiquidationOnLossSpike() public {
        vm.prank(alice);
        fut.open(false, 100_000e6); // short; loses as loss rate rises
        oracle.setAnswer(15e8); // +6 -> short pnl -6k -> equity 14k > 10k maint: healthy
        vm.expectRevert("healthy");
        fut.liquidate(alice);
        oracle.setAnswer(20e8); // +11 -> short pnl -11k -> equity 9k < 10k: liquidatable
        vm.prank(bob);
        fut.liquidate(alice);
        (,,, bool open) = fut.getPosition(alice);
        assertFalse(open);
    }

    function testMarkPriceTracksOracleThenSettlement() public view {
        assertEq(fut.markPrice(), 9e8);
    }
}
