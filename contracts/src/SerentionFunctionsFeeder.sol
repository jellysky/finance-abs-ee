// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import {FunctionsClient} from "@chainlink/contracts/src/v0.8/functions/v1_0_0/FunctionsClient.sol";
import {FunctionsRequest} from "@chainlink/contracts/src/v0.8/functions/v1_0_0/libraries/FunctionsRequest.sol";

interface ISerentionIndexOracle {
    function setAnswer(int256 answer) external;
}

/// @title  SerentionFunctionsFeeder
/// @notice Chainlink Functions consumer (step 2 of the oracle integration). On request,
///         the DON runs `source` (JS) to fetch the published Serention index level from a
///         URL and returns the affine-rebased positive LEVEL * 1e8 as a uint256. On
///         fulfillment this pushes it into SerentionIndexOracle.setAnswer — so this
///         contract must be set as the oracle's `updater`.
///
///         Trigger requestIndexUpdate() manually, or register it with Chainlink
///         Automation (time-based upkeep) for the monthly cadence and set the upkeep's
///         forwarder via setAutomationForwarder().
contract SerentionFunctionsFeeder is FunctionsClient {
    using FunctionsRequest for FunctionsRequest.Request;

    address public owner;
    address public automationForwarder; // Chainlink Automation upkeep forwarder (set post-registration)
    ISerentionIndexOracle public immutable oracle;

    string public source; // JS the DON executes
    bytes32 public donId;
    uint64 public subscriptionId;
    uint32 public gasLimit = 300_000;

    bytes32 public lastRequestId;
    int256 public lastAnswer;
    bytes public lastError;

    event RequestTriggered(bytes32 indexed requestId);
    event OracleUpdated(bytes32 indexed requestId, int256 answer);
    event FulfillmentFailed(bytes32 indexed requestId, bytes err);

    error NotAuthorized();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotAuthorized();
        _;
    }

    constructor(address router, address _oracle, bytes32 _donId, uint64 _subscriptionId, string memory _source)
        FunctionsClient(router)
    {
        owner = msg.sender;
        oracle = ISerentionIndexOracle(_oracle);
        donId = _donId;
        subscriptionId = _subscriptionId;
        source = _source;
    }

    /// @notice Trigger a DON request to refresh the on-chain index level.
    /// @dev Restricted to owner / the Automation forwarder so randoms can't drain the
    ///      LINK subscription.
    function requestIndexUpdate() external returns (bytes32 requestId) {
        if (msg.sender != owner && msg.sender != automationForwarder) revert NotAuthorized();
        FunctionsRequest.Request memory req;
        req.initializeRequestForInlineJavaScript(source);
        requestId = _sendRequest(req.encodeCBOR(), subscriptionId, gasLimit, donId);
        lastRequestId = requestId;
        emit RequestTriggered(requestId);
    }

    /// @inheritdoc FunctionsClient
    function fulfillRequest(bytes32 requestId, bytes memory response, bytes memory err) internal override {
        if (err.length > 0) {
            lastError = err;
            emit FulfillmentFailed(requestId, err);
            return;
        }
        int256 answer = int256(uint256(bytes32(response))); // JS returns uint256 level*1e8
        if (answer <= 0) {
            emit FulfillmentFailed(requestId, "non-positive answer");
            return;
        }
        lastAnswer = answer;
        oracle.setAnswer(answer); // this feeder must be the oracle's authorized updater
        emit OracleUpdated(requestId, answer);
    }

    // --- admin ------------------------------------------------------------
    function setSource(string calldata s) external onlyOwner {
        source = s;
    }

    function setSubscriptionId(uint64 s) external onlyOwner {
        subscriptionId = s;
    }

    function setDonId(bytes32 d) external onlyOwner {
        donId = d;
    }

    function setGasLimit(uint32 g) external onlyOwner {
        gasLimit = g;
    }

    function setAutomationForwarder(address f) external onlyOwner {
        automationForwarder = f;
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "zero");
        owner = newOwner;
    }
}
