import uuid

from app.core.middleware import SessionHolder
from app.models.rating import ResourceRating
from app.models.user import User
from app.utils.dict import entries

DEFAULT_DIMENSION = {
    "key": "default",
    "name": "评分",
    "removable": False,
}
MAX_CUSTOM_DIMENSIONS = 4


def custom_dimensions(preferences: dict | None) -> list[dict]:
    if not isinstance(preferences, dict):
        return []
    values = preferences.get("rating_dimensions", [])
    if not isinstance(values, list):
        return []
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        key = value.get("key")
        name = value.get("name")
        if (
            not isinstance(key, str)
            or len(key) != 32
            or not isinstance(name, str)
            or not name.strip()
            or key in seen
        ):
            continue
        seen.add(key)
        result.append({"key": key, "name": name.strip(), "removable": True})
    return result


async def save_custom_dimensions(user_id: int, dimensions: list[dict]) -> None:
    user = await User.get(id=user_id)
    preferences = dict(user.preferences) if isinstance(user.preferences, dict) else {}
    preferences["rating_dimensions"] = [
        {"key": item["key"], "name": item["name"]} for item in dimensions
    ]
    await User.filter(id=user_id).update(preferences=preferences)
    sessions = SessionHolder.get_sessions()
    for token, login_user in entries(
        sessions, vfilter=lambda value: value.id == user_id
    ):
        login_user.preferences = preferences
        sessions[token] = login_user


async def add_custom_dimension(user_id: int, name: str) -> dict:
    user = await User.get(id=user_id)
    dimensions = custom_dimensions(user.preferences)
    if len(dimensions) >= MAX_CUSTOM_DIMENSIONS:
        raise ValueError("rating dimension limit reached")
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("empty rating dimension")
    if clean_name.casefold() == DEFAULT_DIMENSION["name"].casefold() or any(
        item["name"].casefold() == clean_name.casefold() for item in dimensions
    ):
        raise ValueError("duplicate rating dimension")
    dimension = {
        "key": uuid.uuid4().hex,
        "name": clean_name,
        "removable": True,
    }
    dimensions.append(dimension)
    await save_custom_dimensions(user_id, dimensions)
    return dimension


async def remove_custom_dimension(user_id: int, key: str) -> bool:
    user = await User.get(id=user_id)
    dimensions = custom_dimensions(user.preferences)
    remaining = [item for item in dimensions if item["key"] != key]
    if len(remaining) == len(dimensions):
        return False
    await save_custom_dimensions(user_id, remaining)
    await ResourceRating.filter(scope_user_id=user_id, dimension_key=key).delete()
    return True


async def rating_dimensions(user_id: int) -> list[dict]:
    user = await User.get(id=user_id)
    return [DEFAULT_DIMENSION, *custom_dimensions(user.preferences)]


async def resource_rating_values(
    user_id: int, targets: list[tuple[str, str]]
) -> dict[tuple[str, str], list[dict]]:
    """Return visible, scored dimensions for a set of resource keys."""
    unique_targets = list(dict.fromkeys(targets))
    result = {target: [] for target in unique_targets}
    if not unique_targets:
        return result

    dimensions = await rating_dimensions(user_id)
    dimension_map = {item["key"]: item for item in dimensions}
    rows = await ResourceRating.filter(
        resource_type__in={target[0] for target in unique_targets},
        resource_key__in={target[1] for target in unique_targets},
        dimension_key__in=dimension_map,
        scope_user_id__in=[0, user_id],
    )
    row_map = {}
    for row in rows:
        expected_scope = 0 if row.dimension_key == DEFAULT_DIMENSION["key"] else user_id
        if row.scope_user_id == expected_scope:
            row_map[(row.resource_type, row.resource_key, row.dimension_key)] = (
                row.score
            )

    for target in unique_targets:
        for dimension in dimensions:
            score = row_map.get((*target, dimension["key"]))
            if score is not None:
                result[target].append(
                    {
                        "key": dimension["key"],
                        "name": dimension["name"],
                        "score": score,
                    }
                )
    return result
