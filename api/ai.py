"""
AI interview-question generation.

Mirrors the original Node proxy: if an Anthropic API key is available the
prompt is forwarded to Claude; otherwise a deterministic local generator
produces sensible questions so the feature keeps working offline.
"""
import os
import re

import requests

ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages'
ANTHROPIC_VERSION = '2023-06-01'

# The legacy Claude 3/3.5 snapshot names now return 404 not_found_error for
# this account, which silently forced the canned local-question fallback (the
# AI appeared to "not generate"). Use current-generation models the key can
# access; override via env if the available models change.
GENERATION_MODEL = os.environ.get('ANTHROPIC_MODEL') or 'claude-sonnet-4-5'
VALIDATION_MODEL = os.environ.get('ANTHROPIC_VALIDATION_MODEL') or 'claude-haiku-4-5'

_ai_status_cache = None


def _api_key(request_key=None):
    return request_key or os.environ.get('VITE_ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_API_KEY')


def check_ai_key_valid(request_key=None):
    """Validate a key with a 1-token ping. Caches the env-key result."""
    global _ai_status_cache
    api_key = _api_key(request_key)
    if not api_key:
        return False
    if not request_key and _ai_status_cache is not None:
        return _ai_status_cache
    try:
        resp = requests.post(
            ANTHROPIC_URL,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': ANTHROPIC_VERSION,
            },
            json={
                'model': VALIDATION_MODEL,
                'max_tokens': 1,
                'messages': [{'role': 'user', 'content': 'hi'}],
            },
            timeout=15,
        )
        valid = resp.ok
        if not request_key:
            _ai_status_cache = valid
        return valid
    except requests.RequestException:
        return False


def build_enhanced_prompt(params):
    """Build a rich, structured prompt from candidate and job data.

    params keys (all optional, fall back to prompt if absent):
      resume_text, jd_text, experience_level, skills (list), job_role,
      candidate_name, question_count (int, default 10)
    """
    resume_text = (params.get('resumeText') or params.get('resume_text') or '').strip()
    jd_text = (params.get('jdText') or params.get('jd_text') or '').strip()
    experience_level = str(params.get('experienceLevel') or params.get('experience_level') or 'Mid-level').strip()
    skills = params.get('skills') or []
    job_role = str(params.get('jobRole') or params.get('job_role') or 'Software Engineer').strip()
    candidate_name = str(params.get('candidateName') or params.get('candidate_name') or 'the candidate').strip()
    count = int(params.get('questionCount') or params.get('question_count') or 10)

    skills_str = ', '.join(skills) if skills else 'as identified from the resume and JD'

    prompt = f"""You are an expert technical interviewer. Generate exactly {count} targeted interview questions for the following candidate and position.

CANDIDATE: {candidate_name}
JOB ROLE: {job_role}
EXPERIENCE LEVEL: {experience_level}
KEY SKILLS: {skills_str}

JOB DESCRIPTION:
{jd_text or '(Not provided — infer from job role and skills)'}

CANDIDATE RESUME:
{resume_text or '(Not provided — generate role-appropriate questions)'}

Generate a balanced set of {count} interview questions covering ALL of these categories:
1. Technical Questions (skills from both resume and JD)
2. Experience-Based Questions (from candidate's projects and work history)
3. Scenario-Based Questions (practical, role-specific problem-solving)
4. Behavioral Questions (teamwork, leadership, communication, adaptability)
5. Gap Analysis Questions (skills or experience missing from JD requirements)

Rules:
- Every question must be specific to this candidate's background and the job role.
- Vary difficulty: mix Easy, Medium, and Hard questions.
- For technical questions, reference actual technologies from the resume/JD.
- For experience questions, reference specific projects or roles from the resume.
- For gap questions, target skills listed in the JD but absent from the resume.
- Return ONLY a valid JSON array of question strings, no explanations, no markdown.

Example format:
["Question 1 text?", "Question 2 text?", ...]"""

    return prompt


def generate_questions(prompt, request_key=None, params=None):
    """Return the raw Anthropic-style payload (content[0].text holds JSON).

    If params dict is provided with structured data (resumeText, jdText, etc.)
    a rich prompt is built server-side and used instead of the raw prompt string.
    """
    # Use structured params to build a richer prompt when available
    if params and (params.get('resumeText') or params.get('jdText') or params.get('jobRole')):
        prompt = build_enhanced_prompt(params)

    api_key = _api_key(request_key)
    if not api_key:
        return {'content': [{'text': _json_dumps(generate_local_questions(prompt))}]}

    try:
        upstream = requests.post(
            ANTHROPIC_URL,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': ANTHROPIC_VERSION,
            },
            json={
                'model': GENERATION_MODEL,
                'max_tokens': 3000,
                'messages': [{'role': 'user', 'content': prompt}],
            },
            timeout=90,
        )
        if not upstream.ok:
            return {'content': [{'text': _json_dumps(generate_local_questions(prompt))}]}
        return upstream.json()
    except requests.RequestException:
        return {'content': [{'text': _json_dumps(generate_local_questions(prompt))}]}


def _json_dumps(value):
    import json
    return json.dumps(value)


def generate_local_questions(prompt):
    job_title = 'Software Engineer'
    tech_keywords = ['JavaScript', 'React', 'Node.js']

    jd_index = prompt.find('JOB DESCRIPTION:')
    resume_index = prompt.find('CANDIDATE RESUME:')
    jd_section = ''
    resume_section = ''
    if jd_index != -1:
        jd_section = (
            prompt[jd_index + 16:resume_index]
            if resume_index != -1
            else prompt[jd_index + 16:]
        )
    if resume_index != -1:
        resume_section = prompt[resume_index + 17:]

    combined_text = (jd_section + ' ' + resume_section).lower()

    titles = [
        'frontend', 'backend', 'fullstack', 'full-stack', 'devops',
        'data scientist', 'product manager', 'ux designer', 'sales',
        'engineering manager', 'hr manager',
    ]
    for t in titles:
        if t in combined_text:
            job_title = ' '.join(
                w[:1].upper() + w[1:] for w in re.split(r'[- ]+', t)
            )
            break

    techs = [
        'react', 'vue', 'angular', 'node', 'express', 'python', 'java', 'go',
        'golang', 'rust', 'c++', 'aws', 'docker', 'kubernetes', 'sql', 'mysql',
        'postgresql', 'mongodb', 'typescript', 'javascript', 'css', 'html',
        'next.js', 'django', 'fastapi',
    ]
    display = {
        'javascript': 'JavaScript', 'typescript': 'TypeScript', 'react': 'React',
        'node': 'Node.js', 'golang': 'Go', 'go': 'Go', 'aws': 'AWS',
        'docker': 'Docker', 'kubernetes': 'Kubernetes', 'postgresql': 'PostgreSQL',
        'mongodb': 'MongoDB',
    }
    found = []
    for tech in techs:
        if tech in combined_text:
            name = display.get(tech, tech.upper())
            if name not in found:
                found.append(name)
    if found:
        tech_keywords = found[:4]

    primary = tech_keywords[0] if tech_keywords else 'software engineering'
    list_str = ', '.join(tech_keywords) if tech_keywords else 'relevant technologies'

    total = 8
    count_match = re.search(r'generate exactly (\d+) targeted interview questions', prompt)
    if count_match:
        total = int(count_match.group(1))

    difficulties = []
    diff_list_match = re.search(r'one of each difficulty:\s*([A-Za-z,\s]+)\)', prompt)
    if diff_list_match:
        listed = diff_list_match.group(1).lower()
        if 'easy' in listed:
            difficulties.append('Easy')
        if 'medium' in listed:
            difficulties.append('Medium')
        if 'hard' in listed:
            difficulties.append('Hard')
    if not difficulties:
        single = re.search(r'coding challenge of (Easy|Medium|Hard) difficulty', prompt)
        difficulties.append(single.group(1) if single else 'Medium')

    questions = []
    # Only emit coding challenges when the prompt actually asks for them; the
    # Technical round no longer includes a coding challenge by default.
    if 'Technical' in prompt and 'coding challenge' in prompt.lower():
        for diff in difficulties:
            if diff == 'Easy':
                questions.append('Coding Challenge (Easy): Write a function reverseString(str) that takes a string and returns it reversed in-place.')
            elif diff == 'Hard':
                questions.append('Coding Challenge (Hard): Implement a Least Recently Used (LRU) Cache with get(key) and put(key, value) operations running in O(1) time complexity.')
            else:
                questions.append('Coding Challenge (Medium): Write a function flattenObject(obj, delimiter) that flattens a nested object into a single level, joining key paths using the delimiter.')

    tech_pool = [
        f'Can you describe your experience working as a {job_title}? Specifically, how have you utilized {primary} in your recent projects to solve complex problems?',
        f'When building applications with {list_str}, what architectural trade-offs do you usually consider regarding performance, maintainability, and scalability?',
        f'Explain the concept of state management or data consistency in modern systems. How would you design a robust system using {primary} to prevent data loss?',
        f'Suppose we need to optimize a slow API endpoint in a production environment using {primary}. What steps and diagnostic tools would you use to profile and speed up the response time?',
        f'How do you handle asynchronous operations and concurrent requests in {primary} to prevent race conditions or memory leaks?',
    ]
    hr_pool = [
        'Tell me about a time when you had to collaborate with cross-functional team members (like designers or product managers) who had different priorities. How did you align on a solution?',
        'How do you handle a situation where a technical deadline is fast approaching, but you identify a significant flaw in the system architecture? Walk me through your decision-making process.',
        f'What aspect of working as a {job_title} excites you the most, and how do you keep yourself updated with the fast-evolving landscape of {list_str}?',
        'Describe a challenging technical obstacle you encountered in a previous project. What debugging strategies and tools did you employ to identify and resolve the root cause?',
    ]

    tech_idx = 0
    hr_idx = 0
    while len(questions) < total:
        if tech_idx < len(tech_pool) and (len(questions) % 2 == 1 or hr_idx >= len(hr_pool)):
            questions.append(tech_pool[tech_idx])
            tech_idx += 1
        elif hr_idx < len(hr_pool):
            questions.append(hr_pool[hr_idx])
            hr_idx += 1
        else:
            questions.append(f'Question {len(questions) + 1}: Describe your design pattern preferences when building scalable microservices or front-end components.')
    return questions
