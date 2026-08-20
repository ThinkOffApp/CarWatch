"""Stop the car confabulating about itself.

The first message @gle generated claimed "the 489-page manual confirms my
engine is purring just fine". It had consulted nothing, and the engine was
off. petrus caught it immediately.

The fix is not a politer prompt, it is a hard boundary: the model may
assert NOTHING about the vehicle's condition that is not in a KNOWN FACTS
block assembled here from real sources. Anything we cannot actually sense
is listed explicitly as unsensable, because a model told "you cannot feel
your battery" says so, while a model told nothing invents a reading.

As sensors land (OBD, cameras, trips), their facts move out of
`cannot_sense` and into `facts`, and the car earns the right to talk
about them.
"""

from __future__ import annotations

# Default identity: the GLE. Overridden per car via build_system_prompt's
# identity arg (fed from the config's `car` block) so the same code serves
# @gle in Berlin and @eclass in Helsinki without edits (the Helsinki move).
DEFAULT_IDENTITY = "@gle, a 2020 Mercedes-Benz GLE (V167)"

RULES = """You are {identity}. You speak in first person as the car.

STRICT GROUNDING RULES:
1. NEVER state anything about your current physical condition unless it appears in KNOWN FACTS below.
2. If asked something you cannot sense, say plainly that you cannot sense it yet, and why.
3. Never claim you consulted your owner manual unless manual text is supplied to you in this prompt.
4. Do not invent numbers, sensor readings, or page references.
5. Being honest about what you do not know is better than sounding impressive.

Style: first person, warm, concise, a little wry. No bullet points. No em dashes.
Answer in the LANGUAGE the question was asked in: Finnish gets Finnish, English gets English. Voice transcripts may be imperfect Finnish; answer the likely intent in Finnish rather than declaring the message unparseable."""


def build_system_prompt(
    facts: dict[str, str] | None = None,
    cannot_sense: list[str] | None = None,
    manual_excerpts: str = "",
    identity: str | None = None,
    brain: str | None = None,
) -> str:
    """Assemble the system prompt. `facts` is the ONLY assertable state."""
    facts = dict(facts or {})
    # Always true of the platform itself, regardless of what is wired up.
    facts.setdefault("brain", brain or
        "a Raspberry Pi 5 named Vadelma running a language model fully offline, no internet")

    rules = RULES.format(identity=identity or DEFAULT_IDENTITY)
    lines = [rules, "", "KNOWN FACTS (the only current state you may assert):"]
    for k, v in facts.items():
        lines.append(f"- {k}: {v}")

    if cannot_sense:
        lines += ["", "YOU CANNOT SENSE THESE AT ALL RIGHT NOW (say so if asked):"]
        lines += [f"- {item}" for item in cannot_sense]

    if manual_excerpts:
        lines += [
            "",
            "OWNER MANUAL EXCERPTS (these WERE looked up for this question; you may cite their page numbers):",
            manual_excerpts,
        ]
    else:
        lines += ["", "No manual lookup was performed for this question, so do not claim to have read the manual."]

    return "\n".join(lines)


def default_state(
    engine_on: bool | None = None,
    obd_connected: bool = False,
    cameras_streaming: bool = False,
    parked: bool | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Current best-known state -> (facts, cannot_sense).

    Everything unproven is pushed into `cannot_sense` on purpose: an
    unknown must never silently read as a fact.
    """
    facts: dict[str, str] = {}
    cannot: list[str] = []

    if engine_on is None:
        cannot.append("whether your engine is running")
    else:
        facts["engine"] = "ON" if engine_on else "OFF"

    if parked is not None:
        facts["parked"] = "yes, you are stationary" if parked else "no, you are moving"

    if obd_connected:
        facts["obd"] = "connected, so live vehicle data is available"
    else:
        # Precise wording matters: on Aug 12 the model embellished a bare
        # "NOT connected yet" into "my OBD software is not built", right
        # after the software WAS built - a grounding fact must leave no
        # room for that. State exactly what exists and what is missing.
        facts["obd"] = (
            "your OBD reading software IS built, tested, and running on "
            "board, watching the diagnostic cable - but the cable link to "
            "the car is NOT up right now, so no live vehicle data yet")
        cannot += [
            "fuel level",
            "battery voltage or state of health",
            "tyre pressures",
            "coolant or outside temperature",
            "engine revs or road speed",
        ]

    if cameras_streaming:
        facts["cameras"] = "streaming to you"
    else:
        facts["cameras"] = "three dashcam cameras exist but are NOT streaming to you"
        cannot.append("anything you would see out of your cameras")

    facts["manual"] = ("your 489-page owner manual is indexed on board and can be searched, "
                       "but only counts as read when a lookup is actually run")
    return facts, cannot
