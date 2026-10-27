"""Parse published SIA eAIP radio-navigation facility tables without extra packages."""
import csv
from html.parser import HTMLParser
import io
import re
import unicodedata
from urllib.parse import urlsplit
from qgis.core import QgsProcessingException

DEFAULT_INDEX = "https://www.sia.aviation-civile.gouv.fr/media/dvd/eAIP_01_OCT_2026/FRANCE/AIRAC-2026-10-01/html/index-fr-FR.html"
TYPE_CODES = {"DME": "DME", "NDB": "NDB", "NDBDME": "NDB-DME", "TACAN": "TACAN", "VOR": "VOR", "VORDME": "VOR-DME", "VORTAC": "VORTAC"}


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower())


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.stack = []
        self.skipped = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        deleted = tag in ("del", "s", "strike") or "deleted" in attrs.get("class", "").lower() or "line-through" in attrs.get("style", "")
        if self.skipped or deleted:
            if tag not in ("br", "img", "hr", "meta", "link", "input", "wbr"):
                self.skipped.append(tag)
            return
        if tag == "table":
            self.stack.append({"rows": [], "row": None, "cell": None})
        elif self.stack:
            table = self.stack[-1]
            if tag == "tr":
                table["row"] = []
            elif tag in ("td", "th"):
                table["cell"] = {"text": [], "rowspan": max(1, int(attrs.get("rowspan", "1"))), "colspan": max(1, int(attrs.get("colspan", "1")))}
            elif tag in ("br", "p", "div"):
                self.handle_data(" ")

    def handle_data(self, data):
        if not self.skipped:
            for table in self.stack:
                if table["cell"] is not None:
                    table["cell"]["text"].append(data)

    def handle_endtag(self, tag):
        if self.skipped:
            if tag == self.skipped[-1]:
                self.skipped.pop()
            return
        if not self.stack:
            return
        table = self.stack[-1]
        if tag in ("td", "th") and table["cell"] is not None:
            cell = table["cell"]
            cell["text"] = " ".join(" ".join(cell["text"]).split())
            if table["row"] is not None:
                table["row"].append(cell)
            table["cell"] = None
        elif tag == "tr" and table["row"] is not None:
            table["rows"].append(table["row"])
            table["row"] = None
        elif tag == "table":
            self.tables.append(table["rows"])
            self.stack.pop()


def expanded_rows(rows):
    pending = {}
    for raw in rows:
        cells = {column: text for column, (text, _) in pending.items()}
        following = {column: (text, remaining - 1) for column, (text, remaining) in pending.items() if remaining > 1}
        column = 0
        for cell in raw:
            while column in cells:
                column += 1
            for offset in range(cell["colspan"]):
                cells[column + offset] = cell["text"]
                if cell["rowspan"] > 1:
                    following[column + offset] = (cell["text"], cell["rowspan"] - 1)
            column += cell["colspan"]
        pending = following
        yield [cells.get(column, "") for column in range(max(cells, default=-1) + 1)]


def position(text):
    values = {}
    # SIA publishes degrees/minutes/seconds, with decimal seconds where needed.
    pattern = r"(\d{1,3})\s*[°º]\s*(\d{1,2})\s*['’′]\s*(\d{1,2}(?:[.,]\d+)?)\s*[\"'”″]*\s*([NSEW])"
    for degrees, minutes, seconds, hemisphere in re.findall(pattern, text.upper()):
        degrees, minutes, seconds = float(degrees), float(minutes), float(seconds.replace(",", "."))
        limit = 90 if hemisphere in "NS" else 180
        if minutes >= 60 or seconds >= 60 or degrees > limit:
            continue
        number = degrees + minutes / 60 + seconds / 3600
        if number > limit:
            continue
        values.setdefault("lat" if hemisphere in "NS" else "lon", -number if hemisphere in "SW" else number)
    return (values["lat"], values["lon"]) if {"lat", "lon"}.issubset(values) else None


def facility_type(text):
    direct = TYPE_CODES.get(re.sub(r"[^A-Z0-9]", "", text.upper()))
    if direct:
        return direct
    match = re.match(r"^(NDB\s*[-/+]\s*DME|VOR\s*[-/+]\s*DME|VORTAC|TACAN|NDB|DME|VOR)(?=\s|\(|$)", text.strip().upper())
    return TYPE_CODES.get(re.sub(r"[^A-Z0-9]", "", match[1])) if match else None


def parse_sia_html(data, url):
    parser = TableParser()
    try:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252")
        parser.feed(text)
    except (UnicodeError, ValueError) as error:
        raise QgsProcessingException("Cannot parse SIA HTML: " + str(error)) from error
    records = []
    for table in parser.tables:
        columns = None
        for row in expanded_rows(table):
            headers = [normalized(cell) for cell in row]
            ids = [i for i, value in enumerate(headers) if value in ("id", "ident", "identification")]
            types = [i for i, value in enumerate(headers) if value == "service" or value.startswith("type")]
            freqs = [i for i, value in enumerate(headers) if value.startswith("freq")]
            coords = [i for i, value in enumerate(headers) if "coordonnees" in value or "coordinates" in value or "positiongeo" in value]
            if ids and types and freqs and coords:
                columns = ids[0], types[0], freqs[0], coords[0]
                continue
            if columns is None or len(row) <= max(columns):
                continue
            identifier, service, frequency, coordinates = (row[column] for column in columns)
            kind = facility_type(service)
            coordinate = position(coordinates)
            ident = re.match(r"^([A-Z0-9]{1,8})(?=\s|$)", identifier.strip().upper())
            if not kind or coordinate is None or ident is None:
                continue
            frequencies = [float(value.replace(",", ".")) * (1000 if unit.upper() == "MHZ" else 1)
                           for value, unit in re.findall(r"(\d+(?:[.,]\d+)?)\s*(MHz|kHz)", frequency, re.I)]
            latitude, longitude = coordinate
            records.append({"id": "SIA:" + ident[1] + ":" + kind + ":{:.6f}:{:.6f}".format(latitude, longitude),
                "ident": ident[1], "type": kind, "latitude_deg": latitude, "longitude_deg": longitude,
                "frequency_khz": frequencies[0] if frequencies else "",
                "dme_frequency_khz": frequencies[1] if len(frequencies) > 1 else "", "source_url": url})
    return records


def clean_index_url(value):
    value = value.strip()
    # Accept Markdown pasted from chat, or an angle-bracket autolink.
    match = re.fullmatch(r"\[[^\]]*\]\((https://[^\s]+)\)", value)
    if match:
        value = match[1]
    elif value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    parts = urlsplit(value)
    if parts.query or parts.fragment or parts.username or parts.password or parts.port:
        raise QgsProcessingException("Use the plain SIA edition URL without credentials, query or fragment.")
    return value


def edition_sources(index_url, airports=()):
    index_url = clean_index_url(index_url)
    parts = urlsplit(index_url)
    match = re.search(r"/AIRAC-(\d{4}-\d{2}-\d{2})/html/", parts.path)
    if parts.scheme != "https" or parts.hostname != "www.sia.aviation-civile.gouv.fr" or not match:
        raise QgsProcessingException("Use the SIA eAIP edition index URL containing /AIRAC-YYYY-MM-DD/html/.")
    root = index_url.split("/html/", 1)[0] + "/html/eAIP/"
    codes = sorted(set(airports))
    if any(not re.fullmatch(r"[A-Z]{4}", code) for code in codes):
        raise QgsProcessingException("Use four-letter ICAO codes for supplementary SIA AD 2.19 pages.")
    return match[1], [root + "FR-ENR-4.1-fr-FR.html"] + [root + "FR-AD-2." + code + "-fr-FR.html" for code in codes]


def csv_bytes(records):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=("id", "ident", "type", "latitude_deg", "longitude_deg", "frequency_khz", "dme_frequency_khz", "source_url"))
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue().encode("utf-8")
