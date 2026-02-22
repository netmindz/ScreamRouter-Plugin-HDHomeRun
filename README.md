# HDHomeRun Plugin for ScreamRouter

A ScreamRouter plugin that automatically discovers HDHomeRun devices on your network and adds their radio/TV channels as audio sources.

## Features

- **Auto-discovery** via mDNS/Zeroconf
- Automatically fetches channel lineup from discovered devices
- Adds all stations as permanent sources in ScreamRouter
- Periodic refresh of station lineup
- REST API endpoints for manual control

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Copy `hdhomerun_plugin.py` to your ScreamRouter plugins directory:
```bash
cp hdhomerun_plugin.py /path/to/screamrouter/screamrouter/plugins/
```

3. Register the plugin in `screamrouter/plugin_manager/plugin_manager.py`:
```python
from screamrouter.plugins.hdhomerun_plugin import PluginHDHomeRun

# In PluginManager.__init__():
self.register_plugin(PluginHDHomeRun())
```

4. Restart ScreamRouter

## API Endpoints

- `GET /hdhomerun/devices` - List discovered HDHomeRun devices
- `POST /hdhomerun/refresh` - Manually refresh station lineup
- `POST /hdhomerun/discover` - Manually trigger device discovery

## Configuration

The plugin automatically discovers devices on your network. If you want to:
- **Filter for radio only**: Modify the `fetch_stations_from_device` method
- **Change refresh interval**: Set `self.refresh_interval` (in seconds)
- **Add manual IP**: Call `add_device(ip, name)` in `plugin_start`

## Requirements

- HDHomeRun device on your network
- ScreamRouter with plugin support
- Python packages: `requests`, `zeroconf`

## How It Works

1. Plugin starts and begins mDNS discovery for `_hdhomerun._tcp.local.`
2. When a device is found, fetches `/lineup.json` from the device
3. Creates a permanent source for each channel/station
4. Stations appear in ScreamRouter UI and can be routed to sinks
5. Periodically refreshes the lineup to catch any changes

## Troubleshooting

- **No devices found**: Check that HDHomeRun is on the same network and mDNS is working
- **Stations not appearing**: Check ScreamRouter logs for errors
- **Streams not playing**: Verify ffmpeg is installed and HDHomeRun URLs are accessible

## License

This plugin is compatible with ScreamRouter's licensing.
