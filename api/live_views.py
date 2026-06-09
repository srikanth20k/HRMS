"""
REST-based WebRTC signaling for recruiter live-viewing of an F2F interview.

The candidate's browser (publisher) and the recruiter's browser (viewer)
exchange SDP + ICE candidates by polling a `live_sessions` row — no media
server or websocket is required; the actual audio/video flows peer-to-peer.

  POST /api/live/start                {sessionId, candidateName, role, interviewId, offer}
  GET  /api/live                      -> [{sessionId, candidateName, role, status, ...}]  (recruiter list)
  GET  /api/live/<sid>                -> full signaling state
  POST /api/live/<sid>/answer         {answer}                 (recruiter -> candidate)
  POST /api/live/<sid>/ice            {role, candidate}        (append one ICE candidate)
  POST /api/live/<sid>/update         {transcript, currentQuestion, status}
  POST /api/live/<sid>/end
"""
import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import LiveSession
from .views import err, parse_body

# A session with no publisher update within this window is treated as stale/ended.
STALE_SECONDS = 90


def _session_dict(o, include_signaling=False):
    base = {
        'sessionId': o.session_id,
        'candidateName': o.candidate_name,
        'role': o.role,
        'interviewId': o.interview_id,
        'status': o.status,
        'currentQuestion': o.current_question or '',
        'updatedAt': o.updated_at.strftime('%Y-%m-%d %H:%M:%S') if o.updated_at else None,
    }
    if include_signaling:
        base.update({
            'offer': o.offer,
            'answer': o.answer,
            'candidateIce': o.candidate_ice or [],
            'recruiterIce': o.recruiter_ice or [],
            'transcript': o.transcript or '',
        })
    return base


@csrf_exempt
def live_start(request):
    """Candidate publishes their SDP offer and opens the session."""
    if request.method != 'POST':
        return err('Method not allowed', 405)
    body = parse_body(request)
    sid = str(body.get('sessionId') or '').strip()
    if not sid:
        return err('sessionId is required')
    offer = body.get('offer')
    obj, _ = LiveSession.objects.update_or_create(
        session_id=sid,
        defaults={
            'candidate_name': body.get('candidateName', ''),
            'role': body.get('role', ''),
            'interview_id': body.get('interviewId'),
            'status': 'live',
            'offer': offer if isinstance(offer, str) else (json.dumps(offer) if offer else None),
            'answer': None,
            'candidate_ice': [],
            'recruiter_ice': [],
        },
    )
    return JsonResponse(_session_dict(obj, include_signaling=True), status=201)


@csrf_exempt
def live_list(request):
    """Recruiter dashboard: currently-live sessions."""
    if request.method != 'GET':
        return err('Method not allowed', 405)
    cutoff = timezone.now() - timezone.timedelta(seconds=STALE_SECONDS)
    qs = LiveSession.objects.filter(status='live')
    out = []
    for o in qs:
        # Hide sessions whose publisher stopped updating (likely closed tab).
        if o.updated_at and o.updated_at < cutoff:
            continue
        out.append(_session_dict(o))
    return JsonResponse(out, safe=False)


@csrf_exempt
def live_detail(request, sid):
    obj = LiveSession.objects.filter(session_id=sid).first()
    if not obj:
        return err('Live session not found', 404)
    if request.method != 'GET':
        return err('Method not allowed', 405)
    return JsonResponse(_session_dict(obj, include_signaling=True))


@csrf_exempt
def live_answer(request, sid):
    """Recruiter posts their SDP answer back to the candidate."""
    if request.method != 'POST':
        return err('Method not allowed', 405)
    obj = LiveSession.objects.filter(session_id=sid).first()
    if not obj:
        return err('Live session not found', 404)
    body = parse_body(request)
    answer = body.get('answer')
    if answer is None:
        return err('answer is required')
    obj.answer = answer if isinstance(answer, str) else json.dumps(answer)
    obj.save(update_fields=['answer', 'updated_at'])
    return JsonResponse({'ok': True})


@csrf_exempt
def live_ice(request, sid):
    """Append a single trickled ICE candidate from either peer."""
    if request.method != 'POST':
        return err('Method not allowed', 405)
    obj = LiveSession.objects.filter(session_id=sid).first()
    if not obj:
        return err('Live session not found', 404)
    body = parse_body(request)
    role = body.get('role')
    candidate = body.get('candidate')
    if role not in ('candidate', 'recruiter') or candidate is None:
        return err("role ('candidate'|'recruiter') and candidate are required")
    field = 'candidate_ice' if role == 'candidate' else 'recruiter_ice'
    arr = list(getattr(obj, field) or [])
    arr.append(candidate)
    setattr(obj, field, arr)
    obj.save(update_fields=[field, 'updated_at'])
    return JsonResponse({'ok': True, 'count': len(arr)})


@csrf_exempt
def live_update(request, sid):
    """Candidate pushes live transcript / current question / heartbeat."""
    if request.method != 'POST':
        return err('Method not allowed', 405)
    obj = LiveSession.objects.filter(session_id=sid).first()
    if not obj:
        return err('Live session not found', 404)
    body = parse_body(request)
    fields = ['updated_at']
    if 'transcript' in body:
        obj.transcript = body.get('transcript') or ''
        fields.append('transcript')
    if 'currentQuestion' in body:
        obj.current_question = body.get('currentQuestion') or ''
        fields.append('current_question')
    if body.get('status') in ('waiting', 'live', 'ended'):
        obj.status = body['status']
        fields.append('status')
    obj.save(update_fields=fields)
    return JsonResponse({'ok': True})


@csrf_exempt
def live_end(request, sid):
    if request.method != 'POST':
        return err('Method not allowed', 405)
    obj = LiveSession.objects.filter(session_id=sid).first()
    if not obj:
        return err('Live session not found', 404)
    obj.status = 'ended'
    obj.save(update_fields=['status', 'updated_at'])
    return JsonResponse({'ok': True})
