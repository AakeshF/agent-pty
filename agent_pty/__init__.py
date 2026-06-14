from agent_pty.io import send, snapshot
from agent_pty.keys import KeyParseError
from agent_pty.mesh import (
    LifecycleEvent,
    LifecycleStream,
    Mesh,
    Subscription,
)
from agent_pty.session import (
    SessionExistsError,
    SessionNotFoundError,
    kill,
    list_sessions,
    spawn,
)
from agent_pty.spock import (
    Advisory,
    FleetReport,
    PaneReport,
    Spock,
)
from agent_pty.wait import wait_for

# Bridge-crew layers (M8+). These compose on the core/mesh/spock imports above,
# so they are imported AFTER them to keep module load order acyclic.
from agent_pty.uhura import Uhura, ask, broadcast
from agent_pty.scotty import Scotty, Spec, Supervisor
from agent_pty.prime_directive import Policy, PrimeDirective
from agent_pty.sulu import Sulu
from agent_pty.captains_log import CaptainsLog, LogEntry, Recorder
from agent_pty.red_alert import Alert, Alerter, RedAlert
from agent_pty.holodeck import Holodeck, Simulation
from agent_pty.bones import Bones, Diagnosis
from agent_pty.transporter import Checkpoint, Transporter
from agent_pty.worf import Worf


class Pty:
    spawn = staticmethod(spawn)
    kill = staticmethod(kill)
    list = staticmethod(list_sessions)
    send = staticmethod(send)
    snapshot = staticmethod(snapshot)
    wait_for = staticmethod(wait_for)


__all__ = [
    "Advisory",
    "Alert",
    "Alerter",
    "Bones",
    "CaptainsLog",
    "Checkpoint",
    "Diagnosis",
    "FleetReport",
    "Holodeck",
    "KeyParseError",
    "LifecycleEvent",
    "LifecycleStream",
    "LogEntry",
    "Mesh",
    "PaneReport",
    "Policy",
    "PrimeDirective",
    "Pty",
    "Recorder",
    "RedAlert",
    "Scotty",
    "SessionExistsError",
    "SessionNotFoundError",
    "Simulation",
    "Spec",
    "Spock",
    "Subscription",
    "Sulu",
    "Supervisor",
    "Transporter",
    "Uhura",
    "Worf",
    "ask",
    "broadcast",
    "kill",
    "list_sessions",
    "send",
    "snapshot",
    "spawn",
    "wait_for",
]
