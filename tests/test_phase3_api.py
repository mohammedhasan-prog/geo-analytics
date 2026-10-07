"""Phase 3 API smoke tests using temporary geospatial inputs."""

import os
import shutil
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import numpy as np
from pyogrio.raw import write
from shapely import to_wkb
from shapely.geometry import LineString

_TEST_ROOT = Path(__file__).resolve().parent / f".phase3-test-{uuid4().hex}"
_TEST_ROOT.mkdir()
os.environ["UPLOAD_DIRECTORY"] = str(_TEST_ROOT / "uploads")
os.environ["DATABASE_PATH"] = str(_TEST_ROOT / "results.sqlite3")

from fastapi.testclient import TestClient  # noqa: E402

from app.jobs import ASYNC_THRESHOLD_BYTES, run_measurement_job  # noqa: E402
from app.main import app  # noqa: E402


class Phase3ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        shutil.rmtree(_TEST_ROOT)

    def test_health_and_unknown_file(self) -> None:
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/api/files/missing/").status_code, 404)

    def test_kml_polygon_is_measured_and_persisted(self) -> None:
        kml = b'''<?xml version="1.0" encoding="UTF-8"?>
        <kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>
          <name>sample polygon</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
            0,0 0.001,0 0.001,0.001 0,0.001 0,0
          </coordinates></LinearRing></outerBoundaryIs></Polygon>
        </Placemark></Document></kml>'''
        response = self.client.post(
            "/api/v1/measure", files={"file": ("sample.kml", kml, "application/vnd.google-earth.kml+xml")}
        )
        self.assertEqual(response.status_code, 201, response.text)
        info = response.json()
        self.assertEqual(info["feature_count"], 1)
        self.assertEqual(info["crs"], "EPSG:4326")

        saved_info = self.client.get(f"/api/files/{info['id']}/")
        self.assertEqual(saved_info.status_code, 200)
        self.assertEqual(saved_info.json()["status"], "COMPLETED")

        results = self.client.get(f"/api/files/{info['id']}/measurements/").json()
        feature = results["features"][0]
        self.assertEqual(feature["geometry_type"], "Polygon")
        self.assertEqual(feature["properties"]["Name"], "sample polygon")
        self.assertEqual(feature["measurement"]["type"], "area")
        self.assertEqual(feature["measurement"]["unit"], "square_metres")
        self.assertGreater(feature["measurement"]["value"], 10_000)

    def test_kml_point_is_returned_without_a_measurement(self) -> None:
        kml = b'''<?xml version="1.0" encoding="UTF-8"?>
        <kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>
          <name>sample point</name><Point><coordinates>2,48</coordinates></Point>
        </Placemark></Document></kml>'''
        response = self.client.post(
            "/api/v1/measure", files={"file": ("point.kml", kml, "application/vnd.google-earth.kml+xml")}
        )
        self.assertEqual(response.status_code, 201, response.text)
        feature = self.client.get(
            f"/api/files/{response.json()['id']}/measurements/"
        ).json()["features"][0]
        self.assertEqual(feature["geometry_type"], "Point")
        self.assertIsNone(feature["measurement"])
        self.assertEqual(feature["measurement_status"], "UNSUPPORTED_GEOMETRY")

    def test_content_hash_cache_reuses_results_for_new_upload_id(self) -> None:
        kml = b'''<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>
          <name>cached polygon</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
          0,0 0.001,0 0.001,0.001 0,0.001 0,0
          </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>'''
        cache: dict[str, dict] = {}

        def cache_get(digest: str):
            return cache.get(digest)

        def cache_set(digest: str, value: dict):
            cache[digest] = value

        with patch("app.main.get_cached_measurement", side_effect=cache_get), patch(
            "app.main.set_cached_measurement", side_effect=cache_set
        ):
            first = self.client.post("/api/v1/measure", files={"file": ("original.kml", kml)})
            self.assertEqual(first.status_code, 201, first.text)
            with patch("app.main.process_file", side_effect=AssertionError("cache hit should skip processing")):
                second = self.client.post("/api/v1/measure", files={"file": ("copy.kml", kml)})

        self.assertEqual(second.status_code, 201, second.text)
        reused = second.json()
        self.assertTrue(reused["cache_hit"])
        self.assertNotEqual(first.json()["id"], reused["id"])
        self.assertEqual(reused["filename"], "copy.kml")
        self.assertEqual(self.client.get(f"/api/files/{reused['id']}/").json()["filename"], "copy.kml")
        self.assertEqual(len(self.client.get(f"/api/files/{reused['id']}/measurements/").json()["features"]), 1)

    def test_large_cached_upload_skips_queue(self) -> None:
        kml = b'''<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>
          <name>large cached</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
          0,0 0.001,0 0.001,0.001 0,0.001 0,0
          </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>''' + b" " * (ASYNC_THRESHOLD_BYTES + 1)
        cached = {"feature_count": 0, "crs": "EPSG:4326", "features": []}
        with patch("app.main.get_cached_measurement", return_value=cached), patch(
            "app.main.enqueue_measurement"
        ) as enqueue:
            response = self.client.post("/api/v1/measure", files={"file": ("large.kml", kml)})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.json()["cache_hit"])
        enqueue.assert_not_called()

    def _make_shapefile_zip(self, directory: Path, crs: str | None) -> Path:
        shapefile_path = directory / "routes.shp"
        write(
            shapefile_path,
            np.asarray([to_wkb(LineString([(0, 0), (0.01, 0)]))], dtype=object),
            [np.asarray(["route A"], dtype=object)],
            ["name"],
            geometry_type="LineString",
            crs=crs,
        )
        archive_path = directory / "routes.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for component in directory.glob("routes.*"):
                if component.suffix.casefold() != ".zip":
                    archive.write(component, component.name)
        return archive_path

    def test_shapefile_line_length_and_unknown_crs(self) -> None:
        directory = _TEST_ROOT / "shape-with-crs"
        directory.mkdir()
        archive_path = self._make_shapefile_zip(directory, "EPSG:4326")
        with archive_path.open("rb") as stream:
            response = self.client.post(
                "/api/v1/measure", files={"file": ("routes.zip", stream, "application/zip")}
            )
        self.assertEqual(response.status_code, 201, response.text)
        data = self.client.get(f"/api/files/{response.json()['id']}/measurements/").json()
        feature = data["features"][0]
        self.assertEqual(feature["geometry_type"], "LineString")
        self.assertEqual(feature["measurement"]["type"], "length")
        self.assertEqual(feature["measurement"]["unit"], "metres")
        self.assertGreater(feature["measurement"]["value"], 1_000)

        directory = _TEST_ROOT / "shape-without-crs"
        directory.mkdir()
        archive_path = self._make_shapefile_zip(directory, None)
        with archive_path.open("rb") as stream:
            response = self.client.post(
                "/api/v1/measure", files={"file": ("unknown-crs.zip", stream, "application/zip")}
            )
        self.assertEqual(response.status_code, 201, response.text)
        feature = self.client.get(
            f"/api/files/{response.json()['id']}/measurements/"
        ).json()["features"][0]
        self.assertIsNone(feature["measurement"])
        self.assertEqual(feature["measurement_status"], "CRS_UNKNOWN")

    def test_rejects_malformed_upload(self) -> None:
        response = self.client.post(
            "/api/v1/measure", files={"file": ("broken.kml", b"<not-kml>", "application/xml")}
        )
        self.assertEqual(response.status_code, 400)

    def test_large_upload_is_queued_and_poll_returns_final_result(self) -> None:
        prefix = b'''<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>
          <name>async polygon</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
          0,0 0.001,0 0.001,0.001 0,0.001 0,0
          </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>'''
        padding = b"<!--" + b"x" * (ASYNC_THRESHOLD_BYTES + 1) + b"-->"
        payload = prefix + padding + b"</Document></kml>"

        with patch("app.main.enqueue_measurement") as enqueue:
            response = self.client.post(
                "/api/v1/measure",
                files={"file": ("large.kml", payload, "application/vnd.google-earth.kml+xml")},
            )

        self.assertEqual(response.status_code, 202, response.text)
        accepted = response.json()
        self.assertEqual(accepted["status"], "QUEUED")
        self.assertEqual(enqueue.call_count, 1)

        queued = self.client.get(accepted["status_url"])
        self.assertEqual(queued.status_code, 200)
        self.assertEqual(queued.json()["status"], "QUEUED")

        # Run the captured worker task directly; only Redis publication is mocked.
        run_measurement_job(*enqueue.call_args.args)
        completed = self.client.get(accepted["status_url"])
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "COMPLETED")
        self.assertEqual(completed.json()["result"]["file"]["feature_count"], 1)
        self.assertEqual(len(completed.json()["result"]["features"]), 1)


if __name__ == "__main__":
    unittest.main()
