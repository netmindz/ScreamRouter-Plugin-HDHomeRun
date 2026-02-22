"""HDHomeRun Radio Station Plugin for ScreamRouter with Auto-Discovery"""
import os
import select
import subprocess
import requests
import socket
import time
import threading
from typing import Any, List, Optional, Dict, Tuple
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import FastAPI
from screamrouter.plugin_manager.screamrouter_plugin import ScreamRouterPlugin
from screamrouter.screamrouter_types.configuration import SourceDescription
from screamrouter.screamrouter_logger.screamrouter_logger import get_logger
from screamrouter.audio.scream_header_parser import create_stream_info
from screamrouter.constants import constants

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
        self.tag = "HDHomeRun"  # Base tag for temporary source instance IDs
        self.devices: Dict[str, str] = {}  # ip -> name mapping
        self.refresh_interval = 3600  # Refresh lineup every hour
        self.discovery_interval = 300  # Re-discover devices every 5 minutes
        self.registered_sources: set = set()  # Track registered sources to avoid duplicates
        self.last_discovery = 0
        self.channel_urls: Dict[str, str] = {}  # tag -> URL mapping
        self.channel_names: Dict[str, str] = {}  # tag -> friendly name mapping

        # Streaming infrastructure (similar to play_url plugin)
        self.ffmpeg_processes: Dict[str, subprocess.Popen] = {}  # source_id -> ffmpeg process
        self.ffmpeg_pipes: Dict[str, Tuple[int, int]] = {}  # source_id -> (read_fd, write_fd)
        self.active_streams: Dict[str, Dict[str, Any]] = {}  # source_id -> stream info
        self.source_instance_ids: Dict[str, str] = {}  # tag -> instance_id mapping

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
            if not hasattr(self, 'channel_urls'):
                return {"channels": {}}
            return {"channels": self.channel_urls}

        @api.get("/hdhomerun/play/{tag}")
        async def get_play_url(tag: str):
            """Get the stream URL for a specific channel tag"""
            if not hasattr(self, 'channel_urls'):
                return {"error": "No channels discovered yet"}
            if tag not in self.channel_urls:
                return {"error": f"Channel {tag} not found"}
            return {"tag": tag, "url": self.channel_urls[tag]}


        @api.get("/hdhomerun/stream/active")
        async def get_active_streams():
            """List all currently active streams"""
            return {
                "active_streams": list(self.active_streams.keys()),
                "count": len(self.active_streams)
            }

        @api.post("/hdhomerun/play/{tag}/sink/{sink_name}")
        async def play_channel_on_sink(tag: str, sink_name: str):
            """Play a HDHomeRun channel on a specific sink"""
            if tag not in self.channel_urls:
                return {"error": f"Channel {tag} not found"}

            # Start the stream
            success = self.start_stream_for_sink(tag, sink_name)
            if success:
                return {"status": "started", "tag": tag, "sink": sink_name, "url": self.channel_urls[tag]}
            else:
                return {"error": f"Failed to start stream for {tag}"}

        @api.post("/hdhomerun/stop/{tag}")
        async def stop_channel(tag: str):
            """Stop a HDHomeRun channel stream"""
            if tag in self.active_streams:
                self.stop_stream(tag)
                return {"status": "stopped", "tag": tag}
            else:
                return {"error": f"Stream {tag} not active"}

        # Perform initial discovery in background thread to not block startup
        logger.info("Scheduling initial HDHomeRun device discovery in background")
        discovery_thread = threading.Thread(target=self.discover_devices, daemon=True)
        discovery_thread.start()

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
                
                # Log the URL being registered
                logger.info(f"Registering channel {guide_number} ({guide_name}) with URL: {stream_url}")

                # Store the channel info for when we need to stream it
                # With temporary sources, we create them on-demand when user requests playback
                self.channel_urls[tag] = stream_url
                self.channel_names[tag] = f"HDHomeRun [{device_name}]: {guide_name} ({guide_number})"
                self.registered_sources.add(tag)
                logger.info(f"Registered channel: {self.channel_names[tag]} with tag: {tag}")

        except Exception as e:
            logger.error(f"Failed to fetch lineup from {device_ip}: {e}")
    
    def fetch_all_stations(self):
        """Refresh stations from all known devices"""
        logger.info("Refreshing all HDHomeRun stations")
        for device_ip, device_name in self.devices.items():
            self.fetch_stations_from_device(device_ip, device_name)
    
    def start_stream_for_sink(self, source_tag: str, sink_name: str) -> bool:
        """Start streaming a HDHomeRun channel to a specific sink using temporary source"""
        if source_tag not in self.channel_urls:
            logger.error(f"Cannot start stream: channel {source_tag} not found")
            return False

        if source_tag in self.active_streams:
            logger.warning(f"Stream {source_tag} already active")
            return True

        url = self.channel_urls[source_tag]
        logger.info(f"Starting stream for {source_tag} to sink {sink_name}: {url}")

        try:
            # Create a temporary source for this stream
            source_desc = SourceDescription(
                name=self.channel_names.get(source_tag, f"HDHomeRun {source_tag}"),
                tag=source_tag,
                enabled=True
            )

            # Add temporary source and get instance_id
            instance_id = self.add_temporary_source(sink_name, source_desc)
            if not instance_id:
                logger.error(f"Failed to add temporary source for {source_tag}")
                return False

            self.source_instance_ids[source_tag] = instance_id
            logger.info(f"Created temporary source with instance_id: {instance_id}")

            # Create pipe for ffmpeg output
            read_fd, write_fd = os.pipe()

            # Build ffmpeg command
            ffmpeg_command = [
                "ffmpeg", "-hide_banner",
                "-re",  # Read input at native frame rate
                "-i", url,
                "-f", f"s{self.default_bit_depth}le",
                "-ac", f"{self.default_channels}",
                "-ar", f"{self.default_sample_rate}",
                f"pipe:{write_fd}"
            ]

            logger.debug(f"[HDHomeRun] ffmpeg command: {ffmpeg_command}")

            # Start ffmpeg process
            output = None if constants.SHOW_FFMPEG_OUTPUT else subprocess.DEVNULL
            process = subprocess.Popen(
                ffmpeg_command,
                shell=False,
                start_new_session=True,
                pass_fds=[write_fd],
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=output
            )

            # Store process and pipe info
            self.ffmpeg_processes[source_tag] = process
            self.ffmpeg_pipes[source_tag] = (read_fd, write_fd)

            # Create stream info for this source
            stream_info = create_stream_info(
                self.default_bit_depth,
                self.default_sample_rate,
                self.default_channels,
                self.default_channel_layout
            )

            self.active_streams[source_tag] = {
                'instance_id': instance_id,
                'sink_name': sink_name,
                'bit_depth': self.default_bit_depth,
                'sample_rate': self.default_sample_rate,
                'channels': self.default_channels,
                'chlayout1': stream_info.channel_layout[0],
                'chlayout2': stream_info.channel_layout[1],
                'chunk_size': self.get_chunk_size_bytes(self.default_channels, self.default_bit_depth)
            }

            logger.info(f"Stream started successfully for {source_tag}")
            return True

        except Exception as e:
            logger.error(f"Failed to start stream for {source_tag}: {e}")
            self.stop_stream(source_tag)
            return False

    def stop_stream(self, source_tag: str):
        """Stop streaming a HDHomeRun channel"""
        logger.info(f"Stopping stream for {source_tag}")

        # Remove temporary source
        if source_tag in self.source_instance_ids:
            instance_id = self.source_instance_ids[source_tag]
            try:
                self.remove_temporary_source(instance_id)
                logger.info(f"Removed temporary source {instance_id}")
            except Exception as e:
                logger.warning(f"Error removing temporary source: {e}")
            del self.source_instance_ids[source_tag]

        # Kill ffmpeg process
        if source_tag in self.ffmpeg_processes:
            process = self.ffmpeg_processes[source_tag]
            if process.poll() is None:
                process.kill()
                process.wait()
            del self.ffmpeg_processes[source_tag]

        # Close pipes
        if source_tag in self.ffmpeg_pipes:
            read_fd, write_fd = self.ffmpeg_pipes[source_tag]
            try:
                os.close(read_fd)
            except OSError:
                pass
            try:
                os.close(write_fd)
            except OSError:
                pass
            del self.ffmpeg_pipes[source_tag]

        # Remove stream info
        if source_tag in self.active_streams:
            del self.active_streams[source_tag]

        logger.info(f"Stream stopped for {source_tag}")


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

        # Stop all active streams
        for source_tag in list(self.active_streams.keys()):
            self.stop_stream(source_tag)

        super().stop()
    
    def run(self):
        """Main thread loop - handle streaming, discovery, and refresh"""
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

            # Handle active streams - read from ffmpeg and write to ScreamRouter
            for source_tag in list(self.active_streams.keys()):
                if source_tag not in self.ffmpeg_pipes:
                    continue

                read_fd, _ = self.ffmpeg_pipes[source_tag]
                process = self.ffmpeg_processes.get(source_tag)
                stream_info = self.active_streams[source_tag]
                instance_id = stream_info.get('instance_id')

                # Check if ffmpeg process has ended
                if process and process.poll() is not None:
                    logger.warning(f"FFmpeg process for {source_tag} ended unexpectedly")
                    self.stop_stream(source_tag)
                    continue

                # Check for data from ffmpeg (non-blocking)
                try:
                    ready = select.select([read_fd], [], [], 0.01)  # 10ms timeout
                    if ready[0]:
                        chunk_size = stream_info['chunk_size']
                        pcm_data = os.read(read_fd, chunk_size)

                        if not pcm_data:  # EOF
                            logger.info(f"FFmpeg for {source_tag} sent EOF")
                            self.stop_stream(source_tag)
                            continue

                        if len(pcm_data) == chunk_size:
                            # Write audio data to ScreamRouter using the instance_id
                            self.write_data(
                                source_instance_id=instance_id,
                                pcm_data=pcm_data,
                                channels=stream_info['channels'],
                                sample_rate=stream_info['sample_rate'],
                                bit_depth=stream_info['bit_depth'],
                                chlayout1=stream_info['chlayout1'],
                                chlayout2=stream_info['chlayout2']
                            )
                        elif len(pcm_data) > 0:
                            logger.warning(f"Partial packet from ffmpeg for {source_tag}: {len(pcm_data)} bytes")

                except Exception as e:
                    logger.error(f"Error reading from ffmpeg for {source_tag}: {e}")
                    self.stop_stream(source_tag)

            # Small sleep to prevent CPU spinning when no active streams
            if not self.active_streams:
                time.sleep(1)

        # Cleanup on exit
        logger.info(f"[{self.name}] Plugin thread stopping, cleaning up streams")
        for source_tag in list(self.active_streams.keys()):
            self.stop_stream(source_tag)

        logger.info(f"[{self.name}] Plugin thread stopped")
