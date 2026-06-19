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

    // index net yield 10.0%/yr -> floatingBps 1000; fixed leg 4% -> spread 6%/yr (600 bps).
    // Alice posts exactly the 20% initial margin on 100k notional = 20k.
    function setUp() public {
        usdc = new MockUSDC();
        oracle = new SerentionIndexOracle(10e8, "net yield = 10.0%/yr"); // 10.0 * 1e8
        mi = new MarginedIndex(address(usdc), address(oracle), 2000, 1000); // 20% IM, 10% maint
        mi.setFixedRate(400); // 4%/yr fixed leg
        vm.startPrank(alice);
        usdc.mint(20_000e6);
        usdc.approve(address(mi), type(uint256).max);
        mi.deposit(20_000e6);
        vm.stopPrank();
    }

    // RECEIVER earns the floating-minus-fixed carry over time.
    function testReceiverEarnsCarry() public {
        vm.prank(alice);
        mi.open(true, 100_000e6); // receive net yield, pay fixed; spread = 6%/yr
        vm.warp(block.timestamp + 365 days);
        assertEq(mi.pnlOf(alice), int256(6_000e6)); // 6% * 100k = 6k for the year
        vm.prank(alice);
        mi.close();
        assertEq(mi.collateral(alice), 26_000e6); // 20k collateral + 6k carry
    }

    // PAYER owes the same carry when the spread is positive.
    function testPayerOwesCarry() public {
        vm.prank(alice);
        mi.open(false, 100_000e6);
        vm.warp(block.timestamp + 365 days);
        assertEq(mi.pnlOf(alice), int256(-6_000e6));
    }

    // The headline scenario: open and close inside one week (no new index print).
    function testInOutOneWeek() public {
        vm.prank(alice);
        mi.open(true, 100_000e6); // floating fixed at open = 1000 bps
        vm.warp(block.timestamp + 7 days); // no new print -> floating unchanged for this position
        int256 expected = int256(100_000e6) * 600 * int256(uint256(7 days)) / (10_000 * int256(uint256(365 days)));
        assertEq(mi.pnlOf(alice), expected); // ~ $115.07 of carry for the week
        vm.prank(alice);
        mi.close();
        assertEq(mi.collateral(alice), uint256(int256(20_000e6) + expected));
    }

    // A payer whose adverse carry eats through maintenance margin gets liquidated.
    function testLiquidationOnAdverseCarry() public {
        vm.prank(alice);
        mi.open(false, 100_000e6); // pays 6%/yr; equity = 20k - carry, maint = 10k
        vm.warp(block.timestamp + 365 days); // carry 6k -> equity 14k > 10k: still healthy
        vm.expectRevert("healthy");
        mi.liquidate(alice);
        vm.warp(block.timestamp + 365 days); // carry 12k -> equity 8k < 10k: liquidatable
        vm.prank(bob);
        mi.liquidate(alice);
        (,,, bool open) = mi.getPosition(alice);
        assertFalse(open);
    }

    function testOnlyOwnerSetsFixedRate() public {
        vm.prank(bob);
        vm.expectRevert("owner");
        mi.setFixedRate(900);
    }

    function testCannotWithdrawWhileOpen() public {
        vm.startPrank(alice);
        mi.open(true, 50_000e6);
        vm.expectRevert("position open");
        mi.withdraw(1);
        vm.stopPrank();
    }

    // --- oracle: Chainlink AggregatorV3 surface + floating leg ------------

    function testOracleAggregatorV3Surface() public view {
        assertEq(oracle.decimals(), 8);
        (uint80 roundId, int256 answer,,,) = oracle.latestRoundData();
        assertEq(answer, 10e8);
        assertEq(roundId, 1);
    }

    function testFloatingBpsFromOracle() public view {
        assertEq(mi.floatingBps(), int256(1000)); // 10.0%/yr -> 1000 bps
    }

    function testRejectsNonPositiveAnswer() public {
        vm.expectRevert("answer>0");
        oracle.setAnswer(0);
    }
}
