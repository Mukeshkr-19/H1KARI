#!/usr/bin/env python3
"""Generate the frontend third-party notice input from package-lock.json."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "hikari-frontend" / "package-lock.json"
OUTPUT_PATH = ROOT / "docs" / "FRONTEND_THIRD_PARTY_INPUT.md"

PERMISSIVE_LICENSES = {
    "0BSD",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BlueOak-1.0.0",
    "ISC",
    "MIT",
}


def _package_name(path: str) -> str:
    return path.rsplit("node_modules/", 1)[-1]


def _escape(value: object) -> str:
    return str(value).replace("|", "\\|")


def generate() -> str:
    lock_bytes = LOCK_PATH.read_bytes()
    lock = json.loads(lock_bytes)
    entries = []

    for path, metadata in lock["packages"].items():
        if not path:
            continue
        entries.append(
            {
                "path": path,
                "name": metadata.get("name") or _package_name(path),
                "version": metadata.get("version", "UNKNOWN"),
                "license": metadata.get("license", "UNKNOWN"),
                "resolved": metadata.get("resolved", ""),
            }
        )

    entries.sort(key=lambda entry: entry["path"])
    unknown = [entry["path"] for entry in entries if "UNKNOWN" in entry.values()]
    if unknown:
        raise ValueError(f"missing package metadata: {', '.join(unknown)}")

    counts = Counter(entry["license"] for entry in entries)
    review_entries = [
        entry for entry in entries if entry["license"] not in PERMISSIVE_LICENSES
    ]

    lines = [
        "# Frontend Third-Party Notice Input",
        "",
        "Status: generated audit input; not a final release notice or legal approval",
        "Source: `hikari-frontend/package-lock.json`",
        f"Lock SHA-256: `{hashlib.sha256(lock_bytes).hexdigest()}`",
        f"Package entries: {len(entries)}",
        "",
        "Regenerate with `python scripts/frontend_third_party.py`. Verify with "
        "`python scripts/frontend_third_party.py --check`.",
        "",
        "## Review boundaries",
        "",
        "- Registry license fields and archive URLs are evidence pointers, not substitutes "
        "for the license and notice files inside each resolved artifact.",
        "- The `@img/sharp-libvips-*` archives report LGPL-3.0-or-later or combined "
        "terms, while upstream libvips reports LGPL-2.1-or-later. Exact binary composition, "
        "corresponding-source, relinking, and notice obligations remain release blockers.",
        "- MPL packages require license-file and modified-file review. The resolved "
        "`axe-core` artifact also contains its own third-party notice file.",
        "- CC-BY content requires attribution review. Python-2.0 and CC0 entries still "
        "require their artifact license evidence to be represented in the final notice.",
        "- Permissive families are included in the complete index and still require normal "
        "copyright, license-text, and notice retention review before distribution.",
        "",
        "Upstream evidence: [sharp-libvips](https://github.com/lovell/sharp-libvips), "
        "[libvips](https://github.com/libvips/libvips), "
        "[axe-core](https://github.com/dequelabs/axe-core), "
        "[Lightning CSS](https://github.com/parcel-bundler/lightningcss), "
        "[caniuse-lite](https://github.com/browserslist/caniuse-lite), "
        "[argparse](https://github.com/nodeca/argparse), and "
        "[language-subtag-registry](https://github.com/mattcg/language-subtag-registry).",
        "",
        "## License-family summary",
        "",
        "| Entries | Lock license expression |",
        "|---:|---|",
    ]

    for license_name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {count} | {_escape(license_name)} |")

    lines.extend(
        [
            "",
            "## Focused review set",
            "",
            "| Package path | Version | Lock license expression | Registry archive |",
            "|---|---:|---|---|",
        ]
    )
    for entry in review_entries:
        archive = f"[archive]({entry['resolved']})" if entry["resolved"] else "-"
        lines.append(
            f"| `{_escape(entry['path'])}` | {_escape(entry['version'])} | "
            f"{_escape(entry['license'])} | {archive} |"
        )

    lines.extend(
        [
            "",
            "## Complete lock index",
            "",
            "| Package path | Package | Version | Lock license expression |",
            "|---|---|---:|---|",
        ]
    )
    for entry in entries:
        lines.append(
            f"| `{_escape(entry['path'])}` | `{_escape(entry['name'])}` | "
            f"{_escape(entry['version'])} | {_escape(entry['license'])} |"
        )

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if output is stale")
    args = parser.parse_args()
    generated = generate()

    if args.check:
        if not OUTPUT_PATH.exists() or OUTPUT_PATH.read_text() != generated:
            print(f"{OUTPUT_PATH.relative_to(ROOT)} is stale")
            return 1
        print(f"{OUTPUT_PATH.relative_to(ROOT)} is current")
        return 0

    OUTPUT_PATH.write_text(generated)
    print(f"wrote {OUTPUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
