// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {MockUSDC} from "../src/MockUSDC.sol";
import {IndexOracle} from "../src/IndexOracle.sol";
import {MarginedIndex} from "../src/MarginedIndex.sol";

contract MarginedIndexTest is Test {
    MockUSDC usdc;
    IndexOracle oracle;
    MarginedIndex mi;
    address alice = address(0xA11CE);
    address bob = address(0xB0B);

    function setUp() public {
        usdc = new MockUSDC();
        oracle = new IndexOracle(10e8, "test index = 10.0"); // index level 10.0
        mi = new MarginedIndex(address(usdc), address(oracle), 2000, 1000); // 20% IM, 10% maint
        vm.startPrank(alice);
        usdc.mint(100_000e6);
        usdc.approve(address(mi), type(uint256).max);
        mi.deposit(100_000e6);
        vm.stopPrank();
    }

    function testLongProfit() public {
        vm.prank(alice);
        mi.open(true, 100_000e6);          // notional 100k, IM 20k
        oracle.setPrice(12e8);             // +20%
        assertEq(mi.pnlOf(alice), int256(20_000e6)); // 100k * (12-10)/10 = 20k
        vm.prank(alice);
        mi.close();
        assertEq(mi.collateral(alice), 120_000e6);
    }

    function testShortProfit() public {
        vm.prank(alice);
        mi.open(false, 100_000e6);
        oracle.setPrice(8e8);              // -20%
        assertEq(mi.pnlOf(alice), int256(20_000e6)); // short gains when index falls
        vm.prank(alice);
        mi.close();
        assertEq(mi.collateral(alice), 120_000e6);
    }

    function testLiquidationWhenEquityBelowMaintenance() public {
        vm.prank(alice);
        mi.open(false, 100_000e6);         // short
        oracle.setPrice(18e8);             // +80%: short pnl -80k, equity 20k > maint 10k
        vm.expectRevert("healthy");
        mi.liquidate(alice);
        oracle.setPrice(20e8);             // +100%: short pnl -100k, equity 0 < maint
        vm.prank(bob);
        mi.liquidate(alice);
        (,,, bool open) = mi.getPosition(alice);
        assertFalse(open);
    }

    function testCannotWithdrawWhileOpen() public {
        vm.startPrank(alice);
        mi.open(true, 50_000e6);
        vm.expectRevert("position open");
        mi.withdraw(1);
        vm.stopPrank();
    }

    function testInsufficientMarginReverts() public {
        vm.prank(alice);
        vm.expectRevert("margin");
        mi.open(true, 1_000_000e6);        // IM 200k > 100k collateral
    }
}
