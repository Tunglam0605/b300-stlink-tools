"""Operator-first CLI orchestration for B300 Remote Gateway/Client setup."""

from __future__ import annotations

from typing import Optional

from b300_core.gateway_setup import (
    build_gateway_prepare_plan, client_connection_text, inspect_gateway_host, prepare_gateway_host,
)
from b300_core.remote_connectivity import check_remote_connectivity
from b300_core.remote_profile import (
    RemoteGatewayProfile, clear_remote_profile, default_remote_profile_path, load_remote_profile, save_remote_profile,
)
from b300_core.ssh_host_trust import (
    local_gateway_host_key, scan_gateway_host_key, trust_gateway_host_key, trusted_known_hosts_file,
)
from b300_core.ssh_identity import (
    ensure_ssh_identity, inspect_ssh_client_prerequisites, inspect_ssh_identity,
    prepare_ssh_client_prerequisites, public_key_identity,
)

def gateway_host_record(report, command: str, plan=None) -> dict:
    record = {
        "schema_version": 1,
        "command": command,
        "status": "ok" if report.ready else "blocked",
        "conclusion": report.conclusion,
        "ready": report.ready,
        "platform": report.platform,
        "ssh_port": report.ssh_port,
        "username": report.username,
        "hostname": report.hostname,
        "ipv4_addresses": list(report.ipv4_addresses),
        "security": {
            "debug_ports_private": report.debug_ports_private,
            "openocd_public_ports": [],
            "lan_ingress": [report.ssh_port],
        },
        "checks": [
            {
                "name": item.name, "status": item.status, "code": item.code,
                "message": item.message, "next_action": item.next_action,
            }
            for item in report.checks
        ],
        "client_configuration": client_connection_text(report),
    }
    if plan is not None:
        record["plan"] = {
            "actions": list(plan.actions),
            "changes_required": plan.changes_required,
            "requires_elevation": plan.requires_elevation,
        }
    return record


def apply_saved_remote_profile(args) -> Optional[RemoteGatewayProfile]:
    """Fill omitted remote endpoint fields from the non-secret managed profile."""
    if getattr(args, "ssh_host", None) and getattr(args, "ssh_user", None):
        return None
    profile = load_remote_profile()
    if profile is None:
        return None
    explicit_host = getattr(args, "ssh_host", None)
    if not explicit_host:
        args.ssh_host = profile.host
        args.ssh_user = getattr(args, "ssh_user", None) or profile.user
        args.ssh_port = profile.port
        return profile
    if explicit_host == profile.host and not getattr(args, "ssh_user", None):
        args.ssh_user = profile.user
        if getattr(args, "ssh_port", 22) == 22 and profile.port != 22:
            args.ssh_port = profile.port
        return profile
    return None


def client_authorize_command(public_key: str) -> str:
    # Drop the optional OpenSSH comment so the copy/paste command contains only
    # canonical key type + base64 public material and no shell-sensitive comment.
    normalized = public_key_identity(public_key)
    return 'b300-stlink gateway authorize-key --public-key "%s" --confirm-system-change' % normalized


def gateway_quickstart(args) -> tuple[int, dict, str]:
    before = inspect_gateway_host(ssh_port=args.ssh_port)
    plan = build_gateway_prepare_plan(before)
    if plan.changes_required and not args.confirm_system_change:
        record = gateway_host_record(before, "gateway quickstart", plan)
        record.update({
            "status": "confirmation_required",
            "reason_code": "SYSTEM_CHANGE_CONFIRMATION_REQUIRED",
            "next_action": "Review planned SSH service/firewall changes, then re-run quickstart with --confirm-system-change.",
        })
        text = client_connection_text(before)
        text += "\nPlanned actions: %s" % ", ".join(plan.actions)
        text += "\nNo system changes were made."
        return 1, record, text
    if plan.changes_required:
        prepared = prepare_gateway_host(ssh_port=args.ssh_port)
        after = prepared.after
        changed = prepared.changed
        if not prepared.succeeded or not after.ready:
            record = gateway_host_record(after, "gateway quickstart", prepared.plan)
            record.update({"status": "blocked", "changed": changed, "succeeded": False})
            return 1, record, client_connection_text(after) + "\nGateway Quickstart: BLOCKED"
    else:
        after = before
        changed = False
    host_key = local_gateway_host_key()
    hosts = tuple(after.ipv4_addresses) or (after.hostname,)
    connections = [RemoteGatewayProfile(host, after.username, after.ssh_port).validate() for host in hosts]
    client_commands = [
        (
            'b300-stlink gateway client-setup --ssh-host "%s" --ssh-user "%s" --ssh-port %d '
            '--confirm-host-fingerprint "%s"' %
            (connection.host, connection.user, connection.port, host_key.fingerprint)
        )
        for connection in connections
    ]
    ambiguous = len(client_commands) > 1
    record = gateway_host_record(after, "gateway quickstart", plan)
    record.update({
        "status": "ok",
        "ready": True,
        "changed": changed,
        "host_key_fingerprint": host_key.fingerprint,
        "client_setup_command": client_commands[0] if not ambiguous else None,
        "client_setup_commands": client_commands,
        "network_selection_required": ambiguous,
        "next_action": (
            "Choose the client_setup_commands entry reachable from the Client network, then authorize only its public key on this Gateway."
            if ambiguous else
            "Run client_setup_command on the Client laptop, then authorize only its public key on this Gateway."
        ),
    })
    if ambiguous:
        command_text = "\n".join("  %d. %s" % (index + 1, command) for index, command in enumerate(client_commands))
        ssh_text = "multiple network adapters: %s" % ", ".join(hosts)
    else:
        command_text = client_commands[0]
        ssh_text = "%s@%s:%d" % (after.username, hosts[0], after.ssh_port)
    text = (
        "B300 GATEWAY QUICKSTART READY\n"
        "SSH: %s\n"
        "Gateway fingerprint: %s\n"
        "Run on Client%s:\n%s\n"
        "Debug ports 3333/4444/6666 remain loopback-only."
    ) % (ssh_text, host_key.fingerprint, " (choose the reachable network candidate)" if ambiguous else "", command_text)
    return 0, record, text


def gateway_client_setup(args) -> tuple[int, dict, str]:
    if not args.ssh_host or not args.ssh_user:
        raise ValueError("gateway client-setup requires --ssh-host HOST and --ssh-user USER.")
    profile = RemoteGatewayProfile(args.ssh_host, args.ssh_user, args.ssh_port).validate()
    prereq = inspect_ssh_client_prerequisites()
    system_changed = False
    if not prereq.ready:
        if not args.confirm_system_change:
            record = {
                "schema_version": 1,
                "command": "gateway client-setup",
                "status": "confirmation_required",
                "reason_code": "SYSTEM_CHANGE_CONFIRMATION_REQUIRED",
                "actions": list(prereq.actions),
                "profile": profile.record(),
                "private_key_exported": False,
                "next_action": "Re-run with --confirm-system-change to install OpenSSH Client, then continue setup.",
            }
            return 1, record, "SYSTEM_CHANGE_CONFIRMATION_REQUIRED: OpenSSH Client is missing; no OS/key/trust/profile change made."
        prepared = prepare_ssh_client_prerequisites()
        if not prepared.succeeded or not prepared.after.ready:
            raise RuntimeError("OpenSSH Client setup did not reach READY state.")
        system_changed = prepared.changed
    identity = ensure_ssh_identity(args.identity_file)
    scanned = scan_gateway_host_key(profile.host, profile.port)
    authorize_command = client_authorize_command(identity.public_key_text or "")
    if not args.confirm_host_fingerprint:
        record = {
            "schema_version": 1,
            "command": "gateway client-setup",
            "status": "confirmation_required",
            "reason_code": "HOST_KEY_FINGERPRINT_CONFIRMATION_REQUIRED",
            "profile": profile.record(),
            "scanned_fingerprint": scanned.fingerprint,
            "client_key_fingerprint": identity.fingerprint,
            "public_key": identity.public_key_text,
            "authorize_command": authorize_command,
            "private_key_exported": False,
            "profile_saved": False,
            "next_action": "Compare scanned_fingerprint with `gateway host-key` on the physical Gateway, then re-run with --confirm-host-fingerprint.",
        }
        text = (
            "HOST_KEY_FINGERPRINT_CONFIRMATION_REQUIRED\n"
            "Scanned Gateway fingerprint: %s\n"
            "Compare it on the physical Gateway before trusting.\n"
            "Public key authorization command for Gateway:\n%s"
        ) % (scanned.fingerprint, authorize_command)
        return 1, record, text
    confirmed = args.confirm_host_fingerprint.strip()
    if confirmed != scanned.fingerprint:
        record = {
            "schema_version": 1,
            "command": "gateway client-setup",
            "status": "blocked",
            "reason_code": "HOST_KEY_FINGERPRINT_MISMATCH",
            "profile": profile.record(),
            "scanned_fingerprint": scanned.fingerprint,
            "confirmed_fingerprint": confirmed,
            "private_key_exported": False,
            "profile_saved": False,
        }
        return 1, record, "HOST_KEY_FINGERPRINT_MISMATCH: refusing trust/profile enrollment."
    trusted = trust_gateway_host_key(scanned)
    profile_path = save_remote_profile(profile)
    record = {
        "schema_version": 1,
        "command": "gateway client-setup",
        "status": "ok",
        "profile": profile.record(),
        "profile_path": str(profile_path),
        "profile_saved": True,
        "system_changed": system_changed,
        "client_key_fingerprint": identity.fingerprint,
        "public_key": identity.public_key_text,
        "private_key_path": str(identity.private_key),
        "private_key_exported": False,
        "gateway_fingerprint": trusted.fingerprint,
        "known_hosts_file": str(trusted.known_hosts_file),
        "strict_host_key_checking": True,
        "authorize_command": authorize_command,
        "connect_check_command": "b300-stlink gateway connect-check",
        "next_action": "Run authorize_command on the Gateway, then run connect_check_command on this Client.",
    }
    text = (
        "B300 CLIENT SETUP READY\n"
        "Saved Gateway: %s@%s:%d\n"
        "Gateway fingerprint verified: %s\n"
        "Client key fingerprint: %s\n"
        "Run on Gateway:\n%s\n"
        "Then on Client:\nb300-stlink gateway connect-check"
    ) % (profile.user, profile.host, profile.port, trusted.fingerprint, identity.fingerprint, authorize_command)
    return 0, record, text


def gateway_status() -> tuple[int, dict, str]:
    prereq = inspect_ssh_client_prerequisites()
    identity = inspect_ssh_identity()
    profile = load_remote_profile()
    known = trusted_known_hosts_file(profile.host, profile.port) if profile is not None else None
    local_ready = bool(prereq.ready and identity.ready and profile is not None and known is not None)
    record = {
        "schema_version": 1,
        "command": "gateway status",
        "status": "local_ready" if local_ready else "setup_required",
        "ready": local_ready,
        "local_ready": local_ready,
        "connectivity_verified": False,
        "profile": profile.record() if profile is not None else None,
        "profile_path": str(default_remote_profile_path()),
        "openssh_client_ready": prereq.ready,
        "identity_ready": identity.ready,
        "identity_fingerprint": identity.fingerprint,
        "private_key_exported": False,
        "host_trust_ready": known is not None,
        "known_hosts_file": str(known) if known is not None else None,
        "next_action": "Run `gateway connect-check` to verify real SSH authorization/connectivity." if local_ready else "Run `gateway client-setup --ssh-host HOST --ssh-user USER`.",
    }
    text = (
        "B300 REMOTE CLIENT %s\nProfile: %s\nOpenSSH Client: %s\nClient key: %s\nHost trust: %s\nConnectivity: NOT VERIFIED (run gateway connect-check)"
        % (
            "LOCAL SETUP READY" if local_ready else "SETUP REQUIRED",
            ("%s@%s:%d" % (profile.user, profile.host, profile.port)) if profile else "not configured",
            "READY" if prereq.ready else "MISSING",
            "READY" if identity.ready else "MISSING",
            "READY" if known is not None else "MISSING",
        )
    )
    return (0 if local_ready else 1), record, text


def gateway_connect_check(args) -> tuple[int, dict, str]:
    profile = None
    if args.ssh_host or args.ssh_user:
        if not args.ssh_host or not args.ssh_user:
            raise ValueError("connect-check requires both --ssh-host and --ssh-user when overriding the saved profile.")
        profile = RemoteGatewayProfile(args.ssh_host, args.ssh_user, args.ssh_port).validate()
    else:
        profile = load_remote_profile()
    if profile is None:
        raise ValueError("No saved B300 Gateway profile. Run `gateway client-setup` first.")
    result = check_remote_connectivity(profile)
    record = {
        "schema_version": 1,
        "command": "gateway connect-check",
        "status": "ok" if result.ready else "blocked",
        "ready": result.ready,
        "reason_code": result.reason_code,
        "gateway": result.gateway,
        "message": result.message,
        "strict_host_key_checking": True,
        "password_authentication": False,
        "debug_ports_exposed": False,
    }
    text = "%s: %s\n%s" % (result.reason_code, result.gateway, result.message)
    return (0 if result.ready else 1), record, text
