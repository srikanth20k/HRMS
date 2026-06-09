from django.core.management.base import BaseCommand

from api.seed import run_seed


class Command(BaseCommand):
    help = 'Insert base jobs/interviews when those tables are empty (idempotent).'

    def handle(self, *args, **options):
        created = run_seed()
        self.stdout.write(self.style.SUCCESS(
            f"Seed complete: +{created['jobs']} jobs, +{created['interviews']} interviews"
        ))
