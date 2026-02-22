"""HDHomeRun Radio Station Plugin for ScreamRouter with Auto-Discovery"""
import os
import select
import subprocess
import requests
import socket
import time
import threading
import urllib3
from typing import Any, List, Optional, Dict, Tuple
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import FastAPI
from screamrouter.plugin_manager.screamrouter_plugin import ScreamRouterPlugin
from screamrouter.screamrouter_types.configuration import SourceDescription
from screamrouter.screamrouter_logger.screamrouter_logger import get_logger
from screamrouter.audio.scream_header_parser import create_stream_info
from screamrouter.constants import constants

# Suppress InsecureRequestWarning for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
        self.tag = "HDHomeRun"
        self.devices: Dict[str, str] = {}  # ip -> name mapping
        self.refresh_interval = 3600  # Refresh lineup every hour
        self.discovery_interval = 300  # Re-discover devices every 5 minutes
        self.registered_sources: set = set()
        self.last_discovery = 0
        self.channel_urls: Dict[str, str] = {}  # tag -> URL mapping
        self.channel_names: Dict[str, str] = {}  # tag -> friendly name mapping

        # Streaming infrastructure
        self.ffmpeg_processes: Dict[str, subprocess.Popen] = {}
        self.ffmpeg_pipes: Dict[str, Tuple[int, int]] = {}
        self.active_streams: Dict[str, Dict[str, Any]] = {}

        # Default audio format
        self.default_bit_depth = 16
        self.default_sample_rate = 48000
        self.default_channels = 2
        self.default_channel_layout = "stereo"

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

        @api.get("/hdhomerun/channels")
        async def get_channels():
            """List all discovered HDHomeRun channels with their URLs"""
            return {"channels": self.channel_urls}

        @api.get("/hdhomerun/stream/active")
        async def get_active_streams():
            """List all currently active streams"""
            return {
                "active_streams": list(self.active_streams.keys()),
                "count": len(self.active_streams)
            }

        @api.get("/hdhomerun/sources")
        async def get_permanent_sources():
            """Debug endpoint to see registered permanent sources"""
            return {
                "permanent_sources": [{"name": s.name, "tag": s.tag} for s in self.permanent_sources],
                "registered_sources": list(self.registered_sources),
                "wants_reload": self.wants_reload,
                "count": len(self.permanent_sources)
            }

        # Perform initial discovery in background thread
        logger.info("Scheduling initial HDHomeRun device discovery in background")
        discovery_thread = threading.Thread(target=self.discover_devices, daemon=True)
        discovery_thread.start()

        # Log available methods on audio_manager_instance for debugging
        if self.audio_manager_instance:
            methods = [m for m in dir(self.audio_manager_instance) if not m.startswith('_')]
            logger.info(f"AudioManager available methods: {methods}")

        # Start the plugin's main thread (runs the run() method)
        logger.info("Starting HDHomeRun plugin main thread")
        self.start()

    def discover_devices(self):
        """Discover HDHomeRun devices using all available methods"""
        try:
            logger.info("Running HDHomeRun device discovery (all methods)")
            discovered = discover_all_methods(mdns_timeout=10)

            for ip, name in discovered.items():
                if ip not in self.devices:
                    self.devices[ip] = name
                    logger.info(f"Added new HDHomeRun device: {name} ({ip})")
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

                # Filter for radio stations
                if not is_likely_radio(guide_number, guide_name):
                    continue

                tag = f"hdhomerun_{device_ip.replace('.', '_')}_{guide_number.replace('.', '_')}"

                if tag in self.registered_sources:
                    continue

                logger.info(f"Registering channel {guide_number} ({guide_name}) with URL: {stream_url}")

                # Store channel info
                self.channel_urls[tag] = stream_url
                self.channel_names[tag] = f"HDHomeRun [{device_name}]: {guide_name} ({guide_number})"

                # Create permanent source using the proper method
                source = SourceDescription(
                    name=self.channel_names[tag],
                    tag=tag,
                    enabled=True
                )
                # Use the base class method to add permanent source
                self.add_permanet_source(source)
                self.registered_sources.add(tag)
                logger.info(f"Added permanent source: {self.channel_names[tag]}")

            # Ensure wants_reload is set after adding all sources from this device
            self.wants_reload = True
            logger.info(f"Set wants_reload=True, permanent_sources count: {len(self.permanent_sources)}")

        except Exception as e:
            logger.error(f"Failed to fetch lineup from {device_ip}: {e}")

    def fetch_all_stations(self):
        """Refresh stations from all known devices"""
        logger.info("Refreshing all HDHomeRun stations")
        for device_ip, device_name in self.devices.items():
            self.fetch_stations_from_device(device_ip, device_name)

    def start_stream(self, source_tag: str) -> bool:
        """Start ffmpeg for a HDHomeRun channel"""
        if source_tag not in self.channel_urls:
            logger.error(f"Cannot start stream: channel {source_tag} not found")
            return False

        if source_tag in self.active_streams:
            return True  # Already running

        url = self.channel_urls[source_tag]
        logger.info(f"Starting ffmpeg for {source_tag}: {url}")

        try:
            read_fd, write_fd = os.pipe()

            ffmpeg_command = [
                "ffmpeg", "-hide_banner", "-loglevel", "warning",
                "-re",
                "-i", url,
                "-f", f"s{self.default_bit_depth}le",
                "-ac", f"{self.default_channels}",
                "-ar", f"{self.default_sample_rate}",
                f"pipe:{write_fd}"
            ]

            logger.info(f"FFmpeg command: {' '.join(ffmpeg_command)}")

            process = subprocess.Popen(
                ffmpeg_command,
                shell=False,
                start_new_session=True,
                pass_fds=[write_fd],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            # Close write end in parent process after fork
            os.close(write_fd)

            self.ffmpeg_processes[source_tag] = process
            self.ffmpeg_pipes[source_tag] = read_fd

            stream_info = create_stream_info(
                self.default_bit_depth,
                self.default_sample_rate,
                self.default_channels,
                self.default_channel_layout
            )

            self.active_streams[source_tag] = {
                'bit_depth': self.default_bit_depth,
                'sample_rate': self.default_sample_rate,
                'channels': self.default_channels,
                'chlayout1': stream_info.channel_layout[0],
                'chlayout2': stream_info.channel_layout[1],
                'chunk_size': self.get_chunk_size_bytes(self.default_channels, self.default_bit_depth)
            }

            logger.info(f"FFmpeg started for {source_tag}, pid={process.pid}")
            return True

        except Exception as e:
            logger.error(f"Failed to start ffmpeg for {source_tag}: {e}")
            self.stop_stream(source_tag)
            return False

    def stop_stream(self, source_tag: str):
        """Stop ffmpeg for a channel"""
        logger.info(f"Stopping stream for {source_tag}")

        if source_tag in self.ffmpeg_processes:
            process = self.ffmpeg_processes[source_tag]
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            del self.ffmpeg_processes[source_tag]

        if source_tag in self.ffmpeg_pipes:
            try:
                os.close(self.ffmpeg_pipes[source_tag])
            except OSError:
                pass
            del self.ffmpeg_pipes[source_tag]

        if source_tag in self.active_streams:
            del self.active_streams[source_tag]

    def get_audio_data(self, source_tag: str, num_bytes: int) -> Optional[bytes]:
        """Called by ScreamRouter to get audio data for a permanent source.
        This is the key method that makes permanent sources work."""

        # Check if this is one of our sources
        if source_tag not in self.channel_urls:
            logger.warning(f"get_audio_data: Unknown source tag {source_tag}")
            return None

        # Start stream if not already running
        if source_tag not in self.active_streams:
            logger.info(f"get_audio_data: Starting stream for {source_tag}")
            if not self.start_stream(source_tag):
                return None

        # Check if ffmpeg is still running
        if source_tag in self.ffmpeg_processes:
            process = self.ffmpeg_processes[source_tag]
            if process.poll() is not None:
                logger.warning(f"FFmpeg died for {source_tag}, restarting...")
                self.stop_stream(source_tag)
                if not self.start_stream(source_tag):
                    return None

        # Read from pipe
        if source_tag in self.ffmpeg_pipes:
            read_fd = self.ffmpeg_pipes[source_tag]
            try:
                ready = select.select([read_fd], [], [], 0.01)
                if ready[0]:
                    data = os.read(read_fd, num_bytes)
                    if data:
                        return data
                    else:
                        # EOF - ffmpeg ended
                        logger.info(f"FFmpeg EOF for {source_tag}")
                        self.stop_stream(source_tag)
                        return None
                else:
                    # No data ready, return silence
                    return bytes(num_bytes)
            except Exception as e:
                logger.error(f"Error reading from ffmpeg for {source_tag}: {e}")
                self.stop_stream(source_tag)
                return None

        return None

    def load(self, controller_write_fds: List[int]):
        """Called when available source list changes"""
        super().load(controller_write_fds)
        self.fetch_all_stations()

    def unload(self):
        """Called when unloading the plugin"""
        logger.info("Unloading HDHomeRun plugin")
        # Stop all streams
        for source_tag in list(self.active_streams.keys()):
            self.stop_stream(source_tag)
        self.registered_sources.clear()
        super().unload()

    def stop(self):
        """Stop the plugin and clean up resources"""
        logger.info("Stopping HDHomeRun plugin")
        for source_tag in list(self.active_streams.keys()):
            self.stop_stream(source_tag)
        super().stop()

    def run(self):
        """Main thread loop - periodic discovery, cleanup, and route monitoring"""
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

            # Check for active routes to our sources and start/stop ffmpeg accordingly
            self.check_active_routes()

            # Check for dead ffmpeg processes
            for source_tag in list(self.active_streams.keys()):
                if source_tag in self.ffmpeg_processes:
                    process = self.ffmpeg_processes[source_tag]
                    if process.poll() is not None:
                        logger.warning(f"FFmpeg for {source_tag} died, cleaning up")
                        self.stop_stream(source_tag)

            time.sleep(1)  # Check every second for responsiveness

        # Cleanup
        logger.info(f"[{self.name}] Plugin thread stopping")
        for source_tag in list(self.active_streams.keys()):
            self.stop_stream(source_tag)

        logger.info(f"[{self.name}] Plugin thread stopped")

    def check_active_routes(self):
        """Check if any of our sources have active routes and start/stop ffmpeg accordingly.
        We query ScreamRouter's own API to find active routes."""
        try:
            # Query ScreamRouter's routes API (HTTPS with self-signed cert)
            response = requests.get("https://127.0.0.1:8443/routes", timeout=2, verify=False)
            if response.status_code != 200:
                logger.warning(f"Failed to query ScreamRouter routes API: {response.status_code}")
                return

            routes = response.json()
            logger.debug(f"Got {len(routes)} routes from ScreamRouter API")

            # Build a reverse lookup: source name -> tag
            name_to_tag = {name: tag for tag, name in self.channel_names.items()}
            logger.debug(f"Have {len(name_to_tag)} HDHomeRun channels registered")

            # Find which of our sources are currently routed
            active_tags = set()
            for route in routes:
                if not route.get('enabled', True):
                    continue
                source_name = route.get('source', '')

                # Check if this source name matches any of our channel names
                if source_name in name_to_tag:
                    active_tags.add(name_to_tag[source_name])
                    logger.debug(f"Found active route for source: {source_name}")
                else:
                    # Also check if the source matches any tag directly
                    if source_name in self.channel_urls:
                        active_tags.add(source_name)
                        logger.debug(f"Found active route for tag: {source_name}")

            if active_tags:
                logger.info(f"Active HDHomeRun sources: {active_tags}")

            # Start streams for our sources that are now active
            for tag in active_tags:
                if tag not in self.active_streams:
                    logger.info(f"Source {tag} has active route, starting stream")
                    self.start_stream(tag)

            # Stop streams for sources that are no longer active
            for tag in list(self.active_streams.keys()):
                if tag not in active_tags:
                    logger.info(f"Source {tag} no longer has active route, stopping stream")
                    self.stop_stream(tag)

        except requests.exceptions.RequestException as e:
            logger.warning(f"Could not check routes (request error): {e}")
        except Exception as e:
            logger.warning(f"Error checking active routes: {e}")

