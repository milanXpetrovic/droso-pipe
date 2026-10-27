"""Create the 'Operators' group: people who record and enter metadata, but do not edit catalogues.

    python manage.py setup_roles
Safe to run again; it resets the group's permissions to this list.
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

ENTER = ["recording", "arena", "flygroup", "event"]                          # add, change, view
READ = ["genotype", "condition", "protocol", "rig", "camera", "arenalayout", "eventkind", "experiment"]


class Command(BaseCommand):
    help = "Create or update the Operators group."

    def handle(self, *args, **opts):
        codenames = [f"{a}_{m}" for m in ENTER for a in ("add", "change", "view")] + [f"view_{m}" for m in READ]
        perms = Permission.objects.filter(content_type__app_label="domain", codename__in=codenames)
        group, _ = Group.objects.get_or_create(name="Operators")
        group.permissions.set(perms)
        self.stdout.write(self.style.SUCCESS(f"Operators: {perms.count()} permissions"))
