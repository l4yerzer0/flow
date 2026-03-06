from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Container, Vertical, Horizontal, ScrollableContainer
from textual.widgets import Header, Footer, Static, Input, RichLog, TabbedContent, TabPane, DataTable, Label, Button, Select
from textual.screen import ModalScreen, Screen
from textual.command import CommandPalette
from src.core.config import AccountConfig, ExchangeConfig, SettingsProfile, StrategySettings, StrategySettingsOverride
from src.core.bot_manager import BotManager, StrategyState, create_exchange
from src.core.credentials import has_master_password, initialize_master_password
from src.core.i18n import i18n
from decimal import Decimal
from typing import Iterable
import asyncio
import os
from datetime import datetime

from src.ui.env import is_mobile

# --- Reusable UI ---

def ui_t(key: str) -> str:
    ru = {
        "master_pwd_create_title": "Создайте мастер-пароль",
        "master_pwd_enter_title": "Введите мастер-пароль",
        "master_pwd_label": "Пароль",
        "master_pwd_confirm_label": "Повторите пароль",
        "master_pwd_create_btn": "Создать",
        "master_pwd_unlock_btn": "Разблокировать",
        "master_pwd_short": "Пароль должен быть минимум 6 символов",
        "master_pwd_mismatch": "Пароли не совпадают",
        "master_pwd_invalid": "Неверный мастер-пароль",
        "master_pwd_unlock_failed": "Не удалось разблокировать конфиг: {error}",
        "unlock_first": "Сначала разблокируйте приложение",
    }
    en = {
        "master_pwd_create_title": "Create Master Password",
        "master_pwd_enter_title": "Enter Master Password",
        "master_pwd_label": "Password",
        "master_pwd_confirm_label": "Confirm Password",
        "master_pwd_create_btn": "Create",
        "master_pwd_unlock_btn": "Unlock",
        "master_pwd_short": "Password must be at least 6 characters",
        "master_pwd_mismatch": "Passwords do not match",
        "master_pwd_invalid": "Invalid master password",
        "master_pwd_unlock_failed": "Failed to unlock config: {error}",
        "unlock_first": "Unlock the app first",
    }
    table = ru if i18n.lang == "ru" else en
    extra_ru = {
        "profiles_tab": "Профили",
        "settings_profiles": "Профили настроек",
        "add_profile": "Добавить профиль",
        "edit_profile": "Редактировать профиль",
        "remove_profile": "Удалить профиль",
        "profile_col": "Профиль",
        "profile_id_col": "ID",
        "profile_name_col": "Имя",
        "symbol_col": "Символ",
        "spread_col": "Спред (bps)",
        "rebalance_col": "Ребаланс (с)",
        "profile_add_title": "Добавить профиль",
        "profile_edit_title": "Редактировать профиль",
        "profile_id_label": "Profile ID",
        "profile_name_label": "Profile Name",
        "max_spread_label": "Max Spread (bps)",
        "rebalance_label": "Rebalance Interval (sec)",
        "invalid_profile_numbers": "Неверное числовое значение в профиле",
        "profile_id_required": "Profile ID is required",
        "profile_name_required": "Profile name is required",
        "profile_added": "Профиль '{name}' добавлен",
        "profile_updated": "Профиль '{name}' обновлен",
        "profile_removed": "Профиль '{name}' удален",
        "settings_profile_label": "Профиль настроек",
        "select_profile_prompt": "Выберите профиль",
        "target_override_label": "Переопределение объема (USD)",
        "target_override_placeholder": "Пусто = значение из профиля",
        "select_profile_required": "Выберите профиль настроек",
    }
    extra_en = {
        "profiles_tab": "Profiles",
        "settings_profiles": "Settings Profiles",
        "add_profile": "Add Profile",
        "edit_profile": "Edit Profile",
        "remove_profile": "Remove Profile",
        "profile_col": "Profile",
        "profile_id_col": "ID",
        "profile_name_col": "Name",
        "symbol_col": "Symbol",
        "spread_col": "Spread(bps)",
        "rebalance_col": "Rebalance(s)",
        "profile_add_title": "Add Profile",
        "profile_edit_title": "Edit Profile",
        "profile_id_label": "Profile ID",
        "profile_name_label": "Profile Name",
        "max_spread_label": "Max Spread (bps)",
        "rebalance_label": "Rebalance Interval (sec)",
        "invalid_profile_numbers": "Invalid numeric value in profile fields",
        "profile_id_required": "Profile ID is required",
        "profile_name_required": "Profile name is required",
        "profile_added": "Profile '{name}' added",
        "profile_updated": "Profile '{name}' updated",
        "profile_removed": "Profile '{name}' removed",
        "settings_profile_label": "Settings Profile",
        "select_profile_prompt": "Select profile",
        "target_override_label": "Target Size Override (USD)",
        "target_override_placeholder": "Leave blank to use profile value",
        "select_profile_required": "Select a settings profile",
    }
    extra = extra_ru if i18n.lang == "ru" else extra_en
    return extra.get(key, table.get(key, key))


class StatusPill(Static):
    DEFAULT_CSS = """
    StatusPill {
        width: 1fr;
        height: 4;
        padding: 0 1;
        margin: 0 1 0 0;
        background: #1f2937;
        color: $text;
        border: round #334155;
    }
    """
    def __init__(self, label: str, value: str = "--", **kwargs):
        super().__init__(**kwargs)
        self.label = label
        self.value = value

    def render(self):
        return f"[dim]{self.label}[/]\n{self.value}"

    def update_value(self, label, value, style=None):
        if style:
            self.value = f"[{style}]{value}[/]"
        else:
            self.value = str(value)
        self.label = label
        self.refresh()

class DashboardTab(Vertical):
    """Main view showing all accounts at a glance."""
    def compose(self) -> ComposeResult:
        container_type = Vertical if is_mobile() else Horizontal
        with container_type(id="stats-row"):
            yield StatusPill(i18n.t("total_pnl"), id="stat-pnl")
            yield StatusPill(i18n.t("active_bots"), id="stat-bots")
        
        yield Label(i18n.t("live_bots"), classes="section-title")
        yield DataTable(id="bots-table")
        
        yield Label(i18n.t("global_feed"), classes="section-title")
        yield RichLog(id="feed-log", markup=True, wrap=True)

class AccountsTab(ScrollableContainer):
    """Manage Accounts (List + Add/Remove)."""
    def compose(self) -> ComposeResult:
        yield Label(i18n.t("configured_accounts"), classes="section-title")
        yield DataTable(id="accounts-config-table")
        
        with Horizontal(classes="controls"):
            yield Button(i18n.t("add_account"), variant="primary", id="btn-add-account")
            yield Button(i18n.t("edit_selected"), variant="default", id="btn-edit-account", disabled=True)
            yield Button(i18n.t("remove_selected"), variant="error", id="btn-remove-account", disabled=True)

class StatisticsTab(ScrollableContainer):
    """View detailed statistics for all accounts."""
    def compose(self) -> ComposeResult:
        yield Label(i18n.t("statistics"), classes="section-title")
        
        container_type = Vertical if is_mobile() else Horizontal
        with container_type(id="stats-summary"):
            yield StatusPill(i18n.t("volume_24h"), id="stat-volume")
            yield StatusPill(i18n.t("trades"), id="stat-trades")
            yield StatusPill(i18n.t("funding_total"), id="stat-funding")
            
        yield Label(i18n.t("history"), classes="section-title")
        yield DataTable(id="stats-table")

class SettingsTab(ScrollableContainer):
    """Global Settings."""
    def compose(self) -> ComposeResult:
        yield Label(i18n.t("settings"), classes="section-title")
        with Vertical(classes="settings-card"):
            yield Label(i18n.t("refresh_rate"), classes="field-label")
            yield Input(placeholder="1000", value="1000", id="setting-refresh")
            yield Button(i18n.t("save"), variant="primary", id="btn-save-settings")

    def on_mount(self) -> None:
        if is_mobile():
            self.query_one("#setting-refresh", Input).focus()


class ProfilesTab(ScrollableContainer):
    """Manage strategy profiles."""
    def compose(self) -> ComposeResult:
        yield Label(ui_t("settings_profiles"), classes="section-title")
        yield DataTable(id="profiles-table")
        with Horizontal(classes="controls"):
            yield Button(ui_t("add_profile"), id="btn-add-profile", variant="primary")
            yield Button(ui_t("edit_profile"), id="btn-edit-profile", disabled=True)
            yield Button(ui_t("remove_profile"), id="btn-remove-profile", variant="error", disabled=True)


class ExchangeConfigForm(Vertical):
    """Sub-form for a single exchange configuration."""
    def __init__(self, label: str, id_prefix: str, initial_config: ExchangeConfig | None = None, **kwargs):
        super().__init__(id=id_prefix, **kwargs)
        self.label_text = label
        self.id_prefix = id_prefix
        self.initial_config = initial_config

    def compose(self) -> ComposeResult:
        with Horizontal(classes="dex-header-row"):
            yield Label(self.label_text, classes="dex-header")
            yield Label("", id=f"{self.id_prefix}-status", classes="status-label")
        yield Select(
            [("Pacifica", "pacifica"), ("Variational", "variational"), ("Mock", "mock")],
            prompt=i18n.t("exchange_type"),
            id=f"{self.id_prefix}-type"
        )
        yield Vertical(id=f"{self.id_prefix}-fields")
        yield Label("", id=f"{self.id_prefix}-error", classes="error-label")

    def on_mount(self) -> None:
        if self.initial_config:
            allowed_types = ["pacifica", "variational", "mock"]
            ex_type = self.initial_config.exchange_type
            if ex_type in allowed_types:
                select = self.query_one(f"#{self.id_prefix}-type", Select)
                select.value = ex_type
            
            if self.initial_config.last_error:
                self.set_error(self.initial_config.last_error)
            elif self.initial_config.params:
                self.set_status("OK", "green")

    def set_status(self, text: str, color: str = "white"):
        label = self.query_one(f"#{self.id_prefix}-status", Label)
        label.update(f"[{color}]{text}[/]")

    def set_error(self, text: str):
        err_label = self.query_one(f"#{self.id_prefix}-error", Label)
        err_label.update(f"[red]{text}[/]")
        self.set_status("ERROR", "red")

    def clear_status(self):
        self.query_one(f"#{self.id_prefix}-status", Label).update("")
        self.query_one(f"#{self.id_prefix}-error", Label).update("")

    async def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == f"{self.id_prefix}-type":
            await self.update_fields(event.value)

    async def update_fields(self, ex_type: str):
        container = self.query_one(f"#{self.id_prefix}-fields", Vertical)
        await container.query("*").remove()
        
        fields = {
            "pacifica": ["public_key", "private_key"],
            "variational": ["public_key", "private_key"],
            "mock": []
        }.get(ex_type, [])
        
        for field in fields:
            is_password = "key" in field or "secret" in field or "private" in field
            val = ""
            if self.initial_config and self.initial_config.exchange_type == ex_type:
                # Map old params or new ones
                val = self.initial_config.params.get(field, "")
                if not val:
                    # Fallback for old keys
                    if field == "public_key": val = self.initial_config.params.get("api_key", "")
                    if field == "private_key": val = self.initial_config.params.get("api_secret", "") or self.initial_config.params.get("wallet_private_key", "")
            
            await container.mount(Input(
                placeholder=i18n.t(field), 
                id=f"{self.id_prefix}-{field}", 
                password=is_password,
                value=val
            ))

    def get_config(self) -> ExchangeConfig | None:
        select = self.query_one(f"#{self.id_prefix}-type", Select)
        ex_type = select.value
        
        if not isinstance(ex_type, str) or ex_type == "":
            return None
            
        params = {}
        fields = {
            "pacifica": ["public_key", "private_key"],
            "variational": ["public_key", "private_key"],
            "mock": []
        }.get(ex_type, [])
        
        for field in fields:
            try:
                val = self.query_one(f"#{self.id_prefix}-{field}", Input).value
                params[field] = val
            except Exception:
                params[field] = ""
            
        return ExchangeConfig(exchange_type=ex_type, params=params)


class MasterPasswordScreen(ModalScreen[str | None]):
    """Screen for creating or entering master password."""
    def __init__(self, create_mode: bool, **kwargs):
        super().__init__(**kwargs)
        self.create_mode = create_mode

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            title = ui_t("master_pwd_create_title") if self.create_mode else ui_t("master_pwd_enter_title")
            yield Label(title, id="dialog-title")

            with Vertical(classes="input-group"):
                yield Label(ui_t("master_pwd_label"), classes="field-label")
                yield Input(password=True, id="master-password")

            if self.create_mode:
                with Vertical(classes="input-group"):
                    yield Label(ui_t("master_pwd_confirm_label"), classes="field-label")
                    yield Input(password=True, id="master-password-confirm")

            with Horizontal(id="dialog-buttons"):
                yield Button(i18n.t("cancel"), id="btn-master-cancel", variant="error")
                action_label = ui_t("master_pwd_create_btn") if self.create_mode else ui_t("master_pwd_unlock_btn")
                yield Button(action_label, id="btn-master-submit", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#master-password", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-master-cancel":
            self.dismiss(None)
            return

        password = self.query_one("#master-password", Input).value
        if len(password) < 6:
            self.app.notify(ui_t("master_pwd_short"), severity="error")
            return

        if self.create_mode:
            confirm = self.query_one("#master-password-confirm", Input).value
            if password != confirm:
                self.app.notify(ui_t("master_pwd_mismatch"), severity="error")
                return

        self.dismiss(password)


class ProfileSettingsScreen(ModalScreen[SettingsProfile | None]):
    """Create or edit strategy profile."""
    def __init__(self, profile: SettingsProfile | None = None, **kwargs):
        super().__init__(**kwargs)
        self.profile = profile

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            title = ui_t("profile_edit_title") if self.profile else ui_t("profile_add_title")
            yield Label(title, id="dialog-title")

            with ScrollableContainer():
                with Vertical(classes="input-group"):
                    yield Label(ui_t("profile_id_label"), classes="field-label")
                    yield Input(
                        id="profile-id",
                        placeholder="e.g. scalp",
                        value=self.profile.id if self.profile else "",
                        disabled=self.profile is not None,
                    )
                with Vertical(classes="input-group"):
                    yield Label(ui_t("profile_name_label"), classes="field-label")
                    yield Input(
                        id="profile-name",
                        placeholder="e.g. Scalping",
                        value=self.profile.name if self.profile else "",
                    )
                with Vertical(classes="input-group"):
                    yield Label(i18n.t("target_size_usd"), classes="field-label")
                    yield Input(
                        id="profile-target-size",
                        placeholder="1000",
                        value=str(self.profile.settings.target_size_usd) if self.profile else "1000",
                    )
                with Vertical(classes="input-group"):
                    yield Label(ui_t("symbol_col"), classes="field-label")
                    yield Input(
                        id="profile-symbol",
                        placeholder="BTC-PERP",
                        value=self.profile.settings.symbol if self.profile else "BTC-PERP",
                    )
                with Vertical(classes="input-group"):
                    yield Label(ui_t("max_spread_label"), classes="field-label")
                    yield Input(
                        id="profile-max-spread",
                        placeholder="10",
                        value=str(self.profile.settings.max_spread_bps) if self.profile else "10",
                    )
                with Vertical(classes="input-group"):
                    yield Label(ui_t("rebalance_label"), classes="field-label")
                    yield Input(
                        id="profile-rebalance",
                        placeholder="30",
                        value=str(self.profile.settings.rebalance_interval_sec) if self.profile else "30",
                    )

            with Horizontal(id="dialog-buttons"):
                yield Button(i18n.t("cancel"), id="btn-profile-cancel", variant="error")
                yield Button(i18n.t("save"), id="btn-profile-save", variant="primary")

    def on_mount(self) -> None:
        target = "#profile-name" if self.profile else "#profile-id"
        self.query_one(target, Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-profile-cancel":
            self.dismiss(None)
            return

        try:
            profile_id = self.query_one("#profile-id", Input).value.strip()
            name = self.query_one("#profile-name", Input).value.strip()
            target_size = float(self.query_one("#profile-target-size", Input).value.strip())
            symbol = self.query_one("#profile-symbol", Input).value.strip() or "BTC-PERP"
            max_spread = float(self.query_one("#profile-max-spread", Input).value.strip())
            rebalance = int(self.query_one("#profile-rebalance", Input).value.strip())
        except ValueError:
            self.app.notify(ui_t("invalid_profile_numbers"), severity="error")
            return

        if not profile_id:
            self.app.notify(ui_t("profile_id_required"), severity="error")
            return
        if not name:
            self.app.notify(ui_t("profile_name_required"), severity="error")
            return

        profile = SettingsProfile(
            id=profile_id,
            name=name,
            settings=StrategySettings(
                target_size_usd=target_size,
                symbol=symbol,
                max_spread_bps=max_spread,
                rebalance_interval_sec=rebalance,
            ),
        )
        self.dismiss(profile)


class AccountSettingsScreen(ModalScreen[AccountConfig]):
    """Screen for adding or editing an account."""
    def __init__(self, profiles: list[SettingsProfile], account: AccountConfig | None = None, **kwargs):
        super().__init__(**kwargs)
        self.profiles = profiles
        self.account = account
        self.default_profile_id = profiles[0].id if profiles else "default"

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            title = i18n.t("edit_account") if self.account else i18n.t("add_account")
            yield Label(title, id="dialog-title")
            
            with ScrollableContainer():
                container_type = Vertical if is_mobile() else Horizontal
                with container_type(classes="dialog-row"):
                    with Vertical(classes="input-group"):
                        yield Label(i18n.t("account_name"), classes="field-label")
                        yield Input(
                            placeholder="Demo", 
                            id="acc-name", 
                            value=self.account.name if self.account else ""
                        )
                    with Vertical(classes="input-group"):
                        yield Label(ui_t("settings_profile_label"), classes="field-label")
                        profile_options = [(p.name, p.id) for p in self.profiles]
                        yield Select(
                            profile_options,
                            prompt=ui_t("select_profile_prompt"),
                            id="acc-profile",
                            value=self.account.settings_profile_id if self.account else self.default_profile_id,
                        )
                    with Vertical(classes="input-group"):
                        yield Label(ui_t("target_override_label"), classes="field-label")
                        yield Input(
                            placeholder=ui_t("target_override_placeholder"),
                            id="acc-size", 
                            value=(
                                str(self.account.settings_override.target_size_usd)
                                if self.account and self.account.settings_override.target_size_usd is not None
                                else ""
                            )
                        )
                
                ex_a_init = self.account.exchanges[0] if self.account and len(self.account.exchanges) > 0 else None
                ex_b_init = self.account.exchanges[1] if self.account and len(self.account.exchanges) > 1 else None
                
                with container_type(id="ex-row"):
                    yield ExchangeConfigForm("Exchange A", "ex-a", initial_config=ex_a_init)
                    yield ExchangeConfigForm("Exchange B", "ex-b", initial_config=ex_b_init)

            with Horizontal(id="dialog-buttons"):
                yield Button(i18n.t("cancel"), id="btn-cancel", variant="error")
                yield Button(i18n.t("save"), id="btn-save", variant="primary")

    def on_mount(self) -> None:
        if is_mobile():
            self.query_one("#acc-name", Input).focus()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            try:
                name = self.query_one("#acc-name", Input).value.strip()
                if not name:
                    self.app.notify("Name is required", severity="error")
                    return

                try:
                    size_val = self.query_one("#acc-size", Input).value
                    size = float(size_val) if size_val.strip() else None
                except ValueError:
                    self.app.notify("Invalid target size", severity="error")
                    return
                profile_id = self.query_one("#acc-profile", Select).value
                if not isinstance(profile_id, str) or not profile_id:
                    self.app.notify(ui_t("select_profile_required"), severity="error")
                    return
                
                ex_a_form = self.query_one("#ex-a", ExchangeConfigForm)
                ex_b_form = self.query_one("#ex-b", ExchangeConfigForm)
                
                ex_a_config = ex_a_form.get_config()
                ex_b_config = ex_b_form.get_config()
                
                if not ex_a_config or not ex_b_config:
                    self.app.notify("Select both exchanges", severity="error")
                    return

                # Perform Validation
                self.app.notify(i18n.t("verifying_accounts") or "Verifying accounts...", severity="information")
                
                async def verify(form, config):
                    form.clear_status()
                    form.set_status("WAIT", "yellow")
                    try:
                        ex = create_exchange(config, name, 1)
                        await ex.connect()
                        await ex.get_balance()
                        config.last_error = None
                        form.set_status("OK", "green")
                    except Exception as e:
                        err_msg = str(e)
                        config.last_error = err_msg
                        form.set_error(err_msg)
                
                await asyncio.gather(
                    verify(ex_a_form, ex_a_config),
                    verify(ex_b_form, ex_b_config)
                )

                # Create new config or update existing
                result = AccountConfig(
                    name=name,
                    settings_profile_id=profile_id,
                    settings_override=StrategySettingsOverride(target_size_usd=size),
                    exchanges=[ex_a_config, ex_b_config],
                    enabled=self.account.enabled if self.account else True
                )
                self.dismiss(result)
            except Exception as e:
                # Use plain string to avoid MarkupError if exception contains [ or ]
                error_msg = str(e).replace("[", "\\[").replace("]", "\\]")
                self.app.notify(f"Error: {error_msg}", severity="error")
        else:
            self.dismiss(None)


class Flow(App):
    TITLE = i18n.t("app_title")
    SUB_TITLE = i18n.t("app_subtitle")
    COMMAND_PALETTE_PLACEHOLDER = i18n.t("placeholder_cmd")
    _SYSTEM_COMMAND_TRANSLATIONS = {
        "Maximize the focused widget": {
            "ru_title": "Развернуть активный виджет",
            "ru_help": "Включить режим фокуса для текущего виджета",
        },
        "Minimize the focused widget": {
            "ru_title": "Свернуть активный виджет",
            "ru_help": "Выйти из режима фокуса",
        },
        "Toggle dark mode": {
            "ru_title": "Переключить темную тему",
            "ru_help": "Сменить светлую/темную тему интерфейса",
        },
        "Quit the application": {
            "ru_title": "Выйти из приложения",
            "ru_help": "Закрыть Flow",
        },
    }
    
    CSS = """
    Screen {
        background: #0b1220;
        color: $text;
    }
    *:focus {
        outline: none;
    }
    Header {
        background: #0f172a;
        border-bottom: solid #334155;
        color: $text;
    }
    Footer {
        background: #111827;
        border-top: solid #334155;
        color: $text-muted;
    }
    TabbedContent {
        padding: 1 1 0 1;
        background: transparent;
    }
    TabPane {
        padding: 1;
        border: round #334155;
        background: #111827;
    }
    #stats-row, #stats-summary {
        height: auto;
        min-height: 4;
        margin: 0 0 1 0;
    }
    .section-title {
        margin: 1 0 0 0;
        text-style: bold;
        color: #93c5fd;
        background: #0f172a;
        padding: 0 1;
        border-left: thick #3b82f6;
    }
    RichLog {
        height: 1fr;
        border: round #334155;
        background: #0f172a;
        padding: 0 1;
        margin-top: 1;
    }
    DataTable {
        height: auto;
        min-height: 10;
        border: round #334155;
        background: #0f172a;
        margin-top: 1;
    }
    .controls {
        height: auto;
        margin-top: 1;
        align: left middle;
        border-top: solid #334155;
        padding-top: 1;
    }
    Button,
    Button.-default,
    Button.-primary,
    Button.-error {
        margin-right: 1;
        min-width: 16;
        height: 3;
        padding: 0 1;
        border: round #475569;
        outline: none;
        background: transparent;
        color: #e5e7eb;
    }
    Button.-primary {
        border: round #2563eb;
        background: transparent;
        color: #93c5fd;
        text-style: bold;
    }
    Button.-error {
        border: round #dc2626;
        background: transparent;
        color: #fca5a5;
        text-style: bold;
    }
    Button:hover,
    Button.-default:hover,
    Button.-primary:hover,
    Button.-error:hover {
        background: #1e293b;
    }
    Button:focus,
    Button.-default:focus,
    Button.-primary:focus,
    Button.-error:focus {
        outline: none;
        border: round #93c5fd;
        background: #1e293b;
    }
    Input, Select {
        margin: 0 0 1 0;
        border: round #334155;
        background: #0f172a;
    }
    .field-label {
        color: $text-muted;
        margin-bottom: 0;
    }
    .settings-card {
        border: round #334155;
        padding: 1 1;
        margin: 0 0 1 0;
        background: #111827;
        width: 1fr;
        max-width: 60;
    }
    #btn-save-settings {
        width: auto;
        align-horizontal: right;
    }

    #dialog {
        padding: 1 2;
        background: #111827;
        border: round #3b82f6;
        width: 95%;
        height: auto;
        max-height: 45;
        align: center middle;
    }
    #dialog-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
        color: #bfdbfe;
    }
    .dialog-row {
        height: auto;
        margin-bottom: 1;
    }
    .dialog-row Vertical {
        width: 1fr;
        padding: 0 1 1 1;
        height: auto;
    }
    .input-group {
        border: round #334155;
        background: #0f172a;
    }
    #ex-row {
        height: auto;
        border-top: solid #334155;
        margin-top: 1;
        padding-top: 1;
    }
    .dex-header-row {
        height: auto;
        margin-bottom: 0;
    }
    .dex-header {
        text-style: bold;
        color: $secondary;
        width: auto;
    }
    .status-label {
        margin-left: 1;
        text-style: bold;
    }
    .error-label {
        color: $error;
        height: auto;
        max-height: 2;
        overflow: hidden;
    }
    #dialog-buttons {
        margin-top: 1;
        align: center middle;
        height: auto;
    }
    #dialog-buttons Button {
        margin: 0 1;
    }
    #dialog Input {
        margin-bottom: 0;
    }
    ExchangeConfigForm {
        width: 1fr;
        height: auto;
        padding: 0 1 1 1;
        border: round #334155;
        background: #0f172a;
    }
    #accounts-config-table, #bots-table, #stats-table {
        margin-bottom: 1;
    }
    """

    def __init__(self):
        super().__init__()
        self.manager: BotManager | None = None
        # Create logs directory if not exists
        os.makedirs("logs", exist_ok=True)
        self.log_file = open("logs/debug.log", "a", encoding="utf-8")

    def log_message(self, message: str, color: str = "white"):
        """Write to UI log and persistent file."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        clean_msg = message.replace("[", "").replace("]", "").split("/")[-1] # Simple strip for file
        self.log_file.write(f"[{timestamp}] {clean_msg}\n")
        self.log_file.flush()
        
        if hasattr(self, "log_widget"):
            self.log_widget.write(f"[{color}]{message}[/]")

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane(i18n.t("dashboard"), id="tab-dashboard"):
                yield DashboardTab()
            with TabPane(i18n.t("accounts"), id="tab-accounts"):
                yield AccountsTab()
            with TabPane(ui_t("profiles_tab"), id="tab-profiles"):
                yield ProfilesTab()
            with TabPane(i18n.t("statistics"), id="tab-statistics"):
                yield StatisticsTab()
            with TabPane(i18n.t("settings"), id="tab-settings"):
                yield SettingsTab()
        yield Footer()

    async def on_mount(self) -> None:
        # Dynamic localization for the built-in command palette
        self.COMMAND_PALETTE_PLACEHOLDER = i18n.t("placeholder_cmd")

        self.log_widget = self.query_one("#feed-log", RichLog)
        self.log_widget.write(i18n.t("system_init"))
        
        # Setup Tables
        table = self.query_one("#bots-table", DataTable)
        table.add_columns(
            i18n.t("account"), 
            i18n.t("balance"),
            i18n.t("status"), 
            i18n.t("pnl_unrealized"), 
            i18n.t("positions")
        )
        
        config_table = self.query_one("#accounts-config-table", DataTable)
        config_table.add_columns(
            i18n.t("name"), 
            ui_t("profile_col"),
            i18n.t("target_size"), 
            "Exchange A", 
            "Exchange B",
            i18n.t("balance")
        )
        config_table.cursor_type = "row"

        # Setup Statistics Table
        stats_table = self.query_one("#stats-table", DataTable)
        stats_table.add_columns(
            i18n.t("account"), 
            i18n.t("trades"), 
            i18n.t("volume_24h"), 
            i18n.t("funding_total"),
            i18n.t("status")
        )

        profiles_table = self.query_one("#profiles-table", DataTable)
        profiles_table.add_columns(
            ui_t("profile_id_col"),
            ui_t("profile_name_col"),
            i18n.t("target_size"),
            ui_t("symbol_col"),
            ui_t("spread_col"),
            ui_t("rebalance_col"),
        )
        profiles_table.cursor_type = "row"

        env_password = os.getenv("FLOW_MASTER_PASSWORD", "").strip()
        if env_password:
            asyncio.create_task(self._unlock_with_password(env_password))
        else:
            self.push_screen(
                MasterPasswordScreen(create_mode=not has_master_password()),
                self._handle_master_password_result,
            )

    def _handle_master_password_result(self, password: str | None) -> None:
        if password is None:
            self.exit()
            return
        asyncio.create_task(self._unlock_with_password(password))

    async def _unlock_with_password(self, password: str) -> None:
        try:
            initialize_master_password(password)
            self.manager = BotManager()
            self._refresh_accounts_table()
            self._refresh_profiles_table()
            await self.manager.start_all()
            asyncio.create_task(self.update_loop())
            self.call_after_refresh(self._enable_default_focus_mode)
        except ValueError:
            self.notify(ui_t("master_pwd_invalid"), severity="error")
            self.push_screen(
                MasterPasswordScreen(create_mode=not has_master_password()),
                self._handle_master_password_result,
            )
        except Exception as e:
            self.notify(ui_t("master_pwd_unlock_failed").format(error=e), severity="error")
            self.push_screen(
                MasterPasswordScreen(create_mode=not has_master_password()),
                self._handle_master_password_result,
            )

    def _enable_default_focus_mode(self) -> None:
        """Enable maximize mode for the main dashboard widget on startup."""
        try:
            bots_table = self.query_one("#bots-table", DataTable)
            bots_table.focus()
            self.screen.maximize(bots_table)
        except Exception:
            # Keep normal layout if focus/maximize is unavailable.
            pass

    def _localize_system_command(self, command: SystemCommand) -> SystemCommand:
        """Localize known built-in command palette entries."""
        if i18n.lang != "ru":
            return command

        title = getattr(command, "title", getattr(command, "name", ""))
        help_text = getattr(command, "help", "")
        callback = getattr(command, "callback", None)
        discover = getattr(command, "discover", True)

        translation = self._SYSTEM_COMMAND_TRANSLATIONS.get(title)
        if not translation:
            return command

        return SystemCommand(
            translation["ru_title"],
            translation["ru_help"],
            callback,
            discover,
        )

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        for command in super().get_system_commands(screen):
            yield self._localize_system_command(command)

    def _focus_command_palette_input(self) -> None:
        """Ensure command palette input receives focus immediately after opening."""
        if isinstance(self.screen, CommandPalette):
            try:
                self.screen.query_one(Input).focus()
            except Exception:
                # Keep default behavior if palette internals change.
                pass

    def action_command_palette(self) -> None:
        """Open a localized command palette and force input focus."""
        self.push_screen(CommandPalette(placeholder=i18n.t("placeholder_cmd")))
        self.call_after_refresh(self._focus_command_palette_input)

    async def update_loop(self):
        while True:
            try:
                if self.manager is None:
                    await asyncio.sleep(0.5)
                    continue
                # 0. Smart Balance Update (handles 1s or 10m internally)
                tasks = [bot.update_balances() for bot in self.manager.bots]
                if tasks:
                    await asyncio.gather(*tasks)
                
                # 1. Update Dashboard Table (Only running bots)
                table = self.query_one("#bots-table", DataTable)
                total_pnl = Decimal("0.0")
                active_count = 0
                
                table.clear()
                for bot in self.manager.bots:
                    if not bot.config.enabled:
                        continue
                        
                    if bot.last_bal_update == 0:
                        bal_str = "[dim]Loading...[/]"
                    else:
                        bal_str = f"${bot.bal_a:,.2f} / ${bot.bal_b:,.2f}"
                        
                    status_style = "green" if bot.strategy.state == StrategyState.HEDGED else "white"
                    pnl = bot.strategy.current_pnl
                    total_pnl += pnl
                    active_count += 1
                    
                    state_key = f"state_{bot.strategy.state.value.lower()}"
                    translated_state = i18n.t(state_key)
                    
                    table.add_row(
                        bot.config.name,
                        bal_str,
                        f"[{status_style}]{translated_state}[/]",
                        f"${pnl:.2f}",
                        "2" if bot.strategy.state == StrategyState.HEDGED else "0"
                    )

                # 2. Update Stats (Dashboard Summary)
                pnl_style = "bold green" if total_pnl >= 0 else "bold red"
                self.query_one("#stat-pnl", StatusPill).update_value(i18n.t("total_pnl"), f"${total_pnl:.2f}", pnl_style)
                self.query_one("#stat-bots", StatusPill).update_value(i18n.t("active_bots"), str(active_count))

                # 3. Update Accounts Tab Table (All bots + balances)
                config_table = self.query_one("#accounts-config-table", DataTable)
                config_table.clear()
                for idx, bot in enumerate(self.manager.bots):
                    ex_a = bot.config.exchanges[0] if len(bot.config.exchanges) > 0 else None
                    ex_b = bot.config.exchanges[1] if len(bot.config.exchanges) > 1 else None
                    
                    def fmt_ex(ex):
                        if not ex: return "NONE"
                        name = ex.exchange_type.upper()
                        if hasattr(ex, 'last_error') and ex.last_error:
                            return f"[red]{name}[/]"
                        return name

                    bal_str = f"${bot.bal_a:,.2f} / ${bot.bal_b:,.2f}"
                    config_table.add_row(
                        bot.config.name, 
                        self.manager.get_profile_name(bot.config.settings_profile_id),
                        str(bot.settings.target_size_usd),
                        fmt_ex(ex_a),
                        fmt_ex(ex_b),
                        bal_str,
                        key=str(idx)
                    )

                # 4. Update Statistics Tab
                stats_table = self.query_one("#stats-table", DataTable)
                stats_table.clear()
                total_volume = Decimal("0.0")
                total_trades = 0
                total_funding = Decimal("0.0")

                for bot in self.manager.bots:
                    if not bot.config.enabled: continue
                    
                    volume = Decimal(str(bot.strategy.target_size_usd)) * 2 if bot.running else Decimal("0")
                    trades = 2 if bot.strategy.state != StrategyState.IDLE else 0
                    funding = bot.strategy.current_pnl * Decimal("0.1")
                    
                    total_volume += volume
                    total_trades += trades
                    total_funding += funding

                    stats_table.add_row(
                        bot.config.name,
                        str(trades),
                        f"${volume:.2f}",
                        f"${funding:.4f}",
                        i18n.t(f"state_{bot.strategy.state.value.lower()}")
                    )

                self.query_one("#stat-volume", StatusPill).update_value(i18n.t("volume_24h"), f"${total_volume:.2f}")
                self.query_one("#stat-trades", StatusPill).update_value(i18n.t("trades"), str(total_trades))
                self.query_one("#stat-funding", StatusPill).update_value(i18n.t("funding_total"), f"${total_funding:.4f}")

            except Exception as e:
                import traceback
                error_details = traceback.format_exc()
                self.log_widget.write(f"[red]UI Update Error: {str(e)}[/]")
                self.log_widget.write(f"[dim red]{error_details}[/]")

            await asyncio.sleep(1.0)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self.manager is None:
            self.notify(ui_t("unlock_first"), severity="warning")
            return

        if event.button.id == "btn-add-account":
            def add_account_callback(new_acc: AccountConfig | None) -> None:
                if new_acc:
                    self.manager.add_account(new_acc)
                    self.log_widget.write(f"[blue]{i18n.t('add_account')}: {new_acc.name}[/]")
                    self._refresh_accounts_table()
            
            self.push_screen(AccountSettingsScreen(self.manager.config.settings_profiles), add_account_callback)
            
        elif event.button.id == "btn-edit-account":
            table = self.query_one("#accounts-config-table", DataTable)
            if table.cursor_row is not None:
                idx = table.cursor_row
                acc = self.manager.config.accounts[idx]
                
                def edit_account_callback(new_acc: AccountConfig | None) -> None:
                    if new_acc:
                        self.manager.update_account(idx, new_acc)
                        self.log_widget.write(f"[yellow]{i18n.t('edit_account')}: {new_acc.name}[/]")
                        self._refresh_accounts_table()
                
                self.push_screen(
                    AccountSettingsScreen(self.manager.config.settings_profiles, account=acc),
                    edit_account_callback,
                )

        elif event.button.id == "btn-remove-account":
            table = self.query_one("#accounts-config-table", DataTable)
            if table.cursor_row is not None:
                idx = table.cursor_row
                self.manager.remove_account(idx)
                self.log_widget.write(f"[red]{i18n.t('remove_selected')}[/]")
                self._refresh_accounts_table()

        elif event.button.id == "btn-add-profile":
            def add_profile_callback(profile: SettingsProfile | None) -> None:
                if profile is None:
                    return
                try:
                    self.manager.add_profile(profile)
                    self.notify(ui_t("profile_added").format(name=profile.name), severity="information")
                    self._refresh_profiles_table()
                    self._refresh_accounts_table()
                except ValueError as e:
                    self.notify(str(e), severity="error")

            self.push_screen(ProfileSettingsScreen(), add_profile_callback)

        elif event.button.id == "btn-edit-profile":
            profiles_table = self.query_one("#profiles-table", DataTable)
            if profiles_table.cursor_row is not None:
                idx = profiles_table.cursor_row
                profile = self.manager.config.settings_profiles[idx]

                def edit_profile_callback(updated: SettingsProfile | None) -> None:
                    if updated is None:
                        return
                    try:
                        self.manager.update_profile(profile.id, updated)
                        self.notify(ui_t("profile_updated").format(name=updated.name), severity="information")
                        self._refresh_profiles_table()
                        self._refresh_accounts_table()
                    except ValueError as e:
                        self.notify(str(e), severity="error")

                self.push_screen(ProfileSettingsScreen(profile=profile), edit_profile_callback)

        elif event.button.id == "btn-remove-profile":
            profiles_table = self.query_one("#profiles-table", DataTable)
            if profiles_table.cursor_row is not None:
                idx = profiles_table.cursor_row
                profile = self.manager.config.settings_profiles[idx]
                try:
                    self.manager.remove_profile(profile.id)
                    self.notify(ui_t("profile_removed").format(name=profile.name), severity="information")
                    self._refresh_profiles_table()
                    self._refresh_accounts_table()
                except ValueError as e:
                    self.notify(str(e), severity="error")

    def _refresh_accounts_table(self):
        """Force immediate refresh of the accounts table with current data."""
        config_table = self.query_one("#accounts-config-table", DataTable)
        config_table.clear()
        if self.manager is None:
            return
        
        # We need to map config accounts to bots to get cached balances
        for idx, acc in enumerate(self.manager.config.accounts):
            bot = next((b for b in self.manager.bots if b.config == acc), None)
            
            ex_a = acc.exchanges[0] if len(acc.exchanges) > 0 else None
            ex_b = acc.exchanges[1] if len(acc.exchanges) > 1 else None
            
            def fmt_ex(ex):
                if not ex: return "NONE"
                name = ex.exchange_type.upper()
                if ex.last_error:
                    return f"[red]{name}[/]"
                return name

            bal_str = "--"
            if bot:
                bal_str = f"${bot.bal_a:,.2f} / ${bot.bal_b:,.2f}"

            config_table.add_row(
                acc.name, 
                self.manager.get_profile_name(acc.settings_profile_id),
                str(self.manager.resolve_account_settings(acc).target_size_usd),
                fmt_ex(ex_a),
                fmt_ex(ex_b),
                bal_str,
                key=str(idx)
            )

    def _refresh_profiles_table(self):
        profiles_table = self.query_one("#profiles-table", DataTable)
        profiles_table.clear()
        if self.manager is None:
            return

        for idx, profile in enumerate(self.manager.config.settings_profiles):
            profiles_table.add_row(
                profile.id,
                profile.name,
                str(profile.settings.target_size_usd),
                profile.settings.symbol,
                str(profile.settings.max_spread_bps),
                str(profile.settings.rebalance_interval_sec),
                key=str(idx),
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        if self.manager is None:
            return
        if event.data_table.id == "accounts-config-table":
            self.query_one("#btn-remove-account").disabled = False
            self.query_one("#btn-edit-account").disabled = False
        elif event.data_table.id == "profiles-table":
            self.query_one("#btn-edit-profile").disabled = False
            self.query_one("#btn-remove-profile").disabled = False

if __name__ == "__main__":
    app = Flow()
    app.run()
