import asyncio
import sqlite3


def _query(db_path: str, sql: str):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(sql).fetchall()
    conn.close()
    return rows


def create_user(client, auth, username="alice", password="secret123", **extra):
    return client.post(
        "/api/v1/users",
        json={"username": username, "password": password, **extra},
        headers=auth,
    )


def test_create_user_happy_path(client, auth, fake_runner):
    response = create_user(client, auth)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["username"] == "alice"
    assert body["is_admin"] is False
    assert body["id"]
    assert body["created_at"]

    assert fake_runner.find("useradd", "-M", "-s", "/usr/sbin/nologin", "alice")
    assert fake_runner.find("smbpasswd", "-s", "-a", "alice")


def test_personal_group_auto_created(client, auth, fake_runner, db_path):
    response = create_user(client, auth)
    user_id = response.json()["id"]

    groups = _query(db_path, "SELECT name, is_personal FROM groups WHERE name='alice'")
    assert groups == [("alice", 1)]

    memberships = _query(
        db_path,
        f"SELECT access_level FROM user_groups WHERE user_id='{user_id}'",
    )
    assert memberships == [("rw",)]


def test_invalid_usernames_rejected(client, auth):
    for bad in ["Alice", "a", "-bob", "bo!b", "1digit"]:
        response = create_user(client, auth, username=bad)
        assert response.status_code == 422, f"{bad!r} should be rejected: {response.text}"


def test_duplicate_username_conflict(client, auth):
    assert create_user(client, auth).status_code == 201
    second = create_user(client, auth)
    assert second.status_code == 409


def test_list_and_get_users(client, auth, fake_runner):
    created = create_user(client, auth).json()

    listed = client.get("/api/v1/users", headers=auth)
    assert listed.status_code == 200
    names = {u["username"] for u in listed.json()}
    assert {"admin", "alice"} <= names

    fetched = client.get(f"/api/v1/users/{created['id']}", headers=auth)
    assert fetched.status_code == 200
    assert fetched.json()["username"] == "alice"

    missing = client.get("/api/v1/users/does-not-exist", headers=auth)
    assert missing.status_code == 404


def test_change_password(client, auth, fake_runner, db_path):
    from app.core.security import verify_password

    created = create_user(client, auth).json()
    old_hash = _query(db_path, f"SELECT password_hash FROM users WHERE id='{created['id']}'")[0][0]

    response = client.post(
        f"/api/v1/users/{created['id']}/password",
        json={"new_password": "brand-new-pw"},
        headers=auth,
    )
    assert response.status_code == 200, response.text

    new_hash = _query(db_path, f"SELECT password_hash FROM users WHERE id='{created['id']}'")[0][0]
    assert new_hash != old_hash
    assert asyncio.run(verify_password("brand-new-pw", new_hash)) is True

    delete_calls = fake_runner.find("smbpasswd", "-x", "alice")
    add_calls = fake_runner.find("smbpasswd", "-s", "-a", "alice")
    assert len(delete_calls) == 1
    assert len(add_calls) == 2
    assert add_calls[-1]["stdin"] == "brand-new-pw\nbrand-new-pw\n"


def test_delete_user_cascade(client, auth, fake_runner, db_path):
    created = create_user(client, auth).json()

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO shares (id, name, path, wide_links)"
        " VALUES ('s1', 'photos', 'photos', 0)"
    )
    conn.execute(
        "INSERT INTO group_shares (group_id, share_id) "
        "SELECT id, 's1' FROM groups WHERE name='alice'"
    )
    conn.execute("INSERT INTO groups (id, name, is_personal) VALUES ('g2', 'team', 0)")
    conn.execute(
        "INSERT INTO user_groups (user_id, group_id, access_level) "
        f"VALUES ('{created['id']}', 'g2', 'ro')"
    )
    conn.commit()
    conn.close()

    response = client.delete(f"/api/v1/users/{created['id']}", headers=auth)
    assert response.status_code == 204, response.text

    assert _query(db_path, f"SELECT id FROM users WHERE id='{created['id']}'") == []
    assert _query(db_path, "SELECT id FROM groups WHERE name='alice'") == []
    assert _query(db_path, "SELECT * FROM group_shares") == []
    assert _query(db_path, "SELECT * FROM user_groups") == []

    assert fake_runner.find("userdel", "alice")
    assert fake_runner.find("smbpasswd", "-x", "alice")
    assert not any("rm" in c["args"][0] for c in fake_runner.calls)


def test_last_admin_protection(client, auth):
    admin_id = next(u["id"] for u in client.get("/api/v1/users", headers=auth).json() if u["is_admin"])

    blocked = client.delete(f"/api/v1/users/{admin_id}", headers=auth)
    assert blocked.status_code == 409

    second = create_user(client, auth, username="admin2", is_admin=True)
    assert second.status_code == 201

    allowed = client.delete(f"/api/v1/users/{admin_id}", headers=auth)
    assert allowed.status_code == 204
