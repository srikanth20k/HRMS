"""
HRMS API views — a Django REST Framework port of the original Express server.

CRUD resources are implemented as DRF ``@api_view`` function views backed by
the serializers in ``serializers.py``. Request and response JSON shapes
(camelCase) match the Node API exactly so the existing React frontend works
unchanged.

A few helpers and endpoints are intentionally NOT DRF:
  * ``parse_body`` / ``err`` / ``make_initials`` / ``norm_email`` /
    ``app_user_dict`` are imported by ``auth_views`` and ``live_views`` and so
    are kept here.
  * ``recording_video`` handles a raw binary (video/webm) body, which DRF's
    JSON parser cannot consume, so it stays a plain ``csrf_exempt`` view.
  * ``spa_index`` serves the built React app for non-API routes.
"""
import json
import os
import re
import secrets
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import BooleanField, Case, Value, When
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import ai, mailer, social_poster
from .models import (
    AppUser,
    InterviewLink,
    InterviewRecording,
    JobPost,
    QuestionSet,
    ResumeScore,
    UserDocument,
    UserEmailConfig,
    UserProfile,
)
from .serializers import (
    AppUserSerializer,
    InterviewLinkSerializer,
    InterviewRecordingSerializer,
    JobPostSerializer,
    ResumeScoreSerializer,
    UserDocumentSerializer,
    UserEmailConfigSerializer,
    UserProfileSerializer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_body(request):
    try:
        return json.loads((request.body or b'{}').decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return {}


def err(message, status=400):
    return JsonResponse({'message': message}, status=status)


def serializer_err(serializer, status=400):
    """Flatten DRF validation errors into the API's ``{'message': ...}`` shape."""
    msgs = []
    for field, errs in serializer.errors.items():
        first = errs[0] if isinstance(errs, (list, tuple)) and errs else errs
        msgs.append(f'{field}: {first}')
    return err('; '.join(msgs) or 'Invalid data', status)


def make_initials(name):
    parts = [p for p in re.split(r'\s+', (name or '').strip()) if p]
    return ''.join(p[0] for p in parts).upper()[:2]


def norm_email(value):
    return str(value or '').strip().lower()


def dt(value):
    return value.strftime('%Y-%m-%d %H:%M:%S') if value else None


def safe_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except ValueError:
            return []
    return []


def safe_json(value):
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return None


def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# app_user_dict — kept for auth_views (which imports it). Mirrors
# AppUserSerializer's output exactly.
# ---------------------------------------------------------------------------
def app_user_dict(o):
    return {
        'id': o.id, 'name': o.full_name, 'email': o.email,
        'password': o.password, 'initials': o.initials,
        'role': o.role, 'status': o.status,
        'authProvider': o.auth_provider,
        'profilePic': o.profile_pic or '',
        'createdAt': dt(o.created_at),
    }


def resolve_color(type_value):
    v = str(type_value or '').lower()
    if 'contract' in v:
        return 'purple'
    if 'intern' in v:
        return 'orange'
    if 'part' in v:
        return 'green'
    return 'blue'


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
@api_view(['GET', 'POST'])
def jobs(request):
    if request.method == 'GET':
        return Response(JobPostSerializer(JobPost.objects.all(), many=True).data)

    body = request.data
    if not body.get('title') or not body.get('dept'):
        return err('title and dept are required')
    serializer = JobPostSerializer(data=body)
    if not serializer.is_valid():
        return serializer_err(serializer)
    serializer.save()
    payload = serializer.data
    # Auto-post to the creator's linked social accounts (LinkedIn / X).
    # Non-fatal: a job is still created even if posting fails or isn't set up.
    if body.get('autoPost') is not False:
        payload['socialResults'] = _auto_post_job(payload, body.get('userEmail'))
    return Response(payload, status=201)


def _auto_post_job(job_payload, user_email=None):
    """Look up the user's saved social credentials and post the job. Returns a
    list of per-platform results (empty if nothing is configured)."""
    cfg = None
    if user_email:
        cfg = UserEmailConfig.objects.filter(pk=norm_email(user_email)).first()
    if not cfg:
        cfg = UserEmailConfig.objects.exclude(social__isnull=True).order_by('user_email').first()
    if not cfg:
        return []
    social = safe_json(cfg.social) or (cfg.social if isinstance(cfg.social, dict) else {})
    if not isinstance(social, dict) or not social:
        return []
    try:
        return social_poster.post_job(job_payload, social)
    except Exception as e:  # noqa: BLE001 - never break job creation on a posting error
        return [{'platform': 'all', 'ok': False, 'error': str(e)}]


# ---------------------------------------------------------------------------
# Interviews
# ---------------------------------------------------------------------------
# Default duration (hours) from creation time before interview link expires
INTERVIEW_LINK_EXPIRY_HOURS = 24


def _generate_interview_tokens(interview_date_str, interview_time_str):
    """Generate unique candidate + recruiter access tokens and compute expiry.

    The link stays valid for 24 hours from creation (now), ensuring candidates
    can access it anytime within that window.
    """
    candidate_token = secrets.token_urlsafe(32)
    recruiter_token = secrets.token_urlsafe(32)

    # Expiry is 24 hours from now, so links don't expire prematurely
    # regardless of when the interview is scheduled.
    expiry = datetime.now() + timedelta(hours=INTERVIEW_LINK_EXPIRY_HOURS)

    return candidate_token, recruiter_token, expiry


@api_view(['GET', 'POST'])
def interviews(request):
    if request.method == 'GET':
        # Newest first: the candidate portal matches an interview by email with
        # Array.find(), so the most recent row for a candidate must come first —
        # otherwise a stale older interview (with an old createdAt) is picked and
        # the link is wrongly shown as expired.
        qs = InterviewLink.objects.all().order_by('-id')
        return Response(InterviewLinkSerializer(qs, many=True).data)

    body = request.data
    name = body.get('name')
    email = body.get('email')
    role = body.get('role')
    interview_date = body.get('interviewDate')
    time = body.get('time')
    if not all([name, email, role, interview_date, time]):
        return err('name, email, role, interviewDate and time are required')

    serializer = InterviewLinkSerializer(data=body)
    if not serializer.is_valid():
        return serializer_err(serializer)
    c_token, r_token, expiry = _generate_interview_tokens(interview_date, time)
    serializer.save(candidate_token=c_token, recruiter_token=r_token, link_expires_at=expiry)
    return Response(serializer.data, status=201)


@api_view(['PUT'])
def interview_detail(request, pk):
    obj = InterviewLink.objects.filter(pk=pk).first()
    if not obj:
        return err('Interview not found', 404)
    serializer = InterviewLinkSerializer(obj, data=request.data, partial=True)
    if not serializer.is_valid():
        return serializer_err(serializer)
    serializer.save()
    return Response(serializer.data)


@api_view(['POST'])
def interviews_bulk_send_emails(request):
    """Send emails to multiple candidates (mark email_sent=True for their interviews)."""
    body = request.data
    interview_ids = body.get('interviewIds', [])
    if not isinstance(interview_ids, list) or len(interview_ids) == 0:
        return err('interviewIds array is required')

    qs = InterviewLink.objects.filter(id__in=interview_ids)
    if not qs.exists():
        return err('No interviews found with the provided IDs', 404)

    updated_count = qs.update(email_sent=True)
    updated_interviews = InterviewLinkSerializer(qs, many=True).data

    return JsonResponse({
        'ok': True,
        'message': f'Emails sent to {updated_count} candidate(s)',
        'count': updated_count,
        'interviews': updated_interviews,
    }, status=200)


# ---------------------------------------------------------------------------
# Follow-up emails (Selected / Waitlisted / Rejected) — sent server-side
# ---------------------------------------------------------------------------
def _followup_template(outcome, name, role, start_phrase='the first week of next month'):
    role = role or 'the role'
    name = name or 'there'
    o = str(outcome or '').strip().lower()
    if o == 'selected':
        subject = f'Congratulations! Offer for {role} at Eversoft'
        body = (
            f'Dear {name},<br><br>'
            f'We are delighted to let you know that you have been <strong>selected</strong> for the '
            f'<strong>{role}</strong> position at Eversoft. The whole panel was impressed by your '
            f'performance during the interview.<br><br>'
            f'Our HR team will reach out shortly with your offer details and onboarding steps. '
            f'We are looking forward to having you join us, with a tentative start in {start_phrase}.<br><br>'
            f'Warm regards,<br>The Eversoft Talent Team'
        )
    elif o == 'waitlisted':
        subject = f'Your application for {role} at Eversoft'
        body = (
            f'Dear {name},<br><br>'
            f'Thank you for interviewing for the <strong>{role}</strong> position. You did really well, '
            f'and we have placed your application on our <strong>waitlist</strong>. Should a suitable '
            f'opening become available, we will be in touch right away.<br><br>'
            f'We genuinely appreciate the time and energy you invested in the process.<br><br>'
            f'Warm regards,<br>The Eversoft Talent Team'
        )
    else:  # rejected / default
        subject = f'Update on your application for {role} at Eversoft'
        body = (
            f'Dear {name},<br><br>'
            f'Thank you for taking the time to interview for the <strong>{role}</strong> position and for '
            f'your interest in Eversoft. After careful consideration, we have decided not to move forward '
            f'with your application at this time.<br><br>'
            f'This was a difficult decision — we encourage you to apply for future roles that match your '
            f'skills. We wish you the very best in your job search.<br><br>'
            f'Warm regards,<br>The Eversoft Talent Team'
        )
    return subject, body


@api_view(['POST'])
def interview_send_followup(request):
    """Send a single follow-up email server-side (via the configured SMTP) and
    record the outcome on the interview row. Body:
        {interviewId, outcome}                      (looks up name/email/role)
      or {toEmail, toName, role, outcome}           (explicit)
      optional: senderEmail (whose SMTP to send from), subject, body
    """
    body = request.data
    outcome = body.get('outcome')
    obj = None
    interview_id = body.get('interviewId')
    if interview_id:
        obj = InterviewLink.objects.filter(pk=interview_id).first()
        if not obj:
            return err('Interview not found', 404)

    to_email = body.get('toEmail') or (obj.email if obj else None)
    to_name = body.get('toName') or (obj.name if obj else None)
    role = body.get('role') or (obj.role if obj else None)
    if not to_email:
        return err('toEmail (or a valid interviewId) is required')
    if not outcome:
        return err('outcome is required (Selected | Waitlisted | Rejected)')

    subject = body.get('subject')
    inner = body.get('body')
    if not subject or not inner:
        subject, inner = _followup_template(outcome, to_name, role)

    html = mailer.render_branded(
        title=subject,
        intro='',
        highlight_html=f'<div style="font-size:15px;line-height:1.7;color:#334155;">{inner}</div>',
    )
    result = mailer.send_email(
        to=to_email, subject=subject, html=html,
        text=re.sub(r'<[^>]+>', '', inner.replace('<br>', '\n')),
        sender_email=body.get('senderEmail'),
    )
    if not result.get('ok'):
        return err('Could not send the follow-up email: ' + result.get('error', 'unknown error'), 502)

    if obj:
        obj.outcome = outcome
        obj.email_sent = True
        obj.save(update_fields=['outcome', 'email_sent'])
    return Response({'ok': True, 'message': f'Follow-up sent to {to_name or to_email}.'})


# ---------------------------------------------------------------------------
# Interview token verification & link management
# ---------------------------------------------------------------------------
@api_view(['POST'])
def interview_verify_token(request):
    """Verify a candidate or recruiter interview access token.

    POST /api/interviews/verify-token
    Body: {token: "<candidate_token or recruiter_token>"}

    Returns the interview details and whether the link is still valid.
    """
    body = request.data
    token = str(body.get('token') or '').strip()
    if not token:
        return err('token is required')

    # Determine token type (candidate or recruiter)
    obj = InterviewLink.objects.filter(candidate_token=token).first()
    token_type = 'candidate'
    if not obj:
        obj = InterviewLink.objects.filter(recruiter_token=token).first()
        token_type = 'recruiter'

    if not obj:
        return JsonResponse({'valid': False, 'reason': 'Token not found'}, status=404)

    now = datetime.now()

    # Check expiry
    if obj.link_expires_at and now > obj.link_expires_at:
        # Auto-update status to Expired
        if obj.status not in ('Completed', 'Expired'):
            obj.status = 'Expired'
            obj.save(update_fields=['status'])
        return JsonResponse({
            'valid': False,
            'reason': 'Interview link has expired',
            'expiredAt': dt(obj.link_expires_at),
            'interviewId': obj.id,
        })

    # Mark as Active when first accessed
    if obj.status == 'Scheduled':
        obj.status = 'Active'
        obj.save(update_fields=['status'])

    data = InterviewLinkSerializer(obj).data
    data['tokenType'] = token_type
    data['valid'] = True
    return Response(data)


@api_view(['POST'])
def interview_regenerate_link(request, pk):
    """Regenerate candidate and recruiter tokens for an interview.

    POST /api/interviews/<id>/regenerate-link
    Body: {extendHours: 48}  (optional, default=24)
    """
    obj = InterviewLink.objects.filter(pk=pk).first()
    if not obj:
        return err('Interview not found', 404)

    body = request.data
    extend_hours = int(body.get('extendHours') or INTERVIEW_LINK_EXPIRY_HOURS)

    c_token = secrets.token_urlsafe(32)
    r_token = secrets.token_urlsafe(32)
    # Extend from interview date if available, otherwise from now
    new_expiry, _, _ = _generate_interview_tokens(
        obj.interview_date or '', obj.interview_time or ''
    )
    # Allow explicit override
    if body.get('extendHours'):
        new_expiry = datetime.now() + timedelta(hours=extend_hours)

    obj.candidate_token = c_token
    obj.recruiter_token = r_token
    obj.link_expires_at = new_expiry
    if obj.status == 'Expired':
        obj.status = 'Scheduled'
    obj.save(update_fields=['candidate_token', 'recruiter_token', 'link_expires_at', 'status'])

    return Response({
        'ok': True,
        'candidateToken': c_token,
        'recruiterToken': r_token,
        'linkExpiresAt': dt(new_expiry),
        'message': 'Interview links regenerated successfully.',
    })


@api_view(['POST'])
def interview_resend_invitation(request, pk):
    """Resend the interview invitation email to the candidate.

    POST /api/interviews/<id>/resend-invitation
    Body: {senderEmail, origin}  (optional)
    """
    obj = InterviewLink.objects.filter(pk=pk).first()
    if not obj:
        return err('Interview not found', 404)

    body = request.data
    origin = body.get('origin') or _request_origin_from_meta(request)

    # Regenerate tokens before resending so the new link is fresh
    c_token = secrets.token_urlsafe(32)
    r_token = secrets.token_urlsafe(32)
    new_expiry, _, _ = _generate_interview_tokens(
        obj.interview_date or '', obj.interview_time or ''
    )
    obj.candidate_token = c_token
    obj.recruiter_token = r_token
    obj.link_expires_at = new_expiry
    obj.email_sent = False
    obj.save(update_fields=['candidate_token', 'recruiter_token', 'link_expires_at', 'email_sent'])

    candidate_url = f'{origin}/interview-access?token={c_token}'
    recruiter_url = f'{origin}/interview-access?token={r_token}'

    html = mailer.render_branded(
        title=f'Interview Invitation — {obj.role}',
        intro=(
            f'Dear {obj.name},<br><br>'
            f'Your interview for the <strong>{obj.role}</strong> position has been scheduled.<br>'
            f'<strong>Date:</strong> {obj.interview_date or "TBD"}&nbsp;&nbsp;'
            f'<strong>Time:</strong> {obj.interview_time or "TBD"}<br>'
            f'<strong>Platform:</strong> {obj.platform or "To be confirmed"}<br><br>'
            f'Click the button below to join your interview session at the scheduled time.'
        ),
        highlight_html=(
            f'<div style="text-align:center;margin:18px 0;">'
            f'<a href="{candidate_url}" target="_blank" rel="noreferrer noopener" '
            f'style="display:inline-block;background:linear-gradient(135deg,#4f8ef7,#a855f7);'
            f'color:#fff;font-size:15px;font-weight:700;text-decoration:none;'
            f'padding:14px 38px;border-radius:10px;">Join Interview</a></div>'
            f'<div style="text-align:center;"><a href="{candidate_url}" '
            f'style="color:#94a3b8;font-size:12px;word-break:break-all;">{candidate_url}</a></div>'
        ),
        footer=(
            f'This link is valid for 24 hours from now. '
            f'If you encounter any issues, contact your recruiter.'
        ),
    )
    text = (
        f'Hi {obj.name},\n\nYour interview for {obj.role} is scheduled on '
        f'{obj.interview_date} at {obj.interview_time}.\n\n'
        f'Join here: {candidate_url}\n\n'
        f'This link expires in 24 hours. If you cannot join by then, contact your recruiter for a new link.'
    )
    result = mailer.send_email(
        to=obj.email,
        subject=f'Interview Invitation — {obj.role}',
        html=html,
        text=text,
        sender_email=body.get('senderEmail'),
    )
    if not result.get('ok'):
        return err('Could not send invitation: ' + result.get('error', 'unknown error'), 502)

    obj.email_sent = True
    obj.save(update_fields=['email_sent'])

    return Response({
        'ok': True,
        'message': f'Invitation resent to {obj.email}.',
        'candidateToken': c_token,
        'recruiterToken': r_token,
        'recruiterUrl': recruiter_url,
    })


def _request_origin_from_meta(request):
    scheme = 'https' if request.is_secure() else request.scheme
    host = request.get_host()
    return f'{scheme}://{host}'


# ---------------------------------------------------------------------------
# Resume Scores
# ---------------------------------------------------------------------------
# Minimum qualifying score; resumes below this are not stored in the DB.
RESUME_SCORE_MIN = 75


@api_view(['GET', 'POST'])
def resume_scores(request):
    if request.method == 'GET':
        return Response(ResumeScoreSerializer(ResumeScore.objects.all(), many=True).data)

    body = request.data
    if isinstance(body, list):
        if len(body) == 0:
            return err('resume upload array is required')
        created = []
        for item in body:
            if not item.get('name') and item.get('fileName'):
                item['name'] = os.path.splitext(item.get('fileName'))[0]
            serializer = ResumeScoreSerializer(data=item)
            if not serializer.is_valid():
                return serializer_err(serializer)
            score_val = int(serializer.validated_data.get('score') or 0)
            if score_val < RESUME_SCORE_MIN:
                return Response(
                    {
                        'stored': False,
                        'score': score_val,
                        'threshold': RESUME_SCORE_MIN,
                        'message': f'Score {score_val} is below the minimum of {RESUME_SCORE_MIN}; not stored.',
                    },
                    status=422,
                )
            serializer.save()
            created.append(serializer.data)
        return Response(created, status=201)

    if not body.get('name') and body.get('fileName'):
        body['name'] = os.path.splitext(body.get('fileName'))[0]
    if not body.get('name'):
        return err('name is required')
    serializer = ResumeScoreSerializer(data=body)
    if not serializer.is_valid():
        return serializer_err(serializer)

    # Only persist resumes that meet the qualifying score threshold.
    try:
        score_val = int(serializer.validated_data.get('score') or 0)
    except (TypeError, ValueError):
        score_val = 0
    if score_val < RESUME_SCORE_MIN:
        # Return a non-2xx status so the frontend's fetch helper treats this as
        # "not saved" (it only appends a row on a 2xx record response). A 2xx
        # here would push a non-record object into the UI list and crash render.
        return Response(
            {
                'stored': False,
                'score': score_val,
                'threshold': RESUME_SCORE_MIN,
                'message': f'Score {score_val} is below the minimum of {RESUME_SCORE_MIN}; not stored.',
            },
            status=422,
        )

    serializer.save()
    return Response(serializer.data, status=201)


# ---------------------------------------------------------------------------
# Interview Recordings
# ---------------------------------------------------------------------------
def _recording_list_qs():
    return InterviewRecording.objects.annotate(
        _has_video=Case(
            When(video_buffer__isnull=False, then=Value(True)),
            default=Value(False), output_field=BooleanField(),
        ),
        _has_recording=Case(
            When(recording_data__isnull=False, then=Value(True)),
            default=Value(False), output_field=BooleanField(),
        ),
    ).defer('video_buffer', 'recording_data')


RECORDING_FIELD_MAP = {
    'candidateName': 'candidate_name', 'candidateEmail': 'candidate_email',
    'role': 'role', 'duration': 'duration', 'verdict': 'verdict',
    'totalScore': 'total_score', 'techScore': 'tech_score',
    'commScore': 'comm_score', 'integrityScore': 'integrity_score',
    'recordingData': 'recording_data', 'transcript': 'transcript',
    'responses': 'responses',
}


@api_view(['GET', 'POST'])
def recordings(request):
    if request.method == 'GET':
        return Response(InterviewRecordingSerializer(_recording_list_qs(), many=True).data)

    body = request.data
    if not body.get('candidateName'):
        return err('candidateName is required')
    serializer = InterviewRecordingSerializer(data=body)
    if not serializer.is_valid():
        return serializer_err(serializer)
    serializer.save()
    return Response(serializer.data, status=201)


@api_view(['GET', 'PUT', 'DELETE'])
def recording_detail(request, pk):
    obj = InterviewRecording.objects.filter(pk=pk).first()
    if not obj:
        return err('Recording not found', 404)

    if request.method == 'GET':
        data = InterviewRecordingSerializer(obj).data
        data['recordingData'] = obj.recording_data
        return Response(data)

    if request.method == 'PUT':
        body = request.data
        changed = False
        for key, value in body.items():
            col = RECORDING_FIELD_MAP.get(key)
            if not col:
                continue
            if col == 'responses':
                setattr(obj, col, value if isinstance(value, list) else [])
            else:
                setattr(obj, col, value)
            changed = True
        if changed:
            obj.save()
        return Response({'ok': True, 'id': obj.id})

    # DELETE
    obj.delete()
    return Response({'ok': True})


@csrf_exempt
def recording_video(request, pk):
    """Binary (video/webm) upload + download. Kept as a plain Django view
    because DRF's JSON parser cannot consume a raw binary body."""
    obj = InterviewRecording.objects.filter(pk=pk).first()
    if not obj:
        return err('Recording not found', 404)

    if request.method == 'POST':
        data = request.body
        if not data:
            return err('Invalid or empty video payload')
        mime = (request.META.get('CONTENT_TYPE') or 'video/webm').split(';')[0]
        obj.video_buffer = data
        obj.video_mime = mime
        obj.save(update_fields=['video_buffer', 'video_mime'])
        return JsonResponse({'ok': True})

    if request.method == 'GET':
        if not obj.video_buffer:
            return err('No video data for this recording', 404)
        payload = bytes(obj.video_buffer)
        resp = HttpResponse(payload, content_type=obj.video_mime or 'video/webm')
        resp['Content-Length'] = str(len(payload))
        resp['Accept-Ranges'] = 'bytes'
        resp['Cache-Control'] = 'public, max-age=31536000'
        return resp

    return err('Method not allowed', 405)


# ---------------------------------------------------------------------------
# Question Sets
# ---------------------------------------------------------------------------
@api_view(['POST'])
def question_sets(request):
    body = request.data
    questions = body.get('questions')
    if not isinstance(questions, list) or len(questions) == 0:
        return JsonResponse({'error': 'questions array required'}, status=400)
    new_id = 'q_' + secrets.token_hex(4)
    QuestionSet.objects.create(id=new_id, questions=questions)
    return Response({'id': new_id})


@api_view(['GET'])
def question_set_detail(request, set_id):
    obj = QuestionSet.objects.filter(pk=set_id).first()
    if not obj:
        return JsonResponse({'error': 'Not found'}, status=404)
    return Response({'questions': safe_list(obj.questions)})


# ---------------------------------------------------------------------------
# AI proxy
# ---------------------------------------------------------------------------
@api_view(['GET'])
def ai_status(request):
    available = ai.check_ai_key_valid(request.headers.get('x-api-key'))
    return Response({'available': available})


@api_view(['POST'])
def ai_generate_questions(request):
    """Generate interview questions.

    Supports two modes:
      Legacy: {prompt: "..."}
      Enhanced: {resumeText, jdText, jobRole, experienceLevel, skills, candidateName, questionCount}
    """
    body = request.data
    prompt = body.get('prompt') or ''
    params = {
        'resumeText': body.get('resumeText') or body.get('resume_text') or '',
        'jdText': body.get('jdText') or body.get('jd_text') or '',
        'jobRole': body.get('jobRole') or body.get('job_role') or '',
        'experienceLevel': body.get('experienceLevel') or body.get('experience_level') or 'Mid-level',
        'skills': body.get('skills') or [],
        'candidateName': body.get('candidateName') or body.get('candidate_name') or '',
        'questionCount': body.get('questionCount') or body.get('question_count') or 10,
    }
    has_structured = any([params['resumeText'], params['jdText'], params['jobRole']])
    if not prompt and not has_structured:
        return JsonResponse({'error': {'message': 'prompt or structured params (resumeText/jdText/jobRole) are required'}}, status=400)

    payload = ai.generate_questions(
        prompt,
        request_key=request.headers.get('x-api-key'),
        params=params if has_structured else None,
    )
    return Response(payload)


# ---------------------------------------------------------------------------
# User Settings
# ---------------------------------------------------------------------------
@api_view(['POST','GET'])
def user_settings(request, email):
    email = norm_email(email)
    if not email:
        return err('email is required')
    profile = UserProfile.objects.filter(pk=email).first()
    email_cfg = UserEmailConfig.objects.filter(pk=email).first()
    docs = UserDocument.objects.filter(user_email=email)
    return Response({
        'profile': UserProfileSerializer(profile).data if profile else None,
        'emailConfig': UserEmailConfigSerializer(email_cfg).data if email_cfg else None,
        'documents': UserDocumentSerializer(docs, many=True).data,
    })


@api_view(['PUT'])
def user_profile(request, email):
    email = norm_email(email)
    if not email:
        return err('email is required')
    body = request.data
    obj, _ = UserProfile.objects.update_or_create(
        email=email,
        defaults={
            'first_name': body.get('firstName', ''),
            'last_name': body.get('lastName', ''),
            'phone': body.get('phone', ''),
            'alt_email': body.get('altEmail', ''),
            'blood_group': body.get('bloodGroup', ''),
            'address': body.get('address', ''),
            'profile_pic': body.get('profilePic', ''),
        },
    )
    return Response(UserProfileSerializer(obj).data)


@api_view(['PUT'])
def user_email_config(request, email):
    email = norm_email(email)
    if not email:
        return err('email is required')
    body = request.data
    social = body.get('social', {})
    obj, _ = UserEmailConfig.objects.update_or_create(
        user_email=email,
        defaults={
            'smtp_host': body.get('smtpHost', ''),
            'smtp_port': str(body.get('smtpPort', '') or ''),
            'smtp_user': body.get('smtpUser', ''),
            'smtp_password': body.get('smtpPassword', ''),
            'smtp_secure': bool(body.get('smtpSecure', False)),
            'from_name': body.get('fromName', ''),
            'from_email': body.get('fromEmail', ''),
            'social': social if isinstance(social, dict) else {},
        },
    )
    return Response(UserEmailConfigSerializer(obj).data)


@api_view(['POST'])
def user_documents(request, email):
    email = norm_email(email)
    body = request.data
    doc_type = body.get('docType')
    file_data = body.get('fileData')
    if not email or not doc_type or not file_data:
        return err('email, docType and fileData are required')
    obj, _ = UserDocument.objects.update_or_create(
        user_email=email, doc_type=doc_type,
        defaults={
            'file_name': body.get('fileName', ''),
            'file_mime': body.get('fileMime', ''),
            'file_data': file_data,
        },
    )
    return Response(UserDocumentSerializer(obj).data, status=201)


@api_view(['GET', 'DELETE'])
def user_document_detail(request, email, doc_type):
    email = norm_email(email)
    if request.method == 'GET':
        doc = UserDocument.objects.filter(user_email=email, doc_type=doc_type).first()
        if not doc:
            return err('Document not found', 404)
        return Response(UserDocumentSerializer(doc, include_data=True).data)

    # DELETE
    deleted, _ = UserDocument.objects.filter(user_email=email, doc_type=doc_type).delete()
    if deleted == 0:
        return err('Document not found', 404)
    return Response({'ok': True})


# ---------------------------------------------------------------------------
# App Users (Settings -> User Access logins)
# Backs services/usersApi.js: every login created in the app is stored in the
# `app_users` table so it persists in MySQL, not just browser localStorage.
# ---------------------------------------------------------------------------
@api_view(['GET', 'POST'])
def users(request):
    if request.method == 'GET':
        return Response(AppUserSerializer(AppUser.objects.all(), many=True).data)

    body = request.data
    name = str(body.get('name') or '').strip()
    email = norm_email(body.get('email'))
    password = body.get('password') or ''
    if not name or not email or not password:
        return err('name, email and password are required')
    if AppUser.objects.filter(email=email).exists():
        return err('A login with this email already exists', 409)
    serializer = AppUserSerializer(data={**body, 'name': name, 'email': email})
    if not serializer.is_valid():
        return serializer_err(serializer)
    serializer.save()
    return Response(serializer.data, status=201)


@api_view(['PUT', 'DELETE'])
def user_detail(request, email):
    email = norm_email(email)
    if not email:
        return err('email is required')
    obj = AppUser.objects.filter(email=email).first()
    if not obj:
        return err('User not found', 404)

    if request.method == 'PUT':
        body = request.data
        if body.get('name'):
            obj.full_name = str(body['name']).strip()
            obj.initials = make_initials(obj.full_name)
        if body.get('password'):
            obj.password = body['password']
        if body.get('role'):
            obj.role = body['role']
        if body.get('status'):
            obj.status = body['status']
        obj.save()
        return Response(AppUserSerializer(obj).data)

    # DELETE
    obj.delete()
    return Response({'ok': True})


# ---------------------------------------------------------------------------
# Public client config (exposes safe, non-secret settings to the frontend)
# ---------------------------------------------------------------------------
@api_view(['GET'])
def client_config(request):
    """Return public configuration needed by the frontend JS.
    Only expose values that are safe to be public (e.g. OAuth client IDs).
    """
    import os
    return Response({
        'googleClientId': os.environ.get('GOOGLE_CLIENT_ID', ''),
    })


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@api_view(['GET'])
def health(request):
    return Response({
        'ok': True, 'mode': 'mysql', 'database': settings.DATABASES['default']['NAME'],
        'jobs': JobPost.objects.count(),
        'interviews': InterviewLink.objects.count(),
        'resumeScores': ResumeScore.objects.count(),
        'recordings': InterviewRecording.objects.count(),
        'appUsers': AppUser.objects.count(),
    })


# ---------------------------------------------------------------------------
# SPA fallback — serve the built React index.html for all non-API routes.
# ---------------------------------------------------------------------------
_INDEX_BYTES = None


def spa_index(request):
    global _INDEX_BYTES
    index_path = settings.REACT_BUILD_DIR / 'index.html'
    if not index_path.exists():
        return HttpResponse(
            '<h1>React build not found</h1>'
            f'<p>Expected {index_path}. Run <code>npm run build</code> in the '
            'project root, or set REACT_BUILD_DIR in .env.</p>',
            status=200, content_type='text/html',
        )
    if _INDEX_BYTES is None or settings.DEBUG:
        _INDEX_BYTES = index_path.read_bytes()
    return HttpResponse(_INDEX_BYTES, content_type='text/html')
