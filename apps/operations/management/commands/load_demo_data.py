"""Fill an EMPTY installation with invented data for training and screenshots.

Usage:
    python manage.py load_demo_data --username ADMIN [--people 90] [--days 21] [--seed 7]

Everything is made up: names, IDs, emails and stock. The command refuses to
run if any missionary, item or issue already exists, so it can never be
mixed into a real roster. Use a separate trial installation for demos, then
discard it (or use Protected reset) before loading real data.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

GIVEN_F = ["Aroha", "Ana", "Mele", "Grace", "Lucy", "Emma", "Leilani", "Sina", "Hannah", "Ruby",
           "Olivia", "Tia", "Mere", "Lose", "Esther", "Kalena", "Charlotte", "Amelia", "Isla", "Moana",
           "Sela", "Priya", "Mia", "Ivy", "Hine", "Talia", "Ella", "Lavinia", "Rose", "Faith"]
GIVEN_M = ["Sione", "Tevita", "Wiremu", "Jacob", "Liam", "Noah", "Samuela", "Ioane", "Ethan", "Lachlan",
           "Kahu", "Mosese", "Joseph", "Benjamin", "Tane", "Viliami", "Oliver", "James", "Isaac", "Nikolasi",
           "Ropata", "Lucas", "Mateo", "Hemi", "Daniel", "Paulo", "Aisea", "Caleb", "Nathan", "Elijah"]
FAMILY = ["Tuipulotu", "Fonoti", "Walker", "Lolohea", "Ngata", "Leota", "Robinson", "Vaipulu", "Harris", "Hufanga",
          "Parata", "Thompson", "Vea", "Tuilagi", "Pritchard", "Mahe", "Nikora", "Latu", "Bennett", "Sapolu",
          "Kingi", "Afamasaga", "Clarke", "Moala", "Reid", "Fakatava", "Tamihana", "Young", "Palelei", "Morgan",
          "Hēnare", "Toleafoa", "Wilson", "Pulu", "Tangitau", "Mitchell", "Faleolo", "Rāwiri", "Ward", "Havili"]
MISSIONS = ["New Zealand Auckland", "New Zealand Hamilton", "New Zealand Wellington",
            "New Zealand Christchurch", "Australia Sydney", "Australia Brisbane", "Australia Perth",
            "Australia Adelaide", "Papua New Guinea Port Moresby", "Fiji Suva", "Samoa Apia",
            "Tonga Nuku'alofa", "Vanuatu Port Vila"]
LANGUAGES = ["English", "English", "English", "English (ESL)", "Samoan", "Tongan", "Fijian"]
TOWNS = ["Hamilton", "Tauranga", "Apia", "Nuku'alofa", "Brisbane", "Mesa", "Auckland", "Suva",
         "Rotorua", "Provo", "Christchurch", "Lae"]

# (category, name, sizes, typical stock per size, alert level)
CATALOGUE = [
    ("Clothing", "White Shirts", ["38 S", "38 L", "40 S", "40 L", "42 S", "42 L", "44 L", "46 L"], 12, 4),
    ("Clothing", "Dress Pants", ["30", "32", "34", "36", "38", "40"], 6, 2),
    ("Clothing", "Belt", ["30", "32", "34", "36", "38", "40", "42"], 5, 2),
    ("Clothing", "Tie", [""], 25, 6),
    ("Clothing", "Dress", ["6", "8", "10", "12", "14", "16", "18", "20"], 4, 2),
    ("Clothing", "Skirt", ["8", "10", "12", "14", "16"], 4, 2),
    ("Clothing", "Socks", ["S", "M", "L"], 20, 6),
    ("Clothing", "Sports Tops", ["S", "M", "L", "XL", "2XL", "3XL"], 8, 3),
    ("Clothing", "Sports Bottoms", ["S", "M", "L", "XL", "2XL", "3XL"], 8, 3),
    ("Clothing", "Winter Jacket", ["S", "M", "L", "XL"], 3, 1),
    ("Shoes", "Dress Shoes (men)", ["7", "8", "9", "10", "11", "12"], 4, 2),
    ("Shoes", "Dress Shoes (women)", ["5", "6", "7", "8", "9"], 4, 2),
    ("Shoes", "Sports Shoes", ["6", "7", "8", "9", "10", "11"], 4, 2),
    ("Hygiene", "Soap", [""], 80, 15),
    ("Hygiene", "Shampoo & Conditioner", [""], 40, 10),
    ("Hygiene", "Toothpaste", [""], 60, 12),
    ("Hygiene", "Toothbrush", [""], 60, 12),
    ("Hygiene", "Deodorant", [""], 45, 10),
    ("Hygiene", "Razor", [""], 30, 8),
    ("Hygiene", "Feminine Hygiene", ["Regular", "Super"], 30, 8),
    ("Hygiene", "Lip Balm", [""], 25, 6),
    ("Medical", "Pain Relief Tablets", [""], 30, 8),
    ("Medical", "Plasters", [""], 40, 10),
    ("Medical", "Cold & Flu Tablets", [""], 20, 6),
    ("Medical", "Antiseptic Cream", [""], 15, 5),
    ("Medical", "Sunscreen", [""], 20, 6),
    ("Medical", "Knee Brace", ["S/M", "L/XL"], 3, 1),
    ("Baggage", "Suitcase", ["23 kg", "7 kg carry-on"], 6, 2),
    ("Baggage", "Backpack", [""], 8, 3),
    ("Stationery", "Study Journal", [""], 50, 10),
    ("Stationery", "Pens (pack)", [""], 40, 10),
]
NOTES = ["", "", "", "", "Arrival pack", "Replacement — old pair worn out", "Swapped for a larger size"]
DELIVERY = ["Delivery from the distribution centre", "Donation from a local ward", "Monthly order"]


class Command(BaseCommand):
    help = "Fill an EMPTY installation with invented missionaries, stock and history (for demos)."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="Administrator recorded as the actor")
        parser.add_argument("--people", type=int, default=90)
        parser.add_argument("--days", type=int, default=21, help="Days of issue history")
        parser.add_argument("--seed", type=int, default=7)

    def handle(self, *args, **options):
        from apps.inventory.models import Item
        from apps.issuing.models import Issue
        from apps.roster.models import Missionary

        User = get_user_model()
        try:
            actor = User.objects.get(username=options["username"])
        except User.DoesNotExist as exc:
            raise CommandError("administrator not found") from exc
        if not (actor.is_active and actor.is_superuser):
            raise CommandError("user is not an administrator")
        if Missionary.objects.exists() or Item.objects.exists() or Issue.objects.exists():
            raise CommandError(
                "refusing: this installation already has data. Demo data only goes into an "
                "empty trial installation, never alongside a real roster.")
        rng = random.Random(options["seed"])
        staff = [actor] + list(User.objects.filter(is_active=True).exclude(pk=actor.pk)[:3])
        with transaction.atomic():
            people = self._people(rng, actor, max(1, options["people"]))
            items = self._items(rng, actor, start=timezone.now() - timedelta(days=options["days"] + 1))
        issued = self._history(rng, staff, people, items, options["days"])
        self.stdout.write(self.style.SUCCESS(
            f"Loaded demo data: {len(people)} missionaries, {len(items)} items, {issued} issues. "
            "All names and numbers are invented."))

    def _people(self, rng, actor, count):
        from apps.roster import services as roster

        today = timezone.localdate()
        intakes = [(today - timedelta(days=20), today + timedelta(days=12)),
                   (today - timedelta(days=6), today + timedelta(days=26)),
                   (today + timedelta(days=3), today + timedelta(days=45))]
        people, used = [], set()
        for n in range(count):
            sister = rng.random() < 0.42
            given = rng.choice(GIVEN_F if sister else GIVEN_M)
            middle = rng.choice(GIVEN_F if sister else GIVEN_M)
            family = rng.choice(FAMILY)
            while (given, family) in used:
                family = rng.choice(FAMILY)
            used.add((given, family))
            arrival, departure = intakes[0] if n < count * 0.45 else intakes[1] if n < count * 0.88 else intakes[2]
            departure += timedelta(days=rng.choice([0, 0, 7, 14]))
            arrived = arrival <= today
            people.append(roster.create_missionary(
                actor=actor, external_id=f"DEMO-{n + 1:04d}",
                name=f"{family}, {given} {middle}",
                email=f"{given}.{family}".lower().replace(" ", "").replace("'", "") + "@example.org",
                title_prefix="Sister" if sister else "Elder",
                arrival_status="arrived" if arrived else "expected",
                stake=f"{rng.choice(TOWNS)} Stake", mission=rng.choice(MISSIONS),
                language=rng.choice(LANGUAGES), arrival_date=arrival, departure_date=departure,
                release_date=departure + timedelta(days=rng.choice([540, 720])),
                note="Invented demo record"))
        return people

    def _items(self, rng, actor, start):
        from apps.inventory import services as inventory
        from apps.inventory.models import StockMovement

        items = []
        for category, name, sizes, typical, alert in CATALOGUE:
            for size in sizes:
                qty = max(0, int(rng.gauss(typical, typical * 0.35)))
                item = inventory.create_item(
                    actor=actor, category=category, name=name, size=size, opening_qty=qty,
                    reason="Opening stock (demo)", idempotency_key=f"demo-open-{uuid.uuid4().hex}")
                item.low_threshold = alert
                item.save(update_fields=["low_threshold"])
                items.append(item)
        # Openings happened before the history so every ledger chain stays in order.
        StockMovement.objects.filter(kind=StockMovement.OPENING).update(created_at=start)
        return items

    def _history(self, rng, staff, people, items, days):
        from apps.common.errors import DomainError
        from apps.inventory import services as inventory
        from apps.inventory.models import Item, StockMovement
        from apps.issuing import services as issuing
        from apps.issuing.models import Issue

        tz = timezone.get_current_timezone()
        today = timezone.localdate()
        arrived = [p for p in people if p.arrival_status == "arrived"]
        moments = []
        for person in arrived:
            for _ in range(rng.choice([0, 1, 1, 2, 2, 3])):
                day = today - timedelta(days=rng.randint(0, max(0, min(days, (today - person.arrival_date).days))))
                at = datetime.combine(day, time(rng.randint(8, 17), rng.randint(0, 59)), tzinfo=tz)
                if at < timezone.now():
                    moments.append((at, person))
        moments.sort(key=lambda pair: pair[0])
        count = 0
        for at, person in moments:
            in_stock = list(Item.objects.filter(on_hand__gt=0))
            if not in_stock:
                break
            picks = rng.sample(in_stock, k=min(len(in_stock), rng.choice([1, 2, 2, 3, 4])))
            lines = [{"item_id": str(i.pk), "quantity": min(i.on_hand, rng.choice([1, 1, 1, 2]))} for i in picks]
            try:
                issue = issuing.create_issue(actor=rng.choice(staff), missionary_id=person.pk, lines=lines,
                                             idempotency_key=f"demo-{uuid.uuid4().hex}",
                                             note=rng.choice(NOTES))
            except DomainError:
                continue
            Issue.objects.filter(pk=issue.pk).update(issued_at=at)
            StockMovement.objects.filter(issue=issue).update(created_at=at)
            count += 1
        for issue in Issue.objects.order_by("?")[:2]:
            issuing.reverse_issue(actor=staff[0], issue_id=issue.pk, reason="Given to the wrong missionary")
        for item in rng.sample(list(Item.objects.filter(category="Hygiene")), k=3):
            inventory.receive_stock(actor=staff[-1], item_id=item.pk, quantity=rng.choice([12, 24, 36]),
                                    reason=rng.choice(DELIVERY), idempotency_key=f"demo-rx-{uuid.uuid4().hex}")
        return count
