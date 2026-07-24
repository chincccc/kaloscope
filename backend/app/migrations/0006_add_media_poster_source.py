from tortoise import fields, migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0005_add_media_technical_metadata")]

    initial = False

    operations = [
        ops.AddField(
            "MediaItem",
            "poster_source",
            fields.CharField(max_length=16, null=True),
        ),
    ]
