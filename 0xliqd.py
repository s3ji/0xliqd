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
import statistics
import websockets

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

@dataclass
class PairAgeConfig:
    """Configuration for pair age filtering"""
    min_age_days: int = 30
    enable_age_filter: bool = True
    cache_duration_hours: int = 24
    api_timeout_seconds: int = 10

@dataclass
class PairFilterConfig:
    """Configuration for pair whitelist/blacklist filtering"""
    enable_whitelist: bool = False
    enable_blacklist: bool = False
    whitelist_file: str = "whitelist.txt"
    blacklist_file: str = "blacklist.txt"
    auto_reload: bool = True
    reload_interval_minutes: int = 5

@dataclass
class CompoundingConfig:
    """Auto-compounding configuration"""
    enable_compounding: bool = False
    base_min_notional: float = 15.0         # Always use at least this amount
    compounding_percentage: float = 0.02    # Use 2% of total balance

@dataclass
class Config:
    """Main configuration class"""
    api_key: str = ""
    api_secret: str = ""
    discord_webhook_url: str = ""
    leverage: int = 10
    margin_mode: str = "cross"
    min_notional: float = 11.0
    rapidapi: RapidAPIConfig = field(default_factory=RapidAPIConfig)
    vwap: VWAPConfig = field(default_factory=VWAPConfig)
    dca: DCAConfig = field(default_factory=DCAConfig)
    profit_protection: ProfitProtectionConfig = field(default_factory=ProfitProtectionConfig)
    market_regime: MarketRegimeConfig = field(default_factory=MarketRegimeConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)
    momentum: MomentumConfig = field(default_factory=MomentumConfig)
    pair_age: PairAgeConfig = field(default_factory=PairAgeConfig)
    pair_filter: PairFilterConfig = field(default_factory=PairFilterConfig)
    compounding: CompoundingConfig = field(default_factory=CompoundingConfig)
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
                         MarketRegimeConfig, RiskConfig, DebugConfig, MomentumConfig, PairAgeConfig, PairFilterConfig, CompoundingConfig)):
                        setattr(config, key, value)
                
                for sub_config_name in ['rapidapi', 'vwap', 'dca', 'profit_protection', 
                                       'market_regime', 'risk', 'debug', 'momentum', 'pair_age', 'pair_filter', 'compounding']:
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
# PAIR AGE FILTER
# =============================================================================

class PairAgeFilter:
    """Filter trading pairs based on listing age"""
    
    def __init__(self):
        self.age_cache: Dict[str, Dict] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        
    async def initialize(self):
        """Initialize HTTP session"""
        if not self.session or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=CONFIG.pair_age.api_timeout_seconds)
            self.session = aiohttp.ClientSession(timeout=timeout)
    
    async def get_pair_listing_date(self, exchange, symbol: str) -> Optional[datetime]:
        """Get pair listing date using multiple methods"""
        
        # Method 1: Try exchange API for market info
        try:
            markets = await exchange.load_markets()
            ccxt_symbol = to_ccxt_symbol(symbol)
            
            if ccxt_symbol in markets:
                market_info = markets[ccxt_symbol]
                # Some exchanges provide listing timestamp in market info
                if 'info' in market_info and isinstance(market_info['info'], dict):
                    listing_time = market_info['info'].get('listingDate') or market_info['info'].get('onboardDate')
                    if listing_time:
                        try:
                            return datetime.fromtimestamp(int(listing_time) / 1000)
                        except (ValueError, TypeError):
                            pass
                        
        except Exception as e:
            logging.debug(f"Market info method failed for {symbol}: {e}")
        
        # Method 2: Try historical klines approach
        try:
            return await self._get_listing_date_from_klines(exchange, symbol)
        except Exception as e:
            logging.debug(f"Klines method failed for {symbol}: {e}")
        
        # Method 3: Try Binance public API
        try:
            return await self._get_listing_date_from_binance_api(symbol)
        except Exception as e:
            logging.debug(f"Binance API method failed for {symbol}: {e}")
        
        return None
    
    async def _get_listing_date_from_klines(self, exchange, symbol: str) -> Optional[datetime]:
        """Get listing date by finding earliest available kline data"""
        try:
            ccxt_symbol = to_ccxt_symbol(symbol)
            
            # Try to get very old data (2 years ago)
            two_years_ago = int((datetime.now() - timedelta(days=730)).timestamp() * 1000)
            
            # Fetch klines from 2 years ago with limit 1
            klines = await exchange.fetch_ohlcv(
                ccxt_symbol, 
                '1d', 
                since=two_years_ago, 
                limit=1
            )
            
            if klines and len(klines) > 0:
                # First available timestamp
                first_timestamp = klines[0][0]
                return datetime.fromtimestamp(first_timestamp / 1000)
                
        except Exception as e:
            logging.debug(f"Klines listing date failed for {symbol}: {e}")
        
        return None
    
    async def _get_listing_date_from_binance_api(self, symbol: str) -> Optional[datetime]:
        """Get listing date from Binance public API"""
        try:
            await self.initialize()
            
            # Binance doesn't have a direct listing date API, but we can try exchange info
            url = "https://api.binance.com/api/v3/exchangeInfo"
            
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Find our symbol in the symbols list
                    for symbol_info in data.get('symbols', []):
                        if symbol_info.get('symbol') == normalize_symbol(symbol):
                            # Some symbols have baseAssetPrecision or other timing info
                            if 'permissions' in symbol_info:
                                # If it has SPOT and MARGIN permissions, it's likely older
                                permissions = symbol_info.get('permissions', [])
                                if len(permissions) > 1:
                                    # Assume older pairs (rough heuristic)
                                    return datetime.now() - timedelta(days=365)
                            break
                            
        except Exception as e:
            logging.debug(f"Binance API listing date failed for {symbol}: {e}")
        
        return None
    
    async def get_pair_age_days(self, exchange, symbol: str) -> Optional[int]:
        """Get pair age in days with caching"""
        normalized_symbol = normalize_symbol(symbol)
        
        # Check cache
        if normalized_symbol in self.age_cache:
            cache_entry = self.age_cache[normalized_symbol]
            cache_age = datetime.now() - cache_entry['cached_at']
            
            if cache_age < timedelta(hours=CONFIG.pair_age.cache_duration_hours):
                return cache_entry['age_days']
        
        # Fetch fresh data
        listing_date = await self.get_pair_listing_date(exchange, normalized_symbol)
        
        if listing_date:
            age_days = (datetime.now() - listing_date).days
            
            # Cache result
            self.age_cache[normalized_symbol] = {
                'age_days': age_days,
                'listing_date': listing_date,
                'cached_at': datetime.now()
            }
            
            logging.debug(f"Pair age: {normalized_symbol} = {age_days} days (listed: {listing_date.strftime('%Y-%m-%d')})")
            return age_days
        else:
            # Cache negative result to avoid repeated failed lookups
            self.age_cache[normalized_symbol] = {
                'age_days': None,
                'listing_date': None,
                'cached_at': datetime.now()
            }
            
            logging.debug(f"Could not determine age for {normalized_symbol}")
            return None
    
    async def should_trade_pair(self, exchange, symbol: str) -> tuple[bool, str]:
        """Check if pair meets age requirements"""
        if not CONFIG.pair_age.enable_age_filter:
            return True, "Age filter disabled"
        
        age_days = await self.get_pair_age_days(exchange, symbol)
        
        if age_days is None:
            # If we can't determine age, reject trading.
            return False, "Age unknown - skipping trade"
        
        min_age = CONFIG.pair_age.min_age_days
        
        if age_days >= min_age:
            return True, f"Pair age: {age_days} days (>= {min_age})"
        else:
            return False, f"Pair too new: {age_days} days (< {min_age})"
    
    async def close(self):
        """Clean shutdown"""
        if self.session and not self.session.closed:
            await self.session.close()

# Global instance
pair_age_filter = PairAgeFilter()

# =============================================================================
# PAIR FILTER MANAGER
# =============================================================================

class PairFilterManager:
    """Manages whitelist and blacklist for trading pairs"""
    
    def __init__(self, config: PairFilterConfig):
        self.config = config
        self.whitelist: set = set()
        self.blacklist: set = set()
        self.last_reload = 0
        self.reload_task: Optional[asyncio.Task] = None
        
    async def initialize(self):
        """Initialize the filter manager"""
        await self.load_filters()
        
        if self.config.auto_reload:
            self.reload_task = asyncio.create_task(self._auto_reload_loop())
            logging.info("Pair filter auto-reload enabled")
    
    async def load_filters(self):
        """Load whitelist and blacklist from files"""
        try:
            # Load whitelist
            if self.config.enable_whitelist:
                self.whitelist = await self._load_pairs_from_file(self.config.whitelist_file)
                logging.info(f"Loaded whitelist: {len(self.whitelist)} pairs")
                if self.whitelist:
                    logging.info(f"Whitelist samples: {list(self.whitelist)[:5]}")
            
            # Load blacklist
            if self.config.enable_blacklist:
                self.blacklist = await self._load_pairs_from_file(self.config.blacklist_file)
                logging.info(f"Loaded blacklist: {len(self.blacklist)} pairs")
                if self.blacklist:
                    logging.info(f"Blacklist samples: {list(self.blacklist)[:5]}")
            
            self.last_reload = time.time()
            
        except Exception as e:
            logging.error(f"Failed to load pair filters: {e}")
    
    async def _load_pairs_from_file(self, filename: str) -> set:
        """Load pairs from a text file"""
        pairs = set()
        
        try:
            if Path(filename).exists():
                with open(filename, 'r') as f:
                    for line in f:
                        line = line.strip().upper()
                        if line and not line.startswith('#'):
                            # Normalize the symbol
                            normalized = normalize_symbol(line)
                            if normalized:
                                pairs.add(normalized)
            else:
                # Create example file
                await self._create_example_file(filename)
                
        except Exception as e:
            logging.error(f"Error loading pairs from {filename}: {e}")
        
        return pairs
    
    async def _create_example_file(self, filename: str):
        """Create an example filter file"""
        try:
            example_content = """# Pair Filter File
# Add one symbol per line (case insensitive)
# Lines starting with # are comments
# 
# Examples:
# BTCUSDT
# ETHUSDT
# ADAUSDT
# SOLUSDT
# DOGEUSDT
#
# You can use different formats:
# BTC/USDT (will be converted to BTCUSDT)
# BTC/USDT:USDT (will be converted to BTCUSDT)
# 
# Add your pairs below:

"""
            with open(filename, 'w') as f:
                f.write(example_content)
            logging.info(f"Created example filter file: {filename}")
        except Exception as e:
            logging.error(f"Failed to create example file {filename}: {e}")
    
    async def _auto_reload_loop(self):
        """Auto-reload filters periodically"""
        while True:
            try:
                await asyncio.sleep(self.config.reload_interval_minutes * 60)
                old_whitelist_count = len(self.whitelist)
                old_blacklist_count = len(self.blacklist)
                
                await self.load_filters()
                
                # Log changes
                if self.config.enable_whitelist and len(self.whitelist) != old_whitelist_count:
                    logging.info(f"Whitelist updated: {old_whitelist_count} -> {len(self.whitelist)} pairs")
                
                if self.config.enable_blacklist and len(self.blacklist) != old_blacklist_count:
                    logging.info(f"Blacklist updated: {old_blacklist_count} -> {len(self.blacklist)} pairs")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Auto-reload error: {e}")
                await asyncio.sleep(60)
    
    def should_trade_pair(self, symbol: str) -> tuple[bool, str]:
        """Check if pair should be traded based on filters"""
        normalized_symbol = normalize_symbol(symbol)
        
        # Whitelist check (if enabled)
        if self.config.enable_whitelist:
            if normalized_symbol not in self.whitelist:
                return False, f"Not in whitelist ({len(self.whitelist)} allowed pairs)"
        
        # Blacklist check (if enabled)
        if self.config.enable_blacklist:
            if normalized_symbol in self.blacklist:
                return False, f"In blacklist ({len(self.blacklist)} blocked pairs)"
        
        # Determine which filters are active
        active_filters = []
        if self.config.enable_whitelist:
            active_filters.append("whitelist")
        if self.config.enable_blacklist:
            active_filters.append("blacklist")
        
        if active_filters:
            return True, f"Passed {'/'.join(active_filters)} filter"
        else:
            return True, "No filters enabled"
    
    def get_filter_stats(self) -> dict:
        """Get current filter statistics"""
        return {
            'whitelist_enabled': self.config.enable_whitelist,
            'blacklist_enabled': self.config.enable_blacklist,
            'whitelist_count': len(self.whitelist),
            'blacklist_count': len(self.blacklist),
            'last_reload_minutes': (time.time() - self.last_reload) / 60,
            'auto_reload': self.config.auto_reload
        }
    
    async def close(self):
        """Clean shutdown"""
        if self.reload_task:
            self.reload_task.cancel()
            try:
                await self.reload_task
            except asyncio.CancelledError:
                pass
        logging.info("Pair filter manager closed")

# Global instance
pair_filter_manager: Optional[PairFilterManager] = None

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
            'pair_age_filter': 0,
            'whitelist_filter': 0,
            'blacklist_filter': 0,
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
        
        # Add timestamp status if available
        global timestamp_manager
        if timestamp_manager:
            ts_status = timestamp_manager.get_status()
            summary += f"\n\nTimestamp Management:\n"
            summary += f"  Current Offset: {ts_status['current_offset']:.0f}ms\n"
            summary += f"  Last Calibration: {ts_status['calibration_age_minutes']:.1f} minutes ago\n"
            summary += f"  Total Calibrations: {ts_status['calibration_count']}\n"

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
        """Optimized subscription"""
        normalized = normalize_symbol(symbol)
        if normalized.endswith('USDT') and normalized not in self.subscribed_symbols:
            self.subscribed_symbols.add(normalized)
            if self.is_streaming:
                # Only subscribe if not already subscribed
                asyncio.create_task(self._stream_symbol_data(normalized))
    
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

class DiscordNotifier:
    """Enhanced Discord notifications with better error handling"""
    
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_sent = 0
        self.rate_limit = 1
        self.is_initialized = False
        
        logging.info(f"Discord notifier initialized with webhook: {webhook_url[:50]}...")
    
    async def initialize(self):
        """Initialize with better error handling"""
        try:
            if not self.session or self.session.closed:
                timeout = aiohttp.ClientTimeout(total=30)  # Increased timeout
                connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
                self.session = aiohttp.ClientSession(
                    timeout=timeout, 
                    connector=connector,
                    headers={'User-Agent': '0xLIQD-Bot/1.0'}
                )
                self.is_initialized = True
                logging.debug("Discord session initialized")
        except Exception as e:
            logging.error(f"Discord initialization failed: {e}")
            self.is_initialized = False
    
    async def _send_webhook(self, payload: dict, max_retries: int = 2) -> bool:
        for attempt in range(max_retries):
            try:
                await self.initialize()
                async with self.session.post(self.webhook_url, json=payload) as response:
                    return response.status == 204
            except Exception as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    continue
                logging.warning(f"Discord send failed: {e}")
        return False
    
    async def send_startup_alert(self, zones_count: int, pairs_count: int):
        """Send enhanced startup notification"""
        try:
            # Check rate limit
            now = time.time()
            if now - self.last_sent < self.rate_limit:
                logging.debug("Discord startup alert rate limited")
                return

            # Get filter stats
            filter_info = ""
            global pair_filter_manager
            if pair_filter_manager:
                stats = pair_filter_manager.get_filter_stats()
                if stats['whitelist_enabled'] or stats['blacklist_enabled']:
                    filter_parts = []
                    if stats['whitelist_enabled']:
                        filter_parts.append(f"Whitelist: {stats['whitelist_count']}")
                    if stats['blacklist_enabled']:
                        filter_parts.append(f"Blacklist: {stats['blacklist_count']}")
                    filter_info = f"\n**Filters:** {' | '.join(filter_parts)}"
            
            embed = {
                "title": "⚡ 0xLIQD - ACTIVE",
                "description": "```Real-time liquidation hunting bot for Binance Futures. Monitoring liquidation streams, calculating VWAP zones, and executing automated entries with DCA and profit protection.```",
                "color": 0x00ff88,
                "fields": [
                    {
                        "name": "📊 Configuration",
                        "value": f"**Min. Notional:** ${CONFIG.min_notional}\n**Isolation:** {CONFIG.risk.isolation_pct*100}%\n**DCA Levels:** {CONFIG.dca.max_levels}",
                        "inline": True
                    },
                    {
                        "name": "🔧 Data Status",
                        "value": f"**Zones:** {zones_count}\n**Pairs:** {pairs_count}\n**Max Positions:** {CONFIG.risk.max_positions}{filter_info}",
                        "inline": True
                    },
                    {
                        "name": "🎯 Strategy",
                        "value": f"**Margin Mode:** {CONFIG.margin_mode}\n**Leverage:** {CONFIG.leverage}x\n**TP:** {CONFIG.profit_protection.initial_tp_pct*100:.1f}%\n**Momentum Filter:** {'ON' if CONFIG.momentum.enable_momentum_filter else 'OFF'}",
                        "inline": True
                    }
                ],
                "footer": {"text": "Powered by 0xLIQD"},
                "timestamp": datetime.utcnow().isoformat()
            }
            
            payload = {"username": "⚡ 0xLIQD", "embeds": [embed]}
            
            success = await self._send_webhook(payload)
            if success:
                self.last_sent = now
                logging.info("✅ Startup notification sent to Discord")
            else:
                logging.warning("❌ Failed to send startup notification")
                
        except Exception as e:
            logging.error(f"Startup notification error: {e}")
    
    async def send_trade_alert(self, symbol: str, side: str, qty: float, price: float,
                             reason: str, notional: float, is_dca: bool = False, dca_level: int = 0):
        """Send trade alert with improved formatting"""
        try:
            # Check rate limit
            now = time.time()
            if now - self.last_sent < self.rate_limit:
                logging.debug(f"Discord trade alert rate limited for {symbol}")
                return
            
            color = 0x00ff88 if side == "buy" else 0xff6b6b
            
            if is_dca:
                title = f"🔥 DCA L{dca_level} • {symbol}"
                description = f"```{side.upper()} ${notional:.2f} @ {price:.6f}```"
                emoji = "📈" if side == "buy" else "📉"
            else:
                title = f"⚡ ENTRY • {symbol}"
                description = f"```{side.upper()} ${notional:.2f} @ {price:.6f}```"
                emoji = "🎯"
            
            # Add current timestamp info
            global timestamp_manager
            ts_status = ""
            if timestamp_manager:
                status = timestamp_manager.get_status()
                ts_status = f"\n**Timestamp Offset:** {status['current_offset']:.0f}ms"
            
            embed = {
                "title": title,
                "description": description,
                "color": color,
                "fields": [
                    {
                        "name": "💰 Trade Details",
                        "value": f"**Notional:** ${notional:.2f}\n**Quantity:** {qty:.6f}\n**Leverage:** {CONFIG.leverage}x{ts_status}",
                        "inline": True
                    },
                    {
                        "name": f"{emoji} Entry Reason",
                        "value": reason[:100] + ("..." if len(reason) > 100 else ""),
                        "inline": True
                    }
                ],
                "timestamp": datetime.utcnow().isoformat(),
                "footer": {"text": f"0xLIQD • {datetime.now().strftime('%H:%M:%S')}"}
            }
            
            payload = {"username": "⚡ 0xLIQD", "embeds": [embed]}
            
            success = await self._send_webhook(payload)
            if success:
                self.last_sent = now
                logging.info(f"✅ Trade alert sent: {symbol} {side}")
            else:
                logging.warning(f"❌ Failed to send trade alert for {symbol}")
                
        except Exception as e:
            logging.error(f"Trade alert error for {symbol}: {e}")
    
    async def send_profit_alert(self, symbol: str, pnl_pct: float, pnl_usd: float,
                          action: str, price: float, additional_info: dict = None):
        """Send profit/loss alert with accurate exchange data"""
        try:
            # Check rate limit
            now = time.time()
            if now - self.last_sent < self.rate_limit:
                logging.debug(f"Discord P&L alert rate limited for {symbol}")
                return
            
            color = 0x00ff00 if pnl_pct > 0 else 0xff0000
            emoji = "🎉" if pnl_pct > 0 else "⚠️"
            result = "WIN 🟢" if pnl_pct > 0 else "LOSS 🔴"
            
            # Add performance context
            performance_emoji = "🚀" if pnl_pct >= 1 else "📈" if pnl_pct > 0 else "📉" if pnl_pct >= -2 else "💥"
            
            # Action description based on exit reason
            action_emojis = {
                "TAKE PROFIT": "🎯 TP HIT",
                "STOP LOSS": "🛑 SL HIT", 
                "MANUAL CLOSE": "👤 MANUAL",
                "POSITION CLOSED": "📤 CLOSED",
                "LIQUIDATION": "💥 LIQUIDATED"
            }
            
            enhanced_action = action_emojis.get(action, action)
            
            # Build the embed fields
            fields = [
                {
                    "name": "💵 Results",
                    "value": f"**P&L:** {pnl_pct:+.2f}% {performance_emoji}\n**USD:** ${pnl_usd:+.2f}",
                    "inline": True
                },
                {
                    "name": "📊 Exit Details", 
                    "value": f"**Method:** {enhanced_action}\n**Price:** {price:.6f}\n**Time:** {datetime.now().strftime('%H:%M:%S')}",
                    "inline": True
                }
            ]
            
            # Add additional info if provided (from position history)
            if additional_info:
                extra_info = []
                if 'entry_price' in additional_info:
                    extra_info.append(f"**Entry:** {additional_info['entry_price']:.6f}")
                if 'quantity' in additional_info:
                    extra_info.append(f"**Qty:** {additional_info['quantity']:.6f}")
                if 'fees_paid' in additional_info:
                    extra_info.append(f"**Fees:** ${additional_info['fees_paid']:.2f}")
                
                if extra_info:
                    fields.append({
                        "name": "📋 Trade Info",
                        "value": "\n".join(extra_info),
                        "inline": True
                    })
            
            embed = {
                "title": f"{emoji} {result} • {symbol}",
                "description": f"```{enhanced_action} @ {price:.6f}```",
                "color": color,
                "fields": fields,
                "timestamp": datetime.utcnow().isoformat(),
                "footer": {"text": "Powered by 0xLIQD"}
            }
            
            payload = {"username": "💰 0xLIQD P&L", "embeds": [embed]}
            
            success = await self._send_webhook(payload)
            if success:
                self.last_sent = now
                logging.info(f"✅ P&L alert sent: {symbol} {pnl_pct:+.2f}% via {action}")
            else:
                logging.warning(f"❌ Failed to send P&L alert for {symbol}")
                
        except Exception as e:
            logging.error(f"P&L alert error for {symbol}: {e}")
    
    async def send_stats_update(self, summary: str):
        """Send periodic stats update"""
        try:
            # Less strict rate limiting for stats
            now = time.time()
            if now - self.last_sent < 1:
                return
            
            # Truncate summary to fit Discord limits
            truncated_summary = summary[:1800] if len(summary) > 1800 else summary
            
            embed = {
                "title": "📊 Trading Statistics",
                "description": f"```{truncated_summary}```",
                "color": 0x00ff88,
                "timestamp": datetime.utcnow().isoformat(),
                "footer": {"text": f"0xLIQD Stats • {datetime.now().strftime('%H:%M')}"}
            }
            
            payload = {"username": "📊 0xLIQD Stats", "embeds": [embed]}
            
            success = await self._send_webhook(payload)
            if success:
                self.last_sent = now
                logging.info("✅ Stats update sent to Discord")
            else:
                logging.debug("❌ Failed to send stats update")
                
        except Exception as e:
            logging.error(f"Stats update error: {e}")
    
    async def close(self):
        """Clean shutdown"""
        try:
            if self.session and not self.session.closed:
                await self.session.close()
                logging.debug("Discord session closed")
        except Exception as e:
            logging.warning(f"Discord close error: {e}")

# Initialize Discord notifier
discord_notifier = None
if CONFIG.enable_discord and CONFIG.discord_webhook_url:
    discord_notifier = DiscordNotifier(CONFIG.discord_webhook_url)
    logging.info("🎯 Discord notifier created")
else:
    logging.warning("⚠️ Discord notifications disabled (missing config)")

# =============================================================================
# TIMESTAMP MANAGER
# =============================================================================

class PeriodicTimestampManager:
    """Manages timestamp synchronization with periodic recalibration"""
    
    def __init__(self, exchange):
        self.exchange = exchange
        self.forced_offset = 0
        self.calibrated = False
        self.last_calibration = 0
        self.calibration_interval = 300  # Recalibrate every 5 minutes
        self.monitoring_task = None
        self.is_running = False
        
        # Statistics tracking
        self.calibration_history = []
        self.max_history = 20
        
    async def calibrate_forced_offset(self):
        """Determine how much we need to adjust our timestamps"""
        try:
            logging.info("Calibrating timestamp offset...")
            
            # Take multiple samples for accuracy
            samples = []
            
            for i in range(5):
                try:
                    local_before = time.time() * 1000  # Use system time instead of exchange time
                    server_time = await self.exchange.fetch_time()
                    local_after = time.time() * 1000
                    
                    # Account for network round-trip time
                    network_delay = (local_after - local_before) / 2
                    local_mid = local_before + network_delay
                    
                    # Calculate offset
                    offset = server_time - local_mid
                    samples.append(offset)
                    
                    logging.debug(f"Sample {i+1}: offset={offset:.0f}ms")
                    
                except Exception as e:
                    logging.warning(f"Sample {i+1} failed: {e}")
                    continue
                
                if i < 4:  # Don't sleep after last sample
                    await asyncio.sleep(0.5)
            
            if not samples:
                raise Exception("All calibration samples failed")
            
            # Use median to filter out outliers
            median_offset = statistics.median(samples)
            
            # Add safety margin (be conservative - stay behind server time)
            safety_margin = 2000  # 2 seconds
            self.forced_offset = median_offset - safety_margin
            
            # Track calibration history
            self.calibration_history.append({
                'timestamp': time.time(),
                'offset': self.forced_offset,
                'samples': len(samples),
                'raw_offset': median_offset
            })
            
            # Keep only recent history
            if len(self.calibration_history) > self.max_history:
                self.calibration_history.pop(0)
            
            self.last_calibration = time.time()
            self.calibrated = True
            
            logging.info(f"Calibration complete: offset={self.forced_offset:.0f}ms "
                        f"(raw: {median_offset:.0f}ms, safety: {safety_margin}ms)")
            
            return True
            
        except Exception as e:
            logging.error(f"Calibration failed: {e}")
            
            # Use historical average if available
            if self.calibration_history:
                avg_offset = statistics.mean([h['offset'] for h in self.calibration_history])
                self.forced_offset = avg_offset
                logging.warning(f"Using historical average offset: {self.forced_offset:.0f}ms")
                self.calibrated = True
                return True
            
            # Ultimate fallback
            self.forced_offset = -3000
            self.calibrated = True
            logging.warning(f"Using emergency fallback offset: {self.forced_offset}ms")
            return False
    
    def get_adjusted_timestamp(self):
        """Get timestamp adjusted for Binance"""
        return int(time.time() * 1000 + self.forced_offset)
    
    async def configure_exchange(self):
        """Configure exchange with timestamp adjustment"""
        if not self.calibrated:
            await self.calibrate_forced_offset()
        
        # Override the exchange's milliseconds function
        def adjusted_milliseconds():
            return self.get_adjusted_timestamp()
        
        # Monkey patch
        self.exchange.milliseconds = adjusted_milliseconds
        
        # Configure exchange options
        self.exchange.options.update({
            'recvWindow': 25000,  # 25 seconds (generous but not excessive)
            'adjustForTimeDifference': False,
            'timeDifference': 0,
        })
        
        logging.info("Exchange configured with periodic timestamp management")
    
    async def start_monitoring(self):
        """Start the periodic monitoring task"""
        if self.is_running:
            return
        
        self.is_running = True
        self.monitoring_task = asyncio.create_task(self._monitoring_loop())
        logging.info("Timestamp monitoring started")
    
    async def stop_monitoring(self):
        """Stop the periodic monitoring task"""
        self.is_running = False
        
        if self.monitoring_task and not self.monitoring_task.done():
            self.monitoring_task.cancel()
            try:
                await asyncio.wait_for(self.monitoring_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        
        logging.info("Timestamp monitoring stopped")
    
    async def _monitoring_loop(self):
        """Background monitoring loop"""
        while self.is_running:
            try:
                current_time = time.time()
                
                # Check if recalibration is needed
                if current_time - self.last_calibration >= self.calibration_interval:
                    old_offset = self.forced_offset
                    
                    success = await self.calibrate_forced_offset()
                    
                    if success:
                        offset_change = abs(self.forced_offset - old_offset)
                        if offset_change > 500:  # More than 500ms change
                            logging.warning(f"Significant offset change detected: "
                                          f"{old_offset:.0f}ms -> {self.forced_offset:.0f}ms")
                        
                        # Update exchange configuration
                        def adjusted_milliseconds():
                            return self.get_adjusted_timestamp()
                        
                        self.exchange.milliseconds = adjusted_milliseconds
                
                # Sleep until next check (but wake up if stopped)
                sleep_time = min(60, self.calibration_interval)  # Check at least every minute
                
                for _ in range(sleep_time):
                    if not self.is_running:
                        break
                    await asyncio.sleep(1)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Timestamp monitoring error: {e}")
                await asyncio.sleep(30)  # Back off on errors
    
    def get_status(self):
        """Get current status information"""
        return {
            'calibrated': self.calibrated,
            'current_offset': self.forced_offset,
            'last_calibration': self.last_calibration,
            'calibration_age_minutes': (time.time() - self.last_calibration) / 60,
            'calibration_count': len(self.calibration_history),
            'is_monitoring': self.is_running
        }

# Global timestamp manager
timestamp_manager: Optional[PeriodicTimestampManager] = None

async def initialize_timestamp_management(exchange):
    """Initialize and start periodic timestamp management"""
    global timestamp_manager
    
    try:
        timestamp_manager = PeriodicTimestampManager(exchange)
        
        # Initial configuration
        await timestamp_manager.configure_exchange()
        
        # Start periodic monitoring
        await timestamp_manager.start_monitoring()
        
        # Test the configuration
        test_time = await exchange.fetch_time()
        logging.info(f"Timestamp management initialized successfully. Server time: {test_time}")
        
        return True
        
    except Exception as e:
        logging.error(f"Timestamp management initialization failed: {e}")
        return False

async def cleanup_timestamp_management():
    """Clean up timestamp management and order monitoring"""
    global timestamp_manager
    
    if timestamp_manager:
        await timestamp_manager.stop_monitoring()
        timestamp_manager = None
    
    # Also stop profit protection monitoring
    if profit_protection:
        await profit_protection.stop_order_monitoring()

# =============================================================================
# EXCHANGE INTERFACE
# =============================================================================

def get_exchange():
    """Get configured Binance exchange"""
    exchange = ccxtpro.binanceusdm({
        'apiKey': CONFIG.api_key,
        'secret': CONFIG.api_secret,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future',
            'sandBox': False,
            'recvWindow': 30000,
            'adjustForTimeDifference': False,
            'keepAlive': False,
        },
        'timeout': 30000,
    })
    
    return exchange

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
        """Get VWAP levels - API provides pre-calculated entry prices"""
        
        # Get pre-calculated entry prices from RapidAPI
        api_long_entry_price, api_short_entry_price, mean_volume = rapidapi_client.get_zones(symbol)
        
        final_long_level = 0
        final_short_level = 0
        vwap_reference = current_price
        
        if CONFIG.vwap.use_rapidapi_zones and api_long_entry_price > 0 and api_short_entry_price > 0:
            # Use API prices directly
            final_long_level = api_long_entry_price
            final_short_level = api_short_entry_price
            
            # Reference VWAP is estimated as midpoint
            vwap_reference = (api_long_entry_price + api_short_entry_price) / 2
            
            # Optional: Blend with real-time VWAP if enhancement is enabled
            if CONFIG.vwap.vwap_enhancement:
                cached_vwap = self.vwap_cache.get(symbol)
                if cached_vwap and time.time() - cached_vwap['timestamp'] < 300:
                    rt_vwap = cached_vwap['vwap']
                    
                    # Create RT VWAP entry levels using the SAME offset logic as API
                    rt_long_level = rt_vwap * (1 - CONFIG.vwap.long_offset_pct / 100)
                    rt_short_level = rt_vwap * (1 + CONFIG.vwap.short_offset_pct / 100)
                    
                    # Blend the entry levels
                    final_long_level = final_long_level * 0.7 + rt_long_level * 0.3
                    final_short_level = final_short_level * 0.7 + rt_short_level * 0.3
                    
                    # Update reference
                    vwap_reference = (vwap_reference * 0.7) + (rt_vwap * 0.3)
            
            logging.debug(f"API ENTRY LEVELS {symbol}: Long={api_long_entry_price:.6f}, Short={api_short_entry_price:.6f}")
            
        else:
            # Fallback: Calculate our own entry levels from real-time VWAP
            cached_vwap = self.vwap_cache.get(symbol)
            if cached_vwap and time.time() - cached_vwap['timestamp'] < 300:
                vwap_reference = cached_vwap['vwap']
            else:
                vwap_reference = current_price
                logging.warning(f"Using current price as VWAP fallback for {symbol}")
            
            # Apply the same offset logic as the API would
            final_long_level = vwap_reference * (1 - CONFIG.vwap.long_offset_pct / 100)
            final_short_level = vwap_reference * (1 + CONFIG.vwap.short_offset_pct / 100)
        
        # Basic validation
        if final_long_level <= 0 or final_short_level <= 0:
            logging.error(f"Invalid final levels for {symbol}")
            return 0, 0, vwap_reference
        
        # Log with corrected understanding
        long_discount_pct = ((vwap_reference - final_long_level) / vwap_reference) * 100
        short_premium_pct = ((final_short_level - vwap_reference) / vwap_reference) * 100
        
        logging.debug(f"ENTRY LEVELS {symbol}: "
                    f"Long={final_long_level:.6f} ({long_discount_pct:.1f}% discount), "
                    f"Short={final_short_level:.6f} ({short_premium_pct:.1f}% premium), "
                    f"Est.VWAP={vwap_reference:.6f}")
        
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
    
    async def calculate_momentum_metrics(self, exchange, symbol: str) -> Dict[str, float]:
        """Calculate various momentum and volatility metrics using exchange-aligned methods"""
        try:
            ccxt_symbol = to_ccxt_symbol(symbol)
            current_price = 0
            metrics = {}
            
            # Get current price first
            try:
                ticker = await exchange.fetch_ticker(ccxt_symbol)
                current_price = float(ticker['close'])
                
                # Use exchange's 24h percentage directly (most reliable)
                metrics['24h_change_pct'] = float(ticker.get('percentage', 0))
                logging.debug(f"24h change from exchange ticker: {metrics['24h_change_pct']:.2f}%")
                
            except Exception as e:
                logging.warning(f"Failed to get ticker for {symbol}: {e}")
                return {}
            
            # 1-hour momentum using hourly candles
            try:
                h1_candles = await exchange.fetch_ohlcv(ccxt_symbol, '1h', limit=2)
                if len(h1_candles) >= 2:
                    prev_h1_close = float(h1_candles[-2][4])  # Previous hour close
                    current_h1_close = float(h1_candles[-1][4])  # Current hour close
                    metrics['1h_change_pct'] = ((current_h1_close - prev_h1_close) / prev_h1_close) * 100
                    logging.debug(f"1h change: {prev_h1_close:.6f} -> {current_h1_close:.6f} = {metrics['1h_change_pct']:.2f}%")
                else:
                    # Fallback: current price vs current hour open
                    if len(h1_candles) >= 1:
                        current_h1_open = float(h1_candles[-1][1])  # Current hour open
                        metrics['1h_change_pct'] = ((current_price - current_h1_open) / current_h1_open) * 100
                    else:
                        metrics['1h_change_pct'] = 0
                        logging.warning(f"Insufficient 1h data for {symbol}")
            except Exception as e:
                logging.warning(f"1h momentum calculation failed for {symbol}: {e}")
                metrics['1h_change_pct'] = 0
            
            # 4-hour momentum using 4h candles
            try:
                h4_candles = await exchange.fetch_ohlcv(ccxt_symbol, '4h', limit=2)
                if len(h4_candles) >= 2:
                    prev_h4_close = float(h4_candles[-2][4])  # Previous 4h close
                    current_h4_close = float(h4_candles[-1][4])  # Current 4h close
                    metrics['4h_change_pct'] = ((current_h4_close - prev_h4_close) / prev_h4_close) * 100
                    logging.debug(f"4h change: {prev_h4_close:.6f} -> {current_h4_close:.6f} = {metrics['4h_change_pct']:.2f}%")
                else:
                    # Fallback: current price vs current 4h open
                    if len(h4_candles) >= 1:
                        current_h4_open = float(h4_candles[-1][1])  # Current 4h open
                        metrics['4h_change_pct'] = ((current_price - current_h4_open) / current_h4_open) * 100
                    else:
                        metrics['4h_change_pct'] = 0
                        logging.warning(f"Insufficient 4h data for {symbol}")
            except Exception as e:
                logging.warning(f"4h momentum calculation failed for {symbol}: {e}")
                metrics['4h_change_pct'] = 0
            
            # Daily volatility range using daily candles
            try:
                daily_candles = await exchange.fetch_ohlcv(ccxt_symbol, '1d', limit=2)
                if len(daily_candles) >= 1:
                    latest_daily = daily_candles[-1]
                    daily_high = float(latest_daily[2])
                    daily_low = float(latest_daily[3])
                    
                    if daily_low > 0:
                        metrics['daily_range_pct'] = ((daily_high - daily_low) / daily_low) * 100
                    else:
                        metrics['daily_range_pct'] = 0
                else:
                    metrics['daily_range_pct'] = 10  # Default fallback
            except Exception as e:
                logging.warning(f"Daily range calculation failed for {symbol}: {e}")
                metrics['daily_range_pct'] = 10
            
            # Volatility calculations using minute data (for fine-grained analysis)
            try:
                # Try CCXT Pro data first for volatility
                klines = []
                if self.data_manager:
                    klines = self.data_manager.get_kline_data(symbol, limit=300)
                
                # Fallback to REST API if insufficient CCXT Pro data
                if len(klines) < 100:
                    try:
                        rest_klines = await exchange.fetch_ohlcv(ccxt_symbol, '1m', limit=300)
                        klines = rest_klines
                    except Exception as rest_error:
                        logging.debug(f"REST API volatility fetch failed for {symbol}: {rest_error}")
                
                if len(klines) >= 60:
                    # Convert to price changes
                    price_changes = []
                    for i in range(1, len(klines)):
                        if len(klines[i]) >= 5 and len(klines[i-1]) >= 5:
                            prev_price = float(klines[i-1][4])
                            curr_price = float(klines[i][4])
                            if prev_price > 0:
                                change = (curr_price - prev_price) / prev_price
                                price_changes.append(change)
                    
                    if price_changes:
                        # 1h volatility (last 60 changes)
                        h1_changes = price_changes[-min(60, len(price_changes)):]
                        metrics['volatility_1h'] = float(np.std(h1_changes) * 100) if h1_changes else 0
                        
                        # 4h volatility (last 240 changes)
                        h4_changes = price_changes[-min(240, len(price_changes)):]
                        metrics['volatility_4h'] = float(np.std(h4_changes) * 100) if h4_changes else 0
                    else:
                        metrics['volatility_1h'] = 0
                        metrics['volatility_4h'] = 0
                else:
                    metrics['volatility_1h'] = 0
                    metrics['volatility_4h'] = 0
                    
            except Exception as e:
                logging.debug(f"Volatility calculation failed for {symbol}: {e}")
                metrics['volatility_1h'] = 0
                metrics['volatility_4h'] = 0
            
            # Volume spike detection using ticker data
            try:
                volume_24h = float(ticker.get('quoteVolume', 0))
                # Get historical daily volumes for comparison
                daily_candles = await exchange.fetch_ohlcv(ccxt_symbol, '1d', limit=7)
                if len(daily_candles) >= 2:
                    # Compare current 24h volume to average of last 7 days
                    volumes = [float(candle[5]) for candle in daily_candles[:-1]]  # Exclude current day
                    avg_volume = sum(volumes) / len(volumes) if volumes else volume_24h
                    metrics['volume_spike_ratio'] = volume_24h / avg_volume if avg_volume > 0 else 1
                else:
                    metrics['volume_spike_ratio'] = 1
            except Exception as e:
                logging.debug(f"Volume spike calculation failed for {symbol}: {e}")
                metrics['volume_spike_ratio'] = 1
            
            # Cache the results
            self.momentum_cache[symbol] = {
                'metrics': metrics,
                'timestamp': time.time(),
                'price': current_price,
                'source': 'exchange_aligned'
            }
            
            # Enhanced debug logging
            logging.info(f"MOMENTUM {symbol}: 24h={metrics.get('24h_change_pct', 0):.1f}%, "
                        f"4h={metrics.get('4h_change_pct', 0):.1f}%, "
                        f"1h={metrics.get('1h_change_pct', 0):.1f}% (exchange-aligned)")
            
            return metrics
            
        except Exception as e:
            logging.error(f"Momentum calculation failed for {symbol}: {e}")
            return {}
    
    async def get_momentum_signal(self, exchange, symbol: str) -> Tuple[str, str, Dict]:
        """Get momentum signal for a symbol"""
        try:
            # First ensure we have data by subscribing
            self.data_manager.subscribe_symbol(symbol)
            
            # Get or calculate metrics
            cached = self.momentum_cache.get(symbol)
            if cached and time.time() - cached['timestamp'] < 300:
                metrics = cached['metrics']
            else:
                metrics = await self.calculate_momentum_metrics(exchange, symbol)
            
            if not metrics:
                return "AVOID", "No momentum data", {}
            
            # Extract key metrics with defaults
            change_1h = metrics.get('1h_change_pct', 0)
            change_4h = metrics.get('4h_change_pct', 0)
            change_24h = metrics.get('24h_change_pct', 0)
            daily_range = metrics.get('daily_range_pct', 10)
            volume_spike = metrics.get('volume_spike_ratio', 1)

            # Avoid recent pumps (positive moves beyond threshold)
            if change_1h > CONFIG.momentum.hourly_pump_threshold:
                return "AVOID", f"Recent pump: {change_1h:+.1f}% (1h)", metrics

            # Avoid recent dumps (negative moves beyond threshold)
            if change_1h < CONFIG.momentum.hourly_dump_threshold:
                return "AVOID", f"Recent dump: {change_1h:+.1f}% (1h)", metrics
            
            # Avoid daily pumps
            if change_24h > CONFIG.momentum.daily_pump_threshold:
                return "AVOID", f"Daily pump: {change_24h:+.1f}% (24h)", metrics

            # Avoid daily dumps
            if change_24h < CONFIG.momentum.daily_dump_threshold:
                return "AVOID", f"Daily dump: {change_24h:+.1f}% (24h)", metrics
            
            # Require minimum volatility (ensures movement potential)
            if daily_range < CONFIG.momentum.min_daily_volatility:
                return "AVOID", f"Low volatility: {daily_range:.1f}%", metrics
            
            # Avoid excessive volatility (reduces unpredictable moves)
            if daily_range > CONFIG.momentum.max_daily_volatility:
                return "AVOID", f"Excessive volatility: {daily_range:.1f}%", metrics
            
            return "ALLOW", f"Normal conditions: {change_24h:+.1f}% (24h)", metrics
            
        except Exception as e:
            logging.error(f"Momentum signal error for {symbol}: {e}")
            return "AVOID", "Error in momentum calculation", {}

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
    """Position manager with reliable tracking"""
    
    def __init__(self):
        self.positions: Dict[str, Position] = {}
        self.total_trades = 0
        self._position_locks: Dict[str, asyncio.Lock] = {}
        self.recent_attempts: Dict[str, float] = {}
        self.attempt_cooldown = 10
        self.current_notional = CONFIG.min_notional
        
        # Simplified monitoring
        self.order_monitoring_task: Optional[asyncio.Task] = None
        self.is_monitoring_orders = False
    
    def calculate_position_notional(self, balance: float) -> float:
        """Calculate position size with compounding"""
        if not CONFIG.compounding.enable_compounding:
            return CONFIG.min_notional
        
        # Calculate compounded size
        compounded_size = balance * CONFIG.compounding.compounding_percentage
        
        # Ensure minimum and maximum bounds
        position_size = max(
            CONFIG.compounding.base_min_notional,   # Never below base minimum
            compounded_size                         # Cap maximum
        )
        
        logging.debug(f"Position sizing: Balance=${balance:.2f}, Compounded=${compounded_size:.2f}, Final=${position_size:.2f}")
        return position_size

    def get_position_lock(self, symbol: str) -> asyncio.Lock:
        """Get lock for symbol"""
        normalized = normalize_symbol(symbol)
        if normalized not in self._position_locks:
            self._position_locks[normalized] = asyncio.Lock()
        return self._position_locks[normalized]
    
    async def get_exchange_positions(self, exchange) -> set:
        """Get actual positions from exchange - simplified"""
        try:
            # Use REST API for reliability
            positions = await exchange.fetch_positions()
            active_symbols = {
                normalize_symbol(p['symbol']) 
                for p in positions 
                if float(p.get('contracts', 0)) != 0
            }
            
            logging.debug(f"Exchange positions: {active_symbols}")
            return active_symbols
            
        except Exception as e:
            logging.error(f"Failed to get exchange positions: {e}")
            return set()
    
    async def start_order_monitoring(self, exchange):
        """Start simplified monitoring"""
        if not self.is_monitoring_orders:
            self.is_monitoring_orders = True
            self.order_monitoring_task = asyncio.create_task(self._monitor_positions(exchange))
            logging.info("Position monitoring started")
    
    async def stop_order_monitoring(self):
        """Stop monitoring"""
        self.is_monitoring_orders = False
        if self.order_monitoring_task:
            self.order_monitoring_task.cancel()
            try:
                await asyncio.wait_for(self.order_monitoring_task, timeout=2)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        logging.info("Position monitoring stopped")
    
    async def _monitor_positions(self, exchange):
        """Simplified position monitoring"""
        while self.is_monitoring_orders:
            try:
                # Get actual positions every 10 seconds
                exchange_positions = await self.get_exchange_positions(exchange)
                tracked_positions = set(self.positions.keys())
                
                # Find positions that were closed
                closed_positions = tracked_positions - exchange_positions
                
                for symbol in closed_positions:
                    logging.info(f"Position closed detected: {symbol}")
                    await self._handle_position_closure(exchange, symbol)
                
                await asyncio.sleep(10)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Position monitoring error: {e}")
                await asyncio.sleep(30)
    
    async def _handle_position_closure(self, exchange, symbol: str):
        """Handle position closure with accurate P&L from exchange data"""
        try:
            if symbol not in self.positions:
                return
            
            position = self.positions[symbol]
            ccxt_symbol = to_ccxt_symbol(symbol)
            
            # Get recent position history (last 10 positions)
            try:
                # Binance-specific call for position history
                position_history = await exchange.fetch_positions_history(
                    symbols=[ccxt_symbol], 
                    limit=10
                )
                
                # Find the most recent closed position for our symbol
                target_position = None
                for hist_pos in position_history:
                    if (normalize_symbol(hist_pos['symbol']) == symbol and 
                        hist_pos['side'] == ('long' if position.side == 'buy' else 'short')):
                        target_position = hist_pos
                        break
                
                if target_position:
                    # Extract accurate data from exchange
                    actual_pnl_usd = float(target_position.get('realizedPnl', 0))
                    entry_price = float(target_position.get('entryPrice', position.avg_entry_price))
                    exit_price = float(target_position.get('markPrice', 0))  # Last known price
                    quantity = float(target_position.get('contracts', position.total_qty))
                    
                    # Calculate percentage P&L
                    if entry_price > 0:
                        if position.side == "buy":
                            pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                        else:
                            pnl_pct = ((entry_price - exit_price) / entry_price) * 100
                    else:
                        pnl_pct = (actual_pnl_usd / position.total_notional_used) * 100
                    
                    # Determine exit reason from recent orders
                    exit_reason = await self._determine_exit_reason(exchange, symbol, ccxt_symbol)
                    
                    # Prepare additional info for Discord
                    additional_info = {
                        'entry_price': entry_price,
                        'quantity': quantity,
                        'fees_paid': abs(float(target_position.get('fee', 0)))  # Trading fees
                    }
                    
                    # Send accurate Discord notification
                    if discord_notifier:
                        await discord_notifier.send_profit_alert(
                            symbol, pnl_pct, actual_pnl_usd, exit_reason, exit_price, additional_info
                        )
                    
                    logging.info(f"Position closed: {symbol} | P&L: {pnl_pct:+.2f}% (${actual_pnl_usd:+.2f}) | {exit_reason}")
                    
                else:
                    # Fallback to old method if history not available
                    logging.warning(f"Could not find position history for {symbol}, using fallback P&L")
                    await self._fallback_pnl_calculation(exchange, symbol, position)
                    
            except Exception as history_error:
                logging.debug(f"Position history fetch failed for {symbol}: {history_error}")
                # Fallback to current method
                await self._fallback_pnl_calculation(exchange, symbol, position)
            
            # Remove position from tracking
            del self.positions[symbol]
            
        except Exception as e:
            logging.error(f"Failed to handle position closure for {symbol}: {e}")
    
    async def _determine_exit_reason(self, exchange, symbol: str, ccxt_symbol: str) -> str:
        """Determine how the position was closed by checking recent orders"""
        try:
            # Get recent orders for this symbol (last 5)
            recent_orders = await exchange.fetch_orders(ccxt_symbol, limit=5)
            
            # Look for recently filled exit orders
            for order in recent_orders:
                if (order['status'] == 'closed' and 
                    time.time() - (order['timestamp'] / 1000) < 300):  # Within last 5 minutes
                    
                    client_id = order.get('clientOrderId', '')
                    
                    if client_id.startswith('TP-') or client_id.startswith('RTP-'):
                        return "TAKE PROFIT"
                    elif client_id.startswith('SL-'):
                        return "STOP LOSS"
                    elif order.get('reduceOnly'):
                        return "MANUAL CLOSE"
            
            return "POSITION CLOSED"
            
        except Exception as e:
            logging.debug(f"Could not determine exit reason for {symbol}: {e}")
            return "POSITION CLOSED"

    async def _fallback_pnl_calculation(self, exchange, symbol: str, position: Position):
        """Fallback to original P&L calculation method"""
        try:
            # Get current price for P&L calculation
            try:
                ccxt_symbol = to_ccxt_symbol(symbol)
                ticker = await exchange.fetch_ticker(ccxt_symbol)
                exit_price = float(ticker['close'])
            except:
                exit_price = position.avg_entry_price
            
            # Calculate P&L using original method
            if position.side == "buy":
                pnl_pct = (exit_price - position.avg_entry_price) / position.avg_entry_price
            else:
                pnl_pct = (position.avg_entry_price - exit_price) / position.avg_entry_price
            
            pnl_usd = position.total_notional_used * pnl_pct
            
            # Send Discord notification (fallback method)
            if discord_notifier:
                await discord_notifier.send_profit_alert(
                    symbol, pnl_pct * 100, pnl_usd, "POSITION CLOSED", exit_price
                )
            
            logging.info(f"Position closed (fallback): {symbol} | P&L: {pnl_pct*100:+.2f}% (${pnl_usd:+.2f})")
            
        except Exception as e:
            logging.error(f"Fallback P&L calculation failed for {symbol}: {e}")

    async def create_position(self, exchange, symbol: str, side: str, liq_price: float,
                             liq_size: float, vwap_ref: float, regime: str, balance: float) -> Optional[Position]:
        """Simplified position creation"""
        
        normalized_symbol = normalize_symbol(symbol)
        position_lock = self.get_position_lock(normalized_symbol)
        
        async with position_lock:
            current_time = time.time()
            
            # Rate limiting
            last_attempt = self.recent_attempts.get(normalized_symbol, 0)
            if current_time - last_attempt < self.attempt_cooldown:
                logging.info(f"Cooldown active for {normalized_symbol}")
                return None
            
            # Get current positions from exchange
            exchange_positions = await self.get_exchange_positions(exchange)
            
            # Check max positions limit
            if len(exchange_positions) >= CONFIG.risk.max_positions:
                logging.warning(f"MAX POSITIONS REACHED: {len(exchange_positions)}/{CONFIG.risk.max_positions}")
                stats.log_filter_rejection("max_positions")
                return None
            
            # Check if position already exists
            if normalized_symbol in exchange_positions:
                logging.info(f"Position already exists: {normalized_symbol}")
                stats.log_filter_rejection("existing_position")
                return None
            
            # Isolation check
            total_notional_used = sum(p.total_notional_used for p in self.positions.values())
            isolation_limit = balance * CONFIG.risk.isolation_pct * CONFIG.leverage
            if total_notional_used + CONFIG.min_notional > isolation_limit:
                logging.info(f"Isolation limit reached for {normalized_symbol}")
                stats.log_filter_rejection('isolation_limit')
                return None
            
            # Get pair config
            pair_config = pairs_builder.get_pair_config(normalized_symbol)
            if not pair_config:
                logging.error(f"No pair config for {normalized_symbol}")
                return None
            
            # Calculate position size
            entry_notional = self.calculate_position_notional(balance)
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
            self.recent_attempts[normalized_symbol] = current_time
            
            logging.info(f"Position created: {normalized_symbol} {side} {entry_qty:.6f} @ {liq_price:.6f}")
            return position
    
    async def check_dca_trigger(self, symbol: str, current_price: float) -> Tuple[bool, int]:
        """Check if DCA should be triggered"""
        normalized_symbol = normalize_symbol(symbol)
        
        if normalized_symbol not in self.positions:
            return False, -1
        
        position = self.positions[normalized_symbol]
        
        if position.dca_count >= CONFIG.dca.max_levels:
            return False, -1
        
        # Calculate adverse move
        if position.side == "buy":
            adverse_move = (position.avg_entry_price - current_price) / position.avg_entry_price
        else:
            adverse_move = (current_price - position.avg_entry_price) / position.avg_entry_price
        
        # DEBUG: Log the calculation
        if position.dca_count < len(CONFIG.dca.trigger_pcts):
            trigger_threshold = CONFIG.dca.trigger_pcts[position.dca_count]
            logging.info(f"DCA CHECK {symbol}: Price={current_price:.6f}, Entry={position.avg_entry_price:.6f}, "
                        f"Adverse={adverse_move*100:.2f}%, Threshold={trigger_threshold*100:.2f}%, "
                        f"DCA_Level={position.dca_count}")

        # Check trigger
        if position.dca_count < len(CONFIG.dca.trigger_pcts):
            trigger_threshold = CONFIG.dca.trigger_pcts[position.dca_count]
            if adverse_move >= trigger_threshold:
                return True, position.dca_count + 1
        
        return False, -1
    
    async def execute_dca(self, exchange, symbol: str, current_price: float, dca_level: int) -> bool:
        """Execute DCA"""
        normalized_symbol = normalize_symbol(symbol)
        position_lock = self.get_position_lock(normalized_symbol)
        
        async with position_lock:
            if normalized_symbol not in self.positions:
                return False
            
            position = self.positions[normalized_symbol]
            pair_config = pairs_builder.get_pair_config(normalized_symbol)
            
            if not pair_config:
                return False
            
            # Calculate DCA size
            size_multiplier = 1.0
            if dca_level - 1 < len(CONFIG.dca.size_multipliers):
                size_multiplier = CONFIG.dca.size_multipliers[dca_level - 1]
            
            balance = await get_account_balance(exchange)
            dca_notional = self.calculate_position_notional(balance) * size_multiplier  
            dca_qty = dca_notional / current_price
            
            # Apply rounding
            step_size = pair_config.get('step_size', 0.001)
            dca_qty = round(dca_qty / step_size) * step_size
            actual_notional = dca_qty * current_price
            
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
                
                logging.info(f"DCA L{dca_level}: {normalized_symbol} @ {actual_price:.6f}")
                
                # Send Discord notification
                if discord_notifier:
                    await discord_notifier.send_trade_alert(
                        normalized_symbol, position.side, dca_qty, actual_price,
                        f"DCA Level {dca_level}", actual_notional,
                        is_dca=True, dca_level=dca_level
                    )
                
                stats.log_dca_attempt(True)
                return True
                
            except Exception as e:
                logging.error(f"DCA execution failed for {normalized_symbol}: {e}")
                stats.log_dca_attempt(False)
                return False

    async def recover_existing_positions(self, exchange) -> int:
        """Recover existing positions from exchange on bot startup - TRACKING ONLY"""
        try:
            logging.info("Recovering existing positions from exchange...")
            
            # Get all active positions from exchange
            positions = await exchange.fetch_positions()
            recovered_count = 0
            
            for pos in positions:
                contracts = float(pos.get('contracts', 0))
                if contracts == 0:  # Skip empty positions
                    continue
                    
                symbol = normalize_symbol(pos['symbol'])
                side = 'buy' if pos['side'] == 'long' else 'sell'
                entry_price = float(pos.get('entryPrice', 0))
                
                if entry_price <= 0:
                    continue
                    
                # Create a position object with recovered data
                recovered_position = Position(
                    symbol=symbol,
                    side=side,
                    total_qty=abs(contracts),
                    avg_entry_price=entry_price,
                    total_notional_used=abs(contracts) * entry_price,
                    dca_count=0,  # Conservative: assume no DCA levels used
                    entry_time=time.time() - 3600,  # Assume 1 hour ago
                    vwap_reference=entry_price,  # Use entry price as reference
                    regime="UNKNOWN"
                )
                
                # Add to our tracking
                self.positions[symbol] = recovered_position
                recovered_count += 1
                
                logging.info(f"Recovered position: {symbol} {side} {abs(contracts):.6f} @ {entry_price:.6f}")
            
            if recovered_count > 0:
                logging.info(f"Successfully recovered {recovered_count} existing positions")
            else:
                logging.info("No existing positions found to recover")
                
            return recovered_count
            
        except Exception as e:
            logging.error(f"Position recovery failed: {e}")
            return 0
    
    def get_position_count(self) -> int:
        return len(self.positions)
    
    def has_position(self, symbol: str) -> bool:
        return normalize_symbol(symbol) in self.positions
    
    def remove_position(self, symbol: str):
        normalized = normalize_symbol(symbol)
        if normalized in self.positions:
            del self.positions[normalized]

position_manager = PositionManager()

# =============================================================================
# PROFIT PROTECTION SYSTEM
# =============================================================================

class ProfitProtectionSystem:
    """Profit protection with separate TP/SL orders and monitoring"""
    
    def __init__(self):
        self.order_registry: Dict[str, Dict] = {}
        self.monitoring_task: Optional[asyncio.Task] = None
        self.is_monitoring = False
    
    async def set_initial_take_profit(self, exchange, position: Position) -> bool:
        """Set separate TP and SL orders with monitoring"""
        try:
            normalized_symbol = normalize_symbol(position.symbol)
            
            # Calculate prices
            if position.side == "buy":
                tp_price = position.avg_entry_price * (1 + CONFIG.profit_protection.initial_tp_pct)
                sl_price = position.avg_entry_price * (1 - CONFIG.profit_protection.stop_loss_pct) if CONFIG.profit_protection.enable_stop_loss else None
            else:
                tp_price = position.avg_entry_price * (1 - CONFIG.profit_protection.initial_tp_pct)
                sl_price = position.avg_entry_price * (1 + CONFIG.profit_protection.stop_loss_pct) if CONFIG.profit_protection.enable_stop_loss else None
            
            ccxt_symbol = to_ccxt_symbol(normalized_symbol)
            exit_side = "sell" if position.side == "buy" else "buy"
            
            # Create shorter unique client IDs (max 35 chars to be safe)
            timestamp = int(time.time() * 1000)
            short_symbol = normalized_symbol[:10]  # Truncate symbol to 10 chars max
            
            # Format: TP-SYMBOL-TIMESTAMP (keeping under 36 chars)
            tp_client_id = f"TP-{short_symbol}-{timestamp}"[:35]
            sl_client_id = f"SL-{short_symbol}-{timestamp}"[:35] if sl_price else None
            
            # 1. Create Take Profit Order
            tp_order = await exchange.create_limit_order(
                ccxt_symbol, exit_side, position.total_qty, tp_price,
                params={
                    "positionSide": "LONG" if position.side == "buy" else "SHORT",
                    "newClientOrderId": tp_client_id,
                    "timeInForce": "GTC"
                }
            )
            
            position.tp_price = tp_price
            position.tp_order_id = str(tp_order.get('id', ''))
            position.tp_client_id = tp_client_id
            
            logging.info(f"TP set: {normalized_symbol} @ {tp_price:.6f}")
            
            # 2. Create Stop Loss Order (if enabled)
            if CONFIG.profit_protection.enable_stop_loss and sl_price:
                try:
                    sl_order = await exchange.create_order(
                        ccxt_symbol,
                        'STOP_MARKET',
                        exit_side,
                        position.total_qty,
                        None,  # No limit price for market stop
                        params={
                            "stopPrice": sl_price,
                            "positionSide": "LONG" if position.side == "buy" else "SHORT",
                            "newClientOrderId": sl_client_id,
                            "timeInForce": "GTC"
                        }
                    )
                    
                    position.sl_price = sl_price
                    position.sl_order_id = str(sl_order.get('id', ''))
                    position.sl_client_id = sl_client_id
                    
                    logging.info(f"SL set: {normalized_symbol} @ {sl_price:.6f}")
                    
                except Exception as sl_error:
                    logging.warning(f"Stop loss setup failed for {normalized_symbol}: {sl_error}")
            
            # Store order information for monitoring
            self.order_registry[normalized_symbol] = {
                'tp_order_id': position.tp_order_id,
                'sl_order_id': getattr(position, 'sl_order_id', None),
                'tp_client_id': tp_client_id,
                'sl_client_id': sl_client_id,
                'position_side': position.side,
                'symbol': ccxt_symbol
            }
            
            # Start monitoring if not already running
            if not self.is_monitoring:
                await self.start_order_monitoring(exchange)
            
            return True
            
        except Exception as e:
            logging.error(f"Order setup failed for {position.symbol}: {e}")
            return False
    
    async def start_order_monitoring(self, exchange):
        """Start monitoring for filled orders"""
        if self.is_monitoring:
            return
            
        self.is_monitoring = True
        self.monitoring_task = asyncio.create_task(self._monitor_order_fills(exchange))
        logging.info("Order monitoring started")
    
    async def stop_order_monitoring(self):
        """Stop order monitoring"""
        self.is_monitoring = False
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await asyncio.wait_for(self.monitoring_task, timeout=2)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        logging.info("Order monitoring stopped")
    
    async def _monitor_order_fills(self, exchange):
        """Monitor for filled TP/SL orders and cancel counterparts"""
        while self.is_monitoring:
            try:
                # Check each symbol's orders
                for symbol, order_info in list(self.order_registry.items()):
                    try:
                        ccxt_symbol = order_info['symbol']
                        tp_order_id = order_info.get('tp_order_id')
                        sl_order_id = order_info.get('sl_order_id')
                        
                        # Check TP order status
                        if tp_order_id:
                            try:
                                tp_status = await exchange.fetch_order(tp_order_id, ccxt_symbol)
                                if tp_status['status'] in ['closed', 'filled']:
                                    logging.info(f"TP filled for {symbol}, cancelling SL")
                                    
                                    # Cancel SL order
                                    if sl_order_id:
                                        try:
                                            await exchange.cancel_order(sl_order_id, ccxt_symbol)
                                            logging.info(f"SL cancelled for {symbol}")
                                        except Exception as cancel_error:
                                            logging.debug(f"SL cancel failed (might be filled): {cancel_error}")
                                    
                                    # Remove from monitoring
                                    del self.order_registry[symbol]
                                    continue
                                    
                            except Exception as tp_error:
                                # Order might not exist anymore
                                logging.debug(f"TP check failed for {symbol}: {tp_error}")
                        
                        # Check SL order status
                        if sl_order_id:
                            try:
                                sl_status = await exchange.fetch_order(sl_order_id, ccxt_symbol)
                                if sl_status['status'] in ['closed', 'filled']:
                                    logging.info(f"SL filled for {symbol}, cancelling TP")
                                    
                                    # Cancel TP order
                                    if tp_order_id:
                                        try:
                                            await exchange.cancel_order(tp_order_id, ccxt_symbol)
                                            logging.info(f"TP cancelled for {symbol}")
                                        except Exception as cancel_error:
                                            logging.debug(f"TP cancel failed (might be filled): {cancel_error}")
                                    
                                    # Remove from monitoring
                                    del self.order_registry[symbol]
                                    continue
                                    
                            except Exception as sl_error:
                                # Order might not exist anymore
                                logging.debug(f"SL check failed for {symbol}: {sl_error}")
                        
                    except Exception as symbol_error:
                        logging.debug(f"Order monitoring error for {symbol}: {symbol_error}")
                        continue
                
                await asyncio.sleep(3)  # Check every 3 seconds
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Order monitoring loop error: {e}")
                await asyncio.sleep(10)
    
    async def update_orders_after_dca(self, exchange, position: Position) -> bool:
        """Update orders after DCA"""
        try:
            normalized_symbol = normalize_symbol(position.symbol)
            ccxt_symbol = to_ccxt_symbol(normalized_symbol)
            
            # Cancel existing orders
            if hasattr(position, 'tp_order_id') and position.tp_order_id:
                try:
                    await exchange.cancel_order(position.tp_order_id, ccxt_symbol)
                except:
                    pass
            
            if hasattr(position, 'sl_order_id') and position.sl_order_id:
                try:
                    await exchange.cancel_order(position.sl_order_id, ccxt_symbol)
                except:
                    pass
            
            # Remove from monitoring registry
            if normalized_symbol in self.order_registry:
                del self.order_registry[normalized_symbol]
            
            # Create new orders with updated position
            return await self.set_initial_take_profit(exchange, position)
            
        except Exception as e:
            logging.error(f"Failed to update orders after DCA for {position.symbol}: {e}")
            return False

profit_protection = ProfitProtectionSystem()

# =============================================================================
# MAIN STRATEGY
# =============================================================================

class VWAPHunterStrategy:
    """Simplified VWAP Hunter strategy"""
    
    def __init__(self, exchange):
        self.exchange = exchange
        self.price_cache: Dict[str, float] = {}
        self.last_price_update: Dict[str, float] = {}
        self.entry_timestamps: Dict[str, float] = {}
        
        # Only create momentum detector if data manager exists
        global data_manager
        self.momentum_detector = MomentumDetector(data_manager) if data_manager else None
    
    async def get_current_price(self, symbol: str) -> float:
        """Optimized price fetching"""
        normalized_symbol = normalize_symbol(symbol)
        
        # Check cache first (5 second validity)
        current_time = time.time()
        if (normalized_symbol in self.price_cache and 
            current_time - self.last_price_update.get(normalized_symbol, 0) < 5):
            return self.price_cache[normalized_symbol]
        
        # Try CCXT Pro
        global data_manager
        if data_manager:
            price = data_manager.get_price(normalized_symbol)
            if price > 0:
                self.price_cache[normalized_symbol] = price
                self.last_price_update[normalized_symbol] = current_time
                return price
        
        # REST API fallback
        try:
            ccxt_symbol = to_ccxt_symbol(normalized_symbol)
            ticker = await self.exchange.fetch_ticker(ccxt_symbol)
            price = float(ticker['close'])
            self.price_cache[normalized_symbol] = price
            self.last_price_update[normalized_symbol] = current_time
            return price
        except:
            return self.price_cache.get(normalized_symbol, 0)
    
    async def check_volume_filter(self, symbol: str) -> Tuple[bool, float]:
        """Check volume - simplified"""
        normalized_symbol = normalize_symbol(symbol)
        
        try:
            ccxt_symbol = to_ccxt_symbol(normalized_symbol)
            ticker = await self.exchange.fetch_ticker(ccxt_symbol)
            daily_volume = float(ticker.get("quoteVolume", 0))
            passed = daily_volume >= CONFIG.risk.min_24h_volume
            return passed, daily_volume
        except Exception as e:
            logging.warning(f"Volume check failed for {normalized_symbol}: {e}")
            return False, 0
    
    async def process_liquidation_event(self, liquidation_data: Dict):
        """Process liquidation event - simplified"""
        start_time = time.time()
        
        try:
            # Parse liquidation data
            data = liquidation_data.get('o', {})
            symbol = data.get('s', '')
            if not symbol or not symbol.endswith('USDT'):
                return
            
            normalized_symbol = normalize_symbol(symbol)
            liq_price = float(data.get('ap', 0))
            liq_qty = float(data.get('q', 0))
            liq_side = data.get('S', '')
            
            if liq_price <= 0 or liq_qty <= 0 or not liq_side:
                return
            
            liq_size_usd = liq_price * liq_qty
            our_side = "buy" if liq_side == "SELL" else "sell"
            
            # Log liquidation
            if CONFIG.debug.log_all_liquidations:
                logging.info(f"LIQUIDATION: {normalized_symbol} | {liq_side} | ${liq_size_usd:,.0f}")
            
            stats.log_liquidation(normalized_symbol, liq_size_usd)
            
            # Entry cooldown
            last = self.entry_timestamps.get(normalized_symbol, 0)
            if time.time() - last < 30:
                return
            
            # Filter checks
            if not pairs_builder.is_pair_enabled(normalized_symbol):
                stats.log_filter_rejection('pair_not_enabled')
                return

            global pair_filter_manager
            if pair_filter_manager:
                filter_pass, filter_reason = pair_filter_manager.should_trade_pair(normalized_symbol)
                if not filter_pass:
                    if "whitelist" in filter_reason.lower():
                        stats.log_filter_rejection('whitelist_filter')
                    elif "blacklist" in filter_reason.lower():
                        stats.log_filter_rejection('blacklist_filter')
                    if CONFIG.debug.enable_filter_debug:
                        logging.info(f"PAIR FILTER REJECT: {normalized_symbol} - {filter_reason}")
                    return
            
            # Age filter check
            age_pass, age_reason = await pair_age_filter.should_trade_pair(self.exchange, normalized_symbol)
            if not age_pass:
                stats.log_filter_rejection('pair_age_filter')
                if CONFIG.debug.enable_filter_debug:
                    logging.info(f"AGE FILTER REJECT: {normalized_symbol} - {age_reason}")
                return
            
            volume_pass, daily_volume = await self.check_volume_filter(normalized_symbol)
            if not volume_pass:
                stats.log_filter_rejection('volume_filter')
                return
            
            # Momentum filter
            if CONFIG.momentum.enable_momentum_filter and self.momentum_detector:
                signal, reason, metrics = await self.momentum_detector.get_momentum_signal(self.exchange, normalized_symbol)

                if signal == "AVOID":
                    stats.log_filter_rejection('momentum_filter')
                    if CONFIG.debug.enable_filter_debug:
                        logging.info(f"MOMENTUM FILTER REJECT: {normalized_symbol} - {reason}")
                    return
            
            # Zones check
            if not rapidapi_client.has_zones(normalized_symbol):
                stats.log_filter_rejection('no_zones')
                return
            
            # VWAP levels
            long_level, short_level, vwap_ref = vwap_calculator.get_vwap_levels(normalized_symbol, liq_price)
            
            if long_level <= 0 or short_level <= 0:
                stats.log_filter_rejection('invalid_vwap')
                return
            
            # Entry signal
            entry_valid = False
            entry_reason = ""
            
            if our_side == "buy" and liq_price <= long_level:
                entry_valid = True
                entry_reason = f"LONG: {liq_price:.6f} <= {long_level:.6f}"
            elif our_side == "sell" and liq_price >= short_level:
                entry_valid = True
                entry_reason = f"SHORT: {liq_price:.6f} >= {short_level:.6f}"
            
            if not entry_valid:
                stats.log_filter_rejection('no_entry_signal')
                return
            
            # Market regime
            regime = await regime_detector.detect_regime(self.exchange, normalized_symbol)
            if not regime_detector.should_trade_in_regime(regime):
                stats.log_filter_rejection('regime_filter')
                return
            
            # Create position
            balance = await get_account_balance(self.exchange)
            position = await position_manager.create_position(
                self.exchange, normalized_symbol, our_side, liq_price, 
                liq_size_usd, vwap_ref, regime, balance
            )
            
            if position is None:
                stats.log_filter_rejection('position_create_failed')
                return
            
            # Execute trade
            success = await self._execute_trade(position)
            stats.log_trade_attempt(success)
            
            if success:
                self.entry_timestamps[normalized_symbol] = time.time()
                
                if discord_notifier:
                    await discord_notifier.send_trade_alert(
                        normalized_symbol, our_side, position.total_qty, liq_price,
                        entry_reason, position.total_notional_used
                    )
                
                logging.info(f"TRADE SUCCESS: {normalized_symbol} | {entry_reason}")
            else:
                position_manager.remove_position(normalized_symbol)
                
        except Exception as e:
            logging.error(f"Processing error for {symbol}: {e}")
    
    async def _execute_trade(self, position: Position) -> bool:
        """Execute trade"""
        try:
            normalized_symbol = normalize_symbol(position.symbol)
            ccxt_symbol = to_ccxt_symbol(normalized_symbol)
            
            # Set leverage
            try:
                await self.exchange.set_margin_mode(CONFIG.margin_mode, ccxt_symbol)
                await self.exchange.set_leverage(CONFIG.leverage, ccxt_symbol)
            except Exception as e:
                if "not modified" not in str(e).lower():
                    logging.warning(f"Margin Mode or Leverage setup failed: {e}")
            
            # Execute order
            order = await self.exchange.create_market_order(
                ccxt_symbol, position.side, position.total_qty,
                params={"positionSide": "LONG" if position.side == "buy" else "SHORT"}
            )
            
            # Update position with actual price
            actual_price = float(order.get('price', position.avg_entry_price)) or position.avg_entry_price
            position.avg_entry_price = actual_price
            position.total_notional_used = position.total_qty * actual_price
            
            # Set take profit
            await profit_protection.set_initial_take_profit(self.exchange, position)
            
            return True
            
        except Exception as e:
            logging.error(f"Trade execution failed: {e}")
            return False
    
    async def monitor_positions(self):
        """Monitor positions for DCA"""
        while True:
            try:
                for symbol, position in list(position_manager.positions.items()):
                    try:
                        current_price = await self.get_current_price(symbol)
                        if current_price <= 0:
                            continue
                        
                        # Check DCA
                        should_dca, dca_level = await position_manager.check_dca_trigger(symbol, current_price)
                        if should_dca and CONFIG.dca.enable:
                            logging.info(f"DCA TRIGGER: {symbol} at level {dca_level}")
                            success = await position_manager.execute_dca(
                                self.exchange, symbol, current_price, dca_level
                            )
                            
                            if success:
                                await profit_protection.update_orders_after_dca(self.exchange, position)
                                
                    except Exception as e:
                        logging.error(f"Position monitoring error for {symbol}: {e}")
                
                await asyncio.sleep(10)
                
            except asyncio.CancelledError:
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
                await discord_notifier.send_stats_update(summary)
                    
        except Exception as e:
            logging.error(f"Stats reporting error: {e}")
            await asyncio.sleep(60)

async def run_liquidation_stream_with_shutdown(strategy):
    """Liquidation stream"""
    
    # Primary endpoints in order of preference
    endpoints = [
        "wss://fstream.binance.com/stream?streams=!forceOrder@arr",
        "wss://stream.binance.com:9443/stream?streams=!forceOrder@arr",
        "wss://dstream.binance.com/stream?streams=!forceOrder@arr"
    ]
    
    current_endpoint = 0
    reconnect_delay = 5
    max_delay = 60
    
    while not shutdown_handler.shutdown_event.is_set():
        uri = endpoints[current_endpoint]
        
        try:
            logging.info(f"Connecting to liquidation stream (endpoint {current_endpoint + 1}/{len(endpoints)})...")
            logging.debug(f"Using: {uri}")
            
            # Simple connection with conservative settings
            ws = await asyncio.wait_for(
                websockets.connect(
                    uri,
                    ping_interval=30,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=2**20,
                    compression=None
                ), 
                timeout=20
            )
            
            logging.info("⚡ 0xLIQD liquidation stream ACTIVE")
            reconnect_delay = 5  # Reset on successful connection
            current_endpoint = 0  # Reset to primary endpoint
            
            async with ws:
                last_message = time.time()
                
                while not shutdown_handler.shutdown_event.is_set():
                    try:
                        # Wait for message with generous timeout
                        done, pending = await asyncio.wait([
                            asyncio.create_task(ws.recv()),
                            asyncio.create_task(shutdown_handler.shutdown_event.wait())
                        ], return_when=asyncio.FIRST_COMPLETED, timeout=45)
                        
                        # Cancel pending tasks
                        for task in pending:
                            task.cancel()
                        
                        if shutdown_handler.shutdown_event.is_set():
                            logging.info("Shutdown event detected")
                            break
                        
                        if done:
                            message = done.pop().result()
                            last_message = time.time()
                            
                            try:
                                data = json.loads(message)
                                events = data.get("data", [])
                                if not isinstance(events, list):
                                    events = [events]
                                
                                for event in events:
                                    if not shutdown_handler.shutdown_event.is_set():
                                        asyncio.create_task(strategy.process_liquidation_event(event))
                                        
                            except json.JSONDecodeError:
                                # Normal ping/pong messages
                                continue
                            except Exception as e:
                                logging.error(f"Message processing error: {e}")
                                continue
                        else:
                            # Timeout - check if connection is stale
                            if time.time() - last_message > 120:  # 2 minutes without messages
                                logging.warning("Connection appears stale, reconnecting...")
                                break
                                
                    except websockets.exceptions.ConnectionClosed:
                        logging.warning("Connection closed by server")
                        break
                    except Exception as e:
                        logging.error(f"WebSocket error: {e}")
                        break
                        
        except asyncio.TimeoutError:
            logging.warning(f"Connection timeout with endpoint {current_endpoint + 1}")
            current_endpoint = (current_endpoint + 1) % len(endpoints)
            
        except Exception as e:
            if shutdown_handler.shutdown_event.is_set():
                break
            logging.error(f"Connection error: {e}")
            current_endpoint = (current_endpoint + 1) % len(endpoints)
        
        if not shutdown_handler.shutdown_event.is_set():
            logging.info(f"Reconnecting in {reconnect_delay}s...")
            try:
                await asyncio.wait_for(
                    shutdown_handler.shutdown_event.wait(),
                    timeout=reconnect_delay
                )
                break
            except asyncio.TimeoutError:
                pass
            
            reconnect_delay = min(reconnect_delay * 1.2, max_delay)

# =============================================================================
# MAIN BOT EXECUTION
# =============================================================================

async def main_bot():
    """Main bot execution"""
    setup_enhanced_logging()
    logging.info("0xLIQD - STARTING UP...")
    
    global data_manager, pair_filter_manager
    shutdown_handler.setup_signal_handlers()
    
    try:
        # Initialize exchange
        exchange = get_exchange()
        logging.info("Exchange connected")

        # Initialize timestamp management
        success = await initialize_timestamp_management(exchange)
        if not success:
            logging.error("Timestamp management failed")
            return
        
        # Initialize data manager
        data_manager = CCXTProDataManager(exchange)
        shutdown_handler.register_component(data_manager, data_manager.close)
        await data_manager.start_streaming()
        await asyncio.sleep(3)
        
        # Initialize zones client
        await rapidapi_client.initialize()
        shutdown_handler.register_component(rapidapi_client, rapidapi_client.close)
        
        # Try to fetch fresh zones data
        zones_fetch_success = await rapidapi_client.fetch_zones_data()
        zones_count = rapidapi_client.get_zones_count()
        if zones_count == 0:
            logging.error("No zones data available")
            return
        
        # Build trading pairs
        pairs_data = await pairs_builder.build_trading_pairs(exchange)
        pairs_count = pairs_builder.get_pairs_count()
        if pairs_count == 0:
            logging.error("No trading pairs built")
            return
        
        # Initialize pair age filter
        await pair_age_filter.initialize()
        shutdown_handler.register_component(pair_age_filter, pair_age_filter.close)

        # Initialize pair filter
        global pair_filter_manager
        pair_filter_manager = PairFilterManager(CONFIG.pair_filter)
        await pair_filter_manager.initialize()
        shutdown_handler.register_component(pair_filter_manager, pair_filter_manager.close)
        
        # Subscribe symbols
        for symbol in pairs_data.keys():
            data_manager.subscribe_symbol(symbol)
        
        await asyncio.sleep(2)
        
        # Initialize strategy
        strategy = VWAPHunterStrategy(exchange)
        
        # RECOVER EXISTING POSITIONS ON STARTUP
        recovered_positions = await position_manager.recover_existing_positions(exchange)
        if recovered_positions > 0:
            logging.info(f"🎯 Bot restarted with {recovered_positions} existing positions")

        # Start monitoring
        await position_manager.start_order_monitoring(exchange)
        shutdown_handler.register_component(position_manager, position_manager.stop_order_monitoring)
        
        monitor_task = asyncio.create_task(strategy.monitor_positions())
        stats_task = asyncio.create_task(periodic_stats_reporter())
        
        # Discord startup
        if discord_notifier:
            await discord_notifier.initialize()
            shutdown_handler.register_component(discord_notifier, discord_notifier.close)
            await discord_notifier.send_startup_alert(zones_count, pairs_count)
        
        logging.info(f"Configuration: {CONFIG.risk.max_positions} max positions")

        # LOG FILTER STATISTICS
        if pair_filter_manager:
            filter_stats = pair_filter_manager.get_filter_stats()
            logging.info(f"Pair Filters: Whitelist: {filter_stats['whitelist_enabled']} ({filter_stats['whitelist_count']} pairs), "
                        f"Blacklist: {filter_stats['blacklist_enabled']} ({filter_stats['blacklist_count']} pairs)")
        
        # Main liquidation stream (SIMPLIFIED VERSION)
        await run_liquidation_stream_with_shutdown(strategy)
        
    except KeyboardInterrupt:
        logging.info("Shutdown requested")
    except Exception as e:
        logging.error(f"Fatal error: {e}")
    finally:
        # Cleanup
        if 'monitor_task' in locals():
            monitor_task.cancel()
        if 'stats_task' in locals():
            stats_task.cancel()
        await shutdown_handler.cleanup_all()
        if exchange:
            await exchange.close()

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