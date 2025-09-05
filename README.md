# GetLiqd™ - Liquidation Hunting Trading Bot

A sophisticated algorithmic trading bot that hunts liquidation events on Binance Futures to capture market reversals. The bot monitors real-time liquidation data and executes trades based on adaptive clustering analysis, volume spikes, and technical patterns.

<img width="1891" height="965" alt="image" src="https://github.com/user-attachments/assets/243a88b8-4d37-4f48-a2ee-4c42105282e5" />

## ⚠️ Risk Warning

**This software is for educational and testing purposes only. Trading cryptocurrencies involves substantial risk of loss and is not suitable for all investors. Never trade with money you cannot afford to lose. Past performance does not guarantee future results.**

## 🎯 Strategy Overview

GetLiqd employs a multi-factor approach to identify high-probability reversal opportunities:

- **Liquidation Clustering**: Detects when multiple liquidations occur in short timeframes
- **Adaptive Thresholds**: Adjusts cluster requirements based on market volatility and liquidation size
- **Volume Spike Detection**: Confirms significant market events using statistical analysis (z-score)
- **Wick Rejection Patterns**: Identifies technical reversal signals in price action
- **Risk Management**: Built-in position sizing, leverage controls, and circuit breakers

## 📋 Features

- Real-time liquidation monitoring via Binance WebSocket
- Adaptive signal validation with configurable parameters
- Automatic position sizing based on account balance
- Comprehensive logging system with rotation
- Discord notifications for trades and alerts
- Thread-safe state management
- Circuit breakers for API resilience
- Configurable pair filtering and blacklisting

## 🚀 Quick Start

### Prerequisites

- Python 3.8+
- Binance Futures API credentials
- **RapidAPI Pro subscription ($6/month)** - Required for liquidation data
- Discord webhook (optional)

**Note**: A free alternative to the RapidAPI subscription is currently in consideration.

### Installation

1. Clone the repository:
```bash
git clone https://github.com/s3ji/getliqd.git
cd getliqd
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure your settings in `config.json` (see Configuration section below)

4. Run the bot:
```bash
python getliqd.py
```

### Running with PM2 (Recommended for VPS)

For production deployment on a VPS, PM2 provides process management, auto-restart, and monitoring:

1. Install PM2:
```bash
npm install -g pm2
```

2. Start the bot with PM2:
```bash
pm2 start getliqd.py --name "getliqd" --interpreter python3
pm2 save
pm2 startup
```

3. Useful PM2 commands:
```bash
pm2 status          # Check status
pm2 logs getliqd     # View logs
pm2 restart getliqd  # Restart bot
pm2 stop getliqd     # Stop bot
pm2 delete getliqd   # Remove from PM2
```

## ⚙️ Configuration (config.json)

### API Credentials
```json
{
  "key": "your_binance_api_key",
  "secret": "your_binance_secret_key",
  "discordwebhook": "your_discord_webhook_url",
  "rapidapi_key": "your_rapidapi_key"
}
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `key` | string | Binance API key with futures trading permissions |
| `secret` | string | Binance API secret key |
| `discordwebhook` | string | Discord webhook URL for notifications |
| `rapidapi_key` | string | RapidAPI key for [Liquidation Report API](https://rapidapi.com/AtsutaneDotNet/api/liquidation-report) (requires Pro subscription: $6/month, 750 requests/day limit) |

### Trading Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `leverage` | string | "15" | Trading leverage (1-125) |
| `maxOpenPositions` | integer | 2 | Maximum concurrent positions |
| `nominalValue` | float | 11.0 | Target position size in USD |
| `maxPosition` | integer | 100 | Maximum percentage of balance at risk |

### Position Sizing

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `auto_qty` | string | "True" | Enable automatic quantity calculation |
| `autoPercentBal` | string | "true" | Use percentage-based position sizing |
| `percentBal` | string | "0.0015" | Percentage of balance per trade |
| `longQty` | string | "0.001" | Fixed long position size (if auto disabled) |
| `shortQty` | string | "0.001" | Fixed short position size (if auto disabled) |

### Filtering & Selection

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `whitelist` | string | "" | Comma-separated list of allowed symbols |
| `blacklist` | string | "BTC,ETH,BNB..." | Comma-separated list of blocked symbols |
| `min_age_days` | integer | 14 | Minimum trading pair age in days |
| `min_24h_volume` | integer | 5000000 | Minimum 24h volume in USD |

### DCA (Dollar Cost Averaging)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enableDca` | boolean | false | Enable DCA functionality |
| `dcaOne` | string | "25" | First DCA trigger (% loss) |
| `factorOne` | string | "2" | First DCA size multiplier |
| `dcaTwo` | string | "35" | Second DCA trigger (% loss) |
| `factorTwo` | string | "6" | Second DCA size multiplier |

### Exit Strategy

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enableExitOrders` | boolean | false | Enable automatic TP/SL orders |
| `takeProfitPercentage` | float | 0.484 | Primary take profit percentage |
| `secondaryTakeProfitPercentage` | float | 0.348 | Secondary TP for DCA positions |
| `stopLossPercentage` | float | 200 | Stop loss percentage |
| `useStopLoss` | boolean | false | Enable stop loss orders |

### Emergency Controls

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `useEmergencyExit` | boolean | true | Enable emergency exit conditions |
| `emergencyTriggerPercentage` | float | 20.0 | Account risk threshold for emergency |
| `emergencyProfitPercentage` | float | 0.242 | Emergency exit profit target |

### Scheduling & Monitoring

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `update_coins_interval_minutes` | integer | 5 | Liquidation levels update frequency |
| `poll_status_interval_minutes` | integer | 60 | Discord status update frequency |
| `protection_interval_seconds` | integer | 30 | Position protection check interval |
| `pnl_monitor_interval_seconds` | integer | 5 | PnL monitoring frequency |
| `update_volume_interval_hours` | integer | 24 | Volume data update frequency |

## 📊 Signal Logic

The bot uses a multi-factor validation system:

1. **Basic Filters**: Blacklist, volume, age, pump detection
2. **Adaptive Clustering**: Dynamic liquidation cluster requirements based on:
   - Market volatility (z-score ≥ 3.0 → requires 3 liquidations)
   - Large liquidation size (4x+ mean → requires 1 liquidation)
   - Medium liquidation size (2.5x+ mean → requires 2 liquidations)
   - Normal conditions → requires 3 liquidations
3. **Additional Confirmation**: Requires at least 1 of:
   - Volume spike (z-score ≥ 2.5)
   - Wick rejection pattern

## 📁 File Structure

```
getliqd/
├── getliqd.py              # Main bot script
├── config.json             # Configuration file
├── requirements.txt        # Python dependencies
├── logs/                   # Log files directory
│   ├── trades.log         # Trade execution logs
│   ├── errors.log         # Error logs
│   ├── system.log         # System logs
│   └── performance.log    # Performance metrics
├── liquidation_levels.json # Cached liquidation data
└── pair_age.json          # Cached pair age data
```

## 🔧 Logging System

The bot maintains comprehensive logs with automatic rotation:

- **Trade Logs**: 7 days retention, all executed trades
- **Error Logs**: 14 days retention, debugging information  
- **System Logs**: 10MB max size, 5 backup files
- **Performance Logs**: 3 days retention, bot metrics

## 🐛 Known Issues

### Current Limitations

- **TP/SL Order Placement**: Take profit and stop loss orders may fail to place correctly on some symbols due to Binance API precision requirements
- **Discord Notifications**: Occasional failures in Discord webhook delivery during high volatility periods
- **WebSocket Reconnection**: May experience brief delays during WebSocket reconnection cycles

### Troubleshooting

1. **Bot not taking trades**: Check liquidation signal logs and ensure liquidation levels are populated
2. **High memory usage**: Reduce log retention periods in the configuration
3. **API errors**: Verify API permissions include futures trading and ensure rate limits aren't exceeded

## ⚖️ Legal Disclaimer

This software is provided "as is" without warranties. Trading involves substantial risk and may result in complete loss of capital. Users are responsible for:

- Understanding local regulations regarding algorithmic trading
- Ensuring compliance with tax obligations
- Managing their own risk tolerance and position sizing
- Monitoring bot performance and intervening when necessary

The developers assume no responsibility for trading losses, technical failures, or regulatory compliance issues.

## 📈 Performance Monitoring

Monitor your bot's performance through:

- Discord status updates (configurable intervals)
- Log file analysis in the `/logs` directory
- Binance account interface for real-time positions
- Performance metrics in `performance.log`

## 🛡️ Security Best Practices

- Use API keys with minimal required permissions
- Enable IP restrictions on Binance API keys
- Regularly rotate API credentials
- Monitor for unusual trading activity
- Keep the bot updated with latest security patches

## 📞 Support

For technical support and updates:
- Check the Issues section of this repository
- Review log files for error messages
- Ensure configuration parameters are within valid ranges
- Test with small position sizes before scaling up

---

**Remember: Only trade with capital you can afford to lose. This bot is a tool that requires active monitoring and risk management.**
