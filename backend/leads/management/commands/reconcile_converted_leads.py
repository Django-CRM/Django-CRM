"""Emit a sanitized conversion reconciliation report; never mutate state."""

import json
import os
import stat
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from leads.historical_reconciliation import (
    HistoricalReconciliationError,
    build_historical_conversion_report,
)


def _read_private_key(path_value: str) -> bytes:
    path = Path(path_value)
    if path.is_symlink():
        raise CommandError("Reference key file cannot be a symlink.")
    path = path.resolve(strict=True)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise CommandError("Reference key must be an owned regular file.")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise CommandError("Reference key file must use private permissions.")
    key = path.read_bytes()
    if len(key) < 32:
        raise CommandError("Reference key must contain at least 32 bytes.")
    return key


class Command(BaseCommand):
    help = "Dry-run converted-lead reconciliation; this command cannot write."

    def add_arguments(self, parser):
        parser.add_argument("--reference-key-file", required=True)
        parser.add_argument("--max-records", type=int, default=10_000)

    def handle(self, *args, **options):
        try:
            report = build_historical_conversion_report(
                reference_key=_read_private_key(options["reference_key_file"]),
                max_records=options["max_records"],
            )
        except (OSError, HistoricalReconciliationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, sort_keys=True, separators=(",", ":")))
