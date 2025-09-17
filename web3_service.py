import os
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from web3 import Web3
from web3.middleware.geth_poa import geth_poa_middleware
from web3.exceptions import ContractLogicError, TransactionNotFound
from web3.types import TxParams, Wei
from hexbytes import HexBytes
from eth_account import Account
from eth_utils.address import is_address, to_checksum_address
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
        
        # Load contract ABI - no fallback, let it throw error if missing
        self.factory_abi = self._load_contract_abi()
        self.group_abi = self._load_group_contract_abi()
        
        # Initialize contract instance
        self.factory_contract = self.w3.eth.contract(
            address=to_checksum_address(self.factory_address),
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

    def _get_group_contract(self, group_address: str):
        """Get group contract instance"""
        if not self.validate_address(group_address):
            raise ValueError("Invalid group address")
        
        return self.w3.eth.contract(
            address=to_checksum_address(group_address),
            abi=self.group_abi
        )

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
                getattr(group_data, 'approval_required', False),
                False,  # emergency withdraw allowed
                to_checksum_address(creator_address),
                '0x0000000000000000000000000000000000000000',  # native token
                86400,  # grace period (1 day)
                172800  # contribution window (2 days)
            )
            
            # Build transaction
            nonce = self.w3.eth.get_transaction_count(self.account.address)
            tx_params: TxParams = {
                'from': self.account.address,
                'nonce': nonce,
                'gasPrice': Wei(self._get_gas_price()),
            }
            transaction = self.factory_contract.functions.createGroup(config).build_transaction(tx_params)
            
            # Estimate gas
            transaction['gas'] = self._estimate_gas(transaction)
            
            # Sign and send transaction
            signed_txn = self.w3.eth.account.sign_transaction(transaction, private_key=self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.rawTransaction)
            
            logger.info(f"Transaction sent: {tx_hash.hex()}")
            
            # Wait for transaction receipt with timeout
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            
            # Check transaction status
            if receipt['status'] == 0:
                return {'success': False, 'error': 'Transaction failed'}
            
            # Parse events to get group address
            group_address = self._parse_group_created_event(receipt)
            
            return {
                'success': True,
                'tx_hash': tx_hash.hex(),
                'block_number': receipt['blockNumber'],
                'group_address': group_address,
                'gas_used': receipt['gasUsed'],
                'effective_gas_price': receipt.get('effectiveGasPrice')
            }
            
        except ContractLogicError as e:
            logger.error(f"Contract logic error: {e}")
            return {'success': False, 'error': f'Contract error: {str(e)}'}
        except Exception as e:
            logger.error(f"Group creation failed: {e}")
            return {'success': False, 'error': str(e)}

    async def join_group(self, group_address: str, user_address: str) -> Dict[str, Any]:
        """Join a group on the blockchain"""
        try:
            if not self.validate_address(group_address):
                return {'success': False, 'error': 'Invalid group address'}
            
            if not self.validate_address(user_address):
                return {'success': False, 'error': 'Invalid user address'}
            
            # Get group contract instance
            group_contract = self._get_group_contract(group_address)
            
            # Check if group exists and is active
            try:
                member_count = group_contract.functions.memberCount().call()
                logger.info(f"Current member count: {member_count}")
            except Exception as e:
                return {'success': False, 'error': 'Group contract not found or invalid'}
            
            # Build transaction
            nonce = self.w3.eth.get_transaction_count(self.account.address)
            tx_params: TxParams = {
                'from': self.account.address,
                'nonce': nonce,
                'gasPrice': Wei(self._get_gas_price()),
            }
            transaction = group_contract.functions.joinGroup().build_transaction(tx_params)
            
            # Estimate gas
            transaction['gas'] = self._estimate_gas(transaction)
            
            # Sign and send transaction
            return {
                    'success': True,
                    'transaction': {
                        'to': group_address,
                        'from': user_address,
                        'data': transaction.get('data', ''),
                        'gas': hex(transaction['gas']),
                        'gasPrice': hex(transaction.get('gasPrice', 0)),
                        'nonce': hex(nonce),
                        'value': '0x0'
                    },
                    'message': 'Transaction prepared. Please sign with your wallet.'
                }
                 
        except Exception as e:
            logger.error(f"Join group preparation failed: {e}")
            return {'success': False, 'error': str(e)}

    async def verify_join_transaction(self, tx_hash: str, group_address: str, user_address: str) -> Dict[str, Any]:
        """Verify that a join transaction was successful"""
        try:
            # Convert string to HexBytes for proper type handling
            if not tx_hash.startswith('0x'):
                tx_hash = '0x' + tx_hash
            hash_bytes = HexBytes(tx_hash)
            
            # Get transaction receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(hash_bytes, timeout=300)
            
            # Check transaction status
            if receipt['status'] == 0:
                return {'success': False, 'error': 'Transaction failed'}
                
            # Verify transaction was sent to correct contract
            if receipt.get('to', '').lower() != group_address.lower():
                return {'success': False, 'error': 'Transaction was not sent to the correct contract'}
                
            
            group_contract = self._get_group_contract(group_address)
            is_member = group_contract.functions.isMember(user_address).call()
            
            if not is_member:
                return {'success': False, 'error': 'User is not registered as a member after transaction'}
                
            return {
                'success': True,
                'tx_hash': tx_hash,
                'block_number': receipt['blockNumber'],
                'gas_used': receipt['gasUsed'],
                'effective_gas_price': receipt.get('effectiveGasPrice')
            }
            
        except Exception as e:
            logger.error(f"Transaction verification failed: {e}")
            return {'success': False, 'error': str(e)}
    async def get_member_details(self, group_address: str, member_address: str) -> Dict[str, Any]:
        """Get member details from group contract"""
        try:
            if not self.validate_address(group_address) or not self.validate_address(member_address):
                return {'success': False, 'error': 'Invalid address'}
            
            group_contract = self._get_group_contract(group_address)
            
            # Call getMemberDetails function
            member_details = group_contract.functions.getMemberDetails(
                to_checksum_address(member_address)
            ).call()
            
            return {
                'success': True,
                'exists': member_details[0],
                'is_active': member_details[1],
                'join_date': member_details[2],
                'total_contributions': str(member_details[3]),
                'missed_contributions': member_details[4],
                'penalty_amount': str(member_details[5])
            }
            
        except Exception as e:
            logger.error(f"Error getting member details: {e}")
            return {'success': False, 'error': str(e)}

    async def contribute_to_group(self, group_address: str, contribution_amount: int) -> Dict[str, Any]:
        """Make a contribution to a group"""
        try:
            if not self.validate_address(group_address):
                return {'success': False, 'error': 'Invalid group address'}
            
            group_contract = self._get_group_contract(group_address)
            
            # Build transaction
            nonce = self.w3.eth.get_transaction_count(self.account.address)
            tx_params: TxParams = {
                'from': self.account.address,
                'value': Wei(contribution_amount),
                'nonce': nonce,
                'gasPrice': Wei(self._get_gas_price()),
            }
            transaction = group_contract.functions.contribute().build_transaction(tx_params)
            
            # Estimate gas
            transaction['gas'] = self._estimate_gas(transaction)
            
            # Sign and send transaction
            signed_txn = self.w3.eth.account.sign_transaction(transaction, private_key=self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.rawTransaction)
            
            logger.info(f"Contribution transaction sent: {tx_hash.hex()}")
            
            # Wait for transaction receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            
            if receipt['status'] == 0:
                return {'success': False, 'error': 'Transaction failed'}
            
            return {
                'success': True,
                'tx_hash': tx_hash.hex(),
                'block_number': receipt['blockNumber'],
                'gas_used': receipt['gasUsed']
            }
            
        except ContractLogicError as e:
            logger.error(f"Contract logic error during contribution: {e}")
            return {'success': False, 'error': f'Contract error: {str(e)}'}
        except Exception as e:
            logger.error(f"Contribution failed: {e}")
            return {'success': False, 'error': str(e)}

    async def get_group_member_count(self, group_address: str) -> int:
        """Get current member count for a group"""
        try:
            if not self.validate_address(group_address):
                return 0
            
            group_contract = self._get_group_contract(group_address)
            return group_contract.functions.memberCount().call()
            
        except Exception as e:
            logger.error(f"Error getting member count: {e}")
            return 0

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
            
            # Try to get info from factory contract first
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
                # Try to get info directly from group contract
                try:
                    group_contract = self._get_group_contract(group_address)
                    member_count = group_contract.functions.memberCount().call()
                    return {
                        'address': group_address.lower(),
                        'current_members': member_count,
                        'verified': True
                    }
                except Exception:
                    return {
                        'address': group_address.lower(),
                        'verified': False
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
            
            # Convert to HexBytes for proper type handling
            hash_bytes = HexBytes(tx_hash)
            
            receipt = self.w3.eth.get_transaction_receipt(hash_bytes)
            transaction = self.w3.eth.get_transaction(hash_bytes)
            
            return {
                'hash': tx_hash,
                'status': 'success' if receipt['status'] == 1 else 'failed',
                'block_number': receipt['blockNumber'],
                'gas_used': receipt['gasUsed'],
                'from': transaction.get('from', '').lower(),
                'to': transaction.get('to', '').lower() if transaction.get('to') else None,
                'value': str(transaction.get('value', 0))
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
            
            checksum_address = to_checksum_address(target_address)
            balance_wei = self.w3.eth.get_balance(checksum_address)
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