# -*- coding: utf-8 -*-
"""AutoSave Data : enregistrement automatique du projet QGIS.

Principe :
- chaque modification du projet relance un compte à rebours court ; quand
  l'utilisateur marque une pause, le projet est enregistré sans aucune question ;
- tant qu'une couche vectorielle est en édition (vectorisation, saisie) ou
  qu'un traitement tourne en arrière-plan, rien n'est enregistré : le plugin
  patiente et réessaie ;
- une copie de secours horodatée du fichier projet est faite avant l'écrasement ;
- après chaque écriture, le fichier est contrôlé ; s'il est illisible, la dernière
  version saine est remise en place ;
- un contrôle périodique de sécurité rattrape les cas où un signal serait manqué ;
- un indicateur dans la barre d'état montre ce que fait l'extension.

Licence : GNU GPL v2 ou ultérieure.
"""
import os
import time
import traceback
from datetime import datetime

from qgis.PyQt.QtCore import QTimer, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import QApplication, QToolButton
try:  # Qt 6 : QAction a quitté QtWidgets pour QtGui
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt 5
    from qgis.PyQt.QtWidgets import QAction
from qgis.core import (Qgis, QgsApplication, QgsMessageLog, QgsProject,
                       QgsSettings, QgsVectorLayer)

from . import core

PLUGIN_NAME = "AutoSave Data"
MENU_TITLE = "&AutoSave Data"
RETRY_MS = 15000
RECOVERY_DELAY_MS = 2500   # laisse QGIS finir son démarrage avant d'ouvrir une fenêtre


def _level(name):
    """Niveau de message compatible QGIS 3.22 à 4.x."""
    scoped = getattr(Qgis, "MessageLevel", None)
    holder = scoped if scoped is not None and hasattr(scoped, name) else Qgis
    return getattr(holder, name)


class AutoSaveDataPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.settings = QgsSettings()
        self.config = core.load_config(self._read_setting)
        self.backups = core.BackupScheduler()

        self._debounce = None      # compte à rebours après modification
        self._safety = None        # contrôle périodique
        self._first_dirty = None   # instant de la première modification non enregistrée
        self._saving = False
        self._pause_notified = None
        self._warned_no_file = False
        self._warned_read_only = False
        self._warned_failure = False
        self._last_save_seconds = 0.0
        self._failed = False       # dernier enregistrement en échec, tant que rien n'a réussi
        self._signature = None     # empreinte du fichier projet à notre dernière lecture/écriture
        self._conflict = False     # fichier modifié ailleurs : on n'écrase rien
        self._warned_external = False

        self._indicator = None
        self._indicator_state = None
        self._last_saved_text = ""
        self._actions = []
        self._toggle_action = None
        self._settings_action = None
        self._connections = []
        self.session = None        # journal de session (détection d'une fermeture inattendue)
        self._heartbeat = None
        self._quit_signal = None

    # ------------------------------------------------------------------
    # Réglages persistants
    # ------------------------------------------------------------------
    def _read_setting(self, key, default):
        return self.settings.value("{}/{}".format(core.SETTINGS_GROUP, key), default)

    def _write_setting(self, key, value):
        self.settings.setValue("{}/{}".format(core.SETTINGS_GROUP, key), value)

    def _persist_config(self):
        core.save_config(self._write_setting, self.config)

    # ------------------------------------------------------------------
    # Cycle de vie QGIS
    # ------------------------------------------------------------------
    def initGui(self):
        main_window = self.iface.mainWindow()
        icon = QIcon(os.path.join(self.plugin_dir, "icon.png"))

        # Le bouton de la barre d'outils ouvre les réglages ; l'activation se
        # fait dans le menu ou dans la fenêtre de réglages.
        self._settings_action = QAction(icon, "AutoSave Data : réglages", main_window)
        self._settings_action.setToolTip(
            "Réglages et état de la sauvegarde automatique")
        self._settings_action.triggered.connect(self.open_settings)
        self.iface.addToolBarIcon(self._settings_action)

        self._toggle_action = QAction("Sauvegarde automatique activée", main_window)
        self._toggle_action.setCheckable(True)
        self._toggle_action.setChecked(self.config.enabled)
        self._toggle_action.setToolTip(
            "Active ou désactive l'enregistrement automatique du projet")
        self._toggle_action.toggled.connect(self._on_toggled)
        self.iface.addPluginToMenu(MENU_TITLE, self._toggle_action)
        self._actions.append(self._toggle_action)

        for text, slot in (
            ("Enregistrer le projet maintenant", self.save_now),
            ("Restaurer une copie de secours...", self.open_restore_dialog),
            ("Ouvrir le dossier des copies de secours", self.open_backup_folder),
        ):
            action = QAction(text, main_window)
            action.triggered.connect(slot)
            self.iface.addPluginToMenu(MENU_TITLE, action)
            self._actions.append(action)
        self.iface.addPluginToMenu(MENU_TITLE, self._settings_action)
        self._actions.append(self._settings_action)

        # Indicateur de la barre d'état : un clic ouvre les réglages
        self._indicator = QToolButton()
        self._indicator.setAutoRaise(True)
        self._indicator.setContentsMargins(6, 0, 6, 0)
        self._indicator.clicked.connect(self.open_settings)
        try:
            self.iface.statusBarIface().addPermanentWidget(self._indicator)
        except Exception:  # la barre d'état est un confort, jamais un obstacle
            self._indicator = None

        self._debounce = QTimer(main_window)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._on_debounce_timeout)

        self._safety = QTimer(main_window)
        self._safety.timeout.connect(self._on_safety_tick)

        project = QgsProject.instance()
        if hasattr(project, "dirtySet"):
            self._connect(project.dirtySet, self._on_dirty_set)
        else:  # QGIS < 3.20 : ce signal ne part qu'au passage propre -> modifié
            self._connect(project.isDirtyChanged, self._on_dirty_changed)
        self._connect(project.cleared, self._on_project_changed)
        self._connect(project.readProject, self._on_project_changed)
        self._connect(project.projectSaved, self._on_project_saved)

        self._apply_config()
        self._remember_signature(project.fileName())
        self._start_session()
        if self.config.enabled and project.isDirty():
            self._schedule()

    def unload(self):
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._connections = []

        for timer in (self._debounce, self._safety, self._heartbeat):
            if timer is not None:
                timer.stop()
        self._debounce = None
        self._safety = None
        self._heartbeat = None
        self._stop_session()

        if self._indicator is not None:
            try:
                self.iface.statusBarIface().removeWidget(self._indicator)
                self._indicator.deleteLater()
            except Exception:
                pass
            self._indicator = None

        for action in self._actions:
            self.iface.removePluginMenu(MENU_TITLE, action)
        if self._settings_action is not None:
            self.iface.removeToolBarIcon(self._settings_action)
        self._actions = []
        self._toggle_action = None
        self._settings_action = None

    def _connect(self, signal, slot):
        signal.connect(slot)
        self._connections.append((signal, slot))

    # ------------------------------------------------------------------
    # Journal de session et récupération après fermeture inattendue
    # ------------------------------------------------------------------
    def _start_session(self):
        """Écrit le journal de cette session et signale les sessions mortes."""
        try:
            base = QgsApplication.qgisSettingsDirPath()
        except Exception:
            return
        if not base:
            return
        self.session = core.SessionTracker(base)
        stale = self.session.find_stale()
        self.session.start(QgsProject.instance().fileName())

        self._heartbeat = QTimer(self.iface.mainWindow())
        self._heartbeat.timeout.connect(lambda: self._session("heartbeat"))
        self._heartbeat.start(core.HEARTBEAT_SECONDS * 1000)
        try:
            self._quit_signal = QgsApplication.instance().aboutToQuit
            self._quit_signal.connect(self._stop_session)
        except Exception:
            self._quit_signal = None

        if not stale:
            return
        if self.config.recovery_prompt:
            QTimer.singleShot(RECOVERY_DELAY_MS, lambda: self._offer_recovery(stale))
        else:
            for info in stale:
                self.session.discard(info)

    def _stop_session(self):
        """Fermeture normale : la session ne sera pas signalée comme interrompue."""
        if self._quit_signal is not None:
            try:
                self._quit_signal.disconnect(self._stop_session)
            except (TypeError, RuntimeError):
                pass
            self._quit_signal = None
        if self.session is not None:
            self.session.mark_clean()
            self.session = None

    def _session(self, method, *args):
        if self.session is None:
            return
        try:
            getattr(self.session, method)(*args)
        except Exception:
            pass  # le journal est un confort : jamais un obstacle

    def _offer_recovery(self, stale):
        """Propose de rouvrir le projet de chaque session interrompue."""
        session = self.session
        if session is None:
            return
        if QApplication.activeModalWidget() is not None:
            # Une autre fenêtre est ouverte : on réessaie un peu plus tard
            QTimer.singleShot(RECOVERY_DELAY_MS, lambda: self._offer_recovery(stale))
            return
        for info in stale:
            session.discard(info)  # une seule proposition par session interrompue
            project = info.get("project") or ""
            if project and os.path.isfile(project):
                self._recover_project(project, info)

    def _recover_project(self, project, info):
        from .dialog import RestoreDialog  # import tardif : seul l'écran en a besoin
        try:
            current = core.BackupInfo(
                project, datetime.fromtimestamp(os.path.getmtime(project)),
                os.path.getsize(project), "Projet actuel (dernier enregistrement)")
            entries = [current] + core.describe_backups(project)
        except OSError:
            return
        saved = info.get("last_save")
        when = ""
        if saved:
            moment = datetime.fromtimestamp(saved)
            when = " Dernier enregistrement automatique : {} ({}).".format(
                moment.strftime("%H:%M:%S"), core.relative_age(moment))
        intro = ("La session précédente de QGIS s'est terminée sans fermeture normale "
                 "(plantage ou coupure de courant).{} Les modifications faites depuis, "
                 "et les objets d'une couche en édition non validés, ne peuvent pas "
                 "être retrouvés. Choisissez la version à rouvrir : le projet actuel "
                 "est ouvert tel quel, une copie est dupliquée à côté du projet."
                 .format(when))
        dialog = RestoreDialog(entries, os.path.basename(project),
                               self.iface.mainWindow(),
                               title="QGIS s'est fermé de façon inattendue",
                               intro=intro, ok_text="Ouvrir la version choisie")
        runner = getattr(dialog, "exec", None) or dialog.exec_
        if not runner():
            return
        chosen = dialog.selected_path()
        if not chosen:
            return
        if chosen == project:
            try:
                self.iface.addProject(project)
                self._status("Projet rouvert : {}".format(os.path.basename(project)), 10000)
            except Exception:
                self._warn("Ouvrez le projet avec Projet > Ouvrir : {}".format(project))
            return
        self._restore_and_open(chosen, project)

    # ------------------------------------------------------------------
    # Configuration appliquée aux minuteries et à l'interface
    # ------------------------------------------------------------------
    def _apply_config(self):
        if self._toggle_action is not None:
            blocked = self._toggle_action.blockSignals(True)
            self._toggle_action.setChecked(self.config.enabled)
            self._toggle_action.blockSignals(blocked)
        if self._safety is not None:
            if self.config.enabled:
                self._safety.start(self.config.safety_minutes * 60 * 1000)
            else:
                self._safety.stop()
        if not self.config.enabled and self._debounce is not None:
            self._debounce.stop()
        if not self.config.enabled:
            self._set_indicator(core.IND_OFF)
        elif self._indicator_state in (None, core.IND_OFF):
            # Réactivation (menu ou réglages) : sortir de l'état « désactivé »
            self._set_indicator(core.IND_IDLE)

    def _on_toggled(self, checked):
        self.config.enabled = bool(checked)
        self._persist_config()
        self._apply_config()
        if self.config.enabled:
            self._status("Sauvegarde automatique activée")
            if QgsProject.instance().isDirty():
                self._schedule()
        else:
            self._status("Sauvegarde automatique désactivée")

    def open_settings(self):
        from .dialog import (ACTION_FOLDER, ACTION_RESTORE, ACTION_SAVE,
                             SettingsDialog)  # import tardif : seul l'écran en a besoin
        filename = QgsProject.instance().fileName()
        info = {
            "name": os.path.basename(filename) if filename else "",
            "has_file": bool(filename),
            "state": self._indicator.text() if self._indicator is not None else "",
            "last_saved": self._last_saved_text,
            "backups": len(core.list_backups(filename)) if filename else 0,
        }
        dialog = SettingsDialog(self.config, self.iface.mainWindow(), info)
        runner = getattr(dialog, "exec", None) or dialog.exec_
        if not runner():
            return
        self.config = dialog.config().clamped()
        self._persist_config()
        self._apply_config()
        self._status("Réglages AutoSave Data enregistrés")
        if self.config.enabled and QgsProject.instance().isDirty():
            self._schedule()
        # Les actions demandées s'exécutent fenêtre fermée : une fenêtre modale
        # ouverte suspendrait l'enregistrement.
        if dialog.requested == ACTION_SAVE:
            self.save_now()
        elif dialog.requested == ACTION_RESTORE:
            self.open_restore_dialog()
        elif dialog.requested == ACTION_FOLDER:
            self.open_backup_folder()

    # ------------------------------------------------------------------
    # Réactions aux événements du projet
    # ------------------------------------------------------------------
    def _on_dirty_set(self):
        if self._saving or not self.config.enabled:
            return
        if self._pause_notified is not None:
            # En pause (édition ou traitement) : la minuterie de nouvel essai
            # tourne déjà, inutile de la relancer à chaque modification.
            return
        self._schedule()

    def _on_dirty_changed(self, dirty):
        if dirty:
            self._on_dirty_set()

    def _on_project_changed(self, *_args):
        """Nouveau projet ou projet fermé : on repart d'un état propre."""
        if self._debounce is not None:
            self._debounce.stop()
        self._first_dirty = None
        self._pause_notified = None
        self._warned_no_file = False
        self._warned_read_only = False
        self._warned_failure = False
        self._failed = False
        self._conflict = False
        self._warned_external = False
        self._remember_signature(QgsProject.instance().fileName())
        self._session("set_project", QgsProject.instance().fileName())
        self._set_indicator(core.IND_IDLE if self.config.enabled else core.IND_OFF)

    def _on_project_saved(self):
        # Enregistrement manuel ou automatique : plus rien n'est en attente
        self._first_dirty = None
        if not self._saving:
            # Enregistrement fait par l'utilisateur : le fichier actuel est le sien
            self._conflict = False
            self._warned_external = False
            self._remember_signature(QgsProject.instance().fileName())
            self._session("set_last_save")

    def _remember_signature(self, filename):
        """Mémorise l'état du fichier projet pour repérer une modification externe."""
        self._signature = core.file_signature(filename) if filename else None

    def _schedule(self):
        """Relance le compte à rebours, sans dépasser le délai maximal."""
        if self._debounce is None:
            return
        now = time.monotonic()
        if self._first_dirty is None:
            self._first_dirty = now
        base_delay, max_wait = core.effective_delays(
            self.config, self._last_save_seconds)
        remaining_cap = max_wait - (now - self._first_dirty)
        delay = min(base_delay, max(1.0, remaining_cap))
        self._debounce.start(int(delay * 1000))
        self._set_indicator(core.IND_PENDING)

    def _on_debounce_timeout(self):
        self._attempt_save()

    def _on_safety_tick(self):
        if self.config.enabled and QgsProject.instance().isDirty():
            self._attempt_save()

    # ------------------------------------------------------------------
    # Contexte courant (lecture seule)
    # ------------------------------------------------------------------
    @staticmethod
    def _editing_layers():
        names = []
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.isEditable():
                names.append(layer.name())
        return tuple(names)

    @staticmethod
    def _active_tasks():
        try:
            return int(QgsApplication.taskManager().countActiveTasks())
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Enregistrement
    # ------------------------------------------------------------------
    def save_now(self):
        self._attempt_save(manual=True)

    def _attempt_save(self, manual=False):
        project = QgsProject.instance()
        filename = project.fileName()
        writable = core.file_is_writable(filename)
        external = (writable is not None
                    and core.changed_externally(self._signature, filename))

        decision = core.evaluate(
            enabled=self.config.enabled,
            filename=filename,
            is_dirty=project.isDirty(),
            modal_open=QApplication.activeModalWidget() is not None,
            writable=writable,
            editing_layers=self._editing_layers(),
            tasks_active=self._active_tasks(),
            pause_while_editing=self.config.pause_while_editing,
            pause_during_tasks=self.config.pause_during_tasks,
            manual=manual,
            external_change=external,
        )

        if decision.action == core.WAIT:
            self._handle_wait(decision)
            return
        if decision.action == core.SKIP:
            self._handle_skip(decision, manual)
            return
        # Demande manuelle malgré une modification externe : on écrase, mais
        # la version étrangère est d'abord copiée dans les sauvegardes.
        self._write_project(filename, writable is not None, keep_foreign=external)

    def _handle_wait(self, decision):
        """Un travail est en cours : on ne touche à rien et on réessaie."""
        if self._debounce is not None and self.config.enabled:
            self._debounce.start(RETRY_MS)
        if decision.reason == core.R_MODAL:
            return
        # Un seul message par épisode de pause, pour ne pas harceler
        if self._pause_notified != decision.reason:
            self._pause_notified = decision.reason
            if decision.reason == core.R_EDITING:
                self._status("AutoSave Data en pause : couche en édition ({})"
                             .format(decision.detail), 8000)
                self._set_indicator(core.IND_PAUSED_EDIT, decision.detail)
            else:
                self._status("AutoSave Data en pause : traitement en cours", 8000)
                self._set_indicator(core.IND_PAUSED_TASK)
            self._log("Pause ({}) : {}".format(decision.reason, decision.detail))

    def _handle_skip(self, decision, manual):
        # Après un SKIP aucune minuterie de nouvel essai ne tourne : un épisode
        # de pause resté marqué bloquerait la planification des modifications
        # suivantes (enregistrement manuel pendant une pause, par exemple).
        self._pause_notified = None
        if decision.reason == core.R_CLEAN:
            self._first_dirty = None
            self._set_indicator(core.IND_IDLE)
            if manual:
                self._status("Aucune modification à enregistrer")
        elif decision.reason == core.R_NO_FILE:
            if manual or not self._warned_no_file:
                self._warned_no_file = True
                self._status("AutoSave Data : enregistrez le projet une première fois "
                             "(Projet > Enregistrer sous), la sauvegarde automatique "
                             "prendra le relais ensuite.", 10000)
        elif decision.reason == core.R_EXTERNAL:
            self._conflict = True
            self._set_indicator(core.IND_CONFLICT)
            if not self._warned_external:
                self._warned_external = True
                self._warn("Le fichier du projet a été modifié en dehors de cette "
                           "session de QGIS (autre utilisateur ou autre fenêtre). "
                           "L'enregistrement automatique est suspendu pour ne rien "
                           "écraser. « Enregistrer le projet maintenant » écrase le "
                           "fichier après en avoir copié la version actuelle dans "
                           "les copies de secours ; rouvrir le projet recharge "
                           "la version du fichier.")
                self._log("Modification externe détectée : {}".format(
                    QgsProject.instance().fileName()), "Warning")
        elif decision.reason == core.R_READ_ONLY:
            if manual or not self._warned_read_only:
                self._warned_read_only = True
                self._warn("Le fichier du projet est en lecture seule, "
                           "enregistrement automatique impossible.")

    def _write_project(self, filename, is_local_file, keep_foreign=False):
        project = QgsProject.instance()
        ok = False
        rolled_back = False
        error_text = ""
        started = time.monotonic()
        self._saving = True
        try:
            previous = None
            if is_local_file:
                if self.config.backups_enabled or keep_foreign:
                    self._make_backup(filename, force=keep_foreign)
                if keep_foreign:
                    self._log("Fichier modifié ailleurs : sa version actuelle a été "
                              "copiée dans les copies de secours avant écrasement.",
                              "Warning")
                previous = self._snapshot_previous(filename)
            ok = bool(project.write())
            if not ok:
                error_text = self._project_error(project)
            elif is_local_file:
                healthy, why = core.verify_project_file(filename)
                if not healthy:
                    ok = False
                    error_text = "fichier illisible après écriture ({})".format(why)
                    if previous and self._restore_previous(filename):
                        rolled_back = True
                        error_text += " : version précédente remise en place"
        except Exception:  # ne jamais laisser une exception remonter dans Qt
            error_text = traceback.format_exc()
        finally:
            self._saving = False
        self._last_save_seconds = time.monotonic() - started
        if is_local_file:
            # Le fichier a été écrit (ou restauré) par nous : c'est notre référence
            self._remember_signature(filename)

        if ok:
            self._first_dirty = None
            self._pause_notified = None
            self._warned_failure = False
            self._failed = False
            self._conflict = False
            self._warned_external = False
            self._notify_saved(filename)
        else:
            self._failed = True
            self._log("Échec de l'enregistrement : {}".format(error_text), "Critical")
            self._set_indicator(core.IND_ERROR)
            if rolled_back:
                # Le projet en mémoire est intact : on le marque à nouveau à
                # enregistrer pour qu'un nouvel essai ait lieu.
                try:
                    project.setDirty(True)
                except Exception:
                    pass
            if not self._warned_failure:
                self._warned_failure = True
                self._warn("L'enregistrement automatique a échoué. Détails dans "
                           "le journal des messages (onglet AutoSave Data).")

    def _snapshot_previous(self, filename):
        try:
            return core.snapshot_previous(filename)
        except OSError as exc:
            self._log("Cliché de la version précédente impossible : {}".format(exc),
                      "Warning")
            return None

    def _restore_previous(self, filename):
        try:
            return core.restore_previous(filename)
        except OSError as exc:
            self._log("Restauration de la version précédente impossible : {}"
                      .format(exc), "Critical")
            return False

    @staticmethod
    def _project_error(project):
        try:
            return str(project.error())
        except Exception:
            return ""

    def _make_backup(self, filename, force=False):
        now = time.monotonic()
        interval = self.config.backups_min_interval_minutes * 60
        if not force and not self.backups.due(filename, now, interval):
            return
        try:
            target = core.create_backup(filename)
            if target:
                self.backups.mark(filename, now)
                removed = core.prune_backups(filename, self.config.backups_keep)
                self._log("Copie de secours : {} ({} ancienne(s) supprimée(s))"
                          .format(target, len(removed)))
        except OSError as exc:
            # Une copie impossible (disque plein, droits) ne doit pas bloquer
            # l'enregistrement du travail en cours.
            self._log("Copie de secours impossible : {}".format(exc), "Warning")

    # ------------------------------------------------------------------
    # Copies de secours
    # ------------------------------------------------------------------
    def open_backup_folder(self):
        filename = QgsProject.instance().fileName()
        if not filename:
            self._status("Le projet n'a pas encore de fichier.")
            return
        folder = core.backup_dir_for(filename)
        if not os.path.isdir(folder):
            self._status("Aucune copie de secours pour ce projet pour le moment.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def open_restore_dialog(self):
        filename = QgsProject.instance().fileName()
        if not filename:
            self._status("Le projet n'a pas encore de fichier.")
            return
        backups = core.describe_backups(filename)
        if not backups:
            self._status("Aucune copie de secours pour ce projet pour le moment.")
            return
        from .dialog import RestoreDialog  # import tardif : seul l'écran en a besoin
        dialog = RestoreDialog(backups, os.path.basename(filename),
                               self.iface.mainWindow())
        runner = getattr(dialog, "exec", None) or dialog.exec_
        if not runner():
            return
        chosen = dialog.selected_path()
        if not chosen:
            return
        self._restore_and_open(chosen, filename)

    def _restore_and_open(self, chosen, filename):
        """Duplique la copie choisie à côté du projet puis l'ouvre, sans rien écraser."""
        try:
            restored = core.restore_copy(chosen, filename)
        except OSError as exc:
            self._warn("Restauration impossible : {}".format(exc))
            return
        self._log("Copie restaurée : {} -> {}".format(chosen, restored))
        try:
            self.iface.addProject(restored)
            self._status("Copie restaurée et ouverte : {}"
                         .format(os.path.basename(restored)), 10000)
        except Exception:
            self._warn("La copie a été créée ({}). Ouvrez-la avec Projet > Ouvrir."
                       .format(restored))

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    def _set_indicator(self, state, detail=""):
        if self._indicator is None:
            return
        if (self._failed or self._conflict) and state in (core.IND_PENDING, core.IND_IDLE):
            return  # échec ou conflit : affiché jusqu'au prochain enregistrement réussi
        stamp = time.strftime("%H:%M")
        try:
            self._indicator.setText(core.indicator_text(state, detail, stamp))
            self._indicator.setToolTip(
                "AutoSave Data : cliquez pour ouvrir les réglages")
            self._indicator_state = state
        except Exception:
            pass

    def _notify_saved(self, filename):
        stamp = time.strftime("%H:%M:%S")
        self._last_saved_text = stamp
        self._session("set_last_save")
        self._set_indicator(core.IND_SAVED)
        text = "Projet enregistré automatiquement à {}".format(stamp)
        self._status(text, 6000)
        self._log("{} ({})".format(text, filename))
        if self.config.show_message_bar:
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME, text, level=_level("Success"), duration=3)

    def _status(self, text, timeout_ms=5000):
        try:
            self.iface.statusBarIface().showMessage(text, timeout_ms)
        except Exception:
            self._log(text)

    def _warn(self, text):
        self.iface.messageBar().pushMessage(
            PLUGIN_NAME, text, level=_level("Warning"), duration=10)
        self._log(text, "Warning")

    @staticmethod
    def _log(text, level="Info"):
        QgsMessageLog.logMessage(text, PLUGIN_NAME, _level(level))
