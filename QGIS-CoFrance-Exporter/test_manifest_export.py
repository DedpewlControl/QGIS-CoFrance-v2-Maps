"""Tests for manifest discovery, matching, and non-destructive updates."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from .manifest_export import check_manifest_unchanged, find_manifest, prepare_manifest_update


MANIFEST = {
    "$schema": "https://example.invalid/manifest.schema.json",
    "LFBB": {
        "_maps": ["Shared"],
        "LFBL": ["0_CCA_Limoges", "1_CCA_Limoges_MVA", "unselected_map"],
        "LFBD": ["0_CCA_Aquitaine"],
    },
    "LFFF": {"_maps": ["Shared"], "LFQQ": ["0_CCA_Lille"]},
    "LFXX": ["Coastlines"],
}


class ManifestExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "manifest.json"
        self.document = copy.deepcopy(MANIFEST)
        self.write_manifest()

    def write_manifest(self):
        self.path.write_text(json.dumps(self.document, indent=4) + "\n", encoding="utf-8")

    def test_updates_matching_airport_and_keeps_unselected_and_unrelated_maps(self):
        original = self.path.read_bytes()
        update = prepare_manifest_update(self.root, ["3_CCA_Limoges_FIX_Names.geojson",
                                                   "4_CCA_Limoges_ARPs.geojson"])
        changed = json.loads(update.content)
        self.assertEqual(update.added, 2)
        self.assertEqual(changed["LFBB"]["LFBL"], self.document["LFBB"]["LFBL"] +
                         ["3_CCA_Limoges_FIX_Names", "4_CCA_Limoges_ARPs"])
        self.assertEqual(changed["LFFF"], self.document["LFFF"])
        self.assertEqual(changed["LFBB"]["LFBD"], self.document["LFBB"]["LFBD"])
        self.assertEqual(changed["$schema"], self.document["$schema"])
        self.assertEqual(self.path.read_bytes(), original)

    def test_unchanged_manifest_preserves_original_bytes(self):
        self.path.write_bytes(b'{"LFBB":{"LFBL":["0_CCA_Limoges"]}}')
        update = prepare_manifest_update(self.root, ["0_CCA_Limoges.geojson"] * 2)
        self.assertEqual(update.added, 0)
        self.assertEqual(update.content, self.path.read_bytes())

    def test_detects_parent_manifest_and_uses_airport_folder_for_new_name(self):
        folder = self.root / "LFBB" / "LFBL"
        folder.mkdir(parents=True)
        update = prepare_manifest_update(folder, ["new_map.geojson"])
        self.assertEqual(update.path, self.path)
        self.assertIn("new_map", json.loads(update.content)["LFBB"]["LFBL"])

    def test_nearest_manifest_wins(self):
        folder = self.root / "LFBL"
        folder.mkdir()
        local = folder / "manifest.json"
        local.write_text(json.dumps({"LFBB": {"LFBL": []}}))
        self.assertEqual(find_manifest(folder), local)
        update = prepare_manifest_update(folder, ["new_map.geojson"])
        self.assertEqual(json.loads(update.content)["LFBB"]["LFBL"], ["new_map"])

    def test_ambiguous_shared_map_is_rejected_without_context(self):
        with self.assertRaisesRegex(ValueError, "matches several"):
            prepare_manifest_update(self.root, ["Shared.geojson"])
        folder = self.root / "LFBB" / "_maps"
        folder.mkdir(parents=True)
        self.assertEqual(prepare_manifest_update(folder, ["Shared.geojson"]).added, 0)

    def test_unknown_map_is_rejected_without_writing_manifest(self):
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "no matching entry"):
            prepare_manifest_update(self.root, ["unknown.geojson"])
        self.assertEqual(self.path.read_bytes(), original)

    def test_malformed_or_duplicate_keys_are_rejected(self):
        for content in ["{", '{"LFBB":[],"LFBB":[]}', '{"LFBB":{"LFBL":"wrong"}}']:
            self.path.write_text(content)
            with self.assertRaises(ValueError):
                prepare_manifest_update(self.root, ["0_CCA_Limoges.geojson"])

    def test_case_is_corrected_to_exported_filename(self):
        update = prepare_manifest_update(self.root, ["coastlines.geojson"])
        self.assertEqual(json.loads(update.content)["LFXX"], ["coastlines"])

    def test_multiple_airports_and_decimal_numbered_maps(self):
        update = prepare_manifest_update(self.root, ["2.1_CCA_Limoges_Procedures.geojson",
                                                   "4_CCA_Lille_ARPs.geojson"])
        changed = json.loads(update.content)
        self.assertIn("2.1_CCA_Limoges_Procedures", changed["LFBB"]["LFBL"])
        self.assertIn("4_CCA_Lille_ARPs", changed["LFFF"]["LFQQ"])

    def test_utf8_bom_crlf_and_indentation_are_preserved(self):
        text = json.dumps(self.document, indent=2) + "\n"
        self.path.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
        update = prepare_manifest_update(self.root, ["3_CCA_Limoges_FIX_Names.geojson"])
        self.assertTrue(update.content.startswith(b"\xef\xbb\xbf{\r\n  "))
        self.assertNotIn(b"\n", update.content.replace(b"\r\n", b""))

    def test_concurrent_change_is_detected(self):
        update = prepare_manifest_update(self.root, ["3_CCA_Limoges_FIX_Names.geojson"])
        self.path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "changed during export"):
            check_manifest_unchanged(update)

    def test_legacy_arp_variants_are_replaced_in_place(self):
        for suffix in ["VFR_ARPs", "VFR_ARP", "ARP", "ARP_Names", "ARPs_Names",
                       "VFR_ARP_Names", "vfr_arps_names"]:
            with self.subTest(suffix=suffix):
                self.document["LFBB"]["LFBL"] = ["before", "4_CCA_Limoges_" + suffix,
                                                   "4_CCA_Limoges_VFR_Point_Names", "after"]
                self.write_manifest()
                update = prepare_manifest_update(self.root, ["4_CCA_Limoges_ARPs.geojson"])
                self.assertEqual(json.loads(update.content)["LFBB"]["LFBL"],
                                 ["before", "4_CCA_Limoges_ARPs",
                                  "4_CCA_Limoges_VFR_Point_Names", "after"])
                self.assertEqual((update.added, update.removed), (1, 1))

    def test_legacy_cleanup_runs_when_canonical_entry_already_exists(self):
        self.document["LFBB"]["LFBL"] = ["0_CCA_Limoges", "4_CCA_Limoges_VFR_ARPs",
                                           "4_CCA_Limoges_ARPs", "4_CCA_Limoges_ARP_Names"]
        self.write_manifest()
        update = prepare_manifest_update(self.root, ["4_CCA_Limoges_ARPs.geojson"])
        self.assertEqual(json.loads(update.content)["LFBB"]["LFBL"],
                         ["0_CCA_Limoges", "4_CCA_Limoges_ARPs"])
        self.assertEqual((update.added, update.removed, update.changed), (0, 2, True))
        self.path.write_bytes(update.content)
        repeated = prepare_manifest_update(self.root, ["4_CCA_Limoges_ARPs.geojson"])
        self.assertFalse(repeated.changed)

    def test_partial_export_preserves_unexported_legacy_arp_entries(self):
        self.document["LFBB"]["LFBL"].append("4_CCA_Limoges_VFR_ARPs")
        self.document["LFBB"]["LFBD"].append("4_CCA_Aquitaine_VFR_ARPs")
        self.write_manifest()
        update = prepare_manifest_update(self.root, ["3_CCA_Limoges_FIX_Names.geojson"])
        self.assertIn("4_CCA_Limoges_VFR_ARPs", json.loads(update.content)["LFBB"]["LFBL"])
        update = prepare_manifest_update(self.root, ["4_CCA_Limoges_ARPs.geojson"])
        self.assertEqual(json.loads(update.content)["LFBB"]["LFBD"], self.document["LFBB"]["LFBD"])

    def test_no_manifest_keeps_ordinary_export_available(self):
        # Isolate discovery from any manifest supplied alongside this repository.
        from unittest.mock import patch
        with patch.object(Path, "exists", return_value=False):
            self.assertIsNone(prepare_manifest_update(self.root, ["anything.geojson"]))


if __name__ == "__main__":
    unittest.main()
