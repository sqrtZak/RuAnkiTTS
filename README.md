# RuAnkiTTS (JSON to .apkg Generator)

A Python automation tool that bridges the gap between AI language generation and spaced-repetition study. It ingests strict, AI-generated JSON schemas containing vocabulary and target sentences, attaches automated Text-to-Speech (TTS) audio, and packages everything directly into a ready-to-use `.apkg` file. 

### Why use this?
Manually creating audio-backed flashcards is incredibly tedious. This tool automates the process, allowing you to use LLMs to generate your target language content (e.g., Russian), and instantly compile it into Anki.

### Ecosystem
This script outputs a custom note type designed specifically to work with my [Anki Dependency Resolver (Github)](https://github.com/sqrtZak/VocabularySentenceDependencyResolver) [Anki Dependency Resolver (Anki Add-On Store)](https://ankiweb.net/shared/info/561009161). The generated cards map vocabulary to complex sentences, allowing the add-on to algorithmically unlock sentences only *after* you've learned the root words.
