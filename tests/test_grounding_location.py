"""#15: the car claimed the Pi's location as its own.

Heard live 28 Aug: "I am parked safely at home ON YOUR DESK". The Pi was on
the desk; the car was not. The prompt carried a bare `location` fact derived
from which wifi the onboard computer was on, and a model speaking in first
person as the car read it as the car's own position.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from carwatch.grounding import build_system_prompt  # noqa: E402

HOST_FACT = "your onboard computer's whereabouts (NOT where you are parked)"


class ParkedLocationGrounding(unittest.TestCase):

    def test_rule_forbids_deriving_parked_position_from_the_computer(self):
        p = build_system_prompt(facts={"fuel": "58%"})
        self.assertIn("WHERE YOU ARE PARKED", p)
        self.assertIn("car source", p)
        # the specific failure that was heard out loud
        self.assertIn("desk", p, "the rule should name the desk case explicitly")

    def test_host_location_is_labelled_as_the_computers(self):
        p = build_system_prompt(facts={HOST_FACT: "at home, on home wifi"})
        self.assertIn("NOT where you are parked", p)

    def test_host_location_sorts_after_car_facts(self):
        # Models lead with what is listed first; the car's own state must come
        # before its computer's, or a status answer opens with the wrong thing.
        p = build_system_prompt(facts={HOST_FACT: "at home, on home wifi",
                                       "fuel": "58%", "tyres": "2.4 bar"})
        lines = [l for l in p.splitlines() if l.startswith("- ")]
        idx = {l.split(":")[0][2:]: i for i, l in enumerate(lines)}
        host = next(i for k, i in idx.items() if "onboard computer" in k)
        self.assertLess(idx["fuel"], host)
        self.assertLess(idx["tyres"], host)

    def test_no_bare_location_key_remains_in_the_home_branch(self):
        # A bare "location" is exactly what the model misread. The in-car
        # branches may still use it, because there the Pi IS in the car.
        import inspect
        from carwatch import agent
        src = inspect.getsource(agent)
        self.assertIn(HOST_FACT, src,
                      "the home-wifi branch no longer labels the fact as the "
                      "computer's; #15 will recur")
        self.assertNotIn('facts["location"] = "at home', src)


if __name__ == "__main__":
    unittest.main()
