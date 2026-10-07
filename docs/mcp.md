# MCP: the Mirror's tools for other agents

The [Model Context Protocol](https://modelcontextprotocol.io) is how an AI
agent is given tools. The [companion](../companion/README.md) offers the
Mirror through it, so that an agent of your own can use the Mirror as the
Mirror's own assistant does: read what it shows, look at the glass, put a
countdown or a drawing on it for a while, add a reminder to the board, change
the background, or hand a wish in plain words to the Mirror's assistant.

The tools are the same ones the assistant has, run by the same code. Nothing
is added to the Mirror: the companion is a paired device and uses the
[control API](protocol.md) like any other.

There are two ways to connect, and both can be used at once.

| | Over the network | Started by the client |
|---|---|---|
| What it is | A route of the running companion: `http://COMPANION_ADDRESS:8790/mcp` | A command the client starts itself: `node src/cli.js mcp` |
| For | An agent on any machine of the home network | An agent on the machine that has this repository |
| Needs | A companion that runs, and its MCP key | Nothing running |
| A change to the Mirror | Waits its turn with what people ask the Mirror | Goes straight to the Mirror |

Neither needs a language model, Python or a GPU. Those are for the
assistant, which is the companion's other job and can be left out.

## What you need

- Mirror Home 2.3.0 or later on the Mirror.
- Node.js 24 on the machine that runs the companion.
- The companion's packages, and a pairing with the Mirror. In `companion/`:

  ```bash
  npm ci --omit=dev
  node src/cli.js init
  node src/cli.js pair --host MIRROR_ADDRESS --code 123456
  ```

  The code is the one a paired phone shows in the Mirror's controls under
  **Settings > Paired devices > Show code**. The Mirror lists the companion
  as "Mirror companion" among its paired devices, and revoking it there ends
  what every agent can do through it.

A companion that already runs the assistant has all of this.

## Over the network

1. Make the key that agents have to send:

   ```bash
   node src/cli.js mcp-key
   ```

   It prints the key and stores it in the config. Until a key exists, the
   route answers `404` and no agent is served. The key is not the secret
   that the Mirror has: an agent that holds it can use the tools and cannot
   speak to the companion as the Mirror does.

2. Start the companion, or start it again if it runs. One that also runs
   the assistant needs nothing else:

   ```bash
   systemctl --user restart mirror-companion
   ```

   To run it for agents alone, with no model and no speech-to-text:

   ```bash
   node src/cli.js serve --tools-only
   ```

   As a service, add `--tools-only` to the `ExecStart` line of the
   [unit](../companion/README.md#run).

3. Tell the agent's program where the server is. Most take an entry of this
   shape, in a file called `.mcp.json`, `mcp.json` or `mcp-config.json`:

   ```json
   {
     "mcpServers": {
       "mirror": {
         "type": "http",
         "url": "http://COMPANION_ADDRESS:8790/mcp",
         "headers": { "Authorization": "Bearer THE_KEY" }
       }
     }
   }
   ```

   VS Code keeps the same entry under `servers` in `.vscode/mcp.json`.
   Claude Code takes it as a command:

   ```bash
   claude mcp add --transport http mirror http://COMPANION_ADDRESS:8790/mcp \
     --header "Authorization: Bearer THE_KEY"
   ```

`node src/cli.js health` says whether MCP is on and how many tool calls came
since the companion started.

## Started by the client

A program that starts its servers itself, as desktop apps do, is given the
command:

```json
{
  "mcpServers": {
    "mirror": {
      "command": "node",
      "args": ["/path/to/companion/src/cli.js", "mcp"]
    }
  }
}
```

It reads the same config as the rest of the companion, for the Mirror's
address and token; set `MIRROR_COMPANION_CONFIG` in the entry's `env` if the
config is not at `~/.config/mirror-companion/config.json`. It needs no key:
whoever can start it can read the config. Its log goes to standard error.

Run this way, it does not know of a companion that may be serving a spoken
request at the same moment. Two changes to the layout or to the display's
rules made in the same second can then undo one another. Where a companion
runs, use it over the network.

## The tools

| Tool | What it does |
|---|---|
| `get_state` | Reads the Mirror: its time and zone, whether the display is awake, the widgets and where they are, the background and films, the board, the weather, the moments that show. |
| `look` | A picture of what the glass draws now. It never shows the room. |
| `show_moment`, `end_moment` | Puts a countdown, a few large words, a list, a chart or a drawing on the glass for a while, and takes one away early. See [moments](assistant.md#more-than-a-line-moments). |
| `say` | One line on the glass now, where the assistant's answers appear, or a small card: the line with up to five rows under it. For up to half a minute. |
| `board_add`, `board_update`, `board_remove` | Notes, to-dos and reminders on [the board](board.md). |
| `set_background` | A film, a photo, a colour or a gradient behind the widgets, and how far it is darkened. |
| `arrange_widgets` | Shows, hides, moves and resizes widgets. |
| `set_power`, `set_brightness` | Dark or awake, and how bright. |
| `set_display_rules` | The hours the display is lit, and whether it goes dark when nobody is there. |
| `set_clock`, `set_weather`, `set_film_schedule`, `set_text_color`, `set_name` | The settings an owner can also [ask the Mirror for](assistant.md#settings-you-can-ask-for). |
| `set_character`, `set_answer_place` | Which character the assistant answers as, and where on the glass. |
| `ask` | Hands a wish in plain words to the Mirror's own assistant, as if it were typed in the phone controls. The assistant acts on it and answers on the glass, and the tool returns the answer. It needs the assistant set up. |

Every tool describes itself to the agent, and the server tells it what this
glass is like: that black is mirror, that it is read from across a room,
that it draws no emoji, that times are the Mirror's own, and that a dark
display is to be left dark unless someone asks otherwise.

An agent does not get what belongs to the assistant's own conversations:
what the household asked it to remember, what it does unasked, and the cards
that are its answers. And it cannot do what the assistant cannot do either:
Wi-Fi, pairing, updates, and the switches of voice and of the assistant are
not among the tools.

## What an agent can do with the key

The route speaks plain HTTP, like the Mirror itself, so it is for a network
you trust. Whoever has the key can change everything the tools above can
change, and nothing else. There is one key for all agents;
`node src/cli.js mcp-key --new` replaces it, and every agent then needs the
new one. A web page cannot use the route: it sends no permission for
browsers.

`ask` sends its words to the language model that the assistant uses, with
the summary of the Mirror that every request to the assistant carries; see
[What leaves the Mirror](assistant.md#what-leaves-the-mirror). The other
tools send nothing anywhere but to the Mirror, except that `set_weather`
has the Mirror look a place up.

What agents change is on record. The companion's log has a line for every
tool call, marked `"via":"mcp"`, and `GET /v1/activity` lists the changes
with the source `mcp`: the tool and the time, one line per tool in ten
minutes, so that an agent that updates a score every few seconds does not
crowd out what people said. The hourly look of the assistant leaves alone
what an agent set, as it leaves alone what a person asked for.

## For developers

Over the network the transport is Streamable HTTP without sessions. Each
`POST` to `/mcp` carries one JSON-RPC message, or a list of them, and is
answered with JSON; a message that asks nothing is answered `202`. No stream
is kept open, so `GET` and `DELETE` answer `405`. A request without the key
answers `401`, a body over 256 KB `413`. The protocol versions `2025-11-25`,
`2025-06-18`, `2025-03-26` and `2024-11-05` are spoken; a client that asks
for another is offered the newest. The server has tools only: no resources,
no prompts, and it sends nothing by itself.

Started by the client, the same messages go one to a line over standard
input and output.

A tool that refuses answers with `isError` and a sentence that says what was
wrong and what to give instead, which is for the model to read and act on.
`look` answers with an image.

`src/mcp.js` is the whole of it: which tools are offered, the two that only
agents have (`say` and `ask`), and the protocol. It adds no package to the
companion. The tests use the protocol's reference client
(`@modelcontextprotocol/sdk`) against it, over HTTP and over standard input
and output, so that what a real client sends is what is checked.

A tool that is added to the assistant is not offered to agents until it is
put on the list in `src/mcp.js`; a test fails until someone has decided
either way.

## What it does not do yet

- A key cannot be limited to some of the tools, and there is one for all
  agents.
- The Mirror tells an agent nothing by itself: an agent that wants to know
  whether someone is there, or whether the board changed, asks.
- It is not on the Mirror itself. A machine on the network has to run the
  companion or the command.
