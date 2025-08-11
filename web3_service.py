import os
import json
import asyncio
from datetime import datetime
from decimal import Decimal
from typing import List, Dict, Any, Optional, Tuple
from web3 import Web3
from web3.middleware import geth_poa_middleware
from web3.exceptions import ContractLogicError, TransactionNotFound, BlockNotFound
from eth_account import Account
from eth_utils import is_address, to_checksum_address
import logging

from schemas import GroupCreate

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Web3ServiceError(Exception):
    """Custom exception for Web3Service errors"""
    pass

class Web3Service:
    def __init__(self):
        
        self.provider_url = os.getenv('FUJI_RPC', 'http://127.0.0.1:8545')
        self.w3 = Web3(Web3.HTTPProvider(self.provider_url))
        
        
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        
   
        self.factory_address = os.getenv('FACTORY_CONTRACT_ADDRESS', '0xca0009AF8E28ccfeAA5bB314fD32856B3d278BF7')
        
        # Load contract ABI
        self.factory_abi = self._load_contract_abi()
        
        # Initialize contract instance
        self.factory_contract = self.w3.eth.contract(
            address=self.factory_address,
            abi=self.factory_abi
        )
        
        # Load private key for transactions
        self._initialize_account()
        
        # Gas configuration
        self.default_gas_limit = int(os.getenv('DEFAULT_GAS_LIMIT', '2000000'))
        self.default_gas_price = os.getenv('DEFAULT_GAS_PRICE', '20')  # gwei
        
        # Verify connection on initialization
        self._verify_connection()
    
    def _initialize_account(self):
        """Initialize account from private key"""
        self.private_key = os.getenv("PRIVATE_KEY")
        if not self.private_key:
            raise ValueError("PRIVATE_KEY environment variable not set")
        
        try:
            if not self.private_key.startswith('0x'):
                self.private_key = '0x' + self.private_key
            self.account = Account.from_key(self.private_key)
            logger.info(f"Initialized account: {self.account.address}")
        except Exception as e:
            raise ValueError(f"Invalid private key: {str(e)}")
    
    def _verify_connection(self):
        """Verify Web3 connection and contract"""
        if not self.is_connected():
            raise Web3ServiceError(f"Failed to connect to Web3 provider: {self.provider_url}")
        
        try:
            # Test contract connection
            self.get_group_counter()
            logger.info(f"Successfully connected to factory contract: {self.factory_address}")
        except Exception as e:
            logger.warning(f"Contract connection test failed: {e}")
    
    def _load_contract_abi(self) -> List[Dict[str, Any]]:
        """Load contract ABI from artifacts or return hardcoded ABI"""
        abi_file_path = os.getenv('CONTRACT_ABI_PATH', './artifacts/contracts/ChamaFactory.sol/ChamaFactory.json')
        
        try:
            with open(abi_file_path, 'r') as f:
                contract_artifact = json.load(f)
                logger.info(f"Loaded ABI from {abi_file_path}")
                return contract_artifact['abi']
        except FileNotFoundError:
            logger.warning(f"ABI file not found at {abi_file_path}, using fallback ABI")
            return self._get_fallback_abi()
    
    def _get_fallback_abi(self) -> List[Dict[str, Any]]:
        """Fallback ABI with essential functions"""
        return [
            {
                "inputs": [
                    {
                        "components": [
                            {"name": "name", "type": "string"},
                            {"name": "contributionAmount", "type": "uint256"},
                            {"name": "maxMembers", "type": "uint256"},
                            {"name": "startDate", "type": "uint256"},
                            {"name": "endDate", "type": "uint256"},
                            {"name": "contributionFrequency", "type": "string"},
                            {"name": "punishmentMode", "type": "uint8"},
                            {"name": "approvalRequired", "type": "bool"},
                            {"name": "emergencyWithdrawAllowed", "type": "bool"},
                            {"name": "creator", "type": "address"},
                            {"name": "contributionToken", "type": "address"},
                            {"name": "gracePeriod", "type": "uint256"},
                            {"name": "contributionWindow", "type": "uint256"}
                        ],
                        "name": "config",
                        "type": "tuple"
                    }
                ],
                "name": "createGroup",
                "outputs": [],
                "stateMutability": "nonpayable",
                "type": "function"
            },
            {
                "inputs": [],
                "name": "getAllGroups",
                "outputs": [{"name": "", "type": "address[]"}],
                "stateMutability": "view",
                "type": "function"
            },
            {
                "inputs": [{"name": "creator", "type": "address"}],
                "name": "getCreatorGroups",
                "outputs": [{"name": "", "type": "address[]"}],
                "stateMutability": "view",
                "type": "function"
            },
            {
                "inputs": [],
                "name": "groupCounter",
                "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view",
                "type": "function"
            },
            {
                "inputs": [{"name": "groupAddress", "type": "address"}],
                "name": "getGroupInfo",
                "outputs": [
                    {"name": "name", "type": "string"},
                    {"name": "creator", "type": "address"},
                    {"name": "contributionAmount", "type": "uint256"},
                    {"name": "maxMembers", "type": "uint256"},
                    {"name": "currentMembers", "type": "uint256"}
                ],
                "stateMutability": "view",
                "type": "function"
            },
            {
                "anonymous": False,
                "inputs": [
                    {"indexed": True, "name": "creator", "type": "address"},
                    {"indexed": True, "name": "groupAddress", "type": "address"},
                    {"indexed": False, "name": "name", "type": "string"},
                    {"indexed": False, "name": "contributionAmount", "type": "uint256"},
                    {"indexed": False, "name": "maxMembers", "type": "uint256"}
                ],
                "name": "GroupCreated",
                "type": "event"
            }
        ]

    def _get_gas_price(self) -> int:
        """Get current gas price with fallback"""
        try:
            # Try to get current gas price from network
            network_gas_price = self.w3.eth.gas_price
            # Add 10% buffer
            return int(network_gas_price * 1.1)
        except Exception as e:
            logger.warning(f"Could not fetch network gas price: {e}, using default")
            return self.w3.to_wei(self.default_gas_price, 'gwei')
    
    def _estimate_gas(self, transaction) -> int:
        """Estimate gas for transaction with buffer"""
        try:
            estimated_gas = self.w3.eth.estimate_gas(transaction)
            # Add 20% buffer
            return int(estimated_gas * 1.2)
        except Exception as e:
            logger.warning(f"Gas estimation failed: {e}, using default")
            return self.default_gas_limit

    async def create_group_on_blockchain(self, group_data: GroupCreate, creator_address: str) -> Dict[str, Any]:
        """Create a group on the blockchain"""
        try:
            # Validate creator address
            if not self.validate_address(creator_address):
                return {'success': False, 'error': 'Invalid creator address'}
            
            # Convert group data to blockchain format
            now = int(datetime.now().timestamp())
            
            config = (
                group_data.name,
                self.w3.to_wei(float(group_data.contribution_amount), 'ether'),
                group_data.max_members,
                int(group_data.start_date.timestamp()) if group_data.start_date else now + 3600,
                int(group_data.end_date.timestamp()) if group_data.end_date else now + 30 * 24 * 60 * 60,
                getattr(group_data, 'contribution_frequency', 'weekly') or "weekly",
                0,  # punishment mode
                getattr(group_data, 'approval_required', True),
                False,  # emergency withdraw allowed
                to_checksum_address(creator_address),
                '0x0000000000000000000000000000000000000000',  # native token
                86400,  # grace period (1 day)
                172800  # contribution window (2 days)
            )
            
            # Build transaction
            nonce = self.w3.eth.get_transaction_count(self.account.address)
            transaction = self.factory_contract.functions.createGroup(config).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gasPrice': self._get_gas_price(),
            })
            
            # Estimate gas
            transaction['gas'] = self._estimate_gas(transaction)
            
            # Sign and send transaction
            signed_txn = self.w3.eth.account.sign_transaction(transaction, private_key=self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.rawTransaction)
            
            logger.info(f"Transaction sent: {tx_hash.hex()}")
            
            # Wait for transaction receipt with timeout
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            
            # Check transaction status
            if receipt.status == 0:
                return {'success': False, 'error': 'Transaction failed'}
            
            # Parse events to get group address
            group_address = self._parse_group_created_event(receipt)
            
            return {
                'success': True,
                'tx_hash': tx_hash.hex(),
                'block_number': receipt.blockNumber,
                'group_address': group_address,
                'gas_used': receipt.gasUsed,
                'effective_gas_price': receipt.effectiveGasPrice if hasattr(receipt, 'effectiveGasPrice') else None
            }
            
        except ContractLogicError as e:
            logger.error(f"Contract logic error: {e}")
            return {'success': False, 'error': f'Contract error: {str(e)}'}
        except Exception as e:
            logger.error(f"Group creation failed: {e}")
            return {'success': False, 'error': str(e)}

    def _parse_group_created_event(self, receipt) -> Optional[str]:
        """Parse GroupCreated event from transaction receipt"""
        try:
            for log in receipt.logs:
                if log.address.lower() == self.factory_address.lower():
                    try:
                        decoded_log = self.factory_contract.events.GroupCreated().process_log(log)
                        return decoded_log.args.groupAddress.lower()
                    except Exception as e:
                        logger.warning(f"Error processing log: {e}")
                        continue
            
            logger.warning("GroupCreated event not found in transaction logs")
            return None
        except Exception as e:
            logger.error(f"Error parsing events: {e}")
            return None

    async def get_blockchain_groups(self) -> List[str]:
        """Get all group addresses from blockchain"""
        try:
            group_addresses = self.factory_contract.functions.getAllGroups().call()
            return [address.lower() for address in group_addresses]
        except Exception as e:
            logger.error(f"Error fetching blockchain groups: {e}")
            return []

    async def get_creator_groups_from_blockchain(self, creator_address: str) -> List[str]:
        """Get groups created by a specific address"""
        try:
            if not self.validate_address(creator_address):
                raise ValueError("Invalid creator address")
            
            creator_address = to_checksum_address(creator_address)
            group_addresses = self.factory_contract.functions.getCreatorGroups(creator_address).call()
            return [address.lower() for address in group_addresses]
        except Exception as e:
            logger.error(f"Error fetching creator groups for {creator_address}: {e}")
            return []

    async def get_group_info(self, group_address: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific group"""
        try:
            if not self.validate_address(group_address):
                return None
            
            group_address = to_checksum_address(group_address)
            
            # Check if getGroupInfo function exists in ABI
            try:
                info = self.factory_contract.functions.getGroupInfo(group_address).call()
                return {
                    'name': info[0],
                    'creator': info[1].lower(),
                    'contribution_amount': str(info[2]),
                    'max_members': info[3],
                    'current_members': info[4]
                }
            except Exception:
                # Fallback: just return basic info
                return {
                    'address': group_address.lower(),
                    'verified': True
                }
        except Exception as e:
            logger.error(f"Error fetching group info for {group_address}: {e}")
            return None

    async def verify_group_exists(self, group_address: str) -> bool:
        """Verify if a group exists on blockchain"""
        try:
            all_groups = await self.get_blockchain_groups()
            return group_address.lower() in all_groups
        except Exception as e:
            logger.error(f"Error verifying group existence: {e}")
            return False

    async def get_transaction_status(self, tx_hash: str) -> Dict[str, Any]:
        """Get status of a transaction"""
        try:
            if not tx_hash.startswith('0x'):
                tx_hash = '0x' + tx_hash
            
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
            transaction = self.w3.eth.get_transaction(tx_hash)
            
            return {
                'hash': tx_hash,
                'status': 'success' if receipt.status == 1 else 'failed',
                'block_number': receipt.blockNumber,
                'gas_used': receipt.gasUsed,
                'from': transaction['from'].lower(),
                'to': transaction['to'].lower() if transaction['to'] else None,
                'value': str(transaction['value'])
            }
        except TransactionNotFound:
            return {'hash': tx_hash, 'status': 'pending'}
        except Exception as e:
            logger.error(f"Error getting transaction status: {e}")
            return {'hash': tx_hash, 'status': 'error', 'error': str(e)}

    async def get_network_info(self) -> Dict[str, Any]:
        """Get network information"""
        try:
            chain_id = self.w3.eth.chain_id
            latest_block = self.w3.eth.block_number
            gas_price = self.w3.eth.gas_price
            
            return {
                'chain_id': chain_id,
                'latest_block': latest_block,
                'gas_price': str(gas_price),
                'gas_price_gwei': self.w3.from_wei(gas_price, 'gwei'),
                'provider_url': self.provider_url,
                'factory_address': self.factory_address
            }
        except Exception as e:
            logger.error(f"Error getting network info: {e}")
            return {}

    def is_connected(self) -> bool:
        """Check if Web3 is connected"""
        try:
            return self.w3.is_connected()
        except Exception:
            return False

    def get_latest_block_number(self) -> int:
        """Get the latest block number"""
        try:
            return self.w3.eth.block_number
        except Exception as e:
            logger.error(f"Error getting latest block: {e}")
            return 0

    def get_group_counter(self) -> int:
        """Get the total number of groups created"""
        try:
            return self.factory_contract.functions.groupCounter().call()
        except Exception as e:
            logger.error(f"Error getting group counter: {e}")
            return 0

    def validate_address(self, address: str) -> bool:
        """Validate Ethereum address format"""
        try:
            return is_address(address)
        except Exception:
            return False

    def get_account_balance(self, address: Optional[str] = None) -> Dict[str, Any]:
        """Get account balance"""
        try:
            target_address = address or self.account.address
            if not self.validate_address(target_address):
                return {'error': 'Invalid address'}
            
            balance_wei = self.w3.eth.get_balance(target_address)
            balance_eth = self.w3.from_wei(balance_wei, 'ether')
            
            return {
                'address': target_address.lower(),
                'balance_wei': str(balance_wei),
                'balance_eth': str(balance_eth)
            }
        except Exception as e:
            logger.error(f"Error getting balance: {e}")
            return {'error': str(e)}

    async def batch_verify_groups(self, group_addresses: List[str]) -> Dict[str, bool]:
        """Batch verify multiple groups"""
        try:
            all_groups = await self.get_blockchain_groups()
            result = {}
            
            for address in group_addresses:
                result[address.lower()] = address.lower() in all_groups
            
            return result
        except Exception as e:
            logger.error(f"Error in batch verification: {e}")
            return {addr.lower(): False for addr in group_addresses}

    def __str__(self) -> str:
        return f"Web3Service(provider={self.provider_url}, factory={self.factory_address}, connected={self.is_connected()})"

    def __repr__(self) -> str:
        return self.__str__()