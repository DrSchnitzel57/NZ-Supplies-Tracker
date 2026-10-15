"""Issue-goods views: main multi-line screen, quick profile issues, history, undo."""

from __future__ import annotations

import uuid
import re

from django.contrib import messages
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import staff_required
from apps.common.errors import AlreadyReversedError, DomainError, InsufficientStockError
from apps.inventory.models import Item
from apps.issuing import services as issuing
from apps.issuing.models import Issue
from apps.roster.models import Missionary
from apps.reports.filters import ReportFilters, filter_issues
from apps.common.pagination import paginate


def _line_indices(post):
    return sorted({int(match.group(1)) for key in post
                   if (match := re.fullmatch(r"(?:item|qty)-(\d+)", key))})


def _parse_lines(post) -> list[dict]:
    """Collect item/quantity pairs; consolidate later in the service."""
    lines: list[dict] = []
    # Read every submitted row; the service bounds distinct items, never slices.
    indices = _line_indices(post)
    for i in indices:
        item_id = (post.get(f"item-{i}") or "").strip()
        qty_raw = (post.get(f"qty-{i}") or "").strip()
        if not item_id and not qty_raw:
            continue
        if not item_id:
            raise ValueError("Every line needs an item.")
        try:
            qty = int(qty_raw)
        except (TypeError, ValueError):
            raise ValueError("Quantities must be whole numbers.")
        try:
            uuid.UUID(item_id)
        except ValueError:
            raise ValueError("Choose a valid item.")
        lines.append({"item_id": item_id, "quantity": qty})
    # Non-JS catalogue additions and the legacy single-line endpoint.
    single_item = (post.get("item_id") or "").strip()
    single_qty = (post.get("quantity") or "").strip()
    if single_item or (single_qty and not lines):
        try:
            qty = int(single_qty)
        except (TypeError, ValueError):
            raise ValueError("Quantities must be whole numbers.")
        try:
            uuid.UUID(single_item)
        except ValueError:
            raise ValueError("Choose a valid item.")
        lines.append({"item_id": single_item, "quantity": qty})
    return lines


def _form_context(draft_key=None, draft_lines=None, missionary_id="", note=""):
    missionaries = list(
        Missionary.objects.filter(is_archived=False).order_by("name")
    )
    items = list(
        Item.objects.filter(is_archived=False).order_by("category", "name", "size")
    )
    if draft_lines is None:
        draft_lines = []
    labels = {str(item.pk): str(item) for item in items}
    draft_lines = [{**row, "label": labels.get(str(row["item_id"]), "Unavailable item")}
                   for row in draft_lines if row.get("item_id") or row.get("quantity")]
    return {
        "missionaries": missionaries,
        "items": items,
        "draft_key": draft_key or str(uuid.uuid4().hex),
        "draft_lines": draft_lines,
        "missionary_id": missionary_id,
        "selected_person": next((p for p in missionaries if str(p.pk) == str(missionary_id)), None),
        "note": note,
    }


def _shortage_context(*, missionary, requested_lines, err, draft_key, return_to="/issue/"):
    requested_map = {}
    for entry in requested_lines:
        requested_map[str(entry["item_id"])] = requested_map.get(str(entry["item_id"]), 0) + int(entry["quantity"])
    item_map = {str(item.pk): item for item in Item.objects.filter(pk__in=requested_map)}
    revised = {str(entry['item_id']): entry['quantity'] for entry in err.proposal}
    rows, ok_rows = [], []
    # Always compare the original request against the actual proposal, including
    # items dropped at the first review. Never suggest an excluded line is issued.
    for item_id, requested in requested_map.items():
        item = item_map.get(item_id)
        row = {'item': item, 'item_id': item_id, 'requested': requested,
               'available': item.on_hand if item else 0, 'revised': revised.get(item_id, 0)}
        (rows if row['revised'] < requested else ok_rows).append(row)
    return {
        "missionary": missionary,
        "shortage_rows": rows,
        "ok_rows": ok_rows,
        "proposal": err.proposal,
        "confirm_key": draft_key,
        "return_to": return_to,
        "requested_lines": requested_lines,
    }


@staff_required
def issue_main(request):
    if request.method == "POST":
        return _handle_issue_post(
            request, return_to="/issue/", template="issuing/issue.html"
        )
    ctx = _form_context(missionary_id=request.GET.get("missionary", ""))
    return render(request, "issuing/issue.html", ctx)


@staff_required
@require_http_methods(["POST"])
def issue_quick(request):
    """Single-line issue from a missionary profile; same shared service."""
    missionary_id = (request.POST.get("missionary_id") or request.POST.get("fallback_missionary_id") or "").strip()
    try:
        person = Missionary.objects.get(pk=missionary_id) if missionary_id else None
    except (Missionary.DoesNotExist, ValueError, TypeError, DjangoValidationError):
        person = None
    return_to = f"/roster/{person.pk}/" if person is not None else "/issue/"
    return _handle_issue_post(
        request, return_to=return_to, template="issuing/issue.html", quick_person=person
    )


def _handle_issue_post(request, *, return_to, template, quick_person=None):
    missionary_id = (request.POST.get("missionary_id") or request.POST.get("fallback_missionary_id") or "").strip()
    note = request.POST.get("note", "")
    idem = (request.POST.get("idempotency_key") or "").strip() or str(
        uuid.uuid4().hex
    )
    draft_user = request.POST.get("draft_user_id")
    if draft_user and draft_user != str(request.user.pk):
        messages.error(request, "This draft belongs to a different account. Sign in as the original staff member; nothing was issued.")
        return render(request, template, _form_context(), status=409)
    try:
        lines = _parse_lines(request.POST)
    except ValueError as exc:
        messages.error(request, f"Could not issue goods: {exc}")
        kept_rows = [
            {"item_id": (request.POST.get(f"item-{i}") or ""),
             "quantity": (request.POST.get(f"qty-{i}") or "")}
            for i in _line_indices(request.POST)
        ]
        ctx = _form_context(
            draft_key=idem,
            missionary_id=missionary_id,
            note=note,
            draft_lines=kept_rows or None,
        )
        # Preserve single-line quick drafts too.
        if not ctx["draft_lines"]:
            ctx["draft_lines"].append({
                "item_id": request.POST.get("item_id", ""),
                "quantity": request.POST.get("quantity", ""),
            })
        return render(request, template, ctx, status=400)
    try:
        missionary = Missionary.objects.get(pk=missionary_id) if missionary_id else None
    except (Missionary.DoesNotExist, ValueError, TypeError, DjangoValidationError):
        missionary = None
    if missionary is None:
        messages.error(request, "Choose a person to issue goods to.")
        # Keep the typed lines so nothing is lost.
        kept = []
        for entry in lines:
            kept.append({"item_id": entry["item_id"], "quantity": entry["quantity"]})
        ctx = _form_context(draft_key=idem, missionary_id="", note=note, draft_lines=kept)
        return render(request, template, ctx, status=400)
    if request.POST.get("edit_draft") == "1":
        return render(request, template, _form_context(
            draft_key=idem, missionary_id=missionary_id, note=note, draft_lines=lines))
    try:
        issue = issuing.create_issue(
            actor=request.user,
            missionary_id=missionary.pk,
            lines=lines,
            note=note,
            idempotency_key=idem,
        )
    except InsufficientStockError as exc:
        ctx = _shortage_context(
            missionary=missionary,
            requested_lines=lines,
            err=exc,
            draft_key=idem,
            return_to=return_to,
        )
        ctx["note"] = note
        messages.warning(
            request,
            "Some items are short. Review the revised quantities and confirm — nothing was issued yet.",
        )
        return render(request, "issuing/shortage.html", ctx, status=409)
    except DomainError as exc:
        messages.error(request, f"Could not issue goods: {exc}")
        ctx = _form_context(draft_key=idem, missionary_id=missionary_id, note=note, draft_lines=lines)
        return render(request, template, ctx, status=400)
    messages.success(request, f"Issued goods to {issue.missionary_name}.")
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"redirect_url": reverse("issue-detail", kwargs={"pk": issue.pk})})
    return redirect("issue-detail", pk=issue.pk)


@staff_required
@require_http_methods(["POST"])
def issue_confirm(request):
    """Commit a previously proposed revised list; the server rechecks stock."""
    import html as _html
    import json

    missionary_id = (request.POST.get("missionary_id") or "").strip()
    note = request.POST.get("note", "")
    idem = (request.POST.get("idempotency_key") or "").strip() or str(
        uuid.uuid4().hex
    )
    if request.POST.get("draft_user_id") not in (None, "", str(request.user.pk)):
        messages.error(request, "Sign in as the original staff member. Nothing was issued.")
        return render(request, "issuing/issue.html", _form_context(), status=409)
    proposal = []
    try:
        original = _parse_lines(request.POST)
    except ValueError as exc:
        original = [{"item_id": request.POST.get(f"item-{i}", ""),
                     "quantity": request.POST.get(f"qty-{i}", "")} for i in _line_indices(request.POST)]
        messages.error(request, f"The original request was unclear: {exc}")
        return render(request, 'issuing/issue.html', _form_context(
            draft_key=idem, missionary_id=missionary_id, note=note, draft_lines=original), status=400)
    p_items = request.POST.getlist("p-item")
    p_qtys = request.POST.getlist("p-qty")
    if p_items:
        try:
            if len(p_items) != len(p_qtys):
                raise ValueError("Each revised item needs a quantity.")
            proposal = [
                {"item_id": i, "quantity": int(q)}
                for i, q in zip(p_items, p_qtys)
            ]
        except (ValueError, TypeError):
            proposal = []
    if not proposal:
        raw = _html.unescape(request.POST.get("proposal_json", "[]"))
        try:
            proposal = [
                {"item_id": e["item_id"], "quantity": int(e["quantity"])}
                for e in (json.loads(raw) if isinstance(raw, str) else [])
            ]
        except (ValueError, KeyError, TypeError):
            proposal = []
    if not proposal:
        messages.error(request, "The revised list was unclear. Please try again.")
        ctx = _form_context(draft_key=idem, missionary_id=missionary_id, note=note, draft_lines=original)
        return render(request, "issuing/issue.html", ctx, status=400)
    try:
        missionary = Missionary.objects.get(pk=missionary_id)
    except (Missionary.DoesNotExist, ValueError, TypeError, DjangoValidationError):
        messages.error(request, "Choose a person to issue goods to.")
        kept = original or proposal
        ctx = _form_context(draft_key=idem, missionary_id="", note=note, draft_lines=kept)
        return render(request, "issuing/issue.html", ctx, status=400)
    try:
        issue = issuing.create_issue(
            actor=request.user,
            missionary_id=missionary.pk,
            lines=proposal,
            note=note,
            idempotency_key=idem,
        )
    except InsufficientStockError as exc:
        ctx = _shortage_context(
            missionary=missionary, requested_lines=original or proposal, err=exc, draft_key=idem
        )
        ctx["note"] = note
        messages.warning(
            request,
            "Stock changed again before confirming. Review the new revised quantities — nothing was issued yet.",
        )
        return render(request, "issuing/shortage.html", ctx, status=409)
    except DomainError as exc:
        messages.error(request, f"Could not issue goods: {exc}")
        kept = original or proposal
        ctx = _form_context(draft_key=idem, missionary_id=str(missionary.pk), note=note, draft_lines=kept)
        return render(request, "issuing/issue.html", ctx, status=400)
    messages.success(request, f"Issued revised quantities to {issue.missionary_name}.")
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"redirect_url": reverse("issue-detail", kwargs={"pk": issue.pk})})
    return redirect("issue-detail", pk=issue.pk)


@staff_required
def issue_list(request):
    data = request.GET.copy()
    # History is an immutable operational record: include archives by default.
    # The visible checkbox makes a deliberate active-only view available.
    if data.get('filtered') != '1' and 'archived' not in data:
        data['archived'] = '1'
    form = ReportFilters(data, family='history')
    valid = form.is_valid()
    qs = filter_issues(Issue.objects.all(), form.cleaned_data) if valid else Issue.objects.none()
    issues, pagination = paginate(request, qs.select_related('missionary').prefetch_related('lines').order_by('-issued_at', 'pk'))
    return render(request, 'issuing/history.html', {'issues': issues, 'form': form, **pagination}, status=200 if valid else 400)


@staff_required
def issue_detail(request, pk):
    issue = get_object_or_404(
        Issue.objects.select_related("missionary", "issued_by", "reversed_by").prefetch_related("lines"),
        pk=pk,
    )
    return render(request, "issuing/detail.html", {"issue": issue})


@staff_required
@require_http_methods(["POST"])
def issue_reverse(request, pk):
    issue = get_object_or_404(Issue, pk=pk)
    reason = request.POST.get("reason", "")
    try:
        issuing.reverse_issue(actor=request.user, issue_id=issue.pk, reason=reason)
    except AlreadyReversedError:
        messages.error(request, "This issue was already undone. Nothing changed.")
        return redirect("issue-detail", pk=issue.pk)
    except DomainError as exc:
        messages.error(request, f"Could not undo this issue: {exc}")
        return render(request, 'issuing/detail.html', {'issue': issue, 'undo_reason': reason}, status=400)
    messages.success(request, "Undone: the goods were added back to Available.")
    return redirect("issue-detail", pk=issue.pk)
