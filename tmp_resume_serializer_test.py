import os
import sys
sys.path.insert(0, os.getcwd())
from api.serializers import ResumeScoreSerializer

payload = {
    'fileName': 'resume.docx',
    'fileMime': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'fileData': 'SGVsbG8gd29ybGQ=',
    'score': 80,
}
serializer = ResumeScoreSerializer(data=payload)
print('valid', serializer.is_valid())
print('errors', serializer.errors)
print('validated_data', serializer.validated_data if serializer.is_valid() else None)
