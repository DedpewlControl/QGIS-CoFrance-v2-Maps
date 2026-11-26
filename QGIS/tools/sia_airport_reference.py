"""SIA AD 2.12 runway surfaces and edition-specific airport classification cache."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import time
from qgis.core import QgsProcessingException


def load_module(filename, name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


network = load_module("navaid_type_lookup.py", "_cofrance_airport_network")
sia = network._sia
DEFAULT_INDEX = sia.DEFAULT_INDEX


def surface_type(text):
    value = " ".join(sia.normalized(word) for word in text.split())
    # Check negative forms before the word paved/revetu.
    unpaved = r"\b(?:unpaved|unsealed|grass|herbe|gazon|gravel|terre|graviers?|sable|sand|clay|earth|turf|laterite)\b|non\s*revetu|non\s*paved"
    paved = r"\b(?:asphalt|asphalte|concrete|beton|bituminous|bitumineux|bitume|enrobe|tarmac|paved|sealed|revetue?s?|macadam)\b"
    soft = re.search(unpaved, value)
    if soft:
        value = re.sub(r"non\s*revetu|non\s*paved|unsealed|unpaved", "", value)
        # A cell explicitly mixing both surfaces needs review.
        return None if re.search(paved, value) else "unpaved"
    if re.search(paved, value):
        return "paved"
    return None


def parse_runways(data):
    parser = sia.TableParser()
    try:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252")
        parser.feed(text)
    except (UnicodeError, ValueError) as error:
        raise QgsProcessingException("Cannot parse SIA runway HTML: " + str(error)) from error
    records = []
    for table in parser.tables:
        columns = None
        for row in sia.expanded_rows(table):
            headers = [sia.normalized(cell) for cell in row]
            ids = [i for i, item in enumerate(headers) if item in ("rwynr", "rwy", "runway", "nrdepiste", "pistenr")]
            dimensions = [i for i, item in enumerate(headers) if "dimensions" in item and "rwy" in item and "swy" not in item]
            surfaces = [i for i, item in enumerate(headers) if "surface" in item and ("rwy" in item or "piste" in item)]
            if ids and dimensions and surfaces:
                columns = ids[0], dimensions[0], surfaces[0]
                continue
            if columns is None or len(row) <= max(columns):
                continue
            end, dimension, surface = (row[index] for index in columns)
            if not re.fullmatch(r"(?:0[1-9]|[12][0-9]|3[0-6])[LRC]?(?:\s*[/,-]\s*(?:0[1-9]|[12][0-9]|3[0-6])[LRC]?)?", end.strip().upper()):
                continue
            size = re.search(r"(\d+(?:[.,]\d+)?)\s*[xX\u00d7]\s*\d", dimension)
            if not size:
                continue
            length = float(size[1].replace(",", "."))
            record = {"runway": end.strip().upper(), "length_m": length, "surface": surface,
                      "surface_type": surface_type(surface)}
            if length > 0 and record not in records:
                records.append(record)
    if not records:
        raise QgsProcessingException("No runway physical characteristics table (AD 2.12) found.")
    return records


def classification(records):
    kinds = {row["surface_type"] for row in records}
    if "paved" in kinds:
        return "aerodrome_paved_ticks"
    if kinds == {"unpaved"}:
        return "aerodrome_ticks"
    raise QgsProcessingException("Unrecognized runway surface; use the manual airport symbol override.")


def load_airport(icao, index_url, refresh, feedback, html_folder=""):
    cycle, pages = sia.edition_sources(index_url, (icao,))
    url = pages[1]
    key = hashlib.sha256(("airport-runways-v1:" + url).encode()).hexdigest()[:16]
    path = network.cache_directory() / ("sia_runways_" + cycle + "_" + icao + "_" + key + ".json")
    if html_folder:
        local = Path(html_folder) / ("FR-AD-2." + icao + "-fr-FR.html")
        try:
            data = local.read_bytes()
        except OSError as error:
            raise QgsProcessingException("Cannot read saved SIA page " + str(local) + ": " + str(error)) from error
        rows = parse_runways(data)
        return dict(icao=icao, symbol_type=classification(rows), runways=rows, airac_cycle=cycle,
                    source_url=url, mode="local_html", local_file=str(local), sha256=hashlib.sha256(data).hexdigest())
    cached = None
    if path.is_file():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached["source_url"] != url or cached["airac_cycle"] != cycle or not cached["runways"]:
                cached = None
            else:
                classification(cached["runways"])
        except (OSError, ValueError, KeyError, TypeError, QgsProcessingException):
            cached = None
    if cached and not refresh and 0 <= time.time() - path.stat().st_mtime < 86400:
        return dict(cached, mode="cached")
    feedback.pushInfo("Reading SIA AD 2.12 for " + icao + ": " + url)
    try:
        data = network.fetch_page(url, feedback)
        rows = parse_runways(data)
        result = dict(icao=icao, symbol_type=classification(rows), runways=rows, airac_cycle=cycle,
                      source_url=url, mode="downloaded", sha256=hashlib.sha256(data).hexdigest(),
                      downloaded_utc=datetime.now(timezone.utc).isoformat())
    except QgsProcessingException as error:
        if feedback.isCanceled() or not cached or refresh:
            raise QgsProcessingException(icao + ": " + str(error) + " Use a saved AD 2 HTML page or Manual symbol override if unavailable.") from error
        feedback.pushWarning(icao + ": download unavailable; using cached runway data from the same SIA edition.")
        return dict(cached, mode="cached_stale")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix("." + str(os.getpid()) + ".tmp")
    try:
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return result
