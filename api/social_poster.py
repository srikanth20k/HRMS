"""
Auto-post a new job opening to a user's linked social accounts.

Real platform APIs are called (LinkedIn UGC Posts + X/Twitter v2). Posting to
these platforms requires an OAuth access token with write scope, which the user
supplies in Settings -> Email Configuration -> Social Media Accounts. The token
(and, for LinkedIn, the author URN) are stored alongside each platform's profile
URL in the `user_email_config.social` JSON column, e.g.:

    "social": {
      "linkedin": {
        "url": "https://linkedin.com/in/acme",
        "accessToken": "<OAuth2 token, scope w_member_social>",
        "authorUrn": "urn:li:person:XXXX"   // or urn:li:organization:XXXX
      },
      "twitter": {
        "url": "https://x.com/acme",
        "accessToken": "<OAuth2 user token, scope tweet.write>"
      }
    }

A platform with only a URL (no token) is skipped with an explanatory note, so
the feature degrades gracefully until credentials are added.
"""
import json

import requests

LINKEDIN_POSTS_URL = 'https://api.linkedin.com/v2/ugcPosts'
TWITTER_TWEETS_URL = 'https://api.twitter.com/2/tweets'
TIMEOUT = 20


def _platform_cfg(social, key):
    """Normalise a platform entry to a dict with url/accessToken/authorUrn."""
    val = (social or {}).get(key)
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        return {'url': val}
    return {}


def build_message(job):
    """Plain-text job announcement shared across platforms."""
    title = job.get('title', 'New role')
    dept = job.get('dept', '')
    location = job.get('location', '')
    jtype = job.get('type', '')
    salary = job.get('salary', '')
    bits = [f'\U0001f680 We’re hiring: {title}']
    meta = ' · '.join([x for x in [dept, location, jtype] if x])
    if meta:
        bits.append(meta)
    if salary:
        bits.append(f'\U0001f4b0 {salary}')
    desc = (job.get('description') or '').strip()
    if desc:
        bits.append(desc[:280])
    bits.append('#hiring #jobs #careers')
    return '\n\n'.join(bits)


def _post_linkedin(cfg, message):
    token = cfg.get('accessToken')
    author = cfg.get('authorUrn')
    if not token:
        return {'platform': 'linkedin', 'ok': False, 'skipped': True,
                'error': 'No LinkedIn accessToken configured.'}
    if not author:
        return {'platform': 'linkedin', 'ok': False, 'skipped': True,
                'error': 'No LinkedIn authorUrn (e.g. urn:li:person:XXXX) configured.'}
    payload = {
        'author': author,
        'lifecycleState': 'PUBLISHED',
        'specificContent': {
            'com.linkedin.ugc.ShareContent': {
                'shareCommentary': {'text': message},
                'shareMediaCategory': 'NONE',
            }
        },
        'visibility': {'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC'},
    }
    try:
        r = requests.post(
            LINKEDIN_POSTS_URL,
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'X-Restli-Protocol-Version': '2.0.0',
            },
            data=json.dumps(payload),
            timeout=TIMEOUT,
        )
        if r.ok:
            return {'platform': 'linkedin', 'ok': True,
                    'id': r.headers.get('x-restli-id') or r.headers.get('X-RestLi-Id')}
        return {'platform': 'linkedin', 'ok': False,
                'error': f'{r.status_code}: {r.text[:300]}'}
    except requests.RequestException as e:
        return {'platform': 'linkedin', 'ok': False, 'error': str(e)}


def _post_twitter(cfg, message):
    token = cfg.get('accessToken')
    if not token:
        return {'platform': 'twitter', 'ok': False, 'skipped': True,
                'error': 'No X/Twitter accessToken configured.'}
    try:
        r = requests.post(
            TWITTER_TWEETS_URL,
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            },
            data=json.dumps({'text': message[:280]}),
            timeout=TIMEOUT,
        )
        if r.ok:
            data = (r.json() or {}).get('data', {})
            return {'platform': 'twitter', 'ok': True, 'id': data.get('id')}
        return {'platform': 'twitter', 'ok': False,
                'error': f'{r.status_code}: {r.text[:300]}'}
    except requests.RequestException as e:
        return {'platform': 'twitter', 'ok': False, 'error': str(e)}


_POSTERS = {
    'linkedin': _post_linkedin,
    'twitter': _post_twitter,
}


def post_job(job, social):
    """Post `job` to every linked platform that has credentials.
    Returns a list of per-platform result dicts."""
    message = build_message(job)
    results = []
    for key, poster in _POSTERS.items():
        cfg = _platform_cfg(social, key)
        # Only attempt platforms the user has set up at all (URL or token present).
        if not cfg:
            continue
        results.append(poster(cfg, message))
    return results
