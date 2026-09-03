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
from b300_core.ssh_identity import (
    inspect_ssh_client_prerequisites,
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


def connect_check_command() -> str:
    return "b300-stlink gateway connect-check"


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
    hosts = tuple(after.ipv4_addresses) or (after.hostname,)
    connections = [RemoteGatewayProfile(host, after.username, after.ssh_port).validate() for host in hosts]
    client_commands = [
        (
            'b300-stlink gateway client-setup --ssh-host "%s" --ssh-user "%s" --ssh-port %d' %
            (connection.host, connection.user, connection.port)
        )
        for connection in connections
    ]
    ambiguous = len(client_commands) > 1
    record = gateway_host_record(after, "gateway quickstart", plan)
    record.update({
        "status": "ok",
        "ready": True,
        "changed": changed,
        "client_setup_command": client_commands[0] if not ambiguous else None,
        "client_setup_commands": client_commands,
        "network_selection_required": ambiguous,
        "next_action": (
            "Choose the client_setup_commands entry reachable from the Client network, then use the account password when OpenSSH prompts."
            if ambiguous else
            "Run client_setup_command on the Client laptop, then use the account password when OpenSSH prompts."
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
        "Run on Client%s:\n%s\n"
        "OpenSSH will ask for normal host-key confirmation and the account password.\n"
        "Debug ports 3333/4444/6666 remain loopback-only."
    ) % (ssh_text, " (choose the reachable network candidate)" if ambiguous else "", command_text)
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
            return 1, record, "SYSTEM_CHANGE_CONFIRMATION_REQUIRED: OpenSSH Client is missing; no OS/profile change made."
        prepared = prepare_ssh_client_prerequisites()
        if not prepared.succeeded or not prepared.after.ready:
            raise RuntimeError("OpenSSH Client setup did not reach READY state.")
        system_changed = prepared.changed
    profile_path = save_remote_profile(profile)
    record = {
        "schema_version": 1,
        "command": "gateway client-setup",
        "status": "ok",
        "profile": profile.record(),
        "profile_path": str(profile_path),
        "profile_saved": True,
        "system_changed": system_changed,
        "password_stored": False,
        "connect_check_command": connect_check_command(),
        "next_action": "Run connect_check_command on this Client and enter the account password in the native SSH prompt.",
    }
    text = (
        "B300 CLIENT SETUP READY\n"
        "Saved Gateway: %s@%s:%d\n"
        "Endpoint only is saved; password is never stored.\n"
        "On first connection, confirm the ordinary OpenSSH host-key prompt.\n"
        "Then on Client run:\n%s\n"
        "Enter the SSH account password when OpenSSH prompts."
    ) % (
        profile.user, profile.host, profile.port, connect_check_command(),
    )
    return 0, record, text


def gateway_status() -> tuple[int, dict, str]:
    prereq = inspect_ssh_client_prerequisites()
    profile = load_remote_profile()
    local_ready = bool(prereq.ready and profile is not None)
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
        "password_stored": False,
        "host_key_memory": "OpenSSH default known_hosts",
        "next_action": "Run `gateway connect-check` to verify real SSH authorization/connectivity." if local_ready else "Run `gateway client-setup --ssh-host HOST --ssh-user USER`.",
    }
    text = (
        "B300 REMOTE CLIENT %s\nProfile: %s\nOpenSSH Client: %s\nAuthentication: native password prompt\nHost key: OpenSSH default known_hosts\nConnectivity: NOT VERIFIED (run gateway connect-check)"
        % (
            "LOCAL SETUP READY" if local_ready else "SETUP REQUIRED",
            ("%s@%s:%d" % (profile.user, profile.host, profile.port)) if profile else "not configured",
            "READY" if prereq.ready else "MISSING",
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
        "host_key_memory": "OpenSSH default known_hosts",
        "password_authentication": True,
        "debug_ports_exposed": False,
    }
    text = "%s: %s\n%s" % (result.reason_code, result.gateway, result.message)
    return (0 if result.ready else 1), record, text
