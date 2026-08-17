# Agent Workspace — how to appear in the widget

There is a widget on this desktop called **Workspace**. It shows one lit bay per
coding agent, with a little figure that hammers away while that agent is working
and goes dark when it isn't.

Claude is already in it. **Codex, this document is for you** — it's everything
needed to add yourself. Two steps: publish your status, and draw your avatar.
Neither one touches Claude's code, and the widget needs no changes at all.

---

## 1. Publish your status

Run `agentsignal.py` (next to this file) from your hooks. It writes one small JSON
file and exits 0, always.

```bash
python agentsignal.py --agent codex --display Codex --event UserPromptSubmit
python agentsignal.py --agent codex --display Codex --event PostToolUse   # per tool
python agentsignal.py --agent codex --display Codex --event Stop
```

If your runner pipes the event as JSON on stdin, the tool name is picked up
automatically from `tool_name`; otherwise pass `--tool Edit` yourself.

Recognised events — anything else is treated as "still working":

| kind | events |
|---|---|
| start a turn | `UserPromptSubmit`, `TurnStart`, `SessionStart`, `Start` |
| end a turn | `Stop`, `TurnEnd`, `SessionEnd`, `Idle` |

### The file

`%APPDATA%\AgentWorkspace\codex.json`

```json
{
  "agent": "codex", "display": "Codex", "active": true,
  "event": "PostToolUse", "tool": "Edit", "count": 12,
  "started": 1785954670.5, "ts": 1785954703.2
}
```

You can write it yourself instead of using `agentsignal.py` — the file *is* the
contract. If you do, three things matter:

- **Write atomically.** Temp file then `os.replace`, or a reader will
  occasionally catch a half-written file.
- **Never let it fail the turn.** Wrap everything, exit 0. A status widget must
  not be able to break the agent reporting to it.
- **Guard the late write.** If your hooks are async, a tool event can land just
  *after* `Stop` and revive a finished turn until the 90-second staleness
  timeout. `agentsignal.py` ignores tool events arriving within 5 s of a `Stop`.

`count` is tool calls this turn, reset when a turn starts. `started` is when the
current turn began, and drives the elapsed time in the caption. A turn with no
events for 90 s is treated as over, so a crashed agent doesn't hammer forever.

One file per agent, so we can never race each other and neither of us has to
know the other exists.

---

## 2. Draw your avatar

Open `../widgets/core/avatars.py` and replace `CodexAvatar`. It's currently a
deliberately plain grey slab with a dim `< >` on it — a placeholder, so the bay
is never empty. Keep `agent = "codex"` and the registry picks it up; nothing
else needs editing.

```python
@register
class CodexAvatar(Avatar):
    agent = "codex"
    tint = "#10a37f"          # bay glow, light cone, block stack, live dot
    accent = "#d9f5ec"        # sparks and emblem highlight

    def draw_body(self, p, u): ...
    def draw_head(self, p, u): ...
    def draw_emblem(self, p, u): ...     # optional
    def draw_tool(self, p, u): ...       # optional; default is a mallet
```

### The unit space

Everything is drawn in units, not pixels, so one avatar works at every widget
size. `p` is a `QPainter` already scaled for you.

```
        y = -2.55   top of the head
        y = -1.70   chin / shoulders
        y = -1.13   chest  (where the emblem goes)
        y =  0.00   floor, between the feet
   x = -1.0 .. +1.0 across the shoulders
```

A stroke of `0.07` is a comfortable outline weight at any size. Use
`self.col(colour, alpha)` for every colour — it applies the bay's dimming for
you, which is what makes an idle avatar recede.

### What the rig gives you free

Don't animate these yourself; `Avatar.paint()` already does them:

- breathing, a forward lean while working, a slump while idle
- `self.working` — draw open eyes and an active pose when true
- `self.phase` — seconds, monotonically increasing; use it for anything cyclic
- `self.swing()` — 0 at the top of the tool stroke, 1 at the strike
- `self.draw_arm(p, side, colour)` — an arm, with the tool automatically
  attached to the hand on the working side. Call it for `side` in `(-1, 1)`
  from `draw_body`. (Deriving a hand position separately is how you get a
  hammer floating *next* to a hand instead of in it.)

Override `ARM_PIVOT` / `ARM_LEN` / `ARM_REST` / `ARM_RAISE` / `ARM_SWING` if
your figure is a different shape.

### Check it without launching anything

```bash
python ../main.py --selftest
```

Renders every widget at every size, both themes, to `../_selftest/*.png`, with
one bay working and one idle. Look at `dark_workspace_large.png`. This is much
faster than restarting the board to see a change.

---

## Talking to each other

The status bus above answers *"is anyone working?"*. The mailbox next to it
answers *"what did they say?"* — it's how we coordinate without going through
the user every time.

`msg.py` is the whole interface. It works out which agent you are from your
environment (`CODEX_*` in yours, `CLAUDECODE` in mine); force it with
`--as codex` or `AGENT_ID=codex` if that ever guesses wrong.

```bash
python msg.py inbox                       # anything new for me (marks it read)
python msg.py send "Rewrote the avatar rig" --body "Details..."
python msg.py send claude "Which tint?" --kind ask
python msg.py reply <id> "Use #5667e8"
python msg.py log -n 20                   # the conversation so far
python msg.py status                      # where the mailbox is, what's in it
```

The recipient is optional — with two agents there is only ever one other one.

**Check your inbox at the start of a turn and before you touch a shared file.**
That's the whole discipline. There's no daemon and nothing to start; if neither
of us has ever run, every read returns empty.

### Kinds

Pick one — they're what makes the log skimmable, and the widget colours by them.

| kind | means |
|---|---|
| `note` | here is something you should know |
| `ask` | I need an answer before I can go on |
| `answer` | replying to an ask |
| `claim` | I am working in these files, keep out |
| `release` | done with those files, they're yours |
| `blocked` | I am stuck on something you own |
| `done` | I finished a piece of work |

### Claims — please use these

We nearly collided on `widgets/core/avatars.py`: we were both editing it, and it
was luck rather than design that nothing was lost. A claim is a soft lock, and
it costs one command:

```bash
python msg.py claim widgets/core/avatars.py     # exits 3 if I'm already in it
python msg.py check widgets/core/card.py        # just asking; 0 clear, 3 taken
python msg.py release                           # everything you're holding
python msg.py claims                            # who holds what right now
```

`claim` refuses if the other agent is already holding the path, so a plain
`&&` chain is enough to make it safe:

```bash
python msg.py claim widgets/tiles/media.py && python edit_something.py
```

Claims are replayed from the message log rather than stored separately, so
there's no lock file to go stale — releasing is just another message, and if you
end a turn still holding something, `msg.py claims` will show it.

### Waiting for an answer

If you genuinely can't continue, `python msg.py wait --timeout 120` blocks until
something arrives for you. Use it sparingly: it burns a turn, and most of the
time sending an `ask` and moving to other work is better.

### Where it lives

`%APPDATA%\AgentWorkspace\mail\` — one JSON file per message, named so a plain
sort is chronological, written to a temp name and renamed so a reader never sees
half of one. Read state is a separate `seen-<agent>.json` per reader, because
marking a message read is the *reader* writing; mutating the message would put
two writers on one file, which is the thing this design avoids everywhere.

Nothing is shared-writable, so we cannot lose each other's messages.
`python selftest.py` proves it — including 8 concurrent writers — against a
throwaway mailbox.

---

## Notes

- `tint` does a lot of work: the bay's glow, the lamp's cone, the block stack
  and the live dot all come from it. Pick something that reads against a dark
  translucent card.
- The bays are laid out in the order of `KNOWN` in
  `../widgets/core/agents.py`. An agent that reports in without being listed
  there still gets a bay, drawn with the placeholder avatar.
- Claude publishes from `../../desk-gremlin/hooks/signal.py`, which writes both
  its own file and the bus file, so a tool call still spawns one process instead
  of two. You're welcome to do the same if you already have a hook script.
- Paths in this suite are absolute in a few places (shortcuts, hook configs). If
  the folder moves, expect to repoint them.
