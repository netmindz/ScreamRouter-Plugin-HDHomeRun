"""Test script to discover HDHomeRun devices and list available channels"""
import sys
from typing import List
import requests
from hdhomerun_plugin import (
    discover_all_methods,
    get_device_info,
    is_likely_radio
)


def test_manual_ip(ip: str):
    """Test if a specific IP is a HDHomeRun device"""
    print(f"🔍 Testing manual IP: {ip}\n")

    info = get_device_info(ip)
    if not info:
        print(f"✗ No HDHomeRun device found at {ip}\n")
        return None

    device_name = info.get('FriendlyName', f"HDHomeRun at {ip}")
    print(f"✓ Found HDHomeRun device: {device_name}")
    print(f"  Model: {info.get('ModelNumber', 'Unknown')}")
    print(f"  Device ID: {info.get('DeviceID', 'Unknown')}")
    print(f"  Firmware: {info.get('FirmwareVersion', 'Unknown')}\n")
    return device_name


def fetch_lineup(device_ip: str) -> List[dict]:
    """Fetch channel lineup from a HDHomeRun device"""
    try:
        print(f"\n📡 Fetching lineup from {device_ip}...")
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


def main():
    """Main test function"""
    print("="*80)
    print("HDHomeRun Device Discovery & Channel Test")
    print("="*80)
    print()

    # Run all discovery methods
    print("🔍 Running all discovery methods...")
    print("   (mDNS, UDP Broadcast, and Subnet Scan if needed)\n")

    all_devices = discover_all_methods(mdns_timeout=10)

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
            lineup = fetch_lineup(device_ip)
            if lineup:
                display_channels(lineup, device_name, device_ip)

    print("\n✓ Test complete!")


if __name__ == "__main__":
    # Support command line argument for manual IP testing
    if len(sys.argv) > 2 and sys.argv[1] == "--ip":
        manual_ip = sys.argv[2]
        print(f"Testing manual IP: {manual_ip}\n")
        all_devices = {}
        manual_name = test_manual_ip(manual_ip)
        if manual_name:
            all_devices[manual_ip] = manual_name
            lineup = fetch_lineup(manual_ip)
            if lineup:
                display_channels(lineup, manual_name, manual_ip)
        else:
            print("❌ Could not connect to HDHomeRun device at that IP")
    else:
        main()

