from django.core.management.base import BaseCommand
from reader.models import Feed


class Command(BaseCommand):
    help = 'Update feeds'

    def handle(self, *args, **options):
        Feed.update_feeds(100)
