// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {AggregatorV3Interface} from "./AggregatorV3Interface.sol";

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

/// @title MarginedIndex — a margined, cash-settled position on a Serention index.
/// @notice Total-return payoff: pnl = ±notional * (price - entryPrice) / entryPrice.
///         LONG profits when the index rises (credit deteriorates); SHORT mirrors it.
///         Collateral is an ERC-20 stablecoin (MockUSDC on testnet). One open
///         position per address, for simplicity.
/// @dev    UNAUDITED testnet proof-of-concept. Simplifications: bad debt is floored
///         at zero, no funding rate, single position per user, naive liquidation.
///         Do not use with real funds.
contract MarginedIndex {
    IERC20  public immutable collateralToken;   // e.g. USDC (6 decimals)
    AggregatorV3Interface public immutable oracle;  // index feed (level * 1e8, 8 decimals)
    uint256 public immutable initialMarginBps;  // 2000 = 20%  -> 5x max leverage
    uint256 public immutable maintenanceBps;    // 1000 = 10%
    uint256 public constant LIQ_REWARD_BPS = 50; // 0.5% of notional to liquidator

    struct Position { uint256 notional; uint256 entryPrice; bool isLong; bool open; }

    mapping(address => uint256)   public collateral;  // token units backing the account
    mapping(address => Position)  public positions;

    event Deposited(address indexed user, uint256 amount);
    event Withdrawn(address indexed user, uint256 amount);
    event Opened(address indexed user, bool isLong, uint256 notional, uint256 entryPrice);
    event Closed(address indexed user, int256 pnl, uint256 collateral);
    event Liquidated(address indexed user, address indexed by, int256 pnl, uint256 reward);

    constructor(address _collateral, address _oracle, uint256 _imBps, uint256 _maintBps) {
        require(_maintBps < _imBps, "maint<im");
        collateralToken = IERC20(_collateral);
        oracle = AggregatorV3Interface(_oracle);
        initialMarginBps = _imBps;
        maintenanceBps = _maintBps;
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
    function open(bool isLong, uint256 notional) external {
        require(!positions[msg.sender].open, "already open");
        require(notional > 0, "notional");
        uint256 im = notional * initialMarginBps / 10_000;
        require(collateral[msg.sender] >= im, "margin");
        uint256 p = _price();
        positions[msg.sender] = Position(notional, p, isLong, true);
        emit Opened(msg.sender, isLong, notional, p);
    }

    function close() external {
        require(positions[msg.sender].open, "no position");
        int256 pnl = _settle(msg.sender, _price());
        emit Closed(msg.sender, pnl, collateral[msg.sender]);
    }

    /// @notice Anyone may liquidate an account whose equity has fallen below
    ///         maintenance margin; the liquidator earns a small reward.
    function liquidate(address user) external {
        Position memory pos = positions[user];
        require(pos.open, "no position");
        uint256 p = _price();
        int256 equity = int256(collateral[user]) + _pnl(pos, p);
        uint256 maint = pos.notional * maintenanceBps / 10_000;
        require(equity < int256(maint), "healthy");

        int256 pnl = _settle(user, p);
        uint256 reward = pos.notional * LIQ_REWARD_BPS / 10_000;
        if (reward > collateral[user]) reward = collateral[user];
        collateral[user] -= reward;
        collateral[msg.sender] += reward;
        emit Liquidated(user, msg.sender, pnl, reward);
    }

    function _settle(address user, uint256 p) internal returns (int256 pnl) {
        Position memory pos = positions[user];
        pnl = _pnl(pos, p);
        int256 col = int256(collateral[user]) + pnl;
        collateral[user] = col < 0 ? 0 : uint256(col);   // bad debt floored (POC)
        delete positions[user];
    }

    function _pnl(Position memory pos, uint256 p) internal pure returns (int256) {
        int256 diff = int256(p) - int256(pos.entryPrice);
        int256 pnl = int256(pos.notional) * diff / int256(pos.entryPrice);
        return pos.isLong ? pnl : -pnl;
    }

    /// @dev Latest index level from the AggregatorV3 feed (8 decimals). Reverts on a
    ///      non-positive answer (the affine level convention keeps it > 0).
    function _price() internal view returns (uint256) {
        (, int256 answer,,,) = oracle.latestRoundData();
        require(answer > 0, "price");
        return uint256(answer);
    }

    // --- views (for the UI) ----------------------------------------------
    function pnlOf(address user) public view returns (int256) {
        Position memory pos = positions[user];
        if (!pos.open) return 0;
        return _pnl(pos, _price());
    }

    function equityOf(address user) public view returns (int256) {
        return int256(collateral[user]) + pnlOf(user);
    }

    function getPosition(address user)
        external view returns (uint256 notional, uint256 entryPrice, bool isLong, bool open)
    {
        Position memory pos = positions[user];
        return (pos.notional, pos.entryPrice, pos.isLong, pos.open);
    }
}
