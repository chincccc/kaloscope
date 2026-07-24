from tortoise import fields, migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0006_add_media_poster_source")]

    initial = False

    operations = [
        ops.CreateModel(
            name="ResourceRating",
            fields=[
                (
                    "id",
                    fields.IntField(
                        generated=True, primary_key=True, unique=True, db_index=True
                    ),
                ),
                (
                    "created_at",
                    fields.DatetimeField(null=True, auto_now=False, auto_now_add=True),
                ),
                (
                    "updated_at",
                    fields.DatetimeField(null=True, auto_now=True, auto_now_add=False),
                ),
                ("scope_user_id", fields.IntField(db_index=True)),
                ("resource_type", fields.CharField(max_length=16)),
                ("resource_key", fields.CharField(max_length=4096)),
                ("dimension_key", fields.CharField(max_length=32)),
                ("score", fields.IntField()),
            ],
            options={
                "table": "resource_rating",
                "app": "models",
                "pk_attr": "id",
                "unique_together": (
                    (
                        "scope_user_id",
                        "resource_type",
                        "resource_key",
                        "dimension_key",
                    ),
                ),
            },
            bases=["TortoiseModel"],
        )
    ]
