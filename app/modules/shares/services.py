from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import Conflict, NotFound
from app.core.settings import get_settings
from app.db.models import GroupShare, Share
from app.modules.samba import os_manager, sync_engine


def resolve_share_path(path: str) -> str:
    """Normalize a share path to an absolute filesystem path.

    Empty or relative paths are resolved against the shares root so existing
    legacy shares (path == name) keep working.
    """
    if not path:
        return ""
    p = Path(path)
    if not p.is_absolute():
        p = get_settings().share_mount_path / p
    return str(p)


def _ensure_path_exists(resolved: str) -> None:
    if not Path(resolved).is_dir():
        raise NotFound(f"Path '{resolved}' does not exist")


async def list_shares(session: AsyncSession) -> list[Share]:
    result = await session.scalars(select(Share).order_by(Share.name))
    return list(result.all())


async def available_dirs(session: AsyncSession) -> list[str]:
    registered = {
        resolve_share_path(p)
        for p in (await session.scalars(select(Share.path))).all()
    }
    return [p for p in os_manager.scan_share_dirs() if p not in registered]


async def get_share(session: AsyncSession, share_id: str) -> Share:
    share = await session.get(Share, share_id)
    if share is None:
        raise NotFound(f"Share '{share_id}' not found")
    return share


async def create_share(
    session: AsyncSession,
    name: str,
    path: str = "",
    comment: str = "",
    wide_links: bool = False,
) -> Share:
    resolved = resolve_share_path(path) or resolve_share_path(name)

    existing = await session.scalar(
        select(Share).where((Share.name == name) | (Share.path == resolved))
    )
    if existing is not None:
        raise Conflict(f"Share '{name}' already exists")

    _ensure_path_exists(resolved)

    share = Share(name=name, path=resolved, comment=comment, wide_links=wide_links)
    session.add(share)
    await session.commit()

    await sync_engine.sync(session)
    return share


async def update_share(
    session: AsyncSession,
    share_id: str,
    name: str,
    path: str,
    comment: str,
    wide_links: bool = False,
) -> Share:
    share = await get_share(session, share_id)

    resolved = resolve_share_path(path) or share.path
    name = name or share.name

    other = await session.scalar(
        select(Share).where(
            (Share.id != share.id)
            & ((Share.name == name) | (Share.path == resolved))
        )
    )
    if other is not None:
        raise Conflict(f"Share '{name}' already exists")

    _ensure_path_exists(resolved)

    share.name = name
    share.path = resolved
    share.comment = comment
    share.wide_links = wide_links
    await session.commit()

    await sync_engine.sync(session)
    return share


async def delete_share(session: AsyncSession, share_id: str) -> None:
    share = await get_share(session, share_id)

    await session.execute(delete(GroupShare).where(GroupShare.share_id == share.id))
    await session.delete(share)
    await session.commit()

    await sync_engine.sync(session)