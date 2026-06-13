// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {MockUSDC} from "../src/MockUSDC.sol";
import {SerentionIndexOracle} from "../src/SerentionIndexOracle.sol";
import {MarginedIndex} from "../src/MarginedIndex.sol";

contract MarginedIndexTest is Test {
    MockUSDC usdc;
    SerentionIndexOracle oracle;
    MarginedIndex mi;
    address alice = address(0xA11CE);
    address bob = address(0xB0B);

    function setUp() public {
        usdc = new MockUSDC();
        oracle = new SerentionIndexOracle(10e8, "test index = 10.0"); // index level 10.0
        mi = new MarginedIndex(address(usdc), address(oracle), 2000, 1000); // 20% IM, 10% maint
        vm.startPrank(alice);
        usdc.mint(100_000e6);
        usdc.approve(address(mi), type(uint256).max);
        mi.deposit(100_000e6);
        vm.stopPrank();
    }

    function testLongProfit() public {
        vm.prank(alice);
        mi.open(true, 100_000e6); // notional 100k, IM 20k
        oracle.setAnswer(12e8); // +20%
        assertEq(mi.pnlOf(alice), int256(20_000e6)); // 100k * (12-10)/10 = 20k
        vm.prank(alice);
        mi.close();
        assertEq(mi.collateral(alice), 120_000e6);
    }

    function testShortProfit() public {
        vm.prank(alice);
        mi.open(false, 100_000e6);
        oracle.setAnswer(8e8); // -20%
        assertEq(mi.pnlOf(alice), int256(20_000e6)); // short gains when index falls
        vm.prank(alice);
        mi.close();
        assertEq(mi.collateral(alice), 120_000e6);
    }

    function testLiquidationWhenEquityBelowMaintenance() public {
        vm.prank(alice);
        mi.open(false, 100_000e6); // short
        oracle.setAnswer(18e8); // +80%: short pnl -80k, equity 20k > maint 10k
        vm.expectRevert("healthy");
        mi.liquidate(alice);
        oracle.setAnswer(20e8); // +100%: short pnl -100k, equity 0 < maint
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
        mi.open(true, 1_000_000e6); // IM 200k > 100k collateral
    }

    // --- oracle: Chainlink AggregatorV3 surface ---------------------------

    function testOracleAggregatorV3Surface() public view {
        assertEq(oracle.decimals(), 8);
        assertEq(oracle.version(), 1);
        (uint80 roundId, int256 answer,, uint256 updatedAt, uint80 answeredInRound) = oracle.latestRoundData();
        assertEq(answer, 10e8);
        assertEq(roundId, 1); // seeded in constructor
        assertEq(answeredInRound, 1);
        assertGt(updatedAt, 0);
    }

    function testRoundsIncrementOnUpdate() public {
        oracle.setAnswer(11e8);
        (uint80 roundId, int256 answer,,,) = oracle.latestRoundData();
        assertEq(roundId, 2);
        assertEq(answer, 11e8);
        // prior round is still queryable
        (, int256 prev,,,) = oracle.getRoundData(1);
        assertEq(prev, 10e8);
    }

    function testUpdaterCanWriteOwnerControls() public {
        // a non-writer cannot push
        vm.prank(bob);
        vm.expectRevert("not writer");
        oracle.setAnswer(13e8);
        // owner authorizes bob (stands in for the step-2 Chainlink Functions consumer)
        oracle.setUpdater(bob);
        vm.prank(bob);
        oracle.setAnswer(13e8);
        (, int256 answer,,,) = oracle.latestRoundData();
        assertEq(answer, 13e8);
    }

    function testRejectsNonPositiveAnswer() public {
        vm.expectRevert("answer>0");
        oracle.setAnswer(0);
    }
}
