#!/usr/bin/env python3
"""Point every version anchor at the version you are about to release.

Usage: python3 scripts/set-version.py <repo-root> <version>

Why four anchors: the update check compares the published release tag against
crate::VERSION, which Cargo.toml supplies; `cargo build --locked` refuses a
Cargo.lock whose version disagrees; flutter-build.yml names the artifacts after
its VERSION env and throws when the built binary reports another number; and
flutter-nightly.yml decides which release tag the assets are uploaded to.
"""

import os
import sys


def _ending(line):
    return "\r\n" if line.endswith("\r\n") else "\n"


def bump_cargo(path, version):
    """Rewrite the version line directly below `name = "rustdesk"`."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if line.strip() != 'name = "rustdesk"':
            continue
        if i + 1 >= len(lines):
            continue
        nxt = lines[i + 1]
        if not nxt.lstrip().startswith('version = "'):
            continue
        indent = nxt[: len(nxt) - len(nxt.lstrip())]
        lines[i + 1] = indent + 'version = "%s"' % version + _ending(nxt)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.writelines(lines)
        return True
    return False


def bump_line(path, prefix, new_line):
    """Replace the first line that starts with prefix."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = new_line + _ending(line)
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.writelines(lines)
            return True
    return False


def ensure_upload_tag(path, version):
    """Set the nightly upload tag, inserting it when the file carries none."""
    prefix = '      upload-tag: "'
    new_line = '      upload-tag: "v%s"' % version
    if bump_line(path, prefix, new_line):
        return True
    with open(path, "r", encoding="utf-8", newline="") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if line.strip() == "with:":
            indent = line[: len(line) - len(line.lstrip())] + "  "
            lines.insert(i + 1, indent + new_line.lstrip() + _ending(line))
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.writelines(lines)
            return True
    return False


def main(root, version):
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        print("FAIL version must look like 1.5.5, got %r" % version)
        return 2

    checks = [
        ("Cargo.toml", bump_cargo(os.path.join(root, "Cargo.toml"), version)),
        ("Cargo.lock", bump_cargo(os.path.join(root, "Cargo.lock"), version)),
        (
            ".github/workflows/flutter-build.yml",
            bump_line(
                os.path.join(root, ".github/workflows/flutter-build.yml"),
                '  VERSION: "',
                '  VERSION: "%s"' % version,
            ),
        ),
        (
            ".github/workflows/flutter-nightly.yml",
            ensure_upload_tag(
                os.path.join(root, ".github/workflows/flutter-nightly.yml"), version
            ),
        ),
    ]

    failed = 0
    for name, ok in checks:
        print("%s %s" % ("OK  " if ok else "FAIL", name))
        if not ok:
            failed += 1
    if failed:
        print("set-version: %d anchor(s) not found" % failed)
        return 1
    print("set-version: every anchor now says %s" % version)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: set-version.py <repo-root> <version>")
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
