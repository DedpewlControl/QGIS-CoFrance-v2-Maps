"""Plan subgroup exports without depending on QGIS or Qt."""

import re


def canonical_arp_stem(stem):
    """Use one name for combined ARP symbols and labels, including legacy names."""
    match = re.fullmatch(
        r"(\d+(?:\.\d+)?_(?:CCA|EGA)_.+?)(?:_VFR)?_ARPs?(?:_Names?)?",
        stem, re.I,
    )
    return match.group(1) + "_ARPs" if match else stem


def _filename(name):
    """Make a group name safe for an output file on Windows and Unix."""
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .")
    if not stem:
        raise ValueError("An export group has an empty filename.")
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", stem.split(".")[0], re.I):
        stem = "_" + stem
    return stem + ".geojson"


def plan_group_exports(layer_entries):
    """Return (filename, layers) batches in the supplied layer draw order.

    Entries contain a layer and its tuple of ancestor group names, outermost
    first. The first numbered subgroup owns its nested layers. VFR groups split
    ARP symbols/names from VFR point names, including nested point-name groups.
    Different group paths must never silently overwrite the same filename.
    """
    batches = {}
    owners = {}
    for layer, group_path in layer_entries:
        group_path = tuple(group_path)
        group_index = next(
            (i for i, name in enumerate(group_path) if re.match(r"^\d+(?:\.\d+)?_", name)),
            len(group_path) - 1,
        )
        owner = group_path[:group_index + 1] if group_path else (layer.name(),)
        group_name = owner[-1]
        output_name = group_name
        if re.match(r"^\d+_.*_VFR$", group_name, re.I):
            base = group_name[:-4]
            arp_layer = re.search(
                r"(?:^|[\s_-])ARPs?(?:[\s_-]+Names?)?$", layer.name().strip(), re.I
            )
            output_name = base + ("_ARPs" if arp_layer else "_VFR_Point_Names")
        filename = _filename(canonical_arp_stem(output_name))
        key = filename.casefold()
        if key in owners and owners[key] != owner:
            raise ValueError(
                "Different groups would export to {!r}: {} and {}. "
                "Rename a group or export them separately.".format(
                    filename, " / ".join(owners[key]), " / ".join(owner)
                )
            )
        owners[key] = owner
        if key not in batches:
            batches[key] = (filename, [])
        batches[key][1].append(layer)
    return list(batches.values())
