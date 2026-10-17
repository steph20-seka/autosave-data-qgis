# -*- coding: utf-8 -*-
"""AutoSave Data : logique pure, sans dépendance à Qt ni à QGIS.

Ce module contient les règles de décision (quand enregistrer, quand patienter),
les réglages et la gestion des copies de secours. Il est testable hors de QGIS.

Licence : GNU GPL v2 ou ultérieure.
"""
import json
import math
import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass, fields
from datetime import datetime

SETTINGS_GROUP = "AutoSaveData"
BACKUP_DIR_SUFFIX = "_sauvegardes"
_STAMP_FORMAT = "%Y%m%d_%H%M%S"
PREVIOUS_SUFFIX = "_precedent"
RESTORED_MARK = "_restaure"
# Une pause adaptative vaut au moins N fois la durée du dernier enregistrement
ADAPTIVE_FACTOR = 10
MAX_DELAY_SECONDS = 600

# Actions possibles après évaluation
SAVE = "save"
SKIP = "skip"
WAIT = "wait"

# Raisons
R_OK = "ok"
R_DISABLED = "disabled"
R_NO_FILE = "no_file"
R_CLEAN = "clean"
R_READ_ONLY = "read_only"
R_MODAL = "modal"
R_EDITING = "editing"
R_TASKS = "tasks"
R_EXTERNAL = "external"


# --------------------------------------------------------------------------
# Réglages
# --------------------------------------------------------------------------
@dataclass
class Config:
    enabled: bool = True
    # Enregistrer quand l'utilisateur n'a plus rien modifié depuis N secondes
    delay_seconds: int = 20
    # Délai maximal entre la première modification et l'enregistrement
    max_wait_seconds: int = 120
    # Contrôle périodique de sécurité (minutes)
    safety_minutes: int = 5
    # Allonge la pause quand l'enregistrement du projet est lent (gros projets)
    adaptive_delay: bool = True
    # Ne pas enregistrer tant qu'une couche vectorielle est en édition
    pause_while_editing: bool = True
    # Ne pas enregistrer tant qu'un traitement tourne en arrière-plan
    pause_during_tasks: bool = True
    # Copies de secours horodatées du fichier projet
    backups_enabled: bool = True
    backups_keep: int = 10
    backups_min_interval_minutes: int = 10
    # Notification dans la barre de messages (la barre d'état est toujours utilisée)
    show_message_bar: bool = False
    # Au démarrage, proposer de récupérer le projet après une fermeture inattendue
    recovery_prompt: bool = True

    def clamped(self):
        """Retourne une copie dont les valeurs numériques sont bornées."""
        cfg = Config(**{f.name: getattr(self, f.name) for f in fields(self)})
        cfg.delay_seconds = _clamp(cfg.delay_seconds, 5, 600)
        cfg.max_wait_seconds = _clamp(cfg.max_wait_seconds, 30, 3600)
        cfg.max_wait_seconds = max(cfg.max_wait_seconds, cfg.delay_seconds)
        cfg.safety_minutes = _clamp(cfg.safety_minutes, 1, 120)
        cfg.backups_keep = _clamp(cfg.backups_keep, 1, 100)
        cfg.backups_min_interval_minutes = _clamp(
            cfg.backups_min_interval_minutes, 1, 240)
        return cfg


def _clamp(value, low, high):
    return max(low, min(high, value))


def to_bool(value, default=False):
    """Convertit une valeur lue dans les réglages (bool, str, int) en bool."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "oui", "on"):
        return True
    if text in ("false", "0", "no", "non", "off", ""):
        return False
    return default


def to_int(value, default):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def load_config(getter):
    """Construit un Config à partir de getter(clé, valeur_par_défaut)."""
    default = Config()
    cfg = Config()
    for f in fields(Config):
        default_value = getattr(default, f.name)
        raw = getter(f.name, default_value)
        if isinstance(default_value, bool):
            setattr(cfg, f.name, to_bool(raw, default_value))
        else:
            setattr(cfg, f.name, to_int(raw, default_value))
    return cfg.clamped()


def save_config(setter, cfg):
    """Enregistre chaque champ via setter(clé, valeur)."""
    for f in fields(Config):
        setter(f.name, getattr(cfg, f.name))


# --------------------------------------------------------------------------
# Décision : enregistrer, patienter ou ignorer
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Decision:
    action: str
    reason: str = R_OK
    detail: str = ""


def file_is_writable(path):
    """True/False pour un fichier local existant, None si indéterminé."""
    if not path or not os.path.isfile(path):
        return None
    return os.access(path, os.W_OK)


def file_signature(path):
    """Empreinte légère d'un fichier : (date de modification en ns, taille).

    Sert à repérer qu'un fichier a été modifié par un autre utilisateur ou une
    autre instance de QGIS depuis notre dernière lecture ou écriture.
    Retourne None si le fichier est absent ou illisible.
    """
    if not path:
        return None
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return None
    return (info.st_mtime_ns, info.st_size)


def changed_externally(known, path):
    """True si le fichier ne correspond plus à l'empreinte `known`.

    Sans empreinte connue (None) ou si le fichier a disparu, on ne peut rien
    affirmer : retourne False.
    """
    if known is None:
        return False
    current = file_signature(path)
    return current is not None and current != known


def evaluate(enabled, filename, is_dirty, modal_open, writable,
             editing_layers=(), tasks_active=0,
             pause_while_editing=True, pause_during_tasks=True,
             manual=False, external_change=False):
    """Décide quoi faire au moment où le délai d'enregistrement expire.

    - SAVE : enregistrer maintenant.
    - WAIT : un travail est en cours (édition, traitement, fenêtre modale),
      on réessaie plus tard sans rien enregistrer.
    - SKIP : rien à faire (désactivé, aucun fichier, aucune modification...).

    Une demande manuelle (manual=True) ignore les pauses liées à l'édition et
    aux traitements, car l'utilisateur la déclenche lui-même. Elle ignore aussi
    `external_change` : l'appelant fait alors une copie de la version
    étrangère avant d'écraser.

    Si le fichier a été modifié ailleurs depuis notre dernière lecture ou
    écriture (`external_change`), l'enregistrement automatique n'écrase rien.
    """
    if not enabled and not manual:
        return Decision(SKIP, R_DISABLED)
    if not filename:
        return Decision(SKIP, R_NO_FILE)
    if not is_dirty:
        return Decision(SKIP, R_CLEAN)
    if writable is False:
        return Decision(SKIP, R_READ_ONLY)
    if external_change and not manual:
        return Decision(SKIP, R_EXTERNAL)
    if modal_open:
        return Decision(WAIT, R_MODAL)
    if not manual:
        if pause_while_editing and editing_layers:
            return Decision(WAIT, R_EDITING, ", ".join(editing_layers))
        if pause_during_tasks and tasks_active > 0:
            return Decision(WAIT, R_TASKS, str(tasks_active))
    return Decision(SAVE, R_OK)


# --------------------------------------------------------------------------
# Délais adaptatifs
# --------------------------------------------------------------------------
def effective_delays(cfg, last_save_seconds=0.0):
    """Délais réellement appliqués : (pause, délai maximal), en secondes.

    Sur un gros projet, un enregistrement prend plusieurs secondes pendant
    lesquelles QGIS est figé. Avec `adaptive_delay`, la pause vaut au moins
    ADAPTIVE_FACTOR fois la durée du dernier enregistrement : l'extension ne
    consomme alors qu'environ 10 % du temps de travail.
    """
    delay = cfg.delay_seconds
    if cfg.adaptive_delay and last_save_seconds and last_save_seconds > 0:
        delay = max(delay, int(math.ceil(last_save_seconds * ADAPTIVE_FACTOR)))
    delay = min(delay, MAX_DELAY_SECONDS)
    return delay, max(cfg.max_wait_seconds, delay)


# --------------------------------------------------------------------------
# Indicateur d'état (barre d'état)
# --------------------------------------------------------------------------
IND_OFF = "off"
IND_IDLE = "idle"
IND_PENDING = "pending"
IND_PAUSED_EDIT = "paused_edit"
IND_PAUSED_TASK = "paused_task"
IND_SAVED = "saved"
IND_ERROR = "error"
IND_CONFLICT = "conflict"


def indicator_text(state, detail="", stamp=""):
    """Texte court de l'indicateur de la barre d'état."""
    if state == IND_OFF:
        return "AutoSave : désactivé"
    if state == IND_PENDING:
        return "AutoSave : modifications en attente"
    if state == IND_PAUSED_EDIT:
        shown = detail if len(detail) <= 24 else detail[:23] + "…"
        return "AutoSave : en pause (édition de {})".format(shown) if shown \
            else "AutoSave : en pause (édition)"
    if state == IND_PAUSED_TASK:
        return "AutoSave : en pause (traitement)"
    if state == IND_SAVED:
        return "AutoSave : enregistré à {}".format(stamp) if stamp \
            else "AutoSave : enregistré"
    if state == IND_ERROR:
        return "AutoSave : échec, voir le journal"
    if state == IND_CONFLICT:
        return "AutoSave : fichier modifié ailleurs, rien écrasé"
    return "AutoSave : actif"


# --------------------------------------------------------------------------
# Vérification du fichier projet après écriture
# --------------------------------------------------------------------------
def verify_project_file(path):
    """Contrôle qu'un fichier projet est complet et lisible.

    Retourne (ok, raison). Les extensions inconnues ne sont pas vérifiées
    (ok = True). Un .qgz doit être une archive intacte contenant un .qgs ;
    un .qgs doit commencer par l'élément <qgis> et se terminer par </qgis>.
    """
    if not path or not os.path.isfile(path):
        return False, "fichier absent"
    try:
        if os.path.getsize(path) == 0:
            return False, "fichier vide"
        ext = os.path.splitext(path)[1].lower()
        if ext == ".qgz":
            if not zipfile.is_zipfile(path):
                return False, "archive illisible"
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:
                    return False, "archive corrompue"
                if not any(n.lower().endswith(".qgs") for n in archive.namelist()):
                    return False, "projet absent de l'archive"
            return True, ""
        if ext == ".qgs":
            with open(path, "rb") as handle:
                head = handle.read(8192)
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 256))
                tail = handle.read()
            if b"<qgis" not in head:
                return False, "en-tête du projet absent"
            if b"</qgis>" not in tail:
                return False, "fin du projet absente"
            return True, ""
    except (OSError, zipfile.BadZipFile) as exc:
        return False, str(exc)
    return True, ""


def previous_path(path):
    folder, stem, ext = _split(path)
    return os.path.join(backup_dir_for(path), stem + PREVIOUS_SUFFIX + ext)


def snapshot_previous(path):
    """Garde la dernière version saine du projet avant un nouvel écrasement.

    Un seul fichier, remplacé à chaque enregistrement : il sert uniquement à
    revenir en arrière si l'écriture suivante produit un fichier illisible.
    Retourne le chemin du cliché, ou None si le fichier actuel n'est pas sain.
    """
    ok, _ = verify_project_file(path)
    if not ok:
        return None
    target = previous_path(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copy2(path, target)
    return target


def restore_previous(path):
    """Remet le cliché en place. Retourne True si la restauration a eu lieu."""
    source = previous_path(path)
    if not os.path.isfile(source):
        return False
    shutil.copy2(source, path)
    return True


# --------------------------------------------------------------------------
# Copies de secours
# --------------------------------------------------------------------------
def _split(path):
    folder, base = os.path.split(path)
    stem, ext = os.path.splitext(base)
    return folder, stem, ext


def backup_dir_for(path):
    folder, stem, _ = _split(path)
    return os.path.join(folder, stem + BACKUP_DIR_SUFFIX)


def create_backup(path, now=None):
    """Copie le fichier projet existant dans le dossier de sauvegardes.

    Retourne le chemin de la copie, ou None s'il n'y a rien à copier.
    """
    if not path or not os.path.isfile(path) or os.path.getsize(path) == 0:
        return None
    _, stem, ext = _split(path)
    now = now or datetime.now()
    target_dir = backup_dir_for(path)
    os.makedirs(target_dir, exist_ok=True)
    base_name = "{}_{}".format(stem, now.strftime(_STAMP_FORMAT))
    target = os.path.join(target_dir, base_name + ext)
    counter = 1
    while os.path.exists(target):
        target = os.path.join(
            target_dir, "{}_{}{}".format(base_name, counter, ext))
        counter += 1
    shutil.copy2(path, target)
    return target


def list_backups(path):
    """Copies de secours du projet, de la plus ancienne à la plus récente."""
    _, stem, ext = _split(path)
    folder = backup_dir_for(path)
    if not os.path.isdir(folder):
        return []
    pattern = re.compile(
        r"^{}_(\d{{8}}_\d{{6}})(?:_(\d+))?{}$".format(
            re.escape(stem), re.escape(ext)))
    found = []
    for name in os.listdir(folder):
        match = pattern.match(name)
        if match:
            found.append((match.group(1), int(match.group(2) or 0), name))
    found.sort()
    return [os.path.join(folder, item[2]) for item in found]


def prune_backups(path, keep):
    """Supprime les plus anciennes copies au-delà de `keep`. Retourne la liste."""
    backups = list_backups(path)
    removed = []
    excess = len(backups) - max(1, keep)
    for old in backups[:max(0, excess)]:
        try:
            os.remove(old)
            removed.append(old)
        except OSError:
            pass
    return removed


@dataclass(frozen=True)
class BackupInfo:
    path: str
    when: datetime
    size: int
    label: str = ""   # texte affiché à la place de la date seule (ex. « Projet actuel »)


def describe_backups(path):
    """Copies de secours du projet avec date et taille, de la plus récente à la plus ancienne."""
    infos = []
    for item in list_backups(path):
        match = re.search(r"_(\d{8}_\d{6})(?:_\d+)?\.[^.]+$", os.path.basename(item))
        try:
            when = datetime.strptime(match.group(1), _STAMP_FORMAT)
            size = os.path.getsize(item)
        except (AttributeError, ValueError, OSError):
            continue
        infos.append(BackupInfo(item, when, size))
    infos.sort(key=lambda info: (info.when, info.path), reverse=True)
    return infos


def relative_age(when, now=None):
    """Âge lisible d'une date : « à l'instant », « il y a 5 min », « il y a 2 h »."""
    now = now or datetime.now()
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 60:
        return "à l'instant"
    minutes = seconds // 60
    if minutes < 60:
        return "il y a {} min".format(minutes)
    hours = minutes // 60
    if hours < 24:
        return "il y a {} h".format(hours)
    return "il y a {} j".format(hours // 24)


def human_size(size):
    """Taille lisible : 842 o, 12,4 Ko, 3,1 Mo."""
    value = float(size)
    for unit in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or unit == "Go":
            text = "{:.0f}".format(value) if unit == "o" else "{:.1f}".format(value)
            return "{} {}".format(text.replace(".", ","), unit)
        value /= 1024.0


def read_version(metadata_path):
    """Version déclarée dans metadata.txt, chaîne vide si illisible."""
    try:
        with open(metadata_path, encoding="utf-8") as handle:
            for line in handle:
                key, _, value = line.partition("=")
                if key.strip() == "version":
                    return value.strip()
    except OSError:
        pass
    return ""


def restore_copy(backup_path, project_path, now=None):
    """Duplique une copie de secours à côté du projet, sans rien écraser.

    Le fichier créé s'appelle `<projet>_restaure_<date><ext>`. Le projet
    actuel et la copie d'origine ne sont pas modifiés.
    """
    folder, stem, ext = _split(project_path)
    now = now or datetime.now()
    base = "{}{}_{}".format(stem, RESTORED_MARK, now.strftime(_STAMP_FORMAT))
    target = os.path.join(folder, base + ext)
    counter = 1
    while os.path.exists(target):
        target = os.path.join(folder, "{}_{}{}".format(base, counter, ext))
        counter += 1
    shutil.copy2(backup_path, target)
    return target


class BackupScheduler:
    """Décide quand faire une copie de secours.

    La première copie d'un fichier dans la session est toujours faite, avant
    tout écrasement. Les suivantes respectent un intervalle minimal.
    """

    def __init__(self):
        self._last = {}

    def due(self, path, now_seconds, min_interval_seconds):
        last = self._last.get(path)
        return last is None or (now_seconds - last) >= min_interval_seconds

    def mark(self, path, now_seconds):
        self._last[path] = now_seconds


# --------------------------------------------------------------------------
# Détection d'une fermeture inattendue (coupure de courant, plantage de QGIS)
# --------------------------------------------------------------------------
SESSIONS_DIR = os.path.join("AutoSaveData", "sessions")
HEARTBEAT_SECONDS = 30
# Une session dont le dernier signe de vie date de plus de ce délai est considérée comme morte
STALE_AFTER_SECONDS = 120


def pid_alive(pid):
    """True si un processus portant ce numéro existe encore."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            code = ctypes.c_ulong()
            ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            kernel.CloseHandle(handle)
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class SessionTracker:
    """Journal discret d'une session QGIS : un petit fichier par session ouverte.

    Le fichier est écrit au démarrage, mis à jour toutes les 30 s (signe de vie)
    et supprimé à la fermeture normale. S'il reste après coup, la session s'est
    terminée de façon inattendue : coupure de courant, plantage de QGIS.
    """

    def __init__(self, base_dir, pid=None, clock=time.time):
        self.dir = os.path.join(base_dir, SESSIONS_DIR)
        self.pid = os.getpid() if pid is None else pid
        self.clock = clock
        self.started = clock()
        self.path = os.path.join(
            self.dir, "{}_{}.json".format(self.pid, int(self.started)))
        self.project = ""
        self.last_save = None

    # -- écriture -------------------------------------------------------
    def _write(self):
        data = {"pid": self.pid, "started": self.started,
                "heartbeat": self.clock(), "project": self.project,
                "last_save": self.last_save}
        try:
            os.makedirs(self.dir, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            os.replace(tmp, self.path)
        except OSError:
            pass  # le journal est un confort : jamais un obstacle

    def start(self, project=""):
        self.project = project or ""
        self._write()

    def heartbeat(self):
        self._write()

    def set_project(self, project):
        self.project = project or ""
        self._write()

    def set_last_save(self, project=None):
        """Note un enregistrement ; `project` suit un changement de nom (Enregistrer sous)."""
        if project is not None:
            self.project = project or ""
        self.last_save = self.clock()
        self._write()

    def mark_clean(self):
        """Fermeture normale : la session ne sera pas signalée."""
        for path in (self.path, self.path + ".tmp"):
            try:
                os.remove(path)
            except OSError:
                pass

    # -- lecture --------------------------------------------------------
    def find_stale(self, alive=pid_alive):
        """Sessions terminées de façon inattendue, de la plus récente à la plus ancienne."""
        found = []
        if not os.path.isdir(self.dir):
            return found
        now = self.clock()
        for name in os.listdir(self.dir):
            path = os.path.join(self.dir, name)
            if not name.endswith(".json") or path == self.path:
                continue
            try:
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)
                pid = int(data.get("pid", 0))
                heartbeat = float(data.get("heartbeat", 0))
            except (OSError, ValueError, TypeError):
                self._discard_path(path)  # fichier illisible : inutile de le garder
                continue
            same_pid = pid == self.pid  # numéro réutilisé après un redémarrage
            running = alive(pid) and (now - heartbeat) <= STALE_AFTER_SECONDS
            if same_pid or not running:
                data["_path"] = path
                found.append(data)
        found.sort(key=lambda d: d.get("heartbeat", 0), reverse=True)
        return found

    def discard(self, info):
        self._discard_path(info.get("_path", ""))

    @staticmethod
    def _discard_path(path):
        try:
            os.remove(path)
        except OSError:
            pass
