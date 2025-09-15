import asyncio
import ccxt.pro as ccxtpro
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
import signal
import sys

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
class DCAConfig:
    """DCA system configuration"""
    enable: bool = True
    max_levels: int = 7
    trigger_pcts: List[float] = field(default_factory=lambda: [0.05, 0.07, 0.09, 0.11, 0.13, 0.15, 0.17])
    size_multipliers: List[float] = field(default_factory=lambda: [1.5, 2, 2.5, 3, 3.5, 4, 4.5])

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
    isolation_pct: float = 0.50
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
class MomentumConfig:
    """Momentum detection configuration"""
    enable_momentum_filter: bool = True
    daily_pump_threshold: float = 15.0
    daily_dump_threshold: float = -10.0
    hourly_pump_threshold: float = 8.0
    hourly_dump_threshold: float = -6.0
    min_daily_volatility: float = 5.0
    max_daily_volatility: float = 50.0
    momentum_mode: str = "AVOID_EXTREMES"
    momentum_lookback_hours: int = 24
    volatility_lookback_hours: int = 24

@dataclass
class Config:
    """Main configuration class"""
    api_key: str = ""
    api_secret: str = ""
    discord_webhook_url: str = ""
    leverage: int = 10
    min_notional: float = 11.0
    rapidapi: RapidAPIConfig = field(default_factory=RapidAPIConfig)
    vwap: VWAPConfig = field(default_factory=VWAPConfig)
    dca: DCAConfig = field(default_factory=DCAConfig)
    profit_protection: ProfitProtectionConfig = field(default_factory=ProfitProtectionConfig)
    market_regime: MarketRegimeConfig = field(default_factory=MarketRegimeConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)
    momentum: MomentumConfig = field(default_factory=MomentumConfig)
    enable_discord: bool = True
    log_file: str = "0xliqd.log"
    pairs_file: str = "trading_pairs_auto.json"

def load_config() -> Config:
    """Load configuration from config.yaml ONLY"""
    config = Config()
    
    if Path("config.yaml").exists():
        try:
            with open("config.yaml", 'r') as f:
                file_config = yaml.safe_load(f)
                
            if file_config:
                for key, value in file_config.items():
                    if hasattr(config, key) and not isinstance(getattr(config, key),
                        (RapidAPIConfig, VWAPConfig, DCAConfig, ProfitProtectionConfig, 
                         MarketRegimeConfig, RiskConfig, DebugConfig, MomentumConfig)):
                        setattr(config, key, value)
                
                for sub_config_name in ['rapidapi', 'vwap', 'dca', 'profit_protection', 
                                       'market_regime', 'risk', 'debug', 'momentum']:
                    if sub_config_name in file_config:
                        sub_config = getattr(config, sub_config_name)
                        for key, value in file_config[sub_config_name].items():
                            if hasattr(sub_config, key):
                                setattr(sub_config, key, value)
                                
            print("✅ Configuration loaded from config.yaml")
        except Exception as e:
            print(f"⚠️ Error loading config: {e}")
    else:
        print("⚠️ config.yaml not found!")
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
    
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
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
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)-7s %(message)s'
    ))
    logger.addHandler(console_handler)
    
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
# SYMBOL FORMAT UTILITIES
# =============================================================================

def normalize_symbol(symbol: str) -> str:
    """Convert any symbol format to base format (BTCUSDT)"""
    if symbol.endswith(':USDT'):
        # Convert BTC/USDT:USDT -> BTCUSDT
        return symbol.replace('/USDT:USDT', 'USDT').replace('/', '')
    elif symbol.endswith('/USDT'):
        # Convert BTC/USDT -> BTCUSDT  
        return symbol.replace('/USDT', 'USDT').replace('/', '')
    return symbol

def to_ccxt_symbol(symbol: str) -> str:
    """Convert base symbol to CCXT format (BTC/USDT:USDT)"""
    base_symbol = normalize_symbol(symbol)
    if base_symbol.endswith('USDT'):
        base = base_symbol[:-4]  # Remove USDT
        return f"{base}/USDT:USDT"
    return symbol

# =============================================================================
# GRACEFUL SHUTDOWN
# =============================================================================

class GracefulShutdownHandler:
    """Handle graceful shutdown for all components"""
    
    def __init__(self):
        self.shutdown_event = asyncio.Event()
        self.components_to_cleanup = []
        
    def register_component(self, component, cleanup_method):
        """Register components that need cleanup"""
        self.components_to_cleanup.append((component, cleanup_method))
    
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            logging.info("🛑 Shutdown signal received")
            self.shutdown_event.set()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def cleanup_all(self):
        """Cleanup all registered components"""
        logging.info("🧹 Starting graceful cleanup...")
        
        for component, cleanup_method in self.components_to_cleanup:
            try:
                if asyncio.iscoroutinefunction(cleanup_method):
                    await cleanup_method()
                else:
                    cleanup_method()
                logging.info(f"✅ Cleaned up {component.__class__.__name__}")
            except Exception as e:
                logging.warning(f"⚠️ Cleanup error for {component.__class__.__name__}: {e}")

# Global shutdown handler
shutdown_handler = GracefulShutdownHandler()

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
            'momentum_filter': 0,
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
        
        if self.liquidations_by_symbol:
            top_symbols = sorted(self.liquidations_by_symbol.items(),
                               key=lambda x: x[1]['count'], reverse=True)[:10]
            summary += f"\n🔥 TOP LIQUIDATED SYMBOLS:\n"
            for symbol, data in top_symbols:
                summary += f"  {symbol}: {data['count']} liquidations (${data['total_size']:,.0f})\n"
        
        return summary

stats = TradingStatistics()

# =============================================================================
# CCXT PRO DATA MANAGER SYMBOL HANDLING
# =============================================================================

class CCXTProDataManager:
    """CCXT Pro data manager"""
    
    def __init__(self, exchange):
        self.exchange = exchange
        self.positions_cache: Dict[str, Dict] = {}
        self.balance_cache: Dict[str, float] = {'USDT': 0.0}
        self.price_cache: Dict[str, Dict] = {}
        self.ohlcv_cache: Dict[str, deque] = {}
        self.subscribed_symbols: set = set()
        
        # Streaming tasks
        self.streaming_tasks: List[asyncio.Task] = []
        self.is_streaming = False
        
        # Last update timestamps
        self.last_updates = {
            'positions': 0,
            'balance': 0,
            'prices': {},
            'ohlcv': {}
        }
        
        logging.info("🔌 CCXT Pro Data Manager initialized")
    
    async def start_streaming(self):
        """Start all CCXT Pro streaming tasks"""
        if self.is_streaming:
            return
        
        self.is_streaming = True
        
        try:
            # Start balance streaming
            balance_task = asyncio.create_task(self._stream_balance())
            self.streaming_tasks.append(balance_task)
            
            # Start positions streaming
            positions_task = asyncio.create_task(self._stream_positions())
            self.streaming_tasks.append(positions_task)
            
            logging.info("✅ CCXT Pro streaming started")
            
        except Exception as e:
            logging.error(f"⚠️ Failed to start CCXT Pro streaming: {e}")
            raise
    
    async def _stream_balance(self):
        """Stream balance updates using CCXT Pro"""
        while self.is_streaming:
            try:
                bal = await self.exchange.watchBalance()
                usdt_data = bal.get('USDT')
                if usdt_data:
                    free = usdt_data.get('free')
                    if free is None:
                        free = usdt_data.get('total')
                    if free is None and 'used' in usdt_data and 'free' in usdt_data:
                        free = usdt_data['used'] + usdt_data['free']
                    if free is not None:
                        self.balance_cache['USDT'] = float(free)
                        self.last_updates['balance'] = time.time()
                        logging.debug(f"💰 Balance updated: ${self.balance_cache['USDT']:.2f}")
            except Exception as e:
                if self.is_streaming:
                    logging.warning(f"Balance streaming error: {e}")
                    await asyncio.sleep(5)
    
    async def _stream_positions(self):
        """Stream position updates without triggering premature callbacks"""
        while self.is_streaming:
            try:
                positions = await self.exchange.watchPositions()
                current_positions = {}
                
                for position in positions:
                    if position and float(position.get('contracts', 0)) != 0:
                        normalized_symbol = normalize_symbol(position['symbol'])
                        
                        current_positions[normalized_symbol] = {
                            'symbol': position['symbol'],
                            'side': 'buy' if position['side'] == 'long' else 'sell',
                            'size': abs(float(position['contracts'])),
                            'entry_price': float(position.get('entryPrice', 0)),
                            'unrealized_pnl': float(position.get('unrealizedPnl', 0)),
                            'timestamp': time.time()
                        }
                
                # Update cache without triggering callbacks
                self.positions_cache = current_positions
                self.last_updates['positions'] = time.time()
                
                logging.debug(f"📊 Positions updated: {len(current_positions)} active")
                
            except Exception as e:
                if self.is_streaming:
                    logging.warning(f"Positions streaming error: {e}")
                    await asyncio.sleep(5)
    
    def subscribe_symbol(self, symbol: str):
        """Subscribe to symbol for price and OHLCV data"""
        normalized = normalize_symbol(symbol)
        if normalized.endswith('USDT'):
            self.subscribed_symbols.add(normalized)
            if self.is_streaming:
                asyncio.create_task(self._stream_symbol_data(normalized))
            logging.debug(f"📡 Subscribed to {normalized}")
    
    async def _stream_symbol_data(self, symbol: str):
        """Stream price and OHLCV data for a symbol"""
        ccxt_symbol = to_ccxt_symbol(symbol)
        
        # Start ticker streaming
        asyncio.create_task(self._stream_ticker(symbol, ccxt_symbol))
        # Start OHLCV streaming
        asyncio.create_task(self._stream_ohlcv(symbol, ccxt_symbol))
    
    async def _stream_ticker(self, base_symbol: str, ccxt_symbol: str):
        """Stream ticker data for price and volume"""
        while self.is_streaming and base_symbol in self.subscribed_symbols:
            try:
                ticker = await self.exchange.watchTicker(ccxt_symbol)
                
                self.price_cache[base_symbol] = {
                    'price': float(ticker['close']),
                    'volume_24h': float(ticker.get('quoteVolume', 0)),
                    'timestamp': time.time()
                }
                
                self.last_updates['prices'][base_symbol] = time.time()
                logging.debug(f"💹 Price updated: {base_symbol} = {ticker['close']}")
                
            except Exception as e:
                if self.is_streaming:
                    logging.debug(f"Ticker streaming error for {base_symbol}: {e}")
                    await asyncio.sleep(5)
    
    async def _stream_ohlcv(self, base_symbol: str, ccxt_symbol: str):
        """Stream OHLCV data for VWAP calculations"""
        if base_symbol not in self.ohlcv_cache:
            self.ohlcv_cache[base_symbol] = deque(maxlen=1500)
        
        while self.is_streaming and base_symbol in self.subscribed_symbols:
            try:
                ohlcv_data = await self.exchange.watchOHLCV(ccxt_symbol, '1m')
                
                for ohlcv in ohlcv_data:
                    self.ohlcv_cache[base_symbol].append(ohlcv)
                
                self.last_updates['ohlcv'][base_symbol] = time.time()
                logging.debug(f"📊 OHLCV updated: {base_symbol} ({len(self.ohlcv_cache[base_symbol])} bars)")
                
            except Exception as e:
                if self.is_streaming:
                    logging.debug(f"OHLCV streaming error for {base_symbol}: {e}")
                    await asyncio.sleep(5)
    
    # Data access methods
    def get_positions(self) -> Dict[str, Dict]:
        """Get current positions from cache with normalized symbols"""
        return self.positions_cache.copy()
    
    def get_balance(self, asset: str = 'USDT') -> float:
        """Get current balance from cache"""
        return self.balance_cache.get(asset, 0.0)
    
    def get_price(self, symbol: str) -> float:
        """Get current price from cache"""
        normalized = normalize_symbol(symbol)
        data = self.price_cache.get(normalized, {})
        return data.get('price', 0.0)
    
    def get_volume_24h(self, symbol: str) -> float:
        """Get 24h volume from cache"""
        normalized = normalize_symbol(symbol)
        data = self.price_cache.get(normalized, {})
        return data.get('volume_24h', 0.0)
    
    def get_kline_data(self, symbol: str, limit: int = 200) -> List:
        """Get kline data for VWAP calculations"""
        normalized = normalize_symbol(symbol)
        if normalized in self.ohlcv_cache:
            data = list(self.ohlcv_cache[normalized])
            if data:
                logging.debug(f"Retrieved {len(data)} kline bars for {normalized}")
                return data[-limit:] if len(data) > limit else data
        return []
    
    def is_data_fresh(self, data_type: str, symbol: str = None, max_age: int = None) -> bool:
        """Check if cached data is fresh"""
        current_time = time.time()
        
        if max_age is None:
            if data_type == 'positions':
                max_age = 30
            elif data_type == 'balance':
                max_age = 60
            else:
                max_age = 120
        
        if data_type == 'positions':
            return current_time - self.last_updates['positions'] < max_age
        elif data_type == 'balance':
            return current_time - self.last_updates['balance'] < max_age
        elif data_type == 'price' and symbol:
            normalized = normalize_symbol(symbol)
            last_update = self.last_updates['prices'].get(normalized, 0)
            return current_time - last_update < max_age
        elif data_type == 'ohlcv' and symbol:
            normalized = normalize_symbol(symbol)
            last_update = self.last_updates['ohlcv'].get(normalized, 0)
            return current_time - last_update < max_age
        
        return False
    
    async def close(self):
        """Enhanced close method"""
        self.is_streaming = False
        
        # Cancel all streaming tasks with timeout
        if self.streaming_tasks:
            for task in self.streaming_tasks:
                if not task.done():
                    task.cancel()
            
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self.streaming_tasks, return_exceptions=True),
                    timeout=3.0
                )
            except asyncio.TimeoutError:
                logging.warning("⚠️ Some streaming tasks didn't complete within timeout")
        
        # Close exchange connections
        try:
            await self.exchange.close()
        except Exception as e:
            logging.warning(f"⚠️ Exchange close error: {e}")
        
        logging.info("🔌 CCXT Pro streaming connections closed")

# Global data manager
data_manager: Optional[CCXTProDataManager] = None

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
                title = f"🔥 DCA L{dca_level} • {symbol}"
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
        """Send enhanced profit/loss alert"""
        try:
            color = 0x00ff00 if pnl_pct > 0 else 0xff0000
            emoji = "🎉" if pnl_pct > 0 else "⚠️"
            result = "WIN 🟢" if pnl_pct > 0 else "LOSS 🔴"
            
            embed = {
                "title": f"{emoji} {result} • {symbol}",
                "description": f"```{action} @ {price:.6f}```",
                "color": color,
                "fields": [
                    {
                        "name": "💵 Results",
                        "value": f"**P&L:** {pnl_pct:+.2f}%\n**USD:** ${pnl_usd:+.2f}",
                        "inline": True
                    },
                    {
                        "name": "📊 Details",
                        "value": f"**Exit:** {action}\n**Price:** {price:.6f}",
                        "inline": True
                    }
                ],
                "timestamp": datetime.utcnow().isoformat(),
                "footer": {"text": "Powered by 0xLIQD"}
            }
            
            await self.initialize()
            payload = {"username": "💰 0xLIQD P&L", "embeds": [embed]}
            
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
    """Get configured Binance USDM exchange instance with CCXT Pro"""
    return ccxtpro.binanceusdm({
        'apiKey': CONFIG.api_key,
        'secret': CONFIG.api_secret,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future',
            'sandBox': False,
            'recvWindow': 10000,
            'adjustForTimeDifference': True,
            'keepAlive': False,
        },
        'timeout': 30000,
    })

async def sync_exchange_time(exchange):
    """Sync exchange time to prevent timestamp errors"""
    try:
        # Get Binance server time
        server_time = await exchange.fetch_time()
        local_time = exchange.milliseconds()
        time_diff = server_time - local_time
        
        # Set time difference for future requests
        exchange.options['timeDifference'] = time_diff
        
        logging.info(f"Time sync: Local={local_time}, Server={server_time}, Diff={time_diff}ms")
        
    except Exception as e:
        logging.warning(f"Time sync failed: {e}")

async def get_account_balance(exchange) -> float:
    """Get account balance with CCXT Pro priority"""
    global data_manager
    
    # Try CCXT Pro stream first
    if data_manager and data_manager.is_data_fresh('balance', max_age=60):
        balance = data_manager.get_balance('USDT')
        if balance > 0:
            logging.debug(f"💰 Balance: ${balance:.2f}")
            return balance
    
    # Fallback to REST API
    try:
        balance = await exchange.fetch_balance()
        balance_value = float(balance["info"]["totalWalletBalance"])
        logging.debug(f"💰 Balance from REST API: ${balance_value:.2f}")
        return balance_value
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
                'x-rapidapi-key': CONFIG.rapidapi.api_key,
                'x-rapidapi-host': self.config.base_url.replace('https://', '').replace('http://', ''),
                'Content-Type': 'application/json'
            }
            self.session = aiohttp.ClientSession(timeout=timeout, headers=headers)
        
        await self.load_existing_zones()
        
        if not self.update_task or self.update_task.done():
            self.update_task = asyncio.create_task(self._periodic_update_loop())
    
    async def load_existing_zones(self):
        """Load zones from existing cache file"""
        try:
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
                        logging.warning(f"⚠️ RapidAPI error {response.status}: {error_text}")
                        
            except Exception as e:
                logging.error(f"⚠️ RapidAPI fetch error (attempt {attempt + 1}): {e}")
                
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
            logging.error(f"⚠️ Error processing zones data: {e}")
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
                logging.error(f"⚠️ Error in zones update loop: {e}")
                await asyncio.sleep(60)
    
    def get_zones(self, symbol: str) -> Tuple[float, float, float]:
        """Get long_price, short_price, mean_volume for symbol"""
        normalized = normalize_symbol(symbol)
        zone = self.zones_data.get(normalized, {})
        return (
            zone.get('long_price', 0),
            zone.get('short_price', 0),
            zone.get('mean_volume', 0)
        )
    
    def has_zones(self, symbol: str) -> bool:
        """Check if zones exist for symbol"""
        normalized = normalize_symbol(symbol)
        return normalized in self.zones_data.keys()
    
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
                # Proper symbol conversion using our normalize function
                base_symbol = normalize_symbol(symbol)

                if rapidapi_client.has_zones(base_symbol):
                    long_price, short_price, mean_volume = rapidapi_client.get_zones(base_symbol)
                    
                    if long_price > 0 and short_price > 0 and mean_volume > 0:
                        market_info = markets[symbol]
                        precision = market_info.get('precision', {}).get('amount', 6)
                        step_size = 10 ** -precision
                        
                        built_pairs[base_symbol] = {
                            "symbol": base_symbol,
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
            logging.error(f"⚠️ Failed to build trading pairs: {e}")
            return {}
    
    def get_pair_config(self, symbol: str) -> Optional[Dict]:
        """Get configuration for trading pair"""
        normalized = normalize_symbol(symbol)
        return self.pairs.get(normalized)
    
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
    """Enhanced VWAP calculation with CCXT Pro support"""
    
    def __init__(self):
        self.vwap_cache: Dict[str, Dict] = {}
    
    async def calculate_realtime_vwap(self, exchange, symbol: str, period: int = None) -> float:
        """Calculate real-time VWAP with CCXT Pro priority"""
        global data_manager
        
        if period is None:
            period = CONFIG.vwap.period
        
        try:
            # Try CCXT Pro data first
            if data_manager:
                kline_data = data_manager.get_kline_data(symbol, limit=min(period, 200))
                
                if len(kline_data) >= 10:
                    total_pv = 0
                    total_volume = 0
                    
                    for bar in kline_data:
                        if len(bar) >= 6:
                            typical_price = (float(bar[2]) + float(bar[3]) + float(bar[4])) / 3
                            volume = float(bar[5])
                            total_pv += typical_price * volume
                            total_volume += volume
                    
                    if total_volume > 0:
                        vwap = total_pv / total_volume
                        
                        # Cache result
                        self.vwap_cache[symbol] = {
                            'vwap': vwap,
                            'timestamp': time.time(),
                            'period': period,
                            'source': 'ccxt_pro'
                        }
                        
                        logging.debug(f"📊 VWAP: {symbol} = {vwap:.6f}")
                        return vwap
            
            # Fallback to REST API
            logging.debug(f"VWAP falling back to REST API for {symbol}")
            ccxt_symbol = to_ccxt_symbol(symbol)
            ohlcv = await exchange.fetch_ohlcv(ccxt_symbol, '1m', limit=min(period, 200))
            
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
                'period': period,
                'source': 'rest_api'
            }
            
            logging.debug(f"📊 VWAP from REST API: {symbol} = {vwap:.6f}")
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
        
        # Blend with real-time VWAP if enabled
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
# MOMENTUM DETECTOR
# =============================================================================

class MomentumDetector:
    """Detect momentum and volatility using CCXT Pro data"""
    
    def __init__(self, data_manager: CCXTProDataManager):
        self.data_manager = data_manager
        self.momentum_cache: Dict[str, Dict] = {}
    
    def calculate_momentum_metrics(self, symbol: str) -> Dict[str, float]:
        """Calculate various momentum and volatility metrics"""
        try:
            # Get kline data from CCXT Pro
            klines = self.data_manager.get_kline_data(symbol, limit=1440)
            
            if len(klines) < 10:
                logging.debug(f"Not enough kline data for {symbol}: {len(klines)} bars")
                return {}
            
            # Convert to price data
            prices = []
            highs = []
            lows = []
            volumes = []
            
            for kline in klines:
                if len(kline) >= 6:
                    prices.append(float(kline[4]))
                    highs.append(float(kline[2]))
                    lows.append(float(kline[3]))
                    volumes.append(float(kline[5]))
            
            if len(prices) < 10:
                return {}
            
            current_price = prices[-1]
            metrics = {}
            
            # 1-hour momentum
            hour_bars = min(60, len(prices) - 1)
            if hour_bars >= 10:
                hour_start = prices[-(hour_bars + 1)]
                metrics['1h_change_pct'] = ((current_price - hour_start) / hour_start) * 100
            else:
                metrics['1h_change_pct'] = 0
            
            # 4-hour momentum
            four_hour_bars = min(240, len(prices) - 1)
            if four_hour_bars >= 60:
                four_hour_start = prices[-(four_hour_bars + 1)]
                metrics['4h_change_pct'] = ((current_price - four_hour_start) / four_hour_start) * 100
            else:
                metrics['4h_change_pct'] = 0
            
            # 24-hour momentum
            if len(prices) > 1:
                day_start = prices[0]
                metrics['24h_change_pct'] = ((current_price - day_start) / day_start) * 100
            else:
                metrics['24h_change_pct'] = 0
            
            # Volatility metrics
            if len(highs) >= 10 and len(lows) >= 10:
                recent_high = max(highs)
                recent_low = min(lows)
                metrics['daily_range_pct'] = ((recent_high - recent_low) / recent_low) * 100 if recent_low > 0 else 0
            
            # Price volatility (standard deviation)
            if len(prices) > 1:
                price_changes = []
                for i in range(1, len(prices)):
                    if prices[i-1] > 0:
                        change = (prices[i] - prices[i-1]) / prices[i-1]
                        price_changes.append(change)
                
                if price_changes:
                    metrics['volatility_1h'] = float(np.std(price_changes[-min(60, len(price_changes)):]) * 100)
                    metrics['volatility_4h'] = float(np.std(price_changes[-min(240, len(price_changes)):]) * 100)
                else:
                    metrics['volatility_1h'] = 0
                    metrics['volatility_4h'] = 0
            
            # Volume spike detection
            if volumes:
                recent_volume_bars = min(240, len(volumes))
                if recent_volume_bars >= 10:
                    avg_volume = sum(volumes[-recent_volume_bars:]) / recent_volume_bars
                    current_volume_bars = min(60, len(volumes))
                    current_volume = sum(volumes[-current_volume_bars:]) / current_volume_bars if current_volume_bars > 0 else volumes[-1]
                    metrics['volume_spike_ratio'] = current_volume / avg_volume if avg_volume > 0 else 1
                else:
                    metrics['volume_spike_ratio'] = 1
            else:
                metrics['volume_spike_ratio'] = 1
            
            # Cache the results
            self.momentum_cache[symbol] = {
                'metrics': metrics,
                'timestamp': time.time(),
                'price': current_price,
                'bars_used': len(prices)
            }
            
            logging.debug(f"Calculated momentum for {symbol}: {metrics}")
            return metrics
            
        except Exception as e:
            logging.warning(f"Momentum calculation failed for {symbol}: {e}")
            return {}
    
    def get_momentum_signal(self, symbol: str) -> Tuple[str, str, Dict]:
        """Get momentum signal for a symbol"""
        try:
            # First ensure we have data by subscribing
            self.data_manager.subscribe_symbol(symbol)
            
            # Get or calculate metrics
            cached = self.momentum_cache.get(symbol)
            if cached and time.time() - cached['timestamp'] < 300:
                metrics = cached['metrics']
            else:
                metrics = self.calculate_momentum_metrics(symbol)
            
            if not metrics:
                return "ALLOW", "No momentum data", {}
            
            # Extract key metrics with defaults
            change_1h = metrics.get('1h_change_pct', 0)
            change_4h = metrics.get('4h_change_pct', 0)
            change_24h = metrics.get('24h_change_pct', 0)
            daily_range = metrics.get('daily_range_pct', 10)
            volume_spike = metrics.get('volume_spike_ratio', 1)
            
            # Apply strategy based on mode
            mode = CONFIG.momentum.momentum_mode
            
            if mode == "AVOID_EXTREMES":
                # Avoid entering positions on extreme moves
                if change_24h >= CONFIG.momentum.daily_pump_threshold:
                    return "AVOID", f"Over-pumped: +{change_24h:.1f}% (24h)", metrics
                elif change_24h <= CONFIG.momentum.daily_dump_threshold:
                    return "AVOID", f"Over-dumped: {change_24h:.1f}% (24h)", metrics
                elif change_1h >= CONFIG.momentum.hourly_pump_threshold:
                    return "AVOID", f"Recent pump: +{change_1h:.1f}% (1h)", metrics
                elif change_1h <= CONFIG.momentum.hourly_dump_threshold:
                    return "AVOID", f"Recent dump: {change_1h:.1f}% (1h)", metrics
                elif daily_range >= CONFIG.momentum.max_daily_volatility:
                    return "AVOID", f"Excessive volatility: {daily_range:.1f}%", metrics
                elif daily_range <= CONFIG.momentum.min_daily_volatility and daily_range > 0:
                    return "AVOID", f"Low volatility: {daily_range:.1f}%", metrics
                else:
                    return "ALLOW", f"Normal momentum: {change_24h:+.1f}% (24h), {change_1h:+.1f}% (1h), range: {daily_range:.1f}%", metrics
            
            return "ALLOW", "Default allow", metrics
            
        except Exception as e:
            logging.error(f"Momentum signal error for {symbol}: {e}")
            return "ALLOW", "Error in momentum calculation", {}

# =============================================================================
# MARKET REGIME DETECTOR
# =============================================================================

class MarketRegimeDetector:
    """Market regime detection using ADX + ATR with CCXT Pro support"""
    
    def __init__(self):
        self.regime_cache: Dict[str, Dict] = {}
    
    async def detect_regime(self, exchange, symbol: str) -> str:
        """Detect market regime with CCXT Pro priority"""
        global data_manager
        
        try:
            # Try CCXT Pro data first
            if data_manager:
                kline_data = data_manager.get_kline_data(symbol, limit=50)
                if len(kline_data) >= 30:
                    adx = self._calculate_adx(kline_data)
                    
                    if len(kline_data) >= 10:
                        current_price = float(kline_data[-1][4])
                        old_price = float(kline_data[-10][4])
                        price_change_pct = ((current_price - old_price) / old_price) * 100
                    else:
                        price_change_pct = 0
                    
                    regime = self._classify_regime(adx, price_change_pct)
                    
                    # Cache result
                    self.regime_cache[symbol] = {
                        'regime': regime,
                        'adx': adx,
                        'timestamp': time.time(),
                        'source': 'ccxt_pro'
                    }
                    
                    logging.debug(f"📈 Regime: {symbol} = {regime}")
                    return regime
            
            # Fallback to REST API
            logging.debug(f"Regime falling back to REST API for {symbol}")
            ccxt_symbol = to_ccxt_symbol(symbol)
            ohlcv = await exchange.fetch_ohlcv(ccxt_symbol, '5m', limit=50)
            
            if len(ohlcv) < 30:
                return "UNKNOWN"
            
            adx = self._calculate_adx(ohlcv)
            price_change_pct = ((ohlcv[-1][4] - ohlcv[-10][4]) / ohlcv[-10][4]) * 100
            regime = self._classify_regime(adx, price_change_pct)
            
            # Cache result
            self.regime_cache[symbol] = {
                'regime': regime,
                'adx': adx,
                'timestamp': time.time(),
                'source': 'rest_api'
            }
            
            logging.debug(f"📈 Regime from REST API: {symbol} = {regime}")
            return regime
            
        except Exception as e:
            logging.warning(f"Regime detection failed for {symbol}: {e}")
            return "UNKNOWN"
    
    def _classify_regime(self, adx: float, price_change_pct: float) -> str:
        """Classify market regime based on ADX and price change"""
        if adx > CONFIG.market_regime.trend_threshold:
            if price_change_pct > 0.5:
                return "TREND_UP"
            elif price_change_pct < -0.5:
                return "TREND_DOWN"
            else:
                return "TREND_WEAK"
        elif adx < CONFIG.market_regime.range_threshold:
            return "RANGE"
        else:
            return "VOLATILE"
    
    def _calculate_adx(self, ohlcv: List) -> float:
        """Calculate ADX"""
        try:
            period = CONFIG.market_regime.adx_period
            if len(ohlcv) < period + 1:
                return 20
            
            tr_values = []
            for i in range(1, len(ohlcv)):
                high, low, close = float(ohlcv[i][2]), float(ohlcv[i][3]), float(ohlcv[i][4])
                prev_close = float(ohlcv[i-1][4])
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
# POSITION MANAGEMENT
# =============================================================================

@dataclass
class Position:
    symbol: str
    side: str
    total_qty: float = 0
    avg_entry_price: float = 0
    dca_count: int = 0
    total_notional_used: float = 0
    entry_time: float = field(default_factory=time.time)
    tp_price: float = 0
    sl_price: float = 0
    tp_client_id: str = ""
    tp_order_id: str = ""
    vwap_reference: float = 0
    regime: str = "UNKNOWN"
    alert_sent: bool = False

class PositionManager:
    """Position manager with proper symbol handling"""
    
    def __init__(self):
        self.positions: Dict[str, Position] = {}  # Always use normalized symbols
        self.total_trades = 0
        self._symbol_locks: Dict[str, asyncio.Lock] = {}
        self.recent_attempts: Dict[str, float] = {}
        self.attempt_cooldown = 10
        self._global_position_creation_lock = asyncio.Lock()

        self._recent_closures: Dict[str, float] = {}
        self._closure_grace_period = 3.0  # 3 seconds grace period

        # Track position states to prevent false closures
        self._position_states: Dict[str, Dict] = {}  # Track last known state
        self._closure_confirmations: Dict[str, int] = {}  # Require multiple confirmations
        self._required_confirmations = 3  # Need 3 consecutive checks to confirm closure
        
        # Order monitoring
        self.order_monitoring_task: Optional[asyncio.Task] = None
        self.is_monitoring_orders = False
    
    def get_symbol_lock(self, symbol: str) -> asyncio.Lock:
        """Get or create symbol-specific lock"""
        normalized = normalize_symbol(symbol)
        if normalized not in self._symbol_locks:
            self._symbol_locks[normalized] = asyncio.Lock()
        return self._symbol_locks[normalized]
    
    async def get_authoritative_positions(self, exchange) -> set:
        """Enhanced position checking with fallback verification"""
        global data_manager
        
        try:
            # Try CCXT Pro first
            ccxt_positions = set()
            if data_manager and data_manager.is_data_fresh('positions', max_age=15):
                ccxt_positions = set(data_manager.get_positions().keys())
                
            # Always verify with REST API for accuracy
            rest_positions = await exchange.fetch_positions()
            rest_position_symbols = {
                normalize_symbol(p['symbol']) 
                for p in rest_positions 
                if float(p.get('contracts', 0)) != 0
            }
            
            # Use the union of both sources to avoid false negatives
            all_positions = ccxt_positions | rest_position_symbols
            
            logging.debug(f"📊 CCXT: {ccxt_positions}, REST: {rest_position_symbols}, COMBINED: {all_positions}")
            
            return all_positions
            
        except Exception as e:
            logging.error(f"Failed to get authoritative positions: {e}")
            return set(self.positions.keys())
    
    async def start_order_monitoring(self, exchange):
        """Less aggressive order monitoring"""
        if not self.is_monitoring_orders:
            self.is_monitoring_orders = True
            self.order_monitoring_task = asyncio.create_task(self._monitor_orders_and_positions(exchange))
            logging.info("🔍 Order monitoring started")
    
    async def stop_order_monitoring(self):
        """Enhanced stop monitoring"""
        self.is_monitoring_orders = False
        
        if self.order_monitoring_task and not self.order_monitoring_task.done():
            self.order_monitoring_task.cancel()
            try:
                await asyncio.wait_for(self.order_monitoring_task, timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        
        logging.info("🛑 Order monitoring stopped")
    
    async def _monitor_orders_and_positions(self, exchange):
        """Monitoring with closure confirmation system"""
        
        while self.is_monitoring_orders:
            try:
                current_positions = await self.get_authoritative_positions(exchange)
                
                # Update position states
                for symbol in current_positions:
                    self._position_states[symbol] = {
                        'last_seen': time.time(),
                        'status': 'active'
                    }
                    # Reset closure confirmation if position is active
                    if symbol in self._closure_confirmations:
                        self._closure_confirmations[symbol] = 0
                
                # Check for potential closures (positions we track but aren't in current_positions)
                tracked_positions = set(self.positions.keys())
                potentially_closed = tracked_positions - current_positions
                
                for symbol in potentially_closed:
                    # CRITICAL FIX: Require multiple confirmations before considering closed
                    if symbol not in self._closure_confirmations:
                        self._closure_confirmations[symbol] = 0
                    
                    self._closure_confirmations[symbol] += 1
                    
                    logging.debug(f"🔍 {symbol} potentially closed - confirmation {self._closure_confirmations[symbol]}/{self._required_confirmations}")
                    
                    # Only process closure after multiple confirmations
                    if self._closure_confirmations[symbol] >= self._required_confirmations:
                        logging.info(f"🗑️ Position {symbol} CONFIRMED closed after {self._required_confirmations} checks")
                        
                        # Add to recent closures tracking
                        self._recent_closures[symbol] = time.time()
                        
                        # Clean up orders and handle closure
                        await self.cleanup_all_orders_for_symbol(exchange, symbol)
                        await self._handle_position_closure(exchange, symbol)
                        
                        # Clean up tracking
                        del self._closure_confirmations[symbol]
                        if symbol in self._position_states:
                            del self._position_states[symbol]
                
                # Clean up old closure entries
                current_time = time.time()
                expired_closures = [
                    symbol for symbol, closure_time in self._recent_closures.items()
                    if current_time - closure_time > self._closure_grace_period * 2
                ]
                for symbol in expired_closures:
                    del self._recent_closures[symbol]
                
                # Clean up old confirmation entries for positions that are back
                for symbol in list(self._closure_confirmations.keys()):
                    if symbol in current_positions:
                        del self._closure_confirmations[symbol]
                
                await asyncio.sleep(5)  # Check every 5 seconds
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Order monitoring loop error: {e}")
                await asyncio.sleep(10)
    
    async def cleanup_all_orders_for_symbol(self, exchange, symbol):
        """Order cleanup with proper symbol conversion"""
        normalized_symbol = normalize_symbol(symbol)
        
        try:
            ccxt_symbol = to_ccxt_symbol(normalized_symbol)
            logging.debug(f"🧹 Cleaning orders for {normalized_symbol} (CCXT: {ccxt_symbol})")
            
            open_orders = await exchange.fetch_open_orders(ccxt_symbol)
            
            cancelled = 0
            for order in open_orders:
                try:
                    client_id = (order.get("clientOrderId") or 
                               order.get("info", {}).get("clientOrderId") or "")
                    
                    # Only cancel our orders
                    if client_id and any(client_id.startswith(prefix) for prefix in ['TP-0xLIQD', 'SL-0xLIQD']):
                        await exchange.cancel_order(order['id'], ccxt_symbol)
                        logging.info(f"✅ Cancelled order {order['id']} for {normalized_symbol}")
                        cancelled += 1
                        await asyncio.sleep(0.1)
                        
                except Exception as e:
                    logging.warning(f"⚠️ Failed to cancel order {order.get('id', 'unknown')}: {e}")
            
            if cancelled > 0:
                logging.info(f"🧹 Cleaned up {cancelled} orders for {normalized_symbol}")
                
        except Exception as e:
            logging.warning(f"⚠️ Cleanup failed for {normalized_symbol}: {e}")
    
    async def _handle_position_closure(self, exchange, symbol: str):
        """Handle position closure with P&L calculation"""
        try:
            normalized_symbol = normalize_symbol(symbol)
            
            if normalized_symbol not in self.positions:
                return
            
            position = self.positions[normalized_symbol]
            
            # Get exit price
            exit_price = await self._get_exit_price(exchange, normalized_symbol)
            
            # Calculate P&L
            if position.side == "buy":
                pnl_pct = (exit_price - position.avg_entry_price) / position.avg_entry_price
            else:
                pnl_pct = (position.avg_entry_price - exit_price) / position.avg_entry_price
            
            pnl_usd = position.total_notional_used * pnl_pct
            
            # Determine action
            action = self._determine_exit_action(position, exit_price)
            
            # Send Discord notification
            if discord_notifier:
                await discord_notifier.send_profit_alert(
                    normalized_symbol, pnl_pct * 100, pnl_usd, action, exit_price
                )
            
            # Remove position
            del self.positions[normalized_symbol]
            
            logging.info(f"💰 Position closed: {normalized_symbol} | "
                        f"P&L: {pnl_pct*100:+.2f}% (${pnl_usd:+.2f}) | "
                        f"Entry: {position.avg_entry_price:.6f} | "
                        f"Exit: {exit_price:.6f}")
                        
        except Exception as e:
            logging.error(f"⚠️ Failed to handle position closure for {symbol}: {e}")
    
    async def _get_exit_price(self, exchange, symbol: str) -> float:
        """Get exit price with multiple fallbacks"""
        global data_manager
        
        normalized_symbol = normalize_symbol(symbol)
        
        # Try CCXT Pro price first
        if data_manager:
            price = data_manager.get_price(normalized_symbol)
            if price > 0:
                return price
        
        # Fallback to REST API
        try:
            ccxt_symbol = to_ccxt_symbol(normalized_symbol)
            ticker = await exchange.fetch_ticker(ccxt_symbol)
            return float(ticker['close'])
        except:
            # Use position entry price as absolute fallback
            position = self.positions.get(normalized_symbol)
            return position.avg_entry_price if position else 0
    
    def _determine_exit_action(self, position: Position, exit_price: float) -> str:
        """Determine what caused the position exit"""
        if position.side == "buy":
            if exit_price >= position.avg_entry_price * (1 + CONFIG.profit_protection.initial_tp_pct * 0.9):
                return "TAKE PROFIT"
            elif CONFIG.profit_protection.enable_stop_loss and exit_price <= position.avg_entry_price * (1 - CONFIG.profit_protection.stop_loss_pct * 1.1):
                return "STOP LOSS"
        else:
            if exit_price <= position.avg_entry_price * (1 - CONFIG.profit_protection.initial_tp_pct * 0.9):
                return "TAKE PROFIT"
            elif CONFIG.profit_protection.enable_stop_loss and exit_price >= position.avg_entry_price * (1 + CONFIG.profit_protection.stop_loss_pct * 1.1):
                return "STOP LOSS"
        
        return "POSITION CLOSED"
    
    async def create_position(self, exchange, symbol: str, side: str, liq_price: float,
                         liq_size: float, vwap_ref: float, regime: str,
                         balance: float) -> Optional[Position]:
        """Enhanced position creation with better verification"""
        
        normalized_symbol = normalize_symbol(symbol)
        symbol_lock = self.get_symbol_lock(normalized_symbol)
        
        async with symbol_lock:
            async with self._global_position_creation_lock:
                
                # Check if recently closed
                current_time = time.time()
                if normalized_symbol in self._recent_closures:
                    time_since_closure = current_time - self._recent_closures[normalized_symbol]
                    if time_since_closure < self._closure_grace_period:
                        logging.info(f"⚠️ {normalized_symbol}: Recently closed {time_since_closure:.1f}s ago, skipping")
                        return None
                
                # Enhanced position checking with multiple attempts
                max_attempts = 3
                for attempt in range(max_attempts):
                    authoritative_positions = await self.get_authoritative_positions(exchange)
                    
                    logging.info(f"🔍 Position check attempt {attempt + 1}: {len(authoritative_positions)} active positions")
                    logging.info(f"🔍 Active positions: {authoritative_positions}")
                    
                    # STRICT max position enforcement
                    if len(authoritative_positions) >= CONFIG.risk.max_positions:
                        logging.warning(f"⚠️ MAX POSITIONS REACHED: {len(authoritative_positions)}/{CONFIG.risk.max_positions}")
                        stats.log_filter_rejection("max_positions")
                        return None
                    
                    # Check if position already exists
                    if normalized_symbol in authoritative_positions:
                        logging.info(f"⚠️ Position already exists: {normalized_symbol}")
                        stats.log_filter_rejection("existing_position")
                        return None
                    
                    # If checks pass, proceed
                    break
                    
                    # Brief delay before retry
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(0.5)
                
                # Rate limiting
                last_attempt = self.recent_attempts.get(normalized_symbol, 0)
                if current_time - last_attempt < self.attempt_cooldown:
                    logging.info(f"⚠️ {normalized_symbol}: Cooldown active")
                    return None
                self.recent_attempts[normalized_symbol] = current_time
                
                # Isolation check
                total_notional_used = sum(p.total_notional_used for p in self.positions.values())
                isolation_limit = balance * CONFIG.risk.isolation_pct * CONFIG.leverage
                if total_notional_used + CONFIG.min_notional > isolation_limit:
                    logging.info(f"⚠️ {normalized_symbol}: Isolation limit reached")
                    stats.log_filter_rejection('isolation_limit')
                    return None
                
                # Get pair config
                pair_config = pairs_builder.get_pair_config(normalized_symbol)
                if not pair_config:
                    logging.error(f"⚠️ {normalized_symbol}: No pair config found")
                    return None
                
                # Calculate position size
                entry_notional = CONFIG.min_notional
                entry_qty = entry_notional / liq_price
                
                # Apply quantity rounding
                step_size = pair_config.get('step_size', 0.001)
                entry_qty = round(entry_qty / step_size) * step_size
                actual_notional = entry_qty * liq_price
                
                # Create position object
                position = Position(
                    symbol=normalized_symbol,
                    side=side,
                    total_qty=entry_qty,
                    avg_entry_price=liq_price,
                    total_notional_used=actual_notional,
                    vwap_reference=vwap_ref,
                    regime=regime
                )
                
                self.positions[normalized_symbol] = position
                self.total_trades += 1
                
                # Initialize position state tracking
                self._position_states[normalized_symbol] = {
                    'last_seen': current_time,
                    'status': 'active'
                }
                
                logging.info(f"✅ Position created: {normalized_symbol} {side} {entry_qty:.6f} @ {liq_price:.6f}")
                
                return position
    
    async def check_dca_trigger(self, symbol: str, current_price: float) -> Tuple[bool, int]:
        """Check if DCA should be triggered"""
        normalized_symbol = normalize_symbol(symbol)
        
        if normalized_symbol not in self.positions:
            return False, -1
        
        position = self.positions[normalized_symbol]
        
        # Check limits
        if position.dca_count >= CONFIG.dca.max_levels:
            return False, -1
        
        # Calculate adverse move percentage
        if position.side == "buy":
            adverse_move = (position.avg_entry_price - current_price) / position.avg_entry_price
        else:
            adverse_move = (current_price - position.avg_entry_price) / position.avg_entry_price
        
        # Check trigger
        next_level = position.dca_count
        if next_level < len(CONFIG.dca.trigger_pcts):
            trigger_threshold = CONFIG.dca.trigger_pcts[next_level]
            if adverse_move >= trigger_threshold:
                return True, next_level + 1
        
        return False, -1
    
    async def execute_dca(self, exchange, symbol: str, current_price: float,
                         dca_level: int) -> bool:
        """Execute DCA with enhanced order management"""
        normalized_symbol = normalize_symbol(symbol)
        symbol_lock = self.get_symbol_lock(normalized_symbol)
        
        async with symbol_lock:
            if normalized_symbol not in self.positions:
                return False
            
            position = self.positions[normalized_symbol]
            
            # Get pair config
            pair_config = pairs_builder.get_pair_config(normalized_symbol)
            if not pair_config:
                logging.error(f"⚠️ DCA failed: No pair config for {normalized_symbol}")
                return False
            
            # Calculate DCA size
            size_multiplier = 1.0
            if dca_level - 1 < len(CONFIG.dca.size_multipliers):
                size_multiplier = CONFIG.dca.size_multipliers[dca_level - 1]
            
            dca_notional = CONFIG.min_notional * size_multiplier
            dca_qty = dca_notional / current_price
            
            # Apply rounding
            step_size = pair_config.get('step_size', 0.001)
            dca_qty = round(dca_qty / step_size) * step_size
            actual_notional = dca_qty * current_price
            
            # Isolation check
            total_notional_used = sum(p.total_notional_used for p in self.positions.values())
            balance = await get_account_balance(exchange)
            isolation_limit = balance * CONFIG.risk.isolation_pct * CONFIG.leverage
            
            if total_notional_used + actual_notional > isolation_limit:
                logging.info(f"⚠️ DCA L{dca_level} {normalized_symbol}: Would exceed isolation limit")
                stats.log_dca_attempt(False)
                return False
            
            try:
                # Execute DCA order
                ccxt_symbol = to_ccxt_symbol(normalized_symbol)
                order = await exchange.create_market_order(
                    ccxt_symbol, position.side, dca_qty,
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
                
                logging.info(f"✅ DCA L{dca_level}: {normalized_symbol} {dca_qty:.6f} @ {actual_price:.6f} | "
                           f"Multiplier: {size_multiplier}x | New Avg: {new_avg_price:.6f}")
                
                # Send Discord notification
                if discord_notifier:
                    await discord_notifier.send_trade_alert(
                        normalized_symbol, position.side, dca_qty, actual_price,
                        f"DCA Level {dca_level} (${CONFIG.min_notional} × {size_multiplier}x)", actual_notional,
                        is_dca=True, dca_level=dca_level
                    )
                
                stats.log_dca_attempt(True)
                return True
                
            except Exception as e:
                logging.error(f"⚠️ DCA execution failed for {normalized_symbol} L{dca_level}: {e}")
                stats.log_dca_attempt(False)
                return False
    
    def get_position_count(self) -> int:
        """Get count of local positions"""
        return len(self.positions)
    
    async def get_total_position_count(self, exchange) -> int:
        """Get accurate count from authoritative source"""
        authoritative_positions = await self.get_authoritative_positions(exchange)
        return len(authoritative_positions)
    
    def has_position(self, symbol: str) -> bool:
        normalized_symbol = normalize_symbol(symbol)
        return normalized_symbol in self.positions
    
    def remove_position(self, symbol: str):
        normalized_symbol = normalize_symbol(symbol)
        if normalized_symbol in self.positions:
            del self.positions[normalized_symbol]
            
        # Clean up tracking data
        if normalized_symbol in self._position_states:
            del self._position_states[normalized_symbol]
        if normalized_symbol in self._closure_confirmations:
            del self._closure_confirmations[normalized_symbol]

position_manager = PositionManager()

# =============================================================================
# PROFIT PROTECTION SYSTEM
# =============================================================================

class ProfitProtectionSystem:
    """Profit protection with proper order management"""
    
    def __init__(self):
        self.protection_cache: Dict[str, Dict] = {}
        self.order_registry: Dict[str, Dict] = {}
    
    async def set_initial_take_profit(self, exchange, position: Position) -> bool:
        """Set initial take profit"""
        try:
            normalized_symbol = normalize_symbol(position.symbol)
            
            # Calculate TP price
            if position.side == "buy":
                tp_price = position.avg_entry_price * (1 + CONFIG.profit_protection.initial_tp_pct)
                sl_price = position.avg_entry_price * (1 - CONFIG.profit_protection.stop_loss_pct) if CONFIG.profit_protection.enable_stop_loss else 0
            else:
                tp_price = position.avg_entry_price * (1 - CONFIG.profit_protection.initial_tp_pct)
                sl_price = position.avg_entry_price * (1 + CONFIG.profit_protection.stop_loss_pct) if CONFIG.profit_protection.enable_stop_loss else 0

            position.tp_price = tp_price

            # Create unique but identifiable client IDs
            timestamp = int(time.time() * 1000)
            tp_client_id = f"TP-0xLIQD-{normalized_symbol}-{timestamp}"
            sl_client_id = f"SL-0xLIQD-{normalized_symbol}-{timestamp}"

            # Place TP order
            tp_side = "sell" if position.side == "buy" else "buy"
            ccxt_symbol = to_ccxt_symbol(normalized_symbol)

            tp_order = await exchange.create_limit_order(
                ccxt_symbol, tp_side, position.total_qty, tp_price,
                params={
                    "positionSide": "LONG" if position.side == "buy" else "SHORT",
                    "newClientOrderId": tp_client_id,
                    "timeInForce": "GTC"
                }
            )

            # Store order tracking info
            position.tp_client_id = tp_client_id
            position.tp_order_id = str(tp_order.get('id', ''))
            
            self.order_registry[normalized_symbol] = {
                'tp_client_id': tp_client_id,
                'tp_order_id': position.tp_order_id,
                'timestamp': timestamp
            }

            logging.info(f"✅ TP set: {normalized_symbol} @ {tp_price:.6f} ({CONFIG.profit_protection.initial_tp_pct*100:.1f}%)")

            # Place SL order if enabled
            if CONFIG.profit_protection.enable_stop_loss and sl_price > 0:
                try:
                    sl_side = "sell" if position.side == "buy" else "buy"
                    sl_order = await exchange.create_order(
                        ccxt_symbol,
                        'STOP_MARKET',
                        sl_side,
                        position.total_qty,
                        None,
                        params={
                            "stopPrice": sl_price,
                            "positionSide": "LONG" if position.side == "buy" else "SHORT",
                            "newClientOrderId": sl_client_id
                        }
                    )
                    
                    position.sl_price = sl_price
                    position.sl_client_id = sl_client_id
                    position.sl_order_id = str(sl_order.get('id', ''))
                    
                    self.order_registry[normalized_symbol].update({
                        'sl_client_id': sl_client_id,
                        'sl_order_id': position.sl_order_id
                    })
                    
                    logging.info(f"✅ SL set: {normalized_symbol} @ {sl_price:.6f}")
                    
                except Exception as sl_error:
                    logging.warning(f"⚠️ Stop loss setup failed for {normalized_symbol}: {sl_error}")

            return True

        except Exception as e:
            logging.error(f"⚠️ TP setup failed {position.symbol}: {e}")
            return False
    
    async def update_orders_after_dca(self, exchange, position: Position) -> bool:
        """Update TP/SL orders after DCA execution"""
        try:
            normalized_symbol = normalize_symbol(position.symbol)
            
            # Cancel existing orders first
            ccxt_symbol = to_ccxt_symbol(normalized_symbol)
            orders = await exchange.fetch_open_orders(ccxt_symbol)

            for order in orders:
                client_id = order.get("clientOrderId") or order.get("info", {}).get("clientOrderId") or order.get("info", {}).get("newClientOrderId")
                platform_id = order.get("id")
                
                # Only cancel our specific orders
                if (getattr(position, "tp_client_id", None) and client_id == position.tp_client_id) or \
                   (getattr(position, "tp_order_id", None) and platform_id == position.tp_order_id):
                    try:
                        await exchange.cancel_order(platform_id, ccxt_symbol)
                        logging.debug(f"Cancelled TP order {platform_id} for DCA update")
                    except Exception as e:
                        if '-2011' in str(e) or 'Unknown order' in str(e):
                            logging.debug(f"Ignored unknown order {platform_id}")
                        else:
                            logging.warning(f"Failed to cancel order {platform_id}: {e}")
            
            # Set new orders with updated position size
            return await self.set_initial_take_profit(exchange, position)
            
        except Exception as e:
            logging.error(f"Failed to update orders after DCA for {position.symbol}: {e}")
            return False

profit_protection = ProfitProtectionSystem()

# =============================================================================
# MAIN STRATEGY
# =============================================================================

class VWAPHunterStrategy:
    """Enhanced VWAP Hunter with CCXT Pro integration"""
    
    def __init__(self, exchange):
        self.exchange = exchange
        self.price_cache: Dict[str, float] = {}
        self.last_price_update: Dict[str, float] = {}
        self.entry_timestamps: Dict[str, float] = {}
        
        # Add momentum detector
        global data_manager
        if data_manager:
            self.momentum_detector = MomentumDetector(data_manager)
        else:
            self.momentum_detector = None
    
    async def get_current_price(self, symbol: str) -> float:
        """Get current price with CCXT Pro priority"""
        global data_manager
        
        normalized_symbol = normalize_symbol(symbol)
        
        # Try CCXT Pro first
        if data_manager:
            price = data_manager.get_price(normalized_symbol)
            if price > 0:
                self.price_cache[normalized_symbol] = price
                self.last_price_update[normalized_symbol] = time.time()
                return price
            
            # Subscribe to symbol if not already subscribed
            data_manager.subscribe_symbol(normalized_symbol)
        
        # Fallback to cached price or REST API
        current_time = time.time()
        if (normalized_symbol in self.price_cache and
            current_time - self.last_price_update.get(normalized_symbol, 0) < 60):
            return self.price_cache[normalized_symbol]
        
        # REST API fallback
        try:
            ccxt_symbol = to_ccxt_symbol(normalized_symbol)
            ticker = await self.exchange.fetch_ticker(ccxt_symbol)
            price = float(ticker['close'])
            self.price_cache[normalized_symbol] = price
            self.last_price_update[normalized_symbol] = current_time
            logging.debug(f"Price from REST API: {normalized_symbol} = {price}")
            return price
        except Exception as e:
            if CONFIG.debug.enable_data_debug:
                logging.warning(f"Price fetch failed for {normalized_symbol}: {e}")
            return self.price_cache.get(normalized_symbol, 0)
    
    async def check_volume_filter(self, symbol: str) -> Tuple[bool, float]:
        """Check volume with CCXT Pro priority"""
        global data_manager
        
        normalized_symbol = normalize_symbol(symbol)
        
        # Try CCXT Pro first
        if data_manager:
            daily_volume = data_manager.get_volume_24h(normalized_symbol)
            if daily_volume > 0:
                passed = daily_volume >= CONFIG.risk.min_24h_volume
                if CONFIG.debug.enable_filter_debug:
                    debug_logger = logging.getLogger('trade_debug')
                    debug_logger.debug(f"Volume filter {normalized_symbol}: ${daily_volume:,.0f} ({'PASS' if passed else 'FAIL'})")
                return passed, daily_volume
            
            # Subscribe to symbol for future updates
            data_manager.subscribe_symbol(normalized_symbol)
        
        # Fallback to REST API
        try:
            ccxt_symbol = to_ccxt_symbol(normalized_symbol)
            ticker = await self.exchange.fetch_ticker(ccxt_symbol)
            daily_volume = float(ticker.get("quoteVolume", 0))
            passed = daily_volume >= CONFIG.risk.min_24h_volume
            
            if CONFIG.debug.enable_filter_debug:
                debug_logger = logging.getLogger('trade_debug')
                debug_logger.debug(f"Volume filter {normalized_symbol}: ${daily_volume:,.0f} ({'PASS' if passed else 'FAIL'})")
            
            return passed, daily_volume
        except Exception as e:
            if CONFIG.debug.enable_data_debug:
                logging.warning(f"Volume check failed for {normalized_symbol}: {e}")
            return False, 0
    
    async def process_liquidation_event(self, liquidation_data: Dict):
        """Process liquidation event with comprehensive filtering"""
        start_time = time.time()
        
        try:
            # Parse liquidation data
            data = liquidation_data.get('o', {})
            symbol = data.get('s', '')
            if not symbol or not symbol.endswith('USDT'):
                return
            
            # Normalize symbol immediately
            normalized_symbol = normalize_symbol(symbol)
            
            liq_price = float(data.get('ap', 0))
            liq_qty = float(data.get('q', 0))
            liq_side = data.get('S', '')
            
            if liq_price <= 0 or liq_qty <= 0 or not liq_side:
                return
            
            liq_size_usd = liq_price * liq_qty
            our_side = "buy" if liq_side == "SELL" else "sell"
            
            # Ensure CCXT Pro subscription
            global data_manager
            if data_manager:
                data_manager.subscribe_symbol(normalized_symbol)
            
            # Log all liquidations if enabled
            if CONFIG.debug.log_all_liquidations:
                logging.info(f"📡 LIQUIDATION: {normalized_symbol} | {liq_side} | {liq_qty:.4f} @ {liq_price:.6f} | ${liq_size_usd:,.0f}")
            
            # Update statistics
            stats.log_liquidation(normalized_symbol, liq_size_usd)
            
            # Entry cooldown check
            cooldown = 30
            last = self.entry_timestamps.get(normalized_symbol, 0)
            if time.time() - last < cooldown:
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"⚠️ {normalized_symbol}: Entry cooldown active ({time.time()-last:.0f}s elapsed)")
                return
            
            # Enhanced filter debugging
            if CONFIG.debug.enable_trade_debug:
                debug_logger = logging.getLogger('trade_debug')
                debug_logger.debug(f"🔍 ANALYZING: {normalized_symbol} | Liq: {liq_side} @ {liq_price:.6f} | Our Side: {our_side}")
            
            # Filter 1: Pair enabled check
            if not pairs_builder.is_pair_enabled(normalized_symbol):
                stats.log_filter_rejection('pair_not_enabled')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"⚠️ {normalized_symbol}: Pair not enabled")
                return
            
            # Filter 2: Enhanced volume check
            volume_pass, daily_volume = await self.check_volume_filter(normalized_symbol)
            if not volume_pass:
                stats.log_filter_rejection('volume_filter')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"⚠️ {normalized_symbol}: Volume too low (${daily_volume:,.0f} < ${CONFIG.risk.min_24h_volume:,.0f})")
                return
            
            # Filter 3: Enhanced momentum filter
            if CONFIG.momentum.enable_momentum_filter and self.momentum_detector:
                if data_manager:
                    data_manager.subscribe_symbol(normalized_symbol)
                    await asyncio.sleep(0.1)
                
                momentum_signal, momentum_reason, momentum_metrics = self.momentum_detector.get_momentum_signal(normalized_symbol)
                
                if momentum_signal == "AVOID":
                    stats.log_filter_rejection('momentum_filter')
                    if CONFIG.debug.enable_filter_debug:
                        logging.info(f"⚠️ {normalized_symbol}: Momentum filter - {momentum_reason}")
                    return
                
                if CONFIG.debug.enable_trade_debug:
                    debug_logger = logging.getLogger('trade_debug')
                    debug_logger.debug(f"📈 MOMENTUM {normalized_symbol}: {momentum_reason}")
            
            # Filter 4: Zones data check
            if not rapidapi_client.has_zones(normalized_symbol):
                stats.log_filter_rejection('no_zones')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"⚠️ {normalized_symbol}: No price zones data")
                return
            
            # Filter 5: Data age check
            data_age = rapidapi_client.get_data_age_minutes()
            if data_age > 60:
                stats.log_filter_rejection('old_data')
                if CONFIG.debug.enable_filter_debug:
                    logging.warning(f"⚠️ {normalized_symbol}: Price zones data is {data_age:.0f} minutes old")
            
            # Get pair config
            pair_config = pairs_builder.get_pair_config(normalized_symbol)
            if not pair_config:
                if CONFIG.debug.enable_filter_debug:
                    logging.error(f"⚠️ {normalized_symbol}: No pair config found")
                return
            
            # VWAP level analysis
            if CONFIG.vwap.vwap_enhancement:
                await vwap_calculator.calculate_realtime_vwap(self.exchange, normalized_symbol)
            
            long_level, short_level, vwap_ref = vwap_calculator.get_vwap_levels(normalized_symbol, liq_price)
            
            if long_level <= 0 or short_level <= 0:
                stats.log_filter_rejection('invalid_vwap')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"⚠️ {normalized_symbol}: Invalid VWAP levels (L:{long_level:.6f} S:{short_level:.6f})")
                return
            
            # Entry signal analysis
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
                debug_logger.debug(f"📊 ZONES {normalized_symbol}: Long={long_level:.6f} Short={short_level:.6f} VWAP={vwap_ref:.6f}")
                debug_logger.debug(f"🎯 SIGNAL {normalized_symbol}: {entry_reason}")
            
            if not entry_valid:
                stats.log_filter_rejection('no_entry_signal')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"⚠️ {normalized_symbol}: No entry signal - {entry_reason}")
                return
            
            # Market regime check
            regime = await regime_detector.detect_regime(self.exchange, normalized_symbol)
            if not regime_detector.should_trade_in_regime(regime):
                stats.log_filter_rejection('regime_filter')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"⚠️ {normalized_symbol}: Regime filter - {regime}")
                return
            
            # Create and execute position
            balance = await get_account_balance(self.exchange)
            position = await position_manager.create_position(
                self.exchange, normalized_symbol, our_side, liq_price, liq_size_usd, vwap_ref, regime, balance
            )
            
            if position is None:
                stats.log_filter_rejection('position_create_failed')
                if CONFIG.debug.enable_filter_debug:
                    logging.error(f"⚠️ {normalized_symbol}: Position creation failed")
                return
            
            # Execute trade
            success = await self._execute_trade(position)
            execution_time = time.time() - start_time
            stats.log_trade_attempt(success)
            
            if success:
                self.entry_timestamps[normalized_symbol] = time.time()
                
                # Send Discord notification
                if discord_notifier:
                    await discord_notifier.send_trade_alert(
                        normalized_symbol, our_side, position.total_qty, liq_price,
                        entry_reason, position.total_notional_used
                    )
                
                logging.info(f"🎯 TRADE SUCCESS: {normalized_symbol} | {entry_reason} | "
                           f"Notional: ${position.total_notional_used:.2f} | Time: {execution_time:.2f}s")
                
                if CONFIG.debug.enable_trade_debug:
                    debug_logger = logging.getLogger('trade_debug')
                    debug_logger.debug(f"✅ EXECUTED: {normalized_symbol} {our_side} {position.total_qty:.6f} @ {liq_price:.6f}")
            else:
                position_manager.remove_position(normalized_symbol)
                logging.warning(f"⚠️ TRADE FAILED: {normalized_symbol} in {execution_time:.2f}s")
                
        except Exception as e:
            total_time = time.time() - start_time
            logging.error(f"⚠️ Processing error for {symbol}: {e} | Time: {total_time:.2f}s", exc_info=True)
    
    async def _execute_trade(self, position: Position) -> bool:
        """Execute the actual trade with enhanced error handling"""
        try:
            normalized_symbol = normalize_symbol(position.symbol)
            ccxt_symbol = to_ccxt_symbol(normalized_symbol)
            
            # Set leverage with enhanced error handling
            try:
                await self.exchange.set_margin_mode('cross', ccxt_symbol)
                await self.exchange.set_leverage(CONFIG.leverage, ccxt_symbol)
            except Exception as e:
                if "not modified" not in str(e).lower():
                    logging.warning(f"Leverage setup failed for {normalized_symbol}: {e}")
            
            # Execute market order
            order = await self.exchange.create_market_order(
                ccxt_symbol, position.side, position.total_qty,
                params={"positionSide": "LONG" if position.side == "buy" else "SHORT"}
            )
            
            # Update with actual execution price
            actual_price = float(order.get('price', position.avg_entry_price)) or position.avg_entry_price
            position.avg_entry_price = actual_price
            position.total_notional_used = position.total_qty * actual_price

            # Set initial take profit with enhanced error recovery
            tp_success = await profit_protection.set_initial_take_profit(self.exchange, position)
            if not tp_success:
                logging.warning(f"⚠️ TP setup failed for {normalized_symbol}, position created without protection")
            
            return True
            
        except Exception as e:
            logging.error(f"⚠️ Trade execution failed for {position.symbol}: {e}")
            return False
    
    async def monitor_positions(self):
        """Enhanced position monitoring with better order management"""
        while True:
            try:
                for symbol, position in list(position_manager.positions.items()):
                    try:
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
                                # Update TP/SL orders after DCA
                                await profit_protection.update_orders_after_dca(self.exchange, position)
                                
                    except Exception as e:
                        logging.error(f"Position monitoring error for {symbol}: {e}")
                        continue
                
                await asyncio.sleep(10)  # Check every 10 seconds
                
            except asyncio.CancelledError:
                logging.info("🛑 monitor_positions cancelled")
                break
            except Exception as e:
                logging.error(f"Position monitoring error: {e}")
                await asyncio.sleep(60)

# =============================================================================
# PERIODIC STATS REPORTER
# =============================================================================

async def periodic_stats_reporter():
    """Enhanced periodic statistics reporting"""
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

async def run_liquidation_stream_with_shutdown(strategy):
    """Run liquidation stream with proper shutdown handling"""
    uri = "wss://fstream.binance.com/stream?streams=!forceOrder@arr"
    reconnect_delay = 5
    
    while not shutdown_handler.shutdown_event.is_set():
        try:
            logging.info("🔌 Connecting to Binance liquidation stream...")
            
            import websockets
            
            async with websockets.connect(uri) as ws:
                logging.info("⚡ 0xLIQD ACTIVE")
                reconnect_delay = 5
                
                while not shutdown_handler.shutdown_event.is_set():
                    try:
                        # Use wait_for with shutdown check
                        done, pending = await asyncio.wait([
                            asyncio.create_task(ws.recv()),
                            asyncio.create_task(shutdown_handler.shutdown_event.wait())
                        ], return_when=asyncio.FIRST_COMPLETED, timeout=30.0)
                        
                        # Cancel pending tasks
                        for task in pending:
                            task.cancel()
                        
                        # Check if shutdown was triggered
                        if shutdown_handler.shutdown_event.is_set():
                            logging.info("🛑 Shutdown event detected, closing websocket")
                            break
                        
                        # Process websocket message
                        if done:
                            result = done.pop().result()
                            payload = json.loads(result)
                            
                            # Handle liquidation events
                            events = payload.get("data", [])
                            if not isinstance(events, list):
                                events = [events]
                            
                            for event in events:
                                if not shutdown_handler.shutdown_event.is_set():
                                    asyncio.create_task(strategy.process_liquidation_event(event))
                                    
                    except websockets.exceptions.ConnectionClosed:
                        logging.warning("Liquidation stream disconnected")
                        break
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        logging.error(f"Liquidation stream error: {e}")
                        break
                        
        except Exception as e:
            if shutdown_handler.shutdown_event.is_set():
                break
            logging.error(f"Connection error: {e}")
            logging.info(f"⏳ Reconnecting in {reconnect_delay}s...")
            
            # Wait for reconnect delay or shutdown event
            try:
                await asyncio.wait_for(
                    shutdown_handler.shutdown_event.wait(),
                    timeout=reconnect_delay
                )
                break  # Shutdown event triggered
            except asyncio.TimeoutError:
                pass  # Continue with reconnect
            
            reconnect_delay = min(reconnect_delay * 1.5, 60)

# =============================================================================
# MAIN BOT EXECUTION
# =============================================================================

async def main_bot():
    """Enhanced main bot execution with graceful shutdown"""
    setup_enhanced_logging()
    logging.info("⚡ 0xLIQD - STARTING UP...")
    logging.info("=" * 70)
    
    global data_manager
    
    # Setup signal handlers
    shutdown_handler.setup_signal_handlers()
    
    # Initialize components
    exchange = None
    monitor_task = None
    stats_task = None
    liquidation_ws = None
    
    try:
        # Initialize CCXT Pro exchange
        exchange = get_exchange()
        logging.info("✅ CCXT Pro exchange connected")
        
        # Initialize CCXT Pro data manager
        data_manager = CCXTProDataManager(exchange)
        shutdown_handler.register_component(data_manager, data_manager.close)
        
        await data_manager.start_streaming()
        logging.info("✅ CCXT Pro streaming initialized")
        
        # Initialize zones client
        await rapidapi_client.initialize()
        shutdown_handler.register_component(rapidapi_client, rapidapi_client.close)
        
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
        
        # Subscribe all trading pairs to CCXT Pro streams
        for symbol in pairs_data.keys():
            data_manager.subscribe_symbol(symbol)
        
        logging.info(f"📡 Subscribed to {len(pairs_data)} symbols for CCXT Pro streaming")
        
        # Wait for streaming data to start flowing
        await asyncio.sleep(3)
        
        # Initialize strategy
        strategy = VWAPHunterStrategy(exchange)
        logging.info("✅ Strategy initialized with CCXT Pro integration")
        
        # Start position monitoring
        await position_manager.start_order_monitoring(exchange)
        shutdown_handler.register_component(position_manager, position_manager.stop_order_monitoring)
        
        monitor_task = asyncio.create_task(strategy.monitor_positions())
        logging.info("✅ Position monitoring started")
        
        # Start statistics reporting
        stats_task = asyncio.create_task(periodic_stats_reporter())
        
        # Send startup notification
        if discord_notifier:
            await discord_notifier.initialize()
            shutdown_handler.register_component(discord_notifier, discord_notifier.close)
            await discord_notifier.send_startup_alert(zones_count, pairs_count)
            logging.info("✅ Discord notifications active")
        
        # Configuration summary
        logging.info("📋 Configuration:")
        logging.info(f"  Max Positions: {CONFIG.risk.max_positions}")
        logging.info(f"  CCXT Pro Streaming: ENABLED")
        logging.info("=" * 70)
        
        # Main liquidation stream with graceful shutdown support
        await run_liquidation_stream_with_shutdown(strategy)
        
    except KeyboardInterrupt:
        logging.info("🛑 Keyboard interrupt received")
    except Exception as e:
        logging.error(f"💥 Fatal error: {e}", exc_info=True)
    finally:
        logging.info("🧹 Starting cleanup...")
        
        # Cancel background tasks gracefully
        tasks_to_cancel = []
        
        if monitor_task and not monitor_task.done():
            tasks_to_cancel.append(monitor_task)
            
        if stats_task and not stats_task.done():
            tasks_to_cancel.append(stats_task)
        
        # Cancel tasks
        for task in tasks_to_cancel:
            task.cancel()
        
        # Wait for tasks to complete with timeout
        if tasks_to_cancel:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks_to_cancel, return_exceptions=True),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                logging.warning("⚠️ Some tasks didn't complete within timeout")
        
        # Cleanup all registered components
        await shutdown_handler.cleanup_all()
        
        # Close exchange last
        if exchange:
            try:
                await exchange.close()
                logging.info("✅ Exchange connection closed")
            except Exception as e:
                logging.warning(f"⚠️ Exchange cleanup error: {e}")
        
        logging.info("✅ Shutdown complete")

# =============================================================================
# ENTRY POINT
# =============================================================================

async def main():
    """Fixed main entry point with proper cleanup"""
    try:
        print("⚡ Starting 0xLIQD...")
        
        # Validate configuration
        if not CONFIG.api_key or not CONFIG.api_secret:
            print("⚠️ Missing Binance API credentials in config.yaml")
            return
        
        print("✅ Configuration validated")
        print("🚀 Launching bot...")
        
        await main_bot()
        
    except KeyboardInterrupt:
        logging.info("🛑 Shutdown requested via Ctrl+C")
    except Exception as e:
        logging.error(f"💥 Startup error: {e}")
    finally:
        # Get all tasks before cleanup
        all_tasks = [task for task in asyncio.all_tasks() if not task.done()]
        
        # Filter out the current task (main) to avoid cancelling ourselves
        current_task = asyncio.current_task()
        tasks_to_cancel = [task for task in all_tasks if task != current_task]
        
        if tasks_to_cancel:
            logging.info(f"Cancelling {len(tasks_to_cancel)} remaining tasks")
            
            # Cancel all tasks
            for task in tasks_to_cancel:
                task.cancel()
            
            # Wait for cancellation with suppressed errors
            if tasks_to_cancel:
                results = await asyncio.gather(
                    *tasks_to_cancel, 
                    return_exceptions=True
                )
                
                # Count successful cancellations vs errors
                cancelled_count = sum(1 for r in results if isinstance(r, asyncio.CancelledError))
                error_count = len(results) - cancelled_count
                
                if error_count > 0:
                    logging.debug(f"Task cleanup: {cancelled_count} cancelled, {error_count} errors (expected)")
                else:
                    logging.info(f"All {cancelled_count} tasks cancelled cleanly")

if __name__ == "__main__":
    try:
        # Suppress asyncio debug warnings for cleaner output
        import warnings
        warnings.filterwarnings("ignore", category=RuntimeWarning, module="asyncio")
        
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped gracefully")
    except Exception as e:
        print(f"Startup error: {e}")
        sys.exit(1)