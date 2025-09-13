# 0xLIQD - Advanced Liquidation Hunter Bot ⚡

<div align="center">

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![WebSocket](https://img.shields.io/badge/WebSocket-Real--time-green.svg)](https://github.com/s3ji/0xliqd)
[![Binance](https://img.shields.io/badge/Exchange-Binance%20Futures-orange.svg)](https://accounts.binance.com/register?ref=43408019)

**Real-time liquidation hunting bot for Binance Futures with WebSocket integration, advanced risk management, and intelligent momentum filtering.**

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
- **Momentum Filtering** - Avoid overextended moves and target quality setups

### Advanced Risk Management
- **Isolation-Based Capital Allocation** - Configurable percentage of total balance
- **Smart Position Limits** - Maximum concurrent positions with symbol-level locks
- **Volume Filtering** - Minimum 24h volume requirements for liquidity assurance
- **WebSocket Rate Limit Protection** - Eliminates 95% of REST API calls

### DCA System
- **Configurable DCA Levels** - Up to 7 levels with custom trigger percentages
- **Escalating Size Multipliers** - Increasing position sizes per DCA level
- **Isolation Limit Checks** - Prevents over-leveraging during adverse moves
- **Real-time TP Updates** - Automatic take-profit adjustments after DCA

### Profit Protection
- **Fixed Take-Profit Orders** - Configurable percentage-based exits
- **Optional Stop-Loss Protection** - Risk management for extreme moves
- **Real-time Position Sync** - WebSocket-based position monitoring
- **P&L Tracking** - Accurate profit/loss calculations with Discord alerts

### Monitoring & Analytics
- **Comprehensive Logging** - Debug, trade, and filter logs with rotation
- **Performance Statistics** - Real-time trading metrics and analysis
- **Discord Integration** - Trade alerts, P&L notifications, and status updates
- **WebSocket Health Monitoring** - Connection status and automatic reconnection

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
api_key: "your_binance_api_key"
api_secret: "your_binance_api_secret"
discord_webhook_url: "your_discord_webhook_url"

# Core Trading Settings
leverage: 10                # Trading leverage (1-100)
min_notional: 11            # Minimum trade size in USDT
risk:
  isolation_pct: 1.0        # Maximum % of balance to use
  max_positions: 2          # Maximum concurrent positions
  min_24h_volume: 10000000  # Minimum $10M daily volume

# RapidAPI Configuration
rapidapi:
  api_key: "your_rapidapi_key"
  update_interval_minutes: 5
  enable_caching: true

# VWAP Strategy Settings
vwap:
  period: 200               # VWAP calculation period
  use_rapidapi_zones: true  # Use RapidAPI zones as primary
  vwap_enhancement: true    # Blend with real-time VWAP

# DCA Configuration
dca:
  enable: true
  max_levels: 7
  trigger_pcts: [0.05, 0.07, 0.09, 0.11, 0.13, 0.15, 0.17]
  size_multipliers: [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]

# Profit Protection
profit_protection:
  initial_tp_pct: 0.00484   # 0.484% take profit
  enable_stop_loss: false   # Optional stop loss
  stop_loss_pct: 0.02       # 2% stop loss if enabled

# Momentum Filtering
momentum:
  enable_momentum_filter: true
  momentum_mode: "AVOID_EXTREMES"  # AVOID_EXTREMES, TARGET_MOMENTUM, ENHANCE_SIGNALS
  daily_pump_threshold: 12.0       # Avoid if >12% daily move
  daily_dump_threshold: -12.0      # Avoid if <-12% daily move
  hourly_pump_threshold: 5.0       # Avoid if >5% hourly move
  hourly_dump_threshold: -5.0      # Avoid if <-5% hourly move
  min_daily_volatility: 3.0        # Minimum daily range
  max_daily_volatility: 30.0       # Maximum daily range

# Market Regime Detection
market_regime:
  regime_filter: true       # Enable regime filtering
  trend_threshold: 25.0     # ADX threshold for trending markets
  range_threshold: 20.0     # ADX threshold for ranging markets

# Debug Settings
debug:
  enable_trade_debug: true
  enable_filter_debug: true
  log_all_liquidations: true
  stats_interval_minutes: 60
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

2. **Multi-Layer Filtering**
   - **Pair Validation**: Only enabled trading pairs
   - **Position Limits**: Respect maximum concurrent positions
   - **Volume Filter**: Minimum 24h volume for liquidity
   - **Momentum Filter**: Avoid overextended price moves
   - **Zones Validation**: Require liquidation zone data
   - **Regime Filter**: Market condition appropriateness

3. **Entry Signal Generation**
   - **Long Signals**: Liquidation price ≤ Long zone level
   - **Short Signals**: Liquidation price ≥ Short zone level
   - Combines RapidAPI zones with real-time VWAP

4. **Risk Management**
   - Fixed notional per trade (configurable)
   - Isolation percentage limits total exposure
   - Immediate take-profit orders
   - Optional stop-loss protection

5. **DCA System**
   - Triggers on adverse price moves
   - Escalating position sizes per level
   - Automatic take-profit updates
   - Respects isolation limits

### Performance Metrics

The bot tracks comprehensive statistics:
- **Filter Effectiveness**: Rejection reasons and counts
- **Trade Performance**: Success/failure rates
- **DCA Analytics**: Level triggers and success rates
- **Symbol Analysis**: Most liquidated pairs
- **System Health**: WebSocket connection status

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
- **Monitor Actively**: Don't leave the bot unattended for extended periods
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
- **Rate Limit Protection**: WebSocket-based data reduces API calls
- **Position Verification**: Real-time sync with exchange
- **Error Handling**: Comprehensive error recovery

---

## 📈 Advanced Features

### WebSocket Integration
- **User Data Stream**: Real-time position and balance updates
- **Market Data Stream**: Live price, volume, and kline data
- **Automatic Reconnection**: Handles connection drops gracefully
- **Rate Limit Elimination**: 95% reduction in REST API calls

### Momentum Detection
- **Multiple Timeframes**: 1h, 4h, and 24h momentum analysis
- **Volatility Metrics**: Daily range and volume spike detection
- **Strategy Modes**: Avoid extremes, target momentum, or enhance signals
- **Real-time Calculation**: Uses WebSocket kline data

### Market Regime Analysis
- **ADX-Based Detection**: Trend vs range market identification
- **Regime Filtering**: Trade only in favorable conditions
- **Dynamic Thresholds**: Configurable trend/range boundaries

---

## 🤝 Contributing

We welcome contributions to improve 0xLIQD! Here's how to get involved:

### Development Setup

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Add tests if applicable
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

### Areas for Contribution

- **Strategy Improvements**: Enhanced entry/exit logic
- **Risk Management**: Additional safety features
- **Performance Optimization**: Speed and memory improvements
- **Documentation**: Tutorials and examples
- **Testing**: Unit tests and integration tests

---

## 📞 Support

### Getting Help

- **Issues**: [GitHub Issues](https://github.com/s3ji/0xliqd/issues)
- **Discussions**: [GitHub Discussions](https://github.com/s3ji/0xliqd/discussions)
- **Documentation**: Check this README and code comments

### Reporting Bugs

When reporting bugs, please include:
- Python version and OS
- Full error messages and stack traces
- Configuration (remove sensitive keys)
- Steps to reproduce the issue

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- **Binance**: For providing robust futures trading API
- **RapidAPI**: For liquidation data services
- **CCXT**: For cryptocurrency exchange integration
- **WebSocket Libraries**: For real-time data streaming

---

## ⚖️ Disclaimer

THIS SOFTWARE IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND. TRADING CRYPTOCURRENCY FUTURES INVOLVES SUBSTANTIAL RISK OF LOSS. THE AUTHORS ARE NOT RESPONSIBLE FOR ANY FINANCIAL LOSSES INCURRED THROUGH THE USE OF THIS SOFTWARE. USERS TRADE AT THEIR OWN RISK.