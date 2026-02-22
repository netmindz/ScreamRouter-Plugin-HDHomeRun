"""HDHomeRun Radio Station Plugin for ScreamRouter with Auto-Discovery"""
import requests
import threading
import time
from typing import Any, List, Optional, Dict
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from fastapi import FastAPI
from screamrouter.plugin_manager.screamrouter_plugin import ScreamRouterPlugin
from screamrouter.screamrouter_types.configuration import SourceDescription
from screamrouter.screamrouter_logger.screamrouter_logger import get_logger

logger = get_logger(__name__)

class HDHomeRunDiscoveryListener(ServiceListener):
    """Listens for HDHomeRun devices on the network via mDNS"""
    
    def __init__(self, plugin: 'PluginHDHomeRun'):
        self.plugin = plugin
    
    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a HDHomeRun device is discovered"""
        info = zc.get_service_info(type_, name)
        if info:
            # Extract IP address
            if info.addresses:
                ip = '.'.join(str(b) for b in info.addresses[0])
                logger.info(f"Discovered HDHomeRun device: {name} at {ip}")
                self.plugin.add_device(ip, name)
    
    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a HDHomeRun device disappears"""
        logger.info(f"HDHomeRun device removed: {name}")
        # Optionally handle device removal
    
    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a HDHomeRun device is updated"""
        pass


class PluginHDHomeRun(ScreamRouterPlugin):
    """Discovers HDHomeRun devices and adds radio stations as sources"""
    
    def __init__(self):
        super().__init__("HDHomeRun Radio")
        self.devices: Dict[str, str] = {}  # ip -> name mapping
        self.refresh_interval = 3600  # Refresh lineup every hour
        self.zeroconf: Optional[Zeroconf] = None
        self.browser: Optional[ServiceBrowser] = None
        self.discovery_thread: Optional[threading.Thread] = None
        self.registered_sources: set = set()  # Track registered sources to avoid duplicates
    
    def plugin_start(self, api: FastAPI, audio_manager_instance: Any = None):
        """Start plugin, register endpoints, and begin device discovery"""
        super().plugin_start(api, audio_manager_instance)
        
        # Register API endpoints
        @api.get("/hdhomerun/devices")
        async def get_devices():
            """List discovered HDHomeRun devices"""
            return {"devices": self.devices}
        
        @api.post("/hdhomerun/refresh")
        async def refresh_lineup():
            """Manually refresh station lineup"""
            self.fetch_all_stations()
            return {"status": "refreshed"}
        
        @api.post("/hdhomerun/discover")
        async def trigger_discovery():
            """Manually trigger device discovery"""
            self.start_discovery()
            return {"status": "discovery started"}
        
        # Start auto-discovery
        self.start_discovery()
    
    def start_discovery(self):
        """Start mDNS discovery for HDHomeRun devices"""
        try:
            if not self.zeroconf:
                self.zeroconf = Zeroconf()
                listener = HDHomeRunDiscoveryListener(self)
                # HDHomeRun devices advertise as _hdhomerun._tcp.local.
                self.browser = ServiceBrowser(
                    self.zeroconf, 
                    "_hdhomerun._tcp.local.", 
                    listener
                )
                logger.info("HDHomeRun mDNS discovery started")
        except Exception as e:
            logger.error(f"Failed to start HDHomeRun discovery: {e}")
    
    def add_device(self, ip: str, name: str):
        """Add a discovered HDHomeRun device and fetch its stations"""
        if ip not in self.devices:
            self.devices[ip] = name
            logger.info(f"Added HDHomeRun device: {name} ({ip})")
            # Fetch stations from this device
            self.fetch_stations_from_device(ip, name)
    
    def fetch_stations_from_device(self, device_ip: str, device_name: str):
        """Fetch lineup from a specific HDHomeRun device"""
        try:
            response = requests.get(
                f"http://{device_ip}/lineup.json", 
                timeout=5
            )
            response.raise_for_status()
            lineup = response.json()
            
            logger.info(f"Found {len(lineup)} channels on {device_name}")
            
            for station in lineup:
                guide_number = station.get('GuideNumber', '')
                guide_name = station.get('GuideName', 'Unknown')
                stream_url = station.get('URL', '')
                
                # Filter for radio stations (optional - you can customize this)
                # Some HDHomeRun devices mark audio-only channels
                # or you can filter by channel number range
                
                # Create unique tag for this station
                tag = f"hdhomerun_{device_ip.replace('.', '_')}_{guide_number.replace('.', '_')}"
                
                # Avoid duplicates
                if tag in self.registered_sources:
                    continue
                
                # Create a SourceDescription for each station
                source = SourceDescription(
                    name=f"HDHomeRun [{device_name}]: {guide_name} ({guide_number})",
                    tag=tag,
                    ip="",  # Not needed for URL-based sources
                    port=0,
                    url=stream_url,  # Use the stream URL
                    enabled=True
                )
                
                self.add_permanent_source(source)
                self.registered_sources.add(tag)
                logger.info(f"Added station: {source.name}")
                
        except Exception as e:
            logger.error(f"Failed to fetch lineup from {device_ip}: {e}")
    
    def fetch_all_stations(self):
        """Refresh stations from all known devices"""
        logger.info("Refreshing all HDHomeRun stations")
        for device_ip, device_name in self.devices.items():
            self.fetch_stations_from_device(device_ip, device_name)
    
    def load(self, controller_write_fds: List[int]):
        """Called when available source list changes"""
        super().load(controller_write_fds)
        # Fetch stations from all discovered devices
        self.fetch_all_stations()
    
    def unload(self):
        """Called when unloading the plugin"""
        logger.info("Unloading HDHomeRun plugin")
        self.registered_sources.clear()
        super().unload()
    
    def stop(self):
        """Stop the plugin and clean up resources"""
        logger.info("Stopping HDHomeRun plugin")
        
        # Stop mDNS discovery
        if self.browser:
            self.browser.cancel()
        if self.zeroconf:
            self.zeroconf.close()
        
        super().stop()
    
    def run(self):
        """Main thread loop - periodically refresh station lineup"""
        logger.info(f"[{self.name}] Plugin thread started")
        
        last_refresh = 0
        while self.running_flag.value:
            current_time = time.time()
            
            # Refresh lineup periodically
            if current_time - last_refresh > self.refresh_interval:
                self.fetch_all_stations()
                last_refresh = current_time
            
            time.sleep(10)  # Check every 10 seconds
        
        logger.info(f"[{self.name}] Plugin thread stopped")
