"""#52: /ask answers began with the prompt's own worksheet.

Observed on VTA 14 Sep: the reply opened with the verbatim
"- KNOWN FACTS: OBD is running but cable NOT U..." before the actual answer.
The driver should get the answer, not the scaffold.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from carwatch.grounding import (  # noqa: E402
    CANNOT_HEADING, FACTS_HEADING, build_system_prompt, strip_scaffold,
)


class ScaffoldLeak(unittest.TestCase):

    def test_the_reported_leak_is_removed(self):
        out = strip_scaffold(
            "- KNOWN FACTS: OBD is running but cable NOT UP\n"
            "- fuel: 58%\n\nYour tyres look fine, Petrus.")
        self.assertEqual(out, "Your tyres look fine, Petrus.")

    def test_a_real_echoed_heading_and_its_bullets_go(self):
        # Build the scaffold from the prompt itself, so this test tracks the
        # real wording rather than a copy that can drift.
        p = build_system_prompt(facts={"fuel": "58%", "tyres": "2.4 bar"})
        lines = p.splitlines()
        start = next(i for i, l in enumerate(lines) if l.startswith(FACTS_HEADING))
        echoed = "\n".join(lines[start:start + 4]) + "\n\nHalf a tank, Petrus."
        self.assertEqual(strip_scaffold(echoed), "Half a tank, Petrus.")

    def test_reasoning_blocks_are_removed(self):
        self.assertEqual(
            strip_scaffold("<think>weighing it up</think>Your tyres look fine."),
            "Your tyres look fine.")

    def test_ordinary_prose_is_untouched(self):
        for s in ("I cannot sense that yet.",
                  "I only state what appears in KNOWN FACTS, and that is half a tank.",
                  "Your coolant is 90 degrees."):
            self.assertEqual(strip_scaffold(s), s)

    def test_never_returns_empty(self):
        # Over-stripping must not silence the car. A scaffolded answer is bad;
        # no answer at all is worse.
        only = "- " + FACTS_HEADING + ": only this"
        self.assertTrue(strip_scaffold(only))
        self.assertEqual(strip_scaffold(""), "")

    def test_prompt_and_stripper_share_the_headings(self):
        # If someone reworded the prompt, the stripper must move with it.
        p = build_system_prompt(facts={"fuel": "58%"},
                                cannot_sense=["outside temperature"])
        self.assertIn(FACTS_HEADING, p)
        self.assertIn(CANNOT_HEADING, p)


if __name__ == "__main__":
    unittest.main()
