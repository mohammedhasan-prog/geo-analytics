"""Feature extraction and CRS-aware metric calculations."""

import logging
import zipfile
from pathlib import Path
from typing import Any

import fiona
from fiona.errors import FionaError
from pyproj import CRS, Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform

from app.repository import save_result

logger = logging.getLogger("geospatial_api.geoprocessing")


class ProcessingRejected(ValueError):
    """Raised when a stored upload cannot be interpreted as geospatial data."""


def _crs_for_layer(source: fiona.Collection, is_kml: bool) -> CRS | None:
    if source.crs_wkt:
        return CRS.from_wkt(source.crs_wkt)
    if source.crs:
        return CRS.from_user_input(source.crs)
    # KML uses longitude/latitude in WGS84 by definition.
    return CRS.from_epsg(4326) if is_kml else None


def _crs_label(crs: CRS | None) -> str | None:
    if crs is None:
        return None
    return crs.to_string()


def _local_metric_crs(geometry: Any, source_crs: CRS) -> CRS:
    """Choose a local projected CRS centered on this feature for metric work."""
    to_wgs84 = Transformer.from_crs(source_crs, CRS.from_epsg(4326), always_xy=True)
    centroid = geometry.representative_point()
    longitude, latitude = to_wgs84.transform(centroid.x, centroid.y)

    if -80 <= latitude <= 84:
        zone = max(1, min(60, int((longitude + 180) // 6) + 1))
        epsg = (32600 if latitude >= 0 else 32700) + zone
        return CRS.from_epsg(epsg)

    # A local Lambert azimuthal equal-area projection covers polar features.
    return CRS.from_proj4(
        f"+proj=laea +lat_0={latitude} +lon_0={longitude} "
        "+datum=WGS84 +units=m +no_defs +type=crs"
    )


def _measure(geometry: Any, source_crs: CRS | None) -> tuple[dict[str, Any] | None, str]:
    geom_type = geometry.geom_type
    if geom_type not in {"Polygon", "MultiPolygon", "LineString", "MultiLineString"}:
        return None, "UNSUPPORTED_GEOMETRY"
    if source_crs is None:
        return None, "CRS_UNKNOWN"
    if geometry.is_empty:
        return None, "EMPTY_GEOMETRY"

    try:
        metric_crs = _local_metric_crs(geometry, source_crs)
        transformer = Transformer.from_crs(source_crs, metric_crs, always_xy=True)
        projected = transform(transformer.transform, geometry)
        if geom_type in {"Polygon", "MultiPolygon"}:
            return {
                "type": "area",
                "value": projected.area,
                "unit": "square_metres",
                "crs": metric_crs.to_string(),
            }, "MEASURED"
        return {
            "type": "length",
            "value": projected.length,
            "unit": "metres",
            "crs": metric_crs.to_string(),
        }, "MEASURED"
    except (ValueError, OverflowError) as exc:
        logger.warning("feature_measurement_failed", extra={"geometry_type": geom_type, "error": str(exc)})
        return None, "MEASUREMENT_FAILED"


def process_file(file_id: str, filename: str, size_bytes: int, path: Path) -> dict[str, Any]:
    """Parse an upload, measure supported features, and persist the result."""
    is_kml = path.suffix.casefold() == ".kml"
    source_path = str(path)
    vfs = None
    if path.suffix.casefold() == ".zip":
        vfs = f"zip://{path.resolve().as_posix()}"

    features: list[dict[str, Any]] = []
    source_crs_labels: set[str] = set()
    feature_index = 0

    try:
        layer_names = fiona.listlayers(source_path, vfs=vfs)
        if not layer_names:
            raise ProcessingRejected("No readable geospatial layers were found")

        for layer_name in layer_names:
            with fiona.open(source_path, layer=layer_name, vfs=vfs) as source:
                source_crs = _crs_for_layer(source, is_kml)
                crs_label = _crs_label(source_crs)
                if crs_label:
                    source_crs_labels.add(crs_label)

                for raw_feature in source:
                    feature_id = str(raw_feature.id) if raw_feature.id is not None else str(feature_index)
                    properties = dict(raw_feature.properties or {})
                    raw_geometry = raw_feature.geometry
                    if raw_geometry is None:
                        features.append({
                            "index": feature_index,
                            "id": feature_id,
                            "layer": layer_name,
                            "geometry_type": None,
                            "geometry": None,
                            "crs": crs_label,
                            "properties": properties,
                            "measurement": None,
                            "measurement_status": "NO_GEOMETRY",
                        })
                        feature_index += 1
                        continue

                    geometry = shape(raw_geometry)
                    measurement, measurement_status = _measure(geometry, source_crs)
                    features.append({
                        "index": feature_index,
                        "id": feature_id,
                        "layer": layer_name,
                        "geometry_type": geometry.geom_type,
                        "geometry": mapping(geometry),
                        "crs": crs_label,
                        "properties": properties,
                        "measurement": measurement,
                        "measurement_status": measurement_status,
                    })
                    feature_index += 1
    except ProcessingRejected:
        raise
    except (FionaError, OSError, ValueError, zipfile.BadZipFile) as exc:
        raise ProcessingRejected("The uploaded file could not be read as valid geospatial data") from exc

    if feature_index == 0:
        raise ProcessingRejected("The geospatial file contains no features")

    if len(source_crs_labels) == 1:
        file_crs = next(iter(source_crs_labels))
    elif len(source_crs_labels) > 1:
        file_crs = "MIXED"
    else:
        file_crs = None

    info = {
        "id": file_id,
        "filename": filename,
        "size_bytes": size_bytes,
        "feature_count": feature_index,
        "crs": file_crs,
        "status": "COMPLETED",
    }
    save_result(info, features)
    return info
