# Implementation Plan: Delta Neutral Trading Bot (Pacifica & Variational)

## Phase 1: Foundation & UI Skeleton
- [ ] **Project Setup**
    - Create virtual environment.
    - Setup `pyproject.toml` and `requirements.txt` (textual, aiohttp, pydantic, python-dotenv).
    - Create project directory structure (`src/`, `tests/`, `config/`).
- [ ] **Architecture Design**
    - Define `Exchange` Abstract Base Class (ABC).
        - Methods: `connect()`, `get_balance()`, `get_ticker()`, `open_position()`, `close_position()`.
    - Define `Position` and `Order` data models.
- [ ] **TUI Prototype (Textual)**
    - Main Dashboard:
        - Header (Bot Status, Total PnL).
        - Left Panel: Account/Exchange list.
        - Center Panel: Active Positions & Market Data.
        - Bottom Panel: Logs/Console output.
    - verify the UI runs and handles input.

## Phase 2: Core Logic & Mocking
- [ ] **Mock Exchange Implementation**
    - Create `MockExchange` class that simulates price movements and order execution.
    - Generate fake PnL updates to test UI responsiveness.
- [ ] **Strategy Engine (v1)**
    - Implement the `Strategy` class.
    - Logic: Open Long on Exchange A, Short on Exchange B (Mocked).
    - Implement "Hold Period" logic (async sleep with random variance).
    - Implement "Close All" logic.
- [ ] **Configuration Manager**
    - Load accounts and API keys from secure config/env.

## Phase 3: Integration (Real Exchanges)
- [ ] **Pacifica Integration**
    - Implement `PacificaClient` using `aiohttp` and their REST API.
    - Authentication (Signature generation).
    - Fetch real balances and prices.
- [ ] **Variational Integration**
    - Implement `VariationalClient`.
    - Authentication & Market Data fetching.
- [ ] **Risk Management Checks**
    - Max position size check.
    - Spread check (don't open if spread is too high).
    - Error handling (API timeouts, rate limits).

## Phase 4: Production Readiness
- [ ] **Live Testing (Small Amounts)**
    - Execute minimal size trade on real DEXs.
    - Verify Delta Neutrality (slippage check).
- [ ] **Logging & Reporting**
    - Save trade history to CSV/JSON.
    - Visual indicators for "In Position", "Closing", "Idle".
