"""Transfer views: full bundles (admin) and spreadsheet previews (staff)."""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import admin_required, staff_required
from apps.common.errors import DomainError
from apps.transfers import services as transfer_services
from apps.transfers.models import RowImportPreview
from apps.transfers.row_imports import (
    build_preview,
    current_revision,
    detect_kind,
    new_preview_token,
    read_table,
)

PREVIEW_TTL_SECONDS = 24 * 3600


def _get_preview_or_expired(token: str):
    from django.utils import timezone

    try:
        preview = RowImportPreview.objects.get(token=token)
    except RowImportPreview.DoesNotExist:
        return None
    age = (timezone.now() - preview.created_at).total_seconds()
    if age > PREVIEW_TTL_SECONDS:
        preview.delete()
        return None
    return preview


@admin_required
def bundle_home(request):
    empty = transfer_services.operational_empty()
    return render(request, "transfers/bundle.html", {"empty": empty})


@admin_required
def bundle_export(request):
    data = transfer_services.export_bundle(actor=request.user)
    response = HttpResponse(data, content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="supplies-bundle.zip"'
    return response


@admin_required
@require_http_methods(["POST"])
def bundle_import(request):
    upload = request.FILES.get("bundle")
    if upload is None:
        messages.error(request, "Choose a bundle ZIP to import.")
        return redirect("transfers-bundle")
    if upload.size > 10 * 1024 * 1024:
        messages.error(request, "Bundle is too large (limit 10 MB).")
        return redirect("transfers-bundle")
    try:
        result = transfer_services.import_bundle(
            actor=request.user, data=upload.read(), filename=upload.name)
    except ValueError as exc:
        messages.error(request, f"Could not import bundle: {exc}")
        return redirect("transfers-bundle")
    except DomainError as exc:
        messages.error(request, f"Could not import bundle: {exc}")
        return redirect("transfers-bundle")
    messages.success(request, f"Imported bundle {result.get('counts', {})}.")
    return redirect("dashboard")


def _import_screen(request, kind: str):
    previews = list(RowImportPreview.objects.filter(kind=kind).order_by("-created_at")[:10])
    return render(request, "transfers/rows.html",
                  {"kind": kind, "previews": previews})


@staff_required
def roster_import(request):
    if request.method == "POST":
        return _handle_upload(request, kind="roster")
    return _import_screen(request, "roster")


@staff_required
def inventory_import(request):
    if request.method == "POST":
        return _handle_upload(request, kind="inventory")
    return _import_screen(request, "inventory")


@staff_required
@require_http_methods(["POST"])
def _handle_upload(request, kind: str):
    from apps.accounts import services as permissions

    upload = request.FILES.get("file")
    mode = (request.POST.get("mode") or "add_update").strip()
    if mode not in ("add_update", "replace"):
        messages.error(request, "Unknown import mode.")
        return redirect(f"transfers-{kind}")
    if mode == "replace" and not permissions.is_admin(request.user):
        messages.error(request, "Replacement imports need an administrator.")
        return redirect(f"transfers-{kind}")
    if upload is None:
        messages.error(request, "Choose a .csv or plain .xlsx file.")
        return redirect(f"transfers-{kind}")
    try:
        detect_kind(upload.name)
        rows = read_table(filename=upload.name, content=upload.read())
        preview = build_preview(kind=kind, rows=rows, mode=mode)
    except ValueError as exc:
        messages.error(request, f"Could not read spreadsheet: {exc}")
        return redirect(f"transfers-{kind}")
    token = new_preview_token()
    stored = []
    for entry in preview["valid"]:
        stored.append({**entry, "error": False})
    for err in preview["errors"]:
        stored.append({**err["entry"], "error": True,
                       "problems": err["problems"]})
    RowImportPreview.objects.create(
        token=token, kind=kind, mode=mode, rows=stored,
        base_revision=current_revision(kind), created_by=request.user)
    return redirect("transfers-preview", token=token)


@staff_required
def import_preview(request, token: str):
    preview = _get_preview_or_expired(token)
    if preview is None:
        messages.error(request, "Preview not found or expired; upload again.")
        return redirect("dashboard")
    stale = preview.base_revision != current_revision(preview.kind)
    valid = [r for r in preview.rows if not r.get("error")]
    errors = [r for r in preview.rows if r.get("error")]
    to_archive = 0
    if preview.mode == "replace":
        if preview.kind == "roster":
            from apps.roster.models import Missionary
            keep = {r["external_id"] for r in valid}
            to_archive = Missionary.objects.filter(is_archived=False).exclude(external_id__in=keep).count()
        else:
            from apps.inventory.models import Item, item_key
            keep = {item_key(r["category"], r["name"], r.get("size", "")) for r in valid}
            to_archive = Item.objects.filter(is_archived=False).exclude(key__in=keep).count()
    return render(request, "transfers/preview.html", {
        "preview": preview, "valid": valid, "errors": errors,
        "stale": stale, "to_archive": to_archive,
    })


@staff_required
@require_http_methods(["POST"])
def import_confirm(request, token: str):
    preview = _get_preview_or_expired(token)
    if preview is None:
        messages.error(request, "Preview expired; upload again.")
        return redirect("dashboard")
    # Approved valid subset: checkboxes named row-<n>; absent means all valid.
    has_choices = any(k.startswith("row-") for k in request.POST)
    chosen = []
    for k, v in request.POST.items():
        if k.startswith("row-") and v == "on":
            num = k[len("row-"):]
            if num.isdigit():
                chosen.append(int(num))
    # If no checkboxes were rendered/checked explicitly, treat missing key:
    subset = set(chosen) if has_choices else None
    try:
        result = transfer_services.confirm_row_import(
            actor=request.user, preview=preview, selected=subset)
    except DomainError as exc:
        messages.error(request, f"Could not apply import: {exc}")
        return redirect("transfers-preview", token=token)
    messages.success(
        request,
        f"Import applied: {result['created']} added, {result['updated']} updated, "
        f"{result['adjusted']} counts corrected, {result['archived']} archived.")
    return redirect("dashboard")
