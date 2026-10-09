"""Template context: administrator-published site notice."""

from __future__ import annotations


def site_notice(request):
    try:
        from apps.operations.models import SystemSetting

        notice = SystemSetting.objects.filter(key="site_notice").values_list(
            "value", flat=True).first()
    except Exception:
        notice = ""
    return {"site_notice": notice or ""}
