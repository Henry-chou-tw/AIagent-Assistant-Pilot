"""There is deliberately no Discord client code in this file.

Step 2 of the Pilot kickoff confirmed the real, working Discord <-> Claude
integration is the official Claude Code plugin `plugin:discord@claude-plugins-official`
combined with Claude Code's "Channels (experimental)" feature: Discord
messages are injected directly into a live Claude Code CLI session
started with `--channels`, and replies go back out through that plugin's
own tool. That transport is already real, already working, and explicitly
should NOT be rebuilt (Henry's instruction: 不要重新建立已經存在而且可以
重用的 Discord connector).

The actual "adapter boundary" for this Pilot is therefore not a Python
module -- it is CLAUDE.md's instructions to whatever live session Channels
injects Discord messages into: relay each message to
`python -m pilot_agent.main handle --source discord --channel-ref <id> --input "<message>"`
and send its printed response back to Discord, rather than answering the
message directly out of the live session's own general-purpose reasoning.
That keeps Discord-specific concerns (the plugin, Channels, the live
session's own broad tool access) out of pilot_agent's domain logic, even
though the isolation is enforced by instructions to a live session rather
than by a hard code boundary -- see CLAUDE.md and the Pilot kickoff
report for why that is the current limit of what's achievable, and what
would need to change (finer-grained Channels routing) to do better.
"""
