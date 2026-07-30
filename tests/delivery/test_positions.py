"""fli.delivery.positions — event to holding edges.

The rule under test is when the system is willing to tell a PM that a holding
faces a threat or a tailwind. Getting that wrong is the most expensive kind of
error this system can make, so the bar is deliberately high.
"""
import unittest

from fli.delivery.positions import direction_for


class TestDirectionNeedsAMechanism(unittest.TestCase):
    def test_no_channel_means_no_direction(self):
        """Exposure alone means the text mentions something we own. That is a
        candidate, not a signal."""
        self.assertEqual(direction_for("IREN", None, ""), "unclear")

    def test_a_keyword_match_is_not_a_mechanism(self):
        """THE CASE THIS EXISTS FOR. The lexicon scores F1 0.195 and returned a
        phone codec that 'increased power usage by 14%' as a datacenter signal,
        because `power` is an ambient word here. A direction from that would be
        confidently wrong in front of a PM."""
        self.assertEqual(
            direction_for("IREN", "energy_datacenter", "lexicon"), "unclear")

    def test_the_same_channel_from_the_classifier_is_still_only_a_mechanism(self):
        """Provenance gets the channel trusted, but a demand channel carries no
        sign — see TestDemandChannelsCarryNoSign."""
        self.assertEqual(
            direction_for("IREN", "energy_datacenter", "classifier"), "unclear")


class TestDemandChannelsCarryNoSign(unittest.TestCase):
    """A channel establishes the MECHANISM, not the direction.

        "Mistral is building a 10 MW data center"        demand UP
        "Meta's scheduler saved 3.28 megawatts"          demand DOWN
        "Gemma 4 12B matches a 26B model while smaller"  demand DOWN

    All three are the same channel and point opposite ways. Both wrong calls
    were emitted as `tailwind` before this was made explicit."""

    def test_demand_channels_state_no_direction(self):
        for ch in ("compute_memory", "energy_datacenter"):
            for t in ("MU", "TSM", "IREN"):
                self.assertEqual(direction_for(t, ch, "classifier"), "unclear",
                                 f"{t}/{ch}")


class TestDirectionMatchesWhatTheHoldingIs(unittest.TestCase):
    def test_a_supplier_is_not_threatened_by_displacement(self):
        """Micron does not lose when a lab enters a consumer market."""
        self.assertEqual(
            direction_for("MU", "competitive_displacement", "classifier"),
            "unclear")

    def test_an_incumbent_is_threatened_by_displacement(self):
        self.assertEqual(
            direction_for("HNGE", "competitive_displacement", "classifier"),
            "threat")


class TestAmbiguousChannelsStayUnclear(unittest.TestCase):
    def test_channels_that_cut_both_ways_state_no_direction(self):
        """A licensing deal is revenue for the data holder and cost for the
        lab; talent movement says nothing about a public position at all."""
        for ch in ("data_economics", "talent_movement"):
            self.assertEqual(direction_for("RDDT", ch, "classifier"), "unclear", ch)


if __name__ == "__main__":
    unittest.main()
