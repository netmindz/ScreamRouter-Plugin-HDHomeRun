"""Test script to discover HDHomeRun devices and list available channels"""
import requests
import socket
import time
import struct
from typing import Dict, List, Optional, Tuple
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

class TestHDHomeRunDiscovery(ServiceListener):
    """Test listener for HDHomeRun device discovery"""
    
    def __init__(self):
        self.devices: Dict[str, str] = {}  # ip -> name mapping
        self.discovery_complete = False
    
    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a HDHomeRun device is discovered"""
        info = zc.get_service_info(type_, name)
        if info:
            if info.addresses:
                ip = '.'.join(str(b) for b in info.addresses[0])
                self.devices[ip] = name
                print(f"✓ Discovered HDHomeRun device via mDNS: {name}")
                print(f"  IP Address: {ip}")
                print(f"  Port: {info.port}")
                print()
    
    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a HDHomeRun device disappears"""
        print(f"✗ Device removed: {name}")
    
    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a HDHomeRun device is updated"""
        pass


def discover_via_mdns(timeout: int = 10) -> Dict[str, str]:
    """Discover HDHomeRun devices via mDNS/Zeroconf"""
    print("🔍 Method 1: mDNS/Zeroconf Discovery")
    print(f"   (Searching for {timeout} seconds...\n")
    
    devices = {}
    
    try:
        zeroconf = Zeroconf()
        listener = TestHDHomeRunDiscovery()
        
        # Try multiple service types that HDHomeRun might use
        service_types = [
            "_hdhomerun._tcp.local.",
            "_dvb._tcp.local.",
            "_http._tcp.local.",
        ]
        
        browsers = []
        for service_type in service_types:
            try:
                browser = ServiceBrowser(zeroconf, service_type, listener)
                browsers.append(browser)
            except Exception as e:
                print(f"  Warning: Could not browse {service_type}: {e}")
        
        time.sleep(timeout)
        
        # Stop browsers
        for browser in browsers:
            browser.cancel()
        
        devices = listener.devices
        zeroconf.close()
        
    except Exception as e:
        print(f"✗ mDNS discovery error: {e}\n")
    
    if devices:
        print(f"✓ mDNS found {len(devices)} device(s)\n")
    else:
        print("✗ No devices found via mDNS\n")
    
    return devices

def discover_via_broadcast() -> Dict[str, str]:
    """Discover HDHomeRun devices via UDP broadcast"""
    print("🔍 Method 2: UDP Broadcast Discovery")
    print("   (Sending broadcast to port 65001...\n")
    
    devices = {}
    
    try:
        # HDHomeRun discovery protocol
        # Send a discovery packet on port 65001
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(3)
        
        # HDHomeRun discovery packet format
        # Simple discover request
        discover_packet = bytes([
            0x00, 0x02,  # Packet type: discover request
            0x00, 0x0c,  # Payload length
            0x01, 0x04, 0xff, 0xff, 0xff, 0xff,  # Device ID (wildcard)
            0x02, 0x04, 0xff, 0xff, 0xff, 0xff,  # Device Type (wildcard)
        ])
        
        # Send broadcast
        sock.sendto(discover_packet, ('255.255.255.255', 65001))
        
        # Listen for responses
        start_time = time.time()
        while time.time() - start_time < 3:
            try:
                data, addr = sock.recvfrom(1024)
                ip = addr[0]
                
                if ip not in devices:
                    devices[ip] = f"HDHomeRun at {ip}"
                    print(f"✓ Discovered HDHomeRun device via broadcast")
                    print(f"  IP Address: {ip}")
                    print() 
            except socket.timeout:
                break
        
        sock.close()
        
    except Exception as e:
        print(f"✗ UDP broadcast error: {e}\n")
    
    if devices:
        print(f"✓ Broadcast found {len(devices)} device(s)\n")
    else:
        print("✗ No devices found via broadcast\n")
    
    return devices

def test_manual_ip(ip: str) -> Optional[str]:
    """Test if a specific IP is a HDHomeRun device"""
    print(f"🔍 Method 3: Testing manual IP: {ip}\n")
    
    try:
        # Try to fetch device info
        response = requests.get(f"http://{ip}/discover.json", timeout=2)
        response.raise_for_status()
        info = response.json()
        
        device_name = info.get('FriendlyName', f"HDHomeRun at {ip}")
        print(f"✓ Found HDHomeRun device: {device_name}")
        print(f"  Model: {info.get('ModelNumber', 'Unknown')}")
        print(f"  Device ID: {info.get('DeviceID', 'Unknown')}")
        print()
        return device_name
    except requests.RequestException:
        print(f"✗ No HDHomeRun device found at {ip}\n")
        return None

def fetch_lineup(device_ip: str, device_name: str) -> List[dict]:
    """Fetch channel lineup from a HDHomeRun device"""
    try:
        print(f"\n📡 Fetching lineup from {device_name} ({device_ip})...")
        response = requests.get(f"http://{device_ip}/lineup.json", timeout=5)
        response.raise_for_status()
        lineup = response.json()
        print(f"✓ Successfully fetched {len(lineup)} channels")
        return lineup
    except requests.RequestException as e:
        print(f"✗ Error fetching lineup: {e}")
        return []

def display_channels(lineup: List[dict], device_name: str, device_ip: str):
    """Display channel information"""
    if not lineup:
        print("No channels found.")
        return
    
    print(f"\n{'='*80}")
    print(f"Channels from {device_name}")
    print(f"{'='*80}")
    print(f"{'Channel':<12} {'Name':<40} {'Type':<10}")
    print(f"{'-'*80}")
    
    radio_count = 0
    tv_count = 0
    
    for channel in lineup:
        guide_number = channel.get('GuideNumber', 'N/A')
        guide_name = channel.get('GuideName', 'Unknown')
        stream_url = channel.get('URL', '')
        
        # Try to detect if it's audio-only (radio)
        channel_type = "Radio" if is_likely_radio(guide_number, guide_name) else "TV"
        
        if channel_type == "Radio":
            radio_count += 1
        else:
            tv_count += 1
        
        print(f"{guide_number:<12} {guide_name:<40} {channel_type:<10}")
    
    print(f"{'-'*80}")
    print(f"Total channels: {len(lineup)} ({radio_count} radio, {tv_count} TV)")
    
    # Show what would be added as ScreamRouter sources
    print(f"\n{'='*80}")
    print(f"ScreamRouter Sources that would be created:")
    print(f"{'='*80}")
    
    for channel in lineup:
        guide_number = channel.get('GuideNumber', '')
        guide_name = channel.get('GuideName', 'Unknown')
        stream_url = channel.get('URL', '')
        
        # Only show radio stations (or all if you want)
        if is_likely_radio(guide_number, guide_name):
            source_name = f"HDHomeRun [{device_name}]: {guide_name} ({guide_number})"
            source_tag = f"hdhomerun_{device_ip.replace('.', '_')}_{guide_number.replace('.', '_')}"
            
            print(f"\nSource Name: {source_name}")
            print(f"  Tag: {source_tag}")
            print(f"  URL: {stream_url}")

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
    
    radio_keywords = ['radio', 'fm', 'am', 'music', 'npr', 'jazz', 'classical', 'rock', 'news radio']
    name_lower = guide_name.lower()
    return any(keyword in name_lower for keyword in radio_keywords)

def main():
    """Main test function"""
    print("="*80)
    print("HDHomeRun Device Discovery & Channel Test")
    print("="*80)
    print() 
    
    all_devices = {}
    
    # Method 1: mDNS Discovery
    mdns_devices = discover_via_mdns(timeout=10)
    all_devices.update(mdns_devices)
    
    # Method 2: UDP Broadcast Discovery
    broadcast_devices = discover_via_broadcast()
    all_devices.update(broadcast_devices)
    
    # Method 3: Manual IP (if user wants to test)
    print("💡 You can also test a manual IP address")
    print("   Usage: Uncomment the line in main() or run:")
    print("   python test_discovery.py --ip 192.168.1.XXX\n")
    
    # Uncomment and modify this line to test a specific IP:
    # manual_name = test_manual_ip("192.168.1.100")
    # if manual_name:
    #     all_devices["192.168.1.100"] = manual_name
    
    # Summary
    print(f"{'='*80}")
    print(f"Discovery Complete - Found {len(all_devices)} device(s) total")
    print(f"{'='*80}\n")
    
    if not all_devices:
        print("⚠️  No HDHomeRun devices found on the network.")
        print("\n🔧 Troubleshooting tips:")
        print("  1. Ensure HDHomeRun device is powered on and connected to network")
        print("  2. Check that mDNS/Bonjour is working on your network")
        print("  3. Verify device is on the same subnet/VLAN")
        print("  4. Try manually accessing http://<device-ip>/discover.json in browser")
        print("  5. Check firewall settings (need UDP 65001 and TCP 80)")
        print("  6. Try the HDHomeRun app to verify device is working")
        print("\n💡 If you know your device's IP, uncomment the manual test in main()")
    else:
        # Fetch and display lineup for each device
        for device_ip, device_name in all_devices.items():
            lineup = fetch_lineup(device_ip, device_name)
            if lineup:
                display_channels(lineup, device_name, device_ip)
    
    print("\n✓ Test complete!")


if __name__ == "__main__":
    import sys
    
    # Support command line argument for manual IP testing
    if len(sys.argv) > 2 and sys.argv[1] == "--ip":
        manual_ip = sys.argv[2]
        print(f"Testing manual IP: {manual_ip}\n")
        all_devices = {}
        manual_name = test_manual_ip(manual_ip)
        if manual_name:
            all_devices[manual_ip] = manual_name
            lineup = fetch_lineup(manual_ip, manual_name)
            if lineup:
                display_channels(lineup, manual_name, manual_ip)
        else:
            print("❌ Could not connect to HDHomeRun device at that IP")
    else:
        main()