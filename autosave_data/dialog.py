# -*- coding: utf-8 -*-
"""AutoSave Data : fenêtres de réglages et de restauration (construites en code, sans fichier .ui).

Licence : GNU GPL v2 ou ultérieure.
"""
import os

from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                                 QFormLayout, QFrame, QGroupBox, QHBoxLayout,
                                 QLabel, QListWidget, QListWidgetItem,
                                 QPushButton, QSpinBox, QVBoxLayout)

from . import core

_HERE = os.path.dirname(os.path.abspath(__file__))

# Actions que la fenêtre de réglages peut demander au plugin (après sa fermeture,
# car une fenêtre modale ouverte suspend l'enregistrement automatique).
ACTION_SAVE = "save"
ACTION_RESTORE = "restore"
ACTION_FOLDER = "folder"


# Violet améthyste : teinte moyenne pour les fonds clairs, plus claire pour les fonds sombres
AMETHYST = "#7B4FB3"
AMETHYST_DEEP = "#5B2F91"
AMETHYST_LIGHT = "#9966CC"
AMETHYST_ON_DARK = "#B590E6"


def _is_dark(widget):
    try:
        return widget.palette().window().color().lightness() < 128
    except Exception:
        return False


def _stylesheet(dark):
    """Habillage améthyste, limité à nos fenêtres (feuille de style posée sur le dialogue)."""
    accent = AMETHYST_ON_DARK if dark else AMETHYST
    return """
    QFrame#banner {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {deep}, stop:1 {light}); border-radius: 8px; }}
    QFrame#banner QLabel {{ color: white; background: transparent; }}
    QLabel#bannerTitle {{ font-size: 15pt; font-weight: bold; }}
    QLabel#bannerSub {{ color: #EBDDF7; }}
    QLabel#logoBox {{ background: white; border-radius: 8px; padding: 4px; }}
    QGroupBox {{ border: 1px solid rgba(153, 102, 204, 90); border-radius: 6px;
        margin-top: 14px; padding-top: 8px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px;
        color: {accent}; font-weight: bold; }}
    QGroupBox#projectBox {{ background-color: rgba(153, 102, 204, 28);
        border: 1px solid rgba(153, 102, 204, 140); }}
    QCheckBox#master {{ font-weight: bold; color: {accent}; }}
    QPushButton#primary {{ background-color: {accent_bg}; color: white; border: none;
        border-radius: 4px; padding: 6px 14px; font-weight: bold; }}
    QPushButton#primary:hover {{ background-color: {deep}; }}
    QPushButton#action {{ border: 1px solid {accent}; border-radius: 4px;
        padding: 5px 10px; color: {accent}; background: transparent; }}
    QPushButton#action:hover {{ background-color: rgba(153, 102, 204, 55); }}
    QPushButton#action:disabled {{ color: gray; border-color: rgba(128, 128, 128, 110); }}
    QListWidget::item:selected {{ background-color: {accent_bg}; color: white; }}
    """.format(deep=AMETHYST_DEEP, light=AMETHYST_LIGHT, accent=accent,
               accent_bg=AMETHYST)


def _banner(title, subtitle, with_logo=True):
    """Bandeau d'en-tête violet avec logo, titre et sous-titre."""
    frame = QFrame()
    frame.setObjectName("banner")
    row = QHBoxLayout(frame)
    row.setContentsMargins(14, 10, 14, 10)
    pixmap = QPixmap(os.path.join(_HERE, "icon.png"))
    if with_logo and not pixmap.isNull():
        logo = QLabel()
        logo.setObjectName("logoBox")
        logo.setPixmap(pixmap.scaledToWidth(36))
        row.addWidget(logo)
    texts = QVBoxLayout()
    head = QLabel(title)
    head.setObjectName("bannerTitle")
    sub = QLabel(subtitle)
    sub.setObjectName("bannerSub")
    sub.setWordWrap(True)
    texts.addWidget(head)
    texts.addWidget(sub)
    row.addLayout(texts, 1)
    return frame


def _spin(low, high, value, suffix):
    box = QSpinBox()
    box.setRange(low, high)
    box.setValue(value)
    box.setSuffix(suffix)
    box.setMinimumWidth(110)
    return box


class SettingsDialog(QDialog):
    """Réglages et tableau de bord : état du projet, actions rapides, options.

    `info` (facultatif) décrit le projet en cours : name, has_file, last_saved,
    backups, state. Les boutons d'action ferment la fenêtre en appliquant les
    réglages ; l'action demandée est lue dans `requested`.
    """

    def __init__(self, config, parent=None, info=None):
        super().__init__(parent)
        info = info or {}
        self.requested = None
        self.setWindowTitle("AutoSave Data : réglages")
        self.setMinimumWidth(820)

        # --- En-tête --------------------------------------------------------
        version = core.read_version(os.path.join(_HERE, "metadata.txt"))
        header = _banner("AutoSave Data" + (" " + version if version else ""),
                         "Enregistrement automatique du projet, sans question, "
                         "en pause pendant l'édition des couches.")

        # --- Projet en cours ------------------------------------------------
        self.project_box = QGroupBox("Projet en cours")
        self.project_box.setObjectName("projectBox")
        project_form = QFormLayout()
        has_file = bool(info.get("has_file"))
        name = info.get("name") or "Projet non enregistré"
        project_form.addRow("Projet", QLabel(name))
        project_form.addRow("État", QLabel(info.get("state") or "-"))
        project_form.addRow("Dernier enregistrement",
                            QLabel(info.get("last_saved") or "aucun pour le moment"))
        backups = int(info.get("backups") or 0)
        project_form.addRow("Copies de secours", QLabel(str(backups)))
        hint = None
        if not has_file:
            hint = QLabel("Enregistrez d'abord le projet : Projet > Enregistrer sous.")

        actions = QHBoxLayout()
        self.save_button = QPushButton("Enregistrer maintenant")
        self.save_button.setObjectName("action")
        self.save_button.setEnabled(has_file)
        self.save_button.clicked.connect(lambda: self._request(ACTION_SAVE))
        self.restore_button = QPushButton("Restaurer une copie...")
        self.restore_button.setObjectName("action")
        self.restore_button.setEnabled(has_file and backups > 0)
        self.restore_button.clicked.connect(lambda: self._request(ACTION_RESTORE))
        self.folder_button = QPushButton("Ouvrir le dossier des copies")
        self.folder_button.setObjectName("action")
        self.folder_button.setEnabled(has_file and backups > 0)
        self.folder_button.clicked.connect(lambda: self._request(ACTION_FOLDER))
        for button in (self.save_button, self.restore_button, self.folder_button):
            actions.addWidget(button)
        project_layout = QVBoxLayout(self.project_box)
        project_layout.addLayout(project_form)
        if hint is not None:
            project_layout.addWidget(hint)
        project_layout.addLayout(actions)

        # --- Réglages -------------------------------------------------------
        self.enabled = QCheckBox("Activer la sauvegarde automatique du projet")
        self.enabled.setObjectName("master")

        self.delay = _spin(5, 600, config.delay_seconds, " s")
        self.delay.setToolTip(
            "Le projet est enregistré quand vous n'avez rien modifié "
            "depuis ce délai.")
        self.max_wait = _spin(30, 3600, config.max_wait_seconds, " s")
        self.max_wait.setToolTip(
            "Délai maximal en travail continu : si vous ne faites jamais de pause, "
            "le projet est quand même enregistré après ce délai (sauf pendant une pause automatique).")
        self.safety = _spin(1, 120, config.safety_minutes, " min")
        self.safety.setToolTip(
            "Contrôle de sécurité toutes les N minutes : enregistre s'il reste des modifications.")
        self.adaptive = QCheckBox("Allonger la pause sur les gros projets")
        self.adaptive.setToolTip(
            "La pause devient au moins 10 fois la durée du dernier "
            "enregistrement, pour que QGIS ne se fige pas trop souvent.")

        self.save_box = QGroupBox("Enregistrement")
        save_form = QFormLayout()
        save_form.addRow("Après une pause de", self.delay)
        save_form.addRow("Délai maximal", self.max_wait)
        save_form.addRow("Contrôle de sécurité", self.safety)
        save_layout = QVBoxLayout(self.save_box)
        save_layout.addLayout(save_form)
        save_layout.addWidget(self.adaptive)

        self.pause_editing = QCheckBox("Couche en cours d'édition")
        self.pause_editing.setToolTip(
            "Recommandé pour la vectorisation et la saisie : le projet n'est "
            "pas touché tant qu'une couche vectorielle est en mode édition. "
            "L'enregistrement reprend dès la fin de l'édition.")
        self.pause_tasks = QCheckBox("Traitement en arrière-plan")
        self.pause_tasks.setToolTip(
            "Évite d'enregistrer pendant qu'un algorithme utilise des couches.")
        self.pause_box = QGroupBox("Ne rien enregistrer pendant")
        pause_layout = QVBoxLayout(self.pause_box)
        pause_layout.addWidget(self.pause_editing)
        pause_layout.addWidget(self.pause_tasks)

        self.backups = QCheckBox("Conserver des copies horodatées")
        self.keep = _spin(1, 100, config.backups_keep, " copies")
        self.keep.setToolTip("Nombre de copies conservées par projet.")
        self.backup_interval = _spin(
            1, 240, config.backups_min_interval_minutes, " min")
        self.backup_interval.setToolTip(
            "Intervalle minimal entre deux copies de secours. La première copie de la "
            "session est toujours faite avant le premier écrasement.")
        self.backup_box = QGroupBox("Copies de secours")
        backup_form = QFormLayout()
        backup_form.addRow("Copies à conserver", self.keep)
        backup_form.addRow("Intervalle entre copies", self.backup_interval)
        self.recovery = QCheckBox("Proposer la reprise après un arrêt brutal")
        self.recovery.setToolTip(
            "Au démarrage de QGIS, si la session précédente s'est terminée sans "
            "fermeture normale (plantage, coupure de courant), une fenêtre propose "
            "de rouvrir le projet ou une copie de secours.")
        note = QLabel("Dossier : « nom_du_projet_sauvegardes »")
        note.setToolTip("Les copies sont rangées dans ce dossier, "
                        "à côté du fichier projet.")
        backup_layout = QVBoxLayout(self.backup_box)
        backup_layout.addWidget(self.backups)
        backup_layout.addLayout(backup_form)
        backup_layout.addWidget(note)
        backup_layout.addWidget(self.recovery)

        self.message_bar = QCheckBox("Notifier dans la barre de messages")
        self.message_bar.setToolTip(
            "L'indicateur de la barre d'état est toujours affiché.")
        self.notify_box = QGroupBox("Notifications")
        notify_layout = QVBoxLayout(self.notify_box)
        notify_layout.addWidget(self.message_bar)

        left = QVBoxLayout()
        left.addWidget(self.save_box)
        left.addWidget(self.pause_box)
        left.addStretch(1)
        right = QVBoxLayout()
        right.addWidget(self.backup_box)
        right.addWidget(self.notify_box)
        right.addStretch(1)
        columns = QHBoxLayout()
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)

        # --- Boutons --------------------------------------------------------
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setText("Enregistrer les réglages")
        ok_button.setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        self.defaults_button = buttons.button(
            QDialogButtonBox.StandardButton.RestoreDefaults)
        self.defaults_button.setText("Valeurs par défaut")
        self.defaults_button.clicked.connect(self._restore_defaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addWidget(self.project_box)
        layout.addWidget(self.enabled)
        layout.addLayout(columns)
        layout.addWidget(buttons)

        self.enabled.toggled.connect(self._sync)
        self.backups.toggled.connect(self._sync)
        self.setStyleSheet(_stylesheet(_is_dark(self)))
        self._load(config)
        self.adjustSize()

    # ------------------------------------------------------------------
    def _load(self, config):
        self.enabled.setChecked(config.enabled)
        self.delay.setValue(config.delay_seconds)
        self.max_wait.setValue(config.max_wait_seconds)
        self.safety.setValue(config.safety_minutes)
        self.adaptive.setChecked(config.adaptive_delay)
        self.pause_editing.setChecked(config.pause_while_editing)
        self.pause_tasks.setChecked(config.pause_during_tasks)
        self.backups.setChecked(config.backups_enabled)
        self.keep.setValue(config.backups_keep)
        self.backup_interval.setValue(config.backups_min_interval_minutes)
        self.message_bar.setChecked(config.show_message_bar)
        self.recovery.setChecked(config.recovery_prompt)
        self._sync()

    def _sync(self, *_args):
        """Grise les réglages qui n'ont plus d'effet."""
        active = self.enabled.isChecked()
        for box in (self.save_box, self.pause_box, self.backup_box, self.notify_box):
            box.setEnabled(active)
        backups_on = self.backups.isChecked()
        self.keep.setEnabled(backups_on)
        self.backup_interval.setEnabled(backups_on)

    def _restore_defaults(self):
        self._load(core.Config())

    def _request(self, action):
        self.requested = action
        self.accept()

    def config(self):
        return core.Config(
            enabled=self.enabled.isChecked(),
            delay_seconds=self.delay.value(),
            max_wait_seconds=self.max_wait.value(),
            safety_minutes=self.safety.value(),
            adaptive_delay=self.adaptive.isChecked(),
            pause_while_editing=self.pause_editing.isChecked(),
            pause_during_tasks=self.pause_tasks.isChecked(),
            backups_enabled=self.backups.isChecked(),
            backups_keep=self.keep.value(),
            backups_min_interval_minutes=self.backup_interval.value(),
            show_message_bar=self.message_bar.isChecked(),
            recovery_prompt=self.recovery.isChecked(),
        )


class RestoreDialog(QDialog):
    """Choix d'une copie de secours à restaurer (liste de la plus récente à la plus ancienne)."""

    def __init__(self, backups, project_name, parent=None, title=None,
                 intro=None, ok_text="Restaurer et ouvrir"):
        super().__init__(parent)
        self.setWindowTitle(title or "AutoSave Data : restaurer une copie de secours")
        self.setMinimumWidth(520)

        banner = _banner(title or "Restaurer une copie de secours",
                         "Copies de « {} », de la plus récente à la plus ancienne."
                         .format(project_name))
        intro_label = QLabel(intro or (
            "La copie choisie est dupliquée à côté du projet sous le nom "
            "« ..._restaure_date », puis ouverte. Votre projet actuel et la copie "
            "d'origine ne sont pas modifiés."))
        intro_label.setWordWrap(True)

        self.list = QListWidget()
        self.list.setMinimumHeight(120)
        for info in backups:
            label = "{}{}   ({}, {})".format(
                info.label + "   " if info.label else "Copie du ",
                info.when.strftime("%d/%m/%Y à %H:%M:%S"),
                core.relative_age(info.when), core.human_size(info.size))
            item = QListWidgetItem(label)
            item.setData(self._role(), info.path)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setText(ok_text)
        ok_button.setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(banner)
        layout.addWidget(intro_label)
        layout.addWidget(self.list)
        layout.addWidget(buttons)
        self.setStyleSheet(_stylesheet(_is_dark(self)))

    @staticmethod
    def _role():
        from qgis.PyQt.QtCore import Qt
        scoped = getattr(Qt, "ItemDataRole", None)
        return scoped.UserRole if scoped is not None else Qt.UserRole

    def selected_path(self):
        item = self.list.currentItem()
        return item.data(self._role()) if item is not None else None
