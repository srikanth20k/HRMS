from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0004_auth_and_live'),
    ]

    operations = [
        # AppUser: Google OAuth fields
        migrations.AddField(
            model_name='appuser',
            name='auth_provider',
            field=models.CharField(default='email', max_length=20),
        ),
        migrations.AddField(
            model_name='appuser',
            name='google_id',
            field=models.CharField(blank=True, max_length=128, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='appuser',
            name='profile_pic',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='appuser',
            name='password',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        # InterviewLink: token-based access + resume/JD storage
        migrations.AddField(
            model_name='interviewlink',
            name='candidate_token',
            field=models.CharField(blank=True, db_index=True, max_length=128, null=True),
        ),
        migrations.AddField(
            model_name='interviewlink',
            name='recruiter_token',
            field=models.CharField(blank=True, db_index=True, max_length=128, null=True),
        ),
        migrations.AddField(
            model_name='interviewlink',
            name='link_expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='interviewlink',
            name='resume_text',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='interviewlink',
            name='jd_text',
            field=models.TextField(blank=True, null=True),
        ),
    ]
