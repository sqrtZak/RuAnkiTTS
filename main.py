#!/usr/bin/env python3
import sys
import os
import csv
import io
import json
import tempfile
import shutil
import time
import re
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QLineEdit,
                             QFileDialog, QProgressBar, QTextEdit, QPlainTextEdit, 
                             QMessageBox)
from PyQt6.QtCore import QThread, pyqtSignal, QSettings
from gtts import gTTS
import genanki

# Try to import pymorphy3 for advanced Russian morphology (Yo-fication)
try:
    import pymorphy3
    MORPH = pymorphy3.MorphAnalyzer()
except ImportError:
    MORPH = None

def strip_stress_marks(text):
    return text.replace('\u0301', '')

def restore_yo(text):
    """
    Uses pymorphy3's complete Russian dictionary to automatically restore 'ё' 
    in words where it was written as 'е', while preserving original capitalization.
    """
    if not MORPH:
        return text 

    def replace_match(match):
        word = match.group(0)
        if 'ё' in word.lower():
            return word
            
        parses = MORPH.parse(word)
        if not parses:
            return word
            
        best_parse = parses[0]
        dict_word = best_parse.word 
        
        if 'ё' in dict_word and len(word) == len(dict_word):
            new_word = ""
            for orig_char, dict_char in zip(word, dict_word):
                if dict_char == 'ё':
                    new_word += 'Ё' if orig_char.isupper() else 'ё'
                else:
                    new_word += orig_char
            return new_word
            
        return word

    return re.sub(r'[а-яА-ЯёЁ]+', replace_match, text)

# --- CUSTOM PACKAGE FOR AUTO-SUSPENDING ---
class SuspendedPackage(genanki.Package):
    """
    Subclasses genanki.Package to intercept the database creation.
    Sets the 'queue' column to -1 (Suspended) ONLY for Sentence cards.
    """
    def write_to_db(self, cursor, *args, **kwargs):
        # Pass whatever arguments genanki wants to the original function
        super().write_to_db(cursor, *args, **kwargs)
        
        # Suspend ONLY cards that belong to the Sentence model (Model ID: 1607392002)
        cursor.execute("""
            UPDATE cards 
            SET queue = -1 
            WHERE nid IN (
                SELECT id FROM notes WHERE mid = 1607392002
            )
        """)
    """
    Subclasses genanki.Package to intercept the database creation.
    Sets the 'queue' column to -1, which is Anki's internal code for 'Suspended'.
    """
    def write_to_db(self, cursor, *args, **kwargs):
        # Pass whatever arguments genanki wants to the original function
        super().write_to_db(cursor, *args, **kwargs)
        # Then suspend all cards generated in this package
        cursor.execute("UPDATE cards SET queue = -1")


class DeckGeneratorThread(QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, json_text, deck_name, output_path):
        super().__init__()
        self.json_text = json_text
        self.deck_name = deck_name
        self.output_path = output_path

    def run(self):
        try:
            if not MORPH:
                self.log.emit("WARNING: pymorphy3 not installed. 'ё' restoration will be skipped. Run: pip install pymorphy3")

            self.log.emit(f"Starting processing for deck: {self.deck_name}")
            
            raw_text = self.json_text.strip()
            vocab_items = []
            sentence_items = []

            # Parse JSON
            try:
                data = json.loads(raw_text)
                self.log.emit("Successfully parsed JSON data.")
                
                vocab_items = data.get("vocabulary", [])
                sentence_items = data.get("sentences", [])
                            
            except json.JSONDecodeError:
                self.finished.emit(False, "Invalid JSON format. Please provide the exact JSON structure.")
                return
            
            total_rows = len(vocab_items) + len(sentence_items)
            if total_rows == 0:
                self.finished.emit(False, "No vocabulary or sentences found in the JSON.")
                return

            # --- SETUP CUSTOM ANKI MODELS ---
            
            # 1. Vocabulary Model
            vocab_model = genanki.Model(
                1607392001, # Unique Model ID
                'Russian Custom Vocab',
                fields=[
                    {'name': 'Vocab_ID'},
                    {'name': 'Translation'},
                    {'name': 'Target_Word'},
                    {'name': 'Audio'},
                ],
                templates=[{
                    'name': 'Vocab Card',
                    'qfmt': '<div style="text-align:center; font-size:24px;">{{Translation}}</div>',
                    'afmt': '<div style="text-align:center; font-size:24px;">{{Translation}}</div><hr id="answer"><div style="text-align:center; font-size:28px; color: #2c3e50;">{{Target_Word}}</div><br>{{Audio}}<br><div style="font-size:10px; color:gray;">ID: {{Vocab_ID}}</div>',
                }]
            )

            # 2. Sentence Model
            sentence_model = genanki.Model(
                1607392002, # Unique Model ID
                'Russian Custom Sentence',
                fields=[
                    {'name': 'Sentence_ID'},
                    {'name': 'Translation'},
                    {'name': 'Target_Sentence'},
                    {'name': 'Dependencies'},
                    {'name': 'Difficulty'},
                    {'name': 'Audio'},
                ],
                templates=[{
                    'name': 'Sentence Card',
                    'qfmt': '<div style="text-align:center; font-size:24px;">{{Translation}}</div>',
                    'afmt': '<div style="text-align:center; font-size:24px;">{{Translation}}</div><hr id="answer"><div style="text-align:center; font-size:28px; color: #2c3e50;">{{Target_Sentence}}</div><br>{{Audio}}<br><div style="font-size:14px; color:#7f8c8d;">Dependencies: {{Dependencies}}</div><div style="font-size:14px; color:#7f8c8d;">Difficulty: {{Difficulty}}</div><br><div style="font-size:10px; color:gray;">ID: {{Sentence_ID}}</div>',
                }]
            )

            # Setup Anki Deck
            deck_id = abs(hash(self.deck_name)) % (10 ** 10)
            my_deck = genanki.Deck(deck_id, self.deck_name)

            media_files = []
            temp_dir = tempfile.mkdtemp()
            processed_count = 0

            # --- HELPER FUNCTION FOR TTS ---
            def generate_audio(text, filename):
                clean_text = re.sub(r'\[.*?\]', '', text)
                clean_text = strip_stress_marks(clean_text)
                clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                
                if not clean_text:
                    return ""
                    
                audio_path = os.path.join(temp_dir, filename)
                tlds_to_try = ['com', 'ru', 'co.uk', 'us']
                
                for tld in tlds_to_try:
                    try:
                        tts = gTTS(clean_text, lang='ru', tld=tld)
                        tts.save(audio_path)
                        media_files.append(audio_path)
                        time.sleep(0.5)
                        return f"[sound:{filename}]"
                    except Exception:
                        time.sleep(1)
                
                self.log.emit(f"WARNING: TTS failed for '{clean_text}'.")
                return ""

            # --- PROCESS VOCABULARY ---
            for item in vocab_items:
                v_id = item.get("Vocab_ID", "")
                eng = item.get("Translation", "")
                
                # Restore Ё for both the visual card and the TTS engine
                rus = restore_yo(item.get("Target_Word", ""))
                
                audio_filename = f"ru_audio_{v_id}.mp3"
                audio_field = generate_audio(rus, audio_filename)

                # Create Note using the ID as the GUID
                note = genanki.Note(
                    model=vocab_model,
                    fields=[v_id, eng, rus, audio_field],
                    guid=genanki.guid_for(v_id) # <--- Maps JSON ID to Anki GUID
                )
                my_deck.add_note(note)
                
                processed_count += 1
                self.progress.emit(int((processed_count / total_rows) * 100))
                self.log.emit(f"Vocab Added: {v_id} | {rus}")

            # --- PROCESS SENTENCES ---
            for item in sentence_items:
                s_id = item.get("Sentence_ID", "")
                eng = item.get("Translation", "")
                deps = item.get("Dependencies", "")
                diff = item.get("Difficulty", "")
                
                # Restore Ё for both the visual card and the TTS engine
                rus = restore_yo(item.get("Target_Sentence", ""))
                
                audio_filename = f"ru_audio_{s_id}.mp3"
                audio_field = generate_audio(rus, audio_filename)

                # Create Note using the ID as the GUID
                note = genanki.Note(
                    model=sentence_model,
                    fields=[s_id, eng, rus, deps, diff, audio_field],
                    guid=genanki.guid_for(s_id) # <--- Maps JSON ID to Anki GUID
                )
                my_deck.add_note(note)
                
                processed_count += 1
                self.progress.emit(int((processed_count / total_rows) * 100))
                self.log.emit(f"Sentence Added: {s_id} | {rus}")

            # Package the deck using our Custom SuspendedPackage
            self.log.emit("Packaging Anki deck and auto-suspending cards...")
            my_package = SuspendedPackage(my_deck)
            my_package.media_files = media_files
            my_package.write_to_file(self.output_path)
            
            shutil.rmtree(temp_dir)
            self.finished.emit(True, f"Successfully created deck at:\n{self.output_path}\n\nAll cards have been auto-suspended.")

        except Exception as e:
            self.finished.emit(False, f"An unexpected error occurred: {str(e)}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Anki Russian TTS Generator (JSON Edition)")
        self.resize(750, 650)
        self.settings = QSettings("MyCompany", "AnkiRuTTS")
        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        deck_layout = QHBoxLayout()
        lbl_deck = QLabel("Deck Name:")
        self.txt_deck_name = QLineEdit("Russian_Master_Deck")
        deck_layout.addWidget(lbl_deck)
        deck_layout.addWidget(self.txt_deck_name)
        layout.addLayout(deck_layout)

        input_header_layout = QHBoxLayout()
        lbl_instructions = QLabel("Paste raw JSON data below OR load from a file:")
        self.btn_browse = QPushButton("Load JSON File")
        self.btn_browse.clicked.connect(self.browse_file)
        input_header_layout.addWidget(lbl_instructions)
        input_header_layout.addStretch()
        input_header_layout.addWidget(self.btn_browse)
        layout.addLayout(input_header_layout)

        self.txt_csv_data = QPlainTextEdit()
        self.txt_csv_data.setPlaceholderText('Paste your {"vocabulary": [...], "sentences": [...]} JSON here...')
        layout.addWidget(self.txt_csv_data)

        self.btn_generate = QPushButton("Generate Auto-Suspended Anki Deck")
        self.btn_generate.clicked.connect(self.start_generation)
        self.btn_generate.setStyleSheet("font-weight: bold; padding: 10px; background-color: #2ecc71; color: white;")
        layout.addWidget(self.btn_generate)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setPlaceholderText("Logs will appear here...")
        layout.addWidget(self.log_console)

    def browse_file(self):
        last_dir = self.settings.value("last_load_dir", os.path.expanduser("~"))
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select JSON File", last_dir, "JSON Files (*.json);;Text Files (*.txt);;All Files (*)"
        )
        if file_path:
            self.settings.setValue("last_load_dir", os.path.dirname(file_path))
            try:
                with open(file_path, 'r', encoding='utf-8-sig') as f:
                    content = f.read()
                self.txt_csv_data.setPlainText(content)
                base_name = os.path.splitext(os.path.basename(file_path))[0]
                self.txt_deck_name.setText(base_name)
                self.append_log(f"Loaded data from: {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to read file:\n{str(e)}")

    def start_generation(self):
        json_text = self.txt_csv_data.toPlainText().strip()
        deck_name = self.txt_deck_name.text().strip()

        if not json_text:
            QMessageBox.warning(self, "Error", "Please paste some JSON data or load a file.")
            return
        if not deck_name:
            QMessageBox.warning(self, "Error", "Please enter a Deck Name.")
            return

        last_save_dir = self.settings.value("last_save_dir", os.path.expanduser("~"))
        default_save_path = os.path.join(last_save_dir, f"{deck_name}.apkg")

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save Anki Deck", default_save_path, "Anki Package (*.apkg)"
        )

        if not save_path:
            return 
            
        self.settings.setValue("last_save_dir", os.path.dirname(save_path))

        self.btn_browse.setEnabled(False)
        self.btn_generate.setEnabled(False)
        self.txt_csv_data.setEnabled(False)
        self.txt_deck_name.setEnabled(False)
        self.progress_bar.setValue(0)
        self.log_console.clear()

        self.thread = DeckGeneratorThread(json_text, deck_name, save_path)
        self.thread.progress.connect(self.update_progress)
        self.thread.log.connect(self.append_log)
        self.thread.finished.connect(self.generation_finished)
        self.thread.start()

    def update_progress(self, value):
        self.progress_bar.setValue(value)

    def append_log(self, message):
        self.log_console.append(message)
        scrollbar = self.log_console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def generation_finished(self, success, message):
        self.btn_browse.setEnabled(True)
        self.btn_generate.setEnabled(True)
        self.txt_csv_data.setEnabled(True)
        self.txt_deck_name.setEnabled(True)
        
        if success:
            self.append_log("\n--- DONE ---")
            self.append_log(message)
            QMessageBox.information(self, "Success", message)
        else:
            self.append_log("\n--- ERROR ---")
            self.append_log(message)
            QMessageBox.critical(self, "Error", message)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
