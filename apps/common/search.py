"""Bounded, deterministic term matching shared by lists and finders."""

from django.db import connection
from django.db.models import Case, CharField, F, Func, IntegerField, Q, Value, When


def search_records(qs, query, fields, exact, ordering):
    # SQLite's built-in LIKE/LOWER only fold ASCII. Keep Māori/macron searches
    # case-insensitive without an index dependency or a data/schema migration.
    connection.ensure_connection()
    connection.connection.create_function("supplies_fold", 1,
        lambda value: " ".join((value or "").casefold().split()), deterministic=True)
    aliases = {field: f"find_{index}" for index, field in enumerate(fields)}
    qs = qs.alias(**{alias: Func(F(field), function="supplies_fold", output_field=CharField())
                     for field, alias in aliases.items()})
    query = " ".join((query or "").strip()[:160].casefold().split())
    for term in query.split()[:8]:
        match = Q()
        for field in fields:
            match |= Q(**{f"{aliases[field]}__contains": term})
        qs = qs.filter(match)
    priority = Q()
    for field in exact:
        priority |= Q(**{aliases[field]: query})
    return qs.annotate(search_rank=Case(
        When(priority, then=Value(0)), default=Value(1),
        output_field=IntegerField())).order_by("search_rank", *ordering, "pk")


def people(qs, query, ordering=("name",)):
    return search_records(qs, query,
        ("external_id", "name", "email", "mission", "language", "title_prefix"),
        ("external_id", "name"), ordering)


def items(qs, query, ordering=("category", "name", "size")):
    return search_records(qs, query, ("name", "category", "size"), ("name",), ordering)
