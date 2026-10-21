"""Offline tests for Cambodia fuel and EV location search."""
import unittest
from unittest.mock import patch

import httpx

import location_service
from location_service import LocationSearchError, LocationService


def _station(osm_id, name, kind, lat, lon, **tags):
    element_tags = {"name": name, "amenity": "charging_station" if kind == "ev" else "fuel"}
    element_tags.update(tags)
    return location_service._parse_element({
        "type": "node",
        "id": osm_id,
        "lat": lat,
        "lon": lon,
        "tags": element_tags,
    })


class TestLocationService(unittest.TestCase):
    def setUp(self):
        self.service = LocationService()

    def test_distance_calculation(self):
        self.assertAlmostEqual(
            location_service.distance_km(0, 0, 1, 0),
            111.195,
            places=2,
        )

    def test_normalizes_khmer_and_english_queries(self):
        khmer = location_service.normalize_query("រកកន្លែងសាកឡាន EV នៅកំពង់ចាម")
        english = location_service.normalize_query("Find EV chargers near Siem Reap")
        self.assertEqual(khmer["kind"], "ev")
        self.assertEqual(khmer["location"], "kampong cham")
        self.assertEqual(english["kind"], "ev")
        self.assertEqual(english["location"], "siem reap")

    def test_normalizes_short_khmer_fuel_and_ev_queries(self):
        fuel = location_service.normalize_query("សាំងនៅកំពត")
        ev = location_service.normalize_query("សាកភ្លើងនៅភ្នំពេញ")
        english = location_service.normalize_query("EV charging station in Phnom Penh")
        charge_points = location_service.normalize_query("Find charge points in Siem Reap")

        self.assertEqual(fuel, {"kind": "fuel", "location": "kampot", "operator": None})
        self.assertEqual(ev, {"kind": "ev", "location": "phnom penh", "operator": None})
        self.assertEqual(english["location"], "phnom penh")
        self.assertEqual(charge_points["location"], "siem reap")

    def test_fuel_search_filters_nearest_first_and_builds_directions_links(self):
        stations = [
            _station(1, "Far Fuel", "fuel", 11.1, 104.9),
            _station(2, "PTT Phnom Penh", "fuel", 11.01, 104.01, brand="PTT"),
            _station(3, "Nearby Charger", "ev", 11.005, 104.005),
        ]
        with patch.object(self.service, "_fetch_stations", return_value=stations):
            result = self.service.search("រក PTT ជិតខ្ញុំ", lat=11, lon=104)

        self.assertEqual(result["status"], "ok")
        self.assertEqual([item["name"] for item in result["stations"]], ["PTT Phnom Penh"])
        self.assertGreater(result["stations"][0]["distance_km"], 0)
        self.assertTrue(result["stations"][0]["maps_url"].startswith(
            "https://www.google.com/maps/dir/?"
        ))
        self.assertIn("destination=11.01%2C104.01", result["stations"][0]["maps_url"])

    def test_ev_search_filters_connector_and_charging_speed(self):
        stations = [
            _station(
                4, "CCS2 Fast", "ev", 11.005, 104.005,
                **{"socket:type2_combo": "2", "charging_station:output": "60 kW"},
            ),
            _station(
                5, "Type 2 AC", "ev", 11.006, 104.006,
                **{"socket:type2": "2", "charging_station:output": "22 kW"},
            ),
        ]
        with patch.object(self.service, "_geocode", return_value=(11, 104)), patch.object(
            self.service, "_fetch_stations", return_value=stations
        ):
            result = self.service.search(
                "Find fast CCS2 EV chargers near Siem Reap",
                lat=11,
                lon=104,
            )

        self.assertEqual([item["name"] for item in result["stations"]], ["CCS2 Fast"])
        self.assertEqual(result["stations"][0]["connectors"], ["CCS2"])
        self.assertEqual(result["stations"][0]["power"], "60 kW")

    def test_ev_fast_filter_reads_standard_per_connector_power_tags(self):
        station = _station(
            9,
            "Connector-tagged charger",
            "ev",
            11.005,
            104.005,
            **{
                "socket:type2_combo": "2",
                "socket:type2_combo:output": "60 kW",
                "socket:type2:output": "22 kW",
            },
        )
        with patch.object(self.service, "_fetch_stations", return_value=[station]):
            result = self.service.search("Find fast CCS2 EV chargers", lat=11, lon=104)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stations"][0]["name"], "Connector-tagged charger")
        self.assertEqual(result["stations"][0]["connectors"], ["CCS2"])
        self.assertIn("CCS2: 60 kW", result["stations"][0]["power"])

    def test_typed_location_is_geocoded_before_search(self):
        with patch.object(self.service, "_geocode", return_value=(13.36, 103.86)) as geocode, patch.object(
            self.service, "_fetch_stations", return_value=[]
        ) as fetch:
            result = self.service.search("Find fuel stations in Siem Reap")
        geocode.assert_called_once_with("siem reap")
        fetch.assert_called_once()
        self.assertEqual(result["status"], "empty")

    def test_missing_location_and_empty_results(self):
        missing = self.service.search("រកកន្លែងចាក់សាំងជិតខ្ញុំ")
        self.assertEqual(missing["status"], "missing_location")
        with patch.object(self.service, "_fetch_stations", return_value=[]):
            empty = self.service.search("Find EV chargers", lat=11, lon=104)
        self.assertEqual(empty["status"], "empty")
        self.assertEqual(empty["stations"], [])

    def test_geocoding_and_station_provider_errors_are_reported(self):
        with patch.object(self.service, "_geocode", side_effect=httpx.TimeoutException("timeout")):
            with self.assertRaises(LocationSearchError):
                self.service.search("Find EV chargers near Siem Reap")
        with patch.object(self.service, "_fetch_stations", side_effect=httpx.ConnectError("offline")):
            with self.assertRaises(LocationSearchError):
                self.service.search("រក PTT ជិតខ្ញុំ", lat=11, lon=104)

    def test_distance_radius_filter_and_missing_coordinates(self):
        stations = [
            _station(6, "Near", "fuel", 11.01, 104),
            _station(7, "Outside", "fuel", 11.5, 104),
            location_service._parse_element({
                "type": "way",
                "id": 8,
                "tags": {"amenity": "fuel", "name": "No coordinates"},
            }),
        ]
        with patch.object(self.service, "_geocode", return_value=(11, 104)), patch.object(
            self.service, "_fetch_stations", return_value=stations
        ):
            result = self.service.search(
                "Find fuel stations within 5 km near Phnom Penh",
                lat=11,
                lon=104,
            )
        self.assertEqual([item["name"] for item in result["stations"]], ["Near"])


if __name__ == "__main__":
    unittest.main()
