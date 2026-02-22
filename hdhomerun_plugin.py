"""HDHomeRun Radio Station Plugin for ScreamRouter with Auto-Discovery"""
import requests
import socket
import time
from typing import Any, List, Optional, Dict, Tuple
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import FastAPI
from screamrouter.plugin_manager.screamrouter_plugin import ScreamRouterPlugin
from screamrouter.screamrouter_types.configuration import SourceDescription
from screamrouter.screamrouter_logger.screamrouter_logger import get_logger

logger = get_logger(__name__)


# Discovery Helper Functions
def verify_hdhomerun(ip: str) -> bool:
    """Verify if an IP address is actually a HDHomeRun device"""
    try:
        response = requests.get(f"http://{ip}/discover.json", timeout=2)
        response.raise_for_status()
        info = response.json()

        # Check for HDHomeRun-specific fields
        if 'DeviceID' in info and 'ModelNumber' in info:
            return True
    except:
        pass
    return False


def get_device_name(ip: str) -> str:
    """Get the friendly name of a HDHomeRun device"""
    try:
        response = requests.get(f"http://{ip}/discover.json", timeout=2)
        response.raise_for_status()
        info = response.json()
        return info.get('FriendlyName', f"HDHomeRun at {ip}")
    except:
        return f"HDHomeRun at {ip}"


def get_device_info(ip: str) -> Optional[dict]:
    """Get full device information from a HDHomeRun device"""
    try:
        response = requests.get(f"http://{ip}/discover.json", timeout=2)
        response.raise_for_status()
        return response.json()
    except:
        return None


def is_likely_radio(guide_number: str, guide_name: str) -> bool:
    """Heuristic to detect if a channel is likely a radio station"""
    # Radio stations often have channel numbers in FM range (88-108)
    # or contain keywords like "Radio", "FM", "Music"
    try:
        num = float(guide_number.split('-')[0])
        if 88.0 <= num <= 108.0:
            return True
    except (ValueError, IndexError):
        pass

    radio_keywords = ['radio', 'fm', 'am', 'music', 'npr', 'jazz', 'classical', 'rock', 'news radio', 'talk radio']
    name_lower = guide_name.lower()
    return any(keyword in name_lower for keyword in radio_keywords)


def discover_via_mdns(timeout: int = 10) -> Dict[str, str]:
    """Discover HDHomeRun devices via mDNS/Zeroconf"""
    logger.info(f"Starting mDNS discovery (timeout: {timeout}s)")

    class MDNSListener(ServiceListener):
        def __init__(self):
            self.devices: Dict[str, str] = {}

        def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name)
            if info and info.addresses:
                ip = '.'.join(str(b) for b in info.addresses[0])
                if verify_hdhomerun(ip):
                    device_name = get_device_name(ip)
                    self.devices[ip] = device_name

        def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            pass

        def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            pass

    devices = {}
    try:
        zeroconf = Zeroconf()
        listener = MDNSListener()
        browser = ServiceBrowser(zeroconf, "_hdhomerun._tcp.local.", listener)
        time.sleep(timeout)
        browser.cancel()
        devices = listener.devices
        zeroconf.close()
        logger.info(f"mDNS found {len(devices)} HDHomeRun device(s)")
    except Exception as e:
        logger.error(f"mDNS discovery error: {e}")

    return devices


def discover_via_broadcast() -> Dict[str, str]:
    """Discover HDHomeRun devices via UDP broadcast"""
    logger.info("Starting UDP broadcast discovery")

    devices = {}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(3)

        # HDHomeRun discovery packet format
        discover_packet = bytes([
            0x00, 0x02,  # Packet type: discover request
            0x00, 0x0c,  # Payload length
            0x01, 0x04, 0xff, 0xff, 0xff, 0xff,  # Device ID (wildcard)
            0x02, 0x04, 0xff, 0xff, 0xff, 0xff,  # Device Type (wildcard)
        ])

        sock.sendto(discover_packet, ('255.255.255.255', 65001))

        start_time = time.time()
        while time.time() - start_time < 3:
            try:
                data, addr = sock.recvfrom(1024)
                ip = addr[0]

                if verify_hdhomerun(ip):
                    if ip not in devices:
                        device_name = get_device_name(ip)
                        devices[ip] = device_name
                        logger.info(f"Broadcast found device: {device_name} at {ip}")
            except socket.timeout:
                break

        sock.close()
        logger.info(f"Broadcast found {len(devices)} HDHomeRun device(s)")
    except Exception as e:
        logger.error(f"UDP broadcast error: {e}")

    return devices


def scan_subnet_for_hdhomerun() -> Dict[str, str]:
    """Scan local subnet for HDHomeRun devices (using parallel scanning)"""
    logger.info("Starting subnet scan (parallel)")

    devices = {}

    def check_ip(ip: str) -> Optional[Tuple[str, str]]:
        """Check a single IP address for HDHomeRun device"""
        if verify_hdhomerun(ip):
            device_name = get_device_name(ip)
            return (ip, device_name)
        return None

    try:
        # Get local IP to determine subnet
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()

        # Get subnet (assuming /24)
        subnet_parts = local_ip.split('.')
        subnet_base = '.'.join(subnet_parts[:3])

        logger.info(f"Scanning subnet {subnet_base}.0/24")

        # Build list of IPs to check
        ips_to_check = [f"{subnet_base}.{i}" for i in range(2, 253)]

        # Use ThreadPoolExecutor for parallel scanning
        with ThreadPoolExecutor(max_workers=50) as executor:
            future_to_ip = {executor.submit(check_ip, ip): ip for ip in ips_to_check}

            for future in as_completed(future_to_ip):
                try:
                    result = future.result()
                    if result:
                        ip, device_name = result
                        devices[ip] = device_name
                        logger.info(f"Subnet scan found: {device_name} at {ip}")
                except Exception:
                    pass

        logger.info(f"Subnet scan found {len(devices)} HDHomeRun device(s)")
    except Exception as e:
        logger.error(f"Subnet scan error: {e}")

    return devices


def discover_all_methods(mdns_timeout: int = 10) -> Dict[str, str]:
    """Run all discovery methods and return all found devices"""
    all_devices = {}

    # Method 1: mDNS Discovery
    mdns_devices = discover_via_mdns(timeout=mdns_timeout)
    all_devices.update(mdns_devices)

    # Method 2: UDP Broadcast Discovery
    broadcast_devices = discover_via_broadcast()
    all_devices.update(broadcast_devices)

    # Method 3: Subnet scan (only if nothing found yet to save time)
    if not all_devices:
        logger.info("No devices found yet, trying subnet scan...")
        subnet_devices = scan_subnet_for_hdhomerun()
        all_devices.update(subnet_devices)

    return all_devices


class PluginHDHomeRun(ScreamRouterPlugin):
    """Discovers HDHomeRun devices and adds radio stations as sources"""
    
    def __init__(self):
        super().__init__("HDHomeRun Radio")
        self.devices: Dict[str, str] = {}  # ip -> name mapping
        self.refresh_interval = 3600  # Refresh lineup every hour
        self.discovery_interval = 300  # Re-discover devices every 5 minutes
        self.registered_sources: set = set()  # Track registered sources to avoid duplicates
        self.last_discovery = 0

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
            self.discover_devices()
            return {"status": "discovery started", "devices": self.devices}

        # Perform initial discovery
        logger.info("Starting initial HDHomeRun device discovery")
        self.discover_devices()

    def discover_devices(self):
        """Discover HDHomeRun devices using all available methods"""
        try:
            logger.info("Running HDHomeRun device discovery (all methods)")
            discovered = discover_all_methods(mdns_timeout=10)

            # Add newly discovered devices
            for ip, name in discovered.items():
                if ip not in self.devices:
                    self.devices[ip] = name
                    logger.info(f"Added new HDHomeRun device: {name} ({ip})")
                    # Fetch stations from this device
                    self.fetch_stations_from_device(ip, name)

            self.last_discovery = time.time()

        except Exception as e:
            logger.error(f"Device discovery failed: {e}")

    def fetch_stations_from_device(self, device_ip: str, device_name: str):
        """Fetch lineup from a specific HDHomeRun device"""
        try:
            response = requests.get(f"http://{device_ip}/lineup.json", timeout=5)
            response.raise_for_status()
            lineup = response.json()
            
            if not lineup:
                logger.warning(f"No channels found on {device_name}")
                return

            logger.info(f"Processing {len(lineup)} channels from {device_name}")

            for station in lineup:
                guide_number = station.get('GuideNumber', '')
                guide_name = station.get('GuideName', 'Unknown')
                stream_url = station.get('URL', '')
                
                if not stream_url:
                    continue

                # Filter for radio stations (optional - you can customize this)
                # Comment out the next two lines to include ALL channels (TV + Radio)
                if not is_likely_radio(guide_number, guide_name):
                    continue

                # Create unique tag for this station
                tag = f"hdhomerun_{device_ip.replace('.', '_')}_{guide_number.replace('.', '_')}"
                
                # Avoid duplicates
                if tag in self.registered_sources:
                    continue
                
                # Create a SourceDescription for each station
                source = SourceDescription(
                    name=f"HDHomeRun [{device_name}]: {guide_name} ({guide_number})",
                    tag=tag,
                    ip=device_ip,  # Use device IP for the source
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
        super().stop()
    
    def run(self):
        """Main thread loop - periodically refresh station lineup and re-discover devices"""
        logger.info(f"[{self.name}] Plugin thread started")
        
        last_refresh = 0

        while self.running_flag.value:
            current_time = time.time()
            
            # Re-discover devices periodically
            if current_time - self.last_discovery > self.discovery_interval:
                logger.info("Periodic device discovery triggered")
                self.discover_devices()

            # Refresh lineup periodically
            if current_time - last_refresh > self.refresh_interval:
                self.fetch_all_stations()
                last_refresh = current_time
            
            time.sleep(10)  # Check every 10 seconds
        
        logger.info(f"[{self.name}] Plugin thread stopped")
