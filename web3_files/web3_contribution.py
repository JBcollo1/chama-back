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
   def _build_unsigned_tx(self, fn, caller_wallet: str, value_wei: int = 0) => dict:
        try:
            tx = fn.build_transaction(
                {
                    "from": Web3.to_checksum_address(caller_wallet),
                    "value": value_wei,
                }
            )
            return {
                "to": tx["to"],
                "from": tx["from"],
                "data": tx["data"],
                "value": hex(tx.get("value",0)),
                "gas": hex(tx.get("gas", self.web3.default_gas_limit)),
                "chainId": self.web3.w3.eth.chain_id,
            }
        except ContractLogicError as exc:
            raise HTTPException (status_code = 400, detail = self.web3._parse_web3_error(exc)) from exc

        except Exception as exc:
            raise HTTPException (status_code = 500, detail = self.web3._parse_web3_error(exc)) from exc


    def _creator_sign_and_send(self, fn, creator_private_key: str, value_wei: int = 0) -> str:
       
        if not self.web3.admin_account:
                raise HTTPException(status_code = 500, detail = "Admin account not initialized")

        try:
            nounce = self.web3.w3.eth.get_transaction_count(self.web3.admin_account.address)
            gas_price = self.web3.w3.to_wei(self.web3.default_gas_price, "gwei")

            tx = fn.build_transaction (
                {
                    "from": self.web3.admin_account.address,
                    "value": value_wei,
                    "gas": self.web3.default_gas_limit,
                    "gasPrice": gas_price,
                    "nonce": nounce,
                }
            )

            signed = self.web3.w3.eth.account.sign_transaction (
                tx,
                private_key = self.web3.private_key,
            )
            tx_hash = self.web3.w3.eth.send_raw_transaction(signed.rawTransaction)
            receipt = self.web3.w3.eth.wait_for_transaction_receipt(tx_hash, timeout = 120)
            

            if receipt.status == 0:
                raise Web3ServiceError("Transaction reverted on-chain.")
 
            return tx_hash.hex()
 
        except ContractLogicError as exc:
            raise HTTPException(status_code=400, detail=self.web3._parse_web3_error(exc)) from exc
        except Web3ServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=self.web3._parse_web3_error(exc)) from exc


    # contribution window and period functions

    def is_contribution_window_open(self, group_contract_address: str) -> bool :
        try:
            return self._get_group_contract(group_contract_address).functions.isContributionWindowOpen().call()
        except Exception as exc:
            raise HTTPException(status_code = 502, detail = self.web3._parse_web3_error(exec)) from exc


    def get_current_period(self, group_contract_address: str) -> int:
        try:
            return self._get_group_contract(group_contract_address).functions.getCurrentPeriod().call()
        except Exception as exc:
            raise HTTPException(status_code = 502, detail = self.web3._parse_web3_error(exc)) from exc


    def get_member_contribution_timestamp(self, group_contract_address: str, member_wallet: str, period: int) -> int:
        try:
            return self._get_group_contract(group_contract_address).functions.getMemmberContributionTimestamp(
                Web3.to_checksum_address(member_wallet),
                period
            ).call()
        except Exception as exc:
            raise HTTPException(status_code = 502, detail= self.web3._parse_web3_error(exc)) from exc


    
    # member states functions
    def get_member_details(self, group_contract_address: str, member_wallet: str) -> dict:
        try:
            exists, active, joined_at, total_contributed, missed, fines = (
                self._get_group_contract(group_contract_address)
                .functions.getMemberDetails(Web3.to_checksum_address(member_wallet))
                .call()
            )
            return {
                "wallet": member_wallet,
                "exists": exists,
                "is_active": active,
                "joined_at": joined_at,
                "total_contributed": total_contributed,
                "missed_contributions": missed,
                "consecutive_fines": fines,
            }

        except Exception as exc:
            raise HTTPException(status_code = 502, detail = self.web3._parse_web3_error(exc)) from exc

    def get_missed_periods(self, group_contract_address: str, member_wallet: str) -> list[int]:
        try:
            return self._get_group_contract(group_contract_address).functions.getMissedPeriods(
                Web3.to_checksum_address(member_wallet)
            ).call()
        except Exception as exc:
            raise HTTPException(status_code = 502, detail = self.web3._parse_web3_error(exc)) from exc

    def get_punishment_details(self, group_contract_address: str, member_wallet: str) -> dict:
        
        try:
            action, reason, active, issued_at, fine_amount = (
                self._get_group_contract(group_contract_address)
                .functions.getPunishmentDetails(Web3.to_checksum_address(member_wallet))
                .call()
            )
            return {
                "wallet": member_wallet,
                "action": action,
                "reason": reason,
                "is_active": active,
                "issued_at": issued_at,
                "fine_amount": fine_amount,
            }
        except Exception as exc:
            raise HTTPException(status_code=502, detail=self.web3._parse_web3_error(exc)) from exc


    # balance and payouts
    def get_contract_balance(self, group_contract_address: str) -> int:
        try:
            return self._get_group_contract(group_contract_address).functions.getBalance().call()

        except Exception as exc:
            raise HTTPException (status_code = 502, detail= self.web3._parse_web3_error(exc)) from exc

    
    def get_payout_info(self, group_contract_address: str, period: int) -> dict:
        try:
            recipient, amount, was_skipped = (
                self._get_group_contract(group_contract_address)
                .functions.getPayoutInfo(period).call()
            )

            return {
                "period": period,
                "recipient": recipient,
                "": admin_approve_join_request,
                "amount": amount,
                "was_skipped": was_skipped,
            }

        except Exception as exc:
            raise HTTPException( status_code = 502, detail = self.web3._parse_web3_error(exc)) from exc



    def get_member_payout_history(self, group_contract_address: str, member_wallet: str) -> list[int]:
        try:
            return self._get_group_contract(group_contract_address).functions.getMemberPayoutHistory(Web3.to_checksum_address(member_wallet)).call()

        except Exception as exc:
            raise HTTPException(status_code = 502, detail = self.web3._parse_web3_error(exc)) from exc

    
    def get_active_member_count(self, group_contract_address: str) -> int:
        try:
            return self._get_group_contract(group_contract_address).functions.getActiveMemberCount().call()

        except Exception as exc:
            raise HTTPException(status_code = 502, detail = self.web3._parse_web3_error(exc)) from exc



    # prepare unsigned build for the frontend

    def build_contribute_tx(
        self,
        group_contract_address: str,
        member_wallet: str,
        contribution_amount_wei: int,
        is_token_based: bool,
    ) -> dict:
        try:
            contract = self._get_group_contract(group_contract_address)
            checksum_member = Web3.to_checksum_address(member_wallet)

            if not contract.functions.isContributionWindowOpen().call():
                raise HTTPException(
                    status_code= 400,
                    drtail = "Contribution window is currently closed for this  period."
                )
            period = contract.functions.getCurrentPeriod().call()

            if contract.functions.getMemmberContributionTimestamp(checksum_member, period). call() !=0:
                raise HTTPException (
                    status_code = 400,
                    detail = "Member has already contributed for current period"
                )

            value_wei= 0 id is_token_based else contribution_amount_wei
            tx = self._build_unsigned_tx(contract.functions.contribute(), member_wallet, value_wei)

            tx["_meta"] = {
                "action": "contribute",
                "period": period,
                "amount_wei": contribution_amount_wei,
                "is_token_based": is_token_based,
            }

            logger.info ("Built contribute tx for %s period %d", member_wallet, period)
            return tx

        except HTTPException:
                raise
        except Exception as exc:
                raise HTTPException(status_code=500, detail=self.web3._parse_web3_error(exc)) from exc

        
    def build_pay_fine_tx(
        self,
        group_contract_address: str,
        member_wallet: str,
        is_token_based: bool,
    ) -> dict:
        try:
            contract = self._get_group_contract(group_contract_address)
                        action, _reason, is_active, _issued_at, fine_amount = (
                contract.functions.getPunishmentDetails(Web3.to_checksum_address(member_wallet)).call()
            )
 
            if not is_active:
                raise HTTPException(status_code=400, detail="Member has no active punishment.")
 
            FINE_ACTION = 1  # ChamaStructs.PunishmentAction.Fine
            if action != FINE_ACTION:
                raise HTTPException(status_code=400, detail="Active punishment is not a fine — cannot pay.")
 
            value_wei = 0 if is_token_based else fine_amount
            tx = self._build_unsigned_tx(contract.functions.payFine(), member_wallet, value_wei)
            tx["_meta"] = {
                "action": "pay_fine",
                "fine_amount_wei": fine_amount,
                "is_token_based": is_token_based,
            }
 
            logger.info("Built payFine tx for %s — fine: %d wei", member_wallet, fine_amount)
            return tx
 
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=self.web3._parse_web3_error(exc)) from exc