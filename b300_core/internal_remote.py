"""Low-friction RemoteSession factory for trusted internal B300 engineering networks."""

from __future__ import annotations

from typing import Optional

from .remote_profile import RemoteGatewayProfile
from .remote_session import CredentialStore, RemoteSession


def _internal_ssh_client_factory():
    """Create an embedded SSH client without system known_hosts dependency.

    B300 v0.15 is an internal engineering tool. The normal operator flow therefore
    accepts the Gateway host key for the lifetime of the embedded client and does not
    read, write, prompt for, or validate ~/.ssh/known_hosts. Authentication still uses
    the explicit Gateway username/password supplied by the operator.

    Advanced/legacy callers that require host-key validation can continue constructing
    ``RemoteSession`` with their own SSH client factory/policy.
    """
    try:
        import paramiko
    except ImportError as error:
        raise RuntimeError(
            "Embedded SSH runtime is unavailable. Install the packaged Paramiko dependency."
        ) from error

    client = paramiko.SSHClient()
    # Deliberately do NOT call load_system_host_keys()/load_host_keys(). With an empty
    # host-key set every Gateway key is handled as first-contact by AutoAddPolicy and is
    # retained only in this in-process client object.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    return client


def create_internal_remote_session(
    profile: RemoteGatewayProfile,
    *,
    credential_store: Optional[CredentialStore] = None,
    keepalive_seconds: int = 15,
) -> RemoteSession:
    """Create the default v0.15 Client RemoteSession for a trusted internal LAN."""
    return RemoteSession(
        profile,
        credential_store=credential_store,
        ssh_client_factory=_internal_ssh_client_factory,
        keepalive_seconds=keepalive_seconds,
    )
