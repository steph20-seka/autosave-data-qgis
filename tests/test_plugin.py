# -*- coding: utf-8 -*-
import os
from datetime import datetime

from autosave_data import core


def make_dirty(env, filename=True):
    env.project.filename = str(env.file) if filename else ""
    env.project.dirty = True
    env.project.dirtySet.emit()


def test_modification_starts_short_countdown_then_saves_silently(env):
    make_dirty(env)
    assert env.debounce.active and env.debounce.interval == 20000
    env.debounce.fire()
    assert env.project.writes == 1
    assert any("enregistré automatiquement" in m for m in env.iface.status)
    assert env.iface.bar == []  # pas de notification intrusive par défaut


def test_each_new_change_restarts_the_countdown(env):
    make_dirty(env)
    first = env.debounce.interval
    env.project.dirtySet.emit()
    assert env.debounce.interval == first and env.debounce.active


def test_max_wait_caps_the_countdown(env, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(env.module.time, "monotonic", lambda: clock["t"])
    make_dirty(env)
    clock["t"] += 110  # 110 s de travail continu, plafond à 120 s
    env.project.dirtySet.emit()
    assert env.debounce.interval == 10000
    clock["t"] += 50
    env.project.dirtySet.emit()
    assert env.debounce.interval == 1000  # plafond dépassé : enregistrement imminent


def test_layer_in_edit_mode_blocks_saving_and_retries(env):
    env.project.layers = {"1": env.layers("routes", editable=True)}
    make_dirty(env)
    env.debounce.fire()
    assert env.project.writes == 0
    assert env.debounce.active and env.debounce.interval == 15000
    assert any("couche en édition (routes)" in m for m in env.iface.status)

    # Une seule alerte par épisode, même après plusieurs essais
    env.debounce.fire()
    env.debounce.fire()
    assert sum("en pause" in m for m in env.iface.status) == 1
    assert env.project.writes == 0


def test_saving_resumes_when_editing_stops(env):
    layer = env.layers("routes", editable=True)
    env.project.layers = {"1": layer}
    make_dirty(env)
    env.debounce.fire()
    assert env.project.writes == 0
    layer.editable = False
    env.debounce.fire()
    assert env.project.writes == 1


def test_changes_during_pause_do_not_restart_the_countdown(env):
    env.project.layers = {"1": env.layers("routes", editable=True)}
    make_dirty(env)
    env.debounce.fire()
    env.project.dirtySet.emit()
    assert env.debounce.interval == 15000


def test_layout_work_and_layer_changes_are_saved(env):
    # Aucune couche en édition : mise en page, ajout/suppression de couches
    env.project.layers = {"1": env.layers("routes", editable=False)}
    make_dirty(env)
    env.debounce.fire()
    assert env.project.writes == 1


def test_running_processing_task_blocks_then_releases(env):
    module_tasks = sys_module_tasks()
    module_tasks.active = 1
    make_dirty(env)
    env.debounce.fire()
    assert env.project.writes == 0
    assert any("traitement en cours" in m for m in env.iface.status)
    module_tasks.active = 0
    env.debounce.fire()
    assert env.project.writes == 1


def sys_module_tasks():
    import conftest
    return conftest.FakeTaskManager


def test_modal_dialog_defers_without_message(env):
    import conftest
    conftest.FakeApp.modal = object()
    make_dirty(env)
    env.debounce.fire()
    assert env.project.writes == 0
    assert env.iface.status == []
    assert env.debounce.interval == 15000
    conftest.FakeApp.modal = None
    env.debounce.fire()
    assert env.project.writes == 1


def test_unsaved_project_is_never_written_and_warns_once(env):
    make_dirty(env, filename=False)
    env.debounce.fire()
    env.debounce.fire()
    assert env.project.writes == 0
    assert sum("première fois" in m for m in env.iface.status) == 1


def test_disabled_plugin_ignores_changes(env):
    env.plugin._toggle_action.toggled.emit(False)
    make_dirty(env)
    assert not env.debounce.active
    assert env.project.writes == 0
    assert env.plugin.config.enabled is False
    assert not env.safety.active


def test_reenabling_schedules_pending_changes(env):
    env.plugin._toggle_action.toggled.emit(False)
    make_dirty(env)
    env.plugin._toggle_action.toggled.emit(True)
    assert env.debounce.active


def test_manual_save_works_during_editing(env):
    env.project.layers = {"1": env.layers("routes", editable=True)}
    make_dirty(env)
    env.plugin.save_now()
    assert env.project.writes == 1


def test_manual_save_with_nothing_to_save(env):
    env.project.filename = str(env.file)
    env.project.dirty = False
    env.plugin.save_now()
    assert env.project.writes == 0
    assert any("Aucune modification" in m for m in env.iface.status)


def test_first_save_makes_backup_then_respects_interval(env):
    make_dirty(env)
    env.debounce.fire()
    assert len(core.list_backups(str(env.file))) == 1
    make_dirty(env)
    env.debounce.fire()
    assert env.project.writes == 2
    assert len(core.list_backups(str(env.file))) == 1  # intervalle de 10 min non écoulé


def test_backup_failure_does_not_block_saving(env, monkeypatch):
    def broken(_path):
        raise OSError("disque plein")
    monkeypatch.setattr(core, "create_backup", broken)
    make_dirty(env)
    env.debounce.fire()
    assert env.project.writes == 1
    assert any("Copie de secours impossible" in m[0] for m in conftest_log())


def conftest_log():
    import conftest
    return conftest.FakeLog.messages


def test_backups_can_be_disabled(env):
    env.plugin.config.backups_enabled = False
    make_dirty(env)
    env.debounce.fire()
    assert core.list_backups(str(env.file)) == []
    assert env.project.writes == 1


def test_write_failure_warns_once_and_keeps_pending_state(env):
    env.project.write_ok = False
    make_dirty(env)
    env.debounce.fire()
    env.debounce.fire()
    assert len(env.iface.bar) == 1
    assert env.project.dirty is True
    assert env.plugin._saving is False


def test_write_exception_is_contained(env):
    env.project.write_raises = True
    make_dirty(env)
    env.debounce.fire()  # ne doit pas lever d'exception
    assert env.plugin._saving is False
    assert len(env.iface.bar) == 1


def test_read_only_file_is_reported_once(env):
    os.chmod(env.file, 0o444)
    try:
        if os.access(env.file, os.W_OK):  # exécution en root : test non pertinent
            return
        make_dirty(env)
        env.debounce.fire()
        env.debounce.fire()
        assert env.project.writes == 0
        assert len(env.iface.bar) == 1
    finally:
        os.chmod(env.file, 0o644)


def test_safety_tick_saves_leftover_changes(env):
    env.project.filename = str(env.file)
    env.project.dirty = True  # aucun signal reçu
    env.safety.fire()
    assert env.project.writes == 1


def test_safety_tick_does_nothing_when_clean(env):
    env.project.filename = str(env.file)
    env.safety.fire()
    assert env.project.writes == 0


def test_new_project_resets_state(env):
    env.project.layers = {"1": env.layers("routes", editable=True)}
    make_dirty(env)
    env.debounce.fire()
    assert env.plugin._pause_notified == core.R_EDITING
    env.project.cleared.emit()
    assert env.plugin._pause_notified is None
    assert env.plugin._first_dirty is None


def test_manual_project_save_clears_pending_state(env):
    make_dirty(env)
    env.project.projectSaved.emit()
    assert env.plugin._first_dirty is None


def test_writing_does_not_retrigger_itself(env):
    original_write = env.project.write

    def noisy_write():
        env.project.dirtySet.emit()  # simule un signal émis pendant l'écriture
        return original_write()
    env.project.write = noisy_write
    make_dirty(env)
    env.debounce.fire()
    assert not env.debounce.active


def test_unload_disconnects_everything(env):
    env.plugin.unload()
    for signal in (env.project.dirtySet, env.project.cleared,
                   env.project.readProject, env.project.projectSaved):
        assert signal.slots == []
    assert env.iface.menu == [] and env.iface.toolbar == []
    assert not env.safety.active


def test_settings_are_persisted_and_reloaded(env):
    env.plugin._toggle_action.toggled.emit(False)
    from autosave_data import plugin as plugin_module
    again = plugin_module.AutoSaveDataPlugin(env.iface)
    assert again.config.enabled is False


def test_message_bar_notification_is_optional(env):
    env.plugin.config.show_message_bar = True
    make_dirty(env)
    env.debounce.fire()
    assert len(env.iface.bar) == 1


# --------------------------------------------------------------------------
# Version 0.2 : indicateur, écriture vérifiée, délai adaptatif, restauration
# --------------------------------------------------------------------------
import sys  # noqa: E402
import types  # noqa: E402

import conftest  # noqa: E402


def test_indicator_is_created_and_follows_the_state(env):
    label = env.iface.permanent[0]
    assert label.text() == "AutoSave : actif"
    make_dirty(env)
    assert "en attente" in label.text()
    env.debounce.fire()
    assert label.text().startswith("AutoSave : enregistré à ")


def test_indicator_shows_edit_pause_then_recovers(env):
    layer = env.layers("routes", editable=True)
    env.project.layers = {"1": layer}
    label = env.iface.permanent[0]
    make_dirty(env)
    env.debounce.fire()
    assert label.text() == "AutoSave : en pause (édition de routes)"
    layer.editable = False
    env.debounce.fire()
    assert label.text().startswith("AutoSave : enregistré")


def test_indicator_shows_task_pause_and_disabled(env):
    label = env.iface.permanent[0]
    conftest.FakeTaskManager.active = 1
    make_dirty(env)
    env.debounce.fire()
    assert label.text() == "AutoSave : en pause (traitement)"
    env.plugin._toggle_action.toggled.emit(False)
    assert label.text() == "AutoSave : désactivé"


def test_indicator_removed_on_unload(env):
    label = env.iface.permanent[0]
    env.plugin.unload()
    assert env.iface.permanent == [] and label.deleted is True


def test_indicator_failure_never_breaks_the_plugin(env, monkeypatch):
    def boom(widget):
        raise RuntimeError("barre d'état indisponible")
    env.plugin.unload()
    env.iface.statusBarIface().addPermanentWidget = boom
    env.plugin.initGui()  # ne doit pas lever
    make_dirty(env)
    env.debounce.fire()
    assert env.project.writes == 1


def test_corrupt_write_is_rolled_back_and_retried(env):
    make_dirty(env)
    env.debounce.fire()  # premier enregistrement sain : le cliché sera pris au suivant
    assert env.project.writes == 1
    env.project.corrupt_on_write = True
    make_dirty(env)
    env.debounce.fire()
    ok, _ = core.verify_project_file(str(env.file))
    assert ok is True  # la version précédente a été remise en place
    assert env.project.dirty is True and env.project.dirty_calls[-1] is True
    assert env.iface.permanent[0].text() == "AutoSave : échec, voir le journal"
    assert len(env.iface.bar) == 1
    assert any("version précédente remise en place" in m[0] for m in conftest_log())


def test_corrupt_write_without_previous_snapshot_reports_failure(env):
    env.project.filename = str(env.file)
    env.file.write_bytes(b"deja abime")  # pas de version saine à garder
    env.project.corrupt_on_write = True
    env.project.dirty = True
    env.plugin.save_now()
    assert env.project.dirty_calls == []  # rien à remettre en place, pas de boucle
    assert env.iface.permanent[0].text() == "AutoSave : échec, voir le journal"


def test_failed_write_does_not_loop_forever_without_snapshot(env):
    env.project.write_ok = False
    make_dirty(env)
    env.debounce.fire()
    assert env.project.dirty_calls == []


def test_slow_save_lengthens_the_next_pause(env, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(env.module.time, "monotonic", lambda: clock["t"])
    original_write = env.project.write

    def slow_write():
        clock["t"] += 5.0  # un enregistrement de 5 s
        return original_write()
    env.project.write = slow_write

    make_dirty(env)
    env.debounce.fire()
    make_dirty(env)
    assert env.debounce.interval == 50000  # 10 x 5 s


def test_adaptive_pause_can_be_disabled(env, monkeypatch):
    env.plugin.config.adaptive_delay = False
    env.plugin._last_save_seconds = 30.0
    make_dirty(env)
    assert env.debounce.interval == 20000


def _fake_restore_dialog(monkeypatch, choose):
    class Dialog:
        def __init__(self, backups, name, parent=None, **options):
            Dialog.backups = backups
            Dialog.name = name
            Dialog.options = options

        def exec(self):
            return choose is not None

        def selected_path(self):
            return choose(Dialog.backups) if choose else None

    module = types.ModuleType("autosave_data.dialog")
    module.RestoreDialog = Dialog
    monkeypatch.setitem(sys.modules, "autosave_data.dialog", module)
    return Dialog


def test_restore_without_file_or_backups_only_informs(env):
    env.plugin.open_restore_dialog()
    assert any("pas encore de fichier" in m for m in env.iface.status)
    env.project.filename = str(env.file)
    env.plugin.open_restore_dialog()
    assert any("Aucune copie de secours" in m for m in env.iface.status)
    assert env.iface.opened == []


def test_restore_duplicates_chosen_backup_and_opens_it(env, monkeypatch):
    env.project.filename = str(env.file)
    core.create_backup(str(env.file), now=datetime(2026, 10, 16, 9, 0, 0))
    before = env.file.read_bytes()
    dialog = _fake_restore_dialog(monkeypatch, lambda backups: backups[0].path)
    env.plugin.open_restore_dialog()
    assert len(dialog.backups) == 1 and dialog.name == "carte.qgz"
    assert len(env.iface.opened) == 1
    restored = env.iface.opened[0]
    assert "_restaure_" in os.path.basename(restored) and os.path.isfile(restored)
    assert env.file.read_bytes() == before  # le projet actuel n'est pas touché


def test_restore_cancelled_does_nothing(env, monkeypatch):
    env.project.filename = str(env.file)
    core.create_backup(str(env.file))
    _fake_restore_dialog(monkeypatch, None)
    env.plugin.open_restore_dialog()
    assert env.iface.opened == []


def test_restore_open_failure_gives_a_manual_path(env, monkeypatch):
    env.project.filename = str(env.file)
    core.create_backup(str(env.file))
    _fake_restore_dialog(monkeypatch, lambda backups: backups[0].path)

    def broken(path):
        raise RuntimeError("impossible")
    env.iface.addProject = broken
    env.plugin.open_restore_dialog()
    assert len(env.iface.bar) == 1 and "Projet > Ouvrir" in env.iface.bar[0][1]


def test_restore_menu_entry_exists(env):
    assert len(env.iface.menu) == 5  # bascule + 4 commandes


def test_manual_save_during_pause_does_not_block_next_changes(env):
    """Régression : après une pause, un enregistrement manuel faisait ignorer
    toutes les modifications suivantes jusqu'au contrôle de sécurité."""
    layer = env.layers("routes", editable=True)
    env.project.layers = {"1": layer}
    make_dirty(env)
    env.debounce.fire()                      # pause : couche en édition
    layer.editable = False
    env.project.write()                      # l'utilisateur enregistre à la main
    env.debounce.fire()                      # essai suivant : rien à faire
    assert "pause" not in env.plugin._indicator.text()

    env.project.dirty = True
    env.project.dirtySet.emit()              # nouvelle modification
    assert env.debounce.active and env.debounce.interval == 20000


# --- Modification externe du fichier projet ---------------------------------
def _read_text(path):
    import zipfile
    return zipfile.ZipFile(str(path)).read("projet.qgs").decode()


def _open_project(env):
    """Simule l'ouverture du projet : la référence du fichier est mémorisée."""
    env.project.filename = str(env.file)
    env.project.readProject.emit()


def _foreign_edit(env, text="AUTRE-UTILISATEUR"):
    from conftest import make_qgz
    make_qgz(str(env.file), text)


def test_external_change_blocks_auto_save_and_warns_once(env):
    _open_project(env)
    env.project.dirty = True
    env.project.dirtySet.emit()
    _foreign_edit(env)
    env.debounce.fire()
    assert env.project.writes == 0
    assert "AUTRE-UTILISATEUR" in _read_text(env.file)   # rien écrasé
    assert "modifié ailleurs" in env.plugin._indicator.text()
    assert len(env.iface.bar) == 1 and "modifié en dehors" in env.iface.bar[0][1]

    env.project.dirtySet.emit()
    env.debounce.fire()
    assert env.project.writes == 0 and len(env.iface.bar) == 1   # un seul avertissement
    assert "modifié ailleurs" in env.plugin._indicator.text()    # pas écrasé par « en attente »


def test_own_saves_never_look_like_external_changes(env):
    _open_project(env)
    for _ in range(3):
        env.project.dirty = True
        env.project.dirtySet.emit()
        env.debounce.fire()
    assert env.project.writes == 3
    assert env.iface.bar == []


def test_manual_save_overwrites_but_keeps_the_foreign_version(env):
    _open_project(env)
    env.project.dirty = True
    env.project.dirtySet.emit()
    env.debounce.fire()                              # 1re copie de la session : intervalle en cours
    env.plugin.config.backups_enabled = False        # même sans copies de secours
    env.project.dirty = True
    env.project.dirtySet.emit()
    _foreign_edit(env)
    env.plugin.save_now()
    assert env.project.writes == 2
    backups = sorted(os.listdir(str(env.dir / "carte_sauvegardes")))
    kept = [b for b in backups if "_precedent" not in b]
    assert any("AUTRE-UTILISATEUR" in _read_text(env.dir / "carte_sauvegardes" / name)
               for name in kept)
    assert "modifié ailleurs" not in env.plugin._indicator.text()


def test_reopening_the_project_clears_the_conflict(env):
    _open_project(env)
    env.project.dirty = True
    env.project.dirtySet.emit()
    _foreign_edit(env)
    env.debounce.fire()
    assert env.project.writes == 0
    env.project.readProject.emit()                   # l'utilisateur recharge le fichier
    env.project.dirty = True
    env.project.dirtySet.emit()
    env.debounce.fire()
    assert env.project.writes == 1


def test_manual_save_by_the_user_becomes_the_new_reference(env):
    _open_project(env)
    env.project.dirty = True
    env.project.dirtySet.emit()
    _foreign_edit(env)
    env.debounce.fire()                              # conflit détecté
    env.project.write()                              # l'utilisateur enregistre via QGIS
    env.project.dirty = True
    env.project.dirtySet.emit()
    env.debounce.fire()
    assert env.project.writes == 2                   # l'enregistrement auto reprend


# --- Chargement selon la disposition de Qt -----------------------------------
def _reload_plugin_with(monkeypatch, gui_has_action, widgets_has_action):
    """Recharge plugin.py avec une disposition Qt donnée (QAction dans QtGui ou QtWidgets)."""
    import importlib
    import sys
    import types

    gui = types.ModuleType("qgis.PyQt.QtGui")
    gui.QIcon = lambda *a: None
    gui.QDesktopServices = sys.modules["qgis.PyQt.QtGui"].QDesktopServices
    widgets = types.ModuleType("qgis.PyQt.QtWidgets")
    widgets.QApplication = sys.modules["qgis.PyQt.QtWidgets"].QApplication
    widgets.QToolButton = sys.modules["qgis.PyQt.QtWidgets"].QToolButton
    marker = object()
    if gui_has_action:
        gui.QAction = marker
    if widgets_has_action:
        widgets.QAction = marker
    monkeypatch.setitem(sys.modules, "qgis.PyQt.QtGui", gui)
    monkeypatch.setitem(sys.modules, "qgis.PyQt.QtWidgets", widgets)
    module = importlib.reload(sys.modules["autosave_data.plugin"])
    return module, marker


def _restore_plugin_module(monkeypatch):
    import importlib
    import sys
    monkeypatch.undo()
    importlib.reload(sys.modules["autosave_data.plugin"])


def test_plugin_loads_when_qaction_lives_in_qtgui_only(monkeypatch):
    """Qt 6 : QAction n'existe plus dans QtWidgets."""
    try:
        module, marker = _reload_plugin_with(monkeypatch, True, False)
        assert module.QAction is marker
    finally:
        _restore_plugin_module(monkeypatch)


def test_plugin_loads_when_qaction_lives_in_qtwidgets_only(monkeypatch):
    """Qt 5 : QAction est dans QtWidgets."""
    try:
        module, marker = _reload_plugin_with(monkeypatch, False, True)
        assert module.QAction is marker
    finally:
        _restore_plugin_module(monkeypatch)


# --- Réglages : icône, indicateur cliquable, tableau de bord ------------------
def _fake_settings_dialog(monkeypatch, accepted=True, requested=None, new_config=None):
    import sys
    import types

    class Dialog:
        last = None

        def __init__(self, config, parent=None, info=None):
            Dialog.last = self
            self.initial = config
            self.info = info
            self.requested = requested

        def exec(self):
            return accepted

        def config(self):
            return new_config or self.initial

    module = types.ModuleType("autosave_data.dialog")
    module.SettingsDialog = Dialog
    module.ACTION_SAVE, module.ACTION_RESTORE, module.ACTION_FOLDER = (
        "save", "restore", "folder")
    monkeypatch.setitem(sys.modules, "autosave_data.dialog", module)
    return Dialog


def test_toolbar_icon_and_status_indicator_both_open_the_settings(env, monkeypatch):
    dialog = _fake_settings_dialog(monkeypatch)
    assert env.iface.toolbar == [env.plugin._settings_action]
    env.plugin._settings_action.triggered.emit()
    assert dialog.last is not None
    dialog.last = None
    env.plugin._indicator.clicked.emit()
    assert dialog.last is not None


def test_settings_dialog_receives_the_project_dashboard(env, monkeypatch):
    dialog = _fake_settings_dialog(monkeypatch)
    env.plugin.open_settings()
    assert dialog.last.info["has_file"] is False and dialog.last.info["backups"] == 0

    make_dirty(env)
    env.debounce.fire()                       # un enregistrement automatique a lieu
    env.plugin.open_settings()
    info = dialog.last.info
    assert info["name"] == "carte.qgz" and info["has_file"] is True
    assert info["backups"] >= 1
    assert info["last_saved"] and "enregistré" in info["state"]
    # Le vrai dossier des copies est transmis à la fenêtre, pas un nom générique
    assert info["backup_dir"] == str(env.file.parent / "carte_sauvegardes")
    assert dialog.last.info["backup_dir"].endswith("_sauvegardes")


def test_settings_changes_are_applied_only_when_accepted(env, monkeypatch):
    new = core.Config(delay_seconds=45, enabled=True)
    _fake_settings_dialog(monkeypatch, accepted=False, new_config=new)
    env.plugin.open_settings()
    assert env.plugin.config.delay_seconds == 20
    _fake_settings_dialog(monkeypatch, accepted=True, new_config=new)
    env.plugin.open_settings()
    assert env.plugin.config.delay_seconds == 45


def test_actions_requested_from_the_settings_run_after_it_closes(env, monkeypatch):
    calls = []
    env.plugin.save_now = lambda: calls.append("save")
    env.plugin.open_restore_dialog = lambda: calls.append("restore")
    env.plugin.open_backup_folder = lambda: calls.append("folder")
    for requested in ("save", "restore", "folder", None):
        _fake_settings_dialog(monkeypatch, requested=requested)
        env.plugin.open_settings()
    assert calls == ["save", "restore", "folder"]


def test_indicator_leaves_the_disabled_state_when_reenabled(env, monkeypatch):
    """Régression vue dans QGIS : « désactivé » restait affiché après réactivation."""
    env.plugin._toggle_action.toggled.emit(False)
    assert "désactivé" in env.plugin._indicator.text()
    env.plugin._toggle_action.toggled.emit(True)
    assert env.plugin._indicator.text() == "AutoSave : actif"

    # Même chemin par la fenêtre de réglages
    off = core.Config(enabled=False)
    _fake_settings_dialog(monkeypatch, new_config=off)
    env.plugin.open_settings()
    assert "désactivé" in env.plugin._indicator.text()
    _fake_settings_dialog(monkeypatch, new_config=core.Config(enabled=True))
    env.plugin.open_settings()
    assert env.plugin._indicator.text() == "AutoSave : actif"


# --- Fermeture inattendue : proposition de récupération ---------------------------
def _crashed_session(env, project=None, last_save_ago=180, alive_pid=None):
    """Laisse derrière elle le journal d'une session QGIS interrompue."""
    import time as real_time
    now = real_time.time()
    clock = lambda: now - last_save_ago - 3600  # noqa: E731  (il y a plus d'une heure)
    tracker = core.SessionTracker(env.dir / "_profil", pid=alive_pid or 424242, clock=clock)
    tracker.start(str(project if project is not None else env.file))
    tracker.last_save = now - last_save_ago
    tracker._write()
    return tracker


def _start_new_session(env):
    plugin = env.module.AutoSaveDataPlugin(env.iface)
    plugin.initGui()
    return plugin


def _run_pending_recovery(env):
    """Exécute les propositions de récupération programmées au démarrage."""
    import conftest
    pending = list(conftest.FakeTimer.single_shots)
    conftest.FakeTimer.single_shots.clear()
    for _delay, callback in pending:
        callback()


def test_session_journal_is_removed_on_clean_exit_and_on_unload(env):
    sessions = env.dir / "_profil" / "AutoSaveData" / "sessions"
    assert len(list(sessions.iterdir())) == 1
    env.plugin.unload()
    assert list(sessions.iterdir()) == []

    other = _start_new_session(env)
    assert len(list(sessions.iterdir())) == 1
    import conftest
    conftest.FakeQApp.aboutToQuit.emit()      # fermeture normale de QGIS
    assert list(sessions.iterdir()) == []
    other.unload()


def test_crash_offers_current_project_and_backups(env, monkeypatch):
    make_dirty(env)
    env.debounce.fire()                        # crée une copie de secours
    env.plugin.unload()
    _crashed_session(env)
    dialog = _fake_restore_dialog(monkeypatch, lambda backups: backups[0].path)
    _start_new_session(env)
    _run_pending_recovery(env)
    assert dialog.backups[0].path == str(env.file)
    assert dialog.backups[0].label.startswith("Projet actuel")
    assert len(dialog.backups) >= 2
    assert "plantage ou coupure" in dialog.options["intro"]
    assert "Dernier enregistrement automatique" in dialog.options["intro"]
    assert dialog.options["ok_text"] == "Ouvrir la version choisie"
    assert env.iface.opened == [str(env.file)]      # le projet actuel est ouvert tel quel


def test_crash_recovery_can_restore_an_older_copy(env, monkeypatch):
    make_dirty(env)
    env.debounce.fire()
    env.plugin.unload()
    _crashed_session(env)
    _fake_restore_dialog(monkeypatch, lambda backups: backups[1].path)
    _start_new_session(env)
    _run_pending_recovery(env)
    assert len(env.iface.opened) == 1 and "_restaure_" in env.iface.opened[0]


def test_crash_recovery_is_offered_once_and_can_be_declined(env, monkeypatch):
    env.plugin.unload()
    _crashed_session(env)
    dialog = _fake_restore_dialog(monkeypatch, None)       # l'utilisateur annule
    _start_new_session(env)
    _run_pending_recovery(env)
    assert dialog.backups and env.iface.opened == []
    sessions = list((env.dir / "_profil" / "AutoSaveData" / "sessions").iterdir())
    assert len(sessions) == 1                              # seul le journal de la session en cours reste


def test_crash_recovery_can_be_switched_off(env, monkeypatch):
    env.plugin.unload()
    _crashed_session(env)
    import conftest
    conftest.FakeSettings.store["AutoSaveData/recovery_prompt"] = False
    dialog = _fake_restore_dialog(monkeypatch, lambda backups: backups[0].path)
    new = _start_new_session(env)
    assert new.config.recovery_prompt is False
    _run_pending_recovery(env)
    assert not hasattr(dialog, "backups")
    assert len(list((env.dir / "_profil" / "AutoSaveData" / "sessions").iterdir())) == 1


def test_crash_without_project_file_offers_nothing(env, monkeypatch):
    env.plugin.unload()
    _crashed_session(env, project="")
    _crashed_session(env, project=str(env.dir / "disparu.qgz"), alive_pid=424243)
    dialog = _fake_restore_dialog(monkeypatch, lambda backups: backups[0].path)
    _start_new_session(env)
    _run_pending_recovery(env)
    assert not hasattr(dialog, "backups") and env.iface.opened == []


def test_other_running_qgis_is_not_mistaken_for_a_crash(env, monkeypatch):
    import time as real_time
    env.plugin.unload()
    live = core.SessionTracker(env.dir / "_profil", pid=os.getppid(), clock=real_time.time)
    live.start(str(env.file))                              # processus vivant, signe de vie récent
    dialog = _fake_restore_dialog(monkeypatch, lambda backups: backups[0].path)
    _start_new_session(env)
    _run_pending_recovery(env)
    assert not hasattr(dialog, "backups")


def test_recovery_waits_while_another_window_is_open(env, monkeypatch):
    import conftest
    env.plugin.unload()
    _crashed_session(env)
    dialog = _fake_restore_dialog(monkeypatch, lambda backups: backups[0].path)
    _start_new_session(env)
    conftest.FakeApp.modal = object()
    _run_pending_recovery(env)
    assert not hasattr(dialog, "backups")                  # reporté
    assert conftest.FakeTimer.single_shots                 # un nouvel essai est programmé
    conftest.FakeApp.modal = None
    _run_pending_recovery(env)
    assert dialog.backups


def test_journal_follows_project_and_saves(env):
    import json
    make_dirty(env)
    env.debounce.fire()
    sessions = env.dir / "_profil" / "AutoSaveData" / "sessions"
    data = json.loads(next(sessions.iterdir()).read_text())
    assert data["last_save"] is not None            # l'enregistrement automatique est noté
    env.project.filename = str(env.dir / "autre.qgz")
    env.project.readProject.emit()                  # un autre projet est ouvert
    data = json.loads(next(sessions.iterdir()).read_text())
    assert data["project"].endswith("autre.qgz")


def test_journal_follows_save_as_to_another_file(env):
    """Enregistrer sous change le nom du projet sans rouvrir ni fermer : le journal
    doit suivre, sinon la reprise proposerait l'ancien projet (constaté dans QGIS)."""
    import json
    sessions = env.dir / "_profil" / "AutoSaveData" / "sessions"
    env.project.filename = str(env.dir / "test_reprise.qgz")   # Enregistrer sous
    env.project.projectSaved.emit()                             # enregistrement manuel
    data = json.loads(next(sessions.iterdir()).read_text())
    assert data["project"].endswith("test_reprise.qgz")
    assert data["last_save"] is not None

    env.project.filename = str(env.dir / "troisieme.qgz")      # puis un autre nom, en auto
    env.project.dirty = True
    env.project.dirtySet.emit()
    env.debounce.fire()
    data = json.loads(next(sessions.iterdir()).read_text())
    assert data["project"].endswith("troisieme.qgz")
