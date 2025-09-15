# 0xLIQD - Advanced Liquidation Hunter Bot ⚡

<div align="center">

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CCXT Pro](https://img.shields.io/badge/CCXT%20Pro-WebSocket-green.svg)](https://github.com/s3ji/0xliqd)
[![Binance](https://img.shields.io/badge/Exchange-Binance%20Futures-orange.svg)](https://accounts.binance.com/register?ref=43408019)

**Liquidation hunting bot with CCXT Pro WebSocket integration, advanced position management, and intelligent risk controls.**

[Features](#-features) • [Installation](#-installation) • [Configuration](#-configuration) • [Usage](#-usage) • [Strategy](#-strategy) • [Contributing](#-contributing)

---

<img width="498" height="212" alt="0xliqd" src="https://github.com/user-attachments/assets/981f4016-3ede-4d72-94db-70f2c05ab0df" />

</div>

---

## 🚀 Features

### Core Strategy Engine
- **Real-time Liquidation Monitoring** - Direct Binance WebSocket streams for zero-latency detection
- **VWAP-Enhanced Entry Signals** - Combines RapidAPI liquidation zones with real-time VWAP calculations
- **Market Regime Detection** - ADX-based trend/range filtering with volatility analysis
- **Advanced Momentum Filtering** - Multi-timeframe analysis to avoid overextended moves

### Professional Risk Management
- **Strict Position Limits** - Configurable maximum concurrent positions with race condition protection
- **Isolation-Based Capital Allocation** - Percentage-based balance management with real-time monitoring
- **Multi-Confirmation System** - Prevents false position closures and premature order cleanup
- **Volume & Liquidity Filtering** - Minimum 24h volume requirements with real-time validation

### CCXT Pro Integration
- **WebSocket-First Architecture** - 95% reduction in REST API calls and rate limit issues
- **Real-time Position Sync** - Instant position updates via user data streams
- **Automatic Reconnection** - Robust connection handling with graceful failover
- **Symbol Normalization** - Unified symbol handling across all exchange formats

### Enhanced DCA System
- **Multi-Level Averaging** - Up to 7 configurable DCA levels with smart triggers
- **Dynamic Size Scaling** - Escalating position multipliers per DCA level
- **Automatic TP Updates** - Real-time take-profit recalculation after each DCA
- **Isolation Compliance** - DCA executions respect total exposure limits

### Professional Monitoring
- **Graceful Shutdown** - Clean Ctrl+C handling with proper task cleanup
- **Comprehensive Logging** - Structured logs with rotation and debug levels
- **Discord Integration** - Real-time trade alerts and P&L notifications
- **Performance Analytics** - Detailed statistics and filter effectiveness metrics

---

## 🛠️ Installation

### Prerequisites
- Python 3.8 or higher
- Binance Futures API account with trading permissions
- RapidAPI Pro subscription for liquidation data (recommended)

### Quick Setup

1. **Clone the repository**
```bash
git clone https://github.com/s3ji/0xliqd.git
cd 0xliqd
```

2. **Run the automated setup**
```bash
chmod +x start_bot.sh
./start_bot.sh
```

The script automatically:
- Creates Python virtual environment
- Installs all dependencies (including CCXT Pro)
- Sets up directory structure
- Launches the bot with proper configuration

### Manual Installation

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create configuration
cp config-template.yaml config.yaml

# Edit your configuration
nano config.yaml

# Run the bot
python3 0xliqd.py
```

---

## ⚙️ Configuration

### Required API Keys

1. **Binance Futures API**
   - Visit [Binance API Management](https://www.binance.com/en/my/settings/api-management)
   - Create API key with futures trading permissions
   - **Important**: Restrict to your IP address for security

2. **RapidAPI Liquidation Data**
   - Subscribe to [Liquidation Report API](https://rapidapi.com/AtsutaneDotNet/api/liquidation-report)
   - Pro subscription recommended for reliable data
   - Used for liquidation zone calculations

3. **Discord Webhook (Optional)**
   - Create webhook in your Discord server
   - Enables real-time trade notifications

### Enhanced Configuration

Create `config.yaml` with these settings:

```yaml
# Core API Configuration
api_key: "your_binance_api_key"
api_secret: "your_binance_api_secret"
discord_webhook_url: "your_discord_webhook_url"

# Trading Parameters
leverage: 10                # Trading leverage (1-100)
min_notional: 11            # Minimum trade size in USDT

# Enhanced Risk Management
risk:
  isolation_pct: 0.6        # 60% of balance maximum usage
  max_positions: 2          # Strict position limit with race protection
  min_24h_volume: 15000000  # $15M minimum daily volume

# RapidAPI Integration
rapidapi:
  api_key: "your_rapidapi_key"
  update_interval_minutes: 5
  enable_caching: true
  timeout_seconds: 30
  retry_attempts: 3

# VWAP Strategy Settings
vwap:
  period: 200               # VWAP calculation period
  long_offset_pct: 0.8     # Long entry offset from VWAP
  short_offset_pct: 0.8    # Short entry offset from VWAP
  use_rapidapi_zones: true # Primary zone source
  vwap_enhancement: true   # Blend with real-time VWAP

# Advanced DCA Configuration
dca:
  enable: true
  max_levels: 5            # Reduced for better risk management
  trigger_pcts: [0.05, 0.07, 0.09, 0.11, 0.13]  # DCA trigger percentages
  size_multipliers: [1.5, 2.0, 2.5, 3.0, 3.5]  # Position size multipliers

# Profit Protection System
profit_protection:
  initial_tp_pct: 0.008    # 0.8% take profit (enhanced from 0.484%)
  enable_stop_loss: false  # Optional stop loss
  stop_loss_pct: 0.02      # 2% stop loss if enabled

# Enhanced Momentum Filtering
momentum:
  enable_momentum_filter: true
  momentum_mode: "AVOID_EXTREMES"  # Strategy mode
  daily_pump_threshold: 15.0       # Avoid if >15% daily pump
  daily_dump_threshold: -10.0      # Avoid if <-10% daily dump
  hourly_pump_threshold: 8.0       # Avoid if >8% hourly pump
  hourly_dump_threshold: -6.0      # Avoid if <-6% hourly dump
  min_daily_volatility: 5.0        # Minimum daily range required
  max_daily_volatility: 50.0       # Maximum daily range allowed

# Market Regime Analysis
market_regime:
  regime_filter: true       # Enable regime-based filtering
  adx_period: 14           # ADX calculation period
  trend_threshold: 25.0    # ADX threshold for trending markets
  range_threshold: 20.0    # ADX threshold for ranging markets

# Enhanced Debug & Monitoring
debug:
  enable_trade_debug: true      # Detailed trade logging
  enable_filter_debug: true     # Filter decision logging
  enable_data_debug: true       # Data stream debugging
  log_all_liquidations: true    # Log all liquidation events
  stats_interval_minutes: 5     # Statistics reporting interval
```

---

## 🚀 Usage

### Starting the Bot

**Recommended method:**
```bash
./start_bot.sh
```

**Manual start:**
```bash
source venv/bin/activate
python3 0xliqd.py
```

### Enhanced Startup Sequence

The bot now performs comprehensive initialization:

1. **Configuration Validation** - Verifies all required settings
2. **CCXT Pro Connection** - Establishes WebSocket streams
3. **Zone Data Loading** - Fetches liquidation zones from RapidAPI
4. **Trading Pairs Building** - Auto-generates enabled pairs
5. **WebSocket Subscriptions** - Subscribes to all relevant data streams
6. **Position Monitoring** - Starts real-time position tracking
7. **Liquidation Stream** - Connects to Binance liquidation feed

### Graceful Shutdown

- Press `Ctrl+C` for clean shutdown
- All WebSocket connections closed properly
- Open orders cancelled safely
- No hanging processes or error messages

### Real-time Monitoring

**Enhanced Log Files:**
- `logs/0xliqd.log` - Main application log with structured entries
- `logs/debug_trades.log` - Detailed trade analysis and debugging
- `price_zones_cache.json` - Cached liquidation zones with timestamps
- `trading_pairs_auto.json` - Auto-generated trading pairs configuration

**Live Monitoring:**
- Console output with real-time trade signals
- Discord notifications for all trade actions
- Position limit enforcement messages
- Filter rejection statistics

---

## 📊 Enhanced Strategy

### How the Strategy Works

1. **Multi-Source Liquidation Detection**
   - Direct Binance liquidation WebSocket stream
   - Real-time processing of large liquidations
   - Symbol normalization and validation

2. **Advanced Multi-Layer Filtering**
   - **Pair Validation**: Auto-generated enabled pairs only
   - **Position Limits**: Strict enforcement with race condition protection  
   - **Volume Requirements**: Real-time 24h volume validation
   - **Momentum Analysis**: Multi-timeframe momentum calculations
   - **Zone Validation**: RapidAPI liquidation zones with caching
   - **Regime Assessment**: ADX-based market condition analysis

3. **Enhanced Entry Signal Logic**
   - **Long Signals**: Liquidation price ≤ Dynamic long zone
   - **Short Signals**: Liquidation price ≥ Dynamic short zone
   - **VWAP Integration**: Real-time VWAP blending with zones
   - **Confirmation System**: Multiple checks prevent false signals

4. **Professional Risk Management**
   - **Fixed Notional**: Consistent trade sizing
   - **Isolation Enforcement**: Real-time balance percentage monitoring
   - **Position Verification**: Multi-confirmation closure detection
   - **Rate Limit Protection**: WebSocket-first architecture

5. **Enhanced DCA System**
   - **Smart Triggers**: Percentage-based adverse move detection
   - **Dynamic Scaling**: Escalating position sizes per level
   - **TP Recalculation**: Automatic profit target updates
   - **Isolation Compliance**: Respects total exposure limits throughout

### Key Improvements in This Version

#### Position Management Fixes
- **Race Condition Protection**: Prevents multiple positions on same symbol
- **False Closure Prevention**: Multi-confirmation system before order cleanup
- **Symbol Normalization**: Consistent handling across all exchange formats
- **Max Position Enforcement**: Strict limits with proper counting

#### CCXT Pro Integration
- **WebSocket Priority**: Primary data source for positions, prices, and OHLCV
- **Rate Limit Elimination**: 95% reduction in REST API calls
- **Real-time Updates**: Instant position and balance synchronization
- **Automatic Failover**: Graceful REST API fallback when needed

#### Enhanced Reliability
- **Graceful Shutdown**: Clean Ctrl+C handling with proper cleanup
- **Connection Recovery**: Automatic WebSocket reconnection
- **Error Handling**: Comprehensive exception management
- **Data Validation**: Input sanitization and format verification

---

## 📈 Performance Features

### WebSocket Architecture Benefits
- **Zero Latency**: Direct Binance streams for instant data
- **Rate Limit Free**: WebSocket streams have no rate limits
- **Real-time Sync**: Positions and balances updated instantly
- **Reduced Errors**: Eliminates most API timeout issues

### Advanced Position Tracking
- **Multi-Confirmation Closure**: Prevents premature order cancellation
- **Symbol Lock System**: Prevents race conditions during position creation
- **Grace Period Protection**: Prevents rapid re-entries on same symbol
- **Authoritative Verification**: Cross-validates position data sources

### Enhanced Risk Controls
- **Dynamic Isolation**: Real-time balance percentage enforcement
- **Volume Validation**: Continuous liquidity requirement checking
- **Momentum Filtering**: Multi-timeframe analysis prevents bad entries
- **Regime Awareness**: Market condition-based trade filtering

---

## 🛡️ Risk Management Features

### Position Limits
- **Maximum Concurrent Positions**: Configurable limit (default: 2)
- **Symbol-Level Locks**: Prevents duplicate positions
- **Race Condition Protection**: Atomic position creation checks
- **Real-time Enforcement**: Continuous monitoring and validation

### Capital Protection
- **Isolation Percentage**: Maximum balance usage (recommended: 50-60%)
- **Dynamic Monitoring**: Real-time balance tracking
- **DCA Compliance**: All averaging respects isolation limits
- **Emergency Stops**: Position creation halts if limits exceeded

### Data Quality Assurance
- **Multi-Source Verification**: Cross-validates position data
- **Freshness Checks**: Ensures data currency before decisions
- **Error Recovery**: Graceful handling of temporary data issues
- **Backup Systems**: REST API fallback for critical operations

---

## ⚠️ Enhanced Risk Warnings

> **CRITICAL WARNING**: This is a leveraged futures trading bot. You can lose your entire account balance and more.

### Important Risk Factors

- **Leverage Amplifies Losses**: 10x leverage means 10% adverse move = 100% loss
- **Market Volatility**: Crypto futures are extremely volatile
- **Technical Risks**: Software bugs, connection issues, or exchange problems
- **Liquidation Risk**: Positions can be liquidated during extreme moves
- **Capital Requirements**: Ensure adequate margin for DCA levels

### Enhanced Safety Recommendations

1. **Start with Minimal Capital**: Test thoroughly with small amounts
2. **Conservative Isolation**: Use 50% or less of total balance
3. **Position Limits**: Start with max 1-2 concurrent positions
4. **Monitor Actively**: Check Discord alerts and logs regularly
5. **Stable Connection**: Ensure reliable internet and VPS hosting
6. **API Security**: Use IP restrictions and minimal permissions
7. **Regular Updates**: Keep bot updated with latest fixes

---

## 🔧 Technical Architecture

### File Structure
```
0xliqd/
├── 0xliqd.py                 # Main bot application (enhanced)
├── config-template.yaml      # Configuration template
├── config.yaml              # Your configuration file
├── requirements.txt          # Dependencies (includes CCXT Pro)
├── start_bot.sh             # Automated setup script
├── README.md                # This documentation
├── logs/                    # Enhanced logging directory
│   ├── 0xliqd.log          # Main application log
│   └── debug_trades.log    # Detailed trade debugging
├── price_zones_cache.json   # Cached liquidation zones
└── trading_pairs_auto.json # Auto-generated trading pairs
```

### Key Components

#### Enhanced Position Manager
- Race condition protection with atomic operations
- Multi-confirmation closure detection
- Grace period enforcement for symbol re-entries
- Symbol normalization and format handling

#### CCXT Pro Data Manager
- WebSocket-first architecture for all market data
- Real-time position and balance synchronization
- Automatic reconnection with exponential backoff
- Graceful fallback to REST API when needed

#### Advanced Risk Controller
- Real-time isolation percentage monitoring
- Dynamic position limit enforcement
- Volume and liquidity validation
- Market regime and momentum filtering

#### Professional Monitoring System
- Structured logging with rotation
- Discord integration for alerts
- Performance metrics and statistics
- Graceful shutdown with proper cleanup

---

## 🚀 Getting Started Checklist

### Pre-Launch Setup
- [ ] Create Binance Futures account with API access
- [ ] Subscribe to RapidAPI Liquidation Report (Pro recommended)
- [ ] Set up Discord webhook for notifications (optional)
- [ ] Configure VPS or stable hosting environment
- [ ] Install Python 3.8+ and required dependencies

### Configuration Steps
- [ ] Copy `config-template.yaml` to `config.yaml`
- [ ] Add Binance API credentials with IP restrictions
- [ ] Configure RapidAPI key for liquidation data
- [ ] Set conservative risk parameters (50% isolation, 2 max positions)
- [ ] Enable debug logging for initial testing
- [ ] Add Discord webhook for trade notifications

### Testing & Validation
- [ ] Run bot with minimal capital ($50-100)
- [ ] Verify position limits are respected
- [ ] Test graceful shutdown (Ctrl+C)
- [ ] Monitor Discord notifications
- [ ] Check log files for errors
- [ ] Validate DCA system with small positions

### Production Deployment
- [ ] Increase capital after successful testing
- [ ] Monitor performance for first 24-48 hours
- [ ] Adjust risk parameters based on results
- [ ] Set up log monitoring and alerts
- [ ] Create backup and recovery procedures

---

## 📞 Support & Community

### Getting Help
- **Issues**: [GitHub Issues](https://github.com/s3ji/0xliqd/issues)
- **Documentation**: This README and inline code comments

### Reporting Issues
Include these details when reporting problems:
- Python version and operating system
- Full error messages and stack traces
- Configuration file (remove sensitive keys)
- Log file excerpts showing the issue
- Steps to reproduce the problem

### Feature Requests
We welcome suggestions for improvements:
- Strategy enhancements
- Risk management features
- Performance optimizations
- Additional exchange support
- UI/UX improvements

---

## 🤝 Contributing

### Development Guidelines
1. Fork the repository
2. Create feature branch (`git checkout -b feature/enhancement`)
3. Follow existing code style and patterns
4. Add tests for new functionality
5. Update documentation as needed
6. Submit pull request with detailed description

### Priority Areas
- **Strategy Optimization**: Enhanced entry/exit logic
- **Risk Management**: Additional safety features
- **Performance**: Speed and memory optimizations
- **Testing**: Unit tests and integration tests
- **Documentation**: Tutorials and examples

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- **Binance**: Robust futures trading API and WebSocket streams
- **CCXT Pro**: Professional-grade exchange integration
- **RapidAPI**: Liquidation data services
- **Community**: Contributors and testers who help improve the bot

---

## ⚖️ Legal Disclaimer

**THIS SOFTWARE IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED. TRADING CRYPTOCURRENCY FUTURES INVOLVES SUBSTANTIAL RISK OF LOSS AND IS NOT SUITABLE FOR ALL INVESTORS. THE AUTHORS AND CONTRIBUTORS ARE NOT RESPONSIBLE FOR ANY FINANCIAL LOSSES, DAMAGES, OR OTHER CONSEQUENCES RESULTING FROM THE USE OF THIS SOFTWARE.**

**YOU ACKNOWLEDGE THAT:**
- **PAST PERFORMANCE DOES NOT GUARANTEE FUTURE RESULTS**
- **YOU MAY LOSE YOUR ENTIRE INVESTMENT AND MORE**
- **YOU TRADE AT YOUR OWN RISK AND RESPONSIBILITY**
- **YOU SHOULD CONSULT FINANCIAL PROFESSIONALS BEFORE TRADING**

**USE THIS SOFTWARE ONLY IF YOU FULLY UNDERSTAND AND ACCEPT THESE RISKS.**