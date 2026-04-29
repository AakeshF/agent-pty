# Elevator pitch

Coding agents can run shell commands but can't actually *operate* terminals — they break the moment a program asks a question, redraws its screen, or expects a real keyboard. We close that gap with a small tool that gives the agent a persistent, shareable terminal session it can read and type into, backed by tmux so the human can attach and watch or take over at any time. One primitive unlocks REPLs, debuggers, TUIs, auth prompts, and true human-agent collaboration in the same shell — four problems collapsed into one tool, shippable in days.
