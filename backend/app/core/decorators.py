from functools import wraps
from inspect import isawaitable

from sanic import Request

from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.models.user import Permissions, PermType, UserInfo, UserPermission, UserRole


def authorize(role: UserRole | None = None):
    """Decorator to authorize a user based on their role and permissions.

    Args:
        role: The required role for the user. If None, any logged in user is allowed.
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            if not hasattr(request.ctx, "user") or not request.ctx.user:
                raise UnauthorizedException

            user: UserInfo = request.ctx.user
            if role is not None:
                # valid user role if role is specified
                if user.role != role:
                    raise ForbiddenException
            elif user.role != UserRole.ADMIN:
                # attach permissions to the user info if no specific role is required
                indexer_ids = []
                lib_ids = []
                gallery_ids = []
                for perm in await UserPermission.filter(user_id=user.id):
                    if perm.rel_type == PermType.INDEXER:
                        indexer_ids.append(perm.rel_id)
                    elif perm.rel_type == PermType.MEDIA_LIB:
                        lib_ids.append(perm.rel_id)
                    elif perm.rel_type == PermType.GALLERY:
                        gallery_ids.append(perm.rel_id)
                user.perms = Permissions(
                    indexer_ids=indexer_ids,
                    media_lib_ids=lib_ids,
                    gallery_ids=gallery_ids,
                )

            # call the main method and await if it's a coroutine
            response = func(request, *args, **kwargs)
            if isawaitable(response):
                response = await response
            return response

        return wrapper

    return decorator


def request_ctx(ctx: dict):
    """Sets the context for the request.

    Args:
        ctx: The context to set.
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            for key, value in ctx.items():
                setattr(request.ctx, key, value)

            # call the main method and await if it's a coroutine
            response = func(request, *args, **kwargs)
            if isawaitable(response):
                response = await response
            return response

        return wrapper

    return decorator


def before(method: str):
    """Decorator to execute a method before the main method is called.

    Args:
        method: The method to execute.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            getattr(self, method)()
            return func(self, *args, **kwargs)

        return wrapper

    return decorator


def after(method: str):
    """Decorator to execute a method after the main method returns.

    Args:
        method: The method to execute.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            result = func(self, *args, **kwargs)
            getattr(self, method)()
            return result

        return wrapper

    return decorator
