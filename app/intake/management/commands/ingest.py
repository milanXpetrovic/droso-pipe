import time

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from intake.ingest import ingest_pending, recover_interrupted


def migrations_pending() -> bool:
    executor = MigrationExecutor(connection)
    return bool(executor.migration_plan(executor.loader.graph.leaf_nodes()))


class Command(BaseCommand):
    help = "Ingest registered videos into raw/. With --loop it keeps running (the NAS 'ingest' container)."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=int, default=30, help="Seconds between checks in --loop mode.")

    def handle(self, *args, **opts):
        if opts["loop"]:
            while migrations_pending():            # the app container applies migrations on start
                self.stdout.write("Waiting for migrations...")
                time.sleep(10)
        recover_interrupted()
        while True:
            n = ingest_pending()
            if n:
                self.stdout.write(f"Ingested {n} video(s).")
            if not opts["loop"]:
                break
            connection.close()                     # do not hold a DB connection while sleeping
            time.sleep(opts["interval"])
