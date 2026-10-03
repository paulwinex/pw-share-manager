import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.db.models import AccessLevel, Group, GroupShare, Share, User, UserGroup  # noqa: F401
from app.modules.samba.sync_engine import compute_target, sync


def _run(db_file: Path, seed, action):
    async def main():
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sm = async_sessionmaker(engine, expire_on_commit=False)
            if seed is not None:
                async with sm() as session:
                    await seed(session)
            async with sm() as session:
                return await action(session)
        finally:
            await engine.dispose()

    return asyncio.run(main())


async def _seed_access(session):
    alice = User(username="alice", password_hash="x")
    bob = User(username="bob", password_hash="x")
    g1 = Group(name="g1")
    g2 = Group(name="g2")
    photos = Share(name="photos", path="photos")
    empty = Share(name="empty", path="empty")
    session.add_all([alice, bob, g1, g2, photos, empty])
    await session.flush()

    session.add(GroupShare(group_id=g1.id, share_id=photos.id))
    session.add(GroupShare(group_id=g2.id, share_id=photos.id))
    session.add(UserGroup(user_id=bob.id, group_id=g1.id, access_level=AccessLevel.RO))
    session.add(UserGroup(user_id=bob.id, group_id=g2.id, access_level=AccessLevel.RW))
    session.add(UserGroup(user_id=alice.id, group_id=g1.id, access_level=AccessLevel.RO))
    await session.commit()


async def _seed_share(session, **share_kwargs):
    """One user, one group, one share — the minimum for sync to export it."""
    alice = User(username="alice", password_hash="x")
    group = Group(name="g1")
    share = Share(name="photos", path="photos", **share_kwargs)
    session.add_all([alice, group, share])
    await session.flush()
    session.add(GroupShare(group_id=group.id, share_id=share.id))
    session.add(UserGroup(user_id=alice.id, group_id=group.id, access_level=AccessLevel.RO))
    await session.commit()


def _wide_links_sets(fake_runner):
    return fake_runner.find("net", "conf", "setparm", "photos", "wide links")


def test_sync_sets_wide_links_yes_when_enabled(tmp_path, fake_runner):
    fake_runner.set_response("net conf listshares", 0, "")

    _run(
        tmp_path / "wl_on.db",
        lambda s: _seed_share(s, wide_links=True),
        lambda s: sync(s),
    )

    sets = _wide_links_sets(fake_runner)
    assert len(sets) == 1
    assert sets[0]["args"][-1] == "yes"


def test_sync_reverts_wide_links_when_flag_cleared(tmp_path, fake_runner):
    """A stale 'yes' in the registry must be overwritten, not left behind."""
    root = Path(__import__("os").environ["SHARE_MOUNT_PATH"])
    fake_runner.set_response("net conf listshares", 0, "photos\n")
    fake_runner.set_response(
        "net conf showshare photos",
        0,
        "[photos]\n"
        f"\tpath = {root / 'photos'}\n"
        "\tcomment = NAS Manager\n"
        "\tforce user = service-user\n"
        "\tbrowseable = yes\n"
        "\tvalid users = alice\n"
        "\twrite list = \n"
        "\tread list = alice\n"
        "\twide links = yes\n",
    )

    report = _run(
        tmp_path / "wl_revert.db",
        lambda s: _seed_share(s, wide_links=False),
        lambda s: sync(s),
    )

    assert report.updated == ["photos"]
    assert report.params_set["photos"] == ["wide links"]
    sets = _wide_links_sets(fake_runner)
    assert len(sets) == 1
    assert sets[0]["args"][-1] == "no"


def test_sync_sets_wide_links_no_by_default(tmp_path, fake_runner):
    fake_runner.set_response("net conf listshares", 0, "")

    _run(tmp_path / "wl_off.db", lambda s: _seed_share(s), lambda s: sync(s))

    sets = _wide_links_sets(fake_runner)
    assert len(sets) == 1
    assert sets[0]["args"][-1] == "no"


def test_compute_target_rw_beats_ro(tmp_path):
    target = _run(
        tmp_path / "ct.db",
        _seed_access,
        lambda s: compute_target(s),
    )

    assert set(target) == {"photos", "empty"}
    photos = target["photos"]
    root = Path(__import__("os").environ["SHARE_MOUNT_PATH"])
    assert photos.path == str(root / "photos")
    assert sorted(photos.valid_users) == ["alice", "bob"]
    assert photos.write_list == ["bob"]
    assert photos.read_list == ["alice"]

    empty = target["empty"]
    assert empty.valid_users == []
    assert empty.write_list == []
    assert empty.read_list == []


def test_sync_adds_removes_sets_params_once_then_noop(tmp_path, fake_runner):
    db_file = tmp_path / "sync.db"

    def seed(session):
        return _seed_access(session)

    # Registry starts with a stale share only.
    fake_runner.set_response("net conf listshares", 0, "ghost\n")

    report = _run(db_file, seed, lambda s: sync(s))

    assert report.added == ["photos"]
    assert report.removed == ["ghost"]
    assert sorted(report.params_set["photos"]) == [
        "browseable",
        "comment",
        "force user",
        "read list",
        "valid users",
        "wide links",
        "write list",
    ]

    assert fake_runner.find("net", "conf", "delshare", "ghost")
    root = Path(__import__("os").environ["SHARE_MOUNT_PATH"])
    adds = fake_runner.find("net", "conf", "addshare", "photos", str(root / "photos"))
    assert len(adds) == 1

    setparm_calls = [c for c in fake_runner.calls if "setparm" in c["args"]]
    assert len(setparm_calls) == 7

    # Second run: registry now matches the target exactly -> zero mutating commands.
    fake_runner.set_response("net conf listshares", 0, "photos\n")
    fake_runner.set_response(
        "net conf showshare photos",
        0,
        "[photos]\n"
        f"\tpath = {root / 'photos'}\n"
        "\tcomment = NAS Manager\n"
        "\tforce user = service-user\n"
        "\tbrowseable = yes\n"
        "\tvalid users = alice bob\n"
        "\twrite list = bob\n"
        "\tread list = alice\n"
        "\twide links = no\n",
    )
    fake_runner.calls.clear()

    report2 = _run(db_file, None, lambda s: sync(s))

    assert report2.added == []
    assert report2.removed == []
    assert report2.params_set == {}
    mutating = [c for c in fake_runner.calls if any(a in ("addshare", "delshare", "setparm") for a in c["args"])]
    assert mutating == []


def test_sync_updates_changed_params_only(tmp_path, fake_runner):
    db_file = tmp_path / "upd.db"

    def seed(session):
        return _seed_access(session)

    root = Path(__import__("os").environ["SHARE_MOUNT_PATH"])
    # Share exists but with stale access lists.
    fake_runner.set_response("net conf listshares", 0, "photos\n")
    fake_runner.set_response(
        "net conf showshare photos",
        0,
        "[photos]\n"
        f"\tpath = {root / 'photos'}\n"
        "\tcomment = NAS Manager\n"
        "\tforce user = service-user\n"
        "\tbrowseable = yes\n"
        "\tvalid users = bob\n"
        "\twrite list = bob\n"
        "\twide links = no\n",
    )

    report = _run(db_file, seed, lambda s: sync(s))

    assert report.added == []
    assert report.updated == ["photos"]
    assert sorted(report.params_set["photos"]) == ["read list", "valid users"]
    sets = fake_runner.find("net", "conf", "setparm", "photos", "valid users")
    assert len(sets) == 1
    assert sets[0]["args"][-1] == "alice bob"
