"""Example: drive a Python REPL end-to-end via agent-pty.

Run with the venv active:
    python examples/drive_python_repl.py
"""

from agent_pty import Pty


def main() -> None:
    print("Spawning Python REPL session 'demo'...")
    Pty.spawn("demo", cmd="python3 -q")

    Pty.wait_for("demo", ">>>", timeout=5.0)

    print("Sending: x = 21; print(x * 2)")
    Pty.send("demo", "x = 21; print(x * 2)\n")

    snap = Pty.wait_for("demo", "42", timeout=3.0)

    print()
    print("=== final screen ===")
    print(snap)
    print("=== end ===")

    Pty.kill("demo")
    print("Done.")


if __name__ == "__main__":
    main()
