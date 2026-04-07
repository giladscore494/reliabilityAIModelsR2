#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Seed / manage public example analyses.

Usage:
    python scripts/seed_public_examples.py <history_id> <slug>
    python scripts/seed_public_examples.py --list
    python scripts/seed_public_examples.py --unset <slug>
"""

import argparse
import os
import sys

# Ensure the app root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app, db
from app.models import SearchHistory


def seed(history_id: int, slug: str):
    row = SearchHistory.query.get(history_id)
    if not row:
        print(f"Error: SearchHistory id={history_id} not found.")
        sys.exit(1)
    row.is_public_example = True
    row.example_slug = slug
    db.session.commit()
    print(f"OK: SearchHistory id={history_id} promoted to public example with slug='{slug}'")


def unset(slug: str):
    row = SearchHistory.query.filter_by(example_slug=slug, is_public_example=True).first()
    if not row:
        print(f"No public example with slug='{slug}' found.")
        sys.exit(1)
    row.is_public_example = False
    row.example_slug = None
    db.session.commit()
    print(f"OK: Public example slug='{slug}' (id={row.id}) removed.")


def list_examples():
    rows = SearchHistory.query.filter_by(is_public_example=True).all()
    if not rows:
        print("No public examples configured.")
        return
    print(f"{'ID':>6}  {'Slug':<30}  {'Make':<15}  {'Model':<15}  {'Year':>5}")
    print("-" * 80)
    for r in rows:
        print(f"{r.id:>6}  {r.example_slug or '':30}  {r.make or '':15}  {r.model or '':15}  {r.year or 0:>5}")


def main():
    parser = argparse.ArgumentParser(description="Manage public example analyses")
    parser.add_argument("history_id", nargs="?", type=int, help="SearchHistory row ID to promote")
    parser.add_argument("slug", nargs="?", type=str, help="URL slug for the example")
    parser.add_argument("--list", action="store_true", help="List all public examples")
    parser.add_argument("--unset", type=str, metavar="SLUG", help="Remove a public example by slug")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        if args.list:
            list_examples()
        elif args.unset:
            unset(args.unset)
        elif args.history_id and args.slug:
            seed(args.history_id, args.slug)
        else:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()
