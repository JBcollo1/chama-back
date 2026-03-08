# Core Wallet `invalid sender` on Avalanche Fuji

> **Stack:** React (Vite + TypeScript) + FastAPI + Web3.py + Avalanche Fuji Testnet  
> **Symptom:** `eth_sendTransaction` failed with `invalid sender` on every attempt despite a valid address, chainId, and signature  
> **Root Cause:** Core Wallet's internal RPC broadcasting mechanism rejected all transactions for unknown reasons. Resolved by switching to MetaMask.

---

## The Problem

We were building a group creation flow where users sign a transaction to deploy a savings group smart contract on Avalanche Fuji Testnet (chainId: 43113). The backend prepared the transaction — ABI-encoded calldata, EIP-1559 fees, gas estimate — and returned it to the frontend for the user to sign via Core Wallet.

Every attempt failed with the following error from Core Wallet:

```json
{
  "message": "Unable to get transaction hash",
  "code": -32603,
  "data": {
    "cause": {
      "error": {
        "code": -32000,
        "message": "invalid sender"
      },
      "payload": {
        "method": "eth_sendRawTransaction"
      }
    }
  }
}
```

The error originated at `eth_sendRawTransaction` — meaning Core Wallet signed the transaction internally but the resulting raw transaction was rejected by the RPC node it broadcast to.

---

## Why It Happened

We never found a definitive root cause. Every variable was verified and ruled out one by one — the signer matched, the chainId was correct, the contract existed, the wallet had funds, and a previous transaction from the same address to the same contract had succeeded the day before. Core Wallet was internally signing correctly but its RPC layer was rejecting the broadcast.

The leading hypothesis is that **Core Wallet's default Fuji RPC endpoint was misconfigured or rate-limited** at the time of testing. We were unable to override it (adding a custom network to Core Wallet failed), and `eth_signTransaction` is not supported, so we could not separate signing from broadcasting to confirm this.

---

## Why We Didn't Notice It Sooner

Several things masked the real cause and led us down the wrong paths:

1. **The error message was misleading** — `invalid sender` typically means the signer address doesn't match the `from` field. We spent significant time verifying the address, only to confirm it was correct all along.
2. **Core Wallet was caching the raw transaction** — the signature `r: 0xa01420bc...` was identical across every attempt, meaning Core Wallet was replaying the first (failed) raw transaction rather than signing fresh. This made it look like a persistent signing bug.
3. **The nonce mismatch was a red herring** — the backend was fetching nonce `0xe` (= 14) via `eth_getTransactionCount` while the wallet's live nonce was `0x0`. We fixed this first, which was correct, but it was not the actual cause of the failure.
4. **`net_version` was returning the wrong chainId** — our `walletState.networkInfo` showed `43117` instead of `43113` because we were using `net_version` instead of `eth_chainId`. This looked suspicious but turned out not to cause the rejection.
5. **The contract and balance were fine** — the factory contract existed on Fuji, the wallet had 3.50 AVAX, `eth_call` simulation succeeded, and a prior transaction had gone through. All external signals pointed to the code being correct.

---

## The Fix

### 1. Remove the Nonce from `eth_sendTransaction`

The backend was pre-fetching the nonce and baking it into the transaction. This caused a mismatch because Core Wallet maintains its own internal nonce state. Removing it entirely lets the wallet use the correct value:

```typescript
// ❌ Before — nonce from backend caused mismatch
signedTxHash = await provider.request({
  method: 'eth_sendTransaction',
  params: [{ to, from, data, gas, nonce: liveNonce, ... }],
});

// ✅ After — no nonce, wallet manages it
signedTxHash = await provider.request({
  method: 'eth_sendTransaction',
  params: [{ to, from, data, gas, maxFeePerGas, maxPriorityFeePerGas, value, chainId, type }],
});
```

### 2. Fix ChainId Detection

We were using `net_version` which returns an inconsistent decimal string. Core Wallet returned `43117` for a network where `eth_chainId` correctly returns `0xa869` (= `43113`):

```typescript
// ❌ Wrong — net_version returns unreliable decimal
const networkId = await provider.request({ method: 'net_version' });
// → '43117' on Core Wallet (wrong)

// ✅ Correct — eth_chainId is the standard
const chainIdHex = await provider.request({ method: 'eth_chainId' });
const chainId = parseInt(chainIdHex, 16).toString();
// → '0xa869' → '43113' (correct)
```

This was fixed in both `checkWalletConnection` and `connectWallet`, and the network name map was updated:

```typescript
const networks: Record<string, string> = {
  "43113": "Avalanche Fuji Testnet",
  "43114": "Avalanche Mainnet",
  // ...other networks
};
```

### 3. Switch to MetaMask with ethers.js BrowserProvider

With all other fixes in place and the error persisting, we switched the provider from `window.avalanche` (Core Wallet) to `window.ethereum` (MetaMask) and wrapped it with ethers.js `BrowserProvider`:

```typescript
// ❌ Before — Core Wallet via raw eth_sendTransaction
const provider = (window as any).avalanche ?? window.ethereum;
signedTxHash = await provider.request({
  method: 'eth_sendTransaction',
  params: [{ to, from, data, gas, ... }],
});

// ✅ After — MetaMask via ethers BrowserProvider
const provider = window.ethereum;
const ethersProvider = new ethers.BrowserProvider(provider);
const signer = await ethersProvider.getSigner();

const tx = await signer.sendTransaction({
  to:                   prepareResult.transaction.to,
  data:                 prepareResult.transaction.data,
  gasLimit:             prepareResult.transaction.gas,
  maxFeePerGas:         prepareResult.transaction.maxFeePerGas,
  maxPriorityFeePerGas: prepareResult.transaction.maxPriorityFeePerGas,
  value:                0,
  chainId:              43113,
  type:                 2,
});

signedTxHash = tx.hash;
```

### 4. Add Automatic Network Switching

MetaMask supports `wallet_switchEthereumChain` and `wallet_addEthereumChain`, so the app now automatically prompts the user to switch to Fuji rather than throwing a hard error:

```typescript
if (chainId !== '0xa869') {
  try {
    await provider.request({
      method: 'wallet_switchEthereumChain',
      params: [{ chainId: '0xa869' }],
    });
  } catch (switchErr: any) {
    // Error code 4902 means the chain is not in MetaMask yet — add it
    if (switchErr.code === 4902) {
      await provider.request({
        method: 'wallet_addEthereumChain',
        params: [{
          chainId: '0xa869',
          chainName: 'Avalanche Fuji Testnet',
          nativeCurrency: { name: 'AVAX', symbol: 'AVAX', decimals: 18 },
          rpcUrls: ['https://api.avax-test.network/ext/bc/C/rpc'],
          blockExplorerUrls: ['https://testnet.snowtrace.io'],
        }],
      });
    } else {
      throw new Error('Please switch to Avalanche Fuji Testnet in MetaMask.');
    }
  }
}
```

---

## How the Transaction Flow Works

```
Without nonce fix:
  Backend fetches nonce = 14 (from different RPC state)
  Wallet internal nonce = 0
  → Mismatch → invalid sender

With nonce fix but still on Core Wallet:
  Wallet signs correctly (signer verified ✅, chainId verified ✅)
  Core Wallet broadcasts via its own RPC
  → RPC rejects → invalid sender (reason unknown)

With MetaMask:
  Wallet signs correctly
  MetaMask broadcasts via its own RPC
  → ✅ Accepted → tx hash returned → confirmed on Snowtrace
```

---

## Production

The MetaMask fix works in both development and production. In production, ensure the backend `WEB3_PROVIDER_URL` points to the correct network:

```env
# ❌ Wrong — mainnet
WEB3_PROVIDER_URL=https://api.avax.network/ext/bc/C/rpc

# ✅ Correct — Fuji testnet
WEB3_PROVIDER_URL=https://avalanche-fuji.infura.io/v3/YOUR_INFURA_KEY
```

Verify the backend is connected to the right chain on startup:

```python
logger.info(f"Connected to chainId: {self.w3.eth.chain_id}")
# Must log: 43113
```

---

## Checklist

Use this when debugging wallet transaction failures in future:

- [ ] Does the raw tx signer match the expected address? (decode with `ethers.Transaction.from(rawTx)`)
- [ ] Is the backend `WEB3_PROVIDER_URL` pointing to the correct network?
- [ ] Is the wallet connected to the correct chainId? (use `eth_chainId`, not `net_version`)
- [ ] Is the nonce being passed in `eth_sendTransaction`? (remove it — let the wallet manage it)
- [ ] Is the raw transaction signature identical across multiple attempts? (wallet cache — reset account data)
- [ ] Does the contract exist on the target network? (verify on block explorer)
- [ ] Does the wallet have sufficient balance to cover gas? (estimated ~0.26 AVAX on Fuji)
- [ ] Does `eth_call` simulation succeed on the backend? (rules out contract revert)
- [ ] If Core Wallet is being used — switch to MetaMask and retry

---

## Key Takeaway

> If `eth_sendRawTransaction` returns `invalid sender` but the decoded signer matches the `from` address — the problem is in the wallet's RPC broadcasting layer, not your code. The transaction is signed correctly but rejected during broadcast. Switch wallets or override the wallet's RPC endpoint to isolate the issue.