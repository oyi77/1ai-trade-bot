# ADR 001: Hexagonal Architecture

## Status
Accepted

## Context
The multi-exchange trading bot codebase has been restructured to follow hexagonal architecture (also known as ports and adapters architecture). This decision was made to achieve:

- **Testability**: Core business logic can be tested without external dependencies
- **Flexibility**: Exchange providers, data sources, and strategies can be swapped without modifying core logic
- **Maintainability**: Clear separation of concerns with well-defined boundaries
- **Extensibility**: New exchanges, strategies, and data providers can be added as plugins

## Decision
The codebase is organized into 5 layers following hexagonal architecture principles:

### Layer Structure

```
trading_bot/
├── core/                    # Domain + Application layer
│   ├── interfaces/          # Ports (6 ABCs)
│   ├── models/              # Value objects
│   └── errors/              # Domain errors
├── exchange/                # Infrastructure - Adapters
│   ├── cex/                 # Centralized exchange adapters
│   ├── deriv/               # Deriv exchange
│   └── dex/                 # DEX adapters
│       ├── abi/             # Contract ABIs
│       ├── chain/           # Chain-specific providers
│       └── services/        # DEX services
├── data/                    # Infrastructure - Data
│   └── providers/           # Data provider adapters
├── strategy/                # Application layer
│   ├── cex/                 # CEX strategies
│   └── dex/                 # DEX strategies
├── risk/                    # Application layer
│   └── rules/               # Risk rules
├── interface/               # Presentation layer
│   ├── cli.py, tui_config.py, setup_wizard.py
│   └── base.py
├── ui/                      # Presentation layer
│   ├── cli/
│   └── tui/
└── utils/                   # Utilities
```

### Layer Descriptions

#### 1. Domain Layer (`core/models/`)
Contains pure business entities with no external dependencies:

- **Order**: `Order`, `OrderSide`, `OrderType`, `OrderStatus`
- **Position**: `Position`, `PositionSide`, `PositionStatus`
- **Market Data**: `Candle`, `OrderBook`, `MarketData`
- **Wallet**: `WalletBalance`, `WalletProfile`

These are value objects that represent the core domain concepts.

#### 2. Application Layer (`core/interfaces/`)
Defines 6 port interfaces (Abstract Base Classes) that define contracts:

- **IExchangeProvider**: Exchange operations (connect, place_order, cancel_order, get_balance, etc.)
- **IStrategy**: Strategy execution (analyze, generate_signals, on_tick, on_order_update)
- **IDataProvider**: Market data retrieval (fetch_candles, fetch_orderbook, subscribe)
- **IWalletManager**: Wallet operations (get_balance, transfer, approve)
- **IRiskManager**: Risk checks (validate_order, check_position_limits, check_drawdown)

#### 3. Infrastructure Layer (`exchange/`, `data/`)
Implements adapters for external systems:

- **Exchange Adapters**:
  - `cex/`: Centralized exchange implementations (Bybit, Binance, etc.)
  - `deriv/`: Deriv-specific implementation
  - `dex/`: DEX implementations with sub-structure:
    - `abi/`: Smart contract ABIs
    - `chain/`: Chain-specific providers (Ethereum, BSC, etc.)
    - `services/`: DEX-specific services (Uniswap, PancakeSwap, etc.)

- **Data Adapters** (`data/providers/`):
  - Market data providers
  - Historical data fetchers
  - Real-time data streams

#### 4. Presentation Layer (`interface/`, `ui/`)
User-facing components:

- **CLI**: Command-line interface
- **TUI**: Terminal user interface
- **Setup Wizard**: Configuration and onboarding

#### 5. Test Layer (`tests/`)
Test suites organized by layer and component.

### Dependency Rule
**Outer layers depend on inner layers, never inward.**

```
Presentation → Application → Domain
Infrastructure → Application → Domain
Test → All layers
```

- Domain layer has NO dependencies on other layers
- Application layer depends only on Domain layer
- Infrastructure layer depends on Application and Domain layers
- Presentation layer depends on Application and Domain layers
- Test layer can depend on all layers

### Port Interface Contracts

#### IExchangeProvider
```python
class IExchangeProvider(ABC):
    """Exchange operations port"""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to exchange"""

    @abstractmethod
    async def place_order(self, order: Order) -> Order:
        """Place an order on the exchange"""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order"""

    @abstractmethod
    async def get_balance(self) -> WalletBalance:
        """Get current wallet balance"""

    @abstractmethod
    async def get_positions(self) -> List[Position]:
        """Get all open positions"""

    @abstractmethod
    async def get_orderbook(self, symbol: str) -> OrderBook:
        """Get current order book"""
```

#### IStrategy
```python
class IStrategy(ABC):
    """Strategy execution port"""

    @abstractmethod
    async def analyze(self, market_data: MarketData) -> None:
        """Analyze market data"""

    @abstractmethod
    async def generate_signals(self) -> List[Order]:
        """Generate trading signals"""

    @abstractmethod
    async def on_tick(self, candle: Candle) -> None:
        """Handle new candle tick"""

    @abstractmethod
    async def on_order_update(self, order: Order) -> None:
        """Handle order status updates"""
```

#### IDataProvider
```python
class IDataProvider(ABC):
    """Market data retrieval port"""

    @abstractmethod
    async def fetch_candles(self, symbol: str, timeframe: str, limit: int) -> List[Candle]:
        """Fetch historical candle data"""

    @abstractmethod
    async def fetch_orderbook(self, symbol: str) -> OrderBook:
        """Fetch current order book"""

    @abstractmethod
    async def subscribe(self, symbol: str, callback: Callable) -> None:
        """Subscribe to real-time data updates"""
```

#### IWalletManager
```python
class IWalletManager(ABC):
    """Wallet operations port"""

    @abstractmethod
    async def get_balance(self) -> WalletBalance:
        """Get current wallet balance"""

    @abstractmethod
    async def transfer(self, to_address: str, amount: Decimal) -> str:
        """Transfer funds to another address"""

    @abstractmethod
    async def approve(self, spender: str, amount: Decimal) -> str:
        """Approve token spending for DEX operations"""
```

#### IRiskManager
```python
class IRiskManager(ABC):
    """Risk management port"""

    @abstractmethod
    async def validate_order(self, order: Order) -> bool:
        """Validate order against risk rules"""

    @abstractmethod
    async def check_position_limits(self, position: Position) -> bool:
        """Check if position exceeds limits"""

    @abstractmethod
    async def check_drawdown(self) -> bool:
        """Check if current drawdown exceeds threshold"""
```

### Plugin Discovery Mechanism

Exchange adapters and strategies are discovered dynamically:

1. **Exchange Adapters**: Located in `exchange/cex/`, `exchange/deriv/`, `exchange/dex/`
   - Each adapter implements `IExchangeProvider`
   - Discovered via module introspection
   - Registered in exchange registry

2. **Strategies**: Located in `strategy/cex/` and `strategy/dex/`
   - Each strategy implements `IStrategy`
   - Discovered via module introspection
   - Registered in strategy registry

3. **Data Providers**: Located in `data/providers/`
   - Each provider implements `IDataProvider`
   - Discovered via module introspection
   - Registered in data provider registry

### Data Flow

```
┌─────────────┐
│   UI/CLI    │  Presentation Layer
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────────────────────┐
│                    Application Layer                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   Strategy   │  │ Risk Manager │  │Wallet Manager│  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
└─────────┼──────────────────┼──────────────────┼──────────┘
          │                  │                  │
          └──────────────────┼──────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│                    Domain Layer                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │  Order   │  │ Position │  │  Candle  │  │  Wallet  │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
└─────────────────────────────────────────────────────────┘
                             ▲
                             │
┌─────────────────────────────────────────────────────────┐
│                 Infrastructure Layer                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   Exchange   │  │Data Provider │  │  Blockchain  │  │
│  │   Adapters   │  │   Adapters   │  │   Services   │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### Naming Conventions

#### Layer-Specific Conventions

**Domain Layer (`core/models/`)**:
- Classes: PascalCase (e.g., `Order`, `Position`, `Candle`)
- Enums: PascalCase (e.g., `OrderSide`, `OrderStatus`)
- Files: snake_case (e.g., `order.py`, `position.py`)

**Application Layer (`core/interfaces/`)**:
- Interfaces: PascalCase with `I` prefix (e.g., `IExchangeProvider`, `IStrategy`)
- Files: snake_case (e.g., `exchange_provider.py`, `strategy.py`)

**Infrastructure Layer (`exchange/`, `data/`)**:
- Adapters: PascalCase with exchange name (e.g., `BybitExchange`, `BinanceExchange`)
- DEX services: PascalCase (e.g., `UniswapService`, `PancakeSwapService`)
- Files: snake_case (e.g., `bybit_exchange.py`, `uniswap_service.py`)

**Strategy Layer (`strategy/`)**:
- Strategies: PascalCase with strategy type (e.g., `GridStrategy`, `HedgingStrategy`)
- Files: snake_case (e.g., `grid_strategy.py`, `hedging_strategy.py`)

**Presentation Layer (`interface/`, `ui/`)**:
- Components: PascalCase (e.g., `CLI`, `TUI`, `SetupWizard`)
- Files: snake_case (e.g., `cli.py`, `tui_config.py`)

#### Sub-Module Conventions

**DEX Structure**:
- `exchange/dex/abi/`: Contract ABI files (JSON)
- `exchange/dex/chain/`: Chain-specific providers (e.g., `ethereum_provider.py`)
- `exchange/dex/services/`: DEX services (e.g., `uniswap_service.py`)

**Strategy Split**:
- `strategy/cex/`: CEX-specific strategies
- `strategy/dex/`: DEX-specific strategies

**Risk Rules**:
- `risk/rules/`: Individual risk rule implementations

## Consequences

### Positive
- **Testability**: Core business logic can be unit tested with mock adapters
- **Flexibility**: New exchanges, strategies, and data providers can be added without modifying core logic
- **Maintainability**: Clear separation of concerns makes the codebase easier to understand and modify
- **Extensibility**: Plugin architecture allows for dynamic discovery and registration
- **Isolation**: External dependencies (exchanges, blockchains) are isolated in infrastructure layer

### Negative
- **Complexity**: Additional abstraction layers increase initial complexity
- **Boilerplate**: Need to implement interfaces for each adapter
- **Learning Curve**: Developers need to understand hexagonal architecture principles

### Mitigations
- Clear documentation and examples for implementing new adapters
- Code generators or templates for common adapter patterns
- Comprehensive test coverage to validate layer boundaries

## References
- Hexagonal Architecture: https://alistair.cockburn.us/hexagonal-architecture/
- Ports and Adapters: https://herbertograca.com/2017/09/14/ports-adapters-architecture/
- Clean Architecture: https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html