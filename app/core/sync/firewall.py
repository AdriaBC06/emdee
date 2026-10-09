# SPDX-License-Identifier: GPL-3.0-or-later
"""Open the listening port in the local firewall, when one is blocking it.

Linux desktops often run firewalld or ufw, which reject incoming connections
by default: the other device then sees "No route to host" even though both are
on the same network.  :func:`ensure_port_open` checks whether the port is
already allowed and, if not, asks for the administrator password through
``pkexec`` (polkit's graphical or terminal prompt) to allow it.

* firewalld: the port is added to the default zone at runtime only, so it is
  gone after a reboot or ``firewall-cmd --reload``;
* ufw: rules cannot be read without root, so ports Emdee opened are
  remembered in the sync config folder to avoid asking every time.

Nothing here raises: the result is a line for the log, and the listener keeps
running either way — the connection may still work (another tool may manage
the firewall), and the message says what to run by hand if it does not.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

from ...platform_support import IS_LINUX
from .store import config_dir, write_private

log = logging.getLogger(__name__)

__all__ = ["ensure_port_open"]

_STATE_FILE = "firewall.json"
#: Seconds to wait for a quick query, and for the user to answer the password prompt.
_QUERY_TIMEOUT = 10
_PROMPT_TIMEOUT = 120


def _run(*args: str, timeout: float = _QUERY_TIMEOUT) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("%s failed: %s", args[0], exc)
        return None


def _service_active(name: str) -> bool:
    if shutil.which("systemctl") is None:
        return False
    result = _run("systemctl", "is-active", "--quiet", name)
    return result is not None and result.returncode == 0


def _elevated(*args: str) -> bool:
    if shutil.which("pkexec") is None:
        return False
    result = _run("pkexec", *args, timeout=_PROMPT_TIMEOUT)
    return result is not None and result.returncode == 0


def _firewalld_running() -> bool:
    if shutil.which("firewall-cmd") is None:
        return False
    state = _run("firewall-cmd", "--state")
    return state is not None and state.returncode == 0


def _firewalld(port: int) -> str | None:
    spec = f"{port}/tcp"
    query = _run("firewall-cmd", f"--query-port={spec}")
    if query is not None and query.returncode == 0:
        return None
    if _elevated("firewall-cmd", f"--add-port={spec}"):
        return f"Opened port {port} in the firewall (firewalld) until the next reboot."
    return (
        f"The firewall (firewalld) is blocking port {port} and it could not be opened "
        f"automatically; other devices will not reach this one. "
        f"Run: sudo firewall-cmd --add-port={spec}"
    )


def _ufw_remembered() -> set[int]:
    try:
        data = json.loads((config_dir() / _STATE_FILE).read_text(encoding="utf-8"))
        return {p for p in data.get("ufw", []) if isinstance(p, int)}
    except (OSError, ValueError, AttributeError):
        return set()


def _ufw_running() -> bool:
    return shutil.which("ufw") is not None and _service_active("ufw")


def _ufw(port: int) -> str | None:
    remembered = _ufw_remembered()
    if port in remembered:
        return None
    if _elevated("ufw", "allow", f"{port}/tcp"):
        try:
            payload = json.dumps({"ufw": sorted(remembered | {port})}).encode()
            write_private(config_dir() / _STATE_FILE, payload)
        except OSError:  # pragma: no cover - only means asking again next time
            log.warning("could not remember the ufw rule for port %s", port)
        return f"Opened port {port} in the firewall (ufw)."
    return (
        f"The firewall (ufw) may be blocking port {port} and it could not be opened "
        f"automatically; other devices may not reach this one. Run: sudo ufw allow {port}/tcp"
    )


def ensure_port_open(port: int) -> str | None:
    """Allow ``port`` through the local firewall if needed.

    Returns a message for the log, or None when there was nothing to do.
    """
    if not IS_LINUX:
        return None
    if _firewalld_running():
        return _firewalld(port)
    if _ufw_running():
        return _ufw(port)
    return None
