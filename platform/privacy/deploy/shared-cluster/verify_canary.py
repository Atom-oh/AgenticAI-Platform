#!/usr/bin/env python3
"""Synthetic TCP checks; distinguish a disabled baseline from active enforcement."""
import argparse
import json
import subprocess

K = ["kubectl", "--context", "arn:aws:eks:ap-northeast-2:180294183052:cluster/fsi-demo-cluster",
     "--request-timeout=15s", "-n", "bank-platform-mydata"]


def pod(role):
    r = subprocess.run(K + ["get", "pods", "-l", "mydata-prerequisite=" + role, "-o", "json"],
                       text=True, capture_output=True, timeout=20, check=True)
    items = [i for i in json.loads(r.stdout)["items"] if not i["metadata"].get("deletionTimestamp")
             and any(c["type"] == "Ready" and c["status"] == "True" for c in i["status"].get("conditions", []))]
    if len(items) != 1:
        raise RuntimeError("Expected one ready canary pod per role")
    return items[0]["metadata"]["name"]


def connected(name, host, port):
    # Only fixed synthetic service/loopback hosts. No data or authentication.
    script = (
        "import socket,sys\n"
        "try:\n"
        " s=socket.create_connection((sys.argv[1],int(sys.argv[2])),3)\n"
        " s.close()\n"
        " print('OPEN')\n"
        "except OSError:\n"
        " print('CLOSED')\n"
    )
    r = subprocess.run(K + ["exec", name, "-c", "probe", "--", "python", "-c",
                           script, host, str(port)], text=True, capture_output=True, timeout=20)
    if r.returncode or r.stdout.strip() not in ("OPEN", "CLOSED"):
        raise RuntimeError("Probe execution failed; not evidence of network denial")
    return r.stdout.strip() == "OPEN"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect", choices=("enabled", "disabled"), required=True)
    args = parser.parse_args()
    names = {role: pod(role) for role in ("target", "allowed", "denied")}
    assert connected(names["target"], "127.0.0.1", 18081), "Target listener is not running"
    host = "mydata-np-target.bank-platform-mydata.svc.cluster.local"
    results = [
        ("allowed ingress", connected(names["allowed"], host, 18080), True),
        ("denied ingress", connected(names["denied"], host, 18080), args.expect == "disabled"),
        ("denied egress", connected(names["allowed"], host, 18081), args.expect == "disabled"),
        ("allowed path remains healthy", connected(names["allowed"], host, 18080), True),
    ]
    for name, actual, expected in results:
        print(name + ": " + ("PASS" if actual == expected else "FAIL"))
    if any(actual != expected for _, actual, expected in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
