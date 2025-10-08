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

from schemas import GroupCreate

# Configure loggingCreate
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
        
        # Optional: Load private key only for admin operations
        self._initialize_admin_account()
        
        # Gas configuration
        self.default_gas_limit = int(os.getenv('DEFAULT_GAS_LIMIT', '2000000'))
        self.default_gas_price = os.getenv('DEFAULT_GAS_PRICE', '20')  # gwei
        
        # Verify connection on initialization
        self._verify_connection()
    
    def _initialize_admin_account(self):
        """Initialize admin account from private key (optional, only for admin operations)"""
        self.private_key = os.getenv("ADMIN_PRIVATE_KEY")
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
    
    def _estimate_gas_for_user(self, transaction_data: dict, from_address: str) -> int:
        """Estimate gas for user transaction with buffer"""
        try:
            # Create a transaction dict for estimation
            tx_for_estimation = {
                'from': to_checksum_address(from_address),
                'to': transaction_data.get('to'),
                'data': transaction_data.get('data'),
                'value': transaction_data.get('value', 0)
            }
            
            estimated_gas = self.w3.eth.estimate_gas(tx_for_estimation)
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

    async def prepare_group_creation_transaction(self, group_data: GroupCreate, creator_address: str) -> Dict[str, Any]:
        """Prepare a group creation transaction for user to sign"""
        try:
            # Validate creator address
            if not self.validate_address(creator_address):
                return {'success': False, 'error': 'Invalid creator address'}
            
            creator_checksum = to_checksum_address(creator_address)
            
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
                creator_checksum,
                '0x0000000000000000000000000000000000000000',  # native token
                86400,  # grace period (1 day)
                172800  # contribution window (2 days)
            )
            
            # Get user's nonce
            nonce = self.w3.eth.get_transaction_count(creator_checksum)
            
            # Build transaction data
            transaction_data = self.factory_contract.functions.createGroup(config).build_transaction({
                'from': creator_checksum,
                'nonce': nonce,
                'gasPrice': self._get_gas_price(),
                'gas': self.default_gas_limit  # Will be estimated on frontend
            })
            
            # Estimate gas
            estimated_gas = self._estimate_gas_for_user(transaction_data, creator_address)
            
            return {
                'success': True,
                'transaction': {
                    'to': self.factory_address,
                    'from': creator_address.lower(),
                    'data': transaction_data['data'],
                    'gas': hex(estimated_gas),
                    'gasPrice': hex(transaction_data['gasPrice']),
                    'nonce': hex(nonce),
                    'value': '0x0',
                    'chainId': self.w3.eth.chain_id
                },
                'message': 'Transaction prepared. Please sign with your wallet.',
                'estimated_gas': estimated_gas
            }
            
        except Exception as e:
            logger.error(f"Group creation preparation failed: {e}")
            return {'success': False, 'error': str(e)}
    async def wait_for_transaction_confirmation(
        self, 
        tx_hash: str, 
        timeout: int = 120
    ) -> Dict[str, Any]:
        """Wait for transaction confirmation and extract results"""
        try:
            if not tx_hash.startswith('0x') or len(tx_hash) != 66:
                return {'success': False, 'error': 'Invalid transaction hash'}
            
            # Wait for transaction receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(
                tx_hash, 
                timeout=timeout
            )
            
            # Check if transaction was successful
            if receipt['status'] != 1:
                return {
                    'success': False, 
                    'error': 'Transaction reverted on blockchain'
                }
            
            # Parse logs to get contract address
            contract_address = None
            for log in receipt['logs']:
                try:
                    # Try to parse GroupCreated event
                    parsed_log = self.factory_contract.events.GroupCreated().process_log(log)
                    contract_address = parsed_log['args'].get('groupAddress')
                    break
                except:
                    continue
            
            if not contract_address:
                # Fallback: use contractAddress from receipt if available
                contract_address = receipt.get('contractAddress')
            
            return {
                'success': True,
                'contract_address': contract_address,
                'tx_hash': tx_hash,
                'block_number': receipt['blockNumber'],
                'gas_used': receipt['gasUsed']
            }
            
        except Exception as e:
            logger.error(f"Transaction confirmation failed: {e}")
            return {'success': False, 'error': str(e)}

    async def prepare_join_group_transaction(self, group_address: str, user_address: str) -> Dict[str, Any]:
        """Prepare a join group transaction for user to sign"""
        try:
            if not self.validate_address(group_address):
                return {'success': False, 'error': 'Invalid group address'}
            
            if not self.validate_address(user_address):
                return {'success': False, 'error': 'Invalid user address'}
            
            user_checksum = to_checksum_address(user_address)
            group_checksum = to_checksum_address(group_address)
            
            # Get group contract instance
            group_contract = self._get_group_contract(group_address)
            
            # Check if group exists and is active
            try:
                member_count = group_contract.functions.memberCount().call()
                logger.info(f"Current member count: {member_count}")
            except Exception as e:
                return {'success': False, 'error': 'Group contract not found or invalid'}
            
            # Get user's nonce
            nonce = self.w3.eth.get_transaction_count(user_checksum)
            
            # Build transaction data
            transaction_data = group_contract.functions.joinGroup().build_transaction({
                'from': user_checksum,
                'nonce': nonce,
                'gasPrice': self._get_gas_price(),
                'gas': self.default_gas_limit
            })
            
            # Estimate gas
            estimated_gas = self._estimate_gas_for_user(transaction_data, user_address)
            
            return {
                'success': True,
                'transaction': {
                    'to': group_address.lower(),
                    'from': user_address.lower(),
                    'data': transaction_data['data'],
                    'gas': hex(estimated_gas),
                    'gasPrice': hex(transaction_data['gasPrice']),
                    'nonce': hex(nonce),
                    'value': '0x0',
                    'chainId': self.w3.eth.chain_id
                },
                'message': 'Transaction prepared. Please sign with your wallet.',
                'estimated_gas': estimated_gas
            }
                 
        except Exception as e:
            logger.error(f"Join group preparation failed: {e}")
            return {'success': False, 'error': str(e)}

    async def prepare_contribute_transaction(self, group_address: str, user_address: str, contribution_amount: int) -> Dict[str, Any]:
        """Prepare a contribution transaction for user to sign"""
        try:
            if not self.validate_address(group_address):
                return {'success': False, 'error': 'Invalid group address'}
            
            if not self.validate_address(user_address):
                return {'success': False, 'error': 'Invalid user address'}
            
            user_checksum = to_checksum_address(user_address)
            group_checksum = to_checksum_address(group_address)
            
            # Get group contract instance
            group_contract = self._get_group_contract(group_address)
            
            # Get user's nonce
            nonce = self.w3.eth.get_transaction_count(user_checksum)
            
            # Build transaction data
            transaction_data = group_contract.functions.contribute().build_transaction({
                'from': user_checksum,
                'value': contribution_amount,
                'nonce': nonce,
                'gasPrice': self._get_gas_price(),
                'gas': self.default_gas_limit
            })
            
            # Estimate gas
            estimated_gas = self._estimate_gas_for_user(transaction_data, user_address)
            
            return {
                'success': True,
                'transaction': {
                    'to': group_address.lower(),
                    'from': user_address.lower(),
                    'data': transaction_data['data'],
                    'gas': hex(estimated_gas),
                    'gasPrice': hex(transaction_data['gasPrice']),
                    'nonce': hex(nonce),
                    'value': hex(contribution_amount),
                    'chainId': self.w3.eth.chain_id
                },
                'message': 'Transaction prepared. Please sign with your wallet.',
                'estimated_gas': estimated_gas,
                'contribution_amount_wei': str(contribution_amount),
                'contribution_amount_eth': str(self.w3.from_wei(contribution_amount, 'ether'))
            }
            
        except Exception as e:
            logger.error(f"Contribution preparation failed: {e}")
            return {'success': False, 'error': str(e)}

    async def verify_user_transaction(self, tx_hash: str, expected_from: str, expected_to: str = None) -> Dict[str, Any]:
        """Verify that a user-submitted transaction was successful"""
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
            
            # Get transaction details
            transaction = self.w3.eth.get_transaction(hash_bytes)
            
            # Verify the transaction was sent from the expected address
            if transaction.get('from', '').lower() != expected_from.lower():
                return {
                    'success': False, 
                    'error': f'Transaction was not sent from expected address. Expected: {expected_from}, Got: {transaction.get("from", "")}'
                }
            
            # Verify destination if provided
            if expected_to and transaction.get('to', '').lower() != expected_to.lower():
                return {
                    'success': False,
                    'error': f'Transaction was not sent to expected contract. Expected: {expected_to}, Got: {transaction.get("to", "")}'
                }
                
            return {
                'success': True,
                'tx_hash': tx_hash,
                'block_number': receipt['blockNumber'],
                'gas_used': receipt['gasUsed'],
                'effective_gas_price': receipt.get('effectiveGasPrice'),
                'from': transaction.get('from', '').lower(),
                'to': transaction.get('to', '').lower() if transaction.get('to') else None,
                'value': str(transaction.get('value', 0))
            }
            
        except Exception as e:
            logger.error(f"Transaction verification failed: {e}")
            return {'success': False, 'error': str(e)}

    async def verify_group_creation_transaction(self, tx_hash: str, creator_address: str) -> Dict[str, Any]:
        """Verify a group creation transaction and extract the group address"""
        try:
            verification_result = await self.verify_user_transaction(
                tx_hash, 
                creator_address, 
                self.factory_address
            )
            
            if not verification_result['success']:
                return verification_result
            
            # Get transaction receipt to parse events
            if not tx_hash.startswith('0x'):
                tx_hash = '0x' + tx_hash
            hash_bytes = HexBytes(tx_hash)
            receipt = self.w3.eth.get_transaction_receipt(hash_bytes)
            
            # Parse events to get group address
            group_address = self._parse_group_created_event(receipt)
            
            if not group_address:
                return {'success': False, 'error': 'Could not extract group address from transaction'}
            
            verification_result['group_address'] = group_address
            return verification_result
            
        except Exception as e:
            logger.error(f"Group creation verification failed: {e}")
            return {'success': False, 'error': str(e)}

    async def verify_join_transaction(self, tx_hash: str, group_address: str, user_address: str) -> Dict[str, Any]:
        """Verify that a join transaction was successful"""
        logger.info(f"Starting join transaction verification - TX: {tx_hash}, Group: {group_address}, User: {user_address}")
        
        try:
            # Verify basic transaction
            logger.info("Calling verify_user_transaction...")
            verification_result = await self.verify_user_transaction(
                tx_hash, 
                user_address, 
                group_address
            )
            
            logger.info(f"verify_user_transaction result: {verification_result}")
            
            if not verification_result['success']:
                logger.error(f"Transaction verification failed: {verification_result.get('error')}")
                return verification_result
            
            # Additional verification: check if user is now a member
            logger.info(f"Checking if user is member on contract...")
            try:
                group_contract = self._get_group_contract(group_address)
                checksum_address = to_checksum_address(user_address)
                logger.info(f"Calling isMember for address: {checksum_address}")
                
                is_member = group_contract.functions.isMember(checksum_address).call()
                logger.info(f"isMember result: {is_member}")
                
                if not is_member:
                    logger.error(f"User {user_address} is not registered as member after transaction")
                    return {'success': False, 'error': 'User is not registered as a member after transaction'}
            except Exception as contract_error:
                logger.error(f"Error checking membership on contract: {str(contract_error)}", exc_info=True)
                return {'success': False, 'error': f'Failed to verify membership: {str(contract_error)}'}
            
            logger.info("Join transaction verification successful")
            return verification_result
            
        except Exception as e:
            logger.error(f"Join transaction verification failed with exception: {str(e)}", exc_info=True)
            return {'success': False, 'error': str(e)}


    async def verify_user_transaction(self, tx_hash: str, user_address: str, contract_address: str) -> Dict[str, Any]:
        """Verify a user transaction on the blockchain"""
        logger.info(f"Verifying user transaction - TX: {tx_hash}, User: {user_address}, Contract: {contract_address}")
        
        try:
            # Get transaction receipt
            logger.info(f"Fetching transaction receipt for {tx_hash}...")
            tx_receipt = self.w3.eth.get_transaction_receipt(tx_hash)
            
            if not tx_receipt:
                logger.error(f"Transaction receipt not found for {tx_hash}")
                return {'success': False, 'error': 'Transaction not found'}
            
            logger.info(f"Transaction receipt status: {tx_receipt['status']}")
            logger.info(f"Transaction from: {tx_receipt.get('from')}, to: {tx_receipt.get('to')}")
            logger.info(f"Block number: {tx_receipt['blockNumber']}, Gas used: {tx_receipt['gasUsed']}")
            
            # Check transaction status
            if tx_receipt['status'] != 1:
                logger.error(f"Transaction {tx_hash} failed on blockchain (status: {tx_receipt['status']})")
                
                # Try to get the revert reason
                revert_reason = await self._get_revert_reason(tx_hash, tx_receipt)
                
                error_message = f'Transaction failed on blockchain. {revert_reason}'
                logger.error(error_message)
                
                return {
                    'success': False, 
                    'error': error_message,
                    'status': tx_receipt['status'],
                    'gas_used': tx_receipt['gasUsed']
                }
            
            # Verify sender
            tx_from = tx_receipt.get('from', '').lower()
            expected_from = user_address.lower()
            
            if tx_from != expected_from:
                logger.error(f"Transaction sender mismatch - Expected: {expected_from}, Got: {tx_from}")
                return {'success': False, 'error': f'Transaction not from user wallet (expected: {expected_from}, got: {tx_from})'}
            
            # Verify recipient (contract address)
            tx_to = tx_receipt.get('to', '').lower()
            expected_to = contract_address.lower()
            
            if tx_to != expected_to:
                logger.error(f"Transaction recipient mismatch - Expected: {expected_to}, Got: {tx_to}")
                return {'success': False, 'error': f'Transaction not to group contract (expected: {expected_to}, got: {tx_to})'}
            
            logger.info("Transaction verification successful")
            
            return {
                'success': True,
                'tx_hash': tx_hash,
                'block_number': tx_receipt['blockNumber'],
                'gas_used': tx_receipt['gasUsed']
            }
            
        except Exception as e:
            logger.error(f"Error verifying transaction {tx_hash}: {str(e)}", exc_info=True)
            return {'success': False, 'error': str(e)}


    async def _get_revert_reason(self, tx_hash: str, tx_receipt: dict) -> str:
        """
        Try to extract the revert reason from a failed transaction
        """
        try:
            # Get the original transaction
            tx = self.w3.eth.get_transaction(tx_hash)
            
            logger.info(f"Attempting to decode revert reason for tx {tx_hash}")
            logger.info(f"Transaction input: {tx['input'][:66]}...")  # First 66 chars (0x + 32 bytes)
            
            # Try to replay the transaction to get the revert reason
            try:
                # Build the transaction dict for eth_call
                call_params = {
                    'from': tx['from'],
                    'to': tx['to'],
                    'data': tx['input'],
                    'value': tx.get('value', 0),
                    'gas': tx.get('gas', 0)
                }
                
                # Try to call at the block before the transaction was mined
                block_number = tx_receipt['blockNumber'] - 1
                
                logger.info(f"Replaying transaction at block {block_number}")
                self.w3.eth.call(call_params, block_number)
                
                # If we get here, the call succeeded (shouldn't happen for a failed tx)
                return "Reason: Unknown (call succeeded in replay)"
                
            except Exception as call_error:
                error_str = str(call_error)
                logger.info(f"Call error (this contains revert reason): {error_str}")
                
                # Parse common revert reasons
                if "execution reverted" in error_str.lower():
                    # Try to extract the revert message
                    if ":" in error_str:
                        reason = error_str.split(":", 1)[1].strip()
                        return f"Reason: {reason}"
                    return f"Reason: {error_str}"
                
                # Check for specific error patterns
                if "already a member" in error_str.lower():
                    return "Reason: User is already a member of this group"
                elif "max members" in error_str.lower():
                    return "Reason: Group has reached maximum member capacity"
                elif "not approved" in error_str.lower():
                    return "Reason: Member join requires approval"
                elif "insufficient" in error_str.lower():
                    return "Reason: Insufficient funds or allowance"
                
                return f"Reason: {error_str}"
                
        except Exception as e:
            logger.warning(f"Could not extract revert reason: {str(e)}")
            return "Reason: Could not determine (check smart contract logs)"


    async def diagnose_join_failure(self, group_address: str, user_address: str) -> Dict[str, Any]:
        """
        Diagnose why a user might not be able to join a group
        Useful for debugging before attempting the transaction
        """
        try:
            logger.info(f"Diagnosing join failure for user {user_address} joining group {group_address}")
            
            group_contract = self._get_group_contract(group_address)
            checksum_user = to_checksum_address(user_address)
            
            # Check various conditions
            diagnostics = {}
            
            # 1. Check if already a member
            try:
                is_member = group_contract.functions.isMember(checksum_user).call()
                diagnostics['is_already_member'] = is_member
                logger.info(f"Is already member: {is_member}")
            except Exception as e:
                diagnostics['is_already_member'] = f"Error: {str(e)}"
            
            # 2. Check member count vs max members
            try:
                member_count = group_contract.functions.getMemberCount().call()
                max_members = group_contract.functions.maxMembers().call()
                diagnostics['member_count'] = member_count
                diagnostics['max_members'] = max_members
                diagnostics['is_full'] = member_count >= max_members
                logger.info(f"Members: {member_count}/{max_members}, Full: {member_count >= max_members}")
            except Exception as e:
                diagnostics['member_count_check'] = f"Error: {str(e)}"
            
            # 3. Check if group is active
            try:
                is_active = group_contract.functions.isActive().call()
                diagnostics['is_active'] = is_active
                logger.info(f"Group is active: {is_active}")
            except Exception as e:
                diagnostics['is_active'] = f"Error: {str(e)}"
            
            # 4. Check approval requirements
            try:
                approval_required = group_contract.functions.approvalRequired().call()
                diagnostics['approval_required'] = approval_required
                logger.info(f"Approval required: {approval_required}")
            except Exception as e:
                diagnostics['approval_required'] = f"Error: {str(e)}"
            
            # 5. Check contribution amount
            try:
                contribution_amount = group_contract.functions.contributionAmount().call()
                diagnostics['contribution_amount'] = str(contribution_amount)
                logger.info(f"Contribution amount: {contribution_amount}")
                
                # Check user's balance
                balance = self.w3.eth.get_balance(checksum_user)
                diagnostics['user_balance'] = str(balance)
                diagnostics['has_sufficient_balance'] = balance >= contribution_amount
                logger.info(f"User balance: {balance}, Sufficient: {balance >= contribution_amount}")
            except Exception as e:
                diagnostics['contribution_check'] = f"Error: {str(e)}"
            
            return {
                'success': True,
                'diagnostics': diagnostics
            }
            
        except Exception as e:
            logger.error(f"Error during diagnosis: {str(e)}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    async def verify_contribution_transaction(self, tx_hash: str, group_address: str, user_address: str, expected_amount: int = None) -> Dict[str, Any]:
        """Verify that a contribution transaction was successful"""
        try:
            verification_result = await self.verify_user_transaction(
                tx_hash, 
                user_address, 
                group_address
            )
            
            if not verification_result['success']:
                return verification_result
            
            # Verify the contribution amount if provided
            if expected_amount is not None:
                actual_amount = int(verification_result['value'])
                if actual_amount != expected_amount:
                    return {
                        'success': False, 
                        'error': f'Contribution amount mismatch. Expected: {expected_amount}, Got: {actual_amount}'
                    }
            
            # Add contribution details to response
            verification_result['contribution_amount_wei'] = verification_result['value']
            verification_result['contribution_amount_eth'] = str(self.w3.from_wei(int(verification_result['value']), 'ether'))
            
            return verification_result
            
        except Exception as e:
            logger.error(f"Contribution verification failed: {e}")
            return {'success': False, 'error': str(e)}

    # Admin-only functions (requires admin private key)
    async def admin_approve_join_request(self, group_address: str, applicant_address: str) -> Dict[str, Any]:
        """Admin function to approve a join request (requires admin private key)"""
        if not self.admin_account:
            return {'success': False, 'error': 'Admin account not configured'}
        
        try:
            if not self.validate_address(group_address) or not self.validate_address(applicant_address):
                return {'success': False, 'error': 'Invalid address'}
            
            group_contract = self._get_group_contract(group_address)
            
            # Build transaction
            nonce = self.w3.eth.get_transaction_count(self.admin_account.address)
            tx_params: TxParams = {
                'from': self.admin_account.address,
                'nonce': nonce,
                'gasPrice': Wei(self._get_gas_price()),
            }
            transaction = group_contract.functions.approveJoinRequest(
                to_checksum_address(applicant_address)
            ).build_transaction(tx_params)
            
            # Estimate gas
            transaction['gas'] = self._estimate_gas_for_user(transaction, self.admin_account.address)
            
            # Sign and send transaction
            signed_txn = self.w3.eth.account.sign_transaction(transaction, private_key=self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.rawTransaction)
            
            logger.info(f"Admin approval transaction sent: {tx_hash.hex()}")
            
            # Wait for transaction receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            
            if receipt['status'] == 0:
                return {'success': False, 'error': 'Transaction failed'}
            
            return {
                'success': True,
                'tx_hash': tx_hash.hex(),
                'block_number': receipt['blockNumber'],
                'gas_used': receipt['gasUsed'],
                'message': f'Join request approved for {applicant_address}'
            }
            
        except ContractLogicError as e:
            logger.error(f"Contract logic error during approval: {e}")
            return {'success': False, 'error': f'Contract error: {str(e)}'}
        except Exception as e:
            logger.error(f"Admin approval failed: {e}")
            return {'success': False, 'error': str(e)}

    # Helper function to get current gas prices for frontend
    async def get_gas_estimates(self) -> Dict[str, Any]:
        """Get current gas price estimates for frontend"""
        try:
            current_gas_price = self._get_gas_price()
            
            return {
                'success': True,
                'gas_price_wei': str(current_gas_price),
                'gas_price_gwei': str(self.w3.from_wei(current_gas_price, 'gwei')),
                'estimates': {
                    'slow': str(int(current_gas_price * 0.8)),
                    'standard': str(current_gas_price),
                    'fast': str(int(current_gas_price * 1.2))
                }
            }
        except Exception as e:
            logger.error(f"Error getting gas estimates: {e}")
            return {'success': False, 'error': str(e)}

    # Keep all the existing read-only methods unchanged
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

    async def is_member(self, group_address: str, member_address: str) -> Dict[str, Any]:
        """Check if address is a member of the group"""
        try:
            if not self.validate_address(group_address) or not self.validate_address(member_address):
                return {'success': False, 'error': 'Invalid address'}
            
            group_contract = self._get_group_contract(group_address)
            is_member = group_contract.functions.isMember(to_checksum_address(member_address)).call()
            
            return {
                'success': True,
                'is_member': is_member
            }
            
        except Exception as e:
            logger.error(f"Error checking membership: {e}")
            return {'success': False, 'error': str(e)}

    async def get_member_joined_events(self, group_address: str, from_block: int = 0) -> Dict[str, Any]:
        """Get all MemberJoined events for a group"""
        try:
            if not self.validate_address(group_address):
                return {'success': False, 'error': 'Invalid group address'}
            
            group_contract = self._get_group_contract(group_address)
            
            # Get MemberJoined events
            event_filter = group_contract.events.MemberJoined.create_filter(
                fromBlock=from_block,
                toBlock='latest'
            )
            events = event_filter.get_all_entries()
            
            member_events = []
            for event in events:
                member_events.append({
                    'user': event.args.user.lower(),
                    'block_number': event.blockNumber,
                    'transaction_hash': event.transactionHash.hex(),
                    'timestamp': event.args.get('timestamp', None)
                })
            
            return {
                'success': True,
                'events': member_events,
                'count': len(member_events)
            }
            
        except Exception as e:
            logger.error(f"Error getting member events: {e}")
            return {'success': False, 'error': str(e)}

    async def check_group_status(self, group_address: str) -> Dict[str, Any]:
        """Check comprehensive group status including member limits"""
        try:
            if not self.validate_address(group_address):
                return {'success': False, 'error': 'Invalid group address'}
            
            group_contract = self._get_group_contract(group_address)
            
            # Get basic group info
            member_count = group_contract.functions.memberCount().call()
            
            # Try to get max members (this might not be directly available in all contracts)
            try:
                # This assumes there's a way to get max members - adjust based on your contract
                group_info = await self.get_group_info(group_address)
                max_members = group_info.get('max_members', 0) if group_info else 0
            except Exception:
                max_members = 0
            
            # Check if group is full
            is_full = max_members > 0 and member_count >= max_members
            
            # Get current block time for status checks
            current_block = self.w3.eth.block_number
            block = self.w3.eth.get_block(current_block)
            current_time = block.timestamp
            
            return {
                'success': True,
                'member_count': member_count,
                'max_members': max_members,
                'is_full': is_full,
                'available_spots': max(0, max_members - member_count) if max_members > 0 else float('inf'),
                'current_time': current_time
            }
            
        except Exception as e:
            logger.error(f"Error checking group status: {e}")
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