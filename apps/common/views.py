from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.accounts.decorators import no_activity_update, staff_required, staff_required_api
from apps.common import search
from apps.inventory.models import Item
from apps.roster.models import Missionary


def find(query):
    return (search.people(Missionary.objects.filter(is_archived=False), query),
            search.items(Item.objects.filter(is_archived=False), query))


@staff_required_api
@no_activity_update
@never_cache
@require_GET
def finder(request):
    query = (request.GET.get("q") or "").strip()[:160]
    people, items = find(query)
    scope = request.GET.get("scope", "all")
    results = []
    if scope in ("all", "people"):
        results.extend({"id": str(p.pk), "label": p.display_name, "detail": p.external_id,
                        "sub": " · ".join(part for part in (
                            p.full_name if p.full_name != p.display_name else "",
                            p.external_id, p.mission) if part),
                        "group": "Missionaries", "url": f"/roster/{p.pk}/"}
                       for p in people[:12])
    if scope in ("all", "items"):
        results.extend({"id": str(i.pk), "label": f"{i.name} ({i.size})" if i.size else i.name,
                        "detail": f"{i.category} · {i.size or 'No size'}",
                        "sub": f"{i.category} · {i.on_hand} available",
                        "available": i.on_hand, "group": "Inventory",
                        "url": f"/inventory/{i.pk}/"} for i in items[:12])
    return JsonResponse({"results": results})


@staff_required
@require_GET
def search_page(request):
    query = (request.GET.get("q") or "").strip()[:160]
    people, items = find(query)
    return render(request, "search.html", {"q": query, "people": people[:12],
                  "items": items[:12], "people_total": people.count(), "items_total": items.count()})
