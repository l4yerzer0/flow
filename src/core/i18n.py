import locale
import ctypes
import os

class I18n:
    TRANSLATIONS = {
        "ru": {
            "dashboard": "Дашборд",
            "accounts": "Аккаунты",
            "settings": "Настройки",
            "total_pnl": "ОБЩИЙ PnL",
            "active_bots": "АКТИВНЫЕ БОТЫ",
            "live_bots": "Запущенные боты",
            "global_feed": "Лента событий",
            "account": "Аккаунт",
            "status": "Статус",
            "pnl_unrealized": "PnL (Нереализ.)",
            "positions": "Позиции",
            "configured_accounts": "Настроенные аккаунты",
            "name": "Имя",
            "target_size": "Целевой объем",
            "add_account": "Добавить аккаунт",
            "remove_selected": "Удалить выбранный",
            "save": "Сохранить",
            "system_init": "[bold green]Система инициализирована.[/] Аккаунты загружены.",
            "scanning": "Сканирование рынка...",
            "opened": "✔ Позиции ОТКРЫТЫ. Хеджирование...",
            "closing": "Закрытие позиций...",
            "idle": "Позиции закрыты. ОЖИДАНИЕ.",
            "placeholder_cmd": "Введите команду...",
            "refresh_rate": "Частота обновления (мс)",
            "lang_detected": "Определен язык: {lang}"
        },
        "en": {
            "dashboard": "Dashboard",
            "accounts": "Accounts",
            "settings": "Settings",
            "total_pnl": "TOTAL PnL",
            "active_bots": "ACTIVE BOTS",
            "live_bots": "Live Bots",
            "global_feed": "Global Feed",
            "account": "Account",
            "status": "Status",
            "pnl_unrealized": "PnL (Unrealized)",
            "positions": "Positions",
            "configured_accounts": "Configured Accounts",
            "name": "Name",
            "target_size": "Target Size",
            "add_account": "Add Account",
            "remove_selected": "Remove Selected",
            "save": "Save",
            "system_init": "[bold green]System Initialized.[/] Loaded accounts.",
            "scanning": "Scanning market...",
            "opened": "✔ Positions OPENED. Hedging...",
            "closing": "Closing positions...",
            "idle": "Positions closed. IDLE.",
            "placeholder_cmd": "Type a command...",
            "refresh_rate": "Refresh Rate (ms)",
            "lang_detected": "Detected language: {lang}"
        }
    }

    def __init__(self):
        self.lang = self._get_system_lang()
        self.strings = self.TRANSLATIONS.get(self.lang, self.TRANSLATIONS["en"])

    def _get_system_lang(self) -> str:
        try:
            # For Windows
            windll = ctypes.windll.kernel32
            lang_id = windll.GetUserDefaultUILanguage()
            lang = locale.windows_locale.get(lang_id, "en_US")
            return "ru" if "Russian" in lang or "ru" in lang.lower() else "en"
        except:
            # Fallback for other OS or errors
            lang = locale.getdefaultlocale()[0]
            if lang and lang.startswith("ru"):
                return "ru"
        return "en"

    def t(self, key: str, **kwargs) -> str:
        text = self.strings.get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text

# Global instance
i18n = I18n()
