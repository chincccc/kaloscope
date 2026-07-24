from tortoise import fields, migrations
from tortoise.fields.base import OnDelete
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0003_auto_20260531_1044")]

    initial = False

    operations = [
        ops.CreateModel(
            name="Gallery",
            fields=[
                ("id", fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ("created_at", fields.DatetimeField(null=True, auto_now=False, auto_now_add=True)),
                ("updated_at", fields.DatetimeField(null=True, auto_now=True, auto_now_add=False)),
                ("dir", fields.CharField(unique=True, max_length=4096)),
                ("name", fields.CharField(unique=True, max_length=64)),
                ("priority", fields.IntField(unique=True)),
            ],
            options={"table": "gallery", "app": "models", "pk_attr": "id"},
            bases=["TortoiseModel"],
        ),
        ops.CreateModel(
            name="GalleryItem",
            fields=[
                ("id", fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ("created_at", fields.DatetimeField(null=True, auto_now=False, auto_now_add=True)),
                ("updated_at", fields.DatetimeField(null=True, auto_now=True, auto_now_add=False)),
                (
                    "gallery",
                    fields.ForeignKeyField(
                        "models.Gallery",
                        source_field="gallery_id",
                        db_index=True,
                        db_constraint=True,
                        to_field="id",
                        related_name="items",
                        on_delete=OnDelete.CASCADE,
                    ),
                ),
                ("dir", fields.CharField(max_length=4096)),
                ("path", fields.CharField(max_length=4096)),
                ("name", fields.CharField(max_length=255)),
                ("size", fields.BigIntField()),
                ("modified_at", fields.DatetimeField(null=False, auto_now=False, auto_now_add=False)),
            ],
            options={
                "table": "gallery_item",
                "app": "models",
                "pk_attr": "id",
                "unique_together": (("gallery", "path"),),
            },
            bases=["TortoiseModel"],
        ),
    ]
