
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
widgets_confluence_pipeline.py

Runs the widget discovery tool and publishes a table to Confluence in ONE pipeline.

Modes:
  A) Use existing JSON:
     --widgets-json /path/to/published-widgets.json

  B) Run finder command (flexible, your script + args):
     --finder-cmd "python /mnt/data/find_published_widgets_hardened.py --some-arg foo"
     --widgets-json-out /tmp/published-widgets.json
     If the finder writes to stdout, we'll capture it and write to --widgets-json-out.
     If the finder writes a file at --widgets-json-out, we'll use it.

Also merges versions into your base table (--base) and publishes to Confluence.

DEV rule: version_DEV = max(releaseVersion)
IFT rule: version_IFT = list of unique releaseVersion values, ascending, comma-separated.

Example:
  python widgets_confluence_pipeline.py \
    --finder-cmd "python /mnt/data/find_published_widgets_hardened.py --env DEV --env IFT" \
    --widgets-json-out /mnt/data/published-widgets.json \
    --base /mnt/data/base.csv \
    --page-id 123456 \
    --confluence-url https://confluence.example.com \
    --user user@example.com \
    --token <token>

Or (skip finder; reuse existing JSON):
  python widgets_confluence_pipeline.py \
    --widgets-json /mnt/data/published-widgets.json \
    --base /mnt/data/base.csv \
    --page-id 123456 \
    --confluence-url https://confluence.example.com \
    --user user@example.com \
    --token <token>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import subprocess
from typing import Any, Dict, List, Mapping, Optional

import pandas as pd
import requests


def _extract_versions_for_env(items: List[dict]) -> List[int]:
    vals = []
    for obj in items or []:
        if isinstance(obj, Mapping) and "releaseVersion" in obj:
            try:
                vals.append(int(obj["releaseVersion"]))
            except Exception:
                pass
    return vals


def build_versions_df_from_published_widgets(json_path: str) -> pd.DataFrame:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("published-widgets.json должен быть списком записей.")

    rows = []
    for rec in data:
        if not isinstance(rec, Mapping):
            continue
        widget = str(rec.get("widget", "")).strip()
        if not widget:
            continue

        dev_list = rec.get("DEV", []) or []
        ift_list = rec.get("IFT", []) or []

        dev_vals = _extract_versions_for_env(dev_list)
        ift_vals = _extract_versions_for_env(ift_list)

        version_DEV = max(dev_vals) if dev_vals else None
        version_IFT = ", ".join(str(v) for v in sorted(set(ift_vals))) if ift_vals else None

        rows.append({"widget": widget, "version_DEV": version_DEV, "version_IFT": version_IFT})

    return pd.DataFrame(rows)


def load_base_dataframe(path: Optional[str]) -> pd.DataFrame:
    if not path:
        return pd.DataFrame({"widget": []})

    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".tsv"):
        sep = "," if ext == ".csv" else "\t"
        return pd.read_csv(path, sep=sep)
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path)
    if ext == ".json":
        return pd.read_json(path)
    raise ValueError(f"Unsupported base file format: {ext}")


def dataframe_to_confluence_html_table(df: pd.DataFrame) -> str:
    return df.to_html(index=False, escape=False, border=1)


def get_confluence_session(base_url: str, user: str, token: str) -> requests.Session:
    s = requests.Session()
    s.auth = (user, token)
    s.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    health = s.get(f"{base_url.rstrip('/')}/rest/api/space?limit=1")
    if health.status_code >= 400:
        raise RuntimeError(f"Confluence health check failed: {health.status_code} {health.text[:200]}")
    return s


def get_page_version(session: requests.Session, base_url: str, page_id: str) -> int:
    r = session.get(f"{base_url.rstrip('/')}/rest/api/content/{page_id}", params={"expand": "version"})
    if r.status_code != 200:
        raise RuntimeError(f"Get page {page_id} failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    return int(data.get("version", {}).get("number", 0))


def update_page_storage(session: requests.Session, base_url: str, page_id: str, html_body: str, title: Optional[str] = None):
    cur_version = get_page_version(session, base_url, page_id)
    payload = {
        "id": page_id,
        "type": "page",
        "version": {"number": cur_version + 1},
        "body": {
            "storage": {
                "value": html_body,
                "representation": "storage"
            }
        }
    }
    if title:
        payload["title"] = title

    r = session.put(f"{base_url.rstrip('/')}/rest/api/content/{page_id}", json=payload)
    if r.status_code != 200:
        raise RuntimeError(f"Update page {page_id} failed: {r.status_code} {r.text[:500]}")


def run_finder_capture_json(finder_cmd: str, json_out: str) -> str:
    """Run the finder command.
    If the command writes JSON to stdout, capture and write to json_out.
    If the command writes a file at json_out, we'll just return that path.
    """
    if not finder_cmd:
        raise ValueError("--finder-cmd is required to run the finder tool")

    # Run the command and capture stdout/stderr
    proc = subprocess.run(finder_cmd, shell=True, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    # If process failed, still try to salvage stdout if it looks like JSON
    if proc.returncode != 0 and not stdout.strip():
        raise RuntimeError(f"Finder command failed ({proc.returncode}). Stderr:\n{stderr[:5000]}")

    # If a file exists at json_out (finder may have created it), prefer it
    if json_out and os.path.exists(json_out):
        return json_out

    # Else, try to parse stdout as JSON and write to json_out
    try:
        parsed = json.loads(stdout)
    except Exception as e:
        # Not JSON -> error
        raise RuntimeError("Finder did not produce JSON file and stdout is not valid JSON. "
                           "Pass --widgets-json-out that the finder writes to OR ensure it prints JSON to stdout.\n"
                           f"stderr sample:\n{stderr[:5000]}") from e

    out_path = json_out or "published-widgets.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)
    return out_path


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Run finder and publish versions table to Confluence in one pipeline")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--widgets-json", help="Use existing widgets JSON")
    src.add_argument("--finder-cmd", help="Command to run the finder that outputs JSON (stdout or file)")

    p.add_argument("--widgets-json-out", help="If using --finder-cmd, path where JSON should be written or saved from stdout")

    p.add_argument("--base", help="Path to base table (csv/xlsx/json).")
    p.add_argument("--page-id", required=True, help="Confluence page id to update")
    p.add_argument("--confluence-url", default=os.getenv("CONFLUENCE_URL"), help="Confluence base URL")
    p.add_argument("--user", default=os.getenv("CONFLUENCE_USER"), help="Confluence username/email")
    p.add_argument("--token", default=os.getenv("CONFLUENCE_TOKEN"), help="Confluence API token/password")
    p.add_argument("--title", help="Optional: new page title")

    args = p.parse_args(argv)

    if not args.confluence_url or not args.user or not args.token:
        print("Provide --confluence-url, --user, --token or set env CONFLUENCE_URL/USER/TOKEN", file=sys.stderr)
        return 2

    # Step 1: obtain widgets JSON
    if args.widgets_json:
        widgets_json_path = args.widgets_json
    else:
        widgets_json_path = run_finder_capture_json(args.finder_cmd, args.widgets_json_out or "published-widgets.json")

    # Step 2: build versions df
    versions_df = build_versions_df_from_published_widgets(widgets_json_path)

    # Step 3: load base df
    base_df = load_base_dataframe(args.base)
    if "widget" not in base_df.columns:
        # if empty stub and no widget column, create it from versions list so that publish still works
        if base_df.shape[0] == 0:
            base_df = pd.DataFrame({"widget": versions_df["widget"]})
        else:
            raise ValueError("Base dataframe must contain 'widget' column for merge.")

    # Step 4: merge & publish
    result_df = base_df.merge(versions_df, on="widget", how="left")
    html_table = dataframe_to_confluence_html_table(result_df)

    session = get_confluence_session(args.confluence_url, args.user, args.token)
    update_page_storage(session, args.confluence_url, args.page_id, html_table, title=args.title)

    print(f"OK: page {args.page_id} updated. Rows: {len(result_df)}; Columns: {len(result_df.columns)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
