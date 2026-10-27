from django.core.management.base import BaseCommand

from intake.scanner import scan


class Command(BaseCommand):
    help = "Scan incoming/<rig>/ for video files (the Inbox page also scans on every load)."

    def handle(self, *args, **opts):
        self.stdout.write(str(scan()))
