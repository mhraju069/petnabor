# PetNabor — Social Network for Pet Lovers

PetNabor is a complete backend platform that powers a vibrant social network where pet parents can connect, share, and build meaningful relationships around their furry friends.
It allows users to create detailed profiles for themselves and their pets, share photos and stories, post updates with privacy controls, send messages, create stories that disappear after 24 hours, and even schedule real-life pet meetups. With smart friend suggestions, instant notifications, and a seamless messaging system, PetNabor makes it easy to find like-minded pet lovers nearby and grow your pet community.
Key user benefits include:

Easy onboarding with email, phone, or Google login
Beautiful pet profiles with medical and vaccination records
Share posts, stories, and blogs with reactions, comments, and hashtags
Real-time chat and meeting requests with feedback system
Referral rewards to invite friends and earn benefits
Safe environment with content reporting and moderation tools

Whether you're looking to find playdates for your dog, share adorable pet moments, discover local pet events, or simply connect with other passionate pet owners, PetNabor delivers a joyful, secure, and feature-rich experience tailored for the pet-loving community.

## Live Demo
[Live API](https://backend.petnabor.com/api/swagger/)  

## API Documentation
- **Swagger UI**: [https://backend.petnabor.com/api/swagger/](https://backend.petnabor.com/api/swagger/)  
- **Redoc**: [https://backend.petnabor.com/api/docs/](https://backend.petnabor.com/api/docs/)  


## Features
- Custom user system with email/phone signup, login, Firebase login, OTP verification (email and SMS), and password reset.
- JWT-based authentication with refresh token rotation and protected API by default.
- User profile management with media uploads, cover/profile images, and geolocation support.
- Pet profile management with pet details, medical/vaccination metadata, and media processing.
- Social graph features: friend requests, accept/reject flow, friend list, user blocking, and friend suggestions.
- Post module with privacy controls, media attachments, hashtags, mentions, reactions, comments, replies, and saved posts.
- Story module with expiring stories, views, reactions, and replies.
- Blog module with categories, publishing workflow, comments, likes, and view tracking.
- Real-time messaging with WebSocket support, direct/group threads, message lifecycle actions, and read updates.
- Meeting request and feedback system for user-to-user scheduling and post-meeting rating.
- Notification center with per-user settings, FCM device registration, and push/email notification pipeline.
- Referral wallet and transaction ledger with configurable referral settings and redemption flow.
- Reporting and moderation workflow for user-generated content and activity.
- Async processing via Celery for OTP dispatch, media processing, notification fan-out, and background tasks.
- Interactive API schema and documentation powered by drf-spectacular.
- Dockerized deployment stack with PostgreSQL (PostGIS), Redis, Gunicorn/Uvicorn, Celery, and Nginx.

## Tech Stack
[![Django](https://img.shields.io/badge/Django-5.x-092E20?style=flat&logo=django&logoColor=white)](https://www.djangoproject.com/)
[![Django REST Framework](https://img.shields.io/badge/DRF-3.x-000000?style=flat&logo=django&logoColor=white)](https://www.django-rest-framework.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-316192?style=flat&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![PostGIS](https://img.shields.io/badge/PostGIS-Enabled-4EA94B?style=flat)](https://postgis.net/)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?style=flat&logo=redis&logoColor=white)](https://redis.io/)
[![Celery](https://img.shields.io/badge/Celery-Task%20Queue-37814A?style=flat&logo=celery&logoColor=white)](https://docs.celeryq.dev/)
[![Docker](https://img.shields.io/badge/Docker-Containerized-2496ED?style=flat&logo=docker&logoColor=white)](https://www.docker.com/)
[![JWT](https://img.shields.io/badge/Auth-JWT-blue?style=flat)](https://django-rest-framework-simplejwt.readthedocs.io/)
[![WebSockets](https://img.shields.io/badge/Realtime-Channels-orange?style=flat)](https://channels.readthedocs.io/)

- Django (>=5.1,<6.1)
- Django REST Framework (>=3.15,<4.0)
- Database: PostgreSQL + PostGIS
- Authentication: JWT (SimpleJWT) + Firebase login support
- API Docs: drf-spectacular (OpenAPI, Swagger UI, Redoc)
- Realtime: Django Channels + channels-redis
- Queue/Workers: Celery + Redis
- Media/Image Handling: Pillow, django-cleanup
- Deployment/Runtime: Gunicorn + Uvicorn worker, Nginx, Docker Compose
- Monitoring: Sentry SDK
- Messaging/OTP Integrations: Twilio, SMTP email backend
- Storage/Cloud Integrations: boto3, django-storages, django-ses

## Prerequisites
- Python 3.11+ (Docker image currently uses Python 3.14 slim)
- PostgreSQL with PostGIS extension
- Redis
- GDAL/PROJ system dependencies (for GIS support)
- pip
- virtualenv (recommended)
- Docker and Docker Compose (for containerized setup)

## Installation and Setup
```bash
# 1) Clone the repository
git clone <your-repository-url>
cd PetNabor

# 2) Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate

# 3) Install dependencies
pip install --upgrade pip
pip install -r requirements/local.txt

# 4) Configure environment variables
cp .env.example .env

# 5) Run migrations
python manage.py migrate

# 6) Create a superuser (optional)
python manage.py createsuperuser
```

## Environment Variables
| Variable | Description |
| --- | --- |
| `DEBUG` | Django debug mode (`True` or `False`). |
| `SECRET_KEY` | Django secret key. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hosts. |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated trusted CSRF origins. |
| `POSTGRES_DB` | PostgreSQL database name. |
| `POSTGRES_USER` | PostgreSQL username. |
| `POSTGRES_PASSWORD` | PostgreSQL password. |
| `POSTGRES_HOST` | PostgreSQL host (for Docker: `db`). |
| `POSTGRES_PORT` | PostgreSQL port (default `5432`). |
| `REDIS_URL` | Redis connection URL for cache/channels. |
| `TWILIO_ACCOUNT_SID` | Twilio account SID for SMS OTP. |
| `TWILIO_AUTH_TOKEN` | Twilio auth token. |
| `TWILIO_PHONE_NUMBER` | Twilio sending number. |
| `EMAIL_HOST` | SMTP host. |
| `EMAIL_PORT` | SMTP port. |
| `EMAIL_USE_TLS` | Enable TLS for SMTP (`True`/`False`). |
| `EMAIL_HOST_USER` | SMTP username/email. |
| `EMAIL_HOST_PASSWORD` | SMTP password/app password. |
| `OTP_LENGTH` | Number of digits for generated OTP. |
| `OTP_EXPIRY_MINUTES` | OTP expiration window in minutes. |
| `OTP_MAX_ATTEMPTS` | Maximum OTP verification attempts. |
| `EMAIL_VERIFICATION_EXPIRY_HOURS` | Email verification token expiry. |
| `CORS_ALLOWED_ORIGINS` | Comma-separated allowed CORS origins. |
| `STORY_EXPIRY_HOURS` | Story expiration duration in hours. |
| `POST_MEDIA_MAX_SIZE_MB` | Maximum upload size for post media. |
| `POST_IMAGE_QUALITY` | Output quality for processed post images. |
| `POST_THUMB_QUALITY` | Output quality for generated thumbnails. |
| `FILE_UPLOAD_MAX_MEMORY_SIZE_MB` | Max in-memory file upload size. |
| `DATA_UPLOAD_MAX_MEMORY_SIZE_MB` | Max request payload size. |


## Running the Project
```bash
# Local development server
python manage.py runserver
```

```bash
# Start Celery worker (local)
celery -A config worker -l info
```

```bash
# Docker (build and run full stack)
./docker-up.sh

# Or manually
docker compose up --build -d
```

```bash
# Production-style app process (as used in docker-compose)
gunicorn config.asgi:application -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 --workers 4
```

## Project Structure
```text
PetNabor/
├── api/
│   ├── users/           # authentication, profile, OTP, account flows
│   ├── pet/             # pet profiles and media
│   ├── friends/         # friend requests, friendships, blocks
│   ├── post/            # posts, media, reactions, comments, saved posts
│   ├── story/           # stories, views, reactions, replies
│   ├── blog/            # blogs, categories, comments, likes, view tracking
│   ├── messaging/       # threads, messages, websocket consumer
│   ├── meeting/         # meeting requests and feedback
│   ├── notifications/   # notifications, device tokens, settings
│   ├── referral/        # referral wallet, rewards, transactions
│   └── report/          # moderation/reporting
├── config/              # Django settings, ASGI/WSGI, celery, root URLs
├── requirements/        # dependency groups (base/local)
├── nginx/               # nginx configuration
├── templates/           # Django templates/admin overrides
├── media/               # uploaded media (runtime)
├── manage.py
├── docker-compose.yml
├── Dockerfile
└── entrypoint.sh
```

## Running Tests and Migrations
```bash
# Apply migrations
python manage.py migrate

# Create migration files
python manage.py makemigrations

# Run Django test suite
python manage.py test
```

## Deployment
- Use Docker Compose for single-host deployments with Nginx + Gunicorn/Uvicorn + Celery + Redis + PostGIS.
- Set `DEBUG=False` and provide secure production values for `SECRET_KEY`, hosts, CORS, CSRF, and database credentials.
- Configure persistent volumes for media, static files, and PostgreSQL data.
- Route background processing through the Celery worker service.
- Recommended targets include AWS EC2/ECS, Railway, Render, or any container-compatible platform.
- Place a reverse proxy/load balancer in front of the application and terminate TLS at the edge.


## License
All Rights Reserved - View Only.

```text
Copyright (c) 2026 PetNabor. All rights reserved.

You are allowed to view this project and its source code for reference purposes only.

You are NOT allowed to:
1. Use this project for personal use.
2. Use this project for commercial use.
3. Copy, modify, redistribute, sublicense, or publish any part of this project.
4. Create derivative works based on this project.

No license is granted for use, distribution, or modification.
```

## Contact
Built with care by Roni Ahamed.

My mission is simple: build practical products that solve real problems and create positive impact.

Thanks for visiting this project.

If this work helped you, inspired you, I would truly love to hear from you.
Questions, feedback, or collaboration ideas are always welcome.

<p align="center">
	<a href="https://github.com/roniahamed">
		<img src="https://img.shields.io/badge/GitHub-roniahamed-181717?style=flat&logo=github&logoColor=white" alt="GitHub" />
	</a>
	<a href="mailto:mdroniahamed56@gmail.com">
		<img src="https://img.shields.io/badge/Email-mdroniahamed56%40gmail.com-D14836?style=flat&logo=gmail&logoColor=white" alt="Email" />
	</a>
	<a href="https://www.roniahamed.com">
		<img src="https://img.shields.io/badge/Portfolio-roniahamed.com-0A66C2?style=flat&logo=google-chrome&logoColor=white" alt="Portfolio" />
	</a>
	<a href="https://www.linkedin.com/in/roniahamed/">
		<img src="https://img.shields.io/badge/LinkedIn-roniahamed-0A66C2?style=flat&logo=linkedin&logoColor=white" alt="LinkedIn" />
	</a>
</p>

If you found this project useful, please consider giving the repository a star. Your support means a lot.


---
