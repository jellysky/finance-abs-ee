# Serention testnet contracts (Foundry)

An **unaudited proof-of-concept** for a margined, cash-settled position on a
Serention index, for Ethereum **Sepolia** testnet. Not for mainnet / real funds.

| Contract | Purpose |
|---|---|
| `MockUSDC.sol` | 6-decimal test stablecoin with an open `mint()` faucet (margin collateral). |
| `IndexOracle.sol` | Holds the current index value on-chain (level × 1e8); the off-chain pipeline pushes updates via `setPrice`. |
| `MarginedIndex.sol` | Deposit USDC, open a long/short position on the index, settle PnL = ±notional·(price−entry)/entry, liquidate undercollateralized accounts. 20% initial / 10% maintenance margin. |

## Setup
```
# install Foundry once
curl -L https://foundry.paradigm.xyz | bash && foundryup

cd contracts
forge install foundry-rs/forge-std --no-commit   # test/script dependency
forge build
forge test -vvv
```
`forge test` exercises long/short profit, liquidation below maintenance, the
withdraw-while-open guard, and the margin check.

## Deploy to Sepolia
```
export PRIVATE_KEY=0x...                         # a funded Sepolia test wallet
export SEPOLIA_RPC_URL=https://sepolia.infura.io/v3/<key>   # or Alchemy, etc.

forge script script/Deploy.s.sol --rpc-url sepolia --broadcast
```
The script prints the three deployed addresses. Paste them into
`web/assets/contracts.js` (the `addresses` block) and the trade UI is live.

## Feeding the oracle
The index is computed off-chain by this repo's pipeline. To publish a fresh
value, call `IndexOracle.setPrice(level * 1e8)` from the owner key each period
(e.g. the latest 30+ DPD reading 19.52% → `1952000000`). A small cron/script can
read `csv/index_marks.csv` and push the latest mark.

## Design notes / POC caveats
- One open position per address; cash-settled; no funding rate.
- Bad debt (loss exceeding collateral) is floored at zero rather than socialized.
- Liquidation is naive (anyone can call once equity < maintenance; fixed reward).
- Oracle is a single trusted publisher — fine for testnet, would need
  decentralizing / signing for production.
