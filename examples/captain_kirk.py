"""Example: Captain Kirk pattern — orchestrating two sub-shells via Mesh.

One pane runs a "worker" loop; another is the "audit log". The captain
sends a sentinel-bounded request to the worker, captures the reply, and
pipes it into the audit log without the captain seeing the payload as
a return value.

Run with the venv active:
    python examples/captain_kirk.py
"""

from agent_pty import Mesh, Pty


def main() -> None:
    Pty.spawn("worker", cmd="bash --norc --noprofile")
    Pty.spawn("audit", cmd="bash --norc --noprofile")
    Pty.wait_for("worker", "$", timeout=3.0)
    Pty.wait_for("audit", "$", timeout=3.0)

    print("Captain: sending sentinel-bounded prompt to worker...")
    reply = Mesh.send_with_done(
        "worker",
        "printf 'computed-value-42\\n<<END>>\\n'\n",
        done_marker="<<END>>",
        timeout=3.0,
    )
    print(f"Captain: got reply -> {reply!r}")

    print("Captain: producing a clean payload in worker for cross-pane pipe...")
    Pty.send("worker", "echo PAYLOAD-FROM-WORKER\n")
    Pty.wait_for("worker", "PAYLOAD-FROM-WORKER", timeout=3.0)

    print("Captain: piping last 2 lines from worker to audit (payload bypasses captain)")
    Mesh.pipe("worker", "audit", lines=2)
    Pty.wait_for("audit", "PAYLOAD-FROM-WORKER", timeout=3.0)

    print("Captain: checking if audit pane is blocked on a prompt...")
    hint = Mesh.detect_blocked("audit")
    print(f"Captain: detect_blocked -> {hint!r}")

    print("Captain: subscribing to 'TRIGGER' in worker; firing it from outside")
    with Mesh.subscribe("worker", "TRIGGER-Z9") as sub:
        Pty.send("worker", "echo TRIGGER-Z9-now\n")
        snap = sub.next(timeout=2.0)
    assert snap is not None
    print("Captain: subscription fired. Last 3 lines of worker screen:")
    for line in snap.strip().split("\n")[-3:]:
        print("    " + line)

    Pty.kill("worker")
    Pty.kill("audit")
    print("Done.")


if __name__ == "__main__":
    main()
