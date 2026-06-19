// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {AggregatorV3Interface} from "./AggregatorV3Interface.sol";

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

/// @title MarginedIndex — a margined, cash-settled PERPETUAL FIXED-vs-FLOATING SWAP on a Serention index.
/// @notice Carry / basis swap on the index's net-yield rate:
///           RECEIVER (long)  earns the floating net-yield, pays the fixed leg;
///           PAYER (short)     pays the floating, receives the fixed leg.
///         PnL accrues CONTINUOUSLY by time at the spread (floating − fixed):
///           pnl = ±notional * (floatingBps − fixedBps) * dt / (1e4 * year)
///         The floating rate is FIXED AT OPEN from the latest index print (the "fixing"); the
///         monthly print re-fixes the rate only for positions opened after it. There is NO term
///         and NO lock-in — either side may close at any instant and settles the carry accrued
///         to that moment (pro-rata by time). Collateral is an ERC-20 stablecoin (USDC; MockUSDC).
/// @dev    UNAUDITED testnet POC. Bad debt floored at 0, one position/address, protocol is the
///         implicit counterparty, single-fixing-at-open accrual. Not for real funds.
contract MarginedIndex {
    IERC20  public immutable collateralToken;       // e.g. USDC (6 decimals)
    AggregatorV3Interface public immutable oracle;  // index net-yield level (%/yr * 1e8)
    uint256 public immutable initialMarginBps;      // 2000 = 20% -> 5x max leverage
    uint256 public immutable maintenanceBps;        // 1000 = 10%
    uint256 public constant LIQ_REWARD_BPS = 50;    // 0.5% of notional to liquidator
    uint256 internal constant YEAR = 365 days;

    address public owner;
    int256  public fixedRateBps;   // the fixed leg / par swap rate (bps, annualized); owner-set

    // isReceiver: receives floating (net yield), pays fixed. entryFloatingBps: floating fixing at open.
    struct Position { uint256 notional; int256 entryFloatingBps; bool isReceiver; bool open; uint256 openedAt; }

    mapping(address => uint256)  public collateral; // token units backing the account
    mapping(address => Position) public positions;

    event Deposited(address indexed user, uint256 amount);
    event Withdrawn(address indexed user, uint256 amount);
    event Opened(address indexed user, bool isReceiver, uint256 notional, int256 entryFloatingBps);
    event Closed(address indexed user, int256 pnl, uint256 collateral);
    event Liquidated(address indexed user, address indexed by, int256 pnl, uint256 reward);
    event FixedRateSet(int256 bps);

    constructor(address _collateral, address _oracle, uint256 _imBps, uint256 _maintBps) {
        require(_maintBps < _imBps, "maint<im");
        owner = msg.sender;
        collateralToken = IERC20(_collateral);
        oracle = AggregatorV3Interface(_oracle);
        initialMarginBps = _imBps;
        maintenanceBps = _maintBps;
    }

    /// @notice Set the fixed leg / par swap rate (bps, annualized). Owner only.
    function setFixedRate(int256 bps) external {
        require(msg.sender == owner, "owner");
        fixedRateBps = bps;
        emit FixedRateSet(bps);
    }

    // --- collateral -------------------------------------------------------
    function deposit(uint256 amount) external {
        require(collateralToken.transferFrom(msg.sender, address(this), amount), "transferFrom");
        collateral[msg.sender] += amount;
        emit Deposited(msg.sender, amount);
    }

    function withdraw(uint256 amount) external {
        require(!positions[msg.sender].open, "position open");
        require(collateral[msg.sender] >= amount, "insufficient");
        collateral[msg.sender] -= amount;
        require(collateralToken.transfer(msg.sender, amount), "transfer");
        emit Withdrawn(msg.sender, amount);
    }

    // --- positions --------------------------------------------------------
    /// @param isReceiver true = receive floating (net yield) / pay fixed; false = pay floating / receive fixed.
    function open(bool isReceiver, uint256 notional) external {
        require(!positions[msg.sender].open, "already open");
        require(notional > 0, "notional");
        uint256 im = notional * initialMarginBps / 10_000;
        require(collateral[msg.sender] >= im, "margin");
        int256 fx = _floatingBps();
        positions[msg.sender] = Position(notional, fx, isReceiver, true, block.timestamp);
        emit Opened(msg.sender, isReceiver, notional, fx);
    }

    function close() external {
        require(positions[msg.sender].open, "no position");
        int256 pnl = _settle(msg.sender);
        emit Closed(msg.sender, pnl, collateral[msg.sender]);
    }

    /// @notice Anyone may liquidate an account whose equity has fallen below maintenance margin.
    function liquidate(address user) external {
        Position memory pos = positions[user];
        require(pos.open, "no position");
        int256 equity = int256(collateral[user]) + _pnl(pos);
        uint256 maint = pos.notional * maintenanceBps / 10_000;
        require(equity < int256(maint), "healthy");

        int256 pnl = _settle(user);
        uint256 reward = pos.notional * LIQ_REWARD_BPS / 10_000;
        if (reward > collateral[user]) reward = collateral[user];
        collateral[user] -= reward;
        collateral[msg.sender] += reward;
        emit Liquidated(user, msg.sender, pnl, reward);
    }

    function _settle(address user) internal returns (int256 pnl) {
        Position memory pos = positions[user];
        pnl = _pnl(pos);
        int256 col = int256(collateral[user]) + pnl;
        collateral[user] = col < 0 ? 0 : uint256(col);   // bad debt floored (POC)
        delete positions[user];
    }

    /// @dev Carry accrued since open: ±notional * (floatingFixedAtOpen − fixed) * dt / (1e4 * year).
    function _pnl(Position memory pos) internal view returns (int256) {
        int256 dt = int256(block.timestamp - pos.openedAt);
        int256 carry = int256(pos.notional) * (pos.entryFloatingBps - fixedRateBps) * dt
                       / (10_000 * int256(YEAR));
        return pos.isReceiver ? carry : -carry;
    }

    /// @dev Floating leg = index net-yield as bps. Oracle answer = (%/yr * 1e8), so /1e6 = bps.
    function _floatingBps() internal view returns (int256) {
        (, int256 answer,,,) = oracle.latestRoundData();
        require(answer > 0, "price");
        return answer / 1e6;
    }

    // --- views (for the UI) ----------------------------------------------
    function pnlOf(address user) public view returns (int256) {
        Position memory pos = positions[user];
        return pos.open ? _pnl(pos) : int256(0);
    }

    function equityOf(address user) public view returns (int256) {
        return int256(collateral[user]) + pnlOf(user);
    }

    function floatingBps() external view returns (int256) {
        return _floatingBps();
    }

    function getPosition(address user)
        external view returns (uint256 notional, int256 entryFloatingBps, bool isReceiver, bool open)
    {
        Position memory pos = positions[user];
        return (pos.notional, pos.entryFloatingBps, pos.isReceiver, pos.open);
    }
}
