#!/usr/bin/env python3
"""Construit dist/autosave_data-<version>.zip, installable via
Extensions > Installer/Gérer les extensions > Installer depuis un ZIP.

Le ZIP contient un unique dossier racine ``autosave_data/`` (exigence de QGIS),
sans caches Python, avec la licence copiée à l'intérieur.
"""
import configparser
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = "autosave_data"
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_EXT = {".pyc", ".pyo"}


def read_version():
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(os.path.join(ROOT, PLUGIN, "metadata.txt"), encoding="utf-8")
    return parser["general"]["version"].strip()


def build():
    version = read_version()
    dist = os.path.join(ROOT, "dist")
    os.makedirs(dist, exist_ok=True)
    target = os.path.join(dist, "%s-%s.zip" % (PLUGIN, version))
    if os.path.exists(target):
        os.remove(target)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder, dirs, files in os.walk(os.path.join(ROOT, PLUGIN)):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                if os.path.splitext(name)[1] in SKIP_EXT:
                    continue
                full = os.path.join(folder, name)
                zf.write(full, os.path.relpath(full, ROOT))
        licence = os.path.join(ROOT, "LICENSE")
        if os.path.isfile(licence):
            zf.write(licence, "%s/LICENSE" % PLUGIN)
    return target


if __name__ == "__main__":
    print(build())
    sys.exit(0)
