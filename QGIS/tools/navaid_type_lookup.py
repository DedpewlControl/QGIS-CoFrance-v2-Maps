"""External typed navaid database download/cache and conservative matching."""
from collections import defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import time
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import (
    QgsApplication, QgsBlockingNetworkRequest, QgsCoordinateReferenceSystem,
    QgsDistanceArea, QgsFeatureRequest, QgsPointXY, QgsProcessingException,
)

_path = Path(__file__).with_name("sia_navaid_reference.py")
_spec = importlib.util.spec_from_file_location("_cofrance_sia_reference", _path)
_sia = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sia)
DATABASE_URL = _sia.DEFAULT_INDEX
TYPE_CODES = {"DME": "dme", "NDB": "ndb", "NDBDME": "ndb_dme", "TACAN": "tacan",
              "VOR": "vor", "VORDME": "vor_dme", "VORTAC": "vortac"}


def finite_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def parse_database(data):
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        required = {"ident", "type", "latitude_deg", "longitude_deg"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("CSV requires ident, type, latitude_deg and longitude_deg columns.")
        records = defaultdict(list)
        for row_number, row in enumerate(reader, 2):
            kind = TYPE_CODES.get(re.sub(r"[^A-Z0-9]", "", (row.get("type") or "").upper()))
            latitude, longitude = (finite_number(row.get(key)) for key in ("latitude_deg", "longitude_deg"))
            ident = (row.get("ident") or "").strip().upper()
            if not ident or not kind or latitude is None or longitude is None or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                continue
            dme_lat, dme_lon = (finite_number(row.get(key)) for key in ("dme_latitude_deg", "dme_longitude_deg"))
            frequencies = [value for key in ("frequency_khz", "dme_frequency_khz")
                           if (value := finite_number(row.get(key))) is not None and value > 0]
            records[ident].append({"id": row.get("id") or "row:" + str(row_number), "type": kind,
                "point": QgsPointXY(longitude, latitude),
                "dme_point": QgsPointXY(dme_lon, dme_lat) if dme_lat is not None and dme_lon is not None and -90 <= dme_lat <= 90 and -180 <= dme_lon <= 180 else None,
                "frequencies_khz": frequencies, "source_url": row.get("source_url", "")})
        if not records:
            raise ValueError("No valid typed navaids in the reference CSV.")
        return dict(records)
    except (UnicodeError, csv.Error, ValueError) as error:
        raise QgsProcessingException("Invalid navaid type database: " + str(error)) from error


def cache_directory():
    return Path(QgsApplication.qgisSettingsDirPath()) / "processing" / "cofrance_cache"


def fetch_page(url, feedback):
    request = QNetworkRequest(QUrl(url))
    request.setRawHeader(b"User-Agent", b"CoFrance-QGIS-navaid-types")
    request.setTransferTimeout(20000)
    network = QgsBlockingNetworkRequest()
    result = network.get(request, True, feedback)
    if feedback.isCanceled():
        raise QgsProcessingException("Canceled downloading navaid types.")
    if result != QgsBlockingNetworkRequest.NoError:
        raise QgsProcessingException("Navaid database download failed: " + network.errorMessage())
    reply = network.reply()
    status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
    if status is not None and not 200 <= int(status) < 300:
        raise QgsProcessingException("Navaid database returned HTTP " + str(status))
    return bytes(reply.content())


def fetch_database(feedback, index_url=DATABASE_URL, airports=()):
    cycle, urls = _sia.edition_sources(index_url, airports)
    records = []
    for number, url in enumerate(urls, 1):
        feedback.pushInfo("Reading SIA {} ({}/{}): {}".format(cycle, number, len(urls), url))
        data = fetch_page(url, feedback)
        page = parse_sia_page(data, url)
        if not page and (number == 1 or b"2.19" not in data):
            raise QgsProcessingException("No supported radio-navigation table found in SIA page: " + url)
        records.extend(page)
    return _sia.csv_bytes(records)


def parse_sia_page(data, url):
    return _sia.parse_sia_html(data, url)


def load_database(file, refresh, feedback, index_url=DATABASE_URL, airports=()):
    if file:
        path = Path(file)
        try:
            data = path.read_bytes()
        except OSError as error:
            raise QgsProcessingException("Cannot read typed navaid CSV: " + str(error)) from error
        return parse_database(data), {"source": str(path), "mode": "local_csv", "sha256": hashlib.sha256(data).hexdigest()}
    index_url = _sia.clean_index_url(index_url)
    cycle, urls = _sia.edition_sources(index_url, airports)
    cache_key = hashlib.sha256("\n".join(urls).encode("utf-8")).hexdigest()[:16]
    folder = cache_directory()
    path = folder / ("sia_navaids_" + cycle + "_" + cache_key + ".csv")
    metadata_path = path.with_suffix(".json")
    cached = None
    if path.is_file():
        try:
            cached = path.read_bytes()
            parse_database(cached)
        except (OSError, QgsProcessingException):
            cached = None
    fresh = cached is not None and 0 <= time.time() - path.stat().st_mtime < 86400
    mode = "cached"
    if refresh or not fresh:
        feedback.pushInfo("Downloading typed navaids from the selected SIA eAIP edition...")
        try:
            data = fetch_database(feedback, index_url, airports)
            records = parse_database(data)
        except QgsProcessingException:
            if feedback.isCanceled() or cached is None or refresh:
                raise
            feedback.pushWarning("Download unavailable; using the previously cached navaid database. See its download date in the report.")
            data = cached
            mode = "cached_stale"
        else:
            folder.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix("." + str(os.getpid()) + ".tmp")
            try:
                temporary.write_bytes(data)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            metadata_path.write_text(json.dumps({"downloaded_utc": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
            mode = "downloaded"
    else:
        data = cached
    metadata = {}
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    metadata.update(source=index_url, source_pages=urls, airac_cycle=cycle, cache_file=str(path), mode=mode,
                    sha256=hashlib.sha256(data).hexdigest(), source_kind="SIA eAIP published facility tables for the selected edition")
    return parse_database(data), metadata


def load_terminal_page(icao, index_url, refresh, feedback):
    """Cache each automatically selected AD 2 page independently, including valid empty tables."""
    cycle, urls = _sia.edition_sources(index_url, (icao,))
    url = urls[1]
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    path = cache_directory() / ("sia_ad219_" + cycle + "_" + icao + "_" + key + ".html")

    def validated(data):
        rows = parse_sia_page(data, url)
        if not rows and b"2.19" not in data:
            raise QgsProcessingException("No AD 2.19 radio-navigation section in " + url)
        return rows

    cached = None
    if path.is_file():
        try:
            cached = path.read_bytes()
            validated(cached)
        except (OSError, QgsProcessingException):
            cached = None
    fresh = cached is not None and 0 <= time.time() - path.stat().st_mtime < 86400
    mode = "cached"
    if refresh or not fresh:
        feedback.pushInfo("Reading supplementary SIA AD 2.19 for " + icao)
        try:
            data = fetch_page(url, feedback)
            validated(data)
        except QgsProcessingException:
            if feedback.isCanceled() or cached is None or refresh:
                raise
            feedback.pushWarning(icao + ": using cached AD 2.19 from the same edition after download failure.")
            data = cached
            mode = "cached_stale"
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix("." + str(os.getpid()) + ".tmp")
            try:
                temporary.write_bytes(data)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            mode = "downloaded"
    else:
        data = cached
    rows = validated(data)
    database = parse_database(_sia.csv_bytes(rows)) if rows else {}
    return database, {"airport": icao, "source": url, "airac_cycle": cycle, "mode": mode,
                      "cache_file": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                      "cached_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                      "reference_rows": len(rows)}


def classify_points(points, database, maximum_distance, tools, context, feedback, report_issues=True):
    measure = QgsDistanceArea()
    measure.setSourceCrs(QgsCoordinateReferenceSystem("EPSG:4326"), context.transformContext())
    measure.setEllipsoid("WGS84")
    frequencies = {}
    for stem in {point["master"] for point in points}:
        layer = tools.load_master(stem, context)
        ids = {point["fid"] for point in points if point["master"] == stem}
        for feature in layer.getFeatures(QgsFeatureRequest().setFilterFids(list(ids))):
            value = None
            for field, factor in (("freq_mhz", 1000), ("frequency_mhz", 1000), ("freq_khz", 1), ("frequency_khz", 1)):
                if field in layer.fields().names():
                    value = finite_number(feature[field])
                    if value is not None and value > 0:
                        value *= factor
                        break
                    value = None
            frequencies[(stem, feature.id())] = value
    resolved, issues, merged = [], [], 0
    for point in points:
        if feedback.isCanceled():
            raise QgsProcessingException("Canceled checking navaid types.")
        matches = []
        frequency = frequencies.get((point["master"], point["fid"]))
        nearby = []
        for reference in database.get(point["ident"], []):
            distance = min(measure.measureLine(point["point"], position) for position in
                           (reference["point"], reference["dme_point"]) if position is not None)
            if distance > maximum_distance:
                continue
            nearby.append(reference)
            if frequency is not None and reference["frequencies_khz"] and not any(abs(frequency - other) <= 1.0 for other in reference["frequencies_khz"]):
                continue
            matches.append((distance, reference))
        kinds = {reference["type"] for _, reference in matches}
        if len(kinds) != 1:
            reason = "conflicting database types" if len(kinds) > 1 else "frequency mismatch" if nearby else "no database match within distance"
            issues.append({"ident": point["ident"], "source": point["master"], "fid": point["fid"], "reason": reason})
            if report_issues:
                feedback.pushWarning(point["ident"] + ": " + reason)
            continue
        distance, reference = min(matches, key=lambda item: (item[0], str(item[1]["id"])))
        kind = reference["type"]
        classified = dict(point, navaid_type=kind, symbol_type="ndb" if kind == "ndb_dme" else kind,
                          type_source=reference.get("source_url") or "external_database", type_reference_id=str(reference["id"]), type_distance_m=distance)
        existing = next((item for item in resolved if item["type_reference_id"] == classified["type_reference_id"] and item["ident"] == point["ident"]), None)
        if existing is not None:
            # One external facility may appear in both the VOR/DME and NDB layers.
            # For a combined NDB-DME retain the NDB anchor when available.
            merged += 1
            if kind == "ndb_dme" and point["master"] == "LFXX_NDB":
                resolved[resolved.index(existing)] = classified
        else:
            resolved.append(classified)
    return resolved, issues, merged


def symbol_components(points):
    symbols = []
    for point in points:
        kinds = ("ndb", "dme") if point.get("navaid_type") == "ndb_dme" else (point["symbol_type"],)
        symbols.extend(dict(point, symbol_type=kind, symbol_component=kind) for kind in kinds)
    return symbols
