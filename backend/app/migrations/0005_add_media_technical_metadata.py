from tortoise import fields, migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0004_add_galleries")]

    initial = False

    operations = [
        ops.AddField("MediaItem", "duration", fields.FloatField(null=True)),
        ops.AddField("MediaItem", "width", fields.IntField(null=True)),
        ops.AddField("MediaItem", "height", fields.IntField(null=True)),
        ops.AddField("MediaItem", "bitrate", fields.BigIntField(null=True)),
    ]
