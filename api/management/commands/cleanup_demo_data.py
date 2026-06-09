"""
Management command to remove demo/seed data from the database.
Run this after updating seed.py to clean up existing demo data.

Usage: python manage.py cleanup_demo_data
"""
from django.core.management.base import BaseCommand
from api.models import JobPost, InterviewLink, AppUser


SEED_JOB_TITLES = [
    'Senior Software Engineer',
    'Product Manager - Growth',
    'Data Scientist',
    'Sales Account Executive',
    'UX Designer',
    'DevOps Engineer',
]

SEED_INTERVIEW_NAMES = [
    'Ravi Kumar',
    'Ananya Singh',
    'Vikram Nair',
    'Priya Mehta',
    'Arjun Das',
]

SEED_ADMIN_EMAIL = 'admin@eversoftit.com'


class Command(BaseCommand):
    help = 'Remove demo/seed data from the database. Only real user-entered data remains.'

    def handle(self, *args, **options):
        deleted_jobs = 0
        deleted_interviews = 0
        deleted_users = 0

        # Remove seed jobs by title match
        for title in SEED_JOB_TITLES:
            count, _ = JobPost.objects.filter(title=title).delete()
            deleted_jobs += count
            if count:
                self.stdout.write(self.style.SUCCESS(f"  ✓ Deleted job: {title}"))

        # Remove seed interviews by name match
        for name in SEED_INTERVIEW_NAMES:
            count, _ = InterviewLink.objects.filter(name=name).delete()
            deleted_interviews += count
            if count:
                self.stdout.write(self.style.SUCCESS(f"  ✓ Deleted interview: {name}"))

        # Remove seed admin user
        if AppUser.objects.filter(email=SEED_ADMIN_EMAIL).exists():
            AppUser.objects.filter(email=SEED_ADMIN_EMAIL).delete()
            deleted_users += 1
            self.stdout.write(self.style.SUCCESS(f"  ✓ Deleted default admin user"))

        # Summary
        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Cleanup complete!\n"
                f"  Deleted: {deleted_jobs} jobs, {deleted_interviews} interviews, {deleted_users} user(s)\n"
                f"  Only real database data remains."
            )
        )
