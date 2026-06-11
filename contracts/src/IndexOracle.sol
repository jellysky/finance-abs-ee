// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IndexOracle — publishes a Serention index value on-chain.
/// @notice The off-chain pipeline pushes the latest index level here each period.
///         `price` is the index level scaled by 1e8 (e.g. a 30+ DPD reading of
///         19.52% is stored as 19.52 * 1e8 = 1_952_000_000). Scale is arbitrary so
///         long as the consuming contract uses ratios (entry vs current).
contract IndexOracle {
    address public owner;
    uint256 public price;       // index level * 1e8
    uint256 public updatedAt;   // unix timestamp of last update
    string public description;

    event PriceUpdated(uint256 price, uint256 timestamp);
    event OwnershipTransferred(address indexed from, address indexed to);

    modifier onlyOwner() { require(msg.sender == owner, "owner"); _; }

    constructor(uint256 initialPrice, string memory desc) {
        owner = msg.sender;
        price = initialPrice;
        updatedAt = block.timestamp;
        description = desc;
        emit PriceUpdated(initialPrice, block.timestamp);
    }

    /// @notice Push a new index value (publisher only).
    function setPrice(uint256 newPrice) external onlyOwner {
        require(newPrice > 0, "price>0");
        price = newPrice;
        updatedAt = block.timestamp;
        emit PriceUpdated(newPrice, block.timestamp);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "zero");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }
}
