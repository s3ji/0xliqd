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

### Core Strategy
- **Real-time Liquidation Monitoring** - WebSocket streams for instant liquidation detection
- **VWAP-Based Entry Signals** - Combines RapidAPI price zones with real-time VWAP calculations
- **Market Regime Detection** - ADX-based trend/range filtering for optimal entry conditions
- **Momentum Filtering** - Configurable modes: AVOID_EXTREMES, ENHANCE_SIGNALS
- **Auto Trading Pairs** - Automatically builds trading pairs from available zone data

### Advanced Risk Management
- **Isolation-Based Capital Allocation** - Configurable percentage of total balance
- **Smart Position Limits** - Maximum concurrent positions with symbol-level locks
- **Volume Filtering** - Minimum 24h volume requirements for liquidity assurance
- **CCXT Pro Integration** - 95% reduction in REST API calls via WebSocket streams
- **Enhanced Timestamp Management** - Periodic calibration with automatic drift correction

### DCA System
- **Configurable DCA Levels** - Up to 7 levels with custom trigger percentages
- **Escalating Size Multipliers** - Increasing position sizes per DCA level
- **Isolation Limit Checks** - Prevents over-leveraging during adverse moves
- **Real-time TP Updates** - Automatic take-profit adjustments after DCA

### Profit Protection
- **Separate TP/SL Orders** - Independent take-profit and stop-loss management
- **Order Monitoring System** - Real-time tracking of filled orders with counterpart cancellation
- **Optional Stop-Loss Protection** - Risk management for extreme moves
- **Real-time Position Sync** - CCXT Pro position monitoring
- **P&L Tracking** - Accurate profit/loss calculations with Discord alerts

### Monitoring & Analytics
- **Comprehensive Logging** - Debug, trade, and filter logs with rotation
- **Performance Statistics** - Real-time trading metrics and analysis
- **Discord Integration** - Trade alerts, P&L notifications, and status updates
- **Health Monitoring** - Connection status and automatic reconnection
- **Enhanced Error Handling** - Graceful degradation and recovery

---

## 🛠️ Installation

### Prerequisites
- Python 3.8 or higher
- Binance Futures API account with trading permissions
- RapidAPI Pro subscription ($6/month) for liquidation data

### Quick Setup

1. **Clone the repository**
```bash
git clone https://github.com/s3ji/0xliqd.git
cd 0xliqd
```

2. **Run the setup script**
```bash
chmod +x start_bot.sh
./start_bot.sh
```

The script automatically:
- Creates and activates Python virtual environment
- Installs all required dependencies
- Creates logs directory structure
- Launches the bot

### Manual Installation

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create configuration
cp config-template.yaml config.yaml

# Edit configuration (see Configuration section)
nano config.yaml

# Run the bot
python3 0xliqd.py
```

---

## ⚙️ Configuration

### API Keys Required

1. **Binance Futures API**
   - Go to [Binance API Management](https://www.binance.com/en/my/settings/api-management)
   - Create new API key with futures trading permissions
   - Restrict to your IP address for security

2. **RapidAPI Liquidation Data**
   - Subscribe to [Liquidation Report API](https://rapidapi.com/AtsutaneDotNet/api/liquidation-report)
   - Required for real-time liquidation zones
   - Pro subscription recommended for full features

3. **Discord Webhook (Optional)**
   - Create webhook in your Discord server
   - Used for trade alerts and notifications

### Configuration File

Create `config.yaml` from the template:

```yaml
# API Configuration
api_key: ""
api_secret: ""
discord_webhook_url: ""

# Core Strategy Settings
leverage: 15                # Trading leverage
min_notional: 15            # Minimum trade size in USDT

# Pair age filtering
pair_age:
  min_age_days: 30              # Minimum age in days before trading a pair
  enable_age_filter: true       # Enable/disable the age filter
  cache_duration_hours: 24      # How long to cache age data
  api_timeout_seconds: 10       # Timeout for API requests

# RapidAPI Configuration
rapidapi:
  api_key: ""
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

# DCA System
dca:
  enable: true
  max_levels: 5

  trigger_pcts:
    - 0.04
    - 0.07
    - 0.10
    - 0.13
    - 0.16

  size_multipliers:
    - 1.5
    - 2.0
    - 2.5
    - 3.0
    - 3.5

# Profit Protection System
profit_protection:
  initial_tp_pct: 0.006       # Fixed TP
  enable_stop_loss: false     # Enable SL
  stop_loss_pct: 0.08         # Fixed SL

# Market Regime Detection
market_regime:
  adx_period: 21
  atr_period: 21
  trend_threshold: 25.0      # ADX above = trending market
  range_threshold: 18.0      # ADX below = ranging market
  volatility_multiplier: 2.0 # ATR spike detection multiplier
  regime_filter: true        # Filter trades based on market regime

# Momentum Detection
momentum:
  enable_momentum_filter: true

  # Daily thresholds
  daily_pump_threshold: 12.0    # Avoid if pumped in 24h
  daily_dump_threshold: -9.0   # Avoid if dumped in 24h

  # Hourly thresholds  
  hourly_pump_threshold: 6.0    # Avoid if pumped in 1h
  hourly_dump_threshold: -5.0   # Avoid if dumped in 1h

  # Volatility limits
  min_daily_volatility: 4.0     # Avoid if daily range
  max_daily_volatility: 45.0    # Avoid if daily range

  # Strategy mode
  momentum_mode: "ENHANCE_SIGNALS"  # AVOID_EXTREMES, ENHANCE_SIGNALS

# Risk Management
risk:
  isolation_pct: 0.35
  max_positions: 2
  min_24h_volume: 20000000   # Minimum daily volume

# System Settings
enable_discord: true
log_file: "0xliqd.log"
pairs_file: "trading_pairs_auto.json"

# Debug Settings
debug:
  enable_trade_debug: true        # Logs detailed trade info
  enable_filter_debug: true       # Logs detailed filter info
  enable_data_debug: true        # Logs detailed data info
  log_all_liquidations: true     # Log all liquidation events
  stats_interval_minutes: 15      # Stats logging interval
```

---

## 🚀 Usage

### Starting the Bot

**Recommended (using script):**
```bash
./start_bot.sh
```

**Manual start:**
```bash
source venv/bin/activate
python3 0xliqd.py
```

### Stopping the Bot
- Press `Ctrl+C` for graceful shutdown
- Bot will close all WebSocket connections and save state

### Monitoring

**Log Files:**
- `logs/0xliqd.log` - Main application log
- `logs/debug_trades.log` - Detailed trade debugging
- `price_zones_cache.json` - Cached liquidation zones
- `trading_pairs_auto.json` - Auto-generated trading pairs

**Real-time Monitoring:**
- Discord notifications (if configured)
- Console output with trade alerts
- Periodic statistics reports

---

## 📊 Strategy Overview

### How It Works

1. **Liquidation Detection**
   - Monitors Binance liquidation stream via WebSocket
   - Captures large liquidations that may cause price inefficiencies
   - Uses multiple endpoint fallbacks for reliability

2. **Multi-Layer Filtering**
   - **Pair Validation**: Only auto-generated enabled trading pairs
   - **Position Limits**: Respect maximum concurrent positions
   - **Volume Filter**: Minimum 24h volume for liquidity
   - **Momentum Filter**: Configurable modes to avoid/enhance signals
   - **Zones Validation**: Require liquidation zone data from RapidAPI
   - **Regime Filter**: Market condition appropriateness via ADX

3. **Entry Signal Generation**
   - **Long Signals**: Liquidation price ≤ Long zone level
   - **Short Signals**: Liquidation price ≥ Short zone level
   - Combines RapidAPI zones with real-time VWAP enhancement

4. **Risk Management**
   - Fixed notional per trade (configurable)
   - Isolation percentage limits total exposure
   - Separate take-profit and stop-loss orders
   - Symbol-level position locks prevent race conditions

5. **DCA System**
   - Triggers on adverse price moves with configurable thresholds
   - Escalating position sizes per level
   - Automatic order updates after DCA execution
   - Respects isolation limits to prevent over-leveraging

6. **Profit Protection**
   - Independent TP and SL order management
   - Real-time order monitoring with automatic counterpart cancellation
   - Position closure detection with P&L calculations

### Performance Metrics

The bot tracks comprehensive statistics:
- **Filter Effectiveness**: Rejection reasons and counts
- **Trade Performance**: Success/failure rates
- **DCA Analytics**: Level triggers and success rates
- **Symbol Analysis**: Most liquidated pairs
- **System Health**: WebSocket connection status and timestamp calibration

---

## 🔧 Technical Features

### CCXT Pro Integration
- **Real-time Data Streams**: Balance, positions, prices, and OHLCV
- **Optimized API Usage**: 95% reduction in REST API calls
- **Intelligent Caching**: Fresh data prioritization with fallbacks
- **Symbol Subscription**: Automatic market data subscription

### Enhanced Timestamp Management
- **Periodic Calibration**: Automatic timestamp offset correction
- **Drift Detection**: Monitors and adjusts for clock differences
- **Multiple Sampling**: Median-based calibration for accuracy
- **Safety Margins**: Conservative timing to prevent rejections

### Order Management
- **Separate TP/SL Orders**: Independent profit and loss management
- **Order Monitoring**: Real-time status tracking
- **Automatic Cancellation**: Cancel counterpart orders when one fills
- **Position Synchronization**: Exchange position verification

### Momentum Detection
- **Multi-timeframe Analysis**: 1h, 4h, and 24h momentum
- **Volatility Metrics**: Range and volume spike detection
- **Strategy Modes**: 
  - AVOID_EXTREMES: Skip overextended moves
  - ENHANCE_SIGNALS: Filter for quality setups

---

## 🔧 File Structure

```
0xliqd/
├── 0xliqd.py                 # Main bot application
├── config-template.yaml      # Configuration template
├── config.yaml              # Your configuration (create from template)
├── requirements.txt          # Python dependencies
├── start_bot.sh             # Automated setup script
├── README.md                # This documentation
├── logs/                    # Log files directory
│   ├── 0xliqd.log          # Main application log
│   └── debug_trades.log    # Detailed trade debugging
├── price_zones_cache.json   # Cached liquidation zones
└── trading_pairs_auto.json # Auto-generated trading pairs
```

---

## ⚠️ Risk Warnings

> **HIGH RISK WARNING**: This bot trades leveraged futures contracts. You can lose more than your initial investment.

### Important Considerations

- **Start Small**: Test with minimal capital first
- **Understand Leverage**: Higher leverage = higher risk
- **Monitor Actively**: Check Discord notifications and logs regularly
- **API Security**: Use IP restrictions and minimal required permissions
- **Market Conditions**: Performance varies significantly with market volatility

### Best Practices

1. **Never risk more than you can afford to lose**
2. **Use appropriate isolation percentages (50% or less recommended)**
3. **Monitor Discord alerts for trade notifications**
4. **Regularly review log files for issues**
5. **Keep the bot updated with latest versions**
6. **Test configuration changes with small amounts first**

---

## 🛡️ Security Features

- **API Key Encryption**: Store keys securely in config file
- **IP Restrictions**: Recommended for Binance API
- **Minimal Permissions**: Only futures trading required
- **Rate Limit Protection**: CCXT Pro streams reduce API pressure
- **Position Verification**: Real-time sync with exchange
- **Error Handling**: Comprehensive error recovery and logging

---

## 📈 Advanced Configuration

### Momentum Modes
- **AVOID_EXTREMES**: Skip trades on overextended price moves
- **ENHANCE_SIGNALS**: Filter for higher quality setups with volume confirmation

### Market Regime Detection
- **TREND_UP/DOWN**: Strong directional movement
- **RANGE**: Sideways price action
- **VOLATILE**: High volatility without clear direction

### Auto Trading Pairs
- Automatically generates trading pairs from available zone data
- Matches RapidAPI zones with Binance futures markets
- Configures precision and step sizes automatically

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
