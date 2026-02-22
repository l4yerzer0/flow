from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Horizontal, ScrollableContainer
from textual.widgets import Header, Footer, Static, Input, RichLog, TabbedContent, TabPane, DataTable, Label, Button
from textual.reactive import reactive
from src.exchanges.mock import MockExchange
from src.strategy.delta_neutral import DeltaNeutralStrategy, StrategyState
from decimal import Decimal
import asyncio
from datetime import datetime

# --- Reusable Components ---

class StatCard(Static):
    """A styled card for displaying a single metric."""
    DEFAULT_CSS = """
    StatCard {
        width: 1fr;
        height: 3;
        background: $surface;
        color: $text;
        border: solid $primary;
        padding: 0 1;
        margin: 0 1;
        content-align: center middle;
    }
    .label { color: $text-muted; }
    .value { text-style: bold; color: $accent; }
    """
    
    def __init__(self, label: str, value: str = "--", **kwargs):
        super().__init__(**kwargs)
        self.label = label
        self.value = value

    def render(self):
        return f"[{self.label}] {self.value}"

    def update_value(self, new_value):
        self.value = new_value
        self.refresh()

# --- Tab Contents ---

class DashboardTab(Vertical):
    """The main dashboard view."""
    def compose(self) -> ComposeResult:
        with Horizontal(id="stats-row"):
            yield StatCard("TOTAL PnL", "$0.00", id="card-pnl")
            yield StatCard("STATUS", "IDLE", id="card-status")
            yield StatCard("ACTIVE POS", "0", id="card-pos")

        yield Label("Activity Feed", classes="section-title")
        yield RichLog(id="feed-log", markup=True, wrap=True)

class AccountsTab(Vertical):
    """Table of connected accounts."""
    def compose(self) -> ComposeResult:
        yield DataTable()

    def on_mount(self):
        table = self.query_one(DataTable)
        table.add_columns("Exchange", "Status", "Balance (USDC)", "Ping")
        # Initial Mock Data
        table.add_row("Pacifica", "🟢 Connected", "$10,000.00", "45ms", key="pacifica")
        table.add_row("Variational", "🟢 Connected", "$10,000.00", "32ms", key="variational")

class SettingsTab(ScrollableContainer):
    """Configuration form."""
    def compose(self) -> ComposeResult:
        yield Label("Strategy Settings", classes="section-title")
        yield Label("Target Position Size (USD)")
        yield Input(placeholder="1000", value="1000")
        
        yield Label("Stop Loss (%)")
        yield Input(placeholder="5.0", value="5.0")
        
        yield Label("API Keys (Pacifica)", classes="section-title")
        yield Input(placeholder="API Key", password=True)
        yield Input(placeholder="API Secret", password=True)

        yield Label("API Keys (Variational)", classes="section-title")
        yield Input(placeholder="API Key", password=True)
        yield Input(placeholder="API Secret", password=True)
        
        yield Button("Save Configuration", variant="primary", id="btn-save")

# --- Main App ---

class TradingBotApp(App):
    CSS = """
    Screen { background: $surface-darken-1; }
    
    #stats-row {
        height: 5;
        margin: 1 0;
    }
    
    .section-title {
        margin: 1 0;
        text-style: bold;
        color: $secondary;
    }

    RichLog {
        background: $surface;
        border: solid $primary;
        height: 1fr;
    }

    DataTable {
        height: 1fr;
        border: solid $primary;
    }

    Input { margin-bottom: 1; }
    """

    TITLE = "Flow Bot"
    SUB_TITLE = "Delta Neutral Strategy"

    def __init__(self):
        super().__init__()
        self.ex_a = MockExchange("Pacifica")
        self.ex_b = MockExchange("Variational")
        self.strategy = DeltaNeutralStrategy(self.ex_a, self.ex_b)

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
        self.log_widget.write("[bold green]System Initialized.[/] Waiting for strategy...")
        
        # Start loops
        asyncio.create_task(self.strategy.run_loop())
        asyncio.create_task(self.update_dashboard())

    async def update_dashboard(self):
        """Updates dashboard stats and bridges logs."""
        card_pnl = self.query_one("#card-pnl", StatCard)
        card_status = self.query_one("#card-status", StatCard)
        card_pos = self.query_one("#card-pos", StatCard)
        
        last_state = self.strategy.state

        while True:
            # Update Cards
            pnl = self.strategy.current_pnl
            card_pnl.update_value(f"${pnl:.2f}")
            card_status.update_value(self.strategy.state.value)
            
            # Simple Logic for mock positions count
            pos_count = "2" if self.strategy.state == StrategyState.HEDGED else "0"
            card_pos.update_value(pos_count)

            # Log State Changes
            if self.strategy.state != last_state:
                timestamp = datetime.now().strftime("%H:%M:%S")
                if self.strategy.state == StrategyState.OPENING:
                    self.log_widget.write(f"[{timestamp}] [bold blue]Scanning market...[/]")
                elif self.strategy.state == StrategyState.HEDGED:
                    self.log_widget.write(f"[{timestamp}] [bold green]✔ Positions OPENED. Hedging...[/]")
                elif self.strategy.state == StrategyState.CLOSING:
                     self.log_widget.write(f"[{timestamp}] [yellow]Closing positions...[/]")
                last_state = self.strategy.state
            
            # Update Account Table (if visible)
            try:
                table = self.query_one(DataTable)
                bal_a = await self.ex_a.get_balance()
                bal_b = await self.ex_b.get_balance()
                table.update_cell("pacifica", "Balance (USDC)", f"${bal_a:.2f}")
                table.update_cell("variational", "Balance (USDC)", f"${bal_b:.2f}")
            except:
                pass # Table might not be mounted if tab isn't active (or different structure)

            await asyncio.sleep(0.5)

if __name__ == "__main__":
    app = TradingBotApp()
    app.run()
