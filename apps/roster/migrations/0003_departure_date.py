"""Add the MTC departure date and tidy stored arrival values.

The original HTML marked arrival with a JSON boolean, which earlier imports
stored as the text "True"/"False". Those (and yes/no spellings) become the
canonical ``arrived``/``expected``; any other text is kept unchanged.
"""

from django.db import migrations, models


def normalize_arrival_values(apps, schema_editor):
    from apps.roster.models import normalize_arrival

    Missionary = apps.get_model("roster", "Missionary")
    for person in Missionary.objects.exclude(arrival_status="").only("pk", "arrival_status"):
        tidy = normalize_arrival(person.arrival_status)
        if tidy != person.arrival_status:
            Missionary.objects.filter(pk=person.pk).update(arrival_status=tidy)


class Migration(migrations.Migration):

    dependencies = [
        ("roster", "0002_missionary_arrival_date_missionary_arrival_status_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="missionary",
            name="departure_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(normalize_arrival_values, migrations.RunPython.noop),
    ]
