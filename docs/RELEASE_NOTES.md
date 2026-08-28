# Release notes

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
