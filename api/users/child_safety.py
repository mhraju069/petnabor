"""
GET /child-safety/ — Pet Nabor's public Child Safety Standards.

Google Play requires a public web page (not a PDF) that names the app,
explicitly prohibits child sexual abuse and exploitation (CSAE), and gives a
child safety contact; Play Console → App content → Child safety standards
links here. Contact: settings.CHILD_SAFETY_EMAIL.
"""

from django.conf import settings
from django.shortcuts import render


def child_safety_standards(request):
    return render(request, "web/child_safety.html", {"contact_email": settings.CHILD_SAFETY_EMAIL})
