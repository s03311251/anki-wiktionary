from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any, Callable, cast
from urllib.parse import unquote

import requests

try:
    from anki.utils import strip_html
except ImportError:
    from anki.utils import stripHTML as strip_html

from anki.notes import Note
from aqt import qtmajor
from aqt.main import AnkiQt
from aqt.operations import QueryOp
from aqt.qt import QDialog, QKeySequence, QPixmap, QWidget, qconnect
from aqt.utils import showWarning

from .. import consts
from ..fetcher import WiktionaryFetcher, WordNotFoundError

if TYPE_CHECKING or qtmajor > 5:
    from ..forms.main_qt6 import Ui_Dialog
else:
    from ..forms.main_qt5 import Ui_Dialog  # type: ignore


PROGRESS_LABEL = "Updated {count} out of {total} note(s)"


def get_available_dicts() -> list[str]:
    return [p.name for p in consts.USER_FILES.iterdir() if p.is_dir()]


class WiktionaryFetcherDialog(QDialog):
    def __init__(
        self,
        mw: AnkiQt,
        parent: QWidget,
        notes: list[Note],
    ):
        super().__init__(parent)
        self.form = Ui_Dialog()
        self.form.setupUi(self)
        self.mw = mw
        self.config = mw.addonManager.getConfig(__name__)
        self.notes = notes
        self.combos = [
            self.form.wordFieldComboBox,
            self.form.definitionFieldComboBox,
            self.form.exampleFieldComboBox,
            self.form.genderFieldComboBox,
            self.form.POSFieldComboBox,
            self.form.IPAFieldComboBox,
            self.form.audioFieldComboBox,
            self.form.etymologyFieldComboBox,
            self.form.declensionFieldComboBox,
        ]
        self.setWindowTitle(consts.ADDON_NAME)
        self.form.icon.setPixmap(
            QPixmap(os.path.join(consts.ICONS_DIR, "enwiktionary-1.5x.png"))
        )
        self.form.dictionaryComboBox.addItems(get_available_dicts())
        self.downloader: WiktionaryFetcher | None = None
        qconnect(self.form.addButton.clicked, self.on_add)
        self.form.addButton.setShortcut(QKeySequence("Ctrl+Return"))
        qconnect(self.finished, self.on_finished)

    def exec(self) -> int:
        if self._fill_fields():
            return super().exec()
        return QDialog.DialogCode.Rejected  # pylint: disable=no-member

    def _fill_fields(self) -> int:
        mids = set(note.mid for note in self.notes)
        if len(mids) > 1:
            showWarning(
                "Please select notes from only one notetype.",
                parent=self,
                title=consts.ADDON_NAME,
            )
            return 0
        self.field_names = ["None"] + self.notes[0].keys()
        for i, combo in enumerate(self.combos):
            combo.addItems(self.field_names)
            selected = 0
            if len(self.field_names) - 1 > i:
                selected = i + 1
            combo.setCurrentIndex(selected)
            qconnect(
                combo.currentIndexChanged,
                lambda field_index, combo_index=i: self.on_selected_field_changed(
                    combo_index, field_index
                ),
            )
        self.set_last_used_settings()
        return 1

    CONFIG_MODEL_FIELDS = (
        "word_field",
        "definition_field",
        "example_field",
        "gender_field",
        "part_of_speech_field",
        "ipa_field",
        "audio_field",
        "etymology_field",
        "declension_field",
    )

    def set_last_used_settings(self) -> None:
        dictionary = self.config["dictionary_field"].lower()
        for i in range(self.form.dictionaryComboBox.count()):
            text = self.form.dictionaryComboBox.itemText(i)
            if text.lower() == dictionary:
                self.form.dictionaryComboBox.setCurrentIndex(i)
                break
        for i, field_opt in enumerate(self.CONFIG_MODEL_FIELDS):
            field_name = self.config[field_opt].lower()
            combo = self.combos[i]
            for i in range(combo.count()):
                text = combo.itemText(i)
                if text.lower() == field_name:
                    combo.setCurrentIndex(i)
                    break

    def save_settings(self) -> None:
        self.config["dictionary_field"] = self.form.dictionaryComboBox.currentText()
        for i, field_opt in enumerate(self.CONFIG_MODEL_FIELDS):
            self.config[field_opt] = self.combos[i].currentText()
        self.mw.addonManager.writeConfig(__name__, self.config)

    def on_finished(self, result: int) -> None:
        self.save_settings()

    def on_selected_field_changed(self, combo_index: int, field_index: int) -> None:
        if field_index == 0:
            return
        for i, combo in enumerate(self.combos):
            if i != combo_index and combo.currentIndex() == field_index:
                combo.setCurrentIndex(0)

    def on_add(self) -> None:
        if self.form.wordFieldComboBox.currentIndex() == 0:
            showWarning("No word field selected.", parent=self, title=consts.ADDON_NAME)
            return
        if self.form.dictionaryComboBox.currentIndex() == -1:
            showWarning(
                "No dictionary is available. Please use <b>Tools > Wiktionary > Import a dictionary</b>.",
                textFormat="rich",
            )
            return
        dictionary = self.form.dictionaryComboBox.currentText()
        self.downloader = WiktionaryFetcher(dictionary)
        word_field = self.form.wordFieldComboBox.currentText()
        definition_field_i = self.form.definitionFieldComboBox.currentIndex()
        example_field_i = self.form.exampleFieldComboBox.currentIndex()
        gender_field_i = self.form.genderFieldComboBox.currentIndex()
        pos_field_i = self.form.POSFieldComboBox.currentIndex()
        ipa_field_i = self.form.IPAFieldComboBox.currentIndex()
        audio_field_i = self.form.audioFieldComboBox.currentIndex()
        etymology_field_i = self.form.etymologyFieldComboBox.currentIndex()
        declension_field_i = self.form.declensionFieldComboBox.currentIndex()
        field_tuples = (
            (definition_field_i, self._get_definitions),
            (example_field_i, self._get_examples),
            (gender_field_i, self._get_gender),
            (pos_field_i, self._get_part_of_speech),
            (ipa_field_i, self._get_ipa),
            (audio_field_i, self._get_audio),
            (etymology_field_i, self._get_etymology),
            (declension_field_i, self._get_declension),
        )

        def on_success(ret: Any) -> None:
            self.accept()

        def on_failure(exc: Exception) -> None:
            self.mw.progress.finish()
            showWarning(str(exc), parent=self, title=consts.ADDON_NAME)
            self.accept()

        op = QueryOp(
            parent=self,
            op=lambda col: self._fill_notes(
                word_field,
                field_tuples,
            ),
            success=on_success,
        )
        op.failure(on_failure)
        op.run_in_background()
        self.mw.progress.start(
            max=len(self.notes),
            label=PROGRESS_LABEL.format(count=0, total=len(self.notes)),
            parent=self,
            immediate=True,
        )
        self.mw.progress.set_title(consts.ADDON_NAME)

    def _fill_notes(
        self,
        word_field: str,
        field_tuples: tuple[tuple[int, Callable[[str], str]], ...],
    ) -> None:
        want_cancel = False
        last_progress = 0.0
        self.errors = []
        self.updated_notes: list[Note] = []

        def on_progress() -> None:
            nonlocal want_cancel
            want_cancel = self.mw.progress.want_cancel()
            self.mw.progress.update(
                label=PROGRESS_LABEL.format(
                    count=len(self.updated_notes), total=len(self.notes)
                ),
                value=len(self.updated_notes),
                max=len(self.notes),
            )

        for note in self.notes:
            word = strip_html(note[word_field]).strip()
            if not word:
                continue
            need_updating = False
            try:
                for field_tuple in field_tuples:
                    if not field_tuple[0]:
                        continue
                    contents = field_tuple[1](word)
                    note[self.field_names[field_tuple[0]]] = contents
                    need_updating = True
            except WordNotFoundError as exc:
                self.errors.append(str(exc))
            finally:
                if need_updating:
                    self.updated_notes.append(note)
                if time.time() - last_progress >= 0.01:
                    self.mw.taskman.run_on_main(on_progress)
                    last_progress = time.time()
            if want_cancel:
                break
        self.mw.taskman.run_on_main(self.mw.progress.finish)

    def _get_definitions(self, word: str) -> str:
        downloader = cast(WiktionaryFetcher, self.downloader)
        defs = downloader.get_senses(word)
        if len(defs) == 0:
            return ""
        if len(defs) == 1:
            return defs[0]
        formatted = "<ul>"
        for definition in defs:
            formatted += f"<li>{definition}</li>"
        formatted += "</ul>"
        return formatted

    def _get_examples(self, word: str) -> str:
        downloader = cast(WiktionaryFetcher, self.downloader)
        examples = downloader.get_examples(word)
        if len(examples) == 0:
            return ""
        if len(examples) == 1:
            return examples[0]
        formatted = "<ul>"
        for definition in examples:
            formatted += f"<li>{definition}</li>"
        formatted += "</ul>"
        return formatted

    def _get_gender(self, word: str) -> str:
        downloader = cast(WiktionaryFetcher, self.downloader)
        return downloader.get_gender(word)

    def _get_part_of_speech(self, word: str) -> str:
        downloader = cast(WiktionaryFetcher, self.downloader)
        return downloader.get_part_of_speech(word)

    def _get_ipa(self, word: str) -> str:
        downloader = cast(WiktionaryFetcher, self.downloader)
        return downloader.get_ipa(word)

    def _get_audio(self, word: str) -> str:
        downloader = cast(WiktionaryFetcher, self.downloader)
        url = downloader.get_audio_url(word)

        http_session = requests.Session()
        # https://meta.wikimedia.org/wiki/User-Agent_policy
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Anki Wiktionary add-on, https://github.com/s03311251/anki-wiktionary)"
        }
        try:
            with http_session.get(url, headers=headers, timeout=30) as response:
                response.raise_for_status()
                data = response.content
        except Exception:
            return ""
        filename = self.mw.col.media.write_data(unquote(os.path.basename(url)), data)
        return "[sound:" + filename + "]"

    def _get_etymology(self, word: str) -> str:
        downloader = cast(WiktionaryFetcher, self.downloader)
        return downloader.get_etymology(word)

    def _get_declension(self, word: str) -> str:
        downloader = cast(WiktionaryFetcher, self.downloader)
        declensions = downloader.get_declension(word)
        if len(declensions) == 0:
            return ""
        formatted = "<ul>"
        for key, value in declensions.items():
            formatted += f"<li>{key}: {', '.join(value)}</li>"
        formatted += "</ul>"
        return formatted
