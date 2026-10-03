from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.modules.auth.dependencies import get_current_admin
from app.modules.shares import services
from app.modules.shares.schemas import ShareCreate, ShareOut, ShareUpdate

router = APIRouter(
    prefix="/shares",
    tags=["shares"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=list[ShareOut])
async def list_shares(session: AsyncSession = Depends(get_session)) -> list[ShareOut]:
    shares = await services.list_shares(session)
    return [ShareOut.model_validate(s) for s in shares]


@router.get("/available", response_model=list[str])
async def available_dirs(session: AsyncSession = Depends(get_session)) -> list[str]:
    return await services.available_dirs(session)


@router.post("", response_model=ShareOut, status_code=201)
async def create_share(
    body: ShareCreate, session: AsyncSession = Depends(get_session)
) -> ShareOut:
    share = await services.create_share(
        session, body.name, body.path, body.comment, body.wide_links
    )
    return ShareOut.model_validate(share)


@router.patch("/{share_id}", response_model=ShareOut)
async def update_share(
    share_id: str, body: ShareUpdate, session: AsyncSession = Depends(get_session)
) -> ShareOut:
    share = await services.update_share(
        session, share_id, body.name, body.path, body.comment, body.wide_links
    )
    return ShareOut.model_validate(share)


@router.delete("/{share_id}", status_code=204)
async def delete_share(
    share_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    await services.delete_share(session, share_id)