"""The NAS background service: ingest new videos, plan runs, store results, requeue stalled work."""

import time

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.commit import commit_pending, requeue_stale
from core.rules import plan_runs
from intake.ingest import ingest_pending, recover_interrupted


def migrations_pending() -> bool:
    executor = MigrationExecutor(connection)
    return bool(executor.migration_plan(executor.loader.graph.leaf_nodes()))


def cycle() -> dict:
    return {"requeued": requeue_stale(), "ingested": ingest_pending(), "planned": plan_runs(),
            "committed": commit_pending()}


class Command(BaseCommand):
    help = "Ingest videos, plan runs and store worker results. --loop keeps running (the NAS 'keeper' container)."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=int, default=30)

    def handle(self, *args, **opts):
        if opts["loop"]:
            while migrations_pending():
                self.stdout.write("Waiting for migrations...")
                time.sleep(10)
        recover_interrupted()
        while True:
            done = {k: v for k, v in cycle().items() if v}
            if done:
                self.stdout.write(", ".join(f"{k}: {v}" for k, v in done.items()))
            if not opts["loop"]:
                break
            connection.close()
            time.sleep(opts["interval"])
