# -*- coding: utf-8 -*-
"""AutoSave Data : enregistrement automatique du projet QGIS.

Licence : GNU GPL v2 ou ultérieure.
"""


def classFactory(iface):  # noqa: N802 (nom imposé par QGIS)
    from .plugin import AutoSaveDataPlugin
    return AutoSaveDataPlugin(iface)
