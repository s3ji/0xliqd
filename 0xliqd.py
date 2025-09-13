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
class MomentumConfig:
    """Momentum detection configuration"""
    enable_momentum_filter: bool = True
    
    # Daily momentum thresholds
    daily_pump_threshold: float = 15.0  # % daily gain to consider "pumped"
    daily_dump_threshold: float = -10.0  # % daily loss to consider "dumped"
    
    # Hourly momentum thresholds (more sensitive)
    hourly_pump_threshold: float = 8.0   # % hourly gain
    hourly_dump_threshold: float = -6.0  # % hourly loss
    
    # Volatility thresholds
    min_daily_volatility: float = 5.0    # Minimum daily range %
    max_daily_volatility: float = 50.0   # Maximum daily range %
    
    # Strategy modes
    momentum_mode: str = "AVOID_EXTREMES"  # AVOID_EXTREMES, TARGET_MOMENTUM, ENHANCE_SIGNALS
    
    # Lookback periods
    momentum_lookback_hours: int = 24
    volatility_lookback_hours: int = 24

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
    dca: DCAConfig = field(default_factory=DCAConfig)
    profit_protection: ProfitProtectionConfig = field(default_factory=ProfitProtectionConfig)
    market_regime: MarketRegimeConfig = field(default_factory=MarketRegimeConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)
    momentum: MomentumConfig = field(default_factory=MomentumConfig)
    
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
                        (RapidAPIConfig, VWAPConfig, DCAConfig, ProfitProtectionConfig, MarketRegimeConfig, RiskConfig, DebugConfig, MomentumConfig)):
                        setattr(config, key, value)
                
                # Update sub-configs
                for sub_config_name in ['rapidapi', 'vwap', 'dca', 'profit_protection', 'market_regime', 'risk', 'debug', 'momentum']:
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
        
        # Top liquidated symbols
        if self.liquidations_by_symbol:
            top_symbols = sorted(self.liquidations_by_symbol.items(),
                               key=lambda x: x[1]['count'], reverse=True)[:10]
            summary += f"\n🔥 TOP LIQUIDATED SYMBOLS:\n"
            for symbol, data in top_symbols:
                summary += f"  {symbol}: {data['count']} liquidations (${data['total_size']:,.0f})\n"
        
        return summary

stats = TradingStatistics()

# =============================================================================
# WEBSOCKET DATA MANAGER
# =============================================================================

class BinanceWebSocketManager:
    """Comprehensive WebSocket manager for real-time data"""
    
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        
        # Real-time data caches
        self.positions_cache: Dict[str, Dict] = {}
        self.balance_cache: Dict[str, float] = {}
        self.price_cache: Dict[str, Dict] = {}
        self.volume_cache: Dict[str, float] = {}
        self.kline_cache: Dict[str, deque] = {}
        
        # WebSocket connections
        self.user_data_ws = None
        self.market_data_ws = None
        self.listen_key = None
        
        # Connection management
        self.ws_tasks: List[asyncio.Task] = []
        self.is_running = False
        
        # Subscribed symbols for market data
        self.subscribed_symbols: set = set()
        
        # Last update timestamps
        self.last_updates = {
            'positions': 0,
            'balance': 0,
            'prices': {},
            'volume': {}
        }
        
        logging.info("🔌 WebSocket Manager initialized")
    
    async def initialize(self):
        """Initialize WebSocket connections"""
        if self.is_running:
            return
            
        try:
            # Get listen key for user data stream
            await self._get_listen_key()
            
            # Start WebSocket connections
            self.is_running = True
            
            # Start user data stream
            user_task = asyncio.create_task(self._user_data_stream())
            self.ws_tasks.append(user_task)
            
            # Start market data stream  
            market_task = asyncio.create_task(self._market_data_stream())
            self.ws_tasks.append(market_task)
            
            # Start listen key refresh task
            refresh_task = asyncio.create_task(self._refresh_listen_key())
            self.ws_tasks.append(refresh_task)
            
            logging.info("✅ WebSocket connections initialized")
            
        except Exception as e:
            logging.error(f"❌ WebSocket initialization failed: {e}")
            raise
    
    async def _get_listen_key(self):
        """Get listen key for user data stream"""
        try:
            headers = {'X-MBX-APIKEY': self.api_key}
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://fapi.binance.com/fapi/v1/listenKey',
                    headers=headers
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        self.listen_key = data['listenKey']
                        logging.info("🔑 Listen key obtained")
                    else:
                        raise Exception(f"Failed to get listen key: {response.status}")
        except Exception as e:
            logging.error(f"❌ Listen key error: {e}")
            raise
    
    async def _refresh_listen_key(self):
        """Refresh listen key every 30 minutes"""
        while self.is_running:
            try:
                await asyncio.sleep(30 * 60)  # 30 minutes
                if self.listen_key:
                    headers = {'X-MBX-APIKEY': self.api_key}
                    async with aiohttp.ClientSession() as session:
                        async with session.put(
                            'https://fapi.binance.com/fapi/v1/listenKey',
                            headers=headers,
                            data={'listenKey': self.listen_key}
                        ) as response:
                            if response.status == 200:
                                logging.info("🔄 Listen key refreshed")
                            else:
                                logging.warning(f"⚠️ Listen key refresh failed: {response.status}")
                                await self._get_listen_key()  # Get new key
            except Exception as e:
                logging.error(f"❌ Listen key refresh error: {e}")
                await asyncio.sleep(60)
    
    async def _user_data_stream(self):
        """User data WebSocket stream for positions, balance, orders"""
        reconnect_delay = 5
        
        while self.is_running:
            try:
                if not self.listen_key:
                    await self._get_listen_key()
                
                uri = f"wss://fstream.binance.com/ws/{self.listen_key}"
                
                logging.info("🔌 Connecting to user data stream...")
                async with websockets.connect(uri) as ws:
                    logging.info("✅ User data stream connected")
                    reconnect_delay = 5
                    
                    while self.is_running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=30.0)
                            data = json.loads(message)
                            await self._handle_user_data(data)
                            
                        except websockets.exceptions.ConnectionClosed:
                            logging.warning("🔌 User data stream disconnected")
                            break
                        except asyncio.TimeoutError:
                            await ws.ping()
                        except Exception as e:
                            logging.error(f"❌ User data stream error: {e}")
                            break
                            
            except Exception as e:
                logging.error(f"❌ User data connection error: {e}")
                if self.is_running:
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, 60)
    
    async def _market_data_stream(self):
        """Market data WebSocket stream for prices and volumes"""
        reconnect_delay = 5
        
        while self.is_running:
            try:
                # Build stream URL for all subscribed symbols
                if not self.subscribed_symbols:
                    await asyncio.sleep(5)
                    continue
                
                streams = []
                for symbol in self.subscribed_symbols:
                    symbol_lower = symbol.lower()
                    streams.extend([
                        f"{symbol_lower}@ticker",
                        f"{symbol_lower}@kline_1m"
                    ])
                
                if not streams:
                    await asyncio.sleep(5)
                    continue
                
                uri = f"wss://fstream.binance.com/stream?streams={'/'.join(streams)}"
                
                logging.info(f"🔌 Connecting to market data stream ({len(streams)} streams)...")
                async with websockets.connect(uri) as ws:
                    logging.info("✅ Market data stream connected")
                    reconnect_delay = 5
                    
                    while self.is_running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=30.0)
                            data = json.loads(message)
                            await self._handle_market_data(data)
                            
                        except websockets.exceptions.ConnectionClosed:
                            logging.warning("🔌 Market data stream disconnected")
                            break
                        except asyncio.TimeoutError:
                            await ws.ping()
                        except Exception as e:
                            logging.error(f"❌ Market data stream error: {e}")
                            break
                            
            except Exception as e:
                logging.error(f"❌ Market data connection error: {e}")
                if self.is_running:
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, 60)
    
    async def _handle_user_data(self, data: Dict):
        """Handle user data stream messages"""
        try:
            event_type = data.get('e')
            
            if event_type == 'ACCOUNT_UPDATE':
                # Balance updates
                account_data = data.get('a', {})
                balances = account_data.get('B', [])
                
                for balance in balances:
                    asset = balance.get('a')
                    wallet_balance = float(balance.get('wb', 0))
                    if asset == 'USDT':
                        self.balance_cache['USDT'] = wallet_balance
                        self.last_updates['balance'] = time.time()
                
                # Position updates
                positions = account_data.get('P', [])
                for pos in positions:
                    symbol = pos.get('s')
                    position_amount = float(pos.get('pa', 0))
                    entry_price = float(pos.get('ep', 0))
                    unrealized_pnl = float(pos.get('up', 0))
                    
                    if symbol:
                        base_symbol = symbol.replace('USDT', '') + 'USDT'
                        
                        if abs(position_amount) > 0:
                            self.positions_cache[base_symbol] = {
                                'symbol': symbol,
                                'side': 'buy' if position_amount > 0 else 'sell',
                                'size': abs(position_amount),
                                'entry_price': entry_price,
                                'unrealized_pnl': unrealized_pnl,
                                'timestamp': time.time()
                            }
                        else:
                            # Position closed
                            if base_symbol in self.positions_cache:
                                del self.positions_cache[base_symbol]
                
                self.last_updates['positions'] = time.time()
                
        except Exception as e:
            logging.error(f"❌ User data handling error: {e}")
    
    async def _handle_market_data(self, data: Dict):
        """Handle market data stream messages"""
        try:
            stream_data = data.get('data', {})
            stream_name = data.get('stream', '')
            
            if '@ticker' in stream_name:
                # Price and volume updates
                symbol = stream_data.get('s', '').replace('USDT', '') + 'USDT'
                price = float(stream_data.get('c', 0))
                volume = float(stream_data.get('q', 0))
                
                if symbol and price > 0:
                    self.price_cache[symbol] = {
                        'price': price,
                        'volume_24h': volume,
                        'timestamp': time.time()
                    }
                    self.last_updates['prices'][symbol] = time.time()
                    
                    if volume > 0:
                        self.volume_cache[symbol] = volume
                        self.last_updates['volume'][symbol] = time.time()
            
            elif '@kline' in stream_name:
                # Kline data for VWAP calculations
                kline = stream_data.get('k', {})
                symbol = kline.get('s', '').replace('USDT', '') + 'USDT'
                
                if kline.get('x'):  # Only closed klines
                    ohlcv = [
                        int(kline.get('t', 0)),  # timestamp
                        float(kline.get('o', 0)),  # open
                        float(kline.get('h', 0)),  # high
                        float(kline.get('l', 0)),  # low
                        float(kline.get('c', 0)),  # close
                        float(kline.get('v', 0))   # volume
                    ]
                    
                    if symbol not in self.kline_cache:
                        self.kline_cache[symbol] = deque(maxlen=200)
                    
                    self.kline_cache[symbol].append(ohlcv)
                    
        except Exception as e:
            logging.error(f"❌ Market data handling error: {e}")
    
    def subscribe_symbol(self, symbol: str):
        """Subscribe to market data for a symbol"""
        self.subscribed_symbols.add(symbol)
        logging.debug(f"📡 Subscribed to {symbol}")
    
    # Data getter methods
    def get_positions(self) -> Dict[str, Dict]:
        """Get current positions from cache"""
        return self.positions_cache.copy()
    
    def get_balance(self, asset: str = 'USDT') -> float:
        """Get current balance from cache"""
        return self.balance_cache.get(asset, 0.0)
    
    def get_price(self, symbol: str) -> float:
        """Get current price from cache"""
        data = self.price_cache.get(symbol, {})
        return data.get('price', 0.0)
    
    def get_volume_24h(self, symbol: str) -> float:
        """Get 24h volume from cache"""
        data = self.price_cache.get(symbol, {})
        return data.get('volume_24h', 0.0)
    
    def get_kline_data(self, symbol: str, limit: int = 200) -> List:
        """Get kline data for VWAP calculations"""
        if symbol in self.kline_cache:
            return list(self.kline_cache[symbol])[-limit:]
        return []
    
    def is_data_fresh(self, data_type: str, symbol: str = None, max_age: int = 60) -> bool:
        """Check if cached data is fresh"""
        current_time = time.time()
        
        if data_type == 'positions':
            return current_time - self.last_updates['positions'] < max_age
        elif data_type == 'balance':
            return current_time - self.last_updates['balance'] < max_age
        elif data_type == 'price' and symbol:
            last_update = self.last_updates['prices'].get(symbol, 0)
            return current_time - last_update < max_age
        elif data_type == 'volume' and symbol:
            last_update = self.last_updates['volume'].get(symbol, 0)
            return current_time - last_update < max_age
        
        return False
    
    async def close(self):
        """Close all WebSocket connections"""
        self.is_running = False
        
        # Cancel all tasks
        for task in self.ws_tasks:
            if not task.done():
                task.cancel()
        
        # Wait for tasks to complete
        if self.ws_tasks:
            await asyncio.gather(*self.ws_tasks, return_exceptions=True)
        
        # Close listen key
        if self.listen_key:
            try:
                headers = {'X-MBX-APIKEY': self.api_key}
                async with aiohttp.ClientSession() as session:
                    await session.delete(
                        'https://fapi.binance.com/fapi/v1/listenKey',
                        headers=headers,
                        data={'listenKey': self.listen_key}
                    )
            except:
                pass
        
        logging.info("🔌 WebSocket connections closed")

# Initialize WebSocket manager (will be set in main)
ws_manager: Optional[BinanceWebSocketManager] = None

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
            
            # Format P&L with better precision
            pnl_pct_str = f"{pnl_pct:+.2f}%"
            pnl_usd_str = f"${pnl_usd:+.2f}"
            
            # Add win/loss indicator
            result = "WIN 🟢" if pnl_pct > 0 else "LOSS 🔴"
            
            embed = {
                "title": f"{emoji} {result} • {symbol}",
                "description": f"```{action} @ {price:.6f}```",
                "color": color,
                "fields": [
                    {
                        "name": "💵 Results",
                        "value": f"**P&L:** {pnl_pct_str}\n**USD:** {pnl_usd_str}",
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
                    logging.info(f"P&L alert sent: {symbol} {pnl_pct_str}")
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
# EXCHANGE INTERFACE (ENHANCED WITH WEBSOCKET FALLBACK)
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
    """Get account balance with WebSocket fallback"""
    global ws_manager
    
    # Try WebSocket first
    if ws_manager and ws_manager.is_data_fresh('balance', max_age=300):
        balance = ws_manager.get_balance('USDT')
        if balance > 0:
            logging.debug(f"💰 Balance from WebSocket: ${balance:.2f}")
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
# MOMENTUM/VOLATILITY DETECTOR
# =============================================================================

class MomentumDetector:
    """Detect momentum and volatility using WebSocket kline data"""
    
    def __init__(self, ws_manager: BinanceWebSocketManager):
        self.ws_manager = ws_manager
        self.momentum_cache: Dict[str, Dict] = {}
    
    def calculate_momentum_metrics(self, symbol: str) -> Dict[str, float]:
        """Calculate various momentum and volatility metrics"""
        try:
            # Get kline data from WebSocket (1m bars)
            klines = self.ws_manager.get_kline_data(symbol, limit=1440)  # 24 hours of 1m data
            
            if len(klines) < 60:  # Need at least 1 hour of data
                return {}
            
            # Convert to price data
            prices = [float(kline[4]) for kline in klines]  # closing prices
            highs = [float(kline[2]) for kline in klines]   # highs
            lows = [float(kline[3]) for kline in klines]    # lows
            volumes = [float(kline[5]) for kline in klines] # volumes
            
            current_price = prices[-1]
            
            # Calculate different timeframe moves
            metrics = {}
            
            # 1-hour momentum (last 60 minutes)
            if len(prices) >= 60:
                hour_start = prices[-60]
                metrics['1h_change_pct'] = ((current_price - hour_start) / hour_start) * 100
            
            # 4-hour momentum
            if len(prices) >= 240:
                four_hour_start = prices[-240]
                metrics['4h_change_pct'] = ((current_price - four_hour_start) / four_hour_start) * 100
            
            # 24-hour momentum (full lookback)
            if len(prices) >= 1440:
                day_start = prices[0]
                metrics['24h_change_pct'] = ((current_price - day_start) / day_start) * 100
            elif len(prices) >= 60:
                # Use available data if less than 24h
                day_start = prices[0]
                metrics['24h_change_pct'] = ((current_price - day_start) / day_start) * 100
            
            # Volatility metrics
            if len(prices) >= 240:  # 4 hours minimum for volatility
                # Daily high/low range
                recent_high = max(highs[-1440:] if len(highs) >= 1440 else highs)
                recent_low = min(lows[-1440:] if len(lows) >= 1440 else lows)
                metrics['daily_range_pct'] = ((recent_high - recent_low) / recent_low) * 100
                
                # Price volatility (standard deviation)
                import numpy as np
                price_changes = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]
                metrics['volatility_1h'] = float(np.std(price_changes[-60:]) * 100) if len(price_changes) >= 60 else 0
                metrics['volatility_4h'] = float(np.std(price_changes[-240:]) * 100) if len(price_changes) >= 240 else 0
                
                # Volume spike detection
                avg_volume = sum(volumes[-240:]) / len(volumes[-240:]) if len(volumes) >= 240 else sum(volumes) / len(volumes)
                current_volume = sum(volumes[-60:]) / 60 if len(volumes) >= 60 else volumes[-1]
                metrics['volume_spike_ratio'] = current_volume / avg_volume if avg_volume > 0 else 1
            
            # Cache the results
            self.momentum_cache[symbol] = {
                'metrics': metrics,
                'timestamp': time.time(),
                'price': current_price
            }
            
            return metrics
            
        except Exception as e:
            logging.warning(f"Momentum calculation failed for {symbol}: {e}")
            return {}
    
    def get_momentum_signal(self, symbol: str) -> Tuple[str, str, Dict]:
        """
        Get momentum signal for a symbol
        Returns: (signal, reason, metrics)
        signal: ALLOW, AVOID, ENHANCE_LONG, ENHANCE_SHORT
        """
        try:
            # Get or calculate metrics
            cached = self.momentum_cache.get(symbol)
            if cached and time.time() - cached['timestamp'] < 300:  # 5 min cache
                metrics = cached['metrics']
            else:
                metrics = self.calculate_momentum_metrics(symbol)
            
            if not metrics:
                return "ALLOW", "No momentum data", {}
            
            # Extract key metrics
            change_1h = metrics.get('1h_change_pct', 0)
            change_4h = metrics.get('4h_change_pct', 0)
            change_24h = metrics.get('24h_change_pct', 0)
            daily_range = metrics.get('daily_range_pct', 0)
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
                elif daily_range <= CONFIG.momentum.min_daily_volatility:
                    return "AVOID", f"Low volatility: {daily_range:.1f}%", metrics
                else:
                    return "ALLOW", f"Normal momentum: {change_24h:+.1f}% (24h), {change_1h:+.1f}% (1h)", metrics
            
            elif mode == "TARGET_MOMENTUM":
                # Target symbols with good momentum for continuation
                if abs(change_24h) >= 5 and abs(change_1h) <= 3 and volume_spike >= 1.5:
                    if change_24h > 0:
                        return "ENHANCE_LONG", f"Bullish momentum: +{change_24h:.1f}% (24h), cooling off", metrics
                    else:
                        return "ENHANCE_SHORT", f"Bearish momentum: {change_24h:.1f}% (24h), cooling off", metrics
                elif daily_range >= CONFIG.momentum.min_daily_volatility * 2:
                    return "ALLOW", f"High volatility target: {daily_range:.1f}%", metrics
                else:
                    return "AVOID", f"Insufficient momentum: {change_24h:+.1f}% (24h)", metrics
            
            elif mode == "ENHANCE_SIGNALS":
                # Use momentum to enhance existing signals
                momentum_score = 0
                
                # Score based on various factors
                if 2 <= abs(change_24h) <= 12:  # Good daily move, not extreme
                    momentum_score += 1
                if abs(change_1h) <= 2:  # Not moving too fast recently
                    momentum_score += 1
                if CONFIG.momentum.min_daily_volatility <= daily_range <= CONFIG.momentum.max_daily_volatility:
                    momentum_score += 1
                if volume_spike >= 1.2:  # Some volume interest
                    momentum_score += 1
                
                if momentum_score >= 3:
                    return "ALLOW", f"Good momentum profile (score: {momentum_score}/4)", metrics
                elif momentum_score >= 2:
                    return "ALLOW", f"Acceptable momentum (score: {momentum_score}/4)", metrics
                else:
                    return "AVOID", f"Poor momentum profile (score: {momentum_score}/4)", metrics
            
            return "ALLOW", "Default allow", metrics
            
        except Exception as e:
            logging.error(f"Momentum signal error for {symbol}: {e}")
            return "ALLOW", "Error in momentum calculation", {}
    
    def get_momentum_summary(self, symbol: str) -> str:
        """Get a human-readable momentum summary"""
        try:
            signal, reason, metrics = self.get_momentum_signal(symbol)
            
            if not metrics:
                return f"{signal}: {reason}"
            
            change_1h = metrics.get('1h_change_pct', 0)
            change_24h = metrics.get('24h_change_pct', 0)
            daily_range = metrics.get('daily_range_pct', 0)
            volume_spike = metrics.get('volume_spike_ratio', 1)
            
            summary = f"{signal}: {reason}\n"
            summary += f"  24h: {change_24h:+.1f}%, 1h: {change_1h:+.1f}%\n"
            summary += f"  Range: {daily_range:.1f}%, Vol: {volume_spike:.1f}x"
            
            return summary
            
        except Exception as e:
            return f"Error: {e}"

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
# VWAP CALCULATOR (ENHANCED WITH WEBSOCKET)
# =============================================================================

class EnhancedVWAPCalculator:
    """Enhanced VWAP calculation with WebSocket support"""
    
    def __init__(self):
        self.vwap_cache: Dict[str, Dict] = {}
    
    async def calculate_realtime_vwap(self, exchange, symbol: str, period: int = None) -> float:
        """Calculate real-time VWAP with WebSocket fallback"""
        global ws_manager
        
        if period is None:
            period = CONFIG.vwap.period
        
        try:
            # Try WebSocket data first
            if ws_manager:
                kline_data = ws_manager.get_kline_data(symbol, limit=min(period, 200))
                if len(kline_data) >= 10:
                    total_pv = 0
                    total_volume = 0
                    
                    for bar in kline_data:
                        if len(bar) >= 6:
                            typical_price = (bar[2] + bar[3] + bar[4]) / 3  # (high + low + close) / 3
                            volume = bar[5]
                            total_pv += typical_price * volume
                            total_volume += volume
                    
                    if total_volume > 0:
                        vwap = total_pv / total_volume
                        
                        # Cache result
                        self.vwap_cache[symbol] = {
                            'vwap': vwap,
                            'timestamp': time.time(),
                            'period': period,
                            'source': 'websocket'
                        }
                        
                        logging.debug(f"📊 VWAP from WebSocket: {symbol} = {vwap:.6f}")
                        return vwap
            
            # Fallback to REST API
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
# MARKET REGIME DETECTOR (ENHANCED WITH WEBSOCKET)
# =============================================================================

class MarketRegimeDetector:
    """Market regime detection using ADX + ATR with WebSocket support"""
    
    def __init__(self):
        self.regime_cache: Dict[str, Dict] = {}
    
    async def detect_regime(self, exchange, symbol: str) -> str:
        """Detect market regime with WebSocket fallback"""
        global ws_manager
        
        try:
            # Try WebSocket data first
            if ws_manager:
                kline_data = ws_manager.get_kline_data(symbol, limit=50)
                if len(kline_data) >= 30:
                    adx = self._calculate_adx(kline_data)
                    
                    if len(kline_data) >= 10:
                        current_price = kline_data[-1][4]  # last close
                        old_price = kline_data[-10][4]     # 10 periods ago close
                        price_change_pct = ((current_price - old_price) / old_price) * 100
                    else:
                        price_change_pct = 0
                    
                    regime = self._classify_regime(adx, price_change_pct)
                    
                    # Cache result
                    self.regime_cache[symbol] = {
                        'regime': regime,
                        'adx': adx,
                        'timestamp': time.time(),
                        'source': 'websocket'
                    }
                    
                    logging.debug(f"📈 Regime from WebSocket: {symbol} = {regime}")
                    return regime
            
            # Fallback to REST API
            ohlcv = await exchange.fetch_ohlcv(symbol, '5m', limit=50)
            if len(ohlcv) < 30:
                return "UNKNOWN"
            
            # Simple ADX calculation
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
# POSITION MANAGEMENT (ENHANCED WITH WEBSOCKET)
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
    alert_sent: bool = False

class PositionManager:
    """Position management with WebSocket integration"""
    
    def __init__(self):
        self.positions: Dict[str, Position] = {}
        self.total_trades = 0
        self.profitable_trades = 0
        self._position_lock = asyncio.Lock()
        self.alert_sent_positions: set = set()
        self._symbol_locks: Dict[str, asyncio.Lock] = {}
        self.recent_attempts: Dict[str, float] = {}  # Track recent attempts per symbol
        self.attempt_cooldown = 10  # seconds between attempts for same symbol
    
    def get_symbol_lock(self, symbol: str) -> asyncio.Lock:
        """Get or create symbol-specific lock"""
        if symbol not in self._symbol_locks:
            self._symbol_locks[symbol] = asyncio.Lock()
        return self._symbol_locks[symbol]
    
    async def sync_with_exchange(self, exchange) -> Dict[str, Any]:
        """Enhanced sync with WebSocket integration"""
        global ws_manager
        
        try:
            # 1) Try to get positions from WebSocket first
            if ws_manager and ws_manager.is_data_fresh('positions', max_age=30):
                ws_positions = ws_manager.get_positions()
                logging.debug(f"📊 Got {len(ws_positions)} positions from WebSocket")
                
                # Check for closed positions
                potentially_closed = [s for s in self.positions if s not in ws_positions]
                
                confirmed_closed = []
                for symbol in potentially_closed:
                    if symbol not in self.alert_sent_positions:
                        # Wait a bit and confirm closure
                        await asyncio.sleep(2)
                        
                        # Re-check WebSocket cache
                        current_ws_positions = ws_manager.get_positions()
                        if symbol not in current_ws_positions:
                            confirmed_closed.append(symbol)
                            await self._send_position_closed_alert_ws(symbol)
                            self.alert_sent_positions.add(symbol)
                
                # Remove confirmed closed positions
                for symbol in confirmed_closed:
                    logging.info(f"🔥 Removing confirmed closed position {symbol} (WebSocket)")
                    del self.positions[symbol]
                
                # Add or update active positions
                for symbol, ws_pos in ws_positions.items():
                    if symbol not in self.positions:
                        logging.info(f"➕ Adding live position {symbol} (WebSocket)")
                        self.positions[symbol] = Position(
                            symbol=symbol,
                            side=ws_pos['side'],
                            total_qty=ws_pos['size'],
                            avg_entry_price=ws_pos['entry_price'],
                            total_notional_used=ws_pos['size'] * ws_pos['entry_price'],
                            vwap_reference=ws_pos['entry_price'],
                            regime="UNKNOWN"
                        )
                    else:
                        # Update existing position
                        p = self.positions[symbol]
                        p.total_qty = ws_pos['size']
                        p.avg_entry_price = ws_pos['entry_price']
                        p.total_notional_used = ws_pos['size'] * ws_pos['entry_price']
                
                logging.debug(f"✅ WebSocket sync complete: {len(ws_positions)} live, {len(confirmed_closed)} closed")
                return ws_positions
            
            # 2) Fallback to REST API (original method)
            logging.debug("📊 Falling back to REST API for position sync")
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

            # Identify potentially closed positions
            potentially_closed = [s for s in self.positions if s not in active_positions]
            
            # Confirm closures with a delay and re-check
            confirmed_closed = []
            for symbol in potentially_closed:
                if symbol not in self.alert_sent_positions:
                    await asyncio.sleep(3)
                    
                    try:
                        recheck_positions = await exchange.fetch_positions()
                        still_closed = True
                        
                        for pos in recheck_positions:
                            if pos['contracts'] and float(pos['contracts']) != 0:
                                check_symbol = pos['symbol'].replace(':USDT', '').replace('/USDT', '') + 'USDT'
                                if check_symbol == symbol:
                                    still_closed = False
                                    break
                        
                        if still_closed:
                            confirmed_closed.append(symbol)
                            await self._send_position_closed_alert(symbol, exchange)
                            self.alert_sent_positions.add(symbol)
                    except Exception as e:
                        logging.error(f"Position recheck failed for {symbol}: {e}")

            # Remove confirmed closed positions
            for symbol in confirmed_closed:
                logging.info(f"🔥 Removing confirmed closed position {symbol} (REST API)")
                del self.positions[symbol]

            # Clean up alert tracking
            self.alert_sent_positions = {s for s in self.alert_sent_positions if s in self.positions}

            # Add or update active positions
            for base, info in active_positions.items():
                if base not in self.positions:
                    logging.info(f"➕ Adding live position {base} (REST API)")
                    self.positions[base] = Position(
                        symbol=base,
                        side=info['side'],
                        total_qty=info['qty'],
                        avg_entry_price=info['avg_price'],
                        total_notional_used=info['qty'] * info['avg_price'],
                        vwap_reference=info['avg_price'],
                        regime="UNKNOWN"
                    )
                else:
                    # Update existing memory entry with live data
                    p = self.positions[base]
                    p.total_qty = info['qty']
                    p.avg_entry_price = info['avg_price']
                    p.total_notional_used = info['qty'] * info['avg_price']

            logging.debug(f"✅ REST API sync complete: {len(active_positions)} live, {len(confirmed_closed)} closed")
            return active_positions

        except Exception as e:
            logging.error(f"❌ Sync failed: {e}")
            return {}
    
    async def _send_position_closed_alert_ws(self, symbol: str):
        """Send position closed alert using WebSocket data"""
        global ws_manager
        
        try:
            position = self.positions[symbol]
            
            # Get current price from WebSocket
            exit_price = 0
            if ws_manager:
                exit_price = ws_manager.get_price(symbol)
            
            if exit_price <= 0:
                exit_price = position.avg_entry_price  # Fallback
            
            # Calculate P&L
            if position.side == "buy":
                pnl_pct = (exit_price - position.avg_entry_price) / position.avg_entry_price
            else:
                pnl_pct = (position.avg_entry_price - exit_price) / position.avg_entry_price
                
            pnl_usd = position.total_notional_used * pnl_pct
            
            # Determine action
            if position.side == "buy":
                action = "TAKE PROFIT" if exit_price >= position.avg_entry_price * (1 + CONFIG.profit_protection.initial_tp_pct * 0.9) else "STOP LOSS"
            else:
                action = "TAKE PROFIT" if exit_price <= position.avg_entry_price * (1 - CONFIG.profit_protection.initial_tp_pct * 0.9) else "STOP LOSS"
            
            # Send Discord notification
            if discord_notifier:
                await discord_notifier.send_profit_alert(
                    symbol, pnl_pct * 100, pnl_usd, action, exit_price
                )
            
            logging.info(f"💰 Position closed: {symbol} | "
                        f"P&L: {pnl_pct*100:+.2f}% (${pnl_usd:+.2f}) | "
                        f"Entry: {position.avg_entry_price:.6f} | "
                        f"Exit: {exit_price:.6f} | Source: WebSocket")
                        
        except Exception as e:
            logging.error(f"❌ Failed to send position closed alert for {symbol}: {e}")
    
    async def create_position(self, exchange, symbol: str, side: str, liq_price: float,
                        liq_size: float, vwap_ref: float, regime: str,
                        balance: float) -> Optional[Position]:
        """Create new position with proper race condition protection"""
        symbol_lock = self.get_symbol_lock(symbol)
        async with symbol_lock:
            async with self._position_lock:

                # STEP 0: Check attempt rate limiting
                current_time = time.time()
                last_attempt = self.recent_attempts.get(symbol, 0)
                if current_time - last_attempt < self.attempt_cooldown:
                    logging.info(f"❌ {symbol}: Too soon since last attempt ({current_time - last_attempt:.1f}s)")
                    return None
                
                self.recent_attempts[symbol] = current_time
                
                # STEP 1: Fresh sync with exchange
                active_positions = await self.sync_with_exchange(exchange)
                
                # STEP 2: Check existing position for THIS symbol
                if symbol in active_positions:
                    logging.info(f"❌ {symbol}: Position exists on exchange")
                    stats.log_filter_rejection('existing_position')
                    return None
                    
                if symbol in self.positions:
                    logging.info(f"❌ {symbol}: Position exists in memory")
                    stats.log_filter_rejection('existing_position')
                    return None
                
                # STEP 3: Double-check with fresh exchange query for THIS symbol
                try:
                    fresh_positions = await exchange.fetch_positions([symbol])
                    for pos in fresh_positions:
                        if pos['contracts'] and float(pos['contracts']) != 0:
                            logging.warning(f"❌ {symbol}: Fresh check found existing position")
                            stats.log_filter_rejection('existing_position')
                            return None
                except Exception as e:
                    logging.error(f"Fresh existing position check failed for {symbol}: {e}")
                    return None  # Err on side of caution
                
                # STEP 4: Enhanced max position check (as previously discussed)
                exchange_position_count = len(active_positions)
                memory_position_count = len(self.positions)
                actual_position_count = max(exchange_position_count, memory_position_count)
                
                if actual_position_count >= CONFIG.risk.max_positions:
                    logging.warning(f"❌ {symbol}: Max positions reached "
                                f"(Exchange: {exchange_position_count}, Memory: {memory_position_count})")
                    stats.log_filter_rejection('max_positions')
                    return None
                
                # STEP 5: Final fresh count check
                try:
                    all_fresh_positions = await exchange.fetch_positions()
                    fresh_total_count = len([p for p in all_fresh_positions 
                                        if p['contracts'] and float(p['contracts']) != 0])
                    if fresh_total_count >= CONFIG.risk.max_positions:
                        logging.warning(f"❌ {symbol}: Fresh total count exceeds limit ({fresh_total_count})")
                        stats.log_filter_rejection('max_positions')
                        return None
                except Exception as e:
                    logging.error(f"Fresh total count check failed: {e}")
                    return None
                
                # RISK CHECK: Isolation percentage
                total_notional_used = sum(p.total_notional_used for p in self.positions.values())
                isolation_limit = balance * CONFIG.risk.isolation_pct * CONFIG.leverage
                
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
        
        # Check trigger
        next_level = position.dca_count
        if next_level < len(CONFIG.dca.trigger_pcts):
            trigger_threshold = CONFIG.dca.trigger_pcts[next_level]
            if adverse_move >= trigger_threshold:
                return True, next_level + 1
        
        return False, -1
    
    async def execute_dca(self, exchange, symbol: str, current_price: float,
                         dca_level: int) -> bool:
        """Execute DCA with proper isolation limit check"""
        
        symbol_lock = self.get_symbol_lock(symbol)
        async with symbol_lock:
            if symbol not in self.positions:
                return False
                
            position = self.positions[symbol]
            
            # Get pair config
            pair_config = pairs_builder.get_pair_config(symbol)
            if not pair_config:
                logging.error(f"❌ DCA failed: No pair config for {symbol}")
                return False
            
            # Apply size multiplier to min notional
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
            isolation_limit = balance * CONFIG.risk.isolation_pct * CONFIG.leverage
            
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
        """Send accurate P&L alert using actual execution data with fees"""
        try:
            position = self.positions[symbol]
            
            # Method 1: Try to get actual exit price from recent order history
            actual_exit_price = await self._get_actual_exit_price(symbol, exchange)
            
            if actual_exit_price > 0:
                # Use actual exit price
                exit_price = actual_exit_price
                pnl_source = "ACTUAL"
            else:
                # Fallback to current market price (try WebSocket first)
                global ws_manager
                if ws_manager:
                    exit_price = ws_manager.get_price(symbol)
                    pnl_source = "WEBSOCKET"
                else:
                    ticker = await exchange.fetch_ticker(symbol)
                    exit_price = float(ticker['close'])
                    pnl_source = "MARKET"
            
            # Calculate P&L before fees
            if position.side == "buy":
                pnl_pct = (exit_price - position.avg_entry_price) / position.avg_entry_price
            else:
                pnl_pct = (position.avg_entry_price - exit_price) / position.avg_entry_price
                
            pnl_usd = position.total_notional_used * pnl_pct
            
            # Determine exit reason with some tolerance for TP detection
            if actual_exit_price > 0:
                if position.side == "buy":
                    action = "TAKE PROFIT" if exit_price >= position.avg_entry_price * (1 + CONFIG.profit_protection.initial_tp_pct * 0.9) else "STOP LOSS"
                else:
                    action = "TAKE PROFIT" if exit_price <= position.avg_entry_price * (1 - CONFIG.profit_protection.initial_tp_pct * 0.9) else "STOP LOSS"
            else:
                action = "POSITION CLOSED"
            
            # Send Discord notification with fees included
            if discord_notifier:
                await discord_notifier.send_profit_alert(
                    symbol, pnl_pct * 100, pnl_usd, action, exit_price
                )
            
            logging.info(f"💰 Position closed: {symbol} | "
                        f"P&L: {pnl_pct*100:+.2f}% (${pnl_usd:+.2f}) | "
                        f"Entry: {position.avg_entry_price:.6f} | "
                        f"Exit: {exit_price:.6f} | Source: {pnl_source}")
            
        except Exception as e:
            logging.error(f"❌ Failed to send position closed alert for {symbol}: {e}")

    async def _get_actual_exit_price(self, symbol: str, exchange) -> float:
        """Get actual exit price from recent order history (OPTIMIZED - 2 hours instead of 24)"""
        try:
            # Get recent order history (last 2 hours instead of 24 to avoid rate limits)
            since = int((time.time() - 7200) * 1000)  # 2 hours ago in milliseconds
            orders = await exchange.fetch_orders(symbol, since=since, limit=20)
            
            # Look for the most recent filled order that closed the position
            for order in reversed(orders):  # Most recent first
                if (order['status'] == 'closed' and 
                    order.get('reduceOnly', False) and 
                    order.get('filled', 0) > 0):
                    
                    # This was likely our exit order
                    return float(order.get('average', 0) or order.get('price', 0))
            
            return 0  # No exit order found
            
        except Exception as e:
            logging.warning(f"Could not fetch exit price for {symbol}: {e}")
            return 0

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
                params={
                    "positionSide": "LONG" if position.side == "buy" else "SHORT"
                }
            )
            logging.info(f"✅ TP set: {position.symbol} @ {tp_price:.6f} ({CONFIG.profit_protection.initial_tp_pct*100:.1f}%)")
            
            # Place SL order if enabled
            if CONFIG.profit_protection.enable_stop_loss:
                sl_side = "sell" if position.side == "buy" else "buy"
                await exchange.create_stop_market_order(
                    position.symbol, sl_side, position.total_qty, sl_price,
                    params={
                        "positionSide": "LONG" if position.side == "buy" else "SHORT", 
                        "reduceOnly": True
                    }
                )
                logging.info(f"✅ SL set: {position.symbol} @ {sl_price:.6f}")
        
            return True
        except Exception as e:
            logging.error(f"❌ TP setup failed {position.symbol}: {e}")
            return False

profit_protection = ProfitProtectionSystem()

# =============================================================================
# MAIN STRATEGY (ENHANCED WITH WEBSOCKET)
# =============================================================================

class VWAPHunterStrategy:
    """VWAP Hunter with enhanced WebSocket integration"""
    
    def __init__(self, exchange):
        self.exchange = exchange
        self.price_cache: Dict[str, float] = {}
        self.last_price_update: Dict[str, float] = {}
        self.entry_timestamps: Dict[str, float] = {}

        # Add momentum detector
        global ws_manager
        if ws_manager:
            self.momentum_detector = MomentumDetector(ws_manager)
        else:
            self.momentum_detector = None
    
    async def get_current_price(self, symbol: str) -> float:
        """Get current price with WebSocket priority"""
        global ws_manager
        
        # Try WebSocket first
        if ws_manager and ws_manager.is_data_fresh('price', symbol, max_age=60):
            price = ws_manager.get_price(symbol)
            if price > 0:
                self.price_cache[symbol] = price
                self.last_price_update[symbol] = time.time()
                return price
        
        # Subscribe to symbol if not already subscribed
        if ws_manager:
            ws_manager.subscribe_symbol(symbol)
        
        # Fallback to cached price or REST API
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
        """Check volume with WebSocket priority"""
        global ws_manager
        
        # Try WebSocket first
        if ws_manager and ws_manager.is_data_fresh('volume', symbol, max_age=300):
            daily_volume = ws_manager.get_volume_24h(symbol)
            if daily_volume > 0:
                passed = daily_volume >= CONFIG.risk.min_24h_volume
                if CONFIG.debug.enable_filter_debug:
                    debug_logger = logging.getLogger('trade_debug')
                    debug_logger.debug(f"Volume filter {symbol}: ${daily_volume:,.0f} ({'PASS' if passed else 'FAIL'}) [WebSocket]")
                return passed, daily_volume
        
        # Subscribe to symbol for future updates
        if ws_manager:
            ws_manager.subscribe_symbol(symbol)
        
        # Fallback to REST API
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            daily_volume = float(ticker.get("quoteVolume", 0))
            passed = daily_volume >= CONFIG.risk.min_24h_volume
            
            if CONFIG.debug.enable_filter_debug:
                debug_logger = logging.getLogger('trade_debug')
                debug_logger.debug(f"Volume filter {symbol}: ${daily_volume:,.0f} ({'PASS' if passed else 'FAIL'}) [REST API]")
            
            return passed, daily_volume
        except Exception as e:
            if CONFIG.debug.enable_data_debug:
                logging.warning(f"Volume check failed for {symbol}: {e}")
            return False, 0
    
    async def process_liquidation_event(self, liquidation_data: Dict):
        """Process liquidation event with comprehensive debugging and WebSocket integration"""
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
            
            # Ensure WebSocket subscription
            global ws_manager
            if ws_manager:
                ws_manager.subscribe_symbol(symbol)
            
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
            
            # Filter 2: Existing position check (WebSocket enhanced sync)
            await position_manager.sync_with_exchange(self.exchange)
            
            # Filter 3: Max positions check (use WebSocket when available)
            if ws_manager and ws_manager.is_data_fresh('positions'):
                active_positions_count = len(ws_manager.get_positions())
            else:
                active_positions = await position_manager.sync_with_exchange(self.exchange)
                active_positions_count = len(active_positions)
            
            if active_positions_count >= CONFIG.risk.max_positions:
                stats.log_filter_rejection('max_positions')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"❌ {symbol}: Max positions reached ({active_positions_count}/{CONFIG.risk.max_positions})")
                return
            
            # Filter 4: Volume check
            volume_pass, daily_volume = await self.check_volume_filter(symbol)
            if not volume_pass:
                stats.log_filter_rejection('volume_filter')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"❌ {symbol}: Volume too low (${daily_volume:,.0f} < ${CONFIG.risk.min_24h_volume:,.0f})")
                return
            
            # ADD MOMENTUM FILTER
            if CONFIG.momentum.enable_momentum_filter and self.momentum_detector:
                # Ensure symbol is subscribed for momentum data
                if ws_manager:
                    ws_manager.subscribe_symbol(symbol)
                
                # Get momentum signal
                momentum_signal, momentum_reason, momentum_metrics = self.momentum_detector.get_momentum_signal(symbol)
                
                if momentum_signal == "AVOID":
                    stats.log_filter_rejection('momentum_filter')
                    if CONFIG.debug.enable_filter_debug:
                        logging.info(f"❌ {symbol}: Momentum filter - {momentum_reason}")
                    return
                
                # Log momentum info for successful signals
                if CONFIG.debug.enable_trade_debug:
                    debug_logger = logging.getLogger('trade_debug')
                    debug_logger.debug(f"📈 MOMENTUM {symbol}: {momentum_reason}")
            
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

            # OPTIONAL: Use momentum to enhance entry signals
            if self.momentum_detector and momentum_signal in ["ENHANCE_LONG", "ENHANCE_SHORT"]:
                # You could adjust position size or be more aggressive with entry levels
                if CONFIG.debug.enable_trade_debug:
                    debug_logger = logging.getLogger('trade_debug')
                    debug_logger.debug(f"🚀 MOMENTUM ENHANCEMENT: {symbol} - {momentum_reason}")
            
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

                # Set initial take profit with error recovery
                tp_success = await profit_protection.set_initial_take_profit(self.exchange, position)
                if not tp_success:
                    logging.warning(f"⚠️ TP setup failed for {symbol}, position created without protection")
                
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
        """Execute the actual trade with better error handling"""
        try:
            # Set leverage with error handling
            try:
                await self.exchange.set_margin_mode('cross', position.symbol)
                await self.exchange.set_leverage(CONFIG.leverage, position.symbol)
            except Exception as e:
                if "not modified" not in str(e).lower():
                    logging.warning(f"Leverage setup failed for {position.symbol}: {e}")
                # Continue anyway - leverage might already be set correctly
            
            # Execute market order
            order = await self.exchange.create_market_order(
                position.symbol, position.side, position.total_qty,
                params={"positionSide": "LONG" if position.side == "buy" else "SHORT"}
            )
            
            # Update with actual execution price
            actual_price = float(order.get('price', position.avg_entry_price)) or position.avg_entry_price
            position.avg_entry_price = actual_price
            position.total_notional_used = position.total_qty * actual_price  # Update with actual price
            
            return True
        except Exception as e:
            logging.error(f"❌ Trade execution failed for {position.symbol}: {e}")
            return False
    
    async def monitor_positions(self):
        """Enhanced position monitoring with WebSocket integration"""
        while True:
            try:
                # Sync positions with exchange
                await position_manager.sync_with_exchange(self.exchange)
                
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
                                # Cancel and update TP orders after DCA
                                await self._update_tp_after_dca(symbol, position)
                    except Exception as e:
                        logging.error(f"Position monitoring error for {symbol}: {e}")
                        continue
                
                await asyncio.sleep(15)  # Check every 15 seconds
            except Exception as e:
                logging.error(f"Position monitoring error: {e}")
                await asyncio.sleep(60)

    async def _update_tp_after_dca(self, symbol: str, position: Position):
        """Update TP orders after DCA execution with better error handling"""
        try:
            # Cancel existing TP orders
            open_orders = await self.exchange.fetch_open_orders(symbol)
            for order in open_orders:
                if (order['type'] == 'limit' and 
                    order.get('reduceOnly', False)):
                    try:
                        await self.exchange.cancel_order(order['id'], symbol)
                    except Exception as e:
                        logging.warning(f"Failed to cancel order {order['id']} for {symbol}: {e}")
            
            # Set new TP with updated position size
            tp_success = await profit_protection.set_initial_take_profit(self.exchange, position)
            if not tp_success:
                logging.warning(f"Failed to set new TP after DCA for {symbol}")
            
        except Exception as e:
            logging.error(f"Failed to update TP after DCA for {symbol}: {e}")

# =============================================================================
# MAIN BOT EXECUTION (ENHANCED WITH WEBSOCKET)
# =============================================================================

async def main_bot():
    """Main bot execution"""
    setup_enhanced_logging()
    logging.info("⚡ 0xLIQD - STARTING UP...")
    logging.info("=" * 70)
    
    global ws_manager
    
    try:
        # Initialize exchange
        exchange = get_exchange()
        logging.info("✅ Exchange connected")
        
        # Initialize WebSocket manager
        ws_manager = BinanceWebSocketManager(CONFIG.api_key, CONFIG.api_secret)
        await ws_manager.initialize()
        logging.info("✅ WebSocket manager initialized")
        
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
        
        # Subscribe all trading pairs to WebSocket for market data
        for symbol in pairs_data.keys():
            ws_manager.subscribe_symbol(symbol)
        logging.info(f"📡 Subscribed to {len(pairs_data)} symbols for WebSocket market data")
        
        # Initialize strategy
        strategy = VWAPHunterStrategy(exchange)
        logging.info("✅ Strategy initialized with WebSocket integration")
        
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
        logging.info(f"  WebSocket Data: ENABLED")
        logging.info(f"  Real-time Position Sync: ENABLED")
        logging.info(f"  Rate Limit Protection: ACTIVE")
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
        if ws_manager:
            await ws_manager.close()
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
    """Main entry point with WebSocket support"""
    try:
        print("⚡ Starting 0xLIQD...")
        
        # Validate configuration
        if not CONFIG.api_key or not CONFIG.api_secret:
            print("❌ Missing Binance API credentials in config.yaml")
            return
        
        if CONFIG.rapidapi.api_key:
            print("✅ RapidAPI key configured")
        else:
            print("⚠️ No RapidAPI key - using cached zones data only")
        
        print("✅ Configuration validated")
        print("🚀 Launching bot...")
        print("📊 Features: Real-time data, Rate limit protection, Enhanced position sync")
        
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