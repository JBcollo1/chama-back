"""
Contribution Scheduler
======================
Handles all time-based contribution lifecycle tasks:

  1. create_period_contributions()
     - Runs at the START of every period
     - Creates off-chain Contribution records for every active group member
     - These are the records the frontend uses to call build_contribute_tx / confirm

  2. check_overdue_contributions()
     - Runs after the contribution window + grace period closes
     - Marks DB records as overdue if still pending
     - Triggers on-chain batchCheckMissedContributions() per group

  3. process_rotation_payouts()
     - Runs at the END of every period
     - Calls on-chain processRotationPayout() for every active group

Registered in main.py via lifespan events — see main_lifespan_snippet.py.

Install:
    pip install "apscheduler==3.10.4"
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Contribution, ContributionStatus, Group, GroupMember, GroupStatus
from web3_files.initialize import contribution_contract_svc

logger = logging.getLogger(__name__)

# How often the scheduler polls in seconds.
# Kept at 60s — actual chain calls only happen when a period boundary is due.
POLL_INTERVAL_SECONDS = 60


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _get_db() -> Session:
    return SessionLocal()


def _active_groups(db: Session) -> list[Group]:
    """Groups that are active and have a deployed contract."""
    return (
        db.query(Group)
        .filter(
            Group.status == GroupStatus.active,
            Group.contract_address.isnot(None),
        )
        .all()
    )


def _active_members(db: Session, group_id) -> list[GroupMember]:
    """Active members with wallet addresses for a group."""
    return (
        db.query(GroupMember)
        .filter(
            GroupMember.group_id == group_id,
            GroupMember.is_active == True,
            GroupMember.wallet_address.isnot(None),
        )
        .all()
    )


def _contribution_exists(db: Session, group_id, member_id, period: int) -> bool:
    """True if a DB contribution record already exists for this member + period."""
    return (
        db.query(Contribution)
        .filter(
            Contribution.group_id == group_id,
            Contribution.member_id == member_id,
            Contribution.period == period,
        )
        .first()
        is not None
    )


# ── period boundary helpers ────────────────────────────────────────────────────

def _period_duration(group: Group) -> timedelta:
    """Return the duration of one contribution period for the group."""
    freq = (group.contribution_frequency or "monthly").lower()
    return {
        "weekly":    timedelta(weeks=1),
        "biweekly":  timedelta(weeks=2),
        "monthly":   timedelta(days=30),
        "quarterly": timedelta(days=90),
    }.get(freq, timedelta(days=30))


def _period_start(group: Group, period: int) -> datetime:
    """Return the UTC datetime when a given period starts."""
    start = group.start_date
    if not isinstance(start, datetime):
        start = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    elif start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return start + (_period_duration(group) * period)


def _period_due_date(group: Group, period: int) -> datetime:
    """Due date = start of the NEXT period (members must pay before window closes)."""
    return _period_start(group, period + 1)


def _group_needs_period_check(group: Group) -> bool:
    """
    Return True only if the current wall-clock time is within 2 * POLL_INTERVAL
    of a period boundary. This avoids hitting the chain on every tick for
    groups whose period boundary is far away.
    """
    now = datetime.now(timezone.utc)
    start = group.start_date
    if not isinstance(start, datetime):
        start = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    elif start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    if now < start:
        return False  # group hasn't started yet

    duration = _period_duration(group)
    elapsed = (now - start).total_seconds()
    position_in_period = elapsed % duration.total_seconds()

    # True if we're within the first 2 poll intervals of a new period
    # OR within the last 2 poll intervals of the current period
    window = POLL_INTERVAL_SECONDS * 2
    return position_in_period < window or (duration.total_seconds() - position_in_period) < window


# ── task 1: create contribution records at period start ───────────────────────

def create_period_contributions() -> None:
    """
    For every active group near a period boundary, check whether the current
    on-chain period already has DB contribution records. If not, create them
    for all active members.

    Safe to run repeatedly — idempotent via _contribution_exists check.
    """
    db = _get_db()
    try:
        groups = _active_groups(db)
        if not groups:
            return

        created_total = 0

        for group in groups:
            # Skip chain call if we're not near a period boundary
            if not _group_needs_period_check(group):
                continue

            try:
                current_period = contribution_contract_svc.get_current_period(
                    group.contract_address
                )
                members  = _active_members(db, group.id)
                due_date = _period_due_date(group, current_period)

                for member in members:
                    if _contribution_exists(db, group.id, member.id, current_period):
                        continue

                    db.add(Contribution(
                        group_id=group.id,
                        member_id=member.id,
                        amount=group.contribution_amount,
                        status=ContributionStatus.pending,
                        due_date=due_date,
                        period=current_period,
                    ))
                    created_total += 1

                db.commit()

            except Exception as exc:
                db.rollback()
                logger.error(
                    "create_period_contributions failed for group %s: %s",
                    group.id, exc,
                )

        if created_total:
            logger.info(
                "create_period_contributions: created %d records", created_total
            )

    finally:
        db.close()


# ── task 2: mark overdue + trigger on-chain missed check ──────────────────────

def check_overdue_contributions() -> None:
    """
    For every active group:
      - Mark any pending contributions whose due_date has passed as overdue in DB
      - Trigger batchCheckMissedContributions() on-chain so the contract applies
        punishments accordingly

    Only hits the chain when there are actually overdue records to report.
    """
    db = _get_db()
    try:
        groups = _active_groups(db)
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        for group in groups:
            try:
                # ── mark DB records overdue ────────────────────────────────
                overdue = (
                    db.query(Contribution)
                    .filter(
                        Contribution.group_id == group.id,
                        Contribution.status == ContributionStatus.pending,
                        Contribution.due_date < now,
                        Contribution.paid_date.is_(None),
                    )
                    .all()
                )

                if not overdue:
                    continue

                for record in overdue:
                    record.status = ContributionStatus.overdue
                db.commit()

                logger.info(
                    "Marked %d contributions overdue for group %s",
                    len(overdue), group.id,
                )

                # ── trigger on-chain punishment check only when needed ─────
                members = _active_members(db, group.id)
                wallets = [m.wallet_address for m in members if m.wallet_address]

                if wallets:
                    tx_hash = contribution_contract_svc.batch_check_missed_contributions(
                        group.contract_address, wallets
                    )
                    logger.info(
                        "batchCheckMissedContributions tx for group %s: %s",
                        group.id, tx_hash,
                    )

            except Exception as exc:
                db.rollback()
                logger.error(
                    "check_overdue_contributions failed for group %s: %s",
                    group.id, exc,
                )

    finally:
        db.close()


# ── task 3: process rotation payouts at period end ────────────────────────────

def process_rotation_payouts() -> None:
    """
    For every active group near a period boundary, attempt to process the
    rotation payout. The contract reverts if members haven't all contributed
    or the period was already paid — these are caught and logged, not raised.

    Only attempts payout when near a period boundary to avoid unnecessary
    contract calls on every tick.
    """
    db = _get_db()
    try:
        groups = _active_groups(db)

        for group in groups:
            if not _group_needs_period_check(group):
                continue

            try:
                tx_hash = contribution_contract_svc.process_rotation_payout(
                    group.contract_address
                )
                logger.info(
                    "processRotationPayout confirmed for group %s: %s",
                    group.id, tx_hash,
                )
            except Exception as exc:
                # Expected: already processed, or not all members contributed yet
                logger.warning(
                    "process_rotation_payouts skipped group %s: %s",
                    group.id, exc,
                )

    finally:
        db.close()


# ── scheduler factory ─────────────────────────────────────────────────────────

def build_scheduler() -> AsyncIOScheduler:
    """
    Build and return the configured AsyncIOScheduler.
    Call scheduler.start() in the FastAPI lifespan startup handler.

    All three jobs poll every POLL_INTERVAL_SECONDS but use
    _group_needs_period_check() to skip chain calls when not needed,
    keeping RPC usage low even with many groups.
    """
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        create_period_contributions,
        trigger=IntervalTrigger(seconds=POLL_INTERVAL_SECONDS),
        id="create_period_contributions",
        name="Create contribution records for new periods",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=30,
    )

    scheduler.add_job(
        check_overdue_contributions,
        trigger=IntervalTrigger(seconds=POLL_INTERVAL_SECONDS),
        id="check_overdue_contributions",
        name="Mark overdue contributions and trigger on-chain punishment check",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=30,
    )

    scheduler.add_job(
        process_rotation_payouts,
        trigger=IntervalTrigger(seconds=POLL_INTERVAL_SECONDS),
        id="process_rotation_payouts",
        name="Process rotation payouts at period end",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=30,
    )

    return scheduler