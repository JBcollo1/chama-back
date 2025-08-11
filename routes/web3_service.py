import os
import json
from datetime import datetime
from decimal import Decimal
from typing import List, Dict, Any
from web3 import Web3
from web3.middleware import geth_poa_middleware
from eth_account import Account

from schemas import GroupCreate

class Web3Service:
    def __init__(self):
       
        self.w3 = Web3(Web3.HTTPProvider(os.getenv('WEB3_PROVIDER_URL', 'http://127.0.0.1:8545')))
        
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        
       
        self.factory_address = os.getenv('FACTORY_CONTRACT_ADDRESS', '0xca0009AF8E28ccfeAA5bB314fD32856B3d278BF7')
        
    
        self.factory_abi = self._load_contract_abi()
        
        # Initialize contract instance
        self.factory_contract = self.w3.eth.contract(
            address=self.factory_address,
            abi=self.factory_abi
        )
        
        # Load private key for transactions
        self.private_key = os.getenv("PRIVATE_KEY")
        if self.private_key:
            self.account = Account.from_key(self.private_key)
        else:
            raise ValueError("PRIVATE_KEY environment variable not set")
    
    def _load_contract_abi(self) -> List[Dict[str, Any]]:
        """Load contract ABI from artifacts or return hardcoded ABI"""
        abi_file_path = os.getenv('CONTRACT_ABI_PATH', './artifacts/contracts/ChamaFactory.sol/ChamaFactory.json')
        
        try:
            with open(abi_file_path, 'r') as f:
                contract_artifact = json.load(f)
                return contract_artifact['abi']
        except FileNotFoundError:
            # Fallback to minimal hardcoded ABI
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

    async def create_group_on_blockchain(self, group_data: GroupCreate, creator_address: str) -> Dict[str, Any]:
        """Create a group on the blockchain"""
        try:
            # Convert group data to blockchain format
            now = int(datetime.now().timestamp())
            
            config = (
                group_data.name,
                self.w3.to_wei(float(group_data.contribution_amount), 'ether'),
                group_data.max_members,
                int(group_data.start_date.timestamp()) if group_data.start_date else now + 3600,
                int(group_data.end_date.timestamp()) if group_data.end_date else now + 30 * 24 * 60 * 60,
                getattr(group_data, 'contribution_frequency', 'weekly') or "weekly",
                0,  
                getattr(group_data, 'approval_required', True),
                False,  
                creator_address,
                '0x0000000000000000000000000000000000000000',  # contributionToken (native)
                86400,  
                172800  
            )
            
           
            transaction = self.factory_contract.functions.createGroup(config).build_transaction({
                'from': self.account.address,
                'nonce': self.w3.eth.get_transaction_count(self.account.address),
                'gas': 2000000,
                'gasPrice': self.w3.to_wei('20', 'gwei'),
            })
            
            # Sign and send transaction
            signed_txn = self.w3.eth.account.sign_transaction(transaction, private_key=self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.rawTransaction)
            
            # Wait for transaction receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            
            # Parse events to get group address
            group_address = None
            for log in receipt.logs:
                if log.address.lower() == self.factory_address.lower():
                    try:
                        decoded_log = self.factory_contract.events.GroupCreated().process_log(log)
                        group_address = decoded_log.args.groupAddress
                        break
                    except Exception as e:
                        print(f"Error processing log: {e}")
                        continue
            
            return {
                'success': True,
                'tx_hash': tx_hash.hex(),
                'block_number': receipt.blockNumber,
                'group_address': group_address,
                'gas_used': receipt.gasUsed
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

    async def get_blockchain_groups(self) -> List[str]:
        """Get all group addresses from blockchain"""
        try:
            group_addresses = self.factory_contract.functions.getAllGroups().call()
            return [address.lower() for address in group_addresses]  # Normalize to lowercase
        except Exception as e:
            print(f"Error fetching blockchain groups: {e}")
            return []

    async def get_creator_groups_from_blockchain(self, creator_address: str) -> List[str]:
        """Get groups created by a specific address"""
        try:
            group_addresses = self.factory_contract.functions.getCreatorGroups(creator_address).call()
            return [address.lower() for address in group_addresses]  # Normalize to lowercase
        except Exception as e:
            print(f"Error fetching creator groups: {e}")
            return []

    def is_connected(self) -> bool:
        """Check if Web3 is connected"""
        return self.w3.is_connected()

    def get_latest_block_number(self) -> int:
        """Get the latest block number"""
        return self.w3.eth.block_number

    def get_group_counter(self) -> int:
        """Get the total number of groups created"""
        return self.factory_contract.functions.groupCounter().call()

    def validate_address(self, address: str) -> bool:
        """Validate Ethereum address format"""
        try:
            return self.w3.is_address(address)
        except:
            return False