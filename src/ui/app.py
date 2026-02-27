from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Horizontal, ScrollableContainer
from textual.widgets import Header, Footer, Static, Input, RichLog, TabbedContent, TabPane, DataTable, Label, Button, Select
from textual.screen import ModalScreen
from src.core.config import AccountConfig, ExchangeConfig
from src.core.bot_manager import BotManager, BotInstance, StrategyState
from src.core.i18n import i18n
from decimal import Decimal
import asyncio
from datetime import datetime

# --- Reusable UI ---

class StatusPill(Static):
    DEFAULT_CSS = """
    StatusPill {
        width: auto;
        height: 1;
        padding: 0 1;
        margin: 0 1;
        background: $surface;
        color: $text-muted;
    }
    """
    def __init__(self, label: str, value: str = "--", **kwargs):
        super().__init__(**kwargs)
        self.label = label
        self.value = value

    def render(self):
        return f"{self.label}: {self.value}"

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
        with Horizontal(id="stats-row"):
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

class SettingsTab(ScrollableContainer):
    """Global Settings."""
    def compose(self) -> ComposeResult:
        yield Label(i18n.t("settings"), classes="section-title")
        yield Label(i18n.t("refresh_rate"))
        yield Input(placeholder="1000", value="1000")
        yield Button(i18n.t("save"), variant="primary")


class ExchangeConfigForm(Vertical):
    """Sub-form for a single exchange configuration."""
    def __init__(self, label: str, id_prefix: str, initial_config: ExchangeConfig | None = None, **kwargs):
        super().__init__(id=id_prefix, **kwargs)
        self.label_text = label
        self.id_prefix = id_prefix
        self.initial_config = initial_config

    def compose(self) -> ComposeResult:
        yield Label(self.label_text, classes="dex-header")
        yield Select(
            [("Pacifica", "pacifica"), ("Variational", "variational"), ("Mock", "mock")],
            prompt=i18n.t("exchange_type"),
            id=f"{self.id_prefix}-type"
        )
        yield Vertical(id=f"{self.id_prefix}-fields")

    def on_mount(self) -> None:
        if self.initial_config:
            allowed_types = ["pacifica", "variational", "mock"]
            ex_type = self.initial_config.exchange_type
            if ex_type in allowed_types:
                select = self.query_one(f"#{self.id_prefix}-type", Select)
                select.value = ex_type
                self.update_fields(ex_type)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == f"{self.id_prefix}-type":
            self.update_fields(event.value)

    def update_fields(self, ex_type: str):
        container = self.query_one(f"#{self.id_prefix}-fields", Vertical)
        container.query("*").remove()
        
        fields = {
            "pacifica": ["api_key", "api_secret", "subaccount_id"],
            "variational": ["wallet_private_key"],
            "mock": []
        }.get(ex_type, [])
        
        for field in fields:
            is_password = "key" in field or "secret" in field
            val = ""
            if self.initial_config and self.initial_config.exchange_type == ex_type:
                val = self.initial_config.params.get(field, "")
            
            container.mount(Input(
                placeholder=i18n.t(field), 
                id=f"{self.id_prefix}-{field}", 
                password=is_password,
                value=val
            ))

    def get_config(self) -> ExchangeConfig | None:
        select = self.query_one(f"#{self.id_prefix}-type", Select)
        ex_type = select.value
        
        # Select.BLANK or Select.NULL check
        if not isinstance(ex_type, str) or ex_type == "":
            return None
            
        params = {}
        fields = {
            "pacifica": ["api_key", "api_secret", "subaccount_id"],
            "variational": ["wallet_private_key"],
            "mock": []
        }.get(ex_type, [])
        
        for field in fields:
            try:
                val = self.query_one(f"#{self.id_prefix}-{field}", Input).value
                params[field] = val
            except Exception:
                params[field] = ""
            
        return ExchangeConfig(exchange_type=ex_type, params=params)


class AccountSettingsScreen(ModalScreen[AccountConfig]):
    """Screen for adding or editing an account."""
    def __init__(self, account: AccountConfig | None = None, **kwargs):
        super().__init__(**kwargs)
        self.account = account

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            title = i18n.t("edit_account") if self.account else i18n.t("add_account")
            yield Label(title, id="dialog-title")
            
            with ScrollableContainer():
                with Horizontal(classes="dialog-row"):
                    with Vertical():
                        yield Label(i18n.t("account_name"))
                        yield Input(
                            placeholder="Demo", 
                            id="acc-name", 
                            value=self.account.name if self.account else ""
                        )
                    with Vertical():
                        yield Label(i18n.t("target_size_usd"))
                        yield Input(
                            placeholder="1000", 
                            id="acc-size", 
                            value=str(self.account.target_size_usd) if self.account else "1000"
                        )
                
                ex_a_init = self.account.exchanges[0] if self.account and len(self.account.exchanges) > 0 else None
                ex_b_init = self.account.exchanges[1] if self.account and len(self.account.exchanges) > 1 else None
                
                with Horizontal(id="ex-row"):
                    yield ExchangeConfigForm("Exchange A", "ex-a", initial_config=ex_a_init)
                    yield ExchangeConfigForm("Exchange B", "ex-b", initial_config=ex_b_init)

            with Horizontal(id="dialog-buttons"):
                yield Button(i18n.t("cancel"), id="btn-cancel", variant="error")
                yield Button(i18n.t("save"), id="btn-save", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            try:
                name = self.query_one("#acc-name", Input).value.strip()
                if not name:
                    self.app.notify("Name is required", severity="error")
                    return

                try:
                    size_val = self.query_one("#acc-size", Input).value
                    size = float(size_val or "1000")
                except ValueError:
                    self.app.notify("Invalid target size", severity="error")
                    return
                
                ex_a_form = self.query_one("#ex-a", ExchangeConfigForm)
                ex_b_form = self.query_one("#ex-b", ExchangeConfigForm)
                
                ex_a = ex_a_form.get_config()
                ex_b = ex_b_form.get_config()
                
                if not ex_a or not ex_b:
                    self.app.notify("Select both exchanges", severity="error")
                    return

                # Create new config or update existing
                result = AccountConfig(
                    name=name,
                    target_size_usd=size,
                    exchanges=[ex_a, ex_b],
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
    
    CSS = """
    Screen { background: $surface-darken-1; }
    #stats-row { height: 3; margin: 1 0; border-bottom: solid $primary; }
    .section-title { margin: 1 0; text-style: bold; color: $secondary; }
    RichLog { height: 1fr; border: solid $primary; background: $surface; }
    DataTable { height: auto; min-height: 10; border: solid $primary; }
    .controls { height: auto; margin-top: 1; align: center middle; }
    Button { margin-right: 2; }

    #dialog {
        padding: 1 2;
        background: $surface;
        border: thick $primary;
        width: 100;
        height: auto;
        max-height: 35;
        align: center middle;
    }
    #dialog-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
        color: $primary;
    }
    .dialog-row {
        height: 4;
        margin-bottom: 0;
    }
    .dialog-row Vertical {
        width: 1fr;
        padding: 0 1;
        height: auto;
    }
    #ex-row {
        height: auto;
        border-top: solid $surface-lighten-1;
        margin-top: 1;
        padding-top: 1;
    }
    .dex-header {
        text-style: bold;
        color: $secondary;
        margin-bottom: 0;
    }
    #dialog-buttons {
        margin-top: 1;
        align: center middle;
        height: 3;
    }
    #dialog Input {
        margin-bottom: 0;
    }
    ExchangeConfigForm {
        width: 1fr;
        height: auto;
        padding: 0 2;
    }
    Select {
        margin: 0 0 1 0;
    }
    """

    def __init__(self):
        super().__init__()
        self.manager = BotManager()

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane(i18n.t("dashboard"), id="tab-dashboard"):
                yield DashboardTab()
            with TabPane(i18n.t("accounts"), id="tab-accounts"):
                yield AccountsTab()
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
            i18n.t("status"), 
            i18n.t("pnl_unrealized"), 
            i18n.t("positions")
        )
        
        config_table = self.query_one("#accounts-config-table", DataTable)
        config_table.add_columns(
            i18n.t("name"), 
            i18n.t("target_size"), 
            "Exchange A", 
            "Exchange B"
        )
        config_table.cursor_type = "row"
        self._refresh_accounts_table()

        # Start Manager
        await self.manager.start_all()
        
        # Start UI Loop
        asyncio.create_task(self.update_loop())

    async def update_loop(self):
        while True:
            try:
                # 1. Update Dashboard Table
                table = self.query_one("#bots-table", DataTable)
                total_pnl = Decimal("0.0")
                active_count = 0
                
                table.clear()
                for bot in self.manager.bots:
                    status_style = "green" if bot.strategy.state == StrategyState.HEDGED else "white"
                    pnl = bot.strategy.current_pnl
                    total_pnl += pnl
                    if bot.running: active_count += 1
                    
                    # Translate state using prefixed key
                    state_key = f"state_{bot.strategy.state.value.lower()}"
                    translated_state = i18n.t(state_key)
                    
                    table.add_row(
                        bot.config.name,
                        f"[{status_style}]{translated_state}[/]",
                        f"${pnl:.2f}",
                        "2" if bot.strategy.state == StrategyState.HEDGED else "0"
                    )

                # 2. Update Stats
                pnl_style = "bold green" if total_pnl >= 0 else "bold red"
                self.query_one("#stat-pnl", StatusPill).update_value(i18n.t("total_pnl"), f"${total_pnl:.2f}", pnl_style)
                self.query_one("#stat-bots", StatusPill).update_value(i18n.t("active_bots"), str(active_count))

            except Exception:
                pass

            await asyncio.sleep(1.0)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-add-account":
            def add_account_callback(new_acc: AccountConfig | None) -> None:
                if new_acc:
                    self.manager.add_account(new_acc)
                    self.log_widget.write(f"[blue]{i18n.t('add_account')}: {new_acc.name}[/]")
                    self._refresh_accounts_table()
            
            self.push_screen(AccountSettingsScreen(), add_account_callback)
            
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
                
                self.push_screen(AccountSettingsScreen(account=acc), edit_account_callback)

        elif event.button.id == "btn-remove-account":
            table = self.query_one("#accounts-config-table", DataTable)
            if table.cursor_row is not None:
                idx = table.cursor_row
                self.manager.remove_account(idx)
                self.log_widget.write(f"[red]{i18n.t('remove_selected')}[/]")
                self._refresh_accounts_table()

    def _refresh_accounts_table(self):
        """Force immediate refresh of the accounts table."""
        config_table = self.query_one("#accounts-config-table", DataTable)
        config_table.clear()
        for idx, acc in enumerate(self.manager.config.accounts):
            ex_a_info = acc.exchanges[0].exchange_type if len(acc.exchanges) > 0 else "None"
            ex_b_info = acc.exchanges[1].exchange_type if len(acc.exchanges) > 1 else "None"
            config_table.add_row(
                acc.name, 
                str(acc.target_size_usd),
                ex_a_info.upper(),
                ex_b_info.upper(),
                key=str(idx)
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        if event.data_table.id == "accounts-config-table":
            self.query_one("#btn-remove-account").disabled = False
            self.query_one("#btn-edit-account").disabled = False

if __name__ == "__main__":
    app = Flow()
    app.run()
