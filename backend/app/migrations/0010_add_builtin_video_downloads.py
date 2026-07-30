from tortoise import fields, migrations
from tortoise.fields.base import OnDelete
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0009_add_comic_download_metadata")]

    initial = False

    operations = [
        ops.AddField(
            "ComicDownloadTask",
            "download_type",
            fields.CharField(max_length=16, null=True),
        ),
        ops.RunSQL(
            "UPDATE comic_download_task SET download_type = 'comic' "
            "WHERE download_type IS NULL"
        ),
        ops.AddField(
            "ComicDownloadTask",
            "media_lib",
            fields.ForeignKeyField(
                "models.MediaLib",
                source_field="media_lib_id",
                db_index=True,
                db_constraint=True,
                to_field="id",
                related_name="builtin_download_tasks",
                null=True,
                on_delete=OnDelete.SET_NULL,
            ),
        ),
    ]
