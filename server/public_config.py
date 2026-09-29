"""Validation shared by the server and the setup wizard."""
import copy
import re
from urllib.parse import urlsplit

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
TARGET = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:@\[\]-]{0,253}$")

def require(ok, message):
    if not ok: raise ValueError(message)

def integer(value, low, high, label):
    require(type(value) is int and low <= value <= high, f"{label} must be an integer from {low} to {high}")

def address(value, label):
    require(isinstance(value, str) and bool(TARGET.fullmatch(value)) and '@' not in value, f"{label} must be a hostname or IP address")

def url(value, label):
    require(isinstance(value, str), f"{label} must be a URL")
    parsed = urlsplit(value)
    require(parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username and not parsed.password,
            f"{label} must be an HTTP(S) URL without embedded credentials")
    try: parsed.port
    except ValueError: raise ValueError(f"{label} has an invalid port")

def validate_config(value):
    require(isinstance(value, dict), 'Configuration must be a JSON object')
    config = copy.deepcopy(value)
    config.setdefault('port', 18160)
    config.setdefault('bind', '127.0.0.1')
    config.setdefault('interval_s', 10)
    config.setdefault('history', 90)
    config.setdefault('allow_controls', False)
    config.setdefault('branding', {'name': 'Annunciator', 'subtitle': 'SYSTEM TELEMETRY'})
    config.setdefault('machines', [])
    config.setdefault('services', [])
    integer(config['port'], 1, 65535, 'port')
    integer(config['interval_s'], 1, 3600, 'interval_s')
    integer(config['history'], 2, 8640, 'history')
    address(config['bind'], 'bind')
    require(type(config['allow_controls']) is bool, 'allow_controls must be true or false')
    brand = config['branding']
    require(isinstance(brand, dict), 'branding must be an object')
    for key in ('name', 'subtitle'):
        require(isinstance(brand.get(key, ''), str) and len(brand.get(key, '')) <= 80, f'branding.{key} must be text up to 80 characters')
    if brand.get('accent'):
        require(isinstance(brand['accent'], str) and bool(re.fullmatch(r'#[0-9a-fA-F]{6}', brand['accent'])), 'branding.accent must be a six-digit hex color')
    ids = set()
    for kind in ('machines', 'services'):
        require(isinstance(config[kind], list), f'{kind} must be a list')
        local_ids = set()
        for item in config[kind]:
            require(isinstance(item, dict), f'{kind} entries must be objects')
            mid = item.get('id', '')
            require(isinstance(mid, str) and bool(IDENTIFIER.fullmatch(mid)) and mid not in local_ids,
                    f'{kind} IDs must be unique letters, numbers, hyphens or underscores')
            local_ids.add(mid)
            item.setdefault('name', mid)
            require(isinstance(item['name'], str) and 0 < len(item['name']) <= 80, f'{mid}: name must contain 1-80 characters')
            if kind == 'machines':
                address(item.get('ip'), f'{mid}.ip')
                integer(item.setdefault('port', 22), 1, 65535, f'{mid}.port')
                require(item.get('platform', 'linux') in ('linux', 'windows'), f'{mid}: platform must be linux or windows')
                if item.get('ssh'):
                    require(isinstance(item['ssh'], str) and bool(TARGET.fullmatch(item['ssh'])), f'{mid}: invalid SSH target')
                source = item.get('telemetry', '')
                require(isinstance(source, str), f'{mid}: telemetry must be text')
                if source and source != 'local':
                    prefix, _, target = source.partition(':')
                    require(prefix in ('ssh', 'winps') and bool(TARGET.fullmatch(target)), f'{mid}: use local, ssh:target, or winps:target for telemetry')
                if item.get('containers'):
                    require(item['containers'] in ('docker', 'podman'), f'{mid}: containers must be docker or podman')
                if item.get('mac'):
                    require(isinstance(item['mac'], str) and bool(re.fullmatch(r'(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}', item['mac'])), f'{mid}: invalid MAC address')
            else:
                url(item.get('probe'), f'{mid}.probe')
                if item.get('open'): url(item['open'], f'{mid}.open')
                require(not item.get('host') or item['host'] in ids, f'{mid}: host must refer to a configured machine')
        if kind == 'machines': ids = local_ids
    if config.get('ssh_config'):
        require(isinstance(config['ssh_config'], str) and config['ssh_config'].startswith('/') and len(config['ssh_config']) <= 500, 'ssh_config must be an absolute path')
    if config.get('memory_router'):
        memory = config['memory_router']
        require(isinstance(memory, dict) and isinstance(memory.get('url'), str), 'memory_router.url is required')
        url(memory['url'], 'memory_router.url')
        require(urlsplit(memory['url']).hostname in ('127.0.0.1', 'localhost'), 'memory_router.url must be local to this server')
    if config.get('wake_source'): address(config['wake_source'], 'wake_source')
    if config.get('wake_broadcast'): address(config['wake_broadcast'], 'wake_broadcast')
    speed = config.setdefault('speedtest', {'interval_h': 3, 'routes': []})
    require(isinstance(speed, dict), 'speedtest must be an object')
    require(type(speed.get('interval_h', 3)) in (int, float) and 0.1 <= speed.get('interval_h', 3) <= 8760, 'speedtest.interval_h must be positive')
    routes = speed.setdefault('routes', [])
    require(isinstance(routes, list), 'speedtest.routes must be a list')
    route_ids = set()
    for route in routes:
        require(isinstance(route, dict) and isinstance(route.get('id'), str) and bool(IDENTIFIER.fullmatch(route.get('id', ''))) and route['id'] not in route_ids, 'speedtest route IDs must be unique')
        route_ids.add(route['id'])
        route.setdefault('label', route['id'])
        require(isinstance(route['label'], str), 'speedtest route label must be text')
        if route.get('iface'): require(isinstance(route['iface'], str) and bool(re.fullmatch(r'[A-Za-z0-9_.:-]{1,40}', route['iface'])), 'invalid speedtest interface')
    return config
