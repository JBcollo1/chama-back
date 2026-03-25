import os
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from web3 import Web3
from web3.middleware import geth_poa_middleware
from web3.exceptions import ContractLogicError, TransactionNotFound
from web3.types import TxParams, Wei
from hexbytes import HexBytes
from eth_account import Account
from eth_utils import is_address, to_checksum_address
import logging
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Web3ServiceError(Exception):
    """Raised for Web3/blockchain operation failures."""
    pass

class Web3Service:
    def __init__(self):
        
        self.provider_url = os.getenv('FUJI_RPC', 'http://127.0.0.1:8545')
        self.w3 = Web3(Web3.HTTPProvider(self.provider_url))
        
        
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        
   
        self.factory_address = os.getenv('FACTORY_CONTRACT_ADDRESS', '0xca0009AF8E28ccfeAA5bB314fD32856B3d278BF7')
        
        # Load contract ABI - no fallback, let it throw error if missing
        self.factory_abi = self._load_contract_abi()
        self.group_abi = self._load_group_contract_abi()
        
        # Initialize contract instance
        self.factory_contract = self.w3.eth.contract(
            address=to_checksum_address(self.factory_address),
            abi=self.factory_abi
        )
        
        # Optional: Load private key only for admin operations
        self._initialize_admin_account()
        
        # Gas configuration
        self.default_gas_limit = int(os.getenv('DEFAULT_GAS_LIMIT', '2000000'))
        self.default_gas_price = os.getenv('DEFAULT_GAS_PRICE', '20')  # gwei
        
        # Verify connection on initialization
        self._verify_connection()

  

    def is_connected(self) -> bool:
        """Check if Web3 is connected"""
        try:
            return self.w3.is_connected()
        except Exception:
            return False


    def _parse_web3_error(self, error: Exception) -> str:
        """Extract human-readable message from Web3/RPC errors"""
        err_str = str(error)
        
        # Contract revert with reason string
        if 'execution reverted' in err_str:
            match = re.search(r"execution reverted: (.+?)(?:'|\"|}|$)", err_str)
            if match:
                return f"Contract rejected transaction: {match.group(1)}"
            return "Contract rejected transaction (no reason given)"
        
        # Insufficient funds
        if 'insufficient funds' in err_str.lower():
            return "Insufficient funds to cover gas cost"
        
        # Gas too low
        if 'intrinsic gas too low' in err_str.lower():
            return "Gas limit too low for this transaction"
        
        # Nonce issues
        if 'nonce too low' in err_str.lower():
            return "Transaction nonce conflict — try again"
        if 'nonce too high' in err_str.lower():
            return "Transaction nonce too high — wallet may be out of sync"
        
        # Gas price too low
        if 'gas price too low' in err_str.lower() or 'underpriced' in err_str.lower():
            return "Gas price too low — network is congested"
        
        return f"Blockchain error: {err_str}"



    def _initialize_admin_account(self):
        """Initialize admin account from private key (optional, only for admin operations)"""
        self.private_key = os.getenv("PRIVATE_KEY")
        self.admin_account = None
        
        if self.private_key:
            try:
                if not self.private_key.startswith('0x'):
                    self.private_key = '0x' + self.private_key
                self.admin_account = Account.from_key(self.private_key)
                logger.info(f"Initialized admin account: {self.admin_account.address}")
            except Exception as e:
                logger.warning(f"Admin account initialization failed: {str(e)}")
        else:
            logger.info("No admin private key provided - admin operations will be disabled")

        
    def _verify_connection(self):
        if not self.is_connected():
            raise Web3ServiceError(f"Failed to connect to Web3 provider: {self.provider_url}")
        logger.info(f"Successfully connected to Web3 provider: {self.provider_url}")
        # Remove the get_group_counter() call — it doesn't exist on this service

        
    def _load_contract_abi(self) -> List[Dict[str, Any]]:
        """Load factory contract ABI from artifacts - throw error if missing"""
        abi_file_path = os.getenv('CONTRACT_ABI_PATH', './artifacts/contracts/ChamaFactory.sol/ChamaFactory.json')
        
        with open(abi_file_path, 'r') as f:
            contract_artifact = json.load(f)
            logger.info(f"Loaded Factory ABI from {abi_file_path}")
            return contract_artifact['abi']
    
    def _load_group_contract_abi(self) -> List[Dict[str, Any]]:
        """Load group contract ABI from artifacts"""
        abi_file_path = os.getenv('GROUP_ABI_PATH', './artifacts/contracts/ChamaGroup.sol/ChamaGroup.json')
        
        with open(abi_file_path, 'r') as f:
            contract_artifact = json.load(f)
            logger.info(f"Loaded Group ABI from {abi_file_path}")
            return contract_artifact['abi']


