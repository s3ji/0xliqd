import asyncio
import ccxt.async_support as ccxt
import websockets
import aiohttp
import json
import time
import logging
from logging.handlers import RotatingFileHandler
import os
import numpy as np
from datetime import datetime, timedelta
from collections import deque
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
import yaml
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class RapidAPIConfig:
    """RapidAPI configuration for liquidation zones"""
    api_key: str = ""
    base_url: str = "https://liquidation-report.p.rapidapi.com"
    endpoint: str = "/lickhunterpro"
    update_interval_minutes: int = 5
    timeout_seconds: int = 30
    retry_attempts: int = 3
    retry_delay: float = 5.0
    enable_caching: bool = True
    cache_file: str = "price_zones_cache.json"

@dataclass
class VWAPConfig:
    """VWAP calculation and offset configuration"""
    period: int = 200
    long_offset_pct: float = 0.8
    short_offset_pct: float = 0.8
    use_rapidapi_zones: bool = True
    vwap_enhancement: bool = True

@dataclass
class SmartDCAConfig:
    """Smart DCA system configuration"""
    enable: bool = True
    max_levels: int = 7
    trigger_pcts: List[float] = field(default_factory=lambda: [0.05, 0.07, 0.09, 0.11, 0.13, 0.15, 0.17])

@dataclass
class ProfitProtectionConfig:
    """Profit protection configuration"""
    initial_tp_pct: float = 0.005
    enable_stop_loss: bool = False  
    stop_loss_pct: float = 0.02

@dataclass
class MarketRegimeConfig:
    """Market regime detection configuration"""
    adx_period: int = 14
    atr_period: int = 14
    trend_threshold: float = 25.0
    range_threshold: float = 20.0
    volatility_multiplier: float = 2.0
    regime_filter: bool = True

@dataclass
class RiskConfig:
    """Risk management configuration"""
    isolation_pct: float = 0.50  # Use 50% of balance max
    max_positions: int = 2
    min_24h_volume: int = 10000000

@dataclass
class DebugConfig:
    """Debug and logging configuration"""
    enable_trade_debug: bool = True
    enable_filter_debug: bool = True
    enable_data_debug: bool = True
    log_all_liquidations: bool = True
    stats_interval_minutes: int = 5

@dataclass
class Config:
    """Main configuration class"""
    # API Configuration
    api_key: str = ""
    api_secret: str = ""
    discord_webhook_url: str = ""
    
    # Core Parameters
    leverage: int = 10
    min_notional: float = 11.0
    
    # Strategy Components
    rapidapi: RapidAPIConfig = field(default_factory=RapidAPIConfig)
    vwap: VWAPConfig = field(default_factory=VWAPConfig)
    dca: SmartDCAConfig = field(default_factory=SmartDCAConfig)
    profit_protection: ProfitProtectionConfig = field(default_factory=ProfitProtectionConfig)
    market_regime: MarketRegimeConfig = field(default_factory=MarketRegimeConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)
    
    # System Settings
    enable_discord: bool = True
    log_file: str = "0xliqd.log"
    pairs_file: str = "trading_pairs_auto.json"

def load_config() -> Config:
    """Load configuration from config.yaml ONLY"""
    config = Config()
    
    # Load from config file
    if Path("config.yaml").exists():
        try:
            with open("config.yaml", 'r') as f:
                file_config = yaml.safe_load(f)
            
            if file_config:
                # Update main config
                for key, value in file_config.items():
                    if hasattr(config, key) and not isinstance(getattr(config, key),
                        (RapidAPIConfig, VWAPConfig, SmartDCAConfig, ProfitProtectionConfig, MarketRegimeConfig, RiskConfig, DebugConfig)):
                        setattr(config, key, value)
                
                # Update sub-configs
                for sub_config_name in ['rapidapi', 'vwap', 'dca', 'profit_protection', 'market_regime', 'risk', 'debug']:
                    if sub_config_name in file_config:
                        sub_config = getattr(config, sub_config_name)
                        for key, value in file_config[sub_config_name].items():
                            if hasattr(sub_config, key):
                                setattr(sub_config, key, value)
            
            print("✅ Configuration loaded from config.yaml")
        except Exception as e:
            print(f"❌ Error loading config: {e}")
    else:
        print("❌ config.yaml not found!")
        exit(1)
    
    return config

CONFIG = load_config()

# =============================================================================
# LOGGING SETUP
# =============================================================================

def setup_enhanced_logging():
    """Setup enhanced logging"""
    if not os.path.exists("logs"):
        os.makedirs("logs")
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Clear existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Main log file
    file_handler = RotatingFileHandler(
        filename=os.path.join("logs", CONFIG.log_file),
        maxBytes=20 * 1024 * 1024,
        backupCount=10
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)-7s | %(funcName)-25s | %(message)s'
    ))
    logger.addHandler(file_handler)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)-7s %(message)s'
    ))
    logger.addHandler(console_handler)
    
    # Debug-specific file
    if CONFIG.debug.enable_trade_debug:
        debug_handler = RotatingFileHandler(
            filename=os.path.join("logs", "debug_trades.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5
        )
        debug_handler.setLevel(logging.DEBUG)
        debug_handler.setFormatter(logging.Formatter(
            '%(asctime)s | DEBUG | %(funcName)-25s | %(message)s'
        ))
        debug_logger = logging.getLogger('trade_debug')
        debug_logger.setLevel(logging.DEBUG)
        debug_logger.addHandler(debug_handler)

# =============================================================================
# STATISTICS TRACKING
# =============================================================================

class TradingStatistics:
    """Trading statistics and performance tracking"""
    
    def __init__(self):
        self.reset_stats()
    
    def reset_stats(self):
        """Reset all statistics"""
        self.start_time = time.time()
        self.total_liquidations_seen = 0
        self.liquidations_by_symbol = {}
        self.filter_rejections = {
            'pair_not_enabled': 0,
            'existing_position': 0,
            'max_positions': 0,
            'volume_filter': 0,
            'no_zones': 0,
            'old_data': 0,
            'invalid_vwap': 0,
            'no_entry_signal': 0,
            'regime_filter': 0,
            'isolation_limit': 0,
            'position_create_failed': 0
        }
        self.successful_trades = 0
        self.failed_trades = 0
        self.successful_dcas = 0
        self.failed_dcas = 0
        self.symbols_seen = set()
    
    def log_liquidation(self, symbol: str, liq_size: float):
        """Log a liquidation event"""
        self.total_liquidations_seen += 1
        self.symbols_seen.add(symbol)
        if symbol not in self.liquidations_by_symbol:
            self.liquidations_by_symbol[symbol] = {'count': 0, 'total_size': 0}
        self.liquidations_by_symbol[symbol]['count'] += 1
        self.liquidations_by_symbol[symbol]['total_size'] += liq_size
    
    def log_filter_rejection(self, reason: str):
        """Log why a trade was rejected"""
        if reason in self.filter_rejections:
            self.filter_rejections[reason] += 1
    
    def log_trade_attempt(self, success: bool):
        """Log trade attempt result"""
        if success:
            self.successful_trades += 1
        else:
            self.failed_trades += 1
    
    def log_dca_attempt(self, success: bool):
        """Log DCA attempt result"""
        if success:
            self.successful_dcas += 1
        else:
            self.failed_dcas += 1
    
    def get_summary(self) -> str:
        """Get trading statistics summary"""
        runtime_hours = (time.time() - self.start_time) / 3600
        summary = f"""
📊 TRADING STATISTICS ({runtime_hours:.1f}h runtime)
{'='*60}
Liquidations Processed: {self.total_liquidations_seen}
Unique Symbols Seen: {len(self.symbols_seen)}
Successful Trades: {self.successful_trades}
Failed Trades: {self.failed_trades}
Successful DCAs: {self.successful_dcas}
Failed DCAs: {self.failed_dcas}

🚫 FILTER REJECTIONS:
"""
        for reason, count in self.filter_rejections.items():
            if count > 0:
                summary += f"  {reason.replace('_', ' ').title()}: {count}\n"
        
        # Top liquidated symbols
        if self.liquidations_by_symbol:
            top_symbols = sorted(self.liquidations_by_symbol.items(),
                               key=lambda x: x[1]['count'], reverse=True)[:10]
            summary += f"\n🔝 TOP LIQUIDATED SYMBOLS:\n"
            for symbol, data in top_symbols:
                summary += f"  {symbol}: {data['count']} liquidations (${data['total_size']:,.0f})\n"
        
        return summary

stats = TradingStatistics()

# =============================================================================
# DISCORD NOTIFICATIONS
# =============================================================================

class PremiumDiscordNotifier:
    """Premium Discord notifications"""
    
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_sent = 0
        self.rate_limit = 2
    
    async def initialize(self):
        if not self.session or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self.session = aiohttp.ClientSession(timeout=timeout)
    
    async def send_startup_alert(self, zones_count: int, pairs_count: int):
        """Send enhanced startup notification"""
        try:
            embed = {
                "title": "⚡ 0xLIQD - ACTIVE",
                "description": f"```Real-time liquidation hunting bot for Binance Futures. It monitors liquidation streams, calculates VWAP zones, and executes automated entries with DCA, fixed take-profit, and optional stop-loss.```",
                "color": 0x00ff88,
                "fields": [
                    {
                        "name": "📊 Configuration",
                        "value": f"**Min. Notional:** ${CONFIG.min_notional}\n**Isolation:** {CONFIG.risk.isolation_pct*100}%\n**DCA Levels:** {CONFIG.dca.max_levels}",
                        "inline": True
                    },
                    {
                        "name": "🔧 Data Status",
                        "value": f"**Zones:** {zones_count}\n**Pairs:** {pairs_count}\n**Max Positions:** {CONFIG.risk.max_positions}",
                        "inline": False
                    }
                ],
                "footer": {"text": "Powered by 0xLIQD"},
                "timestamp": datetime.utcnow().isoformat()
            }
            
            await self.initialize()
            payload = {"username": "⚡ 0xLIQD", "embeds": [embed]}
            async with self.session.post(self.webhook_url, json=payload) as response:
                if response.status == 204:
                    logging.info("Startup notification sent")
        except Exception as e:
            logging.warning(f"Startup notification failed: {e}")
    
    async def send_trade_alert(self, symbol: str, side: str, qty: float, price: float,
                             reason: str, notional: float, is_dca: bool = False, dca_level: int = 0):
        """Send trade alert"""
        now = time.time()
        if now - self.last_sent < self.rate_limit:
            return
        
        try:
            color = 0x00ff88 if side == "buy" else 0xff6b6b
            
            if is_dca:
                title = f"🔄 DCA L{dca_level} • {symbol}"
                description = f"```{side.upper()} ${notional:.2f} @ {price:.6f}```"
            else:
                title = f"⚡ ENTRY • {symbol}"
                description = f"```{side.upper()} ${notional:.2f} @ {price:.6f}```"
            
            embed = {
                "title": title,
                "description": description,
                "color": color,
                "fields": [
                    {
                        "name": "💰 Trade Details",
                        "value": f"**Notional:** ${notional:.2f}\n**Quantity:** {qty:.6f}\n**Leverage:** {CONFIG.leverage}x",
                        "inline": True
                    },
                    {
                        "name": "🎯 Entry Reason",
                        "value": reason,
                        "inline": True
                    }
                ],
                "timestamp": datetime.utcnow().isoformat()
            }
            
            await self.initialize()
            payload = {"username": "⚡ 0xLIQD", "embeds": [embed]}
            async with self.session.post(self.webhook_url, json=payload) as response:
                if response.status == 204:
                    self.last_sent = now
        except Exception as e:
            logging.warning(f"Trade alert failed: {e}")
    
    async def send_profit_alert(self, symbol: str, pnl_pct: float, pnl_usd: float,
                              action: str, price: float):
        """Send profit/loss alert"""
        try:
            color = 0x00ff00 if pnl_pct > 0 else 0xff0000
            emoji = "🎉" if pnl_pct > 0 else "⚠️"
            
            embed = {
                "title": f"{emoji} P&L • {symbol}",
                "description": f"```{action} @ {price:.6f}```",
                "color": color,
                "fields": [
                    {
                        "name": "💵 Results",
                        "value": f"**P&L:** {pnl_pct:+.2f}%\n**USD:** ${pnl_usd:+.2f}",
                        "inline": True
                    }
                ],
                "timestamp": datetime.utcnow().isoformat()
            }
            
            await self.initialize()
            payload = {"username": "⚡ 0xLIQD", "embeds": [embed]}
            async with self.session.post(self.webhook_url, json=payload) as response:
                if response.status == 204:
                    logging.info(f"P&L alert sent: {symbol} {pnl_pct:+.2f}%")
        except Exception as e:
            logging.warning(f"P&L alert failed: {e}")
    
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

# Initialize Discord notifier
discord_notifier = None
if CONFIG.enable_discord and CONFIG.discord_webhook_url:
    discord_notifier = PremiumDiscordNotifier(CONFIG.discord_webhook_url)

# =============================================================================
# EXCHANGE INTERFACE
# =============================================================================

def get_exchange():
    """Get configured Binance exchange instance"""
    return ccxt.binance({
        "apiKey": CONFIG.api_key,
        "secret": CONFIG.api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
        "timeout": 30000,
        "sandbox": False,
    })

async def get_account_balance(exchange) -> float:
    """Get account balance"""
    try:
        balance = await exchange.fetch_balance()
        return float(balance["info"]["totalWalletBalance"])
    except Exception as e:
        logging.warning(f"Balance fetch failed: {e}")
        return 1000.0

# =============================================================================
# RAPIDAPI CLIENT
# =============================================================================

class EnhancedPriceZonesClient:
    """Enhanced RapidAPI client"""
    
    def __init__(self, config: RapidAPIConfig):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
        self.zones_data: Dict[str, Dict] = {}
        self.last_update = 0
        self.update_task: Optional[asyncio.Task] = None
    
    async def initialize(self):
        """Initialize the HTTP session"""
        if not self.session or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
            headers = {
                'x-rapidapi-key': CONFIG.rapidapi.api_key,  # Direct from main config
                'x-rapidapi-host': self.config.base_url.replace('https://', '').replace('http://', ''),
                'Content-Type': 'application/json'
            }
            self.session = aiohttp.ClientSession(timeout=timeout, headers=headers)
        
        # Load existing zones data first
        await self.load_existing_zones()
        
        # Start periodic update task
        if not self.update_task or self.update_task.done():
            self.update_task = asyncio.create_task(self._periodic_update_loop())
    
    async def load_existing_zones(self):
        """Load zones from existing cache file"""
        try:
            # Try cached data
            if self.config.enable_caching and Path(self.config.cache_file).exists():
                with open(self.config.cache_file, 'r') as f:
                    cached_data = json.load(f)
                if isinstance(cached_data, dict) and 'data' in cached_data:
                    cache_age = time.time() - cached_data.get('timestamp', 0)
                    if cache_age < self.config.update_interval_minutes * 60 * 3:
                        self.zones_data = cached_data['data']
                        self.last_update = cached_data['timestamp']
                        logging.info(f"✅ Loaded cached zones: {len(self.zones_data)} symbols")
                        return
        except Exception as e:
            logging.warning(f"Failed to load existing zones: {e}")
    
    async def save_cached_data(self):
        """Save zones data to cache file"""
        if not self.config.enable_caching:
            return
        try:
            cache_data = {
                'data': self.zones_data,
                'timestamp': self.last_update,
                'update_interval': self.config.update_interval_minutes
            }
            with open(self.config.cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
        except Exception as e:
            logging.warning(f"Failed to save cached data: {e}")
    
    async def fetch_zones_data(self) -> bool:
        """Fetch fresh liquidation zones data from RapidAPI"""
        if not CONFIG.rapidapi.api_key:
            logging.warning("⚠️ No RapidAPI key configured, using existing zones data")
            return len(self.zones_data) > 0
        
        for attempt in range(self.config.retry_attempts):
            try:
                await self.initialize()
                url = f"{self.config.base_url}{self.config.endpoint}"
                logging.info(f"🔄 Fetching zones from RapidAPI (attempt {attempt + 1})")
                
                async with self.session.get(url) as response:
                    if response.status == 200:
                        raw_data = await response.json()
                        processed_data = await self._process_zones_data(raw_data)
                        
                        if processed_data:
                            self.zones_data = processed_data
                            self.last_update = time.time()
                            await self.save_cached_data()
                            logging.info(f"✅ Updated liquidation zones: {len(self.zones_data)} symbols")
                            return True
                    elif response.status == 429:
                        logging.warning(f"⚠️ Rate limited by RapidAPI")
                        await asyncio.sleep(self.config.retry_delay * 2)
                    else:
                        error_text = await response.text()
                        logging.warning(f"❌ RapidAPI error {response.status}: {error_text}")
            except Exception as e:
                logging.error(f"❌ RapidAPI fetch error (attempt {attempt + 1}): {e}")
                if attempt < self.config.retry_attempts - 1:
                    await asyncio.sleep(self.config.retry_delay)
        
        logging.warning("💥 Failed to fetch fresh zones data, using existing data")
        return len(self.zones_data) > 0
    
    async def _process_zones_data(self, raw_data: Any) -> Dict[str, Dict]:
        """Process and validate raw RapidAPI response"""
        try:
            processed = {}
            if isinstance(raw_data, dict) and "data" in raw_data:
                for item in raw_data["data"]:
                    symbol_name = "".join(filter(str.isalpha, item.get("name", "")))
                    if symbol_name and len(symbol_name) >= 3:
                        processed[symbol_name + "USDT"] = {
                            "mean_volume": float(item.get("liq_volume", 0)),
                            "long_price": float(item.get("long_price", 0)),
                            "short_price": float(item.get("short_price", 0)),
                            "updated": time.time()
                        }
            
            logging.info(f"📊 Processed {len(processed)} zone entries from RapidAPI")
            return processed
        except Exception as e:
            logging.error(f"❌ Error processing zones data: {e}")
            return {}
    
    async def _periodic_update_loop(self):
        """Background task for periodic data updates"""
        while True:
            try:
                await asyncio.sleep(self.config.update_interval_minutes * 60)
                success = await self.fetch_zones_data()
                if not success:
                    logging.warning("⚠️ Failed to update zones data, using existing data")
            except asyncio.CancelledError:
                logging.info("🛑 Zones update loop cancelled")
                break
            except Exception as e:
                logging.error(f"❌ Error in zones update loop: {e}")
                await asyncio.sleep(60)
    
    def get_zones(self, symbol: str) -> Tuple[float, float, float]:
        """Get long_price, short_price, mean_volume for symbol"""
        zone = self.zones_data.get(symbol, {})
        return (
            zone.get('long_price', 0),
            zone.get('short_price', 0),
            zone.get('mean_volume', 0)
        )
    
    def has_zones(self, symbol: str) -> bool:
        """Check if zones exist for symbol"""
        return symbol in self.zones_data.keys()
    
    def get_data_age_minutes(self) -> float:
        """Get age of current data in minutes"""
        if self.last_update == 0:
            return float('inf')
        return (time.time() - self.last_update) / 60
    
    def get_zones_count(self) -> int:
        """Get number of symbols with zone data"""
        return len(self.zones_data)
    
    async def close(self):
        """Clean shutdown"""
        if self.update_task and not self.update_task.done():
            self.update_task.cancel()
            try:
                await self.update_task
            except asyncio.CancelledError:
                pass
        if self.session and not self.session.closed:
            await self.session.close()

# Initialize zones client
rapidapi_client = EnhancedPriceZonesClient(CONFIG.rapidapi)

# =============================================================================
# AUTO TRADING PAIRS BUILDER
# =============================================================================

class AutoTradingPairsBuilder:
    """Automatically build trading pairs from available zones data"""
    
    def __init__(self):
        self.pairs: Dict[str, Dict] = {}
    
    async def build_trading_pairs(self, exchange) -> Dict[str, Dict]:
        """Build trading pairs from zones data and exchange info"""
        logging.info("🔧 Auto-building trading pairs...")
        
        try:
            markets = await exchange.load_markets()
            
            # Get USDT futures symbols
            usdt_futures = []
            for symbol, market in markets.items():
                if (market.get("contract") and
                    market.get("active") and
                    market.get('quote') == 'USDT' and
                    market.get('type') == 'swap'):
                    usdt_futures.append(symbol)
            
            logging.info(f"📊 Found {len(usdt_futures)} USDT futures markets")
            
            built_pairs = {}
            zones_matched = 0
            
            for symbol in usdt_futures:
                base_symbol = symbol.replace(':USDT', '').replace('/USDT', '').replace('1000', '')
                
                if rapidapi_client.has_zones(base_symbol + "USDT"):
                    long_price, short_price, mean_volume = rapidapi_client.get_zones(base_symbol + "USDT")
                    
                    if long_price > 0 and short_price > 0 and mean_volume > 0:
                        market_info = markets[symbol]
                        
                        # Get precision info
                        precision = market_info.get('precision', {}).get('amount', 6)
                        step_size = 10 ** -precision
                        
                        built_pairs[base_symbol + "USDT"] = {
                            "symbol": base_symbol + "USDT",
                            "full_symbol": symbol,
                            "step_size": step_size,
                            "precision": precision,
                            "enabled": True,
                            "long_price": long_price,
                            "short_price": short_price,
                            "mean_volume": mean_volume
                        }
                        zones_matched += 1
            
            self.pairs = built_pairs
            
            # Save to file
            try:
                with open(CONFIG.pairs_file, 'w') as f:
                    json.dump(list(built_pairs.values()), f, indent=2)
                logging.info(f"💾 Saved {len(built_pairs)} trading pairs")
            except Exception as e:
                logging.warning(f"Failed to save trading pairs: {e}")
            
            logging.info(f"✅ Auto-built {len(built_pairs)} trading pairs ({zones_matched} with zones)")
            return built_pairs
        
        except Exception as e:
            logging.error(f"❌ Failed to build trading pairs: {e}")
            return {}
    
    def get_pair_config(self, symbol: str) -> Optional[Dict]:
        """Get configuration for trading pair"""
        return self.pairs.get(symbol)
    
    def is_pair_enabled(self, symbol: str) -> bool:
        """Check if pair is enabled for trading"""
        config = self.get_pair_config(symbol)
        return config is not None and config.get('enabled', False)
    
    def get_pairs_count(self) -> int:
        """Get number of enabled pairs"""
        return len([p for p in self.pairs.values() if p.get('enabled', False)])

pairs_builder = AutoTradingPairsBuilder()

# =============================================================================
# VWAP CALCULATOR
# =============================================================================

class EnhancedVWAPCalculator:
    """Enhanced VWAP calculation"""
    
    def __init__(self):
        self.vwap_cache: Dict[str, Dict] = {}
    
    async def calculate_realtime_vwap(self, exchange, symbol: str, period: int = None) -> float:
        """Calculate real-time VWAP"""
        if period is None:
            period = CONFIG.vwap.period
        
        try:
            ohlcv = await exchange.fetch_ohlcv(symbol, '1m', limit=min(period, 200))
            if len(ohlcv) < 10:
                return 0
            
            total_pv = 0
            total_volume = 0
            
            for bar in ohlcv:
                typical_price = (bar[2] + bar[3] + bar[4]) / 3
                volume = bar[5]
                total_pv += typical_price * volume
                total_volume += volume
            
            if total_volume == 0:
                return 0
            
            vwap = total_pv / total_volume
            
            # Cache result
            self.vwap_cache[symbol] = {
                'vwap': vwap,
                'timestamp': time.time(),
                'period': period
            }
            
            return vwap
        except Exception as e:
            if CONFIG.debug.enable_data_debug:
                logging.warning(f"VWAP calculation failed for {symbol}: {e}")
            return 0
    
    def get_vwap_levels(self, symbol: str, current_price: float) -> Tuple[float, float, float]:
        """Get VWAP levels combining price zones and real-time VWAP"""
        # Get price zones
        long_zone, short_zone, mean_volume = rapidapi_client.get_zones(symbol)
        
        # Primary logic: Use RapidAPI zones if available
        if CONFIG.vwap.use_rapidapi_zones and long_zone > 0 and short_zone > 0:
            final_long_level = long_zone
            final_short_level = short_zone
            vwap_reference = (long_zone + short_zone) / 2
        else:
            # Fallback: Use real-time VWAP with offsets
            cached_vwap = self.vwap_cache.get(symbol)
            if cached_vwap and time.time() - cached_vwap['timestamp'] < 300:
                vwap_reference = cached_vwap['vwap']
            else:
                vwap_reference = current_price
            
            final_long_level = vwap_reference * (1 - CONFIG.vwap.long_offset_pct / 100)
            final_short_level = vwap_reference * (1 + CONFIG.vwap.short_offset_pct / 100)
        
        # Enhancement: Blend with real-time VWAP if enabled
        if CONFIG.vwap.vwap_enhancement and long_zone > 0 and short_zone > 0:
            cached_vwap = self.vwap_cache.get(symbol)
            if cached_vwap:
                rt_vwap = cached_vwap['vwap']
                rt_long = rt_vwap * (1 - CONFIG.vwap.long_offset_pct / 100)
                rt_short = rt_vwap * (1 + CONFIG.vwap.short_offset_pct / 100)
                final_long_level = final_long_level * 0.7 + rt_long * 0.3
                final_short_level = final_short_level * 0.7 + rt_short * 0.3
        
        return final_long_level, final_short_level, vwap_reference

vwap_calculator = EnhancedVWAPCalculator()

# =============================================================================
# MARKET REGIME DETECTOR
# =============================================================================

class MarketRegimeDetector:
    """Market regime detection using ADX + ATR"""
    
    def __init__(self):
        self.regime_cache: Dict[str, Dict] = {}
    
    async def detect_regime(self, exchange, symbol: str) -> str:
        """Detect market regime"""
        try:
            ohlcv = await exchange.fetch_ohlcv(symbol, '5m', limit=50)
            if len(ohlcv) < 30:
                return "UNKNOWN"
            
            # Simple ADX calculation
            adx = self._calculate_adx(ohlcv)
            price_change_pct = ((ohlcv[-1][4] - ohlcv[-10][4]) / ohlcv[-10][4]) * 100
            
            # Regime classification
            if adx > CONFIG.market_regime.trend_threshold:
                if price_change_pct > 0.5:
                    regime = "TREND_UP"
                elif price_change_pct < -0.5:
                    regime = "TREND_DOWN"
                else:
                    regime = "TREND_WEAK"
            elif adx < CONFIG.market_regime.range_threshold:
                regime = "RANGE"
            else:
                regime = "VOLATILE"
            
            # Cache result
            self.regime_cache[symbol] = {
                'regime': regime,
                'adx': adx,
                'timestamp': time.time()
            }
            
            return regime
        except Exception as e:
            logging.warning(f"Regime detection failed for {symbol}: {e}")
            return "UNKNOWN"
    
    def _calculate_adx(self, ohlcv: List) -> float:
        """Calculate ADX"""
        try:
            period = CONFIG.market_regime.adx_period
            if len(ohlcv) < period + 1:
                return 20
            
            tr_values = []
            for i in range(1, len(ohlcv)):
                high, low, close = ohlcv[i][2], ohlcv[i][3], ohlcv[i][4]
                prev_close = ohlcv[i-1][4]
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                tr_values.append(tr)
            
            return min(100, max(0, np.mean(tr_values[-period:]) * 100))
        except:
            return 20
    
    def should_trade_in_regime(self, regime: str) -> bool:
        """Determine if we should trade in this market regime"""
        if not CONFIG.market_regime.regime_filter:
            return True
        return regime in ["TREND_UP", "TREND_DOWN", "RANGE", "TREND_WEAK"]

regime_detector = MarketRegimeDetector()

# =============================================================================
# POSITION MANAGEMENT WITH REAL-TIME SYNC
# =============================================================================

@dataclass
class Position:
    symbol: str
    side: str
    total_qty: float = 0
    avg_entry_price: float = 0
    dca_count: int = 0
    total_notional_used: float = 0  # Track notional instead of capital
    entry_time: float = field(default_factory=time.time)
    tp_price: float = 0
    sl_price: float = 0
    vwap_reference: float = 0
    regime: str = "UNKNOWN"

class PositionManager:
    """Position management with real-time exchange sync"""
    
    def __init__(self):
        self.positions: Dict[str, Position] = {}
        self.total_trades = 0
        self.profitable_trades = 0
        self._position_lock = asyncio.Lock()
    
    async def sync_with_exchange(self, exchange) -> Dict[str, Any]:
        """Synchronize in-memory positions with live exchange positions."""
        try:
            # 1) Fetch actual positions from Binance
            exchange_positions = await exchange.fetch_positions()
            active_positions = {}
            for pos in exchange_positions:
                if pos['contracts'] and float(pos['contracts']) != 0:
                    symbol = pos['symbol']
                    base = symbol.replace(':USDT', '').replace('/USDT', '') + 'USDT'
                    side = 'buy' if pos['side'] == 'long' else 'sell'
                    qty = float(pos['contracts'])
                    price = float(pos.get('entryPrice') or pos.get('markPrice') or 0)
                    active_positions[base] = {
                        'side': side,
                        'qty': qty,
                        'avg_price': price,
                        'unrealizedPnl': float(pos.get('unrealizedPnl') or 0)
                    }

            # 2) Remove closed positions
            closed = [s for s in self.positions if s not in active_positions]
            for s in closed:
                # Calculate final P&L before removing position
                if symbol in self.positions:
                    await self._send_position_closed_alert(symbol, exchange)

                logging.info(f"🔄 Removing closed position {s}")
                del self.positions[s]

            # 3) Add or update active positions
            for base, info in active_positions.items():
                if base not in self.positions:
                    logging.info(f"➕ Adding live position {base}")
                    # Create a Position object to reflect what's on‐exchange
                    self.positions[base] = Position(
                        symbol=base,
                        side=info['side'],
                        total_qty=info['qty'],
                        avg_entry_price=info['avg_price'],
                        total_notional_used=info['qty'] * info['avg_price'],
                        vwap_reference=info['avg_price'],  # placeholder
                        regime="UNKNOWN"
                    )
                else:
                    # Update existing memory entry with live data
                    p = self.positions[base]
                    p.total_qty = info['qty']
                    p.avg_entry_price = info['avg_price']
                    p.total_notional_used = info['qty'] * info['avg_price']

            logging.debug(f"✅ Sync complete: {len(active_positions)} live, {len(closed)} removed")
            return active_positions

        except Exception as e:
            logging.error(f"❌ Sync failed: {e}")
            return {}
    
    async def create_position(self, exchange, symbol: str, side: str, liq_price: float,
                            liq_size: float, vwap_ref: float, regime: str,
                            balance: float) -> Optional[Position]:
        """Create new position"""
        async with self._position_lock:
            # Sync with exchange first
            active_positions = await self.sync_with_exchange(exchange)
            
            # Check if position already exists on exchange
            if symbol in active_positions:
                logging.info(f"❌ {symbol}: Position already exists on exchange")
                stats.log_filter_rejection('existing_position')
                return None
            
            # Check max positions
            if len(active_positions) >= CONFIG.risk.max_positions:
                logging.info(f"❌ {symbol}: Max positions reached ({len(active_positions)}/{CONFIG.risk.max_positions})")
                stats.log_filter_rejection('max_positions')
                return None
            
            # RISK CHECK: Isolation percentage
            total_notional_used = sum(p.total_notional_used for p in self.positions.values())
            isolation_limit = balance * CONFIG.risk.isolation_pct
            
            if total_notional_used + CONFIG.min_notional > isolation_limit:
                logging.info(f"❌ {symbol}: Isolation limit reached (${total_notional_used:.2f} + ${CONFIG.min_notional} > ${isolation_limit:.2f})")
                stats.log_filter_rejection('isolation_limit')
                return None
            
            # Get pair config for precision
            pair_config = pairs_builder.get_pair_config(symbol)
            if not pair_config:
                logging.error(f"❌ {symbol}: No pair config found")
                return None
            
            # NOTIONAL CALCULATION
            entry_notional = CONFIG.min_notional
            entry_qty = entry_notional / liq_price
            
            # Apply proper quantity rounding
            step_size = pair_config.get('step_size', 0.001)
            entry_qty = round(entry_qty / step_size) * step_size
            
            # Recalculate actual notional after rounding
            actual_notional = entry_qty * liq_price
            
            # Create position
            position = Position(
                symbol=symbol,
                side=side,
                total_qty=entry_qty,
                avg_entry_price=liq_price,
                total_notional_used=actual_notional,
                vwap_reference=vwap_ref,
                regime=regime
            )
            
            self.positions[symbol] = position
            self.total_trades += 1
            
            logging.info(f"✅ Position created: {symbol} {side} {entry_qty:.6f} @ {liq_price:.6f} | "
                        f"Notional: ${actual_notional:.2f}")
            
            return position
    
    async def check_dca_trigger(self, symbol: str, current_price: float) -> Tuple[bool, int]:
        """Check if DCA should be triggered"""
        if symbol not in self.positions:
            return False, -1
        
        position = self.positions[symbol]
        
        # Check limits
        if position.dca_count >= CONFIG.dca.max_levels:
            return False, -1
        
        # Calculate adverse move percentage
        if position.side == "buy":
            adverse_move = (position.avg_entry_price - current_price) / position.avg_entry_price
        else:
            adverse_move = (current_price - position.avg_entry_price) / position.avg_entry_price
        
        logging.info(f"🔍 DCA Check {symbol}: Price={current_price:.5f}, Entry={position.avg_entry_price:.5f}, "
            f"Adverse={adverse_move*100:.2f}%")

        # Check trigger
        next_level = position.dca_count
        if next_level < len(CONFIG.dca.trigger_pcts):
            trigger_threshold = CONFIG.dca.trigger_pcts[next_level]
            if adverse_move >= trigger_threshold:
                return True, next_level + 1
        
        return False, -1
    
    async def execute_dca(self, exchange, symbol: str, current_price: float,
                         dca_level: int) -> bool:
        """Execute DCA with PROPER MIN NOTIONAL + SIZE MULTIPLIERS"""
        
        position = self.positions[symbol]
        
        # Get pair config
        pair_config = pairs_builder.get_pair_config(symbol)
        if not pair_config:
            logging.error(f"❌ DCA failed: No pair config for {symbol}")
            return False
        
        # Apply size multiplier to min notional
        # DCA Level 1 = $11 * 2.0 = $22
        # DCA Level 2 = $11 * 3.0 = $33, etc.
        size_multiplier = 1.0
        if dca_level - 1 < len(CONFIG.dca.size_multipliers):
            size_multiplier = CONFIG.dca.size_multipliers[dca_level - 1]
        
        dca_notional = CONFIG.min_notional * size_multiplier
        dca_qty = dca_notional / current_price
        
        # Apply proper rounding
        step_size = pair_config.get('step_size', 0.001)
        dca_qty = round(dca_qty / step_size) * step_size
        
        # Recalculate actual notional after rounding
        actual_notional = dca_qty * current_price
        
        # Check isolation limit with new DCA size
        total_notional_used = sum(p.total_notional_used for p in self.positions.values())
        balance = await get_account_balance(exchange)
        isolation_limit = balance * CONFIG.risk.isolation_pct
        
        if total_notional_used + actual_notional > isolation_limit:
            logging.info(f"❌ DCA L{dca_level} {symbol}: Would exceed isolation limit "
                        f"(${total_notional_used:.2f} + ${actual_notional:.2f} > ${isolation_limit:.2f})")
            stats.log_dca_attempt(False)
            return False
        
        try:
            # Execute DCA order
            order = await exchange.create_market_order(
                symbol, position.side, dca_qty,
                params={"positionSide": "LONG" if position.side == "buy" else "SHORT"}
            )
            
            actual_price = float(order.get('price', current_price)) or current_price
            
            # Update position
            new_total_qty = position.total_qty + dca_qty
            new_avg_price = ((position.avg_entry_price * position.total_qty) +
                           (actual_price * dca_qty)) / new_total_qty
            
            position.total_qty = new_total_qty
            position.avg_entry_price = new_avg_price
            position.dca_count += 1
            position.total_notional_used += actual_notional
            
            logging.info(f"✅ DCA L{dca_level}: {symbol} {dca_qty:.6f} @ {actual_price:.6f} | "
                        f"Multiplier: {size_multiplier}x | Notional: ${actual_notional:.2f} | "
                        f"New Avg: {new_avg_price:.6f}")
            
            # Send Discord notification
            if discord_notifier:
                await discord_notifier.send_trade_alert(
                    symbol, position.side, dca_qty, actual_price,
                    f"DCA Level {dca_level} (${CONFIG.min_notional} × {size_multiplier}x)", actual_notional,
                    is_dca=True, dca_level=dca_level
                )
            
            stats.log_dca_attempt(True)
            return True
        
        except Exception as e:
            logging.error(f"❌ DCA execution failed for {symbol} L{dca_level}: {e}")
            stats.log_dca_attempt(False)
            return False
    
    def get_position_count(self) -> int:
        return len(self.positions)
    
    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions
    
    def remove_position(self, symbol: str):
        if symbol in self.positions:
            del self.positions[symbol]

    async def _send_position_closed_alert(self, symbol: str, exchange):
        """Send P&L alert when position is detected as closed"""
        try:
            position = self.positions[symbol]
            
            # Get current price to calculate final P&L
            ticker = await exchange.fetch_ticker(symbol)
            current_price = float(ticker['close'])
            
            # Calculate P&L
            if position.side == "buy":
                pnl_pct = (current_price - position.avg_entry_price) / position.avg_entry_price
            else:
                pnl_pct = (position.avg_entry_price - current_price) / position.avg_entry_price
                
            pnl_usd = position.total_qty * position.avg_entry_price * pnl_pct
            
            # Send Discord notification
            if discord_notifier:
                action = "TAKE PROFIT" if pnl_pct > 0 else "STOP LOSS"
                await discord_notifier.send_profit_alert(
                    symbol, pnl_pct * 100, pnl_usd, action, current_price
                )
            
            logging.info(f"💰 Position closed: {symbol} | P&L: {pnl_pct*100:+.2f}% (${pnl_usd:+.2f})")
            
        except Exception as e:
            logging.error(f"❌ Failed to send position closed alert for {symbol}: {e}")

position_manager = PositionManager()

# =============================================================================
# PROFIT PROTECTION SYSTEM
# =============================================================================

class ProfitProtectionSystem:
    """Profit protection"""
    
    def __init__(self):
        self.protection_cache: Dict[str, Dict] = {}
    
    async def set_initial_take_profit(self, exchange, position: Position) -> bool:
        """Set initial take profit order"""
        try:
            # Calculate TP price
            if position.side == "buy":
                tp_price = position.avg_entry_price * (1 + CONFIG.profit_protection.initial_tp_pct)
                sl_price = position.avg_entry_price * (1 - CONFIG.profit_protection.stop_loss_pct) if CONFIG.profit_protection.enable_stop_loss else 0
            else:
                tp_price = position.avg_entry_price * (1 - CONFIG.profit_protection.initial_tp_pct)
                sl_price = position.avg_entry_price * (1 + CONFIG.profit_protection.stop_loss_pct) if CONFIG.profit_protection.enable_stop_loss else 0
            
            position.tp_price = tp_price
            
            # Place TP order
            tp_side = "sell" if position.side == "buy" else "buy"
            await exchange.create_limit_order(
                position.symbol, tp_side, position.total_qty, tp_price,
                params={"positionSide": "LONG" if position.side == "buy" else "SHORT"}
            )
            logging.info(f"✅ TP set: {position.symbol} @ {tp_price:.6f} ({CONFIG.profit_protection.initial_tp_pct*100:.1f}%)")
            
            # Place SL order if enabled
            if CONFIG.profit_protection.enable_stop_loss:
                sl_side = "sell" if position.side == "buy" else "buy"
                await exchange.create_stop_market_order(
                    position.symbol, sl_side, position.total_qty, sl_price,
                    params={"positionSide": "LONG" if position.side == "buy" else "SHORT", "reduceOnly": True}
                )
        
            return True
        except Exception as e:
            logging.error(f"❌ TP setup failed {position.symbol}: {e}")
            return False

profit_protection = ProfitProtectionSystem()

# =============================================================================
# MAIN STRATEGY
# =============================================================================

class VWAPHunterStrategy:
    """VWAP Hunter with fixed position management and min notional"""
    
    def __init__(self, exchange):
        self.exchange = exchange
        self.price_cache: Dict[str, float] = {}
        self.last_price_update: Dict[str, float] = {}
        self.entry_timestamps: Dict[str, float] = {}
    
    async def get_current_price(self, symbol: str) -> float:
        """Get current price with caching"""
        current_time = time.time()
        if (symbol in self.price_cache and
            current_time - self.last_price_update.get(symbol, 0) < 30):
            return self.price_cache[symbol]
        
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            price = float(ticker['close'])
            self.price_cache[symbol] = price
            self.last_price_update[symbol] = current_time
            return price
        except Exception as e:
            if CONFIG.debug.enable_data_debug:
                logging.warning(f"Price fetch failed for {symbol}: {e}")
            return self.price_cache.get(symbol, 0)
    
    async def check_volume_filter(self, symbol: str) -> Tuple[bool, float]:
        """Check if symbol meets volume requirements"""
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            daily_volume = float(ticker.get("quoteVolume", 0))
            passed = daily_volume >= CONFIG.risk.min_24h_volume
            
            if CONFIG.debug.enable_filter_debug:
                debug_logger = logging.getLogger('trade_debug')
                debug_logger.debug(f"Volume filter {symbol}: ${daily_volume:,.0f} ({'PASS' if passed else 'FAIL'})")
            
            return passed, daily_volume
        except Exception as e:
            if CONFIG.debug.enable_data_debug:
                logging.warning(f"Volume check failed for {symbol}: {e}")
            return False, 0
    
    async def process_liquidation_event(self, liquidation_data: Dict):
        """Process liquidation event with comprehensive debugging"""
        start_time = time.time()
        
        try:
            # Parse liquidation data
            data = liquidation_data.get('o', {})
            symbol = data.get('s', '')
            
            if not symbol or not symbol.endswith('USDT'):
                return
            
            liq_price = float(data.get('ap', 0))
            liq_qty = float(data.get('q', 0))
            liq_side = data.get('S', '')
            
            if liq_price <= 0 or liq_qty <= 0 or not liq_side:
                return
            
            liq_size_usd = liq_price * liq_qty
            our_side = "buy" if liq_side == "SELL" else "sell"
            
            # Log all liquidations if enabled
            if CONFIG.debug.log_all_liquidations:
                logging.info(f"📡 LIQUIDATION: {symbol} | {liq_side} | {liq_qty:.4f} @ {liq_price:.6f} | ${liq_size_usd:,.0f}")
            
            # Update statistics
            stats.log_liquidation(symbol, liq_size_usd)
            
            # Skip new entries if we just entered within cooldown
            cooldown = 30  # seconds
            last = self.entry_timestamps.get(symbol, 0)
            if time.time() - last < cooldown:
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"❌ {symbol}: Entry cooldown active ({time.time()-last:.0f}s elapsed)")
                return

            # === ENHANCED FILTER DEBUGGING ===
            if CONFIG.debug.enable_trade_debug:
                debug_logger = logging.getLogger('trade_debug')
                debug_logger.debug(f"🔍 ANALYZING: {symbol} | Liq: {liq_side} @ {liq_price:.6f} | Our Side: {our_side}")
            
            # Filter 1: Pair enabled check
            if not pairs_builder.is_pair_enabled(symbol):
                stats.log_filter_rejection('pair_not_enabled')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"❌ {symbol}: Pair not enabled")
                return
            
            # Filter 2: Existing position check (REAL-TIME SYNC)
            await position_manager.sync_with_exchange(self.exchange)
            
            if position_manager.has_position(symbol):
                stats.log_filter_rejection('existing_position')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"❌ {symbol}: Already have position")
                return
            
            # Filter 3: Max positions check
            active_positions = await position_manager.sync_with_exchange(self.exchange)
            if len(active_positions) >= CONFIG.risk.max_positions:
                stats.log_filter_rejection('max_positions')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"❌ {symbol}: Max positions reached ({position_manager.get_position_count()}/{CONFIG.risk.max_positions})")
                return
            
            # Filter 4: Volume check
            volume_pass, daily_volume = await self.check_volume_filter(symbol)
            if not volume_pass:
                stats.log_filter_rejection('volume_filter')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"❌ {symbol}: Volume too low (${daily_volume:,.0f} < ${CONFIG.risk.min_24h_volume:,.0f})")
                return
            
            # Filter 5: Zones data check
            if not rapidapi_client.has_zones(symbol):
                stats.log_filter_rejection('no_zones')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"❌ {symbol}: No price zones data")
                return
            
            # Filter 6: Data age check
            data_age = rapidapi_client.get_data_age_minutes()
            if data_age > 60:
                stats.log_filter_rejection('old_data')
                if CONFIG.debug.enable_filter_debug:
                    logging.warning(f"⚠️ {symbol}: Price zones data is {data_age:.0f} minutes old")
                # Continue anyway
            
            # Get pair config
            pair_config = pairs_builder.get_pair_config(symbol)
            if not pair_config:
                if CONFIG.debug.enable_filter_debug:
                    logging.error(f"❌ {symbol}: No pair config found")
                return
            
            # === VWAP LEVEL ANALYSIS ===
            if CONFIG.vwap.vwap_enhancement:
                await vwap_calculator.calculate_realtime_vwap(self.exchange, symbol)
            
            # Get VWAP levels
            long_level, short_level, vwap_ref = vwap_calculator.get_vwap_levels(symbol, liq_price)
            
            if long_level <= 0 or short_level <= 0:
                stats.log_filter_rejection('invalid_vwap')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"❌ {symbol}: Invalid VWAP levels (L:{long_level:.6f} S:{short_level:.6f})")
                return
            
            # === ENTRY SIGNAL ANALYSIS ===
            entry_valid = False
            entry_reason = ""
            
            if our_side == "buy":
                if liq_price <= long_level:
                    entry_valid = True
                    entry_reason = f"LONG: {liq_price:.6f} ≤ {long_level:.6f} (Zone hit)"
                else:
                    zone_distance = ((liq_price - long_level) / long_level) * 100
                    entry_reason = f"LONG: {liq_price:.6f} > {long_level:.6f} (Above zone by {zone_distance:.2f}%)"
            elif our_side == "sell":
                if liq_price >= short_level:
                    entry_valid = True
                    entry_reason = f"SHORT: {liq_price:.6f} ≥ {short_level:.6f} (Zone hit)"
                else:
                    zone_distance = ((short_level - liq_price) / short_level) * 100
                    entry_reason = f"SHORT: {liq_price:.6f} < {short_level:.6f} (Below zone by {zone_distance:.2f}%)"
            
            if CONFIG.debug.enable_trade_debug:
                debug_logger = logging.getLogger('trade_debug')
                debug_logger.debug(f"📊 ZONES {symbol}: Long={long_level:.6f} Short={short_level:.6f} VWAP={vwap_ref:.6f}")
                debug_logger.debug(f"🎯 SIGNAL {symbol}: {entry_reason}")
            
            if not entry_valid:
                stats.log_filter_rejection('no_entry_signal')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"❌ {symbol}: No entry signal - {entry_reason}")
                return
            
            # === MARKET REGIME CHECK ===
            regime = await regime_detector.detect_regime(self.exchange, symbol)
            if not regime_detector.should_trade_in_regime(regime):
                stats.log_filter_rejection('regime_filter')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"❌ {symbol}: Regime filter - {regime}")
                return
            
            # === CREATE POSITION ===
            balance = await get_account_balance(self.exchange)
            
            position = await position_manager.create_position(
                self.exchange, symbol, our_side, liq_price, liq_size_usd, vwap_ref, regime, balance
            )
            
            if position is None:
                stats.log_filter_rejection('position_create_failed')
                if CONFIG.debug.enable_filter_debug:
                    logging.error(f"❌ {symbol}: Position creation failed")
                return
            
            # === EXECUTE TRADE ===
            success = await self._execute_trade(position)
            execution_time = time.time() - start_time
            
            stats.log_trade_attempt(success)
            
            if success:
                self.entry_timestamps[symbol] = time.time()

                # Set initial take profit
                await profit_protection.set_initial_take_profit(self.exchange, position)
                
                # Send Discord notification
                if discord_notifier:
                    await discord_notifier.send_trade_alert(
                        symbol, our_side, position.total_qty, liq_price,
                        entry_reason, position.total_notional_used
                    )
                
                logging.info(f"🎯 TRADE SUCCESS: {symbol} | {entry_reason} | "
                           f"Notional: ${position.total_notional_used:.2f} | Time: {execution_time:.2f}s")
                
                if CONFIG.debug.enable_trade_debug:
                    debug_logger = logging.getLogger('trade_debug')
                    debug_logger.debug(f"✅ EXECUTED: {symbol} {our_side} {position.total_qty:.6f} @ {liq_price:.6f}")
            else:
                position_manager.remove_position(symbol)
                logging.warning(f"❌ TRADE FAILED: {symbol} in {execution_time:.2f}s")
        
        except Exception as e:
            total_time = time.time() - start_time
            logging.error(f"❌ Processing error for {symbol}: {e} | Time: {total_time:.2f}s", exc_info=True)
    
    async def _execute_trade(self, position: Position) -> bool:
        """Execute the actual trade"""
        try:
            # Set leverage
            try:
                await self.exchange.set_margin_mode('cross', position.symbol)
                await self.exchange.set_leverage(CONFIG.leverage, position.symbol)
            except Exception as e:
                if "not modified" not in str(e).lower():
                    logging.warning(f"Leverage setup failed for {position.symbol}: {e}")
            
            # Execute market order
            order = await self.exchange.create_market_order(
                position.symbol, position.side, position.total_qty,
                params={"positionSide": "LONG" if position.side == "buy" else "SHORT"}
            )
            
            # Update with actual execution price
            actual_price = float(order.get('price', position.avg_entry_price)) or position.avg_entry_price
            position.avg_entry_price = actual_price
            
            return True
        except Exception as e:
            logging.error(f"❌ Trade execution failed for {position.symbol}: {e}")
            return False
    
    async def monitor_positions(self):
        """Background position monitoring for DCA and profit protection"""
        while True:
            try:
                # Sync positions with exchange first
                await position_manager.sync_with_exchange(self.exchange)
                
                for symbol, position in list(position_manager.positions.items()):
                    current_price = await self.get_current_price(symbol)
                    if current_price <= 0:
                        continue
                    
                    # Check DCA triggers
                    should_dca, dca_level = await position_manager.check_dca_trigger(symbol, current_price)
                    
                    if should_dca and CONFIG.dca.enable:
                        success = await position_manager.execute_dca(
                            self.exchange, symbol, current_price, dca_level
                        )
                        
                        if success:
                            # Cancel existing TP orders for this symbol
                            open_orders = await self.exchange.fetch_open_orders(position.symbol)
                            for order in open_orders:
                                if order['type'] == 'limit' and order.get('side') in ('sell', 'buy') and order.get('reduceOnly', False):
                                    await self.exchange.cancel_order(order['id'], position.symbol)

                            # Update TP after DCA
                            await profit_protection.set_initial_take_profit(self.exchange, position)
                
                await asyncio.sleep(15)  # Check every 15 seconds
            except Exception as e:
                logging.error(f"Position monitoring error: {e}")
                await asyncio.sleep(60)

# =============================================================================
# MAIN BOT EXECUTION
# =============================================================================

async def main_bot():
    """Main bot execution"""
    setup_enhanced_logging()
    logging.info("⚡ 0xLIQD - STARTING")
    logging.info("=" * 70)
    
    try:
        # Initialize exchange
        exchange = get_exchange()
        logging.info("✅ Exchange connected")
        
        # Initialize zones client
        await rapidapi_client.initialize()
        
        # Try to fetch fresh zones data
        zones_fetch_success = await rapidapi_client.fetch_zones_data()
        zones_count = rapidapi_client.get_zones_count()
        
        if zones_count == 0:
            logging.error("💥 No zones data available - cannot proceed")
            return
        
        data_age = rapidapi_client.get_data_age_minutes()
        logging.info(f"✅ Zones loaded: {zones_count} symbols, age: {data_age:.1f}min")
        
        # Auto-build trading pairs
        pairs_data = await pairs_builder.build_trading_pairs(exchange)
        pairs_count = pairs_builder.get_pairs_count()
        
        if pairs_count == 0:
            logging.error("💥 No trading pairs built - cannot proceed")
            return
        
        logging.info(f"✅ Trading pairs built: {pairs_count} enabled pairs")
        
        # Initialize strategy
        strategy = VWAPHunterStrategy(exchange)
        logging.info("✅ Strategy initialized")
        
        # Send startup notification
        if discord_notifier:
            await discord_notifier.initialize()
            await discord_notifier.send_startup_alert(zones_count, pairs_count)
            logging.info("✅ Discord notifications active")
        
        # Start position monitoring task
        monitor_task = asyncio.create_task(strategy.monitor_positions())
        logging.info("✅ Position monitoring started")
        
        # Start statistics reporting task
        stats_task = asyncio.create_task(periodic_stats_reporter())
        
        # Configuration summary
        logging.info("📋 Configuration:")
        logging.info(f"  Min Notional: ${CONFIG.min_notional}")
        logging.info(f"  Isolation: {CONFIG.risk.isolation_pct*100}% of balance")
        logging.info(f"  DCA Levels: {CONFIG.dca.max_levels}")
        logging.info(f"  Max Positions: {CONFIG.risk.max_positions}")
        logging.info(f"  Real-time Position Sync: ENABLED")
        logging.info("=" * 70)
        
        # WebSocket connection
        uri = "wss://fstream.binance.com/stream?streams=!forceOrder@arr"
        reconnect_delay = 5
        
        while True:
            try:
                logging.info("🔌 Connecting to Binance liquidation stream...")
                async with websockets.connect(uri) as ws:
                    logging.info("⚡ 0xLIQD ACTIVE")
                    reconnect_delay = 5
                    
                    while True:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=30.0)
                            payload = json.loads(message)
                            
                            # Handle liquidation events
                            events = payload.get("data", [])
                            if not isinstance(events, list):
                                events = [events]
                            
                            for event in events:
                                # Process asynchronously
                                asyncio.create_task(strategy.process_liquidation_event(event))
                        except websockets.exceptions.ConnectionClosed:
                            logging.warning("WebSocket connection closed")
                            break
                        except asyncio.TimeoutError:
                            continue
                        except Exception as e:
                            logging.error(f"WebSocket error: {e}")
                            break
            except Exception as e:
                logging.error(f"Connection error: {e}")
                logging.info(f"⏳ Reconnecting in {reconnect_delay}s...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, 60)
    
    except Exception as e:
        logging.error(f"💥 Fatal error: {e}", exc_info=True)
        raise
    finally:
        logging.info("🧹 Cleanup...")
        await rapidapi_client.close()
        await exchange.close()
        if discord_notifier:
            await discord_notifier.close()
        logging.info("✅ Shutdown complete")

async def periodic_stats_reporter():
    """Periodic statistics reporting"""
    while True:
        try:
            await asyncio.sleep(CONFIG.debug.stats_interval_minutes * 60)
            
            # Generate stats summary
            summary = stats.get_summary()
            
            # Log to console and file
            logging.info(summary)
            
            # Send to Discord if enabled
            if discord_notifier:
                embed = {
                    "title": "📊 Trading Statistics",
                    "description": f"```{summary[:1800]}```",
                    "color": 0x00ff88,
                    "timestamp": datetime.utcnow().isoformat()
                }
                await discord_notifier.initialize()
                payload = {"username": "📊 0xLIQD Stats", "embeds": [embed]}
                try:
                    async with discord_notifier.session.post(discord_notifier.webhook_url, json=payload) as response:
                        if response.status == 204:
                            logging.info("Stats update sent to Discord")
                except:
                    pass
        except Exception as e:
            logging.error(f"Stats reporting error: {e}")
            await asyncio.sleep(60)

# =============================================================================
# ENTRY POINT
# =============================================================================

async def main():
    """Main entry point"""
    try:
        print("⚡ Starting 0xLIQD...")
        
        # Validate configuration
        if not CONFIG.api_key or not CONFIG.api_secret:
            print("❌ Missing Binance API credentials in config.yaml")
            return
        
        if CONFIG.rapidapi.api_key:
            print("✅ RapidAPI key configured")
        else:
            print("⚠️ No RapidAPI key")
        
        print("✅ Configuration validated")
        print("🚀 Launching bot...")
        
        await main_bot()
    
    except KeyboardInterrupt:
        print("\n🛑 Shutdown requested")
    except Exception as e:
        print(f"💥 Fatal error: {e}")
        logging.error(f"💥 Fatal error: {e}", exc_info=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped")
    except Exception as e:
        print(f"💥 Startup error: {e}")
        exit(1)