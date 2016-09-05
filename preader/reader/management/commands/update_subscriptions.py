from django.core.management.base import BaseCommand
from reader.models import UserEntry


class Command(BaseCommand):
    help = 'Update feeds'

    def handle(self, *args, **options):
        UserEntry.update_subscriptions()
