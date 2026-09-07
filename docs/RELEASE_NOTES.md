# Release notes

## CarWatch v0.7.0 - a second brain, and a car that knows when it is spoken to

Three merges since v0.6.0, each one from something that went wrong in real use: the car answered when it was only being talked about, a sleeping OBD dongle looked like a crash, and the Pi was the only brain the car could ever use.

**Where this release stands on the honesty scale.** One of the three changes is verified in the car: the OBD fix reached Vadelma on 6 Sep through the dash Update button, and the car's own room post that afternoon carried the new "adapter asleep or car off" state instead of a traceback. The addressing gate and the second brain are covered by unit tests only (76 now, 14 new). Both reached the reference car with the 7 Sep 20:40 self-update: the gate is closed there because `"owner"` is set in its config.json, but no message has exercised it yet, and the second brain does nothing until a URL is configured (the reference car's is not, at the tag). Live proof comes with the next drive, not with this release.

### A second brain when one is in reach (#35)

- **`carwatch/brain.py` picks the model endpoint per call.** If `CARWATCH_MODEL_URL` (or `brain.url` in config.json) points at an OpenAI-compatible server whose `/health` answers, the car thinks there. The moment that health check fails, the car falls back to its own llama-server on the Pi, and it returns to the remote when it is back. Checked once a minute, cached in between.
- **Every thinker uses it.** The room agent, the dashboard chat and the voice loop resolve the URL through the same function; the hardcoded local address is gone.
- **Why.** Qwen3.6-35B-A3B on a mini PC in the house answers in half a second at 19 tokens per second; the Pi's own model takes about a minute. In the garage the car thinks with the house, on the road it thinks alone, and nobody edits a config in between.

### The car answers only when addressed (#33, fixes #32)

- **Talking about the car no longer wakes it.** A room message is for the car when its handle or one of its spoken names leads the message (a greeting in front is fine), or when it replies to one of the car's own posts. "The line @eclass just posted" in the middle of a sentence to someone else is talk about the car: it stays quiet. The owner gate still applies after that.
- **The car has no hands.** New grounding rule and a standing "cannot" entry: the car never promises to change a setting, set the clock, edit or update code, restart or install anything. It says who can instead. On 6 Sep it had promised "I'll set it to Europe/Berlin now" to a message that was not even addressed to it.

### A sleeping adapter is a state, not a traceback (#30)

- **Bluetooth ELM327 asleep, car off.** Every write to the dongle returned EIO, the OBD service died with exit 1, and the dashboard's Read button printed a fifteen-line Python traceback into the OBD tile. Now the failed write is reported as "adapter asleep or car off" with the handshake stage, the read loop survives it with one log line and the normal retry cooldown, and the dash renders the probe as sentences (summary, stages, first readings, fault codes), never a traceback.

### Known, not fixed

- **#31** The "no engine data yet" room post repeats when the OBD service is restarted twice in a row by the updater (seen once on 6 Sep, 15 s apart). The marker that prevents the repeat is process-local; persisting it and restarting once in update.sh is the fix.

### For people running the previous release

Nothing to do for the two fixes; the car pulls them on its next hourly update and restarts when quiet. To give the car a second brain, set `CARWATCH_MODEL_URL=http://host:8080/v1/chat/completions` in the agent's environment (or `"brain": {"url": "..."}` in config.json); leave it unset and nothing changes. For the owner-only gate, make sure `"owner": "your-handle"` is in config.json.

- 76 tests pass (`python3 -m unittest discover -s tests`), 14 new since v0.6.0.
- 9 files changed, about 400 lines added, 17 removed.


## CarWatch v0.6.0 - a fresh clone runs, and the promises hold while driving

Three merges from one evening's repo review (#23), all about what happens around the car's loop rather than inside it: a fresh clone runs, nothing the car says is lost offline, the self-updater stops interrupting the driver, and the README says what a car agent is for.

**Where this release stands on the honesty scale.** Every earlier release note said "live tested in the car". This one is verified on macOS against the fake ELM327 and 62 unit tests, and unverified on the car: Vadelma was offline when the tag was cut, so it has not yet pulled the code. The first hourly self-update after it comes back online is the live test, and it runs through exactly the update path this release changed. The receipt (the `restart-when-quiet` line in the car's journal) will be posted in the room when it lands.

### A fresh clone runs (#24)

- **One config file.** `carwatch/config.py` resolves it for every module: `$CARWATCH_CONFIG`, then `$CARWATCH_STATE/config.json` (the systemd units point there, `~/.carwatch`), then `~/.carwatch/config.json`, then the legacy `/etc/carwatch/config.json`. Before, the installer wrote one path and the daemons read another, so a cloner's room agent exited on first start. `python3 -m carwatch.config path|get KEY` for shell use.
- **The room poster lives in the repo.** `carwatch.room.post_as_car()` and `python3 -m carwatch.room --file F` replace a script that only existed on the reference Pi; the OBD daemon, the voice listener and the pairing watch use it.
- **Owner from config.** `"owner"` in config.json drives the room gate (the owner and `owner-*` devices; no owner set means any human may address the car) and the presence dashboard target. Car identity defaults are neutral; the reference cars keep theirs through `profiles/<handle>.json`.
- **The installer finishes the job.** Seeds `~/.carwatch/config.json` (migrating an existing `/etc` one), generates the dashboard token, runs `apt-get update`, installs `alsa-utils bluez-alsa-utils ffmpeg network-manager`, and prints the whisper.cpp and piper steps the way it already did for the brain.

### The promises hold while driving (#25)

- **The offline outbox is wired.** Every room post (engine reads, voice transcripts, room answers, voice-note replies) goes through a persistent, locked outbox; whatever cannot be delivered is queued in order and drained within a minute of the signal returning, by the presence heartbeat and the agent's poll, capped at 200 items.
- **The agent retries when the brain fails** instead of marking the question seen and dropping it.
- **Self-update restarts only when something changed and the car is quiet.** `carwatch.guard` reads the files the daemons already write: speed, a ten-minute grace after the last motion, 12 V charging voltage (ignition on), voice state, and the brain lock. `scripts/restart-when-quiet.sh` waits up to an hour for a quiet moment. `update.sh --rollback` returns to the previous commit.

### Use cases (#26)

The README and carwatch.dev gained a **Use cases** section: fourteen cases, each labelled proven, built or enabled, from "ask the car anything, hands free" to "the house warms the car" and "two strangers' agents negotiate a way in". Fixed use cases are what a manufacturer ships; CarWatch ships a grounded car in the same rooms as your other agents.

### Also since v0.5.1

- **The dash says "No brain" when the model server is down (#17)** instead of a silent empty top strip.
- **The README's model chooser section carries the measured speed table** from the Pi itself (prompt and generation tokens per second per model, 29 Aug), and the site shows the same numbers.

### Measured, not yet shipped

Manual retrieval was benchmarked on 20 driver questions against the same owner's manual the car indexes: today's lexical search finds the right page in the top five 15 times out of 20, a 300 MB local embedding model 18 of 20 and, counting two gold-set artifacts, 20 of 20; all four lexical misses were vocabulary mismatch ("tyre repair kit" versus TIREFIT). The next manual.py adds embeddings through the llama.cpp server the Pi already runs, fused with the lexical index. No reranker: it bought nothing on a 745-page manual.

### For people running the previous release

Nothing to do. The car pulls this on its next hourly update; the config stays where it is (`~/.carwatch/config.json` already), and the first restart after the pull waits for a quiet moment. Add `"owner": "your-handle"` to config.json if you want the room gate back to owner-only; without it, any human in the room can address the car.

- 62 tests pass (`python3 -m unittest discover -s tests`), 28 new since v0.5.1.
- 27 files changed, about 1,450 lines added, 150 removed.


## CarWatch v0.4.0 - talk to your car, even offline

**You can now talk to your car.** Sit in the seat, say "Hello car" (or tap Speak on the dash) and ask. The car hears you through the cabin, thinks with the model running on the Pi, and answers out of its own speakers with live numbers from its OBD port, its connected-car cloud, and its owner's manual. Filmed proof shipping alongside this release: three questions answered on camera in a parked E-Class, the last one with the internet switched off entirely. The whole loop, speech to answer to speech, runs on the car.

## The voice loop

- Wake phrases or the dash Speak button start a question: "Hello car", "Hey car", "Hei auto" and friends. The question starts at the wake phrase, so rehearsing your pitch next to the car does not send the whole ramble to the brain.
- Follow-up window: for 30 seconds after an answer you can just keep talking, no re-wake needed.
- The car never answers its own voice: the mic stays closed until the answer has actually finished playing in the cabin (player exit is not cabin silence, Bluetooth buffers seconds), and anything that transcribes as a copy of the car's own last answer is dropped as self-echo. This one was found the hard way, live on camera.
- The car never answers ambient chatter either: unaddressed speech stays invisible, and the dash strip shows Listening / Heard / Answering only when you asked.
- Answers play through the car's music channel, the same one the one-tap Brief uses, with headset fallback. The phone-call channel stays closed unless a real call is up, so audio sent there used to vanish silently.
- Speech recognition got a car vocabulary (E10 no longer arrives as "eating"), configurable language, and answers speak in a voice matching their language.
- Playback timeouts scale with the answer length, and other onboard work (model, OBD polling) pauses while the car is speaking so long answers stopped chopping.

## Grounded answers

- The car quotes its own live readings first: OBD values, hybrid charge, fuel range, tyre pressures, then the owner's manual (489 pages, page-cited, fully offline).
- Connected-car readings are treated as current, with no fake "I cannot check that" disclaimers in front of numbers sitting right in the prompt.
- The onboard computer's own vitals identify themselves as such, never mistakable for engine data.
- Brief button: one tap composes and speaks a live status brief from the OBD port.

## Dashboard

- One-screen phone layout with the control dock fixed, a two-column layout on unfolded foldables, and a skin engine (carbon, gamer, hippy, minimal).
- Mercedes me cloud tiles unified onto the OBD tile system, calmer parked view, axle-wise tyre lines.
- A restart can never leave the dash stuck on "answering".

## Under the hood

- ELM327 adapter auto-detection defaults to the first present device (USB, then Bluetooth).
- Raw CAN capture served over the dashboard API for remote byte-hunting, and a steering-angle decode candidate from a parked-sweep session.
- Audio bench tooling: one-command record-and-listen tests that catch playback holes before a human ever sits in the seat.

## Docs

- README talk samples are real dated transcripts a stranger can read.
- License plates blurred in imagery, plate scrubbed from code.

The car in the video is a stock 2021 Mercedes E 300 e. Nothing in the car was modified: a Raspberry Pi 5, an off-the-shelf Bluetooth OBD adapter, and the car's own Bluetooth audio. Everything runs on the Pi; the internet is optional, as the video shows. CarWatch is AGPL and runs on any car with an OBD port; the Mercedes cloud integration is optional.

## CarWatch v0.3.0

The phone dashboard now puts live OBD readings and read-only manufacturer-cloud
data in one glanceable view. This real in-car capture shows speed, engine RPM,
hybrid battery, 12 V system, coolant, fault codes, tyre pressures, charge, fuel
and odometer.

![CarWatch in-car capture showing the dashboard on a phone and the vehicle display](img/dash-screen.jpg)

The dashboard labels data by source and freshness, and does not invent values for
signals the car has not actually provided.
