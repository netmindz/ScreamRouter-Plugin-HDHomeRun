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
                # Verify this is actually a HDHomeRun device
                if verify_hdhomerun(ip):
                    device_name = get_device_name(ip)
                    self.devices[ip] = device_name
                    print(f"✓ Discovered HDHomeRun device: {device_name}")
                    print(f"  IP Address: {ip}")
                    print(f"  Port: {info.port}")
                    print()
    
    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a HDHomeRun device disappears"""
        pass    
    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a HDHomeRun device is updated"""
        pass


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


def discover_via_mdns(timeout: int = 10) -> Dict[str, str]:
    """Discover HDHomeRun devices via mDNS/Zeroconf"""
    print("🔍 Method 1: mDNS/Zeroconf Discovery")
    print(f"   (Searching for {timeout} seconds...\n")
    
    devices = {}
    
    try:
        zeroconf = Zeroconf()
        listener = TestHDHomeRunDiscovery()
        
        # HDHomeRun-specific service type
        service_types = [
            "_hdhomerun._tcp.local.",
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
        print(f"✓ mDNS found {len(devices)} HDHomeRun device(s)\n")
    else:
        print("✗ No HDHomeRun devices found via mDNS\n")
    
    return devices


def discover_via_broadcast() -> Dict[str, str]:
    """Discover HDHomeRun devices via UDP broadcast"""
    print("🔍 Method 2: UDP Broadcast Discovery")
    print("   (Sending broadcast to port 65001...\n")
    
    devices = {}
    
    try:
        # HDHomeRun discovery protocol
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
        
        # Send broadcast
        sock.sendto(discover_packet, ('255.255.255.255', 65001))
        
        # Listen for responses
        start_time = time.time()
        while time.time() - start_time < 3:
            try:
                data, addr = sock.recvfrom(1024)
                ip = addr[0]
                
                if verify_hdhomerun(ip):
                    if ip not in devices:
                        device_name = get_device_name(ip)
                        devices[ip] = device_name
                        print(f"✓ Discovered HDHomeRun device via broadcast: {device_name}")
                        print(f"  IP Address: {ip}")
                        print() 
            except socket.timeout:
                break
        
        sock.close()
        
    except Exception as e:
        print(f"✗ UDP broadcast error: {e}\n")
    
    if devices:
        print(f"✓ Broadcast found {len(devices)} HDHomeRun device(s)\n")
    else:
        print("✗ No HDHomeRun devices found via broadcast\n")
    
    return devices


def test_manual_ip(ip: str) -> Optional[str]:
    """Test if a specific IP is a HDHomeRun device"""
    print(f"🔍 Method 3: Testing manual IP: {ip}\n")
    
    try:
        response = requests.get(f"http://{ip}/discover.json", timeout=2)
        response.raise_for_status()
        info = response.json()
        
        # Check if it's actually a HDHomeRun
        if 'DeviceID' not in info or 'ModelNumber' not in info:
            print(f"✗ Device at {ip} is not a HDHomeRun\n")
            return None
        
        device_name = info.get('FriendlyName', f"HDHomeRun at {ip}")
        print(f"✓ Found HDHomeRun device: {device_name}")
        print(f"  Model: {info.get('ModelNumber', 'Unknown')}\n")
        print(f"  Device ID: {info.get('DeviceID', 'Unknown')}")
        print(f"  Firmware: {info.get('FirmwareVersion', 'Unknown')}\n")
        return device_name
    except requests.RequestException as e:
        print(f"✗ No HDHomeRun device found at {ip}: {e}\n")
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
    if radio_count > 0:
        print(f"\n{'='*80}")
        print(f"ScreamRouter Sources that would be created (Radio only):")
        print(f"{'='*80}")
        
        for channel in lineup:
            guide_number = channel.get('GuideNumber', '')
            guide_name = channel.get('GuideName', 'Unknown')
            stream_url = channel.get('URL', '')
            
            # Only show radio stations
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
    
    radio_keywords = ['radio', 'fm', 'am', 'music', 'npr', 'jazz', 'classical', 'rock', 'news radio', 'talk radio']
    name_lower = guide_name.lower()
    return any(keyword in name_lower for keyword in radio_keywords)


def scan_subnet_for_hdhomerun() -> Dict[str, str]:
    """Scan local subnet for HDHomeRun devices"""
    print("🔍 Method 4: Subnet Scan (checking common IPs)")
    print("   (This may take a minute...\n")
    
    devices = {}
    
    try:
        # Get local IP to determine subnet
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        
        # Get subnet (assuming /24) - FIXED: removed \n from split
        subnet_parts = local_ip.split('.')
        subnet_base = '.'.join(subnet_parts[:3])
        
        print(f"  Scanning subnet {subnet_base}.0/24 for HDHomeRun devices...")
        
        # Check common IPs that HDHomeRun might use (faster than full scan)
        common_ips = list(range(2, 255))
        
        for i in common_ips:  # Test first 20 IPs for speed
            test_ip = f"{subnet_base}.{i}"
            if verify_hdhomerun(test_ip):
                device_name = get_device_name(test_ip)
                devices[test_ip] = device_name
                print(f"✓ Found HDHomeRun: {device_name} at {test_ip}")
        
        if devices:
            print(f"\n✓ Subnet scan found {len(devices)} HDHomeRun device(s)\n")
        else:
            print(f"\n✗ No HDHomeRun devices found in subnet scan\n")
            
    except Exception as e:
        print(f"✗ Subnet scan error: {e}\n")
    
    return devices


def main():
    """Main test function"""
    print("="*80)
    print("HDHomeRun Device Discovery & Channel Test")
    print("="*80)
    print() 
    
    all_devices = {}
    
    # Method 1: mDNS Discovery (HDHomeRun-specific)
    mdns_devices = discover_via_mdns(timeout=10)
    all_devices.update(mdns_devices)
    
    # Method 2: UDP Broadcast Discovery
    broadcast_devices = discover_via_broadcast()
    all_devices.update(broadcast_devices)
    
    # Method 3: Subnet scan (if nothing found yet)
    if not all_devices:
        print("⚠️  No devices found yet, trying subnet scan...\n")
        subnet_devices = scan_subnet_for_hdhomerun()
        all_devices.update(subnet_devices)
    
    # Summary
    print(f"{'='*80}")
    print(f"Discovery Complete - Found {len(all_devices)} HDHomeRun device(s) total")
    print(f"{'='*80}\n")
    
    if not all_devices:
        print("⚠️  No HDHomeRun devices found on the network.")
        print("\n🔧 Troubleshooting tips:")
        print("  1. Ensure HDHomeRun device is powered on and connected to network")
        print("  2. Verify device is on the same subnet/VLAN as this computer")
        print("  3. Try the HDHomeRun app to verify device is working")
        print("  4. Check firewall settings (need UDP 65001 and TCP 80)")
        print("  5. Try accessing http://<device-ip>/discover.json in browser")
        print("\n💡 Manual test: python test_discovery.py --ip <your-hdhomerun-ip>")
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
