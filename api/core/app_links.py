"""
Files that let https://app.petnabor.com/post/<id> links open the app.

GET /.well-known/apple-app-site-association — iOS Universal Links
GET /.well-known/assetlinks.json            — Android App Links

Only /post/* opens the app; every other path stays on the web. Without the
app (or before the OS has verified these files) the link opens the web page
in api/post/web_share.py instead.
"""

from django.conf import settings
from django.http import JsonResponse

APP_PATHS = ["/post/*"]


def apple_app_site_association(request):
    return JsonResponse(
        {
            "applinks": {
                "details": [
                    {
                        "appIDs": [settings.IOS_APP_ID],
                        "components": [{"/": path} for path in APP_PATHS],
                    }
                ]
            }
        }
    )


def android_asset_links(request):
    return JsonResponse(
        [
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": settings.ANDROID_PACKAGE,
                    "sha256_cert_fingerprints": settings.ANDROID_CERT_FINGERPRINTS,
                },
            }
        ],
        safe=False,
    )
