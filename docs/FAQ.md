# CarWatch FAQ

The questions below the divider were asked by real people on the Hacker News
and r/raspberry_pi threads, named. The v0.4 questions above it are the ones
the release video raises. Answers are checked against the repo and measured
runs on the real car.

**Q: How do I actually talk to the car? (v0.4)**
A: Say a wake phrase ("Hello car", "Hey car", "Hei auto") or tap Speak on the
phone dashboard, then ask in the same breath. For 30 seconds after an answer
you can keep talking with no new wake phrase. Everything before the wake
phrase is discarded, so talking NEAR the car does not summon it.

**Q: Does it really work with no internet? (v0.4)**
A: Yes - the last question in the release video is asked after switching the
phone hotspot off on camera. Speech-to-text (whisper.cpp), the model, and the
voice (piper) all run on the Pi. To be precise about the split: OBD readings and
owner's-manual answers work fully offline - they never left the car. The
Mercedes me fuel/tyre/charge tiles are the separate ONLINE enrichment; offline
they show last-known values, labeled as such. Room transcript posts also
resume when the connection returns.

**Q: Will it answer its own voice, or the radio? (v0.4)**
A: Not anymore, and the release video is why we can say that: during filming
the car heard the tail of its own answer and replied to itself. v0.4 ships the
echo gate - the mic stays closed until the cabin audio has actually finished,
and anything that transcribes as a copy of the car's last answer is dropped.
Ambient speech without a wake phrase is ignored and never posted.

**Q: The answers in the video stutter. Known? (v0.4)**
A: Known, root-caused after the shoot, fixed the same evening: the Bluetooth
OBD dongle was polled every ~20 s on the same radio that carries the answer
audio, and voice answers never set the radio-quiet window the daily brief
already used (commit b6904bf closes exactly that). The journal-forensics
write-up is in the commit message; the next filmed answer is the proof run.

**Q: Can it see - dashcam, camera, "look at this light"? (v0.4)**
A: Not yet. The candidate is benched: Gemma 4 12B (multimodal, vision
projector already on the SD card) runs at 1.5 tokens/s generation on the Pi -
too slow to talk with, plausible as a slow background frame-reader. It stays
a candidate until something is proven on the real car.

---

**Q: What hardware do you need? (yjftsjthsd-h, HN)**
A: A Raspberry Pi 5, a ~15 € Bluetooth ELM327 OBD adapter (Vgate iCar Pro is
the tested unit), and a power source in the car. No cables into the car beyond
the OBD dongle, no soldering. The engine values (RPM, coolant, speed, hybrid
battery, 12 V) are standard OBD-II.

**Q: Which runtime, and what speed do you really get? (pranaysparihar, Reddit)**
A: llama.cpp, serving Qwen3.6-35B-A3B in Unsloth's UD-Q3_K_S dynamic quant
(14.3 GB). Measured on the real Pi in the real car: 3.5 tokens/s generation,
25+ tokens/s prompt processing, 65 °C sustained. A full spoken,
manual-grounded answer takes roughly 1.5-4 minutes (approximate measured
range) depending on the question and how hot the cabin is - the dash strip shows live progress while it thinks. That is
honest; it is a car that thinks before it speaks, not a chatbot race. (Faster
brains are being benched in the open: Gemma 4 12B measured 1.5 tok/s - slower,
rejected; Ornith-1.5-35B-A3B is on the bench tonight.)

**Q: Why Qwen 35B-A3B and not Gemma 4 E4B? (dofm, HN)**
A: The A3B matters more than the 35B: it is a mixture-of-experts model with
~3B parameters active per token, so it fits the Pi's memory as a quant and
still generates at usable speed. Dense models of comparable quality either do
not fit or crawl. We publish the measured numbers rather than claiming it is
fast.

**Q: Is the model trained or fine-tuned for cars? (javier123454321, HN)**
A: No. It is a stock open-weight model. The car knowledge comes from grounding,
not training: live OBD values are passed to it verbatim, and the owner's manual
is chunked with page numbers so answers cite the page they came from.

**Q: What is the LLM actually for, then? (hypfer, HN)**
A: It is the interface, not the sensor. The real data comes from OBD, Home
Assistant, and the manual; the model turns a spoken question into the right
lookup and the numbers into a sentence. When it has no data, the rules force it
to say so instead of improvising.

**Q: How does the Pi do TLS to the manufacturer API? (Reubachi, HN)**
A: It does not talk to the manufacturer at all. The Pi reads your Home
Assistant over Tailscale, and HA holds the Mercedes integration and its
credentials. There are no manufacturer credentials in the car. (see carwatch/cloudcar.py)

**Q: How does it handle uncertainty about trim and year? (jiangriver66, HN)**
A: By saying so. The manual RAG cites pages, live values are quoted as read,
and the system prompt requires it to state plainly what it does not know. The
"Stated by the Berry" section shows what that sounds like in practice.

**Q: Shouldn't the Pi collect data and a bigger machine at home think?
(EightyNineMillion, Reddit)**
A: That works, and nothing in the architecture forbids it; the room protocol
does not care where the brain lives. We run the brain in the car because the
car should answer in a parking garage with no home dependency, and because the
data staying in the car is the privacy story. If you prefer a home box, point
the same services at it.

**Q: Should you trust anything a Pi-sized LLM says about your car? (trouthat,
Reddit; "the weights are the weights", AllRealityIsVirtua1)**
A: No, and the design assumes you should not. The model is never the source of
truth: OBD numbers pass through verbatim, manual answers carry page citations,
and anything unverified is labeled. The status table in the README marks every
feature proven / in testing / planned, and "proven" means it worked on the
real car.

**Q: "A lot of this looks aspirational." (VTimofeenko, HN, quoting the
README's own "unverified against the real car" line)**
A: Fair, and that line exists on purpose. The README keeps a status table with
an honesty policy: nothing is marked proven until it ran against the real car.
What is proven today: OBD live readings, the room agent, manual RAG with page
citations, the phone dashboard, and on-Pi voice transcription. What is not is
listed just as plainly.

Notes for review: the Reddit thread also contained "this reads like an ad"
(lostrouteros). I suggest we answer that by keeping this FAQ exactly this dry.
