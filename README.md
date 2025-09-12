# 0xLIQD – Liquidation Hunter Bot 🎯

**0xLIQD** is a real-time liquidation hunting bot for Binance Futures. It monitors liquidation streams, calculates VWAP zones, and executes automated entries with DCA, fixed take-profit, and optional stop-loss.

## 🚀 Features

**Core Strategy**

- Liquidation stream listener via Binance WebSocket
- VWAP-based entry signals (Price zones \& real-time VWAP)
- Market-regime filtering (ADX-based trend/range detection)
- Auto-build trading pairs from live zone data

**Risk Management**

- Isolation-based capital allocation
- Configurable max concurrent positions
- Volume filter (min. 24h volume requirement)

**Smart DCA**

- Configurable DCA levels
- Adverse-move trigger percentages
- Escalating size multipliers per level

**Profit Protection**

- Fixed take-profit order (configurable %)
- Optional fixed stop-loss order (configurable %)
- Real-time position synchronization
- P\&L notifications via Discord

**Monitoring \& Logging**

- Detailed debug and filter logs
- Periodic performance statistics
- Discord trade and P\&L alerts

## 🛠️ Installation

1. **Clone repository**

```bash
git clone https://github.com/s3ji/0xliqd.git
cd 0xliqd
```

2. **Prepare virtual environment \& dependencies**

```bash
chmod +x start_bot.sh
./start_bot.sh
```

The script will:
  - Create/activate `venv`
  - Install packages from `requirements.txt`
  - Initialize `logs/` directory
  - Launch the bot

**Note: This requires a RapidAPI Pro subscription ($6/month)** - Required for liquidation data
  - https://rapidapi.com/AtsutaneDotNet/api/liquidation-report

## ⚙️ Configuration

Create new `config.yaml` or copy `config-template.yaml`:

```yaml
# API Configuration
api_key: "<BINANCE FUTURES API KEY HERE>"
api_secret: "<BINANCE FUTURES SECRET KEY HERE>"
discord_webhook_url: "<DISCORD WEBHOOK URL HERE>"

# Core Strategy Settings
risk_pct: 0.05              # Risk per trade
leverage: 10                # Trading leverage
min_notional: 11            # Minimum trade size in USDT

# RapidAPI Configuration
rapidapi:
  api_key: "<RAPIDAPI API KEY HERE>"
  base_url: "https://liquidation-report.p.rapidapi.com"
  endpoint: "/lickhunterpro"
  update_interval_minutes: 5          # Fetch fresh data every 5 minutes
  timeout_seconds: 30                 # API request timeout
  retry_attempts: 3                   # Number of retry attempts
  retry_delay: 5.0                    # Delay between retries (seconds)
  enable_caching: true                # Cache data locally
  cache_file: "price_zones_cache.json"

# VWAP Configuration
vwap:
  period: 200                # VWAP calculation period
  long_offset_pct: 0.8       # Default long offset percentage
  short_offset_pct: 0.8      # Default short offset percentage
  use_rapidapi_zones: true   # Use RapidAPI zones as primary signal
  vwap_enhancement: true     # Enhance with real-time VWAP

# Smart DCA System
dca:
  enable: true
  max_levels: 7              # Maximum DCA levels (user configurable)
  
  # DCA trigger percentages (adverse move %)
  trigger_pcts:
    - 0.05  # DCA level 1
    - 0.07  # DCA level 2
    - 0.09  # DCA level 3
    - 0.11  # DCA level 4
    - 0.13  # DCA level 5
    - 0.15  # DCA level 6
    - 0.17  # DCA level 7
  
  # DCA size multipliers
  size_multipliers:
    - 2.0    # Level 1
    - 3.0    # Level 2
    - 4.0    # Level 3
    - 5.0    # Level 4
    - 6.0    # Level 5
    - 7.0    # Level 6
    - 8.0    # Level 7

# Profit Protection System
profit_protection:
  initial_tp_pct: 0.00484    # Fixed TP
  enable_stop_loss: false    # Enable SL
  stop_loss_pct: 0.02        # Fixed SL

# Market Regime Detection
market_regime:
  adx_period: 14
  atr_period: 14
  trend_threshold: 25.0      # ADX above = trending market
  range_threshold: 20.0      # ADX below = ranging market
  volatility_multiplier: 2.0 # ATR spike detection multiplier
  regime_filter: true        # Filter trades based on market regime

# Risk Management
risk:
  isolation_pct: 1.0
  max_positions: 2
  min_24h_volume: 10000000   # Minimum $10M daily volume

# System Settings
enable_discord: true
log_file: "0xliqd.log"
pairs_file: "trading_pairs_auto.json"

# Debug Settings
debug:
  enable_trade_debug: true    # Logs detailed trade info
  enable_filter_debug: true   # Logs detailed filter info
  enable_data_debug: true     # Logs detailed data info
  log_all_liquidations: true  # Log all liquidation events
  stats_interval_minutes: 60  # Stats logging interval
```

## 🏃 Usage

- **Quick start:**

```bash
./start_bot.sh
```

- **Manual start:**

```bash
source venv/bin/activate
pip install -r requirements.txt
python3 0xliqd.py
```

Stop with `Ctrl+C`. Logs are stored in `logs/`.

## 📁 File Structure

```
0xliqd/
├── 0xliqd.py            # Main bot script
├── start_bot.sh         # Setup & launch script
├── config.yaml          # User configuration
├── requirements.txt     # Python dependencies
├── logs/                # Log files
│   ├── 0xliqd.log
│   └── debug_trades.log
├── price_zones_cache.json   # Price zones cache
└── trading_pairs_auto.json  # Auto-built pairs
```

## 📊 Trading Workflow

1. **Startup**
    - Load configuration
    - Connect to Binance \& RapidAPI
    - Auto-build pair list
2. **Liquidation Monitoring**
    - Listen for liquidation events
    - Apply filters (volume, zones, regime, max positions)
3. **Entry Execution**
    - Place market entry (fixed notional)
    - Set TP (and SL if enabled)
4. **Position Management**
    - Sync positions in real time
    - Execute DCA on adverse moves
    - Update TP/SL orders after DCA
5. **Exit \& Notifications**
    - Detect position closures
    - Send P\&L alerts via Discord
    - Log stats and performance

## ⚠️ Warnings \& Best Practices

- **High Risk:** Leverage amplifies both gains and losses.
- **API Security:** Restrict IP, use only needed permissions.
- **Capital Allocation:** Never allocate more than you can afford to lose.
- **Testing:** Validate on small balances or testnet first.

## 🤝 Contributing

1. Fork and branch
2. Implement feature/fix
3. Add tests/documentation
4. Submit pull request

## 📄 License

This project is licensed under the MIT License.

**USE AT YOUR OWN RISK**