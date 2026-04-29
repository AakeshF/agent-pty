# How the executive uses this

You don't operate the tool directly — your agent does, on your behalf. What changes is what you can ask for and what working alongside the agent feels like.

**Today** you say "debug this failing test" and the agent runs the test, reads the output, guesses, runs it again. If the test needs you to type a password, hit `y` to confirm, or step through a debugger — the agent stops and asks you to do it.

**With this tool** you say the same thing. The agent opens a terminal session, runs the test, drops into the Python debugger when it hits the failure, steps through frame by frame, finds the bug, fixes it, re-runs the test, commits. If `sudo` prompts for a password, it asks you once and remembers. If a deploy script asks "are you sure?", it knows to answer. You stayed in your chair the whole time.

**The killer move:** at any point you can run one command (`tmux attach -t <session>`) and you're *inside* the same terminal the agent is working in. You watch the cursor move. You can take the keyboard, type something yourself, hand it back. It's pair programming with an agent that can actually drive the keyboard — not a chatbot that narrates what it would do if it could.

Practically: longer-running work (debugging sessions, database investigations, server triage, migrations, anything with a REPL or a TUI) becomes one continuous flow instead of a back-and-forth where the agent keeps bouncing decisions back to you. You delegate more, interrupt less, and when you do want to step in, the seam is invisible — same session, same screen.
