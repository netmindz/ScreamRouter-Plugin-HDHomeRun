"""Test script to discover HDHomeRun devices and list available channels"""
import requests
import time
from typing import Dict, List
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
                print(f"✓ Discovered HDHomeRun device: {name}")
                print(f"  IP Address: {ip}")
                print(f"  Port: {info.port}")
                print()
    
    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a HDHomeRun device disappears"""
        print(f"✗ Device removed: {name}")
    
    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a HDHomeRun device is updated"""
        pass


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


def display_channels(lineup: List[dict], device_name: str):
    """Display channel information"""
    if not lineup:
        print("No channels found.")
        return
    
    print(f"\n{'='*80}")
    print(f"Channels from {device_name}")
    print(f"{'='*80}")
    print(f"{'Channel':<12} {'Name':<40} {'Type':<10}")
    print(f"{'-'*80}")
    
    for channel in lineup:
        guide_number = channel.get('GuideNumber', 'N/A')
        guide_name = channel.get('GuideName', 'Unknown')
        stream_url = channel.get('URL', '')
        
        # Try to detect if it's audio-only (radio)
        # HDHomeRun doesn't always mark this clearly, but you can check metadata
        channel_type = "Radio" if is_likely_radio(guide_number, guide_name) else "TV"
        
        print(f"{guide_number:<12} {guide_name:<40} {channel_type:<10}")
    
    print(f"{'-'*80}")
    print(f"Total channels: {len(lineup)}")
    
    # Show what would be added as ScreamRouter sources
    print(f"\n{'='*80}")
    print(f"ScreamRouter Sources that would be created:")
    print(f"{'='*80}")
    
    for channel in lineup:
        guide_number = channel.get('GuideNumber', '')
        guide_name = channel.get('GuideName', 'Unknown')
        stream_url = channel.get('URL', '')
        
        source_name = f"HDHomeRun [{device_name}]: {guide_name} ({guide_number})"
        source_tag = f"hdhomerun_{stream_url.split('//')[-1].split(':')[0].replace('.', '_')}_{guide_number.replace('.', '_')}"
        
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
    
    radio_keywords = ['radio', 'fm', 'music', 'npr', 'jazz', 'classical', 'rock']
    name_lower = guide_name.lower()
    return any(keyword in name_lower for keyword in radio_keywords)


def main():
    """Main test function"""
    print("="*80)
    print("HDHomeRun Device Discovery & Channel Test")
    print("="*80)
    print("\n🔍 Starting mDNS discovery for HDHomeRun devices...")
    print("   (This will run for 10 seconds)\n")
    
    # Start Zeroconf discovery
    zeroconf = Zeroconf()
    listener = TestHDHomeRunDiscovery()
    browser = ServiceBrowser(zeroconf, "_hdhomerun._tcp.local.", listener)
    
    # Wait for discovery
    try:
        time.sleep(10)  # Discovery period
    except KeyboardInterrupt:
        print("\n\nDiscovery interrupted by user.")
    
    # Stop discovery
    browser.cancel()
    
    # Display results
    print(f"\n{'='*80}")
    print(f"Discovery Complete - Found {len(listener.devices)} device(s)")
    print(f"{'='*80}\n")
    
    if not listener.devices:
        print("⚠️  No HDHomeRun devices found on the network.")
        print("\nTroubleshooting tips:")
        print("  - Ensure HDHomeRun device is powered on and connected to network")
        print("  - Check that mDNS/Bonjour is working on your network")
        print("  - Try manually accessing http://<device-ip>/lineup.json")
        print("  - Check firewall settings")
    else:
        # Fetch and display lineup for each device
        for device_ip, device_name in listener.devices.items():
            lineup = fetch_lineup(device_ip, device_name)
            if lineup:
                display_channels(lineup, device_name)
    
    # Cleanup
    zeroconf.close()
    print("\n✓ Test complete!")


if __name__ == "__main__":
    main()