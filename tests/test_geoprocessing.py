"""Unit tests for CRS-aware geometry measurements."""

import unittest

from pyproj import CRS
from shapely.geometry import LineString, Point, Polygon

from app.geoprocessing import _measure


class GeometryMeasurementTests(unittest.TestCase):
    def test_polygon_area_is_reported_in_square_metres(self) -> None:
        polygon = Polygon([(0, 0), (0.001, 0), (0.001, 0.001), (0, 0.001)])
        measurement, status = _measure(polygon, CRS.from_epsg(4326))

        self.assertEqual(status, "MEASURED")
        self.assertEqual(measurement["type"], "area")
        self.assertEqual(measurement["unit"], "square_metres")
        self.assertGreater(measurement["value"], 10_000)
        self.assertLess(measurement["value"], 13_000)

    def test_line_length_is_reported_in_metres(self) -> None:
        line = LineString([(0, 0), (0.01, 0)])
        measurement, status = _measure(line, CRS.from_epsg(4326))

        self.assertEqual(status, "MEASURED")
        self.assertEqual(measurement["type"], "length")
        self.assertEqual(measurement["unit"], "metres")
        self.assertGreater(measurement["value"], 1_000)
        self.assertLess(measurement["value"], 1_200)

    def test_points_are_reported_as_unsupported(self) -> None:
        measurement, status = _measure(Point(0, 0), CRS.from_epsg(4326))

        self.assertIsNone(measurement)
        self.assertEqual(status, "UNSUPPORTED_GEOMETRY")

    def test_missing_crs_does_not_claim_a_metric_measurement(self) -> None:
        measurement, status = _measure(LineString([(0, 0), (1, 0)]), None)

        self.assertIsNone(measurement)
        self.assertEqual(status, "CRS_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
