"""
Fill the app with realistic demo activity: pet parents and PetPals with
photos, their pets, posts with real pet pictures, likes, comments,
friendships and a few stories — all placed around a location you choose so
they show up on the map, in "PetPals near you" and in the feed.

    python manage.py seed_demo_content --lat 40.7128 --lng -74.0060 --city "New York" --state NY
    python manage.py seed_demo_content --befriend you@example.com,teammate@example.com
        # also friends with these accounts (comma-separated or repeat the flag);
        # on an existing demo dataset this only adds the friendships
    python manage.py seed_demo_content --reset                      # remove all demo content first

Demo accounts use emails ending in @demo.petnabor.app and have no usable
password, so nobody can sign in as them. Photos come from public pet/portrait
APIs (dog.ceo, thecatapi.com, randomuser.me); use --no-images to skip
downloads (pets/posts get no photos then). Stories expire after 24h — run
again with --stories-only to refresh them.
"""

import json
import math
import random
import urllib.request
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

User = get_user_model()

DEMO_DOMAIN = "demo.petnabor.app"
UA = {"User-Agent": "PetNabor-demo-seeder/1.0"}

# ── People ───────────────────────────────────────────────────────────────────
# (first, last, role, gender for the portrait, portrait #, bio, pets)
# Portrait numbers are randomuser.me photos picked by hand so the demo
# community looks like a real US neighborhood (Black, Latino, East and South
# Asian, white). Check the photo when changing a number: the number says
# nothing about who is in it.
# pets: (name, type, breed, image key, gender, birthday, size, weight kg, temperament)
PEOPLE = [
    ("Amara", "Okafor", "petparent", "women", 16,
     "Golden retriever mom 🐾 Weekend hiker, coffee addict, always up for a park playdate.",
     [("Bailey", "Dog", "Golden Retriever", "dog:retriever/golden", "female", date(2021, 4, 12), "Large", 29,
       ["friendly_with_dogs", "good_with_kids", "playful", "house_trained"])]),
    ("Jamal", "Carter", "petpal", "men", 80,
     "Certified pet sitter • 6 years experience • dog walks, drop-ins & overnight stays. Pet first aid trained.",
     [("Rocky", "Dog", "German Shepherd", "dog:germanshepherd", "male", date(2019, 9, 3), "Large", 34,
       ["leash_trained", "calm", "house_trained"])]),
    ("Olivia", "Martinez", "petparent", "women", 47,
     "Cat lady with a beagle problem 😹🐶 Love meeting new pet pals in the neighborhood.",
     [("Luna", "Cat", "Maine Coon", "cat:mcoo", "female", date(2020, 2, 18), "Large", 7,
       ["calm", "friendly_with_dogs", "house_trained"]),
      ("Milo", "Dog", "Beagle", "dog:beagle", "male", date(2022, 6, 1), "Medium", 11,
       ["playful", "energetic", "friendly_with_cats"])]),
    ("Noah", "Kim", "petpal", "men", 90,
     "Student & part-time pet sitter. Cats, small dogs and bunnies welcome. Flexible evenings & weekends.",
     []),
    ("Ava", "Thompson", "petparent", "women", 36,
     "Corgi parent. Biscuit runs this household. Looking for corgi meetups! 🍑",
     [("Biscuit", "Dog", "Pembroke Welsh Corgi", "dog:pembroke", "male", date(2022, 11, 20), "Small", 12,
       ["playful", "friendly_with_dogs", "good_with_kids"])]),
    ("Marcus", "Brooks", "petparent", "men", 54,
     "Trail runner with a very talkative husky. Early-morning park crew ☀️",
     [("Koda", "Dog", "Siberian Husky", "dog:husky", "male", date(2020, 12, 8), "Large", 24,
       ["energetic", "friendly_with_dogs", "leash_trained"])]),
    ("Sophia", "Patel", "petpal", "women", 99,
     "Vet tech by day, pet sitter by heart 💛 Medication-friendly care for seniors and special-needs pets.",
     [("Chai", "Cat", "Siamese", "cat:siam", "female", date(2018, 5, 25), "Medium", 4,
       ["calm", "shy", "house_trained"])]),
    ("Mateo", "Rivera", "petparent", "men", 86,
     "First-time cat dad. Mochi is basically a tiny loaf of bread.",
     [("Mochi", "Cat", "Scottish Fold", "cat:sfol", "male", date(2023, 3, 14), "Small", 4,
       ["playful", "shy", "house_trained"])]),
    ("Isabella", "Chen", "petparent", "women", 2,
     "Poodle mom & groomer in training ✂️ Ask me anything about doodles and poodles!",
     [("Daisy", "Dog", "Standard Poodle", "dog:poodle/standard", "female", date(2021, 8, 30), "Large", 22,
       ["good_with_kids", "calm", "leash_trained", "house_trained"])]),
    ("Lucas", "Bennett", "petpal", "men", 46,
     "Dog walker in the neighborhood — group walks every weekday. Big dogs are my favorite.",
     [("Bear", "Dog", "Labrador Retriever", "dog:labrador", "male", date(2018, 1, 9), "Large", 33,
       ["friendly_with_dogs", "good_with_kids", "calm"])]),
    ("Mia", "Robinson", "petparent", "women", 44,
     "Two pets, zero chill. Cooper chases squirrels, Pepper judges everyone.",
     [("Cooper", "Dog", "Labrador Retriever", "dog:labrador", "male", date(2022, 2, 2), "Large", 30,
       ["energetic", "playful", "good_with_kids"]),
      ("Pepper", "Cat", "Tabby", "cat:", "female", date(2019, 10, 10), "Medium", 5,
       ["calm", "house_trained"])]),
    ("Arjun", "Mehta", "petparent", "men", 58,
     "Pug enthusiast. Waffles snores louder than me. Always down for a slow walk 🐢",
     [("Waffles", "Dog", "Pug", "dog:pug", "male", date(2020, 7, 4), "Small", 8,
       ["calm", "friendly_with_dogs", "good_with_kids"])]),
    ("Chloe", "Nguyen", "petparent", "women", 17,
     "Shiba owner = professional negotiator. Love sunset walks by the river.",
     [("Kenji", "Dog", "Shiba Inu", "dog:shiba", "male", date(2021, 1, 22), "Medium", 10,
       ["energetic", "leash_trained"])]),
    ("Daniel", "Foster", "petpal", "men", 83,
     "Retired teacher, now full-time pet sitter. House sitting and long walks. References on request.",
     [("Ziggy", "Dog", "Dachshund", "dog:dachshund", "male", date(2017, 4, 16), "Small", 9,
       ["calm", "house_trained", "good_with_kids"])]),
]

POST_CAPTIONS = {
    "Dog": [
        "Morning walk with {pet} ☀️ Who else is out at the park today?",
        "{pet} found the biggest stick in the whole park and is VERY proud 🪵",
        "Post-bath zoomies are a real thing 🛁💨",
        "Someone learned a new trick this week! {pet} can finally 'shake' 🐾",
        "Lazy Sunday mode activated with {pet} 😴",
        "Any recommendations for a good groomer nearby? {pet} needs a fresh cut ✂️",
        "{pet}'s birthday today! 🎂 Puppuccino has been secured.",
        "Muddy paws, happy heart. Best hike yet with {pet} 🌲",
    ],
    "Cat": [
        "{pet} has claimed the laundry basket. Again. 🧺",
        "Window watching is a full-time job for {pet} 🐦",
        "Caught {pet} mid-yawn and I'm obsessed 😹",
        "If it fits, {pet} sits 📦",
        "{pet} judging my life choices at 7am ☕",
        "Sun puddle season 🌞 {pet} approves.",
    ],
}
SITTER_CAPTIONS = [
    "Great group walk this morning — 5 happy pups and zero escaped leashes 🐕‍🦺",
    "Have a few openings next week for drop-ins and overnight stays. Message me! 📅",
    "Reminder: keep your pets hydrated on warm days 💧 Bring water on walks!",
    "Pet sitting tip: leave a worn T-shirt with your pet when you travel — your scent helps them relax 🧡",
]
COMMENTS = [
    "So cute!! 😍", "Look at that face 🥹", "Need a playdate ASAP!", "Adorable 🐾",
    "This made my day 😂", "Haha same energy as mine", "Gorgeous coat!", "Bring them to the park Saturday?",
    "Too precious 💛", "What a good pup!", "Love this 🙌", "The ears!! 😭",
]
STORY_TEXTS = [
    ("Park meetup at 5pm? 🐶", "#3E77BC"),
    ("Who's up for a weekend hike? 🌲", "#2F9A72"),
    ("Booking open for next week 📅", "#D97706"),
    ("Happy Caturday 🐱", "#7C5CD6"),
]


def _fetch(url: str, timeout: int = 12):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def _fetch_json(url: str):
    raw = _fetch(url)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def pet_photo(key: str):
    """'dog:<breed path>' via dog.ceo, 'cat:<breed id>' via thecatapi."""
    kind, _, breed = key.partition(":")
    if kind == "dog":
        data = _fetch_json(f"https://dog.ceo/api/breed/{breed}/images/random")
        url = data.get("message") if isinstance(data, dict) else None
    else:
        query = f"?breed_ids={breed}" if breed else ""
        data = _fetch_json(f"https://api.thecatapi.com/v1/images/search{query}")
        url = data[0].get("url") if isinstance(data, list) and data else None
    return _fetch(url) if url else None


def _jpeg(raw: bytes):
    """Normalise to a reasonably sized JPEG (some sources send huge PNG/GIF)."""
    try:
        from PIL import Image

        img = Image.open(BytesIO(raw)).convert("RGB")
        img.thumbnail((1600, 1600))
        out = BytesIO()
        img.save(out, format="JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return None


def _near(lat: float, lng: float, radius_km: float) -> Point:
    r = radius_km * math.sqrt(random.random())
    theta = random.random() * 2 * math.pi
    dlat = (r * math.cos(theta)) / 111.0
    dlng = (r * math.sin(theta)) / (111.0 * math.cos(math.radians(lat)))
    return Point(lng + dlng, lat + dlat, srid=4326)


class Command(BaseCommand):
    help = "Create demo users, pets, posts, likes, comments, friendships and stories."

    def add_arguments(self, parser):
        parser.add_argument("--lat", type=float, default=40.7128, help="Center latitude (default: New York)")
        parser.add_argument("--lng", type=float, default=-74.0060, help="Center longitude")
        parser.add_argument("--radius-km", type=float, default=4.0)
        parser.add_argument("--city", default="New York")
        parser.add_argument("--state", default="NY")
        parser.add_argument(
            "--befriend",
            action="append",
            default=[],
            help="Email(s) of existing accounts to befriend all demo users. "
            "Comma-separated or repeat the flag.",
        )
        parser.add_argument("--reset", action="store_true", help="Delete existing demo content first")
        parser.add_argument("--no-images", action="store_true", help="Skip photo downloads")
        parser.add_argument("--stories-only", action="store_true", help="Only (re)create today's stories")
        parser.add_argument("--seed", type=int, default=42, help="Random seed (same data each run)")

    # ── helpers ──────────────────────────────────────────────────────────

    def _image(self, getter, name: str, use_images: bool):
        if not use_images:
            return None
        raw = getter()
        data = _jpeg(raw) if raw else None
        if not data:
            self.stdout.write(self.style.WARNING(f"  could not download image for {name}"))
            return None
        return ContentFile(data, name=f"{name}.jpg")

    def handle(self, *args, **o):
        random.seed(o["seed"])
        use_images = not o["no_images"]

        demo_users = User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}")
        if o["reset"]:
            count = demo_users.count()
            demo_users.delete()  # cascades; django-cleanup removes their files
            self.stdout.write(self.style.SUCCESS(f"Removed {count} demo user(s) and their content."))

        if o["stories_only"]:
            users = list(User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}"))
            if not users:
                raise CommandError("No demo users yet — run without --stories-only first.")
            self._stories(users, use_images)
            return

        befriend = [e.strip() for value in o["befriend"] for e in value.split(",") if e.strip()]

        existing = list(User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}"))
        if existing:
            if befriend:
                # Demo data is already there: just add the friendships.
                self._befriend(existing, befriend)
                return
            raise CommandError("Demo content already exists. Use --reset to recreate it.")

        users, pets_by_user = self._users_and_pets(o, use_images)
        self._friendships(users)
        self._befriend(users, befriend)
        posts = self._posts(users, pets_by_user, use_images)
        self._engagement(users, posts)
        self._stories(users, use_images)

        self.stdout.write(self.style.SUCCESS(
            f"Done: {len(users)} users, {sum(len(p) for p in pets_by_user.values())} pets, "
            f"{len(posts)} posts around ({o['lat']}, {o['lng']})."
        ))

    def _users_and_pets(self, o, use_images):
        from api.pet.models import PetProfile

        users, pets_by_user = [], {}
        now = timezone.now()
        for first, last, role, portrait_gender, portrait_n, bio, pets in PEOPLE:
            handle = f"{first}.{last}".lower()
            self.stdout.write(f"Creating {first} {last} ({role})…")
            with transaction.atomic():
                user = User.objects.create_user(
                    email=f"{handle}@{DEMO_DOMAIN}",
                    password=None,  # unusable: nobody can sign in as a demo user
                    username=f"demo_{first.lower()}_{last.lower()}",
                    first_name=first,
                    last_name=last,
                    user_type=role,
                    is_petpal=role == "petpal",
                    is_verified=True,
                    is_email_verified=True,
                    agree_to_terms_and_conditions=True,
                    last_active=now - timedelta(minutes=random.randint(1, 600)),
                )
                profile = user.profile
                profile.bio = bio
                profile.city = o["city"]
                profile.state = o["state"]
                profile.location_point = _near(o["lat"], o["lng"], o["radius_km"])
                profile.date_of_birth = date(random.randint(1975, 2001), random.randint(1, 12), random.randint(1, 28))
                photo = self._image(
                    lambda: _fetch(f"https://randomuser.me/api/portraits/{portrait_gender}/{portrait_n}.jpg"),
                    f"avatar_{handle}",
                    use_images,
                )
                if photo:
                    profile.profile_picture = photo
                profile.save()

                user_pets = []
                for name, kind, breed, img_key, gender, born, size, weight, temperament in pets:
                    pet = PetProfile(
                        user=user,
                        pet_name=name,
                        pet_type=kind,
                        breed=breed,
                        gender=gender,
                        is_spayed_neutered=random.choice([True, True, False]),
                        temperament=temperament,
                        size=size,
                        weight=Decimal(str(weight)),
                        date_of_birth=born,
                        vaccination_status="Up to date",
                    )
                    image = self._image(lambda: pet_photo(img_key), f"pet_{handle}_{name.lower()}", use_images)
                    if image:
                        pet.image = image
                    pet.save()
                    user_pets.append((pet, img_key))
            users.append(user)
            pets_by_user[user.id] = user_pets
        return users, pets_by_user

    def _friendships(self, users):
        from api.friends.models import Friendship

        # A loose neighborhood network: everyone knows a few people.
        for i, user in enumerate(users):
            for other in random.sample(users[i + 1:], k=min(4, len(users[i + 1:]))):
                Friendship.objects.get_or_create(sender=user, receiver=other)

    def _befriend(self, users, emails):
        """Make every demo user a friend of each real account in `emails`."""
        from django.db.models import Q

        from api.friends.models import Friendship
        from api.messaging.services import invalidate_messaging_permission

        for email in emails:
            me = User.objects.filter(email__iexact=email).first()
            if not me:
                self.stdout.write(self.style.WARNING(f"No account with email {email}; skipped."))
                continue
            added = 0
            for user in users:
                # A friendship is one row in either direction.
                if not Friendship.objects.filter(
                    Q(sender=user, receiver=me) | Q(sender=me, receiver=user)
                ).exists():
                    Friendship.objects.create(sender=user, receiver=me)
                    invalidate_messaging_permission(user.id, me.id)
                    added += 1
            self.stdout.write(self.style.SUCCESS(
                f"{email}: {added} new demo friend(s) ({len(users)} total)."
            ))

    def _posts(self, users, pets_by_user, use_images):
        from api.post.models import MediaTypeChoices, Post, PostMedia
        from api.post.tasks import process_post_media_task

        posts, media_ids = [], []
        now = timezone.now()
        for user in users:
            user_pets = pets_by_user[user.id]
            captions = []
            if user_pets:
                for pet, img_key in user_pets:
                    pool = random.sample(POST_CAPTIONS[pet.pet_type], k=2)
                    captions += [(c.format(pet=pet.pet_name), img_key, pet.pet_name) for c in pool]
            if user.user_type == "petpal":
                captions += [(c, "dog:retriever/golden", "walk") for c in random.sample(SITTER_CAPTIONS, k=2)]

            for text, img_key, label in captions:
                post = Post.objects.create(author=user, content_text=text, privacy="PUBLIC")
                with_photo = use_images and random.random() < 0.85
                image = (
                    self._image(lambda: pet_photo(img_key), f"post_{label.lower()}_{post.id.hex[:6]}", True)
                    if with_photo else None
                )
                if image:
                    media = PostMedia.objects.create(
                        post=post, media_type=MediaTypeChoices.IMAGE, file=image, order=0
                    )
                    media_ids.append(str(media.id))
                # Spread posts over the last ten days for a believable feed.
                Post.objects.filter(id=post.id).update(
                    created_at=now - timedelta(hours=random.randint(1, 240), minutes=random.randint(0, 59))
                )
                posts.append(post)

        if media_ids:
            # Medium/thumbnail variants, as for real uploads (needs the Celery worker).
            transaction.on_commit(lambda: process_post_media_task.delay(media_ids))
        return posts

    def _engagement(self, users, posts):
        from django.db.models import F

        from api.post.models import Post, PostComment, PostLike

        for post in posts:
            others = [u for u in users if u.id != post.author_id]
            likers = random.sample(others, k=random.randint(2, min(9, len(others))))
            PostLike.objects.bulk_create([PostLike(post=post, user=u) for u in likers], ignore_conflicts=True)
            commenters = random.sample(others, k=random.randint(0, 3))
            for u in commenters:
                PostComment.objects.create(post=post, user=u, comment_text=random.choice(COMMENTS))
            Post.objects.filter(id=post.id).update(
                likes_count=F("likes_count") + len(likers),
                comments_count=F("comments_count") + len(commenters),
            )

    def _stories(self, users, use_images):
        from api.story.models import Story

        now = timezone.now()
        expires = now + timedelta(hours=24)
        Story.objects.filter(author__in=users).delete()
        tellers = random.sample(users, k=min(6, len(users)))
        for i, user in enumerate(tellers):
            pets = list(user.pets.all())
            pet = pets[0] if pets else None
            image = None
            if pet and pet.image and use_images and i % 2 == 0:
                # Reuse the pet's photo: one download fewer.
                try:
                    pet.image.open("rb")
                    image = ContentFile(pet.image.read(), name=f"story_{pet.pet_name.lower()}.jpg")
                    pet.image.close()
                except Exception:
                    image = None
            if image:
                Story.objects.create(
                    author=user, media_type="IMAGE", media=image,
                    text_content=f"{pet.pet_name} says hi 👋", expires_at=expires,
                )
            else:
                text, color = STORY_TEXTS[i % len(STORY_TEXTS)]
                Story.objects.create(
                    author=user, media_type="TEXT", text_content=text, bg_color=color, expires_at=expires,
                )
        self.stdout.write(self.style.SUCCESS(f"Created {len(tellers)} stories (they expire in 24h)."))
