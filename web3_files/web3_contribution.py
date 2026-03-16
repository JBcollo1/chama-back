import logging
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from web3 import Web3
from web3.exception import ContractLogicError

from .web3_main import Web3Service

logger = logging.getLogger(__name__)

class ContributionContractService:

    def __init__ (self, web3_service: Web3Service):
        self.web3 = web3_service


    # Helper function
    def _get_group_contract(self,group_contract_address: str):
        checksum_address = Web3.to_checksum_address(group_contract_address)
        return self.web3.w3.eth.contract(
            address = checksum_address,
            abi = self.web3.group_abi,
        )

    def _build_and_send(self, fn, extra_value: int = 0) -> str:
        self.
