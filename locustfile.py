"""Locust profile for concurrent KML measurement uploads.

Each task creates a small, valid polygon or line KML with unique coordinates so
every request exercises parsing and measurement instead of hitting the cache.
"""

from itertools import count

from locust import HttpUser, between, task

_sequence = count()


def _polygon_kml(sequence: int) -> bytes:
    longitude = (sequence % 3600) / 1000
    latitude = (sequence % 1200) / 1000
    east = longitude + 0.001
    north = latitude + 0.001
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>
  <name>benchmark-{sequence}</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
    {longitude},{latitude} {east},{latitude} {east},{north} {longitude},{north} {longitude},{latitude}
  </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>'''.encode()


def _line_kml(sequence: int) -> bytes:
    longitude = (sequence % 3600) / 1000
    latitude = (sequence % 1200) / 1000
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>
  <name>benchmark-{sequence}</name><LineString><coordinates>
    {longitude},{latitude} {longitude + 0.01},{latitude}
  </coordinates></LineString></Placemark></Document></kml>'''.encode()


class MeasurementApiUser(HttpUser):
    wait_time = between(0, 0.2)

    @task
    def upload_kml(self) -> None:
        sequence = next(_sequence)
        is_polygon = sequence % 2 == 0
        filename = f"benchmark-{sequence}.kml"
        payload = _polygon_kml(sequence) if is_polygon else _line_kml(sequence)
        response = self.client.post(
            "/api/v1/measure",
            files={"file": (filename, payload, "application/vnd.google-earth.kml+xml")},
            name="POST /api/v1/measure (unique polygon and line KML)",
        )
        if response.status_code != 201:
            response.failure(f"Expected 201, got {response.status_code}: {response.text[:200]}")
            return

        try:
            body = response.json()
            if body.get("status") != "COMPLETED" or body.get("feature_count") != 1:
                response.failure("Upload response did not contain one completed feature")
        except (ValueError, AttributeError):
            response.failure("Upload response was not valid JSON")

    @task(5)
    def health_check(self) -> None:
        response = self.client.get("/health", name="GET /health")
        if response.status_code != 200 or response.json().get("status") != "ok":
            response.failure(f"Health check failed: HTTP {response.status_code}")
