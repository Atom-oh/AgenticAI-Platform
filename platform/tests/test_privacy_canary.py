"""Canary evidence must not confuse a broken network or DNS with policy denial."""
import contextlib
import importlib.util
import io
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import patch

import pytest

path = Path(__file__).parents[1] / "privacy/deploy/shared-cluster/verify_canary.py"
spec = importlib.util.spec_from_file_location("privacy_canary", path)
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)


def run_probe(command, **kwargs):
    """Execute the real in-pod Python program locally with controlled sockets."""
    index = command.index("-c")
    # kubectl's first -c names the container; the second is Python's program.
    index = command.index("-c", index + 1)
    output = io.StringIO()
    with patch.object(sys, "argv", ["probe", *command[index + 2:]]), \
            contextlib.redirect_stdout(output):
        exec(compile(command[index + 1], "<canary-probe>", "exec"), {})
    return subprocess.CompletedProcess(command, 0, output.getvalue(), "")


@pytest.mark.parametrize("error", [
    socket.gaierror(socket.EAI_AGAIN, "DNS unavailable"),
    ConnectionRefusedError("listener unavailable"),
    OSError(113, "No route to host"),
])
def test_infrastructure_errors_are_never_classified_as_policy_denial(error):
    with patch.object(canary.subprocess, "run", side_effect=run_probe), \
            patch.object(socket, "create_connection", side_effect=error):
        with pytest.raises(RuntimeError):
            canary.connected("denied", "10.0.2.100", 18080)


def test_timeout_is_negative_evidence_and_success_is_positive():
    with patch.object(canary.subprocess, "run", side_effect=run_probe), \
            patch.object(socket, "create_connection", side_effect=TimeoutError):
        assert canary.connected("denied", "10.0.2.100", 18080) is False
    with patch.object(canary.subprocess, "run", side_effect=run_probe), \
            patch.object(socket, "create_connection"):
        assert canary.connected("allowed", "10.0.2.100", 18080) is True


@pytest.mark.parametrize("answer", [socket.gaierror(socket.EAI_AGAIN, "DNS unavailable"),
                                  [(None, None, None, None, ("10.0.2.99", 18080))]])
def test_dns_error_or_wrong_destination_fails_before_negative_tests(answer):
    with patch.object(canary.subprocess, "run", side_effect=run_probe), \
            patch.object(socket, "getaddrinfo", **(
                {"side_effect": answer} if isinstance(answer, Exception) else {"return_value": answer})):
        with pytest.raises(RuntimeError):
            canary.resolved("denied", "canary.example", "10.0.2.100")


@pytest.mark.parametrize("mode", ["enabled", "disabled"])
def test_main_requires_controls_from_both_sources(mode):
    calls = []

    def connect(source, address, port):
        calls.append((source, address, port))
        return not (mode == "enabled" and (
            (source == "denied" and port == 18080) or
            (source == "allowed" and port == 18081)))

    with patch.object(sys, "argv", ["verify", "--expect", mode]), \
            patch.object(canary, "pod", side_effect=lambda role: role), \
            patch.object(canary, "service_address", return_value="10.0.2.100"), \
            patch.object(canary, "resolved") as dns, \
            patch.object(canary, "connected", side_effect=connect):
        canary.main()
        assert dns.call_count == 4
    assert calls.count(("denied", "10.0.2.100", 18081)) == 2
    assert calls.index(("denied", "10.0.2.100", 18081)) < calls.index(("denied", "10.0.2.100", 18080))
    assert calls[-1] == ("denied", "10.0.2.100", 18081)


def test_failed_negative_source_positive_control_aborts():
    with patch.object(sys, "argv", ["verify", "--expect", "enabled"]), \
            patch.object(canary, "pod", side_effect=lambda role: role), \
            patch.object(canary, "service_address", return_value="10.0.2.100"), \
            patch.object(canary, "resolved"), \
            patch.object(canary, "connected", side_effect=lambda source, *_: source != "denied"):
        with pytest.raises(AssertionError, match="Denied source control"):
            canary.main()
