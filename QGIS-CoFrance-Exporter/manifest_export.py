"""Discover and update CoFrance manifests without depending on QGIS."""

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .export_plan import canonical_arp_stem


@dataclass
class ManifestUpdate:
    path: Path
    original: bytes
    content: bytes
    added: int
    removed: int = 0

    @property
    def changed(self):
        return self.content != self.original


def find_manifest(output_dir):
    """Use the nearest manifest in the destination or one of its parents."""
    directory = Path(output_dir).resolve()
    for parent in (directory, *directory.parents):
        candidate = parent / "manifest.json"
        if candidate.exists():
            if not candidate.is_file():
                raise ValueError("{} is not a manifest file.".format(candidate))
            return candidate
    return None


def _buckets(document):
    if not isinstance(document, dict):
        raise ValueError("manifest.json must contain a JSON object.")
    buckets = []
    for region, value in document.items():
        if region.startswith("$"):
            continue
        entries = value.items() if isinstance(value, dict) else [(None, value)]
        for airport, maps in entries:
            path = (region, airport) if airport is not None else (region,)
            if not isinstance(maps, list) or any(not isinstance(name, str) for name in maps):
                raise ValueError("Manifest entry {} must be a list of map names.".format(" / ".join(path)))
            buckets.append((path, maps))
    return buckets


def _destination(stem, buckets, output_dir):
    # An ICAO folder provides context when a map is shared by several sections.
    parts = [part.casefold() for part in Path(output_dir).resolve().parts]
    scoped = buckets
    folder_scoped = False
    airport_matches = [bucket for bucket in buckets
                       if len(bucket[0]) == 2 and bucket[0][1] != "_maps"
                       and bucket[0][1].casefold() in parts]
    if airport_matches:
        scoped = airport_matches
        folder_scoped = True
    else:
        regions = [bucket for bucket in buckets if bucket[0][0].casefold() in parts]
        if regions:
            scoped = regions
            folder_scoped = True
            if "_maps" in parts:
                scoped = [bucket for bucket in scoped if bucket[0][-1] == "_maps"]

    matches = [bucket for bucket in scoped if stem.casefold() in
               {canonical_arp_stem(name).casefold() for name in bucket[1]}]
    if not matches:
        # The base map identifies its airport even for a newly exported subgroup.
        body = re.sub(r"^\d+(?:\.\d+)?_", "", stem).casefold()
        scored = []
        for bucket in scoped:
            anchors = [name[2:].casefold() for name in bucket[1]
                       if re.match(r"^0_(?:CCA|EGA)_", name, re.I)]
            lengths = [len(anchor) for anchor in anchors
                       if body == anchor or body.startswith(anchor + "_")]
            if lengths:
                scored.append((max(lengths), bucket))
        if scored:
            best = max(length for length, _ in scored)
            matches = [bucket for length, bucket in scored if length == best]
        elif len(scoped) == 1 and folder_scoped:
            matches = scoped

    if len(matches) != 1:
        detail = "matches several entries" if matches else "has no matching entry"
        raise ValueError(
            "Map {!r} {} in manifest.json. Choose its FIR/airport folder "
            "or add the map's entry to the manifest before exporting.".format(stem, detail)
        )
    return matches[0][1]


def _unique_object(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("Duplicate manifest key {!r}.".format(key))
        document[key] = value
    return document


def prepare_manifest_update(output_dir, filenames):
    """Register exports and replace matching legacy ARP entries in their section."""
    path = find_manifest(output_dir)
    if path is None:
        return None
    original = path.read_bytes()
    try:
        text = original.decode("utf-8-sig")
        document = json.loads(text, object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as error:
        raise ValueError("Cannot read {}: {}".format(path, error)) from error
    buckets = _buckets(document)
    added = 0
    removed = 0
    for filename in filenames:
        stem = Path(filename).stem
        maps = _destination(stem, buckets, output_dir)
        if stem.endswith("_ARPs"):
            aliases = [name for name in maps
                       if canonical_arp_stem(name).casefold() == stem.casefold()]
            if aliases:
                # Keep the first ARP slot and collapse legacy names, standalone
                # ARP label entries, and duplicate references into the combined map.
                updated_maps = []
                inserted = False
                for name in maps:
                    if canonical_arp_stem(name).casefold() == stem.casefold():
                        if not inserted:
                            updated_maps.append(stem)
                            inserted = True
                    else:
                        updated_maps.append(name)
                added += int(stem not in aliases)
                removed += len(aliases) - int(stem in aliases)
                maps[:] = updated_maps
                continue
        if stem not in maps:
            # Keep the spelling identical to the exported filename, even on
            # case-sensitive systems serving the finished maps.
            case_match = next((i for i, name in enumerate(maps) if name.casefold() == stem.casefold()), None)
            if case_match is None:
                maps.append(stem)
            else:
                maps[case_match] = stem
            added += 1
    content = original
    if added or removed:
        indent_match = re.search(r"\n([ \t]+)\"", text)
        indent = indent_match.group(1) if indent_match else "    "
        newline = "\r\n" if "\r\n" in text else "\n"
        updated = json.dumps(document, ensure_ascii=False, indent=indent)
        if text.endswith(("\n", "\r")):
            updated += "\n"
        content = updated.replace("\n", newline).encode("utf-8")
        if original.startswith(b"\xef\xbb\xbf"):
            content = b"\xef\xbb\xbf" + content
    return ManifestUpdate(path, original, content, added, removed)


def check_manifest_unchanged(update):
    if update is not None and update.path.read_bytes() != update.original:
        raise ValueError("manifest.json changed during export. Run the export again to use the latest version.")
