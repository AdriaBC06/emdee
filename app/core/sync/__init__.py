# SPDX-License-Identifier: GPL-3.0-or-later
"""Share and synchronise vaults between devices on the same local network.

* :mod:`.store` — this device's key pair and the contacts it trusts;
* :mod:`.channel` — the code-authenticated, end-to-end encrypted connection;
* :mod:`.files` — manifests, the sync plan and safe writes into a vault;
* :mod:`.session` — the listener and the pair/pull/push/sync operations.

No Qt here: ``emdee sync`` and the window use exactly the same code.
"""
