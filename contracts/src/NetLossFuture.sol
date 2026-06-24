// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {AggregatorV3Interface} from "./AggregatorV3Interface.sol";

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

/// @title NetLossFuture — a dated, cash-settled future on a Serention net-loss rate.
/// @notice One contract series referencing one period. It cash-settles IN ARREARS to the
///         realized net-loss print for that period (set via `settle`). Payoff is linear in
///         the loss rate — there is no `/entry` scaling:
///
///           PnL = side * notional * (loss_mark - loss_entry) / 100
///
///         where the loss rate is a percent (e.g. 9.0 = 9%/yr) and `notional` is the dollar
///         balance being hedged, so a 1-point move = 1% of notional (DV01 = notional/1e4 per bp).
///         LONG gains when losses RISE (the hedge for an ABS holder); SHORT fades them.
///         Before settlement the position marks to the oracle's current loss rate; after
///         settlement it marks to the realized print. Collateral is USDC (MockUSDC on testnet).
/// @dev    UNAUDITED testnet POC. Bad debt floored at 0, one position/address, naive liquidation,
///         protocol is the implicit counterparty. A monthly strip = one deploy per reference month.
contract NetLossFuture {
    IERC20  public immutable collateralToken;       // USDC (6 decimals)
    AggregatorV3Interface public immutable oracle;  // net-loss rate (%/yr * 1e8, 8 decimals)
    uint256 public immutable initialMarginBps;      // 2000 = 20%
    uint256 public immutable maintenanceBps;        // 1000 = 10%
    uint256 public constant LIQ_REWARD_BPS = 50;    // 0.5% of notional to liquidator
    uint256 internal constant SCALE = 1e10;         // %*1e8 -> fraction (/1e8) and percent (/100)

    address public owner;
    uint256 public immutable settlementTime;        // realized print expected at/after this time
    string  public referenceLabel;                  // e.g. "Serention Net-Loss Future — Apr 2026"
    bool    public settled;
    int256  public settlePrice;                     // realized net-loss rate (%/yr * 1e8)

    struct Position { uint256 notional; int256 entryPrice; bool isLong; bool open; }

    mapping(address => uint256)  public collateral;
    mapping(address => Position) public positions;

    event Deposited(address indexed user, uint256 amount);
    event Withdrawn(address indexed user, uint256 amount);
    event Opened(address indexed user, bool isLong, uint256 notional, int256 entryPrice);
    event Closed(address indexed user, int256 pnl, uint256 collateral);
    event Liquidated(address indexed user, address indexed by, int256 pnl, uint256 reward);
    event Settled(int256 realizedLossRate);

    constructor(
        address _collateral, address _oracle, uint256 _imBps, uint256 _maintBps,
        uint256 _settlementTime, string memory _label
    ) {
        require(_maintBps < _imBps, "maint<im");
        require(_settlementTime > block.timestamp, "settle in past");
        owner = msg.sender;
        collateralToken = IERC20(_collateral);
        oracle = AggregatorV3Interface(_oracle);
        initialMarginBps = _imBps;
        maintenanceBps = _maintBps;
        settlementTime = _settlementTime;
        referenceLabel = _label;
    }

    /// @notice Post the realized net-loss print for the reference period; the contract settles to it.
    function settle(int256 realizedLossRate) external {
        require(msg.sender == owner, "owner");
        require(block.timestamp >= settlementTime, "too early");
        require(!settled, "settled");
        require(realizedLossRate >= 0, "rate>=0");
        settled = true;
        settlePrice = realizedLossRate;
        emit Settled(realizedLossRate);
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
    /// @param isLong true = gain when the loss rate rises (hedge an ABS book); false = fade losses.
    function open(bool isLong, uint256 notional) external {
        require(!settled && block.timestamp < settlementTime, "expired");
        require(!positions[msg.sender].open, "already open");
        require(notional > 0, "notional");
        uint256 im = notional * initialMarginBps / 10_000;
        require(collateral[msg.sender] >= im, "margin");
        int256 p = _mark();
        positions[msg.sender] = Position(notional, p, isLong, true);
        emit Opened(msg.sender, isLong, notional, p);
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

    /// @dev Linear payoff: side * notional * (mark - entry) / 100, in USDC base units.
    function _pnl(Position memory pos) internal view returns (int256) {
        int256 mark = _mark();
        int256 raw = int256(pos.notional) * (mark - pos.entryPrice) / int256(SCALE);
        return pos.isLong ? raw : -raw;
    }

    /// @dev Mark = realized print once settled, else the oracle's current net-loss rate (%/yr * 1e8).
    function _mark() internal view returns (int256) {
        if (settled) return settlePrice;
        (, int256 answer,,,) = oracle.latestRoundData();
        require(answer >= 0, "rate");
        return answer;
    }

    // --- views ------------------------------------------------------------
    function markPrice() external view returns (int256) { return _mark(); }

    function pnlOf(address user) public view returns (int256) {
        Position memory pos = positions[user];
        return pos.open ? _pnl(pos) : int256(0);
    }

    function equityOf(address user) external view returns (int256) {
        return int256(collateral[user]) + pnlOf(user);
    }

    function getPosition(address user)
        external view returns (uint256 notional, int256 entryPrice, bool isLong, bool open)
    {
        Position memory pos = positions[user];
        return (pos.notional, pos.entryPrice, pos.isLong, pos.open);
    }
}
