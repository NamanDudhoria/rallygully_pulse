
import unittest

from app.domain.venue import Venue, Facility


class TestVenueFacility(unittest.TestCase):

    def setUp(self):
        self.venue_a = Venue("Dwarka", "Delhi", "Active")
        self.venue_b = Venue("Noida", "Delhi", "Active")

    def test_facility_added_to_own_venue(self):
        facility = Facility(
            "Court 1", "Pickleball", "Active", 800, self.venue_a
        )

        self.venue_a.add_facility(facility)

        self.assertIn(facility, self.venue_a.facilities)

    def test_facility_cannot_be_added_to_another_venue(self):
        facility = Facility(
            "Court 1", "Pickleball", "Active", 800, self.venue_a
        )

        with self.assertRaises(ValueError):
            self.venue_b.add_facility(facility)

    def test_non_facility_object_is_rejected(self):
        with self.assertRaises(ValueError):
            self.venue_a.add_facility("Court 1")

    def test_facility_requires_valid_venue(self):
        with self.assertRaises(ValueError):
            Facility(
                "Court 1", "Pickleball", "Active", 800, "Dwarka"
            )


if __name__ == "__main__":
    unittest.main()
