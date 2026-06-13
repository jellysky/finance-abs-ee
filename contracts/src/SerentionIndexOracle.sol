// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {AggregatorV3Interface} from "./AggregatorV3Interface.sol";

/// @title  SerentionIndexOracle
/// @notice Publishes a Serention index level on-chain behind Chainlink's standard
///         AggregatorV3Interface, so `MarginedIndex` (or any AggregatorV3 consumer,
///         or a future real Chainlink feed) reads it identically.
///
///         `answer` is the index LEVEL scaled by 1e8 (decimals() == 8). The level is
///         computed off-chain and pushed here. For the current z-score stress
///         composite the convention is an affine rebasing that keeps it strictly
///         positive and tradeable:
///
///             level  = 100 + 25 * stress_index        (floored > 0)
///             answer = round(level * 1e8)
///
///         e.g. stress -0.128 -> 96.80 -> answer 9_680_000_000. The transform lives
///         OFF-chain so the oracle stays generic: to switch the published series
///         (e.g. to the net-yield index) you just push that level instead.
///
///         STEP 1 of the Chainlink integration: pushed via a standard interface by
///         the owner (or an authorized `updater`). STEP 2 will set a Chainlink
///         Functions consumer (the DON) as `updater`, fetching the level from the
///         published index JSON — the hook is already here.
contract SerentionIndexOracle is AggregatorV3Interface {
    uint8 private constant DECIMALS = 8;
    uint256 private constant VERSION = 1;

    address public owner;
    address public updater; // optional 2nd writer — the Chainlink Functions consumer in step 2
    string public descriptionText;

    struct Round {
        int256 answer;
        uint256 updatedAt;
    }

    uint80 public latestRound;
    mapping(uint80 => Round) private rounds;

    event AnswerUpdated(int256 indexed answer, uint80 indexed roundId, uint256 updatedAt);
    event UpdaterSet(address indexed updater);
    event OwnershipTransferred(address indexed from, address indexed to);

    modifier onlyOwner() {
        require(msg.sender == owner, "owner");
        _;
    }

    modifier onlyWriter() {
        require(msg.sender == owner || (updater != address(0) && msg.sender == updater), "not writer");
        _;
    }

    constructor(int256 initialAnswer, string memory desc) {
        owner = msg.sender;
        descriptionText = desc;
        _setAnswer(initialAnswer);
    }

    // --- writes -----------------------------------------------------------

    /// @notice Push a new index level (owner or authorized updater). New round id.
    function setAnswer(int256 answer) external onlyWriter {
        _setAnswer(answer);
    }

    function _setAnswer(int256 answer) internal {
        require(answer > 0, "answer>0");
        uint80 r = ++latestRound;
        rounds[r] = Round(answer, block.timestamp);
        emit AnswerUpdated(answer, r, block.timestamp);
    }

    /// @notice Authorize a second writer (e.g. the Chainlink Functions consumer).
    function setUpdater(address newUpdater) external onlyOwner {
        updater = newUpdater;
        emit UpdaterSet(newUpdater);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "zero");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    // --- AggregatorV3Interface --------------------------------------------

    function decimals() external pure returns (uint8) {
        return DECIMALS;
    }

    function description() external view returns (string memory) {
        return descriptionText;
    }

    function version() external pure returns (uint256) {
        return VERSION;
    }

    function getRoundData(uint80 _roundId)
        public
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        Round memory rd = rounds[_roundId];
        require(rd.updatedAt != 0, "no data");
        return (_roundId, rd.answer, rd.updatedAt, rd.updatedAt, _roundId);
    }

    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        return getRoundData(latestRound);
    }
}
