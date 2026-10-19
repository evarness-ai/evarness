"""Reminders: "tell the owner about X at time T", delivered by the harness.

A plugin schedules a `Reminder` in the one `ReminderStore` (beside the tasks, in
``data/tasks.db``); the heartbeat fires it on every channel and records the delivery.
A row whose status is in `TERMINAL_STATUSES` is over; `NOT_YET` is the
``closed_reason`` of one the owner answered "Not yet". `parse_snooze` reads the
owner's snooze (a fixed choice from `SNOOZE_CHOICES`, or typed words) into the instant
it fires again. `REMINDER_COMPLETED` and `REMINDER_SNOOZED` are process-bus topics a
plugin subscribes to when the reminder is about something it owns.
"""

from __future__ import annotations

from evarness.services.notifications.events import REMINDER_COMPLETED, REMINDER_SNOOZED
from evarness.services.notifications.models import TERMINAL_STATUSES, Reminder
from evarness.services.notifications.snooze import SNOOZE_CHOICES, parse_snooze
from evarness.services.notifications.store import NOT_YET, ReminderStore

__all__ = [
    "NOT_YET",
    "REMINDER_COMPLETED",
    "REMINDER_SNOOZED",
    "SNOOZE_CHOICES",
    "TERMINAL_STATUSES",
    "Reminder",
    "ReminderStore",
    "parse_snooze",
]
