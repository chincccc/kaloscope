from tortoise import fields, migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0008_add_comic_download_tasks")]

    initial = False

    operations = [
        ops.AddField(
            "ComicDownloadTask",
            "title",
            fields.CharField(max_length=255, null=True),
        ),
        ops.AddField(
            "ComicDownloadTask",
            "cover",
            fields.TextField(null=True),
        ),
    ]
