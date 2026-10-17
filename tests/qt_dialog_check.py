# -*- coding: utf-8 -*-
"""Contrôle de la fenêtre de réglages avec un vrai Qt (lancé par test_dialog_qt.py)."""
import sys, types, importlib
qt = sys.argv[1]
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules["qgis"] = types.ModuleType("qgis"); sys.modules["qgis.PyQt"] = types.ModuleType("qgis.PyQt")
for sub in ("QtCore", "QtGui", "QtWidgets"):
    sys.modules["qgis.PyQt." + sub] = importlib.import_module("%s.%s" % (qt, sub))
from qgis.PyQt.QtWidgets import QApplication
app = QApplication([])
from autosave_data import core
from autosave_data.dialog import SettingsDialog, ACTION_SAVE, ACTION_RESTORE, ACTION_FOLDER
d = SettingsDialog(core.Config(delay_seconds=45, backups_enabled=False, show_message_bar=True),
                   None, {"name": "a.qgz", "has_file": True, "backups": 3})
# options grisées selon l'état
assert d.keep.isEnabled() is False and d.backup_interval.isEnabled() is False
d.backups.setChecked(True); assert d.keep.isEnabled() and d.backup_interval.isEnabled()
d.enabled.setChecked(False)
assert not d.save_box.isEnabled() and not d.pause_box.isEnabled() and not d.backup_box.isEnabled()
assert d.config().enabled is False
d.enabled.setChecked(True); assert d.save_box.isEnabled()
# valeurs par défaut
assert d.delay.value() == 45 and d.message_bar.isChecked()
d.defaults_button.click()
c = d.config(); assert (c.delay_seconds, c.backups_enabled, c.show_message_bar) == (20, True, False), c
# boutons d'action : fermeture avec acceptation et action demandée
for button, action in ((d.save_button, ACTION_SAVE), (d.restore_button, ACTION_RESTORE), (d.folder_button, ACTION_FOLDER)):
    dd = SettingsDialog(core.Config(), None, {"name": "a.qgz", "has_file": True, "backups": 3})
    assert button.isEnabled()
    button.click()
    assert dd.requested is None  # le bouton du dialogue testé n'est pas celui de dd
    b = {ACTION_SAVE: dd.save_button, ACTION_RESTORE: dd.restore_button, ACTION_FOLDER: dd.folder_button}[action]
    b.click(); assert dd.requested == action and dd.result() == 1, (action, dd.result())
# sans projet : actions grisées
n = SettingsDialog(core.Config(), None, {})
assert not n.save_button.isEnabled() and not n.restore_button.isEnabled() and not n.folder_button.isEnabled()
# sans copies : restauration grisée, enregistrement possible
m = SettingsDialog(core.Config(), None, {"has_file": True, "backups": 0})
assert m.save_button.isEnabled() and not m.restore_button.isEnabled()
print(qt, "comportement OK")
