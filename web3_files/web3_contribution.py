
import logging
from typing import Optional

from fastapi import HTTPException
from web3 import Web3
from web3.exceptions import ContractLogicError

from .web3_main import Web3Service, Web3ServiceError

logger = logging.getLogger(__name__)


class ContributionContractService:
   

    def __init__(self, web3_service: Web3Service):
        self.web3 = web3_service



    def _get_group_contract(self, group_contract_address: str):
        """Return a bound ChamaGroup contract instance."""
        return self.web3.w3.eth.contract(
            address=Web3.to_checksum_address(group_contract_address),
            abi=self.web3.group_abi,
        )

    def _build_unsigned_tx(self, fn, caller_wallet: str, value_wei: int = 0) -> dict:
        """
        Build an unsigned tx payload for a wallet to sign on the frontend.
        Nonce and gasPrice are omitted — the signing wallet fills those in.
        Used for member-signed actions (contribute, payFine).
        """
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
                "value": hex(tx.get("value", 0)),
                "gas": hex(tx.get("gas", self.web3.default_gas_limit)),
                "chainId": self.web3.w3.eth.chain_id,
            }
        except ContractLogicError as exc:
            raise HTTPException(status_code=400, detail=self.web3._parse_web3_error(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=self.web3._parse_web3_error(exc)) from exc

    def _sign_and_send(self, fn, value_wei: int = 0) -> str:
       
        if not self.web3.admin_account:
            raise HTTPException(
                status_code=503,
                detail="Backend wallet not configured — set ADMIN_PRIVATE_KEY in env.",
            )
        try:
            nonce = self.web3.w3.eth.get_transaction_count(self.web3.admin_account.address)
            gas_price = self.web3.w3.to_wei(self.web3.default_gas_price, "gwei")

            tx = fn.build_transaction(
                {
                    "from": self.web3.admin_account.address,
                    "nonce": nonce,
                    "gas": self.web3.default_gas_limit,
                    "gasPrice": gas_price,
                    "value": value_wei,
                }
            )

            signed = self.web3.w3.eth.account.sign_transaction(
                tx, private_key=self.web3.private_key
            )
            tx_hash = self.web3.w3.eth.send_raw_transaction(signed.rawTransaction)
            receipt = self.web3.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt.status == 0:
                raise Web3ServiceError("Transaction reverted on-chain.")

            return tx_hash.hex()

        except ContractLogicError as exc:
            raise HTTPException(status_code=400, detail=self.web3._parse_web3_error(exc)) from exc
        except Web3ServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=self.web3._parse_web3_error(exc)) from exc


    def is_contribution_window_open(self, group_contract_address: str) -> bool:
        """Check whether the contribution window is open for the active period."""
        try:
            return self._get_group_contract(group_contract_address).functions.isContributionWindowOpen().call()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=self.web3._parse_web3_error(exc)) from exc

    def get_current_period(self, group_contract_address: str) -> int:
        """Return the current contribution period index."""
        try:
            return self._get_group_contract(group_contract_address).functions.getCurrentPeriod().call()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=self.web3._parse_web3_error(exc)) from exc

    def get_member_contribution_timestamp(
        self, group_contract_address: str, member_wallet: str, period: int
    ) -> int:
        """Return the Unix timestamp a member contributed for a period, or 0 if not yet."""
        try:
            return self._get_group_contract(group_contract_address).functions.getMemberContributionTimestamp(
                Web3.to_checksum_address(member_wallet), period
            ).call()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=self.web3._parse_web3_error(exc)) from exc


    def get_member_details(self, group_contract_address: str, member_wallet: str) -> dict:
        """Fetch on-chain member details."""
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
            raise HTTPException(status_code=502, detail=self.web3._parse_web3_error(exc)) from exc

    def get_missed_periods(self, group_contract_address: str, member_wallet: str) -> list[int]:
        """Return a list of period indices the member missed."""
        try:
            return self._get_group_contract(group_contract_address).functions.getMissedPeriods(
                Web3.to_checksum_address(member_wallet)
            ).call()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=self.web3._parse_web3_error(exc)) from exc

    def get_punishment_details(self, group_contract_address: str, member_wallet: str) -> dict:
        """Fetch active punishment details for a member."""
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

    # -------------------------------------------------------------------------
    # Read — balance & payout
    # -------------------------------------------------------------------------

    def get_contract_balance(self, group_contract_address: str) -> int:
        """Return the current native/token balance held by the group contract."""
        try:
            return self._get_group_contract(group_contract_address).functions.getBalance().call()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=self.web3._parse_web3_error(exc)) from exc

    def get_payout_info(self, group_contract_address: str, period: int) -> dict:
        """Return payout details for a specific period."""
        try:
            recipient, amount, timestamp, was_skipped = (
                self._get_group_contract(group_contract_address)
                .functions.getPayoutInfo(period)
                .call()
            )
            return {
                "period": period,
                "recipient": recipient,
                "amount": amount,
                "timestamp": timestamp,
                "was_skipped": was_skipped,
            }
        except Exception as exc:
            raise HTTPException(status_code=502, detail=self.web3._parse_web3_error(exc)) from exc

    def get_member_payout_history(self, group_contract_address: str, member_wallet: str) -> list[int]:
        """Return the list of periods in which a member received a payout."""
        try:
            return self._get_group_contract(group_contract_address).functions.getMemberPayoutHistory(
                Web3.to_checksum_address(member_wallet)
            ).call()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=self.web3._parse_web3_error(exc)) from exc

    def get_active_member_count(self, group_contract_address: str) -> int:
        """Return the number of currently active members."""
        try:
            return self._get_group_contract(group_contract_address).functions.getActiveMemberCount().call()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=self.web3._parse_web3_error(exc)) from exc



    def build_contribute_tx(
        self,
        group_contract_address: str,
        member_wallet: str,
        contribution_amount_wei: int,
        is_token_based: bool,
    ) -> dict:
        """
        Build an unsigned contribute() tx for the member's wallet to sign.
        Pre-flight checks run first so the wallet prompt only appears when
        the tx is actually expected to succeed.
        """
        try:
            contract = self._get_group_contract(group_contract_address)
            checksum_member = Web3.to_checksum_address(member_wallet)

            if not contract.functions.isContributionWindowOpen().call():
                raise HTTPException(
                    status_code=400,
                    detail="Contribution window is currently closed for this period.",
                )

            period = contract.functions.getCurrentPeriod().call()
            if contract.functions.getMemberContributionTimestamp(checksum_member, period).call() != 0:
                raise HTTPException(
                    status_code=400,
                    detail="Member has already contributed for the current period.",
                )

            value_wei = 0 if is_token_based else contribution_amount_wei
            tx = self._build_unsigned_tx(contract.functions.contribute(), member_wallet, value_wei)
            tx["_meta"] = {
                "action": "contribute",
                "period": period,
                "amount_wei": contribution_amount_wei,
                "is_token_based": is_token_based,
            }

            logger.info("Built contribute tx for %s period %d", member_wallet, period)
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
        """
        Build an unsigned payFine() tx for the member's wallet to sign.
        Reads the fine amount from chain so the frontend can display it
        before the user confirms.
        """
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

    # -------------------------------------------------------------------------
    # Write — backend-signed automated actions (ADMIN_PRIVATE_KEY from env)
    # Scheduled tasks that must run without human interaction.
    # The backend wallet address must be registered as creator/admin on the
    # group contract at deploy time.
    # -------------------------------------------------------------------------

    def process_rotation_payout(self, group_contract_address: str) -> str:
        """
        Execute processRotationPayout() for the current period.
        Called by the period-end scheduler. Returns transaction hash.
        """
        try:
            contract = self._get_group_contract(group_contract_address)
            period = contract.functions.getCurrentPeriod().call()

            recipient, _amount, _ts, _skipped = contract.functions.getPayoutInfo(period).call()
            if recipient != "0x0000000000000000000000000000000000000000":
                raise HTTPException(
                    status_code=400,
                    detail=f"Payout for period {period} has already been processed.",
                )

            tx_hash = self._sign_and_send(contract.functions.processRotationPayout())
            logger.info("processRotationPayout confirmed for period %d: %s", period, tx_hash)
            return tx_hash

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=self.web3._parse_web3_error(exc)) from exc

    def check_missed_contribution(
        self, group_contract_address: str, member_wallet: str
    ) -> str:
        """
        Trigger on-chain missed-contribution check for a single member.
        Returns transaction hash.
        """
        try:
            fn = self._get_group_contract(group_contract_address).functions.checkMissedContribution(
                Web3.to_checksum_address(member_wallet)
            )
            tx_hash = self._sign_and_send(fn)
            logger.info("checkMissedContribution confirmed for %s: %s", member_wallet, tx_hash)
            return tx_hash
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=self.web3._parse_web3_error(exc)) from exc

    def batch_check_missed_contributions(
        self, group_contract_address: str, member_wallets: list[str]
    ) -> str:
        """
        Batch check missed contributions for multiple members in one tx.
        Called by the period scheduler after the contribution window closes.
        Returns transaction hash.
        """
        try:
            checksum_members = [Web3.to_checksum_address(w) for w in member_wallets]
            fn = self._get_group_contract(group_contract_address).functions.batchCheckMissedContributions(
                checksum_members
            )
            tx_hash = self._sign_and_send(fn)
            logger.info(
                "batchCheckMissedContributions confirmed (%d members): %s",
                len(member_wallets), tx_hash
            )
            return tx_hash
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=self.web3._parse_web3_error(exc)) from exc

    def reset_last_checked_period(
        self, group_contract_address: str, member_wallet: str, period: int
    ) -> str:
        """
        Emergency reset of lastCheckedPeriod for a member. Returns transaction hash.
        """
        try:
            fn = self._get_group_contract(group_contract_address).functions.resetLastCheckedPeriod(
                Web3.to_checksum_address(member_wallet), period
            )
            tx_hash = self._sign_and_send(fn)
            logger.info(
                "resetLastCheckedPeriod confirmed for %s to period %d: %s",
                member_wallet, period, tx_hash
            )
            return tx_hash
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=self.web3._parse_web3_error(exc)) from exc

    def set_payout_queue(
        self, group_contract_address: str, ordered_wallets: list[str]
    ) -> str:
        """
        Set the one-time payout rotation queue. onlyCreator — called once at
        group setup. Returns transaction hash.
        """
        try:
            checksum_queue = [Web3.to_checksum_address(w) for w in ordered_wallets]
            fn = self._get_group_contract(group_contract_address).functions.setPayoutQueue(checksum_queue)
            tx_hash = self._sign_and_send(fn)
            logger.info("setPayoutQueue confirmed: %s", tx_hash)
            return tx_hash
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=self.web3._parse_web3_error(exc)) from exc

 
    def verify_contribution_on_chain(
        self,
        group_contract_address: str,
        member_wallet: str,
        expected_period: int,
        tx_hash: str,
    ) -> dict:
        """
        Called after the frontend broadcasts a contribute() tx.
        Waits for receipt and confirms the timestamp was recorded on-chain.
        Returns verified state so ContributionRoutes can call mark_as_paid().
        """
        try:
            receipt = self.web3.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt.status == 0:
                raise HTTPException(
                    status_code=400,
                    detail="Contribution transaction reverted on-chain.",
                )

            ts = self.get_member_contribution_timestamp(
                group_contract_address, member_wallet, expected_period
            )
            if ts == 0:
                raise HTTPException(
                    status_code=400,
                    detail="Transaction succeeded but contribution timestamp not found — unexpected state.",
                )

            logger.info(
                "Contribution verified on-chain: %s period %d tx %s",
                member_wallet, expected_period, tx_hash
            )
            return {
                "verified": True,
                "tx_hash": tx_hash,
                "period": expected_period,
                "contribution_timestamp": ts,
                "block_number": receipt.blockNumber,
            }

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=self.web3._parse_web3_error(exc)) from exc



    def sync_contribution_status(
        self,
        group_contract_address: str,
        member_wallet: str,
        period: Optional[int] = None,
    ) -> dict:
        """
        Read on-chain state for a member — used by ContributionRoutes to decide
        whether to mark an off-chain record as completed / overdue.
        """
        if period is None:
            period = self.get_current_period(group_contract_address)

        ts = self.get_member_contribution_timestamp(group_contract_address, member_wallet, period)
        member = self.get_member_details(group_contract_address, member_wallet)
        punishment = self.get_punishment_details(group_contract_address, member_wallet)

        return {
            "contributed": ts > 0,
            "contribution_timestamp": ts,
            "is_active": member["is_active"],
            "missed_contributions": member["missed_contributions"],
            "has_active_punishment": punishment["is_active"],
            "punishment_action": punishment["action"],
            "fine_amount": punishment["fine_amount"],
        }

    def get_group_on_chain_summary(self, group_contract_address: str) -> dict:
        """
        Aggregate on-chain summary — complements the off-chain
        get_group_contribution_summary in ContributionRoutes.
        """
        return {
            "contract_address": group_contract_address,
            "current_period": self.get_current_period(group_contract_address),
            "active_member_count": self.get_active_member_count(group_contract_address),
            "contract_balance": self.get_contract_balance(group_contract_address),
            "contribution_window_open": self.is_contribution_window_open(group_contract_address),
        }