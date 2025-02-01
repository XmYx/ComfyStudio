import json
import os


class LocalizationManager:
    def __init__(self, settings_manager, locales_dir=None):
        self.settings_manager = settings_manager
        default_locales_dir = os.path.join(os.path.dirname(__file__), "..", "locales")
        self.locales_dir = os.path.abspath(locales_dir or default_locales_dir)
        self.translations = {}
        self.current_language = "en"
        self.load_language(self.settings_manager.get("language", "en"))

    def load_language(self, language_code):
        language_file = os.path.join(self.locales_dir, f"{language_code}.json")
        if not os.path.exists(language_file):
            print(f"Localization file for '{language_code}' not found. Falling back to 'en'.")
            language_file = os.path.join(self.locales_dir, "en.json")
            language_code = "en"

        with open(language_file, "r", encoding="utf-8") as f:
            self.translations = json.load(f)

        self.current_language = language_code

    def translate(self, key, **kwargs):
        default = kwargs.get("default", key)
        text = self.translations.get(key, default)  # Fallback to key if not found
        if kwargs:
            try:
                text = text.format(**kwargs)
            except KeyError as e:
                print(f"Missing placeholder in translation for key '{key}': {e}")
        return text

    def set_language(self, language_code):
        self.load_language(language_code)
        self.settings_manager.set("language", language_code)

    def get_available_languages(self):
        languages = []
        for filename in os.listdir(self.locales_dir):
            if filename.endswith(".json"):
                lang_code = filename[:-5]  # Remove '.json'
                languages.append(lang_code)
        return languages
    def get_language(self):
        """
        Retrieve the currently set language code.
        """
        return self.current_language
