# pyrefly: ignore [missing-import]
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0003_app_users'),
    ]

    operations = [
        migrations.CreateModel(
            name='LoginOtp',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.CharField(db_index=True, max_length=255)),
                ('code_hash', models.CharField(max_length=128)),
                ('salt', models.CharField(max_length=32)),
                ('attempts', models.IntegerField(default=0)),
                ('consumed', models.BooleanField(default=False)),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'db_table': 'login_otps', 'ordering': ['-id']},
        ),
        migrations.CreateModel(
            name='PasswordReset',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.CharField(db_index=True, max_length=255)),
                ('token', models.CharField(max_length=128, unique=True)),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'db_table': 'password_resets', 'ordering': ['-id']},
        ),
        migrations.CreateModel(
            name='LiveSession',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('session_id', models.CharField(max_length=64, unique=True)),
                ('candidate_name', models.CharField(blank=True, default='', max_length=255)),
                ('role', models.CharField(blank=True, default='', max_length=255)),
                ('interview_id', models.IntegerField(blank=True, null=True)),
                ('status', models.CharField(default='waiting', max_length=20)),
                ('offer', models.TextField(blank=True, null=True)),
                ('answer', models.TextField(blank=True, null=True)),
                ('candidate_ice', models.JSONField(blank=True, default=list)),
                ('recruiter_ice', models.JSONField(blank=True, default=list)),
                ('transcript', models.TextField(blank=True, null=True)),
                ('current_question', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'db_table': 'live_sessions', 'ordering': ['-id']},
        ),
    ]
