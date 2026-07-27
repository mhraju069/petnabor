# PetNabor Architectural Overview

## 1. Purpose

This document is the primary architectural reference for PetNabor. It explains how the system is structured, how the major components interact, which technologies are used, and how data moves through the platform.

For AWS production topology, database operations, Redis/CDN caching, load balancing, redundancy, and infrastructure security, read this document together with [Backend Infrastructure & Caching](./infrastructure.md).

## 2. Executive Summary

PetNabor is a pet-focused social, marketplace, messaging, and services platform. The backend is built as a Django modular monolith that exposes REST APIs, WebSocket chat, admin operations, background jobs, media processing, OTP flows, notifications, referrals, vendor/product features, reports, meetings, and Stripe-powered tipping.

The current codebase packages these capabilities into one deployable Django project. Each domain lives in a separate Django app with its own models, serializers, views, services, tasks, admin configuration, and tests where applicable. This provides strong domain separation without the operational cost of microservices.

The intended production environment is AWS-managed infrastructure:

| Capability | Production Service |
| --- | --- |
| Application runtime | Dockerized Django ASGI app behind a reverse proxy/load balancer |
| Database | AWS RDS PostgreSQL with PostGIS support |
| Object storage | AWS S3 |
| CDN | AWS CloudFront |
| DNS | Domain DNS provider or Amazon Route 53 |
| TLS | Wildcard SSL certificate |
| Push and social login | Firebase project for FCM, Google Login, Apple Login |
| Maps | Google Maps API or Mapbox |
| SMS OTP | Twilio |
| Email OTP | AWS SES or SMTP provider |
| Cache, queues, realtime coordination | Redis-compatible service, preferably AWS ElastiCache for production |

## 3. High-Level Architecture

### 3.1 Logical System Diagram

```text
Mobile App / Web Frontend / Admin Browser
        |
        | HTTPS, WebSocket, REST JSON, media upload/download
        v
DNS + Wildcard TLS
        |
        v
CloudFront CDN
  |-- Static assets
  |-- Public/media objects from S3
  |-- Optional API edge caching for safe GET endpoints
        |
        v
AWS Load Balancer or Edge Reverse Proxy
        |
        v
Nginx Reverse Proxy
  |-- Routes HTTP API and Django Admin traffic
  |-- Upgrades /ws/ WebSocket traffic
  |-- Applies upload limits and proxy timeouts
        |
        v
Django ASGI Application
  |-- Django REST Framework APIs
  |-- Django Admin
  |-- Django Channels WebSocket consumer
  |-- Domain service layer
        |
        |------------------|------------------|------------------|
        v                  v                  v                  v
AWS RDS PostgreSQL     Redis              Celery Workers     External Services
PostGIS-enabled        Cache, broker,     Async processing   Firebase, Twilio,
source of truth        channel layer      and fan-out        SES/SMTP, Stripe,
                                                            Maps provider
        |
        v
AWS S3 + CloudFront
Media and static asset delivery
```

### 3.2 Recommended Visual Diagram

Create a formal architecture diagram with the following swimlanes:

| Swimlane | Nodes |
| --- | --- |
| Client | iOS/Android app, web frontend, admin browser |
| Edge | DNS, wildcard TLS, CloudFront |
| Application | load balancer, Nginx, Django ASGI web containers, Celery workers |
| Data | RDS PostgreSQL/PostGIS, Redis, S3 |
| External integrations | Firebase, Twilio, AWS SES/SMTP, Stripe, Google Maps/Mapbox |
| Operations | logging, Sentry, backups, monitoring, deployment pipeline |

Use solid arrows for synchronous request/response traffic and dashed arrows for asynchronous jobs, webhooks, and notification fan-out.

## 4. Architectural Style

PetNabor uses a modular monolith.

| Design Choice | Rationale | Trade-off |
| --- | --- | --- |
| Single Django project | Simple deployment, shared auth/session model, easier local development | A large codebase requires clear app boundaries and review discipline |
| Domain-oriented Django apps | Keeps business logic discoverable by feature | Cross-app dependencies must stay intentional |
| DRF for API surface | Mature serializer, permission, throttling, pagination, and schema ecosystem | API performance depends on disciplined queryset optimization |
| ASGI runtime | Supports HTTP and WebSocket from one deployment unit | Requires ASGI-compatible process management and WebSocket-aware proxy config |
| Redis for cache, Celery, Channels | One fast coordination layer for multiple runtime needs | Production should separate logical Redis workloads as traffic grows |
| PostgreSQL/PostGIS | Strong relational integrity plus geospatial capability | Requires index and query management for high-volume feeds and location queries |
| Celery workers | Slow, retryable, or fan-out work moves outside request latency | Worker queues must be monitored and scaled independently |

## 5. Core Components

### 5.1 Client Applications

Client applications consume REST APIs under `/api/`, open WebSocket connections for chat under `/ws/`, upload media, display CDN-hosted assets, receive Firebase push notifications, and initiate third-party login or OTP verification flows.

Client responsibilities:

| Area | Responsibility |
| --- | --- |
| Auth | Store and refresh JWT tokens, send Firebase ID token for social login |
| Media | Upload images/videos; render CloudFront/S3 media URLs |
| Messaging | Maintain WebSocket connection and recover from reconnects |
| Maps | Render location UX using Google Maps API or Mapbox |
| Notifications | Register FCM device token with backend |

### 5.2 Edge and Routing Layer

The edge layer provides DNS, HTTPS, CDN delivery, and request routing. In AWS production, CloudFront should serve static/media assets and may sit in front of the API domain where appropriate. A load balancer or reverse proxy forwards application traffic to the Django runtime.

The repository currently includes Nginx configuration for:

| Function | Current Implementation |
| --- | --- |
| HTTP reverse proxy | `nginx/nginx.conf` proxies to the `web` container |
| WebSocket upgrades | `/ws/` location passes `Upgrade` and `Connection` headers |
| Large uploads | `client_max_body_size 5000M` and long proxy timeouts |
| Local static/media serving | `/static/` and `/media/` aliases for Docker volumes |
| Host-level TLS | `nginx/app.petnabor.com.host.conf` documents HTTPS proxying |

In AWS production, local media serving should be replaced by S3-backed storage and CloudFront delivery. See [Backend Infrastructure & Caching](./infrastructure.md#7-object-storage-cdn-and-media-delivery).

### 5.3 Django ASGI Application

The main application runs through ASGI:

| File | Role |
| --- | --- |
| `config/asgi.py` | ASGI entrypoint for HTTP and WebSocket routing |
| `config/wsgi.py` | WSGI compatibility entrypoint |
| `config/urls.py` | Root URL router and API docs paths |
| `api/urls.py` | Domain API router |
| `config/settings.py` | Runtime configuration, installed apps, database, Redis, auth, throttling |

Production command in Docker Compose:

```bash
gunicorn config.asgi:application -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 --workers 4
```

### 5.4 Domain Apps

| App | Responsibility |
| --- | --- |
| `api.users` | Custom user model, signup, login, JWT, Firebase login, OTP verification, password reset, profile media, user activity |
| `api.pet` | Pet profiles and pet media processing |
| `api.friends` | Friend requests, friendships, blocking, public profiles, suggestions |
| `api.post` | Social posts, post media, privacy, hashtags, likes, comments, replies, saved posts |
| `api.story` | 24-hour stories, views, reactions, replies, story media |
| `api.blog` | Blog categories, blog posts, likes, comments, views, blog media |
| `api.messaging` | Chat threads, participants, messages, WebSocket chat, read/archive/delete flows |
| `api.notifications` | Notification settings, FCM devices, in-app notifications, push delivery |
| `api.report` | User/content reporting and moderation workflow |
| `api.meeting` | Meeting requests and feedback |
| `api.referral` | Referral settings, wallets, transaction ledger, reward and redemption flows |
| `api.vendor` | Vendor profiles, plans, and subscriptions |
| `api.product` | Categories, brands, products, product media, product events |
| `api.wishlist` | Product wishlist records |
| `api.tip` | Stripe Connect onboarding, tips, held transfers, withdrawals, webhooks |
| `api.site_settings` | Global site and admin configuration |

### 5.5 Database

PostgreSQL is the durable source of truth. The app uses the PostGIS backend through `django.contrib.gis.db.backends.postgis`, enabling geolocation fields and location-aware features.

Primary data groups:

| Data Group | Representative Models |
| --- | --- |
| Identity | `User`, `Profile`, `OTPVerification` |
| Pets | `PetProfile` |
| Social graph | `FriendRequest`, `Friendship`, `UserBlock` |
| Content | `Post`, `PostMedia`, `PostLike`, `PostComment`, `SavedPost`, `Hashtag` |
| Stories | `Story`, `StoryView`, `StoryReaction`, `StoryReply` |
| Blogs | `BlogCategory`, `Blog`, `BlogLike`, `BlogComment`, `BlogViewTracker` |
| Messaging | `ChatThread`, `ThreadParticipant`, `Message` |
| Notifications | `NotificationSettings`, `FCMDevice`, `Notifications` |
| Referrals | `ReferralSettings`, `ReferralWallet`, `ReferralTransaction` |
| Vendor/store | `VendorPlan`, `Vendor`, `VendorSubscription`, `Categories`, `Brand`, `Product`, `ProductMedia`, `ProductEvent`, `ProductWishlist` |
| Tips/payments | `TipSettings`, `StripeConnectAccount`, `Tip`, `TipWithdrawal` |
| Moderation | `Report` |
| Site settings | `SiteSettings` |

### 5.6 Redis

Redis is used for three runtime concerns:

| Concern | Usage |
| --- | --- |
| Django cache | Short-lived application cache using `django.core.cache.backends.redis.RedisCache` |
| Celery broker/result backend | Queueing and task result coordination |
| Channels layer | WebSocket group messaging between ASGI workers |

Production should use a managed Redis-compatible service and separate workloads by database index, cluster, or instance as traffic increases.

### 5.7 Celery Workers

Celery executes asynchronous work such as OTP delivery, media processing, notification fan-out, referral reward jobs, and messaging push notifications. This protects API latency from slow third-party calls and CPU-heavy media work.

Main async workloads:

| App | Background Work |
| --- | --- |
| `api.users` | Email/SMS OTP, profile media processing |
| `api.pet` | Pet image processing |
| `api.post` | Post and comment media processing |
| `api.story` | Story media processing |
| `api.blog` | Blog view tracking and media processing |
| `api.messaging` | New message push notification tasks |
| `api.notifications` | Push fan-out and notification persistence |
| `api.referral` | Referral points awarding |

### 5.8 Admin and Operations

Django Admin, styled with `django-unfold`, is the primary internal operations surface. It exposes users, profiles, pets, posts, stories, blogs, messaging, reports, notifications, referrals, vendors, products, wishlists, tips, withdrawals, and site settings.

## 6. Technology Stack

| Layer | Technology | Purpose |
| --- | --- | --- |
| Backend framework | Django 5.x | Main application framework |
| API framework | Django REST Framework | REST views, serializers, permissions, throttling, pagination |
| API documentation | drf-spectacular | OpenAPI schema, Swagger UI, Redoc |
| Runtime | ASGI | HTTP and WebSocket support |
| App server | Gunicorn + Uvicorn worker | Production process model |
| Realtime | Django Channels + channels-redis | WebSocket chat delivery |
| Database | PostgreSQL + PostGIS | Relational and geospatial data |
| Cache/queue/realtime | Redis | Cache, Celery broker/result backend, Channels layer |
| Background jobs | Celery | Async processing and external-service retries |
| Authentication | SimpleJWT + Firebase Admin | JWT auth and Firebase ID token verification |
| Admin UI | Django Admin + django-unfold | Operations dashboard |
| Push | Firebase Cloud Messaging | Device push notifications |
| SMS | Twilio | Phone OTP |
| Email | AWS SES or SMTP | Email OTP and transactional email |
| Object storage | AWS S3 | Static and uploaded media in production |
| CDN | AWS CloudFront | Global media/static delivery |
| Maps | Google Maps API or Mapbox | Location/map features |
| Payments | Stripe / Stripe Connect | Tips, onboarding, payouts, webhooks |
| Error reporting | Sentry SDK | Django, Celery, Redis error/tracing integrations |
| Deployment | Docker / Docker Compose today; AWS runtime target | Containerized deployment |

## 7. API Surface

The API root is mounted at `/api/`.

| Base Path | Capability |
| --- | --- |
| `/api/users/` | Signup, login, OTP, Firebase login, token refresh, user/profile |
| `/api/notifications/` | Notifications, settings, FCM device records |
| `/api/pets/` | Pet profile APIs |
| `/api/friends/` | Friend requests, user search, suggestions, block/unfriend |
| `/api/messaging/` | Inbox, threads, messages, read/delete/archive flows |
| `/api/post/` | Posts, comments, saved posts, user posts |
| `/api/report/` | Reports and moderation submissions |
| `/api/story/` | Stories and story actions |
| `/api/blog/` | Blogs and blog comments |
| `/api/vendor/` | Vendor and subscription APIs |
| `/api/referral/` | Referral dashboard, transactions, verification, redemption |
| `/api/settings/` | Global settings |
| `/api/tip/` | Stripe Connect, send tip, history, balance, withdraw, webhook |
| `/api/meetings/` | Meeting requests and feedback |

API documentation:

| Path | Purpose |
| --- | --- |
| `/api/schema/` | OpenAPI schema |
| `/api/swagger/` | Swagger UI |
| `/api/docs/` | Redoc |

Current routing note: `api.product` and `api.wishlist` are installed and represented in models/admin, but dedicated public product and wishlist routes are not currently mounted in `api/urls.py`.

## 8. Authentication and Authorization

PetNabor uses a custom Django user model:

```python
AUTH_USER_MODEL = "users.User"
```

Authentication flows:

| Flow | Description |
| --- | --- |
| Email signup | Creates account and sends email OTP |
| Phone signup/login | Uses Twilio-backed OTP verification |
| Email/phone login | Issues JWT access and refresh tokens |
| Firebase login | Verifies Firebase ID token for Google/Apple-supported login |
| Password reset | OTP-based reset workflow |
| Token refresh | SimpleJWT refresh endpoint |

Authorization is enforced through DRF permissions, custom permissions, object-level checks, middleware, and domain services. REST APIs use JWT authentication by default. WebSocket chat uses a custom JWT middleware that authenticates access tokens passed to:

```text
/ws/chat/?token=<access_token>
```

Security infrastructure details are expanded in [Backend Infrastructure & Caching](./infrastructure.md#12-security-and-authentication-infrastructure).

## 9. Data Flow

### 9.1 REST Request Flow

```text
Client
  -> CloudFront / DNS / TLS
  -> Load balancer or reverse proxy
  -> Nginx
  -> Gunicorn/Uvicorn ASGI worker
  -> Django middleware
  -> DRF view or viewset
  -> Serializer/service layer
  -> PostgreSQL, Redis, S3, or external service
  -> JSON response
```

### 9.2 WebSocket Chat Flow

```text
Client
  -> /ws/chat/?token=<JWT>
  -> WebSocket-aware proxy
  -> Django Channels JWT middleware
  -> ChatConsumer
  -> PostgreSQL read/write
  -> Redis channel layer broadcast
  -> Recipient WebSocket clients
  -> Celery task for push notification when needed
```

### 9.3 Media Upload Flow

Current implementation:

```text
Client upload
  -> Nginx
  -> Django
  -> local Docker media volume
  -> optional image compression or video thumbnail generation
```

Recommended AWS production flow:

```text
Client upload
  -> Django API authorization and validation
  -> S3 object write or pre-signed direct upload
  -> Celery media processing
  -> processed object stored in S3
  -> CloudFront URL returned to clients
```

### 9.4 Notification Flow

```text
Domain event
  -> notification service or Celery task
  -> user notification preferences checked
  -> notification row persisted when needed
  -> Firebase Cloud Messaging delivery
  -> mobile device receives push
```

### 9.5 OTP Flow

```text
Signup/login/password reset request
  -> throttle check
  -> OTP row created
  -> Celery task
  -> Twilio for phone OTP or AWS SES/SMTP for email OTP
  -> user submits OTP
  -> backend verifies code, expiry, and attempt limit
  -> account state or token response updated
```

### 9.6 Tip Payment Flow

```text
Tip request
  -> backend validates sender, receiver, amount, settings
  -> Stripe PaymentIntent / Connect operation
  -> Tip row persisted
  -> Stripe webhook confirms payment or payout event
  -> backend updates tip/withdrawal state
  -> optional notification fan-out
```

## 10. Integration Points

| Integration | Purpose | Backend Touchpoints |
| --- | --- | --- |
| Firebase Admin SDK | FCM push, Firebase ID token verification for Google/Apple login flows | `api.notifications`, `api.users`, `firebase-credentials.json` |
| Twilio | Phone OTP delivery | `api.users.tasks`, Twilio env vars |
| AWS SES / SMTP | Email OTP and transactional email | Django email backend, `api.notifications.email_service` |
| AWS S3 | Production object storage | Media/static storage target |
| AWS CloudFront | CDN for media/static assets | Public asset URL generation and edge caching |
| AWS RDS PostgreSQL | Production database | Django ORM/PostGIS |
| Google Maps API or Mapbox | Maps and location UX | Client integration; backend stores location data where needed |
| Stripe / Stripe Connect | Tips, onboarding, withdrawals, webhooks | `api.tip.services`, `api.tip.views` |
| Sentry | Error reporting and tracing | `config/settings.py` |

## 11. Deployment Architecture

The repository currently ships with a Docker Compose topology:

| Service | Responsibility |
| --- | --- |
| `db` | PostGIS-enabled PostgreSQL container for local/single-host deployment |
| `redis` | Redis for cache, Celery, and Channels |
| `web` | Django ASGI app via Gunicorn/Uvicorn |
| `celery` | Background worker |
| `nginx` | Reverse proxy and local static/media serving |

Entrypoint responsibilities:

1. Create temporary upload directory.
2. Run `collectstatic`.
3. Run database migrations.
4. Start the requested process.

AWS production should keep the same application container model but replace single-host stateful services with managed services:

| Current | AWS Production Target |
| --- | --- |
| Docker PostgreSQL | AWS RDS PostgreSQL with PostGIS |
| Docker Redis | AWS ElastiCache Redis or equivalent managed Redis |
| Local media volume | AWS S3 bucket |
| Local `/static/` serving | S3 + CloudFront or collectstatic to S3 |
| Host Nginx TLS | CloudFront/ALB TLS using wildcard certificate |
| Manual host deployment | CI/CD-driven image build and rollout |

See [Backend Infrastructure & Caching](./infrastructure.md#3-production-aws-topology) for the detailed topology.

## 12. Scalability and Performance

PetNabor can scale horizontally while remaining a modular monolith.

| Area | Scaling Approach |
| --- | --- |
| REST APIs | Add more ASGI web containers behind a load balancer |
| WebSockets | Add more ASGI containers using Redis Channels as shared coordination |
| Background jobs | Add Celery workers; split queues by workload such as media, notifications, OTP |
| Database | Tune indexes and queries; use RDS storage/compute scaling and read replicas for read-heavy workloads |
| Media | Move uploads and static files to S3; serve through CloudFront |
| Cache | Use Redis for feed fragments, permission checks, inbox pages, OTP/rate-limit state, and idempotency keys |
| External services | Use Celery retries, timeouts, and idempotency for Twilio, Firebase, Stripe, and email |
| Large uploads | Prefer direct-to-S3 uploads to avoid tying up web workers |

Performance-sensitive areas:

| Area | Concern | Mitigation |
| --- | --- | --- |
| Feed and listing APIs | N+1 queries and expensive counts | `select_related`, `prefetch_related`, cursor pagination, targeted indexes |
| Messaging inbox | Frequent reads and unread counts | Short-lived Redis cache and invalidation on message writes |
| Product listings | Filtering by vendor/category/brand/active state | Indexed fields and cursor pagination |
| Media processing | CPU and disk pressure | Celery offload, S3 storage, separate worker pool |
| Notifications | Fan-out latency | Celery batching, FCM retries, idempotency keys |
| OTP | Abuse and provider cost | Identity-based throttles, expiry, max attempts |

## 13. Reliability and Failure Modes

| Failure | Expected Behavior |
| --- | --- |
| External SMS/email provider outage | API should avoid blocking indefinitely; Celery retries and operational alerting should capture failures |
| Firebase push failure | Persist notification where required and retry or mark delivery failure |
| Redis outage | Cache miss behavior should degrade where possible; Celery and WebSocket delivery are directly impacted |
| Database failover | Application should reconnect; RDS backups and Multi-AZ reduce data-loss risk |
| S3/CloudFront issue | New media uploads/downloads impacted; existing API operations may continue |
| Stripe webhook delay | Tip state remains pending until verified webhook or reconciliation job updates it |

## 14. Current Implementation Notes

| Item | Current State |
| --- | --- |
| Database | Dockerized PostGIS for current Compose deployment; RDS is the intended AWS production target |
| Redis | Single Redis instance shared by cache, Celery, and Channels |
| Static/media | Local Docker volumes today; S3/CloudFront recommended for AWS production |
| TLS | Host Nginx config exists for `app.petnabor.com`; AWS should terminate TLS at CloudFront/ALB |
| Product API | Product models/admin exist, but product URLs are not mounted in the root API router |
| Wishlist API | Wishlist model/admin exists, but wishlist URLs are not mounted in the root API router |
| Health checks | No dedicated health endpoint is currently visible in the root URL configuration |
| Scheduled jobs | Celery worker exists; Celery Beat is not configured in Docker Compose |
| S3 settings | Dependencies are present, but `config/settings.py` currently uses local `MEDIA_ROOT` storage |

## 15. Future Evolution

Keep the modular monolith until operational data justifies extraction. Likely future extraction candidates:

| Candidate | Reason |
| --- | --- |
| Media processing | CPU-heavy, storage-heavy, and independently scalable |
| Notifications | High fan-out, external dependency latency, retry policy complexity |
| Messaging | Low-latency realtime traffic and potentially distinct scaling patterns |
| Payments/tips | Audit, compliance, and operational isolation |
| Search/recommendations | Specialized indexing and ranking requirements |

The near-term priority is not service extraction. The higher-value production work is moving stateful infrastructure to AWS-managed services, adding health checks, centralizing logs/metrics, configuring S3/CloudFront storage, and splitting Redis/Celery workloads as load increases.
