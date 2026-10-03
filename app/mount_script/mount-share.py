#!/usr/bin/env python3
"""nasmount — one-file NAS share mount helper for the simple-share server.

Stdlib only, no dependencies. Works on Linux, macOS and Windows.

Commands:
  auth, a      - log in and store tokens locally
  setup        - interactive wizard: configure, log in, and mount shares
  mount, m     - mount shares (names optional; --root PATH overrides the mount root)
  umount, u    - unmount mounts made to this NAS (names optional)
  status, s    - list shares on the server with mounted markers
  config, c    - show config, or set it: config --root PATH / config --url URL

Mount/umount accept specific share names as arguments, e.g.
"nasmount.py mount temp" or "nasmount.py umount movies"; with no names
every share from the server is used.

Config is stored at ~/.config/nasmanager/config.json (override with the
NASMOUNT_CONFIG_DIR environment variable); mounted shares are tracked in the
"mounts" file next to it.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_URL = "http://nas:8000"
DEFAULT_ROOT = "/mnt/nas"
HTTP_TIMEOUT = 10
CMD_TIMEOUT = 180


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, status: int, detail: str = ""):
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


class NotAuthenticated(Exception):
    pass


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

@dataclass
class Config:
    server_url: str = DEFAULT_URL
    mount_root: str = ""
    windows_mode: str = "drive"
    username: str = ""
    access_token: str = ""
    refresh_token: str = ""


def config_dir() -> Path:
    env = os.environ.get("NASMOUNT_CONFIG_DIR")
    if env:
        return Path(env)
    return Path.home() / ".config" / "nasmanager"


def config_file() -> Path:
    return config_dir() / "config.json"


def mounts_file() -> Path:
    return config_dir() / "mounts"


def load_config() -> Config:
    if not config_file().exists():
        return Config()
    try:
        data = json.loads(config_file().read_text())
    except Exception:
        return Config()
    fields = set(Config.__dataclass_fields__)
    return Config(**{k: v for k, v in data.items() if k in fields})


def save_config(cfg: Config) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    path = config_file()
    path.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def show_config(cfg: Config) -> None:
    print("Config:")
    print(f"  server_url:   {cfg.server_url}")
    print(f"  mount_root:   {cfg.mount_root or '(not set)'}")
    print(f"  username:     {cfg.username or '(not set)'}")
    print(f"  access_token: {'set' if cfg.access_token else 'unset'}")
    print(f"  refresh_token: {'set' if cfg.refresh_token else 'unset'}")


def load_mounts() -> list[tuple[str, str, str]]:
    """Return [(share, source_uri, target)] from the local mounts log."""
    if not mounts_file().exists():
        return []
    entries = []
    for line in mounts_file().read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 3:
            entries.append((parts[0], parts[1], parts[2]))
    return entries


def _write_mounts(entries: list[tuple[str, str, str]]) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    lines = ["# share  source_uri  target"]
    lines.extend(f"{share}  {source}  {target}" for share, source, target in entries)
    path = mounts_file()
    path.write_text("\n".join(lines) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def add_mount(share: str, source: str, target: str) -> None:
    entries = [e for e in load_mounts() if e[0] != share]
    entries.append((share, source, target))
    _write_mounts(entries)


def remove_mounts_where(pred) -> None:
    entries = [e for e in load_mounts() if not pred(*e)]
    _write_mounts(entries)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def host_from_url(url: str) -> str:
    if "://" not in url:
        url = "http://" + url
    return urllib.parse.urlparse(url).hostname or ""


def share_source(host: str, share: str) -> str:
    return f"//{host}/{share}"


def source_host(source: str) -> str:
    m = re.match(r"^[/\\]{1,2}(?:[^@/\\]+@)?([^/\\:]+)", source or "")
    return m.group(1) if m else ""


def source_share(source: str) -> str:
    m = re.match(r"^[/\\]{1,2}(?:[^@/\\]+@)?[^/\\:]+[/\\](.+)$", source or "")
    return m.group(1) if m else ""


def _norm_source(s: str) -> str:
    s = s.casefold().replace("\\", "/")
    s = re.sub(r"^//[^/@]+@", "//", s)
    return s.rstrip("/")


def _input(prompt: str = "") -> str:
    return input(prompt)


def _getpass(prompt: str = "Password: ") -> str:
    return getpass.getpass(prompt)


def _ask_sudo_password() -> str:
    return _getpass("[sudo] password for current user: ")


def _ask_smb_password(username: str, host: str) -> str:
    return _getpass(f"Password for NAS server \n({username}@{host}): ")
    # return _getpass(f"Password for {username}@{host}: ")


# ---------------------------------------------------------------------------
# HTTP / API (urllib only)
# ---------------------------------------------------------------------------

def _urlopen(req, timeout: int = HTTP_TIMEOUT):
    return urllib.request.urlopen(req, timeout=timeout)


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, body=None, headers=None, content_type: str | None = None):
        h = {"Accept": "application/json"}
        if content_type:
            h["Content-Type"] = content_type
        if headers:
            h.update(headers)
        req = urllib.request.Request(self.base_url + path, data=body, headers=h, method=method)
        try:
            with _urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                detail = json.loads(raw).get("detail", raw)
            except Exception:
                detail = raw or str(e.reason)
            raise ApiError(e.code, detail) from None
        except urllib.error.URLError as e:
            raise ApiError(0, str(e.reason)) from None
        except OSError as e:
            raise ApiError(0, str(e)) from None

    def login(self, username: str, password: str) -> dict:
        form = urllib.parse.urlencode({"username": username, "password": password}).encode()
        return self._request(
            "POST", "/api/v1/auth/token",
            body=form, content_type="application/x-www-form-urlencoded",
        )

    def refresh(self, refresh_token: str) -> dict:
        body = json.dumps({"refresh_token": refresh_token}).encode()
        return self._request("POST", "/api/v1/auth/refresh", body=body, content_type="application/json")

    def list_shares(self, access_token: str) -> list[dict]:
        return self._request(
            "GET", "/api/v1/users/me/shares",
            headers={"Authorization": f"Bearer {access_token}"},
        )


def get_valid_token(cfg: Config) -> str:
    """Return a working access token, refreshing or failing with NotAuthenticated."""
    api = ApiClient(cfg.server_url)
    if cfg.access_token:
        try:
            api.list_shares(cfg.access_token)
            return cfg.access_token
        except ApiError as e:
            if e.status != 401:
                raise
    if cfg.refresh_token:
        try:
            tokens = api.refresh(cfg.refresh_token)
            cfg.access_token = tokens["access_token"]
            cfg.refresh_token = tokens["refresh_token"]
            save_config(cfg)
            return cfg.access_token
        except ApiError:
            pass
    raise NotAuthenticated("Run 'auth' first")


# ---------------------------------------------------------------------------
# command execution (sudo-aware)
# ---------------------------------------------------------------------------

def _is_sudo_cmd(cmd: list[str]) -> bool:
    return cmd[:2] == ["sudo", "-S"]


def run_cmd(cmd: list[str], stdin_data: str | None = None) -> tuple[bool, str]:
    """Run a command. sudo -S commands get the sudo password when needed."""
    if _is_sudo_cmd(cmd) and stdin_data is None and sudo_needs_password():
        stdin_data = _ask_sudo_password() + "\n"
    try:
        p = subprocess.run(
            cmd, input=stdin_data, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=CMD_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, "command timed out"
    except OSError as e:
        return False, str(e)
    if p.returncode != 0:
        msg = (p.stderr or p.stdout or "").strip()
        return False, msg or f"exit code {p.returncode}"
    return True, ""


def sudo_needs_password() -> bool:
    try:
        r = subprocess.run(["sudo", "-n", "true"], capture_output=True)
        return r.returncode != 0
    except OSError:
        return True


def run_privileged(cmd: list[str], password: str | None = None) -> tuple[bool, str]:
    if password is None and sudo_needs_password():
        password = _ask_sudo_password()
    return run_cmd(["sudo", "-S"] + cmd, stdin_data=(password + "\n") if password else None)


def run_privileged_and_report(cmd: list[str]) -> bool:
    ok, msg = run_privileged(cmd)
    if not ok:
        print(f"  error: {' '.join(cmd)}: {msg}")
    return ok


def ensure_dir(path) -> tuple[bool, str]:
    return run_privileged(["mkdir", "-p", str(path)])


# ---------------------------------------------------------------------------
# system mount discovery
# ---------------------------------------------------------------------------

def _capture(cmd: list[str]) -> tuple[bool, str]:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
    except Exception:
        return False, ""
    return p.returncode == 0, p.stdout or ""


def _unescape_fs(s: str) -> str:
    return s.replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")


def parse_linux_mounts(text: str) -> list[tuple[str, str]]:
    """Parse /proc/mounts: return (source, mountpoint) for SMB mounts only."""
    result = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mountpoint, fstype = parts[0], parts[1], parts[2]
        if fstype in ("cifs", "smb3") or device.startswith("//"):
            result.append((_unescape_fs(device), _unescape_fs(mountpoint)))
    return result


def parse_macos_mounts(text: str) -> list[tuple[str, str]]:
    result = []
    for line in text.splitlines():
        m = re.match(r"^(\S+)\s+on\s+(.+?)\s+\((\w+)", line.strip())
        if not m:
            continue
        source, target, fstype = m.groups()
        if fstype == "smbfs":
            result.append((source, target))
    return result


def parse_net_use(text: str) -> list[tuple[str, str]]:
    """Parse 'net use' output: return (device, remote) pairs, device may be ''."""
    result = []
    for line in text.splitlines():
        tokens = line.split()
        for i, tok in enumerate(tokens):
            if tok.startswith("\\\\"):
                device = tokens[i - 1] if i > 0 and re.fullmatch(r"[A-Za-z]:", tokens[i - 1]) else ""
                result.append((device, tok))
                break
    return result


def system_mounts() -> list[tuple[str, str]]:
    plat = platform.system()
    if plat == "Linux":
        try:
            text = Path("/proc/mounts").read_text(errors="replace")
        except OSError:
            return []
        return parse_linux_mounts(text)
    if plat == "Darwin":
        ok, out = _capture(["mount"])
        return parse_macos_mounts(out) if ok else []
    if plat == "Windows":
        ok, out = _capture(["net", "use"])
        return parse_net_use(out) if ok else []
    return []


def is_mounted(host: str, share: str, mounts: list[tuple[str, str]]) -> bool:
    needle = _norm_source(share_source(host, share))
    return any(_norm_source(src) == needle for src, _ in mounts)


# ---------------------------------------------------------------------------
# mount / umount planning
# ---------------------------------------------------------------------------

def mount_plan(
    host: str, share: str, port: int, username: str,
    mount_root: str, password: str, platform: str | None = None,
) -> tuple[list[str], str, bool]:
    if platform is None:
        platform = sys.platform
    if platform.startswith("win"):
        return _plan_mount_windows(host, share, username, password)
    if platform == "darwin":
        return _plan_mount_darwin(host, share, port, username, mount_root, password)
    return _plan_mount_linux(host, share, port, username, mount_root, password)


def _plan_mount_linux(
    host: str, share: str, port: int, username: str, mount_root: str, password: str,
) -> tuple[list[str], str, bool]:
    target = os.path.join(mount_root, share)
    source = share_source(host, share)
    opts = (
        f"username={username},password={password},port={port},"
        f"uid={os.getuid()},gid={os.getgid()},"
        f"dir_mode=0755,file_mode=0644,"
        f"vers=3.1.1,rsize=1048576,wsize=1048576,"
        f"cache=strict,noserverino"
    )
    cmd = [
        "sudo",
        "-S",
        "mount",
        "-t", "cifs",
        source, target,
        "-o", opts]
    return cmd, target, True


def _plan_mount_darwin(
    host: str, share: str, port: int, username: str, mount_root: str, password: str,
) -> tuple[list[str], str, bool]:
    target = os.path.join(mount_root, share)
    user = urllib.parse.quote(username, safe="")
    passw = urllib.parse.quote(password, safe="")
    url = f"//{user}:{passw}@{host}:{port}/{share}"
    cmd = ["sudo", "-S", "mount_smbfs", url, target]
    return cmd, target, True


def _plan_mount_windows(
    host: str, share: str, username: str, password: str,
) -> tuple[list[str], str, bool]:
    source = f"\\\\{host}\\{share}"
    cmd = ["net", "use", "*", source, password, "/user:" + username]
    return cmd, source, False


def umount_plan(source: str, target: str, platform: str | None = None) -> tuple[list[str], str, bool]:
    if platform is None:
        platform = sys.platform
    if platform.startswith("win"):
        what = source or target
        return ["net", "use", what, "/delete"], what, False
    return ["sudo", "-S", "umount", target], target, True


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_auth(cfg: Config, args: argparse.Namespace) -> int:
    if not cfg.server_url:
        cfg.server_url = _input(f"Server URL (default {DEFAULT_URL}): ").strip() or DEFAULT_URL
        save_config(cfg)
    username = _input("Username: ").strip()
    if not username:
        print("Username required")
        return 1
    password = _getpass("Password: ")
    try:
        tokens = ApiClient(cfg.server_url).login(username, password)
    except ApiError as e:
        print(f"Login failed: {e}")
        return 1
    cfg.username = username
    cfg.access_token = tokens["access_token"]
    cfg.refresh_token = tokens["refresh_token"]
    save_config(cfg)
    print("Authentication succeeded")
    return 0


def cmd_setup(cfg: Config, args: argparse.Namespace) -> int:
    url_default = cfg.server_url or DEFAULT_URL
    url = _input(f"Server URL (default {url_default}): ").strip() or url_default
    url = url.rstrip("/")

    if cfg.username:
        username = _input(f"Username (default {cfg.username}): ").strip() or cfg.username
    else:
        username = _input("Username: ").strip()
    if not username:
        print("Username required")
        return 1

    password = _getpass("Password: ")
    try:
        tokens = ApiClient(url).login(username, password)
    except ApiError as e:
        print(f"Login failed: {e}")
        return 1

    cfg.server_url = url
    cfg.username = username
    cfg.access_token = tokens["access_token"]
    cfg.refresh_token = tokens["refresh_token"]

    root_default = cfg.mount_root or DEFAULT_ROOT
    root = _input(f"Mount root path (default {root_default}): ").strip() or root_default
    cfg.mount_root = root
    save_config(cfg)

    try:
        shares = ApiClient(cfg.server_url).list_shares(cfg.access_token)
    except ApiError as e:
        print(f"API error: {e}")
        return 1

    if not shares:
        print("No shares available")
        return 0

    print("Available shares:")
    print(f"{'Share':<24} {'Access':<8} Host")
    print('-' * 60)
    for share in sorted(shares, key=lambda s: s["name"]):
        name, host, port, access = share["name"], share["host"], share["port"], share["access"]
        print(f"{name:<24} {access:<8} {host}:{port}")
    print('-' * 60)

    answer = _input(f"Mount all {len(shares)} shares? [Y/n]: ").strip().lower()
    if answer not in ("", "y", "yes"):
        print("Skipped mounting")
        return 0

    return 1 if _mount_shares(cfg, shares, root) else 0


def _mount_shares(cfg: Config, shares: list[dict], root: str) -> int:
    """Mount the given shares under root. Returns the number of failures."""
    if not shares:
        print("No shares available")
        return 0

    nas_host = shares[0].get("host") or host_from_url(cfg.server_url)
    smb_password = _ask_smb_password(cfg.username, nas_host)

    if not sys.platform.startswith("win"):
        ok, msg = ensure_dir(root)
        if not ok:
            print(f"Could not create mount root {root}: {msg}")
            return 1

    errors = 0
    for share in sorted(shares, key=lambda s: s["name"]):
        name, host, port = share["name"], share["host"], share["port"]
        cmd, target, needs_sudo = mount_plan(host, name, port, cfg.username, root, smb_password)
        if needs_sudo:
            ok, msg = ensure_dir(target)
            if not ok:
                errors += 1
                print(f"error mounting {name}: {msg}")
                continue
        print(f"  $ {' '.join(_redact_cmd(cmd))}")     # <-- печатаем команду (с маскировкой пароля)
        ok, msg = run_cmd(cmd)
        if ok:
            add_mount(name, share_source(host, name), target)
            print(f"mounted {name} -> {target}")
        else:
            errors += 1
            print(f"error mounting {name}: {msg}")
    return errors


def _redact_cmd(cmd: list[str]) -> list[str]:
    """Hide the password value in a command line for printing."""
    out = []
    redact_next = False
    for tok in cmd:
        if redact_next:
            out.append("****")
            redact_next = False
            continue
        if tok.startswith("password="):
            out.append("password=****")
            continue
        out.append(tok)
    return out


def cmd_mount(cfg: Config, args: argparse.Namespace) -> int:
    root_arg = getattr(args, "root", None)
    if root_arg:
        cfg.mount_root = root_arg
        save_config(cfg)

    if not (cfg.access_token or cfg.refresh_token):
        print("Not authenticated. Run 'auth' first.")
        return 1

    root = cfg.mount_root
    if not root:
        answer = _input(f"Mount root path (default {DEFAULT_ROOT}): ").strip()
        root = answer or DEFAULT_ROOT
        cfg.mount_root = root
        save_config(cfg)

    try:
        token = get_valid_token(cfg)
        shares = ApiClient(cfg.server_url).list_shares(token)
    except NotAuthenticated as e:
        print(f"Not authenticated: {e}")
        return 1
    except ApiError as e:
        print(f"API error: {e}")
        return 1

    not_found: list[str] = []
    names = getattr(args, "names", None) or []
    if names:
        by_name = {s["name"]: s for s in shares}
        picked = []
        for n in names:
            if n in by_name:
                picked.append(by_name[n])
            else:
                not_found.append(n)
                print(f"error: share '{n}' not found on server")
        shares = picked

    if not shares:
        if not_found:
            return 1
        print("No shares available")
        return 0

    errors = _mount_shares(cfg, shares, root)
    return 1 if (errors or not_found) else 0


def cmd_umount(cfg: Config, args: argparse.Namespace) -> int:
    hosts: set[str] = set()
    host = host_from_url(cfg.server_url)
    if host:
        hosts.add(host)

    if cfg.access_token or cfg.refresh_token:
        try:
            token = get_valid_token(cfg)
            shares = ApiClient(cfg.server_url).list_shares(token)
            hosts.update(s["host"] for s in shares)
        except (NotAuthenticated, ApiError):
            pass

    if not hosts:
        print("No NAS host configured")
        return 1

    names = getattr(args, "names", None) or []
    mounts = system_mounts()
    ours = [(src, tgt) for src, tgt in mounts if source_host(src) in hosts]
    if names:
        chosen = set(names)
        ours = [(src, tgt) for src, tgt in ours if source_share(src) in chosen]
    if not ours:
        suffix = " for " + ", ".join(names) if names else " from this NAS"
        print(f"No mounts{suffix} found")
        return 0

    errors = []
    for src, tgt in ours:
        cmd, what, _ = umount_plan(src, tgt)
        ok, msg = run_cmd(cmd)
        if ok:
            print(f"unmounted {src} from {tgt}")
        else:
            errors.append(f"{what}: {msg}")
            print(f"error unmounting {what}: {msg}")

    remove_mounts_where(
        lambda share, source, target: source_host(source) in hosts
        and (not names or share in set(names))
    )
    return 1 if errors else 0


def cmd_status(cfg: Config, args: argparse.Namespace) -> int:
    if not (cfg.access_token or cfg.refresh_token):
        print("Not authenticated. Run 'auth' first.")
        return 1

    try:
        token = get_valid_token(cfg)
        shares = ApiClient(cfg.server_url).list_shares(token)
    except NotAuthenticated as e:
        print(f"Not authenticated: {e}")
        return 1
    except ApiError as e:
        print(f"API error: {e}")
        return 1

    mounts = system_mounts()
    if not shares:
        print("No shares available")
        return 0

    print(f"{'Share':<24} {'Access':<8} {'Host':<24} {'Status'}")
    print('-'*70)
    for share in sorted(shares, key=lambda s: s["name"]):
        name, host, port, access = share["name"], share["host"], share["port"], share["access"]
        status = "[mounted]" if is_mounted(host, name, mounts) else "[not mounted]"
        print(f"{name:<24} {access:<8} {f'{host}:{port}':<24} {status}")
    print('-' * 70)
    return 0


def cmd_config(cfg: Config, args: argparse.Namespace) -> int:
    changed = False
    root = getattr(args, "root", None)
    if root:
        cfg.mount_root = root
        changed = True
    url = getattr(args, "url", None)
    if url:
        cfg.server_url = url.rstrip("/")
        changed = True
    if changed:
        save_config(cfg)
    show_config(cfg)
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="nasmount",
        description="Mount NAS shares from the simple-share server.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    pa = sub.add_parser("auth", aliases=["a"], help="log in and store tokens")
    pa.set_defaults(func=cmd_auth)

    pset = sub.add_parser("setup", help="interactive wizard: configure, log in, and mount")
    pset.set_defaults(func=cmd_setup)

    pm = sub.add_parser("mount", aliases=["m"], help="mount shares")
    pm.add_argument("--root", metavar="PATH", help="mount root directory (else config or prompt)")
    pm.add_argument("names", metavar="NAME", nargs="*", help="share names to mount (default: all)")
    pm.set_defaults(func=cmd_mount)

    pu = sub.add_parser("umount", aliases=["u"], help="unmount shares from this NAS")
    pu.add_argument("names", metavar="NAME", nargs="*", help="share names to unmount (default: all)")
    pu.set_defaults(func=cmd_umount)

    ps = sub.add_parser("status", aliases=["s"], help="list shares with mounted markers")
    ps.set_defaults(func=cmd_status)

    pc = sub.add_parser("config", aliases=["c"], help="show or change config")
    pc.add_argument("--root", metavar="PATH", help="set mount root")
    pc.add_argument("--url", metavar="URL", help="set server URL")
    pc.set_defaults(func=cmd_config)

    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parse_args(["--help"])
        return 1
    return func(load_config(), args) or 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)