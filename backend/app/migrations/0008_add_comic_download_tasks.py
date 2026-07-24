from tortoise import fields, migrations
from tortoise.fields.base import OnDelete
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0007_add_resource_ratings")]

    initial = False

    operations = [
        ops.CreateModel(
            name="ComicDownloadTask",
            fields=[
                (
                    "id",
                    fields.IntField(
                        generated=True,
                        primary_key=True,
                        unique=True,
                        db_index=True,
                    ),
                ),
                (
                    "created_at",
                    fields.DatetimeField(null=True, auto_now_add=True),
                ),
                (
                    "updated_at",
                    fields.DatetimeField(null=True, auto_now=True),
                ),
                (
                    "gallery",
                    fields.ForeignKeyField(
                        "models.Gallery",
                        source_field="gallery_id",
                        db_index=True,
                        db_constraint=True,
                        to_field="id",
                        related_name="download_tasks",
                        null=True,
                        on_delete=OnDelete.SET_NULL,
                    ),
                ),
                ("url", fields.TextField()),
                ("request_headers", fields.JSONField(null=True)),
                ("dir", fields.CharField(max_length=4096)),
                ("name", fields.CharField(max_length=255)),
                ("temp_path", fields.CharField(max_length=4096)),
                ("final_path", fields.CharField(max_length=4096)),
                ("state", fields.CharField(max_length=16)),
                ("error_msg", fields.TextField(null=True)),
                ("dl_speed", fields.BigIntField(null=True)),
                ("percentage", fields.FloatField(null=True)),
                ("total_size", fields.BigIntField(null=True)),
                ("completed_size", fields.BigIntField(null=True)),
                ("completed_at", fields.DatetimeField(null=True)),
            ],
            options={
                "table": "comic_download_task",
                "ordering": ["-created_at"],
                "app": "models",
                "pk_attr": "id",
            },
            bases=["TortoiseModel"],
        )
    ]
