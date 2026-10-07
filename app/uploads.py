"""Streaming upload storage and lightweight format validation."""

import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

CHUNK_SIZE = 1024 * 1024
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_SIZE_BYTES", str(100 * 1024 * 1024)))
UPLOAD_DIRECTORY = Path(os.getenv("UPLOAD_DIRECTORY", "data/uploads"))
ALLOWED_EXTENSIONS = {".kml", ".zip"}


class UploadRejected(ValueError):
    """Raised when an upload is too large, unsupported, or malformed."""


def _validate_kml(path: Path) -> None:
    try:
        root_name = None
        for event, element in ET.iterparse(path, events=("start", "end")):
            if root_name is None and event == "start":
                root_name = element.tag.rsplit("}", 1)[-1].lower()
                if root_name != "kml":
                    raise UploadRejected("KML file must have a <kml> root element")
            elif event == "end":
                element.clear()
        if root_name is None:
            raise UploadRejected("KML document is empty")
    except ET.ParseError as exc:
        raise UploadRejected("Malformed KML document") from exc


def _validate_shapefile_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            shapefile_keys = {
                (
                    Path(entry.filename.replace("\\", "/")).parent.as_posix().casefold(),
                    Path(entry.filename.replace("\\", "/")).stem.casefold(),
                )
                for entry in entries
                if Path(entry.filename.replace("\\", "/")).suffix.casefold() == ".shp"
            }
            if not shapefile_keys:
                raise UploadRejected("ZIP archive does not contain a Shapefile (.shp)")

            component_exts = {".shx", ".dbf"}
            for parent, stem in shapefile_keys:
                found = {
                    Path(entry.filename.replace("\\", "/")).suffix.casefold()
                    for entry in entries
                    if (
                        Path(entry.filename.replace("\\", "/")).parent.as_posix().casefold(),
                        Path(entry.filename.replace("\\", "/")).stem.casefold(),
                    )
                    == (parent, stem)
                }
                if not component_exts.issubset(found):
                    raise UploadRejected("Shapefile ZIP must include matching .shp, .shx, and .dbf files")

            if len(entries) > 10_000:
                raise UploadRejected("ZIP archive contains too many files")
            if any(entry.flag_bits & 0x1 for entry in entries):
                raise UploadRejected("Encrypted ZIP archives are not supported")
    except zipfile.BadZipFile as exc:
        raise UploadRejected("Malformed ZIP archive") from exc


async def save_upload(upload: UploadFile) -> dict[str, str | int]:
    """Stream an uploaded file to disk, enforce a size cap, and validate it."""
    original_name = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    extension = Path(original_name).suffix.casefold()
    if not original_name or extension not in ALLOWED_EXTENSIONS:
        raise UploadRejected("Only .kml and zipped Shapefile (.zip) uploads are supported")

    UPLOAD_DIRECTORY.mkdir(parents=True, exist_ok=True)
    file_id = uuid4().hex
    stored_path = UPLOAD_DIRECTORY / f"{file_id}{extension}"
    size = 0

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=UPLOAD_DIRECTORY, prefix=f".{file_id}.", delete=False
        ) as temp_file:
            temp_path = Path(temp_file.name)
            while chunk := await upload.read(CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise UploadRejected(f"File exceeds the {MAX_UPLOAD_BYTES}-byte upload limit")
                temp_file.write(chunk)

        if size == 0:
            raise UploadRejected("Uploaded file is empty")

        if extension == ".kml":
            _validate_kml(temp_path)
        else:
            _validate_shapefile_archive(temp_path)

        shutil.move(str(temp_path), str(stored_path))
    except Exception:
        if "temp_path" in locals():
            temp_path.unlink(missing_ok=True)
        stored_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    return {
        "id": file_id,
        "filename": original_name,
        "size_bytes": size,
        "status": "RECEIVED",
    }
