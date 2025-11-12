#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
find_published_widgets.py — собрать список опубликованных виджетов по версиям (DEV/IFT) и сохранить в JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Tuple

import ssl
import urllib.request
import urllib.error

DEFAULT_DEV_BASE = "https://cms-res-web.online.sberbank.ru/da-sdk-b2c/widget-store/widget-store"
DEFAULT_IFT_BASE = "https://cms-res-web.iftonline.sberbank.ru/SBERCMS/da-sdk-b2c/widget-store/widget-store"

DEFAULT_TIMEOUT = 10.0
DEFAULT_UA = "Mozilla/5.0 (compatible; WidgetScanner/1.0; +https://sberbank.ru)"

def _build_candidates(base: str, version: int, filename: str) -> List[str]:
    base = (base or '').rstrip('/')
    fn = (filename or '').lstrip('/')
    v = str(version)
    urls = [
        f"{base}/{v}/{fn}",
        f"{base}/v{v}/{fn}",
        f"{base}/release/{v}/{fn}"
    ]
    out = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out

def _make_context(insecure: bool) -> ssl.SSLContext | None:
    if not insecure:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def _headers() -> dict:
    return {"Accept": "application/json, */*;q=0.1", "User-Agent": DEFAULT_UA}

def _http_get_json_any(urls: List[str], timeout: float, ctx: ssl.SSLContext | None, verbose_prefix: str = '') -> Tuple[bool, str | None, int | None, Any, str | None]:
    last_err = None
    for u in urls:
        if verbose_prefix:
            print(f"↗️  {verbose_prefix}GET  {u}")
        req = urllib.request.Request(u, method='GET', headers=_headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                status = getattr(resp, 'status', 200)
                if 200 <= status < 400:
                    data = resp.read()
                    try:
                        obj = json.loads(data.decode('utf-8', 'ignore'))
                        if verbose_prefix:
                            cnt = len(obj.get('exposes') or []) if isinstance(obj, dict) else 0
                            print(f"↘️  {verbose_prefix}{status} OK JSON ({cnt} widgets)")
                        return True, u, status, obj, None
                    except Exception as e:
                        last_err = f"Invalid JSON: {e}"
                        continue
                else:
                    last_err = f"HTTP {status}"
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
        except Exception as e:
            last_err = str(e)
    return False, None, None, None, last_err

def _http_head_any(urls: List[str], timeout: float, ctx: ssl.SSLContext | None, verbose_prefix: str = '') -> Tuple[bool, str | None, int | None, str | None]:
    last_err = None
    for u in urls:
        if verbose_prefix:
            print(f"↗️  {verbose_prefix}HEAD {u}")
        req = urllib.request.Request(u, method='HEAD', headers={"User-Agent": DEFAULT_UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                status = getattr(resp, 'status', 200)
                ok = 200 <= status < 400
                if verbose_prefix:
                    print(f"↘️  {verbose_prefix}{status} {'OK' if ok else 'NOT OK'} for container")
                if ok:
                    return True, u, status, None
                last_err = f"HTTP {status}"
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
        except Exception as e:
            last_err = str(e)
    return False, None, None, last_err

def normalize_widget_name(raw: Any) -> str:
    if not raw:
        return ''
    s = str(raw).strip()
    if not s:
        return ''
    s = s.replace('_', '-').replace(' ', '-')
    while '--' in s:
        s = s.replace('--', '-')
    return s.lower()

def create_display_name(raw: Any) -> str:
    if not raw:
        return 'unknown'
    s = str(raw).strip().replace('_', '-').replace(' ', '-')
    while '--' in s:
        s = s.replace('--', '-')
    return s.lower()

def ensure_widget_entry(store: Dict[str, Dict[str, Any]], key: str, display_name: str) -> Dict[str, Any]:
    if key not in store:
        store[key] = {'name': display_name or key, 'environments': {}}
    entry = store[key]
    if not entry.get('name') and display_name:
        entry['name'] = display_name
    return entry

def expand_range(start: int, end: int) -> List[int]:
    lo, hi = (start, end) if start <= end else (end, start)
    return list(range(lo, hi + 1))

def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Collect published widgets for versions and save to JSON.')
    p.add_argument('tokens', nargs='*', help='Диапазон(ы) версий: "20-30", "15..20" или отдельные "56"')
    p.add_argument('--range', dest='range_token', help='Диапазон "start-end" или "start..end"')
    p.add_argument('--from', dest='from_v', type=int, help='Начало диапазона (включительно)')
    p.add_argument('--to', dest='to_v', type=int, help='Конец диапазона (включительно)')
    p.add_argument('--versions', '-v', help='Список через запятую, например: 12,14,18')
    p.add_argument('--dev-base', dest='dev_base', default=os.getenv('WIDGET_STORE_DEV_BASE', DEFAULT_DEV_BASE))
    p.add_argument('--ift-base', dest='ift_base', default=os.getenv('WIDGET_STORE_IFT_BASE', DEFAULT_IFT_BASE))
    p.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT, help=f'Таймаут в секундах (по умолчанию {DEFAULT_TIMEOUT})')
    p.add_argument('--out', '-o', dest='out_file', default='published-widgets.json', help='Куда сохранить JSON')
    p.add_argument('--json', action='store_true', help='Также вывести JSON в stdout')
    p.add_argument('--verbose', action='store_true', help='Печатать логи запросов')
    p.add_argument('--insecure', action='store_true', help='Отключить проверку TLS-сертификата для всех хостов')
    p.add_argument('--strict-tls', action='store_true', help='Жёсткая проверка TLS и для IFT (по умолчанию IFT — insecure)')
    return p.parse_args(argv)

def parse_range_token(token: str) -> List[int]:
    token = (token or '').strip()
    import re as _re
    m = _re.match(r'^(\d+)\s*(?:-|\.\.)\s*(\d+)$', token)
    if not m:
        raise ValueError(f'Invalid range token "{token}". Ожидался формат "start-end"')
    return expand_range(int(m.group(1)), int(m.group(2)))

def collect_versions_from_args(ns: argparse.Namespace) -> List[int]:
    versions: List[int] = []
    if ns.tokens:
        for t in ns.tokens:
            t = t.strip()
            if t.isdigit():
                versions.append(int(t))
            elif ('-' in t) or ('..' in t):
                versions.extend(parse_range_token(t))
            else:
                raise ValueError(f'Unknown argument "{t}"')
    if ns.range_token:
        versions.extend(parse_range_token(ns.range_token))
    if ns.from_v is not None or ns.to_v is not None:
        if ns.from_v is None or ns.to_v is None:
            raise ValueError('Both --from and --to must be provided together')
        versions.extend(expand_range(ns.from_v, ns.to_v))
    if ns.versions:
        for x in ns.versions.split(','):
            x = x.strip()
            if x.isdigit():
                versions.append(int(x))
    versions = sorted(set([v for v in versions if isinstance(v, int) and v > 0]))
    return versions

def fetch_version(env_key: str, base_url: str, version: int, timeout: float, ctx: ssl.SSLContext | None, verbose: bool) -> Dict[str, Any]:
    prefix = f"[{env_key}] " if verbose else ""
    stats_urls = _build_candidates(base_url, version, 'mf-stats.json')
    cont_urls  = _build_candidates(base_url, version, 'container.js')

    ok_stats, stats_url, status_stats, data_stats, err_stats = _http_get_json_any(stats_urls, timeout, ctx, prefix)
    ok_container, container_url, status_container, err_container = _http_head_any(cont_urls, timeout, ctx, prefix)

    widgets: List[str] = []
    if ok_stats and isinstance(data_stats, dict):
        exposes = data_stats.get('exposes')
        if isinstance(exposes, list):
            for e in exposes:
                if isinstance(e, dict) and e.get('name'):
                    widgets.append(str(e['name']))

    return {
        'env': env_key,
        'version': version,
        'widgets': widgets,
        'stats_ok': ok_stats,
        'stats_status': status_stats,
        'stats_error': err_stats,
        'stats_url': stats_url,
        'container_ok': ok_container,
        'container_status': status_container,
        'container_error': err_container,
        'container_url': container_url,
    }

def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        versions = collect_versions_from_args(args)
    except Exception as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2
    if not versions:
        print("❌ No versions provided. Specify a range like 20-30 or --versions 12,14,18", file=sys.stderr)
        return 2

    insecure_all = bool(args.insecure)
    insecure_ift = not args.strict_tls  # по умолчанию True
    ctx_dev = _make_context(insecure_all)
    ctx_ift = _make_context(insecure_all or insecure_ift)

    envs = [
        {'key': 'DEV', 'label': 'Dev', 'base': args.dev_base, 'ctx': ctx_dev},
        {'key': 'IFT', 'label': 'IFT', 'base': args.ift_base, 'ctx': ctx_ift},
    ]

    widget_map: Dict[str, Dict[str, Any]] = {}
    diagnostics: List[Dict[str, Any]] = []

    for env in envs:
        for v in versions:
            info = fetch_version(env['key'], env['base'], v, args.timeout, env['ctx'], args.verbose)

            if not info['stats_ok']:
                diagnostics.append({
                    'environment': env['key'],
                    'version': v,
                    'target': 'mf-stats.json',
                    'status': info['stats_status'],
                    'error': info['stats_error'],
                    'url': info['stats_url'],
                })
                continue

            if not info['container_ok']:
                diagnostics.append({
                    'environment': env['key'],
                    'version': v,
                    'target': 'container.js',
                    'status': info['container_status'],
                    'error': info['container_error'],
                    'url': info['container_url'],
                })

            for raw in info['widgets']:
                normalized = normalize_widget_name(raw)
                if not normalized:
                    continue
                display = create_display_name(raw)
                entry = ensure_widget_entry(widget_map, normalized, display)
                entry['environments'].setdefault(env['key'], []).append({'releaseVersion': v})

    payload = []
    for entry in sorted(widget_map.values(), key=lambda x: (x.get('name') or '')):
        row = {'widget': entry.get('name')}
        for env in envs:
            key = env['key']
            items = entry['environments'].get(key, [])
            items = sorted(items, key=lambda it: it.get('releaseVersion', 0))
            row[key] = items
        payload.append(row)

    out_path = os.path.abspath(args.out_file)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"✅ Saved JSON with {len(payload)} widgets to: {out_path}")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if diagnostics:
        print('\\n⚠️  Some versions had issues:', file=sys.stderr)
        for d in diagnostics:
            status = f"HTTP {d['status']}" if d.get('status') else (d.get('error') or 'Unknown error')
            tgt = f" [{d['target']}]" if d.get('target') else ''
            print(f"   • {d['environment']} v{d['version']}{tgt}: {status} ({d.get('url','')})", file=sys.stderr)
        if not args.verbose:
            print('   Use --verbose to see request logs.', file=sys.stderr)

    return 0

if __name__ == '__main__':
    raise SystemExit(main())
