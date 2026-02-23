from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Horizontal, ScrollableContainer
from textual.widgets import Header, Footer, Static, Input, RichLog, TabbedContent, TabPane, DataTable, Label, Button
from src.core.config import AccountConfig, ExchangeConfig
from src.core.bot_manager import BotManager, BotInstance, StrategyState
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
        # Use simple string if no style, or ensure tags match
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
            yield StatusPill("TOTAL PnL", id="stat-pnl")
            yield StatusPill("ACTIVE BOTS", id="stat-bots")
        
        yield Label("Live Bots", classes="section-title")
        yield DataTable(id="bots-table")
        
        yield Label("Global Feed", classes="section-title")
        yield RichLog(id="feed-log", markup=True, wrap=True)

class AccountsTab(ScrollableContainer):
    """Manage Accounts (List + Add/Remove)."""
    def compose(self) -> ComposeResult:
        yield Label("Configured Accounts", classes="section-title")
        yield DataTable(id="accounts-config-table")
        
        with Horizontal(classes="controls"):
            yield Button("Add New Account", variant="primary", id="btn-add-account")
            yield Button("Remove Selected", variant="error", id="btn-remove-account", disabled=True)

class SettingsTab(ScrollableContainer):
    """Global Settings."""
    def compose(self) -> ComposeResult:
        yield Label("Global Settings", classes="section-title")
        yield Label("Refresh Rate (ms)")
        yield Input(placeholder="1000", value="1000")
        yield Button("Save", variant="primary")


class TradingBotApp(App):
    CSS = """
    Screen { background: $surface-darken-1; }
    #stats-row { height: 3; margin: 1 0; border-bottom: solid $primary; }
    .section-title { margin: 1 0; text-style: bold; color: $secondary; }
    RichLog { height: 1fr; border: solid $primary; background: $surface; }
    DataTable { height: auto; min-height: 10; border: solid $primary; }
    .controls { height: auto; margin-top: 1; align: center middle; }
    Button { margin-right: 2; }
    """

    def __init__(self):
        super().__init__()
        self.manager = BotManager()

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane("Dashboard", id="tab-dashboard"):
                yield DashboardTab()
            with TabPane("Accounts", id="tab-accounts"):
                yield AccountsTab()
            with TabPane("Settings", id="tab-settings"):
                yield SettingsTab()
        yield Footer()

    async def on_mount(self) -> None:
        self.log_widget = self.query_one("#feed-log", RichLog)
        self.log_widget.write("[bold green]System Initialized.[/] Loaded accounts.")
        
        # Setup Tables
        table = self.query_one("#bots-table", DataTable)
        table.add_columns("Account", "Status", "PnL (Unrealized)", "Positions")
        
        config_table = self.query_one("#accounts-config-table", DataTable)
        config_table.add_columns("Name", "Target Size", "Pacifica Key", "Variational Key")
        config_table.cursor_type = "row"

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
                    
                    table.add_row(
                        bot.config.name,
                        f"[{status_style}]{bot.strategy.state.value}[/]",
                        f"${pnl:.2f}",
                        "2" if bot.strategy.state == StrategyState.HEDGED else "0"
                    )

                # 2. Update Stats
                pnl_style = "bold green" if total_pnl >= 0 else "bold red"
                self.query_one("#stat-pnl", StatusPill).update_value("TOTAL PnL", f"${total_pnl:.2f}", pnl_style)
                self.query_one("#stat-bots", StatusPill).update_value("ACTIVE BOTS", str(active_count))

                # 3. Update Config Table (only if in Accounts tab to save CPU)
                config_table = self.query_one("#accounts-config-table", DataTable)
                if config_table.row_count != len(self.manager.config.accounts):
                    config_table.clear()
                    for idx, acc in enumerate(self.manager.config.accounts):
                        pk = acc.pacifica.api_key[-4:] if acc.pacifica.api_key else "None"
                        vk = acc.variational.api_key[-4:] if acc.variational.api_key else "None"
                        config_table.add_row(
                            acc.name, 
                            str(acc.target_size_usd),
                            f"***{pk}",
                            f"***{vk}",
                            key=str(idx)
                        )
            except Exception:
                pass # Prevent UI loop from dying if widgets are transient

            await asyncio.sleep(1.0)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-add-account":
            new_acc = AccountConfig(name=f"Account {len(self.manager.config.accounts)+1}")
            self.manager.add_account(new_acc)
            self.log_widget.write(f"[blue]Added new account: {new_acc.name}[/]")
            
        elif event.button.id == "btn-remove-account":
            table = self.query_one("#accounts-config-table", DataTable)
            if table.cursor_row is not None:
                idx = table.cursor_row
                self.manager.remove_account(idx)
                self.log_widget.write(f"[red]Removed account[/]")

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        if event.data_table.id == "accounts-config-table":
            self.query_one("#btn-remove-account").disabled = False

if __name__ == "__main__":
    app = TradingBotApp()
    app.run()
